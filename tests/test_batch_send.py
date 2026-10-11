import copy
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import transaction
from backend.zenmoney import apply_diff, DEMO_ACCOUNT
from test_creation import setup, draft
from test_flow import TOKEN


def batch(tmp_path):
    c,path,remote,identifier=setup(tmp_path)
    remote.data['transaction']=[{'id':'historical','date':'2026-09-01','income':0,'outcome':100,
        'incomeAccount':DEMO_ACCOUNT,'outcomeAccount':DEMO_ACCOUNT,'incomeInstrument':1,'outcomeInstrument':1,'tag':[],'changed':1}]
    with transaction(path) as db:
        apply_diff(db,{'serverTimestamp':2,**remote.data})
        account=db.execute('SELECT id FROM bank_accounts').fetchone()[0]
    draft(c,identifier)
    assert c.put('/api/zenmoney/transactions/historical/category',json={'category_id':'food','expected_tags':[]}).status_code==200
    def write(creations,changes,cursor):
        remote.writes.append(copy.deepcopy(creations+[p for _,p in changes]))
        if remote.behavior=='fail_before':raise TimeoutError()
        if remote.behavior!='partial':remote.data['transaction'].extend(copy.deepcopy(creations))
        for before,payload in changes:
            remote.data['transaction']=[copy.deepcopy(payload) if t['id']==payload['id'] else t for t in remote.data['transaction']]
        if remote.behavior=='lost_read':remote.fail_read=True
        if remote.behavior=='lost_response':raise TimeoutError()
    remote.write_transactions=write
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
