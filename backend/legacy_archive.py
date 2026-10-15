"""Lossless, deterministic SQLite snapshot archive. Source SQL is data, never code."""
import base64
from hashlib import sha256
import json


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def packed(values):
    def cell(value):
        if value is None:
            return ['null', None]
        if isinstance(value, bytes):
            return ['blob', base64.b64encode(value).decode('ascii')]
        if isinstance(value, float):
            return ['real', value.hex()]
        if isinstance(value, int):
            return ['integer', str(value)]
        return ['text', value]
    return json.dumps([cell(v) for v in values], ensure_ascii=True, separators=(',', ':'))


def objects(source):
    return list(source.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))


def table_columns(source, name):
    return [r[1] for r in source.execute('PRAGMA table_info(' + quote(name) + ')')]


def rows(source, name):
    columns = table_columns(source, name)
    # No dependency on rowid (also works for WITHOUT ROWID and shadowed rowid).
    order = ','.join('typeof(' + quote(c) + '),' + quote(c) + ' COLLATE BINARY' for c in columns)
    return source.execute('SELECT * FROM ' + quote(name) + ' ORDER BY ' + order)


def fingerprint(source):
    digest, counts = sha256(), {}
    for obj in objects(source):
        digest.update((packed(obj) + '\n').encode())
        if obj['type'] == 'table':
            name = obj['name']
            digest.update((packed(table_columns(source, name)) + '\n').encode())
            count = 0
            for row in rows(source, name):
                digest.update((packed(row) + '\n').encode())
                count += 1
            counts[name] = count
    return digest.hexdigest(), counts


def copy_archive(source, target, digest):
    for obj in objects(source):
        columns = table_columns(source, obj['name']) if obj['type'] == 'table' else None
        target.execute('INSERT INTO legacy_objects VALUES (?,?,?,?,?,?)',
                       (digest, *obj, json.dumps(columns) if columns is not None else None))
        if columns is not None:
            target.executemany('INSERT INTO legacy_rows VALUES (?,?,?,?)',
                              ((digest, obj['name'], i, packed(row)) for i, row in enumerate(rows(source, obj['name']))))


def verify_archive(db, digest, counts):
    actual = dict(db.execute('SELECT table_name,count(*) FROM legacy_rows WHERE source_digest=? GROUP BY table_name', (digest,)))
    if any(actual.get(name, 0) != count for name, count in counts.items()) or set(actual) - counts.keys():
        raise RuntimeError('Legacy archive row count mismatch')
    # Check archived content, not just counts, on every repeated conversion.
    result = sha256()
    for obj in db.execute('SELECT type,name,table_name,sql,columns_json FROM legacy_objects WHERE source_digest=? ORDER BY type,name', (digest,)):
        result.update((packed(tuple(obj)[:4]) + '\n').encode())
        if obj['type'] == 'table':
            result.update((packed(json.loads(obj['columns_json'])) + '\n').encode())
            for row in db.execute('SELECT values_json FROM legacy_rows WHERE source_digest=? AND table_name=? ORDER BY ordinal', (digest, obj['name'])):
                result.update((row[0] + '\n').encode())
    if result.hexdigest() != digest:
        raise RuntimeError('Legacy archive content mismatch')
