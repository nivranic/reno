"""Two processes compete for one source opportunity and one actual fake child."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from reno import config, db, storage_admin, tasks


def test_two_process_dispatchers_launch_only_one_synthetic_child(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    monkeypatch.setattr(config, "ORIG", tmp_path / "media/originals")
    config.set_local({"node_role": "all", "budget_enabled": True})
    db.initialize()
    profile = storage_admin.profile()
    storage_admin.initialize(profile)
    url = "https://source.synthetic.test/cas"
    with storage_admin.connection(profile) as con:
        task_id = tasks.enqueue(con, kind="ingest", payload={"target": url}, profile=profile)
        con.execute("UPDATE job SET status='running',owner_id='synthetic-owner' WHERE job_id=?", (task_id,))
        con.commit()
    marker = tmp_path / "physical-child-count"
    release = tmp_path / "release-dispatchers"
    helper = tmp_path / "dispatcher.py"
    helper.write_text('''import hashlib, json, pathlib, subprocess, sys, time
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from reno import config, db, source_budget, source_contract as codec, storage_admin, worker
details=json.loads(sys.argv[2])
config.DATA=pathlib.Path(details['data'])
config.MEDIA=pathlib.Path(details['media'])
config.ORIG=config.MEDIA/'originals'
profile=storage_admin.profile()
assert str(profile.database)==details['database']
con=db.connect(profile.database)
job=dict(con.execute('SELECT * FROM job WHERE job_id=?',(details['task_id'],)).fetchone())
con.close()
def run(plan, **kwargs):
    child_code="import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); f=p.open('ab'); f.write(b'x'); f.close(); time.sleep(0.25)"
    child=subprocess.Popen([sys.executable,'-I','-c',child_code,details['marker']],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert child.wait(timeout=10)==0
    body=codec.canonical_json({'id':'SyntheticCAS','title':'Synthetic'})
    return codec.SourceProcessResult(plan.request_sha256,True,True,0,'exited',
        stdout_bytes=len(body.encode()),stdout_sha256=hashlib.sha256(body.encode()).hexdigest(),metadata_json=body)
runner=SimpleNamespace(prepare=lambda plan,**kwargs:plan,run=run,verify_prepared_files=lambda prepared:())
source_budget._components=lambda:(codec,runner)
pathlib.Path(details['ready']).write_bytes(b'ready')
deadline=time.monotonic()+15
while not pathlib.Path(details['release']).exists():
    if time.monotonic()>deadline: raise RuntimeError('synthetic barrier timeout')
    time.sleep(0.01)
with worker._execution(job,'synthetic-owner',None,profile=profile):
    with source_budget.source_scope(profile):
        try:
            result=source_budget.run_source(codec.SourcePlan('source_metadata',details['url']))
            assert result.successful
            print('complete')
        except source_budget.contract.BudgetDeferred as error:
            assert error.reason=='indeterminate'
            print('waiting')
''', encoding="utf-8")
    children = []
    workspace = str(Path(__file__).resolve().parents[1])
    try:
        ready_paths = [tmp_path / ("ready-" + str(index)) for index in range(2)]
        for ready in ready_paths:
            details = {"data": str(config.DATA), "media": str(profile.media), "database": str(profile.database),
                "task_id": task_id, "url": url, "marker": str(marker), "ready": str(ready), "release": str(release)}
            children.append(subprocess.Popen([sys.executable, "-I", str(helper), workspace, json.dumps(details)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        deadline = time.monotonic() + 15
        while not all(path.exists() for path in ready_paths):
            assert time.monotonic() < deadline and all(child.poll() is None for child in children), "Dispatcher failed before barrier"
            time.sleep(0.01)
        release.write_bytes(b"release")
        outcomes = []
        for child in children:
            stdout, stderr = child.communicate(timeout=15)
            assert child.returncode == 0, stderr
            outcomes.append(stdout.strip())
        assert "complete" in outcomes and set(outcomes) <= {"complete", "waiting"}
        assert marker.read_bytes() == b"x"
        con = db.connect(profile.database)
        try:
            attempts = con.execute("SELECT state,response_complete FROM budget_attempt").fetchall()
            assert [(row["state"], row["response_complete"]) for row in attempts] == [("settled", 1)]
        finally:
            con.close()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
