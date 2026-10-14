"""One explicit mixed write, reusing durable per-record intents and verification."""
from . import jsoncodec as json
import time
from decimal import Decimal
from fastapi import HTTPException
from . import category_send, creation, operations
from .db import transaction, entities
from .service import fail, get_row, audit, SCALES
from zenmoney_api import transactions as tx


def target(db, scope):
    account = get_row(db, 'bank_accounts', scope.account)
    if account['bank'] != scope.bank or not account['zenmoney_account']:
        fail('Счёт не относится к выбранному банку или не связан с ZenMoney')
    return account['zenmoney_account']


def refresh(path, client):
    creation.refresh(path, client)
    category_send.refresh(path, client)
    operations.refresh(path,client)


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
    for i in operations.preview(db):
        original=txs.get(i['zen_id'],{})
        if zen_account not in (original.get('incomeAccount'),original.get('outcomeAccount')):continue
        if scope.start and (i['date'] or '')<scope.start.isoformat() or scope.end and (i['date'] or '')>scope.end.isoformat():continue
        items.append(i)
    return items


def results(path, scope):
    with transaction(path, read_only=True) as db:
        zen_account = target(db, scope)
        txs = {t['id']:t for t in entities(db,'transaction')}
        instruments={i['id']:i.get('shortTitle','') for i in entities(db,'instrument')}
        def selected(day):
            return not ((scope.start and (day or '')<scope.start.isoformat()) or (scope.end and (day or '')>scope.end.isoformat()))
        values = [{'kind':'creation','id':r['bank_id'],'state':r['state'],'error':r['error'],
                   'date':r['date'],'description':r['description'] or 'Без описания','currency':r['currency'],
                   'amount':str(Decimal(r['amount_minor'])/SCALES[r['currency']])}
                  for r in db.execute('''SELECT j.*,b.date,b.description,b.amount_minor,a.currency FROM creation_jobs j
                      JOIN bank_transactions b ON b.id=j.bank_id JOIN bank_accounts a ON a.id=b.bank_account
                      WHERE b.bank_account=?''', (scope.account,)) if selected(r['date'])]
        if scope.creations_only:return values
        for kind,table in (('category','zen_category_sends'),('operation','zen_operation_sends')):
            for r in db.execute('SELECT * FROM '+table):
                original=json.loads(r['payload'])
                if zen_account not in (original.get('incomeAccount'),original.get('outcomeAccount')):continue
                current=txs.get(r['zen_id'],{})
                actual=current if zen_account in (current.get('incomeAccount'),current.get('outcomeAccount')) else original
                if not selected(actual.get('date')):continue
                income=actual.get('incomeAccount')==zen_account and Decimal(str(actual.get('income',0)))>0
                values.append({'kind':kind,'id':r['zen_id'],'state':r['state'],'error':r['error'],
                               'date':actual.get('date'),'description':actual.get('comment') or actual.get('payee') or 'Без описания',
                               'amount':str(operations.signed(actual,zen_account)),
                               'currency':instruments.get(actual.get('incomeInstrument' if income else 'outcomeInstrument'),'')})
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
            elif item.kind=='operation':
                changes.append((txs[item.id],operations.intent(db,item.id)))
            else:
                draft = db.execute('SELECT * FROM zen_category_drafts WHERE zen_id=?',(item.id,)).fetchone()
                before = txs[item.id]
                payload = tx.update(before, tag=[draft['category_id']], now=max(int(time.time()),before.get('changed',0)+1))
                db.execute('INSERT OR REPLACE INTO zen_category_sends VALUES (?,?,?,?,?,?,NULL)',
                           (item.id,draft['category_id'],draft['updated_at'],json.dumps(before),json.dumps(payload),'uncertain'))
                audit(db,'category_send_intent',{'zen_id':item.id,'before':before,'payload':payload})
                changes.append((before,payload))
        if len({p['id'] for p in creations+[p for _,p in changes]}) != len(keys):
            fail('Идентификатор повторяется в общей отправке')
        cursor = int(db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0])
    try:
        client.upsert('transaction', creations + [payload for before, payload in changes], cursor)
    except Exception as exc:
        creation.record_write_failure(path,exc,[item.id for item in scope.items if item.kind=='creation'])
    for verify in (creation.refresh,category_send.refresh,operations.refresh):
        try: verify(path, client)
        except HTTPException: pass
    return {'results':[r for r in results(path,scope) if (r['kind'],r['id']) in keys]}
