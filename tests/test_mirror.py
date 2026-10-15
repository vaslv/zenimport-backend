from contextlib import closing
from copy import deepcopy
from decimal import Decimal
import sqlite3

import httpx
import pytest
from zenmoney_api import Client
from zenmoney_api.protocol import KINDS, encode_json

from backend import storage
from backend.mirror import Mirror
from backend.capture_policy import SyncError
from mirror_factory import Fixture, movement, snapshot


@pytest.fixture
def f(tmp_path):
    value = Fixture(tmp_path / 'mirror.sqlite3')
    yield value
    value.close()


def test_full_and_incremental_preserve_all_kinds_unknown_fields_and_exact_raw(f):
    full = snapshot()
    for kind in KINDS:
        if not full[kind]:
            full[kind] = [{'user': 1, 'date': '2026-10-01', 'tag': None}] if kind == 'budget' else [{'id': 'fixture-' + kind}]
    full['transaction'] = [movement('tx', amount=Decimal('123456789012345678901234.123456789012'),
                                       custom={'amount': Decimal('0.12345678901234567890123456789')})]
    full['futureCollection'] = [{'unknown': Decimal('1.00000000000000000000001')}]
    raw = encode_json(full)
    f.refresh(raw)
    assert f.requests[0]['serverTimestamp'] == 0 and 'forceFetch' in f.requests[0]
    f.refresh({'serverTimestamp': 101, 'account': [{'id': 'zen-vnd', 'title': 'Changed on mobile', 'instrument': 2, 'type': 'cash'}]})
    with storage.transaction(f.path, read_only=True) as db:
        assert {r[0] for r in db.execute('SELECT DISTINCT kind FROM zen_entities')} == set(KINDS)
        assert db.execute('SELECT payload FROM zen_sync_runs ORDER BY rowid LIMIT 1').fetchone()[0].encode() == raw
        assert db.execute('SELECT outcome FROM zen_movements WHERE id=\'tx\'').fetchone()[0] == '123456789012345678901234.123456789012'
        assert db.execute('SELECT count(*) FROM zen_tags').fetchone()[0] == 1
        assert db.execute('SELECT cursor FROM zen_mirror_state').fetchone()[0] == 101
        assert 'Changed on mobile' in db.execute("SELECT data FROM zen_entities WHERE kind='account' AND id='zen-vnd'").fetchone()[0]
    assert f.requests[1]['serverTimestamp'] == 100 and 'forceFetch' not in f.requests[1]


def test_soft_hard_deletion_suppress_known_bank_and_preserve_payload(f):
    bank = f.bank()
    original = '{ "id": "known", "comment": "never overwrite" }'
    with storage.transaction(f.path) as db:
        db.execute("UPDATE bank_processing SET state='uncertain'")
        db.execute("INSERT INTO deliveries VALUES (?,'known',?,'uncertain',1,'fixture',NULL,0,0)", (bank, original))
    f.refresh(snapshot(transaction=[movement('known', deleted=True)]))
    f.refresh({'serverTimestamp': 101, 'deletion': [{'object': 'transaction', 'id': 'known', 'stamp': 100, 'extension': 'preserved'}]})
    f.refresh({'serverTimestamp': 102, 'transaction': [movement('known', comment='Restored by mobile')]})
    with storage.transaction(f.path, read_only=True) as db:
        assert db.execute('SELECT state FROM bank_processing').fetchone()[0] == 'suppressed'
        assert db.execute('SELECT state,payload FROM deliveries').fetchone()[:] == ('suppressed', original)
        assert db.execute('SELECT count(*) FROM zen_tombstones').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM zen_deletions').fetchone()[0] == 1


def test_existing_uuid_confirms_even_after_mobile_edits(f):
    bank = f.bank()
    original = '{"id":"known","outcome":100,"tag":["category-a"]}'
    with storage.transaction(f.path) as db:
        db.execute("UPDATE bank_processing SET state='uncertain'")
        db.execute("INSERT INTO deliveries VALUES (?,'known',?,'uncertain',1,'fixture',NULL,0,0)", (bank, original))
    f.refresh(snapshot(transaction=[movement('known', 'category-b', amount=200, comment='Mobile edit')]))
    with storage.transaction(f.path, read_only=True) as db:
        assert db.execute('SELECT state FROM bank_processing').fetchone()[0] == 'delivered'
        assert db.execute('SELECT state,payload FROM deliveries').fetchone()[:] == ('delivered', original)
        assert db.execute('SELECT zen_id FROM processing_receipts').fetchone()[0] == 'known'


def test_absence_in_partial_or_full_snapshot_does_not_prove_write_failure(f):
    bank = f.bank()
    with storage.transaction(f.path) as db:
        db.execute("UPDATE bank_processing SET state='uncertain'")
        db.execute("INSERT INTO delivery_attempts VALUES ('attempt',?,'missing',0,'unknown','{}')", (bank,))
    f.refresh(snapshot())
    f.refresh({'serverTimestamp': 101})
    with storage.transaction(f.path, read_only=True) as db:
        assert db.execute('SELECT state FROM bank_processing').fetchone()[0] == 'uncertain'
        assert db.execute('SELECT count(*) FROM zen_tombstones').fetchone()[0] == 0


@pytest.mark.parametrize('invalid', [
    b'not-json', b'{"serverTimestamp":true}',
    {'serverTimestamp': 99}, {'serverTimestamp': 101, 'transaction': [movement('x', income='bad')]},
    {'serverTimestamp': 101, 'transaction': [movement('x', tag=False)]},
    {'serverTimestamp': 101, 'transaction': [movement('x'), movement('x')]},
])
def test_invalid_response_keeps_cursor_entities_and_original_archive(f, invalid):
    f.refresh(snapshot(transaction=[movement('kept')]))
    f.responses.append(invalid)
    with pytest.raises(SyncError):
        f.mirror.refresh(force=True)
    with storage.transaction(f.path, read_only=True) as db:
        assert db.execute('SELECT cursor,ready FROM zen_mirror_state').fetchone()[:] == (100, 0)
        assert db.execute('SELECT count(*) FROM zen_sync_runs').fetchone()[0] == 1
        assert db.execute("SELECT 1 FROM zen_entities WHERE id='kept'").fetchone()
        assert not db.execute("SELECT 1 FROM service_meta WHERE key='zenmoney_refreshed_at'").fetchone()


def test_initial_missing_collections_does_not_claim_fresh_catalog(f):
    f.responses.append({'serverTimestamp': 100, 'instrument': [], 'tag': [], 'transaction': []})
    with pytest.raises(SyncError):
        f.mirror.refresh()
    assert f.mirror.info()['zenmoney_state'] == 'unavailable'
    with storage.transaction(f.path, read_only=True) as db:
        assert db.execute('SELECT cursor,needs_full FROM zen_mirror_state').fetchone()[:] == (0, 1)


def test_network_outside_write_transaction_and_atomic_storage_failure(f, monkeypatch):
    def handle(request):
        with storage.transaction(f.path) as db:
            db.execute("INSERT INTO service_meta VALUES ('network-did-not-hold-write-lock','yes')")
        return httpx.Response(200, content=encode_json(snapshot()))
    f.responses.append(handle)
    import backend.predictions as predictions
    original = predictions.rebuild_evidence
    def fail(db):
        original(db)
        raise sqlite3.OperationalError('fixture-apply-failure')
    monkeypatch.setattr(predictions, 'rebuild_evidence', fail)
    with pytest.raises(SyncError):
        f.mirror.refresh()
    with storage.transaction(f.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM zen_entities').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM zen_sync_runs').fetchone()[0] == 0
        assert db.execute('SELECT cursor FROM zen_mirror_state').fetchone()[0] == 0


@pytest.mark.parametrize('failure', [httpx.ReadTimeout('fixture'), httpx.Response(503), httpx.Response(429)])
def test_only_temporary_reads_retry_and_can_recover(f, failure):
    f.responses.extend([failure, snapshot()])
    f.mirror.refresh()
    assert len(f.requests) == 2 and f.mirror.info()['zenmoney_state'] == 'ready'


@pytest.mark.parametrize('status', [400, 401, 403])
def test_permanent_read_errors_do_not_retry(f, status):
    f.responses.append(httpx.Response(status, text='fixture-private-token-and-finance'))
    with pytest.raises(SyncError) as error:
        f.mirror.refresh()
    assert 'fixture-private' not in str(error.value)
    assert len(f.requests) == 1


def test_freshness_restart_cache_and_failed_refresh(f):
    f.refresh(snapshot())
    restarted = Mirror(f.path, f.client, 'fixture-zenmoney-token', lambda: f.now, sleep=lambda _: None)
    restarted.refresh()
    assert len(f.requests) == 1
    f.now += 301
    assert restarted.info()['zenmoney_state'] == 'unavailable'
    f.responses.extend([httpx.Response(503), httpx.Response(503)])
    with pytest.raises(SyncError):
        restarted.refresh()
    assert restarted.info()['last_zenmoney_refresh'] is not None


def test_concurrent_newer_generation_prevents_old_response_application(f):
    other = Mirror(f.path, f.client, 'fixture-zenmoney-token', lambda: f.now, sleep=lambda _: None)
    def stale_request(request):
        f.responses.append(snapshot(cursor=200, account=[]))
        other.refresh(force=True)
        return httpx.Response(200, content=encode_json(snapshot(cursor=100)))
    f.responses.append(stale_request)
    f.mirror.refresh(force=True)
    with storage.transaction(f.path, read_only=True) as db:
        assert db.execute('SELECT cursor FROM zen_mirror_state').fetchone()[0] == 200
        assert db.execute('SELECT count(*) FROM zen_sync_runs').fetchone()[0] == 1


def test_credential_change_requires_full_read_and_old_reader_cannot_overwrite(f):
    f.refresh(snapshot())
    replacement = Mirror(f.path, f.client, 'fixture-other-token', lambda: f.now, sleep=lambda _: None)
    assert replacement.info()['zenmoney_state'] == 'unavailable'
    with pytest.raises(SyncError):
        f.mirror.refresh(force=True)
    f.responses.append(snapshot(cursor=200, transaction=[]))
    replacement.refresh()
    assert f.requests[-1]['serverTimestamp'] == 0


def test_unexpected_projection_failure_revokes_previous_freshness(f, monkeypatch):
    f.refresh()
    import backend.predictions as predictions
    def fail(db):
        raise RuntimeError('fixture-unexpected-projection-failure')
    monkeypatch.setattr(predictions, 'rebuild_evidence', fail)
    f.responses.append({'serverTimestamp': 101, 'transaction': [movement('rolled-back')]})
    with pytest.raises(RuntimeError):
        f.mirror.refresh(force=True)
    assert f.mirror.info()['zenmoney_state'] == 'unavailable'
    with storage.transaction(f.path, read_only=True) as db:
        assert db.execute('SELECT cursor FROM zen_mirror_state').fetchone()[0] == 100
        assert db.execute('SELECT count(*) FROM zen_sync_runs').fetchone()[0] == 1
        assert not db.execute("SELECT 1 FROM zen_entities WHERE id='rolled-back'").fetchone()
