"""Exact JSON at database, intent and HTTP boundaries; money never uses float."""
from decimal import Decimal
import simplejson
from functools import wraps
from fastapi.responses import JSONResponse
from zenmoney_api.protocol import encode_json, decode_json


def loads(value):
    return decode_json(value.encode('utf-8') if isinstance(value, str) else value)


def dumps(value, *, sort_keys=False, separators=None, ensure_ascii=False):
    encode_json(value)
    return simplejson.dumps(value, use_decimal=True, allow_nan=False,
                            ensure_ascii=ensure_ascii, sort_keys=sort_keys, separators=separators)


def canonical(value):
    """Same numerical value has the same fingerprint despite trailing zeros."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('Non-finite decimal')
        sign, digits, exponent = value.as_tuple()
        digits = list(digits)
        if not any(digits):
            return 0
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        if exponent >= 0:
            return int(value)
        return Decimal((sign, digits, exponent))
    if type(value) is float:
        return canonical(Decimal(str(value)))
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    return value


class ExactJSONResponse(JSONResponse):
    def render(self, content):
        return encode_json(content)


def exact_response(endpoint):
    """Return Response directly, before FastAPI can coerce Decimal to float."""
    @wraps(endpoint)
    def wrapped(*args, **kwargs):
        return ExactJSONResponse(endpoint(*args, **kwargs))
    return wrapped
