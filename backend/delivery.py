"""Durable, create-only delivery. An ambiguous write is only ever read again."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import fcntl
import logging
import os
import sqlite3
import threading
from uuid import uuid4

from zenmoney_api import HTTPError, ResponseError, TransportError
from zenmoney_api import transactions as tx

from . import accounts, capture, existing, predictions, storage
from .capture_policy import SyncError, get
from .mirror import apply_diff, reconcile_known, text

BATCH_SIZE = 20


def schedule(db, now):
    db.execute('UPDATE delivery_schedule SET next_run_at=?,failures=0 WHERE id=1', (now,))


def authorize_job(db, job_id, now):
    """A click/complete authorizes only immutable revisions of complete captures."""
    job = get(db, 'capture_jobs', job_id)
    source = db.execute('SELECT source_hash FROM zen_mirror_state WHERE id=1').fetchone()[0]
    rows = db.execute('''SELECT r.*,a.settings_version,a.target_id,a.authorized_start FROM job_accounts a
        JOIN bank_accounts b ON b.id=a.account_id JOIN bank_transactions t ON t.account_id=b.id
        JOIN bank_processing p ON p.bank_id=t.id JOIN captured_records r ON r.bank_id=t.id
        JOIN capture_intervals i ON i.id=r.interval_id LEFT JOIN deliveries d ON d.bank_id=t.id
        WHERE a.job_id=? AND b.selection='enabled' AND b.settings_version=a.settings_version
          AND b.zenmoney_account_id=a.target_id AND p.state IN ('pending','prepared')
          AND coalesce(d.ever_attempted,0)=0 AND i.state='complete'
        ORDER BY r.bank_id,r.revision DESC,r.interval_id''', (str(job_id),))
    seen = set()
    for row in list(rows):
        bank = row['bank_id']
        if bank in seen:
            continue
        record = predictions.bank_record(db, bank, row['revision'])
        if not row['authorized_start'] <= record['local_date'] <= job['end_date']:
            continue
        seen.add(bank)
        db.execute('''INSERT INTO delivery_authorizations VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(bank_id) DO UPDATE SET
            revision=excluded.revision,job_id=excluded.job_id,interval_id=excluded.interval_id,
            settings_version=excluded.settings_version,target_id=excluded.target_id,source_hash=excluded.source_hash,
            authorized_at=excluded.authorized_at''',
            (bank, row['revision'], str(job_id), row['interval_id'], row['settings_version'], row['target_id'], source, now))
    # A new explicit click also requests another read of unresolved UUIDs.
    schedule(db, now)


def refresh_status(db, now, job_id=None):
    jobs = list(db.execute('SELECT * FROM capture_jobs' + (' WHERE id=?' if job_id else ''), (str(job_id),) if job_id else ()))
    for job in jobs:
        counts = capture.delivery_counts(db, job)
        unknown = counts.get('uncertain', 0)
        if unknown:
            capture.add_error(db, job['id'], None, 'delivery_uncertain', now, True)
        else:
            db.execute("UPDATE capture_errors SET resolved=1 WHERE job_id=? AND code='delivery_uncertain'", (job['id'],))
        errors = db.execute('''SELECT 1 FROM capture_errors WHERE job_id=? AND resolved=0
            AND code IN ('zenmoney_unavailable','zenmoney_account_unavailable','delivery_uncertain','account_disabled','settings_conflict')''', (job['id'],)).fetchone()
        active = db.execute("SELECT 1 FROM deliveries WHERE authorized_job_id=? AND state='attempting'", (job['id'],)).fetchone()
        pending = counts.get('pending', 0) + counts.get('prepared', 0)
        finished = not pending and not unknown and job['capture_state'] == 'complete'
        state = 'running' if active else 'complete' if finished else 'blocked' if errors else 'waiting'
        db.execute('UPDATE capture_jobs SET delivery_state=? WHERE id=?', (state, job['id']))


def recover(db, now):
    # This runs only under the writer's file lock. Another active writer cannot
    # be mistaken for a crashed one; no timeout ever grants a write retry.
    db.execute("UPDATE deliveries SET state='uncertain',updated_at=? WHERE state='attempting'", (now,))
    db.execute("""UPDATE bank_processing SET state='uncertain',reason='write_outcome_unknown',updated_at=?
        WHERE state IN ('pending','prepared') AND (EXISTS(SELECT 1 FROM deliveries d WHERE d.bank_id=bank_processing.bank_id AND d.ever_attempted=1)
        OR EXISTS(SELECT 1 FROM delivery_attempts a WHERE a.bank_id=bank_processing.bank_id))""", (now,))
    for bank, in list(db.execute('SELECT bank_id FROM bank_exclusions')):
        db.execute("UPDATE bank_processing SET state='suppressed',reason='excluded',updated_at=? WHERE bank_id=?", (now, bank))
        db.execute("UPDATE deliveries SET state='suppressed',updated_at=? WHERE bank_id=?", (now, bank))
    reconcile_known(db, now)


def eligible(db, row, source_hash, now):
    bank = predictions.bank_record(db, row['bank_id'], row['revision'])
    account = get(db, 'bank_accounts', bank['account_id'])
    if account['selection'] != 'enabled':
        raise SyncError('account_disabled')
    if account['settings_version'] != row['settings_version'] or account['zenmoney_account_id'] != row['target_id']:
        raise SyncError('settings_conflict')
    if row['source_hash'] != source_hash or not source_hash:
        raise SyncError('zenmoney_unavailable', 503, retryable=True)
    if not db.execute('''SELECT 1 FROM captured_records r JOIN capture_intervals i ON i.id=r.interval_id
        WHERE r.interval_id=? AND r.bank_id=? AND r.revision=? AND i.state='complete' ''', (row['interval_id'], row['bank_id'], row['revision'])).fetchone():
        raise SyncError('history_incomplete')
    target = next((a for a in accounts.catalog(db, now)['accounts'] if a['id'] == row['target_id']), None)
    remote = predictions.entities(db, 'account').get(row['target_id'])
    instrument = predictions.entities(db, 'instrument').get(str(remote.get('instrument'))) if remote else None
    if not target or target['currency'] != bank['currency'] or not remote or not instrument or type(remote.get('user')) is not int or remote['user'] <= 0:
        raise SyncError('zenmoney_account_unavailable')
    return bank, remote


def intent(db, mirror, now):
    """Commit UUID, exact payload and attempt before the first byte goes out."""
    mirror_state = db.execute('SELECT * FROM zen_mirror_state WHERE id=1').fetchone()
    if not mirror.fresh(mirror_state, now):
        raise SyncError('zenmoney_unavailable', 503, retryable=True)
    candidates = list(db.execute('''SELECT q.* FROM delivery_authorizations q JOIN bank_processing p ON p.bank_id=q.bank_id
        LEFT JOIN deliveries d ON d.bank_id=q.bank_id WHERE p.state IN ('pending','prepared') AND coalesce(d.ever_attempted,0)=0
        AND NOT EXISTS(SELECT 1 FROM delivery_attempts a WHERE a.bank_id=q.bank_id)
        AND NOT EXISTS(SELECT 1 FROM processing_receipts r WHERE r.bank_id=q.bank_id)
        AND NOT EXISTS(SELECT 1 FROM link_bank_members l WHERE l.bank_id=q.bank_id)
        AND NOT EXISTS(SELECT 1 FROM bank_exclusions e WHERE e.bank_id=q.bank_id)
        ORDER BY q.authorized_at,q.bank_id'''))
    payloads, rows, validated = [], [], []
    for row in candidates:
        try:
            bank, account = eligible(db, row, mirror.source_hash, now)
            validated.append((row, bank, account))
        except SyncError as exc:
            capture.add_error(db, row['job_id'], get(db, 'bank_transactions', row['bank_id'])['account_id'], exc.code, now, True)
        except (KeyError, ValueError, TypeError):
            capture.add_error(db, row['job_id'], get(db, 'bank_transactions', row['bank_id'])['account_id'], 'zenmoney_account_unavailable', now, True)
    recognized = existing.reconcile(db, [(bank, account) for _, bank, account in validated], now)
    if recognized:
        predictions.rebuild_evidence(db)
    context = predictions.evidence_context(db)
    for row, bank, account in validated:
        try:
            if bank['id'] in recognized:
                db.execute("UPDATE capture_errors SET resolved=1 WHERE job_id=? AND account_id=? AND code IN ('zenmoney_account_unavailable','settings_conflict','account_disabled')", (row['job_id'], bank['account_id']))
                continue
            prior = db.execute('SELECT * FROM deliveries WHERE bank_id=?', (row['bank_id'],)).fetchone()
            identifier = prior['zen_id'] if prior else str(uuid4())
            # Never reuse a UUID that the mobile user has already deleted.
            if db.execute("SELECT 1 FROM zen_tombstones WHERE kind='transaction' AND entity_id=?", (identifier,)).fetchone():
                continue
            prediction = predictions.predict(db, bank['id'], now, revision=row['revision'], context=context)
            builder = tx.income if bank['direction'] == 'income' else tx.expense
            # ZenMoney uses created to order operations inside a calendar day.
            # Keep changed at send time for diff/conflict handling.
            created = int(datetime.fromisoformat(bank['occurred_at']).timestamp()) if bank.get('occurred_at') else now
            payload = builder(id=identifier, now=created, user=account['user'], date=bank['local_date'],
                account=account['id'], instrument=account['instrument'], amount=Decimal(bank['amount']),
                categories=[prediction['category_id']] if prediction['category_id'] else None,
                payee=bank['beneficiary_name'], comment=bank['description'], viewed=False)
            payload['changed'] = now
        except SyncError as exc:
            capture.add_error(db, row['job_id'], get(db, 'bank_transactions', row['bank_id'])['account_id'], exc.code, now, True)
            continue
        except (KeyError, ValueError, TypeError):
            capture.add_error(db, row['job_id'], get(db, 'bank_transactions', row['bank_id'])['account_id'], 'zenmoney_account_unavailable', now, True)
            continue
        db.execute('''INSERT INTO deliveries VALUES (?,?,?,'prepared',0,'sync',?,?,0) ON CONFLICT(bank_id) DO UPDATE SET
            payload=excluded.payload,state='prepared',authorized_job_id=excluded.authorized_job_id,updated_at=excluded.updated_at,needs_rebuild=0''',
            (bank['id'], identifier, text(payload), row['job_id'], now))
        db.execute("UPDATE deliveries SET state='attempting',ever_attempted=1,updated_at=? WHERE bank_id=?", (now, bank['id']))
        db.execute("UPDATE bank_processing SET state='uncertain',reason='write_intent',updated_at=? WHERE bank_id=?", (now, bank['id']))
        db.execute('INSERT INTO delivery_attempts VALUES (?,?,?,?,?,?)', (str(uuid4()), bank['id'], identifier, now, 'intent', text(payload)))
        db.execute("UPDATE capture_errors SET resolved=1 WHERE job_id=? AND account_id=? AND code IN ('zenmoney_account_unavailable','settings_conflict','account_disabled')", (row['job_id'], bank['account_id']))
        payloads.append(payload)
        rows.append(dict(row) | {'zen_id': identifier})
        if len(rows) == BATCH_SIZE:
            break
    refresh_status(db, now)
    return rows, payloads, dict(mirror_state)


@contextmanager
def writer_lock(path):
    descriptor = os.open(str(path.resolve()) + '.delivery.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


class DeliveryWorker:
    def __init__(self, path, mirror, clock):
        self.path, self.mirror, self.clock = path, mirror, clock
        self.stopping, self.signal = threading.Event(), threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.loop, name='zenmoney-delivery', daemon=True)
        self.thread.start()

    def wake(self):
        self.signal.set()

    def stop(self):
        self.stopping.set()
        self.wake()
        if self.thread:
            self.thread.join()

    def loop(self):
        while not self.stopping.is_set():
            self.signal.clear()
            try:
                self.run_once()
            except Exception:
                # No financial rows, credentials or SDK response in diagnostics.
                logging.getLogger(__name__).error('Delivery cycle failed; durable journal retained')
            self.signal.wait(5)

    def defer(self, *, immediate=False):
        with storage.transaction(self.path) as db:
            failures = db.execute('SELECT failures FROM delivery_schedule WHERE id=1').fetchone()[0]
            delay = 0 if immediate else min(300, 15 * 2 ** min(failures, 5))
            db.execute('UPDATE delivery_schedule SET next_run_at=?,failures=? WHERE id=1', (self.clock() + delay, 0 if immediate else min(failures + 1, 5)))
            refresh_status(db, self.clock())

    def has_unknown(self):
        with storage.transaction(self.path, read_only=True) as db:
            return bool(db.execute("SELECT 1 FROM bank_processing WHERE state='uncertain' LIMIT 1").fetchone())

    def run_once(self):
        with writer_lock(self.path) as acquired:
            if not acquired:
                return
            with storage.transaction(self.path) as db:
                due = db.execute('SELECT next_run_at FROM delivery_schedule WHERE id=1').fetchone()[0]
                work = db.execute('''SELECT 1 FROM bank_processing p WHERE p.state='uncertain' OR (p.state IN ('pending','prepared')
                    AND EXISTS(SELECT 1 FROM delivery_authorizations q WHERE q.bank_id=p.bank_id)) LIMIT 1''').fetchone()
                if not work or due > self.clock():
                    return
                recover(db, self.clock())
                refresh_status(db, self.clock())
            # One SDK/cursor exchange at a time, including catalogue reads.
            with self.mirror.lock:
                try:
                    self.mirror.refresh(force=True, transactions=self.has_unknown())
                except SyncError:
                    with storage.transaction(self.path) as db:
                        for job, in db.execute("SELECT id FROM capture_jobs WHERE delivery_state<>'complete'"):
                            capture.add_error(db, job, None, 'zenmoney_unavailable', self.clock(), True)
                    self.defer()
                    return
                if self.stopping.is_set():
                    return
                with storage.transaction(self.path) as db:
                    rows, payloads, state = intent(db, self.mirror, self.clock())
                if not rows:
                    self.defer()
                    return
                try:
                    result = self.mirror.client.upsert('transaction', payloads, state['cursor'])
                    with storage.transaction(self.path) as db:
                        applied = apply_diff(db, result, expected_generation=state['generation'], source_hash=self.mirror.source_hash,
                            requested_cursor=state['cursor'], full=False, now=self.clock(), origin='delivery')
                    outcome = 'response_applied' if applied else 'response_superseded'
                except (TransportError, HTTPError, ResponseError, ValueError, sqlite3.Error):
                    outcome = 'write_outcome_unknown'
                with storage.transaction(self.path) as db:
                    for row, payload in zip(rows, payloads):
                        db.execute('INSERT INTO delivery_attempts VALUES (?,?,?,?,?,?)',
                            (str(uuid4()), row['bank_id'], row['zen_id'], self.clock(), outcome, text(payload)))
                    db.execute("UPDATE deliveries SET state='uncertain',updated_at=? WHERE state='attempting'", (self.clock(),))
                    refresh_status(db, self.clock())
                # The exchange response can be partial. Explicitly fetch the
                # transaction collection, never infer rejection from absence.
                if self.has_unknown():
                    try:
                        self.mirror.refresh(force=True, transactions=True)
                    except SyncError:
                        pass
                self.defer(immediate=not self.has_unknown())
