"""Read-only suggestions and display statuses. Candidate != confirmed link."""
import unicodedata
from collections import defaultdict, Counter
from decimal import Decimal


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


def recipient(row):
    details = (row['raw'].get('details') or {})
    name = normalized(details.get('corresponsiveName'))
    account = normalized(details.get('corresponsiveAccount'))
    # An exact recipient account is stronger than a name. No fuzzy text stripping.
    description = normalized(row.get('description'))
    return ('account:' + account if account else 'name:' + name if name else
            'description:' + description if len(description) >= 6 else None), name


def enrich(rows, accounts, transactions, categories):
    zen = {t['id']: t for t in transactions if not t.get('deleted') and not t.get('hold')}
    tags = {t['id']: t['title'] for t in categories if not t.get('deleted')}
    named, learned = defaultdict(dict), defaultdict(dict)

    def example(tx):
        if not tx.get('date'):
            return None
        cat = tx.get('tag') or []
        if len(cat) != 1 or cat[0] not in tags:
            return None
        # Transfers and compound operations never train expense/income categories.
        inc, out = Decimal(str(tx.get('income', 0))), Decimal(str(tx.get('outcome', 0)))
        if tx.get('incomeAccount') != tx.get('outcomeAccount') or (inc > 0) == (out > 0):
            return None
        return {'id': tx['id'], 'date': tx['date'], 'category': cat[0], 'title': tags[cat[0]],
                'payee': tx.get('payee') or '', 'direction': 1 if inc > 0 else -1}

    for tx in zen.values():
        e = example(tx)
        if e and normalized(tx.get('payee')):
            named[(tx.get('incomeAccount'), e['direction'], normalized(tx['payee']))][tx['id']] = e
    for row in rows:
        tx = zen.get(row.get('linked_id'))
        if row['state'] not in ('linked', 'sent') or not tx or tx['id'] not in {c['id'] for c in row['candidates']}:
            continue
        e, (key, _) = example(tx), recipient(row)
        if e and key:
            learned[(row['bank_account'], e['direction'], key)][tx['id']] = e

    for row in rows:
        account = accounts[row['bank_account']]
        row['candidates'] = [{**c, 'categories': [tags.get(t, 'Неизвестная категория') for t in zen[c['id']].get('tag') or []],
                              'transfer': zen[c['id']].get('incomeAccount') != zen[c['id']].get('outcomeAccount')}
                             for c in row['candidates'] if c['id'] in zen]
        linked = next((c for c in row['candidates'] if c['id'] == row.get('linked_id')), None)
        row['linked'] = linked
        row['category_suggestion'] = None
        if row['state'] in ('linked', 'sent'):
            row['reconciliation_status'] = 'synced' if linked else 'conflict'
        elif row['state'] != 'pending':
            row['reconciliation_status'] = row['state']
        elif not row['mapped']:
            row['reconciliation_status'] = 'unmapped'
        else:
            row['reconciliation_status'] = 'possible' if row['candidates'] else 'new'
        if row['state'] != 'pending' or not row['mapped'] or row['candidates']:
            continue
        key, name = recipient(row)
        sign = 1 if row['amount_minor'] > 0 else -1
        examples = dict(named.get((account['zenmoney_account'], sign, name), {})) if name else {}
        learned_examples = learned.get((row['bank_account'], sign, key), {}) if key else {}
        examples.update(learned_examples)
        examples = [e for e in examples.values() if e['date'] < row['date']]
        if len(examples) < 2:
            continue
        counts = Counter(e['category'] for e in examples)
        conflicts = [{'id': k, 'title': tags[k], 'count': n} for k, n in counts.most_common()]
        agreed = len(counts) == 1
        row['category_suggestion'] = {
            'category': conflicts[0]['id'] if agreed else None,
            'title': conflicts[0]['title'] if agreed else None,
            'count': len(examples), 'conflict': not agreed, 'distribution': conflicts,
            'reason': ('То же описание в подтверждённых связях этого счёта' if key and key.startswith('description:')
                       else 'Тот же получатель в истории этого счёта; направление совпадает'),
            'examples': sorted(examples, key=lambda e: e['date'], reverse=True)[:3]}
    return rows
