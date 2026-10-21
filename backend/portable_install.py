"""Atomic financial installation for an already verified, armed participant.

Only packaged table/column/trigger definitions are executable. Archive SQL and
physical participant authority are never imported. This has no HTTP route.
"""
import json

from . import migration_participant as participant, storage
from .portable_codec import CONTRACT, TABLES, _scan_table, require, validate_manifest, canonical
from .portable_graph import quote,metadata_value


def install(db, operation, stage, *, source_hash):
    """Called inside destination_activate's transaction after fresh owner checks.

The caller must validate the full financial graph before invoking the participant
transition. Files are private, but their hashes are checked again before copying.
Any error restores data, schema, authority and receipts through SQL rollback.
"""
    manifest = validate_manifest(stage.manifest)
    require(type(source_hash) is str and len(source_hash) == 64, 'missing_destination_context')
    require(operation['role'] == 'destination' and operation['state'] == 'armed' and operation['validated'], 'not_armed')
    require(operation['dataset_digest'] == manifest['dataset_digest'] and operation['dataset_id'] == manifest['dataset_id'], 'dataset_mismatch')
    control = participant.installation(db)
    require(control['state'] == 'armed' and control['operation_id'] == operation['switch_id'], 'not_armed')
    participant._eligible(db, control, state='armed')
    return _copy_facts(db,stage,source_hash=source_hash,archive_sha256=operation['archive_sha256'],accepted_at=operation['created_at'])


def install_recovery(db,record,stage,*,source_hash,preview_id):
    """Archive-only authority, separately guarded from live peer activation."""
    manifest=validate_manifest(stage.manifest);current=participant.installation(db)
    require(record['kind']=='recovery' and record['phase']=='preview','not_armed')
    require(current['installation_id']==record['installation_id'] and current['state']=='import_hold' and
        current['operation_id']==record['id'] and current['participant_epoch']==record['initial_epoch']+1,'not_armed')
    participant._eligible(db,current,state='import_hold')
    request=json.loads(record['request_json']);preview=json.loads(record['preview_json'])
    require(request['former_writers_retired'] is True and request['accept_post_snapshot_evidence_loss'] is True,'not_armed')
    require(preview['id']==preview_id and preview['archive_sha256']==record['archive_sha256'] and preview['source_hash']==source_hash,'not_armed')
    require(record['manifest_json']==canonical(manifest).decode() and record['dataset_id']==manifest['dataset_id'],'dataset_mismatch')
    return _copy_facts(db,stage,source_hash=source_hash,archive_sha256=record['archive_sha256'],accepted_at=record['created_at'])


def _copy_facts(db,stage,*,source_hash,archive_sha256,accepted_at):
    """Private copier; callers prove distinct live/recovery authority first.

    A temporary read-only preview may also call this with a disposable database;
    it never exposes that database through a running financial service.
    """
    manifest=validate_manifest(stage.manifest)
    require(type(source_hash) is str and len(source_hash)==64,'missing_destination_context')
    for entry in manifest['tables']:
        _scan_table(stage.root/entry['filename'],entry['name'],expected=entry)
    definitions = CONTRACT['schema']['tables']
    # Pre-sync setup/catalog data can be replaced. Permanent financial history
    # cannot: the participant's eligibility check excludes any started sync.
    # Temporarily remove only our packaged financial triggers, in this same SQL
    # transaction, so setup tombstones can be replaced and parent order cannot
    # bypass/reject historical INSERT proofs already checked by graph validation.
    # Runtime authority triggers and destination migration markers stay installed.
    triggers = [trigger for table in definitions.values() for trigger in table['triggers']]
    db.execute('PRAGMA defer_foreign_keys=ON')
    for trigger in triggers:
        db.execute('DROP TRIGGER '+quote(trigger['name']))
    for name in definitions:
        if name != 'schema_migrations':
            db.execute('DELETE FROM '+quote(name))
    db.execute('DELETE FROM portable_schema_audit')
    report = dict(metadata_holds_audited=0,mirror_context_rebound=0,predictions_invalidated=0,unattempted_authorizations_rebound=0)
    for entry in manifest['tables']:
        policy = TABLES[entry['name']]
        target = 'portable_schema_audit' if entry['name'] == 'source_schema_migrations' else policy['source']
        query = 'INSERT INTO '+quote(target)+' ('+','.join(map(quote,policy['columns']))+') VALUES ('+','.join('?' for _ in policy['columns'])+')'
        for original in stage.rows(entry['name']):
            row = list(original)
            if entry['name'] == 'dataset_metadata' and row[0] == 'migration_hold':
                row[0] = 'portable:migration_hold'
                report['metadata_holds_audited'] += 1
            db.execute(query,row)
    generation = db.execute('SELECT generation FROM zen_mirror_state WHERE id=1').fetchone()[0]
    require(generation < 2**63-1,'generation_overflow')
    report['mirror_context_rebound'] = db.execute('''UPDATE zen_mirror_state SET source_hash=?,cursor=0,generation=generation+1,
        needs_full=1,ready=0,refreshed_at=NULL,error_code=NULL WHERE id=1''',(source_hash,)).rowcount
    report['unattempted_authorizations_rebound'] = db.execute('''UPDATE delivery_authorizations SET source_hash=?
        WHERE bank_id NOT IN (SELECT bank_id FROM deliveries WHERE ever_attempted=1)''',(source_hash,)).rowcount
    db.execute("DELETE FROM service_meta WHERE key='zenmoney_refreshed_at'")
    previous=db.execute("SELECT value FROM service_meta WHERE key='portable_imports'").fetchone()
    history=json.loads(previous[0]) if previous else []
    history.append(dict(archive_id=manifest['archive_id'],archive_sha256=archive_sha256,
        dataset_digest=manifest['dataset_digest'],export_session_id=manifest['export_session_id'],accepted_at=str(accepted_at)))
    raw=json.dumps(history,ensure_ascii=True,separators=(',',':'))
    metadata_value('portable_imports',raw,manifest['dataset_id'])
    db.execute("INSERT OR REPLACE INTO service_meta VALUES ('portable_imports',?)",(raw,))
    report['import_provenance_appended']=1
    report['predictions_invalidated'] = db.execute('DELETE FROM category_predictions').rowcount
    for trigger in triggers:
        db.execute(trigger['sql'])
    storage.validate_owner(db,allow_upgrade=False)
    storage.check_integrity(db)
    return report
