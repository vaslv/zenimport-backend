import json
import pytest
from backend.db import transaction, entities
from backend.zenmoney import apply_diff, DEMO_ACCOUNT
from test_reconciliation import bulk_fixture


def ordered_fixture(tmp_path):
    client,path=bulk_fixture(tmp_path)
    with transaction(path) as db:
        txs=entities(db,'transaction')
        for tx in txs:tx['created']=1700000000+int(tx['id'][-1])*100
        apply_diff(db,{'serverTimestamp':2,'transaction':list(reversed(txs))})
        for row in db.execute('SELECT * FROM bank_transactions').fetchall():
            raw=json.loads(row['raw']);day=row['date'].split('-')
            hour='09' if row['external_id'].endswith('-2') else '12'
            raw['date']=f'{day[2]}/{day[1]}/{day[0]} {hour}:00:00'
            db.execute('UPDATE bank_transactions SET raw=? WHERE id=?',(json.dumps(raw),row['id']))
    return client,path


def get_day(client):
    state=client.get('/api/state').json()
    return state,next(d for d in state['day_review'] if d['date']=='2026-10-03')


def body(days):return {'days':[{k:d[k] for k in ('bank_account','date','snapshot')} for d in days]}


@pytest.mark.parametrize('scope',['day','all'])
def test_ordered_preview_bulk_save_uses_chronology_and_no_remote_writes(tmp_path,scope):
    client,path=ordered_fixture(tmp_path);state,day=get_day(client)
    assert day['bulk_eligible'] and day['ambiguous']==0 and len(day['preview_pairs'])==3
    rows={r['id']:r for r in state['review']}
    chronological=[p for p in day['preview_pairs'] if p.get('order')=='chronological']
    assert [(rows[p['bank_id']]['external_id'],p['zen_id']) for p in chronological]==[
        ('2026-10-03-2','2026-10-03-1'),('2026-10-03-1','2026-10-03-2')]
    assert all(r['state']=='pending' for r in state['review'])
    selected=[day] if scope=='day' else state['day_review'];request=body(selected)
    response=client.post('/api/review-pairs/confirm',json=request)
    assert response.json()=={'days':len(selected),'linked':3 if scope=='day' else 6}
    state=client.get('/api/state').json();rows={r['id']:r for r in state['review']}
    for pair in chronological:assert rows[pair['bank_id']]['linked_id']==pair['zen_id']
    assert not state['outbox']
    assert client.post('/api/review-pairs/confirm',json=request).status_code==409
    with transaction(path) as db:
        event=json.loads(db.execute("SELECT data FROM audit WHERE operation='confirm_preview_pairs' ORDER BY id DESC").fetchone()[0])
        assert any(p.get('order')=='chronological' for p in event['pairs'])


def test_repeated_pairs_still_available_in_otherwise_mismatched_day(tmp_path):
    client,path=ordered_fixture(tmp_path)
    with transaction(path) as db:
        apply_diff(db,{'serverTimestamp':3,'transaction':[{'id':'unmatched','date':'2026-10-03','incomeAccount':DEMO_ACCOUNT,'outcomeAccount':DEMO_ACCOUNT,'income':0,'outcome':555}]})
    state,day=get_day(client)
    assert not day['equal'] and len(day['preview_pairs'])==3
    assert client.post('/api/review-pairs/confirm',json=body([day])).json()['linked']==3
    assert not get_day(client)[1]['resolved']


@pytest.mark.parametrize('change',['unequal','excluded','missing_time'])
def test_nonmatching_counts_or_missing_time_remain_manual(tmp_path,change):
    client,path=ordered_fixture(tmp_path)
    if change=='excluded':assert client.put('/api/exclusions/zenmoney/2026-10-03-1',json={}).status_code==200
    else:
        with transaction(path) as db:
            if change=='unequal':
                tx=next(t for t in entities(db,'transaction') if t['id']=='2026-10-03-1')
                apply_diff(db,{'serverTimestamp':3,'transaction':[{**tx,'id':'extra'}]})
            else:
                db.execute("UPDATE bank_transactions SET raw='{}' WHERE external_id='2026-10-03-1'")
    state,day=get_day(client)
    assert len(day['preview_pairs'])==1
    assert not any(p.get('order')=='chronological' for p in day['preview_pairs'])


def test_manual_link_preserved_and_remaining_duplicate_can_be_bulk_saved(tmp_path):
    client,path=ordered_fixture(tmp_path);state,day=get_day(client)
    row=next(r for r in state['review'] if r['external_id']=='2026-10-03-1')
    assert client.post('/api/review/'+row['id']+'/link',json={'action':'link','match_id':'2026-10-03-1','reviewed_candidates':[c['id'] for c in row['candidates']]}).status_code==200
    state,day=get_day(client)
    assert len(day['preview_pairs'])==2
    assert client.post('/api/review-pairs/confirm',json=body([day])).status_code==200
    assert next(r for r in get_day(client)[0]['review'] if r['id']==row['id'])['linked_id']=='2026-10-03-1'


def test_changed_order_invalidates_entire_bulk_snapshot(tmp_path):
    client,path=ordered_fixture(tmp_path);state,day=get_day(client);request=body(state['day_review'])
    with transaction(path) as db:
        tx=next(t for t in entities(db,'transaction') if t['id']=='2026-10-03-1')
        apply_diff(db,{'serverTimestamp':3,'transaction':[{**tx,'created':1700000999}]})
    assert client.post('/api/review-pairs/confirm',json=request).status_code==409
    assert all(r['state']=='pending' for r in get_day(client)[0]['review'])


def test_equal_timestamps_have_stable_order_and_explicit_marker(tmp_path):
    client,path=ordered_fixture(tmp_path)
    with transaction(path) as db:
        txs=entities(db,'transaction')
        for t in txs:t['created']=1700000000
        apply_diff(db,{'serverTimestamp':3,'transaction':txs})
    first=get_day(client)[1]['preview_pairs'];second=get_day(client)[1]['preview_pairs']
    assert first==second
    assert all(p['order_tied'] for p in first if p.get('order')=='chronological')
