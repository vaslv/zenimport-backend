"""Offline exchange with the actual native MV3 participant, never a real account.

Peer statuses are private runner fixtures in lieu of authenticated HTTP. This
checks protocol/SQLite/IDB interoperability, not live SDK or transport identity.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from uuid import uuid4

from backend import migration_participant as participant,portable_codec as codec,portable_graph as graph,portable_install,storage

sys.path.insert(0,str(Path('tests').resolve()))
from delivery_factory import DeliveryHarness
from test_migration_participant import Endpoint


def save(root,name,value):(root/(name+'.json')).write_text(json.dumps(value))
def load(root,name):return json.loads((root/(name+'.json')).read_text())
def tagged(rows):return {name:[[codec.scalar(cell) for cell in row] for row in values] for name,values in rows.items()}


def authenticated_fixture(root,name,receipt):
    status=load(root,name)
    assert status['installation_id']==receipt['installation_id'] and status['operation']['state']==receipt['state']
    assert receipt in status['receipts']


def command(endpoint,name,switch,**fields):return endpoint.command(name,switch,**fields)


def run(phase,root):
    if phase=='prepare':
        source=DeliveryHarness(root/'source.sqlite3')
        try:
            job,*_=source.capture(count=2);source.due()
            assert source.client.post('/api/v1/jobs/'+job['id']+'/stop',json={}).status_code==200
            with storage.transaction(source.path) as db:
                db.execute("DELETE FROM service_meta WHERE key='fixture-network-write-lock-free'")
        finally:source.close()
        a,b=Endpoint(root/'source.sqlite3'),Endpoint(root/'target.sqlite3')
        save(root,'python',dict(source=a.status()['installation_id'],target=b.status()['installation_id']));return
    native=load(root,'native');a,b=Endpoint(root/'source.sqlite3'),Endpoint(root/'target.sqlite3')
    if phase=='exchange':
        switch=native['incoming'];command(a,'source_start',switch,peer_id=native['target'])
        with storage.transaction(a.path,read_only=True) as db:
            lineage=db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
            values,_=graph.source_tables(db,lineage);rows={name:list(items) for name,items in values.items()}
        manifest=codec.create_archive(root/'python.zip',rows,dataset_id=lineage,owners=['1'],session_id=switch,
            archive_id=str(uuid4()),created_at='2026-10-21T00:00:00Z',version='offline-authority-interop')
        stage=codec.read_archive(root/'python.zip',root/'source-stage');graph.validate_stage(stage,expected_owners=['1'])
        raw=(root/'python.zip').read_bytes()
        for number,start in enumerate(range(0,len(raw),262144)):a.chunk(switch,raw[start:start+262144],number)
        seal=command(a,'source_seal',switch,dataset_digest=manifest['dataset_digest'],archive_sha256=sha256(raw).hexdigest(),archive_size=str(len(raw)))
        save(root,'python-seal',seal);save(root,'python-source-status',a.status(switch));save(root,'python-incoming-rows',tagged(rows))
        switch=native['outgoing'];seal=load(root,'native-seal');authenticated_fixture(root,'native-outgoing-status',seal)
        command(b,'destination_start',switch,peer_receipt=seal)
        raw=(root/'native.zip').read_bytes();assert sha256(raw).hexdigest()==seal['archive_sha256']
        for number,start in enumerate(range(0,len(raw),262144)):b.chunk(switch,raw[start:start+262144],number)
        stage=codec.read_archive(root/'native.zip',root/'target-stage');graph.validate_stage(stage,expected_owners=['1'])
        assert stage.manifest['export_session_id']==switch and stage.manifest['dataset_digest']==seal['dataset_digest']
        with storage.transaction(b.path) as db:participant.set_validated(db,switch,dataset_digest=stage.manifest['dataset_digest'])
        save(root,'python-arm',command(b,'destination_arm',switch));save(root,'python-target-status',b.status(switch));return
    if phase=='handoff':
        switch=native['incoming'];armed=load(root,'native-arm');authenticated_fixture(root,'native-incoming-status',armed)
        save(root,'python-handoff',command(a,'source_handoff',switch,peer_receipt=armed));save(root,'python-source-status',a.status(switch))
        switch=native['outgoing'];handed=load(root,'native-handoff');authenticated_fixture(root,'native-outgoing-status',handed)
        stage=codec.read_archive(root/'native.zip',root/'activation-stage');graph.validate_stage(stage,expected_owners=['1'])
        request=b.request('destination_activate',switch,peer_receipt=handed)
        receipt=b.command('destination_activate',request=request,install=lambda db,op:portable_install.install(db,op,stage,source_hash='f'*64))
        assert b.command('destination_activate',request=request)==receipt
        assert a.status()['state']=='handed_off' and b.status()['awaiting_sync']
        with storage.transaction(a.path,read_only=True) as db:assert participant.blocked(db,worker=True)
        with storage.transaction(b.path,read_only=True) as db:
            assert participant.blocked(db,worker=True)
            values,_=graph.source_tables(db,stage.manifest['dataset_id']);after={name:list(items) for name,items in values.items()}
        before={name:list(stage.rows(name)) for name in codec.TABLES};expected=deepcopy(before)
        names=codec.TABLES['zen_mirror_state']['columns'];state=expected['zen_mirror_state'][0]
        for key,value in dict(source_hash='f'*64,cursor=0,generation=state[names.index('generation')]+1,needs_full=1,ready=0,refreshed_at=None,error_code=None).items():state[names.index(key)]=value
        expected['category_predictions']=[]
        attempted={row[codec.TABLES['deliveries']['columns'].index('bank_id')] for row in before['deliveries'] if row[codec.TABLES['deliveries']['columns'].index('ever_attempted')]==1}
        for row in expected['delivery_authorizations']:
            if row[codec.TABLES['delivery_authorizations']['columns'].index('bank_id')] not in attempted:row[codec.TABLES['delivery_authorizations']['columns'].index('source_hash')]='f'*64
        metadata=dict(expected['dataset_metadata']);metadata.pop('zenmoney_refreshed_at',None)
        history=json.loads(metadata.get('portable_imports','[]'))
        history.append(dict(archive_id=stage.manifest['archive_id'],archive_sha256=sha256((root/'native.zip').read_bytes()).hexdigest(),
            dataset_digest=stage.manifest['dataset_digest'],export_session_id=switch,accepted_at='1792454400'))
        assert json.loads(dict(after['dataset_metadata'])['portable_imports'])==history
        metadata['portable_imports']=dict(after['dataset_metadata'])['portable_imports']
        expected['dataset_metadata']=[[key,value] for key,value in sorted(metadata.items(),key=lambda item:item[0].encode())]
        assert after==expected,'Undeclared Python destination normalization'
        save(root,'python-installed-rows',tagged(after));return
    assert phase=='verify'
    assert load(root,'native-incoming-rows')==load(root,'python-incoming-rows'),'Native activation changed portable facts'
    stage=codec.read_archive(root/'native.zip',root/'verify-native')
    assert tagged({name:list(stage.rows(name)) for name in codec.TABLES})==load(root,'native-outgoing-rows')
    assert a.status()['state']=='handed_off' and b.status()['awaiting_sync']


if __name__=='__main__':
    phase,root=sys.argv[1],Path(sys.argv[2]);run(phase,root)
    print('PASS: '+phase+'; real native/SQLite participants, fixture identity ports, no external network')
