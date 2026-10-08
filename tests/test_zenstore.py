import json
import sqlite3
from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient
from zenimport_zenmoney import Diff, KINDS
from backend.app import create_app
from backend.db import initialize, transaction, entities, SCHEMA
from backend.zenmoney import apply_diff, request_diff
from backend.zenstore import TABLES, summary, history

TOKEN = 'test-token-that-is-long-enough'


def tx(identifier='tx', **kw):
    return {'id': identifier, 'date': '2020-01-01', 'income': 0, 'outcome': 12.25,
            'incomeAccount': 'a', 'outcomeAccount': 'a', 'tag': ['food'], **kw}


@pytest.fixture
def path(tmp_path):
    path = tmp_path / 'db'
    initialize(path, 'demo')
    return path


def test_all_entities_and_lossless_archive(path):
    data = {kind: [{'id': 'one', 'futureField': {'x': 1}}] for kind in KINDS}
    data['transaction'] = [tx()]
    data['budget'] = [{'user': 1, 'date': '2020-01-01', 'tag': None, 'outcome': 5}]
    data['serverTimestamp'] = 50
    data['futureCollection'] = [{'id': 'f'}]
    raw = json.dumps(data).replace('12.25', '12.250000000000000001')
    with transaction(path) as db:
        apply_diff(db, Diff(json.loads(raw), raw), 0)
        assert all(summary(db)['counts'][kind] == 1 for kind in KINDS)
        assert db.execute('SELECT payload FROM zen_sync_runs').fetchone()[0] == raw
        assert db.execute('SELECT outcome FROM zen_transaction').fetchone()[0] == '12.250000000000000001'
        assert db.execute('SELECT tag_id FROM zen_transaction_tags').fetchone()[0] == 'food'
        assert history(db)['items'][0]['_amounts']['outcome'] == '12.250000000000000001'
        assert summary(db)['coverage_verified'] is False


def test_increment_updates_deletions_and_retry(path):
    seen = []
    class Remote:
        def diff(self, payload):
            seen.append(payload)
            if len(seen) == 1:
                return {'serverTimestamp': 10, 'transaction': [tx('a'), tx('b')]}
            return {'serverTimestamp': 11, 'transaction': [tx('a', tag=['other'], deleted=True)],
                    'deletion': [{'object': 'transaction', 'id': 'b'}]}
    remote = Remote()
    with transaction(path) as db:
        request_diff(db, remote)
    with transaction(path) as db:
        request_diff(db, remote)
        request_diff(db, remote)
        assert len(entities(db, 'transaction')) == 1
        assert history(db)['total'] == 0
        assert history(db, include_deleted=True)['total'] == 1
        assert db.execute('SELECT tag_id FROM zen_transaction_tags').fetchone()[0] == 'other'
        assert db.execute('SELECT count(*) FROM zen_deletions').fetchone()[0] == 2
    assert seen[0]['serverTimestamp'] == 0 and 'forceFetch' in seen[0]
    assert seen[1]['serverTimestamp'] == 10 and 'forceFetch' not in seen[1]


@pytest.mark.parametrize('bad', [tx(date='2020-02-31'), tx(outcome='bad'), tx(tag='food'), tx(hold=1)])
def test_malformed_entity_rolls_back_whole_diff(path, bad):
    with transaction(path) as db:
        apply_diff(db, {'serverTimestamp': 10, 'transaction': [tx()]}, 0)
    with pytest.raises(ValueError), transaction(path) as db:
        apply_diff(db, {'serverTimestamp': 11, 'account': [{'id': 'new'}], 'transaction': [bad]}, 10)
    with transaction(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0] == '10'
        assert entities(db, 'account') == []
        assert db.execute('SELECT count(*) FROM zen_sync_runs').fetchone()[0] == 1
        assert history(db)['items'][0]['outcome'] == 12.25


def test_interruption_and_backwards_cursor(path):
    with pytest.raises(RuntimeError), transaction(path) as db:
        apply_diff(db, {'serverTimestamp': 10, 'transaction': [tx()]}, 0)
        raise RuntimeError('crash before commit')
    with transaction(path) as db:
        assert summary(db)['transactions']['count'] == 0
        assert db.execute('SELECT count(*) FROM zen_sync_runs').fetchone()[0] == 0
    with pytest.raises(ValueError), transaction(path) as db:
        apply_diff(db, {'serverTimestamp': 2}, 3)


def test_migration_preserves_v1_and_mode(tmp_path):
    path = tmp_path / 'old'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO meta VALUES ('mode','demo')")
        db.execute("INSERT INTO meta VALUES ('cursor','100')")
        db.execute('INSERT INTO zen_entities VALUES (?,?,?)', ('transaction', 'old', json.dumps(tx('old'))))
        db.execute("INSERT INTO bank_accounts VALUES ('bank','demo','a','VND',NULL)")
        db.execute('PRAGMA user_version=1')
    initialize(path, 'demo')
    initialize(path, 'demo')
    with transaction(path) as db:
        assert history(db)['total'] == 1
        assert db.execute('SELECT count(*) FROM bank_accounts').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM zen_sync_runs').fetchone()[0] == 1
        assert db.execute('PRAGMA user_version').fetchone()[0] == 7
        assert db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0] == '100'
    with pytest.raises(RuntimeError):
        initialize(path, 'live')


def test_future_schema_rejected(path):
    with transaction(path) as db:
        db.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError):
        initialize(path, 'demo')
    with transaction(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 99


def test_paginated_history_and_filters(path):
    client = TestClient(create_app(path, TOKEN, 'demo'))
    client.headers['Authorization'] = f'Bearer {TOKEN}'
    records = [tx(f't{i:05}', date=str(date(2020, 1, 1) + timedelta(days=i)),
                  outcomeAccount='other' if i%2 else 'a', incomeAccount='other' if i%2 else 'a') for i in range(1200)]
    with transaction(path) as db:
        apply_diff(db, {'serverTimestamp': 50, 'transaction': records})
    first = client.get('/api/zenmoney/history?limit=200').json()
    second = client.get('/api/zenmoney/history?limit=200&offset=200').json()
    assert first['total'] == 1200 and first['has_more']
    assert len(first['items']) == 200
    assert not ({t['id'] for t in first['items']} & {t['id'] for t in second['items']})
    filtered = client.get('/api/zenmoney/history?start=2020-01-01&end=2020-01-10&account=a').json()
    assert filtered['total'] == 5
    assert client.get('/api/zenmoney/history?start=2021-01-01&end=2020-01-01').status_code == 422
    assert client.get('/api/zenmoney/history?limit=999').status_code == 422
    state = client.get('/api/state').json()
    assert len(state['zenmoney_transactions']) == 100
    assert state['zenmoney_summary']['transactions']['count'] == 1200
    assert state['zenmoney_summary']['transactions']['first_date'] == '2020-01-01'
    assert client.get('/api/zenmoney/history', headers={'Authorization': 'bad'}).status_code == 401


def test_migrated_mirror_requests_all_collections_and_retries_flag(path):
    with transaction(path) as db:
        db.execute("INSERT INTO meta VALUES ('cursor','100')")
        db.execute("INSERT INTO meta VALUES ('needs_full_sync','1')")
    seen = []
    class Remote:
        def diff(self, payload):
            seen.append(payload)
            if len(seen) == 1:
                raise TimeoutError()
            return {'serverTimestamp': 101, 'company': [{'id': 1, 'title': 'Bank'}]}
    with pytest.raises(TimeoutError), transaction(path) as db:
        request_diff(db, Remote())
    with transaction(path) as db:
        assert db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0] == '100'
        request_diff(db, Remote())
        assert not db.execute("SELECT 1 FROM meta WHERE key='needs_full_sync'").fetchone()
        assert len(entities(db, 'company')) == 1
    assert all(p['serverTimestamp'] == 0 and 'company' in p['forceFetch'] for p in seen)
