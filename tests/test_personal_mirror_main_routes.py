"""Normal application registration, using isolated actual mirror producers.

No service or startup worker is launched. Runtime schema/files/pair checks are
real; this verifies route registration and normal middleware, not live loading.
"""
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_personal_mirror_api import http_env, base, selected, PREFIX, TOKEN
from tests.test_personal_mirror_transfer import mirror_env, table_state


def test_normal_application_registers_all_four_gated_mirror_routes(http_env):
    app = create_app()
    before = table_state(http_env)
    client = TestClient(app, base_url='https://mirror.example.invalid')
    try:
        refused = client.post(PREFIX + '/prepare', json={**base(http_env), 'prepare': http_env['prepare']})
        assert refused.status_code == 401 and table_state(http_env) == before
        absent = client.post(PREFIX + '/receipt', json=selected(http_env), headers={'X-Sync-Token': TOKEN})
        assert absent.status_code == 200 and absent.json() == {'receipt': None}
        prepared = client.post(PREFIX + '/prepare', json={**base(http_env), 'prepare': http_env['prepare']},
                               headers={'X-Sync-Token': TOKEN})
        assert prepared.status_code == 200 and prepared.json()['state'] == 'prepared'
        attachment = client.post(PREFIX + '/attachment', content=b'',
                                 headers={'X-Sync-Token': TOKEN, 'Content-Type': 'application/octet-stream'})
        assert attachment.status_code == 400
        assert attachment.json()['code'] == 'PERSONAL_MIRROR_INVALID_REQUEST'
        committed = client.post(PREFIX + '/commit', json=selected(http_env), headers={'X-Sync-Token': TOKEN})
        assert committed.status_code == 200 and committed.json()['installed_revision'] == 1
    finally:
        client.close()
