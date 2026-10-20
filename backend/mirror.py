"""Read-only SDK synchronization; network runs outside SQLite transactions."""
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import threading
import time
import sqlite3
from uuid import uuid4

from zenmoney_api import HTTPError, ResponseError, TransportError
from zenmoney_api.protocol import FORCE_FETCH, KINDS, decode_diff, encode_json, entity_key

from . import storage, migration_participant as participant
from .accounts import CATALOG_MAX_AGE
from .capture_policy import SyncError

REQUIRED_FULL = frozenset(('account', 'instrument', 'tag', 'transaction'))


def text(value):
    return encode_json(value).decode('utf-8')


def flag(value):
    if value is None:
        return None
    if type(value) not in (bool, int) or value not in (0, 1):
        raise ResponseError('Invalid mirror boolean')
    return int(value)


def amount(value):
    if value is None:
        return None
    if type(value) is not int and not isinstance(value, Decimal):
        raise ResponseError('Invalid mirror amount')
    if not Decimal(value).is_finite() or value < 0:
        raise ResponseError('Invalid mirror amount')
    return str(value)


def save_entity(db, kind, item, run_id):
    identifier = entity_key(kind, item)
    changed = item.get('changed')
    if changed is not None and (type(changed) is not int or changed < 0):
        raise ResponseError('Invalid mirror timestamp')
    deleted = flag(item.get('deleted')) or 0
    db.execute('''INSERT INTO zen_entities VALUES (?,?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET
        data=excluded.data,changed=excluded.changed,deleted=excluded.deleted,run_id=excluded.run_id''',
               (kind, identifier, text(item), changed, deleted, run_id))
    if deleted:
        db.execute('INSERT OR IGNORE INTO zen_tombstones VALUES (?,?,?,?)', (kind, identifier, 'soft_delete', text(item)))
    if kind not in ('transaction', 'reminder', 'reminderMarker'):
        return
    day = item.get('startDate' if kind == 'reminder' else 'date')
    if day is not None:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ResponseError('Invalid mirror date')
    tags = [] if item.get('tag') is None else item['tag']
    if not isinstance(tags, list) or any(not isinstance(t, str) or not t for t in tags) or len(set(tags)) != len(tags):
        raise ResponseError('Invalid mirror categories')
    fields = [item.get(name) for name in ('incomeAccount', 'outcomeAccount', 'incomeInstrument', 'outcomeInstrument')]
    fields = [str(v) if v is not None else None for v in fields]
    db.execute('''INSERT INTO zen_movements VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET
        date=excluded.date,income_account=excluded.income_account,outcome_account=excluded.outcome_account,
        income_instrument=excluded.income_instrument,outcome_instrument=excluded.outcome_instrument,
        income=excluded.income,outcome=excluded.outcome,viewed=excluded.viewed,hold=excluded.hold''',
               (kind, identifier, day, *fields, amount(item.get('income')), amount(item.get('outcome')),
                flag(item.get('viewed')), flag(item.get('hold'))))
    db.execute('DELETE FROM zen_tags WHERE kind=? AND entity_id=?', (kind, identifier))
    db.executemany('INSERT INTO zen_tags VALUES (?,?,?,?)', ((kind, identifier, tag, i) for i, tag in enumerate(tags)))


def reconcile_known(db, now):
    # Only explicit identities; similar dates/amounts never create a confirmed link.
    # A reviewed cleanup explicitly chooses the original as the canonical UUID.
    # Retired duplicate deletions must not suppress that surviving bank record.
    replacements = {}
    for bank, zen in db.execute('''SELECT b.bank_id,z.zen_id FROM link_groups g
        JOIN link_bank_members b ON b.group_id=g.id JOIN link_zen_members z ON z.group_id=g.id
        WHERE g.origin='duplicate_cleanup' '''):
        replacements.setdefault(bank, set()).add(zen)
    db.execute("""UPDATE deliveries SET state='suppressed',updated_at=? WHERE state<>'suppressed'
        AND EXISTS(SELECT 1 FROM zen_tombstones t WHERE t.kind='transaction' AND t.entity_id=deliveries.zen_id)""", (now,))
    known = db.execute('''SELECT bank_id,zen_id FROM deliveries UNION SELECT bank_id,zen_id FROM delivery_attempts
        UNION SELECT bank_id,zen_id FROM processing_receipts
        UNION SELECT b.bank_id,z.zen_id FROM link_bank_members b JOIN link_zen_members z ON z.group_id=b.group_id''')
    for bank_id, zen_id in list(known):
        if bank_id in replacements and zen_id not in replacements[bank_id]:
            continue
        deletion = db.execute("SELECT 1 FROM zen_tombstones WHERE kind='transaction' AND entity_id=?", (zen_id,)).fetchone()
        actual = db.execute("SELECT 1 FROM zen_entities WHERE kind='transaction' AND id=? AND deleted=0", (zen_id,)).fetchone()
        state = db.execute('SELECT state FROM bank_processing WHERE bank_id=?', (bank_id,)).fetchone()[0]
        if deletion:
            db.execute("UPDATE bank_processing SET state='suppressed',reason='zenmoney_deleted',updated_at=? WHERE bank_id=?", (now, bank_id))
            db.execute("UPDATE deliveries SET state='suppressed',updated_at=? WHERE bank_id=?", (now, bank_id))
        elif actual and state != 'suppressed':
            db.execute('INSERT OR IGNORE INTO processing_receipts VALUES (?,?,?,?)', (bank_id, zen_id, 'mirror_exists', now))
            db.execute("UPDATE bank_processing SET state='delivered',reason='zenmoney_exists',updated_at=? WHERE bank_id=?", (now, bank_id))
            db.execute("UPDATE deliveries SET state='delivered',updated_at=? WHERE bank_id=? AND zen_id=? AND state<>'suppressed'", (now, bank_id, zen_id))


def apply_diff(db, diff, *, expected_generation, source_hash, requested_cursor, full, now, origin='read'):
    state = db.execute('SELECT * FROM zen_mirror_state WHERE id=1').fetchone()
    if state['generation'] != expected_generation or state['source_hash'] != source_hash:
        return False
    # Parse the SDK's original bytes again: never save a cursor from an altered
    # dict while archiving different raw data.
    data = decode_diff(diff.raw_json.encode('utf-8'), requested_cursor)
    if full and not REQUIRED_FULL <= data.keys():
        raise ResponseError('Incomplete initial mirror collections')
    run_id = str(uuid4())
    db.execute('INSERT INTO zen_sync_runs VALUES (?,?,?,?,?,?)', (run_id, now, requested_cursor, data.server_timestamp, origin, data.raw_json))
    for kind in KINDS:
        if kind not in data:
            continue
        identities = [entity_key(kind, item) for item in data[kind]]
        if len(set(identities)) != len(identities):
            raise ResponseError('Duplicate mirror identity')
        if full:
            # An explicit full collection replaces its current projection, but
            # absence never proves that a write was not applied or was deleted.
            db.execute('DELETE FROM zen_entities WHERE kind=?', (kind,))
        for item in data[kind]:
            save_entity(db, kind, item, run_id)
    for item in data.get('deletion', []):
        identifier, kind = str(item['id']), item['object']
        db.execute('INSERT INTO zen_deletions VALUES (?,?,?,?,?)', (str(uuid4()), run_id, kind, identifier, text(item)))
        db.execute('INSERT OR IGNORE INTO zen_tombstones VALUES (?,?,?,?)', (kind, identifier, 'hard_delete', text(item)))
        db.execute('DELETE FROM zen_entities WHERE kind=? AND id=?', (kind, identifier))
    reconcile_known(db, now)
    db.execute('''UPDATE zen_mirror_state SET cursor=?,generation=generation+1,needs_full=0,ready=1,
                  refreshed_at=?,error_code=NULL WHERE id=1''', (data.server_timestamp, now))
    db.execute("INSERT OR REPLACE INTO service_meta VALUES ('zenmoney_refreshed_at',?)", (str(now),))
    from . import predictions
    predictions.rebuild_evidence(db)
    predictions.prepare(db, now)
    db.execute("UPDATE capture_errors SET resolved=1 WHERE code='zenmoney_unavailable'")
    db.execute('''UPDATE capture_jobs SET delivery_state='waiting' WHERE delivery_state='blocked'
        AND NOT EXISTS (SELECT 1 FROM capture_errors e WHERE e.job_id=capture_jobs.id AND e.resolved=0
                        AND e.code IN ('delivery_uncertain','existing_ownership_unknown','zenmoney_account_unavailable'))''')
    return True


class Mirror:
    def __init__(self, path, client, token, clock, *, sleep=time.sleep):
        self.path, self.client, self.clock, self.sleep = path, client, clock, sleep
        self.source_hash = sha256(token.encode()).hexdigest() if client is not None else None
        self.lock = threading.RLock()
        self.context_deferred = not self.bind_context()

    def bind_context(self):
        with storage.transaction(self.path) as db:
            legacy = db.execute("SELECT value FROM service_meta WHERE key='migration_hold'").fetchone()
            if participant.blocked(db) or legacy is not None and legacy[0] == 'exported':
                return False
            state = db.execute('SELECT * FROM zen_mirror_state WHERE id=1').fetchone()
            if self.client is None or state['source_hash'] != self.source_hash:
                db.execute('''UPDATE zen_mirror_state SET source_hash=?,cursor=0,needs_full=1,ready=0,
                    generation=generation+1,error_code=NULL WHERE id=1''', (self.source_hash,))
                db.execute("DELETE FROM service_meta WHERE key='zenmoney_refreshed_at'")
                db.execute('DELETE FROM category_predictions')
        return True

    def fresh(self, state, now):
        return bool(self.client is not None and state['source_hash'] == self.source_hash and state['ready']
                    and state['refreshed_at'] is not None and 0 <= now - state['refreshed_at'] <= CATALOG_MAX_AGE)

    def info(self):
        with storage.transaction(self.path, read_only=True) as db:
            state = db.execute('SELECT * FROM zen_mirror_state WHERE id=1').fetchone()
        stamp = state['refreshed_at'] if self.client is not None and state['source_hash'] == self.source_hash else None
        return {'zenmoney_state': ('not_configured' if self.client is None else 'ready' if self.fresh(state, self.clock()) else 'unavailable'),
                'last_zenmoney_refresh': datetime.fromtimestamp(stamp, timezone.utc) if stamp is not None else None}

    def failed(self, generation):
        with storage.transaction(self.path) as db:
            state = db.execute('SELECT * FROM zen_mirror_state WHERE id=1').fetchone()
            if state['generation'] == generation and state['source_hash'] == self.source_hash:
                db.execute("UPDATE zen_mirror_state SET ready=0,error_code='zenmoney_unavailable' WHERE id=1")
                db.execute("DELETE FROM service_meta WHERE key='zenmoney_refreshed_at'")
                db.execute('DELETE FROM category_predictions')

    def refresh(self, *, force=False, transactions=False):
        if self.context_deferred:
            if not self.bind_context():
                raise SyncError('migration_paused')
            self.context_deferred = False
        else:
            with storage.transaction(self.path, read_only=True) as db:
                if participant.blocked(db):
                    raise SyncError('migration_paused')
        if self.client is None:
            with storage.transaction(self.path, read_only=True) as db:
                generation = db.execute('SELECT generation FROM zen_mirror_state WHERE id=1').fetchone()[0]
            self.failed(generation)
            raise SyncError('zenmoney_unavailable', 503, retryable=True)
        with self.lock:
            for contention in range(2):
                with storage.transaction(self.path, read_only=True) as db:
                    state = db.execute('SELECT * FROM zen_mirror_state WHERE id=1').fetchone()
                if state['source_hash'] != self.source_hash:
                    raise SyncError('zenmoney_unavailable', 503, retryable=True)
                if self.fresh(state, self.clock()) and (not transactions and (not force or contention)):
                    return
                cursor = 0 if state['needs_full'] else state['cursor']
                try:
                    for attempt in range(2):
                        try:
                            diff = self.client.read_diff(cursor, force_fetch=FORCE_FETCH if state['needs_full'] else ['transaction'] if transactions else None)
                            break
                        except (TransportError, HTTPError) as exc:
                            temporary = isinstance(exc, TransportError) or exc.status_code == 429 or exc.status_code >= 500
                            if not temporary or attempt:
                                raise
                            self.sleep(0.1)
                    with storage.transaction(self.path) as db:
                        if apply_diff(db, diff, expected_generation=state['generation'], source_hash=self.source_hash,
                                      requested_cursor=cursor, full=bool(state['needs_full']), now=self.clock()):
                            return
                except (TransportError, HTTPError, ResponseError, ValueError, sqlite3.Error):
                    self.failed(state['generation'])
                    raise SyncError('zenmoney_unavailable', 503, retryable=True) from None
                except Exception:
                    # An unexpected projection fault also revokes freshness;
                    # keep the original fault for the generic redacted handler.
                    self.failed(state['generation'])
                    raise
            raise SyncError('zenmoney_unavailable', 503, retryable=True)
