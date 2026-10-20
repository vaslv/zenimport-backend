"""Offline golden format only; no application database, credentials or API calls."""
import json
from pathlib import Path
import sys

from backend import portable_codec as codec

root=Path(sys.argv[1])
golden=json.loads(Path('fixtures/business/v1/portable-golden-v2.json').read_text())
expected={name:[[codec.unscalar(cell) for cell in row] for row in values] for name,values in golden['rows'].items()}
stage=codec.read_archive(root/'typescript.zip',root/'python-stage')
assert stage.manifest['dataset_digest']==golden['expected_digest']
assert stage.manifest['producer']['engine']=='indexeddb'
assert stage.manifest['producer']['contract_revision']==codec.CONTRACT_REVISION
assert stage.manifest['tables']==golden['expected_tables']
for name,rows in expected.items():assert list(stage.rows(name))==rows,name
manifest=codec.create_archive(root/'python.zip',expected,dataset_id=golden['dataset_id'],owners=golden['owner_ids'],
    session_id='00000000-0000-4000-8000-000000000002',archive_id='00000000-0000-4000-8000-000000000003',
    created_at='2026-10-20T00:00:00.000Z',version='format-only')
assert manifest['dataset_digest']==golden['expected_digest']
print('PASS: TypeScript ZIP verified by Python; Python golden ZIP exported; no activation authority')
