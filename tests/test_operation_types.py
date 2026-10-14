import copy
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from zenmoney_api import transactions as tx
from backend.app import create_app
from backend.db import transaction,entities
from backend.zenmoney import apply_diff
from backend.link_groups import insert
from test_flow import TOKEN,IMPORT


class Remote:
    def __init__(self):
        self.data={'user':[{'id':1,'currency':2}], 'instrument':[{'id':1,'shortTitle':'VND'},{'id':2,'shortTitle':'USD'}],
            'account':[{'id':'cash','user':1,'type':'cash','instrument':1,'title':'Bank'},
                       {'id':'second','user':1,'type':'cash','instrument':1,'title':'Other VND'},
                       {'id':'foreign','user':1,'type':'cash','instrument':2,'title':'Other USD'},
                       {'id':'debt','user':1,'type':'debt','instrument':2,'title':'Debts'}],
            'tag':[{'id':'food','user':1,'title':'Продукты','showIncome':False,'showOutcome':True},
                   {'id':'salary','user':1,'title':'Зарплата','showIncome':True,'showOutcome':False}],
            'merchant':[{'id':'person','user':1,'title':'Test person'}], 'transaction':[]}
        self.writes=[];self.behavior='ok';self.fail_read=False
    def read_diff(self,cursor=0,*,force_fetch=None):
        if self.fail_read:raise TimeoutError()
        return {'serverTimestamp':max(cursor,100),**copy.deepcopy(self.data)}
    def upsert(self,kind,payloads,cursor=0):
        assert kind=='transaction';self.writes.append(copy.deepcopy(payloads))
        if self.behavior=='before':raise TimeoutError()
        existing={t['id']:t for t in self.data['transaction']}
        for i,p in enumerate(payloads):
            if self.behavior=='partial' and i:continue
            p=copy.deepcopy(p)
            if self.behavior=='conflict':p['comment']='External edit'
            for k in ('opIncome','opOutcome'):
                if p.get(k) is None and p.get(k+'Instrument') is None:p[k]=0
            existing[p['id']]=p
        self.data['transaction']=list(existing.values())
        if self.behavior=='lost_read':self.fail_read=True
        if self.behavior=='lost_response':raise TimeoutError()
        return {'serverTimestamp':100}


def setup(tmp_path,amount=-65000,existing=False):
    remote=Remote();path=tmp_path/'ops.sqlite3';api=TestClient(create_app(path,TOKEN,'live',remote));api.headers['Authorization']='Bearer '+TOKEN
    imported=copy.deepcopy(IMPORT);imported['transactions'][0]['amount_minor']=amount
    assert api.post('/api/imports',json=imported).status_code==200
    if existing:
        factory=tx.income if amount>0 else tx.expense
        item=factory(user=1,id='existing',date='2026-10-01',now=10,account='cash',instrument=1,amount=abs(amount),categories=['salary' if amount>0 else 'food'],payee='Shop',comment='Coffee')
        item['futureField']={'keep':Decimal('1.000000000000000001')};remote.data['transaction']=[item]
    with transaction(path) as db:
        apply_diff(db,{'serverTimestamp':1,**remote.data});db.execute("UPDATE bank_accounts SET zenmoney_account='cash'")
        bank_id=db.execute('SELECT id FROM bank_transactions').fetchone()[0]
        if existing:insert(db,[bank_id],['existing'],'single','manual')
    return api,path,remote,bank_id


def model(api,bank_id):return next(r for r in api.get('/api/state').json()['review'] if r['id']==bank_id)['operation_edit']


def definition(kind,other='second'):
    return dict(kind=kind,account_id='cash',category_id='food' if kind in ('expense','refund') else 'salary' if kind=='income' else None,
                other_account_id=other if kind=='transfer' else None,other_amount='1.250000000001' if kind=='transfer' and other=='foreign' else None,
                payee='Test person' if kind in ('lend','borrow','repay','collect') else None)


def save(api,bank_id,d):
    m=model(api,bank_id);path='/api/new-operations/'+bank_id+'/operation' if m['source']=='bank' else '/api/zenmoney/transactions/'+m['id']+'/operation'
    return api.put(path,json={'definition':d,'expected_snapshot':m['snapshot']})


CASES=[('expense',-65000),('income',65000),('refund',65000),('transfer',-65000),('transfer',65000),('lend',-65000),('borrow',65000),('repay',-65000),('collect',65000)]


@pytest.mark.parametrize('kind,amount',CASES)
@pytest.mark.parametrize('existing',[False,True])
def test_local_type_then_common_confirmation_retains_balance_and_uuid(tmp_path,kind,amount,existing):
    api,path,remote,bank_id=setup(tmp_path,amount,existing);before=copy.deepcopy(remote.data['transaction'])
    assert save(api,bank_id,definition(kind)).status_code==200
    assert not remote.writes and remote.data['transaction']==before
    assert model(api,bank_id)['draft']
    # Survive a backend restart without sending anything.
    api=TestClient(create_app(path,TOKEN,'live',remote));api.headers['Authorization']='Bearer '+TOKEN
    assert model(api,bank_id)['definition']['kind']==kind and not remote.writes
    account=api.get('/api/state').json()['bank_accounts'][0]['id'];scope={'bank':'demo','account':account}
    response=api.post('/api/batch/preview',json=scope);assert response.status_code==200,response.text
    items=response.json()['items'];assert len(items)==1 and not items[0]['error'],items
    assert not remote.writes
    body={**scope,'items':[{k:i[k] for k in ('kind','id','snapshot')} for i in items]}
    sent=api.post('/api/batch/send',json=body);assert sent.status_code==200,sent.text
    assert sent.json()['results'][0]['state']=='sent',sent.text
    assert len(remote.writes)==1
    p=remote.data['transaction'][0]
    delta=(p['income'] if p['incomeAccount']=='cash' else 0)-(p['outcome'] if p['outcomeAccount']=='cash' else 0)
    assert delta==amount
    if existing:
        assert p['id']=='existing' and p['created']==10 and p['futureField']==before[0]['futureField']
    assert p['date']=='2026-10-01'
    if kind=='transfer':assert {p['incomeAccount'],p['outcomeAccount']}=={'cash','second'} and not p['tag'] and p['payee'] is None
    if kind in ('lend','borrow','repay','collect'):assert 'debt' in (p['incomeAccount'],p['outcomeAccount']) and p['payee']=='Test person' and p['incomeInstrument']==p['outcomeInstrument']==1
    if kind=='refund':assert p['tag']==['food'] and p['income']>0 and p['outcome']==0
    assert api.post('/api/batch/send',json=body).status_code==409 and len(remote.writes)==1


@pytest.mark.parametrize('existing',[False,True])
def test_exact_foreign_transfer_and_saved_input(tmp_path,existing):
    api,path,remote,bank_id=setup(tmp_path,existing=existing)
    assert save(api,bank_id,definition('transfer','foreign')).status_code==200
    assert model(api,bank_id)['definition']['other_amount']=='1.250000000001'
    account=api.get('/api/state').json()['bank_accounts'][0]['id'];scope={'bank':'demo','account':account}
    items=api.post('/api/batch/preview',json=scope).json()['items']
    body={**scope,'items':[{k:i[k] for k in ('kind','id','snapshot')} for i in items]}
    result=api.post('/api/batch/send',json=body)
    assert result.json()['results'][0]['state']=='sent'
    p=remote.writes[0][0];assert p['income']==Decimal('1.250000000001') and p['outcome']==65000
    assert p['incomeInstrument']==2 and p['outcomeInstrument']==1


@pytest.mark.parametrize('changes',[{'kind':'income','category_id':'salary'}, {'kind':'transfer','other_account_id':'cash'},
    {'kind':'transfer','other_account_id':'foreign','other_amount':None}, {'kind':'refund','category_id':'salary'},
    {'kind':'lend','payee':'  '}, {'kind':'transfer','other_account_id':'second','other_amount':'123'}, {'account_id':'second'}])
def test_invalid_configuration_does_not_change_local_or_remote_data(tmp_path,changes):
    amount=65000 if changes.get('kind')=='refund' else -65000
    api,path,remote,bank_id=setup(tmp_path,amount)
    d=definition(changes.get('kind','expense'));d.update(changes)
    assert save(api,bank_id,d).status_code==409
    assert not remote.writes and not model(api,bank_id)['draft']


@pytest.mark.parametrize('behavior,expected',[('before','not_applied'),('lost_response','sent'),('lost_read','uncertain'),('conflict','conflict')])
def test_existing_edit_unknown_result_is_never_retried_and_blocks_editing(tmp_path,behavior,expected):
    api,path,remote,bank_id=setup(tmp_path,existing=True)
    assert save(api,bank_id,definition('lend')).status_code==200
    items=api.post('/api/operation-updates/preview',json={}).json()['items'];body={'items':[{'zen_id':i['zen_id'],'snapshot':i['snapshot']} for i in items]}
    remote.behavior=behavior
    result=api.post('/api/operation-updates/send',json=body)
    assert result.json()['results'][0]['state']==expected and len(remote.writes)==1
    if expected=='uncertain':assert save(api,bank_id,definition('repay')).status_code==409
    remote.fail_read=False
    api.post('/api/operation-updates/verify',json={})
    assert len(remote.writes)==1


def test_stale_draft_or_remote_change_blocks_entire_send(tmp_path):
    api,path,remote,bank_id=setup(tmp_path,existing=True)
    assert save(api,bank_id,definition('lend')).status_code==200
    items=api.post('/api/operation-updates/preview',json={}).json()['items'];body={'items':[{'zen_id':i['zen_id'],'snapshot':i['snapshot']} for i in items]}
    remote.data['transaction'][0]['comment']='New external edit'
    assert api.post('/api/operation-updates/send',json=body).status_code==409 and not remote.writes
    assert save(api,bank_id,definition('repay')).status_code==200
    old=model(api,bank_id)
    assert save(api,bank_id,definition('lend')).status_code==200
    assert api.put('/api/zenmoney/transactions/existing/operation',json={'definition':definition('repay'),'expected_snapshot':old['snapshot']}).status_code==409


def test_prepared_creation_can_change_type_without_changing_uuid(tmp_path):
    api,path,remote,bank_id=setup(tmp_path)
    assert api.put('/api/new-operations/'+bank_id+'/category',json={'category_id':'food'}).status_code==200
    api.post('/api/new-operations/create',json={'bank':'demo'})
    with transaction(path) as db:old=dict(db.execute('SELECT * FROM creation_jobs').fetchone())
    assert save(api,bank_id,definition('lend')).status_code==200
    with transaction(path) as db:new=dict(db.execute('SELECT * FROM creation_jobs').fetchone())
    assert old['zen_id']==new['zen_id'] and old['state']==new['state']=='prepared' and old['payload']!=new['payload']
    assert not remote.writes


def test_both_imported_transfer_legs_share_uuid_and_unlink_independently(tmp_path):
    from backend.link_groups import unlink
    api,path,remote,first=setup(tmp_path,existing=True)
    remote.data['transaction']=[tx.transfer(user=1,id='existing',date='2026-10-01',now=10,
        from_account='cash',to_account='second',from_instrument=1,to_instrument=1,amount_out=65000,amount_in=65000)]
    data=copy.deepcopy(IMPORT);data['account']='second-bank';data['transactions'][0]['amount_minor']=65000
    assert api.post('/api/imports',json=data).status_code==200
    with transaction(path) as db:
        apply_diff(db,{'serverTimestamp':2,**remote.data})
        db.execute("UPDATE bank_accounts SET zenmoney_account='second' WHERE account='second-bank'")
        second=db.execute('SELECT b.id FROM bank_transactions b JOIN bank_accounts a ON a.id=b.bank_account WHERE a.account=?',('second-bank',)).fetchone()[0]
        group=insert(db,[second],['existing'],'single','manual')
        assert db.execute('SELECT COUNT(*) FROM reconciliation_groups').fetchone()[0]==1
    state=api.get('/api/state').json();assert len(state['day_review'])==2
    assert all(d['resolved'] and d['zen_rows'][0]['linked_bank_id'] in (first,second) for d in state['day_review'])
    assert save(api,first,definition('expense')).status_code==409 and not remote.writes
    with transaction(path) as db:
        unlink(db,second)
        assert db.execute('SELECT bank_id FROM reconciliation_bank_members').fetchone()[0]==first
        assert db.execute('SELECT group_id FROM reconciliation_zen_members').fetchone()[0]==group
    state=api.get('/api/state').json();r=next(r for r in state['review'] if r['id']==second)
    assert r['candidates'][0]['id']=='existing'
    assert api.post('/api/review/'+second+'/link',json={'action':'link','match_id':'existing','reviewed_candidates':['existing']}).status_code==200
    assert not remote.writes


@pytest.mark.parametrize('change',[{'archive':True},{'deleted':True},{'user':2}])
def test_transfer_rejects_unavailable_or_other_family_account(tmp_path,change):
    api,path,remote,bank_id=setup(tmp_path)
    remote.data['account'][1].update(change)
    with transaction(path) as db:apply_diff(db,{'serverTimestamp':2,**remote.data})
    assert save(api,bank_id,definition('transfer')).status_code==409 and not remote.writes


def test_transfer_supports_account_of_family_member(tmp_path):
    api,path,remote,bank_id=setup(tmp_path)
    remote.data['user'].append({'id':2,'parent':1,'currency':2});remote.data['account'][1]['user']=2
    with transaction(path) as db:apply_diff(db,{'serverTimestamp':2,**remote.data})
    assert save(api,bank_id,definition('transfer')).status_code==200


@pytest.mark.parametrize('behavior',['ok','partial'])
def test_one_common_write_contains_new_and_existing_changes_and_verifies_individually(tmp_path,behavior):
    api,path,remote,first=setup(tmp_path,existing=True)
    data=copy.deepcopy(IMPORT);data['transactions'][0].update(external_id='second',date='2026-10-08',amount_minor=99000)
    assert api.post('/api/imports',json=data).status_code==200
    second=next(r['id'] for r in api.get('/api/state').json()['review'] if r['id']!=first)
    assert save(api,first,definition('lend')).status_code==200
    assert save(api,second,definition('refund')).status_code==200
    account=api.get('/api/state').json()['bank_accounts'][0]['id'];scope={'bank':'demo','account':account}
    items=api.post('/api/batch/preview',json=scope).json()['items'];assert {i['kind'] for i in items}=={'creation','operation'}
    remote.behavior=behavior
    result=api.post('/api/batch/send',json={**scope,'items':[{k:i[k] for k in ('kind','id','snapshot')} for i in items]})
    assert result.status_code==200 and len(remote.writes)==1 and len(remote.writes[0])==2
    statuses={r['kind']:r['state'] for r in result.json()['results']}
    assert statuses==({'creation':'sent','operation':'sent'} if behavior=='ok' else {'creation':'sent','operation':'not_applied'})
    if behavior=='ok':assert model(api,first)['definition']['kind']=='lend'


def test_category_draft_replaced_by_type_draft_without_two_writes(tmp_path):
    api,path,remote,bank_id=setup(tmp_path,existing=True)
    assert api.put('/api/zenmoney/transactions/existing/category',json={'category_id':'food','expected_tags':['food']}).status_code==200
    assert save(api,bank_id,definition('repay')).status_code==200
    with transaction(path) as db:assert db.execute('SELECT COUNT(*) FROM zen_category_drafts').fetchone()[0]==0
    items=api.post('/api/operation-updates/preview',json={}).json()['items'];assert len(items)==1 and not items[0]['error']


def test_refund_preserves_expense_category_without_negative_wire_amounts(tmp_path):
    api,path,remote,bank_id=setup(tmp_path,65000,existing=True)
    assert save(api,bank_id,definition('refund')).status_code==200
    items=api.post('/api/operation-updates/preview',json={}).json()['items']
    remote.data['tag'][0]['showIncome']=True
    assert api.post('/api/operation-updates/send',json={'items':[{'zen_id':i['zen_id'],'snapshot':i['snapshot']} for i in items]}).status_code==409
    assert not remote.writes


def test_common_batch_includes_category_and_type_changes_and_new_record(tmp_path):
    api,path,remote,first=setup(tmp_path,existing=True)
    third=tx.expense(user=1,id='third',date='2026-10-09',now=10,account='cash',instrument=1,amount=45000,comment='Other expense')
    remote.data['transaction'].append(third)
    with transaction(path) as db:apply_diff(db,{'serverTimestamp':2,**remote.data})
    assert api.put('/api/zenmoney/transactions/third/category',json={'category_id':'food','expected_tags':[]}).status_code==200
    data=copy.deepcopy(IMPORT);data['transactions'][0].update(external_id='second',date='2026-10-08',amount_minor=99000)
    assert api.post('/api/imports',json=data).status_code==200
    second=next(r['id'] for r in api.get('/api/state').json()['review'] if r['id']!=first)
    assert save(api,first,definition('repay')).status_code==200
    assert save(api,second,definition('income')).status_code==200
    scope={'bank':'demo','account':api.get('/api/state').json()['bank_accounts'][0]['id']}
    items=api.post('/api/batch/preview',json=scope).json()['items']
    assert {i['kind'] for i in items}=={'creation','operation','category'}
    result=api.post('/api/batch/send',json={**scope,'items':[{k:i[k] for k in ('kind','id','snapshot')} for i in items]})
    assert result.status_code==200 and all(i['state']=='sent' for i in result.json()['results'])
    assert len(remote.writes)==1 and len(remote.writes[0])==3


def test_new_debt_person_is_kept_without_inventing_merchant_uuid(tmp_path):
    api,path,remote,bank_id=setup(tmp_path);d=definition('lend');d['payee']='New person'
    assert save(api,bank_id,d).status_code==200
    scope={'bank':'demo','account':api.get('/api/state').json()['bank_accounts'][0]['id']}
    items=api.post('/api/batch/preview',json=scope).json()['items']
    result=api.post('/api/batch/send',json={**scope,'items':[{k:i[k] for k in ('kind','id','snapshot')} for i in items]})
    assert result.json()['results'][0]['state']=='sent'
    assert remote.writes[0][0]['payee']=='New person' and remote.writes[0][0]['merchant'] is None


def test_new_debt_remembers_repayment_semantics_after_sent(tmp_path):
    api,path,remote,bank_id=setup(tmp_path)
    assert save(api,bank_id,definition('repay')).status_code==200
    scope={'bank':'demo','account':api.get('/api/state').json()['bank_accounts'][0]['id']}
    items=api.post('/api/batch/preview',json=scope).json()['items']
    api.post('/api/batch/send',json={**scope,'items':[{k:i[k] for k in ('kind','id','snapshot')} for i in items]})
    assert model(api,bank_id)['definition']['kind']=='repay'


def test_other_family_category_is_rejected(tmp_path):
    api,path,remote,bank_id=setup(tmp_path);remote.data['tag'][0]['user']=7
    with transaction(path) as db:apply_diff(db,{'serverTimestamp':2,**remote.data})
    assert save(api,bank_id,definition('expense')).status_code==409 and not remote.writes


def test_existing_type_verification_accepts_exact_original_payee_copy(tmp_path):
    api,path,remote,bank_id=setup(tmp_path,existing=True)
    assert save(api,bank_id,definition('expense')).status_code==200
    items=api.post('/api/operation-updates/preview',json={}).json()['items']
    remote.behavior='lost_read'
    body={'items':[{'zen_id':i['zen_id'],'snapshot':i['snapshot']} for i in items]}
    api.post('/api/operation-updates/send',json=body)
    actual=remote.data['transaction'][0];assert actual['originalPayee'] is None
    actual['originalPayee']=actual['payee'];remote.fail_read=False
    with transaction(path) as db:db.execute("UPDATE zen_operation_sends SET state='conflict'")
    result=api.post('/api/operation-updates/verify',json={})
    assert result.json()['results'][0]['state']=='sent' and not model(api,bank_id)['draft']
    assert len(remote.writes)==1


def test_typed_result_describes_bank_side_of_foreign_transfer(tmp_path):
    api,path,remote,bank_id=setup(tmp_path,existing=True)
    assert save(api,bank_id,definition('transfer','foreign')).status_code==200
    scope={'bank':'demo','account':api.get('/api/state').json()['bank_accounts'][0]['id']}
    items=api.post('/api/batch/preview',json=scope).json()['items']
    response=api.post('/api/batch/send',json={**scope,'items':[{k:i[k] for k in ('kind','id','snapshot')} for i in items]})
    result=response.json()['results'][0]
    assert result['kind']=='operation' and result['state']=='sent' and result['description']=='Coffee'
    assert result['date']=='2026-10-01' and result['amount']=='-65000' and result['currency']=='VND'
    assert api.post('/api/batch/verify',json={**scope,'creations_only':True}).json()['results']==[]
    assert len(remote.writes)==1
