"""Resumable synthetic migration work through the pinned SDK and private staging."""
from hashlib import sha256
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import signal
import subprocess
import sys
import time

import pytest

from backend import storage
from backend.capture_policy import SyncError
from backend.portable_service import PortableMigration
from delivery_factory import DeliveryHarness
from test_portable_graph import source


def service(h):return PortableMigration(h.path,h.app.state.migration_gate,h.worker.mirror,h.worker.clock)


@pytest.fixture
def services(source,tmp_path):
    target=DeliveryHarness(tmp_path/'target.sqlite3')
    a,b=service(source),service(target)
    try:yield source,target,a,b,str(uuid4())
    finally:target.close()


def request(service,command,switch,**fields):
    return dict(request_id=str(uuid4()),switch_id=switch,expected_epoch=service.capabilities()['participant_epoch'],**fields)


def snapshot(values):
    source,target,a,b,switch=values
    accepted=a.command('source_start',request(a,'source_start',switch,peer_id=b.capabilities()['installation_id']))
    assert accepted['work']['state']=='queued'
    assert a.run_once()
    status=a.status(switch)
    assert status['work'][0]['state']=='complete',status
    seal=next(receipt for receipt in status['receipts'] if receipt['command']=='source_seal')
    return seal


def upload(values,seal):
    source,target,a,b,switch=values
    b.command('destination_start',request(b,'destination_start',switch,peer_receipt=seal))
    chunks=[]
    for number in range((int(seal['archive_size'])+262143)//262144):
        data,checksum=a.download(switch,number);chunks.append(data)
        result=b.chunk(switch,b.capabilities()['participant_epoch'],number,data,checksum)
        assert result['sha256']==checksum
    assert sha256(b''.join(chunks)).hexdigest()==seal['archive_sha256']
    return chunks


def validate(values):
    source,target,a,b,switch=values
    ticket=b.finalize(request(b,'destination_finalize',switch))
    assert ticket['state']=='queued' and b.run_once()
    assert b.status(switch)['work'][-1]['state']=='complete',b.status(switch)
    return b.command('destination_arm',request(b,'destination_arm',switch))['receipt']


def handoff(values,armed):
    source,target,a,b,switch=values
    return a.command('source_handoff',request(a,'source_handoff',switch,peer_receipt=armed))['receipt']


def test_full_transfer_is_async_and_never_sends_financial_changes(services):
    source,target,a,b,switch=services
    before=len(source.remote.writes),len(target.remote.writes)
    upload(services,snapshot(services));proof=handoff(services,validate(services))
    activate=request(b,'destination_activate',switch,peer_receipt=proof)
    accepted=b.command('destination_activate',activate)
    assert accepted['work']['state']=='queued' and b.capabilities()['state']=='armed'
    assert b.run_once()
    status=b.status(switch)
    assert status['state']=='active' and status['awaiting_sync']
    assert status['operation']['state']=='activated'
    assert all(task['state']=='complete' for task in status['work'])
    assert b.command('destination_activate',activate)['receipt']['state']=='activated'
    target.worker.run_once();source.worker.run_once()
    assert (len(source.remote.writes),len(target.remote.writes))==before
    assert len(target.remote.reads)>=2  # Independent fresh reads before arm and activation.
    with storage.transaction(target.path,read_only=True) as db:
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM processing_targets').fetchone()[0]==2


@pytest.mark.parametrize('moment',['after_seal','after_activation'])
def test_lost_work_reply_recovers_from_committed_receipt_without_another_api_read(services,moment,monkeypatch):
    source,target,a,b,switch=services
    if moment=='after_seal':
        chosen=a
        a.command('source_start',request(a,'source_start',switch,peer_id=b.capabilities()['installation_id']))
    else:
        upload(services,snapshot(services));proof=handoff(services,validate(services));chosen=b
        b.command('destination_activate',request(b,'destination_activate',switch,peer_receipt=proof))
    original=chosen._perform
    def crash(task):
        original(task)
        raise SystemExit('synthetic-process-death-after-commit')
    monkeypatch.setattr(chosen,'_perform',crash)
    with pytest.raises(SystemExit):chosen.run_once()
    reads=len(source.remote.reads),len(target.remote.reads)
    recovered=service(source if moment=='after_seal' else target)
    assert recovered.run_once()
    assert all(task['state']=='complete' for task in recovered.status(switch)['work'])
    assert (len(source.remote.reads),len(target.remote.reads))==reads


def test_wrong_owner_validation_stays_held_and_can_retry_after_correction(services):
    source,target,a,b,switch=services
    upload(services,snapshot(services))
    target.remote.data['user']=[{'id':2,'login':'other-synthetic-owner'}]
    original=request(b,'destination_finalize',switch)
    ticket=b.finalize(original);b.run_once()
    task=b.status(switch)['work'][0]
    assert task['state']=='blocked' and task['error_code']=='migration_wrong_account'
    assert b.capabilities()['state']=='import_hold' and a.capabilities()['state']=='export_hold'
    assert b.finalize(original)['id']==ticket['id']
    assert not b.run_once()  # No background retry storm against wrong credentials.
    target.remote.data['user']=[{'id':1,'login':'fixture'}]
    assert b.retry(ticket['id'])['state']=='queued'
    assert b.run_once() and b.status(switch)['work'][0]['state']=='complete'
    assert not target.remote.writes


def test_snapshot_failure_preserves_source_hold_until_verified_cancellation(services):
    source,target,a,b,switch=services
    with storage.transaction(source.path) as db:db.execute("INSERT INTO service_meta VALUES ('unknown-future-secret','synthetic')")
    a.command('source_start',request(a,'source_start',switch,peer_id=b.capabilities()['installation_id']))
    a.run_once()
    assert a.status(switch)['work'][0]['state']=='blocked'
    assert a.capabilities()['state']=='export_hold'
    with pytest.raises(SyncError):a.download(switch,0)
    cancel=a.command('source_cancel',request(a,'source_cancel',switch))['receipt']
    discard=b.command('destination_discard',request(b,'destination_discard',switch,peer_receipt=cancel))['receipt']
    a.command('source_resume',request(a,'source_resume',switch,peer_receipt=discard))
    assert a.capabilities()['state']=='active'


def test_work_retry_does_not_change_original_request_binding(services):
    source,target,a,b,switch=services
    upload(services,snapshot(services))
    original=request(b,'destination_finalize',switch)
    first=b.finalize(original)
    assert b.finalize(original)==first
    with pytest.raises(SyncError):b.finalize({**original,'expected_epoch':'999'})
    assert b.capabilities()['state']=='import_hold'


def test_inflight_mirror_response_cannot_mutate_financial_snapshot_after_hold(services):
    from backend.mirror import Mirror
    source,target,a,b,switch=services
    # A separately owned old reader has no shared Python lock with the migration
    # service. The persistent SQL guard must reject its late response as well.
    foreign=Mirror(source.path,source.sdk,'fixture-sdk-token',source.worker.clock)
    entered,release=Event(),Event()
    source.remote.before_read=lambda:(entered.set(),release.wait(10))
    with storage.transaction(source.path,read_only=True) as db:
        before=dict(db.execute('SELECT * FROM zen_mirror_state').fetchone())
    try:
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(foreign.refresh,force=True)
            assert entered.wait(5)
            a.command('source_start',request(a,'source_start',switch,peer_id=b.capabilities()['installation_id']))
            release.set()
            with pytest.raises(SyncError):future.result(timeout=5)
    finally:release.set();source.remote.before_read=None
    with storage.transaction(source.path,read_only=True) as db:
        assert dict(db.execute('SELECT * FROM zen_mirror_state').fetchone())==before


@pytest.mark.parametrize('point',['during-staging','after-seal','after-activation'])
def test_sigkill_recovery_uses_persisted_authority_and_cleans_only_own_scratch(services,tmp_path,point):
    from zenmoney_api.protocol import encode_json
    source,target,a,b,switch=services
    if point=='after-activation':
        upload(services,snapshot(services));proof=handoff(services,validate(services));chosen=b;h=target
        b.command('destination_activate',request(b,'destination_activate',switch,peer_receipt=proof))
    else:
        chosen=a;h=source
        a.command('source_start',request(a,'source_start',switch,peer_id=b.capabilities()['installation_id']))
    response=tmp_path/'read-only-diff.json';marker=tmp_path/'child-ready'
    response.write_bytes(encode_json(h.remote.data|{'transaction':list(h.remote.transactions.values()),'serverTimestamp':h.remote.cursor+1}))
    other=tmp_path/'.portable-work-unrelated';other.mkdir();(other/'keep').write_text('not-owned')
    process=subprocess.Popen([sys.executable,'tests/portable_worker_crash.py',str(h.path),str(response),str(marker),point],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+10
        while not marker.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(0.02)
        assert marker.exists(),('child exited',process.poll())
        process.send_signal(signal.SIGKILL)
        assert process.wait(timeout=5)==-signal.SIGKILL
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
    assert chosen.status(switch)['work'][-1]['state']=='running' or point=='after-activation'
    recovered=service(h)
    assert recovered.run_once()
    assert all(task['state']=='complete' for task in recovered.status(switch)['work'])
    identity=chosen.capabilities()['installation_id']
    assert not list(h.path.parent.glob('.portable-work-'+identity+'-*'))
    assert (other/'keep').read_text()=='not-owned'
    assert not target.remote.writes


def test_source_start_preserves_crashed_send_as_uncertain_without_an_api_retry(tmp_path):
    h=DeliveryHarness(tmp_path/'crashed-send.sqlite3')
    try:
        job,*_=h.capture()
        h.remote.mode='crash_after'
        with pytest.raises(SystemExit):h.worker.run_once()
        assert h.client.post('/api/v1/jobs/'+job['id']+'/stop',json={}).status_code==200
        with storage.transaction(h.path) as db:
            db.execute("DELETE FROM service_meta WHERE key='fixture-network-write-lock-free'")
            assert db.execute('SELECT state FROM deliveries').fetchone()[0]=='attempting'
        before=len(h.remote.writes)
        controller=service(h);switch=str(uuid4())
        controller.command('source_start',request(controller,'source_start',switch,peer_id=str(uuid4())))
        assert controller.run_once()
        assert controller.status(switch)['work'][0]['state']=='complete'
        with storage.transaction(h.path,read_only=True) as db:
            assert db.execute('SELECT state,ever_attempted FROM deliveries').fetchone()[:]==('uncertain',1)
            assert db.execute('SELECT count(*) FROM delivery_attempts').fetchone()[0]==1
        assert len(h.remote.writes)==before
    finally:h.close()


def test_snapshot_task_recovers_success_even_after_coordinator_committed_handoff(services,monkeypatch):
    source,target,a,b,switch=services
    a.command('source_start',request(a,'source_start',switch,peer_id=b.capabilities()['installation_id']))
    original=a._perform
    def crash(task):original(task);raise SystemExit('synthetic-lost-snapshot-reply')
    monkeypatch.setattr(a,'_perform',crash)
    with pytest.raises(SystemExit):a.run_once()
    seal=next(receipt for receipt in a.status(switch)['receipts'] if receipt['command']=='source_seal')
    upload(services,seal);handoff(services,validate(services))
    recovered=service(source)
    assert recovered.run_once()
    assert recovered.status(switch)['work'][0]['state']=='complete'
    assert recovered.capabilities()['state']=='handed_off'
