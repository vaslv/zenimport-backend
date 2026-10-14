"""Exact, versioned projections of the SDK entities with lossless diff archives."""
import time
from datetime import date
from decimal import Decimal
from . import jsoncodec as json
from zenmoney_api.protocol import KINDS, entity_key, validate_diff
from zenmoney_api.transactions import CREATION_DEFAULTS

TEXT, NUMBER, FLAG, DAY, IDENT, INTEGER, ARRAY = 'text', 'number', 'flag', 'date', 'id', 'integer', 'array'
MOVEMENT = {
    'incomeAccount': ('income_account', IDENT), 'outcomeAccount': ('outcome_account', IDENT),
    'incomeInstrument': ('income_instrument', IDENT), 'outcomeInstrument': ('outcome_instrument', IDENT),
    'income': ('income', NUMBER), 'outcome': ('outcome', NUMBER),
    'merchant': ('merchant_id', IDENT), 'payee': ('payee', TEXT), 'comment': ('comment', TEXT),
}
# SDK wire fields -> SQL column/type. Arrays of category IDs use child tables.
FIELDS = {
    'instrument': {'title': ('title', TEXT), 'shortTitle': ('code', TEXT), 'symbol': ('symbol', TEXT), 'rate': ('rate', NUMBER)},
    'company': {'title': ('title', TEXT), 'fullTitle': ('full_title', TEXT), 'www': ('www', TEXT), 'country': ('country_id', IDENT)},
    'country': {'title': ('title', TEXT)},
    'user': {'login': ('login', TEXT), 'parent': ('parent_id', IDENT), 'currency': ('currency_id', IDENT)},
    'account': {
        'user': ('user_id', IDENT), 'instrument': ('instrument_id', IDENT), 'company': ('company_id', IDENT),
        'title': ('title', TEXT), 'type': ('type', TEXT), 'archive': ('archive', FLAG), 'balance': ('balance', NUMBER),
        'role': ('role_id', IDENT), 'syncID': ('sync_ids', ARRAY), 'startBalance': ('start_balance', NUMBER),
        'creditLimit': ('credit_limit', NUMBER), 'inBalance': ('in_balance', FLAG), 'savings': ('savings', FLAG),
        'enableCorrection': ('enable_correction', FLAG), 'enableSMS': ('enable_sms', FLAG), 'private': ('private', FLAG),
        'balanceCorrectionType': ('balance_correction_type', TEXT), 'capitalization': ('capitalization', FLAG),
        'percent': ('percent', NUMBER), 'startDate': ('start_date', DAY), 'endDateOffset': ('end_date_offset', INTEGER),
        'endDateOffsetInterval': ('end_date_offset_interval', TEXT), 'payoffStep': ('payoff_step', INTEGER),
        'payoffInterval': ('payoff_interval', TEXT),
    },
    'tag': {'user': ('user_id', IDENT), 'parent': ('parent_id', IDENT), 'title': ('title', TEXT),
            'icon': ('icon', TEXT), 'picture': ('picture', TEXT), 'color': ('color', INTEGER),
            'showIncome': ('show_income', FLAG), 'showOutcome': ('show_outcome', FLAG),
            'budgetIncome': ('budget_income', FLAG), 'budgetOutcome': ('budget_outcome', FLAG),
            'required': ('required', FLAG), 'archive': ('archive', FLAG), 'staticId': ('static_id', TEXT)},
    'merchant': {'user': ('user_id', IDENT), 'title': ('title', TEXT), 'mcc': ('mcc', INTEGER)},
    'budget': {'user': ('user_id', IDENT), 'date': ('date', DAY), 'tag': ('tag_id', IDENT),
               'income': ('income', NUMBER), 'outcome': ('outcome', NUMBER),
               'incomeLock': ('income_lock', FLAG), 'outcomeLock': ('outcome_lock', FLAG),
               'isIncomeForecast': ('is_income_forecast', FLAG), 'isOutcomeForecast': ('is_outcome_forecast', FLAG)},
    'reminder': {**MOVEMENT, 'user': ('user_id', IDENT), 'startDate': ('start_date', DAY), 'endDate': ('end_date', DAY),
                 'interval': ('interval', TEXT), 'step': ('step', INTEGER), 'points': ('points', ARRAY), 'notify': ('notify', FLAG)},
    'reminderMarker': {**MOVEMENT, 'user': ('user_id', IDENT), 'reminder': ('reminder_id', IDENT), 'date': ('date', DAY),
                       'state': ('state', TEXT), 'notify': ('notify', FLAG), 'isForecast': ('is_forecast', FLAG)},
    'transaction': {**MOVEMENT, 'user': ('user_id', IDENT), 'date': ('date', DAY), 'created': ('created', INTEGER),
        'opIncome': ('op_income', NUMBER), 'opOutcome': ('op_outcome', NUMBER),
        'opIncomeInstrument': ('op_income_instrument', IDENT), 'opOutcomeInstrument': ('op_outcome_instrument', IDENT),
        'originalPayee': ('original_payee', TEXT), 'hold': ('hold', FLAG), 'mcc': ('mcc', INTEGER),
        'reminderMarker': ('reminder_marker_id', IDENT), 'latitude': ('latitude', NUMBER), 'longitude': ('longitude', NUMBER),
        'viewed': ('viewed', FLAG), 'qrCode': ('qr_code', TEXT), 'source': ('source', TEXT),
        'incomeBankID': ('income_bank_id', TEXT), 'outcomeBankID': ('outcome_bank_id', TEXT)},
}
TABLES = {kind: 'zen_' + ('reminder_marker' if kind == 'reminderMarker' else kind) for kind in KINDS}
TAG_TABLES = {'transaction': 'zen_transaction_tags', 'reminder': 'zen_reminder_tags', 'reminderMarker': 'zen_reminder_marker_tags'}


def convert(value, kind):
    if value is None:
        return None
    if kind == IDENT:
        if type(value) not in (str, int):
            raise ValueError('Invalid related identifier')
        return str(value)
    if kind == FLAG:
        if type(value) is not bool:
            raise ValueError('Invalid boolean')
        return int(value)
    if kind == INTEGER:
        if type(value) is not int:
            raise ValueError('Invalid integer')
        return value
    if kind == NUMBER:
        if type(value) not in (int, float, Decimal):
            raise ValueError('Invalid amount')
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError('Non-finite amount')
        return str(number)
    if kind == ARRAY:
        if not isinstance(value, list):
            raise ValueError('Invalid array')
        return json.dumps(value)
    if not isinstance(value, str):
        raise ValueError('Invalid text field')
    if kind == DAY and date.fromisoformat(value).isoformat() != value:
        raise ValueError('Invalid calendar date')
    return value


def sql_type(kind):
    return 'INTEGER' if kind in (FLAG, INTEGER) else 'TEXT'


def create_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS zen_sync_runs (
        id INTEGER PRIMARY KEY, received_at INTEGER NOT NULL, requested_cursor INTEGER,
        response_cursor INTEGER, origin TEXT NOT NULL, payload TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS zen_deletions (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES zen_sync_runs(id),
        kind TEXT NOT NULL, entity_id TEXT NOT NULL, data TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS zen_migrations (
        version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL, report TEXT NOT NULL)''')
    for kind, table in TABLES.items():
        columns = ','.join(f'{column} {sql_type(value_type)}' for column, value_type in FIELDS[kind].values())
        db.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
            id TEXT PRIMARY KEY, {columns}, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id))''')
        present = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
        for column, value_type in FIELDS[kind].values():
            if column not in present:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {sql_type(value_type)}')
    for kind, table in TAG_TABLES.items():
        owner = 'transaction_id' if kind == 'transaction' else 'owner_id'
        db.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
            {owner} TEXT NOT NULL REFERENCES {TABLES[kind]}(id) ON DELETE CASCADE,
            tag_id TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY({owner},tag_id))''')
        db.execute(f'CREATE INDEX IF NOT EXISTS {table}_tag ON {table}(tag_id)')
    for column in ('date', 'income_account', 'outcome_account', 'payee', 'merchant_id', 'reminder_marker_id'):
        db.execute(f'CREATE INDEX IF NOT EXISTS zen_transaction_{column} ON zen_transaction({column})')
    for table, columns in [('zen_account', ('user_id', 'instrument_id', 'company_id')),
                           ('zen_tag', ('user_id', 'parent_id')), ('zen_budget', ('user_id', 'date', 'tag_id')),
                           ('zen_reminder_marker', ('reminder_id', 'date'))]:
        for column in columns:
            db.execute(f'CREATE INDEX IF NOT EXISTS {table}_{column} ON {table}({column})')


def project(db, kind, item, run_id):
    key = entity_key(kind, item)
    fields = FIELDS[kind]
    columns = ['id', *[column for column, _ in fields.values()], 'changed', 'deleted', 'data', 'run_id']
    changed = item.get('changed')
    if changed is not None and type(changed) is not int:
        raise ValueError('Invalid changed timestamp')
    projected = []
    for field, (_, type_) in fields.items():
        try:
            projected.append(convert(item.get(field), type_))
        except ValueError:
            raise ValueError(f'Invalid {kind}.{field} ({type(item.get(field)).__name__})') from None
    values = [key, *projected,
              changed, convert(item.get('deleted', False), FLAG), json.dumps(item), run_id]
    updates = ','.join(f'{column}=excluded.{column}' for column in columns[1:])
    db.execute(f'INSERT INTO {TABLES[kind]} ({",".join(columns)}) VALUES ({",".join("?" for _ in values)}) '
               f'ON CONFLICT(id) DO UPDATE SET {updates}', values)
    if kind in TAG_TABLES:
        tags = item.get('tag') or []
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError('Invalid movement categories')
        owner = 'transaction_id' if kind == 'transaction' else 'owner_id'
        db.execute(f'DELETE FROM {TAG_TABLES[kind]} WHERE {owner}=?', (key,))
        for position, tag in enumerate(dict.fromkeys(tags)):
            db.execute(f'INSERT INTO {TAG_TABLES[kind]} VALUES (?,?,?)', (key, tag, position))


def complete_creation(payload):
    return {**CREATION_DEFAULTS, 'mcc': None, **payload}


def migrate(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > 11:
        raise RuntimeError('Database schema is newer than this application')
    create_schema(db)
    legacy = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='zen_entities'").fetchone()
    if version < 2 and legacy:
        rows = db.execute('SELECT kind,data FROM zen_entities').fetchall()
        payload = {kind: [] for kind in KINDS}
        for row in rows:
            if row['kind'] in KINDS:
                payload[row['kind']].append(json.loads(row['data']))
        run_id = db.execute('INSERT INTO zen_sync_runs(received_at,origin,payload) VALUES (?,?,?)',
                            (int(time.time()), 'migration-v1', json.dumps(payload))).lastrowid if rows else None
        for kind in KINDS:
            for item in payload[kind]:
                project(db, kind, item, run_id)
        if rows:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('needs_full_sync','1')")
        db.execute('PRAGMA user_version=2')
    if version < 11:
        # Rebuild CURRENT entities only. Hard-deleted entities must not be resurrected.
        # Parse one archived run at a time, not the entire archive in memory.
        by_run = {}
        for kind, table in TABLES.items():
            for row in db.execute(f'SELECT id,data,run_id FROM {table}').fetchall():
                by_run.setdefault(row['run_id'], []).append((kind, row['id'], row['data']))
        restored, missing = 0, 0
        for run_id, rows in by_run.items():
            run = db.execute('SELECT payload,origin FROM zen_sync_runs WHERE id=?', (run_id,)).fetchone()
            raw = json.loads(run['payload']) if run else {}
            precise = {(kind, entity_key(kind, item)): item for kind in KINDS for item in raw.get(kind, [])}
            for kind, key, data in rows:
                item = precise.get((kind, key))
                if item is not None:
                    restored += 1
                else:
                    item = json.loads(data)
                    missing += 1
                project(db, kind, item, run_id)
        if missing:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('needs_full_sync','1')")
        # Add SDK creation defaults only to never-sent drafts; attempted writes stay immutable.
        for table, condition in [('creation_jobs', "state='prepared'"), ('outbox', "state='queued'")]:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists:
                for row in db.execute(f'SELECT rowid,payload FROM {table} WHERE {condition}').fetchall():
                    db.execute(f'UPDATE {table} SET payload=? WHERE rowid=?',
                               (json.dumps(complete_creation(json.loads(row['payload']))), row['rowid']))
        db.execute('INSERT OR REPLACE INTO zen_migrations VALUES (?,?,?)',
                   (11, int(time.time()), json.dumps({'restored_from_archives': restored, 'without_archive': missing})))
        if legacy:
            db.execute('DROP TABLE zen_entities')


def store_diff(db, result, requested_cursor=None, *, full_collections=None):
    validate_diff(result)
    if requested_cursor is not None and result['serverTimestamp'] < requested_cursor:
        raise ValueError('Synchronization cursor moved backwards')
    raw = getattr(result, 'raw_json', None) or json.dumps(result)
    precise = json.loads(raw)
    validate_diff(precise)
    run_id = db.execute('''INSERT INTO zen_sync_runs(received_at,requested_cursor,response_cursor,origin,payload)
        VALUES (?,?,?,?,?)''', (int(time.time()), requested_cursor, precise['serverTimestamp'], 'diff', raw)).lastrowid
    for kind in KINDS:
        for item in precise.get(kind, []):
            project(db, kind, item, run_id)
        # Only explicitly fetched, present collections are authoritative snapshots.
        # No NOT IN list: a full history may contain tens of thousands of IDs.
        if kind in (full_collections or ()) and kind in precise:
            db.execute(f'DELETE FROM {TABLES[kind]} WHERE run_id IS NULL OR run_id<>?', (run_id,))
    for item in precise.get('deletion', []):
        kind, key = item['object'], str(item['id'])
        db.execute('INSERT INTO zen_deletions(run_id,kind,entity_id,data) VALUES (?,?,?,?)',
                   (run_id, kind, key, json.dumps(item)))
        if kind in TABLES:
            if kind == 'budget':
                raise ValueError('Budget deletion identity requires an observed API fixture')
            db.execute(f'DELETE FROM {TABLES[kind]} WHERE id=?', (key,))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('cursor',?)", (str(precise['serverTimestamp']),))


def summary(db):
    counts = {kind: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for kind, table in TABLES.items()}
    row = db.execute('SELECT count(*) AS count,min(date) AS first_date,max(date) AS last_date FROM zen_transaction WHERE deleted=0').fetchone()
    return {'counts': counts, 'transactions': dict(row), 'coverage_verified': False,
            'schema_version': db.execute('PRAGMA user_version').fetchone()[0]}


def history(db, *, limit=100, offset=0, start=None, end=None, account=None, include_deleted=False):
    where, params = [], []
    if not include_deleted:
        where.append('deleted=0')
    if start:
        where.append('date>=?'); params.append(str(start))
    if end:
        where.append('date<=?'); params.append(str(end))
    if account:
        where.append('(income_account=? OR outcome_account=?)'); params.extend([account, account])
    clause = ' WHERE ' + ' AND '.join(where) if where else ''
    total = db.execute('SELECT count(*) FROM zen_transaction' + clause, params).fetchone()[0]
    rows = db.execute('SELECT data,income,outcome FROM zen_transaction' + clause + ' ORDER BY date DESC,id LIMIT ? OFFSET ?',
                      [*params, limit, offset]).fetchall()
    items = []
    for row in rows:
        item = json.loads(row['data'])
        item['_amounts'] = {'income': row['income'], 'outcome': row['outcome']}
        items.append(item)
    return {'items': items, 'total': total, 'offset': offset, 'limit': limit, 'has_more': offset + len(items) < total}
