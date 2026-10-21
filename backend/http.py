"""Small ASGI boundary: bounded bodies, authentication and redacted errors."""
import secrets
from uuid import UUID, uuid4

from starlette.responses import JSONResponse

from .sync_contracts import ApiError, ErrorResponse, MAX_BODY_BYTES, MAX_MIGRATION_BYTES
from .capture_policy import MESSAGES, SyncError


def error_response(request_id, code, message, status, *, retryable=False):
    body = ErrorResponse(error=ApiError(
        code=code, message=message, retryable=retryable, request_id=request_id,
    ))
    return JSONResponse(body.model_dump(mode='json'), status_code=status,
                        headers={'Cache-Control': 'no-store', 'X-Request-ID': str(request_id)})


class ApiBoundary:
    def __init__(self, app, *, token, migration_gate=None):
        self.app = app
        self.authorization = ('Bearer ' + token).encode('ascii')
        self.migration_gate = migration_gate

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
            return await reject('unauthorized', 'Invalid connection token', 401)
        expected_context = None
        context_values = [[v for k, v in scope['headers'] if k.lower() == name]
                          for name in (b'x-zenimport-installation', b'x-zenimport-dataset')]
        if any(context_values):
            try:
                if any(len(values) != 1 for values in context_values):
                    raise ValueError('Incomplete context')
                expected_context = tuple(values[0].decode('ascii') for values in context_values)
                if any(str(UUID(value)) != value for value in expected_context):
                    raise ValueError('Invalid context')
            except (ValueError, UnicodeError):
                return await reject('validation_error', 'Invalid storage context', 422)
        if path.startswith('/api/v1/contexts/'):
            parts = path.split('/', 6)
            try:
                if len(parts) != 7 or not parts[6] or parts[6].startswith('migration/'):
                    raise ValueError('Not an ordinary context route')
                selected = tuple(parts[4:6])
                if any(str(UUID(value)) != value for value in selected):
                    raise ValueError('Invalid context')
                if expected_context is not None and expected_context != selected:
                    raise ValueError('Conflicting context')
            except ValueError:
                return await reject('validation_error', 'Invalid storage context', 422)
            expected_context = selected
            path = '/api/v1/' + parts[6]
            scope = dict(scope, path=path, raw_path=path.encode('utf-8'))
        parts = path.split('/')
        if len(parts) > 2 and parts[2] and parts[2] != 'v1':
            return await reject('unsupported_api_version', 'Unsupported API version', 404)
        maximum = MAX_MIGRATION_BYTES if path == '/api/v1/migration/import' and scope['method'] == 'POST' else MAX_BODY_BYTES
        lengths = [v for k, v in scope['headers'] if k.lower() == b'content-length']
        if lengths:
            if len(lengths) != 1 or not lengths[0].isdigit():
                return await reject('validation_error', 'Invalid request', 422)
            if len(lengths[0]) > 10 or int(lengths[0]) > maximum:
                return await reject('payload_too_large', 'Request exceeds the size limit', 413)

        body = bytearray()
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            chunk = event.get('body', b'')
            if len(body) + len(chunk) > maximum:
                return await reject('payload_too_large', 'Request exceeds the size limit', 413)
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
            if self.migration_gate and path != '/api/v1/info' and not path.startswith('/api/v1/migration/'):
                try:
                    with self.migration_gate().operation(expected_context=expected_context):
                        await self.app(scope, replay, response)
                except SyncError as exc:
                    await error_response(identifier, exc.code, MESSAGES[exc.code], exc.status, retryable=exc.retryable)(scope, receive, send)
            else:
                await self.app(scope, replay, response)
        except Exception:
            if started:
                raise RuntimeError(f'Response failed; request_id={identifier}') from None
            await error_response(identifier, 'internal_error', 'Internal server error', 500)(scope, receive, send)
