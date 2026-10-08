"""Exact-day proposals built from the complete local mirrors, never UI filters."""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from fastapi import HTTPException
from .service import candidate_index, mapped_account, fail, confirm, audit
from .models import Decision
from .db import entities



def bank_time(row):
    raw = row.get('raw') or {}
    for value in [raw.get('date'), (raw.get('details') or {}).get('processDate')]:
        if not isinstance(value, str):
            continue
        for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
            try:
                stamp = datetime.strptime(value.strip(), fmt)
                if stamp.date().isoformat() == row['date']:
                    return stamp
            except ValueError:
                pass
    return None


def chronological_pairs(bank, zen, transactions):
    """Order both free sets oldest first. Never use changed/import order as event time."""
    bank_times = {r['id']: bank_time(r) for r in bank}
    zen_times = {r['id']: transactions[r['id']].get('created') for r in zen}
    if any(t is None for t in bank_times.values()) or any(type(t) is not int or t <= 0 for t in zen_times.values()):
        return None
    tied = len(set(bank_times.values())) != len(bank) or len(set(zen_times.values())) != len(zen)
    banks = sorted(bank, key=lambda r: (bank_times[r['id']], r['external_id'], r['id']))
    zens = sorted(zen, key=lambda r: (zen_times[r['id']], r['id']))
    return [{'bank_id': b['id'], 'zen_id': z['id'], 'order': 'chronological', 'order_tied': tied}
            for b, z in zip(banks, zens)]


def proposals(db, rows):
    accounts = {r['id']: dict(r) for r in db.execute('SELECT * FROM bank_accounts')}
    transactions = entities(db, 'transaction')
    index = candidate_index(db, accounts.values(), transactions, include_excluded=True)
    from .exclusions import zen_ids
    excluded = zen_ids(db)
    groups = defaultdict(list)
    for row in rows:
        groups[(row['bank_account'], row['date'])].append(row)
    # Include ZenMoney-only days within the observed bank-history range.
    ranges = {}
    for row in rows:
        lo, hi = ranges.get(row['bank_account'], (row['date'], row['date']))
        ranges[row['bank_account']] = (min(lo, row['date']), max(hi, row['date']))
    for account_id, (lo, hi) in ranges.items():
        target = accounts[account_id]['zenmoney_account']
        for (zen_account, ordinal, _amount) in index:
            day = date.fromordinal(ordinal).isoformat()
            if zen_account == target and lo <= day <= hi:
                groups.setdefault((account_id, day), [])
    tx_by_id = {t['id']: t for t in transactions}
    tags = {t['id']: t['title'] for t in entities(db, 'tag') if not t.get('deleted')}
    used = {identifier: r['id'] for r in rows if r['state'] in ('linked', 'sent') for identifier in (r.get('split_ids') or [r['linked_id']]) if identifier}
    result = []
    for (account_id, day), bank in sorted(groups.items(), reverse=True):
        account = accounts[account_id]
        if not account['zenmoney_account']:
            continue
        zen = defaultdict(list)
        for (target, ordinal, amount), matches in index.items():
            if target == account['zenmoney_account'] and ordinal == date.fromisoformat(day).toordinal():
                zen[amount].extend(m for _, m in matches)
        zen_rows = []
        for amount, matches in zen.items():
            for match in matches:
                tx = tx_by_id[match['id']]
                zen_rows.append({**match, 'amount_minor': str(amount), 'currency': account['currency'],
                    'categories': [tags.get(t, t) for t in tx.get('tag') or []],
                    'transfer': tx.get('incomeAccount') != tx.get('outcomeAccount'),
                    'split_eligible': (Decimal(str(tx.get('income', 0))) > 0) != (Decimal(str(tx.get('outcome', 0))) > 0),
                    'created': tx.get('created'), 'linked_bank_id': used.get(match['id']), 'excluded': match['id'] in excluded,
                    'same_day_bank_ids': [r['id'] for r in bank if r['state'] != 'ignored' and r['amount_minor'] == amount]})
        all_bank = bank
        all_zen_rows = zen_rows
        bank = [r for r in bank if r['state'] != 'ignored']
        zen_rows = [z for z in zen_rows if not z['excluded']]
        zen = {amount: [m for m in matches if m['id'] not in excluded] for amount, matches in zen.items()}
        zen = {amount: matches for amount, matches in zen.items() if matches}
        bank_counts = Counter(r['amount_minor'] for r in bank)
        equal = bank_counts == Counter({a: len(v) for a, v in zen.items()})
        pairs, ambiguous = [], 0
        valid = True
        for row in bank:
            matches = zen.get(row['amount_minor'], [])
            if row['state'] in ('linked', 'sent'):
                valid &= bool(row.get('linked')) if row.get('split_ids') else any(m['id'] == row['linked_id'] for m in matches)
            elif row['state'] != 'pending':
                valid = False
        remaining = {a: [m for m in v if m['id'] not in used] for a, v in zen.items()}
        for amount in sorted({r['amount_minor'] for r in bank if r['state'] == 'pending'}):
            pending = [r for r in bank if r['state'] == 'pending' and r['amount_minor'] == amount]
            matches = remaining.get(amount, [])
            if len(pending) == len(matches) == 1:
                pairs.append({'bank_id': pending[0]['id'], 'zen_id': matches[0]['id']})
            elif len(pending) == len(matches) and (ordered := chronological_pairs(pending, matches, tx_by_id)):
                pairs.extend(ordered)
            else:
                ambiguous += len(pending)
        account_valid = True
        try:
            mapped_account(db, account)
        except HTTPException:
            valid = False
            account_valid = False
        # Hash includes full rows/candidate details/tags so stale previews cannot be accepted.
        payload = {'used_links': used, 'account': account, 'bank': all_bank, 'excluded_zen_ids': sorted(excluded), 'zen': [t for t in transactions if t.get('date') == day and account['zenmoney_account'] in (t.get('incomeAccount'), t.get('outcomeAccount'))]}
        snapshot = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        result.append({'bank_account': account_id, 'date': day, 'bank_count': len(bank),
                       'zen_count': sum(map(len, zen.values())), 'equal': equal,
                       'bank_ids': [r['id'] for r in all_bank], 'zen_rows': all_zen_rows,
                       'preview_pairs': pairs if account_valid else [],
                       'pairs': sorted(pairs, key=lambda p: p['bank_id']) if equal and valid else [],
                       'ambiguous': ambiguous if equal else 0, 'snapshot': snapshot,
                       'bulk_eligible': bool(equal and valid and pairs and ambiguous == 0),
                       'resolved': all(r.get('reconciliation_status') == 'synced' for r in bank) and
                           all(z['linked_bank_id'] in {r['id'] for r in bank} for z in zen_rows),
                       'bank_total': str(sum(r['amount_minor'] for r in all_bank)),
                       'reconcile_bank_total': str(sum(r['amount_minor'] for r in bank)),
                       'reconcile_zen_total': str(sum(Decimal(z['amount_minor']) for z in zen_rows)),
                       'excluded_count': len(all_bank)-len(bank)+len(all_zen_rows)-len(zen_rows),
                       'zen_total': str(sum(Decimal(z['amount_minor']) for z in all_zen_rows))})
    # Two bank accounts mapped to the same ZenMoney account must not claim one ID.
    owners = Counter(pair['zen_id'] for proposal in result for pair in proposal['pairs'])
    for proposal in result:
        if any(owners[pair['zen_id']] > 1 for pair in proposal['pairs']):
            proposal['bulk_eligible'] = False
    preview_owners = Counter(p['zen_id'] for d in result for p in d['preview_pairs'])
    for d in result:
        d['preview_pairs'] = [p for p in d['preview_pairs'] if preview_owners[p['zen_id']] == 1]
    from .splits import search_splits
    for d in result:
        reserved = {p['zen_id'] for p in d['preview_pairs']}
        pending = [r for r in groups[(d['bank_account'], d['date'])] if r['state'] == 'pending']
        available = [z for z in d['zen_rows'] if not z['linked_bank_id'] and not z.get('excluded')]
        # Rows with any exact same-day candidate are not split speculatively.
        pending = [r for r in pending if not any(Decimal(z['amount_minor']) == r['amount_minor'] for z in available)]
        d['split_candidates'], d['split_search_limited'] = search_splits(pending, [z for z in available if z['id'] not in reserved])
    all_options = [o for d in result for o in d['split_candidates']]
    for option in all_options:
        if any(o['bank_id'] != option['bank_id'] and set(o['zen_ids']) & set(option['zen_ids']) for o in all_options):
            option['unique'] = False
    return result


def confirm_day(db, account_id, day, snapshot, rows):
    proposal = next((p for p in proposals(db, rows) if p['bank_account'] == account_id and p['date'] == day), None)
    if not proposal or proposal['snapshot'] != snapshot or not proposal['pairs']:
        fail('Сверка дня изменилась или однозначных пар нет. Обновите список.')
    by_id = {r['id']: r for r in rows}
    index = candidate_index(db, [dict(a) for a in db.execute('SELECT * FROM bank_accounts')])
    for pair in proposal['pairs']:
        row = by_id[pair['bank_id']]
        confirm(db, row['id'], Decision(action='link', match_id=pair['zen_id'], reviewed_candidates=[c['id'] for c in row['candidates']]), index=index)
    audit(db, 'confirm_day', {'account': account_id, 'date': day, 'pairs': proposal['pairs'], 'snapshot': snapshot})
    return {'linked': len(proposal['pairs'])}


def confirm_days(db, decisions, rows, *, pairs_only=False):
    """Validate the entire preview before any writes; caller owns the transaction."""
    current = {(p['bank_account'], p['date']): p for p in proposals(db, rows)}
    chosen, seen = [], set()
    for decision in decisions:
        key = (decision.bank_account, str(decision.date))
        proposal = current.get(key)
        if key in seen or not proposal or not (proposal['preview_pairs'] if pairs_only else proposal['bulk_eligible']) or proposal['snapshot'] != decision.snapshot:
            fail('Список однозначных дней изменился. Обновите данные и повторите подтверждение.')
        seen.add(key)
        chosen.append(proposal)
    by_id = {row['id']: row for row in rows}
    # Fresh once inside the write transaction; no concurrent imports/sync can change it.
    index = candidate_index(db, [dict(a) for a in db.execute('SELECT * FROM bank_accounts')])
    linked = 0
    for proposal in chosen:
        for pair in (proposal['preview_pairs'] if pairs_only else proposal['pairs']):
            row = by_id[pair['bank_id']]
            confirm(db, row['id'], Decision(action='link', match_id=pair['zen_id'],
                    reviewed_candidates=[c['id'] for c in row['candidates']]), index=index)
            linked += 1
    audit(db, 'confirm_preview_pairs' if pairs_only else 'confirm_days', {'days': [d.model_dump(mode='json') for d in decisions], 'linked': linked, 'pairs': [p for d in chosen for p in (d['preview_pairs'] if pairs_only else d['pairs'])]})
    return {'days': len(chosen), 'linked': linked}


def confirm_split(db, identifier, decision, rows):
    row = next((r for r in rows if r['id'] == identifier), None)
    if not row or row['state'] != 'pending':
        fail('Операция уже обработана или не найдена')
    from .service import get_row
    mapped_account(db, get_row(db, 'bank_accounts', row['bank_account']))
    day = next(d for d in proposals(db, rows) if d['bank_account'] == row['bank_account'] and d['date'] == row['date'])
    ids = sorted(decision.zen_ids)
    if len(ids) != len(set(ids)) or day['snapshot'] != decision.snapshot or not any(
            p['bank_id'] == identifier and p['zen_ids'] == ids for p in day['split_candidates']):
        fail('Составная связь изменилась. Обновите данные и проверьте состав.')
    from .service import get_row
    mapped_account(db, get_row(db, 'bank_accounts', row['bank_account']))
    from .link_groups import insert
    insert(db, [identifier], ids, 'split', 'manual')
    audit(db, 'confirm_split', {'id': identifier, 'zen_ids': ids, 'snapshot': decision.snapshot})
    return {'state': 'linked', 'parts': len(ids)}
