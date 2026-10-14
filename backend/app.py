from . import jsoncodec as json
import os
import secrets
import threading
from pathlib import Path
from contextlib import asynccontextmanager
from zenmoney_api import Client
from .jsoncodec import exact_response
from .models import OperationEdit, OperationUpdates
from fastapi import FastAPI, HTTPException, Request, Query
from datetime import date
from .zenstore import history, summary
from fastapi.responses import FileResponse, JSONResponse
from .db import initialize, transaction, entities
from .models import BatchScope, BatchSend, BankImport, Mapping, Decision, DayDecision, BulkDayDecision, SplitDecision, SettlementDecision, CategoryDraft, CategorySend, NewOperationScope, NewOperationCategory, NewOperationSend
from .service import (audit, candidates, confirm, fail, get_row, import_bank,
                      mapped_account, review, same_transaction)
from .zenmoney import DemoClient, request_diff

ROOT = Path(__file__).resolve().parent.parent


def create_app(db_path=None, token=None, mode=None, zen_client=None):
    mode = mode or os.getenv('ZENMONEY_MODE', 'demo')
    token = token or os.getenv('API_TOKEN', '')
    if mode not in ('demo', 'live') or len(token) < 24:
        raise RuntimeError('Set ZENMONEY_MODE=demo|live and API_TOKEN (at least 24 characters)')
    if mode == 'live' and not os.getenv('ZENMONEY_TOKEN') and zen_client is None:
        raise RuntimeError('Live mode requires ZENMONEY_TOKEN')
    path = str(db_path or os.getenv('DATABASE_PATH', 'data/demo.sqlite3'))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    initialize(path, mode)
    client = zen_client if zen_client is not None else (Client(os.environ['ZENMONEY_TOKEN']) if mode == 'live' else DemoClient())

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if zen_client is None:
                client.close()
    # One process/worker. DB BEGIN IMMEDIATE additionally serializes mutations.
    lock = threading.Lock()
    app = FastAPI(title='ZenImport', version='0.1.0', lifespan=lifespan)

    @app.middleware('http')
    async def guard(request: Request, call_next):
        if request.url.path.startswith('/api/'):
            supplied = request.headers.get('authorization', '')
            if not secrets.compare_digest(supplied, f'Bearer {token}'):
                return JSONResponse({'detail': 'Неверный API-токен'}, status_code=401)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    return JSONResponse({'detail': 'Импорт превышает 2 МБ'}, status_code=413)
            request._body = bytes(body)
        return await call_next(request)

    def sync(db):
        try:
            request_diff(db, client)
        except Exception as exc:
            raise HTTPException(502, 'Не удалось синхронизировать ZenMoney; данные сохранены') from exc

    @app.get('/health')
    def health():
        return {'status': 'ok', 'mode': mode}

    @app.get('/demo-bank')
    def demo_bank():
        if mode != 'demo':
            fail('Демо отключено', 404)
        return FileResponse(ROOT / 'fixtures/demo-bank.html')

    @app.get('/api/state')
    @exact_response
    def state():
        with transaction(path, read_only=True) as db:
            cursor = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
            from .exclusions import zen_ids
            excluded = zen_ids(db)
            rows = review(db)
            from .day_review import proposals
            days = proposals(db, rows)
            from .creation import decorate as decorate_creation
            decorate_creation(db, rows)
            from .reconciliation import recipient_profiles
            profiles = recipient_profiles(rows, {}, entities(db, 'transaction'), entities(db, 'tag'))
            from .operations import public_catalog
            return {'mode': mode, 'capabilities': {'write': mode == 'demo', 'local_review': True, 'category_write': mode == 'live', 'create': mode == 'live'}, 'last_sync': int(cursor['value']) if cursor else None,
                    'bank_accounts': [dict(r) for r in db.execute('SELECT * FROM bank_accounts')],
                    'bank_imports': [{'id': r['id'], 'received_at': r['received_at'], 'bank_account': r['bank_account'],
                                      'capture': json.loads(r['payload']).get('capture')}
                                     for r in db.execute('SELECT id,received_at,bank_account,payload FROM imports ORDER BY received_at DESC,id DESC LIMIT 50')],
                    'zenmoney_accounts': entities(db, 'account'), 'categories': entities(db, 'tag'),
                    'zenmoney_instruments': entities(db, 'instrument'), 'zenmoney_merchants': entities(db, 'merchant'),
                    'zenmoney_transactions': history(db)['items'], 'zenmoney_summary': summary(db), 'review': rows, 'day_review': days, 'recipient_profiles': profiles,
                    'excluded_zen_ids': sorted(excluded),
                    'category_sends': [dict(r) for r in db.execute('SELECT zen_id,state,error FROM zen_category_sends')],
                    'category_draft_count': db.execute('SELECT count(*) FROM zen_category_drafts').fetchone()[0],
                    'operation_sends': [dict(r) for r in db.execute('SELECT zen_id,state,error FROM zen_operation_sends')],
                    'operation_draft_count': db.execute('SELECT count(*) FROM zen_operation_drafts').fetchone()[0],
                    'operation_catalog':public_catalog(db),
                    'outbox': [dict(r) for r in db.execute('SELECT bank_transaction,zen_id,state,error FROM outbox')]}

    @app.get('/api/zenmoney/history')
    @exact_response
    def zen_history(limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
                    start: date | None = None, end: date | None = None,
                    account: str | None = None, include_deleted: bool = False):
        if start and end and start > end:
            fail('Начало периода позже конца', 422)
        with transaction(path, read_only=True) as db:
            return history(db, limit=limit, offset=offset, start=start, end=end,
                           account=account, include_deleted=include_deleted)

    @app.post('/api/imports')
    @exact_response
    def imports(data: BankImport):
        with transaction(path) as db:
            return import_bank(db, data)

    @app.put('/api/new-operations/{identifier}/operation')
    @exact_response
    def bank_operation(identifier: str, data: OperationEdit):
        from .operations import save_bank
        with lock,transaction(path) as db:return save_bank(db,identifier,data)

    @app.put('/api/zenmoney/transactions/{identifier}/operation')
    @exact_response
    def zen_operation(identifier: str, data: OperationEdit):
        from .operations import save_zen
        with lock,transaction(path) as db:return save_zen(db,identifier,data)

    @app.post('/api/operation-updates/preview')
    @exact_response
    def operation_preview():
        from .operations import refresh,preview
        if mode!='live':fail('Отправка доступна в LIVE',403)
        with lock:
            refresh(path,client)
            with transaction(path,read_only=True) as db:return {'items':preview(db)}

    @app.post('/api/operation-updates/send')
    @exact_response
    def operation_send(data: OperationUpdates):
        from .operations import send
        if mode!='live':fail('Отправка доступна в LIVE',403)
        with lock:return send(path,client,data.items)

    @app.post('/api/operation-updates/verify')
    @exact_response
    def operation_verify():
        from .operations import refresh,results
        if mode!='live':fail('Отправка доступна в LIVE',403)
        with lock:
            refresh(path,client)
            with transaction(path,read_only=True) as db:return {'results':results(db)}

    @app.post('/api/zenmoney/sync')
    @exact_response
    def synchronize():
        with lock, transaction(path) as db:
            sync(db)
            return {'status': 'synced', 'mode': mode}

    @app.put('/api/accounts/{identifier}/mapping')
    @exact_response
    def mapping(identifier: str, data: Mapping):
        with transaction(path) as db:
            account = get_row(db, 'bank_accounts', identifier)
            if db.execute("SELECT 1 FROM bank_transactions WHERE bank_account=? AND state IN ('queued','sent','linked')",
                          (identifier,)).fetchone():
                fail('Счёт уже использован в подтверждённых операциях')
            account['zenmoney_account'] = data.zenmoney_account
            mapped_account(db, account)
            if db.execute('SELECT 1 FROM bank_accounts WHERE zenmoney_account=? AND id<>?',
                          (data.zenmoney_account, identifier)).fetchone():
                fail('Счёт ZenMoney уже связан с другим банковским счётом')
            db.execute('UPDATE bank_accounts SET zenmoney_account=? WHERE id=?',
                       (data.zenmoney_account, identifier))
            audit(db, 'mapping', {'bank_account': identifier, **data.model_dump()})
            return {'status': 'mapped'}

    @app.post('/api/review/{identifier}/confirm')
    @exact_response
    def decision(identifier: str, data: Decision):
        if mode != 'demo':
            fail('Запись отключена до завершения этапа интеграции', 403)
        with lock, transaction(path) as db:
            sync(db)
            return confirm(db, identifier, data)

    @app.post('/api/review-pairs/confirm')
    @exact_response
    def local_preview_pairs(data: BulkDayDecision):
        from .day_review import confirm_days
        with lock, transaction(path) as db:
            return confirm_days(db, data.days, review(db), pairs_only=True)

    @app.post('/api/review-days/confirm')
    @exact_response
    def local_days(data: BulkDayDecision):
        from .day_review import confirm_days
        with lock, transaction(path) as db:
            return confirm_days(db, data.days, review(db))

    @app.post('/api/review-days/{account_id}/{day}/confirm')
    @exact_response
    def local_day(account_id: str, day: date, data: DayDecision):
        from .day_review import confirm_day
        with lock, transaction(path) as db:
            return confirm_day(db, account_id, str(day), data.snapshot, review(db))

    @app.post('/api/review/{identifier}/link')
    @exact_response
    def local_link(identifier: str, data: Decision):
        # This endpoint never calls the remote API or creates an outbox payload.
        if data.action != 'link':
            fail('Здесь доступно только подтверждение существующей связи', 422)
        with lock, transaction(path) as db:
            return confirm(db, identifier, data)

    @app.post('/api/batch/preview')
    @exact_response
    def batch_preview(data: BatchScope):
        if mode != 'live': fail('Отправка доступна в режиме LIVE',403)
        from . import batch_send, creation
        with lock:
            with transaction(path,read_only=True) as db: batch_send.target(db,data)
            batch_send.refresh(path,client)
            with transaction(path) as db:
                creation.create_local(db,data)
                return {'items':batch_send.preview(db,data)}

    @app.post('/api/batch/retry-preview')
    @exact_response
    def batch_retry_preview(data: BatchScope):
        if mode != 'live': fail('Отправка доступна в режиме LIVE',403)
        if not data.creations_only: fail('Повторная подготовка доступна только для новых записей',422)
        from . import batch_send, creation
        with lock:
            with transaction(path,read_only=True) as db: batch_send.target(db,data)
            return {'items':[{**item,'kind':'creation','id':item['bank_id']}
                             for item in creation.retry_preview(path,client,data)]}

    @app.post('/api/batch/send')
    @exact_response
    def batch_send_route(data: BatchSend):
        if mode != 'live': fail('Отправка доступна в режиме LIVE',403)
        from .batch_send import send
        with lock: return send(path,client,data)

    @app.post('/api/batch/verify')
    @exact_response
    def batch_verify(data: BatchScope):
        if mode != 'live': fail('Отправка доступна в режиме LIVE',403)
        from . import batch_send
        with lock:
            with transaction(path,read_only=True) as db: batch_send.target(db,data)
            batch_send.refresh(path,client)
            return {'results':batch_send.results(path,data)}

    @app.post('/api/new-operations/prepare')
    @exact_response
    def prepare_new(data: NewOperationScope):
        from .creation import prepare
        with lock, transaction(path) as db:
            return prepare(db, data)

    @app.put('/api/new-operations/{identifier}/category')
    @exact_response
    def new_category(identifier: str, data: NewOperationCategory):
        from .creation import save_category
        with lock, transaction(path) as db:
            return save_category(db, identifier, data.category_id, data.remember_recipient)

    @app.delete('/api/new-operations/{identifier}/recipient-rule')
    @exact_response
    def delete_recipient_rule(identifier: str):
        from .creation import remove_rule
        with lock, transaction(path) as db: return remove_rule(db,identifier)

    @app.post('/api/new-operations/create')
    @exact_response
    def create_local_new(data: NewOperationScope):
        if mode != 'live': fail('Создание доступно в режиме LIVE',403)
        from .creation import create_local
        with lock, transaction(path) as db: return create_local(db,data)

    @app.post('/api/new-operations/{identifier}/reopen')
    @exact_response
    def reopen_new(identifier: str):
        from .creation import reopen
        with lock, transaction(path) as db: return reopen(db,identifier)

    @app.post('/api/new-operations/prepare-preview')
    @exact_response
    def prepare_preview_new(data: NewOperationScope):
        if mode != 'live': fail('Создание доступно в режиме LIVE',403)
        from .creation import refresh, create_local, preview
        with lock:
            refresh(path,client)
            with transaction(path) as db:
                create_local(db,data)
                return {'items':preview(db,data)}

    @app.post('/api/new-operations/preview')
    @exact_response
    def preview_new(data: NewOperationScope):
        if mode != 'live': fail('Создание доступно в режиме LIVE',403)
        from .creation import refresh, preview
        with lock:
            refresh(path,client)
            with transaction(path) as db:
                return {'items':preview(db,data)}

    @app.post('/api/new-operations/send')
    @exact_response
    def send_new(data: NewOperationSend):
        if mode != 'live': fail('Создание доступно в режиме LIVE',403)
        from .creation import send
        with lock: return send(path,client,data)

    @app.post('/api/new-operations/verify')
    @exact_response
    def verify_new():
        if mode != 'live': fail('Проверка создания доступна в режиме LIVE',403)
        from .creation import refresh
        with lock: return refresh(path,client)

    @app.post('/api/category-updates/preview')
    @exact_response
    def preview_categories():
        if mode != 'live': fail('Отправка категорий доступна в LIVE', 403)
        from .category_send import refresh, preview
        with lock:
            refresh(path, client)
            with transaction(path, read_only=True) as db: return {'items': preview(db)}

    @app.post('/api/category-updates/send')
    @exact_response
    def send_categories(data: CategorySend):
        if mode != 'live': fail('Отправка категорий доступна в LIVE', 403)
        from .category_send import send
        with lock: return send(path, client, data.items)

    @app.post('/api/category-updates/verify')
    @exact_response
    def verify_categories():
        from .category_send import refresh, results
        with lock:
            refresh(path, client)
            with transaction(path, read_only=True) as db: return {'results': results(db)}

    @app.put('/api/zenmoney/transactions/{identifier}/category')
    @exact_response
    def category_draft(identifier: str, data: CategoryDraft):
        from .category_drafts import save
        with lock, transaction(path) as db:
            return save(db, identifier, data.category_id, data.expected_tags)

    @app.put('/api/exclusions/{source}/{identifier}')
    @exact_response
    def exclude(source: str, identifier: str):
        from .exclusions import set_excluded
        with lock, transaction(path) as db:
            return set_excluded(db, source, identifier, True)

    @app.delete('/api/exclusions/{source}/{identifier}')
    @exact_response
    def restore_excluded(source: str, identifier: str):
        from .exclusions import set_excluded
        with lock, transaction(path) as db:
            return set_excluded(db, source, identifier, False)

    @app.post('/api/reconciliation-groups')
    @exact_response
    def local_settlement(data: SettlementDecision):
        from .link_groups import confirm_settlement
        with lock, transaction(path) as db:
            return confirm_settlement(db, data, review(db))

    @app.post('/api/review/{identifier}/split-link')
    @exact_response
    def local_split(identifier: str, data: SplitDecision):
        from .day_review import confirm_split
        with lock, transaction(path) as db:
            return confirm_split(db, identifier, data, review(db))

    @app.delete('/api/review/{identifier}/link')
    @exact_response
    def unlink(identifier: str):
        with lock, transaction(path) as db:
            row = get_row(db, 'bank_transactions', identifier)
            if row['state'] != 'linked':
                fail('Отменить можно только локальную связь')
            from .link_groups import unlink as unlink_group
            unlink_group(db, identifier)
            return {'state': 'pending'}

    @app.post('/api/outbox/send')
    @exact_response
    def send():
        if mode != 'demo':
            fail('Запись отключена до завершения этапа интеграции', 403)
        results = []
        with lock:
            with transaction(path) as db:
                ids = [r[0] for r in db.execute("SELECT bank_transaction FROM outbox WHERE state IN ('queued','uncertain')")]
            for identifier in ids:
                with transaction(path) as db:
                    sync(db)
                    out = dict(db.execute('SELECT * FROM outbox WHERE bank_transaction=?', (identifier,)).fetchone())
                    payload = json.loads(out['payload'])
                    row = get_row(db, 'bank_transactions', identifier)
                    account = get_row(db, 'bank_accounts', row['bank_account'])
                    mapped_account(db, account)
                    existing = next((x for x in entities(db, 'transaction') if x['id'] == out['zen_id']), None)
                    if existing:
                        if same_transaction(payload, existing):
                            db.execute("UPDATE outbox SET state='sent', error=NULL WHERE bank_transaction=?", (identifier,))
                            from .link_groups import insert
                            insert(db, [identifier], [out['zen_id']], 'single', 'outbox', state='sent')
                            results.append({'id': identifier, 'state': 'sent'})
                        else:
                            db.execute("UPDATE outbox SET state='conflict',error='Удалённая операция отличается; нужна ручная сверка' WHERE bank_transaction=?", (identifier,))
                            results.append({'id': identifier, 'state': 'conflict'})
                        continue
                    # Any new candidate after approval blocks automatic sending.
                    decision_row = db.execute("SELECT data FROM audit WHERE operation='confirm' ORDER BY id DESC").fetchall()
                    approved = next(json.loads(r[0]) for r in decision_row if json.loads(r[0])['id'] == identifier)
                    current = sorted(x['id'] for x in candidates(db, row, account))
                    if current != sorted(approved['reviewed_candidates']):
                        db.execute("UPDATE outbox SET error='Совпадения изменились; отправка заблокирована' WHERE bank_transaction=?", (identifier,))
                        results.append({'id': identifier, 'state': 'blocked'})
                        continue
                    # Commit intent before the network call; crash/retry reuses immutable UUID/payload.
                    db.execute("UPDATE outbox SET state='uncertain',error=NULL WHERE bank_transaction=?", (identifier,))
                try:
                    with transaction(path) as db:
                        request_diff(db, client, [payload])
                        existing = next((x for x in entities(db, 'transaction') if x['id'] == out['zen_id']), None)
                        if not existing or not same_transaction(payload, existing):
                            raise ValueError('No matching acknowledgement')
                        db.execute("UPDATE outbox SET state='sent',error=NULL WHERE bank_transaction=?", (identifier,))
                        from .link_groups import insert
                        insert(db, [identifier], [out['zen_id']], 'single', 'outbox', state='sent')
                        audit(db, 'sent', {'id': identifier, 'zen_id': out['zen_id']})
                    results.append({'id': identifier, 'state': 'sent'})
                except Exception:
                    with transaction(path) as db:
                        db.execute("UPDATE outbox SET error='Результат неизвестен. Повтор сначала сверит сервер и сохранит UUID' WHERE bank_transaction=?", (identifier,))
                    results.append({'id': identifier, 'state': 'uncertain'})
        return {'mode': mode, 'results': results}

    @app.post('/api/outbox/{identifier}/reopen')
    @exact_response
    def reopen(identifier: str):
        with lock, transaction(path) as db:
            out = db.execute('SELECT state FROM outbox WHERE bank_transaction=?', (identifier,)).fetchone()
            if not out or out['state'] != 'queued':
                fail('Вернуть на проверку можно только ещё не отправлявшуюся операцию')
            db.execute('DELETE FROM outbox WHERE bank_transaction=?', (identifier,))
            db.execute("UPDATE bank_transactions SET state='pending',linked_id=NULL WHERE id=?", (identifier,))
            audit(db, 'reopen', {'id': identifier})
            return {'state': 'pending'}

    return app
