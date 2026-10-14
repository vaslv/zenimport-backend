import copy
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import transaction
from backend.zenmoney import apply_diff, DEMO_ACCOUNT
from test_creation import setup, draft
from test_flow import TOKEN
from test_flow import IMPORT


def batch(tmp_path):
    c,path,remote,identifier=setup(tmp_path)
    remote.data['transaction']=[{'id':'historical','date':'2026-09-01','income':0,'outcome':100,
        'incomeAccount':DEMO_ACCOUNT,'outcomeAccount':DEMO_ACCOUNT,'incomeInstrument':1,'outcomeInstrument':1,'tag':[],'changed':1}]
    with transaction(path) as db:
        apply_diff(db,{'serverTimestamp':2,**remote.data})
        account=db.execute('SELECT id FROM bank_accounts').fetchone()[0]
    draft(c,identifier)
    assert c.put('/api/zenmoney/transactions/historical/category',json={'category_id':'food','expected_tags':[]}).status_code==200
    def write(kind,payloads,cursor=0):
        assert kind == 'transaction'
        existing={t['id']:t for t in remote.data['transaction']}
        creations=[p for p in payloads if p['id'] not in existing]
        changes=[(existing[p['id']],p) for p in payloads if p['id'] in existing]
        remote.writes.append(copy.deepcopy(creations+[p for _,p in changes]))
        if remote.behavior=='fail_before':raise TimeoutError()
        if remote.behavior!='partial':remote.data['transaction'].extend(copy.deepcopy(creations))
        for before,payload in changes:
            remote.data['transaction']=[copy.deepcopy(payload) if t['id']==payload['id'] else t for t in remote.data['transaction']]
        if remote.behavior=='lost_read':remote.fail_read=True
        if remote.behavior=='lost_response':raise TimeoutError()
    remote.upsert=write
    scope={'bank':'demo','account':account}
    response=c.post('/api/batch/preview',json=scope)
    assert response.status_code==200,response.text
    items=response.json()['items'];assert len(items)==2 and all(not i['error'] for i in items)
    body={**scope,'items':[{k:i[k] for k in ('kind','id','snapshot')} for i in items]}
    return c,path,remote,scope,body


def test_mixed_one_write_and_retry_blocked(tmp_path):
    c,path,remote,scope,body=batch(tmp_path)
    assert not remote.writes
    r=c.post('/api/batch/send',json=body);assert r.status_code==200,r.text
    assert [i['state'] for i in r.json()['results']]==['sent','sent']
    assert len(remote.writes)==1 and len(remote.writes[0])==2
    assert c.post('/api/batch/send',json=body).status_code==409
    assert len(remote.writes)==1
    assert c.post('/api/batch/preview',json=scope).json()['items']==[]


def test_results_have_durable_metadata_and_respect_period_and_creation_mode(tmp_path):
    c,path,remote,scope,body=batch(tmp_path)
    assert c.post('/api/batch/send',json=body).status_code==200
    data=copy.deepcopy(IMPORT);data['transactions'][0].update(external_id='waiting',date='2026-10-05',amount_minor=-12345,description='Waiting expense')
    assert c.post('/api/imports',json=data).status_code==200
    waiting=next(r['id'] for r in c.get('/api/state').json()['review'] if r['external_id']=='waiting')
    draft(c,waiting);assert c.post('/api/new-operations/create',json={'bank':'demo'}).status_code==200
    response=c.post('/api/batch/verify',json=scope);assert response.status_code==200
    records=response.json()['results'];assert len(records)==3
    created=next(r for r in records if r['kind']=='creation' and r['state']=='sent')
    assert created['description']=='Coffee' and created['date']=='2026-10-01' and created['amount']=='-65000' and created['currency']=='VND'
    pending=next(r for r in records if r['state']=='prepared')
    assert pending['description']=='Waiting expense' and pending['date']=='2026-10-05' and pending['amount']=='-12345'
    categories=next(r for r in records if r['kind']=='category')
    assert categories['date']=='2026-09-01' and categories['amount']=='-100'
    response=c.post('/api/batch/verify',json={**scope,'creations_only':True})
    assert len(response.json()['results'])==2 and all(r['kind']=='creation' for r in response.json()['results'])
    response=c.post('/api/batch/verify',json={**scope,'start':'2026-10-01','end':'2026-10-02'})
    assert response.json()['results']==[created]
    with transaction(path) as db:db.execute("UPDATE bank_transactions SET description='' WHERE id=?",(waiting,))
    response=c.post('/api/batch/verify',json=scope)
    assert next(r for r in response.json()['results'] if r['state']=='prepared')['description']=='Без описания'
    assert len(remote.writes)==1


def test_missing_remote_record_keeps_last_known_result_description_and_state(tmp_path):
    c,path,remote,scope,body=batch(tmp_path);remote.behavior='lost_read'
    c.post('/api/batch/send',json=body);remote.fail_read=False;remote.data['transaction']=[]
    response=c.post('/api/batch/verify',json=scope)
    assert response.status_code==200
    records=response.json()['results'];assert len(records)==2
    category=next(r for r in records if r['kind']=='category')
    assert category['date']=='2026-09-01' and category['amount']=='-100' and category['state']=='uncertain'
    assert next(r for r in records if r['kind']=='creation')['description']=='Coffee'
    assert len(remote.writes)==1


@pytest.mark.parametrize('behavior',['lost_response','lost_read','partial','fail_before'])
def test_mixed_outcomes_and_restart(tmp_path,behavior):
    c,path,remote,scope,body=batch(tmp_path);remote.behavior=behavior
    r=c.post('/api/batch/send',json=body);assert r.status_code==200,r.text
    states={i['kind']:i['state'] for i in r.json()['results']}
    if behavior=='lost_response':assert set(states.values())=={'sent'}
    elif behavior=='lost_read':assert set(states.values())=={'uncertain'}
    elif behavior=='partial':assert states=={'creation':'uncertain','category':'sent'}
    else:assert states=={'creation':'uncertain','category':'not_applied'}
    remote.fail_read=False
    restarted=TestClient(create_app(path,TOKEN,'live',zen_client=remote));restarted.headers.update(c.headers)
    assert restarted.post('/api/batch/verify',json=scope).status_code==200
    assert restarted.post('/api/batch/send',json=body).status_code==409
    assert len(remote.writes)==1


@pytest.mark.parametrize('change',['remote','duplicate','bank','category','oversize'])
def test_entire_mixed_batch_rejected_before_write(tmp_path,change):
    c,path,remote,scope,body=batch(tmp_path)
    if change=='remote':remote.data['transaction'][0]['outcome']=999
    elif change=='duplicate':body['items']*=2
    elif change=='oversize':body['items']*=11
    elif change=='bank':body['bank']='other'
    else:
        with transaction(path) as db:db.execute("UPDATE creation_drafts SET category_id='toys'")
    assert c.post('/api/batch/send',json=body).status_code in (409,422)
    assert not remote.writes


def test_creation_only_batch_excludes_category_changes_and_respects_mode(tmp_path):
    c,path,remote,scope,body=batch(tmp_path)
    creation=next(i for i in body['items'] if i['kind']=='creation')
    assert c.post('/api/new-operations/'+creation['id']+'/reopen',json={}).status_code==200
    draft(c,creation['id'],None)
    scope={**scope,'creations_only':True}
    assert c.post('/api/batch/preview',json=scope).json()['items']==[]
    scope['include_uncategorized']=True
    items=c.post('/api/batch/preview',json=scope).json()['items']
    assert len(items)==1 and items[0]['category'] is None and items[0]['kind']=='creation'
    assert c.post('/api/batch/send',json={**scope,'items':body['items']}).status_code==409
    assert not remote.writes
    result=c.post('/api/batch/send',json={**scope,'items':[{k:items[0][k] for k in ('kind','id','snapshot')}]})
    assert result.status_code==200,result.text
    assert result.json()['results'][0]['state']=='sent'
    assert len(remote.writes)==1 and len(remote.writes[0])==1 and remote.writes[0][0]['tag'] is None
    assert remote.data['transaction'][0]['tag']==[]


def test_failed_write_diagnostic_survives_verification_and_explicit_retry_keeps_uuid(tmp_path):
    from zenmoney_api import HTTPError
    c,path,remote,scope,body=batch(tmp_path)
    original=remote.upsert
    def rejected(*args):raise HTTPError(400)
    remote.upsert=rejected
    response=c.post('/api/batch/send',json=body)
    created=next(r for r in response.json()['results'] if r['kind']=='creation')
    assert 'HTTP 400' in created['error']
    response=c.post('/api/batch/verify',json=scope)
    assert 'HTTP 400' in next(r for r in response.json()['results'] if r['kind']=='creation')['error']
    with transaction(path,read_only=True) as db:
        before=dict(db.execute('SELECT * FROM creation_jobs').fetchone())
    assert c.post('/api/batch/retry-preview',json=scope).status_code==422
    requests=[];old_diff=remote.read_diff
    def read(cursor=0, *, force_fetch=None):
        requests.append({'serverTimestamp':cursor, 'forceFetch':force_fetch})
        return old_diff(cursor, force_fetch=force_fetch)
    remote.read_diff=read
    retry_scope={**scope,'creations_only':True,'include_uncategorized':True}
    response=c.post('/api/batch/retry-preview',json=retry_scope)
    assert response.status_code==200,response.text
    items=response.json()['items'];assert len(items)==1 and not items[0]['error']
    assert requests[0]['serverTimestamp']==0 and not remote.writes
    with transaction(path,read_only=True) as db:
        after=dict(db.execute('SELECT * FROM creation_jobs').fetchone())
        assert before['zen_id']==after['zen_id'] and before['payload']==after['payload']
    remote.upsert=original
    response=c.post('/api/batch/send',json={**retry_scope,'items':[{k:items[0][k] for k in ('kind','id','snapshot')}]})
    assert response.json()['results'][0]['state']=='sent'
    assert len(remote.writes)==1 and remote.writes[0][0]['id']==before['zen_id']


def test_retry_never_reprepares_remote_applied_record_after_lost_read(tmp_path):
    c,path,remote,scope,body=batch(tmp_path);remote.behavior='lost_read'
    c.post('/api/batch/send',json=body)
    remote.fail_read=False
    response=c.post('/api/batch/retry-preview',json={**scope,'creations_only':True,'include_uncategorized':True})
    assert response.status_code==200 and response.json()['items']==[]
    assert len(remote.writes)==1
