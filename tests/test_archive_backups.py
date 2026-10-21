"""Native server backups with synthetic SDK and private disposable databases."""
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4
import pytest
from backend import storage,portable_codec as codec,portable_graph as graph
from backend.capture_policy import SyncError
from backend.archive_service import ArchiveService
from test_portable_graph import source,snapshot

BASE='/api/v1/migration/archives'


def request(h):
    cap=h.app.state.portable.capabilities()
    return dict(id=str(uuid4()),installation_id=cap['installation_id'],expected_epoch=cap['participant_epoch'])


def test_backup_releases_only_own_hold_preserves_all_facts_and_requires_download_ack(source,tmp_path):
    service=source.app.state.archives;before=snapshot(source)[1];writes=len(source.remote.writes)
    body=request(source);start=source.client.post(BASE+'/backups',json=body)
    assert start.status_code==202,start.text
    assert start.json()['phase']=='building'
    cap=source.app.state.portable.capabilities();assert cap['state']=='export_hold' and cap['operation_id']==body['id']
    assert source.client.post('/api/v1/migration/resume',json={}).status_code==409
    assert source.client.get('/api/v1/connections').status_code==409
    source.worker.run_once();assert len(source.remote.writes)==writes
    assert source.client.post(BASE+'/backups',json=body).json()==start.json()
    assert service.run_once();ready=service.status(body['id']);assert ready['phase']=='ready'
    cap=source.app.state.portable.capabilities();assert cap['state']=='active' and cap['participant_epoch']==str(int(body['expected_epoch'])+2)
    assert snapshot(source)[1]==before and len(source.remote.writes)==writes
    chunks=[]
    for i in range(int(ready['chunks'])):
        response=source.client.get(BASE+'/'+body['id']+'/chunks/'+str(i));assert response.status_code==200
        assert response.headers['x-chunk-sha256']==sha256(response.content).hexdigest();chunks.append(response.content)
    raw=b''.join(chunks);assert sha256(raw).hexdigest()==ready['archive_sha256']
    archive=tmp_path/'download.zip';archive.write_bytes(raw);stage=codec.read_archive(archive,tmp_path/'checked');graph.validate_stage(stage)
    assert {t:list(stage.rows(t)) for t in codec.TABLES}==before
    assert source.client.post(BASE+'/'+body['id']+'/clean',json={}).status_code==409
    assert source.client.post(BASE+'/'+body['id']+'/acknowledge',json={'sha256':'a'*64}).status_code==409
    ack=service.acknowledge(body['id'],ready['archive_sha256']);assert ack['acknowledged_at']
    assert service.acknowledge(body['id'],ready['archive_sha256'])==ack
    calls=0
    while not service.clean(body['id'])['complete']:calls+=1;assert calls<=256
    assert calls==int(ready['chunks']) and snapshot(source)[1]==before
    assert service.status(body['id'])['phase']=='cleaned'
    assert service.begin_backup(request(source))['phase']=='building'


def test_cancel_and_restart_never_resume_another_source_hold(source):
    service=source.app.state.archives;body=request(source);service.begin_backup(body)
    with pytest.raises(SyncError):service.begin_backup(request(source))
    cancelled=service.cancel(body['id']);assert cancelled['phase']=='cancelled'
    assert service.cancel(body['id'])==cancelled and not service.run_once()
    while not service.clean(body['id'])['complete']:pass
    next_request=request(source);service.begin_backup(next_request)
    assert service.cancel(body['id'])==service.status(body['id'])
    assert source.app.state.portable.capabilities()['operation_id']==next_request['id']
    restarted=ArchiveService(source.path,source.app.state.migration_gate,source.worker.mirror,source.worker.clock)
    assert restarted.run_once() and restarted.status(next_request['id'])['phase']=='ready'
    assert source.app.state.portable.capabilities()['state']=='active'


def test_failed_snapshot_stays_paused_and_retries_without_network_send(source,monkeypatch):
    service=source.app.state.archives;body=request(source);service.begin_backup(body);writes=len(source.remote.writes)
    original=codec.create_archive
    monkeypatch.setattr(codec,'create_archive',lambda *a,**k:(_ for _ in ()).throw(OSError('synthetic-private-failure')))
    assert service.run_once();blocked=service.status(body['id']);assert blocked['phase']=='blocked' and blocked['error_code']=='migration_invalid'
    assert source.app.state.portable.capabilities()['state']=='export_hold'
    assert 'synthetic-private-failure' not in json.dumps(blocked)
    monkeypatch.setattr(codec,'create_archive',original);assert service.retry(body['id'])['phase']=='building'
    assert service.run_once() and service.status(body['id'])['phase']=='ready'
    assert len(source.remote.writes)==writes


@pytest.mark.parametrize('field,value',[('expected_epoch','9223372036854775806'),('installation_id',str(uuid4()))])
def test_backup_rejects_exhausted_fence_or_wrong_installation_without_hold(source,field,value):
    service=source.app.state.archives;body=request(source);before=source.app.state.portable.capabilities();body[field]=value
    with pytest.raises(SyncError):service.begin_backup(body)
    assert source.app.state.portable.capabilities()==before


def test_backup_routes_authenticate_and_reject_unknown_fields(source):
    body=request(source)
    for method,path,kwargs in [('post','/backups',dict(json=body)),('get','/'+body['id'],{}),('post','/'+body['id']+'/cancel',dict(json={}))]:
        assert getattr(source.client,method)(BASE+path,headers={'Authorization':'Bearer wrong-synthetic-token'},**kwargs).status_code==401
    assert source.client.post(BASE+'/backups',json=body|{'skip_verification':True}).status_code==422


def test_orphan_scratch_is_installation_scoped_and_cleaned_even_after_cancel(source,tmp_path):
    service=source.app.state.archives;body=request(source);service.begin_backup(body);service.cancel(body['id'])
    prefix='.archive-work-'+body['installation_id']+'-'
    orphan=source.path.parent/(prefix+'interrupted');orphan.mkdir();(orphan/'archive.zip').write_bytes(b'synthetic')
    other=source.path.parent/('.archive-work-'+str(uuid4())+'-other');other.mkdir();(other/'keep').write_text('other installation')
    target=tmp_path/'keep-symlink-target';target.mkdir();(target/'keep').write_text('keep')
    link=source.path.parent/(prefix+'link');link.symlink_to(target,target_is_directory=True)
    restarted=ArchiveService(source.path,source.app.state.migration_gate,source.worker.mirror,source.worker.clock)
    assert not restarted.run_once()
    assert not orphan.exists() and not link.is_symlink()
    assert (target/'keep').read_text()=='keep' and (other/'keep').read_text()=='other installation'
    assert service.status(body['id'])['phase']=='cancelled'


def test_repeated_sigkill_collects_only_own_orphans_and_recovers_exact_snapshot(source,tmp_path):
    import signal,subprocess,sys,time
    service=source.app.state.archives;body=request(source);before=snapshot(source)[1];writes=len(source.remote.writes)
    service.begin_backup(body);prefix='.archive-work-'+body['installation_id']+'-'
    other=tmp_path/'.archive-work-foreign';other.mkdir();(other/'keep').write_text('keep')
    for iteration in range(2):
        marker=tmp_path/('crash-ready-'+str(iteration))
        process=subprocess.Popen([sys.executable,'tests/archive_worker_crash.py',str(source.path),str(marker)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+10
            while not marker.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            assert marker.exists(),('child exited',process.poll())
            process.send_signal(signal.SIGKILL);assert process.wait(timeout=5)==-signal.SIGKILL
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
        assert len(list(source.path.parent.glob(prefix+'*')))==1
        assert service.status(body['id'])['phase']=='building'
        assert source.app.state.portable.capabilities()['state']=='export_hold'
    assert service.run_once() and service.status(body['id'])['phase']=='ready'
    assert not list(source.path.parent.glob(prefix+'*')) and (other/'keep').read_text()=='keep'
    assert snapshot(source)[1]==before and len(source.remote.writes)==writes


def test_v8_upgrade_preserves_financial_and_physical_authority_with_private_backup(source):
    from contextlib import closing
    import sqlite3
    before=snapshot(source)[1]
    with storage.transaction(source.path) as db:
        original=dict(db.execute('SELECT * FROM migration_installation').fetchone())
        for kind,name in sorted(storage.OBJECTS-storage.OBJECTS_V8,key=lambda item:(item[0]!='trigger',item[1])):
            db.execute('DROP '+kind.upper()+' "'+name+'"')
        db.execute('DELETE FROM schema_migrations WHERE version=9');db.execute('PRAGMA user_version=8')
    saved=storage.initialize(source.path);assert saved and saved.stat().st_mode&0o777==0o600
    with closing(storage.connect_readonly(saved)) as db:
        assert storage.validate_owner(db)==8 and dict(db.execute('SELECT * FROM migration_installation').fetchone())==original
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0]==2
    with storage.transaction(source.path,read_only=True) as db:
        assert dict(db.execute('SELECT * FROM migration_installation').fetchone())==original
        assert db.execute('SELECT count(*) FROM archive_records').fetchone()[0]==0
        storage.check_integrity(db)
    after=snapshot(source)[1]
    assert {k:v for k,v in after.items() if k!='source_schema_migrations'}=={k:v for k,v in before.items() if k!='source_schema_migrations'}
