"""Rebuildable category evidence from confirmed payments and current mobile data."""
from collections import Counter, defaultdict
from decimal import Decimal, localcontext
import json
import unicodedata

from zenmoney_api.protocol import decode_json

from .accounts import CATALOG_MAX_AGE, ORDINARY_TYPES
from .capture_policy import canonical, get


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


def recipient(row):
    account, name, description = (normalized(row.get(key)) for key in ('beneficiary_account', 'beneficiary_name', 'description'))
    # Never drop a known recipient identity to match a generic description.
    return ('account:' + account if account else 'name:' + name if name else
            'description:' + description if len(description) >= 6 else None)


def fresh(db, now):
    value = db.execute("SELECT value FROM service_meta WHERE key='zenmoney_refreshed_at'").fetchone()
    return bool(value and 0 <= now - int(value[0]) <= CATALOG_MAX_AGE)


def entities(db, kind):
    return {r['id']: decode_json(r['data'].encode()) for r in db.execute('''SELECT * FROM zen_entities e WHERE kind=? AND deleted=0
            AND NOT EXISTS (SELECT 1 FROM zen_tombstones t WHERE t.kind=e.kind AND t.entity_id=e.id)''', (kind,))}


def valid_tag(tags, identifier, direction):
    item = tags.get(identifier)
    return bool(item and not item.get('deleted') and not item.get('archive')
                and item.get('showIncome' if direction == 'income' else 'showOutcome', True) not in (False, 0))


def bank_record(db, bank_id, revision=None):
    current = dict(get(db, 'bank_transactions', bank_id))
    if revision is None or revision == current['revision']:
        return current
    row = db.execute('SELECT * FROM bank_versions WHERE bank_id=? AND revision=?', (bank_id, revision)).fetchone()
    if not row:
        raise ValueError('Unknown bank revision')
    data = json.loads(row['data_json'])
    raw = json.loads(row['raw_json'])
    details = raw.get('details') or {}
    if 'local_date' in data:
        current.update({k: data.get(k) for k in ('local_date', 'occurred_at', 'direction', 'amount', 'description', 'beneficiary_account', 'beneficiary_name')})
    else:
        # Legacy snapshots contain signed minor units and the original raw row.
        scale = get(db, 'bank_accounts', current['account_id'])['scale']
        digits = str(abs(data['amount_minor'])).rjust(scale + 1, '0')
        current.update(local_date=data['date'], direction='income' if data['amount_minor'] > 0 else 'expense',
            amount=digits[:-scale] + '.' + digits[-scale:] if scale else digits, description=data['description'],
            occurred_at=None,
            beneficiary_account=details.get('corresponsiveAccount'), beneficiary_name=details.get('corresponsiveName'))
    current['revision'] = revision
    return current


def rebuild_evidence(db):
    db.execute('DELETE FROM category_evidence')
    db.execute('DELETE FROM category_payments')
    tags, transactions, accounts, instruments = (entities(db, k) for k in ('tag', 'transaction', 'account', 'instrument'))
    bindings = defaultdict(set)
    for row in db.execute('SELECT account_id,zenmoney_account_id FROM account_bindings'):
        bindings[row[0]].add(row[1])
    for row in db.execute("SELECT id,zenmoney_account_id FROM bank_accounts WHERE zenmoney_account_id IS NOT NULL"):
        bindings[row[0]].add(row[1])
    groups = {}
    for row in db.execute('SELECT * FROM link_groups'):
        groups[row['id']] = {'kind': row['kind'], 'banks': [], 'zens': []}
    membership = {}
    for group, bank in db.execute('SELECT group_id,bank_id FROM link_bank_members'):
        groups[group]['banks'].append(bank)
        membership[bank] = group
    for group, zen in db.execute('SELECT group_id,zen_id FROM link_zen_members'):
        groups[group]['zens'].append(zen)
    receipts = defaultdict(set)
    # Chronological pairing of repeated equal amounts preserves multiplicity,
    # but does not prove a particular recipient/category association.
    for bank, zen in db.execute("SELECT bank_id,zen_id FROM processing_receipts WHERE origin<>'day_amount_order'"):
        receipts[bank].add(zen)
    deliveries = {r['bank_id']: r['zen_id'] for r in db.execute('SELECT bank_id,zen_id FROM deliveries')}
    excluded = {r[0] for r in db.execute('SELECT zen_id FROM zen_exclusions')}
    banks = [dict(row) for row in db.execute('''SELECT b.*,a.bank FROM bank_transactions b JOIN bank_accounts a ON a.id=b.account_id
                            JOIN bank_processing p ON p.bank_id=b.id WHERE p.state='delivered'
                            AND NOT EXISTS(SELECT 1 FROM bank_exclusions x WHERE x.bank_id=b.id)''')]
    proofs, owners = {}, defaultdict(set)
    for bank in banks:
        group = groups.get(membership.get(bank['id']))
        if group:
            if group['kind'] == 'settlement' or len(group['banks']) != 1:
                continue
            zens, origin = group['zens'], 'confirmed_group:' + group['kind']
        else:
            # A current delivery identity is stronger than obsolete receipts of
            # a former editor. Several unrelated receipts are not one payment.
            zens = [deliveries[bank['id']]] if bank['id'] in deliveries else sorted(receipts[bank['id']])
            if len(zens) != 1:
                continue
            origin = 'confirmed_receipt'
        proofs[bank['id']] = zens, origin
        for zen in zens:
            owners[zen].add(bank['id'])
    for bank in banks:
        if not recipient(bank) or bank['id'] not in proofs:
            continue
        zens, origin = proofs[bank['id']]
        if any(len(owners[zen]) != 1 for zen in zens):
            continue
        categories, values, evidence = set(), [], []
        valid = bool(zens)
        for identifier in zens:
            tx = transactions.get(identifier)
            if not tx or identifier in excluded or tx.get('viewed') not in (True, 1) or tx.get('hold') or tx.get('deleted'):
                valid = False
                break
            target = tx.get('incomeAccount')
            account = accounts.get(target)
            if target != tx.get('outcomeAccount') or target not in bindings[bank['account_id']] or not account or account.get('type') not in ORDINARY_TYPES:
                valid = False
                break
            inc, out = tx.get('income'), tx.get('outcome')
            if any(type(v) is not int and not isinstance(v, Decimal) for v in (inc, out)):
                valid = False
                break
            inc, out = Decimal(inc), Decimal(out)
            value = inc if bank['direction'] == 'income' else out
            other = out if bank['direction'] == 'income' else inc
            instrument = instruments.get(str(tx.get('incomeInstrument' if bank['direction'] == 'income' else 'outcomeInstrument')))
            if value <= 0 or other != 0 or value > Decimal(bank['amount']) or value.as_tuple().exponent < -12 or not instrument or instrument.get('shortTitle') != bank['currency']:
                valid = False
                break
            tx_tags = tx.get('tag') or []
            if any(not valid_tag(tags, tag, bank['direction']) for tag in tx_tags):
                valid = False
                break
            values.append(value)
            categories.update(tx_tags)
            evidence.extend((identifier, tag) for tag in tx_tags)
        if not valid:
            continue
        with localcontext() as context:
            context.prec = 60 + len(str(len(values)))
            if sum(values, Decimal(0)) != Decimal(bank['amount']):
                continue
        unambiguous = len(categories) == 1 and all(len(transactions[z].get('tag') or []) == 1 for z in zens)
        category = next(iter(categories)) if unambiguous else None
        db.execute('INSERT INTO category_payments VALUES (?,?,?,?,?)',
                   (bank['id'], category, 'single_category' if unambiguous else 'ambiguous_categories', int(unambiguous), canonical(zens)))
        db.executemany('INSERT INTO category_evidence VALUES (?,?,?,?,?,?)',
            ((bank['id'], zen, tag, bank['direction'], origin, int(unambiguous)) for zen, tag in evidence))


def evidence_context(db):
    tags = entities(db, 'tag')
    profiles = {}
    for row in db.execute('SELECT b.*,p.eligible,p.category_id FROM category_payments p JOIN bank_transactions b ON b.id=p.bank_id'):
        key = (row['account_id'], row['direction'], recipient(dict(row)))
        profile = profiles.setdefault(key, {'counts': Counter(), 'blocked': 0, 'payments': {}})
        if row['eligible'] and valid_tag(tags, row['category_id'], row['direction']):
            profile['counts'][row['category_id']] += 1
            profile['payments'][row['id']] = row['category_id']
        elif not row['eligible']:
            profile['blocked'] += 1
            profile['payments'][row['id']] = None
    rules = defaultdict(set)
    for rule in db.execute('SELECT * FROM recipient_rules'):
        if valid_tag(tags, rule['category_id'], rule['direction']):
            rules[(rule['bank'], rule['direction'], normalized(rule['name']))].add(rule['category_id'])
    return {'tags': tags, 'profiles': profiles, 'rules': rules,
            'manual': dict(db.execute("SELECT bank_id,category_id FROM category_choices WHERE source='manual'")),
            'banks': dict(db.execute('SELECT id,bank FROM bank_accounts'))}


def predict(db, bank_id, now, *, revision=None, context=None):
    bank = bank_record(db, bank_id, revision)
    result = {'category_id': None, 'source': 'unknown', 'reason': 'no_confirmed_history', 'candidates': []}
    if not fresh(db, now):
        return result | {'reason': 'zenmoney_unavailable'}
    context = context or evidence_context(db)
    tags = context['tags']
    manual = context['manual'].get(bank_id)
    if manual and valid_tag(tags, manual, bank['direction']):
        return result | {'category_id': manual, 'source': 'manual', 'reason': 'manual_choice'}
    name = normalized(bank.get('beneficiary_name'))
    rules = context['rules'].get((context['banks'][bank['account_id']], bank['direction'], name), set()) if name else set()
    if len(rules) == 1:
        return result | {'category_id': next(iter(rules)), 'source': 'rule', 'reason': 'recipient_rule'}
    key = recipient(bank)
    if not key:
        return result
    profile = context['profiles'].get((bank['account_id'], bank['direction'], key), {'counts': {}, 'blocked': 0, 'payments': {}})
    counts, blockers = Counter(profile['counts']), profile['blocked']
    if bank_id in profile['payments']:
        own = profile['payments'][bank_id]
        if own is None:
            blockers -= 1
        else:
            counts[own] -= 1
            if not counts[own]:
                del counts[own]
    blocked = bool(rules) or blockers > 0
    result['candidates'] = [{'category_id': cat, 'title': tags[cat].get('title', ''), 'count': count}
                            for cat, count in sorted(counts.items(), key=lambda item: (-item[1], normalized(tags[item[0]].get('title', '')), item[0]))]
    if blocked or len(counts) > 1:
        return result | {'reason': 'conflicting_history'}
    if len(counts) == 1 and next(iter(counts.values())) >= 3:
        return result | {'category_id': next(iter(counts)), 'source': 'history', 'reason': 'three_consistent_payments'}
    return result | {'reason': 'insufficient_history' if counts else result['reason']}


def prepare(db, now, bank_ids=None):
    generation = db.execute('SELECT generation FROM zen_mirror_state WHERE id=1').fetchone()[0]
    context = evidence_context(db)
    db.execute('''DELETE FROM category_predictions WHERE bank_id IN (SELECT p.bank_id FROM bank_processing p LEFT JOIN deliveries d ON d.bank_id=p.bank_id
        WHERE p.state NOT IN ('pending','prepared') OR d.ever_attempted=1)''')
    for row in db.execute('''SELECT b.id,b.revision FROM bank_transactions b JOIN bank_processing p ON p.bank_id=b.id LEFT JOIN deliveries d ON d.bank_id=b.id
                            WHERE p.state IN ('pending','prepared') AND coalesce(d.ever_attempted,0)=0'''):
        if bank_ids is not None and row['id'] not in bank_ids:
            continue
        prediction = predict(db, row['id'], now, context=context)
        previous = db.execute('SELECT bank_revision,category_id FROM category_predictions WHERE bank_id=?', (row['id'],)).fetchone()
        if previous is None or tuple(previous) != (row['revision'], prediction['category_id']):
            db.execute("UPDATE deliveries SET needs_rebuild=1 WHERE bank_id=? AND state='prepared' AND ever_attempted=0", (row['id'],))
        db.execute('''INSERT INTO category_predictions VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(bank_id) DO UPDATE SET
            bank_revision=excluded.bank_revision,category_id=excluded.category_id,source=excluded.source,reason=excluded.reason,
            candidates_json=excluded.candidates_json,mirror_generation=excluded.mirror_generation,updated_at=excluded.updated_at''',
            (row['id'], row['revision'], prediction['category_id'], prediction['source'], prediction['reason'],
             canonical(prediction['candidates']), generation, now))
