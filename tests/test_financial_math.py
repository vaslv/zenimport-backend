from decimal import Decimal, localcontext

import pytest

from backend import discrepancies, existing
from backend.financial_math import exact_sum

AMOUNT = Decimal('100.' + '0'*90 + '1')
EPSILON = Decimal('0.' + '0'*90 + '1')


def test_signed_movements_never_round_into_a_false_bank_pair():
    tx = {'income': 0, 'outcome': AMOUNT, 'incomeAccount': 'a', 'outcomeAccount': 'a',
          'incomeInstrument': 'vnd', 'outcomeInstrument': 'vnd'}
    with localcontext() as context:
        context.prec = 2
        value = existing.movement(tx, 'a', 'vnd')
        assert value == AMOUNT.copy_negate() and value != Decimal('-100')
        assert discrepancies.movement(tx, 'a') == value
        assert discrepancies.unmatched_sides([Decimal('-100')], {'tx': value}) == ({0}, {'tx'})
        tx.update(income=AMOUNT, outcome=100)
        assert existing.movement(tx, 'a', 'vnd') == EPSILON
        assert discrepancies.movement(tx, 'a') == EPSILON
        large = Decimal('-12345678901234567890123.' + '0'*90 + '1')
        assert discrepancies.unmatched_sides([Decimal('-999999999999999999999999')], {'tx': large}) == ({0}, {'tx'})


def test_unique_subset_does_not_discard_a_high_precision_difference():
    with localcontext() as context:
        context.prec = 2
        items = [('a', Decimal('-40.' + '0'*90 + '1')), ('b', Decimal('-60'))]
        assert existing.unique_subset(items, Decimal('-100'), [20000]) is None


def test_exact_sums_keep_cancellation_and_fail_explicitly_outside_resource_bounds():
    with localcontext() as context:
        context.prec = 2
        assert exact_sum([AMOUNT, Decimal('-100')]) == EPSILON
        assert exact_sum([AMOUNT, AMOUNT.copy_negate(), EPSILON]) == EPSILON
        with pytest.raises(ValueError, match='Unsupported financial decimal range'):
            exact_sum([Decimal('1e9000'), Decimal('1e-9000')])
        with pytest.raises(ValueError, match='Expected finite financial values'):
            exact_sum([Decimal('NaN')])
        with pytest.raises(ValueError, match='Expected exact financial values'):
            exact_sum([0.1])
