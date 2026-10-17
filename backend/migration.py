"""Authenticated, quiescent migration of a complete journal; no executable dump SQL."""
from contextlib import closing, contextmanager
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
from uuid import UUID
import zipfile

from . import __version__, storage
from .capture_policy import SyncError
from .delivery import writer_lock
from .snapshot import copy_snapshot
from .sync_contracts import MAX_MIGRATION_BYTES

MAX_DATABASE_BYTES = 256 * 1024 * 1024


def meta(db, key):
    row = db.execute('SELECT value FROM service_meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else None


def started(db):
    return bool(meta(db, 'sync_ever_started') or any(db.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone()
        for table in ('capture_jobs', 'bank_transactions', 'deliveries', 'processing_receipts', 'processing_targets')))


def owners(db):
    rows = list(db.execute("SELECT id FROM zen_entities WHERE kind='user' AND deleted=0 ORDER BY id"))
    return [r[0] for r in rows]


class MigrationGate:
    """Normal requests may overlap; exclusive transfer fails fast instead of racing."""
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.active = 0
        self.exclusive_active = False

    @contextmanager
    def operation(self, *, worker=False):
        with self.lock:
            if self.exclusive_active:
                raise SyncError('migration_busy', retryable=True)
            self.active += 1
        try:
            with storage.transaction(self.path, read_only=True) as db:
                hold = meta(db, 'migration_hold')
            if hold == 'exported' or worker and hold == 'imported':
                raise SyncError('migration_paused')
            yield
        finally:
            with self.lock:
                self.active -= 1

    @contextmanager
    def exclusive(self):
        with self.lock:
            if self.active or self.exclusive_active:
                raise SyncError('migration_busy', retryable=True)
            self.exclusive_active = True
        try:
            with writer_lock(self.path) as acquired:
                if not acquired:
                    raise SyncError('migration_busy', retryable=True)
                yield
        finally:
            with self.lock:
                self.exclusive_active = False


class Migration:
    def __init__(self, path, gate, mirror, clock):
        self.path, self.gate, self.mirror, self.clock = path, gate, mirror, clock

    def status(self):
        with storage.transaction(self.path, read_only=True) as db:
            hold = meta(db, 'migration_hold')
            return dict(format_version=1, can_import=not started(db) and not meta(db, 'migration_import_sha256'),
                        paused=hold == 'exported', awaiting_sync=hold == 'imported',
                        imported_sha256=meta(db, 'migration_import_sha256'), max_archive_bytes=MAX_MIGRATION_BYTES)

    def export(self):
        with self.gate.exclusive(), self.mirror.lock:
            with storage.transaction(self.path, read_only=True) as db:
                if db.execute("SELECT 1 FROM capture_jobs WHERE capture_state IN ('planned','collecting','paused') LIMIT 1").fetchone():
                    raise SyncError('migration_incomplete')
                if not owners(db):
                    raise SyncError('zenmoney_unavailable', 503, retryable=True)
            # Persistent before copying: closing the popup or restarting the process
            # must never allow the source to advance beyond the exported journal.
            with storage.transaction(self.path) as db:
                db.execute("INSERT OR REPLACE INTO service_meta VALUES ('migration_hold','exported')")
            with TemporaryDirectory(prefix='zenimport-export-') as work:
                snapshot = Path(work) / 'database.sqlite3'
                copy_snapshot(self.path, snapshot)
                if snapshot.stat().st_size > MAX_DATABASE_BYTES:
                    raise SyncError('payload_too_large', 413)
                with storage.transaction(snapshot, read_only=True) as db:
                    identity = meta(db, 'instance_id')
                    users = owners(db)
                data = snapshot.read_bytes()
                manifest = dict(format='zenimport-migration', format_version=1, schema_version=storage.SCHEMA_VERSION,
                    service_version=__version__, created_at=self.clock(), instance_id=identity, zenmoney_users=users,
                    database_size=len(data), database_sha256=sha256(data).hexdigest())
                output = BytesIO()
                with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('manifest.json', json.dumps(manifest, sort_keys=True))
                    archive.writestr('database.sqlite3', data)
                result = output.getvalue()
                if len(result) > MAX_MIGRATION_BYTES:
                    raise SyncError('payload_too_large', 413)
                return result

    def resume(self):
        with self.gate.exclusive(), storage.transaction(self.path) as db:
            if meta(db, 'migration_hold') == 'exported':
                db.execute("DELETE FROM service_meta WHERE key='migration_hold'")
        return self.status()

    def restore(self, payload):
        if len(payload) > MAX_MIGRATION_BYTES:
            raise SyncError('payload_too_large', 413)
        digest = sha256(payload).hexdigest()
        with self.gate.exclusive(), self.mirror.lock:
            with storage.transaction(self.path, read_only=True) as db:
                previous = meta(db, 'migration_import_sha256')
                if previous:
                    if previous == digest and meta(db, 'migration_hold') == 'imported':
                        return dict(restored=True, already_imported=True, sha256=digest)
                    raise SyncError('migration_not_empty')
                if started(db):
                    raise SyncError('migration_not_empty')
            with TemporaryDirectory(prefix='zenimport-import-') as work:
                incoming = Path(work) / 'database.sqlite3'
                try:
                    manifest = unpack(payload, incoming)
                except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error, zipfile.BadZipFile, NotImplementedError):
                    raise SyncError('migration_invalid', 422) from None
                if self.mirror.client is None:
                    raise SyncError('zenmoney_unavailable', 503, retryable=True)
                # A fresh full read proves the configured destination identity.
                # It sends no financial changes. Compare all users, not display names.
                self.mirror.refresh(force=True, transactions=True)
                with storage.transaction(self.path, read_only=True) as db:
                    if owners(db) != manifest['zenmoney_users']:
                        raise SyncError('migration_wrong_account', 409)
                with storage.transaction(incoming) as db:
                    db.execute("INSERT OR REPLACE INTO service_meta VALUES ('migration_hold','imported')")
                    db.execute("INSERT OR REPLACE INTO service_meta VALUES ('migration_import_sha256',?)", (digest,))
                    # A new token for the same verified identity is allowed. The next
                    # explicit sync fetches current data before any attempted write.
                    db.execute('UPDATE zen_mirror_state SET source_hash=?,cursor=0,needs_full=1,ready=0,generation=generation+1,error_code=NULL WHERE id=1', (self.mirror.source_hash,))
                    db.execute('UPDATE delivery_authorizations SET source_hash=? WHERE bank_id NOT IN (SELECT bank_id FROM deliveries WHERE ever_attempted=1)', (self.mirror.source_hash,))
                    db.execute("DELETE FROM service_meta WHERE key='zenmoney_refreshed_at'")
                    db.execute('DELETE FROM category_predictions')
                # SQLite backup is one destination transaction. No os.replace/WAL
                # unlink: concurrent file readers and crash recovery remain valid.
                with closing(storage.connect_readonly(incoming)) as source, closing(storage.configured(sqlite3.connect(self.path))) as target:
                    source.backup(target)
                return dict(restored=True, already_imported=False, sha256=digest)


def unpack(payload, destination):
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
        if sorted(names) != ['database.sqlite3', 'manifest.json']:
            raise ValueError('Unexpected members')
        for item in archive.infolist():
            maximum = 16384 if item.filename == 'manifest.json' else MAX_DATABASE_BYTES
            if item.flag_bits & 1 or not 0 < item.file_size <= maximum or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError('Invalid archive member')
        manifest = json.loads(archive.read('manifest.json'))
        if (not isinstance(manifest, dict) or manifest.get('format') != 'zenimport-migration'
                or type(manifest.get('format_version')) is not int or manifest['format_version'] != 1
                or type(manifest.get('schema_version')) is not int or manifest['schema_version'] not in (6, storage.SCHEMA_VERSION)
                or not isinstance(manifest.get('instance_id'), str)):
            raise ValueError('Unsupported format')
        UUID(manifest['instance_id'])
        data = archive.read('database.sqlite3')
        if len(data) != manifest['database_size'] or sha256(data).hexdigest() != manifest['database_sha256']:
            raise ValueError('Checksum mismatch')
        storage.create_private(destination)
        destination.write_bytes(data)
        with closing(storage.connect_readonly(destination)) as db:
            actual_version = storage.validate_owner(db)
            if actual_version != manifest['schema_version']:
                raise ValueError('Manifest schema mismatch')
            storage.check_integrity(db)
            if meta(db, 'instance_id') != manifest['instance_id'] or not owners(db) or owners(db) != manifest['zenmoney_users']:
                raise ValueError('Manifest identity mismatch')
        # Upgrade only a fully verified private temporary copy. Published 0.2.0
        # archives (schema 6) remain usable when moving to the new server.
        if actual_version < storage.SCHEMA_VERSION:
            storage.initialize(destination)
        return manifest
