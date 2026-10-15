"""Small ASGI boundary: bounded bodies, authentication and redacted errors."""
import secrets
from uuid import uuid4

from starlette.responses import JSONResponse

from .sync_contracts import ApiError, ErrorResponse, MAX_BODY_BYTES


def error_response(request_id, code, message, status, *, retryable=False):
    body = ErrorResponse(error=ApiError(
        code=code, message=message, retryable=retryable, request_id=request_id,
    ))
    return JSONResponse(body.model_dump(mode='json'), status_code=status,
                        headers={'Cache-Control': 'no-store', 'X-Request-ID': str(request_id)})


class ApiBoundary:
    def __init__(self, app, *, token):
        self.app = app
        self.authorization = ('Bearer ' + token).encode('ascii')

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        identifier = uuid4()
        scope.setdefault('state', {})['request_id'] = identifier
        path = scope['path']
        if not (path == '/api' or path.startswith('/api/')):
            return await self.app(scope, receive, send)

        async def reject(code, message, status):
            await error_response(identifier, code, message, status)(scope, receive, send)

        supplied = [value for key, value in scope['headers'] if key.lower() == b'authorization']
        if len(supplied) != 1 or not secrets.compare_digest(supplied[0], self.authorization):
            return await reject('unauthorized', 'Неверный токен подключения', 401)
        parts = path.split('/')
        if len(parts) > 2 and parts[2] and parts[2] != 'v1':
            return await reject('unsupported_api_version', 'Версия API не поддерживается', 404)
        lengths = [v for k, v in scope['headers'] if k.lower() == b'content-length']
        if lengths:
            if len(lengths) != 1 or not lengths[0].isdigit():
                return await reject('validation_error', 'Некорректный запрос', 422)
            if len(lengths[0]) > 10 or int(lengths[0]) > MAX_BODY_BYTES:
                return await reject('payload_too_large', 'Запрос превышает допустимый размер', 413)

        body = bytearray()
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            chunk = event.get('body', b'')
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                return await reject('payload_too_large', 'Запрос превышает допустимый размер', 413)
            body.extend(chunk)
            if not event.get('more_body', False):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        started = False

        async def response(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                message = dict(message)
                headers = [(k, v) for k, v in message.get('headers', [])
                           if k.lower() not in (b'cache-control', b'x-request-id')]
                message['headers'] = headers + [(b'cache-control', b'no-store'), (b'x-request-id', str(identifier).encode())]
            await send(message)

        try:
            await self.app(scope, replay, response)
        except Exception:
            if started:
                raise RuntimeError(f'Response failed; request_id={identifier}') from None
            await error_response(identifier, 'internal_error', 'Внутренняя ошибка сервера', 500)(scope, receive, send)
