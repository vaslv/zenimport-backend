"""Cross-runtime fixture comes from isolated real capture/delivery code paths."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import pytest
from backend import portable_codec as codec,portable_graph as graph


FIXTURE=json.loads(Path('fixtures/business/v1/portable-financial-v2.json').read_text())


def decoded():
    return {name:[[codec.unscalar(cell) for cell in row] for row in values] for name,values in FIXTURE['rows'].items()}


def stage(tmp_path,rows,fixture=FIXTURE):
    archive=tmp_path/(str(uuid4())+'.zip')
    codec.create_archive(archive,rows,dataset_id=fixture['dataset_id'],owners=fixture['owner_ids'],
        session_id=str(uuid4()),archive_id=str(uuid4()),created_at='2026-10-20T00:00:00Z',version='financial-fixture')
    return codec.read_archive(archive,tmp_path/str(uuid4()))


@pytest.mark.parametrize('name',['portable-financial-v2','portable-legacy-v2'])
def test_shared_financial_fixture_has_independent_complete_python_graph_proof(tmp_path,name):
    fixture=json.loads(Path('fixtures/business/v1',name+'.json').read_text())
    rows={name:[[codec.unscalar(cell) for cell in row] for row in values] for name,values in fixture['rows'].items()}
    data=stage(tmp_path,rows,fixture)
    assert data.manifest['dataset_digest']==fixture['expected_digest']
    result=graph.validate_stage(data,expected_owners=fixture['owner_ids'])
    assert dict(result.counts)=={name:len(rows) for name,rows in fixture['rows'].items()}


def test_shared_populated_foreign_keys_cannot_lose_their_parent(tmp_path):
    original=decoded();tested=0
    for name,table in codec.TABLES.items():
        definition=codec.CONTRACT['schema']['tables'][table['source']]
        groups={part['id']:[] for part in definition['foreign_keys']}
        for part in definition['foreign_keys']:groups[part['id']].append(part)
        for parts in groups.values():
            names=table['columns']
            index=next((i for i,row in enumerate(original[name]) if all(row[names.index(part['from'])] is not None for part in parts)),None)
            if index is None:continue
            rows=deepcopy(original);column=names.index(parts[0]['from']);value=rows[name][index][column]
            rows[name][index][column]=9223372036854775807 if type(value) is int else 'missing-parent'
            with pytest.raises(codec.PortableError):graph.validate_stage(stage(tmp_path,rows))
            tested+=1
    assert tested>=30


@pytest.mark.parametrize('problem',['missing_attempt','different_payload','missing_authorization','wrong_job',
    'authorization_job','attempt_id','delivery_id','attempting','terminal','missing_terminal'])
def test_shared_fixture_rejects_broken_create_once_history(tmp_path,problem):
    rows=decoded()
    def offset(table,column):return codec.TABLES[table]['columns'].index(column)
    if problem=='missing_attempt':rows['delivery_attempts']=[]
    if problem=='different_payload':
        for row in rows['delivery_attempts']:row[offset('delivery_attempts','payload')]+=' '
    if problem=='missing_authorization':rows['delivery_authorizations']=[]
    if problem=='wrong_job':
        for row in rows['deliveries']:row[offset('deliveries','authorized_job_id')]='wrong-job'
    if problem=='authorization_job':rows['delivery_authorizations'][0][offset('delivery_authorizations','job_id')]='wrong-job'
    if problem=='attempt_id':rows['delivery_attempts'][0][offset('delivery_attempts','payload')]='{"id":"another-uuid"}'
    if problem=='delivery_id':rows['deliveries'][0][offset('deliveries','payload')]='{"id":"another-uuid"}'
    if problem=='attempting':rows['deliveries'][0][offset('deliveries','state')]='attempting'
    if problem=='terminal':rows['bank_processing'][0][offset('bank_processing','state')]='pending'
    if problem=='missing_terminal':rows['processing_receipts']=[];rows['link_bank_members']=[]
    with pytest.raises(codec.PortableError):graph.validate_stage(stage(tmp_path,rows))


def test_shared_exact_mirror_amount_and_category_positions_match_original_data(tmp_path):
    rows=decoded()
    def offset(table,column):return codec.TABLES[table]['columns'].index(column)
    movement=rows['zen_movements'][0]
    entity=next(row for row in rows['zen_entities'] if row[:2]==movement[:2])
    data=json.loads(entity[offset('zen_entities','data')]);data['tag']=['category-a'];data['outcome']='replace-exact-decimal'
    entity[offset('zen_entities','data')]=json.dumps(data).replace('"replace-exact-decimal"','1.00000000000000000001')
    movement[offset('zen_movements','outcome')]='1.00000000000000000001'
    rows['zen_tags']=[[*movement[:2],'category-a',0]]
    graph.validate_stage(stage(tmp_path,rows))
    rows['zen_tags'][0][-1]=10
    with pytest.raises(codec.PortableError,match='invalid_mirror_tags'):graph.validate_stage(stage(tmp_path,rows))


def test_shared_current_bank_time_accepts_equivalent_offset_but_rejects_a_different_microsecond(tmp_path):
    from datetime import datetime,timezone
    rows=decoded();index=codec.TABLES['bank_transactions']['columns'].index('occurred_at')
    original=rows['bank_transactions'][0][index]
    rows['bank_transactions'][0][index]=datetime.fromisoformat(original).astimezone(timezone.utc).isoformat()
    graph.validate_stage(stage(tmp_path,rows))
    rows['bank_transactions'][0][index]=datetime.fromisoformat(original).replace(microsecond=1).isoformat()
    with pytest.raises(codec.PortableError,match='bank_raw_mismatch'):graph.validate_stage(stage(tmp_path,rows))


def test_shared_capture_accepts_equivalent_record_dict_and_time_but_rejects_value_changes(tmp_path):
    from datetime import datetime,timezone
    rows=decoded();offset=lambda table,column:codec.TABLES[table]['columns'].index(column)
    page=rows['capture_pages'][0];payload=json.loads(page[offset('capture_pages','payload_json')]);record=payload['records'][0]
    record['details']={'remark':'Fixture','currency':'VND'}
    bank=next(row for row in rows['bank_transactions'] if row[offset('bank_transactions','external_id')]==record['external_id'])
    original=dict(record,occurred_at=datetime.fromisoformat(record['occurred_at']).astimezone(timezone.utc).isoformat().replace('+00:00','Z'))
    raw=json.dumps(original,ensure_ascii=False,separators=(',',':'));bank[offset('bank_transactions','raw_json')]=raw
    for version in rows['bank_versions']:
        if version[0]==bank[0]:
            version[offset('bank_versions','raw_json')]=raw;version[offset('bank_versions','data_json')]=raw
    def rehash():
        raw=graph.capture_canonical(payload);page[offset('capture_pages','payload_json')]=raw;page[offset('capture_pages','digest')]=sha256(raw.encode()).hexdigest()
    rehash();graph.validate_stage(stage(tmp_path,rows))
    record['description']='Substituted description';rehash()
    with pytest.raises(codec.PortableError,match='invalid_captured_revision'):graph.validate_stage(stage(tmp_path,rows))
