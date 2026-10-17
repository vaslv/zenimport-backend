"""Composite bank/ZenMoney representations; fake accounts and SDK only."""
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import pytest

from backend import existing, storage
from capture_factory import TARGET, record
from delivery_factory import DeliveryHarness
from mirror_factory import movement


@pytest.fixture
def h(tmp_path):
    value = DeliveryHarness(tmp_path / 'groups.sqlite3')
    yield value
    value.close()


def prepare(h, banks, zens):
    identifiers=[]
    for i, value in enumerate(zens):
        identifier=str(uuid4());identifiers.append(identifier)
        h.remote.transactions[identifier]=movement(identifier, date='2026-09-15', incomeAccount=TARGET,
            outcomeAccount=TARGET, income=max(value,0), outcome=max(-value,0), comment=None, created=i+1)
    job,auth=h.leased(h.connection['id']);interval=job['accounts'][0]['intervals'][0]
    rows=[record(interval, 'fixture-group-'+str(i), amount=str(abs(value)), direction='income' if value>0 else 'expense')
          for i,value in enumerate(banks)]
    assert h.page(job,interval,auth,records=rows).status_code==200
    assert h.complete(job,interval,auth,records=len(rows)).status_code==200
    return identifiers


@pytest.mark.parametrize('banks,zens,kind', [([-99],[-49,-6,-44],'split'),
    ([99],[49,6,44],'split'), ([-580,-17],[-597],'settlement'),
    ([580,17],[597],'settlement'), ([-58,35,20],[-3],'settlement'),
    ([58,-35,-20],[3],'settlement'), ([-3],[-58,35,20],'settlement')])
def test_unique_split_merge_or_net_settlement_is_persisted_without_create(h,banks,zens,kind):
    identifiers=prepare(h,banks,zens);original=deepcopy(h.remote.transactions)
    h.worker.run_once()
    assert h.remote.writes==[] and h.remote.transactions==original
    assert h.states()==['delivered']*len(banks)
    with storage.transaction(h.path,read_only=True) as db:
        assert db.execute('SELECT kind,origin FROM link_groups').fetchone()[:]==(kind,'day_amount_group')
        assert db.execute('SELECT count(*) FROM link_bank_members').fetchone()[0]==len(banks)
        assert db.execute('SELECT count(*) FROM link_zen_members').fetchone()[0]==len(zens)
        assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM delivery_attempts').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM processing_targets WHERE target_id=?', (TARGET,)).fetchone()[0]==len(banks)*len(zens)
    # Subsequent mobile edits and deletion must not authorize recreation.
    h.remote.transactions[identifiers[0]]['comment']='Edited in mobile'
    h.worker.mirror.refresh(force=True)
    del h.remote.transactions[identifiers[0]]
    h.remote.deletions.append({'object':'transaction','id':identifiers[0]})
    h.worker.mirror.refresh(force=True)
    h.start(h.connection['id']);h.due()
    assert h.remote.writes==[]


def test_unique_split_can_train_only_one_independent_payment(h):
    prepare(h,[-100],[-40,-60]);h.worker.run_once()
    with storage.transaction(h.path,read_only=True) as db:
        assert db.execute('SELECT count(*) FROM category_payments').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM category_evidence').fetchone()[0]==2


def test_mixed_direction_settlement_never_teaches_three_independent_payments(h):
    prepare(h,[-58,35,20],[-3]);h.worker.run_once()
    with storage.transaction(h.path,read_only=True) as db:
        assert db.execute('SELECT count(*) FROM category_evidence').fetchone()[0]==0


@pytest.mark.parametrize('banks,zens', [([-100],[-40,-60,-50,-50]),
    ([-100,-150],[-40,-60,-110]), ([-100,100],[]), ([-100],[1]*17), ([-100],[-40,-59])])
def test_ambiguous_overlapping_zero_or_inexact_groups_are_not_invented(h,banks,zens):
    prepare(h,banks,zens);h.worker.run_once()
    with storage.transaction(h.path,read_only=True) as db:
        assert db.execute('SELECT count(*) FROM link_groups').fetchone()[0]==0
    assert sum(map(len,h.remote.writes))==len(banks)


def test_subset_precision_and_budget():
    amount=Decimal('90071992547409931234567.123456789012')
    assert existing.signed({'amount':str(amount),'direction':'expense'})==amount.copy_negate()
    assert existing.unique_subset([('a',Decimal('90071992547409931234567')),('b',Decimal('.123456789012'))],amount,[20000])==('a','b')
    assert existing.unique_subset([('a',Decimal(40)),('b',Decimal(60))],Decimal(100),[0]) is None
