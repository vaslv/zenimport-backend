"""HTTP boundary and end-to-end transfer using only synthetic SDK data."""
from base64 import b64encode
from hashlib import sha256
from uuid import uuid4

import pytest
import json
from pathlib import Path

from backend import migration_participant as participant, storage
from delivery_factory import DeliveryHarness
from test_portable_graph import source

BASE='/api/v1/migration/portable'


def test_public_migration_schema_matches_shared_contract():
    from contracts.portable_http import specification
    assert specification()==json.loads(Path('fixtures/business/v1/portable-http-v2.json').read_text())


def command(h,name,switch,**fields):
    cap=h.client.get(BASE+'/capabilities').json()
    body=dict(request_id=str(uuid4()),switch_id=switch,expected_epoch=cap['participant_epoch'],**fields)
    response=h.client.post(BASE+'/commands/'+name,json=body)
    assert response.status_code==202,response.text
    return response.json(),body


def test_http_transfer_keeps_null_receipt_fields_and_authenticates_every_endpoint(source,tmp_path):
    target=DeliveryHarness(tmp_path/'target.sqlite3');switch=str(uuid4())
    try:
        cap=target.client.get(BASE+'/capabilities')
        assert cap.status_code==200 and cap.json()['protocol_version']=='2' and cap.json()['can_import']
        assert cap.headers['cache-control']=='no-store'
        for path in ('/capabilities','/switches/'+switch,'/switches/'+switch+'/chunks/0'):
            denied=target.client.get(BASE+path,headers={'Authorization':'Bearer wrong-synthetic-token'})
            assert denied.status_code==401
        started,_=command(source,'source_start',switch,peer_id=cap.json()['installation_id'])
        assert started['receipt']['dataset_digest'] is None
        assert 'peer_epoch' in started['receipt'] and 'archive_size' in started['receipt']
        assert source.app.state.portable.run_once()
        source_status=source.client.get(BASE+'/switches/'+switch).json()
        seal=next(row for row in source_status['receipts'] if row['command']=='source_seal')
        started,_=command(target,'destination_start',switch,peer_receipt=seal)
        assert started['receipt']['state']=='import_hold'
        for number in range((int(seal['archive_size'])+262143)//262144):
            downloaded=source.client.get(BASE+'/switches/'+switch+'/chunks/'+str(number))
            assert downloaded.status_code==200 and downloaded.headers['content-type']=='application/octet-stream'
            data=downloaded.content;checksum=sha256(data).hexdigest()
            assert downloaded.headers['x-chunk-sha256']==checksum
            uploaded=target.client.put(BASE+'/switches/'+switch+'/chunks/'+str(number),json=dict(
                expected_epoch=started['receipt']['participant_epoch'],data=b64encode(data).decode(),sha256=checksum))
            assert uploaded.status_code==200,uploaded.text
            assert uploaded.json()['bytes']==str(len(data))
        finalize=target.client.post(BASE+'/finalize',json=dict(request_id=str(uuid4()),switch_id=switch,expected_epoch=started['receipt']['participant_epoch']))
        assert finalize.status_code==202
        assert target.app.state.portable.run_once()
        ready,_=command(target,'destination_arm',switch)
        moved,_=command(source,'source_handoff',switch,peer_receipt=ready['receipt'])
        accepted,original=command(target,'destination_activate',switch,peer_receipt=moved['receipt'])
        assert accepted['work']['state']=='queued'
        assert target.app.state.portable.run_once()
        replay=target.client.post(BASE+'/commands/destination_activate',json=original)
        assert replay.status_code==202 and replay.json()['receipt']['state']=='activated'
        final=target.client.get(BASE+'/switches/'+switch).json()
        assert final['state']=='active' and final['awaiting_sync']
        assert isinstance(final['operation']['validated'],bool)
        with storage.transaction(target.path,read_only=True) as db:
            participant.verify_receipt(moved['receipt'],command='source_handoff',current=participant.installation(db))
        assert target.client.get('/api/v1/info').status_code==200
        target.worker.run_once()
        assert not target.remote.writes
    finally:target.close()


@pytest.mark.parametrize('body',[
    {},{'request_id':'not-uuid'}, {'request_id':str(uuid4()),'switch_id':str(uuid4()),'expected_epoch':1,'peer_id':str(uuid4())},
    {'request_id':str(uuid4()),'switch_id':str(uuid4()),'expected_epoch':'1','peer_id':str(uuid4()),'skip_validation':True},
])
def test_malformed_commands_do_not_create_holds_or_private_errors(source,body):
    response=source.client.post(BASE+'/commands/source_start',json=body)
    assert response.status_code==422,response.text
    assert response.json()['error']['code']=='migration_invalid'
    assert source.client.get(BASE+'/capabilities').json()['state']=='active'


@pytest.mark.parametrize('data',['%%%=', 'Zg', 'Zh==', 'eA==\n'])
def test_noncanonical_base64_is_rejected_before_any_chunk_write(source,data):
    response=source.client.put(BASE+'/switches/'+str(uuid4())+'/chunks/0',json=dict(expected_epoch='1',data=data,sha256='a'*64))
    assert response.status_code==422
    with storage.transaction(source.path,read_only=True) as db:assert not db.execute('SELECT 1 FROM migration_chunks').fetchone()


def test_unknown_status_never_invents_an_export_or_activation_receipt(source):
    response=source.client.get(BASE+'/switches/'+str(uuid4()))
    assert response.status_code==200
    assert response.json()['operation'] is None and response.json()['receipts']==[] and response.json()['work']==[]
    assert source.client.post(BASE+'/commands/source_seal',json={}).status_code==422


def test_fresh_identity_read_preserves_financial_state_and_hold(source,tmp_path):
    before_reads=len(source.remote.reads)
    with storage.transaction(source.path,read_only=True) as db:
        before=tuple(db.execute('SELECT cursor,generation,refreshed_at FROM zen_mirror_state').fetchone())
        attempts=db.execute('SELECT count(*) FROM delivery_attempts').fetchone()[0]
    response=source.client.post(BASE+'/identity',json={})
    assert response.status_code==200,response.text
    identity=response.json();assert identity['owner_ids']==['1'] and identity['checked_at'].isdigit()
    assert len(source.remote.reads)==before_reads+1 and response.headers['cache-control']=='no-store'
    with storage.transaction(source.path,read_only=True) as db:
        assert tuple(db.execute('SELECT cursor,generation,refreshed_at FROM zen_mirror_state').fetchone())==before
        assert db.execute('SELECT count(*) FROM delivery_attempts').fetchone()[0]==attempts
    assert source.client.post(BASE+'/identity',json={},headers={'Authorization':'Bearer invalid-fixture'}).status_code==401
    assert source.client.post(BASE+'/identity',json={'unexpected':True}).status_code==422
    target=DeliveryHarness(tmp_path/'identity-target.sqlite3')
    try:
        switch=str(uuid4());command(source,'source_start',switch,peer_id=target.client.get(BASE+'/capabilities').json()['installation_id'])
        held=source.client.post(BASE+'/identity',json={});assert held.status_code==200,held.text
        assert held.json()['state']=='export_hold'
        assert source.client.get(BASE+'/capabilities').json()['state']=='export_hold'
    finally:target.close()


def test_fresh_identity_failure_is_redacted_and_does_not_invent_an_owner(source,monkeypatch):
    from zenmoney_api import TransportError
    def unavailable(*args,**kwargs):raise TransportError('synthetic private response')
    monkeypatch.setattr(source.app.state.portable,'_read_identity',unavailable)
    response=source.client.post(BASE+'/identity',json={})
    assert response.status_code==503 and response.json()['error']['code']=='zenmoney_unavailable'
    assert 'synthetic private response' not in response.text
    assert not list(source.path.parent.glob('.portable-identity-*'))
