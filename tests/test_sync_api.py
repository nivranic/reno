"""Real FastAPI transport and isolated SQLite/files; no remote network/model."""
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from fastapi.testclient import TestClient
import pytest

from reno import config, db, sync_events, sync_queue
from tests.test_sync_transfer import env, CONTENT, VIDEO, URL, upload_binding


@pytest.fixture()
def http_env(env, monkeypatch):
    from app.main import create_app
    config.set_local({"node_role":"cloud", "sync_token":"test-sync-secret", "auth_token":"test-browser-secret"})
    env["lease"] = sync_queue.heartbeat(env["con"], env["identity"], str(uuid.uuid4()))["lease"]
    env["con"].commit()
    env["app"] = create_app()
    with TestClient(env["app"], base_url="https://sync.example.test") as client:
        env["client"] = client
        prepared = post(env, "uploads/prepare", {**base(env), "binding": upload_binding(env), "descriptors": [env["media"]]})
        assert prepared.status_code == 200, prepared.text
        uploaded = client.post("/api/sync/media", headers=media_headers(env), content=CONTENT)
        assert uploaded.status_code == 200, uploaded.text
        yield env


def base(env):
    return {"source": env["identity"], "lease": env["lease"]}


def recovery_stream(env):
    version = db.knowledge_version(env["con"])
    return {key: version[key] for key in ("library_id", "library_epoch")}


def post(env, path, payload):
    return env["client"].post('/api/sync/' + path, json=payload, headers={'X-Sync-Token':'test-sync-secret'})


def pulling(env, count=0, **paging):
    return {**base(env), "events": {"after":0, "limit":200, "through":None, "stream":None, **paging},
            "claim_limit":count}


def media_headers(env, descriptor=None):
    descriptor = descriptor or env["media"]
    binding = upload_binding(env)
    if descriptor != env["media"]:
        binding["publication_id"] = str(uuid.uuid5(uuid.UUID(binding["publication_id"]), json.dumps(descriptor, sort_keys=True)))
    value = {**base(env), "intent": {**binding, "descriptor": descriptor}}
    return {'X-Sync-Token':'test-sync-secret', 'Content-Type':'application/octet-stream',
            'X-Sync-Media':base64.urlsafe_b64encode(json.dumps(value).encode()).decode()}


def test_handshake_requires_sync_credential_and_does_not_pair(http_env):
    env = http_env
    env['con'].execute('DELETE FROM sync_session')
    env['con'].execute('DELETE FROM sync_pair')
    env['con'].commit()
    for headers in ({}, {'X-Reno-Token':'test-browser-secret'}):
        assert env['client'].get('/api/sync/handshake',headers=headers).status_code == 401
    response = env['client'].get('/api/sync/handshake',headers={'X-Sync-Token':'test-sync-secret'})
    assert response.status_code == 200
    assert response.json()['target']['library_id'] == db.knowledge_version(env['con'])['library_id']
    assert env['con'].execute('SELECT count(*) FROM sync_pair').fetchone()[0] == 0
    result = post(env,'heartbeat',{'source':env['identity'],'session_id':str(uuid.uuid4())})
    assert result.status_code == 409 and result.json()['code'] == 'SYNC_NOT_PAIRED'


def test_heartbeat_commit_and_old_lease_fence(http_env):
    env = http_env
    result = post(env,'heartbeat',{**base(env), 'session_id':env['lease']['session_id'],
                                  'metrics':{'cookie_state':'unknown','queue_depth':2}})
    assert result.status_code == 200
    assert result.json()['lease']['generation'] == env['lease']['generation']
    assert json.loads(env['con'].execute('SELECT metrics_json FROM sync_session').fetchone()[0])['queue_depth'] == 2
    malformed = base(env)
    malformed['lease'] = {**malformed['lease'], 'lease_token':str(uuid.uuid4())}
    rejected = post(env,'heartbeat',{**malformed,'session_id':env['lease']['session_id']})
    assert rejected.status_code == 409


def test_pull_repeats_claim_after_lost_response_without_attempt_increment(http_env):
    env = http_env
    sync_queue.enqueue(env['con'],[URL,URL+'?p=2'])
    env['con'].commit()
    first = post(env,'pull',pulling(env,1))
    assert first.status_code == 200
    second = post(env,'pull',pulling(env,1))
    assert second.json()['claims'] == first.json()['claims']
    assert len(first.json()['claims']) == 1
    assert dict(env['con'].execute('SELECT status,count(*) FROM sync_queue GROUP BY status')) == {'claimed':1,'queued':1}
    assert env['con'].execute("SELECT attempts FROM sync_queue WHERE status='claimed'").fetchone()[0] == 1


def test_pull_invalid_stream_rolls_back_claims_and_requires_rescan(http_env):
    env = http_env
    sync_queue.enqueue(env['con'],[URL])
    env['con'].commit()
    version = db.knowledge_version(env['con'])
    stream = {'library_id':version['library_id'],'library_epoch':version['library_epoch']+1}
    result = post(env,'pull',pulling(env,1,stream=stream))
    assert result.status_code == 409 and result.json()['code'] == 'SYNC_EVENT_STREAM_CHANGED'
    assert env['con'].execute('SELECT status FROM sync_queue').fetchone()[0] == 'queued'
    assert post(env,'pull',pulling(env,1,after=1)).status_code == 400


@pytest.mark.parametrize('epoch', [True,False,1.0,'1'])
def test_pull_stream_epoch_requires_an_integer_not_python_equal_values(http_env,epoch):
    env=http_env
    version=db.knowledge_version(env['con'])
    response=post(env,'pull',pulling(env,stream={'library_id':version['library_id'],'library_epoch':epoch}))
    assert response.status_code == 400 and response.json()['code']=='INVALID_SYNC_REQUEST'


def test_snapshot_commits_receipt_queue_and_knowledge_before_response(http_env):
    env = http_env
    sync_queue.enqueue(env['con'],[URL])
    env['con'].commit()
    claimed = post(env,'pull',pulling(env,1)).json()['claims'][0]
    payload = {**base(env),'kind':'snapshot','snapshot':env['payload'],'recovery_generation':0,'recovery_stream':recovery_stream(env),
               'claims':[{'video_id':VIDEO,'claim':claimed['claim']}]}
    first = post(env,'push',payload)
    assert first.status_code == 200 and not first.json()['replayed']
    assert env['con'].execute('SELECT status FROM sync_queue').fetchone()[0] == 'done'
    assert env['con'].execute('SELECT claim FROM knowledge_atom').fetchone()[0] == '施工前检查基层'
    again = post(env,'push',payload)
    assert again.status_code == 200 and again.json()['replayed']
    assert env['con'].execute('SELECT count(*) FROM sync_publication').fetchone()[0] == 1


def test_events_merge_is_idempotent_and_pull_is_fixed_high_watermark(http_env):
    from reno import review
    env = http_env
    review.record_decision(env['source'], atom_id='atom', action='confirm',client_id='source-node')
    env['source'].commit()
    events = sync_events.export_events(env['source'])['events']
    payload = {**base(env),'kind':'events','events':events}
    assert post(env,'push',payload).json()['inserted'] == 1
    assert post(env,'push',payload).json()['duplicates'] == 1
    first = post(env,'pull',pulling(env)).json()['events']
    assert first['events'] == events
    second = post(env,'pull',pulling(env,after=first['next_cursor'],through=first['high_watermark'],stream=first['stream']))
    assert second.status_code == 200 and second.json()['events']['events'] == []


def test_failure_is_bounded_and_committed(http_env):
    env = http_env
    sync_queue.enqueue(env['con'],[URL])
    env['con'].commit()
    claimed = post(env,'pull',pulling(env,1)).json()['claims'][0]
    payload = {**base(env),'kind':'fail','claim':claimed['claim'],'error_class':'processing_failed',
               'recovery_generation':0,'recovery_stream':recovery_stream(env)}
    response = post(env,'push',payload)
    assert response.status_code == 200 and response.json()['status'] == 'retry_wait'
    assert env['con'].execute('SELECT status FROM sync_queue').fetchone()[0] == 'retry_wait'
    rejected = post(env,'push',{**payload,'error_class':'secret exception /private/path'})
    assert rejected.status_code == 400 and 'private' not in rejected.text


def test_media_upload_is_streamed_checked_and_does_not_publish(http_env):
    env = http_env
    content = b'new upload bytes'
    descriptor = {**env['media'],'sha256':hashlib.sha256(content).hexdigest(),'byte_size':len(content)}
    intent = json.loads(base64.urlsafe_b64decode(media_headers(env, descriptor)['X-Sync-Media']))['intent']
    prepared = post(env, 'uploads/prepare', {**base(env), 'binding': {k: intent[k] for k in upload_binding(env)}, 'descriptors': [descriptor]})
    assert prepared.status_code == 200
    response = env['client'].post('/api/sync/media',headers=media_headers(env,descriptor),
                                   content=iter([content[:5],content[5:]]))
    assert response.status_code == 200 and response.json()['published'] is False
    assert env['store'].verified_path(descriptor).read_bytes() == content
    assert env['con'].execute('SELECT count(*) FROM video_asset').fetchone()[0] == 0


@pytest.mark.parametrize('content,expected_status,expected_code', [
    (CONTENT[:-1],400,'INVALID_SYNC_REQUEST'),(CONTENT+b'x',413,'INVALID_SYNC_REQUEST'),
    (b'x'*len(CONTENT),422,'SYNC_MEDIA_HASH_MISMATCH')])
def test_invalid_media_keeps_old_content_and_leaves_no_part(http_env,content,expected_status,expected_code):
    env=http_env
    response=env['client'].post('/api/sync/media',headers=media_headers(env),content=content)
    assert response.status_code == expected_status and response.json()['code'] == expected_code
    assert env['store'].verified_path(env['media']).read_bytes() == CONTENT
    assert not list((config.MEDIA/'.sync-tmp').glob('*.part'))


@pytest.mark.parametrize('extra', [{'now':'2099-01-01'},{'lease_seconds':999999},{'bind_peer':True}])
def test_heartbeat_rejects_client_clock_or_privilege_fields(http_env,extra):
    env=http_env
    body={**base(env),'session_id':env['lease']['session_id'],**extra}
    assert post(env,'heartbeat',body).status_code == 400


@pytest.mark.parametrize('raw', [b'[]',b'{bad',b'{"source":{},"source":{}}',b'{"limit":NaN}'])
def test_malformed_json_is_controlled(http_env,raw):
    env=http_env
    response=env['client'].post('/api/sync/pull',headers={'X-Sync-Token':'test-sync-secret','Content-Type':'application/json'},content=raw)
    assert response.status_code == 400 and 'test-sync-secret' not in response.text


def test_json_and_media_header_limits_reject_before_work(http_env,monkeypatch):
    from app import sync_api
    env=http_env
    monkeypatch.setattr(sync_api,'MAX_JSON_BYTES',128)
    result=post(env,'push',{**base(env),'kind':'events','events':[]})
    assert result.status_code == 413
    headers=media_headers(env)
    headers['X-Sync-Media']='a'*(sync_api.MAX_MEDIA_HEADER+1)
    assert env['client'].post('/api/sync/media',headers=headers,content=b'x').status_code == 400


def test_storage_error_and_failed_commit_return_no_receipt(http_env,monkeypatch):
    from app import sync_api
    env=http_env
    def broken(con,*args,**kwargs):
        raise OSError('secret token and private data path')
    monkeypatch.setattr(sync_api,'_authorized_commit',broken)
    response=post(env,'push',{**base(env),'kind':'snapshot','snapshot':env['payload'],'claims':[],
                              'recovery_generation':0,'recovery_stream':recovery_stream(env)})
    assert response.status_code == 503 and 'private' not in response.text and 'secret token' not in response.text
    assert env['con'].execute('SELECT count(*) FROM sync_publication').fetchone()[0] == 0
    assert env['con'].execute('SELECT count(*) FROM knowledge_atom').fetchone()[0] == 0


def test_status_uses_browser_gate_and_reports_configuration_and_compatibility(http_env,monkeypatch):
    env=http_env
    assert env['client'].get('/api/sync-status',headers={'X-Sync-Token':'test-sync-secret'}).status_code == 401
    headers={'X-Reno-Token':'test-browser-secret'}
    result=env['client'].get('/api/sync-status',headers=headers)
    assert result.status_code == 200
    assert all(result.json()[key] for key in ('online','paired','compatible','configured'))
    assert 'lease_token' not in result.text and 'test-sync-secret' not in result.text
    env['con'].execute("UPDATE sync_pair SET schema_fingerprint=?",('0'*64,))
    env['con'].commit()
    state=env['client'].get('/api/sync-status',headers=headers).json()
    assert state['paired'] and not state['compatible'] and not state['online']
    config.set_local({'sync_token':''})
    state=env['client'].get('/api/sync-status',headers=headers).json()
    assert not state['configured'] and not state['online']


def test_media_concurrency_rejects_without_consuming_body(http_env):
    from app import sync_api
    env=http_env
    assert sync_api._uploads.acquire(False) and sync_api._uploads.acquire(False)
    try:
        result=env['client'].post('/api/sync/media',headers=media_headers(env),content=CONTENT)
        assert result.status_code == 429
    finally:
        sync_api._uploads.release()
        sync_api._uploads.release()


def test_asgi_disconnect_cleans_partial_media_and_releases_slot(http_env):
    env=http_env
    content=b'synthetic interrupted media'
    descriptor={**env['media'],'sha256':hashlib.sha256(content).hexdigest(),'byte_size':len(content)}
    intent=json.loads(base64.urlsafe_b64decode(media_headers(env,descriptor)['X-Sync-Media']))['intent']
    assert post(env,'uploads/prepare',{**base(env),'binding':{k:intent[k] for k in upload_binding(env)},'descriptors':[descriptor]}).status_code==200
    headers=[(key.lower().encode(),value.encode()) for key,value in media_headers(env,descriptor).items()]
    scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST','scheme':'https',
           'path':'/api/sync/media','raw_path':b'/api/sync/media','query_string':b'',
           'root_path':'','headers':headers,'client':('127.0.0.1',40001),'server':('testserver',443)}
    messages=[{'type':'http.request','body':content[:5],'more_body':True},{'type':'http.disconnect'}]
    sent=[]
    async def run():
        async def receive():
            if messages:
                return messages.pop(0)
            return {'type':'http.disconnect'}
        async def send(message):
            sent.append(message)
        await env['app'](scope,receive,send)
    asyncio.run(run())
    assert messages == [], 'The prepared upload must actually consume bytes and the disconnect'
    result=next(message for message in sent if message['type']=='http.response.start')
    body=json.loads(b''.join(message.get('body',b'') for message in sent if message['type']=='http.response.body'))
    assert result['status']==408 and body['code']=='SYNC_BODY_INTERRUPTED'
    from reno.sync_upload_pins import get, intent_id
    assert get(env['con'],intent_id(intent))['state']=='reserved'
    assert not list((config.MEDIA/'.sync-tmp').glob('*.part'))
    with pytest.raises(Exception):
        env['store'].verified_path(descriptor)
    # A later real upload succeeds, proving the global upload capacity was
    # released only after the failed worker finished its file cleanup.
    response=env['client'].post('/api/sync/media',headers=media_headers(env),content=CONTENT)
    assert response.status_code == 200
