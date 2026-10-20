"""Reproducibly vendor the pinned financial contract into the runtime package."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
source=root/'fixtures/business/v1'
FILES={'schema':'sqlite-v7.json','policy':'portable-policy.json','manifest':'portable-v2.schema.json',
       'format':'portable-format-v2.json','validation':'portable-validation-v2.json'}


def build():
    manifest=json.loads((source/'manifest.json').read_text())
    values={}
    for key,name in FILES.items():
        raw=(source/name).read_bytes()
        assert sha256(raw).hexdigest()==manifest['files'][name], 'Unpinned contract input'
        values[key]=json.loads(raw)
    return (json.dumps(values,ensure_ascii=False,separators=(',',':'))+'\n').encode()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    target=root/'backend/portable_contract.json'
    raw=build()
    if args.write:target.write_bytes(raw)
    else:assert target.read_bytes()==raw, 'Regenerate the runtime contract with --write'
    print('PASS: pinned runtime contract bytes')
