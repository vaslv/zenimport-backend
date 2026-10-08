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
    client,path=setup_split(tmp_path)
    save_split(client)
    url='/api/zenmoney/transactions/a/category'
    with transaction(path) as db:before=entities(db,'transaction')
    body={'category_id':'toys','expected_tags':['food']}
    assert client.put(url,json=body).status_code==200
    restarted=TestClient(create_app(path,TOKEN,'live',zen_client=object()));restarted.headers.update(client.headers)
    state=restarted.get('/api/state').json()
    zs={z['id']:z for z in state['day_review'][0]['zen_rows']}
    assert zs['a']['category_draft']=={'id':'toys','title':'Toys','valid':True,'conflict':False}
    assert zs['b']['category_draft'] is None
    assert state['review'][0]['state']=='linked' and not state['outbox']
    with transaction(path) as db:assert entities(db,'transaction')==before
    assert restarted.put(url,json={**body,'category_id':None}).status_code==200
    assert all(z['category_draft'] is None for z in restarted.get('/api/state').json()['day_review'][0]['zen_rows'])


def test_invalid_categories_exclusions_and_stale_mirror_rejected(tmp_path):
    client,path=setup_split(tmp_path)
    url='/api/zenmoney/transactions/a/category'
    body={'category_id':'food','expected_tags':['food']}
    assert client.put(url,json={**body,'category_id':'missing'}).status_code==409
    assert client.put(url,json=body).status_code==200
    with transaction(path) as db:
        tx=next(t for t in entities(db,'transaction') if t['id']=='a')
        apply_diff(db,{'serverTimestamp':2,'transaction':[{**tx,'tag':['toys']}]})
    z=next(z for z in client.get('/api/state').json()['day_review'][0]['zen_rows'] if z['id']=='a')
    assert not z['category_draft']['valid'] and z['category_draft']['conflict']
    assert client.put(url,json=body).status_code==409
    body['expected_tags']=['toys']
    assert client.put(url,json={**body,'category_id':None}).status_code==200
    assert client.put('/api/exclusions/zenmoney/a',json={}).status_code==200
    assert client.put(url,json=body).status_code==409
    bank=client.get('/api/state').json()['review'][0]['id']
    assert client.put(f'/api/review/{bank}/category',json=body).status_code==404


def test_missing_category_on_confirmed_split_is_editable(tmp_path):
    client,path=setup_split(tmp_path)
    with transaction(path) as db:
        tx=next(t for t in entities(db,'transaction') if t['id']=='a')
        apply_diff(db,{'serverTimestamp':2,'transaction':[{**tx,'tag':[]}]})
    save_split(client)
    assert client.put('/api/zenmoney/transactions/a/category',json={'category_id':'food','expected_tags':[]}).status_code==200
    state=client.get('/api/state').json()
    assert state['review'][0]['linked']['missing_category']
    assert not state['outbox']


def test_unsupported_zen_transactions_and_deleted_category_rejected(tmp_path):
    client,path=setup_split(tmp_path)
    url='/api/zenmoney/transactions/a/category'
    body={'category_id':'food','expected_tags':['food']}
    with transaction(path) as db:
        original=next(t for t in entities(db,'transaction') if t['id']=='a')
    for changes in [{'deleted':True},{'hold':True},{'incomeAccount':'other'}]:
        with transaction(path) as db:apply_diff(db,{'serverTimestamp':3,'transaction':[{**original,**changes}]})
        assert client.put(url,json=body).status_code==409
    with transaction(path) as db:
        apply_diff(db,{'serverTimestamp':4,'transaction':[original],'tag':[{'id':'food','deleted':True}]})
    assert client.put(url,json=body).status_code==409


def test_confirmed_uncategorized_payment_keeps_historical_ranking():
    rows,accounts,history,categories=data()
    row=rows[0]
    tx={**history[0],'id':'current','date':row['date'],'tag':[]}
    row.update(state='linked',linked_id='current',candidates=[{'id':'current'}])
    result=enrich(rows,accounts,[*history,tx],categories)[0]
    assert result['linked'] and not result['linked']['categories']
    assert result['category_ranking'][0]['id']=='food'
    assert result['category_ranking'][0]['count']==2


def test_historical_ranking_uses_later_history_but_excludes_payment_itself():
    rows,accounts,history,categories=data()
    row=rows[0]
    row['date']='2026-08-01'
    # For a new payment, later history remains excluded.
    assert not enrich(copy.deepcopy(rows),accounts,history,categories)[0]['category_ranking']
    tx={**history[0],'id':'current','date':row['date'],'tag':['other']}
    row.update(state='linked',linked_id='current',candidates=[{'id':'current'}])
    result=enrich(rows,accounts,[*history,tx],categories)[0]
    assert [(c['id'],c['count']) for c in result['category_ranking']]==[('food',2)]
