"""Late bank connections corroborate legacy transfer legs without remote writes."""
from copy import deepcopy
from uuid import uuid4

import pytest

from backend import ownership, storage
from capture_factory import TARGET, record
from delivery_factory import DeliveryHarness
from mirror_factory import movement

OTHER = 'fixture-other-vnd'
DAY = '2026-09-15'


@pytest.fixture
def h(tmp_path):
    value = DeliveryHarness(tmp_path / 'transfers.sqlite3')
    yield value
    value.close()


def setup_old(h, incoming=False, fx=False):
    if fx:
        h.remote.data['account'][1]['instrument'] = 3
    zen = str(uuid4())
    h.remote.transactions[zen] = movement(zen, date=DAY, outcomeAccount=OTHER if incoming else TARGET,
        incomeAccount=TARGET if incoming else OTHER, income=100 if incoming else (4 if fx else 100),
        outcome=(4 if fx else 100) if incoming else 100,
        incomeInstrument=2 if incoming or not fx else 3, outcomeInstrument=3 if incoming and fx else 2)
    h.worker.mirror.refresh(force=True)
    job, _, auth, _ = h.capture(changes={'external_id':'old', 'direction':'income' if incoming else 'expense'})
    for interval in job['accounts'][0]['intervals'][1:]:
        assert h.page(job, interval, auth).status_code == 200
        assert h.complete(job, interval, auth).status_code == 200
    old = h.bank_ids()['old']
    with storage.transaction(h.path) as db:
        db.execute('INSERT INTO processing_receipts VALUES (?,?,?,?)', (old, zen, 'mirror_exists', h.now))
        db.execute("UPDATE bank_processing SET state='delivered' WHERE bank_id=?", (old,))
    return old, zen


def add_peer(h, incoming=False, fx=False, count=1):
    connection = h.discover(bank='vietinbank' if fx else 'bidv', profile='second-login', names=('second-account',),
        accounts=[{'source_key':'second-account','identity_scope':'bank','label':'Second bank',
                   'masked_number':'••5678','currency':'USD' if fx else 'VND','scale':2 if fx else 0,'supported':True}],
        capabilities={'timezone':'Asia/Ho_Chi_Minh','currencies':['USD' if fx else 'VND'],
                      'max_window_days':30,'earliest_date':None,'details':'included'})
    second = connection['accounts'][0]['id']
    assert h.configure(second, target=OTHER).status_code == 200
    job, auth = h.leased(connection['id'])
    for index, interval in enumerate(job['accounts'][0]['intervals']):
        rows = [record(interval, 'peer-'+str(i), direction='expense' if incoming else 'income',
                       amount='4.00' if fx else '100', currency='USD' if fx else 'VND', details={}) for i in range(count)] if index == 0 else []
        response = h.page(job, interval, auth, records=rows)
        assert response.status_code == 200, response.text
        assert h.complete(job, interval, auth, records=len(rows)).status_code == 200
    return job


def repeat_peer(h, connection, incoming=False, fx=False):
    response = h.start(connection, reimport_from=DAY)
    assert response.status_code == 202
    job = response.json()
    worker = str(uuid4())
    lease = h.client.post('/api/v1/jobs/'+job['id']+'/lease', json={'worker_id':worker})
    assert lease.status_code == 200
    auth = {'worker_id':worker,'lease_epoch':lease.json()['epoch']}
    for index, interval in enumerate(job['accounts'][0]['intervals']):
        rows = [record(interval, 'peer-0', direction='expense' if incoming else 'income',
            amount='4.00' if fx else '100', currency='USD' if fx else 'VND',details={})] if index == 0 else []
        assert h.page(job, interval, auth, records=rows).status_code == 200
        assert h.complete(job, interval, auth, records=len(rows)).status_code == 200
    h.due()
    assert h.job(job)['delivery_state'] == 'complete'


@pytest.mark.parametrize('incoming', [False, True])
@pytest.mark.parametrize('fx', [False, True])
def test_late_other_bank_resolves_legacy_transfer_and_repeat_is_idempotent(h, incoming, fx):
    old, zen = setup_old(h, incoming, fx)
    original = deepcopy(h.remote.transactions)
    h.due()  # Opposite account has no bank connection yet: no invented ownership.
    with storage.transaction(h.path, read_only=True) as db:
        assert ownership.claims(db) == (set(), {zen})
    job = add_peer(h, incoming, fx)
    h.due(); h.due()
    assert h.job(job)['delivery_state'] == 'complete'
    repeat_peer(h,job['connection_id'],incoming,fx)
    assert h.remote.writes == [] and h.remote.transactions == original
    with storage.transaction(h.path, read_only=True) as db:
        assert ownership.claims(db) == ({(zen, TARGET), (zen, OTHER)}, set())
        assert db.execute('SELECT origin FROM processing_targets WHERE bank_id=?', (old,)).fetchone()[0] == 'corroborated_transfer'
        assert db.execute('SELECT count(*) FROM processing_targets').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM delivery_attempts').fetchone()[0] == 0
        storage.check_integrity(db)
    del h.remote.transactions[zen]
    h.remote.deletions.append({'object':'transaction','id':zen})
    h.worker.mirror.refresh(force=True)
    repeat_peer(h,job['connection_id'],incoming,fx)
    assert not h.remote.transactions and not h.remote.writes


@pytest.mark.parametrize('conflict', ['duplicate_bank', 'duplicate_remote', 'changed_mapping', 'changed_amount', 'changed_date'])
def test_ambiguous_or_changed_evidence_does_not_release_legacy_transfer(h, conflict):
    old, zen = setup_old(h)
    if conflict == 'duplicate_remote':
        other = str(uuid4())
        h.remote.transactions[other] = deepcopy(h.remote.transactions[zen]) | {'id':other}
    if conflict == 'changed_mapping':
        assert h.configure(h.account_id, version=1, target=OTHER).status_code == 200
    if conflict == 'changed_amount':
        h.remote.transactions[zen]['outcome'] = 125
    if conflict == 'changed_date':
        h.remote.transactions[zen]['date'] = '2026-09-16'
    job = add_peer(h, count=2 if conflict == 'duplicate_bank' else 1)
    # No financial worker here: inspect recovery in isolation when the date
    # changes, since a genuinely different day may legitimately create a record.
    from backend import existing, predictions
    h.worker.mirror.refresh(force=True)
    with storage.transaction(h.path) as db:
        peer = predictions.bank_record(db, h.bank_ids()['peer-0'])
        candidates = [(peer, {'id':OTHER,'instrument':2})]
        if conflict == 'duplicate_bank':
            candidates.append((predictions.bank_record(db,h.bank_ids()['peer-1']),{'id':OTHER,'instrument':2}))
        existing.recover_transfer_ownership(db,candidates,h.now)
        assert not db.execute('SELECT 1 FROM processing_targets WHERE bank_id=?', (old,)).fetchone()
    assert not h.remote.writes


def test_transfer_to_external_unsynchronized_account_needs_no_second_connection(h):
    zen = str(uuid4())
    h.remote.transactions[zen] = movement(zen,date=DAY,outcomeAccount=TARGET,incomeAccount=OTHER,income=100,outcome=100)
    job, _, auth, _ = h.capture()
    for interval in job['accounts'][0]['intervals'][1:]:
        assert h.page(job,interval,auth).status_code == 200
        assert h.complete(job,interval,auth).status_code == 200
    h.due(); h.due()
    assert h.job(job)['delivery_state'] == 'complete'
    assert not h.remote.writes
    with storage.transaction(h.path,read_only=True) as db:
        assert ownership.claims(db) == ({(zen,TARGET)},set())


def test_equal_transfer_already_claimed_on_peer_side_is_not_an_alternative(h):
    old, zen = setup_old(h)
    second_zen = str(uuid4())
    h.remote.transactions[second_zen] = deepcopy(h.remote.transactions[zen]) | {'id':second_zen}
    job = add_peer(h,count=2)
    with storage.transaction(h.path) as db:
        second_bank = h.bank_ids()['peer-1']
        db.execute('INSERT INTO processing_receipts VALUES (?,?,?,?)',(second_bank,second_zen,'day_amount',h.now))
        ownership.remember(db,second_bank,second_zen,OTHER,'day_amount',h.now)
        db.execute("UPDATE bank_processing SET state='delivered' WHERE bank_id=?",(second_bank,))
    h.due()
    assert h.job(job)['delivery_state'] == 'complete'
    assert not h.remote.writes
    with storage.transaction(h.path,read_only=True) as db:
        assert ownership.claims(db) == ({(zen,TARGET),(zen,OTHER),(second_zen,OTHER)},set())
