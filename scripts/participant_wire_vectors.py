"""Observe protocol-2 receipt integrity/canonicalization against the Python owner."""
from copy import deepcopy
from hashlib import sha256
import json
import sys

from backend import migration_participant as participant
from backend.capture_policy import SyncError

A='00000000-0000-4000-8000-000000000001'
B='00000000-0000-4000-8000-000000000002'
SWITCH='00000000-0000-4000-8000-000000000003'
DATASET='00000000-0000-4000-8000-000000000004'
REQUEST='00000000-0000-4000-8000-000000000005'


def receipt(command):
    role,state={'source_seal':('source','export_hold'),'destination_arm':('destination','armed'),
        'source_handoff':('source','handed_off'),'source_cancel':('source','cancel_pending'),
        'destination_discard':('destination','discarded')}[command]
    current=dict(installation_id=A,participant_epoch=9007199254740993)
    operation=dict(switch_id=SWITCH,role=role,peer_id=B,peer_epoch=9007199254740992,dataset_id=DATASET,state=state,
        dataset_digest='a'*64,archive_sha256='b'*64,archive_size=262145)
    return participant._proof(current,operation,REQUEST,command)


def observe(value,command,local_id,operation):
    source=None if operation is None else operation|dict(archive_size=None if operation['archive_size'] is None else int(operation['archive_size']))
    try:
        participant.verify_receipt(value,command=command,current=dict(installation_id=local_id),operation=source)
        return True
    except (SyncError,ValueError,TypeError,KeyError):return False


def vectors():
    receipts=[]
    for command in ('source_seal','destination_arm','source_handoff','source_cancel','destination_discard'):
        value=receipt(command)
        operation={key:value[key] for key in ('switch_id','dataset_id','dataset_digest','archive_sha256','archive_size')}
        operation['peer_id']=A
        def add(candidate,local=B,binding=operation):
            receipts.append(dict(value=candidate,command=command,local_id=local,operation=binding,accepted=observe(candidate,command,local,binding)))
        add(value);add(value,A);add(value,REQUEST);add(value,binding=None)
        for field in value:
            for replacement in (None,False,0,'bad',REQUEST):
                changed=deepcopy(value);changed[field]=replacement
                if field!='receipt_sha256':
                    plain={key:item for key,item in changed.items() if key!='receipt_sha256'}
                    try:changed['receipt_sha256']=sha256(participant.canonical(plain)).hexdigest()
                    except SyncError:pass
                add(changed)
        for field in operation:
            add(value,binding=operation|{field:None})
        absent=dict(value);del absent['peer_epoch'];add(absent)
        extra=value|dict(extra='unexpected');add(extra)
    values=[None,True,False,1,{},[],{'z':'Привет','\ue000':'bmp','😀':'astral','control':'\n\x00'},
        'x'*16382,'x'*16383,'\x00'*2730,'\x00'*2731,{'invalid':'\ud800'}]
    for depth in (31,32,33):
        value=None
        for _ in range(depth):value=[value]
        values.append(value)
    canonical=[]
    for value in values:
        try:raw=participant.canonical(value).hex()
        except SyncError:raw=None
        canonical.append(dict(value=value,hex=raw))
    return dict(version=1,receipts=receipts,canonical=canonical)


if __name__=='__main__':json.dump(vectors(),sys.stdout,ensure_ascii=True,sort_keys=True,separators=(',',':'));print()
