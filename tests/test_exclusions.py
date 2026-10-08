import copy
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import transaction, entities
from backend.zenmoney import apply_diff
from test_splits import setup_split, save_split
from test_flow import IMPORT, TOKEN
from test_link_groups import setup_settlement, decision


def test_exclusions_resolve_day_restore_and_survive_sync_restart(tmp_path):
    client,path=setup_split(tmp_path)
    initial=client.get('/api/state').json();bank=initial['review'][0]['id']
    for source,id in [('bank',bank),('zenmoney','a'),('zenmoney','b')]:
        assert client.put(f'/api/exclusions/{source}/{id}',json={}).status_code==200
    restarted=TestClient(create_app(path,TOKEN,'live',zen_client=object()));restarted.headers.update(client.headers)
    assert restarted.post('/api/imports',json={**copy.deepcopy(IMPORT),'transactions':[{**IMPORT['transactions'][0],'amount_minor':-100}]}).json()['duplicates']==1
    with transaction(path) as db:
        tx=next(t for t in entities(db,'transaction') if t['id']=='a')
        apply_diff(db,{'serverTimestamp':2,'transaction':[{**tx,'comment':'Changed'}]})
    state=restarted.get('/api/state').json();day=state['day_review'][0]
    assert day['resolved'] and day['equal'] and day['bank_count']==day['zen_count']==0
    assert day['bank_total']==day['zen_total']=='-100'
    assert day['reconcile_bank_total']==day['reconcile_zen_total']=='0'
    assert day['excluded_count']==3 and not day['split_candidates'] and not day['preview_pairs']
    assert state['review'][0]['candidates']==[] and state['excluded_zen_ids']==['a','b']
    assert not state['outbox'] and not state['recipient_profiles']
    assert restarted.delete('/api/exclusions/bank/'+bank).status_code==200
    assert not restarted.get('/api/state').json()['day_review'][0]['resolved']
    for id in ['a','b']:assert restarted.delete('/api/exclusions/zenmoney/'+id).status_code==200
    assert restarted.get('/api/state').json()['day_review'][0]['split_candidates']


def test_exclusion_blocks_stale_split_and_settlement(tmp_path):
    client,path=setup_settlement(tmp_path);body=decision(client)
    assert client.put('/api/exclusions/zenmoney/net',json={}).status_code==200
    assert client.post('/api/reconciliation-groups',json=body).status_code==409
    current=client.get('/api/state').json();body['snapshot']=current['day_review'][0]['snapshot']
    assert client.post('/api/reconciliation-groups',json=body).status_code==409
    assert all(r['state']=='pending' for r in current['review'])


def test_linked_members_cannot_be_excluded(tmp_path):
    client,path=setup_split(tmp_path);bank,_=save_split(client)
    for source,id in [('bank',bank),('zenmoney','a')]:
        assert client.put(f'/api/exclusions/{source}/{id}',json={}).status_code==409
    assert client.get('/api/state').json()['review'][0]['linked']['split']


def test_excluded_exact_match_cannot_be_saved_and_bank_ignore_needs_no_mapping(tmp_path):
    client,path=setup_split(tmp_path)
    with transaction(path) as db:db.execute('UPDATE bank_transactions SET amount_minor=-40')
    state=client.get('/api/state').json();bank=state['review'][0]['id'];day=state['day_review'][0]
    assert client.put('/api/exclusions/zenmoney/a',json={}).status_code==200
    assert client.post('/api/review/'+bank+'/link',json={'action':'link','match_id':'a','reviewed_candidates':[]}).status_code==409
    assert client.post('/api/review-pairs/confirm',json={'days':[{'bank_account':day['bank_account'],'date':day['date'],'snapshot':day['snapshot']}]}).status_code==409
    with transaction(path) as db:db.execute('UPDATE bank_accounts SET zenmoney_account=NULL')
    assert client.put('/api/exclusions/bank/'+bank,json={}).status_code==200
    assert client.delete('/api/exclusions/bank/'+bank).status_code==200


def test_invalid_exclusion_and_zen_without_mapped_account(tmp_path):
    client,path=setup_split(tmp_path)
    assert client.put('/api/exclusions/nope/a',json={}).status_code==422
    assert client.put('/api/exclusions/bank/missing',json={}).status_code==404
    assert client.put('/api/exclusions/zenmoney/missing',json={}).status_code==409
    with transaction(path) as db:db.execute('UPDATE bank_accounts SET zenmoney_account=NULL')
    assert client.put('/api/exclusions/zenmoney/a',json={}).status_code==200
    assert client.delete('/api/exclusions/zenmoney/a').status_code==200


def test_excluded_zenmoney_does_not_train_categories(tmp_path):
    client,path=setup_split(tmp_path)
    with transaction(path) as db:
        template=next(t for t in entities(db,'transaction') if t['id']=='a')
        apply_diff(db,{'serverTimestamp':2,'transaction':[{**template,'id':'history-'+str(i),'date':'2026-09-01','outcome':10} for i in range(2)]})
    imported=copy.deepcopy(IMPORT);imported['transactions'][0].update(external_id='future',date='2026-10-10',amount_minor=-200,
        raw={**IMPORT['transactions'][0]['raw'],'details':{'corresponsiveName':'Shop'}})
    assert client.post('/api/imports',json=imported).status_code==200
    def later():return next(r for r in client.get('/api/state').json()['review'] if r['external_id']=='future')
    assert later()['category_suggestion']
    for id in ['a','b','history-0','history-1']:assert client.put('/api/exclusions/zenmoney/'+id,json={}).status_code==200
    assert later()['category_suggestion'] is None
