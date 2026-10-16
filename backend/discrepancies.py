"""Read-only candidates, bounded by proved bank coverage and explicit account bindings."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext

from . import accounts, capture, predictions
from .capture_policy import fail, get, today
from .sync_contracts import WireModel, WireDate, WireTime


class ReportAccount(WireModel):
    id: str
    title: str
    currency: str
    zenmoney_title: str
    status: str
    checked_days: int
    unchecked_days: int


class ExtraOperation(WireModel):
    id: str
    amount: str
    payee: str
    comment: str
    categories: list[str]
    created: int | None


class DifferenceDay(WireModel):
    date: WireDate
    account_id: str
    bank_total: str
    zenmoney_total: str
    difference: str
    operations: list[ExtraOperation]


class DiscrepancyReport(WireModel):
    connection_id: str
    bank: str
    from_date: WireDate
    to_date: WireDate
    refreshed_at: WireTime
    accounts: list[ReportAccount]
    days: list[DifferenceDay]


def consume_matches(banks, zens):
    """No persisted links: equal amounts first, then only unique small split combinations."""
    if banks and sum(banks, Decimal(0)) == sum(zens.values(), Decimal(0)):
        return set()  # A balanced unlinked group can be a split or net settlement.
    remaining = dict(zens)
    unmatched = []
    for amount in banks:
        found = next((key for key, value in remaining.items() if value == amount), None)
        if found is None:
            unmatched.append(amount)
        else:
            del remaining[found]
    budget = 20000
    for amount in unmatched:
        choices = [(key, value) for key, value in remaining.items() if value * amount > 0 and abs(value) < abs(amount)]
        # Conservative limit: never spend unbounded CPU or invent a match.
        if len(choices) > 16:
            continue
        solutions, exhausted = [], False
        def visit(index, total, ids):
            nonlocal budget, exhausted
            if budget <= 0:
                exhausted = True
                return
            budget -= 1
            if len(solutions) > 1:
                return
            if total == abs(amount):
                if len(ids) > 1:
                    solutions.append(ids)
                return
            if index == len(choices) or total > abs(amount):
                return
            key, value = choices[index]
            visit(index+1, total+abs(value), ids+[key])
            visit(index+1, total, ids)
        visit(0, Decimal(0), [])
        if len(solutions) == 1 and not exhausted:
            for key in solutions[0]:
                del remaining[key]
    return set(remaining)


def movement(tx, target):
    # Transfers and debts are compared by their leg on this particular account.
    inc = Decimal(tx.get('income') or 0) if tx.get('incomeAccount') == target else Decimal(0)
    out = Decimal(tx.get('outcome') or 0) if tx.get('outcomeAccount') == target else Decimal(0)
    return inc-out


def build(db, identifier, now, *, from_date=None, to_date=None, account_id=None):
    connection = accounts.connection_view(db, identifier)
    config = get(db, 'connections', identifier)
    selected = [a for a in connection['accounts'] if a['selection'] == 'enabled' and a['supported']]
    if account_id is not None:
        if not any(a['id'] == str(account_id) for a in selected):
            fail('not_found', 404)
        selected = [a for a in selected if a['id'] == str(account_id)]
    end = to_date or today(now, config['timezone'])
    start = from_date or min((date.fromisoformat(a['import_start']) for a in selected), default=end)
    if start > end or (end-start).days > 3660 or end > today(now, config['timezone']):
        fail('validation_error', 422)
    if not predictions.fresh(db, now):
        fail('zenmoney_unavailable', 503, retryable=True)
    transactions, tags, remote_accounts, instruments = (predictions.entities(db, k) for k in ('transaction', 'tag', 'account', 'instrument'))
    refreshed = int(db.execute("SELECT value FROM service_meta WHERE key='zenmoney_refreshed_at'").fetchone()[0])
    result = {'connection_id': str(identifier), 'bank': connection['bank'], 'from_date': start, 'to_date': end,
              'refreshed_at': datetime.fromtimestamp(refreshed, timezone.utc), 'accounts': [], 'days': []}
    with localcontext() as precision:
        precision.prec = 80
        for account in selected:
            target = account['zenmoney_account_id']
            remote_account = remote_accounts.get(target, {})
            info = {'id': account['id'], 'title': account['label'], 'currency': account['currency'],
                    'zenmoney_title': remote_account.get('title', ''), 'status': 'ready', 'checked_days': 0, 'unchecked_days': 0}
            result['accounts'].append(info)
            shared = db.execute("SELECT 1 FROM bank_accounts WHERE id<>? AND selection='enabled' AND zenmoney_account_id=?", (account['id'], target)).fetchone()
            working = db.execute("""SELECT 1 FROM job_accounts a JOIN capture_jobs j ON j.id=a.job_id
                WHERE a.account_id=? AND j.capture_state IN ('planned','collecting') UNION ALL
                SELECT 1 FROM bank_transactions b JOIN bank_processing p ON p.bank_id=b.id
                WHERE b.account_id=? AND b.local_date BETWEEN ? AND ? AND p.state IN ('pending','prepared','uncertain') LIMIT 1""", (account['id'], account['id'], start.isoformat(), end.isoformat())).fetchone()
            instrument = instruments.get(str(remote_account.get('instrument')), {})
            if shared:
                info['status'] = 'shared_target'
            elif working:
                info['status'] = 'sync_in_progress'
            elif not remote_account or instrument.get('shortTitle') != account['currency']:
                info['status'] = 'account_unavailable'
            if info['status'] != 'ready':
                info['unchecked_days'] = (end-start).days+1
                continue
            coverage = capture.coverage_ranges(db, account['id'])
            eligible_days = set()
            day = start
            while day <= end:
                if day.isoformat() >= account['import_start'] and any(left <= day <= right for left, right in coverage):
                    eligible_days.add(day.isoformat())
                day += timedelta(days=1)
            info['checked_days'] = len(eligible_days)
            info['unchecked_days'] = (end-start).days+1-len(eligible_days)
            # Only immutable versions proved by a completed interval, not partial new observations.
            bank_rows = [predictions.bank_record(db, row[0], row[1]) for row in db.execute('''SELECT r.bank_id,max(r.revision)
                FROM captured_records r JOIN capture_intervals i ON i.id=r.interval_id
                JOIN bank_transactions b ON b.id=r.bank_id WHERE b.account_id=? AND i.state='complete' GROUP BY r.bank_id''', (account['id'],))]
            edges = defaultdict(set)
            for bank, zen in db.execute('''SELECT bank_id,zen_id FROM processing_receipts UNION SELECT bank_id,zen_id FROM deliveries
                UNION SELECT b.bank_id,z.zen_id FROM link_bank_members b JOIN link_zen_members z ON z.group_id=b.group_id'''):
                edges[bank].add(zen)
            remote = {key: tx for key, tx in transactions.items() if target in (tx.get('incomeAccount'), tx.get('outcomeAccount')) and not tx.get('hold')}
            known = {zen for bank in bank_rows for zen in edges[bank['id']] if zen in remote}
            days = defaultdict(lambda: {'banks': [], 'zens': {}, 'bank_total': Decimal(0), 'zenmoney_total': Decimal(0)})
            for bank in bank_rows:
                if bank['local_date'] not in eligible_days:
                    continue
                amount = Decimal(bank['amount']) * (1 if bank['direction'] == 'income' else -1)
                bucket = days[bank['local_date']]
                bucket['bank_total'] += amount
                if not any(zen in remote for zen in edges[bank['id']]):
                    bucket['banks'].append(amount)
            for key, tx in sorted(remote.items(), key=lambda item: (item[1].get('created') or 0, item[0])):
                if tx.get('date') not in eligible_days:
                    continue
                amount = movement(tx, target)
                if not amount:
                    continue
                # Currency belongs to the mapped leg, not to the other account of an FX transfer.
                side = 'income' if amount > 0 else 'outcome'
                currency = instruments.get(str(tx.get(side+'Instrument')), {}).get('shortTitle')
                if currency != account['currency']:
                    info['status'] = 'unsupported_movement'
                    continue
                bucket = days[tx['date']]
                bucket['zenmoney_total'] += amount
                if key not in known:
                    bucket['zens'][key] = amount
            for day, bucket in days.items():
                extras = consume_matches(bucket['banks'], bucket['zens'])
                if not extras:
                    continue
                result['days'].append({'date': day, 'account_id': account['id'],
                    'bank_total': str(bucket['bank_total']), 'zenmoney_total': str(bucket['zenmoney_total']),
                    'difference': str(bucket['zenmoney_total']-bucket['bank_total']),
                    'operations': [{'id': key, 'amount': str(bucket['zens'][key]), 'payee': remote[key].get('payee') or '',
                        'comment': remote[key].get('comment') or '', 'categories': [tags.get(t, {}).get('title', '') for t in remote[key].get('tag') or []],
                        'created': remote[key].get('created')} for key in sorted(extras, key=lambda k: (-(remote[k].get('created') or 0), k))]})
    result['days'].sort(key=lambda item: (str(item['date']), item['account_id']), reverse=True)
    return result
