"""SDK 0.6 integration, complete projections and recovery of legacy precision."""
import sqlite3
from decimal import Decimal
from copy import deepcopy
import json as legacy_json
import httpx
import pytest
from fastapi.testclient import TestClient
from zenmoney_api import Client, models, transactions as tx
from zenmoney_api.protocol import encode_json, decode_json, decode_diff
from backend import jsoncodec as json
from backend.app import create_app
from backend.db import initialize, transaction, entities
from backend.zenstore import FIELDS, TABLES, project, store_diff
from backend.category_send import fingerprint
from test_flow import TOKEN


def operation(**fields):
    return tx.expense(user=1, id='one', date='2026-10-14', account='cash', instrument=1,
                      amount=Decimal('12.250000000000000001'), **fields)


def test_every_sdk_field_has_a_projection_or_category_relation():
    for kind in TABLES:
        name = {'reminderMarker': 'ReminderMarker'}.get(kind, kind.capitalize())
        model = getattr(models, name, None)
        if model:
            assert set(model.__annotations__) - {'id', 'changed', 'deleted', 'tag'} <= FIELDS[kind].keys()
    assert 'tag' in FIELDS['budget']  # Its single category is part of the composite key.


def test_exact_json_and_fingerprint_do_not_round_or_depend_on_trailing_zero():
    amount = Decimal('12.250000000000000001')
    assert json.loads(json.dumps({'amount': amount}))['amount'] == amount
    assert fingerprint({'amount': Decimal('100.000')}) == fingerprint({'amount': 100})
    assert fingerprint({'amount': Decimal('12.2500')}) == fingerprint({'amount': Decimal('12.25')})
    assert fingerprint({'amount': amount}) != fingerprint({'amount': Decimal('12.25')})
    with pytest.raises(ValueError): json.dumps({'amount': Decimal('NaN')})


def legacy_v10(path):
    initialize(path, 'live')
    data = {'serverTimestamp': 30, 'transaction': [operation()], 'futureCollection': [{'x': 'keep'}]}
    with transaction(path) as db:
        store_diff(db, decode_diff(encode_json(data), 0), 0)
        # Old mirror and full entity JSON passed through binary float; archive did not.
        rounded = legacy_json.dumps({**data['transaction'][0], 'outcome': float(data['transaction'][0]['outcome'])})
        db.execute('UPDATE zen_transaction SET data=?,outcome=?', (rounded, '12.250000000000000001'))
        db.execute('CREATE TABLE zen_entities(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id))')
        db.execute('INSERT INTO zen_entities VALUES (?,?,?)', ('transaction', 'one', rounded))
        db.execute("INSERT INTO bank_accounts VALUES ('bank','demo','123','VND','cash')")
        db.execute("INSERT INTO bank_transactions VALUES ('b','bank','ext','2026-10-14',-12,'Shop','{}','queued','draft')")
        old = operation();old['id'] = 'draft';old.pop('viewed');old.pop('mcc')
        db.execute('INSERT INTO creation_jobs VALUES (?,?,?,?,NULL)', ('b','draft',json.dumps(old),'prepared'))
        db.execute('PRAGMA user_version=10')
    return data


def test_v10_migration_restores_precision_and_preserves_local_jobs_and_archive(tmp_path):
    path = tmp_path / 'db'
    data = legacy_v10(path)
    initialize(path, 'live')
    backups = list(tmp_path.glob('db.pre-v12-*.sqlite3'))
    assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backups[0]) as old:
        assert old.execute('PRAGMA user_version').fetchone()[0] == 10
    with transaction(path) as db:
        assert entities(db, 'transaction')[0]['outcome'] == Decimal('12.250000000000000001')
        assert db.execute('SELECT outcome FROM zen_transaction').fetchone()[0] == '12.250000000000000001'
        assert db.execute('SELECT payload FROM zen_sync_runs').fetchone()[0] == encode_json(data).decode()
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='zen_entities'").fetchone()
        job = dict(db.execute('SELECT * FROM creation_jobs').fetchone())
        assert job['zen_id'] == 'draft' and job['state'] == 'prepared'
        assert json.loads(job['payload'])['viewed'] is True
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0] == 1
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert json.loads(db.execute('SELECT report FROM zen_migrations').fetchone()[0])['restored_from_archives'] == 1
    initialize(path, 'live')
    assert len(list(tmp_path.glob('db.pre-v12-*.sqlite3'))) == 1


def test_failed_migration_rolls_back_schema_and_keeps_attempted_payload(tmp_path):
    path = tmp_path / 'db'
    legacy_v10(path)
    with transaction(path) as db:
        original = db.execute('SELECT payload FROM creation_jobs').fetchone()[0]
        db.execute("UPDATE creation_jobs SET state='uncertain'")
        db.execute('UPDATE zen_sync_runs SET payload=?', (json.dumps({'transaction': [{**operation(), 'viewed':'bad'}]}),))
        db.execute('ALTER TABLE zen_transaction DROP COLUMN viewed')
    with pytest.raises(ValueError): initialize(path, 'live')
    with transaction(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 10
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='zen_entities'").fetchone()
        assert 'viewed' not in {r['name'] for r in db.execute('PRAGMA table_info(zen_transaction)')}
        assert db.execute('SELECT payload FROM creation_jobs').fetchone()[0] == original


def test_snapshot_prunes_only_requested_present_collections_and_cascades_tags(tmp_path):
    path = tmp_path / 'db';initialize(path, 'demo')
    with transaction(path) as db:
        store_diff(db, {'serverTimestamp': 1, 'transaction': [operation(categories=['food'])], 'tag':[{'id':'food'}]})
        store_diff(db, {'serverTimestamp': 2, 'transaction': []})
        assert len(entities(db, 'transaction')) == 1
        store_diff(db, {'serverTimestamp': 3, 'transaction': []}, full_collections=['transaction','tag'])
        assert entities(db, 'transaction') == [] and len(entities(db, 'tag')) == 1
        assert db.execute('SELECT count(*) FROM zen_transaction_tags').fetchone()[0] == 0


def test_movement_arrays_and_currency_fields_are_preserved(tmp_path):
    path = tmp_path / 'db';initialize(path, 'demo')
    row = operation(opOutcome=Decimal('1.01'), opOutcomeInstrument=2, latitude=Decimal('10.123456789012345'),
                    longitude=Decimal('100.123456789012345'), incomeBankID='incoming', outcomeBankID='outgoing')
    movement = {k: row[k] for k in ('id','user','changed','income','outcome','incomeAccount','outcomeAccount',
                                  'incomeInstrument','outcomeInstrument')}
    with transaction(path) as db:
        store_diff(db, {'serverTimestamp':1,'transaction':[row],
            'reminder':[{**movement,'tag':['food','toys'],'points':[0,2],'interval':'week','step':7}],
            'reminderMarker':[{**movement,'tag':['food'],'reminder':row['id'],'state':'planned','isForecast':False}]})
        assert db.execute('SELECT op_outcome_instrument FROM zen_transaction').fetchone()[0] == '2'
        assert db.execute('SELECT latitude FROM zen_transaction').fetchone()[0] == '10.123456789012345'
        assert db.execute('SELECT count(*) FROM zen_reminder_tags').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM zen_reminder_marker_tags').fetchone()[0] == 1


def test_sdk_read_and_http_response_keep_exact_amounts(tmp_path):
    received = []
    data = {'serverTimestamp':1,'transaction':[operation()]}
    def remote(request):
        received.append(decode_json(request.content))
        return httpx.Response(200, content=encode_json(data))
    with Client('synthetic-token', transport=httpx.MockTransport(remote)) as sdk:
        with TestClient(create_app(tmp_path / 'db', TOKEN, 'live', zen_client=sdk)) as api:
            api.headers['Authorization'] = 'Bearer '+TOKEN
            assert api.post('/api/zenmoney/sync').status_code == 200
            raw = api.get('/api/zenmoney/history').content
            assert b'12.250000000000000001' in raw
            assert decode_json(raw)['items'][0]['outcome'] == Decimal('12.250000000000000001')
            assert b'12.250000000000000001' in api.get('/api/state').content
    assert received[0]['serverTimestamp'] == 0 and 'transaction' in received[0]['forceFetch']


def test_app_closes_owned_sdk_but_not_injected_sdk(tmp_path, monkeypatch):
    from backend import app
    class Owned:
        closed = False
        def close(self): self.closed = True
    client = Owned()
    monkeypatch.setenv('ZENMONEY_TOKEN', 'synthetic-token')
    monkeypatch.setattr(app, 'Client', lambda token: client)
    with TestClient(create_app(tmp_path/'owned', TOKEN, 'live')): pass
    assert client.closed
    client.closed = False
    with TestClient(create_app(tmp_path/'injected', TOKEN, 'live', zen_client=client)): pass
    assert not client.closed
