import json
import time
import uuid
from datetime import date
from decimal import Decimal
from fastapi import HTTPException
from .db import entities

SCALES = {'VND': 1, 'USD': 100, 'EUR': 100}


def fail(message, code=409):
    raise HTTPException(code, message)


def audit(db, operation, data):
    db.execute('INSERT INTO audit(at,operation,data) VALUES (?,?,?)',
               (int(time.time()), operation, json.dumps(data)))


def get_row(db, table, identifier):
    # table is always an internal constant, never request input.
    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (identifier,)).fetchone()
    if row is None:
        fail('Запись не найдена', 404)
    return dict(row)


def get_entity(db, kind, identifier):
    row = db.execute('SELECT data FROM zen_entities WHERE kind=? AND id=?', (kind, identifier)).fetchone()
    if row is None:
        fail('Сначала синхронизируйте ZenMoney; объект не найден')
    return json.loads(row['data'])


def mapped_account(db, account):
    if not account['zenmoney_account']:
        fail('Сначала сопоставьте банковский счёт')
    target = get_entity(db, 'account', account['zenmoney_account'])
    instrument = get_entity(db, 'instrument', str(target['instrument']))
    if target.get('archive') or target.get('deleted') or instrument.get('shortTitle') != account['currency']:
        fail('Счёт архивный или валюта не совпадает')
    return target


def candidate_index(db, accounts, transactions=None):
    """One request-local index; never reused across synchronization/confirmation."""
    scales = {a['zenmoney_account']: SCALES[a['currency']] for a in accounts if a['zenmoney_account']}
    index = {}
    if not scales:
        return index
    for position, tx in enumerate(entities(db, 'transaction') if transactions is None else transactions):
        if tx.get('deleted') or tx.get('hold') or not tx.get('date'):
            continue
        for account_id in {tx.get('incomeAccount'), tx.get('outcomeAccount')} & scales.keys():
            net = Decimal(0)
            if tx.get('incomeAccount') == account_id:
                net += Decimal(str(tx.get('income', 0)))
            if tx.get('outcomeAccount') == account_id:
                net -= Decimal(str(tx.get('outcome', 0)))
            key = (account_id, date.fromisoformat(tx['date']).toordinal(), net * scales[account_id])
            match = {'id': tx['id'], 'date': tx['date'], 'payee': tx.get('payee'),
                     'comment': tx.get('comment'), 'reason': 'Сумма и счёт совпадают; дата ±2 дня'}
            index.setdefault(key, []).append((position, match))
    return index


def candidates(db, row, account, index=None):
    if not account['zenmoney_account']:
        return []
    if index is None:
        index = candidate_index(db, [account])
    day = date.fromisoformat(row['date']).toordinal()
    found = []
    for delta in range(-2, 3):
        found.extend(index.get((account['zenmoney_account'], day + delta, row['amount_minor']), []))
    return [match for _, match in sorted(found, key=lambda item: item[0])]


def import_bank(db, data):
    account_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'zenbridge:{data.bank}:{data.account}'))
    db.execute('INSERT OR IGNORE INTO bank_accounts(id,bank,account,currency) VALUES (?,?,?,?)',
               (account_id, data.bank, data.account, data.currency))
    account = get_row(db, 'bank_accounts', account_id)
    if account['currency'] != data.currency:
        fail('Валюта существующего банковского счёта отличается')
    added = 0
    for tx in data.transactions:
        if tx.amount_minor == 0:
            fail('Нулевые операции не поддерживаются', 422)
        old = db.execute('SELECT * FROM bank_transactions WHERE bank_account=? AND external_id=?',
                         (account_id, tx.external_id)).fetchone()
        if old:
            if (old['date'], old['amount_minor'], old['description']) != (str(tx.date), tx.amount_minor, tx.description):
                fail('Операция с тем же банковским ID изменилась; импорт отменён')
            continue
        if data.bank == 'vietinbank' or tx.raw.reference_kind == 'fingerprint':
            # Do not silently duplicate a DOM import when switching to bank IDs.
            peers = db.execute('SELECT raw FROM bank_transactions WHERE bank_account=? AND date=? AND amount_minor=?',
                               (account_id, str(tx.date), tx.amount_minor)).fetchall()
            for peer in peers:
                original = json.loads(peer['raw'])
                if (original.get('reference_kind') == 'fingerprint' or tx.raw.reference_kind == 'fingerprint') and original['date'] == tx.raw.date:
                    fail('Неоднозначный повтор операции без банковского ID: то же время и сумма. Нужна ручная сверка; импорт отменён')
        db.execute('INSERT INTO bank_transactions(id,bank_account,external_id,date,amount_minor,description,raw) VALUES (?,?,?,?,?,?,?)',
                   (str(uuid.uuid4()), account_id, tx.external_id, str(tx.date), tx.amount_minor,
                    tx.description, tx.raw.model_dump_json()))
        added += 1
    import_id = str(uuid.uuid4())
    db.execute('INSERT INTO imports VALUES (?,?,?,?,?)',
               (import_id, int(time.time()), account_id, data.adapter_version, data.model_dump_json()))
    audit(db, 'import', {'id': import_id, 'added': added})
    return {'import_id': import_id, 'added': added, 'duplicates': len(data.transactions) - added}


def review(db):
    accounts = {r['id']: dict(r) for r in db.execute('SELECT * FROM bank_accounts')}
    transactions = entities(db, 'transaction')
    index = candidate_index(db, accounts.values(), transactions)
    split_ids = {}
    for link in db.execute('SELECT bank_id,zen_id FROM split_links ORDER BY zen_id'):
        split_ids.setdefault(link['bank_id'], []).append(link['zen_id'])
    rows = []
    for r in db.execute('SELECT * FROM bank_transactions ORDER BY date DESC, id'):
        row = dict(r)
        account = accounts[row['bank_account']]
        row.update(currency=account['currency'], candidates=candidates(db, row, account, index),
                   mapped=bool(account['zenmoney_account']))
        row['raw'] = json.loads(row['raw'])
        row['split_ids'] = split_ids.get(row['id'], [])
        rows.append(row)
    from .reconciliation import enrich
    from .db import entities as read_entities
    return enrich(rows, accounts, transactions, read_entities(db, 'tag'))


def confirm(db, identifier, decision, *, index=None):
    row = get_row(db, 'bank_transactions', identifier)
    if row['state'] != 'pending':
        fail('Операция уже обработана; обновите список')
    account = get_row(db, 'bank_accounts', row['bank_account'])
    target = mapped_account(db, account)
    matches = candidates(db, row, account, index)
    if sorted(decision.reviewed_candidates) != sorted(x['id'] for x in matches):
        fail('Совпадения изменились после синхронизации; обновите список')
    if decision.action == 'link':
        if decision.match_id not in [x['id'] for x in matches]:
            fail('Выберите существующее совпадение')
        if db.execute("SELECT 1 FROM bank_transactions WHERE linked_id=? AND state IN ('linked','sent')",
                      (decision.match_id,)).fetchone():
            fail('Эта операция ZenMoney уже связана с банковской операцией')
        if db.execute('SELECT 1 FROM split_links WHERE zen_id=?', (decision.match_id,)).fetchone():
            fail('Эта запись ZenMoney уже входит в составную связь')
        db.execute("UPDATE bank_transactions SET state='linked', linked_id=? WHERE id=?", (decision.match_id, identifier))
    elif decision.action == 'ignore':
        db.execute("UPDATE bank_transactions SET state='ignored' WHERE id=?", (identifier,))
    else:
        if decision.category:
            get_entity(db, 'tag', decision.category)
        now = int(time.time())
        amount = Decimal(abs(row['amount_minor'])) / SCALES[account['currency']]
        if Decimal(str(float(amount))) != amount:
            fail('Сумма не может быть точно представлена для API')
        zen_id = str(uuid.uuid4())
        payload = {'id': zen_id, 'user': target['user'], 'created': now, 'changed': now,
                   'deleted': False, 'hold': False, 'date': row['date'],
                   'incomeAccount': target['id'], 'outcomeAccount': target['id'],
                   'incomeInstrument': target['instrument'], 'outcomeInstrument': target['instrument'],
                   'income': float(amount) if row['amount_minor'] > 0 else 0,
                   'outcome': float(amount) if row['amount_minor'] < 0 else 0,
                   'comment': row['description'], 'tag': [decision.category] if decision.category else None}
        db.execute('INSERT INTO outbox(bank_transaction,zen_id,payload) VALUES (?,?,?)',
                   (identifier, zen_id, json.dumps(payload)))
        db.execute("UPDATE bank_transactions SET state='queued', linked_id=? WHERE id=?", (zen_id, identifier))
    audit(db, 'confirm', {'id': identifier, **decision.model_dump()})
    return {'state': get_row(db, 'bank_transactions', identifier)['state']}


def same_transaction(expected, actual):
    keys = ('id', 'date', 'incomeAccount', 'outcomeAccount', 'incomeInstrument',
            'outcomeInstrument', 'income', 'outcome', 'deleted', 'comment', 'tag')
    return all(expected.get(k) == actual.get(k) for k in keys)
