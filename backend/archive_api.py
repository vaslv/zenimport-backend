"""Authenticated archive controls, separate from live peer migration endpoints."""
from typing import Literal,Annotated
from pydantic import Field
from base64 import b64decode,b64encode
from . import migration_participant as participant
from uuid import UUID
from fastapi import APIRouter
from starlette.responses import Response
from .sync_contracts import WireModel,ErrorCode
from .portable_api import DecimalText,Digest,Empty


class BackupRequest(WireModel):
    id:UUID
    installation_id:UUID
    expected_epoch:DecimalText


class RecoveryRequest(BackupRequest):
    dataset_id:UUID
    archive_sha256:Digest
    archive_size:DecimalText
    owner_ids:list[Annotated[str,Field(strict=True,min_length=1,max_length=240)]]
    former_writers_retired:bool
    accept_post_snapshot_evidence_loss:bool


class ArchiveCapabilities(WireModel):
    archive_control_version:Literal['1']
    recovery_withdrawal:Literal[True]


class BackupCapabilities(WireModel):
    archive_control_version:Literal['1']
    backup_withdrawal:Literal[True]


class RecoveryWithdrawal(WireModel):
    withdrawal_version:Literal['1']
    decision:Literal['withdrawn']
    id:UUID
    request_sha256:Digest
    installation_id:UUID
    dataset_id:UUID
    participant_epoch:DecimalText


class RecoveryChunk(WireModel):
    data:Annotated[str,Field(strict=True,min_length=4,max_length=349528)]
    sha256:Digest


class RecoveryChunkResult(WireModel):
    number:DecimalText
    bytes:DecimalText
    sha256:Digest


class ActivateRequest(WireModel):
    preview_id:UUID
    accept_summary:bool


class RecoveryPreview(WireModel):
    id:UUID
    archive_sha256:Digest
    sdk_sha256:Digest
    source_hash:Digest
    checked_at:DecimalText
    cursor:DecimalText
    bank_transactions:DecimalText
    zen_transactions:DecimalText
    missing_bank:int
    extra_zen:int
    unchecked_accounts:int


class ArchiveStatus(WireModel):
    id:UUID
    kind:Literal['backup','recovery']
    phase:Literal['building','ready','uploading','validating','preview','activated','blocked','cancelled','cleaned']
    installation_id:UUID
    initial_epoch:DecimalText
    dataset_id:UUID
    created_at:DecimalText
    archive_sha256:Digest|None
    archive_size:DecimalText|None
    chunks:DecimalText|None
    dataset_digest:Digest|None
    acknowledged_at:DecimalText|None
    error_code:ErrorCode|None


class AcknowledgeRequest(WireModel):
    sha256:Digest


class CleanResult(WireModel):
    complete:bool


def router(service):
    routes=APIRouter(prefix='/api/v1/migration/archives')

    @routes.get('/capabilities',response_model=ArchiveCapabilities)
    def capabilities():return dict(archive_control_version='1',recovery_withdrawal=True)

    @routes.get('/backups/capabilities',response_model=BackupCapabilities)
    def backup_capabilities():return dict(archive_control_version='1',backup_withdrawal=True)

    @routes.post('/backups/withdraw',response_model=RecoveryWithdrawal)
    def withdraw_backup(data:BackupRequest):return service().withdraw_backup(data.model_dump(mode='json'))

    @routes.post('/backups',response_model=ArchiveStatus,status_code=202)
    def backup(data:BackupRequest):return service().begin_backup(data.model_dump(mode='json'))

    @routes.post('/recoveries',response_model=ArchiveStatus,status_code=202)
    def recovery(data:RecoveryRequest):return service().begin_recovery(data.model_dump(mode='json'))

    @routes.post('/recoveries/withdraw',response_model=RecoveryWithdrawal)
    def withdraw(data:RecoveryRequest):return service().withdraw_recovery(data.model_dump(mode='json'))

    @routes.put('/{id}/chunks/{number}',response_model=RecoveryChunkResult)
    def upload(id:UUID,number:int,data:RecoveryChunk):
        try:raw=b64decode(data.data,validate=True)
        except ValueError:participant.require(False)
        participant.require(b64encode(raw).decode()==data.data)
        return service().upload(str(id),number,raw,data.sha256)

    @routes.post('/{id}/finalize',response_model=ArchiveStatus,status_code=202)
    def finalize(id:UUID,data:Empty):return service().finalize_recovery(str(id))

    @routes.get('/{id}/preview',response_model=RecoveryPreview)
    def preview(id:UUID):return service().preview(str(id))

    @routes.post('/{id}/activate',response_model=ArchiveStatus)
    def activate(id:UUID,data:ActivateRequest):return service().activate_recovery(str(id),str(data.preview_id),data.accept_summary)

    @routes.get('/{id}',response_model=ArchiveStatus)
    def status(id:UUID):return service().status(str(id))

    @routes.post('/{id}/retry',response_model=ArchiveStatus,status_code=202)
    def retry(id:UUID,data:Empty):return service().retry(str(id))

    @routes.post('/{id}/cancel',response_model=ArchiveStatus)
    def cancel(id:UUID,data:Empty):return service().cancel(str(id))

    @routes.get('/{id}/chunks/{number}')
    def download(id:UUID,number:int):
        raw,checksum=service().download(str(id),number)
        return Response(raw,media_type='application/octet-stream',headers={'X-Chunk-SHA256':checksum})

    @routes.post('/{id}/acknowledge',response_model=ArchiveStatus)
    def acknowledge(id:UUID,data:AcknowledgeRequest):return service().acknowledge(str(id),data.sha256)

    @routes.post('/{id}/clean',response_model=CleanResult)
    def clean(id:UUID,data:Empty):return service().clean(str(id))

    return routes
