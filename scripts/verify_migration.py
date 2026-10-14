"""Offline migration audit. Run against a disposable SQLite backup, not the live file.

Outputs counts/check results only; no transactions, recipients, tokens or account IDs.
"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.db import initialize, SCHEMA_VERSION
from backend.zenstore import TABLES

LOCAL = ('bank_accounts', 'bank_transactions', 'imports', 'outbox', 'audit',
         'reconciliation_groups', 'reconciliation_bank_members', 'reconciliation_zen_members',
         'reconciliation_exclusions', 'category_drafts', 'zen_category_drafts', 'zen_category_sends',
         'creation_drafts', 'recipient_rules')


def digest(db, table, query=None):
    result = hashlib.sha256()
    for row in db.execute(query or f'SELECT * FROM {table} ORDER BY rowid'):
        result.update(json.dumps(tuple(row), ensure_ascii=False, separators=(',', ':')).encode())
        result.update(b'\n')
    return result.hexdigest()


def snapshot(path):
    with sqlite3.connect(path) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLES.values() if t in names}
        stable = {t: digest(db, t) for t in LOCAL if t in names}
        stable['archive'] = digest(db, 'zen_sync_runs')
        stable['jobs'] = digest(db, 'creation_jobs', "SELECT bank_id,zen_id,state,error FROM creation_jobs ORDER BY rowid")
        stable['attempted_jobs'] = digest(db, 'creation_jobs', "SELECT * FROM creation_jobs WHERE state<>'prepared' ORDER BY rowid")
        return counts, stable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--mode', choices=['demo', 'live'], required=True)
    args = parser.parse_args()
    before_counts, before_stable = snapshot(args.path)
    initialize(args.path, args.mode)
    counts, stable = snapshot(args.path)
    with sqlite3.connect(args.path) as db:
        checks = {'version': db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION,
                  'integrity': db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok',
                  'foreign_keys': not db.execute('PRAGMA foreign_key_check').fetchall(),
                  'entity_counts': before_counts == counts, 'local_data_and_archive': before_stable == stable}
        report = json.loads(db.execute('SELECT report FROM zen_migrations WHERE version=11').fetchone()[0])
    print(json.dumps({'checks': checks, 'counts': counts, 'migration': report}), flush=True)
    if not all(checks.values()):
        raise RuntimeError('Offline migration audit failed')
    initialize(args.path, args.mode)
    if snapshot(args.path) != (counts, stable):
        raise RuntimeError('Repeated initialization is not idempotent')
    print(json.dumps({'idempotent': True}), flush=True)


if __name__ == '__main__':
    main()
