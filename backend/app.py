"""Independent backend bootstrap. Business endpoints are implemented in B2–B5."""
from contextlib import asynccontextmanager
import asyncio
import sqlite3
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from zenmoney_api import Client

from . import __version__, storage
from .config import Settings
from .http import ApiBoundary, error_response
from .capture_policy import SyncError, MESSAGES
from .sync_api import router
from .sync_contracts import ServerInfo
from .mirror import Mirror
from .delivery import DeliveryWorker


def create_app(settings: Settings | None = None, *, clock=None, client_factory=Client, start_worker=True):
    settings = settings or Settings.from_env()
    clock = clock or (lambda: int(time.time()))

    @asynccontextmanager
    async def lifespan(app):
        storage.initialize(settings.database_path)
        client = client_factory(settings.zenmoney_token) if settings.zenmoney_token else None
        app.state.zenmoney = client
        try:
            app.state.mirror = Mirror(settings.database_path, client, settings.zenmoney_token, clock)
            app.state.delivery = DeliveryWorker(settings.database_path, app.state.mirror, clock)
            if start_worker:
                app.state.delivery.start()
            try:
                yield
            finally:
                await asyncio.to_thread(app.state.delivery.stop)
        finally:
            if client is not None:
                client.close()

    app = FastAPI(title='ZenImport Backend', version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(ApiBoundary, token=settings.api_token)
    app.include_router(router(settings.database_path, clock, lambda: app.state.mirror, lambda: app.state.delivery.wake()))

    @app.exception_handler(SyncError)
    async def sync_error(request: Request, exc):
        return error_response(request.state.request_id, exc.code, MESSAGES[exc.code], exc.status, retryable=exc.retryable)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc):
        return error_response(request.state.request_id, 'validation_error', 'Invalid request', 422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc):
        code = 'method_not_allowed' if exc.status_code == 405 else 'not_found'
        response = error_response(request.state.request_id, code, 'Method or resource unavailable', exc.status_code)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc):
        return error_response(request.state.request_id, 'internal_error', 'Internal server error', 500)

    @app.get('/health')
    def health():
        try:
            storage.check(settings.database_path)
        except (sqlite3.Error, RuntimeError, OSError):
            return JSONResponse({'status': 'unavailable'}, status_code=503)
        return {'status': 'ok'}

    @app.get('/api/v1/info', response_model=ServerInfo)
    def info():
        return ServerInfo(service_version=__version__, **app.state.mirror.info())

    return app
