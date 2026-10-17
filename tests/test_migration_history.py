"""Controlled-history contracts and transport accounting; no live API."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import sys
from uuid import uuid4

import httpx
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import migration_history as history
import migration_lab as lab
from zenmoney_api.protocol import encode_json, decode_json


def fixture():
    token='test-only-token'
    identity=dict(run=str(uuid4()),expected_user=123,token_sha256=lab.fingerprint(token),
        forbidden_token_sha256=lab.fingerprint('not-the-test-token'))
    data={'user':[{'id':123}], 'instrument':[{'id':1,'shortTitle':'USD'},{'id':2,'shortTitle':'VND'}],
        'account':[{'id':'original-cash','type':'cash','balance':0},
            {'id':'system-debt','type':'debt','balance':0}], 'tag':[], 'transaction':[], 'merchant':[]}
    catalog={'plan':lab.catalog_plan(data,identity)}
    for kind,rows in catalog['plan'].items():data[kind].extend(deepcopy(rows))
    return token,identity,data,catalog


def test_independent_history_counts_types_and_bank_movements():
    _,identity,data,catalog=fixture()
    plan=history.history_plan(data,catalog,identity)
    assert set(plan['variants'])=={'migrated','empty'}
    all_ids=[]
    for variant,item in plan['variants'].items():
        assert len(item['seed'])==18
        initial=[x for x in item['records'] if x['phase']=='initial']
        assert len(initial)==21 and len(item['records'])==23
        assert sum(c['creates'] for c in item['cases'] if c['phase']=='initial')==2
        cases={c['label']:c for c in item['cases']}
        assert len(cases['transfer']['bank_keys'])==len(cases['fx']['bank_keys'])==2
        assert len(cases['split']['seed_ids'])==len(cases['settlement']['seed_ids'])==2
        assert len(cases['merge']['bank_keys'])==2 and len(cases['merge']['seed_ids'])==1
        missing={k for c in item['cases'] if c['creates'] for k in c['bank_keys']}
        seeded_bank=[x for x in initial if x['key'] not in missing]
        targets={a['id'] for a in item['accounts']}
        assert history.net_ledger(history.ledger(item['seed'],targets))==history.bank_ledger(seeded_bank)
        assert len({x['record']['external_id'] for x in item['records']})==23
        all_ids.extend(row['id'] for row in item['seed'])
        assert all(row['user']==identity['expected_user'] for row in item['seed'])
        assert all(not row['record']['external_id'].startswith('vib:id:') for row in item['records'])
    assert len(all_ids)==len(set(all_ids))==36
    assert not ({a['id'] for a in plan['variants']['migrated']['accounts']} &
        {a['id'] for a in plan['variants']['empty']['accounts']})


@pytest.mark.parametrize('failure',[False,True])
def test_audit_counts_actual_attempts_without_secrets(tmp_path,failure):
    def forward(request):
        if failure:raise httpx.ReadTimeout('response lost')
        return httpx.Response(200,content=encode_json({'serverTimestamp':1}))
    path=tmp_path/'audit.jsonl'
    wrapped=history.AuditedTransport(path,httpx.MockTransport(forward))
    read=httpx.Request('POST','https://api.zenmoney.ru/v8/diff/',content=encode_json({'serverTimestamp':0}))
    if failure:
        with pytest.raises(httpx.ReadTimeout):wrapped.handle_request(read)
    else:wrapped.handle_request(read)
    assert not path.exists()
    request=httpx.Request('POST','https://api.zenmoney.ru/v8/diff/',headers={'Authorization':'Bearer secret-token'},
        content=encode_json({'serverTimestamp':0,'transaction':[{'id':'owned-id','comment':'private-payee'}]}))
    if failure:
        with pytest.raises(httpx.ReadTimeout):wrapped.handle_request(request)
    else:wrapped.handle_request(request)
    records=[json.loads(line) for line in path.read_text().splitlines()]
    assert len(records)==1 and records[0]['entities']=={'transaction':['owned-id']}
    assert 'secret-token' not in path.read_text() and 'private-payee' not in path.read_text()
    assert path.stat().st_mode & 0o777==0o600
    wrapped.close()


class API:
    def __init__(self,data,*,fail=False,apply=True):
        self.data=deepcopy(data);self.writes=0;self.fail=fail;self.apply=apply
    def read_diff(self):return deepcopy(self.data)
    def diff(self,cursor,changes):
        self.writes+=1
        if self.apply:
            for kind,rows in changes.items():
                current={r['id']:r for r in self.data.setdefault(kind,[])}
                for row in rows:current[row['id']]=deepcopy(row)
                self.data[kind]=list(current.values())
            for account in self.data['account']:
                if account['id'].startswith('original'):continue
                account['balance']=sum((r['income'] if r['incomeAccount']==account['id'] else 0)-
                    (r['outcome'] if r['outcomeAccount']==account['id'] else 0)
                    for r in self.data['transaction'] if not r.get('deleted'))
        if self.fail:raise RuntimeError('lost')


def test_live_history_resume_never_repeats_owned_catalog_or_seeds(tmp_path):
    token,identity,data,catalog=fixture();lab.save(tmp_path/'catalog.json',catalog)
    api=API(data)
    journal=history.prepare_history(api,tmp_path,identity,token)
    assert api.writes==3 and len(api.data['transaction'])==36
    assert all(stage['state']=='verified' for stage in journal['stages'].values())
    history.prepare_history(api,tmp_path,identity,token)
    assert api.writes==3
    assert history.originals_unchanged(api.data,journal)


@pytest.mark.parametrize('applied',[True,False])
def test_unknown_write_can_only_be_verified_not_resent(tmp_path,applied):
    token,identity,data,catalog=fixture();lab.save(tmp_path/'catalog.json',catalog)
    api=API(data,fail=True,apply=applied)
    with pytest.raises(RuntimeError,match='unknown'):
        history.prepare_history(api,tmp_path,identity,token)
    if applied:
        # The first attempt was catalog only. Resume verifies it, then tries
        # the next distinct seed stage exactly once; fail again after applying.
        with pytest.raises(RuntimeError,match='unknown'):
            history.prepare_history(api,tmp_path,identity,token)
        assert api.writes==2
        saved=decode_json((tmp_path/'history.json').read_bytes())
        assert saved['stages']['history-catalog']['state']=='verified'
    else:
        with pytest.raises(RuntimeError,match='not confirmed'):
            history.prepare_history(api,tmp_path,identity,token)
        assert api.writes==1


def test_mobile_mutations_only_touch_owned_rows_and_are_not_repeated(tmp_path):
    token,identity,data,catalog=fixture();lab.save(tmp_path/'catalog.json',catalog)
    api=API(data);journal=history.prepare_history(api,tmp_path,identity,token)
    plan=journal['variants']['migrated']
    missing=next(x for x in plan['records'] if x['key']=='migrated:missing-expense:0')
    from zenmoney_api import transactions as tx
    row=tx.expense(user=identity['expected_user'],date=missing['record']['local_date'],
        account=missing['target'],instrument=2,amount=1709)
    api.diff(0,{'transaction':[row]})
    plan['created'][missing['key']]=row['id'];lab.save(tmp_path/'history.json',journal)
    history.mobile_changes(api,tmp_path,identity,token)
    writes=api.writes
    history.mobile_changes(api,tmp_path,identity,token)
    assert api.writes==writes
    journal=decode_json((tmp_path/'history.json').read_bytes())
    assert len(journal['mobile']['rows'])==2
    current={r['id']:r for r in api.data['transaction']}
    assert current[journal['mobile']['deleted']]['deleted']
    assert current[row['id']]['incomeAccount']==journal['debt_account']
    assert history.originals_unchanged(api.data,journal)


def test_baseline_detects_unrelated_edits_but_allows_declared_owned_balance(tmp_path):
    _,identity,data,catalog=fixture();journal=history.history_plan(data,catalog,identity)
    assert history.originals_unchanged(data,journal)
    changed=deepcopy(data);changed['account'][0]['balance']=1
    assert not history.originals_unchanged(changed,journal)
    changed=deepcopy(data);changed['account'][-1]['balance']=42
    assert history.originals_unchanged(changed,journal)


def test_ledger_does_not_hide_equal_net_duplicate_pairs():
    row={'id':'a','date':'2026-10-01','incomeAccount':'cash','outcomeAccount':'cash',
        'incomeInstrument':2,'outcomeInstrument':2,'income':0,'outcome':100}
    duplicate={'id':'b',**(row|{'income':100,'outcome':100})}
    gross=history.ledger([row],{'cash'})
    wrong=history.ledger([row,duplicate],{'cash'})
    assert history.net_ledger(gross)==history.net_ledger(wrong)
    assert gross!=wrong


def test_per_uuid_verification_preserves_explicit_category_and_unread_state():
    row={'id':'owned','user':1,'date':'2026-10-01','incomeAccount':'cash','outcomeAccount':'cash',
        'incomeInstrument':2,'outcomeInstrument':2,'income':0,'outcome':100,
        'deleted':False,'viewed':False,'tag':['selected']}
    assert history.verify_rows({'transaction':[row]},{'transaction':[row]})
    for change in ({'viewed':True},{'tag':['wrong']},{'outcome':101}):
        assert not history.verify_rows({'transaction':[row|change]},{'transaction':[row]})
