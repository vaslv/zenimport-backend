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


def candidates(db, row, account):
    if not account['zenmoney_account']:
        return []
    result = []
    for tx in entities(db, 'transaction'):
        if tx.get('deleted') or tx.get('hold') or not tx.get('date'):
            continue
        delta = abs((date.fromisoformat(tx['date']) - date.fromisoformat(row['date'])).days)
        if delta > 2:
            continue
        net = Decimal(0)
        if tx.get('incomeAccount') == account['zenmoney_account']:
            net += Decimal(str(tx.get('income', 0)))
        if tx.get('outcomeAccount') == account['zenmoney_account']:
            net -= Decimal(str(tx.get('outcome', 0)))
        if net * SCALES[account['currency']] == row['amount_minor']:
            result.append({'id': tx['id'], 'date': tx['date'], 'payee': tx.get('payee'),
                           'comment': tx.get('comment'), 'reason': 'Сумма и счёт совпадают; дата ±2 дня'})
    return result


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
        if tx.raw.reference_kind == 'fingerprint':
            # A changed description/balance is not automatically a new financial event.
            peers = db.execute('SELECT raw FROM bank_transactions WHERE bank_account=? AND date=? AND amount_minor=?',
                               (account_id, str(tx.date), tx.amount_minor)).fetchall()
            for peer in peers:
                original = json.loads(peer['raw'])
                if original.get('reference_kind') == 'fingerprint' and original['date'] == tx.raw.date:
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
    rows = []
    for r in db.execute('SELECT * FROM bank_transactions ORDER BY date DESC, id'):
        row = dict(r)
        account = get_row(db, 'bank_accounts', row['bank_account'])
        row.update(currency=account['currency'], candidates=candidates(db, row, account),
                   mapped=bool(account['zenmoney_account']))
        row['raw'] = json.loads(row['raw'])
        rows.append(row)
    return rows


def confirm(db, identifier, decision):
    row = get_row(db, 'bank_transactions', identifier)
    if row['state'] != 'pending':
        fail('Операция уже обработана; обновите список')
    account = get_row(db, 'bank_accounts', row['bank_account'])
    target = mapped_account(db, account)
    matches = candidates(db, row, account)
    if sorted(decision.reviewed_candidates) != sorted(x['id'] for x in matches):
        fail('Совпадения изменились после синхронизации; обновите список')
    if decision.action == 'link':
        if decision.match_id not in [x['id'] for x in matches]:
            fail('Выберите существующее совпадение')
        if db.execute("SELECT 1 FROM bank_transactions WHERE linked_id=? AND state IN ('linked','sent')",
                      (decision.match_id,)).fetchone():
            fail('Эта операция ZenMoney уже связана с банковской операцией')
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
