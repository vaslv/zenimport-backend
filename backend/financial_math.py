"""Bounded exact Decimal sums, independent of the caller's ambient precision."""
from contextlib import contextmanager
from decimal import Decimal, Inexact, localcontext

MAX_FINANCIAL_SPAN = 8192


@contextmanager
def exact_context(values):
    values = tuple(values)
    if any(type(value) is not int and not isinstance(value, Decimal) for value in values):
        raise ValueError('Expected exact financial values')
    terms = [Decimal(value) for value in values]
    if any(not value.is_finite() for value in terms):
        raise ValueError('Expected finite financial values')
    nonzero = [value for value in terms if value]
    if nonzero:
        low = min(value.as_tuple().exponent for value in nonzero)
        high = max(value.adjusted() for value in nonzero)
        precision = max(80, high - low + len(str(len(nonzero))) + 2)
        if low < -MAX_FINANCIAL_SPAN or high > MAX_FINANCIAL_SPAN or precision > MAX_FINANCIAL_SPAN:
            raise ValueError('Unsupported financial decimal range')
    else:
        precision = 80
    with localcontext() as context:
        context.prec = precision
        context.traps[Inexact] = True
        yield context


def exact_sum(values):
    values = tuple(values)
    with exact_context(values):
        return sum(values, Decimal(0))
