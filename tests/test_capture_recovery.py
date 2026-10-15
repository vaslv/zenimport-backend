from contextlib import closing
from hashlib import sha256
import json
import sqlite3
from uuid import uuid4

import pytest

from backend import storage
from capture_factory import Harness, record


@pytest.fixture
def h(tmp_path):
    value = Harness(tmp_path / 'recovery.sqlite3')
    yield value
    value.close()


def test_partial_forced_reimport_is_resumed_by_normal_click(h):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    h.finish_empty(job, auth)
    forced = h.start(connection['id'], reimport_from='2026-08-01').json()
    worker = str(uuid4())
    lease = h.client.post(f'/api/v1/jobs/{forced["id"]}/lease', json={'worker_id': worker}).json()
    auth = {'worker_id': worker, 'lease_epoch': lease['epoch']}
    first = forced['accounts'][0]['intervals'][0]
    assert first['period'] == {'start': '2026-08-01', 'end': '2026-08-30'}
    assert h.page(forced, first, auth).status_code == 200
    assert h.complete(forced, first, auth).status_code == 200
    h.client.post(f'/api/v1/jobs/{forced["id"]}/stop', json={})
    next_job, auth = h.leased(connection['id'])
    assert next_job['accounts'][0]['intervals'][0]['period']['start'] == '2026-08-31'
    h.finish_empty(next_job, auth)
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT remaining_json FROM reimport_ranges').fetchone()[0] == '[]'
        assert db.execute('SELECT authorized_start FROM job_accounts WHERE job_id=?', (next_job['id'],)).fetchone()[0] == '2026-08-31'


def test_settings_change_invalidates_prepared_payload_but_not_uuid(h):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    assert h.page(job, interval, auth, records=[record(interval)]).status_code == 200
    with storage.transaction(h.path) as db:
        bank_id = db.execute('SELECT id FROM bank_transactions').fetchone()[0]
        db.execute("UPDATE bank_processing SET state='prepared'")
        db.execute("INSERT INTO deliveries VALUES (?,'fixture-stable-uuid','{}','prepared',0,'fixture',?,0,0)", (bank_id, job['id']))
    account_id = job['accounts'][0]['account_id']
    changed = h.configure(account_id, version=1, target='fixture-other-vnd')
    assert changed.status_code == 200
    with storage.transaction(h.path, read_only=True) as db:
        row = db.execute('SELECT * FROM deliveries').fetchone()
        assert row['zen_id'] == 'fixture-stable-uuid' and row['payload'] == '{}'
        assert row['needs_rebuild'] == 1 and row['authorized_job_id'] is None
        assert db.execute('SELECT target_id FROM job_accounts').fetchone()[0] != changed.json()['zenmoney_account_id']


def test_unknown_delivery_blocks_remapping_even_after_disable(h):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    h.page(job, interval, auth, records=[record(interval)])
    with storage.transaction(h.path) as db:
        db.execute("UPDATE bank_processing SET state='uncertain'")
    account_id = job['accounts'][0]['account_id']
    conflict = h.configure(account_id, version=1, target='fixture-other-vnd')
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'delivery_uncertain'
    assert h.configure(account_id, version=1, selection='ignored').status_code == 200
    assert h.configure(account_id, version=2, target='fixture-other-vnd').json()['error']['code'] == 'delivery_uncertain'
    assert h.configure(account_id, version=2).status_code == 200


@pytest.mark.parametrize('code', ['capture_context_changed', 'history_incomplete'])
def test_unsafe_context_report_creates_new_snapshot_after_next_click(h, code):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    assert h.page(job, interval, auth).status_code == 200
    result = h.client.post(f'/api/v1/jobs/{job["id"]}/capture-status', json={**auth, 'state': 'paused', 'code': code})
    assert result.json()['capture_state'] == 'failed'
    new = h.start(connection['id']).json()
    assert new['id'] != job['id']
    assert new['accounts'][0]['intervals'][0]['id'] != interval['id']
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM capture_pages').fetchone()[0] == 1


def test_one_failed_account_does_not_lose_others(h):
    connection = h.ready(names=('one', 'two'))
    job, auth = h.leased(connection['id'])
    a, b = job['accounts']
    first = a['intervals'][0]
    assert h.page(job, first, auth, records=[record(first)], total_pages=2, total_records=2).status_code == 200
    failed = h.page(job, first, auth, number=2, records=[record(first)], total_pages=3, total_records=3)
    assert failed.status_code == 409
    for i in b['intervals']:
        assert h.page(job, i, auth).status_code == 200
        assert h.complete(job, i, auth).status_code == 200
    status = h.client.get(f'/api/v1/jobs/{job["id"]}').json()
    assert status['accounts'][1]['capture_state'] == 'complete'
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT last_complete_end FROM bank_accounts WHERE id=?', (b['account_id'],)).fetchone()[0] == job['end_date']
        assert db.execute('SELECT last_complete_end FROM bank_accounts WHERE id=?', (a['account_id'],)).fetchone()[0] is None


def test_service_restart_retains_pages_job_request_and_lease_epoch(h):
    connection = h.ready()
    command = str(uuid4())
    job = h.start(connection['id'], request_id=command).json()
    worker = str(uuid4())
    lease = h.client.post(f'/api/v1/jobs/{job["id"]}/lease', json={'worker_id': worker}).json()
    auth = {'worker_id': worker, 'lease_epoch': lease['epoch']}
    interval = job['accounts'][0]['intervals'][0]
    assert h.page(job, interval, auth, records=[record(interval)]).status_code == 200
    other = Harness(h.path)
    try:
        replay = other.start(connection['id'], request_id=command).json()
        assert replay['id'] == job['id'] and replay['accounts'][0]['intervals'][0]['received_records'] == 1
        other.now += 121
        new_lease = other.client.post(f'/api/v1/jobs/{job["id"]}/lease', json={'worker_id': str(uuid4())}).json()
        assert new_lease['epoch'] > lease['epoch']
        assert other.page(job, interval, {'worker_id': str(uuid4()), 'lease_epoch': 999}, records=[record(interval)]).json()['repeated'] is True
    finally:
        other.close()


def make_v2(path):
    storage.create_private(path)
    with closing(storage.configured(sqlite3.connect(path))) as db:
        db.execute('CREATE TABLE service_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT INTO service_meta VALUES ('instance_id','fixture-v2-instance')")
        storage.apply_schema(db)
        db.execute("INSERT INTO zen_entities VALUES ('account','fixture-kept','{\"amount\":0.123456789012345678901}',NULL,0,NULL)")
        db.execute('INSERT INTO schema_migrations VALUES (2,0,NULL)')
        db.execute(f'PRAGMA application_id={storage.APPLICATION_ID}')
        db.execute('PRAGMA user_version=2')
        db.commit()


def test_b2_upgrade_preserves_finance_and_private_backup(tmp_path):
    path = tmp_path / 'v2.sqlite3'
    make_v2(path)
    backup = storage.initialize(path)
    assert backup.stat().st_mode & 0o777 == 0o600
    with storage.transaction(path, read_only=True) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT data FROM zen_entities').fetchone()[0] == '{"amount":0.123456789012345678901}'
        assert db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0] == 'fixture-v2-instance'
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
    assert storage.initialize(path) is None


def test_b2_upgrade_failure_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / 'v2.sqlite3'
    make_v2(path)
    before = sha256(path.read_bytes()).hexdigest()
    original = storage.execute_schema
    def interrupted(db, sql):
        original(db, sql)
        raise RuntimeError('fixture-v3-failure')
    monkeypatch.setattr(storage, 'execute_schema', interrupted)
    with pytest.raises(RuntimeError):
        storage.initialize(path)
    assert sha256(path.read_bytes()).hexdigest() == before


def test_reimport_before_configured_start_counts_authorized_records(h):
    connection = h.ready()
    forced = h.start(connection['id'], reimport_from='2026-08-01').json()
    worker = str(uuid4())
    epoch = h.client.post(f'/api/v1/jobs/{forced["id"]}/lease', json={'worker_id': worker}).json()['epoch']
    interval = forced['accounts'][0]['intervals'][0]
    assert h.page(forced, interval, {'worker_id': worker, 'lease_epoch': epoch}, records=[record(interval)]).status_code == 200
    assert h.client.get(f'/api/v1/jobs/{forced["id"]}').json()['delivery']['pending'] == 1


@pytest.mark.parametrize('sql', [
    "UPDATE capture_pages SET payload_json='{}'", 'DELETE FROM capture_pages',
    'UPDATE captured_records SET revision=revision', 'DELETE FROM captured_records',
    'UPDATE job_requests SET request_json=request_json', 'DELETE FROM job_requests',
    'UPDATE job_accounts SET settings_version=settings_version',
    'UPDATE job_context SET connector_version=connector_version',
    "UPDATE capture_intervals SET start_date='2020-01-01'",
])
def test_committed_capture_proofs_and_command_identity_are_immutable(h, sql):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    h.page(job, interval, auth, records=[record(interval)])
    with storage.transaction(h.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(sql)


def test_six_calendar_month_start_and_complete_frontier_are_preserved(h):
    h.seed_mirror()
    connection = h.discover()
    account = connection['accounts'][0]['id']
    assert h.configure(account, start='2026-04-15').status_code == 200
    job, auth = h.leased(connection['id'])
    intervals = job['accounts'][0]['intervals']
    assert intervals[0]['period']['start'] == '2026-04-15' and intervals[-1]['period']['end'] == '2026-10-15'
    assert len(intervals) == 7
    first = intervals[0]
    assert h.page(job, first, auth).status_code == 200
    assert h.complete(job, first, auth).status_code == 200
    with storage.transaction(h.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE capture_intervals SET state='planned' WHERE id=?", (first['id'],))


def test_old_legacy_placeholder_requires_actual_discovery(h):
    connection = h.discover()
    with storage.transaction(h.path) as db:
        db.execute("UPDATE connections SET capabilities_json='{}' WHERE id=?", (connection['id'],))
    response = h.start(connection['id'])
    assert response.status_code == 409 and response.json()['error']['code'] == 'capture_context_changed'
