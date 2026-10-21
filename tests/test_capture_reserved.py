"""Live connector data cannot forge verified legacy archive provenance."""
import pytest
from backend import records,storage
from backend.sync_contracts import BankRecord
from capture_factory import Harness,record


def test_live_discovery_rejects_legacy_connector_marker(tmp_path):
    h=Harness(tmp_path/'reserved.sqlite3')
    try:
        payload=dict(bank='vietinbank',profile_key='reserved',profile_identity='bank',connector_version='legacy-v12',
            capabilities=dict(timezone='Asia/Ho_Chi_Minh',currencies=['VND'],max_window_days=30,details='included'),accounts_complete=True,accounts=[])
        reply=h.client.post('/api/v1/connections/discover',json=payload)
        assert reply.status_code==422 and reply.json()['error']['code']=='validation_error'
        with storage.transaction(h.path,read_only=True) as db:assert db.execute('SELECT count(*) FROM connections').fetchone()[0]==0
    finally:h.close()


def test_rejected_legacy_record_does_not_leave_an_unexportable_version(tmp_path):
    h=Harness(tmp_path/'reserved.sqlite3')
    try:
        connection=h.ready();job,auth=h.leased(connection['id']);interval=job['accounts'][0]['intervals'][0]
        rejected=h.page(job,interval,auth,records=[record(interval,identity_version='legacy-v12')])
        assert rejected.status_code==422 and rejected.json()['error']['code']=='validation_error'
        with storage.transaction(h.path,read_only=True) as db:
            assert db.execute('SELECT count(*) FROM bank_versions').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM capture_pages').fetchone()[0]==0
        assert h.page(job,interval,auth,records=[record(interval)]).status_code==200
        assert h.complete(job,interval,auth,records=1).status_code==200
    finally:h.close()


@pytest.mark.parametrize('parser,identity',[('legacy-v12','fixture'),('fixture','legacy-v12')])
def test_internal_live_record_writer_also_rejects_reserved_provenance(tmp_path,parser,identity):
    h=Harness(tmp_path/'reserved.sqlite3')
    try:
        connection=h.ready();job,_=h.leased(connection['id']);interval=job['accounts'][0]['intervals'][0]
        value=BankRecord.model_validate(record(interval,identity_version=identity))
        with storage.transaction(h.path) as db:
            with pytest.raises(ValueError,match='reserved'):
                records.save_record(db,connection['accounts'][0]['id'],value,parser_version=parser,timezone='Asia/Ho_Chi_Minh')
            assert db.execute('SELECT count(*) FROM bank_versions').fetchone()[0]==0
    finally:h.close()
