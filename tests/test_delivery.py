from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from datetime import datetime
from threading import Event
from uuid import uuid4

import pytest
from zenmoney_api.protocol import decode_json

from backend import delivery, storage
from capture_factory import TARGET, record
from delivery_factory import DeliveryHarness


@pytest.fixture
def h(tmp_path):
    value = DeliveryHarness(tmp_path / 'delivery.sqlite3')
    yield value
    value.close()


@pytest.mark.parametrize('direction', ['expense', 'income'])
def test_exact_unread_creation_then_repeated_commands_never_write_again(h, direction):
    job, _, _, _ = h.capture(changes={'direction': direction, 'amount': '90071992547409931234567'})
    h.worker.run_once()
    assert h.states() == ['delivered']
    sent = h.remote.writes[0][0]
    assert sent['income' if direction == 'income' else 'outcome'] == Decimal('90071992547409931234567')
    assert sent['viewed'] is False and sent['tag'] is None
    assert sent['incomeAccount'] == sent['outcomeAccount'] == TARGET
    assert sent['date'] == '2026-09-15' and sent['comment'] == 'Fixture purchase'
    h.start(h.connection['id'])
    h.due()
    assert len(h.remote.writes) == 1
    assert h.job(job)['delivery'] == {'pending': 0, 'delivered': 1, 'suppressed': 0, 'uncertain': 0}


def test_partial_interval_never_authorizes_delivery(h):
    job, interval, auth, rows = h.capture(complete=False)
    h.worker.run_once()
    assert h.remote.writes == []
    assert h.complete(job, interval, auth, records=len(rows)).status_code == 200
    h.worker.run_once()
    assert h.states() == ['delivered']


def test_known_manual_category_is_preserved_and_unknown_stays_null(h):
    h.capture(count=2)
    bank = h.bank_ids()['fixture-0']
    with storage.transaction(h.path) as db:
        db.execute('INSERT INTO category_choices VALUES (?,?,?,?)', (bank, 'category-a', 'manual', h.now))
    h.worker.run_once()
    assert sorted([tx['tag'] for tx in h.remote.writes[0]], key=str) == [None, ['category-a']]
    assert all(tx['viewed'] is False for tx in h.remote.writes[0])


@pytest.mark.parametrize('mode', ['timeout_after', 'edited', 'deleted', 'no_echo', 'malformed'])
def test_read_verification_handles_applied_write_lost_response_and_mobile_changes(h, mode):
    job, _, _, _ = h.capture(2)
    h.remote.mode = mode
    h.worker.run_once()
    assert h.states() == (['suppressed', 'suppressed'] if mode == 'deleted' else ['delivered', 'delivered'])
    h.due()
    assert len(h.remote.writes) == 1
    if mode == 'edited':
        with storage.transaction(h.path, read_only=True) as db:
            assert all(decode_json(row[0].encode())['tag'] is None for row in db.execute('SELECT payload FROM deliveries'))
            assert all(decode_json(row[0].encode())['tag'] == ['category-b'] for row in db.execute("SELECT data FROM zen_entities WHERE kind='transaction'"))
    assert h.job(job)['delivery']['uncertain'] == 0


@pytest.mark.parametrize('mode', ['timeout_before', 'reject'])
def test_nonapplication_cannot_be_inferred_from_timeout_or_http_error(h, mode):
    job, _, _, _ = h.capture()
    h.remote.mode = mode
    h.worker.run_once()
    assert h.states() == ['uncertain']
    assert h.job(job)['delivery_state'] == 'blocked'
    assert any(e['code'] == 'delivery_uncertain' for e in h.job(job)['errors'])
    for _ in range(3):
        h.due()
    assert len(h.remote.writes) == 1
    assert all('fixture-private' not in str(e) for e in h.job(job)['errors'])
    assert any(read.get('forceFetch') == ['transaction'] for read in h.remote.reads)


def test_partial_batch_resolves_each_uuid_without_resending_remainder(h):
    job, _, _, _ = h.capture(3)
    h.remote.mode = 'partial'
    h.worker.run_once()
    assert sorted(h.states()) == ['delivered', 'uncertain', 'uncertain']
    assert h.job(job)['delivery']['delivered'] == 1
    h.due()
    assert len(h.remote.writes) == 1
    with storage.transaction(h.path, read_only=True) as db:
        ids = {r[0] for r in db.execute('SELECT zen_id FROM deliveries')}
        assert len(ids) == 3
        assert {r[0] for r in db.execute('SELECT zen_id FROM delivery_attempts')} == ids


def test_crash_after_intent_before_network_never_blindly_resends(h):
    h.capture()
    h.app.state.mirror.refresh(force=True)
    with storage.transaction(h.path) as db:
        rows, _, _ = delivery.intent(db, h.app.state.mirror, h.now)
    assert rows and h.remote.writes == []
    restarted = delivery.DeliveryWorker(h.path, h.app.state.mirror, lambda: h.now)
    restarted.run_once()
    assert h.states() == ['uncertain'] and h.remote.writes == []
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT zen_id FROM deliveries').fetchone()[0] == rows[0]['zen_id']


def test_crash_after_remote_commit_is_recovered_by_reading_old_uuid(h):
    h.capture()
    h.remote.mode = 'crash_after'
    with pytest.raises(SystemExit):
        h.worker.run_once()
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT state FROM deliveries').fetchone()[0] == 'attempting'
    restarted = delivery.DeliveryWorker(h.path, h.app.state.mirror, lambda: h.now)
    restarted.run_once()
    assert h.states() == ['delivered']
    assert len(h.remote.writes) == 1


def test_successful_write_with_failed_verification_stays_uncertain_until_read_recovers(h):
    h.capture()
    h.remote.mode = 'timeout_after'
    h.remote.before_write = lambda: setattr(h.remote, 'read_failure', 503)
    h.worker.run_once()
    assert h.states() == ['uncertain']
    h.remote.read_failure = None
    h.due()
    assert h.states() == ['delivered'] and len(h.remote.writes) == 1


@pytest.mark.parametrize('change', ['archive', 'delete', 'currency', 'debt'])
def test_invalid_target_blocks_without_attempt_then_can_recover(h, change):
    job, _, _, _ = h.capture()
    original = deepcopy(h.remote.data['account'][0])
    h.remote.data['account'][0].update({'archive': True} if change == 'archive' else {'deleted': True} if change == 'delete' else {'instrument': 3} if change == 'currency' else {'type': 'debt'})
    h.worker.run_once()
    assert h.remote.writes == []
    assert h.job(job)['delivery_state'] == 'blocked'
    assert any(e['code'] == 'zenmoney_account_unavailable' for e in h.job(job)['errors'])
    h.remote.data['account'][0] = original
    h.due()
    # A deletion is final, even if the API later returns a restored object.
    if change == 'delete':
        assert h.remote.writes == []
    else:
        assert h.states() == ['delivered']


def test_disable_between_refresh_and_intent_prevents_request(h):
    job, _, _, _ = h.capture()
    h.remote.before_read = lambda: h.configure(h.account_id, version=1, selection='ignored')
    h.worker.run_once()
    assert h.remote.writes == []
    assert h.job(job)['delivery_state'] == 'blocked'


def test_disable_after_intent_allows_only_already_started_request_to_finish(h):
    h.capture(21)
    h.remote.before_write = lambda: h.configure(h.account_id, version=1, selection='ignored')
    h.worker.run_once()
    h.due()
    assert len(h.remote.writes) == 1 and len(h.remote.writes[0]) == delivery.BATCH_SIZE
    assert sorted(h.states()).count('delivered') == 20


def test_target_change_requires_new_click_and_preserves_prepared_uuid(h):
    h.capture()
    bank = next(iter(h.bank_ids().values()))
    identifier = str(uuid4())
    with storage.transaction(h.path) as db:
        db.execute("UPDATE bank_processing SET state='prepared' WHERE bank_id=?", (bank,))
        db.execute("INSERT INTO deliveries VALUES (?,?,?,'prepared',0,'fixture',NULL,0,1)", (bank, identifier, '{}'))
    assert h.configure(h.account_id, version=1, target='fixture-other-vnd').status_code == 200
    h.worker.run_once()
    assert h.remote.writes == []
    h.start(h.connection['id'])
    h.worker.run_once()
    assert h.remote.writes[0][0]['id'] == identifier
    assert h.remote.writes[0][0]['outcomeAccount'] == 'fixture-other-vnd'


def test_newer_unfinished_revision_cannot_replace_complete_revision(h):
    job, _, _, _ = h.capture()
    assert h.client.post('/api/v1/jobs/' + job['id'] + '/stop', json={}).status_code == 200
    newer, _, _, _ = h.capture(complete=False, changes={'amount': '999'})
    h.worker.run_once()
    assert h.remote.writes[0][0]['outcome'] == 100
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT revision FROM bank_transactions').fetchone()[0] == 2
        assert db.execute('SELECT revision FROM delivery_authorizations').fetchone()[0] == 1
    assert h.job(newer)['delivery']['delivered'] == 1


def test_account_day_amount_existing_operation_is_adopted_without_write(h):
    h.capture()
    h.remote.transactions['unrelated-uuid'] = {'id': 'unrelated-uuid', 'date': '2026-09-15', 'income': 0, 'outcome': 100,
        'incomeAccount': TARGET, 'outcomeAccount': TARGET, 'incomeInstrument': 2, 'outcomeInstrument': 2, 'viewed': True, 'tag': None}
    h.worker.run_once()
    assert len(h.remote.transactions) == 1
    assert h.remote.writes == []
    assert h.states() == ['delivered']


def test_one_writer_lock_prevents_concurrent_dispatch(h):
    h.capture()
    entered, release = Event(), Event()
    def wait():
        entered.set()
        assert release.wait(5)
    h.remote.before_write = wait
    other = delivery.DeliveryWorker(h.path, h.app.state.mirror, lambda: h.now)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(h.worker.run_once)
        assert entered.wait(5)
        second = pool.submit(other.run_once)
        try:
            second.result(timeout=2)
            assert len(h.remote.writes) == 1
        finally:
            release.set()
        first.result(timeout=5)
    assert h.states() == ['delivered']


def test_read_backoff_survives_worker_restart(h):
    h.capture()
    h.remote.read_failure = 503
    h.worker.run_once()
    count = len(h.remote.reads)
    delivery.DeliveryWorker(h.path, h.app.state.mirror, lambda: h.now).run_once()
    assert len(h.remote.reads) == count and h.remote.writes == []
    h.remote.read_failure = None
    h.due()
    assert h.states() == ['delivered']


def test_reviewed_deliveries_train_future_category_but_unread_predictions_do_not(h):
    first, _, _, _ = h.capture(3)
    h.worker.run_once()
    assert all(tx['tag'] is None for tx in h.remote.writes[0])
    for tx in h.remote.transactions.values():
        tx.update(viewed=True, tag=['category-a'])
    h.client.post('/api/v1/jobs/' + first['id'] + '/stop', json={})
    h.capture(4)
    h.worker.run_once()
    assert len(h.remote.writes) == 2 and len(h.remote.writes[1]) == 1
    assert h.remote.writes[1][0]['tag'] == ['category-a']
    assert h.remote.writes[1][0]['viewed'] is False


def test_explicit_exclusions_and_old_processing_receipts_never_create_new_uuid(h):
    h.capture(3)
    ids = list(h.bank_ids().values())
    with storage.transaction(h.path) as db:
        db.execute('INSERT INTO bank_exclusions VALUES (?,?,?)', (ids[0], 'manual', h.now))
        db.execute("UPDATE bank_processing SET state='delivered' WHERE bank_id=?", (ids[1],))
        db.execute('INSERT INTO processing_receipts VALUES (?,?,?,?)', (ids[1], 'previously-deleted-unknown', 'old_confirmation', h.now))
        db.execute("UPDATE bank_processing SET state='suppressed' WHERE bank_id=?", (ids[2],))
    h.worker.run_once()
    assert h.remote.writes == []
    assert sorted(h.states()) == ['delivered', 'suppressed', 'suppressed']


def test_completed_capture_and_attempted_authorization_are_database_constraints(h):
    import sqlite3
    h.capture()
    h.remote.mode = 'reject'
    h.worker.run_once()
    with storage.transaction(h.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('UPDATE delivery_authorizations SET revision=revision')
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE deliveries SET payload='{}'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('DELETE FROM delivery_attempts')


@pytest.mark.parametrize('when', ['before', 'after'])
def test_real_process_exit_preserves_intent_and_recovery_never_replays(h, tmp_path, when):
    from multiprocessing import get_context
    from delivery_process import crash_writer
    h.capture()
    remote_file = tmp_path / 'fixture-remote-result.json'
    process = get_context('spawn').Process(target=crash_writer, args=(h.path, remote_file, when))
    process.start()
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail('Disposable writer did not exit')
    assert process.exitcode == (22 if when == 'before' else 23)
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT state FROM deliveries').fetchone()[0] == 'attempting'
        identifier = db.execute('SELECT zen_id FROM deliveries').fetchone()[0]
    if when == 'after':
        h.remote.transactions = {tx['id']: tx for tx in decode_json(remote_file.read_bytes())}
    h.worker.run_once()
    assert h.remote.writes == []
    assert h.states() == (['uncertain'] if when == 'before' else ['delivered'])
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT zen_id FROM deliveries').fetchone()[0] == identifier


def test_runtime_worker_finishes_without_further_browser_requests(tmp_path):
    from time import monotonic, sleep
    value = DeliveryHarness(tmp_path / 'runtime.sqlite3', start_worker=True)
    try:
        value.capture(complete=False)
        assert value.remote.writes == []
        # Completing the interval is the last browser request. The server's
        # lifespan-owned worker handles the durable queue by itself.
        job = value.client.get('/api/v1/connections/' + value.connection['id']).json()
        assert job['accounts']
        with storage.transaction(value.path, read_only=True) as db:
            capture_job = dict(db.execute('SELECT * FROM capture_jobs').fetchone())
            interval = dict(db.execute('SELECT * FROM capture_intervals ORDER BY start_date LIMIT 1').fetchone())
        auth = {'worker_id': capture_job['worker_id'], 'lease_epoch': capture_job['lease_epoch']}
        result = value.complete({'id': capture_job['id']}, {'id': interval['id']}, auth, records=1)
        assert result.status_code == 200
        deadline = monotonic() + 6
        while value.states() != ['delivered'] and monotonic() < deadline:
            sleep(0.01)
        assert value.states() == ['delivered']
        assert len(value.remote.writes) == 1
    finally:
        value.close()
    assert not value.worker.thread.is_alive()


def test_stop_capture_keeps_delivery_of_already_complete_intervals(h):
    job, _, _, _ = h.capture()
    assert h.client.post('/api/v1/jobs/' + job['id'] + '/stop', json={}).status_code == 200
    h.worker.run_once()
    assert h.states() == ['delivered']
    assert h.job(job)['capture_state'] == 'cancelled'


def test_corrupted_prepared_data_is_rebuilt_before_first_attempt_with_same_uuid(h):
    h.capture()
    bank = next(iter(h.bank_ids().values()))
    identifier = str(uuid4())
    with storage.transaction(h.path) as db:
        db.execute("UPDATE bank_processing SET state='prepared' WHERE bank_id=?", (bank,))
        db.execute("INSERT INTO deliveries VALUES (?,?,?,'prepared',0,'legacy',NULL,0,1)",
            (bank, identifier, '{"id":"wrong","viewed":true,"outcome":1}'))
    h.worker.run_once()
    sent = h.remote.writes[0][0]
    assert sent['id'] == identifier and sent['viewed'] is False and sent['outcome'] == 100


def test_credential_change_cannot_reuse_old_authorization_without_new_click(h):
    from backend.mirror import Mirror
    h.capture()
    mirror = Mirror(h.path, h.sdk, 'fixture-new-token', lambda: h.now, sleep=lambda _: None)
    worker = delivery.DeliveryWorker(h.path, mirror, lambda: h.now)
    worker.run_once()
    assert h.remote.writes == []
    h.app.state.mirror = mirror
    h.start(h.connection['id'])
    worker.run_once()
    assert len(h.remote.writes) == 1


def test_delivered_then_deleted_operation_is_not_recreated_after_bank_reread(h):
    job, _, _, _ = h.capture()
    h.worker.run_once()
    identifier = h.remote.writes[0][0]['id']
    del h.remote.transactions[identifier]
    h.remote.deletions.append({'object': 'transaction', 'id': identifier})
    h.client.post('/api/v1/jobs/' + job['id'] + '/stop', json={})
    h.capture()
    h.worker.run_once()
    assert h.states() == ['suppressed'] and len(h.remote.writes) == 1


def test_database_failure_before_committing_intent_never_sends_and_can_resume(h, monkeypatch):
    h.capture()
    original = delivery.refresh_status
    def fail(db, now, job_id=None):
        original(db, now, job_id)
        if db.execute("SELECT 1 FROM deliveries WHERE state='attempting'").fetchone():
            raise RuntimeError('fixture-before-commit')
    monkeypatch.setattr(delivery, 'refresh_status', fail)
    with pytest.raises(RuntimeError):
        h.worker.run_once()
    assert h.remote.writes == [] and h.states() == ['pending']
    with storage.transaction(h.path, read_only=True) as db:
        assert not db.execute('SELECT 1 FROM delivery_attempts').fetchone()
    monkeypatch.setattr(delivery, 'refresh_status', original)
    h.worker.run_once()
    assert h.states() == ['delivered']


def test_empty_completed_job_has_complete_delivery_without_sdk_write(h):
    job, auth = h.leased(h.connection['id'])
    h.finish_empty(job, auth)
    h.worker.run_once()
    assert h.remote.writes == []
    result = h.job(job)
    assert result['capture_state'] == result['delivery_state'] == 'complete'


def test_multiple_batches_use_fresh_target_checks_each_time(h):
    h.capture(42)
    for _ in range(3):
        h.worker.run_once()
    assert [len(batch) for batch in h.remote.writes] == [20, 20, 2]
    assert len({tx['id'] for batch in h.remote.writes for tx in batch}) == 42
    assert h.states() == ['delivered'] * 42


def test_changed_current_date_does_not_remove_original_job_progress(h):
    from backend import records
    from backend.sync_contracts import BankRecord
    job, _, _, rows = h.capture()
    with storage.transaction(h.path) as db:
        changed = BankRecord.model_validate(rows[0] | {'occurred_at': '2026-10-20T10:12:25+07:00', 'local_date': '2026-10-20'})
        records.save_record(db, h.account_id, changed, parser_version='fixture-new', timezone='Asia/Ho_Chi_Minh')
    assert h.job(job)['delivery']['pending'] == 1
    h.worker.run_once()
    assert h.remote.writes[0][0]['date'] == '2026-09-15'
    assert h.remote.writes[0][0]['created'] == int(datetime.fromisoformat(rows[0]['occurred_at']).timestamp())
    assert h.job(job)['delivery']['delivered'] == 1


def test_one_unavailable_target_does_not_block_other_account(h):
    connection = h.discover(names=('account-1', 'account-2'))
    second = next(a for a in connection['accounts'] if a['source_key'] == 'account-2')
    assert h.configure(second['id'], target='fixture-other-vnd').status_code == 200
    job, auth = h.leased(connection['id'])
    for account in job['accounts']:
        interval = account['intervals'][0]
        rows = [record(interval, 'bank-' + account['account_id'], amount='100')]
        assert h.page(job, interval, auth, records=rows).status_code == 200
        assert h.complete(job, interval, auth, records=1).status_code == 200
    h.remote.data['account'][0]['archive'] = True
    h.worker.run_once()
    assert sorted(h.states()) == ['delivered', 'pending']
    assert h.remote.writes[0][0]['outcomeAccount'] == 'fixture-other-vnd'
    assert h.job(job)['delivery_state'] == 'blocked'
    h.remote.data['account'][0]['archive'] = False
    h.due()
    assert h.states() == ['delivered', 'delivered']


def test_existing_prepared_uuid_is_confirmed_without_overwriting_mobile_fields(h):
    h.capture()
    bank = next(iter(h.bank_ids().values()))
    identifier = str(uuid4())
    with storage.transaction(h.path) as db:
        db.execute("UPDATE bank_processing SET state='prepared' WHERE bank_id=?", (bank,))
        db.execute("INSERT INTO deliveries VALUES (?,?,?,'prepared',0,'legacy',NULL,0,1)", (bank, identifier, '{}'))
    h.remote.transactions[identifier] = {'id': identifier, 'income': 0, 'outcome': 200, 'tag': ['category-b'], 'viewed': True}
    h.worker.run_once()
    assert h.states() == ['delivered'] and h.remote.writes == []
    assert h.remote.transactions[identifier]['outcome'] == 200


def test_job_only_reports_delivery_complete_after_capture_and_delivery_both_finish(h):
    job, _, auth, _ = h.capture()
    h.worker.run_once()
    assert h.job(job)['delivery_state'] == 'waiting'
    for interval in job['accounts'][0]['intervals'][1:]:
        assert h.page(job, interval, auth).status_code == 200
        assert h.complete(job, interval, auth).status_code == 200
    assert h.job(job)['delivery_state'] == 'complete'


def test_target_archive_between_batches_leaves_remaining_unattempted(h):
    job, _, _, _ = h.capture(21)
    h.worker.run_once()
    h.remote.data['account'][0]['archive'] = True
    h.worker.run_once()
    assert len(h.remote.writes) == 1
    assert sorted(h.states()).count('pending') == 1
    assert h.job(job)['delivery_state'] == 'blocked'


def test_empty_history_without_completed_evidence_is_not_sent_on_click(h):
    from backend import records
    from backend.sync_contracts import BankRecord
    job, auth = h.leased(h.connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    with storage.transaction(h.path) as db:
        records.save_record(db, h.account_id, BankRecord.model_validate(record(interval)), parser_version='legacy-unproved', timezone='Asia/Ho_Chi_Minh')
    h.start(h.connection['id'])
    h.worker.run_once()
    assert h.states() == ['pending'] and h.remote.writes == []
    with storage.transaction(h.path, read_only=True) as db:
        assert not db.execute('SELECT 1 FROM delivery_authorizations').fetchone()


def test_worker_never_recreates_missing_runtime_database(h):
    import sqlite3
    h.path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        h.worker.run_once()
    assert not h.path.exists()


@pytest.mark.parametrize('occurred', ['2026-09-15T00:05:01+07:00', '2026-09-15T13:32:07+07:00'])
def test_creation_uses_bank_time_but_diff_change_uses_send_time(h, occurred):
    h.capture(changes={'occurred_at': occurred})
    h.worker.run_once()
    sent = h.remote.writes[0][0]
    assert sent['created'] == int(datetime.fromisoformat(occurred).timestamp())
    assert sent['changed'] == h.now
    assert sent['date'] == '2026-09-15'
    h.now += 301
    h.worker.run_once()
    assert len(h.remote.writes) == 1


def test_same_day_operations_keep_distinct_bank_time_despite_same_send_batch(h):
    job, auth = h.leased(h.connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    day = interval['period']['end']
    earlier, later = day+'T09:55:42+07:00', day+'T13:32:07+07:00'
    # The bank returns newest first; IDs and insertion order cannot choose chronology.
    rows = [record(interval, 'fixture-later', occurred_at=later, local_date=day), record(interval, 'fixture-earlier', occurred_at=earlier, local_date=day)]
    assert h.page(job, interval, auth, records=rows).status_code == 200
    assert h.complete(job, interval, auth, records=2).status_code == 200
    h.worker.run_once()
    sent = {tx['created']:tx for tx in h.remote.writes[0]}
    assert set(sent) == {int(datetime.fromisoformat(t).timestamp()) for t in (earlier,later)}
    assert all(tx['changed'] == h.now and tx['date'] == day for tx in sent.values())


def test_remote_auto_category_does_not_become_confirmed_local_history(h):
    from backend import predictions, records
    from backend.sync_contracts import BankRecord
    _, interval, _, _ = h.capture(3)
    h.remote.mode = 'auto_category'
    h.worker.run_once()
    assert h.states() == ['delivered'] * 3
    assert all(tx['tag'] is None for tx in h.remote.writes[0])
    with storage.transaction(h.path, read_only=True) as db:
        remote = [decode_json(row[0].encode()) for row in db.execute("SELECT data FROM zen_entities WHERE kind='transaction'")]
        assert all(tx['tag'] == ['category-b'] and tx['viewed'] is False for tx in remote)
        assert not db.execute('SELECT 1 FROM category_payments').fetchone()
        assert not db.execute('SELECT 1 FROM category_evidence').fetchone()
    # Their unreviewed server guesses cannot classify the next operation.
    with storage.transaction(h.path) as db:
        pending = records.save_record(db, h.account_id, BankRecord.model_validate(record(interval, 'fixture-next', amount='100')),
            parser_version='fixture', timezone='Asia/Ho_Chi_Minh')
        assert predictions.predict(db, pending, h.now)['category_id'] is None
