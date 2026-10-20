"""Migration uses synthetic accounts only; imported journals never write on arrival."""
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import sqlite3
from threading import Event
import zipfile

from fastapi.testclient import TestClient
import pytest

from backend import storage
from backend.app import create_app
from backend.config import Settings
from backend.migration import meta
from capture_factory import TOKEN
from delivery_factory import DeliveryHarness
from storage_factory import remove_schema8


@pytest.fixture
def source(tmp_path):
    harness = DeliveryHarness(tmp_path / 'source.sqlite3')
    yield harness
    if harness.context is not None:
        harness.close()


def exported(source):
    job, *_ = source.capture()
    source.due()
    assert source.client.post('/api/v1/jobs/' + job['id'] + '/stop', json={}).status_code == 200
    response = source.client.post('/api/v1/migration/export')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/zip'
    assert response.headers['cache-control'] == 'no-store'
    return response.content


def target_client(tmp_path, source, *, worker=False):
    # The SDK fake remains the same account, but its client is independently owned.
    import httpx
    from zenmoney_api import Client
    sdk = Client('fixture-sdk-token', transport=httpx.MockTransport(source.remote.handle))
    app = create_app(Settings(tmp_path / 'target.sqlite3', TOKEN, 'fixture-sdk-token'),
                     client_factory=lambda _: sdk, start_worker=worker)
    return app, TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN})


def upload(client, payload):
    return client.post('/api/v1/migration/import', content=payload, headers={'Content-Type': 'application/zip'})


def repack(payload, change):
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    change(members)
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return output.getvalue()


@pytest.mark.parametrize('schema_version',[6,7])
def test_published_archive_upgrades_only_verified_temporary_copy(source, tmp_path,schema_version):
    payload = exported(source)
    def downgrade(members):
        old = tmp_path / 'published-v6.sqlite3'
        old.write_bytes(members['database.sqlite3'])
        with sqlite3.connect(old) as db:
            remove_schema8(db)
            if schema_version==6:
                db.execute('DROP TABLE connection_labels')
                db.execute('DELETE FROM schema_migrations WHERE version=7')
            db.execute('PRAGMA user_version='+str(schema_version))
        data = old.read_bytes()
        manifest = json.loads(members['manifest.json'])
        manifest.update(schema_version=schema_version, service_version='0.2.0' if schema_version==6 else '0.2.1', database_size=len(data), database_sha256=sha256(data).hexdigest())
        members.update({'database.sqlite3': data, 'manifest.json': json.dumps(manifest).encode()})
    published = repack(payload, downgrade)
    before = len(source.remote.writes)
    app, client = target_client(tmp_path / 'imported-v6', source)
    with client:
        response = upload(client, published)
        assert response.status_code == 200, response.text
        assert client.get('/api/v1/connections?bank=vietinbank').json()['connections']
        assert app.state.migration.status()['awaiting_sync']
        assert len(source.remote.writes) == before
    with storage.transaction(tmp_path / 'imported-v6/target.sqlite3', read_only=True) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == storage.SCHEMA_VERSION
        assert db.execute('SELECT count(*) FROM processing_receipts').fetchone()[0] > 0


def test_manifest_cannot_lie_about_schema6(source, tmp_path):
    payload = exported(source)
    def lie(members):
        manifest = json.loads(members['manifest.json']);manifest['schema_version'] = 6
        members['manifest.json'] = json.dumps(manifest).encode()
    _, client = target_client(tmp_path / 'invalid-v6', source)
    with client:
        assert upload(client, repack(payload, lie)).json()['error']['code'] == 'migration_invalid'


def test_format1_keeps_dataset_but_never_clones_physical_installation(source,tmp_path):
    from backend.migration_participant import installation
    payload=exported(source)
    with storage.transaction(source.path,read_only=True) as db:
        source_id=installation(db)['installation_id']
        lineage=meta(db,'instance_id')
    app,client=target_client(tmp_path/'physical-target',source)
    with client:
        with storage.transaction(app.state.migration.path,read_only=True) as db:
            before=installation(db)['installation_id']
        assert upload(client,payload).status_code==200
        with storage.transaction(app.state.migration.path,read_only=True) as db:
            assert installation(db)['installation_id']==before!=source_id
            assert meta(db,'instance_id')==lineage


def test_export_pauses_source_and_stays_paused_after_restart(source):
    payload = exported(source)
    assert payload.startswith(b'PK')
    assert source.client.get('/api/v1/migration/status').json()['paused']
    assert source.client.post('/api/v1/jobs', json={}).json()['error']['code'] == 'migration_paused'
    before = len(source.remote.writes)
    source.due()
    assert len(source.remote.writes) == before
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        assert set(archive.namelist()) == {'manifest.json', 'database.sqlite3'}
        assert TOKEN.encode() not in archive.read('database.sqlite3')
    source.close()
    source.context = None
    app = create_app(Settings(source.path, TOKEN), start_worker=False)
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        assert client.get('/api/v1/migration/status').json()['paused']
        assert client.post('/api/v1/migration/resume').status_code == 200
        assert not client.get('/api/v1/migration/status').json()['paused']


def test_roundtrip_keeps_deletions_receipts_ids_and_no_automatic_send(source, tmp_path):
    job, *_ = source.capture()
    source.due()
    identifier = next(iter(source.remote.transactions))
    del source.remote.transactions[identifier]
    source.remote.deletions.append({'object': 'transaction', 'id': identifier})
    source.app.state.mirror.refresh(force=True, transactions=True)
    assert source.client.post('/api/v1/jobs/' + job['id'] + '/stop', json={}).status_code == 200
    response = source.client.post('/api/v1/migration/export')
    assert response.status_code == 200, response.text
    payload = response.content
    with storage.transaction(source.path, read_only=True) as db:
        original_id = meta(db, 'instance_id')
        receipts = [tuple(r) for r in db.execute('SELECT * FROM processing_receipts')]
        tombstones = [tuple(r) for r in db.execute('SELECT * FROM zen_tombstones')]
    app, context = target_client(tmp_path, source)
    before = len(source.remote.writes)
    with context as client:
        assert client.get('/api/v1/migration/status').json()['can_import']
        result = upload(client, payload)
        assert result.status_code == 200, result.text
        assert result.json()['restored']
        assert not client.get('/api/v1/migration/status').json()['can_import']
        assert client.get('/api/v1/migration/status').json()['awaiting_sync']
        app.state.delivery.run_once()
        assert len(source.remote.writes) == before
        with storage.transaction(app.state.delivery.path, read_only=True) as db:
            assert meta(db, 'instance_id') == original_id
            assert [tuple(r) for r in db.execute('SELECT * FROM processing_receipts')] == receipts
            assert [tuple(r) for r in db.execute('SELECT * FROM zen_tombstones')] == tombstones
            storage.check_integrity(db)
        # A lost import response is safe to repeat with the exact same archive.
        assert upload(client, payload).json()['already_imported']


def test_import_locked_after_first_started_sync_even_without_transactions(source, tmp_path):
    payload = exported(source)
    app, context = target_client(tmp_path, source)
    with context as client:
        with storage.transaction(app.state.delivery.path) as db:
            db.execute("INSERT INTO service_meta VALUES ('sync_ever_started','1')")
        response = upload(client, payload)
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'migration_not_empty'
        assert not client.get('/api/v1/migration/status').json()['can_import']


@pytest.mark.parametrize('case', ['garbage', 'checksum', 'schema', 'extra', 'traversal', 'duplicate', 'sql'])
def test_invalid_archives_leave_target_and_sources_unchanged(source, tmp_path, case):
    payload = exported(source)
    def damage(members):
        if case in ('checksum', 'schema'):
            manifest = json.loads(members['manifest.json'])
            manifest['database_sha256' if case == 'checksum' else 'schema_version'] = 'wrong'
            members['manifest.json'] = json.dumps(manifest).encode()
        elif case in ('extra', 'traversal'):
            members['extra' if case == 'extra' else '../escape'] = b'bad'
        elif case == 'sql':
            members['database.sqlite3'] = b'DROP TABLE bank_transactions;'
    if case == 'garbage':
        payload = b'not a zip'
    elif case == 'duplicate':
        out = BytesIO(payload)
        with zipfile.ZipFile(out, 'a') as archive:
            archive.writestr('manifest.json', '{}')
        payload = out.getvalue()
    else:
        payload = repack(payload, damage)
    app, context = target_client(tmp_path, source)
    with context as client:
        with storage.transaction(app.state.delivery.path, read_only=True) as db:
            original_id = meta(db, 'instance_id')
        response = upload(client, payload)
        assert response.status_code == 422, response.text
        assert response.json()['error']['code'] == 'migration_invalid'
        with storage.transaction(app.state.delivery.path, read_only=True) as db:
            assert meta(db, 'instance_id') == original_id
            assert not db.execute('SELECT 1 FROM bank_transactions').fetchone()


def test_wrong_account_is_rejected_without_restoring(source, tmp_path):
    payload = exported(source)
    source.remote.data['user'] = [{'id': 99, 'login': 'another-fixture'}]
    app, context = target_client(tmp_path, source)
    with context as client:
        response = upload(client, payload)
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'migration_wrong_account'
        with storage.transaction(app.state.delivery.path, read_only=True) as db:
            assert not db.execute('SELECT 1 FROM bank_transactions').fetchone()


def test_migration_requires_auth_and_exclusive_gate(source):
    for method, path in [('get', 'status'), ('post', 'export'), ('post', 'import'), ('post', 'resume')]:
        assert getattr(source.client, method)('/api/v1/migration/' + path, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    with source.app.state.migration_gate.operation():
        response = source.client.post('/api/v1/migration/export')
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'migration_busy'
    with source.app.state.migration_gate.exclusive():
        assert source.client.get('/api/v1/zenmoney/accounts').json()['error']['code'] == 'migration_busy'
        source.worker.run_once()


def test_export_rejects_running_capture_without_pausing(source):
    source.capture(complete=False)
    response = source.client.post('/api/v1/migration/export')
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'migration_incomplete'
    assert not source.client.get('/api/v1/migration/status').json()['paused']


def test_export_cannot_cross_an_inflight_financial_write(source):
    source.capture()
    entered, release = Event(), Event()
    def in_flight():
        entered.set()
        assert release.wait(5)
    source.remote.before_write = in_flight
    with ThreadPoolExecutor() as pool:
        task = pool.submit(source.worker.run_once)
        assert entered.wait(5)
        try:
            response = source.client.post('/api/v1/migration/export')
            assert response.json()['error']['code'] == 'migration_busy'
            assert not source.client.get('/api/v1/migration/status').json()['paused']
        finally:
            release.set()
        task.result(timeout=5)


def test_verified_foreign_sqlite_and_oversized_request_are_rejected(source, tmp_path):
    payload = exported(source)
    foreign = tmp_path / 'foreign.sqlite3'
    with sqlite3.connect(foreign) as db:
        db.execute('CREATE TABLE foreign_table(id INTEGER)')
    def change(members):
        members['database.sqlite3'] = foreign.read_bytes()
        manifest = json.loads(members['manifest.json'])
        manifest.update(database_size=foreign.stat().st_size, database_sha256=sha256(foreign.read_bytes()).hexdigest())
        members['manifest.json'] = json.dumps(manifest).encode()
    app, context = target_client(tmp_path, source)
    with context as client:
        assert upload(client, repack(payload, change)).json()['error']['code'] == 'migration_invalid'
        from backend.sync_contracts import MAX_MIGRATION_BYTES
        response = client.post('/api/v1/migration/import', content=b'abc', headers={
            'Content-Type': 'application/zip', 'Content-Length': str(MAX_MIGRATION_BYTES + 1)})
        assert response.status_code == 413
        assert client.post('/api/v1/migration/import', content=payload).status_code == 422
        with storage.transaction(app.state.delivery.path, read_only=True) as db:
            assert not meta(db, 'migration_import_sha256')


def test_import_preserves_uncertain_uuid_and_never_retries_it(source, tmp_path):
    job, *_ = source.capture()
    source.remote.mode = 'timeout_after'
    # Keep read verification unavailable, leaving the committed write uncertain.
    source.remote.before_write = lambda: setattr(source.remote, 'read_failure', 503)
    source.due()
    assert source.states() == ['uncertain']
    source.client.post('/api/v1/jobs/' + job['id'] + '/stop', json={})
    response = source.client.post('/api/v1/migration/export')
    assert response.status_code == 200
    source.remote.read_failure = None
    source.remote.before_write = None
    app, context = target_client(tmp_path, source)
    with context as client:
        assert upload(client, response.content).status_code == 200
        with storage.transaction(app.state.delivery.path, read_only=True) as db:
            imported = db.execute('SELECT zen_id FROM deliveries').fetchone()[0]
            assert imported == source.remote.writes[0][0]['id']
        # Explicit user synchronization unblocks the imported worker; it verifies
        # the same UUID by reading the remote collection, never by another write.
        source.remote.path = app.state.delivery.path
        with storage.transaction(app.state.delivery.path) as db:
            db.execute("DELETE FROM service_meta WHERE key='migration_hold'")
            db.execute('UPDATE delivery_schedule SET next_run_at=0')
        app.state.delivery.run_once()
        assert len(source.remote.writes) == 1
        with storage.transaction(app.state.delivery.path, read_only=True) as db:
            assert db.execute('SELECT state FROM deliveries').fetchone()[0] == 'delivered'


def test_archive_preserves_named_connections_memberships_and_shared_identity(source, tmp_path):
    first = source.client.get('/api/v1/connections?bank=vietinbank').json()['connections'][0]
    assert source.client.put('/api/v1/connections/' + first['id'] + '/label', json={'label': 'Personal'}).status_code == 200
    second = source.discover(profile='another-login', names=(first['accounts'][0]['source_key'],), label='Shared access')
    assert second['accounts'][0]['id'] == first['accounts'][0]['id']
    job, *_ = source.capture()
    source.due()
    assert source.client.post('/api/v1/jobs/' + job['id'] + '/stop', json={}).status_code == 200
    catalog = source.client.get('/api/v1/connections?bank=vietinbank').json()
    payload = source.client.post('/api/v1/migration/export').content
    writes = len(source.remote.writes)
    _, client = target_client(tmp_path / 'named-profiles', source)
    with client:
        response = upload(client, payload)
        assert response.status_code == 200, response.text
        assert client.get('/api/v1/connections?bank=vietinbank').json() == catalog
        assert len(source.remote.writes) == writes
