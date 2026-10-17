"""Explicit schema-12 → independent storage conversion; no network or source writes."""
import argparse
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from zenmoney_api import transactions as tx
from zenmoney_api.protocol import KINDS, encode_json

from . import legacy_archive as archive, storage

SCALES = {'VND': 0, 'USD': 2, 'EUR': 2}


def uid(value):
    return str(uuid5(NAMESPACE_URL, 'zenimport:legacy:' + value))


def decode(value):
    return json.loads(value, parse_float=Decimal)


def money(minor, scale):
    if type(minor) is not int or not minor:
        raise RuntimeError('Legacy bank amount must be a nonzero integer')
    digits = str(abs(minor)).rjust(scale + 1, '0')
    return digits[:-scale] + '.' + digits[-scale:] if scale else digits


def occurred(raw, bank, local_date):
    if bank != 'vietinbank':
        return None
    for value in ((raw.get('details') or {}).get('processDate'), raw.get('date')):
        if not isinstance(value, str):
            continue
        for pattern in ('%d/%m/%Y %H:%M:%S', '%d-%m-%Y %H:%M:%S'):
            try:
                parsed = datetime.strptime(value, pattern)
                if parsed.date().isoformat() == local_date:
                    return parsed.replace(tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')).isoformat()
            except ValueError:
                pass
    return None


def import_accounts(source, target):
    for row in source.execute('SELECT * FROM bank_accounts'):
        if row['currency'] not in SCALES:
            raise RuntimeError('Unsupported legacy currency; conversion requires an explicit scale')
        connection = uid('connection:' + row['bank'])
        target.execute('INSERT OR IGNORE INTO connections VALUES (?,?,?,?,?,?,?,?)',
                       (connection, row['bank'], 'legacy-import', 'user_selected', 'legacy-v12',
                        'Asia/Ho_Chi_Minh' if row['bank'] == 'vietinbank' else None, '{}', 0))
        target.execute('''INSERT INTO bank_accounts
            (id,bank,identity_scope,source_key,currency,scale,label,masked_number,supported)
            VALUES (?,?,'bank',?,?,?,?,?,1)''',
                       (row['id'], row['bank'], row['account'], row['currency'], SCALES[row['currency']],
                        row['bank'] + ' ' + row['currency'], '…' + row['account'][-4:]))
        target.execute('INSERT INTO connection_accounts VALUES (?,?)', (connection, row['id']))
        if row['zenmoney_account']:
            target.execute('INSERT INTO account_bindings VALUES (?,?,?,?,?,?)',
                           (uid('binding:' + row['id']), row['id'], row['zenmoney_account'], None, 'legacy', 0))


def import_bank(source, target):
    for row in source.execute('''SELECT t.*,a.bank,a.currency FROM bank_transactions t
                               JOIN bank_accounts a ON a.id=t.bank_account'''):
        date.fromisoformat(row['date'])
        raw = decode(row['raw'])
        details = raw.get('details') or {}
        if row['state'] not in ('pending', 'queued', 'linked', 'sent', 'ignored'):
            raise RuntimeError('Unrecognized legacy bank state')
        kind = raw.get('reference_kind', 'bank')
        target.execute('INSERT INTO bank_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (row['id'], row['bank_account'], row['external_id'], kind, 'legacy-v12', row['date'],
                        occurred(raw, row['bank'], row['date']), 'income' if row['amount_minor'] > 0 else 'expense',
                        money(row['amount_minor'], SCALES[row['currency']]), row['currency'], row['description'],
                        details.get('corresponsiveAccount'), details.get('corresponsiveName'), row['raw'], 1))
        target.execute('INSERT INTO bank_aliases VALUES (?,?,?,?)',
                       (row['bank_account'], row['external_id'], 'legacy-v12', row['id']))
        target.execute('INSERT INTO bank_versions VALUES (?,?,?,?,?,?)',
                       (row['id'], 1, json.dumps(dict(row)), row['raw'], 0, 'legacy-v12'))
        ignored = row['state'] == 'ignored'
        target.execute('INSERT INTO bank_processing VALUES (?,?,?,?)',
                       (row['id'], 'suppressed' if ignored else 'pending', 'legacy_exclusion' if ignored else 'legacy_unprocessed', 0))
        if ignored:
            target.execute('INSERT INTO bank_exclusions VALUES (?,?,?)', (row['id'], 'legacy', 0))


def import_zen(source, target):
    for row in source.execute('SELECT * FROM zen_sync_runs'):
        target.execute('INSERT INTO zen_sync_runs VALUES (?,?,?,?,?,?)',
                       ('legacy:' + str(row['id']), row['received_at'], row['requested_cursor'],
                        row['response_cursor'], row['origin'], row['payload']))
    for kind in KINDS:
        name = 'zen_' + ('reminder_marker' if kind == 'reminderMarker' else kind)
        for row in source.execute('SELECT * FROM ' + archive.quote(name)):
            payload = decode(row['data'])
            target.execute('INSERT INTO zen_entities VALUES (?,?,?,?,?,?)',
                           (kind, row['id'], row['data'], row['changed'], row['deleted'],
                            'legacy:' + str(row['run_id']) if row['run_id'] is not None else None))
            if row['deleted']:
                target.execute('INSERT INTO zen_tombstones VALUES (?,?,?,?)', (kind, row['id'], 'legacy_soft_delete', row['data']))
            if kind not in ('transaction', 'reminder', 'reminderMarker'):
                continue
            fields = ['date' if kind != 'reminder' else 'start_date', 'income_account', 'outcome_account',
                      'income_instrument', 'outcome_instrument', 'income', 'outcome']
            # Numeric projections use exact source TEXT. Raw entity remains authoritative.
            for field in ('income', 'outcome'):
                if row[field] is not None and payload.get(field) is not None and Decimal(row[field]) != Decimal(str(payload[field])):
                    raise RuntimeError('Legacy monetary projection differs from raw entity')
            target.execute('INSERT INTO zen_movements VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (kind, row['id'], *(row[f] for f in fields),
                            row['viewed'] if kind == 'transaction' else None,
                            row['hold'] if kind == 'transaction' else None))
            tags = name + '_tags'
            owner = 'transaction_id' if kind == 'transaction' else 'owner_id'
            for tag in source.execute('SELECT * FROM ' + tags + ' WHERE ' + owner + '=?', (row['id'],)):
                target.execute('INSERT INTO zen_tags VALUES (?,?,?,?)', (kind, row['id'], tag['tag_id'], tag['position']))
    for row in source.execute('SELECT * FROM zen_deletions'):
        target.execute('INSERT INTO zen_deletions VALUES (?,?,?,?,?)',
                       ('legacy:' + str(row['id']), 'legacy:' + str(row['run_id']), row['kind'], row['entity_id'], row['data']))
        target.execute('INSERT OR IGNORE INTO zen_tombstones VALUES (?,?,?,?)',
                       (row['kind'], row['entity_id'], 'legacy_hard_delete', row['data']))
    for row in source.execute('SELECT * FROM meta'):
        target.execute('INSERT INTO service_meta VALUES (?,?)', ('legacy:' + row['key'], row['value']))


def set_processing(target, bank_id, state, reason):
    old = target.execute('SELECT state FROM bank_processing WHERE bank_id=?', (bank_id,)).fetchone()
    if old is None:
        raise RuntimeError('Legacy processing refers to an unknown bank operation')
    if old[0] == 'suppressed' or (old[0] == 'delivered' and state != 'suppressed'):
        return
    target.execute('UPDATE bank_processing SET state=?,reason=? WHERE bank_id=?', (state, reason, bank_id))


def deleted(target, identifier):
    return bool(target.execute("SELECT 1 FROM zen_tombstones WHERE kind='transaction' AND entity_id=?", (identifier,)).fetchone())


def receipt(target, bank_id, zen_id, origin, at):
    target.execute('INSERT OR IGNORE INTO processing_receipts VALUES (?,?,?,?)', (bank_id, zen_id, origin, at))
    set_processing(target, bank_id, 'suppressed' if deleted(target, zen_id) else 'delivered', origin)


def import_links(source, target):
    for row in source.execute('SELECT * FROM reconciliation_groups'):
        target.execute('INSERT INTO link_groups VALUES (?,?,?,?,?)', tuple(row))
        banks = list(source.execute('SELECT bank_id FROM reconciliation_bank_members WHERE group_id=?', (row['id'],)))
        zens = list(source.execute('SELECT zen_id FROM reconciliation_zen_members WHERE group_id=?', (row['id'],)))
        if not banks or not zens:
            raise RuntimeError('Legacy reconciliation group is incomplete')
        target.executemany('INSERT INTO link_bank_members VALUES (?,?)', ((row['id'], b[0]) for b in banks))
        target.executemany('INSERT INTO link_zen_members VALUES (?,?)', ((row['id'], z[0]) for z in zens))
        for bank in banks:
            for zen in zens:
                receipt(target, bank[0], zen[0], 'legacy_confirmed_group', row['created_at'])
    # Older 1:1 confirmations may predate group tables. Never forget them.
    for row in source.execute("SELECT * FROM bank_transactions WHERE state IN ('linked','sent')"):
        if not target.execute('SELECT 1 FROM link_bank_members WHERE bank_id=?', (row['id'],)).fetchone():
            if not row['linked_id']:
                raise RuntimeError('Legacy processed operation has no confirmed identity')
            identifier = uid('link:' + row['id'])
            target.execute('INSERT INTO link_groups VALUES (?,?,?,?,?)', (identifier, 'single', row['bank_account'], 0, 'legacy_confirmed'))
            target.execute('INSERT INTO link_bank_members VALUES (?,?)', (identifier, row['id']))
            target.execute('INSERT INTO link_zen_members VALUES (?,?)', (identifier, row['linked_id']))
            receipt(target, row['id'], row['linked_id'], 'legacy_confirmed_link', 0)
    target.executemany('INSERT INTO zen_exclusions VALUES (?,?)', source.execute('SELECT * FROM reconciliation_exclusions'))


def import_choices(source, target):
    target.executemany('INSERT INTO recipient_rules VALUES (?,?,?,?,?)', source.execute('SELECT * FROM recipient_rules'))
    for table in ('creation_drafts', 'category_drafts'):
        for row in source.execute('SELECT * FROM ' + table):
            pending = target.execute('SELECT state FROM bank_processing WHERE bank_id=?', (row['bank_id'],)).fetchone()
            source_kind = row['source'] if table == 'creation_drafts' else 'manual'
            if pending[0] in ('delivered', 'suppressed') or source_kind != 'manual':
                continue
            valid = target.execute("SELECT 1 FROM zen_entities WHERE kind='tag' AND id=? AND deleted=0", (row['category_id'],)).fetchone()
            if valid:
                target.execute('INSERT OR IGNORE INTO category_choices VALUES (?,?,?,?)',
                               (row['bank_id'], row['category_id'], 'manual', row['updated_at']))


def rebuild_unattempted(target, bank_id, identifier, original):
    bank = target.execute('SELECT * FROM bank_transactions WHERE id=?', (bank_id,)).fetchone()
    binding = target.execute('SELECT zenmoney_account_id FROM account_bindings WHERE account_id=?', (bank['account_id'],)).fetchone()
    remote = target.execute("SELECT data FROM zen_entities WHERE kind='account' AND id=? AND deleted=0", (binding[0],)).fetchone() if binding else None
    if not remote:
        return None
    account = decode(remote[0])
    choice = target.execute("SELECT category_id FROM category_choices WHERE bank_id=? AND source='manual'", (bank_id,)).fetchone()
    builder = tx.income if bank['direction'] == 'income' else tx.expense
    try:
        return encode_json(builder(id=identifier, now=original.get('created', 0), user=account['user'],
            date=bank['local_date'], account=account['id'], instrument=account['instrument'],
            amount=Decimal(bank['amount']), categories=[choice[0]] if choice else None,
            payee=bank['beneficiary_name'], comment=bank['description'], viewed=False)).decode()
    except (KeyError, TypeError, ValueError):
        return None


def import_history(source, target):
    for row in source.execute("SELECT * FROM audit WHERE operation='creation_intent' ORDER BY id"):
        data = decode(row['data'])
        payload = data['payload']
        target.execute('INSERT INTO delivery_attempts VALUES (?,?,?,?,?,?)',
                       ('legacy:audit:' + str(row['id']), data['bank_id'], payload['id'], row['at'], 'legacy_unknown', encode_json(payload).decode()))
        set_processing(target, data['bank_id'], 'uncertain', 'legacy_write_attempt')
    # A former editor could unlink/reopen a previously verified payment. The
    # historical confirmation still proves it must never be imported as new.
    for row in source.execute("SELECT * FROM audit WHERE operation IN ('creation_verified','sent','confirm') ORDER BY id"):
        data = decode(row['data'])
        if row['operation'] == 'creation_verified':
            bank_id, zen_id = data['bank_id'], data['zen_id']
        elif row['operation'] == 'sent':
            bank_id, zen_id = data['id'], data['zen_id']
        elif data.get('action') == 'link':
            bank_id, zen_id = data['id'], data['match_id']
        else:
            continue
        receipt(target, bank_id, zen_id, 'legacy:audit:' + row['operation'], row['at'])


def import_deliveries(source, target):
    # Consult history, because retry preparation reset the old state to 'prepared'.
    local_ids, retry_ids = set(), set()
    for event in source.execute("SELECT operation,data FROM audit WHERE operation IN ('creation_local','creation_retry_prepared')"):
        data = decode(event['data'])
        pair = (data.get('bank_id'), data.get('zen_id') or data.get('payload', {}).get('id'))
        (retry_ids if event['operation'] == 'creation_retry_prepared' else local_ids).add(pair)
    for table, key, initial in (('creation_jobs', 'bank_id', 'prepared'), ('outbox', 'bank_transaction', 'queued')):
        for row in source.execute('SELECT * FROM ' + table):
            bank_id, identifier = row[key], row['zen_id']
            payload = row['payload']
            original = decode(payload)
            if original.get('id') != identifier:
                raise RuntimeError('Legacy delivery UUID differs from payload')
            if target.execute('SELECT 1 FROM deliveries WHERE bank_id=? OR zen_id=?', (bank_id, identifier)).fetchone():
                raise RuntimeError('Conflicting legacy delivery identities')
            # A removed previous job may have another UUID. It is still a write
            # attempt for this payment and must block blindly sending the new one.
            historical_attempt = target.execute('SELECT 1 FROM delivery_attempts WHERE bank_id=?', (bank_id,)).fetchone()
            retry = (bank_id, identifier) in retry_ids
            local = table == 'outbox' or (bank_id, identifier) in local_ids
            if row['state'] not in ('prepared', 'queued', 'uncertain', 'conflict', 'sent'):
                raise RuntimeError('Unrecognized legacy delivery state')
            attempted = bool(historical_attempt or retry or row['state'] != initial or not local)
            processed = target.execute('SELECT state FROM bank_processing WHERE bank_id=?', (bank_id,)).fetchone()[0]
            remote_exists = target.execute("SELECT 1 FROM zen_entities WHERE kind='transaction' AND id=?", (identifier,)).fetchone()
            if deleted(target, identifier) or processed == 'suppressed':
                state = 'suppressed'
            elif remote_exists or processed == 'delivered' or row['state'] == 'sent':
                state = 'delivered'
            else:
                state = 'uncertain' if attempted else 'prepared'
            origin = 'legacy:' + table
            needs_rebuild = 0
            if not attempted and state == 'prepared':
                rebuilt = rebuild_unattempted(target, bank_id, identifier, original)
                if rebuilt is not None:
                    payload, origin = rebuilt, origin + ':rebuilt'
                else:
                    # Keep intent, but block sending until B5 can safely rebuild it.
                    origin += ':rebuild_required'
                    needs_rebuild = 1
            target.execute('INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,?,?)',
                           (bank_id, identifier, payload, state, int(attempted), origin, None, 0, needs_rebuild))
            set_processing(target, bank_id, state, 'legacy_delivery')
            if state == 'delivered':
                receipt(target, bank_id, identifier, 'legacy:' + table, 0)
    # Preserve unknown attempts even when an old reopen removed the current job.
    for row in source.execute("SELECT id,state FROM bank_transactions WHERE state='queued'"):
        state = target.execute('SELECT state FROM bank_processing WHERE bank_id=?', (row['id'],)).fetchone()[0]
        if state == 'pending':
            raise RuntimeError('Legacy queued operation has no durable delivery identity')


def convert(source_path: Path, destination: Path):
    source_path, destination = source_path.resolve(), destination.absolute()
    if destination.resolve() == source_path or (destination.exists() and os.path.samefile(source_path, destination)):
        raise RuntimeError('Conversion requires a separate destination')
    with closing(storage.connect_readonly(source_path)) as source:
        source.execute('BEGIN')
        if source.execute('PRAGMA application_id').fetchone()[0] != 0 or source.execute('PRAGMA user_version').fetchone()[0] != 12:
            raise RuntimeError('Only legacy schema 12 is supported')
        storage.check_integrity(source)
        digest, counts = archive.fingerprint(source)
        if destination.exists():
            with storage.transaction(destination, read_only=True) as target:
                previous = target.execute('SELECT * FROM legacy_imports WHERE digest=?', (digest,)).fetchone()
                if not previous:
                    raise RuntimeError('Destination already exists; it will not be overwritten')
                archive.verify_archive(target, digest, counts)
                storage.check_integrity(target)
                return json.loads(previous['report_json']) | {'repeated': True}
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='.legacy-conversion-', dir=destination.parent) as temporary:
            staged = Path(temporary) / 'converted.sqlite3'
            storage.initialize(staged)
            with storage.transaction(staged) as target:
                report = {'source_version': 12, 'storage_version': storage.SCHEMA_VERSION, 'archived_rows': sum(counts.values()), 'tables': len(counts), 'repeated': False}
                target.execute('INSERT INTO legacy_imports VALUES (?,?,?,?,?)',
                               (digest, int(time.time()), 12, json.dumps(counts, sort_keys=True), json.dumps(report)))
                archive.copy_archive(source, target, digest)
                import_accounts(source, target)
                import_bank(source, target)
                import_zen(source, target)
                import_links(source, target)
                import_history(source, target)
                import_choices(source, target)
                import_deliveries(source, target)
                from .ownership import backfill
                backfill(target)
                archive.verify_archive(target, digest, counts)
                storage.check_integrity(target)
                report['processing_states'] = dict(target.execute('SELECT state,count(*) FROM bank_processing GROUP BY state'))
                report['delivery_states'] = dict(target.execute('SELECT state,count(*) FROM deliveries GROUP BY state'))
                report['normalized_rows'] = {table: target.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in (
                    'bank_accounts', 'bank_transactions', 'bank_aliases', 'bank_versions', 'link_groups', 'link_bank_members',
                    'link_zen_members', 'processing_receipts', 'processing_targets', 'delivery_attempts', 'zen_entities', 'zen_tombstones',
                    'bank_exclusions', 'zen_exclusions', 'category_choices', 'recipient_rules')}
                target.execute('UPDATE legacy_imports SET report_json=? WHERE digest=?', (json.dumps(report, sort_keys=True), digest))
            # Last connection closed/checkpointed; publish atomically without overwriting a racing writer.
            with closing(storage.configured(sqlite3.connect(staged))) as db:
                if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
                    raise RuntimeError('Cannot checkpoint converted database')
            with staged.open('rb') as file:
                os.fsync(file.fileno())
            os.link(staged, destination)
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = convert(args.source, args.destination)
    except (RuntimeError, ValueError, sqlite3.Error, OSError, KeyError, TypeError):
        # Do not echo source rows, SQL, paths, tokens or payloads to container logs.
        parser.exit(1, 'Legacy conversion failed; destination was not overwritten. Check schema and integrity.\n')
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
