"""HTTP bindings for B3. Domain work commits before the response is returned."""
from uuid import UUID
from datetime import date

from fastapi import APIRouter

from . import accounts, capture, delivery, predictions, storage, migration_participant as participant
from . import discrepancies
from .capture_policy import get
from .capture_policy import SyncError
from .sync_contracts import (AccountCatalog, AccountSettings, AccountView, CaptureComplete,
    CaptureLease, CaptureLeaseRequest, CapturePage, CaptureStatus, ConnectionView,
    ConnectionCatalog, ConnectionLabel, DiscoverConnection, IntervalView, JobView, PageReceipt, StartSync, WireModel)


class EmptyRequest(WireModel):
    pass


def router(path, clock, mirror, wake):
    routes = APIRouter(prefix='/api/v1')

    def run(operation, *args):
        with storage.transaction(path) as db:
            if participant.blocked(db):
                raise SyncError('migration_paused')
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

    @routes.get('/connections', response_model=ConnectionCatalog)
    def connections(bank: str):
        with storage.transaction(path, read_only=True) as db:
            return accounts.connection_catalog(db, bank)

    @routes.put('/connections/{identifier}/label', response_model=ConnectionView)
    def rename(identifier: UUID, data: ConnectionLabel):
        return run(accounts.rename_connection, identifier, data)

    @routes.post('/connections/{identifier}/accounts/{account_id}/unlink', response_model=ConnectionView)
    def unlink(identifier: UUID, account_id: UUID, data: EmptyRequest):
        return run(accounts.unlink_account, identifier, account_id)

    @routes.get('/connections/{identifier}', response_model=ConnectionView)
    def connection(identifier: UUID):
        with storage.transaction(path, read_only=True) as db:
            return accounts.connection_view(db, identifier)

    @routes.get('/connections/{identifier}/discrepancies', response_model=discrepancies.DiscrepancyReport)
    def differences(identifier: UUID, from_date: date | None = None, to_date: date | None = None,
                    account_id: UUID | None = None, refresh: bool = False):
        with storage.transaction(path, read_only=True) as db:
            get(db, 'connections', identifier)
        if mirror().client is not None:
            mirror().refresh(force=refresh)
        with storage.transaction(path, read_only=True) as db:
            return discrepancies.build(db, identifier, clock(), from_date=from_date, to_date=to_date, account_id=account_id)

    @routes.get('/zenmoney/accounts', response_model=AccountCatalog)
    def catalog():
        if mirror().client is not None:
            mirror().refresh()
        return run(accounts.catalog)

    @routes.put('/accounts/{identifier}/settings', response_model=AccountView)
    def settings(identifier: UUID, data: AccountSettings):
        if data.selection == 'enabled' and mirror().client is not None:
            with storage.transaction(path, read_only=True) as db:
                current = get(db, 'bank_accounts', identifier)
                changed = (current['selection'], current['zenmoney_account_id'], current['import_start']) != (
                    data.selection, data.zenmoney_account_id, data.import_start.isoformat())
                needs_catalog = changed and data.expected_version == current['settings_version']
            if needs_catalog:
                mirror().refresh()
        return run(accounts.settings, identifier, data)

    @routes.post('/jobs', response_model=JobView, status_code=202)
    def start(data: StartSync):
        def command(db, data, now):
            repeated = db.execute('SELECT 1 FROM job_requests WHERE request_id=?', (str(data.request_id),)).fetchone()
            job = capture.start(db, data, now)
            db.execute("INSERT OR REPLACE INTO service_meta VALUES ('sync_ever_started','1')")
            db.execute("DELETE FROM service_meta WHERE key='migration_hold' AND value='imported'")
            db.execute('UPDATE migration_installation SET awaiting_sync=0 WHERE id=1 AND state=\'active\'')
            if not repeated:
                delivery.authorize_job(db, job['id'], now)
            return job, bool(repeated)
        job, repeated = run(command, data)
        if not repeated:
            try:
                mirror().refresh(force=True)
            except SyncError:
                with storage.transaction(path) as db:
                    capture.add_error(db, job['id'], None, 'zenmoney_unavailable', clock(), True)
                    db.execute("UPDATE capture_jobs SET delivery_state='blocked' WHERE id=? AND delivery_state='waiting'", (job['id'],))
        wake()
        return run(capture.job_view, job['id'])

    @routes.get('/jobs/{identifier}', response_model=JobView)
    def job(identifier: UUID):
        return run(capture.job_view, identifier)

    @routes.post('/jobs/{identifier}/lease', response_model=CaptureLease)
    def lease(identifier: UUID, data: CaptureLeaseRequest):
        return run(capture.lease, identifier, data)

    @routes.put('/jobs/{job_id}/intervals/{interval_id}/pages/{number}', response_model=PageReceipt)
    def page(job_id: UUID, interval_id: UUID, number: int, data: CapturePage):
        def store_page(db, job, interval, number, data, now):
            receipt = capture.page(db, job, interval, number, data, now)
            if not receipt['repeated']:
                identifiers = {r[0] for r in db.execute('SELECT bank_id FROM captured_records WHERE interval_id=? AND page_number=?', (str(interval), number))}
                historical = any(db.execute("SELECT 1 FROM bank_processing WHERE bank_id=? AND state='delivered'", (bank_id,)).fetchone() for bank_id in identifiers)
                if historical:
                    predictions.rebuild_evidence(db)
                predictions.prepare(db, now, None if historical else identifiers)
            return receipt
        return run(store_page, job_id, interval_id, number, data)

    @routes.post('/jobs/{job_id}/intervals/{interval_id}/complete', response_model=IntervalView)
    def complete(job_id: UUID, interval_id: UUID, data: CaptureComplete):
        def finish(db, job_id, interval_id, data, now):
            result = capture.complete(db, job_id, interval_id, data, now)
            delivery.authorize_job(db, job_id, now)
            return result
        result = run(finish, job_id, interval_id, data)
        wake()
        return result

    @routes.post('/jobs/{identifier}/capture-status', response_model=JobView)
    def status(identifier: UUID, data: CaptureStatus):
        return run(capture.status, identifier, data)

    @routes.post('/jobs/{identifier}/stop', response_model=JobView)
    def stop(identifier: UUID, data: EmptyRequest):
        return run(capture.stop, identifier)

    return routes
