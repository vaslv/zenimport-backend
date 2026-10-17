"""Persist the bank-side target before history or account settings can change."""
from collections import defaultdict

from zenmoney_api.protocol import decode_json


KNOWN = """SELECT bank_id,zen_id FROM processing_receipts
    UNION SELECT bank_id,zen_id FROM deliveries WHERE ever_attempted=1
    UNION SELECT bank_id,zen_id FROM delivery_attempts
    UNION SELECT b.bank_id,z.zen_id FROM link_bank_members b
          JOIN link_zen_members z ON z.group_id=b.group_id"""


def remember(db, bank, zen, target, origin, now):
    prior = db.execute('SELECT target_id FROM processing_targets WHERE bank_id=? AND zen_id=?', (bank, zen)).fetchone()
    if prior:
        if prior[0] != target:
            raise RuntimeError('Known bank/ZenMoney identity has another target')
        return
    db.execute('INSERT INTO processing_targets VALUES (?,?,?,?,?)', (bank, zen, target, origin, now))


def backfill(db):
    """Only durable request/authorization evidence; never mutable mobile data.

    Missing evidence stays missing. The matching stage blocks the affected
    account/day rather than guessing a target and creating another payment.
    """
    unknown = list(db.execute('SELECT k.bank_id,k.zen_id FROM (' + KNOWN + ''') k
        LEFT JOIN processing_targets p ON p.bank_id=k.bank_id AND p.zen_id=k.zen_id
        WHERE p.bank_id IS NULL'''))
    for bank, zen in unknown:
        proofs = set()
        for payload, in db.execute('''SELECT payload FROM deliveries WHERE bank_id=? AND zen_id=? AND ever_attempted=1
            UNION SELECT payload FROM delivery_attempts WHERE bank_id=? AND zen_id=?''', (bank, zen, bank, zen)):
            data = decode_json(payload.encode())
            # Import-created ordinary expenses/incomes use one account. An old
            # editor's transfer has two sides and needs separate authorization.
            if isinstance(data, dict) and data.get('incomeAccount') == data.get('outcomeAccount'):
                target = data.get('incomeAccount')
                if isinstance(target, str) and target:
                    proofs.add(target)
        authorization = db.execute('''SELECT target_id,authorized_at FROM delivery_authorizations
            WHERE bank_id=?''', (bank,)).fetchone()
        # These links were created by v5's validated intent. Terminal processing
        # prevents authorize_job from rewriting the corresponding authorization.
        adopted = db.execute('''SELECT 1 FROM processing_receipts WHERE bank_id=? AND zen_id=?
            AND origin IN ('bank_reference','day_amount','day_amount_order')
            UNION SELECT 1 FROM link_bank_members b JOIN link_groups g ON g.id=b.group_id
            JOIN link_zen_members z ON z.group_id=g.id WHERE b.bank_id=? AND z.zen_id=?
            AND g.origin IN ('day_amount_group')''', (bank, zen, bank, zen)).fetchone()
        attempted = db.execute('''SELECT 1 FROM deliveries WHERE bank_id=? AND zen_id=? AND ever_attempted=1
            UNION SELECT 1 FROM delivery_attempts WHERE bank_id=? AND zen_id=?''', (bank, zen, bank, zen)).fetchone()
        if authorization and (adopted or attempted):
            proofs.add(authorization['target_id'])
        if len(proofs) == 1:
            remember(db, bank, zen, proofs.pop(), 'durable_history', authorization['authorized_at'] if authorization else 0)


def claims(db):
    known = list(db.execute(KNOWN))
    targets = {(r[0], r[1]): r[2] for r in db.execute('SELECT bank_id,zen_id,target_id FROM processing_targets')}
    exact, unknown = set(), set()
    for bank, zen in known:
        target = targets.get((bank, zen))
        if target is None:
            unknown.add(zen)
        else:
            exact.add((zen, target))
    return exact, unknown


def blocked(db, candidates):
    """Unresolved historical ownership reserves only the affected account/days."""
    _, unknown = claims(db)
    days = defaultdict(set)
    for zen in unknown:
        row = db.execute("SELECT data FROM zen_entities WHERE kind='transaction' AND id=? AND deleted=0", (zen,)).fetchone()
        if not row or db.execute('SELECT 1 FROM zen_exclusions WHERE zen_id=?', (zen,)).fetchone():
            continue
        tx = decode_json(row[0].encode())
        for side in ('income', 'outcome'):
            if tx.get(side):
                days[tx.get(side+'Account')].add(tx.get('date'))
    return {bank['id'] for bank, account in candidates if bank['local_date'] in days[account['id']]}
