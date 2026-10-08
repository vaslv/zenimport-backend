"""Authoritative reconciliation groups. Legacy fields are compatibility projections."""
import time
import uuid
from decimal import Decimal


def create_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS reconciliation_groups (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('single','split','settlement')),
        bank_account TEXT NOT NULL REFERENCES bank_accounts(id), created_at INTEGER NOT NULL,
        origin TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS reconciliation_bank_members (
        group_id TEXT NOT NULL REFERENCES reconciliation_groups(id) ON DELETE CASCADE,
        bank_id TEXT NOT NULL UNIQUE REFERENCES bank_transactions(id), PRIMARY KEY(group_id,bank_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS reconciliation_zen_members (
        group_id TEXT NOT NULL REFERENCES reconciliation_groups(id) ON DELETE CASCADE,
        zen_id TEXT NOT NULL UNIQUE, PRIMARY KEY(group_id,zen_id))''')


def insert(db, bank_ids, zen_ids, kind, origin, identifier=None, state='linked'):
    from .service import fail
    if len(set(bank_ids)) != len(bank_ids) or len(set(zen_ids)) != len(zen_ids) or not bank_ids or not zen_ids:
        fail('Пустая группа или повтор участников')
    accounts = set()
    for bank_id in bank_ids:
        row = db.execute('SELECT bank_account FROM bank_transactions WHERE id=?', (bank_id,)).fetchone()
        if not row or db.execute('SELECT 1 FROM reconciliation_bank_members WHERE bank_id=?', (bank_id,)).fetchone():
            fail('Банковская операция не найдена или уже связана')
        accounts.add(row['bank_account'])
    if len(accounts) != 1:
        fail('Группа должна относиться к одному банковскому счёту')
    for zen_id in zen_ids:
        if db.execute('SELECT 1 FROM reconciliation_zen_members WHERE zen_id=?', (zen_id,)).fetchone():
            fail('Операция ZenMoney уже входит в другую группу')
    identifier = identifier or str(uuid.uuid4())
    db.execute('INSERT INTO reconciliation_groups VALUES (?,?,?,?,?)',
               (identifier, kind, accounts.pop(), int(time.time()), origin))
    db.executemany('INSERT INTO reconciliation_bank_members VALUES (?,?)', [(identifier, i) for i in bank_ids])
    db.executemany('INSERT INTO reconciliation_zen_members VALUES (?,?)', [(identifier, i) for i in zen_ids])
    # Kept for outbox/backward-compatible reads; never used as link ownership authority.
    legacy = zen_ids[0] if len(bank_ids) == len(zen_ids) == 1 else None
    db.executemany('UPDATE bank_transactions SET state=?,linked_id=? WHERE id=?', [(state, legacy, i) for i in bank_ids])
    return identifier


def migrate(db):
    create_schema(db)
    if db.execute('PRAGMA user_version').fetchone()[0] < 4:
        for row in db.execute("SELECT * FROM bank_transactions WHERE state IN ('linked','sent') ORDER BY id").fetchall():
            parts = [r['zen_id'] for r in db.execute('SELECT zen_id FROM split_links WHERE bank_id=? ORDER BY zen_id', (row['id'],))]
            ids = parts or ([row['linked_id']] if row['linked_id'] else [])
            if not ids:
                raise RuntimeError('Confirmed legacy transaction has no link; migration cancelled')
            insert(db, [row['id']], ids, 'split' if parts else 'single', 'migration-v3',
                   str(uuid.uuid5(uuid.NAMESPACE_URL, 'zenimport:group:'+row['id'])), row['state'])
        db.execute('DROP TABLE split_links')
        db.execute('''CREATE VIEW split_links AS SELECT b.bank_id,z.zen_id
            FROM reconciliation_bank_members b JOIN reconciliation_groups g ON g.id=b.group_id
            JOIN reconciliation_zen_members z ON z.group_id=g.id WHERE g.kind='split' ''')
        db.execute('PRAGMA user_version=4')


def read(db):
    groups = {r['id']: {**dict(r), 'bank_ids': [], 'zen_ids': []} for r in db.execute('SELECT * FROM reconciliation_groups')}
    for r in db.execute('SELECT * FROM reconciliation_bank_members ORDER BY bank_id'):
        groups[r['group_id']]['bank_ids'].append(r['bank_id'])
    for r in db.execute('SELECT * FROM reconciliation_zen_members ORDER BY zen_id'):
        groups[r['group_id']]['zen_ids'].append(r['zen_id'])
    return list(groups.values())


def unlink(db, bank_id):
    from .service import fail, audit
    group = next((g for g in read(db) if bank_id in g['bank_ids']), None)
    if not group:
        fail('Группа связи не найдена')
    if any(db.execute('SELECT state FROM bank_transactions WHERE id=?', (i,)).fetchone()['state'] != 'linked' for i in group['bank_ids']):
        fail('Отменить можно только локальную группу')
    db.execute('DELETE FROM reconciliation_groups WHERE id=?', (group['id'],))
    db.executemany("UPDATE bank_transactions SET state='pending',linked_id=NULL WHERE id=?", [(i,) for i in group['bank_ids']])
    audit(db, 'unlink_group', group)
    return group


def settlement_view(group, rows, transactions, account, tags):
    """Revalidate every member; one changed member invalidates the entire group."""
    banks = [rows[i] for i in group['bank_ids'] if i in rows]
    parts = []
    if len(banks) != len(group['bank_ids']) or len({b['date'] for b in banks}) != 1:
        return None, []
    if any(b['bank_account'] != group['bank_account'] for b in banks):
        return None, []
    day = banks[0]['date']; scale = 1 if account['currency'] == 'VND' else 100
    for identifier in group['zen_ids']:
        tx = transactions.get(identifier)
        if not tx or tx.get('deleted') or tx.get('hold') or tx.get('date') != day:
            return None, []
        target = account['zenmoney_account']
        if not target or target not in (tx.get('incomeAccount'), tx.get('outcomeAccount')):
            return None, []
        amount = ((Decimal(str(tx.get('income', 0))) if tx.get('incomeAccount') == target else 0) -
                  (Decimal(str(tx.get('outcome', 0))) if tx.get('outcomeAccount') == target else 0)) * scale
        parts.append({'id': identifier, 'date': day, 'amount_minor': str(amount),
                      'categories': [tags.get(t, 'Неизвестная категория') for t in tx.get('tag') or []],
                      'payee': tx.get('payee'), 'comment': tx.get('comment'),
                      'transfer': tx.get('incomeAccount') != tx.get('outcomeAccount')})
    if sum(b['amount_minor'] for b in banks) != sum(Decimal(p['amount_minor']) for p in parts):
        return None, []
    return {'id': None, 'date': day, 'payee': 'Взаиморасчёт', 'settlement': True, 'transfer': False,
            'categories': list(dict.fromkeys(c for p in parts for c in p['categories'])),
            'missing_category': any(not p['transfer'] and not p['categories'] for p in parts)}, parts


def confirm_settlement(db, data, rows):
    from .service import fail, get_row, mapped_account, audit
    from .day_review import proposals
    by_id = {r['id']: r for r in rows}
    if len(data.bank_ids)+len(data.zen_ids) < 3 or len(set(data.bank_ids)) != len(data.bank_ids) or len(set(data.zen_ids)) != len(data.zen_ids):
        fail('Выберите разные операции для составной группы')
    banks = [by_id.get(i) for i in data.bank_ids]
    if any(not r or r['state'] != 'pending' for r in banks) or len({r['bank_account'] for r in banks}) != 1 or len({r['date'] for r in banks}) != 1:
        fail('Нужны несопоставленные операции одного счёта и дня')
    account = get_row(db, 'bank_accounts', banks[0]['bank_account']);mapped_account(db, account)
    day = next(d for d in proposals(db, rows) if d['bank_account'] == account['id'] and d['date'] == banks[0]['date'])
    if data.snapshot != day['snapshot']:
        fail('Данные дня изменились; обновите список')
    zs = {z['id']: z for z in day['zen_rows'] if not z['linked_bank_id']}
    if any(i not in zs for i in data.zen_ids) or sum(r['amount_minor'] for r in banks) != sum(Decimal(zs[i]['amount_minor']) for i in data.zen_ids):
        fail('Суммы сторон не совпадают или записи ZenMoney уже заняты')
    identifier = insert(db, data.bank_ids, data.zen_ids, 'settlement', 'manual')
    audit(db, 'confirm_settlement', {'group_id': identifier, **data.model_dump()})
    return {'group_id': identifier, 'state': 'linked'}
