"""Recognize explicit bank references before creating another remote UUID."""
from collections import Counter, defaultdict
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
    """One unique remote record per unique bank reference, never amount alone.

    candidates contains (authorized bank revision, validated remote account).
    The caller persists accepted identities atomically with delivery intent.
    """
    if not candidates:
        return {}
    claimed = {r[0] for r in db.execute('''SELECT zen_id FROM processing_receipts
        UNION SELECT zen_id FROM deliveries WHERE ever_attempted=1
        UNION SELECT zen_id FROM delivery_attempts
        UNION SELECT zen_id FROM link_zen_members
        UNION SELECT zen_id FROM zen_exclusions''')}
    buckets = defaultdict(list)
    for tx in predictions.entities(db, 'transaction').values():
        if tx['id'] in claimed or tx.get('hold'):
            continue
        for target in {tx.get('incomeAccount'), tx.get('outcomeAccount')} - {None}:
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
    owners = Counter(proposed.values())
    return {bank: zen for bank, zen in proposed.items() if owners[zen] == 1}
