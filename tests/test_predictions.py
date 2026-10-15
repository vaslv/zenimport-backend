from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import pytest

from backend import predictions, records, storage
from backend.sync_contracts import BankRecord
from mirror_factory import Fixture, movement


@pytest.fixture
def f(tmp_path):
    value = Fixture(tmp_path / 'predictions.sqlite3')
    yield value
    value.close()


@pytest.mark.parametrize('count,expected', [(2, None), (3, 'category-a'), (5, 'category-a')])
def test_three_independent_consistent_payments(f, count, expected):
    f.history(count)
    pending = f.bank()
    f.refresh()
    assert f.predict(pending)['category_id'] == expected
    with storage.transaction(f.path, read_only=True) as db:
        assert db.execute('SELECT category_id FROM category_predictions WHERE bank_id=?', (pending,)).fetchone()[0] == expected


def test_repeat_fetch_does_not_multiply_and_mobile_category_change_is_authoritative(f):
    _, remote = f.history()
    pending = f.bank()
    f.refresh()
    f.refresh()
    assert f.predict(pending)['candidates'][0]['count'] == 3
    changed = deepcopy(remote[0]) | {'tag': ['category-b']}
    f.refresh({'serverTimestamp': 101, 'transaction': [changed]})
    prediction = f.predict(pending)
    assert prediction['category_id'] is None and prediction['reason'] == 'conflicting_history'
    assert [c['count'] for c in prediction['candidates']] == [2, 1]


@pytest.mark.parametrize('change', ['delete', 'transfer', 'debt', 'unreviewed', 'hold', 'amount', 'opposite_direction'])
def test_changed_deleted_or_incompatible_payment_stops_counting(f, change):
    _, remote = f.history()
    pending = f.bank()
    f.refresh()
    tx = deepcopy(remote[0])
    diff = {'serverTimestamp': 101, 'transaction': [tx]}
    if change == 'delete':
        diff = {'serverTimestamp': 101, 'deletion': [{'object': 'transaction', 'id': tx['id']}]}
    elif change == 'transfer':
        tx['incomeAccount'] = 'other-account'
        tx['income'] = 100
    elif change == 'debt':
        diff['account'] = [{'id': 'zen-vnd', 'type': 'debt'}]
    elif change == 'unreviewed':
        tx['viewed'] = False
    elif change == 'hold':
        tx['hold'] = True
    elif change == 'amount':
        tx['outcome'] = 101
    else:
        tx['income'], tx['outcome'] = 100, 0
    f.refresh(diff)
    assert f.predict(pending)['category_id'] is None


def test_unreviewed_own_predictions_only_train_after_mobile_review(f):
    _, remote = f.history(3, receipt=True, viewed=False)
    pending = f.bank()
    f.refresh()
    assert f.predict(pending)['candidates'] == []
    f.refresh({'serverTimestamp': 101, 'transaction': [dict(tx, viewed=True) for tx in remote]})
    assert f.predict(pending)['category_id'] == 'category-a'


def test_split_counts_as_one_and_mixed_split_blocks_confidence(f):
    f.history(2)
    split_bank = f.bank()
    zens = [movement('split-1', amount=40), movement('split-2', amount=60)]
    with storage.transaction(f.path) as db:
        group = str(uuid4())
        db.execute('INSERT INTO link_groups VALUES (?,?,?,?,?)', (group, 'split', 'bank-vnd', f.now, 'manual'))
        db.execute('INSERT INTO link_bank_members VALUES (?,?)', (group, split_bank))
        db.executemany('INSERT INTO link_zen_members VALUES (?,?)', ((group, tx['id']) for tx in zens))
        db.execute("UPDATE bank_processing SET state='delivered' WHERE bank_id=?", (split_bank,))
    f.data['transaction'].extend(zens)
    pending = f.bank()
    f.refresh()
    assert f.predict(pending)['candidates'][0]['count'] == 3
    f.refresh({'serverTimestamp': 101, 'transaction': [dict(zens[1], tag=['category-b'])]})
    assert f.predict(pending)['category_id'] is None
    assert f.predict(pending)['reason'] == 'conflicting_history'


def test_one_remote_uuid_cannot_be_three_independent_payments(f):
    for _ in range(3):
        bank = f.bank()
        with storage.transaction(f.path) as db:
            db.execute("UPDATE bank_processing SET state='delivered' WHERE bank_id=?", (bank,))
            db.execute('INSERT INTO processing_receipts VALUES (?,?,?,?)', (bank, 'shared-zen', 'fixture', f.now))
    f.data['transaction'] = [movement('shared-zen')]
    pending = f.bank()
    f.refresh()
    assert f.predict(pending)['category_id'] is None


@pytest.mark.parametrize('changes', [
    {'beneficiary_account': 'different-account'}, {'direction': 'income'},
    {'account': 'other-bank-account'}, {'beneficiary_account': None, 'beneficiary_name': None},
])
def test_history_does_not_cross_account_direction_or_known_recipient(f, changes):
    f.history()
    with storage.transaction(f.path) as db:
        f.account(db, 'other-bank-account')
    pending = f.bank(**changes)
    f.refresh()
    assert f.predict(pending)['category_id'] is None


def test_description_fallback_is_exact_after_normalization(f):
    f.history(3, beneficiary_account=None, beneficiary_name=None, description='  OPENAI *CHATGPT  ')
    pending = f.bank(beneficiary_account=None, beneficiary_name=None, description='openai *chatgpt')
    other = f.bank(beneficiary_account=None, beneficiary_name=None, description='fee OPENAI *CHATGPT')
    f.refresh()
    assert f.predict(pending)['category_id'] == 'category-a'
    assert f.predict(other)['category_id'] is None


def test_manual_then_explicit_name_rule_then_history_and_never_correction(f):
    f.history()
    pending = f.bank(beneficiary_account='new-qr-shop-code')
    with storage.transaction(f.path) as db:
        db.execute('INSERT INTO recipient_rules VALUES (?,?,?,?,?)', ('vietinbank', 'fixture shop', 'expense', 'category-b', f.now))
        db.execute('INSERT INTO category_choices VALUES (?,?,?,?)', (pending, 'category-a', 'manual', f.now))
    f.refresh()
    assert f.predict(pending)['source'] == 'manual'
    f.refresh({'serverTimestamp': 101, 'tag': [{'id': 'category-a', 'deleted': True}]})
    assert f.predict(pending)['category_id'] == 'category-b' and f.predict(pending)['source'] == 'rule'
    unknown = f.bank(beneficiary_name='Unknown shop')
    assert f.predict(unknown)['category_id'] is None
    with storage.transaction(f.path, read_only=True) as db:
        assert db.execute('SELECT category_id FROM category_choices WHERE bank_id=?', (pending,)).fetchone()[0] == 'category-a'


def test_income_categories_and_visibility_are_direction_specific(f):
    f.history(3, direction='income')
    pending = f.bank(direction='income')
    f.refresh()
    assert f.predict(pending)['category_id'] == 'category-a'
    f.refresh({'serverTimestamp': 101, 'tag': [{'id': 'category-a', 'showIncome': False}]})
    assert f.predict(pending)['category_id'] is None


def test_no_self_training_and_revision_can_be_selected_explicitly(f):
    banks, _ = f.history()
    pending = f.bank()
    f.refresh()
    assert f.predict(banks[0])['category_id'] is None
    with storage.transaction(f.path) as db:
        row = db.execute('SELECT raw_json FROM bank_transactions WHERE id=?', (pending,)).fetchone()
        record = BankRecord.model_validate_json(row[0]).model_copy(update={'beneficiary_account': 'new-merchant'})
        records.save_record(db, 'bank-vnd', record, parser_version='fixture-2', timezone='Asia/Ho_Chi_Minh')
    assert f.predict(pending)['category_id'] is None
    assert f.predict(pending, revision=1)['category_id'] == 'category-a'


def test_started_payload_is_immutable_but_unattempted_marks_rebuild(f):
    f.history()
    attempted, prepared = f.bank(), f.bank()
    with storage.transaction(f.path) as db:
        for bank, state, flag in [(attempted, 'uncertain', 1), (prepared, 'prepared', 0)]:
            db.execute('UPDATE bank_processing SET state=? WHERE bank_id=?', (state, bank))
            db.execute('INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,?,?)', (bank, 'zen-' + bank, '{ "tag": ["old"] }', state, flag, 'fixture', None, 0, 0))
    f.refresh()
    with storage.transaction(f.path, read_only=True) as db:
        assert db.execute('SELECT payload,needs_rebuild FROM deliveries WHERE bank_id=?', (attempted,)).fetchone()[:] == ('{ "tag": ["old"] }', 0)
        assert db.execute('SELECT needs_rebuild FROM deliveries WHERE bank_id=?', (prepared,)).fetchone()[0] == 1
        assert not db.execute('SELECT 1 FROM category_predictions WHERE bank_id=?', (attempted,)).fetchone()


def test_stale_mirror_never_produces_automatic_category(f):
    f.history()
    pending = f.bank()
    f.refresh()
    f.now += 301
    assert f.predict(pending)['category_id'] is None
    assert f.predict(pending)['reason'] == 'zenmoney_unavailable'


@pytest.mark.parametrize('excluded', ['bank', 'zen', 'settlement', 'multiple_banks'])
def test_excluded_or_settled_payments_do_not_train(f, excluded):
    banks, remote = f.history()
    pending = f.bank()
    with storage.transaction(f.path) as db:
        group = db.execute('SELECT group_id FROM link_bank_members WHERE bank_id=?', (banks[0],)).fetchone()[0]
        if excluded == 'bank':
            db.execute('INSERT INTO bank_exclusions VALUES (?,?,?)', (banks[0], 'manual', f.now))
        elif excluded == 'zen':
            db.execute('INSERT INTO zen_exclusions VALUES (?,?)', (remote[0]['id'], f.now))
        elif excluded == 'settlement':
            db.execute("UPDATE link_groups SET kind='settlement' WHERE id=?", (group,))
        else:
            db.execute('INSERT INTO link_bank_members VALUES (?,?)', (group, pending))
    f.refresh()
    assert f.predict(pending)['category_id'] is None


def test_name_without_beneficiary_account_can_train_but_never_cross_known_accounts(f):
    f.history(3, beneficiary_account=None, beneficiary_name='  Known   SHOP ')
    same = f.bank(beneficiary_account=None, beneficiary_name='known shop', description='Different order')
    other = f.bank(beneficiary_account='new-account', beneficiary_name='known shop')
    f.refresh()
    assert f.predict(same)['category_id'] == 'category-a'
    assert f.predict(other)['category_id'] is None


def test_legacy_immutable_revision_uses_exact_minor_units(f):
    pending = f.bank(amount='100')
    with storage.transaction(f.path) as db:
        db.execute('INSERT INTO bank_versions VALUES (?,?,?,?,?,?)', (pending, 2,
            '{"date":"2026-09-30","amount_minor":-123456789012345678901,"description":"Legacy"}',
            '{"details":{"corresponsiveAccount":"legacy-account","corresponsiveName":"Legacy shop"}}',
            f.now, 'legacy-fixture'))
        row = predictions.bank_record(db, pending, revision=2)
        assert (row['amount'], row['direction'], row['beneficiary_account']) == ('123456789012345678901', 'expense', 'legacy-account')
