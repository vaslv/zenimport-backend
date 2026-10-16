"""Explicit, journaled maintenance of a separately reviewed duplicate plan.

Never called by HTTP routes, capture jobs or the delivery worker. No retries of
financial writes: an interrupted journal can only be verified by reading.
"""
import argparse
from hashlib import sha256
import os
from pathlib import Path
import time
from uuid import uuid4

from zenmoney_api import Client, HTTPError, ResponseError, TransportError, transactions as tx
from zenmoney_api.protocol import decode_json, encode_json

from . import predictions, storage
from .config import Settings
from .delivery import writer_lock
from .mirror import Mirror
from .snapshot import copy_snapshot


def save(path, value):
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(encode_json(value))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def semantic(item):
    return {k: v for k, v in item.items() if k != 'changed'}


def validate_plan(plan, approved):
    unsigned = {k: v for k, v in plan.items() if k != 'sha256'}
    if sha256(encode_json(unsigned)).hexdigest() != approved or plan.get('sha256') != approved:
        raise ValueError('Plan differs from the approved document')
    rows = plan['ready'] + plan['needs_review']
    removals, keeps, banks = ({r[key] for r in rows} for key in ('remove', 'keep', 'bank_id'))
    if not rows or len(removals) != len(rows) or len(keeps) != len(rows) or len(banks) != len(rows) or removals & keeps:
        raise ValueError('Plan must contain independent keep/remove pairs')
    return rows


def check_rows(db, rows):
    remote = predictions.entities(db, 'transaction')
    for row in rows:
        for key, snapshot in [('keep', 'keep_snapshot'), ('remove', 'copy_snapshot')]:
            actual = remote.get(row[key])
            if actual is None or semantic(actual) != semantic(row[snapshot]):
                raise ValueError('A reviewed record changed or disappeared; no deletion authorized')
        delivery = db.execute('SELECT * FROM deliveries WHERE bank_id=?', (row['bank_id'],)).fetchone()
        if not delivery or delivery['zen_id'] != row['remove'] or delivery['state'] != 'delivered':
            raise ValueError('Removal is not a confirmed local delivery')
        if db.execute('SELECT 1 FROM link_bank_members WHERE bank_id=?', (row['bank_id'],)).fetchone():
            raise ValueError('Bank operation already has another canonical group')
        if db.execute('SELECT 1 FROM link_zen_members WHERE zen_id IN (?,?)', (row['keep'], row['remove'])).fetchone():
            raise ValueError('A planned UUID belongs to another group')
    return remote


def verify(db, rows):
    remote = predictions.entities(db, 'transaction')
    return {'removed': sum(r['remove'] not in remote for r in rows),
            'preserved': sum(r['keep'] in remote and semantic(remote[r['keep']]) == semantic(r['keep_snapshot']) for r in rows),
            'total': len(rows)}


def execute(path, mirror, plan, approved, journal_path, *, clock=time.time):
    rows = validate_plan(plan, approved)
    with writer_lock(path) as acquired:
        if not acquired:
            raise RuntimeError('Delivery writer is active')
        mirror.refresh(force=True, transactions=True)
        if journal_path.exists():
            journal = decode_json(journal_path.read_bytes())
            if journal['approved_sha256'] != approved:
                raise ValueError('Journal belongs to another plan')
            with storage.transaction(path, read_only=True) as db:
                result = verify(db, rows)
            journal['verification'] = result
            save(journal_path, journal)
            return result  # A resumed command NEVER repeats or starts deletes.
        with storage.transaction(path, read_only=True) as db:
            if db.execute("SELECT 1 FROM capture_jobs WHERE capture_state IN ('planned','collecting') LIMIT 1").fetchone():
                raise RuntimeError('Stop active capture before maintenance')
            if db.execute("SELECT 1 FROM bank_processing WHERE state IN ('pending','prepared','uncertain') LIMIT 1").fetchone():
                raise RuntimeError('Resolve pending deliveries before maintenance')
            check_rows(db, rows)
        backup = journal_path.with_suffix('.before.sqlite3')
        copy_snapshot(path, backup)
        journal = {'approved_sha256': approved, 'backup': str(backup), 'batches': []}
        save(journal_path, journal)
        # Canonical replacements are durable before deletion. Their originals
        # remain the processing identity; retired duplicate UUIDs stay archived.
        with storage.transaction(path) as db:
            check_rows(db, rows)
            for row in rows:
                bank = db.execute('SELECT account_id FROM bank_transactions WHERE id=?', (row['bank_id'],)).fetchone()
                group = str(uuid4())
                db.execute('INSERT INTO link_groups VALUES (?,?,?,?,?)', (group, 'single', bank['account_id'], int(clock()), 'duplicate_cleanup'))
                db.execute('INSERT INTO link_bank_members VALUES (?,?)', (group, row['bank_id']))
                db.execute('INSERT INTO link_zen_members VALUES (?,?)', (group, row['keep']))
        for offset in range(0, len(rows), 20):
            batch = rows[offset:offset+20]
            mirror.refresh(force=True, transactions=True)
            with storage.transaction(path, read_only=True) as db:
                remote = predictions.entities(db, 'transaction')
                # Recheck every retained original, not only the current batch.
                if any(r['keep'] not in remote or semantic(remote[r['keep']]) != semantic(r['keep_snapshot']) for r in rows):
                    raise ValueError('A retained original changed during cleanup')
                if any(r['remove'] not in remote or semantic(remote[r['remove']]) != semantic(r['copy_snapshot']) for r in batch):
                    raise ValueError('A duplicate changed during cleanup')
                cursor = db.execute('SELECT cursor FROM zen_mirror_state WHERE id=1').fetchone()[0]
            removals = [tx.deletion('transaction', r['remove'], user=remote[r['remove']]['user'],
                now=max(int(clock()), remote[r['remove']].get('changed', 0)+1)) for r in batch]
            entry = {'deletions': removals, 'intent': True}
            journal['batches'].append(entry)
            save(journal_path, journal)  # Commit exact intent before network.
            try:
                mirror.client.delete(removals, cursor)
            except (HTTPError, ResponseError, TransportError) as exc:
                entry['error'] = type(exc).__name__
                save(journal_path, journal)
            mirror.refresh(force=True, transactions=True)
            with storage.transaction(path, read_only=True) as db:
                result = verify(db, batch)
            entry['verification'] = result
            save(journal_path, journal)
            if result['removed'] != len(batch) or result['preserved'] != len(batch):
                raise RuntimeError('Deletion not confirmed; use read-only verification')
            print('Confirmed duplicate removals:', offset+len(batch), 'of', len(rows), flush=True)
        with storage.transaction(path, read_only=True) as db:
            result = verify(db, rows)
        journal['verification'] = result
        save(journal_path, journal)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--approved-sha256', required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    with Client(settings.zenmoney_token, timeout=30) as client:
        mirror = Mirror(settings.database_path, client, settings.zenmoney_token, lambda: int(time.time()))
        result = execute(settings.database_path, mirror, decode_json(args.plan.read_bytes()), args.approved_sha256, args.journal)
        print('Cleanup verification:', result, flush=True)


if __name__ == '__main__':
    main()
