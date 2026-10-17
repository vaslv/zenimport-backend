"""Canonical account discovery and optimistic settings; mirror reads only."""
from datetime import datetime, timezone
import json
from uuid import uuid4

from .capture_policy import canonical, capabilities, fail, get, today, validate_discovery

CATALOG_MAX_AGE = 300
ORDINARY_TYPES = {'cash', 'ccard', 'checking', 'deposit', 'emoney'}


def account_view(row, discovery=None):
    result = {key: row[key] for key in ('id', 'source_key', 'label', 'masked_number', 'currency', 'scale',
        'supported', 'unsupported_reason', 'settings_version', 'selection', 'zenmoney_account_id', 'import_start', 'last_complete_end')}
    if discovery is not None:
        result.update({key: discovery[key] for key in ('supported', 'unsupported_reason', 'label', 'masked_number')})
    result['supported'] = bool(result['supported'])
    return result


def connection_view(db, identifier):
    row = get(db, 'connections', identifier)
    accounts = []
    for found in db.execute('SELECT * FROM connection_discovery WHERE connection_id=? AND present=1 ORDER BY account_id', (row['id'],)):
        accounts.append(account_view(get(db, 'bank_accounts', found['account_id']), found))
    name = db.execute('SELECT label FROM connection_labels WHERE connection_id=?', (row['id'],)).fetchone()
    bank = {'bidv': 'BIDV', 'vietinbank': 'VietinBank'}.get(row['bank'], row['bank'])
    return {'id': row['id'], 'bank': row['bank'], 'accounts_complete': bool(row['accounts_complete']), 'accounts': accounts,
            'profile_key': row['profile_key'], 'profile_identity': row['profile_identity'],
            'label': name[0] if name else bank + ' ' + row['id'][:6]}


def connection_catalog(db, bank):
    if bank not in {'bidv', 'vietinbank'}:
        fail('unsupported_bank', 422)
    return {'connections': [connection_view(db, row[0]) for row in db.execute('SELECT id FROM connections WHERE bank=? ORDER BY id', (bank,))]}


def rename_connection(db, identifier, data, now):
    get(db, 'connections', identifier)
    db.execute('INSERT INTO connection_labels VALUES (?,?) ON CONFLICT(connection_id) DO UPDATE SET label=excluded.label', (str(identifier), data.label))
    return connection_view(db, identifier)


def unlink_account(db, identifier, account_id, now):
    from .capture import invalidate_accounts
    get(db, 'connections', identifier)
    row = db.execute('SELECT 1 FROM connection_accounts WHERE connection_id=? AND account_id=?', (str(identifier), str(account_id))).fetchone()
    if row is None:
        fail('not_found', 404)
    db.execute('UPDATE connection_discovery SET present=0 WHERE connection_id=? AND account_id=?', (str(identifier), str(account_id)))
    invalidate_accounts(db, [str(account_id)], 'settings_conflict', now, connection_id=str(identifier))
    db.execute('''UPDATE bank_accounts SET supported=EXISTS(SELECT 1 FROM connection_discovery d
               WHERE d.account_id=bank_accounts.id AND d.present=1 AND d.supported=1) WHERE id=?''', (str(account_id),))
    return connection_view(db, identifier)


def discover(db, data, now):
    from .capture import invalidate_accounts, invalidate_connection
    validate_discovery(data)
    caps = canonical(data.capabilities.model_dump(mode='json'))
    old = db.execute('SELECT * FROM connections WHERE bank=? AND profile_key=?', (data.bank, data.profile_key)).fetchone()
    identifier = old['id'] if old else str(uuid4())
    if old and old['profile_identity'] != data.profile_identity:
        fail('settings_conflict')
    if old and (old['capabilities_json'] != caps or old['connector_version'] != data.connector_version):
        invalidate_connection(db, identifier, now)
    db.execute('''INSERT INTO connections VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        connector_version=excluded.connector_version,timezone=excluded.timezone,
        capabilities_json=excluded.capabilities_json,accounts_complete=excluded.accounts_complete''',
        (identifier, data.bank, data.profile_key, data.profile_identity, data.connector_version,
         data.capabilities.timezone, caps, int(data.accounts_complete)))
    if data.label is not None:
        db.execute('INSERT INTO connection_labels VALUES (?,?) ON CONFLICT(connection_id) DO UPDATE SET label=excluded.label', (identifier, data.label))
    previous = {r[0] for r in db.execute('SELECT account_id FROM connection_discovery WHERE connection_id=? AND present=1', (identifier,))}
    if data.accounts_complete:
        db.execute('UPDATE connection_discovery SET present=0 WHERE connection_id=?', (identifier,))
    seen = set()
    for found in data.accounts:
        profile = data.profile_key if found.identity_scope == 'profile' else ''
        account = db.execute('''SELECT * FROM bank_accounts WHERE bank=? AND identity_scope=? AND profile_key=? AND source_key=? AND currency=?''',
                             (data.bank, found.identity_scope, profile, found.source_key, found.currency)).fetchone()
        if account and account['scale'] != found.scale:
            fail('unsupported_currency', 422)
        account_id = account['id'] if account else str(uuid4())
        if account is None:
            db.execute('''INSERT INTO bank_accounts (id,bank,identity_scope,profile_key,source_key,currency,scale,label,masked_number,supported,unsupported_reason)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (account_id, data.bank, found.identity_scope, profile, found.source_key,
                       found.currency, found.scale, found.label, found.masked_number, int(found.supported), found.unsupported_reason))
        db.execute('INSERT OR IGNORE INTO connection_accounts VALUES (?,?)', (identifier, account_id))
        db.execute('''INSERT INTO connection_discovery VALUES (?,?,1,?,?,?,?) ON CONFLICT(connection_id,account_id) DO UPDATE SET
            present=1,supported=excluded.supported,unsupported_reason=excluded.unsupported_reason,label=excluded.label,masked_number=excluded.masked_number''',
                   (identifier, account_id, int(found.supported), found.unsupported_reason, found.label, found.masked_number))
        seen.add(account_id)
        if not found.supported:
            invalidate_accounts(db, [account_id], 'capture_context_changed', now, connection_id=identifier)
    if data.accounts_complete:
        invalidate_accounts(db, previous - seen, 'capture_context_changed', now, connection_id=identifier)
    db.execute('''UPDATE bank_accounts SET supported=EXISTS(SELECT 1 FROM connection_discovery d
               WHERE d.account_id=bank_accounts.id AND d.present=1 AND d.supported=1)''')
    return connection_view(db, identifier)


def catalog(db, now):
    refreshed = db.execute("SELECT value FROM service_meta WHERE key='zenmoney_refreshed_at'").fetchone()
    if not refreshed or not 0 <= now - int(refreshed[0]) <= CATALOG_MAX_AGE:
        fail('zenmoney_unavailable', 503, retryable=True)
    instruments = {str(r['id']): json.loads(r['data']) for r in db.execute("SELECT id,data FROM zen_entities WHERE kind='instrument' AND deleted=0")}
    result = []
    for row in db.execute("SELECT * FROM zen_entities WHERE kind='account' AND deleted=0 ORDER BY id"):
        value = json.loads(row['data'])
        instrument = instruments.get(str(value.get('instrument')))
        if value.get('deleted') or value.get('archive') or value.get('type') not in ORDINARY_TYPES or not instrument or instrument.get('deleted'):
            continue
        currency = instrument.get('shortTitle')
        if not isinstance(currency, str) or not currency or type(value.get('instrument')) is not int or value['instrument'] <= 0:
            continue
        result.append({'id': row['id'], 'title': value.get('title', ''), 'currency': currency, 'instrument': value['instrument']})
    return {'refreshed_at': datetime.fromtimestamp(int(refreshed[0]), timezone.utc), 'accounts': result}


def settings(db, identifier, data, now):
    from .capture import invalidate_accounts
    row = get(db, 'bank_accounts', identifier)
    desired = (data.selection, data.zenmoney_account_id, data.import_start.isoformat() if data.import_start else None)
    current = (row['selection'], row['zenmoney_account_id'], row['import_start'])
    if desired == current and data.expected_version <= row['settings_version']:
        return account_view(row)
    if data.expected_version != row['settings_version']:
        fail('settings_conflict')
    if data.selection == 'enabled':
        if not row['supported']:
            fail('unsupported_currency', 422)
        connection = db.execute('''SELECT c.* FROM connections c JOIN connection_discovery d ON d.connection_id=c.id
            WHERE d.account_id=? AND d.present=1 AND d.supported=1 ORDER BY c.id LIMIT 1''', (row['id'],)).fetchone()
        if connection is None:
            fail('account_disabled')
        caps = capabilities(connection)
        if data.import_start > today(now, caps['timezone']):
            fail('validation_error', 422)
        earliest = caps.get('earliest_date')
        if earliest and data.import_start.isoformat() < earliest:
            fail('validation_error', 422)
        target = next((a for a in catalog(db, now)['accounts'] if a['id'] == data.zenmoney_account_id), None)
        if target is None or target['currency'] != row['currency']:
            fail('zenmoney_account_unavailable')
        prior = db.execute('''SELECT zenmoney_account_id FROM account_bindings WHERE account_id=?
                             ORDER BY created_at DESC,settings_version DESC LIMIT 1''', (row['id'],)).fetchone()
        old_target = row['zenmoney_account_id'] or (prior[0] if prior else None)
        unknown = db.execute('''SELECT 1 FROM bank_transactions b JOIN bank_processing p ON p.bank_id=b.id
            LEFT JOIN deliveries d ON d.bank_id=b.id WHERE b.account_id=? AND (p.state='uncertain' OR d.state IN ('attempting','uncertain')
            OR (d.state='prepared' AND d.ever_attempted=1)) LIMIT 1''', (row['id'],)).fetchone()
        if old_target and old_target != data.zenmoney_account_id and unknown:
            fail('delivery_uncertain')
    version = row['settings_version'] + 1
    db.execute('UPDATE bank_accounts SET selection=?,zenmoney_account_id=?,import_start=?,settings_version=? WHERE id=?', (*desired, version, row['id']))
    db.execute('''UPDATE deliveries SET needs_rebuild=1,authorized_job_id=NULL WHERE state='prepared' AND ever_attempted=0
                  AND bank_id IN (SELECT id FROM bank_transactions WHERE account_id=?)''', (row['id'],))
    if data.selection == 'enabled':
        db.execute('INSERT INTO account_bindings VALUES (?,?,?,?,?,?)', (str(uuid4()), row['id'], data.zenmoney_account_id, version, 'settings', now))
    invalidate_accounts(db, [row['id']], 'settings_conflict', now)
    # Coverage is bank-reading evidence, independent of target. Recompute its
    # contiguous frontier after moving the requested start backwards or forwards.
    from .capture import update_frontier
    update_frontier(db, row['id'])
    return account_view(get(db, 'bank_accounts', row['id']))
