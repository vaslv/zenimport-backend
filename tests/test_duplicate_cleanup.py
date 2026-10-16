"""Manual financial maintenance against a fake SDK transport and artificial DB."""
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

import pytest
from zenmoney_api.protocol import encode_json

from backend import duplicate_cleanup as cleanup, storage
from delivery_factory import DeliveryHarness


@pytest.fixture
def h(tmp_path):
    value = DeliveryHarness(tmp_path / 'cleanup.sqlite3')
    yield value
    value.close()


def plan(h, count=1):
    job, _, auth, _ = h.capture(count)
    for interval in job['accounts'][0]['intervals'][1:]:
        assert h.page(job, interval, auth).status_code == 200
        assert h.complete(job, interval, auth).status_code == 200
    for _ in range((count+19)//20):
        h.due()
    rows = []
    with storage.transaction(h.path, read_only=True) as db:
        for bank, zen in db.execute('SELECT bank_id,zen_id FROM deliveries'):
            copy = deepcopy(h.remote.transactions[zen])
            original = deepcopy(copy) | {'id': str(uuid4()), 'viewed': True}
            h.remote.transactions[original['id']] = deepcopy(original)
            rows.append({'bank_id': bank, 'remove': zen, 'keep': original['id'],
                'copy_snapshot': copy, 'keep_snapshot': original})
    result = {'ready': rows, 'needs_review': []}
    result['sha256'] = sha256(encode_json(result)).hexdigest()
    return result


def run(h, approved, journal):
    return cleanup.execute(h.path, h.worker.mirror, approved, approved['sha256'], journal, clock=lambda: h.now)


def test_cleanup_relinks_originals_preserves_history_and_survives_later_deletion(h, tmp_path):
    approved = plan(h, 21)
    journal = tmp_path / 'cleanup.json'
    originals = {r['keep']: deepcopy(r['keep_snapshot']) for r in approved['ready']}
    result = run(h, approved, journal)
    assert result == {'removed': 21, 'preserved': 21, 'total': 21}
    assert h.remote.transactions == originals
    assert [len(v) for v in h.remote.delete_writes] == [20, 1]
    assert journal.with_suffix('.before.sqlite3').exists()
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM deliveries WHERE state='suppressed'").fetchone()[0] == 21
        assert db.execute("SELECT count(*) FROM bank_processing WHERE state='delivered'").fetchone()[0] == 21
        assert db.execute("SELECT count(*) FROM link_groups WHERE origin='duplicate_cleanup'").fetchone()[0] == 21
        assert db.execute('SELECT count(*) FROM delivery_attempts').fetchone()[0] == 42
    assert run(h, approved, journal) == result
    assert len(h.remote.delete_writes) == 2
    # A future mobile deletion of the chosen original is still final.
    row = approved['ready'][0]
    del h.remote.transactions[row['keep']]
    h.remote.deletions.append({'object': 'transaction', 'id': row['keep']})
    h.worker.mirror.refresh(force=True)
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT state FROM bank_processing WHERE bank_id=?', (row['bank_id'],)).fetchone()[0] == 'suppressed'
    h.start(h.connection['id'])
    h.due()
    assert len(h.remote.writes) == 2 and len(h.remote.delete_writes) == 2


@pytest.mark.parametrize('mode', ['timeout_before', 'timeout_after'])
def test_lost_delete_response_never_allows_a_second_write(h, tmp_path, mode):
    approved = plan(h)
    journal = tmp_path / 'cleanup.json'
    h.remote.delete_mode = mode
    if mode == 'timeout_before':
        with pytest.raises(RuntimeError, match='not confirmed'):
            run(h, approved, journal)
    else:
        assert run(h, approved, journal)['removed'] == 1
    run(h, approved, journal)
    assert len(h.remote.delete_writes) == 1


@pytest.mark.parametrize('side', ['keep', 'remove'])
def test_changed_mobile_data_aborts_before_first_delete(h, tmp_path, side):
    approved = plan(h)
    h.remote.transactions[approved['ready'][0][side]]['tag'] = ['category-b']
    with pytest.raises(ValueError, match='changed'):
        run(h, approved, tmp_path / 'cleanup.json')
    assert h.remote.delete_writes == []
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM link_groups').fetchone()[0] == 0


def test_plan_cannot_remove_an_original_or_change_approved_ids(h, tmp_path):
    approved = plan(h)
    approved['ready'][0]['remove'] = approved['ready'][0]['keep']
    with pytest.raises(ValueError, match='approved'):
        run(h, approved, tmp_path / 'cleanup.json')
    assert h.remote.delete_writes == []
