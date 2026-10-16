"""Account/day/amount reconciliation before creating another remote UUID."""
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
import re

from . import predictions


def reference(bank):
    # This namespace is defined by the VietinBank connector. Fingerprints and
    # generic descriptions are not remote identities.
    value = bank['external_id']
    if bank['identity_kind'] == 'bank' and value.startswith('vib:id:'):
        token = value[len('vib:id:'):]
        if re.fullmatch(r'[A-Za-z0-9]{6,128}', token):
            return token
    return None


def matches(db, candidates):
    """References first, then count-aware chronological account/day/amount pairs.

    candidates contains (authorized bank revision, validated remote account).
    The caller persists accepted identities atomically with delivery intent.
    """
    if not candidates:
        return {}
    # Ownership belongs to the bank-side account. One remote transfer can
    # legitimately represent an outgoing leg in bank A and an incoming leg in B.
    claimed = {tuple(row) for row in db.execute('''WITH known AS (
        SELECT bank_id,zen_id FROM processing_receipts
        UNION SELECT bank_id,zen_id FROM deliveries WHERE ever_attempted=1
        UNION SELECT bank_id,zen_id FROM delivery_attempts
        UNION SELECT b.bank_id,z.zen_id FROM link_bank_members b JOIN link_zen_members z ON z.group_id=b.group_id),
        bindings AS (SELECT id account_id,zenmoney_account_id target FROM bank_accounts
        UNION SELECT account_id,zenmoney_account_id FROM account_bindings)
        SELECT DISTINCT k.zen_id,a.target FROM known k JOIN bank_transactions b ON b.id=k.bank_id
        JOIN bindings a ON a.account_id=b.account_id WHERE a.target IS NOT NULL''')}
    excluded = {r[0] for r in db.execute('SELECT zen_id FROM zen_exclusions')}
    buckets = defaultdict(list)
    for tx in predictions.entities(db, 'transaction').values():
        if tx['id'] in excluded or tx.get('hold'):
            continue
        for target in {tx.get('incomeAccount'), tx.get('outcomeAccount')} - {None}:
            if (tx['id'], target) not in claimed:
                buckets[(target, tx.get('date'))].append(tx)
    proposed = {}
    for bank, account in candidates:
        token = reference(bank)
        if token is None:
            continue
        pattern = re.compile(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])')
        found = []
        for tx in buckets[(account['id'], bank['local_date'])]:
            if not pattern.search(tx.get('comment') or ''):
                continue
            # A transfer or debt edited on the phone retains its bank-side leg.
            field = 'income' if bank['direction'] == 'income' else 'outcome'
            other = 'outcome' if field == 'income' else 'income'
            if tx.get(field+'Account') != account['id'] or tx.get(field+'Instrument') != account['instrument']:
                continue
            if Decimal(tx.get(field) or 0) != Decimal(bank['amount']):
                continue
            if tx.get(other+'Account') == account['id'] and Decimal(tx.get(other) or 0) != 0:
                continue
            found.append(tx['id'])
        if len(found) == 1:
            proposed[bank['id']] = found[0]
    bank_targets = {bank['id']: account['id'] for bank, account in candidates}
    owners = Counter((zen, bank_targets[bank]) for bank, zen in proposed.items())
    result = {bank: (zen, 'bank_reference') for bank, zen in proposed.items() if owners[(zen, bank_targets[bank])] == 1}
    claimed.update((zen, bank_targets[bank]) for bank, (zen, _) in result.items())
    groups = defaultdict(list)
    targets = defaultdict(set)
    for identifier, target in db.execute("SELECT id,zenmoney_account_id FROM bank_accounts WHERE selection='enabled'"):
        targets[target].add(identifier)
    for bank, account in candidates:
        if bank['id'] not in result:
            key = (account['id'], bank['local_date'], bank['direction'], Decimal(bank['amount']), account['instrument'])
            groups[key].append(bank)
    for (target, day, direction, amount, instrument), banks in groups.items():
        # Two distinct physical accounts bound to the same target need explicit
        # references; they cannot compete for a day/amount-only match.
        if len(targets[target]) != 1:
            continue
        side = 'income' if direction == 'income' else 'outcome'
        other = 'outcome' if side == 'income' else 'income'
        remote = [t for t in buckets[(target, day)] if (t['id'], target) not in claimed
            and t.get(side+'Account') == target and t.get(side+'Instrument') == instrument
            and Decimal(t.get(side) or 0) == amount
            and (t.get(other+'Account') != target or Decimal(t.get(other) or 0) == 0)]
        remote.sort(key=lambda t: (t.get('created') or 0, t['id']))
        banks.sort(key=lambda b: (datetime.fromisoformat(b['occurred_at']).timestamp() if b.get('occurred_at') else 0, b['id']))
        origin = 'day_amount' if len(banks) == len(remote) == 1 else 'day_amount_order'
        for bank in banks:
            token = reference(bank)
            # An explicit different bank reference overrides amount similarity.
            found = next((t for t in remote if t.get('incomeAccount') != t.get('outcomeAccount')
                or not token or not comment_reference(t)
                or token == comment_reference(t)), None)
            if found is not None:
                result[bank['id']] = (found['id'], origin)
                claimed.add((found['id'], target))
                remote.remove(found)
    return result


def comment_reference(tx):
    comment = tx.get('comment') or ''
    found = re.search(r'\bCT (?:DI|DEN):\s*([A-Za-z0-9]{6,128})\b|\b(580[A-Z0-9]{8,128})\b', comment)
    return next((v for v in found.groups() if v), None) if found else None
