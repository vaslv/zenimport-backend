"""Bounded exact subset search; proposals never write data."""
from decimal import Decimal
from itertools import combinations

MAX_PARTS = 18
MAX_OPTIONS = 30


def search_splits(bank, zen):
    result, limited = [], False
    visited = 0
    for row in bank:
        if row['state'] != 'pending':
            continue
        if visited >= 60000:
            limited = True
            continue
        target = Decimal(row['amount_minor'])
        # Exact one-to-one possibilities take precedence, including ambiguous ones.
        if any(Decimal(z['amount_minor']) == target for z in zen):
            continue
        eligible = [z for z in zen if not z['transfer'] and z.get('split_eligible', True) and
                    Decimal(z['amount_minor']) * target > 0 and abs(Decimal(z['amount_minor'])) < abs(target)]
        if len(eligible) > MAX_PARTS:
            limited = True
            continue
        options = []
        for size in range(2, len(eligible) + 1):
            for subset in combinations(eligible, size):
                visited += 1
                if visited >= 60000:
                    limited = True
                    break
                if sum(Decimal(z['amount_minor']) for z in subset) == target:
                    options.append({'bank_id': row['id'], 'zen_ids': sorted(z['id'] for z in subset)})
                    if len(options) == MAX_OPTIONS or visited >= 60000:
                        limited = True
                        break
            if len(options) == MAX_OPTIONS or visited >= 60000:
                break
        result.extend(options)
    for option in result:
        option['unique'] = not limited and sum(o['bank_id'] == option['bank_id'] for o in result) == 1 and not any(
            o['bank_id'] != option['bank_id'] and set(o['zen_ids']) & set(option['zen_ids']) for o in result)
    return result, limited


def linked_parts(row, account, zen, tags):
    ids = row.get('split_ids') or []
    if not ids:
        return [], None
    parts = []
    for identifier in ids:
        tx = zen.get(identifier)
        if not tx or tx.get('date') != row['date'] or tx.get('incomeAccount') != account['zenmoney_account'] or tx.get('outcomeAccount') != account['zenmoney_account']:
            return [], None
        inc, out = Decimal(str(tx.get('income', 0))), Decimal(str(tx.get('outcome', 0)))
        if (inc > 0) == (out > 0):
            return [], None
        amount = (inc-out) * (1 if row['currency'] == 'VND' else 100)
        if amount * row['amount_minor'] <= 0:
            return [], None
        parts.append({'id': identifier, 'date': tx['date'], 'amount_minor': str(amount),
                      'categories': [tags.get(c, 'Неизвестная категория') for c in tx.get('tag') or []],
                      'payee': tx.get('payee'), 'comment': tx.get('comment'), 'transfer': False})
    if len(parts) < 2 or sum(Decimal(p['amount_minor']) for p in parts) != row['amount_minor']:
        return [], None
    linked = {'id': None, 'date': row['date'], 'categories': list(dict.fromkeys(c for p in parts for c in p['categories'])),
              'payee': 'Разбивка по категориям', 'transfer': False, 'split': True,
              'missing_category': any(not p['categories'] for p in parts)}
    return parts, linked
