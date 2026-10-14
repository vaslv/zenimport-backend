"""Disposable Docker browser fixture. Never connects to a real ZenMoney account."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import copy
import uvicorn
from backend.app import create_app
from backend.db import transaction
from backend.service import import_bank
from backend.models import BankImport
from backend.zenmoney import apply_diff
from backend.link_groups import insert
from zenmoney_api import transactions as tx
from test_operation_types import Remote

TOKEN='docker-test-token-not-for-real-data'
path=Path('/data/operations.sqlite3')
remote=Remote()
app=create_app(path,TOKEN,'live',remote)
# Production middleware also protects these test-only /api routes.

@app.post('/api/test/reset')
def reset(options:dict):
    remote.data=Remote().data;remote.writes=[];remote.fail_read=False;remote.behavior='ok'
    existing=options.get('existing',False)
    positive=options.get('positive',False)
    chosen=1 if positive else 0
    records=[{'external_id':'TEST0001','date':'2026-10-06','amount_minor':-65000,'description':'CT DI:TEST0001 Coffee',
              'raw':{'reference':'TEST0001','date':'06/10/2026 12:01:00','balance':'935000','amount':'-65000','description':'CT DI:TEST0001 Coffee'}},
             {'external_id':'TEST0002','date':'2026-10-05','amount_minor':100000,'description':'CT DEN:TEST0002 Transfer',
              'raw':{'reference':'TEST0002','date':'05/10/2026 10:30:00','balance':'1000000','amount':'100000','description':'CT DEN:TEST0002 Transfer'}}]
    if existing:
        r=records[chosen]
        remote.data['transaction']=[(tx.income if positive else tx.expense)(user=1,id='existing',date=r['date'],now=10,account='cash',instrument=1,amount=abs(r['amount_minor']),categories=[] if options.get('uncategorized') else ['salary' if positive else 'food'],comment=r['description'],payee=options.get('payee'))]
    with transaction(path) as db:
        for table in ('reconciliation_bank_members','reconciliation_zen_members','reconciliation_groups','zen_operation_sends','zen_operation_drafts','bank_operation_drafts','creation_jobs','creation_drafts','zen_category_sends','zen_category_drafts','reconciliation_exclusions','outbox','bank_transactions','imports','bank_accounts'):
            db.execute('DELETE FROM '+table)
        db.execute('DELETE FROM zen_transaction')
        apply_diff(db,{'serverTimestamp':1,**remote.data})
        data=BankImport.model_validate({'bank':'vietinbank','account':'111122223333','currency':'VND','adapter_version':'test',
             'capture':{'row_start':1,'row_end':2,'total_rows':2,'pages':1,'complete':True},'transactions':records})
        import_bank(db,data);db.execute("UPDATE bank_accounts SET zenmoney_account='cash'")
        if existing:
            identifier=db.execute('SELECT id FROM bank_transactions WHERE external_id=?',(records[chosen]['external_id'],)).fetchone()[0]
            insert(db,[identifier],['existing'],'single','test')
    return {'chosen':chosen}

@app.post('/api/test/behavior')
def behavior(options:dict):
    remote.behavior=options.get('behavior','ok');remote.fail_read=False
    return {'ok':True}

@app.get('/api/test/remote')
def result():
    return {'writes':remote.writes,'transactions':remote.data['transaction']}

if __name__=='__main__':uvicorn.run(app,host='0.0.0.0',port=8001)
