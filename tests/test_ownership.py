"""Account rebindings cannot claim an unrelated leg of a known remote transfer."""
from contextlib import closing
from copy import deepcopy
import sqlite3
from uuid import uuid4

import pytest

from backend import existing, ownership, storage
from capture_factory import TARGET, record
from delivery_factory import DeliveryHarness
from mirror_factory import movement

OTHER = 'fixture-other-vnd'


@pytest.fixture
def h(tmp_path):
    value = DeliveryHarness(tmp_path / 'ownership.sqlite3')
    yield value
    value.close()


def finish_first(h, direction):
    job, _, auth, _ = h.capture(changes={'external_id': 'vib:id:580K26A0FIXTURE', 'direction': direction})
    for interval in job['accounts'][0]['intervals'][1:]:
        assert h.page(job, interval, auth).status_code == 200
        assert h.complete(job, interval, auth).status_code == 200
    h.worker.run_once()
    assert not h.remote.writes
    return next(iter(h.bank_ids().values()))


def rebind(h, target):
    with storage.transaction(h.path, read_only=True) as db:
        version = db.execute('SELECT settings_version FROM bank_accounts WHERE id=?', (h.account_id,)).fetchone()[0]
    assert h.configure(h.account_id, version=version, target=target).status_code == 200


def v5_copy(h):
    # Recreate the pre-fix schema on this disposable fixture only. All v5
    # receipts, authorizations and immutable request payloads remain intact.
    with closing(storage.configured(sqlite3.connect(h.path))) as db:
        db.execute('DROP TABLE connection_labels')
        db.execute('DROP TRIGGER processing_target_immutable')
        db.execute('DROP TRIGGER processing_target_preserved')
        db.execute('DROP TABLE processing_targets')
        db.execute('DELETE FROM schema_migrations WHERE version>=6')
        db.execute('PRAGMA user_version=5')
        db.commit()


@pytest.mark.parametrize('incoming_first', [False, True])
@pytest.mark.parametrize('remap', ['before', 'after', 'upgrade'])
def test_rebinding_does_not_create_the_other_side_of_a_transfer(h, incoming_first, remap):
    first_target, second_target = (OTHER, TARGET) if incoming_first else (TARGET, OTHER)
    if incoming_first:
        rebind(h, first_target)
    if remap == 'before':
        rebind(h, second_target)
        rebind(h, first_target)
    zen = str(uuid4())
    h.remote.transactions[zen] = movement(zen, date='2026-09-15', outcomeAccount=TARGET,
        incomeAccount=OTHER, income=100, outcome=100, comment='CT DI:580K26A0FIXTURE')
    original = deepcopy(h.remote.transactions)
    first_bank = finish_first(h, 'income' if incoming_first else 'expense')
    if remap in ('after', 'upgrade'):
        rebind(h, second_target)
        with storage.transaction(h.path, read_only=True) as db:
            # Ownership survives a real change of the current mapping.
            assert ownership.claims(db)[0] == {(zen, first_target)}
            assert zen in [t['id'] for t in existing.pool(db)[second_target, '2026-09-15']]
        if remap == 'upgrade':
            v5_copy(h)
            backup = storage.initialize(h.path)
            assert backup.stat().st_mode & 0o777 == 0o600
            with closing(sqlite3.connect(backup)) as db:
                assert db.execute('PRAGMA user_version').fetchone()[0] == 5
            assert storage.initialize(h.path) is None
        rebind(h, first_target)
    connection = h.discover(names=('account-2',))
    second = next(a['id'] for a in connection['accounts'] if a['source_key'] == 'account-2')
    assert h.configure(second, target=second_target).status_code == 200
    job, auth = h.leased(h.connection['id'])
    interval = next(a for a in job['accounts'] if a['account_id'] == second)['intervals'][0]
    row = record(interval, 'vib:id:580K26A0OTHERBANK', direction='expense' if incoming_first else 'income', amount='100')
    assert h.page(job, interval, auth, records=[row]).status_code == 200
    assert h.complete(job, interval, auth, records=1).status_code == 200
    h.due()
    assert h.remote.writes == [] and h.remote.transactions == original
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM processing_receipts WHERE zen_id=?', (zen,)).fetchone()[0] == 2
        assert ownership.claims(db) == ({(zen, first_target), (zen, second_target)}, set())
        assert db.execute('SELECT target_id FROM processing_targets WHERE bank_id=?', (first_bank,)).fetchone()[0] == first_target


@pytest.mark.parametrize('safe_count', [1, 21])
def test_unknown_old_target_blocks_only_affected_account_day_without_financial_write(h, safe_count):
    zen = str(uuid4())
    h.remote.transactions[zen] = movement(zen, date='2026-09-15', incomeAccount=TARGET, outcomeAccount=TARGET)
    job, auth = h.leased(h.connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    rows = [record(interval, 'old-link', amount='100'), record(interval, 'unmatched', amount='100')] + [
        record(interval, 'next-day-'+str(i), amount='70', local_date='2026-09-16', occurred_at='2026-09-16T10:00:00+07:00')
        for i in range(safe_count)]
    assert h.page(job, interval, auth, records=rows).status_code == 200
    assert h.complete(job, interval, auth, records=len(rows)).status_code == 200
    old = h.bank_ids()['old-link']
    pending = h.bank_ids()['unmatched']
    with storage.transaction(h.path) as db:
        db.execute('INSERT INTO processing_receipts VALUES (?,?,?,?)', (old, zen, 'legacy_confirmation', 0))
        db.execute("UPDATE bank_processing SET state='delivered' WHERE bank_id=?", (old,))
        # Make the blocked row come after a full 20-item financial write batch.
        db.execute('UPDATE delivery_authorizations SET authorized_at=authorized_at+1 WHERE bank_id=?', (pending,))
    h.worker.run_once()
    assert [str(t['outcome']) for batch in h.remote.writes for t in batch] == ['70']*min(safe_count, 20)
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT state FROM bank_processing WHERE bank_id=?', (pending,)).fetchone()[0] == 'pending'
        assert not db.execute('SELECT 1 FROM delivery_attempts WHERE bank_id=?', (pending,)).fetchone()
        assert not db.execute('SELECT 1 FROM processing_targets WHERE bank_id=?', (old,)).fetchone()
    result = h.job(job)
    assert result['delivery_state'] == 'blocked'
    assert any(e['code'] == 'existing_ownership_unknown' for e in result['errors'])
    h.due()
    h.due()
    assert sum(map(len, h.remote.writes)) == safe_count


def test_old_payload_claim_survives_mobile_edits_and_current_mapping_change(h):
    job, _, auth, _ = h.capture()
    for interval in job['accounts'][0]['intervals'][1:]:
        assert h.page(job, interval, auth).status_code == 200
        assert h.complete(job, interval, auth).status_code == 200
    h.worker.run_once()
    zen = next(iter(h.remote.transactions))
    rebind(h, OTHER)
    h.remote.transactions[zen].update(incomeAccount=OTHER, outcomeAccount=OTHER)
    h.worker.mirror.refresh(force=True)
    # Even if authorization metadata is absent in an older imported delivery,
    # the attempted immutable payload proves its original account.
    v5_copy(h)
    with closing(storage.configured(sqlite3.connect(h.path))) as db:
        db.execute('DROP TRIGGER delivery_authorization_preserved')
        db.execute('DELETE FROM delivery_authorizations')
        # Restore the schema trigger verbatim for genuine v5 ownership validation.
        statement = storage.B5_SQL.split('CREATE TRIGGER delivery_authorization_preserved')[1].split('CREATE TRIGGER')[0]
        storage.execute_schema(db, 'CREATE TRIGGER delivery_authorization_preserved' + statement)
        db.commit()
    storage.initialize(h.path)
    with storage.transaction(h.path) as db:
        assert ownership.claims(db) == ({(zen, TARGET)}, set())
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('UPDATE processing_targets SET target_id=?', (OTHER,))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('DELETE FROM processing_targets')


def test_failed_v6_upgrade_rolls_back_and_leaves_original_backup(h, monkeypatch):
    finish_first_setup = str(uuid4())
    h.remote.transactions[finish_first_setup] = movement(finish_first_setup, date='2026-09-15',
        incomeAccount=TARGET, outcomeAccount=TARGET)
    finish_first(h, 'expense')
    v5_copy(h)
    original = ownership.backfill
    def fail(db):
        original(db)
        raise RuntimeError('fixture-v6-failure')
    monkeypatch.setattr(ownership, 'backfill', fail)
    with pytest.raises(RuntimeError, match='fixture-v6-failure'):
        storage.initialize(h.path)
    with closing(storage.connect_readonly(h.path)) as db:
        assert storage.validate_owner(db) == 5
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='processing_targets'").fetchone()
    assert len(list(h.path.parent.glob(f'*.pre-v{storage.SCHEMA_VERSION}-*'))) == 1


def test_v5_group_keeps_original_authorized_account_after_rebinding(h):
    from test_existing_groups import prepare
    zens = prepare(h, [-100], [-40, -60])
    h.worker.run_once()
    # No more page reads are needed to test the immutable group evidence.
    with storage.transaction(h.path) as db:
        db.execute("UPDATE capture_jobs SET capture_state='complete'")
    rebind(h, OTHER)
    v5_copy(h)
    storage.initialize(h.path)
    with storage.transaction(h.path, read_only=True) as db:
        assert ownership.claims(db) == ({(z, TARGET) for z in zens}, set())
        storage.check_integrity(db)


def test_v5_cleanup_cannot_inherit_original_delivery_target_after_mobile_move(h):
    job, _, auth, _ = h.capture()
    for interval in job['accounts'][0]['intervals'][1:]:
        assert h.page(job, interval, auth).status_code == 200
        assert h.complete(job, interval, auth).status_code == 200
    h.worker.run_once()
    duplicate = next(iter(h.remote.transactions))
    bank = next(iter(h.bank_ids().values()))
    # The old cleanup selected the current B, whereas delivery authorization
    # still refers to A. That authorization does not prove the retained UUID.
    rebind(h, OTHER)
    h.remote.transactions[duplicate].update(incomeAccount=OTHER, outcomeAccount=OTHER)
    keep = str(uuid4())
    h.remote.transactions[keep] = movement(keep, date='2026-09-15', incomeAccount=OTHER, outcomeAccount=OTHER)
    h.worker.mirror.refresh(force=True)
    with storage.transaction(h.path) as db:
        group = str(uuid4())
        db.execute('INSERT INTO link_groups VALUES (?,?,?,?,?)', (group, 'single', h.account_id, h.now, 'duplicate_cleanup'))
        db.execute('INSERT INTO link_bank_members VALUES (?,?)', (group, bank))
        db.execute('INSERT INTO link_zen_members VALUES (?,?)', (group, keep))
    v5_copy(h)
    storage.initialize(h.path)
    with storage.transaction(h.path, read_only=True) as db:
        assert ownership.claims(db) == ({(duplicate, TARGET)}, {keep})
        assert ownership.blocked(db, [({'id':'fixture-new', 'local_date':'2026-09-15'}, {'id':OTHER})]) == {'fixture-new'}
        assert not db.execute('SELECT 1 FROM processing_targets WHERE zen_id=?', (keep,)).fetchone()
