import copy
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import transaction, entities
from backend.models import CategorySendItem
from backend.category_send import preview, refresh
from test_splits import setup_split
from test_flow import TOKEN


class Remote:
    def __init__(self, data):
        self.data=copy.deepcopy(data);self.writes=[];self.behavior='ok';self.fail_read=False
    def diff(self, request):
        if self.fail_read:raise TimeoutError()
        return {'serverTimestamp':max(10,request['serverTimestamp']),**copy.deepcopy(self.data)}
    def update_categories(self, changes, cursor):
        self.writes.append(copy.deepcopy(changes))
        if self.behavior=='fail_before':raise TimeoutError()
        for index,(before,payload) in enumerate(changes):
            if self.behavior=='partial' and index:continue
            assert {k:v for k,v in before.items() if k not in ('tag','changed')}=={k:v for k,v in payload.items() if k not in ('tag','changed')}
            self.data['transaction']=[copy.deepcopy(payload) if t['id']==payload['id'] else t for t in self.data['transaction']]
        if self.behavior=='lost_response':raise TimeoutError()
        if self.behavior=='lost_read':self.fail_read=True
        return {'serverTimestamp':11}  # No echo: HTTP success is not proof of persistence.


def setup(tmp_path):
    _,path=setup_split(tmp_path)
    with transaction(path) as db:data={k:entities(db,k) for k in ('transaction','account','tag','instrument')}
    remote=Remote(data);c=TestClient(create_app(path,TOKEN,'live',zen_client=remote));c.headers['Authorization']='Bearer '+TOKEN
    for id,original in [('a','food'),('b','toys')]:
        assert c.put(f'/api/zenmoney/transactions/{id}/category',json={'category_id':'toys' if id=='a' else 'food','expected_tags':[original]}).status_code==200
    return c,path,remote


def request(c):
    r=c.post('/api/category-updates/preview',json={});assert r.status_code==200
    return {'items':[{'zen_id':x['zen_id'],'snapshot':x['snapshot']} for x in r.json()['items'] if not x['error']]}


def test_verified_category_update_only_and_no_duplicate_send(tmp_path):
    c,path,remote=setup(tmp_path);body=request(c)
    assert len(body['items'])==2 and not remote.writes
    r=c.post('/api/category-updates/send',json=body)
    assert [x['state'] for x in r.json()['results']]==['sent','sent']
    assert len(remote.writes)==1
    with transaction(path) as db:
        assert db.execute('SELECT count(*) FROM zen_category_drafts').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM zen_category_sends').fetchone()[0]==2
        assert not db.execute('SELECT * FROM outbox').fetchall()
    assert c.post('/api/category-updates/send',json=body).status_code==409
    assert len(remote.writes)==1


@pytest.mark.parametrize('behavior,states',[('lost_response',['sent','sent']),('partial',['sent','not_applied']),('fail_before',['not_applied','not_applied']),('lost_read',['uncertain','uncertain'])])
def test_failure_verification_is_independent_and_never_blindly_retries(tmp_path,behavior,states):
    c,path,remote=setup(tmp_path);body=request(c);remote.behavior=behavior
    assert [r['state'] for r in c.post('/api/category-updates/send',json=body).json()['results']]==states
    assert len(remote.writes)==1
    if behavior=='lost_read':
        assert c.put('/api/zenmoney/transactions/a/category',json={'category_id':'food','expected_tags':['food']}).status_code==409
        remote.fail_read=False
        restarted=TestClient(create_app(path,TOKEN,'live',zen_client=remote));restarted.headers.update(c.headers)
        assert all(r['state']=='sent' for r in restarted.post('/api/category-updates/verify',json={}).json()['results'])
        assert len(remote.writes)==1


def test_changed_remote_or_changed_draft_blocks_whole_batch(tmp_path):
    c,path,remote=setup(tmp_path);body=request(c)
    remote.data['transaction'][0]['comment']='New remote edit'
    assert c.post('/api/category-updates/send',json=body).status_code==409 and not remote.writes
    body=request(c)
    assert c.put('/api/zenmoney/transactions/a/category',json={'category_id':'food','expected_tags':['food']}).status_code==200
    assert c.post('/api/category-updates/send',json=body).status_code==409 and not remote.writes


def test_absent_deleted_excluded_and_removed_category_not_offered(tmp_path):
    c,path,remote=setup(tmp_path)
    remote.data['transaction']=[t for t in remote.data['transaction'] if t['id']!='a']
    remote.data['tag']=[{**t,'deleted':True} for t in remote.data['tag']]
    assert all(x['error'] for x in c.post('/api/category-updates/preview',json={}).json()['items'])
    assert not remote.writes


def test_uncertain_intent_after_crash_is_verified_without_sending(tmp_path):
    c,path,remote=setup(tmp_path);body=request(c);remote.behavior='lost_read'
    c.post('/api/category-updates/send',json=body)
    # A conflicting remote edit after the unknown result must not be called successful.
    remote.fail_read=False;remote.data['transaction'][0]['comment']='Other edit'
    result=c.post('/api/category-updates/verify',json={}).json()['results']
    assert [r['state'] for r in result]==['conflict','sent'] and len(remote.writes)==1


def test_verified_api_normalization_and_conflict_recheck_never_write(tmp_path):
    from backend.category_send import content
    c,path,remote=setup(tmp_path)
    for t in remote.data['transaction']:
        t.update(opIncome=None,opOutcome=None,opIncomeInstrument=None,opOutcomeInstrument=None)
    body=request(c);remote.behavior='lost_read'
    c.post('/api/category-updates/send',json=body)
    remote.fail_read=False
    for t in remote.data['transaction']:t.update(opIncome=0,opOutcome=0)
    # Also recover verification-only false conflicts persisted by the earlier verifier.
    with transaction(path) as db:db.execute("UPDATE zen_category_sends SET state='conflict'")
    result=c.post('/api/category-updates/verify',json={}).json()['results']
    assert all(r['state']=='sent' for r in result) and len(remote.writes)==1
    assert content({'opIncome':None,'opIncomeInstrument':None})==content({'opIncome':0,'opIncomeInstrument':None})
    assert content({'opIncome':None,'opIncomeInstrument':1})!=content({'opIncome':0,'opIncomeInstrument':1})
    assert content({'opIncome':None})!=content({'opIncome':1})
    assert content({})!=content({'opIncome':0})



def test_preview_survives_remote_clock_adjustment_and_uses_fresh_record(tmp_path):
    c,path,remote=setup(tmp_path)
    for tx in remote.data['transaction']:
        tx.update(changed=100, opIncome=None, opOutcome=None,
                  opIncomeInstrument=None, opOutcomeInstrument=None)
    body=request(c)
    for tx in remote.data['transaction']:
        tx.update(changed=99, opIncome=0, opOutcome=0)
    assert request(c)==body
    result=c.post('/api/category-updates/send',json=body)
    assert result.status_code==200
    assert all(r['state']=='sent' for r in result.json()['results'])
    assert len(remote.writes)==1
    assert all(before['changed']==99 for before,payload in remote.writes[0])


@pytest.mark.parametrize('field,value',[
    ('income',1),('outcome',12345),('date','2026-01-01'),
    ('incomeAccount','other'),('incomeInstrument',999),
    ('tag',['other']),('comment','Edited'),('payee','Other recipient'),
    ('opOutcome',7),('opOutcomeInstrument',999),
])
def test_content_changes_still_block_entire_category_batch(tmp_path,field,value):
    c,path,remote=setup(tmp_path)
    body=request(c)
    remote.data['transaction'][0][field]=value
    response=c.post('/api/category-updates/send',json=body)
    assert response.status_code==409
    assert not remote.writes
    with transaction(path,read_only=True) as db:
        assert db.execute('SELECT count(*) FROM zen_category_drafts').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM zen_category_sends').fetchone()[0]==0
