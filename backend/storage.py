"""Versioned SQLite storage; legacy databases require explicit out-of-place conversion."""
from contextlib import closing, contextmanager
from decimal import Decimal
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

APPLICATION_ID = 0x5A494231
SCHEMA_VERSION = 7
SCHEMA_SQL = Path(__file__).with_name('schema_v2.sql').read_text()
B3_SQL = Path(__file__).with_name('schema_v3.sql').read_text()
B4_SQL = Path(__file__).with_name('schema_v4.sql').read_text()
B5_SQL = Path(__file__).with_name('schema_v5.sql').read_text()
B6_SQL = Path(__file__).with_name('schema_v6.sql').read_text()
B7_SQL = Path(__file__).with_name('schema_v7.sql').read_text()
OBJECTS_V2 = {(kind.lower(), name) for kind, name in re.findall(r'CREATE (TABLE|TRIGGER) (\w+)', SCHEMA_SQL)}
OBJECTS_V3 = OBJECTS_V2 | {(kind.lower(), name) for kind, name in re.findall(r'CREATE (TABLE|TRIGGER) (\w+)', B3_SQL)}
OBJECTS_V4 = OBJECTS_V3 | {(kind.lower(), name) for kind, name in re.findall(r'CREATE (TABLE|TRIGGER) (\w+)', B4_SQL)}
OBJECTS_V5 = OBJECTS_V4 | {(kind.lower(), name) for kind, name in re.findall(r'CREATE (TABLE|TRIGGER) (\w+)', B5_SQL)}
OBJECTS_V6 = OBJECTS_V5 | {(kind.lower(), name) for kind, name in re.findall(r'CREATE (TABLE|TRIGGER) (\w+)', B6_SQL)}
OBJECTS = OBJECTS_V6 | {(kind.lower(), name) for kind, name in re.findall(r'CREATE (TABLE|TRIGGER) (\w+)', B7_SQL)}
MONEY_PATTERN = re.compile(r'(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,12})?')


def money_valid(value):
    return int(isinstance(value, str) and MONEY_PATTERN.fullmatch(value) is not None and Decimal(value) > 0)


def configured(db):
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA recursive_triggers=ON')
    db.create_function('money_valid', 1, money_valid, deterministic=True)
    return db


def connect_readonly(path: Path):
    return configured(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2))


def validate_owner(db, *, allow_upgrade=True):
    app_id = db.execute('PRAGMA application_id').fetchone()[0]
    version = db.execute('PRAGMA user_version').fetchone()[0]
    objects = {(r[0], r[1]) for r in db.execute(
        "SELECT type,name FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'")}
    if app_id == 0 and version == 0 and not objects and allow_upgrade:
        return 0
    base = {('table', 'service_meta')}
    expected = base if version == 1 else base | {2: OBJECTS_V2, 3: OBJECTS_V3, 4: OBJECTS_V4, 5: OBJECTS_V5, 6: OBJECTS_V6}.get(version, OBJECTS)
    if app_id != APPLICATION_ID or version not in (tuple(range(1, SCHEMA_VERSION + 1)) if allow_upgrade else (SCHEMA_VERSION,)) or objects != expected:
        raise RuntimeError('Database belongs to another application or schema; use the explicit migration procedure')
    return version


def create_private(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)


def backup(path):
    target = Path(str(path) + f'.pre-v{SCHEMA_VERSION}-' + uuid4().hex + '.sqlite3')
    create_private(target)
    try:
        with closing(connect_readonly(path)) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def apply_schema(db):
    execute_schema(db, SCHEMA_SQL)


def execute_schema(db, sql):
    # executescript would commit a pending transaction, so execute complete statements individually.
    pending = ''
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            db.execute(pending)
            pending = ''
    if pending.strip():
        raise RuntimeError('Incomplete storage schema')


def initialize(path: Path):
    previous = 0
    if path.exists():
        with closing(connect_readonly(path)) as db:
            previous = validate_owner(db)
    saved = backup(path) if 0 < previous < SCHEMA_VERSION else None
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            create_private(path)
        except FileExistsError:
            pass
    with closing(configured(sqlite3.connect(path, timeout=2))) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            version = validate_owner(db)
            db.execute('CREATE TABLE IF NOT EXISTS service_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO service_meta VALUES (?,?)', ('instance_id', str(uuid4())))
            if version < 2:
                apply_schema(db)
                db.execute('INSERT INTO schema_migrations VALUES (?,?,?)', (2, int(time.time()), str(saved) if saved else None))
            if version < 3:
                execute_schema(db, B3_SQL)
                db.execute('INSERT INTO schema_migrations VALUES (?,?,?)', (3, int(time.time()), str(saved) if saved else None))
            if version < 4:
                execute_schema(db, B4_SQL)
                db.execute('INSERT INTO schema_migrations VALUES (?,?,?)', (4, int(time.time()), str(saved) if saved else None))
            if version < 5:
                execute_schema(db, B5_SQL)
                db.execute('INSERT INTO schema_migrations VALUES (?,?,?)', (5, int(time.time()), str(saved) if saved else None))
            if version < 6:
                execute_schema(db, B6_SQL)
                from .ownership import backfill
                backfill(db)
                db.execute('INSERT INTO schema_migrations VALUES (?,?,?)', (6, int(time.time()), str(saved) if saved else None))
            if version < 7:
                execute_schema(db, B7_SQL)
                db.execute('INSERT INTO schema_migrations VALUES (?,?,?)', (7, int(time.time()), str(saved) if saved else None))
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            db.commit()
        except Exception:
            db.rollback()
            raise
        if db.execute('PRAGMA journal_mode=WAL').fetchone()[0] != 'wal':
            raise RuntimeError('Database must support WAL mode')
    return saved


@contextmanager
def transaction(path: Path, *, read_only=False):
    db = connect_readonly(path) if read_only else configured(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=2))
    try:
        db.execute('BEGIN' if read_only else 'BEGIN IMMEDIATE')
        validate_owner(db, allow_upgrade=False)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_integrity(db):
    if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise RuntimeError('Storage integrity validation failed')


def check(path: Path):
    with closing(connect_readonly(path)) as db:
        validate_owner(db, allow_upgrade=False)
        if not db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone():
            raise RuntimeError('Storage bootstrap is incomplete')
