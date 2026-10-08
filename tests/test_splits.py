import copy
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import transaction
from backend.zenmoney import apply_diff, DEMO_ACCOUNT
from backend.splits import search_splits
from test_flow import IMPORT, TOKEN


def setup_split(tmp_path):
    path=tmp_path/'splits.db'
    client=TestClient(create_app(path,TOKEN,'live',zen_client=object()))
    client.headers['Authorization']='Bearer '+TOKEN
    imported=copy.deepcopy(IMPORT);imported['transactions'][0]['amount_minor']=-100
    assert client.post('/api/imports',json=imported).status_code==200
    with transaction(path) as db:
        apply_diff(db,{'serverTimestamp':1,'instrument':[{'id':1,'shortTitle':'VND'}],
            'account':[{'id':DEMO_ACCOUNT,'title':'Bank','instrument':1,'user':1}],
            'tag':[{'id':'food','title':'Food'},{'id':'toys','title':'Toys'}],
            'transaction':[{'id':identifier,'date':'2026-10-01','incomeAccount':DEMO_ACCOUNT,'outcomeAccount':DEMO_ACCOUNT,
                'income':0,'outcome':amount,'tag':[cat],'payee':'Shop'} for identifier,amount,cat in [('a',40,'food'),('b',60,'toys')]]})
        db.execute('UPDATE bank_accounts SET zenmoney_account=?',(DEMO_ACCOUNT,))
    return client,path


def save_split(client):
    state=client.get('/api/state').json();day=state['day_review'][0];row=state['review'][0]
    body={'snapshot':day['snapshot'],'zen_ids':['a','b']}
    assert client.post(f"/api/review/{row['id']}/split-link",json=body).status_code==200
    return row['id'],body


def test_split_live_preview_confirm_restart_unlink(tmp_path):
    client,path=setup_split(tmp_path)
    state=client.get('/api/state').json();day=state['day_review'][0];row=state['review'][0]
    assert row['state']=='pending' and not state['outbox']
    assert day['bank_total']==day['zen_total']=='-100' and not day['equal']
    assert day['split_candidates']==[{'bank_id':row['id'],'zen_ids':['a','b'],'unique':True}]
    url=f"/api/review/{row['id']}/split-link"
    assert client.post(url,json={'snapshot':'0'*64,'zen_ids':['a','b']}).status_code==409
    assert client.post(url,json={'snapshot':day['snapshot'],'zen_ids':['a','a']}).status_code==409
    identifier,body=save_split(client)
    assert client.post(url,json=body).status_code==409
    restarted=TestClient(create_app(path,TOKEN,'live',zen_client=object()));restarted.headers.update(client.headers)
    state=restarted.get('/api/state').json();row=state['review'][0]
    assert row['reconciliation_status']=='synced' and row['linked']['split']
    assert len(row['linked_parts'])==2 and row['split_ids']==['a','b']
    assert state['day_review'][0]['resolved'] and all(z['linked_bank_id']==identifier for z in state['day_review'][0]['zen_rows'])
    assert sorted(c['count'] for c in state['recipient_profiles'][0]['categories'])==[1,1]
    assert not state['outbox'] and restarted.post('/api/outbox/send',json={}).status_code==403
    assert restarted.delete(f'/api/review/{identifier}/link').status_code==200
    state=restarted.get('/api/state').json()
    assert state['review'][0]['split_ids']==[] and not state['recipient_profiles']
    assert state['day_review'][0]['split_candidates']


def test_split_children_cannot_be_reused_and_deleted_child_is_conflict(tmp_path):
    client,path=setup_split(tmp_path);identifier,_=save_split(client)
    imported=copy.deepcopy(IMPORT);imported['transactions'][0].update(external_id='other',amount_minor=-40)
    client.post('/api/imports',json=imported)
    row=next(r for r in client.get('/api/state').json()['review'] if r['id']!=identifier)
    assert client.post(f"/api/review/{row['id']}/link",json={'action':'link','match_id':'a','reviewed_candidates':['a']}).status_code==409
    with transaction(path) as db:
        apply_diff(db,{'serverTimestamp':2,'transaction':[{'id':'b','deleted':True}]})
    row=next(r for r in client.get('/api/state').json()['review'] if r['id']==identifier)
    assert row['reconciliation_status']=='conflict' and not row['linked_parts']


def test_split_search_ambiguity_direction_transfers_and_bounds():
    bank=[{'id':'bank','state':'pending','amount_minor':-100}]
    def z(identifier,amount,transfer=False):return {'id':identifier,'amount_minor':str(amount),'transfer':transfer}
    candidates,limited=search_splits(bank,[z('a',-40),z('b',-60),z('c',-30),z('d',-70),z('income',100)])
    assert len(candidates)==2 and all(not c['unique'] for c in candidates) and not limited
    assert search_splits(bank,[z('a',-40),z('b',-60,True)])[0]==[]
    assert search_splits(bank,[z('a',-40),{**z('b',-60),'split_eligible':False}])[0]==[]
    assert search_splits(bank,[z('a',-100),z('b',-40),z('c',-60)])[0]==[]
    both=bank+[{'id':'other','state':'pending','amount_minor':-100}]
    assert all(not c['unique'] for c in search_splits(both,[z('a',-40),z('b',-60)])[0])
    assert search_splits(bank,[z(str(i),-1) for i in range(19)])[1]


def test_v2_upgrade_preserves_existing_single_links(tmp_path):
    client,path=setup_split(tmp_path)
    with transaction(path) as db:
        db.execute('DROP VIEW split_links')
        db.execute('CREATE TABLE split_links(bank_id TEXT, zen_id TEXT)')
        db.execute('PRAGMA user_version=2')
        db.execute("UPDATE bank_transactions SET state='linked',linked_id='old-existing'")
    create_app(path,TOKEN,'live',zen_client=object())
    with transaction(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==7
        assert db.execute('SELECT linked_id FROM bank_transactions').fetchone()[0]=='old-existing'
        assert db.execute('SELECT count(*) FROM split_links').fetchone()[0]==0


def test_split_learning_counts_payments_not_repeated_parts(tmp_path):
    client,path=setup_split(tmp_path);identifier,_=save_split(client)
    # A later payment to the same recipient sees both categories, never a confident single one.
    imported=copy.deepcopy(IMPORT);imported['transactions'][0].update(external_id='later',date='2026-10-10',amount_minor=-100)
    client.post('/api/imports',json=imported)
    later=next(r for r in client.get('/api/state').json()['review'] if r['id']!=identifier)
    assert later['category_suggestion']['conflict']
    assert later['category_suggestion']['count']==1
    assert {c['title'] for c in later['category_suggestion']['distribution']}=={'Food','Toys'}
