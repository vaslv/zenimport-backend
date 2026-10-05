import copy
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import transaction, entities
from backend.zenmoney import ZenMoney, DEMO_ACCOUNT, apply_diff

TOKEN = 'test-token-that-is-long-enough'
IMPORT = {'bank': 'demo', 'adapter_version': '1', 'account': 'bank-001', 'currency': 'VND',
          'transactions': [{'external_id': 'tx-001', 'date': '2026-10-01', 'amount_minor': -65000,
                            'description': 'Coffee', 'raw': {'reference': 'tx-001', 'date': '01/10/2026',
                            'amount': '-65,000', 'description': 'Coffee'}}]}


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / 'test.sqlite3'
    client = TestClient(create_app(path, TOKEN, 'demo'))
    client.headers['Authorization'] = f'Bearer {TOKEN}'
    return client, path


def prepare(client):
    assert client.post('/api/imports', json=IMPORT).status_code == 200
    assert client.post('/api/zenmoney/sync', json={}).status_code == 200
    state = client.get('/api/state').json()
    account = state['bank_accounts'][0]['id']
    assert client.put(f'/api/accounts/{account}/mapping', json={'zenmoney_account': DEMO_ACCOUNT}).status_code == 200
    return state['review'][0]['id']


def approve(client, identifier, **kwargs):
    return client.post(f'/api/review/{identifier}/confirm', json={'action': 'create', **kwargs})


def test_end_to_end_and_restart(setup):
    client, path = setup
    identifier = prepare(client)
    assert client.post('/api/imports', json=IMPORT).json()['duplicates'] == 1
    assert approve(client, identifier).json()['state'] == 'queued'
    assert client.get('/api/state').json()['zenmoney_transactions'] == []
    restarted = TestClient(create_app(path, TOKEN, 'demo'))
    restarted.headers.update(client.headers)
    assert restarted.post('/api/outbox/send', json={}).json()['results'][0]['state'] == 'sent'
    assert restarted.post('/api/outbox/send', json={}).json()['results'] == []
    state = restarted.get('/api/state').json()
    assert len(state['zenmoney_transactions']) == 1
    assert state['zenmoney_transactions'][0]['outcome'] == 65000
    assert state['review'][0]['raw']['amount'] == '-65,000'


def test_atomic_import_conflict(setup):
    client, _ = setup
    prepare(client)
    changed = copy.deepcopy(IMPORT)
    first = copy.deepcopy(changed['transactions'][0]); first['external_id'] = 'new'
    changed['transactions'][0]['amount_minor'] = -1
    changed['transactions'].insert(0, first)
    assert client.post('/api/imports', json=changed).status_code == 409
    assert len(client.get('/api/state').json()['review']) == 1


def test_same_amount_distinct_bank_ids_preserved(setup):
    client, _ = setup
    data = copy.deepcopy(IMPORT)
    second = copy.deepcopy(data['transactions'][0]); second['external_id'] = 'tx-002'
    data['transactions'].append(second)
    assert client.post('/api/imports', json=data).json()['added'] == 2


def test_auth_and_validation(setup):
    client, _ = setup
    assert client.get('/api/state', headers={'Authorization': 'bad'}).status_code == 401
    data = {**IMPORT, 'html': '<html>private</html>'}
    assert client.post('/api/imports', json=data).status_code == 422
    data = copy.deepcopy(IMPORT); data['transactions'][0]['amount_minor'] = 1.5
    assert client.post('/api/imports', json=data).status_code == 422
    data['transactions'][0]['amount_minor'] = 0
    assert client.post('/api/imports', json=data).status_code == 422
    assert client.post('/api/imports', content=b'x' * 2_000_001).status_code == 413


def add_candidate(path, identifier='existing'):
    with transaction(path) as db:
        apply_diff(db, {'serverTimestamp': 1, 'transaction': [{'id': identifier,
                   'incomeAccount': DEMO_ACCOUNT, 'outcomeAccount': DEMO_ACCOUNT,
                   'income': 0, 'outcome': 65000, 'date': '2026-10-02', 'deleted': False}]})


def test_stale_candidates_and_link(setup):
    client, path = setup
    identifier = prepare(client)
    add_candidate(path)
    assert approve(client, identifier).status_code == 409
    assert client.post(f'/api/review/{identifier}/confirm', json={
        'action': 'link', 'match_id': 'existing', 'reviewed_candidates': ['existing']}).status_code == 200
    assert client.get('/api/state').json()['review'][0]['state'] == 'linked'
    assert client.post('/api/outbox/send', json={}).json()['results'] == []


def test_new_candidate_after_confirmation_blocks_send(setup):
    client, path = setup
    identifier = prepare(client)
    approve(client, identifier)
    add_candidate(path)
    assert client.post('/api/outbox/send', json={}).json()['results'][0]['state'] == 'blocked'
    assert len(client.get('/api/state').json()['zenmoney_transactions']) == 1


def test_currency_mismatch_and_mapping_freeze(setup):
    client, path = setup
    identifier = prepare(client)
    state = client.get('/api/state').json()
    account = state['bank_accounts'][0]['id']
    with transaction(path) as db:
        db.execute("UPDATE bank_accounts SET currency='USD' WHERE id=?", (account,))
    assert approve(client, identifier).status_code == 409
    with transaction(path) as db:
        db.execute("UPDATE bank_accounts SET currency='VND' WHERE id=?", (account,))
    assert approve(client, identifier).status_code == 200
    assert client.put(f'/api/accounts/{account}/mapping', json={'zenmoney_account': DEMO_ACCOUNT}).status_code == 409


class LostResponse(ZenMoney):
    def __init__(self):
        super().__init__('demo', '')
        self.remote = {}
        self.writes = 0

    def diff(self, payload):
        result = super().diff(payload)
        if payload.get('transaction'):
            self.writes += 1
            for item in payload['transaction']:
                self.remote[item['id']] = item
            raise TimeoutError('Remote committed, response lost')
        result['transaction'] = list(self.remote.values())
        return result


def test_lost_response_reconciles_without_second_write(tmp_path):
    remote = LostResponse()
    client = TestClient(create_app(tmp_path / 'db', TOKEN, 'demo', remote))
    client.headers['Authorization'] = f'Bearer {TOKEN}'
    identifier = prepare(client)
    approve(client, identifier)
    assert client.post('/api/outbox/send', json={}).json()['results'][0]['state'] == 'uncertain'
    assert client.post('/api/outbox/send', json={}).json()['results'][0]['state'] == 'sent'
    assert remote.writes == 1


def test_concurrent_confirmation_creates_one_outbox(setup):
    client, _ = setup
    identifier = prepare(client)
    with ThreadPoolExecutor(2) as pool:
        statuses = list(pool.map(lambda _: approve(client, identifier).status_code, range(2)))
    assert sorted(statuses) == [200, 409]
    assert len(client.get('/api/state').json()['outbox']) == 1


def test_diff_deletions_and_bad_cursor_roll_back(setup):
    _, path = setup
    add_candidate(path)
    with transaction(path) as db:
        apply_diff(db, {'serverTimestamp': 2, 'deletion': [{'object': 'transaction', 'id': 'existing'}]})
        assert entities(db, 'transaction') == []
    with pytest.raises(ValueError), transaction(path) as db:
        apply_diff(db, {'serverTimestamp': 'bad'})
    with transaction(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0] == '2'


def test_mode_isolation(setup):
    _, path = setup
    with pytest.raises(RuntimeError):
        create_app(path, TOKEN, 'live', ZenMoney('demo', ''))


def test_reopen_queued_but_never_uncertain(setup):
    client, path = setup
    identifier = prepare(client)
    approve(client, identifier)
    assert client.post(f'/api/outbox/{identifier}/reopen', json={}).status_code == 200
    assert client.get('/api/state').json()['review'][0]['state'] == 'pending'
    approve(client, identifier)
    with transaction(path) as db:
        db.execute("UPDATE outbox SET state='uncertain'")
    assert client.post(f'/api/outbox/{identifier}/reopen', json={}).status_code == 409


def test_live_http_contract(monkeypatch):
    import httpx
    client_type = httpx.Client
    captured = []
    def remote(request):
        captured.append(request)
        return httpx.Response(200, json={'serverTimestamp': 123, 'account': [], 'transaction': []})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client_type(transport=httpx.MockTransport(remote), **kwargs))
    result = ZenMoney('live', 'server-only-secret').diff({'currentClientTimestamp': 100, 'serverTimestamp': 0})
    assert result['serverTimestamp'] == 123
    assert str(captured[0].url) == 'https://api.zenmoney.ru/v8/diff/'
    assert captured[0].headers['Authorization'] == 'Bearer server-only-secret'
    assert json.loads(captured[0].content)['serverTimestamp'] == 0


def test_deleted_transaction_is_not_a_candidate(setup):
    client, path = setup
    identifier = prepare(client)
    add_candidate(path)
    with transaction(path) as db:
        tx = entities(db, 'transaction')[0]
        tx['deleted'] = True
        apply_diff(db, {'serverTimestamp': 2, 'transaction': [tx]})
    assert client.get('/api/state').json()['review'][0]['candidates'] == []
    assert approve(client, identifier).status_code == 200
