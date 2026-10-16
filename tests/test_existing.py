"""Bootstrap from an existing ZenMoney history without changing mobile data."""
from copy import deepcopy
from uuid import uuid4

import pytest

from backend import storage
from delivery_factory import DeliveryHarness
from capture_factory import TARGET, record
from mirror_factory import movement


@pytest.fixture
def h(tmp_path):
    value = DeliveryHarness(tmp_path / 'existing.sqlite3')
    yield value
    value.close()


def seed(h, **changes):
    identifier = str(uuid4())
    tx = movement(identifier, **({'date': '2026-09-15', 'incomeAccount': TARGET, 'outcomeAccount': TARGET,
        'comment': 'CT DI:580K26A0FIXTURE QR - Fixture'} | changes))
    h.remote.transactions[identifier] = tx
    return identifier


def capture(h, **changes):
    return h.capture(changes={'external_id': 'vib:id:580K26A0FIXTURE', **changes})


@pytest.mark.parametrize('direction', ['expense', 'income'])
def test_existing_reference_is_receipted_without_create_or_mobile_update(h, direction):
    identifier = seed(h, income=100 if direction == 'income' else 0, outcome=100 if direction == 'expense' else 0)
    original = deepcopy(h.remote.transactions)
    job, _, _, _ = capture(h, direction=direction)
    h.worker.run_once()
    assert h.remote.writes == [] and h.remote.transactions == original
    assert h.states() == ['delivered']
    assert h.job(job)['delivery']['delivered'] == 1
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT zen_id,origin FROM processing_receipts').fetchone()[:] == (identifier, 'bank_reference')
        assert db.execute('SELECT count(*) FROM delivery_attempts').fetchone()[0] == 0
    # Explicit mobile deletion is final, even after another synchronize click.
    del h.remote.transactions[identifier]
    h.remote.deletions.append({'object': 'transaction', 'id': identifier})
    h.worker.mirror.refresh(force=True)
    h.start(h.connection['id'])
    h.due()
    assert h.states() == ['suppressed'] and h.remote.writes == []


@pytest.mark.parametrize('change', [
    {'date': '2026-09-16'}, {'outcome': 101}, {'hold': True},
    {'outcomeAccount': 'fixture-other-vnd'}, {'outcomeInstrument': 3},
    {'income': 1},
])
def test_similarity_alone_is_not_an_identity(h, change):
    seed(h, **change)
    capture(h)
    h.worker.run_once()
    assert len(h.remote.writes) == 1


def test_extra_existing_copies_do_not_create_a_third_copy(h):
    older = seed(h, created=100)
    seed(h, created=200)
    capture(h)
    h.worker.run_once()
    assert h.remote.writes == []
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT zen_id,origin FROM processing_receipts').fetchone()[:] == (older, 'day_amount_order')


def test_mobile_transfer_is_recognized_by_unchanged_bank_leg(h):
    seed(h, incomeAccount='fixture-other-vnd', income=200, incomeInstrument=2)
    capture(h)
    h.worker.run_once()
    assert h.remote.writes == [] and h.states() == ['delivered']


def test_prepared_unattempted_uuid_is_discarded_when_original_exists(h):
    identifier = seed(h)
    capture(h)
    bank = next(iter(h.bank_ids().values()))
    with storage.transaction(h.path) as db:
        db.execute("INSERT INTO deliveries VALUES (?,?,?,'prepared',0,'fixture',NULL,0,1)", (bank, str(uuid4()), '{}'))
    h.worker.run_once()
    assert h.remote.writes == []
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 0
        assert db.execute('SELECT zen_id FROM processing_receipts').fetchone()[0] == identifier


def test_uncertain_write_never_changes_identity_to_similar_existing_record(h):
    capture(h)
    h.remote.mode = 'timeout_before'
    h.worker.run_once()
    seed(h)
    h.due()
    assert h.states() == ['uncertain'] and len(h.remote.writes) == 1


@pytest.mark.parametrize('direction', ['expense', 'income'])
@pytest.mark.parametrize('bank_count,remote_count', [(1, 1), (2, 2), (3, 2), (2, 3), (2, 0)])
def test_day_amount_pairs_in_time_order_and_sends_only_missing_count(h, direction, bank_count, remote_count):
    originals = []
    for i in range(remote_count):
        originals.append(seed(h, comment=None, created=100+i,
            income=100 if direction == 'income' else 0, outcome=100 if direction == 'expense' else 0))
    job, auth = h.leased(h.connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    rows = [record(interval, 'ordered-'+str(i), amount='100', direction=direction,
        occurred_at=f'2026-09-15T{10+i:02d}:00:00+07:00') for i in range(bank_count)]
    assert h.page(job, interval, auth, records=rows).status_code == 200
    assert h.complete(job, interval, auth, records=bank_count).status_code == 200
    h.worker.run_once()
    assert sum(map(len, h.remote.writes)) == max(0, bank_count-remote_count)
    bank_ids = h.bank_ids()
    with storage.transaction(h.path, read_only=True) as db:
        receipts = dict(db.execute('SELECT bank_id,zen_id FROM processing_receipts'))
        for i in range(min(bank_count, remote_count)):
            assert receipts[bank_ids['ordered-'+str(i)]] == originals[i]
    # The adopted UUIDs survive repeated reads/commands, including mobile edits.
    for identifier in originals:
        h.remote.transactions[identifier]['comment'] = 'Changed in mobile'
    h.worker.mirror.refresh(force=True)
    h.start(h.connection['id'])
    h.due()
    assert sum(map(len, h.remote.writes)) == max(0, bank_count-remote_count)


def test_equal_amount_order_does_not_train_a_guessed_recipient_category(h):
    for i in range(3):
        seed(h, comment=None, created=100+i)
    h.capture(3)
    h.worker.run_once()
    h.worker.mirror.refresh(force=True)
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM processing_receipts WHERE origin='day_amount_order'").fetchone()[0] == 3
        assert db.execute('SELECT count(*) FROM category_evidence').fetchone()[0] == 0


def test_shared_target_without_pending_peer_does_not_guess_which_bank_account(h):
    h.ready(names=('account-1', 'account-2'))
    seed(h, comment=None)
    h.capture()
    h.worker.run_once()
    assert sum(map(len, h.remote.writes)) == 1


def test_two_bank_sides_of_existing_transfer_are_not_created_as_two_expenses(h):
    transfer = seed(h, incomeAccount='fixture-other-vnd', income=100, comment='CT DI:580K26A0FIXTURE')
    # Outgoing account first; the already claimed transfer is still available
    # for the incoming leg on a different explicitly bound account.
    first, _, first_auth, _ = capture(h)
    for interval in first['accounts'][0]['intervals'][1:]:
        assert h.page(first, interval, first_auth).status_code == 200
        assert h.complete(first, interval, first_auth).status_code == 200
    h.worker.run_once()
    connection = h.discover(names=('account-2',))
    other_account = next(a['id'] for a in connection['accounts'] if a['source_key'] == 'account-2')
    assert h.configure(other_account, target='fixture-other-vnd').status_code == 200
    job, auth = h.leased(h.connection['id'])
    other = next(a for a in job['accounts'] if a['account_id'] == other_account)
    interval = other['intervals'][0]
    row = record(interval, 'vib:id:580K26A0OTHERBANK', direction='income', amount='100')
    assert h.page(job, interval, auth, records=[row]).status_code == 200
    assert h.complete(job, interval, auth, records=1).status_code == 200
    h.due()
    assert h.remote.writes == []
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM processing_receipts WHERE zen_id=?', (transfer,)).fetchone()[0] == 2


def test_different_reference_text_does_not_override_account_day_amount(h):
    seed(h, comment='Other reference 580K26A0FIXTURE9')
    capture(h)
    h.worker.run_once()
    assert h.remote.writes == [] and h.states() == ['delivered']


@pytest.mark.parametrize('kind,direction', [('expense','expense'), ('income','income'), ('refund','income'),
    ('transfer_in','income'), ('transfer_out','expense'), ('lend','expense'), ('repay','expense'),
    ('borrow','income'), ('collect','income')])
def test_all_operation_types_match_the_selected_bank_side(h, kind, direction):
    changes = {'comment': None, 'income': 100 if direction == 'income' else 0,
        'outcome': 100 if direction == 'expense' else 0}
    if kind in ('transfer_in','borrow','collect'):
        changes.update(outcomeAccount='fixture-other-vnd', outcome=200, outcomeInstrument=3)
    elif kind in ('transfer_out','lend','repay'):
        changes.update(incomeAccount='fixture-other-vnd', income=200, incomeInstrument=3)
    if kind in ('lend','repay','borrow','collect'):
        h.remote.data['account'][1]['type'] = 'debt'
        if direction == 'income':
            changes.update(outcome=100, outcomeInstrument=2)
        else:
            changes.update(income=100, incomeInstrument=2)
    seed(h, **changes)
    capture(h, direction=direction)
    h.worker.run_once()
    assert h.remote.writes == [] and h.states() == ['delivered']


def test_net_movement_on_same_account_is_used_instead_of_gross_side(h):
    seed(h, comment=None, income=50, outcome=150)
    capture(h)
    h.worker.run_once()
    assert h.remote.writes == [] and h.states() == ['delivered']
