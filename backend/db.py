from . import jsoncodec as json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
from contextlib import contextmanager, closing

SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bank_accounts (
 id TEXT PRIMARY KEY, bank TEXT NOT NULL, account TEXT NOT NULL,
 currency TEXT NOT NULL, zenmoney_account TEXT,
 UNIQUE(bank, account));
CREATE TABLE IF NOT EXISTS imports (
 id TEXT PRIMARY KEY, received_at INTEGER NOT NULL, bank_account TEXT NOT NULL,
 adapter_version TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bank_transactions (
 id TEXT PRIMARY KEY, bank_account TEXT NOT NULL REFERENCES bank_accounts(id),
 external_id TEXT NOT NULL, date TEXT NOT NULL, amount_minor INTEGER NOT NULL,
 description TEXT NOT NULL, raw TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', linked_id TEXT,
 UNIQUE(bank_account, external_id));
CREATE TABLE IF NOT EXISTS outbox (
 bank_transaction TEXT PRIMARY KEY REFERENCES bank_transactions(id),
 zen_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', error TEXT);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, at INTEGER NOT NULL, operation TEXT NOT NULL, data TEXT NOT NULL);

'''


def connect(path):
    conn = sqlite3.connect(path, timeout=35)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


SCHEMA_VERSION = 11


def backup(db, path):
    destination = Path(str(path) + '.pre-v11-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8] + '.sqlite3')
    destination.touch(mode=0o600, exist_ok=False)
    try:
        with closing(sqlite3.connect(destination)) as target:
            db.backup(target)
    except Exception:
        destination.unlink()
        raise
    return destination


def initialize(path, mode):
    with closing(connect(path)) as db, db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError('Database schema is newer than this application')
        has_meta = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        previous = db.execute("SELECT value FROM meta WHERE key='mode'").fetchone() if has_meta else None
        if previous and previous['value'] != mode:
            raise RuntimeError('Use a separate database for demo and live mode')
        if has_meta and version < SCHEMA_VERSION:
            backup(db, path)
        db.executescript(SCHEMA)
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT OR IGNORE INTO meta VALUES ('mode',?)", (mode,))
        from .zenstore import migrate
        migrate(db)
        if version < 4:
            db.execute('CREATE TABLE IF NOT EXISTS split_links (bank_id TEXT NOT NULL REFERENCES bank_transactions(id), zen_id TEXT NOT NULL UNIQUE, PRIMARY KEY(bank_id,zen_id))')
        from .link_groups import migrate as migrate_groups
        migrate_groups(db)
        db.execute('CREATE TABLE IF NOT EXISTS reconciliation_exclusions (zen_id TEXT PRIMARY KEY, created_at INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS category_drafts (bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id), category_id TEXT NOT NULL, updated_at INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS zen_category_drafts (zen_id TEXT PRIMARY KEY, category_id TEXT NOT NULL, original_tags TEXT NOT NULL, updated_at INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS zen_category_sends (zen_id TEXT PRIMARY KEY, category_id TEXT NOT NULL, draft_updated_at INTEGER NOT NULL, before_payload TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, error TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS creation_drafts (bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id), category_id TEXT, source TEXT NOT NULL, updated_at INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS creation_jobs (bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id), zen_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, state TEXT NOT NULL, error TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS recipient_rules (bank TEXT NOT NULL, name TEXT NOT NULL, direction TEXT NOT NULL, category_id TEXT NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY(bank,name,direction))')
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')


@contextmanager
def transaction(path, *, read_only=False):
    db = connect(path)
    try:
        if read_only:
            db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN' if read_only else 'BEGIN IMMEDIATE')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def entities(db, kind):
    from .zenstore import TABLES
    return [json.loads(r['data']) for r in db.execute(
        f'SELECT data FROM {TABLES[kind]} ORDER BY rowid')]
