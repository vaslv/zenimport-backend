import copy
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import transaction, entities, initialize
from backend.zenmoney import apply_diff, DEMO_ACCOUNT
from backend.link_groups import insert
from test_flow import TOKEN, IMPORT
from test_category_send import Remote


class CreationRemote(Remote):
    def create_transactions(self, transactions, cursor):
        self.writes.append(copy.deepcopy(transactions))
        if self.behavior=='fail_before': raise TimeoutError()
        self.data['transaction'].extend(copy.deepcopy(transactions[:1] if self.behavior=='partial' else transactions))
        if self.behavior=='conflict': self.data['transaction'][-1]['outcome']+=1
        if self.behavior=='lost_read': self.fail_read=True
        if self.behavior=='lost_response': raise TimeoutError()
        return {'serverTimestamp':10}


def setup(tmp_path, currency='VND'):
    path=tmp_path/'creation.sqlite3'
    remote=CreationRemote({'transaction':[], 'account':[{'id':DEMO_ACCOUNT,'user':1,'instrument':1,'title':'Bank','archive':False}],
        'instrument':[{'id':1,'shortTitle':currency}], 'tag':[{'id':'food','title':'Еда'},{'id':'toys','title':'Игрушки'}]})
    c=TestClient(create_app(path,TOKEN,'live',zen_client=remote));c.headers['Authorization']='Bearer '+TOKEN
    data=copy.deepcopy(IMPORT);data['currency']=currency
    data['transactions'][0]['raw']['details']={'corresponsiveAccount':'123','corresponsiveName':'Shop'}
    assert c.post('/api/imports',json=data).status_code==200
    with transaction(path) as db:
        apply_diff(db,{'serverTimestamp':1,**remote.data})
        db.execute('UPDATE bank_accounts SET zenmoney_account=?',(DEMO_ACCOUNT,))
        identifier=db.execute('SELECT id FROM bank_transactions').fetchone()[0]
    return c,path,remote,identifier


def draft(c,id,category='food'):
    assert c.put(f'/api/new-operations/{id}/category',json={'category_id':category}).status_code==200


def request(c):
    assert c.post('/api/new-operations/create',json={'bank':'demo'}).status_code==200
    p=c.post('/api/new-operations/preview',json={'bank':'demo'})
    assert p.status_code==200,p.text
    return {'bank':'demo','items':[{'bank_id':i['bank_id'],'snapshot':i['snapshot']} for i in p.json()['items'] if not i['error']]}


def test_create_only_after_preview_verify_restart_and_duplicate_block(tmp_path):
    c,path,remote,id=setup(tmp_path);draft(c,id)
    body=request(c);assert len(body['items'])==1 and not remote.writes
    result=c.post('/api/new-operations/send',json=body)
    assert result.status_code==200,result.text
    assert result.json()['results'][0]['state']=='sent'
    tx=remote.writes[0][0]
    assert uuid.UUID(tx['id']).version==4 and tx['outcome']==65000 and tx['income']==0
    assert tx['tag']==['food'] and tx['payee']=='Shop' and tx['date']=='2026-10-01'
    with transaction(path,read_only=True) as db:
        assert db.execute('SELECT state FROM bank_transactions WHERE id=?',(id,)).fetchone()[0]=='sent'
        assert db.execute('SELECT count(*) FROM creation_drafts').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM reconciliation_groups').fetchone()[0]==1
    assert c.post('/api/new-operations/send',json=body).status_code==409
    restarted=TestClient(create_app(path,TOKEN,'live',zen_client=remote));restarted.headers.update(c.headers)
    assert restarted.post('/api/new-operations/verify',json={}).status_code==200
    assert len(remote.writes)==1


@pytest.mark.parametrize('behavior,expected',[('lost_response','sent'),('lost_read','uncertain'),('fail_before','uncertain'),('conflict','conflict')])
def test_unknown_outcome_never_retried_and_survives_restart(tmp_path,behavior,expected):
    c,path,remote,id=setup(tmp_path);draft(c,id);body=request(c);remote.behavior=behavior
    result=c.post('/api/new-operations/send',json=body)
    assert result.json()['results'][0]['state']==expected
    remote.fail_read=False
    c2=TestClient(create_app(path,TOKEN,'live',zen_client=remote));c2.headers.update(c.headers)
    assert c2.post('/api/new-operations/verify',json={}).status_code==200
    assert c2.post('/api/new-operations/send',json=body).status_code==409
    assert c2.put(f'/api/new-operations/{id}/category',json={'category_id':'toys'}).status_code==409
    assert c2.put(f'/api/exclusions/bank/{id}',json={}).status_code==409
    assert len(remote.writes)==1


@pytest.mark.parametrize('mutation',['category','mapping','amount','candidate','deleted_tag','missing_account','ignored','duplicate_id','other_bank'])
def test_preflight_blocks_stale_or_unsafe_batch(tmp_path,mutation):
    c,path,remote,id=setup(tmp_path);draft(c,id);body=request(c)
    if mutation=='category':
        with transaction(path) as db:db.execute("UPDATE creation_drafts SET category_id='toys' WHERE bank_id=?",(id,))
    elif mutation=='mapping':remote.data['account'][0]['archive']=True
    elif mutation=='deleted_tag':remote.data['tag'][0]['deleted']=True
    elif mutation=='missing_account':remote.data['account']=[]
    elif mutation=='candidate':remote.data['transaction']=[{'id':'existing','date':'2026-10-01','income':0,'outcome':65000,'incomeAccount':DEMO_ACCOUNT,'outcomeAccount':DEMO_ACCOUNT,'tag':['food']}]
    elif mutation=='ignored':
        assert c.post(f'/api/new-operations/{id}/reopen',json={}).status_code==200
        assert c.put(f'/api/exclusions/bank/{id}',json={}).status_code==200
    elif mutation=='duplicate_id':body['items']*=2
    elif mutation=='other_bank':body['bank']='other'
    else:
        with transaction(path) as db:db.execute('UPDATE bank_transactions SET amount_minor=-200 WHERE id=?',(id,))
    assert c.post('/api/new-operations/send',json=body).status_code in (409,422)
    assert not remote.writes
    with transaction(path,read_only=True) as db:assert db.execute("SELECT count(*) FROM creation_jobs WHERE state!='prepared'").fetchone()[0]==0


def historical(db,id,cat='food',recipient='123',direction=-1):
    original=dict(db.execute('SELECT * FROM bank_transactions WHERE id=?',(id,)).fetchone())
    raw=json.loads(original['raw']);raw['details']['corresponsiveAccount']=recipient
    bank_id=str(uuid.uuid4());zen_id=str(uuid.uuid4())
    db.execute('INSERT INTO bank_transactions(id,bank_account,external_id,date,amount_minor,description,raw) VALUES (?,?,?,?,?,?,?)',
        (bank_id,original['bank_account'],bank_id,'2026-09-01',direction*100,'Shop',json.dumps(raw)))
    tx={'id':zen_id,'date':'2026-09-01','incomeAccount':DEMO_ACCOUNT,'outcomeAccount':DEMO_ACCOUNT,'income':100 if direction>0 else 0,'outcome':100 if direction<0 else 0,'tag':[cat]}
    apply_diff(db,{'serverTimestamp':1,'transaction':[tx]})
    insert(db,[bank_id],[zen_id],'single','test')


def test_automatic_only_repeated_unanimous_recipient_and_manual_override(tmp_path):
    c,path,remote,id=setup(tmp_path)
    def prepare():
        assert c.post('/api/new-operations/prepare',json={'bank':'demo'}).status_code==200
        with transaction(path,read_only=True) as db:return dict(db.execute('SELECT * FROM creation_drafts WHERE bank_id=?',(id,)).fetchone())
    assert prepare()['category_id'] is None
    with transaction(path) as db:historical(db,id)
    assert prepare()['category_id'] is None
    with transaction(path) as db:historical(db,id)
    assert prepare()['category_id'] is None
    with transaction(path) as db:historical(db,id)
    assert prepare()['category_id']=='food'
    with transaction(path) as db:historical(db,id,'toys',recipient='different');historical(db,id,'toys',direction=1)
    assert prepare()['category_id']=='food'
    with transaction(path) as db:historical(db,id,'toys')
    assert prepare()['category_id'] is None
    draft(c,id,'food');assert prepare()['source']=='manual'
    draft(c,id,None);assert prepare()['category_id'] is None


@pytest.mark.parametrize('amount',[12345,-12345])
def test_exact_foreign_currency_income_and_expense(tmp_path,amount):
    c,path,remote,id=setup(tmp_path,'USD')
    with transaction(path) as db:db.execute('UPDATE bank_transactions SET amount_minor=?',(amount,))
    draft(c,id);body=request(c)
    assert c.post('/api/new-operations/send',json=body).json()['results'][0]['state']=='sent'
    tx=remote.writes[0][0];assert tx['income' if amount>0 else 'outcome']==123.45


def test_missing_category_does_not_default_to_adjustment(tmp_path):
    c,path,remote,id=setup(tmp_path)
    remote.data['tag'].append({'id':'adjust','title':'Корректировка'})
    p=c.post('/api/new-operations/create',json={'bank':'demo'}).json()
    assert p['created']==[] and p['skipped'][0]['reason']=='Выберите категорию'
    assert c.post('/api/new-operations/preview',json={'bank':'demo'}).json()['items']==[]
    assert not remote.writes


def test_partial_batch_and_incomplete_read_never_create_again(tmp_path):
    c,path,remote,id=setup(tmp_path)
    data=copy.deepcopy(IMPORT);data['transactions'][0].update(external_id='new-second',amount_minor=23456)
    c.post('/api/imports',json=data)
    for row in c.get('/api/state').json()['review']:draft(c,row['id'])
    body=request(c);assert len(body['items'])==2
    remote.behavior='partial'
    result=c.post('/api/new-operations/send',json=body).json()['results']
    assert sorted(r['state'] for r in result)==['sent','uncertain']
    del remote.data['transaction']
    assert c.post('/api/new-operations/verify',json={}).status_code==502
    assert len(remote.writes)==1


def test_local_creation_is_separate_persistent_scoped_and_reversible(tmp_path):
    c,path,remote,id=setup(tmp_path);draft(c,id)
    data=copy.deepcopy(IMPORT);data['transactions'][0].update(external_id='unknown',amount_minor=23456)
    c.post('/api/imports',json=data)
    assert c.post('/api/new-operations/create',json={'bank':'other'}).json()['created']==[]
    # Local creation must work even while ZenMoney is unavailable.
    remote.fail_read=True
    result=c.post('/api/new-operations/create',json={'bank':'demo'}).json()
    assert result['created']==[id] and len(result['skipped'])==1 and not remote.writes
    with transaction(path,read_only=True) as db:
        job=dict(db.execute('SELECT * FROM creation_jobs').fetchone())
        assert job['state']=='prepared'
        assert json.loads(job['payload'])['tag']==['food']
    initialize(path,'live')
    assert c.post('/api/new-operations/create',json={'bank':'demo'}).json()['created']==[]
    remote.fail_read=False
    assert c.post('/api/new-operations/verify',json={}).json()['results'][0]['state']=='prepared'
    body=request(c)
    assert len(body['items'])==1 and not remote.writes
    assert c.post('/api/new-operations/send',json=body).json()['results'][0]['state']=='sent'
    assert remote.writes[0][0]['id']==job['zen_id']
    assert c.post(f'/api/new-operations/{id}/reopen',json={}).status_code==409


def test_reopen_prepared_record_keeps_category_and_invalidates_preview(tmp_path):
    c,path,remote,id=setup(tmp_path);draft(c,id);body=request(c)
    assert c.post(f'/api/new-operations/{id}/reopen',json={}).status_code==200
    assert c.post('/api/new-operations/send',json=body).status_code==409
    draft(c,id,'toys');body=request(c)
    assert c.post('/api/new-operations/send',json=body).json()['results'][0]['state']=='sent'
    assert remote.writes[0][0]['tag']==['toys']


def test_exact_description_auto_category_without_recipient_fields(tmp_path):
    c,path,remote,id=setup(tmp_path)
    with transaction(path) as db:
        raw=json.loads(db.execute('SELECT raw FROM bank_transactions WHERE id=?',(id,)).fetchone()[0])
        raw['details']={}
        db.execute('UPDATE bank_transactions SET raw=? WHERE id=?',(json.dumps(raw),id))
        for _ in range(2):historical(db,id)
        # Real card history may have neither a recipient account nor a name.
        db.execute('UPDATE bank_transactions SET raw=?,description=?',(json.dumps(raw),'Phi GD MOTO tai OPENAI *CHATGPT SUBSCR, the 3610'))
    def category():
        assert c.post('/api/new-operations/prepare',json={'bank':'demo'}).status_code==200
        return next(r for r in c.get('/api/state').json()['review'] if r['id']==id)['creation_draft']
    assert category()['category_id'] is None
    with transaction(path) as db:
        historical(db,id)
        db.execute('UPDATE bank_transactions SET raw=?,description=?',(json.dumps(raw),'Phi GD MOTO tai OPENAI *CHATGPT SUBSCR, the 3610'))
    assert category()['category_id']=='food' and category()['source']=='auto'
    with transaction(path) as db:
        db.execute('UPDATE bank_transactions SET description=? WHERE id=?',('Different card payment',id))
    assert category()['category_id'] is None
    with transaction(path) as db:
        db.execute('UPDATE bank_transactions SET description=? WHERE id=?',('Phi GD MOTO tai OPENAI *CHATGPT SUBSCR, the 3610',id))
        historical(db,id,'toys')
        db.execute('UPDATE bank_transactions SET raw=?,description=?',(json.dumps(raw),'Phi GD MOTO tai OPENAI *CHATGPT SUBSCR, the 3610'))
    assert category()['category_id'] is None
    assert not remote.writes


def test_explicit_recipient_rule_across_accounts_and_manual_precedence(tmp_path):
    c,path,remote,id=setup(tmp_path)
    def add(external,account,name=' SHOP ',amount=-111,bank='demo'):
        data=copy.deepcopy(IMPORT);data.update(account=account,bank=bank)
        data['transactions'][0].update(external_id=external,amount_minor=amount)
        data['transactions'][0]['raw']['details']={'corresponsiveName':name,'corresponsiveAccount':external}
        assert c.post('/api/imports',json=data).status_code==200
        with transaction(path) as db:
            db.execute('UPDATE bank_accounts SET zenmoney_account=?',(DEMO_ACCOUNT,))
            return db.execute('SELECT id FROM bank_transactions WHERE external_id=?',(external,)).fetchone()[0]
    same=add('same','other-account');manual=add('manual','other-account',amount=-112)
    incoming=add('incoming','other-account',amount=113)
    other=add('other','other-account',bank='other');different=add('different','other-account',name='Shop Other')
    draft(c,manual,'toys')
    response=c.put(f'/api/new-operations/{id}/category',json={'category_id':'food','remember_recipient':True})
    assert response.status_code==200,response.text
    def rows():return {r['id']:r for r in c.get('/api/state').json()['review']}
    r=rows();assert r[same]['creation_draft']['category_id']=='food'
    assert r[same]['creation_draft']['source']=='rule' and r[same]['recipient_rule']['title']=='Еда'
    assert r[manual]['creation_draft']['category_id']=='toys'
    for identifier in (incoming,other,different):assert not (r[identifier]['creation_draft'] or {}).get('category_id')
    initialize(path,'live')
    future=add('future','other-account',amount=-114)
    c.post('/api/new-operations/prepare',json={'bank':'demo'})
    assert rows()[future]['creation_draft']['source']=='rule'
    assert c.delete(f'/api/new-operations/{id}/recipient-rule').status_code==200
    r=rows();assert r[same]['creation_draft']['category_id'] is None
    assert r[id]['creation_draft']['category_id']=='food' and r[manual]['creation_draft']['category_id']=='toys'
    assert not remote.writes


def test_rule_requires_name_and_category_and_does_not_change_prepared(tmp_path):
    c,path,remote,id=setup(tmp_path)
    assert c.put(f'/api/new-operations/{id}/category',json={'category_id':None,'remember_recipient':True}).status_code==422
    with transaction(path) as db:
        original=db.execute('SELECT raw FROM bank_transactions WHERE id=?',(id,)).fetchone()[0]
        db.execute('UPDATE bank_transactions SET raw=? WHERE id=?',('{"details":{}}',id))
    assert c.put(f'/api/new-operations/{id}/category',json={'category_id':'food','remember_recipient':True}).status_code==422
    with transaction(path) as db:db.execute('UPDATE bank_transactions SET raw=? WHERE id=?',(original,id))
    assert c.put(f'/api/new-operations/{id}/category',json={'category_id':'food','remember_recipient':True}).status_code==200
    assert c.post('/api/new-operations/create',json={'bank':'demo'}).json()['created']==[id]
    assert c.delete(f'/api/new-operations/{id}/recipient-rule').status_code==200
    with transaction(path,read_only=True) as db:
        job=db.execute('SELECT * FROM creation_jobs').fetchone()
        assert job['state']=='prepared' and json.loads(job['payload'])['tag']==['food']
