"""Offline verification of a legacy COPY, never an application or API writer."""
import argparse
from contextlib import closing
from decimal import Decimal
from hashlib import file_digest
import json
from pathlib import Path
import sqlite3

from zenmoney_api.protocol import KINDS
from . import legacy_archive as archive, migrate_legacy, storage
from .snapshot import copy_snapshot


def checksum(path):
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def require(condition):
    if not condition:
        raise RuntimeError('Offline migration verification failed')


def verify(source_path, target_path):
    checks = {}
    with closing(storage.connect_readonly(source_path)) as source, storage.transaction(target_path, read_only=True) as target:
        source.execute('BEGIN')
        digest, counts = archive.fingerprint(source)
        archive.verify_archive(target, digest, counts)
        checks['archived_rows'] = sum(counts.values())
        checks['source_tables'] = len(counts)
        for row in source.execute('SELECT * FROM bank_accounts'):
            account = target.execute('SELECT * FROM bank_accounts WHERE id=?', (row['id'],)).fetchone()
            require(account is not None and (account['bank'], account['source_key'], account['currency']) == (row['bank'], row['account'], row['currency']))
            require(account['selection'] == 'unconfigured' and account['last_complete_end'] is None)
            if row['zenmoney_account']:
                require(target.execute('SELECT 1 FROM account_bindings WHERE account_id=? AND zenmoney_account_id=?', (row['id'], row['zenmoney_account'])).fetchone())
        checks['accounts'] = source.execute('SELECT count(*) FROM bank_accounts').fetchone()[0]
        for row in source.execute('SELECT t.*,a.currency FROM bank_transactions t JOIN bank_accounts a ON a.id=t.bank_account'):
            actual = target.execute('SELECT * FROM bank_transactions WHERE id=?', (row['id'],)).fetchone()
            require(actual is not None)
            require((actual['account_id'],actual['external_id'],actual['local_date'],actual['currency'],actual['description'],actual['raw_json']) ==
                    (row['bank_account'],row['external_id'],row['date'],row['currency'],row['description'],row['raw']))
            require(Decimal(actual['amount']).scaleb(migrate_legacy.SCALES[row['currency']]) == abs(row['amount_minor']))
            require(actual['direction'] == ('income' if row['amount_minor'] > 0 else 'expense'))
            require(target.execute('SELECT 1 FROM bank_aliases WHERE account_id=? AND external_id=? AND bank_id=?',
                (row['bank_account'],row['external_id'],row['id'])).fetchone())
            state = target.execute('SELECT state FROM bank_processing WHERE bank_id=?', (row['id'],)).fetchone()[0]
            if row['state'] == 'ignored': require(state == 'suppressed')
            if row['state'] in ('linked','sent'): require(state in ('delivered','suppressed'))
        checks['bank_operations'] = source.execute('SELECT count(*) FROM bank_transactions').fetchone()[0]
        for old,new in [('reconciliation_groups','link_groups'),('reconciliation_bank_members','link_bank_members'),
                ('reconciliation_zen_members','link_zen_members'),('reconciliation_exclusions','zen_exclusions'),('recipient_rules','recipient_rules')]:
            # Exact key/content inclusion; legacy one-to-one links may add rows.
            expected = {tuple(row) for row in source.execute('SELECT * FROM '+archive.quote(old))}
            actual = {tuple(row) for row in target.execute('SELECT * FROM '+archive.quote(new))}
            require(expected <= actual); checks[old] = len(expected)
        entities = 0
        for kind in KINDS:
            table = 'zen_' + ('reminder_marker' if kind == 'reminderMarker' else kind)
            for row in source.execute('SELECT * FROM '+archive.quote(table)):
                actual = target.execute('SELECT data,deleted FROM zen_entities WHERE kind=? AND id=?',(kind,row['id'])).fetchone()
                require(actual is not None and tuple(actual) == (row['data'],row['deleted']))
                entities += 1
                if kind in ('transaction','reminder','reminderMarker'):
                    movement=target.execute('SELECT income,outcome FROM zen_movements WHERE kind=? AND id=?',(kind,row['id'])).fetchone()
                    require(tuple(movement) == (row['income'],row['outcome']))
                if row['deleted']:
                    require(target.execute('SELECT 1 FROM zen_tombstones WHERE kind=? AND entity_id=?',(kind,row['id'])).fetchone())
        checks['zen_entities'] = entities
        for row in source.execute('SELECT * FROM zen_deletions'):
            require(target.execute('SELECT 1 FROM zen_tombstones WHERE kind=? AND entity_id=?',(row['kind'],row['entity_id'])).fetchone())
        checks['deletions'] = source.execute('SELECT count(*) FROM zen_deletions').fetchone()[0]
        for row in source.execute('SELECT * FROM meta'):
            actual=target.execute('SELECT value FROM service_meta WHERE key=?',('legacy:'+row['key'],)).fetchone()
            require(actual is not None and actual[0] == row['value'])
        for row in source.execute('SELECT * FROM creation_jobs'):
            known=target.execute('''SELECT zen_id FROM deliveries WHERE bank_id=? UNION SELECT zen_id FROM delivery_attempts WHERE bank_id=?
                UNION SELECT zen_id FROM processing_receipts WHERE bank_id=?''',(row['bank_id'],)*3)
            require(row['zen_id'] in {item[0] for item in known})
            actual=target.execute('SELECT * FROM deliveries WHERE bank_id=? AND zen_id=?',(row['bank_id'],row['zen_id'])).fetchone()
            if actual is not None and actual['ever_attempted']:
                require(actual['payload'] == row['payload'])
        checks['creation_jobs'] = source.execute('SELECT count(*) FROM creation_jobs').fetchone()[0]
        require(target.execute('SELECT count(*) FROM coverage').fetchone()[0] == 0)
        require(target.execute('SELECT count(*) FROM delivery_authorizations').fetchone()[0] == 0)
        storage.check_integrity(target)
    return checks


def rehearse(source_copy: Path, destination: Path, restored: Path):
    before = checksum(source_copy)
    first = migrate_legacy.convert(source_copy, destination)
    checks = verify(source_copy, destination)
    with storage.transaction(destination, read_only=True) as target:
        initial = archive.fingerprint(target)[0]
    repeated = migrate_legacy.convert(source_copy, destination)
    require(repeated['repeated'])
    with storage.transaction(destination, read_only=True) as target:
        require(archive.fingerprint(target)[0] == initial)
    copy_snapshot(destination, restored)
    with storage.transaction(restored, read_only=True) as target:
        require(archive.fingerprint(target)[0] == initial)
    require(checksum(source_copy) == before)
    return {'source_sha256':before,'migration':first,'checks':checks,'repeat_identical':True,
            'restored_identical':True,'source_copy_unchanged':True,'network_used':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-copy',required=True,type=Path)
    parser.add_argument('--destination',required=True,type=Path)
    parser.add_argument('--restore-check',required=True,type=Path)
    arguments=parser.parse_args()
    try:
        result=rehearse(arguments.source_copy,arguments.destination,arguments.restore_check)
    except (RuntimeError,ValueError,sqlite3.Error,OSError,KeyError,TypeError):
        parser.exit(1,'Rehearsal failed; no live API was contacted. Inspect the private copies, not application logs.\n')
    print(json.dumps(result,sort_keys=True))


if __name__ == '__main__':main()
