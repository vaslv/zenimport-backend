from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from backend import storage
from capture_factory import Harness, TARGET


@pytest.fixture
def h(tmp_path):
    value = Harness(tmp_path / 'capture.sqlite3')
    yield value
    value.close()


def test_bank_scope_reuses_uuid_but_profile_scope_does_not(h):
    first = h.ready()
    again = h.discover(profile='another-profile')
    assert first['id'] != again['id']
    assert first['accounts'][0]['id'] == again['accounts'][0]['id']
    assert again['accounts'][0]['selection'] == 'enabled'
    a = h.discover(profile='profile-a', scope='profile')['accounts'][0]['id']
    b = h.discover(profile='profile-b', scope='profile')['accounts'][0]['id']
    assert a != b and a != first['accounts'][0]['id']


def test_partial_account_discovery_preserves_and_complete_list_marks_absence(h):
    connection = h.ready(names=('one', 'two'))
    partial = h.discover(names=('one',), accounts_complete=False)
    assert len(partial['accounts']) == 2
    complete = h.discover(names=('one',))
    assert len(complete['accounts']) == 1
    restored = h.discover(names=('one', 'two'))
    assert {a['id'] for a in restored['accounts']} == {a['id'] for a in connection['accounts']}
    assert all(a['selection'] == 'enabled' for a in restored['accounts'])


@pytest.mark.parametrize('changes,code', [({'bank': 'unknown-bank'}, 'unsupported_bank'),
    ({'capabilities': {'timezone': 'UTC', 'currencies': ['VND'], 'max_window_days': 30, 'details': 'included'}}, 'validation_error'),
    ({'capabilities': {'timezone': 'Asia/Ho_Chi_Minh', 'currencies': ['VND'], 'max_window_days': 30, 'details': 'unavailable'}}, 'validation_error')])
def test_server_policy_not_only_client_capabilities(h, changes, code):
    payload = {
        'bank': 'vietinbank', 'profile_key': 'fixture-policy', 'profile_identity': 'user_selected', 'connector_version': '1',
            'capabilities': {'timezone': 'Asia/Ho_Chi_Minh', 'currencies': ['VND'], 'max_window_days': 30, 'details': 'included'}, 'accounts_complete': True, 'accounts': []}
    response = h.client.post('/api/v1/connections/discover', json=payload | changes)
    assert response.status_code == 422 and response.json()['error']['code'] == code


def test_account_settings_repeat_and_optimistic_conflict(h):
    connection = h.ready()
    identifier = connection['accounts'][0]['id']
    first = h.configure(identifier)
    assert first.status_code == 200 and first.json()['settings_version'] == 1
    stale = h.configure(identifier, start='2026-09-01')
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'settings_conflict'
    future = h.configure(identifier, version=20)
    assert future.status_code == 409
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM account_bindings').fetchone()[0] == 1


@pytest.mark.parametrize('target', ['fixture-usd', 'missing-target'])
def test_target_currency_and_existence(h, target):
    h.seed_mirror()
    identifier = h.discover()['accounts'][0]['id']
    result = h.configure(identifier, target=target)
    assert result.status_code == 409 and result.json()['error']['code'] == 'zenmoney_account_unavailable'


@pytest.mark.parametrize('flag', ['archive', 'deleted', 'debt', 'loan'])
def test_catalog_excludes_archived_deleted_debt_and_loan(h, flag):
    h.seed_mirror()
    with storage.transaction(h.path) as db:
        value = json.loads(db.execute("SELECT data FROM zen_entities WHERE kind='account' AND id=?", (TARGET,)).fetchone()[0])
        value.update({flag: True} if flag in ('archive', 'deleted') else {'type': flag})
        db.execute("UPDATE zen_entities SET data=? WHERE kind='account' AND id=?", (json.dumps(value), TARGET))
    response = h.client.get('/api/v1/zenmoney/accounts')
    assert response.status_code == 200
    assert TARGET not in [a['id'] for a in response.json()['accounts']]


def test_stale_catalog_not_claimed_fresh_and_disabled_settings_still_work(h):
    h.seed_mirror()
    identifier = h.discover()['accounts'][0]['id']
    h.now += 301
    assert h.client.get('/api/v1/zenmoney/accounts').status_code == 503
    assert h.configure(identifier).status_code == 503
    assert h.configure(identifier, selection='ignored').status_code == 200


def test_future_import_date_and_unsupported_scale(h):
    h.seed_mirror()
    identifier = h.discover()['accounts'][0]['id']
    response = h.configure(identifier, start='2026-10-16')
    assert response.status_code == 422
    source = {'source_key': 'bad-scale', 'identity_scope': 'bank', 'label': 'Fixture', 'masked_number': '', 'currency': 'VND', 'scale': 2, 'supported': True}
    payload = {'bank': 'vietinbank', 'profile_key': 'fixture-scale', 'profile_identity': 'user_selected', 'connector_version': '1',
        'capabilities': {'timezone': 'Asia/Ho_Chi_Minh', 'currencies': ['VND'], 'max_window_days': 30, 'details': 'included'}, 'accounts_complete': True, 'accounts': [source]}
    response = h.client.post('/api/v1/connections/discover', json=payload)
    assert response.status_code == 422 and response.json()['error']['code'] == 'unsupported_currency'


def test_unconfigured_is_not_silently_ignored(h):
    connection = h.discover(names=('one', 'two'))
    result = h.start(connection['id'])
    assert result.status_code == 409 and result.json()['error']['code'] == 'account_unconfigured'
    for account in connection['accounts']:
        assert h.configure(account['id'], selection='ignored').status_code == 200
    result = h.start(connection['id'])
    assert result.status_code == 409 and result.json()['error']['code'] == 'account_disabled'


def test_concurrent_settings_only_one_distinct_update_wins(h):
    h.seed_mirror()
    identifier = h.discover()['accounts'][0]['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda start: h.configure(identifier, start=start), ['2026-09-15', '2026-09-01']))
    assert sorted(r.status_code for r in responses) == [200, 409]
