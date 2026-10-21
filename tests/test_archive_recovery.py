"""Archive-only restore gates, synthetic SDK; no live peer/handoff simulation."""
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4
from base64 import b64encode
import pytest
from backend import storage,portable_codec as codec,migration_participant as participant
from backend.archive_service import ArchiveService
from backend.capture_policy import SyncError
from delivery_factory import DeliveryHarness
from test_portable_graph import source,snapshot

BASE='/api/v1/migration/archives'


@pytest.fixture
def recovery(source,tmp_path):
    before=snapshot(source)[1];service=source.app.state.archives;cap=source.app.state.portable.capabilities();id=str(uuid4())
    service.begin_backup(dict(id=id,installation_id=cap['installation_id'],expected_epoch=cap['participant_epoch']));assert service.run_once()
    info=service.status(id);raw=b''.join(service.download(id,i)[0] for i in range(int(info['chunks'])))
    target=DeliveryHarness(tmp_path/'target.sqlite3');target.remote.transactions=deepcopy(source.remote.transactions)
    cap=target.app.state.portable.capabilities()
    body=dict(id=str(uuid4()),installation_id=cap['installation_id'],expected_epoch=cap['participant_epoch'],dataset_id=info['dataset_id'],
        archive_sha256=info['archive_sha256'],archive_size=info['archive_size'],owner_ids=['1'],former_writers_retired=True,accept_post_snapshot_evidence_loss=True)
    # Only the copied archive remains available to the destination; the source
    # worker/client is explicitly stopped before asserting retirement.
    source.close()
    try:yield source,target,body,raw,before
    finally:target.close()


def upload(target,body,raw):
    for i,start in enumerate(range(0,len(raw),262144)):
        part=raw[start:start+262144];response=target.client.put(BASE+'/'+body['id']+'/chunks/'+str(i),json=dict(data=b64encode(part).decode(),sha256=sha256(part).hexdigest()))
        assert response.status_code==200,response.text


def preview(target,body,raw):
    response=target.client.post(BASE+'/recoveries',json=body);assert response.status_code==202,response.text
    upload(target,body,raw);service=target.app.state.archives
    assert service.finalize_recovery(body['id'])['phase']=='validating' and service.run_once()
    assert service.status(body['id'])['phase']=='preview',service.status(body['id'])
    return service.preview(body['id'])


def test_archive_restore_preserves_authority_and_journal_then_waits_for_explicit_sync(recovery):
    source,target,body,raw,before=recovery;service=target.app.state.archives;proof=preview(target,body,raw)
    assert proof['bank_transactions']=='2' and proof['zen_transactions']=='2' and len(proof['sdk_sha256'])==64
    assert proof['missing_bank']==0 and proof['extra_zen']==0
    assert target.app.state.portable.capabilities()['state']=='import_hold'
    with storage.transaction(target.path,read_only=True) as db:
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM migration_receipts').fetchone()[0]==0
    assert not target.remote.writes
    response=target.client.post(BASE+'/'+body['id']+'/activate',json=dict(preview_id=proof['id'],accept_summary=True));assert response.status_code==200,response.text
    active=response.json();assert active['phase']=='activated'
    cap=target.app.state.portable.capabilities();assert cap['state']=='active' and cap['awaiting_sync']
    assert cap['installation_id']==body['installation_id'] and cap['dataset_id']==body['dataset_id']
    assert cap['participant_epoch']==str(int(body['expected_epoch'])+2)
    assert service.activate_recovery(body['id'],proof['id'],True)==active
    with pytest.raises(SyncError):service.cancel(body['id'])
    with storage.transaction(target.path,read_only=True) as db:
        assert db.execute('SELECT count(*) FROM migration_receipts').fetchone()[0]==0
        for table in ['bank_transactions','bank_versions','deliveries','delivery_attempts','processing_receipts','processing_targets']:
            policy=codec.TABLES[table]
            values=[list(row) for row in db.execute('SELECT '+','.join(policy['columns'])+' FROM '+policy['source'])]
            assert sorted(values,key=str)==sorted(before[table],key=str),table
    target.worker.run_once();assert not target.remote.writes
    assert service.acknowledge(body['id'],body['archive_sha256'])['acknowledged_at']
    while not service.clean(body['id'])['complete']:pass
    assert service.activate_recovery(body['id'],proof['id'],True)['phase']=='activated'
    assert target.app.state.portable.capabilities()==cap
    # Cache cleanup never rewrites activation evidence. Once an explicit sync
    # acknowledges the imported writer, a new backup can reserve its own hold.
    job=target.start(source.connection['id']);assert job.status_code==202,job.text
    assert target.client.post('/api/v1/jobs/'+job.json()['id']+'/stop',json={}).status_code==200
    cap=target.app.state.portable.capabilities()
    assert service.begin_backup(dict(id=str(uuid4()),installation_id=cap['installation_id'],expected_epoch=cap['participant_epoch']))['phase']=='building'


@pytest.mark.parametrize('change',[{'former_writers_retired':False},{'accept_post_snapshot_evidence_loss':False},{'owner_ids':['2']}])
def test_restore_missing_declarations_and_wrong_owner_never_reserve(recovery,change):
    source,target,body,raw,before=recovery;cap=target.app.state.portable.capabilities()
    result=target.client.post(BASE+'/recoveries',json=body|change);assert result.status_code==409,result.text
    assert target.app.state.portable.capabilities()==cap and not target.remote.writes


def test_incomplete_conflicting_and_cancelled_uploads_never_activate_or_change_source(recovery):
    source,target,body,raw,before=recovery;service=target.app.state.archives
    initial=service.begin_recovery(body);assert service.begin_recovery(body)==initial
    with pytest.raises(SyncError):service.finalize_recovery(body['id'])
    upload(target,body,raw);upload(target,body,raw)
    different=b'x'*min(len(raw),262144)
    with pytest.raises(SyncError):service.upload(body['id'],0,different,sha256(different).hexdigest())
    cancelled=service.cancel(body['id']);assert cancelled['phase']=='cancelled'
    assert service.cancel(body['id'])==cancelled and not service.run_once()
    with pytest.raises(SyncError):service.finalize_recovery(body['id'])
    while not service.clean(body['id'])['complete']:pass
    assert target.app.state.portable.capabilities()['state']=='active'
    assert snapshot(source)[1]==before and not target.remote.writes


def test_recovery_preview_rejects_offline_expired_or_unaccepted_activation_and_rebuilds(recovery):
    source,target,body,raw,before=recovery;service=target.app.state.archives;proof=preview(target,body,raw)
    with pytest.raises(SyncError):service.activate_recovery(body['id'],proof['id'],False)
    target.now+=301
    with pytest.raises(SyncError):service.activate_recovery(body['id'],proof['id'],True)
    assert service.finalize_recovery(body['id'])['phase']=='validating' and service.run_once()
    fresh=service.preview(body['id']);assert fresh['id']!=proof['id']
    target.remote.read_failure=503
    with pytest.raises(SyncError) as failure:service.activate_recovery(body['id'],fresh['id'],True)
    assert failure.value.code=='zenmoney_unavailable' and failure.value.status==503
    assert service.status(body['id'])['phase']=='preview' and target.app.state.portable.capabilities()['state']=='import_hold'
    target.remote.read_failure=None
    assert service.activate_recovery(body['id'],fresh['id'],True)['phase']=='activated' and not target.remote.writes


def test_used_destination_cannot_be_overwritten_by_recovery(recovery):
    source,target,body,raw,before=recovery
    job,*_=target.capture(count=1)
    with pytest.raises(SyncError):target.app.state.archives.begin_recovery(body)
    with storage.transaction(target.path,read_only=True) as db:assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0]==1
    assert target.app.state.portable.capabilities()['state']=='active'


def test_cancelled_preview_cleanup_is_not_a_successful_activation(recovery):
    source,target,body,raw,before=recovery;service=target.app.state.archives;proof=preview(target,body,raw)
    service.cancel(body['id'])
    while not service.clean(body['id'])['complete']:pass
    with pytest.raises(SyncError):service.activate_recovery(body['id'],proof['id'],True)
    assert not target.app.state.portable.capabilities()['awaiting_sync'] and not target.remote.writes
    with storage.transaction(target.path,read_only=True) as db:assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0]==0


@pytest.mark.parametrize('point',['sdk-read','copy'])
def test_preview_expiry_during_activation_rolls_back_all_facts_and_authority(recovery,monkeypatch,point):
    from backend import portable_install
    source,target,body,raw,before=recovery;service=target.app.state.archives;proof=preview(target,body,raw)
    target.now=int(proof['checked_at'])+299
    if point=='sdk-read':target.remote.before_read=lambda:setattr(target,'now',target.now+2)
    else:
        original=portable_install.install_recovery
        def delayed(*args,**kwargs):
            result=original(*args,**kwargs);target.now+=2;return result
        monkeypatch.setattr(portable_install,'install_recovery',delayed)
    with pytest.raises(SyncError):service.activate_recovery(body['id'],proof['id'],True)
    assert service.status(body['id'])['phase']=='preview'
    cap=target.app.state.portable.capabilities();assert cap['state']=='import_hold' and not cap['awaiting_sync']
    with storage.transaction(target.path,read_only=True) as db:
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0]==0
        storage.check_integrity(db)
    assert not target.remote.writes


@pytest.mark.parametrize('point',['projection','summary'])
def test_summary_age_and_digest_refer_to_the_actual_sdk_snapshot(recovery,monkeypatch,point):
    from backend import portable_install,mirror
    from zenmoney_api.protocol import decode_diff
    import json
    source,target,body,raw,before=recovery;service=target.app.state.archives
    original=target.worker.mirror.client.read_diff;responses=[]
    def spaced(*args,**kwargs):
        diff=original(*args,**kwargs);data=json.dumps(json.loads(diff.raw_json),indent=2,ensure_ascii=False).encode()
        responses.append((data,target.now));return decode_diff(data,0)
    monkeypatch.setattr(target.worker.mirror.client,'read_diff',spaced)
    copy=portable_install._copy_facts
    def slow_summary(*args,**kwargs):
        result=copy(*args,**kwargs);target.now+=301;return result
    if point=='summary':monkeypatch.setattr(portable_install,'_copy_facts',slow_summary)
    else:
        apply=mirror.apply_diff
        def slow_projection(*args,**kwargs):
            result=apply(*args,**kwargs);target.now+=301;return result
        monkeypatch.setattr(mirror,'apply_diff',slow_projection)
    proof=preview(target,body,raw)
    assert proof['checked_at']==str(responses[-1][1]) and proof['sdk_sha256']==sha256(responses[-1][0]).hexdigest()
    with pytest.raises(SyncError):service.activate_recovery(body['id'],proof['id'],True)
    assert target.app.state.portable.capabilities()['state']=='import_hold'


def test_tombstoned_reappearance_is_excluded_from_summary_like_reports(recovery,tmp_path):
    source,target,body,raw,before=recovery
    incoming=tmp_path/'original.zip';incoming.write_bytes(raw);stage=codec.read_archive(incoming,tmp_path/'original-stage')
    rows={name:list(stage.rows(name)) for name in codec.TABLES}
    identifier=next(iter(target.remote.transactions))
    rows['zen_tombstones'].append(['transaction',identifier,'hard_delete','{}'])
    rows['zen_tombstones'].sort(key=lambda row:(row[0].encode(),row[1].encode()))
    changed=tmp_path/'with-tombstone.zip';manifest=stage.manifest
    codec.create_archive(changed,rows,dataset_id=manifest['dataset_id'],owners=['1'],session_id=manifest['export_session_id'],
        archive_id=str(uuid4()),created_at=manifest['created_at'],version='synthetic-tombstone')
    data=changed.read_bytes();body=body|dict(archive_sha256=sha256(data).hexdigest(),archive_size=str(len(data)))
    proof=preview(target,body,data)
    assert proof['zen_transactions']=='1' and proof['missing_bank']==1
    assert not target.remote.writes


def test_malformed_fresh_projection_is_a_retryable_sdk_error_without_hold(recovery,monkeypatch):
    from types import SimpleNamespace
    import json
    source,target,body,raw,before=recovery
    monkeypatch.setattr(target.worker.mirror.client,'read_diff',lambda *a,**k:SimpleNamespace(raw_json=json.dumps({'serverTimestamp':100})))
    response=target.client.post(BASE+'/recoveries',json=body)
    assert response.status_code==503,response.text
    assert response.json()['error']['code']=='zenmoney_unavailable'
    assert target.app.state.portable.capabilities()['state']=='active'


def test_recovery_survives_sigkill_validation_and_lost_activation_response(recovery,tmp_path):
    import signal,subprocess,sys,time
    from zenmoney_api.protocol import encode_json
    source,target,body,raw,before=recovery;service=target.app.state.archives
    service.begin_recovery(body);upload(target,body,raw);service.finalize_recovery(body['id'])
    response=tmp_path/'read-only-sdk.json'
    response.write_bytes(encode_json(target.remote.data|{'transaction':list(target.remote.transactions.values()),'deletion':[],'serverTimestamp':target.remote.cursor+1}))
    prefix='.archive-work-'+body['installation_id']+'-'
    proof=None
    for point in ['during-validation','after-activation']:
        marker=tmp_path/point
        process=subprocess.Popen([__import__('sys').executable,'tests/archive_recovery_crash.py',str(target.path),str(response),str(marker),point,body['id'],proof['id'] if proof else 'unused',str(target.now)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+10
            while not marker.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            assert marker.exists(),(point,'child exited',process.poll())
            process.send_signal(signal.SIGKILL);assert process.wait(timeout=5)==-signal.SIGKILL
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
        restarted=ArchiveService(target.path,target.app.state.migration_gate,target.worker.mirror,target.worker.clock)
        if point=='during-validation':
            assert restarted.status(body['id'])['phase']=='validating'
            assert restarted.run_once();proof=restarted.preview(body['id'])
            assert restarted.status(body['id'])['phase']=='preview'
        else:
            assert restarted.status(body['id'])['phase']=='activated'
            assert restarted.activate_recovery(body['id'],proof['id'],True)['phase']=='activated'
            assert target.app.state.portable.capabilities()['awaiting_sync']
        assert not list(target.path.parent.glob(prefix+'*'))
        assert not target.remote.writes


def test_failed_activation_transaction_retains_empty_destination_and_original_preview(recovery,monkeypatch):
    import sqlite3
    from backend import portable_install
    source,target,body,raw,before=recovery;service=target.app.state.archives;proof=preview(target,body,raw)
    original=portable_install.install_recovery
    def full_disk(*args,**kwargs):
        original(*args,**kwargs);raise sqlite3.OperationalError('synthetic-disk-full')
    monkeypatch.setattr(portable_install,'install_recovery',full_disk)
    with pytest.raises(sqlite3.OperationalError):service.activate_recovery(body['id'],proof['id'],True)
    with storage.transaction(target.path,read_only=True) as db:
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0]==0
        assert db.execute('SELECT state FROM migration_installation').fetchone()[0]=='import_hold'
        storage.check_integrity(db)
    assert service.preview(body['id'])==proof
    monkeypatch.setattr(portable_install,'install_recovery',original)
    assert service.activate_recovery(body['id'],proof['id'],True)['phase']=='activated'
    assert not target.remote.writes


def test_unused_recovery_withdrawal_permanently_fences_a_delayed_begin(recovery):
    _,target,body,_,_=recovery;before=target.app.state.portable.capabilities()
    response=target.client.post(BASE+'/recoveries/withdraw',json=body);assert response.status_code==200,response.text
    proof=response.json();assert proof==dict(withdrawal_version='1',decision='withdrawn',id=body['id'],
        request_sha256=sha256(participant.canonical(body)).hexdigest(),
        installation_id=body['installation_id'],dataset_id=before['dataset_id'],participant_epoch=before['participant_epoch'])
    assert target.app.state.portable.capabilities()==before
    for _ in range(2):
        late=target.client.post(BASE+'/recoveries',json=body);assert late.status_code==202 and late.json()['phase']=='cleaned',late.text
        assert target.client.post(BASE+'/recoveries/withdraw',json=body).json()==proof
    assert target.app.state.portable.capabilities()==before and not target.remote.writes
    assert target.client.post(BASE+'/'+body['id']+'/finalize',json={}).status_code==409
    retried=target.client.post(BASE+'/'+body['id']+'/retry',json={});assert retried.status_code==202 and retried.json()['phase']=='cleaned'
    assert target.client.post(BASE+'/'+body['id']+'/activate',json=dict(preview_id=str(uuid4()),accept_summary=True)).status_code==409


def test_withdrawal_of_an_owned_hold_cancels_atomically_and_never_cancels_another_hold(recovery):
    _,target,body,_,_=recovery;service=target.app.state.archives
    service.begin_recovery(body);proof=service.withdraw_recovery(body)
    assert service.status(body['id'])['phase']=='cancelled'
    assert proof['participant_epoch']==str(int(body['expected_epoch'])+2)
    assert target.app.state.portable.capabilities()['state']=='active'
    while not service.clean(body['id'])['complete']:pass
    cap=target.app.state.portable.capabilities()
    foreign=body|dict(id=str(uuid4()),expected_epoch=cap['participant_epoch'])
    service.begin_recovery(foreign);held=target.app.state.portable.capabilities()
    # Lost cancellation replies can be retried while another operation owns hold.
    again=service.withdraw_recovery(body);assert again['decision']=='withdrawn'
    assert target.app.state.portable.capabilities()==held
    unused=body|dict(id=str(uuid4()))
    assert service.withdraw_recovery(unused)['decision']=='withdrawn'
    assert target.app.state.portable.capabilities()==held and not target.remote.writes


def test_withdrawal_rejects_conflicts_and_permanent_activation(recovery):
    _,target,body,raw,_=recovery;service=target.app.state.archives
    unused=body|dict(id=str(uuid4()));service.withdraw_recovery(unused)
    response=target.client.post(BASE+'/recoveries/withdraw',json=unused|dict(archive_sha256='f'*64));assert response.status_code==422
    summary=preview(target,body,raw);service.activate_recovery(body['id'],summary['id'],True)
    before=target.app.state.portable.capabilities()
    response=target.client.post(BASE+'/recoveries/withdraw',json=body);assert response.status_code==409
    assert target.app.state.portable.capabilities()==before and not target.remote.writes


def test_withdrawal_and_delayed_begin_serialize_to_a_permanent_non_writable_outcome(recovery):
    from concurrent.futures import ThreadPoolExecutor
    _,target,body,_,_=recovery;service=target.app.state.archives
    with ThreadPoolExecutor(max_workers=2) as pool:
        def attempt(operation):
            try:return operation(deepcopy(body))
            except SyncError as error:
                assert error.code=='migration_busy';return {'busy':True}
        begin=pool.submit(attempt,service.begin_recovery)
        withdraw=pool.submit(attempt,service.withdraw_recovery)
        first,second=begin.result(),withdraw.result()
        assert first.get('busy') or first['phase'] in ('uploading','cleaned')
        assert second.get('busy') or second['decision']=='withdrawn'
    assert service.withdraw_recovery(body)['decision']=='withdrawn'
    assert service.status(body['id'])['phase'] in ('cancelled','cleaned')
    assert target.app.state.portable.capabilities()['state']=='active'
    assert service.begin_recovery(body)['phase'] in ('cancelled','cleaned')
    assert not service.run_once() and not target.remote.writes


@pytest.mark.parametrize('reserved',[False,True])
def test_withdrawal_sql_failure_cannot_partially_cancel_or_publish_a_tombstone(recovery,reserved,monkeypatch):
    import sqlite3
    _,target,body,_,_=recovery;service=target.app.state.archives
    if reserved:service.begin_recovery(body)
    before=target.app.state.portable.capabilities()
    from contextlib import contextmanager
    original=storage.transaction;armed=True
    @contextmanager
    def transaction(path,*,read_only=False):
        nonlocal armed
        with original(path,read_only=read_only) as db:
            if armed and not read_only:
                armed=False
                trigger="BEFORE UPDATE ON migration_installation" if reserved else "BEFORE INSERT ON archive_records"
                db.execute("CREATE TRIGGER synthetic_withdraw_failure "+trigger+" BEGIN SELECT RAISE(ABORT,'synthetic withdrawal failure'); END")
            yield db
    monkeypatch.setattr(storage,'transaction',transaction)
    with pytest.raises(sqlite3.IntegrityError):service.withdraw_recovery(body)
    assert target.app.state.portable.capabilities()==before
    with storage.transaction(target.path) as db:
        row=db.execute('SELECT phase FROM archive_records WHERE id=?',(body['id'],)).fetchone()
        assert row is not None and row[0]=='uploading' if reserved else row is None
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='synthetic_withdraw_failure'").fetchone()[0]==0
    assert service.withdraw_recovery(body)['decision']=='withdrawn' and not target.remote.writes
