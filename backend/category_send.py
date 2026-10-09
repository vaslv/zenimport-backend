"""Explicit category-only writes, durable intent, and independent remote verification."""
import hashlib
import json
import time
from .db import transaction, entities
from .service import fail, audit
from .zenmoney import apply_diff


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def content(tx):
    result = {k:v for k,v in tx.items() if k != 'changed'}
    # Observed API normalization: no original currency + null amount becomes zero.
    # Preserve missing fields and all nonzero or currency-qualified amounts.
    for field in ('opIncome', 'opOutcome'):
        if field in result and result[field] in (None, 0) and result.get(field+'Instrument') is None:
            result[field] = 0
    return result


def results(db):
    return [dict(r) for r in db.execute('SELECT zen_id,state,error FROM zen_category_sends')]


def refresh(path, client):
    # Force a fresh authoritative read. A write response need not echo the changes.
    with transaction(path) as db:
        row = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
        cursor = int(row[0]) if row else 0
        try:
            result = client.diff({'serverTimestamp': cursor, 'currentClientTimestamp': int(time.time()), 'forceFetch': ['transaction', 'tag', 'account']})
            apply_diff(db, result, cursor)
        except Exception:
            fail('Не удалось проверить свежие данные ZenMoney. Отправка не подтверждена.', 502)
        # Verify against the actual fresh response, never a stale mirror entry.
        remote = {t['id']:t for t in result.get('transaction', [])}
        for job in db.execute("SELECT * FROM zen_category_sends WHERE state!='sent'").fetchall():
            tx = remote.get(job['zen_id'])
            if tx and content(tx) == content(json.loads(job['payload'])):
                db.execute("UPDATE zen_category_sends SET state='sent',error=NULL WHERE zen_id=?", (job['zen_id'],))
                db.execute('DELETE FROM zen_category_drafts WHERE zen_id=? AND category_id=? AND updated_at=?',
                           (job['zen_id'], job['category_id'], job['draft_updated_at']))
                audit(db, 'category_sent', {'zen_id': job['zen_id'], 'category_id': job['category_id']})
            elif tx:
                state = 'not_applied' if content(tx) == content(json.loads(job['before_payload'])) else 'conflict'
                db.execute('UPDATE zen_category_sends SET state=?,error=? WHERE zen_id=?',
                           (state, 'Изменение не подтверждено. Проверьте запись и подготовьте отправку заново.', job['zen_id']))
            else:
                db.execute("UPDATE zen_category_sends SET error='Запись отсутствует в свежем ответе. Требуется повторная проверка.' WHERE zen_id=?", (job['zen_id'],))
        # Do not offer records absent from a successful full response (e.g. deletion).
        wanted = {r[0] for r in db.execute('SELECT zen_id FROM zen_category_drafts')}
        db.execute("INSERT OR REPLACE INTO meta VALUES ('category_fresh_ids',?)", (json.dumps(sorted(wanted & remote.keys())),))


def preview(db):
    txs = {t['id']:t for t in entities(db, 'transaction')}
    tags = {t['id']:t for t in entities(db, 'tag') if not t.get('deleted')}
    accounts = {a['id']:a for a in entities(db, 'account')}
    excluded = {r[0] for r in db.execute('SELECT zen_id FROM reconciliation_exclusions')}
    jobs = {r['zen_id']:dict(r) for r in db.execute('SELECT * FROM zen_category_sends')}
    fresh = db.execute("SELECT value FROM meta WHERE key='category_fresh_ids'").fetchone()
    fresh = set(json.loads(fresh[0])) if fresh else set()
    items = []
    for row in db.execute('SELECT * FROM zen_category_drafts ORDER BY zen_id'):
        draft = dict(row);tx = txs.get(row['zen_id']);reason = None
        if not tx or row['zen_id'] not in fresh or tx.get('deleted') or tx.get('hold'):
            reason = 'Операция недоступна в ZenMoney'
        elif tx.get('incomeAccount') != tx.get('outcomeAccount') or row['zen_id'] in excluded:
            reason = 'Перевод или исключённая операция'
        elif row['category_id'] not in tags:
            reason = 'Категория недоступна'
        elif sorted(tx.get('tag') or []) != json.loads(row['original_tags']):
            reason = 'Категория в ZenMoney уже изменилась; проверьте выбор'
        elif jobs.get(row['zen_id'], {}).get('state') == 'uncertain':
            reason = 'Сначала проверьте результат предыдущей отправки'
        tx = tx or {}
        items.append({'zen_id': row['zen_id'], 'date':tx.get('date'), 'income':tx.get('income'), 'outcome':tx.get('outcome'),
                      'account':accounts.get(tx.get('incomeAccount'),{}).get('title','Счёт'),
                      'instrument':tx.get('incomeInstrument'), 'payee':tx.get('payee') or tx.get('comment') or '',
                      'before_categories':[tags.get(t,{}).get('title',t) for t in tx.get('tag') or []],
                      'category':tags.get(row['category_id'],{}).get('title','Недоступна'), 'error':reason,
                      'snapshot':fingerprint([tx,draft,jobs.get(row['zen_id'])])})
    return items


def send(path, client, items):
    ids = [i.zen_id for i in items]
    if len(set(ids)) != len(ids): fail('Операция повторяется в запросе', 422)
    refresh(path, client)
    with transaction(path) as db:
        previews = {r['zen_id']:r for r in preview(db)}
        for item in items:
            current = previews.get(item.zen_id)
            if not current or current['error'] or current['snapshot'] != item.snapshot:
                fail('Данные изменились. Откройте просмотр отправки заново.')
        txs = {t['id']:t for t in entities(db, 'transaction')}
        changes = []
        for identifier in ids:
            draft = db.execute('SELECT * FROM zen_category_drafts WHERE zen_id=?',(identifier,)).fetchone()
            before = txs[identifier]
            payload = {**before, 'tag':[draft['category_id']], 'changed':max(int(time.time()), before.get('changed',0)+1)}
            db.execute('INSERT OR REPLACE INTO zen_category_sends VALUES (?,?,?,?,?,?,NULL)',
                       (identifier,draft['category_id'],draft['updated_at'],json.dumps(before),json.dumps(payload),'uncertain'))
            audit(db, 'category_send_intent', {'zen_id':identifier,'before':before,'payload':payload})
            changes.append((before,payload))
        cursor = int(db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0])
    try:
        # Write response is deliberately not used as the verification or sync cursor.
        client.update_categories(changes,cursor)
    except Exception:
        with transaction(path) as db:
            for identifier in ids:
                db.execute("UPDATE zen_category_sends SET error='Ответ на отправку не получен. Проверяем фактическое состояние.' WHERE zen_id=?",(identifier,))
    try:
        refresh(path, client)
    except Exception:
        pass  # Intent and drafts remain durable; verification can be repeated without writes.
    with transaction(path, read_only=True) as db:
        return {'results':[r for r in results(db) if r['zen_id'] in ids]}
