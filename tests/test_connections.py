"""Multiple logins per bank share only proven physical account identities."""
from contextlib import closing
import sqlite3

import pytest

from backend import storage
from capture_factory import Harness, TARGET


@pytest.fixture
def h(tmp_path):
    value = Harness(tmp_path / 'connections.sqlite3')
    yield value
    value.close()


def test_named_connections_have_separate_account_sets_jobs_and_dates(h):
    h.seed_mirror()
    first = h.discover(bank='bidv', profile='login-a', names=('11112222', '33334444'), label='Personal')
    second = h.discover(bank='bidv', profile='login-b', names=('55556666', '77778888'), label='Work')
    assert first['id'] != second['id']
    for connection in (first, second):
        assert connection['profile_identity'] == 'user_selected'
        assert h.configure(connection['accounts'][0]['id'], start='2026-10-01').status_code == 200
        assert h.configure(connection['accounts'][1]['id'], selection='ignored').status_code == 200
    a = h.start(first['id']).json()
    b = h.start(second['id']).json()
    assert [v['account_id'] for v in a['accounts']] == [first['accounts'][0]['id']]
    assert [v['account_id'] for v in b['accounts']] == [second['accounts'][0]['id']]
    catalog = h.client.get('/api/v1/connections?bank=bidv').json()['connections']
    assert {c['label'] for c in catalog} == {'Personal', 'Work'}
    assert {c['profile_key'] for c in catalog} == {'login-a', 'login-b'}
    assert h.client.get('/api/v1/connections?bank=vietinbank').json() == {'connections': []}


def test_rename_is_nonfinancial_and_unlink_keeps_shared_account_settings(h):
    first = h.ready(bank='bidv', profile='first', names=('shared',), label='First')
    second = h.discover(bank='bidv', profile='second', names=('shared',), label='Second')
    shared = second['accounts'][0]
    assert shared['id'] == first['accounts'][0]['id']
    job = h.start(first['id']).json()
    renamed = h.client.put(f"/api/v1/connections/{first['id']}/label", json={'label': ' Renamed '})
    assert renamed.status_code == 200 and renamed.json()['label'] == 'Renamed'
    assert h.client.get('/api/v1/jobs/' + job['id']).json()['capture_state'] == 'planned'
    unlinked = h.client.post(f"/api/v1/connections/{first['id']}/accounts/{shared['id']}/unlink", json={})
    assert unlinked.status_code == 200 and unlinked.json()['accounts'] == []
    assert h.client.get('/api/v1/jobs/' + job['id']).json()['capture_state'] == 'cancelled'
    other = h.client.get('/api/v1/connections/' + second['id']).json()
    assert other['accounts'][0]['selection'] == 'enabled'
    assert other['accounts'][0]['zenmoney_account_id'] == TARGET
    restored = h.discover(bank='bidv', profile='first', names=('shared',))
    assert restored['label'] == 'Renamed' and restored['accounts'][0]['id'] == shared['id']
    assert restored['accounts'][0]['settings_version'] == shared['settings_version']


@pytest.mark.parametrize('label', ['', '   ', 'x' * 121, 'bad\nname'])
def test_invalid_names_are_rejected_without_changing_connection(h, label):
    connection = h.discover(label='Kept')
    result = h.client.put('/api/v1/connections/' + connection['id'] + '/label', json={'label': label})
    assert result.status_code == 422
    assert h.client.get('/api/v1/connections/' + connection['id']).json()['label'] == 'Kept'


def test_unlink_cannot_touch_an_account_from_another_connection(h):
    first = h.discover(profile='a', names=('one',))
    second = h.discover(profile='b', names=('two',))
    response = h.client.post(f"/api/v1/connections/{first['id']}/accounts/{second['accounts'][0]['id']}/unlink", json={})
    assert response.status_code == 404
    assert len(h.client.get('/api/v1/connections/' + second['id']).json()['accounts']) == 1


def test_schema6_upgrade_backs_up_and_keeps_legacy_membership_and_settings(tmp_path):
    from storage_factory import remove_schema8
    path = tmp_path / 'v6.sqlite3'
    h = Harness(path)
    connection = h.ready(profile='legacy-uuid')
    h.close()
    with closing(storage.configured(sqlite3.connect(path))) as db:
        remove_schema8(db)
        db.execute('DROP TABLE connection_labels')
        db.execute('DELETE FROM schema_migrations WHERE version=7')
        db.execute('PRAGMA user_version=6')
        db.commit()
    saved = storage.initialize(path)
    assert saved and saved.stat().st_mode & 0o777 == 0o600
    with closing(storage.connect_readonly(saved)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 6
    with storage.transaction(path, read_only=True) as db:
        assert db.execute('SELECT profile_key FROM connections').fetchone()[0] == 'legacy-uuid'
        account = db.execute('SELECT id,zenmoney_account_id,selection FROM bank_accounts').fetchone()
        assert tuple(account) == (connection['accounts'][0]['id'], TARGET, 'enabled')
        assert db.execute('SELECT count(*) FROM connection_labels').fetchone()[0] == 0
        storage.check_integrity(db)
