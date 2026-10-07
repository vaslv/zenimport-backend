import copy
import json
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import initialize, transaction
from backend.reconciliation import enrich
from backend.zenmoney import apply_diff, DEMO_ACCOUNT
from test_flow import TOKEN, IMPORT, prepare, add_candidate


def test_local_link_live_is_reversible_and_cannot_write(tmp_path):
    path=tmp_path/'live.db'
    class NoRemote:
        def __getattr__(self,name):
            raise AssertionError('Local linking must not call ZenMoney')
    client=TestClient(create_app(path,TOKEN,'live',zen_client=NoRemote()))
    client.headers['Authorization']='Bearer '+TOKEN
    client.post('/api/imports',json=IMPORT)
    with transaction(path) as db:
        apply_diff(db,{'serverTimestamp':1,'instrument':[{'id':1,'shortTitle':'VND'}],
                       'account':[{'id':DEMO_ACCOUNT,'title':'Bank','instrument':1,'user':1}]})
        db.execute('UPDATE bank_accounts SET zenmoney_account=?',(DEMO_ACCOUNT,))
    add_candidate(path)
    state=client.get('/api/state').json();row=state['review'][0]
    assert row['reconciliation_status']=='possible'
    decision={'action':'link','match_id':'existing','reviewed_candidates':['existing']}
    url=f"/api/review/{row['id']}/link"
    assert client.post(url,json={**decision,'action':'create'}).status_code==422
    assert client.post(url,json={**decision,'reviewed_candidates':[]}).status_code==409
    assert client.post(url,json=decision).status_code==200
    state=client.get('/api/state').json();assert state['review'][0]['reconciliation_status']=='synced'
    assert state['outbox']==[] and state['capabilities']['write'] is False
    assert client.post('/api/outbox/send',json={}).status_code==403
    add_candidate(path,'another')
    with transaction(path) as db:db.execute("DELETE FROM zen_entities WHERE kind='transaction' AND id='existing'")
    assert client.get('/api/state').json()['review'][0]['reconciliation_status']=='conflict'
    assert client.delete(url).status_code==200
    assert client.get('/api/state').json()['review'][0]['state']=='pending'
    assert client.delete(url).status_code==409


def data():
    accounts={'bank':{'zenmoney_account':'zen'}}
    categories=[{'id':'food','title':'Еда'},{'id':'other','title':'Другое'}]
    rows=[{'id':'new','state':'pending','mapped':True,'bank_account':'bank','date':'2026-10-05','amount_minor':-100,
           'candidates':[],'raw':{'details':{'corresponsiveName':' SHOP ','corresponsiveAccount':'123'}}}]
    history=[{'id':str(i),'date':f'2026-09-0{i+1}','incomeAccount':'zen','outcomeAccount':'zen',
              'income':0,'outcome':100,'payee':'Shop','tag':['food']} for i in range(2)]
    return rows,accounts,history,categories


def test_category_evidence_is_scoped_and_conflicts_are_visible():
    rows,accounts,history,categories=data()
    row=enrich(copy.deepcopy(rows),accounts,history,categories)[0]
    assert row['category_suggestion']['category']=='food'
    assert row['category_suggestion']['count']==2
    for patch in [{'deleted':True},{'hold':True},{'date':'2026-10-06'},
                  {'incomeAccount':'different'},{'tag':['food','other']},{'income':100,'outcome':0}]:
        changed=copy.deepcopy(history);changed[1].update(patch)
        assert enrich(copy.deepcopy(rows),accounts,changed,categories)[0]['category_suggestion'] is None
    history[1]['tag']=['other']
    suggestion=enrich(copy.deepcopy(rows),accounts,history,categories)[0]['category_suggestion']
    assert suggestion['conflict'] and suggestion['category'] is None


def test_learning_requires_confirmed_valid_links():
    rows,accounts,history,categories=data()
    for t in history:t['payee']='Different name'
    for i,tx in enumerate(history):
        rows.append({**copy.deepcopy(rows[0]),'id':str(i),'date':tx['date'],'state':'linked','linked_id':tx['id'],
                     'candidates':[{'id':tx['id']} ]})
    assert enrich(copy.deepcopy(rows),accounts,history,categories)[0]['category_suggestion']['count']==2
    rows[1]['state']='pending'
    assert enrich(copy.deepcopy(rows),accounts,history,categories)[0]['category_suggestion'] is None


def test_two_bank_rows_cannot_claim_same_zen_transaction(tmp_path):
    path=tmp_path/'test.db';client=TestClient(create_app(path,TOKEN,'demo'))
    client.headers['Authorization']='Bearer '+TOKEN
    first=prepare(client);add_candidate(path)
    another=copy.deepcopy(IMPORT);another['transactions'][0]['external_id']='another-bank-row'
    client.post('/api/imports',json=another)
    decision={'action':'link','match_id':'existing','reviewed_candidates':['existing']}
    assert client.post(f'/api/review/{first}/link',json=decision).status_code==200
    second=next(t['id'] for t in client.get('/api/state').json()['review'] if t['id']!=first)
    assert client.post(f'/api/review/{second}/link',json=decision).status_code==409
    assert client.get('/api/state').json()['outbox']==[]


def test_confirmed_card_descriptions_can_train_without_recipient_fields():
    rows,accounts,history,categories=data()
    rows[0]['raw']={};rows[0]['description']='GD thanh toan tai SHOP, the 1234'
    for i,tx in enumerate(history):
        tx['payee']='Different display name'
        rows.append({**copy.deepcopy(rows[0]),'id':str(i),'date':tx['date'],'state':'linked','linked_id':tx['id'],'candidates':[{'id':tx['id']}]})
    assert enrich(copy.deepcopy(rows),accounts,history,categories)[0]['category_suggestion']['count']==2
    rows[0]['description']='Phi GD thanh toan tai SHOP, the 1234'
    assert enrich(copy.deepcopy(rows),accounts,history,categories)[0]['category_suggestion'] is None


def test_day_confirmation_is_explicit_atomic_and_stale_safe(tmp_path):
    path=tmp_path/'days.db';client=TestClient(create_app(path,TOKEN,'demo'))
    client.headers['Authorization']='Bearer '+TOKEN
    first=prepare(client);add_candidate(path)
    with transaction(path) as db: db.execute("UPDATE bank_transactions SET date='2026-10-02'")
    state=client.get('/api/state').json();day=state['day_review'][0]
    assert day['equal'] and len(day['pairs'])==1
    assert state['review'][0]['state']=='pending'
    url=f"/api/review-days/{day['bank_account']}/{day['date']}/confirm"
    assert client.post(url,json={'snapshot':'0'*64}).status_code==409
    assert client.post(url,json={'snapshot':day['snapshot']}).json()=={'linked':1}
    assert client.post(url,json={'snapshot':day['snapshot']}).status_code==409
    state=client.get('/api/state').json()
    assert state['review'][0]['state']=='linked' and not state['outbox']
    client.delete(f'/api/review/{first}/link')
    day=client.get('/api/state').json()['day_review'][0]
    add_candidate(path,'extra')
    assert client.post(url,json={'snapshot':day['snapshot']}).status_code==409
    state=client.get('/api/state').json()
    assert not state['day_review'][0]['equal'] and not state['day_review'][0]['pairs']
    assert state['review'][0]['state']=='pending'


def test_duplicate_amounts_require_individual_links(tmp_path):
    path=tmp_path/'duplicate.db';client=TestClient(create_app(path,TOKEN,'demo'))
    client.headers['Authorization']='Bearer '+TOKEN
    first=prepare(client);add_candidate(path);add_candidate(path,'second')
    imported=copy.deepcopy(IMPORT);imported['transactions'][0]['external_id']='second'
    client.post('/api/imports',json=imported)
    with transaction(path) as db: db.execute("UPDATE bank_transactions SET date='2026-10-02'")
    day=client.get('/api/state').json()['day_review'][0]
    assert day['equal'] and day['ambiguous']==2 and not day['pairs']
    client.post(f'/api/review/{first}/link',json={'action':'link','match_id':'existing','reviewed_candidates':['existing','second']})
    day=client.get('/api/state').json()['day_review'][0]
    assert day['equal'] and day['ambiguous']==0 and len(day['pairs'])==1


def test_recipient_profile_keeps_multiple_categories_and_removes_unlinked_evidence():
    from backend.reconciliation import recipient_profiles
    rows,accounts,history,categories=data()
    history.append({**history[0],'id':'third','tag':['other']})
    linked=[]
    for tx in history:
        linked.append({**copy.deepcopy(rows[0]),'id':tx['id'],'date':tx['date'],'state':'linked','linked_id':tx['id'],'candidates':[{'id':tx['id']}]})
    profiles=recipient_profiles(enrich(linked,accounts,history,categories),accounts,history,categories)
    assert [(c['id'],c['count'],c['repeated']) for c in profiles[0]['categories']]==[('food',2,True),('other',1,False)]
    linked[0]['state']='pending'
    profiles=recipient_profiles(enrich(linked,accounts,history,categories),accounts,history,categories)
    assert all(c['count']==1 for c in profiles[0]['categories'])
