"""Synthetic participant decisions: no network, production data or API writes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import sqlite3
from uuid import uuid4

import pytest

from backend import migration_participant as participant, storage
from backend.capture_policy import SyncError


class Endpoint:
    def __init__(self, path):
        self.path = path
        storage.initialize(path)

    def status(self, switch=None):
        with storage.transaction(self.path, read_only=True) as db:
            return participant.status(db, switch)

    def request(self, command, switch, **fields):
        return dict(request_id=str(uuid4()), switch_id=switch,
            expected_epoch=self.status()['participant_epoch'], **fields)

    def command(self, command, switch=None, *, request=None, install=None, **fields):
        request = request or self.request(command, switch, **fields)
        with storage.transaction(self.path) as db:
            return participant.transition(db, command, request, 1792454400, install=install)

    def chunk(self, switch, data=b'synthetic-bounded-archive', number=0):
        with storage.transaction(self.path) as db:
            return participant.put_chunk(db, switch, expected_epoch=self.status()['participant_epoch'],
                number=number,data=data,sha256_digest=sha256(data).hexdigest())


@pytest.fixture
def pair(tmp_path):
    return Endpoint(tmp_path/'source.sqlite3'), Endpoint(tmp_path/'target.sqlite3'), str(uuid4())


def sealed(pair):
    source, target, switch = pair
    source.command('source_start', switch, peer_id=target.status()['installation_id'])
    source.chunk(switch)
    return source.command('source_seal', switch, dataset_digest='a'*64,
        archive_sha256=sha256(b'synthetic-bounded-archive').hexdigest(), archive_size='25')


def armed(pair):
    source, target, switch = pair
    seal = sealed(pair)
    target.command('destination_start', switch, peer_receipt=seal)
    target.chunk(switch)
    with storage.transaction(target.path) as db:
        participant.set_validated(db, switch, dataset_digest='a'*64)
    return target.command('destination_arm', switch)


def test_identity_is_physical_and_restart_preserves_fence_and_immutable_reply(pair):
    source,target,switch=pair
    assert source.status()['installation_id'] != target.status()['installation_id']
    request=source.request('source_start',switch,peer_id=target.status()['installation_id'])
    first=source.command('source_start',request=request)
    restarted=Endpoint(source.path)
    assert restarted.status()['state']=='export_hold'
    assert restarted.command('source_start',request=request)==first
    assert restarted.status()['participant_epoch']==first['participant_epoch']=='2'
    altered={**request,'peer_id':str(uuid4())}
    with pytest.raises(SyncError): restarted.command('source_start',request=altered)
    assert restarted.status(switch)['receipts']==[first]


def test_handoff_is_irreversible_and_activation_is_atomic_and_idempotent(pair):
    source,target,switch=pair
    ready=armed(pair)
    handoff=source.command('source_handoff',switch,peer_receipt=ready)
    assert source.status()['state']=='handed_off'
    with pytest.raises(SyncError):source.command('source_cancel',switch)
    request=target.request('destination_activate',switch,peer_receipt=handoff)
    def fail(db, operation):
        db.execute("INSERT INTO service_meta VALUES ('fixture-installation','not-committed')")
        raise RuntimeError('synthetic-installation-interruption')
    with pytest.raises(RuntimeError):target.command('destination_activate',request=request,install=fail)
    assert target.status()['state']=='armed'
    with storage.transaction(target.path,read_only=True) as db:
        assert not db.execute("SELECT 1 FROM service_meta WHERE key='fixture-installation'").fetchone()
    def install(db, operation):
        db.execute("INSERT INTO service_meta VALUES ('fixture-installation','committed-once')")
    receipt=target.command('destination_activate',request=request,install=install)
    assert target.command('destination_activate',request=request,install=fail)==receipt
    assert target.status()['state']=='active' and target.status()['awaiting_sync']
    with storage.transaction(target.path,read_only=True) as db:
        assert not participant.blocked(db) and participant.blocked(db,worker=True)
    with pytest.raises(SyncError):source.command('source_resume',switch,peer_receipt=receipt)


@pytest.mark.parametrize('phase',['unsealed','sealed','uploading','armed'])
def test_cancel_must_win_source_before_discard_and_resume(pair,phase):
    source,target,switch=pair
    seal=None
    if phase=='unsealed':source.command('source_start',switch,peer_id=target.status()['installation_id'])
    elif phase=='armed':armed(pair)
    else:seal=sealed(pair)
    if phase=='uploading':target.command('destination_start',switch,peer_receipt=seal)
    cancelled=source.command('source_cancel',switch)
    assert source.status()['state']=='cancel_pending'
    discarded=target.command('destination_discard',switch,peer_receipt=cancelled)
    resumed=source.command('source_resume',switch,peer_receipt=discarded)
    assert resumed['state']=='resumed' and source.status()['state']=='active'
    assert target.status(switch)['operation']['state']=='discarded'
    if seal:
        with pytest.raises(SyncError):target.command('destination_start',switch,peer_receipt=seal)


def test_cancel_and_handoff_race_has_exactly_one_winner(pair):
    source,target,switch=pair
    ready=armed(pair)
    cancel=source.request('source_cancel',switch)
    handoff=source.request('source_handoff',switch,peer_receipt=ready)
    def decide(command,request):
        try:return source.command(command,request=request)
        except SyncError:return None
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(decide,'source_cancel',cancel);b=pool.submit(decide,'source_handoff',handoff)
        results=[a.result(),b.result()]
    assert sum(value is not None for value in results)==1
    assert source.status()['state'] in ('cancel_pending','handed_off')


@pytest.mark.parametrize('field,value',[
    ('dataset_digest','b'*64),('peer_id',str(uuid4())),('switch_id',str(uuid4())),
    ('installation_id',str(uuid4())),('peer_epoch','99'),('state','discarded'),
    ('archive_sha256','c'*64),('archive_size','26'),('request_id','not-uuid'),
])
def test_peer_receipt_substitution_cannot_win_handoff(pair,field,value):
    source,target,switch=pair
    ready=deepcopy(armed(pair));ready[field]=value
    plain={key:item for key,item in ready.items() if key!='receipt_sha256'}
    ready['receipt_sha256']=sha256(participant.canonical(plain)).hexdigest()
    with pytest.raises(SyncError):source.command('source_handoff',switch,peer_receipt=ready)
    assert source.status()['state']=='export_hold'


@pytest.mark.parametrize('epoch',['0','01','1.0','1e0','-1','9223372036854775808',1,True])
def test_protocol_epoch_is_exact_canonical_string(pair,epoch):
    source,target,switch=pair
    request=source.request('source_start',switch,peer_id=target.status()['installation_id']);request['expected_epoch']=epoch
    with pytest.raises(SyncError):source.command('source_start',request=request)
    assert source.status()['participant_epoch']=='1'


def test_upload_ack_survives_restart_and_conflicting_chunk_is_rejected(pair):
    source,target,switch=pair
    source.command('source_start',switch,peer_id=target.status()['installation_id'])
    ack=source.chunk(switch)
    restarted=Endpoint(source.path)
    assert restarted.chunk(switch)==ack
    with pytest.raises(SyncError):restarted.chunk(switch,b'different')
    with storage.transaction(source.path,read_only=True) as db:
        assert participant.archive_details(db,switch)==(sha256(b'synthetic-bounded-archive').hexdigest(),25)


@pytest.mark.parametrize('chunks',[[1],[0,2],[0,1]])
def test_missing_or_short_intermediate_chunks_cannot_be_sealed(pair,chunks):
    source,target,switch=pair
    source.command('source_start',switch,peer_id=target.status()['installation_id'])
    for number in chunks:source.chunk(switch,b'short',number)
    with pytest.raises(SyncError):
        source.command('source_seal',switch,dataset_digest='a'*64,archive_sha256='b'*64,archive_size='10')


@pytest.mark.parametrize('sql',[
    'DELETE FROM migration_installation',
    "UPDATE migration_installation SET installation_id='other'",
    'UPDATE migration_installation SET participant_epoch=1',
    'DELETE FROM migration_operations',
    "UPDATE migration_operations SET peer_id='other'",
    'DELETE FROM migration_receipts',
    "UPDATE migration_receipts SET command='other'",
    "UPDATE migration_chunks SET data=x'00'",
])
def test_permanent_authority_evidence_is_enforced_by_sqlite(pair,sql):
    source,target,switch=pair
    sealed(pair)
    with pytest.raises(sqlite3.IntegrityError),storage.transaction(source.path) as db:db.execute(sql)


def test_used_destination_rejects_import_but_can_acknowledge_unstarted_discard(pair):
    source,target,switch=pair
    with storage.transaction(target.path) as db:db.execute("INSERT INTO service_meta VALUES ('sync_ever_started','1')")
    seal=sealed(pair)
    with pytest.raises(SyncError):target.command('destination_start',switch,peer_receipt=seal)
    cancelled=source.command('source_cancel',switch)
    discarded=target.command('destination_discard',switch,peer_receipt=cancelled)
    source.command('source_resume',switch,peer_receipt=discarded)
    with storage.transaction(target.path,read_only=True) as db:
        assert db.execute("SELECT value FROM service_meta WHERE key='sync_ever_started'").fetchone()[0]=='1'


def test_legacy_endpoints_and_mirror_restart_cannot_clear_participant_hold(tmp_path):
    from delivery_factory import DeliveryHarness
    from backend.mirror import Mirror
    h=DeliveryHarness(tmp_path/'guarded.sqlite3')
    try:
        endpoint=Endpoint(h.path)
        switch=str(uuid4())
        endpoint.command('source_start',switch,peer_id=str(uuid4()))
        with storage.transaction(h.path,read_only=True) as db:
            before=dict(db.execute('SELECT * FROM zen_mirror_state').fetchone())
        replacement=Mirror(h.path,h.worker.mirror.client,'rotated-synthetic-token',h.worker.clock)
        with storage.transaction(h.path,read_only=True) as db:
            assert dict(db.execute('SELECT * FROM zen_mirror_state').fetchone())==before
        for route in ('/api/v1/jobs','/api/v1/connections/discover','/api/v1/migration/resume','/api/v1/migration/export'):
            response=h.client.post(route,json={})
            assert response.json()['error']['code']=='migration_paused',response.text
        assert h.client.get('/api/v1/info').status_code==200
        assert h.client.get('/health').status_code==200
        assert h.client.get('/api/v1/migration/status').json()['paused']
        with pytest.raises(SyncError):replacement.refresh(force=True)
        writes=len(h.remote.writes);h.worker.run_once()
        assert len(h.remote.writes)==writes
        assert endpoint.status()['state']=='export_hold'
    finally:h.close()


def test_sqlite_rejects_reversing_a_committed_handoff(pair):
    source,target,switch=pair
    ready=armed(pair);source.command('source_handoff',switch,peer_receipt=ready)
    with pytest.raises(sqlite3.IntegrityError),storage.transaction(source.path) as db:
        db.execute("UPDATE migration_operations SET state='cancel_pending' WHERE switch_id=?",(switch,))


def test_activation_rechecks_destination_eligibility_before_installer(pair):
    source,target,switch=pair
    ready=armed(pair)
    proof=source.command('source_handoff',switch,peer_receipt=ready)
    with storage.transaction(target.path) as db:
        db.execute("INSERT INTO service_meta VALUES ('sync_ever_started','1')")
    called=[]
    with pytest.raises(SyncError):
        target.command('destination_activate',switch,peer_receipt=proof,install=lambda *_:called.append(True))
    assert not called and target.status()['state']=='armed'


def test_original_source_schema_audit_is_not_replaced_by_destination_runtime(tmp_path):
    from backend.portable_graph import source_tables
    endpoint=Endpoint(tmp_path/'audit.sqlite3')
    with storage.transaction(endpoint.path) as db:
        db.execute('INSERT INTO portable_schema_audit VALUES (7,9007199254740993,NULL)')
        lineage=db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
        rows,_=source_tables(db,lineage)
        assert list(rows['source_schema_migrations'])==[[7,9007199254740993,None]]
        assert db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==storage.SCHEMA_VERSION
