"""Versioned projections over lossless ZenMoney diff archives."""
import json
import time
from datetime import date
from decimal import Decimal
from zenimport_zenmoney import KINDS, entity_key, validate_diff

# Each entry is API field -> (SQL column, converter). Fields not projected remain in data/archive.
TEXT, NUMBER, FLAG, DAY, IDENT = 'text', 'number', 'flag', 'date', 'id'
FIELDS = {
    'instrument': {'title': ('title', TEXT), 'shortTitle': ('code', TEXT), 'rate': ('rate', NUMBER)},
    'company': {'title': ('title', TEXT), 'country': ('country_id', IDENT)},
    'country': {'title': ('title', TEXT)},
    'user': {'login': ('login', TEXT), 'parent': ('parent_id', IDENT), 'currency': ('currency_id', IDENT)},
    'account': {'user': ('user_id', IDENT), 'instrument': ('instrument_id', IDENT), 'company': ('company_id', IDENT),
                'title': ('title', TEXT), 'type': ('type', TEXT), 'archive': ('archive', FLAG), 'balance': ('balance', NUMBER)},
    'tag': {'user': ('user_id', IDENT), 'parent': ('parent_id', IDENT), 'title': ('title', TEXT)},
    'merchant': {'user': ('user_id', IDENT), 'title': ('title', TEXT)},
    'budget': {'user': ('user_id', IDENT), 'date': ('date', DAY), 'tag': ('tag_id', IDENT),
               'income': ('income', NUMBER), 'outcome': ('outcome', NUMBER)},
    'reminder': {'user': ('user_id', IDENT), 'startDate': ('start_date', DAY), 'endDate': ('end_date', DAY),
                 'income': ('income', NUMBER), 'outcome': ('outcome', NUMBER)},
    'reminderMarker': {'user': ('user_id', IDENT), 'reminder': ('reminder_id', IDENT), 'date': ('date', DAY)},
    'transaction': {'user': ('user_id', IDENT), 'date': ('date', DAY), 'incomeAccount': ('income_account', IDENT),
        'outcomeAccount': ('outcome_account', IDENT), 'incomeInstrument': ('income_instrument', IDENT),
        'outcomeInstrument': ('outcome_instrument', IDENT), 'income': ('income', NUMBER), 'outcome': ('outcome', NUMBER),
        'opIncome': ('op_income', NUMBER), 'opOutcome': ('op_outcome', NUMBER), 'merchant': ('merchant_id', IDENT),
        'payee': ('payee', TEXT), 'originalPayee': ('original_payee', TEXT), 'comment': ('comment', TEXT), 'hold': ('hold', FLAG)},
}
TABLES = {kind: 'zen_' + ('reminder_marker' if kind == 'reminderMarker' else kind) for kind in KINDS}


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
    if kind == NUMBER:
        if type(value) not in (int, float, Decimal):
            raise ValueError('Invalid amount')
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError('Non-finite amount')
        return str(number)
    if not isinstance(value, str):
        raise ValueError('Invalid text field')
    if kind == DAY:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError('Invalid calendar date')
    return value


def create_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS zen_sync_runs (
        id INTEGER PRIMARY KEY, received_at INTEGER NOT NULL, requested_cursor INTEGER,
        response_cursor INTEGER, origin TEXT NOT NULL, payload TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS zen_deletions (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES zen_sync_runs(id),
        kind TEXT NOT NULL, entity_id TEXT NOT NULL, data TEXT NOT NULL)''')
    for kind, table in TABLES.items():
        columns = ','.join(f'{column} {"INTEGER" if value_type == FLAG else "TEXT"}'
                           for column, value_type in FIELDS[kind].values())
        db.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
            id TEXT PRIMARY KEY, {columns}, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS zen_transaction_tags (
        transaction_id TEXT NOT NULL REFERENCES zen_transaction(id) ON DELETE CASCADE,
        tag_id TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(transaction_id,tag_id))''')
    for column in ('date', 'income_account', 'outcome_account', 'payee', 'merchant_id'):
        db.execute(f'CREATE INDEX IF NOT EXISTS zen_transaction_{column} ON zen_transaction({column})')


def project(db, kind, item, run_id, precise=None):
    source = precise or item
    key = entity_key(kind, item)
    fields = FIELDS[kind]
    columns = ['id', *[column for column, _ in fields.values()], 'changed', 'deleted', 'data', 'run_id']
    changed = item.get('changed')
    if changed is not None and type(changed) is not int:
        raise ValueError('Invalid changed timestamp')
    values = [key, *[convert(source.get(field), type_) for field, (_, type_) in fields.items()],
              changed, convert(item.get('deleted', False), FLAG), json.dumps(item, ensure_ascii=False), run_id]
    updates = ','.join(f'{column}=excluded.{column}' for column in columns[1:])
    db.execute(f'INSERT INTO {TABLES[kind]} ({",".join(columns)}) VALUES ({",".join("?" for _ in values)}) '
               f'ON CONFLICT(id) DO UPDATE SET {updates}', values)
    if kind == 'transaction':
        tags = item.get('tag') or []
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError('Invalid transaction categories')
        db.execute('DELETE FROM zen_transaction_tags WHERE transaction_id=?', (key,))
        for position, tag in enumerate(dict.fromkeys(tags)):
            db.execute('INSERT INTO zen_transaction_tags VALUES (?,?,?)', (key, tag, position))


def migrate(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > 5:
        raise RuntimeError('Database schema is newer than this application')
    create_schema(db)
    if version < 2:
        # A v1 mirror has only its latest entity versions. Do not invent earlier history.
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


def store_diff(db, result, requested_cursor=None):
    validate_diff(result)
    if requested_cursor is not None and result['serverTimestamp'] < requested_cursor:
        raise ValueError('Synchronization cursor moved backwards')
    raw = getattr(result, 'raw_json', None) or json.dumps(result, ensure_ascii=False)
    precise = json.loads(raw, parse_float=Decimal)
    run_id = db.execute('''INSERT INTO zen_sync_runs(received_at,requested_cursor,response_cursor,origin,payload)
        VALUES (?,?,?,?,?)''', (int(time.time()), requested_cursor, result['serverTimestamp'], 'diff', raw)).lastrowid
    for kind in KINDS:
        for index, item in enumerate(result.get(kind, [])):
            project(db, kind, item, run_id, precise.get(kind, [])[index])
            db.execute('INSERT OR REPLACE INTO zen_entities VALUES (?,?,?)',
                       (kind, entity_key(kind, item), json.dumps(item)))
    for item in result.get('deletion', []):
        kind, key = item['object'], str(item['id'])
        db.execute('INSERT INTO zen_deletions(run_id,kind,entity_id,data) VALUES (?,?,?,?)',
                   (run_id, kind, key, json.dumps(item)))
        if kind in TABLES:
            if kind == 'budget':
                raise ValueError('Budget deletion identity requires an observed API fixture')
            db.execute(f'DELETE FROM {TABLES[kind]} WHERE id=?', (key,))
        db.execute('DELETE FROM zen_entities WHERE kind=? AND id=?', (kind, key))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('cursor',?)", (str(result['serverTimestamp']),))


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
        # Amount projections preserve decimals as strings; display never needs a binary float.
        item['_amounts'] = {'income': row['income'], 'outcome': row['outcome']}
        items.append(item)
    return {'items': items, 'total': total, 'offset': offset, 'limit': limit, 'has_more': offset + len(items) < total}
