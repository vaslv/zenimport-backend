import copy
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app

TOKEN = 'synthetic-bank-test-token-value'
PAYLOAD = {'bank':'vietinbank','adapter_version':'1.0.0','account':'111122223333','currency':'VND',
 'capture':{'start_date':'2026-09-01','end_date':'2026-10-06','row_start':1,'row_end':1,'total_rows':2,'pages':1,'complete':False},
 'transactions':[{'external_id':'vib:f1:synthetic','date':'2026-10-01','amount_minor':-65000,'description':'Coffee',
  'raw':{'reference':'vib:f1:synthetic','reference_kind':'fingerprint','date':'01/10/2026 12:00:00',
         'amount':'-65,000 VND','balance':'935,000 VND','description':'Coffee'}}]}


@pytest.fixture
def client(tmp_path):
    c=TestClient(create_app(tmp_path/'db',TOKEN,'demo'))
    c.headers['Authorization']='Bearer '+TOKEN
    return c


def test_partial_import_repetition_and_metadata(client):
    assert client.post('/api/imports',json=PAYLOAD).json()['added']==1
    assert client.post('/api/imports',json=PAYLOAD).json()['duplicates']==1
    s=client.get('/api/state').json()
    assert s['bank_imports'][0]['capture']['complete'] is False
    assert s['review'][0]['raw']['balance']=='935,000 VND'
    assert s['outbox']==[]


@pytest.mark.parametrize('capture',[
 {'complete':True}, {'row_end':2}, {'start_date':'2026-10-02'}, {'total_rows':0}, {'end_date':None}
])
def test_invalid_coverage_rejected(client,capture):
    data=copy.deepcopy(PAYLOAD);data['capture'].update(capture)
    assert client.post('/api/imports',json=data).status_code==422
    assert client.get('/api/state').json()['review']==[]


def test_changed_fingerprint_is_not_silently_a_new_operation(client):
    assert client.post('/api/imports',json=PAYLOAD).status_code==200
    data=copy.deepcopy(PAYLOAD)
    data['transactions'][0]['external_id']='vib:f1:changed'
    data['transactions'][0]['description']='Changed description'
    data['transactions'][0]['raw']['description']='Changed description'
    assert client.post('/api/imports',json=data).status_code==409
    assert len(client.get('/api/state').json()['review'])==1


def test_distinct_timestamp_same_amount_remains_distinct(client):
    client.post('/api/imports',json=PAYLOAD)
    data=copy.deepcopy(PAYLOAD)
    data['transactions'][0]['external_id']='vib:f1:second'
    data['transactions'][0]['raw']['date']='01/10/2026 12:01:00'
    assert client.post('/api/imports',json=data).json()['added']==1
