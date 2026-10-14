"""Local operation definitions and verified edits of existing ZenMoney UUIDs."""
import time
from decimal import Decimal
from fastapi import HTTPException
from zenmoney_api import transactions as tx
from . import jsoncodec as json
from .db import entities, transaction
from .models import OperationDefinition
from .service import fail, get_row, get_entity, mapped_account, audit, SCALES
from .category_send import content, fingerprint
from .zenmoney import apply_diff

LABELS = {'expense':'Расход','income':'Приход','refund':'Возврат покупки','transfer':'Перевод',
          'lend':'Дать в долг','borrow':'Взять в долг','repay':'Вернуть долг','collect':'Получить возврат долга'}


def public_definition(definition):
    return {**definition,'other_amount':str(definition['other_amount']) if definition.get('other_amount') is not None else None}


def context(db):
    return {'accounts':{a['id']:a for a in entities(db,'account')},
            'instruments':{a['id']:a for a in entities(db,'instrument')},
            'tags':{a['id']:a for a in entities(db,'tag')},
            'users':{a['id']:a for a in entities(db,'user')}, 'merchants':entities(db,'merchant'),
            'bank_drafts':{r['bank_id']:dict(r) for r in db.execute('SELECT * FROM bank_operation_drafts')},
            'zen_drafts':{r['zen_id']:dict(r) for r in db.execute('SELECT * FROM zen_operation_drafts')},
            'category_drafts':{r['zen_id']:dict(r) for r in db.execute('SELECT * FROM zen_category_drafts')},
            'created_definitions':{r['zen_id']:json.loads(r['definition']) for r in db.execute("SELECT j.zen_id,d.definition FROM creation_jobs j JOIN bank_operation_drafts d ON d.bank_id=j.bank_id WHERE j.state='sent'")},
            'sends':{r['zen_id']:dict(r) for r in db.execute('SELECT * FROM zen_operation_sends')}}


def family(ctx, user):
    seen=set()
    while user not in seen:
        seen.add(user);parent=ctx['users'].get(user,{}).get('parent')
        if parent is None:return user
        user=parent
    fail('Некорректная структура пользователей ZenMoney')


def account(ctx, identifier, *, debt=False):
    a=ctx['accounts'].get(identifier)
    if not a or a.get('deleted') or a.get('archive') or (a.get('type')=='debt') != debt:
        fail('Счёт недоступен или имеет неподходящий тип')
    if not debt and a.get('instrument') not in ctx['instruments']:fail('Валюта счёта недоступна')
    return a


def signed(before, anchor):
    if anchor not in (before.get('incomeAccount'),before.get('outcomeAccount')):fail('Счёт не относится к операции')
    return ((Decimal(str(before.get('income',0))) if before.get('incomeAccount')==anchor else Decimal(0))-
            (Decimal(str(before.get('outcome',0))) if before.get('outcomeAccount')==anchor else Decimal(0)))


def inferred(before, anchor, ctx):
    amount=signed(before,anchor);category=(before.get('tag') or [None])[0]
    kind='income' if amount>0 else 'expense';other=None;other_amount=None
    if before.get('incomeAccount')!=before.get('outcomeAccount'):
        other=before.get('outcomeAccount') if amount>0 else before.get('incomeAccount')
        if ctx['accounts'].get(other,{}).get('type')=='debt':kind='borrow' if amount>0 else 'lend';other=None
        else:kind='transfer';other_amount=str(before.get('outcome') if amount>0 else before.get('income'))
        category=None
    elif amount>0 and ctx['tags'].get(category,{}).get('showIncome') is False and ctx['tags'].get(category,{}).get('showOutcome') is True:kind='refund'
    return dict(kind=kind,account_id=anchor,category_id=category,other_account_id=other,other_amount=other_amount,payee=before.get('payee'))


def build(ctx, definition, *, amount, day, user, before=None, comment=None, payee=None):
    d=OperationDefinition.model_validate(definition);a=account(ctx,d.account_id)
    if not amount:fail('Нулевая операция не поддерживается')
    if a.get('user') is None or family(ctx,a['user'])!=family(ctx,user):fail('Счёт принадлежит другому пользователю')
    if d.kind in ('expense','lend','repay') and amount>0 or d.kind in ('income','refund','borrow','collect') and amount<0:
        fail('Тип не соответствует направлению банковской операции')
    base=dict(user=user,date=day,account=a['id'],instrument=a['instrument'],amount=abs(amount),
              comment=comment,payee=payee,categories=[d.category_id] if d.category_id else None)
    if d.kind in ('expense','income','refund'):
        if d.other_account_id or d.other_amount is not None:fail('Второй счёт доступен только для перевода')
        if not d.category_id:fail('Выберите категорию')
        tag=ctx['tags'].get(d.category_id)
        if not tag or tag.get('deleted') or tag.get('archive'):fail('Категория недоступна')
        if tag.get('user') is not None and family(ctx,tag['user'])!=family(ctx,user):fail('Категория принадлежит другому пользователю')
        if d.kind=='refund' and (tag.get('showIncome') is not False or tag.get('showOutcome') is not True):
            fail('Для возврата нужна категория, учитываемая только как расходы')
        if d.kind=='expense' and tag.get('showOutcome') is False or d.kind=='income' and tag.get('showIncome') is False:
            fail('Категория не подходит выбранному типу')
        result=(tx.expense if d.kind=='expense' else tx.income)(**base)
    elif d.kind=='transfer':
        if d.category_id:fail('Категория переводу не назначается')
        b=account(ctx,d.other_account_id)
        if b['id']==a['id'] or family(ctx,b.get('user'))!=family(ctx,user):fail('Выберите другой счёт того же пользователя или семьи')
        same=a['instrument']==b['instrument'];other_amount=abs(amount) if same else d.other_amount
        if not other_amount:fail('Укажите сумму на втором счёте в его валюте')
        if same and d.other_amount is not None and d.other_amount!=abs(amount):fail('Суммы перевода в одной валюте должны совпадать')
        if ctx['instruments'][b['instrument']].get('shortTitle')=='VND' and other_amount!=other_amount.to_integral_value():fail('Сумма VND должна быть целой')
        outgoing=amount<0
        result=tx.transfer(user=user,date=day,comment=comment,
            from_account=a['id'] if outgoing else b['id'],to_account=b['id'] if outgoing else a['id'],
            from_instrument=a['instrument'] if outgoing else b['instrument'],to_instrument=b['instrument'] if outgoing else a['instrument'],
            amount_out=abs(amount) if outgoing else other_amount,amount_in=other_amount if outgoing else abs(amount))
    else:
        if d.category_id or d.other_account_id or d.other_amount is not None:fail('У долга указывается человек, не категория или второй обычный счёт')
        person=(d.payee or '').strip()
        if not person:fail('Укажите человека для долговой операции')
        debts=[v for v in ctx['accounts'].values() if v.get('type')=='debt' and not v.get('deleted') and not v.get('archive') and v.get('user')==user]
        if not debts:debts=[v for v in ctx['accounts'].values() if v.get('type')=='debt' and family(ctx,v.get('user'))==family(ctx,user) and not v.get('deleted') and not v.get('archive')]
        if len(debts)!=1:fail('Не удалось однозначно определить системный счёт долгов')
        merchants=[m for m in ctx['merchants'] if (m.get('title') or '').strip().casefold()==person.casefold() and family(ctx,m.get('user'))==family(ctx,user)]
        result=tx.debt(user=user,date=day,comment=comment,action=d.kind,account=a['id'],debt_account=debts[0]['id'],
                       instrument=a['instrument'],amount=abs(amount),payee=person,merchant=merchants[0]['id'] if len(merchants)==1 else None,originalPayee=person)
    if before:
        changes={k:result[k] for k in ('incomeAccount','outcomeAccount','incomeInstrument','outcomeInstrument','income','outcome','tag','payee','merchant','originalPayee')}
        if d.kind in ('expense','income','refund'):
            for field in ('payee','merchant','originalPayee'):changes[field]=before.get(field)
        return tx.update(before,now=max(int(time.time()),before.get('changed',0)+1),**changes)
    return result


def bank_definition(db, row, category=None):
    stored=db.execute('SELECT definition FROM bank_operation_drafts WHERE bank_id=?',(row['id'],)).fetchone()
    if stored:return json.loads(stored[0])
    target=mapped_account(db,get_row(db,'bank_accounts',row['bank_account']))
    raw=json.loads(row['raw']) if isinstance(row['raw'],str) else row['raw']
    tag=get_entity(db,'tag',category) if category else {}
    kind='refund' if row['amount_minor']>0 and tag.get('showIncome') is False and tag.get('showOutcome') is True else 'income' if row['amount_minor']>0 else 'expense'
    return dict(kind=kind,account_id=target['id'],category_id=category,
                other_account_id=None,other_amount=None,payee=(raw.get('details') or {}).get('corresponsiveName'))


def bank_payload(db, row, category):
    d=bank_definition(db,row,category);bank=get_row(db,'bank_accounts',row['bank_account']);a=mapped_account(db,bank)
    if d['account_id']!=a['id']:fail('Привязка банковского счёта изменилась')
    raw=json.loads(row['raw']) if isinstance(row['raw'],str) else row['raw']
    return build(context(db),d,amount=Decimal(row['amount_minor'])/SCALES[bank['currency']],day=row['date'],user=a['user'],
                 comment=row['description'],payee=(raw.get('details') or {}).get('corresponsiveName'))


def describe_zen(db,before,anchor=None,ctx=None):
    ctx=ctx or context(db);identifier=before['id'];draft=ctx['zen_drafts'].get(identifier);job=ctx['sends'].get(identifier)
    ordinary=[i for i in (before.get('outcomeAccount'),before.get('incomeAccount')) if ctx['accounts'].get(i,{}).get('type')!='debt']
    anchor=anchor if anchor in ordinary else next(iter(ordinary),None)
    error=None;definition={}
    try:
        definition=json.loads(draft['definition']) if draft else inferred(before,anchor,ctx)
        category_draft=ctx['category_drafts'].get(identifier)
        if not draft and category_draft and definition['kind'] in ('expense','income','refund'):definition['category_id']=category_draft['category_id']
        remembered=json.loads(job['definition']) if job and job['state']=='sent' else ctx['created_definitions'].get(identifier)
        if not draft and not category_draft and remembered and remembered['account_id']==anchor:
            candidate=build(ctx,remembered,amount=signed(before,anchor),day=before['date'],user=before['user'],before=before,comment=before.get('comment'),payee=before.get('payee'))
            if content(candidate)==content(before):definition=remembered
        account(ctx,definition['account_id'])
        if before.get('deleted') or before.get('hold') or not before.get('user') or not signed(before,definition['account_id']):fail('Операция недоступна для изменения')
        if job and job['state']=='uncertain':fail('Сначала проверьте результат отправки')
        if db.execute("SELECT 1 FROM creation_jobs WHERE zen_id=? AND state NOT IN ('sent','prepared')",(identifier,)).fetchone():fail('Сначала проверьте результат создания')
        if db.execute("SELECT 1 FROM zen_category_sends WHERE zen_id=? AND state='uncertain'",(identifier,)).fetchone():fail('Сначала проверьте отправку категории')
    except HTTPException as exc:error=exc.detail
    return {'source':'zen','id':identifier,'definition':public_definition(definition),'draft':bool(draft),'editable':error is None,'error':error,
            'snapshot':fingerprint([content(before),draft,job,ctx['category_drafts'].get(identifier)]),'label':LABELS.get(definition.get('kind'),'Операция'),
            'amount':str(signed(before,definition['account_id'])) if definition.get('account_id') in (before.get('incomeAccount'),before.get('outcomeAccount')) else '0'}


def describe_bank(db,row,ctx=None):
    ctx=ctx or context(db);raw=get_row(db,'bank_transactions',row['id']);draft=ctx['bank_drafts'].get(row['id'])
    job=db.execute('SELECT * FROM creation_jobs WHERE bank_id=?',(row['id'],)).fetchone();job=dict(job) if job else None
    definition={};error=None;ready=False
    category=db.execute('SELECT category_id FROM creation_drafts WHERE bank_id=?',(row['id'],)).fetchone()
    try:
        if row['state']!='pending' and not(job and job['state']=='prepared'):fail('Сначала проверьте соответствие или результат отправки')
        definition=bank_definition(db,raw,category[0] if category else None)
        if not row.get('mapped') or row.get('candidates'):fail('Сначала уточните соответствие операции')
        ready=bool(category and category[0])
        if draft:
            try:bank_payload(db,raw,category[0] if category else None);ready=True
            except HTTPException as exc:ready=False;error=exc.detail
    except HTTPException as exc:error=exc.detail
    return {'source':'bank','id':row['id'],'definition':public_definition(definition),'draft':bool(draft),'editable':row.get('mapped',False) and (row['state']=='pending' or bool(job and job['state']=='prepared')) and not row.get('candidates'),
            'error':error,'ready':ready,'snapshot':fingerprint([raw,draft,job,category[0] if category else None]),'label':LABELS.get(definition.get('kind'),'Операция'),'amount':str(row['amount_minor'])}


def save_bank(db,identifier,edit):
    from .service import review
    row=next((r for r in review(db) if r['id']==identifier),None)
    if not row:fail('Операция не найдена',404)
    model=describe_bank(db,row)
    if not model['editable'] or model['snapshot']!=edit.expected_snapshot:fail('Данные изменились или отправка не проверена. Обновите операцию.')
    definition=edit.definition.model_dump();bank=get_row(db,'bank_accounts',row['bank_account']);a=mapped_account(db,bank)
    if definition['account_id']!=a['id']:fail('Банковский счёт нельзя заменить')
    raw=get_row(db,'bank_transactions',identifier)
    # Validate before saving any local decision.
    payload=build(context(db),definition,amount=Decimal(raw['amount_minor'])/SCALES[bank['currency']],day=raw['date'],user=a['user'],comment=raw['description'],payee=(row['raw'].get('details') or {}).get('corresponsiveName'))
    db.execute('INSERT OR REPLACE INTO bank_operation_drafts VALUES (?,?,?)',(identifier,json.dumps(definition),int(time.time())))
    if definition['kind'] in ('expense','income','refund'):
        db.execute('INSERT OR REPLACE INTO creation_drafts VALUES (?,?,?,?)',(identifier,definition['category_id'],'manual',int(time.time())))
    job=db.execute("SELECT * FROM creation_jobs WHERE bank_id=? AND state='prepared'",(identifier,)).fetchone()
    if job:
        old=json.loads(job['payload']);payload.update(id=old['id'],created=old['created'],changed=max(int(time.time()),old['changed']+1))
        db.execute('UPDATE creation_jobs SET payload=? WHERE bank_id=?',(json.dumps(payload),identifier))
    audit(db,'operation_bank_draft',{'bank_id':identifier,'definition':definition})
    return {'status':'saved'}


def save_zen(db,identifier,edit):
    before=get_entity(db,'transaction',identifier);ctx=context(db);model=describe_zen(db,before,edit.definition.account_id,ctx)
    if not model['editable'] or model['snapshot']!=edit.expected_snapshot:fail('Данные изменились или отправка не проверена. Обновите операцию.')
    definition=edit.definition.model_dump()
    payload=build(ctx,definition,amount=signed(before,definition['account_id']),day=before['date'],user=before['user'],before=before,comment=before.get('comment'),payee=before.get('payee'))
    validate_links(db,payload)
    db.execute('INSERT OR REPLACE INTO zen_operation_drafts VALUES (?,?,?,?)',(identifier,json.dumps(definition),json.dumps(before),int(time.time())))
    db.execute('DELETE FROM zen_category_drafts WHERE zen_id=?',(identifier,))
    db.execute("DELETE FROM zen_category_sends WHERE zen_id=? AND state NOT IN ('sent','uncertain')",(identifier,))
    audit(db,'operation_zen_draft',{'zen_id':identifier,'definition':definition})
    return {'status':'saved'}


def preview(db):
    ctx=context(db);fresh=db.execute("SELECT value FROM meta WHERE key='operation_fresh_ids'").fetchone();fresh=set(json.loads(fresh[0])) if fresh else set();items=[]
    for identifier,draft in ctx['zen_drafts'].items():
        error=None;payload={};actual={};definition=json.loads(draft['definition'])
        try:
            actual=get_entity(db,'transaction',identifier);model=describe_zen(db,actual,definition['account_id'],ctx)
            if not model['editable'] or identifier not in fresh:fail(model['error'] or 'Операция отсутствует в свежих данных')
            if content(actual)!=content(json.loads(draft['before_payload'])):fail('Операция в ZenMoney изменилась. Сохраните решение заново.')
            if db.execute('SELECT 1 FROM reconciliation_exclusions WHERE zen_id=?',(identifier,)).fetchone():fail('Операция исключена')
            payload=build(ctx,definition,amount=signed(actual,definition['account_id']),day=actual['date'],user=actual['user'],before=actual,comment=actual.get('comment'),payee=actual.get('payee'))
            validate_links(db,payload)
        except HTTPException as exc:error=exc.detail
        a=ctx['accounts'].get(definition['account_id'],{})
        items.append({'zen_id':identifier,'kind':'operation','id':identifier,'date':actual.get('date'),'account':a.get('title'),'description':actual.get('comment') or actual.get('payee'),
                      'definition':definition,'type_label':LABELS[definition['kind']],'details':details(ctx,payload),'before_details':details(ctx,actual),'error':error,
                      'snapshot':fingerprint([content(actual),draft,ctx['sends'].get(identifier),payload and content(payload)])})
    return items


def details(ctx,payload):
    return {'income':str(payload.get('income',0)),'outcome':str(payload.get('outcome',0)),
            'from_account':ctx['accounts'].get(payload.get('outcomeAccount'),{}).get('title'),
            'to_account':ctx['accounts'].get(payload.get('incomeAccount'),{}).get('title'),
            'income_currency':ctx['instruments'].get(payload.get('incomeInstrument'),{}).get('shortTitle'),
            'outcome_currency':ctx['instruments'].get(payload.get('outcomeInstrument'),{}).get('shortTitle'),
            'categories':[ctx['tags'].get(c,{}).get('title',c) for c in payload.get('tag') or []], 'payee':payload.get('payee')}


def intent(db,identifier):
    draft=dict(db.execute('SELECT * FROM zen_operation_drafts WHERE zen_id=?',(identifier,)).fetchone());before=get_entity(db,'transaction',identifier);definition=json.loads(draft['definition'])
    payload=build(context(db),definition,amount=signed(before,definition['account_id']),day=before['date'],user=before['user'],before=before,comment=before.get('comment'),payee=before.get('payee'))
    validate_links(db,payload)
    db.execute('INSERT OR REPLACE INTO zen_operation_sends VALUES (?,?,?,?,?,?,NULL)',(identifier,draft['definition'],fingerprint(draft),json.dumps(before),json.dumps(payload),'uncertain'))
    audit(db,'operation_send_intent',{'zen_id':identifier,'before':before,'payload':payload})
    return payload


def refresh(path,client):
    with transaction(path) as db:
        cursor=db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone();cursor=int(cursor[0]) if cursor else 0
        try:
            result=client.read_diff(cursor,force_fetch=['transaction','account','tag','instrument','merchant','user'])
            if any(k not in result for k in ('transaction','account','tag','instrument')):raise ValueError('Incomplete response')
            apply_diff(db,result,cursor,full_collections=['transaction','account','tag','instrument','merchant','user'])
        except Exception:fail('Не удалось проверить свежие данные ZenMoney',502)
        remote={t['id']:t for t in result['transaction']}
        for job in db.execute("SELECT * FROM zen_operation_sends WHERE state<>'sent'").fetchall():
            actual=remote.get(job['zen_id']);payload=json.loads(job['payload'])
            state='sent' if actual and content(actual)==content(payload) else 'not_applied' if actual and content(actual)==content(json.loads(job['before_payload'])) else 'conflict' if actual else 'uncertain'
            db.execute('UPDATE zen_operation_sends SET state=?,error=? WHERE zen_id=?',(state,None if state=='sent' else 'Результат не подтверждён. Проверьте операцию; автоматического повтора нет.',job['zen_id']))
            if state=='sent':
                draft=db.execute('SELECT * FROM zen_operation_drafts WHERE zen_id=?',(job['zen_id'],)).fetchone()
                if draft and fingerprint(dict(draft))==job['draft_snapshot']:db.execute('DELETE FROM zen_operation_drafts WHERE zen_id=?',(job['zen_id'],))
                audit(db,'operation_verified',{'zen_id':job['zen_id']})
        db.execute("INSERT OR REPLACE INTO meta VALUES ('operation_fresh_ids',?)",(json.dumps(sorted(remote)),))


def results(db):return [dict(r) for r in db.execute('SELECT zen_id,state,error FROM zen_operation_sends')]


def validate_links(db,payload):
    groups=db.execute('SELECT group_id FROM reconciliation_zen_members WHERE zen_id=?',(payload['id'],)).fetchall()
    for group in groups:
        members=db.execute('SELECT b.amount_minor,a.zenmoney_account,a.currency FROM reconciliation_bank_members m JOIN bank_transactions b ON b.id=m.bank_id JOIN bank_accounts a ON a.id=b.bank_account WHERE m.group_id=?',(group[0],)).fetchall()
        parts=[payload if r[0]==payload['id'] else get_entity(db,'transaction',r[0]) for r in db.execute('SELECT zen_id FROM reconciliation_zen_members WHERE group_id=?',(group[0],))]
        for anchor in {r['zenmoney_account'] for r in members}:
            expected=sum(r['amount_minor'] for r in members if r['zenmoney_account']==anchor)
            observed=sum(((Decimal(str(t.get('income',0))) if t.get('incomeAccount')==anchor else 0)-(Decimal(str(t.get('outcome',0))) if t.get('outcomeAccount')==anchor else 0)) for t in parts)
            scale=next(SCALES[r['currency']] for r in members if r['zenmoney_account']==anchor)
            if observed*scale!=expected:fail('Изменение нарушит движение по связанному банковскому счёту')


def public_catalog(db):
    ctx=context(db)
    return {'accounts':[{k:a.get(k) for k in ('id','title','user','instrument','type','archive','deleted')} for a in ctx['accounts'].values()],
            'users':list(ctx['users'].values()),'merchants':ctx['merchants'],
            'instruments':list(ctx['instruments'].values())}


def send(path,client,items):
    identifiers=[i.zen_id for i in items]
    if len(set(identifiers))!=len(identifiers):fail('Повтор операции',422)
    refresh(path,client)
    with transaction(path) as db:
        current={i['zen_id']:i for i in preview(db)}
        if any(i.zen_id not in current or current[i.zen_id]['error'] or current[i.zen_id]['snapshot']!=i.snapshot for i in items):fail('Данные изменились. Откройте просмотр заново.')
        payloads=[intent(db,i) for i in identifiers];cursor=int(db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0])
    try:client.upsert('transaction',payloads,cursor)
    except Exception:pass  # The durable uncertain intent is authoritative until a fresh read.
    try:refresh(path,client)
    except HTTPException:pass
    with transaction(path,read_only=True) as db:return {'results':[r for r in results(db) if r['zen_id'] in identifiers]}
