import copy
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import transaction, entities
from backend.reconciliation import enrich
from backend.zenmoney import apply_diff
from test_flow import TOKEN
from test_splits import setup_split, save_split
from test_reconciliation import data


def test_ranked_categories_include_single_example_and_sort_by_frequency():
    rows,accounts,history,categories=data()
    categories.append({'id':'rare','title':'Редкая'})
    history.extend([{**history[0],'id':'third','tag':['other']},{**history[0],'id':'fourth','tag':['rare']},
                    {**history[0],'id':'fifth','tag':['other']}])
    rows[0]['candidates']=[{'id':history[0]['id']}]
    row=enrich(copy.deepcopy(rows),accounts,history,categories)[0]
    assert [(c['id'],c['count']) for c in row['category_ranking']]==[('other',2),('food',2),('rare',1)]
    assert row['category_suggestion']['conflict']
    row=enrich(copy.deepcopy(rows),accounts,history[:1],categories)[0]
    assert row['category_ranking'][0]['count']==1 and not row['category_ranking'][0]['repeated']
    assert row['category_suggestion'] is None


def test_draft_is_local_survives_restart_and_can_be_cleared(tmp_path):
    client,path=setup_split(tmp_path);bank=client.get('/api/state').json()['review'][0]['id']
    url=f'/api/review/{bank}/category'
    with transaction(path) as db:before=entities(db,'transaction')
    assert client.put(url,json={'category_id':'food'}).status_code==200
    restarted=TestClient(create_app(path,TOKEN,'live',zen_client=object()));restarted.headers.update(client.headers)
    state=restarted.get('/api/state').json();row=state['review'][0]
    assert row['category_draft']=={'id':'food','title':'Food','valid':True}
    assert row['state']=='pending' and not state['outbox'] and not state['recipient_profiles']
    with transaction(path) as db:assert entities(db,'transaction')==before
    assert restarted.put(url,json={'category_id':'toys'}).status_code==200
    assert restarted.put(url,json={'category_id':None}).status_code==200
    assert restarted.get('/api/state').json()['review'][0]['category_draft'] is None


def test_deleted_unknown_categories_and_processed_rows_rejected(tmp_path):
    client,path=setup_split(tmp_path);bank=client.get('/api/state').json()['review'][0]['id'];url=f'/api/review/{bank}/category'
    assert client.put(url,json={'category_id':'missing'}).status_code==409
    assert client.put(url,json={'category_id':'food'}).status_code==200
    with transaction(path) as db:apply_diff(db,{'serverTimestamp':2,'tag':[{'id':'food','deleted':True}]})
    assert not client.get('/api/state').json()['review'][0]['category_draft']['valid']
    assert client.put(url,json={'category_id':'food'}).status_code==409
    assert client.put(url,json={'category_id':None}).status_code==200
    assert client.put('/api/exclusions/bank/'+bank,json={}).status_code==200
    assert client.put(url,json={'category_id':'toys'}).status_code==409
    assert client.delete('/api/exclusions/bank/'+bank).status_code==200
    save_split(client)
    assert client.put(url,json={'category_id':'toys'}).status_code==409
