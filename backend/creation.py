"""Local new-operation drafts and explicit, at-most-once remote creation attempts."""
from . import jsoncodec as json
import time
import uuid
from decimal import Decimal
from fastapi import HTTPException
from .db import transaction, entities
from .service import review, get_row, get_entity, mapped_account, fail, audit, SCALES
from .reconciliation import recipient, recipient_profiles, normalized
from .category_send import fingerprint
from .zenmoney import apply_diff
from zenmoney_api import transactions as tx
from .zenstore import complete_creation


def selected(row, accounts, scope):
    return (accounts[row['bank_account']]['bank'] == scope.bank and
            (not scope.account or row['bank_account'] == scope.account) and
            (not scope.start or row['date'] >= str(scope.start)) and
            (not scope.end or row['date'] <= str(scope.end)))


def profiles(db, rows):
    txs = entities(db, 'transaction')
    result = recipient_profiles(rows, {}, txs, entities(db, 'tag'))
    return {(p['bank_account'], p['key'], p['direction']):p['categories'] for p in result}


def ranking(row, learned):
    key, _ = recipient(row)
    return learned.get((row['bank_account'], key, 'income' if row['amount_minor'] > 0 else 'expense'), [])


def prepare(db, scope):
    if scope.start and scope.end and scope.start > scope.end: fail('Начало периода позже конца', 422)
    rows = review(db); learned = profiles(db, rows)
    historical_txs = {t['id']:t for t in entities(db, 'transaction')}
    accounts = {a['id']:dict(a) for a in db.execute('SELECT * FROM bank_accounts')}
    rules = {(r['bank'],r['name'],r['direction']):dict(r) for r in db.execute('SELECT * FROM recipient_rules')}
    tags = {t['id'] for t in entities(db,'tag') if not t.get('deleted')}
    for row in rows:
        if row['state'] != 'pending' or not selected(row, accounts, scope): continue
        old = db.execute('SELECT * FROM creation_drafts WHERE bank_id=?', (row['id'],)).fetchone()
        if old and old['source'] == 'manual': continue
        choices = ranking(row, learned); key, _ = recipient(row)
        category = choices[0]['id'] if key and len(choices) == 1 and choices[0]['count'] >= 3 else None
        # Split/uncategorized confirmed payments are not evidence of one unambiguous category.
        if category and any(other['bank_account']==row['bank_account'] and recipient(other)[0]==key
                            and (other['amount_minor']>0)==(row['amount_minor']>0)
                            and other.get('reconciliation_status')=='synced'
                            and (other.get('split_ids') or historical_txs.get(other.get('linked_id'),{}).get('tag') != [category]) for other in rows):
            category = None
        rule = rules.get(rule_key(row,accounts[row['bank_account']]['bank']))
        source = 'rule' if rule else 'auto'
        if rule: category = rule['category_id'] if rule['category_id'] in tags else None
        if not old or old['category_id'] != category or old['source'] != source:
            db.execute('INSERT OR REPLACE INTO creation_drafts VALUES (?,?,?,?)', (row['id'], category, source, int(time.time())))
            audit(db, 'creation_category_'+source, {'bank_id':row['id'], 'category_id':category, 'evidence':choices})
    return {'status':'prepared'}


def rule_key(row, bank):
    raw = json.loads(row['raw']) if isinstance(row['raw'],str) else row['raw']
    return (bank, normalized((raw.get('details') or {}).get('corresponsiveName')), 'income' if row['amount_minor']>0 else 'expense')


def remove_rule(db, identifier):
    from .models import NewOperationScope
    row = get_row(db,'bank_transactions',identifier)
    bank = get_row(db,'bank_accounts',row['bank_account'])['bank']
    key = rule_key(row,bank)
    db.execute('DELETE FROM recipient_rules WHERE bank=? AND name=? AND direction=?',key)
    audit(db,'recipient_rule_removed',{'bank':bank,'name':key[1],'direction':key[2]})
    prepare(db,NewOperationScope(bank=bank))
    return {'status':'removed'}


def save_category(db, identifier, category, remember=False):
    row = get_row(db, 'bank_transactions', identifier)
    if row['state'] != 'pending' or db.execute('SELECT 1 FROM creation_jobs WHERE bank_id=?',(identifier,)).fetchone():
        fail('Операция уже связана, исключена или отправлялась')
    if category and get_entity(db, 'tag', category).get('deleted'): fail('Категория удалена')
    if remember:
        from .models import NewOperationScope
        bank = get_row(db,'bank_accounts',row['bank_account'])['bank']
        key = rule_key(row,bank)
        if not key[1] or not category: fail('Для правила нужны имя получателя и категория',422)
        db.execute('INSERT OR REPLACE INTO recipient_rules VALUES (?,?,?,?,?)',(*key,category,int(time.time())))
        audit(db,'recipient_rule_saved',{'bank':bank,'name':key[1],'direction':key[2],'category_id':category})
    db.execute('INSERT OR REPLACE INTO creation_drafts VALUES (?,?,?,?)', (identifier, category, 'manual', int(time.time())))
    if remember: prepare(db,NewOperationScope(bank=bank))
    audit(db, 'creation_category_manual', {'bank_id':identifier, 'category_id':category})
    return {'category_id':category}


def decorate(db, rows):
    drafts = {r['bank_id']:dict(r) for r in db.execute('SELECT * FROM creation_drafts')}
    jobs = {r['bank_id']:dict(r) for r in db.execute('SELECT * FROM creation_jobs')}
    tags = {t['id']:t for t in entities(db, 'tag') if not t.get('deleted')}
    learned = profiles(db, rows)
    accounts = {r['id']:r['bank'] for r in db.execute('SELECT id,bank FROM bank_accounts')}
    rules = {(r['bank'],r['name'],r['direction']):dict(r) for r in db.execute('SELECT * FROM recipient_rules')}
    for row in rows:
        rule = rules.get(rule_key(row,accounts[row['bank_account']]))
        row['recipient_rule'] = {**rule,'title':tags.get(rule['category_id'],{}).get('title')} if rule else None
        draft = drafts.get(row['id'])
        row['creation_draft'] = {**draft, 'title':tags.get(draft['category_id'],{}).get('title'),
                                 'valid':draft['category_id'] in tags} if draft else None
        row['creation_ranking'] = ranking(row, learned)
        job = jobs.get(row['id'])
        row['creation_job'] = {k:job[k] for k in ('zen_id','state','error')} if job else None


def preview(db, scope, local=False):
    from .day_review import proposals
    prepare(db, scope)
    rows = review(db)
    jobs = {r['bank_id']:dict(r) for r in db.execute("SELECT * FROM creation_jobs WHERE state='prepared'")}
    days = proposals(db, [{**r, 'state':'pending', 'linked_id':None} if r['id'] in jobs else r for r in rows])
    split_ids = {c['bank_id'] for d in days for c in d.get('split_candidates', [])}
    accounts = {a['id']:dict(a) for a in db.execute('SELECT * FROM bank_accounts')}
    drafts = {r['bank_id']:dict(r) for r in db.execute('SELECT * FROM creation_drafts')}
    fresh_row = db.execute("SELECT value FROM meta WHERE key='creation_fresh'").fetchone()
    fresh = json.loads(fresh_row[0]) if fresh_row else {}
    items = []
    for row in rows:
        if not selected(row, accounts, scope): continue
        job = jobs.get(row['id'])
        if local and row['state'] != 'pending': continue
        if not local and (not job or row['state'] != 'queued'): continue
        draft = drafts.get(row['id']);target = {}; tag = {}; error = None
        if not local and not scope.include_uncategorized and draft and not draft['category_id']: continue
        try:
            target = mapped_account(db, accounts[row['bank_account']])
            if row['candidates'] or row['id'] in split_ids: fail('Сначала разберите возможное совпадение с ZenMoney')
            if not row['amount_minor']: fail('Нулевая сумма: создание недоступно')
            if not draft or (not draft['category_id'] and not scope.include_uncategorized): fail('Выберите категорию')
            tag = get_entity(db, 'tag', draft['category_id']) if draft['category_id'] else {}
            if tag.get('deleted'): fail('Категория удалена')
            if not local and (str(target['id']) not in fresh.get('account',[]) or str(target['instrument']) not in fresh.get('instrument',[]) or (tag and tag['id'] not in fresh.get('tag',[]))):
                fail('Счёт, валюта или категория отсутствуют в свежем ответе ZenMoney')
            if target.get('type') == 'debt': fail('Долговой счёт требует отдельного оформления')
            if local and db.execute('SELECT 1 FROM creation_jobs WHERE bank_id=?',(row['id'],)).fetchone(): fail('Запись уже создана')
            if job:
                payload = json.loads(job['payload'])
                expected = payload_for(db, get_row(db,'bank_transactions',row['id']), draft['category_id'])
                expected['id'] = job['zen_id']
                if not matches(expected,payload): fail('Локальная запись не соответствует текущим данным')
                if db.execute('SELECT 1 FROM zen_transaction WHERE id=?',(job['zen_id'],)).fetchone(): fail('Идентификатор уже существует в ZenMoney')
        except HTTPException as exc: error = exc.detail
        target_fields = {k:target.get(k) for k in ('id','user','instrument','archive','deleted','type','title')}
        items.append({'bank_id':row['id'], 'date':row['date'], 'amount_minor':row['amount_minor'], 'currency':row['currency'],
                      'description':row['description'], 'account':target.get('title','Счёт не привязан'),
                      'category':tag.get('title'), 'source':draft['source'] if draft else None, 'error':error,
                      'snapshot':fingerprint([get_row(db,'bank_transactions',row['id']),draft,target_fields,tag.get('id'),tag.get('title'),job])})
    return items


def create_local(db, scope):
    items = preview(db, scope, local=True)
    created = []
    for item in items:
        if item['error']: continue
        identifier = item['bank_id']
        row = get_row(db,'bank_transactions',identifier)
        draft = db.execute('SELECT category_id FROM creation_drafts WHERE bank_id=?',(identifier,)).fetchone()
        payload = payload_for(db,row,draft[0])
        db.execute('INSERT INTO creation_jobs VALUES (?,?,?,?,NULL)',(identifier,payload['id'],json.dumps(payload),'prepared'))
        db.execute("UPDATE bank_transactions SET state='queued',linked_id=? WHERE id=?",(payload['id'],identifier))
        audit(db,'creation_local',{'bank_id':identifier,'payload':payload})
        created.append(identifier)
    return {'created':created,'skipped':[{'bank_id':i['bank_id'],'reason':i['error']} for i in items if i['error']]}


def reopen(db, identifier):
    job = db.execute('SELECT state FROM creation_jobs WHERE bank_id=?',(identifier,)).fetchone()
    if not job or job['state'] != 'prepared': fail('Можно отменить только локальную запись до отправки')
    db.execute('DELETE FROM creation_jobs WHERE bank_id=?',(identifier,))
    db.execute("UPDATE bank_transactions SET state='pending',linked_id=NULL WHERE id=?",(identifier,))
    audit(db,'creation_reopened',{'bank_id':identifier})
    return {'status':'reopened'}


def payload_for(db, row, category):
    account = get_row(db,'bank_accounts',row['bank_account']);target = mapped_account(db,account)
    amount = Decimal(abs(row['amount_minor'])) / SCALES[account['currency']]
    raw = json.loads(row['raw']);payee = (raw.get('details') or {}).get('corresponsiveName') or None
    builder = tx.income if row['amount_minor'] > 0 else tx.expense
    return builder(user=target['user'], date=row['date'], account=target['id'], instrument=target['instrument'],
                   amount=amount, categories=[category] if category else None,
                   payee=payee, comment=row['description'])


def matches(expected, actual):
    from .category_send import content
    expected, actual = content(expected), content(actual)
    # Compare every field we authored except server-adjusted timestamps; server may add defaults.
    return all((actual.get(k) or None) == (value or None) if k == 'tag' else actual.get(k) == value
               for k,value in expected.items() if k not in ('created','changed'))


def refresh(path, client, *, full=False):
    with transaction(path) as db:
        cursor = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
        cursor = int(cursor[0]) if cursor and not full else 0
        try:
            result = client.read_diff(cursor, force_fetch=['transaction','account','tag','instrument'])
            if any(k not in result for k in ('transaction','account','tag','instrument')):
                raise ValueError('Incomplete authoritative response')
            apply_diff(db,result,cursor,full_collections=['transaction','account','tag','instrument'])
        except Exception: fail('Не удалось получить свежие данные ZenMoney. Создание не подтверждено.',502)
        remote = {t['id']:t for t in result['transaction']}
        for job in db.execute("SELECT * FROM creation_jobs WHERE state NOT IN ('sent','prepared')").fetchall():
            actual = remote.get(job['zen_id'])
            state = 'sent' if actual and matches(json.loads(job['payload']),actual) else 'conflict' if actual else 'uncertain'
            error = None if state=='sent' else 'Данные записи отличаются от отправленных' if actual else 'Запись пока не подтверждена. Повторите проверку; новая отправка заблокирована.'
            if state=='uncertain' and (job['error'] or '').startswith('Ошибка отправки:'):
                error = job['error']
            if state=='sent':
                from .link_groups import insert
                insert(db,[job['bank_id']],[job['zen_id']],'single','creation',state='sent')
                db.execute('DELETE FROM creation_drafts WHERE bank_id=?',(job['bank_id'],))
                audit(db,'creation_verified',{'bank_id':job['bank_id'],'zen_id':job['zen_id']})
            db.execute('UPDATE creation_jobs SET state=?,error=? WHERE bank_id=?',(state,error,job['bank_id']))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('creation_fresh',?)", (json.dumps({k:[str(t['id']) for t in result[k]] for k in ('account','tag','instrument')}),))
    return results(path)


def results(path):
    with transaction(path,read_only=True) as db:
        return {'results':[dict(r) for r in db.execute('SELECT bank_id,zen_id,state,error FROM creation_jobs')]}


def send(path, client, scope):
    ids = [i.bank_id for i in scope.items]
    if len(set(ids)) != len(ids): fail('Повтор операции в запросе',422)
    refresh(path,client)
    with transaction(path) as db:
        current = {r['bank_id']:r for r in preview(db,scope)}
        for item in scope.items:
            row = current.get(item.bank_id)
            if not row or row['error'] or row['snapshot'] != item.snapshot:
                fail('Данные изменились или есть препятствия для создания. Откройте просмотр заново.')
        payloads = []
        for identifier in ids:
            job = db.execute('SELECT * FROM creation_jobs WHERE bank_id=?',(identifier,)).fetchone()
            payload = json.loads(job['payload']);payloads.append(payload)
            db.execute("UPDATE creation_jobs SET state='uncertain',error=NULL WHERE bank_id=?",(identifier,))
            audit(db,'creation_intent',{'bank_id':identifier,'payload':payload})
        cursor = int(db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0])
    try:
        client.upsert('transaction', payloads, cursor)
    except Exception as exc:
        record_write_failure(path,exc,ids)
    try: refresh(path,client)
    except HTTPException: pass
    return {'results':[r for r in results(path)['results'] if r['bank_id'] in ids]}


def record_write_failure(path, exc, identifiers):
    from zenmoney_api import HTTPError, TransportError, ResponseError
    if isinstance(exc, HTTPError): reason = f'ZenMoney вернул HTTP {exc.status_code}'
    elif isinstance(exc, TransportError): reason = 'сбой соединения с ZenMoney'
    elif isinstance(exc, ResponseError): reason = 'не удалось проверить ответ ZenMoney'
    elif isinstance(exc, ValueError): reason = 'локальная проверка запроса не пройдена'
    else: reason = 'внутренняя ошибка при отправке'
    error = 'Ошибка отправки: '+reason+'. Создание не подтверждено; автоматический повтор заблокирован.'
    with transaction(path) as db:
        audit(db,'zenmoney_write_failed',{'bank_ids':identifiers,'error_type':type(exc).__name__,'reason':reason})
        for identifier in identifiers:
            db.execute("UPDATE creation_jobs SET error=? WHERE bank_id=? AND state='uncertain'",(error,identifier))


def retry_preview(path, client, scope):
    # An explicit user action only. Keep the original UUID and payload, never clone a job.
    refresh(path,client,full=True)
    with transaction(path) as db:
        accounts={r['id']:dict(r) for r in db.execute('SELECT * FROM bank_accounts')}
        recovered=[]
        for row in db.execute("SELECT b.* FROM bank_transactions b JOIN creation_jobs j ON j.bank_id=b.id WHERE j.state='uncertain'").fetchall():
            if not selected(row,accounts,scope): continue
            job=db.execute('SELECT * FROM creation_jobs WHERE bank_id=?',(row['id'],)).fetchone()
            if db.execute('SELECT 1 FROM zen_transaction WHERE lower(id)=lower(?)',(job['zen_id'],)).fetchone(): continue
            db.execute("UPDATE creation_jobs SET state='prepared',error=NULL,payload=? WHERE bank_id=?",
                       (json.dumps(complete_creation(json.loads(job['payload']))), row['id']))
            audit(db,'creation_retry_prepared',{'bank_id':row['id'],'zen_id':job['zen_id']})
            recovered.append(row['id'])
        return [item for item in preview(db,scope) if item['bank_id'] in recovered]
