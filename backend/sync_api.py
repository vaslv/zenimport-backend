"""HTTP bindings for B3. Domain work commits before the response is returned."""
from uuid import UUID

from fastapi import APIRouter

from . import accounts, capture, storage
from .capture_policy import SyncError
from .sync_contracts import (AccountCatalog, AccountSettings, AccountView, CaptureComplete,
    CaptureLease, CaptureLeaseRequest, CapturePage, CaptureStatus, ConnectionView,
    DiscoverConnection, IntervalView, JobView, PageReceipt, StartSync, WireModel)


class EmptyRequest(WireModel):
    pass


def router(path, clock):
    routes = APIRouter(prefix='/api/v1')

    def run(operation, *args):
        with storage.transaction(path) as db:
            try:
                return operation(db, *args, clock())
            except SyncError as exc:
                if exc.persist:
                    # A rejected changed snapshot/expired lease must still retain
                    # its failed/paused state. No page data is partially accepted.
                    db.commit()
                raise

    @routes.post('/connections/discover', response_model=ConnectionView)
    def discover(data: DiscoverConnection):
        return run(accounts.discover, data)

    @routes.get('/connections/{identifier}', response_model=ConnectionView)
    def connection(identifier: UUID):
        with storage.transaction(path, read_only=True) as db:
            return accounts.connection_view(db, identifier)

    @routes.get('/zenmoney/accounts', response_model=AccountCatalog)
    def catalog():
        return run(accounts.catalog)

    @routes.put('/accounts/{identifier}/settings', response_model=AccountView)
    def settings(identifier: UUID, data: AccountSettings):
        return run(accounts.settings, identifier, data)

    @routes.post('/jobs', response_model=JobView, status_code=202)
    def start(data: StartSync):
        return run(capture.start, data)

    @routes.get('/jobs/{identifier}', response_model=JobView)
    def job(identifier: UUID):
        return run(capture.job_view, identifier)

    @routes.post('/jobs/{identifier}/lease', response_model=CaptureLease)
    def lease(identifier: UUID, data: CaptureLeaseRequest):
        return run(capture.lease, identifier, data)

    @routes.put('/jobs/{job_id}/intervals/{interval_id}/pages/{number}', response_model=PageReceipt)
    def page(job_id: UUID, interval_id: UUID, number: int, data: CapturePage):
        return run(capture.page, job_id, interval_id, number, data)

    @routes.post('/jobs/{job_id}/intervals/{interval_id}/complete', response_model=IntervalView)
    def complete(job_id: UUID, interval_id: UUID, data: CaptureComplete):
        return run(capture.complete, job_id, interval_id, data)

    @routes.post('/jobs/{identifier}/capture-status', response_model=JobView)
    def status(identifier: UUID, data: CaptureStatus):
        return run(capture.status, identifier, data)

    @routes.post('/jobs/{identifier}/stop', response_model=JobView)
    def stop(identifier: UUID, data: EmptyRequest):
        return run(capture.stop, identifier)

    return routes
