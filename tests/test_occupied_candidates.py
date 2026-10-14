import copy
from test_operation_types import setup,model,save,definition
from test_flow import IMPORT
from backend.db import transaction


def test_linked_payment_does_not_block_same_amount_new_payment(tmp_path):
    api,path,remote,bank_id=setup(tmp_path,existing=True)
    extra=copy.deepcopy(IMPORT);extra['transactions'][0].update(external_id='next-day',date='2026-10-02',description='Different recipient')
    assert api.post('/api/imports',json=extra).status_code==200
    rows=api.get('/api/state').json()['review'];new=next(r for r in rows if r['id']!=bank_id)
    assert new['candidates']==[] and new['operation_edit']['editable']
    assert next(r for r in rows if r['id']==bank_id)['linked']
    assert save(api,new['id'],definition('expense')).status_code==200
    assert not remote.writes
    account=api.get('/api/state').json()['bank_accounts'][0]['id']
    result=api.post('/api/batch/preview',json={'bank':'demo','account':account,'bank_ids':[new['id']],'creations_only':True})
    assert result.status_code==200 and not result.json()['items'][0]['error']


def test_unowned_candidate_allows_local_edit_but_blocks_creation(tmp_path):
    api,path,remote,bank_id=setup(tmp_path,existing=True)
    with transaction(path) as db:
        db.execute('DELETE FROM reconciliation_bank_members');db.execute('DELETE FROM reconciliation_zen_members');db.execute('DELETE FROM reconciliation_groups')
        db.execute("UPDATE bank_transactions SET state='pending',linked_id=NULL")
    row=api.get('/api/state').json()['review'][0]
    assert row['candidates'] and row['operation_edit']['editable']
    assert save(api,bank_id,definition('expense')).status_code==200
    account=api.get('/api/state').json()['bank_accounts'][0]['id']
    result=api.post('/api/batch/preview',json={'bank':'demo','account':account,'bank_ids':[bank_id],'creations_only':True})
    assert result.status_code==200 and result.json()['items']==[]
    assert not remote.writes
