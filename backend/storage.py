"""Bootstrap storage only; financial tables and migrations belong to B2."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3
from uuid import uuid4

APPLICATION_ID = 0x5A494231  # ZIB1: independent backend, not the legacy database.
SCHEMA_VERSION = 1


def connect_readonly(path: Path):
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)


def validate_owner(db):
    app_id = db.execute('PRAGMA application_id').fetchone()[0]
    version = db.execute('PRAGMA user_version').fetchone()[0]
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    empty = app_id == 0 and version == 0 and not tables
    if not empty and (app_id != APPLICATION_ID or version != SCHEMA_VERSION or tables != {'service_meta'}):
        raise RuntimeError('Database belongs to another application or schema; use the explicit migration procedure')


def initialize(path: Path):
    # Inspect existing databases read-only before enabling WAL or creating tables.
    if path.exists():
        with closing(connect_readonly(path)) as db:
            validate_owner(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
    with closing(sqlite3.connect(path, timeout=2)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            validate_owner(db)
            db.execute('CREATE TABLE IF NOT EXISTS service_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO service_meta VALUES (?,?)', ('instance_id', str(uuid4())))
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            db.commit()
        except Exception:
            db.rollback()
            raise
        if db.execute('PRAGMA journal_mode=WAL').fetchone()[0] != 'wal':
            raise RuntimeError('Database must support WAL mode')


def check(path: Path):
    with closing(connect_readonly(path)) as db:
        validate_owner(db)
        if not db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone():
            raise RuntimeError('Storage bootstrap is incomplete')
