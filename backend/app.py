"""Independent backend bootstrap. Business endpoints are implemented in B2–B5."""
from contextlib import asynccontextmanager
import sqlite3

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from zenmoney_api import Client

from . import __version__, storage
from .config import Settings
from .http import ApiBoundary, error_response
from .sync_contracts import ServerInfo


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        storage.initialize(settings.database_path)
        client = Client(settings.zenmoney_token) if settings.zenmoney_token else None
        app.state.zenmoney = client
        try:
            yield
        finally:
            if client is not None:
                client.close()

    app = FastAPI(title='ZenImport Backend', version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(ApiBoundary, token=settings.api_token)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc):
        return error_response(request.state.request_id, 'validation_error', 'Некорректный запрос', 422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc):
        code = 'method_not_allowed' if exc.status_code == 405 else 'not_found'
        response = error_response(request.state.request_id, code, 'Метод или ресурс недоступен', exc.status_code)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc):
        return error_response(request.state.request_id, 'internal_error', 'Внутренняя ошибка сервера', 500)

    @app.get('/health')
    def health():
        try:
            storage.check(settings.database_path)
        except (sqlite3.Error, RuntimeError, OSError):
            return JSONResponse({'status': 'unavailable'}, status_code=503)
        return {'status': 'ok'}

    @app.get('/api/v1/info', response_model=ServerInfo)
    def info():
        # Configuration does not prove connectivity or a fresh mirror. B4 owns refresh.
        return ServerInfo(service_version=__version__,
                          zenmoney_state='unavailable' if settings.zenmoney_token else 'not_configured',
                          last_zenmoney_refresh=None)

    return app
