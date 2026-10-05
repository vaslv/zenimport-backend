import json
import sqlite3
from contextlib import contextmanager

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
CREATE TABLE IF NOT EXISTS zen_entities (
 kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS outbox (
 bank_transaction TEXT PRIMARY KEY REFERENCES bank_transactions(id),
 zen_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', error TEXT);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, at INTEGER NOT NULL, operation TEXT NOT NULL, data TEXT NOT NULL);
PRAGMA user_version=1;
'''


def connect(path):
    conn = sqlite3.connect(path, timeout=35)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def initialize(path, mode):
    with connect(path) as db:
        db.executescript(SCHEMA)
        previous = db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
        if previous and previous['value'] != mode:
            raise RuntimeError('Use a separate database for demo and live mode')
        db.execute("INSERT OR IGNORE INTO meta VALUES ('mode',?)", (mode,))


@contextmanager
def transaction(path):
    db = connect(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def entities(db, kind):
    return [json.loads(r['data']) for r in db.execute(
        'SELECT data FROM zen_entities WHERE kind=?', (kind,))]
