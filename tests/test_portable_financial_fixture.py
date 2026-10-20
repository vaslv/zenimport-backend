"""Cross-runtime fixture comes from isolated real capture/delivery code paths."""
from copy import deepcopy
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
