import json
from datetime import date
from decimal import Decimal
from backend import service
from backend.db import initialize, transaction
from backend.zenstore import project


def legacy(rows, bank, account):
    result=[]
    for t in rows:
        if t.get('deleted') or t.get('hold') or not t.get('date'):
            continue
        if abs((date.fromisoformat(t['date'])-date.fromisoformat(bank['date'])).days)>2:
            continue
        net=Decimal(0)
        if t.get('incomeAccount')==account['zenmoney_account']:net+=Decimal(str(t['income']))
        if t.get('outcomeAccount')==account['zenmoney_account']:net-=Decimal(str(t['outcome']))
        if net*service.SCALES[account['currency']]==bank['amount_minor']:result.append(t['id'])
    return result


def test_index_matches_legacy_for_transfers_dates_precision_and_flags(tmp_path):
    path=tmp_path/'db';initialize(path,'demo')
    accounts=[{'zenmoney_account':'a','currency':'USD'},{'zenmoney_account':'b','currency':'VND'}]
    rows=[]
    for day in range(1,8):
        for inc,out in [('a','a'),('a','b'),('b','a'),('b','b')]:
            for income,outcome in [('1.01','0'),('0','1.01'),('1.01','1.00'),('0.001','0')]:
                for flags in [{},{'deleted':True},{'hold':True}]:
                    rows.append({'id':str(len(rows)),'date':f'2026-10-{day:02}','incomeAccount':inc,'outcomeAccount':out,'income':Decimal(income),'outcome':Decimal(outcome),**flags})
    with transaction(path) as db:
        for row in rows: project(db, 'transaction', row, None)
        stored=service.entities(db,'transaction');index=service.candidate_index(db,accounts)
        for account in accounts:
            for value in [-101,101,1,-1]:
                bank={'date':'2026-10-04','amount_minor':value}
                assert [t['id'] for t in service.candidates(db,bank,account,index)]==legacy(stored,bank,account)
        # New requests/confirmation must not retain stale matches after sync.
        db.execute("DELETE FROM zen_transaction")
        assert service.candidates(db,{'date':'2026-10-04','amount_minor':101},accounts[0])==[]


def test_review_reads_history_once_for_many_bank_rows(tmp_path,monkeypatch):
    path=tmp_path/'db';initialize(path,'demo');calls=[]
    original=service.entities
    def counted(db,kind):
        calls.append(kind);return original(db,kind)
    monkeypatch.setattr(service,'entities',counted)
    with transaction(path) as db:
        db.execute("INSERT INTO bank_accounts VALUES ('bank','demo','123','VND','zen')")
        for i in range(100):
            db.execute('INSERT INTO bank_transactions(id,bank_account,external_id,date,amount_minor,description,raw) VALUES (?,?,?,?,?,?,?)',(str(i),'bank',str(i),'2026-10-01',-100,'Example','{}'))
        assert len(service.review(db))==100
        assert calls==['transaction']
