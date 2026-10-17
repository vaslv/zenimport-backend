"""Offline guards for the explicitly invoked live-account acceptance runner."""
from copy import deepcopy
import fcntl
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest


spec = importlib.util.spec_from_file_location('migration_lab', Path(__file__).resolve().parents[1]/'scripts/migration_lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


def fixture():
    token = 'only-a-test-token'
    identity = dict(run=str(uuid4()), expected_user=123, token_sha256=lab.fingerprint(token),
        forbidden_token_sha256=lab.fingerprint('production-token'))
    data = {'user':[{'id':123}], 'instrument':[{'id':1,'shortTitle':'USD'},{'id':2,'shortTitle':'VND'}],
        'account':[{'id':'existing','balance':0,'title':'Never change'}], 'tag':[], 'transaction':[], 'merchant':[]}
    return token, identity, data


def test_valid_identity_requires_no_financial_writes():
    token, identity, data = fixture()
    lab.validate_identity(data, identity, token, identity['run'])


@pytest.mark.parametrize('problem', ['token', 'run'])
def test_wrong_local_source_is_rejected_before_any_remote_read(tmp_path, monkeypatch, problem):
    import zenmoney_api
    token, identity, _ = fixture()
    (tmp_path/'identity.json').write_text(json.dumps(identity))
    (tmp_path/'test.env').write_text('ZENMONEY_TEST_TOKEN='+('wrong-token' if problem=='token' else token))
    monkeypatch.setenv('LAB_IDENTITY_FILE',str(tmp_path/'identity.json'))
    monkeypatch.setenv('LAB_TOKEN_FILE',str(tmp_path/'test.env'))
    monkeypatch.setenv('LAB_RUN_ID',str(uuid4()) if problem=='run' else identity['run'])
    def unexpected_client(*args, **kwargs):
        pytest.fail('No network client may be opened before the local source guard')
    monkeypatch.setattr(zenmoney_api,'Client',unexpected_client)
    with pytest.raises(RuntimeError): lab.remote_action('inspect')


@pytest.mark.parametrize('problem', ['user','family','token','production','run','partial','missing_pin'])
def test_rejects_unverified_accounts_and_sources(problem):
    token, identity, data = fixture()
    run = identity['run']
    if problem == 'user': data['user'][0]['id'] = 321
    if problem == 'family': data['user'].append({'id':456})
    if problem == 'token': token = 'another-test-token'
    if problem == 'production': identity['forbidden_token_sha256'] = lab.fingerprint(token)
    if problem == 'run': run = str(uuid4())
    if problem == 'partial': del data['transaction']
    if problem == 'missing_pin': del identity['expected_user']
    with pytest.raises(RuntimeError): lab.validate_identity(data, identity, token, run)


def test_catalog_is_limited_to_new_isolated_entities():
    _, identity, data = fixture()
    plan = lab.catalog_plan(data, identity)
    assert set(plan) == {'account','tag','merchant'}
    assert len(plan['account']) == 3
    assert [row['instrument'] for row in plan['account']] == [2,2,1]
    assert len({row['id'] for rows in plan.values() for row in rows}) == 6
    assert all(row['user'] == identity['expected_user'] for rows in plan.values() for row in rows)
    assert all(row['startBalance'] == row['balance'] == 0 for row in plan['account'])
    assert all(identity['run'][:8] in row['title'] for rows in plan.values() for row in rows)


class API:
    def __init__(self, data, fail=False, apply=True):
        self.data=deepcopy(data); self.writes=0; self.fail=fail; self.apply=apply
    def read_diff(self): return deepcopy(self.data)
    def diff(self, cursor, changes):
        self.writes += 1
        if self.apply:
            for kind, rows in changes.items(): self.data.setdefault(kind, []).extend(deepcopy(rows))
        if self.fail: raise RuntimeError('Secret response must not escape')


def test_catalog_intent_precedes_write_and_resume_does_not_resend(tmp_path):
    token, identity, data = fixture()
    api=API(data)
    original=api.diff
    def diff(cursor, changes):
        journal=json.loads((tmp_path/'catalog.json').read_text())
        assert journal['state'] == 'intent' and journal['plan'] == changes
        original(cursor, changes)
    api.diff=diff
    result=lab.prepare_catalog(api, tmp_path, identity, token)
    assert result['state'] == 'verified' and api.writes == 1
    assert lab.unchanged_baseline(api.data, data)
    lab.prepare_catalog(api, tmp_path, identity, token)
    assert api.writes == 1
    assert (tmp_path/'catalog.json').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('applied', [True, False])
def test_lost_catalog_response_never_causes_automatic_repeat(tmp_path, applied):
    token, identity, data = fixture()
    api=API(data, fail=True, apply=applied)
    with pytest.raises(RuntimeError, match='uncertain'):
        lab.prepare_catalog(api, tmp_path, identity, token)
    assert json.loads((tmp_path/'catalog.json').read_text())['state'] == 'uncertain'
    if applied: assert lab.prepare_catalog(api, tmp_path, identity, token)['state'] == 'verified'
    else:
        with pytest.raises(RuntimeError, match='unconfirmed'):
            lab.prepare_catalog(api, tmp_path, identity, token)
    assert api.writes == 1


def test_other_user_is_rejected_before_write(tmp_path):
    token, identity, data = fixture(); data['user'][0]['id']=321
    api=API(data)
    with pytest.raises(RuntimeError): lab.prepare_catalog(api, tmp_path, identity, token)
    assert api.writes == 0 and not (tmp_path/'catalog.json').exists()


def test_parallel_catalog_process_cannot_create_another_plan(tmp_path):
    token, identity, data = fixture(); api=API(data)
    with (tmp_path/'.catalog.lock').open('a') as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='Another'):
            lab.prepare_catalog(api, tmp_path, identity, token)
    assert api.writes == 0 and not (tmp_path/'catalog.json').exists()


def test_modified_existing_entity_is_not_hidden_by_successful_catalog(tmp_path):
    token, identity, data = fixture(); api=API(data)
    lab.prepare_catalog(api, tmp_path, identity, token)
    api.data['account'][0]['title']='Unexpected edit'
    with pytest.raises(RuntimeError): lab.prepare_catalog(api, tmp_path, identity, token)
    assert api.writes == 1


@pytest.mark.parametrize('problem', ['none','same_token','unverified','ports'])
def test_init_creates_private_separate_state_without_copying_tokens(tmp_path, problem):
    test=tmp_path/'test.env'; test.write_text('ZENMONEY_TEST_TOKEN=test-token\n')
    main=tmp_path/'main.env'; main.write_text('ZENMONEY_TOKEN='+('test-token' if problem=='same_token' else 'production-token')+'\n')
    old=tmp_path/'identity.json'; old.write_text(json.dumps({'user':123, 'cleanup':{'state':'unknown' if problem=='unverified' else 'verified'}}))
    ports=[18792,18893] if problem=='ports' else [18892,18893]
    if problem != 'none':
        with pytest.raises(RuntimeError): lab.initialize(tmp_path/'state',test,old,main,ports)
        return
    state=lab.initialize(tmp_path/'state',test,old,main,ports)
    env=lab.read_env(state/'lab.env')
    assert (state/'server-a').is_dir() and (state/'server-b').is_dir()
    assert env['LAB_API_TOKEN_A'] != env['LAB_API_TOKEN_B']
    for f in ('lab.env','identity.json'):
        assert (state/f).stat().st_mode & 0o777 == 0o600
        text=(state/f).read_text()
        assert 'test-token' not in text and 'production-token' not in text
