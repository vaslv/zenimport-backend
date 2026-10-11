import json
import httpx
import pytest
from zenimport_zenmoney import Client, HTTPError, ResponseError, TransportError, FORCE_FETCH, entity_key
from backend.zenmoney import ZenMoney


def response_client(response, **kwargs):
    return Client('private-secret', transport=httpx.MockTransport(lambda request: response), **kwargs)


def test_read_contract_lossless():
    seen = []
    raw = '{"serverTimestamp":22,"budget":[{"user":1,"date":"2026-10-01","tag":null}],"country":[{"id":1}],"newEntity":[{"x":123}],"transaction":[{"id":"t","newField":{"amount":1.2300}}]}'
    def handle(request):
        seen.append(request)
        return httpx.Response(200, content=raw)
    result = Client('private-secret', transport=httpx.MockTransport(handle), clock=lambda: 17).read_diff(0, force_fetch=FORCE_FETCH)
    assert result.raw_json == raw
    assert result['newEntity'][0]['x'] == 123
    assert entity_key('budget', result['budget'][0]) == '[1,"2026-10-01",null]'
    assert seen[0].headers['authorization'] == 'Bearer private-secret'
    assert json.loads(seen[0].content) == {'currentClientTimestamp': 17, 'serverTimestamp': 0, 'forceFetch': list(FORCE_FETCH)}


@pytest.mark.parametrize('status', [301, 401, 403, 429, 500, 503])
def test_http_errors_no_body_or_token(status):
    with pytest.raises(HTTPError) as caught:
        response_client(httpx.Response(status, text='private-secret')).read_diff()
    assert caught.value.status_code == status
    assert 'private-secret' not in str(caught.value)


@pytest.mark.parametrize('error', [httpx.ReadTimeout, httpx.ConnectError])
def test_network_errors_no_retry(error):
    count = 0
    def handle(request):
        nonlocal count
        count += 1
        raise error('private-secret')
    with pytest.raises(TransportError) as caught:
        Client('private-secret', transport=httpx.MockTransport(handle)).read_diff()
    assert count == 1
    assert 'private-secret' not in str(caught.value)


@pytest.mark.parametrize('raw', [b'html', b'\xff', b'[]', b'{"serverTimestamp":true}',
    b'{"serverTimestamp":1,"serverTimestamp":2}', b'{"serverTimestamp":1,"x":NaN}',
    b'{"serverTimestamp":1,"transaction":{}}', b'{"serverTimestamp":1,"account":[{"id":null}]}',
    b'{"serverTimestamp":1,"budget":[{"id":"x"}]}', b'{"serverTimestamp":1,"deletion":[{}]}',
    b'{"serverTimestamp":1,"error":"private-secret"}'])
def test_rejects_invalid_response(raw):
    with pytest.raises(ResponseError):
        response_client(httpx.Response(200, content=raw)).read_diff()


def test_limits_and_cursor():
    with pytest.raises(ResponseError):
        response_client(httpx.Response(200, json={'serverTimestamp': 1}), max_response_bytes=5).read_diff()
    with pytest.raises(ResponseError):
        response_client(httpx.Response(200, json={'serverTimestamp': 1})).read_diff(2)
    with pytest.raises(ValueError):
        response_client(httpx.Response(200)).read_diff(force_fetch=['unknown'])
    with pytest.raises(ValueError):
        ZenMoney('live', 'secret').diff({'serverTimestamp': 0, 'transaction': []})


def test_category_transport_cannot_change_amount_or_identity():
    before={'id':'t','incomeAccount':'a','outcomeAccount':'a','income':0,'outcome':42,'tag':None,'changed':1,'extra':{'x':2}}
    payload={**before,'tag':['food'],'changed':2}
    seen=[]
    def handle(request):
        seen.append(json.loads(request.content));return httpx.Response(200,json={'serverTimestamp':3})
    client=Client('private-secret',transport=httpx.MockTransport(handle),clock=lambda:2)
    client.update_categories([(before,payload)],1)
    assert seen[0]['transaction']==[payload] and len(seen)==1
    for bad in [{**payload,'outcome':43},{**payload,'id':'new'},{**payload,'tag':[]}]:
        with pytest.raises(ValueError):client.update_categories([(before,bad)],1)
    assert len(seen)==1


def test_create_transactions_transport_and_guardrails():
    import uuid
    import copy
    payload={'id':str(uuid.uuid4()),'user':1,'created':100,'changed':100,'deleted':False,'hold':False,
             'date':'2026-10-11','incomeAccount':'account','outcomeAccount':'account','incomeInstrument':1,'outcomeInstrument':1,
             'income':0,'outcome':12.34,'tag':['food'],'payee':'Shop','comment':'Payment'}
    requests=[]
    def handle(request):
        requests.append(json.loads(request.content))
        assert str(request.url)=='https://api.zenmoney.ru/v8/diff/'
        return httpx.Response(200,json={'serverTimestamp':20})
    client=Client('private-secret',transport=httpx.MockTransport(handle),clock=lambda:200)
    client.create_transactions([payload],10)
    assert requests==[{'serverTimestamp':10,'currentClientTimestamp':200,'transaction':[payload]}]
    for key,value in [('deleted',True),('hold',True),('income',2),('outcome',-1),('outcome',float('nan')),('tag',[]),('incomeAccount','other'),('id','existing'),('user',0)]:
        invalid=copy.deepcopy(payload);invalid[key]=value
        with pytest.raises(ValueError):client.create_transactions([invalid],10)
    with pytest.raises(ValueError):client.create_transactions([payload,payload],10)
    assert len(requests)==1
