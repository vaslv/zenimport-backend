from datetime import date
from decimal import Decimal
from uuid import uuid4
import pytest

from backend import discrepancies, storage
from delivery_factory import DeliveryHarness
from mirror_factory import movement

DAY = '2026-10-15'


@pytest.fixture
def h(tmp_path):
    value = DeliveryHarness(tmp_path/'report.sqlite3')
    assert value.configure(value.account_id, version=1, start=DAY).status_code == 200
    yield value
    value.close()


def extra(h, identifier='extra', **changes):
    from capture_factory import TARGET
    tx = movement(identifier, **({'incomeAccount':TARGET, 'outcomeAccount':TARGET, 'date':DAY} | changes))
    h.remote.transactions[identifier] = tx
    h.app.state.mirror.refresh(force=True)
    return tx


def report(h, **params):
    return h.client.get('/api/v1/connections/'+h.connection['id']+'/discrepancies', params=params)


def test_delivered_identity_is_not_extra_but_manual_duplicate_is(h):
    h.capture();h.worker.run_once()
    extra(h, comment='<script>untrusted</script>')
    result = report(h).json()
    assert result['accounts'][0]['checked_days'] == 1
    assert len(result['days']) == 1
    day = result['days'][0]
    assert day['date'] == DAY and day['difference'] == '-100'
    assert [op['id'] for op in day['operations']] == ['extra']
    assert day['operations'][0]['comment'] == '<script>untrusted</script>'
    assert len(h.remote.writes) == 1


def test_no_bank_coverage_never_labels_zenmoney_entries_extra(h):
    extra(h)
    result=report(h).json()
    assert result['days'] == [] and result['accounts'][0]['unchecked_days'] == 1


def test_fully_captured_empty_day_detects_extras_even_when_they_cancel_out(h):
    h.capture(0)
    extra(h, 'expense');extra(h, 'income', direction='income')
    result=report(h).json()
    assert len(result['days']) == 1 and len(result['days'][0]['operations']) == 2
    assert result['days'][0]['difference'] == '0'


def test_mobile_split_without_original_id_is_not_extra(h):
    h.capture();h.worker.run_once()
    identifier=next(iter(h.remote.transactions))
    del h.remote.transactions[identifier]
    h.remote.deletions.append({'object':'transaction','id':identifier})
    extra(h, 'split-a', amount=40);extra(h, 'split-b', amount=60)
    assert report(h).json()['days'] == []


@pytest.mark.parametrize('kind', ['transfer','debt'])
def test_transfer_or_debt_uses_only_bound_account_leg(h, kind):
    h.capture();h.worker.run_once()
    identifier=next(iter(h.remote.transactions))
    row=h.remote.transactions[identifier]
    row.update(incomeAccount='debt-account' if kind == 'debt' else 'other-account', income=100)
    h.app.state.mirror.refresh(force=True)
    assert report(h).json()['days'] == []


def test_unknown_other_accounts_other_days_deleted_and_hold_are_not_extra(h):
    h.capture(0)
    extra(h,'other',incomeAccount='elsewhere',outcomeAccount='elsewhere')
    extra(h,'old',date='2026-09-01');extra(h,'held',hold=True)
    extra(h,'deleted',deleted=True)
    assert report(h).json()['days'] == []


def test_report_works_before_delivery_and_rejects_foreign_account_and_invalid_period(h):
    h.capture()
    extra(h)
    result=report(h).json()
    assert result['days'] == [] and result['accounts'][0]['status'] == 'ready'
    assert report(h,account_id=str(uuid4())).status_code == 404
    assert report(h,from_date='2026-10-16',to_date=DAY).status_code == 422
    assert report(h,from_date='not-a-date').status_code == 422
    assert h.client.get('/api/v1/connections/'+h.connection['id']+'/discrepancies',headers={'Authorization':'Bearer wrong'}).status_code == 401


def test_shared_zenmoney_target_is_not_attributed_to_one_bank_account(h):
    h.capture(0);extra(h)
    connection=h.discover(names=('account-1','account-2'))
    second=next(a for a in connection['accounts'] if a['id'] != h.account_id)
    assert h.configure(second['id'],start=DAY).status_code == 200
    assert all(a['status']=='shared_target' for a in report(h).json()['accounts'])
    assert report(h).json()['days'] == []


def test_report_does_not_change_financial_journal_and_never_sends_a_write(h):
    h.capture();h.worker.run_once();extra(h)
    def snapshot():
        with storage.transaction(h.path,read_only=True) as db:
            return {t:[tuple(row) for row in db.execute('SELECT * FROM '+t+' ORDER BY 1')] for t in
                ('bank_transactions','deliveries','delivery_attempts','processing_receipts','link_groups')}
    before=snapshot();writes=len(h.remote.writes)
    assert report(h,refresh='true').status_code == 200
    assert snapshot() == before and len(h.remote.writes) == writes


def test_exact_amounts_split_and_net_settlement_are_read_only_matches():
    dec=lambda values:[Decimal(v) for v in values]
    assert discrepancies.consume_matches(dec(['-100']),{'a':Decimal('-40'),'b':Decimal('-60')}) == set()
    assert discrepancies.unmatched_sides(dec(['-961440','1000000']),{'a':Decimal('38560')}) == ({0,1},{'a'})
    assert discrepancies.consume_matches([],{'a':Decimal('-100'),'b':Decimal('100')}) == {'a','b'}
    assert len(discrepancies.consume_matches(dec(['-100']),{'a':Decimal('-100'),'b':Decimal('-100')})) == 1


def test_api_refresh_failure_never_claims_no_differences(h):
    h.capture(0);extra(h)
    h.remote.read_failure=503
    response=report(h,refresh='true')
    assert response.status_code == 503 and response.json()['error']['code']=='zenmoney_unavailable'


def test_combinations_are_bounded_and_never_commit_links():
    banks=[Decimal('-3')]
    zens={str(i):Decimal('-1') for i in range(17)}
    assert discrepancies.consume_matches(banks,zens) == set(zens)


@pytest.mark.parametrize('state', ['pending', 'prepared', 'uncertain'])
def test_delivery_state_does_not_hide_missing_bank_operations(h, state):
    h.capture()
    with storage.transaction(h.path) as db:
        db.execute("UPDATE bank_processing SET state=?", (state,))
    h.app.state.mirror.refresh(force=True)
    result = report(h).json()
    assert result['accounts'][0]['status'] == 'ready'
    day = result['days'][0]
    assert len(day['bank_operations']) == 1
    assert day['bank_operations'][0]['amount'] == '-100'
    assert day['operations'] == []
    assert not h.remote.writes


def test_mobile_amount_edit_shows_both_sides_despite_persisted_receipt(h):
    h.capture(); h.worker.run_once()
    identifier = next(iter(h.remote.transactions))
    h.remote.transactions[identifier]['outcome'] = 125
    h.app.state.mirror.refresh(force=True)
    day = report(h).json()['days'][0]
    assert [op['amount'] for op in day['bank_operations']] == ['-100']
    assert [op['amount'] for op in day['operations']] == ['-125']
    h.remote.transactions[identifier]['outcome'] = 100
    assert report(h, refresh='true').json()['days'] == []
    assert len(h.remote.writes) == 1


def test_deleted_remote_operation_is_visible_on_bank_side(h):
    h.capture(); h.worker.run_once()
    identifier = next(iter(h.remote.transactions))
    del h.remote.transactions[identifier]
    h.remote.deletions.append({'object': 'transaction', 'id': identifier})
    day = report(h, refresh='true').json()['days'][0]
    assert len(day['bank_operations']) == 1 and day['operations'] == []
    assert len(h.remote.writes) == 1
