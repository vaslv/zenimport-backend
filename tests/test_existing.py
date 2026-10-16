"""Bootstrap from an existing ZenMoney history without changing mobile data."""
from copy import deepcopy
from uuid import uuid4

import pytest

from backend import storage
from delivery_factory import DeliveryHarness
from capture_factory import TARGET
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
    {'comment': 'Other reference 580K26A0FIXTURE9'},
    {'comment': 'Same amount without bank reference'},
    {'date': '2026-09-16'}, {'outcome': 101}, {'hold': True},
    {'outcomeAccount': 'fixture-other-vnd'}, {'outcomeInstrument': 3},
    {'income': 1},
])
def test_similarity_alone_is_not_an_identity(h, change):
    seed(h, **change)
    capture(h)
    h.worker.run_once()
    assert len(h.remote.writes) == 1


def test_ambiguous_remote_references_are_not_chosen_arbitrarily(h):
    seed(h)
    seed(h)
    capture(h)
    h.worker.run_once()
    assert len(h.remote.writes) == 1


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
