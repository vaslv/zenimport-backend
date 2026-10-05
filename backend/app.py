import json
import os
import secrets
import threading
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from .db import initialize, transaction, entities
from .models import BankImport, Mapping, Decision
from .service import (audit, candidates, confirm, fail, get_row, import_bank,
                      mapped_account, review, same_transaction)
from .zenmoney import ZenMoney, request_diff

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
    client = zen_client or ZenMoney(mode, os.getenv('ZENMONEY_TOKEN', ''))
    # One process/worker. DB BEGIN IMMEDIATE additionally serializes mutations.
    lock = threading.Lock()
    app = FastAPI(title='ZenBridge', version='0.1.0')

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
    def state():
        with transaction(path) as db:
            cursor = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
            return {'mode': mode, 'last_sync': int(cursor['value']) if cursor else None,
                    'bank_accounts': [dict(r) for r in db.execute('SELECT * FROM bank_accounts')],
                    'zenmoney_accounts': entities(db, 'account'), 'categories': entities(db, 'tag'),
                    'zenmoney_transactions': entities(db, 'transaction'), 'review': review(db),
                    'outbox': [dict(r) for r in db.execute('SELECT bank_transaction,zen_id,state,error FROM outbox')]}

    @app.post('/api/imports')
    def imports(data: BankImport):
        with transaction(path) as db:
            return import_bank(db, data)

    @app.post('/api/zenmoney/sync')
    def synchronize():
        with lock, transaction(path) as db:
            sync(db)
            return {'status': 'synced', 'mode': mode}

    @app.put('/api/accounts/{identifier}/mapping')
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
    def decision(identifier: str, data: Decision):
        with lock, transaction(path) as db:
            sync(db)
            return confirm(db, identifier, data)

    @app.post('/api/outbox/send')
    def send():
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
                            db.execute("UPDATE bank_transactions SET state='sent' WHERE id=?", (identifier,))
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
                        db.execute("UPDATE bank_transactions SET state='sent' WHERE id=?", (identifier,))
                        audit(db, 'sent', {'id': identifier, 'zen_id': out['zen_id']})
                    results.append({'id': identifier, 'state': 'sent'})
                except Exception:
                    with transaction(path) as db:
                        db.execute("UPDATE outbox SET error='Результат неизвестен. Повтор сначала сверит сервер и сохранит UUID' WHERE bank_transaction=?", (identifier,))
                    results.append({'id': identifier, 'state': 'uncertain'})
        return {'mode': mode, 'results': results}

    @app.post('/api/outbox/{identifier}/reopen')
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
