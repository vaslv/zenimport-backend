"""Durable capture jobs, account locks, leases and complete-interval evidence."""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import sqlite3
from uuid import uuid4

from . import records
from .capture_policy import MESSAGES, SyncError, canonical, capabilities, fail, get, today, validate_record

ACTIVE = ('planned', 'collecting', 'paused')
LEASE_SECONDS = 120
DAY = timedelta(days=1)


def aggregate(states):
    for state in ('collecting', 'planned', 'paused', 'failed', 'cancelled'):
        if state in states:
            return state
    return 'complete'


def add_error(db, job_id, account_id, code, now, retryable=False):
    if not db.execute('SELECT 1 FROM capture_errors WHERE job_id=? AND account_id IS ? AND code=? AND resolved=0', (job_id, account_id, code)).fetchone():
        db.execute('INSERT INTO capture_errors VALUES (?,?,?,?,?,0,?)', (str(uuid4()), job_id, account_id, code, int(retryable), now))


def refresh(db, job_id):
    states = [r[0] for r in db.execute('SELECT state FROM capture_intervals WHERE job_id=?', (job_id,))]
    state = aggregate(states)
    if state == 'planned' and get(db, 'capture_jobs', job_id)['worker_id'] is not None:
        state = 'collecting'
    db.execute('UPDATE capture_jobs SET capture_state=? WHERE id=?', (state, job_id))
    db.execute('''DELETE FROM account_capture_locks WHERE job_id=? AND NOT EXISTS
        (SELECT 1 FROM capture_intervals i WHERE i.job_id=? AND i.account_id=account_capture_locks.account_id
         AND i.state IN ('planned','collecting','paused'))''', (job_id, job_id))
    if state not in ACTIVE:
        db.execute('UPDATE capture_jobs SET worker_id=NULL,lease_expires_at=NULL WHERE id=?', (job_id,))


def invalidate_accounts(db, account_ids, code, now, connection_id=None):
    for account_id in account_ids:
        query = '''SELECT DISTINCT i.job_id FROM capture_intervals i JOIN capture_jobs j ON j.id=i.job_id
                   WHERE i.account_id=? AND i.state IN ('planned','collecting','paused')'''
        params = [account_id]
        if connection_id is not None:
            query += ' AND j.connection_id=?'
            params.append(connection_id)
        for row in list(db.execute(query, params)):
            db.execute("UPDATE capture_intervals SET state=? WHERE job_id=? AND account_id=? AND state IN ('planned','collecting','paused')",
                       ('cancelled' if code == 'settings_conflict' else 'failed', row[0], account_id))
            add_error(db, row[0], account_id, code, now)
            refresh(db, row[0])


def invalidate_connection(db, connection_id, now):
    identifiers = [r[0] for r in db.execute('SELECT account_id FROM connection_accounts WHERE connection_id=?', (connection_id,))]
    invalidate_accounts(db, identifiers, 'capture_context_changed', now, connection_id)


def expire(db, job_id, now):
    row = get(db, 'capture_jobs', job_id)
    if row['worker_id'] and row['lease_expires_at'] <= now and row['capture_state'] in ACTIVE:
        db.execute("UPDATE capture_intervals SET state='paused' WHERE job_id=? AND state IN ('planned','collecting')", (job_id,))
        db.execute("UPDATE capture_jobs SET capture_state='paused',worker_id=NULL,lease_expires_at=NULL WHERE id=?", (job_id,))
        add_error(db, job_id, None, 'lease_expired', now, True)
        return True
    return False


def interval_view(db, row):
    count = db.execute('SELECT count(*),coalesce(sum(records_count),0) FROM capture_pages WHERE interval_id=?', (row['id'],)).fetchone()
    return {'id': row['id'], 'period': {'start': row['start_date'], 'end': row['end_date']}, 'state': row['state'],
            'received_pages': count[0], 'received_records': count[1]}


def delivery_counts(db, job):
    # Captured identity stays in this job even if a later unfinished revision
    # moved the current date. Mutable bank fields cannot erase its progress.
    return dict(db.execute("""SELECT p.state,count(*) FROM job_accounts a
        JOIN bank_transactions b ON b.account_id=a.account_id JOIN bank_processing p ON p.bank_id=b.id
        WHERE a.job_id=? AND ((b.local_date>=a.authorized_start AND b.local_date<=?) OR EXISTS(
          SELECT 1 FROM captured_records r JOIN capture_intervals i ON i.id=r.interval_id
          WHERE r.bank_id=b.id AND i.job_id=a.job_id)) GROUP BY p.state""", (job['id'], job['end_date'])))


def job_view(db, identifier, now):
    expire(db, str(identifier), now)
    from .delivery import refresh_status
    refresh_status(db, now, identifier)
    row = get(db, 'capture_jobs', identifier)
    accounts = []
    for account in db.execute('SELECT * FROM job_accounts WHERE job_id=? ORDER BY account_id', (row['id'],)):
        intervals = [interval_view(db, i) for i in db.execute('SELECT * FROM capture_intervals WHERE job_id=? AND account_id=? ORDER BY start_date,id', (row['id'], account['account_id']))]
        accounts.append({'account_id': account['account_id'], 'settings_version': account['settings_version'],
                         'capture_state': aggregate([i['state'] for i in intervals]), 'intervals': intervals})
    counts = {'pending': 0, 'delivered': 0, 'suppressed': 0, 'uncertain': 0}
    for state, count in delivery_counts(db, row).items():
        counts['pending' if state == 'prepared' else state] += count
    errors = [{'code': e['code'], 'message': MESSAGES[e['code']], 'retryable': bool(e['retryable']), 'request_id': e['id']}
              for e in db.execute('SELECT * FROM capture_errors WHERE job_id=? AND resolved=0 ORDER BY created_at,id', (row['id'],))]
    return {'id': row['id'], 'connection_id': row['connection_id'], 'capture_state': row['capture_state'],
            'delivery_state': row['delivery_state'], 'end_date': row['end_date'], 'accounts': accounts, 'delivery': counts, 'errors': errors}


def coverage_ranges(db, account_id):
    return [(date.fromisoformat(r[0]), date.fromisoformat(r[1])) for r in db.execute(
        'SELECT start_date,end_date FROM coverage WHERE account_id=? ORDER BY start_date,end_date', (account_id,))]


def frontier(db, account_id, start):
    cursor = start
    for left, right in coverage_ranges(db, account_id):
        if left > cursor:
            break
        if right >= cursor:
            cursor = right + DAY
    return cursor - DAY if cursor > start else None


def update_frontier(db, account_id):
    account = get(db, 'bank_accounts', account_id)
    end = frontier(db, account_id, date.fromisoformat(account['import_start'])) if account['import_start'] else None
    db.execute('UPDATE bank_accounts SET last_complete_end=? WHERE id=?', (end.isoformat() if end else None, account_id))


def planned_ranges(db, account, end, forced):
    start = date.fromisoformat(account['import_start'])
    if forced is not None:
        return [(forced, end)]
    contiguous = frontier(db, account['id'], start)
    if contiguous is not None and contiguous >= end:
        return [(max(start, min(contiguous, end) - timedelta(days=14)), end)]
    # Initial/partial capture: skip proved full islands, fill holes first. The
    # rolling overlap starts at the contiguous frontier, never at a later island.
    cursor = max(start, contiguous - timedelta(days=14)) if contiguous else start
    ranges = []
    overlap_end = min(contiguous, end) if contiguous else None
    if overlap_end and cursor <= overlap_end:
        ranges.append((cursor, overlap_end))
        cursor = overlap_end + DAY
    for left, right in coverage_ranges(db, account['id']):
        if right < cursor or left > end:
            continue
        if left > cursor:
            ranges.append((cursor, min(end, left - DAY)))
        cursor = max(cursor, right + DAY)
    if cursor <= end:
        ranges.append((cursor, end))
    return ranges


def chunks(ranges, days):
    for left, right in ranges:
        while left <= right:
            end = min(right, left + timedelta(days=days - 1))
            yield left, end
            left = end + DAY


def merge_ranges(ranges):
    result = []
    for left, right in sorted(ranges):
        if result and left <= result[-1][1] + DAY:
            result[-1] = (result[-1][0], max(result[-1][1], right))
        else:
            result.append((left, right))
    return result


def outstanding_reimports(db, account):
    return [(date.fromisoformat(left), date.fromisoformat(right))
            for row in db.execute('SELECT remaining_json FROM reimport_ranges WHERE account_id=? AND settings_version=?',
                                  (account['id'], account['settings_version'])) for left, right in json.loads(row[0])]


def satisfy_reimports(db, account, start, end):
    for row in list(db.execute('SELECT * FROM reimport_ranges WHERE account_id=? AND settings_version=?', (account['id'], account['settings_version']))):
        remaining = []
        for left, right in json.loads(row['remaining_json']):
            if right < start or left > end:
                remaining.append([left, right])
            else:
                if left < start:
                    remaining.append([left, (date.fromisoformat(start) - DAY).isoformat()])
                if right > end:
                    remaining.append([(date.fromisoformat(end) + DAY).isoformat(), right])
        db.execute('UPDATE reimport_ranges SET remaining_json=? WHERE id=?', (canonical(remaining), row['id']))


def resume(db, job_id):
    db.execute("UPDATE capture_intervals SET state='planned' WHERE job_id=? AND state='paused'", (job_id,))
    db.execute("UPDATE capture_errors SET resolved=1 WHERE job_id=? AND code IN ('bank_login_required','lease_expired')", (job_id,))
    refresh(db, job_id)


def start(db, data, now):
    from .accounts import connection_view
    body = canonical(data.model_dump(mode='json'))
    known = db.execute('SELECT * FROM job_requests WHERE request_id=?', (str(data.request_id),)).fetchone()
    if known:
        if known['request_json'] != body:
            fail('idempotency_conflict')
        return job_view(db, known['job_id'], now)
    connection = get(db, 'connections', data.connection_id)
    if not connection['timezone']:
        fail('capture_context_changed')
    end = today(now, connection['timezone'])
    caps = capabilities(connection)
    if data.reimport_from and (data.reimport_from > end or (caps.get('earliest_date') and data.reimport_from.isoformat() < caps['earliest_date'])):
        fail('validation_error', 422)
    active = db.execute("SELECT * FROM capture_jobs WHERE connection_id=? AND capture_state IN ('planned','collecting','paused')", (connection['id'],)).fetchone()
    if active:
        original = json.loads(active['request_json'])
        if original['reimport_from'] != (data.reimport_from.isoformat() if data.reimport_from else None):
            fail('account_busy')
        expire(db, active['id'], now)
        resume(db, active['id'])
        db.execute('INSERT INTO job_requests VALUES (?,?,?)', (str(data.request_id), active['id'], body))
        return job_view(db, active['id'], now)
    discovered = connection_view(db, connection['id'])['accounts']
    if any(a['selection'] == 'unconfigured' for a in discovered):
        fail('account_unconfigured')
    selected = [get(db, 'bank_accounts', a['id']) for a in discovered if a['selection'] == 'enabled' and a['supported']]
    if not selected:
        fail('account_disabled')
    for account in selected:
        if db.execute('SELECT 1 FROM account_capture_locks WHERE account_id=?', (account['id'],)).fetchone():
            fail('account_busy')
    identifier = str(uuid4())
    db.execute('INSERT INTO capture_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
               (identifier, connection['id'], str(data.request_id), body, end.isoformat(), 'planned', 'waiting', None, 0, None, now))
    db.execute('INSERT INTO job_requests VALUES (?,?,?)', (str(data.request_id), identifier, body))
    db.execute('INSERT INTO job_context VALUES (?,?,?,?)', (identifier, connection['connector_version'], connection['timezone'], connection['capabilities_json']))
    total_intervals = 0
    for account in selected:
        ranges = merge_ranges(planned_ranges(db, account, end, data.reimport_from) + outstanding_reimports(db, account))
        authorized_start = min([account['import_start']] + [left.isoformat() for left, right in ranges])
        db.execute('INSERT INTO job_accounts VALUES (?,?,?,?,?,?)', (identifier, account['id'], account['settings_version'], account['zenmoney_account_id'], account['import_start'], authorized_start))
        if data.reimport_from:
            db.execute('INSERT INTO reimport_ranges VALUES (?,?,?,?,?)', (str(uuid4()), account['id'], account['settings_version'], identifier,
                       canonical([[data.reimport_from.isoformat(), end.isoformat()]])))
        db.execute('INSERT INTO account_capture_locks VALUES (?,?)', (account['id'], identifier))
        for left, right in chunks(ranges, caps['max_window_days']):
            total_intervals += 1
            if total_intervals > 10000:
                fail('validation_error', 422)
            db.execute('INSERT INTO capture_intervals VALUES (?,?,?,?,?,?,?,?,?)',
                       (str(uuid4()), identifier, account['id'], account['settings_version'], left.isoformat(), right.isoformat(), 'planned', None, None))
    refresh(db, identifier)
    return job_view(db, identifier, now)


def lease(db, identifier, data, now):
    identifier = str(identifier)
    expire(db, identifier, now)
    row = get(db, 'capture_jobs', identifier)
    if row['capture_state'] not in ACTIVE:
        fail('capture_context_changed', persist=True)
    if db.execute("SELECT 1 FROM capture_errors WHERE job_id=? AND code='bank_login_required' AND resolved=0", (identifier,)).fetchone():
        fail('bank_login_required', persist=True)
    if row['worker_id'] and row['worker_id'] != str(data.worker_id):
        fail('capture_busy', retryable=True)
    epoch = row['lease_epoch'] if row['worker_id'] else row['lease_epoch'] + 1
    resume(db, identifier)
    db.execute("UPDATE capture_jobs SET worker_id=?,lease_epoch=?,lease_expires_at=?,capture_state='collecting' WHERE id=?",
               (str(data.worker_id), epoch, now + LEASE_SECONDS, identifier))
    return {'worker_id': data.worker_id, 'epoch': epoch, 'expires_at': datetime.fromtimestamp(now + LEASE_SECONDS, timezone.utc)}


def guard(db, job_id, data, now):
    expired = expire(db, job_id, now)
    job = get(db, 'capture_jobs', job_id)
    if expired or job['worker_id'] != str(data.worker_id) or job['lease_epoch'] != data.lease_epoch or not job['lease_expires_at'] or job['lease_expires_at'] <= now:
        fail('lease_expired', retryable=True, persist=expired)
    if job['capture_state'] not in ACTIVE:
        fail('capture_context_changed')
    return job


def interval_guard(db, job_id, interval_id):
    get(db, 'capture_jobs', job_id)
    interval = get(db, 'capture_intervals', interval_id)
    if interval['job_id'] != job_id:
        fail('not_found', 404)
    account = get(db, 'bank_accounts', interval['account_id'])
    if account['selection'] != 'enabled':
        fail('account_disabled')
    if account['settings_version'] != interval['settings_version']:
        fail('settings_conflict')
    if interval['state'] not in ACTIVE:
        fail('capture_context_changed')
    return interval, account


def fail_interval(db, interval, code, now):
    db.execute("UPDATE capture_intervals SET state='failed' WHERE id=?", (interval['id'],))
    add_error(db, interval['job_id'], interval['account_id'], code, now)
    refresh(db, interval['job_id'])
    fail(code, persist=True)


def page(db, job_id, interval_id, number, data, now):
    job_id, interval_id = str(job_id), str(interval_id)
    get(db, 'capture_jobs', job_id)
    interval = get(db, 'capture_intervals', interval_id)
    if interval['job_id'] != job_id:
        fail('not_found', 404)
    if number != data.page_number:
        fail('validation_error', 422)
    body = canonical(data.model_dump(mode='json', exclude={'worker_id', 'lease_epoch'}))
    digest = sha256(body.encode()).hexdigest()
    old = db.execute('SELECT * FROM capture_pages WHERE interval_id=? AND page_number=?', (interval_id, number)).fetchone()
    if old and old['digest'] == digest and old['payload_json'] == body:
        return {'page_number': number, 'received_records': old['records_count'], 'repeated': True}
    interval, account = interval_guard(db, job_id, interval_id)
    guard(db, job_id, data, now)
    if data.total_records and data.total_pages > data.total_records:
        fail_interval(db, interval, 'history_incomplete', now)
    if old:
        fail_interval(db, interval, 'idempotency_conflict', now)
    if interval['total_pages'] is not None and (interval['total_pages'], interval['total_records']) != (data.total_pages, data.total_records):
        fail_interval(db, interval, 'history_incomplete', now)
    context = db.execute('SELECT * FROM job_context WHERE job_id=?', (job_id,)).fetchone()
    for record in data.records:
        try:
            validate_record(record, account, context, interval['start_date'], interval['end_date'])
        except SyncError as exc:
            if exc.code == 'capture_context_changed':
                fail_interval(db, interval, exc.code, now)
            raise
    received = db.execute('SELECT coalesce(sum(records_count),0) FROM capture_pages WHERE interval_id=?', (interval_id,)).fetchone()[0]
    if received + len(data.records) > data.total_records:
        fail_interval(db, interval, 'history_incomplete', now)
    db.execute('SAVEPOINT capture_page')
    try:
        db.execute('INSERT INTO capture_pages VALUES (?,?,?,?,?)', (interval_id, number, digest, len(data.records), body))
        for record in data.records:
            bank_id = records.save_record(db, account['id'], record, parser_version=context['connector_version'], timezone=context['timezone'])
            revision = get(db, 'bank_transactions', bank_id)['revision']
            db.execute('INSERT INTO captured_records VALUES (?,?,?,?)', (interval_id, number, bank_id, revision))
    except sqlite3.IntegrityError:
        db.execute('ROLLBACK TO capture_page')
        db.execute('RELEASE capture_page')
        fail_interval(db, interval, 'history_incomplete', now)
    db.execute('RELEASE capture_page')
    db.execute("UPDATE capture_intervals SET state='collecting',total_pages=?,total_records=? WHERE id=?", (data.total_pages, data.total_records, interval_id))
    refresh(db, job_id)
    return {'page_number': number, 'received_records': len(data.records), 'repeated': False}


def complete(db, job_id, interval_id, data, now):
    job_id, interval_id = str(job_id), str(interval_id)
    interval = get(db, 'capture_intervals', interval_id)
    if interval['job_id'] != job_id:
        fail('not_found', 404)
    if interval['state'] == 'complete':
        if (interval['total_pages'], interval['total_records']) != (data.total_pages, data.total_records):
            fail('idempotency_conflict')
        return interval_view(db, interval)
    interval, account = interval_guard(db, job_id, interval_id)
    guard(db, job_id, data, now)
    pages = list(db.execute('SELECT * FROM capture_pages WHERE interval_id=? ORDER BY page_number', (interval_id,)))
    if interval['total_pages'] is not None and (interval['total_pages'], interval['total_records']) != (data.total_pages, data.total_records):
        fail_interval(db, interval, 'history_incomplete', now)
    if (interval['total_pages'], interval['total_records']) != (data.total_pages, data.total_records) or [p['page_number'] for p in pages] != list(range(1, data.total_pages + 1)) or sum(p['records_count'] for p in pages) != data.total_records:
        fail('history_incomplete', retryable=True)
    if db.execute('SELECT count(*) FROM captured_records WHERE interval_id=?', (interval_id,)).fetchone()[0] != data.total_records:
        fail_interval(db, interval, 'history_incomplete', now)
    db.execute("UPDATE capture_intervals SET state='complete' WHERE id=?", (interval_id,))
    db.execute('INSERT INTO coverage VALUES (?,?,?,?)', (account['id'], interval['start_date'], interval['end_date'], interval_id))
    satisfy_reimports(db, account, interval['start_date'], interval['end_date'])
    update_frontier(db, account['id'])
    refresh(db, job_id)
    return interval_view(db, get(db, 'capture_intervals', interval_id))


def status(db, identifier, data, now):
    identifier = str(identifier)
    guard(db, identifier, data, now)
    state = data.state if data.code == 'bank_login_required' else 'failed'
    db.execute("UPDATE capture_intervals SET state=? WHERE job_id=? AND state IN ('planned','collecting','paused')", (state, identifier))
    db.execute('UPDATE capture_jobs SET worker_id=NULL,lease_expires_at=NULL WHERE id=?', (identifier,))
    add_error(db, identifier, None, data.code, now, state == 'paused')
    refresh(db, identifier)
    return job_view(db, identifier, now)


def stop(db, identifier, now):
    identifier = str(identifier)
    get(db, 'capture_jobs', identifier)
    db.execute("UPDATE capture_intervals SET state='cancelled' WHERE job_id=? AND state IN ('planned','collecting','paused')", (identifier,))
    refresh(db, identifier)
    return job_view(db, identifier, now)
