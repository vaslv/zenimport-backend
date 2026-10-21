"""Authenticated archive controls, separate from live peer migration endpoints."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter
from starlette.responses import Response
from .sync_contracts import WireModel,ErrorCode
from .portable_api import DecimalText,Digest,Empty


class BackupRequest(WireModel):
    id:UUID
    installation_id:UUID
    expected_epoch:DecimalText


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

    @routes.post('/backups',response_model=ArchiveStatus,status_code=202)
    def backup(data:BackupRequest):return service().begin_backup(data.model_dump(mode='json'))

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
