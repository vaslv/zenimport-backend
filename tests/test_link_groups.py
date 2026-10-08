import copy
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import transaction, initialize
from backend.link_groups import read
from backend.zenmoney import apply_diff, DEMO_ACCOUNT
from test_flow import IMPORT, TOKEN
from test_splits import setup_split


def setup_settlement(tmp_path, many=False):
    client,path=setup_split(tmp_path)
    with transaction(path) as db:
        db.execute('UPDATE bank_transactions SET amount_minor=-961440')
        apply_diff(db,{'serverTimestamp':2,'transaction':[
            {'id':'a','deleted':True},{'id':'b','deleted':True},
            *[{'id':identifier,'date':'2026-10-01','incomeAccount':DEMO_ACCOUNT,'outcomeAccount':DEMO_ACCOUNT,
               'income':amount,'outcome':0,'tag':['food'],'payee':'Shop'}
              for identifier,amount in ([('net',30000),('extra',8560)] if many else [('net',38560)])]]})
    imported=copy.deepcopy(IMPORT);imported['transactions'][0].update(external_id='refund',amount_minor=1000000)
    assert client.post('/api/imports',json=imported).status_code==200
    return client,path


def decision(client):
    state=client.get('/api/state').json();day=state['day_review'][0]
    return {'bank_ids':day['bank_ids'],'zen_ids':[z['id'] for z in day['zen_rows']], 'snapshot':day['snapshot']}


@pytest.mark.parametrize('many',[False,True])
def test_settlement_preserves_members_restart_and_atomic_unlink(tmp_path,many):
    client,path=setup_settlement(tmp_path,many);body=decision(client)
    result=client.post('/api/reconciliation-groups',json=body)
    assert result.status_code==200,result.text
    restarted=TestClient(create_app(path,TOKEN,'live',zen_client=object()));restarted.headers.update(client.headers)
    state=restarted.get('/api/state').json()
    assert all(r['reconciliation_status']=='synced' and r['linked']['settlement'] for r in state['review'])
    assert len({r['group']['id'] for r in state['review']})==1
    assert state['day_review'][0]['resolved'] and not state['recipient_profiles'] and not state['outbox']
    assert client.post('/api/reconciliation-groups',json=body).status_code==409
    assert restarted.delete('/api/review/'+body['bank_ids'][1]+'/link').status_code==200
    state=restarted.get('/api/state').json()
    assert all(r['state']=='pending' and not r['group'] and not r['split_ids'] for r in state['review'])
    with transaction(path) as db:
        assert read(db)==[]
        assert db.execute('SELECT count(*) FROM reconciliation_zen_members').fetchone()[0]==0
    assert client.post('/api/outbox/send',json={}).status_code==403


@pytest.mark.parametrize('change',['stale','duplicate','unequal','missing','day','account'])
def test_settlement_rejects_invalid_selection_without_partial_writes(tmp_path,change):
    client,path=setup_settlement(tmp_path);body=decision(client)
    if change=='stale':body['snapshot']='0'*64
    elif change=='duplicate':body['bank_ids'].append(body['bank_ids'][0])
    elif change=='unequal':body['zen_ids']=['a']
    elif change=='missing':body['bank_ids'][0]='missing'
    else:
        with transaction(path) as db:
            if change=='day':db.execute("UPDATE bank_transactions SET date='2026-10-02' WHERE id=?",(body['bank_ids'][0],))
            else:
                db.execute("INSERT INTO bank_accounts VALUES ('other','demo','other','VND',?)",(DEMO_ACCOUNT,))
                db.execute("UPDATE bank_transactions SET bank_account='other' WHERE id=?",(body['bank_ids'][0],))
    assert client.post('/api/reconciliation-groups',json=body).status_code==409
    with transaction(path) as db:assert read(db)==[]


def test_changed_settlement_member_invalidates_whole_group_and_can_be_unlinked(tmp_path):
    client,path=setup_settlement(tmp_path);body=decision(client)
    assert client.post('/api/reconciliation-groups',json=body).status_code==200
    with transaction(path) as db:apply_diff(db,{'serverTimestamp':3,'transaction':[{'id':'net','deleted':True}]})
    state=client.get('/api/state').json()
    assert all(r['reconciliation_status']=='conflict' for r in state['review'])
    assert not state['day_review'][0]['resolved']
    assert client.delete('/api/review/'+body['bank_ids'][0]+'/link').status_code==200


def legacy_database(tmp_path,duplicate=False):
    client,path=setup_split(tmp_path)
    with transaction(path) as db:
        account=db.execute('SELECT id FROM bank_accounts').fetchone()[0]
        db.execute('DROP VIEW split_links')
        db.execute('CREATE TABLE split_links(bank_id TEXT,zen_id TEXT UNIQUE)')
        for table in ['reconciliation_bank_members','reconciliation_zen_members','reconciliation_groups']:db.execute('DROP TABLE '+table)
        db.execute("UPDATE bank_transactions SET state='linked',linked_id='single-z'")
        for identifier,state,zen in [('split-b','linked',None),('sent-b','sent','single-z' if duplicate else 'sent-z')]:
            db.execute('INSERT INTO bank_transactions VALUES (?,?,?,?,?,?,?,?,?)',(identifier,account,identifier,'2026-10-01',-100,'Shop','{}',state,zen))
        db.executemany('INSERT INTO split_links VALUES (?,?)',[('split-b','a'),('split-b','b')])
        db.execute('PRAGMA user_version=3')
    return path


def test_migration_preserves_single_split_sent_ids_and_is_idempotent(tmp_path):
    path=legacy_database(tmp_path)
    initialize(path,'live')
    with transaction(path) as db:
        first=read(db)
        assert len(first)==3
        assert sorted(g['kind'] for g in first)==['single','single','split']
        assert sorted(z for g in first for z in g['zen_ids'])==['a','b','sent-z','single-z']
        assert db.execute("SELECT state FROM bank_transactions WHERE id='sent-b'").fetchone()[0]=='sent'
        assert db.execute('SELECT count(*) FROM split_links').fetchone()[0]==2
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    initialize(path,'live')
    with transaction(path) as db:assert read(db)==first


def test_invalid_legacy_ownership_rolls_back_migration(tmp_path):
    path=legacy_database(tmp_path,duplicate=True)
    with pytest.raises(Exception):initialize(path,'live')
    with transaction(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==3
        assert db.execute('SELECT count(*) FROM split_links').fetchone()[0]==2
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='reconciliation_groups'").fetchone()
