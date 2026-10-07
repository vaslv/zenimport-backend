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


def bulk_fixture(tmp_path):
    path=tmp_path/'bulk.db';client=TestClient(create_app(path,TOKEN,'demo'))
    client.headers['Authorization']='Bearer '+TOKEN
    prepare(client)
    txs=[]
    for day, amounts in [('2026-10-01',[65000]),('2026-10-02',[100,200]),('2026-10-03',[300,400,400])]:
        for i,amount in enumerate(amounts):
            identifier=f'{day}-{i}'
            if day!='2026-10-01':
                imported=copy.deepcopy(IMPORT);row=imported['transactions'][0]
                row.update(external_id=identifier,date=day,amount_minor=-amount)
                assert client.post('/api/imports',json=imported).status_code==200
            txs.append({'id':identifier,'date':day,'incomeAccount':DEMO_ACCOUNT,'outcomeAccount':DEMO_ACCOUNT,
                        'income':0,'outcome':amount,'tag':[]})
    with transaction(path) as db:apply_diff(db,{'serverTimestamp':1,'transaction':txs})
    return client,path


def bulk_body(state):
    return {'days':[{k:d[k] for k in ('bank_account','date','snapshot')} for d in state['day_review'] if d['bulk_eligible']]}


def test_bulk_days_excludes_whole_ambiguous_day_and_is_reversible(tmp_path):
    client,path=bulk_fixture(tmp_path)
    state=client.get('/api/state').json();body=bulk_body(state)
    assert len(body['days'])==2
    doubtful=next(d for d in state['day_review'] if d['date']=='2026-10-03')
    assert len(doubtful['pairs'])==1 and doubtful['ambiguous']==2 and not doubtful['bulk_eligible']
    assert client.post('/api/review-days/confirm',json=body).json()=={'days':2,'linked':3}
    state=client.get('/api/state').json()
    assert all(r['state']=='pending' for r in state['review'] if r['date']=='2026-10-03')
    assert not state['outbox']
    assert client.post('/api/review-days/confirm',json=body).status_code==409
    linked=next(r for r in state['review'] if r['state']=='linked')
    assert client.delete(f"/api/review/{linked['id']}/link").status_code==200


def test_bulk_stale_or_ambiguous_day_rejects_entire_batch(tmp_path):
    client,path=bulk_fixture(tmp_path)
    state=client.get('/api/state').json();body=bulk_body(state)
    bad=copy.deepcopy(body);bad['days'][-1]['snapshot']='0'*64
    assert client.post('/api/review-days/confirm',json=bad).status_code==409
    doubtful=next(d for d in state['day_review'] if not d['bulk_eligible'])
    bad=copy.deepcopy(body);bad['days'].append({k:doubtful[k] for k in ('bank_account','date','snapshot')})
    assert client.post('/api/review-days/confirm',json=bad).status_code==409
    assert client.post('/api/review-days/confirm',json={'days':[body['days'][0]]*2}).status_code==409
    assert client.post('/api/review-days/confirm',json={'days':[]}).status_code==422
    with transaction(path) as db:
        tx=json.loads(db.execute("SELECT data FROM zen_entities WHERE kind='transaction' AND id='2026-10-01-0'").fetchone()[0])
        tx['comment']='Changed after preview'
        apply_diff(db,{'serverTimestamp':2,'transaction':[tx]})
    assert client.post('/api/review-days/confirm',json=body).status_code==409
    assert all(r['state']=='pending' for r in client.get('/api/state').json()['review'])


def test_bulk_respects_selected_days_and_rejects_shared_zen_claims(tmp_path):
    client,path=bulk_fixture(tmp_path)
    state=client.get('/api/state').json();body=bulk_body(state)
    chosen=next(d for d in body['days'] if d['date']=='2026-10-01')
    assert client.post('/api/review-days/confirm',json={'days':[chosen]}).json()=={'days':1,'linked':1}
    imported=copy.deepcopy(IMPORT);imported['account']='other-bank-account'
    row=imported['transactions'][0];row.update(date='2026-10-02',amount_minor=-100)
    other=copy.deepcopy(row);other.update(external_id='other',amount_minor=-200)
    imported['transactions'].append(other)
    client.post('/api/imports',json=imported)
    with transaction(path) as db: db.execute('UPDATE bank_accounts SET zenmoney_account=?',(DEMO_ACCOUNT,))
    days=[d for d in client.get('/api/state').json()['day_review'] if d['date']=='2026-10-02']
    assert len(days)==2 and all(not d['bulk_eligible'] for d in days)


def test_bulk_builds_candidate_index_once_not_per_pair(tmp_path, monkeypatch):
    import backend.service as service
    import backend.day_review as days
    client,path=bulk_fixture(tmp_path)
    body=bulk_body(client.get('/api/state').json())
    calls=[];original=service.candidate_index
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(service,'candidate_index',counted)
    monkeypatch.setattr(days,'candidate_index',counted)
    assert client.post('/api/review-days/confirm',json=body).json()=={'days':2,'linked':3}
    # Review, day proposal and one shared confirmation index; not one per linked row.
    assert len(calls)==3


def test_state_and_history_read_committed_snapshot_during_write(tmp_path):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    client,path=bulk_fixture(tmp_path)
    before=client.get('/api/state').json()
    writer=sqlite3.connect(path)
    writer.execute('BEGIN IMMEDIATE')
    writer.execute("UPDATE bank_transactions SET state='ignored'")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            state=pool.submit(client.get,'/api/state')
            history=pool.submit(client.get,'/api/zenmoney/history')
            response=state.result(timeout=5)
            assert response.status_code==200
            assert response.json()['review']==before['review']
            assert history.result(timeout=5).status_code==200
    finally:
        writer.rollback();writer.close()
    with transaction(path,read_only=True) as db:
        import pytest
        with pytest.raises(sqlite3.OperationalError):
            db.execute("UPDATE bank_transactions SET state='ignored'")
