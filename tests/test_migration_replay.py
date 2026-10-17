"""Collector-side replay idempotency without Docker or outside network."""
import json
from pathlib import Path
import sys
import urllib.error

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from migration_replay import Replay


def replay(tmp_path):
    value=object.__new__(Replay)
    value.state=tmp_path;value.path=tmp_path/'replay.json'
    value.journal={'completed':{},'commands':{}}
    value.ensure_server=lambda _:None
    value.history=lambda :{'variants':{'migrated':{'import_start':'2026-10-01'}}}
    value.connection=lambda *args:{'id':'connection','accounts':[]}
    value.audit=lambda _:[]
    return value


def test_capture_resume_reuses_request_id_after_lost_job_ack(tmp_path):
    value=replay(tmp_path);requests=[]
    done={'id':'job','accounts':[],'capture_state':'complete','delivery_state':'complete','delivery':{'uncertain':0},'errors':[]}
    def request(instance,path,body=None,method=None):
        if path=='/jobs':
            requests.append(body['request_id'])
            if len(requests)==1:raise urllib.error.URLError('lost initial ACK')
        return done
    value.http=request
    with pytest.raises(urllib.error.URLError):value.capture('step','a','migrated')
    value.capture('step','a','migrated')
    value.capture('step','a','migrated')
    assert len(requests)==2 and requests[0]==requests[1]
    assert json.loads(value.path.read_text())['completed']['step']['new_transaction_ids']==0


def test_unexpected_created_ids_fail_even_if_job_claims_success(tmp_path):
    value=replay(tmp_path);events=[]
    value.audit=lambda _:events.copy()
    def request(instance,path,body=None,method=None):
        if path=='/jobs':events.append({'entities':{'transaction':['unexpected-copy']}})
        return {'id':'job','capture_state':'complete','delivery_state':'complete','accounts':[],'delivery':{'uncertain':0},'errors':[]}
    value.http=request
    with pytest.raises(RuntimeError,match='expected 0'):value.capture('step','a','migrated')
    assert not value.journal['completed']


def test_unknown_delivery_result_does_not_start_another_command(tmp_path):
    value=replay(tmp_path);requests=[]
    def request(instance,path,body=None,method=None):
        if path=='/jobs':requests.append(body['request_id'])
        return {'id':'job','capture_state':'complete','delivery_state':'blocked','accounts':[],
            'delivery':{'uncertain':1},'errors':[]}
    value.http=request
    with pytest.raises(RuntimeError,match='Unknown delivery'):value.capture('step','a','migrated')
    assert len(requests)==1 and not value.journal['completed']


def test_in_flight_uncertain_is_waited_for_without_repeating_command(tmp_path,monkeypatch):
    value=replay(tmp_path);polls=[]
    monkeypatch.setattr('migration_replay.time.sleep',lambda _:None)
    def request(instance,path,body=None,method=None):
        result={'id':'job','capture_state':'complete','delivery_state':'complete','accounts':[],
            'delivery':{'uncertain':0},'errors':[]}
        if path!='/jobs':
            polls.append(path)
            if len(polls)==1:
                result.update(delivery_state='running',delivery={'uncertain':1},errors=[{'code':'delivery_uncertain'}])
        return result
    value.http=request
    value.capture('step','a','migrated')
    assert len(polls)==2 and value.journal['completed']['step']['delivery_state']=='complete'
