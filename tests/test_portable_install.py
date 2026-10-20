"""Installation is atomic with authority consumption; all account data is synthetic."""
from hashlib import sha256
from uuid import uuid4
import sqlite3
import json

import pytest

from backend import portable_codec as codec, portable_graph as graph, portable_install, storage
from backend import migration_participant as participant
from test_portable_graph import source, snapshot
from test_migration_participant import Endpoint


def prepare(source,tmp_path,*,audit_hold=False,empty_schema_audit=False):
    origin=Endpoint(source.path);target=Endpoint(tmp_path/'target.sqlite3');switch=str(uuid4())
    origin.command('source_start',switch,peer_id=target.status()['installation_id'])
    lineage,rows,_=snapshot(source)
    if empty_schema_audit:rows['source_schema_migrations']=[]
    if audit_hold:
        rows['dataset_metadata'].append(['migration_hold','exported'])
        rows['dataset_metadata'].sort(key=lambda row:row[0].encode())
    archive=tmp_path/'financial.zip'
    manifest=codec.create_archive(archive,rows,dataset_id=lineage,owners=['1'],session_id=switch,
        archive_id=str(uuid4()),created_at='2026-10-21T00:00:00Z',version='synthetic')
    raw=archive.read_bytes()
    for number,start in enumerate(range(0,len(raw),262144)):origin.chunk(switch,raw[start:start+262144],number)
    seal=origin.command('source_seal',switch,dataset_digest=manifest['dataset_digest'],archive_sha256=sha256(raw).hexdigest(),archive_size=str(len(raw)))
    target.command('destination_start',switch,peer_receipt=seal)
    for number,start in enumerate(range(0,len(raw),262144)):target.chunk(switch,raw[start:start+262144],number)
    stage=codec.read_archive(archive,tmp_path/'verified-stage')
    graph.validate_stage(stage,expected_owners=['1'])
    with storage.transaction(target.path) as db:
        participant.set_validated(db,switch,dataset_digest=manifest['dataset_digest'])
    armed=target.command('destination_arm',switch)
    handoff=origin.command('source_handoff',switch,peer_receipt=armed)
    return origin,target,switch,rows,stage,handoff


@pytest.mark.parametrize('audit_hold',[False,True])
def test_complete_graph_installation_preserves_journal_and_only_normalizes_runtime(source,tmp_path,audit_hold):
    origin,target,switch,before,stage,proof=prepare(source,tmp_path,audit_hold=audit_hold)
    original_target=target.status()['installation_id']
    with storage.transaction(target.path) as db:
        db.execute("INSERT INTO zen_tombstones VALUES ('transaction','pre-sync-other-context','hard_delete','{}')")
    reports=[]
    def install(db,operation):reports.append(portable_install.install(db,operation,stage,source_hash='f'*64))
    result=target.command('destination_activate',switch,peer_receipt=proof,install=install)
    assert result['state']=='activated' and reports[0]['metadata_holds_audited']==int(audit_hold)
    assert target.status()['installation_id']==original_target!=origin.status()['installation_id']
    assert target.status()['awaiting_sync'] and origin.status()['state']=='handed_off'
    with storage.transaction(target.path,read_only=True) as db:
        lineage=db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
        values,_=graph.source_tables(db,lineage)
        after={name:list(rows) for name,rows in values.items()}
        assert participant.blocked(db,worker=True)
        assert not db.execute("SELECT 1 FROM service_meta WHERE key='migration_hold'").fetchone()
        assert db.execute('SELECT source_hash,cursor,needs_full,ready FROM zen_mirror_state').fetchone()[:]==('f'*64,0,1,0)
        assert db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==storage.SCHEMA_VERSION
        assert len(participant.status(db,switch)['receipts'])==3
        storage.check_integrity(db)
    for name,rows in before.items():
        if name not in ('dataset_metadata','zen_mirror_state','category_predictions','delivery_authorizations'):
            assert after[name]==rows,name
    assert after['delivery_authorizations']==before['delivery_authorizations']  # Already attempted: never rebind.
    assert [row for row in after['dataset_metadata'] if row[0]!='portable_imports']==[row for row in before['dataset_metadata'] if row[0]!='zenmoney_refreshed_at']
    provenance=json.loads(dict(after['dataset_metadata'])['portable_imports'])
    assert len(provenance)==1 and provenance[0]['archive_id']==stage.manifest['archive_id']
    assert provenance[0]['dataset_digest']==stage.manifest['dataset_digest']
    assert not after['category_predictions']
    assert stage.manifest['dataset_digest']==graph.validate_stage(stage).dataset_digest
    for sql in ("UPDATE deliveries SET payload='{}'",'DELETE FROM processing_receipts','DELETE FROM bank_versions'):
        with pytest.raises(sqlite3.IntegrityError),storage.transaction(target.path) as db:db.execute(sql)


def test_file_changed_after_validation_cannot_install_or_consume_handoff(source,tmp_path):
    _,target,switch,_,stage,proof=prepare(source,tmp_path)
    (stage.root/'tables/bank_transactions.ndjson').write_bytes(b'[]\n')
    before=target.status(switch)
    with pytest.raises(codec.PortableError):
        target.command('destination_activate',switch,peer_receipt=proof,
            install=lambda db,operation:portable_install.install(db,operation,stage,source_hash='f'*64))
    assert target.status(switch)==before
    with storage.transaction(target.path,read_only=True) as db:
        assert not db.execute('SELECT 1 FROM bank_transactions').fetchone()


def test_an_empty_original_schema_audit_is_not_replaced_by_destination_runtime_history(source,tmp_path):
    _,target,switch,_,stage,proof=prepare(source,tmp_path,empty_schema_audit=True)
    target.command('destination_activate',switch,peer_receipt=proof,
        install=lambda db,operation:portable_install.install(db,operation,stage,source_hash='f'*64))
    with storage.transaction(target.path,read_only=True) as db:
        assert db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==storage.SCHEMA_VERSION
        assert not db.execute('SELECT 1 FROM portable_schema_audit').fetchone()
        rows,_=graph.source_tables(db,stage.manifest['dataset_id'])
        assert list(rows['source_schema_migrations'])==[]


def test_a_never_imported_server_still_exports_its_own_schema_audit(tmp_path):
    endpoint=Endpoint(tmp_path/'fresh.sqlite3')
    with storage.transaction(endpoint.path) as db:
        db.execute("INSERT OR REPLACE INTO service_meta VALUES ('portable_imports','[]')")
        lineage=db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
        rows,_=graph.source_tables(db,lineage)
        audit=list(rows['source_schema_migrations'])
        assert audit and audit[-1][0]==storage.SCHEMA_VERSION
        assert all(row[2] is None for row in audit)


def test_install_failure_after_table_copy_rolls_back_schema_and_source_facts(source,tmp_path,monkeypatch):
    _,target,switch,_,stage,proof=prepare(source,tmp_path)
    before=target.status(switch)
    def fail(_):raise RuntimeError('synthetic-post-copy-crash')
    monkeypatch.setattr(storage,'check_integrity',fail)
    with pytest.raises(RuntimeError,match='synthetic-post-copy-crash'):
        target.command('destination_activate',switch,peer_receipt=proof,
            install=lambda db,operation:portable_install.install(db,operation,stage,source_hash='f'*64))
    assert target.status(switch)==before
    with storage.transaction(target.path,read_only=True) as db:
        storage.validate_owner(db,allow_upgrade=False)
        assert not db.execute('SELECT 1 FROM bank_transactions').fetchone()
        assert db.execute('SELECT ready FROM zen_mirror_state').fetchone()[0]==0


def test_reexport_and_second_migration_preserve_original_import_provenance(source,tmp_path):
    with storage.transaction(source.path,read_only=True) as db:
        attempts=[tuple(row) for row in db.execute('SELECT * FROM delivery_attempts ORDER BY id')]
    _,target,switch,_,stage,proof=prepare(source,tmp_path)
    target.command('destination_activate',switch,peer_receipt=proof,
        install=lambda db,operation:portable_install.install(db,operation,stage,source_hash='f'*64))
    with storage.transaction(target.path) as db:
        first=json.loads(db.execute("SELECT value FROM service_meta WHERE key='portable_imports'").fetchone()[0])
        # This isolated installer test emulates the ordinary explicit Sync fence
        # acknowledgment. End-to-end browser mode switching is tested in L10.
        db.execute('UPDATE migration_installation SET awaiting_sync=0 WHERE id=1')
    _,third,next_switch,_,next_stage,next_proof=prepare(target,tmp_path/'next-server')
    third.command('destination_activate',next_switch,peer_receipt=next_proof,
        install=lambda db,operation:portable_install.install(db,operation,next_stage,source_hash='e'*64))
    with storage.transaction(third.path,read_only=True) as db:
        history=json.loads(db.execute("SELECT value FROM service_meta WHERE key='portable_imports'").fetchone()[0])
        assert history[:-1]==first and len(history)==2
        assert history[-1]['archive_id']==next_stage.manifest['archive_id']
        assert [tuple(row) for row in db.execute('SELECT * FROM delivery_attempts ORDER BY id')]==attempts
