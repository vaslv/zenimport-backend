"""Explicit offline native-browser interop. All databases and owners are fixtures."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from uuid import uuid4

from backend import portable_codec as codec,portable_graph as graph,portable_install,storage
from backend import migration_participant as participant

sys.path.insert(0,str(Path('tests').resolve()))
from test_migration_participant import Endpoint
from delivery_factory import DeliveryHarness


def archive(path,rows,fixture):
    return codec.create_archive(path,rows,dataset_id=fixture['dataset_id'],owners=fixture['owner_ids'],
        session_id=str(uuid4()),archive_id=str(uuid4()),created_at='2026-10-21T00:00:00Z',version='offline-native-interop')


def decoded(fixture):
    return {name:[[codec.unscalar(cell) for cell in row] for row in values] for name,values in fixture['rows'].items()}


def convert_format1(root):
    """Supported old-file path: compatible temporary backend, never browser SQL."""
    source,target=DeliveryHarness(root/'format1-source.sqlite3'),DeliveryHarness(root/'compatible.sqlite3')
    try:
        job,*_=source.capture(count=2);source.due()
        assert source.client.post('/api/v1/jobs/'+job['id']+'/stop',json={}).status_code==200
        with storage.transaction(source.path) as db:db.execute("DELETE FROM service_meta WHERE key='fixture-network-write-lock-free'")
        payload=source.app.state.migration.export();(root/'format1.zip').write_bytes(payload)
        result=target.app.state.migration.restore(payload)
        assert result['restored'] and not target.remote.writes and not target.remote.delete_writes
        with storage.transaction(target.path,read_only=True) as db:
            lineage=db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
            rows,_=graph.source_tables(db,lineage);rows={name:list(values) for name,values in rows.items()}
            assert len(rows['bank_transactions'])==2 and len(rows['processing_targets'])==2
        fixture=dict(dataset_id=lineage,owner_ids=['1'],rows={name:[[codec.scalar(cell) for cell in row] for row in values] for name,values in rows.items()})
        fixture['expected_digest']=archive(root/'converted-format2.zip',rows,fixture)['dataset_digest']
        return fixture
    finally:source.close();target.close()


def read(path,root,owners):
    data=codec.read_archive(path,root);graph.validate_stage(data,expected_owners=owners)
    return data,{name:list(data.rows(name)) for name in codec.TABLES}


def install(root,stage):
    # This test peer authenticates synthetic owners through the independent graph
    # fixture. It is not evidence of a fresh real SDK/token-owner check.
    origin,target=Endpoint(root/'source.sqlite3'),Endpoint(root/'target.sqlite3');switch=str(uuid4())
    with storage.transaction(origin.path) as db:
        db.execute("UPDATE service_meta SET value=? WHERE key='instance_id'",(stage.manifest['dataset_id'],))
    origin.command('source_start',switch,peer_id=target.status()['installation_id'])
    raw=(root/'browser.zip').read_bytes()
    for number,start in enumerate(range(0,len(raw),262144)):origin.chunk(switch,raw[start:start+262144],number)
    seal=origin.command('source_seal',switch,dataset_digest=stage.manifest['dataset_digest'],archive_sha256=sha256(raw).hexdigest(),archive_size=str(len(raw)))
    target.command('destination_start',switch,peer_receipt=seal)
    for number,start in enumerate(range(0,len(raw),262144)):target.chunk(switch,raw[start:start+262144],number)
    with storage.transaction(target.path) as db:participant.set_validated(db,switch,dataset_digest=stage.manifest['dataset_digest'])
    armed=target.command('destination_arm',switch);handoff=origin.command('source_handoff',switch,peer_receipt=armed)
    result=target.command('destination_activate',switch,peer_receipt=handoff,
        install=lambda db,operation:portable_install.install(db,operation,stage,source_hash='f'*64))
    assert result['state']=='activated' and target.status()['awaiting_sync'] and origin.status()['state']=='handed_off'
    with storage.transaction(target.path,read_only=True) as db:
        rows,_=graph.source_tables(db,stage.manifest['dataset_id']);return {name:list(values) for name,values in rows.items()}


def normalizations(before,after,stage):
    """Check declared destination-only changes independently of the installer."""
    expected=deepcopy(before)
    names=codec.TABLES['zen_mirror_state']['columns'];state=expected['zen_mirror_state'][0]
    for key,value in dict(source_hash='f'*64,cursor=0,generation=state[names.index('generation')]+1,
        needs_full=1,ready=0,refreshed_at=None,error_code=None).items():state[names.index(key)]=value
    expected['category_predictions']=[]
    attempted={row[codec.TABLES['deliveries']['columns'].index('bank_id')] for row in before['deliveries']
        if row[codec.TABLES['deliveries']['columns'].index('ever_attempted')]==1}
    for row in expected['delivery_authorizations']:
        names=codec.TABLES['delivery_authorizations']['columns']
        if row[names.index('bank_id')] not in attempted:row[names.index('source_hash')]='f'*64
    metadata=dict(expected['dataset_metadata']);metadata.pop('zenmoney_refreshed_at',None)
    # The physical audit alias is projected back to the original logical key by
    # source_tables. It must neither become executable nor change archive facts.
    before_provenance=json.loads(metadata.get('portable_imports','[]'));provenance=json.loads(dict(after['dataset_metadata'])['portable_imports'])
    assert provenance[:-1]==before_provenance
    assert provenance[-1]==dict(archive_id=stage.manifest['archive_id'],archive_sha256=sha256((stage.root.parent/'browser.zip').read_bytes()).hexdigest(),
        dataset_digest=stage.manifest['dataset_digest'],export_session_id=stage.manifest['export_session_id'],accepted_at='1792454400')
    metadata['portable_imports']=dict(after['dataset_metadata'])['portable_imports']
    expected['dataset_metadata']=[[key,value] for key,value in sorted(metadata.items(),key=lambda item:item[0].encode())]
    differences={name:dict(expected=len(expected[name]),actual=len(after[name]),
        columns=sorted({column for left,right in zip(expected[name],after[name]) for column,a,b in zip(codec.TABLES[name]['columns'],left,right) if a!=b}))
        for name in expected if expected[name]!=after[name]}
    if 'dataset_metadata' in differences:
        left,right=dict(expected['dataset_metadata']),dict(after['dataset_metadata'])
        differences['dataset_metadata']['keys']=[key for key in sorted(left.keys()|right.keys()) if left.get(key)!=right.get(key)]
    assert not differences,('undeclared financial normalization',differences)


mode,work=sys.argv[1],Path(sys.argv[2])
for name,source in [('current','portable-financial-v2'),('legacy','portable-legacy-v2'),('format1',None)]:
    root=work/name;root.mkdir(parents=True,exist_ok=True)
    if source is not None:fixture=json.loads(Path('fixtures/business/v1',source+'.json').read_text())
    elif mode=='prepare':
        fixture=convert_format1(root);(root/'fixture.json').write_text(json.dumps(fixture))
    else:fixture=json.loads((root/'fixture.json').read_text())
    expected=decoded(fixture)
    if mode=='prepare':
        manifest=archive(root/'python.zip',expected,fixture)
        assert manifest['dataset_digest']==fixture['expected_digest']
        read(root/'python.zip',root/'prepared',fixture['owner_ids'])
        (root/'expected.json').write_text(json.dumps(dict(digest=manifest['dataset_digest'])))
    elif mode=='install':
        stage,rows=read(root/'browser.zip',root/'browser-stage',fixture['owner_ids'])
        assert rows==expected and stage.manifest['dataset_digest']==fixture['expected_digest']
        after=install(root,stage);normalizations(expected,after,stage)
        manifest=archive(root/'server-installed.zip',after,fixture)
        read(root/'server-installed.zip',root/'installed-stage',fixture['owner_ids'])
        (root/'server-installed.json').write_text(json.dumps(dict(digest=manifest['dataset_digest'])))
    elif mode=='verify':
        _,expected=read(root/'server-installed.zip',root/'final-python',fixture['owner_ids'])
        _,actual=read(root/'browser-installed.zip',root/'final-browser',fixture['owner_ids'])
        assert actual==expected
    else:raise ValueError('unsupported offline interop phase')
print('PASS: '+mode+'; complete current/legacy/converted-format1 graphs, no real API calls or production data')
