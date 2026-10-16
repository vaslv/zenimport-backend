"""Reviewed composite cleanup uses only synthetic records and a fake SDK."""
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

import pytest
from zenmoney_api.protocol import encode_json

from backend import duplicate_cleanup as cleanup, storage
from capture_factory import TARGET, record
from delivery_factory import DeliveryHarness
from mirror_factory import movement


@pytest.fixture
def h(tmp_path):
    value = DeliveryHarness(tmp_path / 'composite-cleanup.sqlite3')
    yield value
    value.close()


def seal(value):
    value.pop('sha256', None)
    value['sha256'] = sha256(encode_json(value)).hexdigest()
    return value


def plan(h, banks, zens, kind):
    job, auth = h.leased(h.connection['id'])
    intervals = job['accounts'][0]['intervals']
    for index, interval in enumerate(intervals):
        rows = [record(interval, 'fixture-composite-' + str(i), amount=str(abs(v)),
                       direction='income' if v > 0 else 'expense') for i, v in enumerate(banks)] if index == 0 else []
        assert h.page(job, interval, auth, records=rows).status_code == 200
        assert h.complete(job, interval, auth, records=len(rows)).status_code == 200
    h.due()
    with storage.transaction(h.path, read_only=True) as db:
        removals = [{'bank_id': b, 'snapshot': deepcopy(h.remote.transactions[z])}
                    for b, z in db.execute('SELECT bank_id,zen_id FROM deliveries')]
    keeps = [movement(str(uuid4()), date=intervals[0]['period']['start'], incomeAccount=TARGET,
                      outcomeAccount=TARGET, income=max(v, 0), outcome=max(-v, 0)) for v in zens]
    h.remote.transactions.update({z['id']: deepcopy(z) for z in keeps})
    return seal({'groups': [{'kind': kind, 'bank_ids': [r['bank_id'] for r in removals],
                             'keeps': keeps, 'removals': removals}]})


def run(h, approved, journal):
    return cleanup.execute(h.path, h.worker.mirror, approved, approved['sha256'], journal, clock=lambda: h.now)


@pytest.mark.parametrize('banks,zens,kind', [([-99], [-49, -6, -44], 'split'),
    ([99], [49, 6, 44], 'split'), ([-580, -17], [-597], 'settlement'),
    ([-58, 35, 20], [-3], 'settlement'), ([58, -35, -20], [3], 'settlement'),
    ([-3], [-58, 35, 20], 'settlement'), ([100], [100], 'single')])
def test_group_cleanup_preserves_all_originals_and_never_recreates(h, tmp_path, banks, zens, kind):
    approved = plan(h, banks, zens, kind)
    group = approved['groups'][0]
    journal = tmp_path / 'composite.json'
    writes_before = len(h.remote.writes)
    result = run(h, approved, journal)
    assert result == {'removed': len(banks), 'preserved': len(zens), 'total': len(banks)}
    assert h.remote.transactions == {z['id']: z for z in group['keeps']}
    assert journal.with_suffix('.before.sqlite3').is_file()
    assert h.states() == ['delivered'] * len(banks)
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM deliveries WHERE state='suppressed'").fetchone()[0] == len(banks)
        assert db.execute('SELECT count(*) FROM link_bank_members').fetchone()[0] == len(banks)
        assert db.execute('SELECT count(*) FROM link_zen_members').fetchone()[0] == len(zens)
    assert run(h, approved, journal) == result
    assert len(h.remote.delete_writes) == 1
    # Delete one chosen original in mobile: every affected bank remains terminal.
    original = group['keeps'][0]['id']
    del h.remote.transactions[original]
    h.remote.deletions.append({'object': 'transaction', 'id': original})
    h.worker.mirror.refresh(force=True)
    assert h.states() == ['suppressed'] * len(banks)
    h.start(h.connection['id']); h.due()
    assert len(h.remote.writes) == writes_before and len(h.remote.delete_writes) == 1


@pytest.mark.parametrize('mode', ['timeout_before', 'timeout_after'])
def test_uncertain_group_delete_can_only_be_verified(h, tmp_path, mode):
    approved = plan(h, [-58, 35, 20], [-3], 'settlement')
    journal = tmp_path / 'composite.json'
    h.remote.delete_mode = mode
    if mode == 'timeout_before':
        with pytest.raises(RuntimeError, match='not confirmed'):
            run(h, approved, journal)
    else:
        assert run(h, approved, journal)['removed'] == 3
    run(h, approved, journal)
    assert len(h.remote.delete_writes) == 1


@pytest.mark.parametrize('change', ['sum', 'day', 'account', 'overlap', 'missing_removal'])
def test_invalid_composite_plan_never_deletes(h, tmp_path, change):
    approved = plan(h, [-100], [-40, -60], 'split')
    group = approved['groups'][0]
    if change == 'sum':
        group['keeps'][0]['outcome'] = 41
    elif change == 'day':
        group['keeps'][0]['date'] = '2026-09-16'
    elif change == 'account':
        group['keeps'][0]['outcomeAccount'] = 'fixture-other-vnd'
    elif change == 'overlap':
        group['keeps'][0] = deepcopy(group['removals'][0]['snapshot'])
    else:
        group['removals'] = []
    h.remote.transactions.update({z['id']: deepcopy(z) for z in group['keeps']})
    seal(approved)
    with pytest.raises(ValueError):
        run(h, approved, tmp_path / 'composite.json')
    assert not h.remote.delete_writes
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM link_groups').fetchone()[0] == 0


def test_foreign_currency_transfer_original_keeps_its_other_leg(h, tmp_path):
    approved = plan(h, [100], [100], 'single')
    original = approved['groups'][0]['keeps'][0]
    original.update(outcomeAccount='fixture-other-vnd', outcome=25, outcomeInstrument=3)
    h.remote.transactions[original['id']] = deepcopy(original)
    seal(approved)
    assert run(h, approved, tmp_path / 'composite.json')['preserved'] == 1
    assert h.remote.transactions[original['id']] == original
