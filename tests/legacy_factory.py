"""Artificial schema-12 database. Never reads the user's financial data."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, uuid5


def ident(name):
    return str(uuid5(NAMESPACE_URL, 'fixture:' + name))


def insert(db, table, **data):
    columns = ','.join('"' + key + '"' for key in data)
    db.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in data)})', tuple(data.values()))


def entity(db, kind, name, **fields):
    identifier = ident(name)
    data = {'id': identifier, **fields}
    table = 'zen_' + kind
    projected = {'incomeAccount': 'income_account', 'outcomeAccount': 'outcome_account',
                 'incomeInstrument': 'income_instrument', 'outcomeInstrument': 'outcome_instrument'}
    columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
    values = {projected.get(k, k): v for k, v in data.items()
              if projected.get(k, k) in columns and k != 'id'}
    insert(db, table, id=identifier, data=json.dumps(data), run_id=1, **values)
    return identifier


def bank(db, name, *, account='vnd', state='pending', amount=-12345, linked=None):
    identifier = ident(name)
    raw = json.dumps({'date': '10/10/2026 19:19:33', 'reference': 'fixture-' + name,
                      'details': {'corresponsiveAccount': 'fixture-beneficiary', 'corresponsiveName': 'Fixture Shop'},
                      'unknown': {'text': 'preserve café', 'number': '9007199254740993.123456789012'}})
    insert(db, 'bank_transactions', id=identifier, bank_account=ident(account), external_id='external-' + name,
           date='2026-10-10', amount_minor=amount, description='Fixture ' + name, raw=raw, state=state, linked_id=linked)
    return identifier


def group(db, name, banks, zens, kind='single'):
    insert(db, 'reconciliation_groups', id=ident(name), kind=kind, bank_account=ident('vnd'), created_at=1700000000, origin='manual')
    db.executemany('INSERT INTO reconciliation_bank_members VALUES (?,?)', ((ident(name), b) for b in banks))
    db.executemany('INSERT INTO reconciliation_zen_members VALUES (?,?)', ((ident(name), z) for z in zens))
    db.executemany("UPDATE bank_transactions SET state='linked',linked_id=NULL WHERE id=?", ((b,) for b in banks))


def job(db, name, state, *, outbox=False, local=True, intent=False, retry=False):
    bank_id = bank(db, name, state='queued')
    zen_id = ident('zen-' + name)
    payload = {'id': zen_id, 'created': 1700000000, 'income': 0, 'outcome': 12345,
               'viewed': True, 'custom': {'exact': '0.123456789012345678901'}}
    raw = json.dumps(payload, indent=2)
    table, key = ('outbox', 'bank_transaction') if outbox else ('creation_jobs', 'bank_id')
    insert(db, table, **{key: bank_id}, zen_id=zen_id, payload=raw, state=state)
    for enabled, operation, data in [(local, 'creation_local', {'bank_id': bank_id, 'payload': payload}),
                                     (intent, 'creation_intent', {'bank_id': bank_id, 'payload': payload}),
                                     (retry, 'creation_retry_prepared', {'bank_id': bank_id, 'zen_id': zen_id})]:
        if enabled:
            insert(db, 'audit', at=1700000000, operation=operation, data=json.dumps(data))
    return bank_id, zen_id


def build(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(Path('fixtures/legacy-v12/schema.sql').read_text())
        insert(db, 'zen_sync_runs', id=1, received_at=1700000000, requested_cursor=0, response_cursor=100,
               origin='fixture', payload='{"serverTimestamp":100,"unknown":0.1234567890123456789012345}')
        db.execute("INSERT INTO meta VALUES ('zenmoney_cursor','100')")
        entity(db, 'account', 'zen-vnd', user=1, instrument=2, title='Fixture VND')
        entity(db, 'account', 'zen-usd', user=1, instrument=3, title='Fixture USD')
        category = entity(db, 'tag', 'groceries', title='Fixture groceries')
        for name, currency in [('vnd', 'VND'), ('usd', 'USD')]:
            insert(db, 'bank_accounts', id=ident(name), bank='vietinbank', account='fixture-account-' + name,
                   currency=currency, zenmoney_account=ident('zen-' + name))
        for name, amount, account in [('manual', -12345, 'vnd'), ('huge', -9007199254740993, 'vnd'),
                                      ('cents', 12345, 'usd'), ('ignored', -100, 'vnd')]:
            bank(db, name, amount=amount, account=account, state='ignored' if name == 'ignored' else 'pending')
        insert(db, 'creation_drafts', bank_id=ident('manual'), category_id=category, source='manual', updated_at=1700000001)
        insert(db, 'creation_drafts', bank_id=ident('huge'), category_id=category, source='auto', updated_at=1700000001)
        insert(db, 'recipient_rules', bank='vietinbank', name='fixture shop', direction='expense', category_id=category, updated_at=1700000001)
        for name, kind, bank_names, zen_names in [
            ('one', 'single', ['one-bank'], ['one-zen']),
            ('split', 'split', ['split-bank'], ['split-z1', 'split-z2']),
            ('settlement', 'settlement', ['settlement-b1', 'settlement-b2'], ['settlement-z']),
            ('transfer', 'single', ['transfer-b1', 'transfer-b2'], ['transfer-z']),
        ]:
            banks = [bank(db, b, account='usd' if b == 'transfer-b2' else 'vnd') for b in bank_names]
            zens = [entity(db, 'transaction', z, income='0', outcome='123.450000000001', date='2026-10-10', viewed=True) for z in zen_names]
            group(db, name, banks, zens, kind)
        for name in ('deleted', 'absent', 'edited'):
            identifier = bank(db, name, state='linked', linked=ident('zen-' + name))
            if name == 'deleted':
                insert(db, 'zen_deletions', id=1, run_id=1, kind='transaction', entity_id=ident('zen-deleted'), data='{"unknown":"preserve"}')
            elif name == 'edited':
                entity(db, 'transaction', 'zen-edited', income='12345', outcome='0', viewed=True)
            if name == 'absent':
                insert(db, 'zen_operation_drafts', zen_id=ident('zen-absent'), definition='{}', before_payload='{}', updated_at=1700000002)
        soft = entity(db, 'transaction', 'soft-zen', deleted=1, income='0', outcome='100')
        bank(db, 'soft', state='sent', linked=soft)
        insert(db, 'reconciliation_exclusions', zen_id=ident('excluded-zen'), created_at=1700000000)
        for name, state, kwargs in [
            ('prepared', 'prepared', {}), ('unknown', 'uncertain', {'intent': True}),
            ('retry', 'prepared', {'intent': True, 'retry': True}),
            ('unproven', 'prepared', {'local': False}),
            ('conflict', 'conflict', {'intent': True}),
            ('out-queued', 'queued', {'outbox': True}), ('out-sent', 'sent', {'outbox': True}),
            ('remote-edited', 'conflict', {'intent': True}),
        ]:
            b, z = job(db, name, state, **kwargs)
            if name == 'remote-edited':
                entity(db, 'transaction', 'zen-' + name, income='0', outcome='999', viewed=True)
        orphan = bank(db, 'orphan')
        insert(db, 'audit', at=1700000001, operation='creation_intent',
               data=json.dumps({'bank_id': orphan, 'payload': {'id': ident('orphan-attempt'), 'outcome': 1}}))
        db.execute('CREATE TABLE extra_unknown (n INTEGER, r REAL, t TEXT, b BLOB, missing TEXT)')
        db.execute('INSERT INTO extra_unknown VALUES (?,?,?,?,NULL)', (9007199254740993, 1.25, 'unknown\x00text', b'\x00\xff'))
        db.commit()
    return path
