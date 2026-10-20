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

from . import existing, ownership, predictions, storage
from .config import Settings
from .delivery import writer_lock
from .financial_math import exact_sum
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
    if 'groups' in plan:
        if 'ready' in plan or 'needs_review' in plan:
            raise ValueError('Mixed cleanup formats')
        groups = plan['groups']
    else:
        groups = [{'kind': 'single', 'bank_ids': [r['bank_id']],
                   'keeps': [r['keep_snapshot']], 'removals': [{'bank_id': r['bank_id'], 'snapshot': r['copy_snapshot']}]}
                  for r in plan['ready'] + plan['needs_review']]
        if any(r['keep'] != r['keep_snapshot']['id'] or r['remove'] != r['copy_snapshot']['id']
               for r in plan['ready'] + plan['needs_review']):
            raise ValueError('Snapshot identity differs from the plan')
    banks, keeps, removes = [], [], []
    for group in groups:
        bs, zs, rs = group['bank_ids'], group['keeps'], group['removals']
        shape = (len(bs), len(zs))
        valid = ((group['kind'] == 'single' and shape == (1, 1)) or
                 (group['kind'] == 'split' and len(bs) == 1 and len(zs) > 1) or
                 (group['kind'] == 'settlement' and min(shape) == 1 and max(shape) > 1))
        if not valid or sorted(bs) != sorted(r['bank_id'] for r in rs):
            raise ValueError('Each group must replace exactly one delivery per bank record')
        banks.extend(bs)
        keeps.extend(z['id'] for z in zs)
        removes.extend(r['snapshot']['id'] for r in rs)
    if not groups or any(len(set(v)) != len(v) for v in (banks, keeps, removes)) or set(keeps) & set(removes):
        raise ValueError('Cleanup groups must have independent identities')
    return groups


def check_snapshots(remote, snapshots):
    if any(item['id'] not in remote or semantic(remote[item['id']]) != semantic(item) for item in snapshots):
        raise ValueError('A reviewed record changed or disappeared; no deletion authorized')


def check_rows(db, groups):
    remote = predictions.entities(db, 'transaction')
    accounts = predictions.entities(db, 'account')
    for group in groups:
        snapshots = group['keeps'] + [r['snapshot'] for r in group['removals']]
        check_snapshots(remote, snapshots)
        banks = [dict(db.execute('SELECT * FROM bank_transactions WHERE id=?', (b,)).fetchone()) for b in group['bank_ids']]
        if len({b['account_id'] for b in banks}) != 1 or len({b['local_date'] for b in banks}) != 1:
            raise ValueError('A group must belong to one account and day')
        account = db.execute('SELECT * FROM bank_accounts WHERE id=?', (banks[0]['account_id'],)).fetchone()
        target = accounts.get(account['zenmoney_account_id'])
        if target is None or any(z['date'] != banks[0]['local_date'] or z['user'] != target['user'] for z in snapshots):
            raise ValueError('Reviewed target or date differs')
        values = [existing.movement(z, target['id'], target['instrument']) for z in group['keeps']]
        if None in values or exact_sum(values) != exact_sum(existing.signed(b) for b in banks):
            raise ValueError('Retained group does not equal signed bank movements')
        for row in group['removals']:
            delivery = db.execute('SELECT * FROM deliveries WHERE bank_id=?', (row['bank_id'],)).fetchone()
            if not delivery or delivery['zen_id'] != row['snapshot']['id'] or delivery['state'] != 'delivered':
                raise ValueError('Removal is not a confirmed local delivery')
            bank = next(b for b in banks if b['id'] == row['bank_id'])
            if existing.movement(row['snapshot'], target['id'], target['instrument']) != existing.signed(bank):
                raise ValueError('Duplicate differs from the bank movement')
            if db.execute('SELECT 1 FROM link_bank_members WHERE bank_id=?', (row['bank_id'],)).fetchone():
                raise ValueError('Bank operation already has another canonical group')
        if any(db.execute('SELECT 1 FROM link_zen_members WHERE zen_id=?', (z['id'],)).fetchone() for z in snapshots):
            raise ValueError('A planned UUID belongs to another group')
    return remote


def verify(db, groups):
    remote = predictions.entities(db, 'transaction')
    removals = [r['snapshot']['id'] for g in groups for r in g['removals']]
    keeps = [z for g in groups for z in g['keeps']]
    return {'removed': sum(z not in remote and bool(db.execute(
                "SELECT 1 FROM zen_tombstones WHERE kind='transaction' AND entity_id=?", (z,)).fetchone()) for z in removals),
            'preserved': sum(z['id'] in remote and semantic(remote[z['id']]) == semantic(z) for z in keeps),
            'total': len(removals)}


def execute(path, mirror, plan, approved, journal_path, *, clock=time.time):
    groups = validate_plan(plan, approved)
    removals = [r['snapshot'] for g in groups for r in g['removals']]
    keeps = [z for g in groups for z in g['keeps']]
    with writer_lock(path) as acquired:
        if not acquired:
            raise RuntimeError('Delivery writer is active')
        mirror.refresh(force=True, transactions=True)
        if journal_path.exists():
            journal = decode_json(journal_path.read_bytes())
            if journal['approved_sha256'] != approved:
                raise ValueError('Journal belongs to another plan')
            with storage.transaction(path, read_only=True) as db:
                result = verify(db, groups)
            journal['verification'] = result
            save(journal_path, journal)
            return result  # A resumed command NEVER repeats or starts deletes.
        with storage.transaction(path, read_only=True) as db:
            if db.execute("SELECT 1 FROM capture_jobs WHERE capture_state IN ('planned','collecting','paused') LIMIT 1").fetchone():
                raise RuntimeError('Stop active capture before maintenance')
            if db.execute("SELECT 1 FROM bank_processing WHERE state IN ('pending','prepared','uncertain') LIMIT 1").fetchone():
                raise RuntimeError('Resolve pending deliveries before maintenance')
            check_rows(db, groups)
        backup = journal_path.with_suffix('.before.sqlite3')
        copy_snapshot(path, backup)
        journal = {'approved_sha256': approved, 'backup': str(backup), 'groups': groups, 'batches': []}
        save(journal_path, journal)
        # The retained group is durable before retiring any duplicate UUID.
        with storage.transaction(path) as db:
            check_rows(db, groups)
            for group in groups:
                bank = db.execute('SELECT account_id FROM bank_transactions WHERE id=?', (group['bank_ids'][0],)).fetchone()
                target = db.execute('SELECT zenmoney_account_id FROM bank_accounts WHERE id=?', (bank['account_id'],)).fetchone()[0]
                for bank_id in group['bank_ids']:
                    for zen in group['keeps']:
                        ownership.remember(db, bank_id, zen['id'], target, 'duplicate_cleanup', int(clock()))
                identifier = str(uuid4())
                db.execute('INSERT INTO link_groups VALUES (?,?,?,?,?)', (identifier, group['kind'], bank['account_id'], int(clock()), 'duplicate_cleanup'))
                db.executemany('INSERT INTO link_bank_members VALUES (?,?)', ((identifier, b) for b in group['bank_ids']))
                db.executemany('INSERT INTO link_zen_members VALUES (?,?)', ((identifier, z['id']) for z in group['keeps']))
        for offset in range(0, len(removals), 20):
            batch = removals[offset:offset+20]
            mirror.refresh(force=True, transactions=True)
            with storage.transaction(path, read_only=True) as db:
                remote = predictions.entities(db, 'transaction')
                check_snapshots(remote, keeps + batch)
                cursor = db.execute('SELECT cursor FROM zen_mirror_state WHERE id=1').fetchone()[0]
            deletions = [tx.deletion('transaction', r['id'], user=remote[r['id']]['user'],
                now=max(int(clock()), remote[r['id']].get('changed', 0)+1)) for r in batch]
            entry = {'deletions': deletions, 'intent': True}
            journal['batches'].append(entry)
            save(journal_path, journal)  # Commit exact intent before network.
            try:
                mirror.client.delete(deletions, cursor)
            except (HTTPError, ResponseError, TransportError) as exc:
                entry['error'] = type(exc).__name__
                save(journal_path, journal)
            mirror.refresh(force=True, transactions=True)
            with storage.transaction(path, read_only=True) as db:
                result = verify(db, groups)
            entry['verification'] = result
            save(journal_path, journal)
            if result['removed'] != offset+len(batch) or result['preserved'] != len(keeps):
                raise RuntimeError('Deletion not confirmed; use read-only verification')
            print('Confirmed duplicate removals:', offset+len(batch), 'of', len(removals), flush=True)
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
