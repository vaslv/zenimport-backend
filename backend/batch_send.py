"""One explicit mixed write, reusing durable per-record intents and verification."""
import json
import time
from fastapi import HTTPException
from . import category_send, creation
from .db import transaction, entities
from .service import fail, get_row, audit


def target(db, scope):
    account = get_row(db, 'bank_accounts', scope.account)
    if account['bank'] != scope.bank or not account['zenmoney_account']:
        fail('Счёт не относится к выбранному банку или не связан с ZenMoney')
    return account['zenmoney_account']


def refresh(path, client):
    creation.refresh(path, client)
    category_send.refresh(path, client)


def preview(db, scope):
    zen_account = target(db, scope)
    txs = {t['id']: t for t in entities(db, 'transaction')}
    instruments = {t['id']:t.get('shortTitle','') for t in entities(db,'instrument')}
    items = [{**i, 'kind':'creation', 'id':i['bank_id']} for i in creation.preview(db, scope)]
    if scope.creations_only: return items
    for i in category_send.preview(db):
        tx = txs.get(i['zen_id'], {})
        if tx.get('incomeAccount') != zen_account or tx.get('outcomeAccount') != zen_account:
            continue
        if scope.start and (i['date'] or '') < scope.start.isoformat(): continue
        if scope.end and (i['date'] or '') > scope.end.isoformat(): continue
        items.append({**i, 'kind':'category', 'id':i['zen_id'], 'currency':instruments.get(i['instrument'],'')})
    return items


def results(path, scope):
    with transaction(path, read_only=True) as db:
        zen_account = target(db, scope)
        txs = {t['id']:t for t in entities(db,'transaction')}
        values = [{'kind':'creation','id':r['bank_id'],'state':r['state'],'error':r['error']}
                  for r in db.execute('SELECT j.* FROM creation_jobs j JOIN bank_transactions b ON b.id=j.bank_id WHERE b.bank_account=?', (scope.account,))]
        values += [{'kind':'category','id':r['zen_id'],'state':r['state'],'error':r['error']}
                   for r in category_send.results(db) if txs.get(r['zen_id'],{}).get('incomeAccount') == zen_account]
        return values


def send(path, client, scope):
    keys = [(i.kind,i.id) for i in scope.items]
    if len(set(keys)) != len(keys): fail('Повтор операции в запросе',422)
    refresh(path, client)
    with transaction(path) as db:
        current = {(i['kind'],i['id']):i for i in preview(db,scope)}
        for item in scope.items:
            value = current.get((item.kind,item.id))
            if not value or value['error'] or value['snapshot'] != item.snapshot:
                fail('Данные изменились. Откройте просмотр отправки заново.')
        txs = {t['id']:t for t in entities(db,'transaction')}
        creations, changes = [], []
        for item in scope.items:
            if item.kind == 'creation':
                job = db.execute('SELECT * FROM creation_jobs WHERE bank_id=?',(item.id,)).fetchone()
                payload = json.loads(job['payload']);creations.append(payload)
                db.execute("UPDATE creation_jobs SET state='uncertain',error=NULL WHERE bank_id=?",(item.id,))
                audit(db,'creation_intent',{'bank_id':item.id,'payload':payload})
            else:
                draft = db.execute('SELECT * FROM zen_category_drafts WHERE zen_id=?',(item.id,)).fetchone()
                before = txs[item.id]
                payload = {**before,'tag':[draft['category_id']],'changed':max(int(time.time()),before.get('changed',0)+1)}
                db.execute('INSERT OR REPLACE INTO zen_category_sends VALUES (?,?,?,?,?,?,NULL)',
                           (item.id,draft['category_id'],draft['updated_at'],json.dumps(before),json.dumps(payload),'uncertain'))
                audit(db,'category_send_intent',{'zen_id':item.id,'before':before,'payload':payload})
                changes.append((before,payload))
        if len({p['id'] for p in creations+[p for _,p in changes]}) != len(keys):
            fail('Идентификатор повторяется в общей отправке')
        cursor = int(db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0])
    try:
        client.write_transactions(creations, changes, cursor)
    except Exception as exc:
        creation.record_write_failure(path,exc,[item.id for item in scope.items if item.kind=='creation'])
    for verify in (creation.refresh,category_send.refresh):
        try: verify(path, client)
        except HTTPException: pass
    return {'results':[r for r in results(path,scope) if (r['kind'],r['id']) in keys]}
