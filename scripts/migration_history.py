"""Controlled live history and independent assertions for migration_lab only."""
from collections import defaultdict
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from zenmoney_api import Client, entities, transactions as tx
from zenmoney_api.protocol import decode_json, encode_json

import migration_lab as lab


class AuditedTransport(httpx.BaseTransport):
    """Persist actual outgoing write IDs without credentials or response bodies."""
    def __init__(self, path, transport=None):
        self.path = path
        self.transport = transport if transport is not None else httpx.HTTPTransport(retries=0,trust_env=False)

    def handle_request(self, request):
        data = decode_json(request.content)
        writes = {k:[row['id'] for row in v] for k,v in data.items()
            if k not in ('serverTimestamp','currentClientTimestamp','forceFetch','deletion') and v}
        if writes or data.get('deletion'):
            event = {'event_id':str(uuid4()),'at':int(time.time()),'entities':writes,
                'deletions':[row['id'] for row in data.get('deletion',[])]}
            fd=os.open(self.path, os.O_WRONLY|os.O_CREAT|os.O_APPEND, 0o600)
            with os.fdopen(fd,'ab') as stream:
                fcntl.flock(stream,fcntl.LOCK_EX)
                stream.write(encode_json(event)+b'\n');stream.flush();os.fsync(stream.fileno())
        return self.transport.handle_request(request)

    def close(self):
        self.transport.close()


def create_lab_app():
    from backend.app import create_app
    from backend.config import Settings
    settings=Settings.from_env()
    audit=settings.database_path.parent/'sdk-audit.jsonl'
    return create_app(settings,client_factory=lambda token:Client(token,transport=AuditedTransport(audit)))


@contextmanager
def history_lock(state):
    with (state/'.history.lock').open('a') as lock:
        os.chmod(lock.name,0o600)
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another history action is running') from None
        yield


def history_plan(data, catalog, identity):
    user=identity['expected_user'];run=identity['run'];prefix='ZenImport LAB '+run[:8]
    base=catalog['plan'];accounts=base['account']
    assert lab.verify_catalog(data,base), 'Initial lab catalog must still be untouched'
    other=entities.tag(user=user,title=prefix+' Other Expense',showIncome=False,budgetIncome=False)
    empty=[entities.account(user=user,title=prefix+' EMPTY '+a['title'].split()[-2]+' '+str(i),
        instrument=a['instrument']) for i,a in enumerate(accounts)]
    debt=next(a['id'] for a in data['account'] if a.get('type')=='debt')
    today=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()
    result={'run':run,'user':user,'debt_account':debt,'stages':{},
        'catalog_changes':{'account':empty,'tag':[other]},'other_tag':other['id'],
        'baseline':{k:data.get(k,[]) for k in ('account','tag','merchant','transaction')},'variants':{}}
    for variant,targets in [('migrated',accounts),('empty',empty)]:
        a,b,c=targets;vnd=a['instrument'];usd=c['instrument']
        variant_plan={'accounts':targets,'seed':[],'records':[],'cases':[], 'created':{}}
        serial=0
        def add(label,offset,bank_moves,remote_moves=None,phase='initial'):
            nonlocal serial
            serial+=1
            day=(today-timedelta(days=offset)).isoformat()
            stamp=int(datetime.fromisoformat(day+f'T10:{serial:02d}:00+07:00').timestamp())
            common=dict(user=user,date=day,now=stamp,viewed=True,
                comment=prefix+' '+variant+' '+label,payee=base['merchant'][0]['title'])
            rows=[]
            if remote_moves=='transfer':
                rows=[tx.transfer(from_account=a['id'],to_account=b['id'],from_instrument=vnd,
                    to_instrument=vnd,amount_out=1103,amount_in=1103,**common)]
            elif remote_moves=='fx':
                rows=[tx.transfer(from_account=a['id'],to_account=c['id'],from_instrument=vnd,
                    to_instrument=usd,amount_out=1207,amount_in=Decimal('1.23'),**common)]
            elif remote_moves in ('lend','collect','borrow','repay'):
                rows=[tx.debt(action=remote_moves,account=a['id'],debt_account=debt,instrument=vnd,
                    amount=abs(bank_moves[0][1]),**common)]
            elif remote_moves is not None:
                for account,amount,tag in remote_moves:
                    builder=tx.income if amount>0 else tx.expense
                    rows.append(builder(account=account['id'],instrument=account['instrument'],
                        amount=abs(amount),categories=[tag] if tag else None,**common))
            keys=[]
            for index,(account,amount) in enumerate(bank_moves):
                key=variant+':'+label+':'+str(index);keys.append(key)
                variant_plan['records'].append({'key':key,'target':account['id'],'phase':phase,
                    'record':{'external_id':'lab:'+run+':'+key,'identity_kind':'bank','identity_version':'migration-lab-1',
                        'occurred_at':day+f'T10:{serial:02d}:00+07:00','local_date':day,
                        'direction':'income' if amount>0 else 'expense','amount':str(abs(amount)),
                        'currency':'USD' if account['instrument']==usd else 'VND','description':common['comment'],
                        'beneficiary_name':base['merchant'][0]['title'],'beneficiary_account':'lab-beneficiary-'+label,
                        'details_state':'complete','details':{'channel':'60'}}})
            variant_plan['cases'].append({'label':label,'bank_keys':keys,'seed_ids':[r['id'] for r in rows],
                'phase':phase,'creates':0 if rows else len(keys)})
            variant_plan['seed'].extend(rows)
        expense,income=[x['id'] for x in base['tag']]
        for label,value,category in [('expense',-1001,expense),('income',2002,income),
            ('purchase',-301,expense),('refund',301,expense),('uncategorized',-503,None),
            ('repeat-1',-607,expense),('repeat-2',-607,expense)]:
            add(label,12,[(a,value)],[(a,value,category)])
        for action,amount in [('lend',-161),('collect',162),('borrow',163),('repay',-164)]:
            add('debt-'+action,11,[(a,amount)],action)
        add('transfer',11,[(a,-1103),(b,1103)],'transfer')
        add('fx',11,[(a,-1207),(c,Decimal('1.23'))],'fx')
        add('split',10,[(a,-1901)],[(a,-701,expense),(a,-1200,expense)])
        add('merge',9,[(a,-1301),(a,-1303)],[(a,-2604,expense)])
        add('settlement',8,[(a,-4001)],[(a,-5003,expense),(a,1002,income)])
        add('missing-expense',7,[(a,-1709)])
        add('missing-income',7,[(b,1907)])
        add('new-expense',6,[(a,-2011)],phase='later')
        add('new-income-usd',6,[(c,Decimal('2.34'))],phase='later')
        variant_plan['import_start']=min(x['record']['local_date'] for x in variant_plan['records'])
        result['variants'][variant]=variant_plan
    return result


def verify_rows(data, changes):
    for kind,rows in changes.items():
        current={r['id']:r for r in data.get(kind,[])}
        for row in rows:
            got=current.get(row['id'])
            keys={'id','user','title','instrument','type'} if kind=='account' else {'id','user','title'}
            if kind=='transaction':
                keys={'id','user','date','incomeAccount','outcomeAccount','incomeInstrument','outcomeInstrument','income','outcome','deleted','viewed'}
                if row.get('tag') is not None:keys.add('tag')
            if got is None or any(got.get(k)!=row.get(k) for k in keys): return False
    return True


def originals_unchanged(data,journal):
    owned={a['id'] for v in journal['variants'].values() for a in v['accounts']}
    for kind,rows in journal['baseline'].items():
        current={r['id']:r for r in data.get(kind,[])}
        for old in rows:
            got=current.get(old['id'])
            ignore={'changed'}
            if kind=='account' and (old['id'] in owned or old['id']==journal['debt_account']):ignore.add('balance')
            if got is None or any(got.get(k)!=v for k,v in old.items() if k not in ignore):return False
    return True


def write_stage(api,path,journal,label,changes,verify=None):
    """A persisted intent can only be checked on resume, never resent."""
    step=journal['stages'].get(label)
    if step is None:
        existing={r['id'] for rows in journal['baseline'].values() for r in rows}
        if label!='mobile-edit' and any(r['id'] in existing for rows in changes.values() for r in rows):
            raise RuntimeError('Write would target a baseline identity')
        step={'state':'intent','changes':changes};journal['stages'][label]=step;lab.save(path,journal)
        try: api.diff(0,changes=changes)
        except Exception:
            step['state']='uncertain';lab.save(path,journal)
            raise RuntimeError('Live write outcome unknown; read verification only on resume') from None
    data=api.read_diff()
    if not originals_unchanged(data,journal):raise RuntimeError('Preexisting data changed')
    if not (verify(data,step['changes']) if verify else verify_rows(data,step['changes'])):
        raise RuntimeError('Persisted write not confirmed; do not resend')
    step['state']='verified';lab.save(path,journal)
    return data


def prepare_history(api,state,identity,token):
    path=state/'history.json'
    data=api.read_diff();lab.validate_identity(data,identity,token,identity['run'])
    if path.exists():
        journal=decode_json(path.read_bytes())
        if journal.get('run')!=identity['run'] or journal.get('user')!=identity['expected_user']:
            raise RuntimeError('History belongs to another user/run')
    else:
        catalog=decode_json((state/'catalog.json').read_bytes())
        journal=history_plan(data,catalog,identity);lab.save(path,journal)
    write_stage(api,path,journal,'history-catalog',journal['catalog_changes'])
    for variant,plan in journal['variants'].items():
        write_stage(api,path,journal,'seed-'+variant,{'transaction':plan['seed']})
    return journal


def ledger(rows,targets):
    result=defaultdict(Decimal)
    for row in rows:
        if row.get('deleted') or row.get('hold'):continue
        for side in ('income','outcome'):
            if row.get(side+'Account') in targets and row.get(side):
                result[(row['date'],row[side+'Account'],str(row[side+'Instrument']),side)]+=Decimal(row[side])
    return dict(result)


def bank_ledger(records):
    result=defaultdict(Decimal)
    for item in records:
        row=item['record'];sign=1 if row['direction']=='income' else -1
        result[(row['local_date'],item['target'])]+=sign*Decimal(row['amount'])
    return {k:v for k,v in result.items() if v}


def net_ledger(gross):
    result=defaultdict(Decimal)
    for (day,account,_,side),amount in gross.items():result[(day,account)]+=amount*(1 if side=='income' else -1)
    return {k:v for k,v in result.items() if v}


def collect_delivery_ids(db,plan):
    rows=list(db.execute('''SELECT b.external_id,d.zen_id FROM deliveries d
        JOIN bank_transactions b ON b.id=d.bank_id WHERE d.ever_attempted=1
        UNION SELECT b.external_id,r.zen_id FROM processing_receipts r
        JOIN bank_transactions b ON b.id=r.bank_id'''))
    expected={item['record']['external_id']:item['key'] for item in plan['records']}
    # Only the intentionally missing records can own newly generated delivery IDs.
    missing={k for case in plan['cases'] if case['creates'] for k in case['bank_keys']}
    result={}
    for external,zen in rows:
        key=expected.get(external)
        if key not in missing:continue
        if key in result and result[key]!=zen:raise RuntimeError('Multiple UUIDs for one bank identity')
        result[key]=zen
    return result


def verify_history(api,state,identity,token,variant,instance,*,later=False,edited=False):
    from backend import storage
    path=state/'history.json';journal=decode_json(path.read_bytes());plan=journal['variants'][variant]
    if journal['run']!=identity['run'] or journal['user']!=identity['expected_user']:raise RuntimeError('Wrong history')
    data=api.read_diff();lab.validate_identity(data,identity,token,identity['run'])
    if not originals_unchanged(data,journal):raise RuntimeError('Preexisting data changed')
    targets={a['id'] for a in plan['accounts']}
    records=[x for x in plan['records'] if later or x['phase']=='initial']
    missing={key for case in plan['cases'] if case['creates'] and (later or case['phase']=='initial') for key in case['bank_keys']}
    with closing(storage.connect_readonly(state/('server-'+instance)/'zenimport.sqlite3')) as db:
        storage.validate_owner(db,allow_upgrade=False);storage.check_integrity(db)
        created=collect_delivery_ids(db,plan)
        if set(created)!=missing:raise RuntimeError('Unexpected created bank identities')
        for key,zen in created.items():
            previously_known=key in plan['created']
            if key in plan['created'] and plan['created'][key]!=zen:raise RuntimeError('UUID changed across servers')
            plan['created'][key]=zen
            count=db.execute("SELECT count(*) FROM delivery_attempts WHERE zen_id=? AND outcome='intent'",(zen,)).fetchone()[0]
            if count not in ((0,1) if previously_known else (1,)):raise RuntimeError('Duplicate/missing delivery intent')
        snapshot=dict(instance_id=db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0],
            intents=db.execute("SELECT count(*) FROM delivery_attempts WHERE outcome='intent'").fetchone()[0])
    expected=deepcopy(plan['seed'])
    for item in records:
        if item['key'] not in missing:continue
        row=item['record'];account=next(a for a in plan['accounts'] if a['id']==item['target'])
        builder=tx.income if row['direction']=='income' else tx.expense
        expected.append(builder(id=created[item['key']],user=journal['user'],date=row['local_date'],
            account=account['id'],instrument=account['instrument'],amount=Decimal(row['amount']),viewed=False))
    deleted=set()
    if edited:
        mobile=journal['mobile']
        deleted.add(mobile['deleted'])
        expected=[mobile['rows'].get(row['id'],row) for row in expected if row['id'] not in deleted]
        for identifier,saved in mobile['rows'].items():
            current=next((r for r in data['transaction'] if r['id']==identifier and not r.get('deleted')),None)
            if current is None or any(current.get(k)!=v for k,v in saved.items() if k!='changed'):
                raise RuntimeError('Mobile edits overwritten')
        deleted_keys=set(mobile['deleted_bank_keys'])
        records=[r for r in records if r['key'] not in deleted_keys]
    observed=[r for r in data['transaction'] if not r.get('deleted') and not r.get('hold')
        and targets.intersection((r.get('incomeAccount'),r.get('outcomeAccount')))]
    if {r['id'] for r in observed}!={r['id'] for r in expected}:raise RuntimeError('Remote IDs include missing/duplicate entries')
    if not verify_rows(data,{'transaction':expected}):raise RuntimeError('Per-UUID fields, categories or unread state differ')
    actual_gross=ledger(observed,targets);expected_gross=ledger(expected,targets)
    if actual_gross!=expected_gross:raise RuntimeError('Daily expense/income ledger differs')
    if net_ledger(actual_gross)!=bank_ledger(records):raise RuntimeError('Daily bank movement differs')
    accounts={a['id']:a for a in data['account']}
    for target in targets:
        movement=sum(v for (_,account),v in net_ledger(actual_gross).items() if account==target)
        if Decimal(accounts[target]['balance'])!=movement:raise RuntimeError('Account balance differs')
    # The virtual debt balance is not an FX sum. Check its per-currency movements.
    if ledger(observed,{journal['debt_account']})!=ledger(expected,{journal['debt_account']}):
        raise RuntimeError('Debt ledger differs')
    lab.save(path,journal)
    result=dict(variant=variant,instance=instance,later=later,edited=edited,
        bank_records=len(records)+len(deleted),remote_active=len(observed),
        intentional_deletions=len(deleted),expected_creations=len(missing),
        bank_comparison='excluding_intentionally_deleted' if edited else 'all_bank_records',
        identity_verified=True,baseline_preserved=True,exact_ids=True,
        gross_daily_ledger=True,bank_daily_net=True,account_balances=True,debt_ledger=True,**snapshot)
    lab.save(state/(variant+'-'+instance+('-later' if later else '')+('-edited' if edited else '')+'-verification.json'),result)
    print(encode_json(result).decode())
    return result


def mobile_changes(api,state,identity,token):
    path=state/'history.json';journal=decode_json(path.read_bytes());plan=journal['variants']['migrated']
    data=api.read_diff();lab.validate_identity(data,identity,token,identity['run'])
    rows={r['id']:r for r in data['transaction']}
    if 'mobile-edit' not in journal['stages']:
        category_id=next(c['seed_ids'][0] for c in plan['cases'] if c['label']=='expense')
        deletion_id=next(c['seed_ids'][0] for c in plan['cases'] if c['label']=='uncategorized')
        debt_id=plan['created']['migrated:missing-expense:0']
        changes=[tx.update(rows[category_id],tag=[journal['other_tag']],comment='LAB user changed category/comment',viewed=True),
            tx.update(rows[debt_id],incomeAccount=journal['debt_account'],incomeInstrument=rows[debt_id]['outcomeInstrument'],
                income=rows[debt_id]['outcome'],tag=None,payee='LAB user debt',merchant=None,viewed=True),
            tx.update(rows[deletion_id],deleted=True)]
        journal['mobile']={'deleted':deletion_id,'deleted_bank_keys':['migrated:uncategorized:0'],
            'rows':{r['id']:r for r in changes if r['id']!=deletion_id}}
        lab.save(path,journal)
    else: changes=journal['stages']['mobile-edit']['changes']['transaction']
    def verify_mobile(actual,planned):
        current={r['id']:r for r in actual['transaction']}
        for row in planned['transaction']:
            got=current.get(row['id'])
            if row.get('deleted'):
                if got is not None and not got.get('deleted'):return False
            elif got is None or any(got.get(k)!=row[k] for k in
                    ('incomeAccount','outcomeAccount','income','outcome','tag','comment','viewed')):
                return False
        return True
    updated=write_stage(api,path,journal,'mobile-edit',{'transaction':changes},verify_mobile)
    # Save normalized API output, so later assertions do not invent server fields.
    normalized={r['id']:r for r in updated['transaction']}
    journal['mobile']['rows']={key:normalized[key] for key in journal['mobile']['rows']}
    lab.save(path,journal)
    print(json.dumps({'stage':'mobile-edit','owned_rows_updated':2,'owned_rows_deleted':1,'verified':True}))
