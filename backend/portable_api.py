"""Authenticated portable-v2 endpoints, independent of ordinary API version 1."""
from base64 import b64decode,b64encode
from typing import Annotated,Literal
from uuid import UUID

from fastapi import APIRouter
from starlette.responses import Response
from pydantic import Field

from . import migration_participant as participant
from .sync_contracts import WireModel,ErrorCode

DecimalText=Annotated[str,Field(strict=True,pattern=r'^(?:0|[1-9][0-9]{0,18})$')]
Digest=Annotated[str,Field(strict=True,pattern=r'^[0-9a-f]{64}$')]
State=Literal['active','export_hold','import_hold','armed','cancel_pending','handed_off']
OperationState=Literal['export_hold','import_hold','armed','cancel_pending','handed_off','activated','discarded','resumed']
Command=Literal['source_start','destination_start','destination_arm','source_handoff','source_cancel','destination_discard','source_resume','destination_activate']


class Receipt(WireModel):
    receipt_version:Literal['1']
    request_id:UUID
    command:Literal['source_start','source_seal','destination_start','destination_arm','source_handoff','source_cancel','destination_discard','source_resume','destination_activate']
    installation_id:UUID
    participant_epoch:DecimalText
    switch_id:UUID
    role:Literal['source','destination']
    peer_id:UUID
    peer_epoch:DecimalText|None
    dataset_id:UUID
    state:OperationState
    dataset_digest:Digest|None
    archive_sha256:Digest|None
    archive_size:DecimalText|None
    receipt_sha256:Digest


class Task(WireModel):
    id:UUID
    switch_id:UUID
    kind:Literal['snapshot','validate','activate']
    state:Literal['queued','running','blocked','complete']
    error_code:ErrorCode|None


class Operation(WireModel):
    switch_id:UUID
    role:Literal['source','destination']
    peer_id:UUID
    dataset_id:UUID
    state:OperationState
    dataset_digest:Digest|None
    archive_sha256:Digest|None
    archive_size:DecimalText|None
    peer_epoch:DecimalText|None
    validated:bool
    created_at:DecimalText
    updated_at:DecimalText


class ParticipantStatus(WireModel):
    installation_id:UUID
    participant_epoch:DecimalText
    state:State
    operation_id:UUID|None
    awaiting_sync:bool
    legacy_hold:Literal['exported','imported']|None


class Capabilities(ParticipantStatus):
    protocol_version:Literal['2']
    archive_version:Literal['2']
    financial_schema_version:Literal['1']
    chunk_bytes:Literal['262144']
    max_archive_bytes:Literal['67108864']
    can_import:bool


class Status(ParticipantStatus):
    operation:Operation|None
    receipts:list[Receipt]
    work:list[Task]


class CommandResult(WireModel):
    receipt:Receipt|None=None
    work:Task|None=None


class ChunkRequest(WireModel):
    expected_epoch:DecimalText
    data:Annotated[str,Field(strict=True,min_length=4,max_length=349528)]
    sha256:Digest


class ChunkReceipt(WireModel):
    switch_id:UUID
    number:DecimalText
    bytes:DecimalText
    sha256:Digest


class FinalizeRequest(WireModel):
    request_id:UUID
    switch_id:UUID
    expected_epoch:DecimalText


class Empty(WireModel):pass


def router(service):
    routes=APIRouter(prefix='/api/v1/migration/portable')

    @routes.get('/capabilities',response_model=Capabilities)
    def capabilities():return service().capabilities()

    @routes.get('/switches/{switch}',response_model=Status)
    def status(switch:UUID):return service().status(str(switch))

    @routes.post('/commands/{command}',response_model=CommandResult,status_code=202)
    def command(command:Command,data:dict):return service().command(command,data)

    @routes.put('/switches/{switch}/chunks/{number}',response_model=ChunkReceipt)
    def upload(switch:UUID,number:int,data:ChunkRequest):
        try:raw=b64decode(data.data,validate=True)
        except ValueError:participant.require(False)
        participant.require(b64encode(raw).decode()==data.data)
        return service().chunk(str(switch),data.expected_epoch,number,raw,data.sha256)

    @routes.get('/switches/{switch}/chunks/{number}')
    def download(switch:UUID,number:int):
        data,checksum=service().download(str(switch),number)
        return Response(data,media_type='application/octet-stream',headers={'X-Chunk-SHA256':checksum})

    @routes.post('/finalize',response_model=Task,status_code=202)
    def finalize(data:FinalizeRequest):return service().finalize(data.model_dump(mode='json'))

    @routes.post('/work/{task}/retry',response_model=Task,status_code=202)
    def retry(task:UUID,data:Empty):return service().retry(str(task))

    return routes
