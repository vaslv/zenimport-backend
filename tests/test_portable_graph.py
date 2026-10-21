"""Private graph validation, with no live token or installed-dataset mutation."""
from copy import deepcopy
from contextlib import closing
from hashlib import sha256
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from backend import portable_codec as codec, portable_graph as graph, storage
from delivery_factory import DeliveryHarness


@pytest.fixture
def source(tmp_path):
    harness = DeliveryHarness(tmp_path/'source.sqlite3')
    job, *_ = harness.capture(count=2)
    harness.due()
    assert harness.client.post('/api/v1/jobs/'+job['id']+'/stop', json={}).status_code == 200
    # The fake writes this assertion-only marker while proving network I/O runs
    # outside SQLite locks. It is deliberately not a portable production key.
    with storage.transaction(harness.path) as db:
        db.execute("DELETE FROM service_meta WHERE key='fixture-network-write-lock-free'")
    try:
        yield harness
    finally:
        harness.close()


def snapshot(source):
    with storage.transaction(source.path, read_only=True) as db:
        lineage = db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
        rows, normalized = graph.source_tables(db, lineage)
        return lineage, {name: list(values) for name, values in rows.items()}, normalized


def stage(tmp_path, lineage, rows):
    archive = tmp_path/(str(uuid4())+'.zip')
    codec.create_archive(archive, rows, dataset_id=lineage, owners=['1'], session_id=str(uuid4()),
        archive_id=str(uuid4()), created_at='2026-10-20T00:00:00Z', version='test-only')
    return codec.read_archive(archive, tmp_path/str(uuid4()))


def offset(name, column):
    return codec.TABLES[name]['columns'].index(column)


def test_source_projection_and_graph_preserve_a_complete_attempted_journal(source, tmp_path):
    lineage, rows, normalized = snapshot(source)
    data = stage(tmp_path, lineage, rows)
    summary = graph.validate_stage(data, expected_owners=['1'])
    assert summary.dataset_id == lineage and summary.declared_owners == ('1',)
    assert dict(summary.counts)['bank_transactions'] == 2
    assert dict(summary.counts)['delivery_attempts'] >= 2
    assert dict(summary.counts)['processing_targets'] == 2
    assert all(value == 0 for value in normalized.values())
    assert all(row[offset('capture_jobs','worker_id')] is None for row in rows['capture_jobs'])
    assert source.states() == ['delivered','delivered']
    assert not list(data.root.glob('.graph-validation-*'))


@pytest.mark.parametrize('kind,day_field',[('reminder','startDate'),('reminderMarker','date')])
def test_current_mirror_unknown_movement_flags_remain_portable(source,tmp_path,kind,day_field):
    # SDK reads retain unknown fields. Generic mirror.save_entity projects these
    # flags for every movement kind, so portable proof must check the same rule.
    source.remote.data[kind]=[dict(id='fixture-reminder',**{day_field:'2026-10-01'},
        income=0,outcome=1,viewed=True,hold=False,tag=[])]
    source.app.state.mirror.refresh(force=True)
    with storage.transaction(source.path) as db:db.execute("DELETE FROM service_meta WHERE key='fixture-network-write-lock-free'")
    lineage,rows,_=snapshot(source)
    graph.validate_stage(stage(tmp_path,lineage,rows),expected_owners=['1'])
    projection=next(row for row in rows['zen_movements'] if row[:2]==[kind,'fixture-reminder'])
    assert projection[offset('zen_movements','viewed')]==1
    projection[offset('zen_movements','viewed')]=0
    with pytest.raises(codec.PortableError,match='invalid_mirror_projection'):
        graph.validate_stage(stage(tmp_path,lineage,rows),expected_owners=['1'])


def test_format_valid_golden_is_never_mistaken_for_a_valid_financial_graph(tmp_path):
    golden = json.loads(Path('fixtures/business/v1/portable-golden-v2.json').read_text())
    rows = {name: [[codec.unscalar(cell) for cell in row] for row in values] for name, values in golden['rows'].items()}
    archive = tmp_path/'golden.zip'
    codec.create_archive(archive, rows, dataset_id=golden['dataset_id'], owners=golden['owner_ids'],
        session_id=str(uuid4()), archive_id=str(uuid4()), created_at='2026-10-20T00:00:00Z', version='format-only')
    data = codec.read_archive(archive, tmp_path/'stage')
    with pytest.raises(codec.PortableError): graph.validate_stage(data)
    assert not list(data.root.glob('.graph-validation-*'))


def test_capture_proofs_accept_original_detail_key_order_without_rewriting_audit_bytes(source,tmp_path):
    job,*_=source.capture(count=1,changes={'details':{'remark':'Fixture','currency':'VND'}})
    assert source.client.post('/api/v1/jobs/'+job['id']+'/stop',json={}).status_code==200
    lineage,rows,_=snapshot(source)
    original=deepcopy(rows['bank_versions'])
    # Page canonicalization sorts detail keys; bank revision JSON preserves the
    # original wire dictionary order. Both describe exactly the same record.
    assert any('"details":{"remark":"Fixture","currency":"VND"}' in row[offset('bank_versions','raw_json')] for row in original)
    data=stage(tmp_path,lineage,rows)
    graph.validate_stage(data,expected_owners=['1'])
    assert list(data.rows('bank_versions'))==original


@pytest.mark.parametrize('field',['description','details'])
def test_capture_proof_value_changes_remain_invalid_after_rehashing_the_page(source,tmp_path,field):
    lineage,rows,_=snapshot(source)
    page=rows['capture_pages'][0]
    payload=json.loads(page[offset('capture_pages','payload_json')])
    payload['records'][0][field]='Changed description' if field=='description' else {'remark':'Changed detail'}
    raw=graph.capture_canonical(payload)
    page[offset('capture_pages','payload_json')]=raw
    page[offset('capture_pages','digest')]=sha256(raw.encode()).hexdigest()
    data=stage(tmp_path,lineage,rows)
    with pytest.raises(codec.PortableError,match='invalid_captured_revision'):
        graph.validate_stage(data,expected_owners=['1'])


@pytest.mark.parametrize('prefix',['\ufeff',' \ufeff','\ufeff \ufeff','\x1c','\x85','\u2000'])
def test_nested_json_credential_screening_cannot_be_bypassed_with_prefix_markers(prefix):
    raw=json.dumps({'futureField':prefix+json.dumps({'token':'synthetic-value'})})
    with pytest.raises(codec.PortableError,match='credential_material'):
        graph.inspect_json(raw)


@pytest.mark.parametrize('prefix',['\ufeff',' \ufeff','\ufeff \ufeff','\x1c','\x85','\u2000'])
def test_screening_prefix_normalization_does_not_rewrite_ordinary_financial_text(prefix):
    value={'futureField':prefix+json.dumps({'ordinary':'retained'})}
    assert graph.inspect_json(json.dumps(value))==value


@pytest.mark.parametrize('value',[{'text':'\ud800'},{'\ud800':'text'},{'nested':json.dumps({'text':'\ud800'})}])
def test_financial_json_cannot_hide_non_utf8_surrogates_in_escaped_values_or_keys(value):
    with pytest.raises(codec.PortableError,match='invalid_financial_json'):
        graph.inspect_json(json.dumps(value))


def test_literal_backslash_unicode_notes_are_preserved_without_becoming_surrogates():
    value={'note':'literal \\ud800 text'}
    assert graph.inspect_json(json.dumps(value))==value


@pytest.mark.parametrize('kind', ['foreign_key', 'missing_processing', 'missing_revision', 'missing_alias', 'bad_raw_amount',
    'missing_attempt', 'attempt_payload', 'terminal_state', 'unknown_metadata', 'metadata_token', 'secret_raw',
    'nested_secret', 'bad_page_digest', 'bad_coverage', 'bad_frontier', 'missing_capture_proof', 'mirror_amount', 'missing_mirror_context', 'missing_mirror_projection',
    'fake_legacy_identity','fake_legacy_version','revision_projection','revision_gap'])
def test_self_consistent_archive_hashes_cannot_hide_broken_financial_facts(source, tmp_path, kind):
    lineage, rows, _ = snapshot(source)
    rows = deepcopy(rows)
    if kind == 'foreign_key': rows['bank_transactions'][0][offset('bank_transactions','account_id')] = 'missing'
    elif kind == 'missing_processing': rows['bank_processing'].pop()
    elif kind == 'missing_revision': rows['bank_versions'].clear()
    elif kind == 'missing_alias': rows['bank_aliases'].clear()
    elif kind == 'bad_raw_amount': rows['bank_transactions'][0][offset('bank_transactions','amount')] = '999'
    elif kind == 'missing_attempt': rows['delivery_attempts'].clear()
    elif kind == 'attempt_payload': rows['deliveries'][0][offset('deliveries','payload')] = '{"id":"another-uuid"}'
    elif kind == 'terminal_state': rows['bank_processing'][0][offset('bank_processing','state')] = 'pending'
    elif kind == 'unknown_metadata': rows['dataset_metadata'].append(['zzz_unknown','value'])
    elif kind == 'metadata_token': rows['dataset_metadata'].append(['zzz_api_token','synthetic-secret-value'])
    elif kind in ('secret_raw','nested_secret'):
        index=offset('zen_entities','data'); entity=json.loads(rows['zen_entities'][0][index])
        entity['accessToken'] = 'synthetic-secret' if kind == 'secret_raw' else 'placeholder'
        if kind == 'nested_secret':
            del entity['accessToken']; entity['futureField'] = '{"request":{"headers":{"Authorization":"Bearer synthetic-secret-value"}}}'
        rows['zen_entities'][0][index]=json.dumps(entity)
    elif kind == 'bad_page_digest': rows['capture_pages'][0][offset('capture_pages','digest')] = '0'*64
    elif kind == 'bad_coverage': rows['coverage'][0][offset('coverage','end_date')] = '2026-10-30'
    elif kind == 'bad_frontier': rows['bank_accounts'][0][offset('bank_accounts','last_complete_end')] = '2026-09-14'
    elif kind == 'missing_capture_proof': rows['captured_records'].pop()
    elif kind == 'mirror_amount': rows['zen_movements'][0][offset('zen_movements','outcome')] = '999'
    elif kind == 'missing_mirror_context': rows['zen_mirror_state'].clear()
    elif kind == 'missing_mirror_projection':
        rows['zen_tags'].clear(); rows['zen_movements'].clear()
    elif kind == 'fake_legacy_identity': rows['bank_transactions'][0][offset('bank_transactions','identity_version')]='legacy-v12'
    elif kind == 'fake_legacy_version': rows['bank_versions'][0][offset('bank_versions','parser_version')]='legacy-v12'
    elif kind == 'revision_projection': rows['bank_versions'][0][offset('bank_versions','data_json')]='{}'
    elif kind == 'revision_gap':
        extra=deepcopy(rows['bank_versions'][0]);extra[offset('bank_versions','revision')]=3
        rows['bank_versions'].append(extra)
        rows['bank_versions'].sort(key=lambda row:(row[0].encode(),row[1]))
        bank=next(row for row in rows['bank_transactions'] if row[0]==extra[0]);bank[offset('bank_transactions','revision')]=3
    data = stage(tmp_path, lineage, rows)
    with pytest.raises(codec.PortableError): graph.validate_stage(data)
    assert not list(data.root.glob('.graph-validation-*'))
    assert source.states() == ['delivered','delivered']


def test_declared_owners_and_fresh_destination_owners_are_distinct_gates(source, tmp_path):
    lineage, rows, _ = snapshot(source)
    data = stage(tmp_path, lineage, rows)
    with pytest.raises(codec.PortableError, match='destination_owner_mismatch'):
        graph.validate_stage(data, expected_owners=['2'])
    assert graph.validate_stage(data).declared_owners == ('1',)


def test_new_source_columns_or_runtime_tables_require_explicit_disposition(source):
    with storage.transaction(source.path) as db:
        db.execute('CREATE TABLE future_financial_data(id TEXT PRIMARY KEY)')
    with closing(storage.connect_readonly(source.path)) as db:
        with pytest.raises(codec.PortableError, match='unclassified_source_table'):
            graph.source_tables(db, 'unused')


def test_unfinished_capture_is_not_exported_as_complete(tmp_path):
    harness = DeliveryHarness(tmp_path/'source.sqlite3')
    try:
        harness.capture(complete=False)
        with pytest.raises(codec.PortableError, match='capture_not_quiescent'):
            snapshot(harness)
    finally: harness.close()


def test_unknown_legacy_metadata_fails_instead_of_being_dropped_or_leaked(source):
    with storage.transaction(source.path) as db:
        db.execute("INSERT INTO service_meta VALUES ('legacy:unknown','synthetic')")
    with pytest.raises(codec.PortableError, match='unclassified_metadata'): snapshot(source)


def test_schema_backup_host_paths_are_removed_before_portable_encoding(source):
    with storage.transaction(source.path) as db:
        db.execute("UPDATE schema_migrations SET backup_path='/private/synthetic-host-path.sqlite3' WHERE version=2")
    _, rows, normalized = snapshot(source)
    assert normalized['source_schema_migrations.backup_path'] == 1
    assert all(row[offset('source_schema_migrations','backup_path')] is None for row in rows['source_schema_migrations'])


def test_classified_legacy_audit_is_preserved_as_data_without_executing_its_sql(tmp_path):
    from legacy_factory import build
    from backend.migrate_legacy import convert
    legacy = build(tmp_path/'legacy.sqlite3')
    # The general converter fixture deliberately includes an unknown table.
    # This positive case covers only the explicitly classified stock schema.
    with closing(sqlite3.connect(legacy)) as db:
        db.execute('DROP TABLE extra_unknown')
        db.execute('INSERT INTO zen_transaction_tags SELECT (SELECT id FROM zen_transaction LIMIT 1),(SELECT id FROM zen_tag LIMIT 1),0')
        db.execute('UPDATE zen_transaction SET run_id=NULL WHERE id=(SELECT id FROM zen_transaction LIMIT 1)')
        db.commit()
    destination = tmp_path/'converted.sqlite3'
    convert(legacy, destination)
    with storage.transaction(destination) as db:
        # Graph provenance is not authentication: synthetic owner evidence is
        # enough for this test, while the live service must verify fresh identity.
        db.execute("INSERT INTO zen_entities VALUES ('user','1',?,NULL,0,NULL)", ('{"id":1}',))
    with storage.transaction(destination, read_only=True) as db:
        lineage = db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
        values, _ = graph.source_tables(db, lineage)
        rows = {name: list(items) for name, items in values.items()}
    data = stage(tmp_path, lineage, rows)
    result = graph.validate_stage(data)
    assert dict(result.counts)['legacy_rows'] > 0
    assert dict(result.counts)['legacy_objects'] == len(graph.RULES['legacy_objects'])
    modified = deepcopy(rows)
    modified['legacy_objects'][0][offset('legacy_objects','sql')] = 'DROP TABLE bank_transactions'
    bad = stage(tmp_path, lineage, modified)
    with pytest.raises(codec.PortableError): graph.validate_stage(bad)
    assert graph.validate_stage(data).dataset_digest == result.dataset_digest
    modified = deepcopy(rows)
    modified['zen_movements'][0][offset('zen_movements','outcome')] = '999'
    with pytest.raises(codec.PortableError, match='invalid_legacy_projection'):
        graph.validate_stage(stage(tmp_path, lineage, modified))
    modified=deepcopy(rows)
    modified['zen_tags'][0][offset('zen_tags','position')]=10
    with pytest.raises(codec.PortableError,match='invalid_legacy_tags'):
        graph.validate_stage(stage(tmp_path,lineage,modified))


def test_current_mirror_tags_match_raw_order_and_exact_positions(source,tmp_path):
    lineage,rows,_=snapshot(source)
    movement=rows['zen_movements'][0]
    entity=next(row for row in rows['zen_entities'] if row[:2]==movement[:2])
    raw=json.loads(entity[offset('zen_entities','data')]);raw['tag']=['category-a']
    entity[offset('zen_entities','data')]=json.dumps(raw)
    rows['zen_tags']=[[*movement[:2],'category-a',0]]
    graph.validate_stage(stage(tmp_path,lineage,rows))
    rows['zen_tags'][0][offset('zen_tags','position')]=10
    with pytest.raises(codec.PortableError,match='invalid_mirror_tags'):
        graph.validate_stage(stage(tmp_path,lineage,rows))


def test_unclassified_legacy_objects_are_rejected_instead_of_being_executed(tmp_path):
    from legacy_factory import build
    from backend.migrate_legacy import convert
    destination = tmp_path/'converted.sqlite3'
    convert(build(tmp_path/'legacy.sqlite3'), destination)
    with storage.transaction(destination) as db:
        db.execute("INSERT INTO zen_entities VALUES ('user','1','{\"id\":1}',NULL,0,NULL)")
    with storage.transaction(destination, read_only=True) as db:
        lineage = db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
        values, _ = graph.source_tables(db, lineage)
        rows = {name:list(items) for name,items in values.items()}
    with pytest.raises(codec.PortableError, match='unclassified_legacy_object'):
        graph.validate_stage(stage(tmp_path, lineage, rows))
