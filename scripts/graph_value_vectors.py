"""Synthetic archive-screening/Decimal observations from actual Python routines."""
from decimal import Decimal
import json
import unicodedata

from backend import portable_graph as graph, mirror
from backend.portable_codec import PortableError
from zenmoney_api.protocol import decode_json
from zenmoney_api import ResponseError

LINEAGE='00000000-0000-4000-8000-000000000001'


def observed(operation):
    try:
        value=operation()
        return dict(accepted=True,value=value)
    except PortableError as error:
        return dict(accepted=False,code=error.code)
    except (ValueError,TypeError,ArithmeticError,ResponseError):
        return dict(accepted=False)


def generate():
    screening=[]
    values=[None,True,100,'ordinary note',{'id':9007199254740993,'amount':Decimal('1.00000000000000000001')},
        {'token':'synthetic'},{'accessToken':'synthetic'},{'API_TOKEN':'synthetic'},
        {'__proto__':{'password':'synthetic'}},{'nested':'{"cookie":"synthetic"}'},
        {'nested':'not JSON {"token":"ordinary note"}'},
        'Bearer ABCDEFGHIJKL','prefix Bearer ABCDEFGHIJKL','abearer ABCDEFGHIJKL',
        'аbearer ABCDEFGHIJKL','\U0001c89bearer ABCDEFGHIJKL','\ua7cbbearer ABCDEFGHIJKL','\u0264bearer ABCDEFGHIJKL','\u0300bearer ABCDEFGHIJKL',
        'apassword: Bearer ABCDEFGHIJKL','acookie=APITOKEN=synthetic',
        'APıTOKEN=synthetic','APİTOKEN=synthetic','PASSWORD: synthetic','ordinary token mention']
    # Decimal-containing raw text is encoded by the SDK, preserving its quantum.
    from zenmoney_api.protocol import encode_json
    for value in values:
        raw=encode_json(value).decode()
        expected=observed(lambda: graph.inspect_json(raw) and None)
        expected.pop('value',None);screening.append(dict(raw=raw,expected=expected))
    for prefix in ['\ufeff',' \ufeff','\ufeff \ufeff','\x1c','\x85','\u2000']:
        for body in [{'token':'synthetic'},{'ordinary':'retained'}]:
            raw=json.dumps({'futureField':prefix+json.dumps(body)})
            expected=observed(lambda: graph.inspect_json(raw) and None);expected.pop('value',None)
            screening.append(dict(raw=raw,expected=expected))
    for raw in ['{"duplicate":1,"duplicate":1}','{"bad":','{"text":"\\ud800"}']:
        expected=observed(lambda: graph.inspect_json(raw) and None);expected.pop('value',None)
        screening.append(dict(raw=raw,expected=expected))
    provenance=dict(archive_id=LINEAGE,archive_sha256='a'*64,dataset_digest='b'*64,
        export_session_id='00000000-0000-4000-8000-000000000002',accepted_at='1792540800')
    metadata=[]
    candidates=[None,True,'',LINEAGE,'exported','imported','a'*64,'A'*64,'0','1','01','-1','1e3',
        '9223372036854775807','9223372036854775808','demo','live','[]','["a","b"]','["same","same"]',
        json.dumps(['😀'*240]),json.dumps(['😀'*241]),json.dumps(dict(account=['a'],tag=['b'],instrument=['2'])),
        json.dumps([provenance]),json.dumps([provenance,provenance]),
        json.dumps([provenance|{'accepted_at':'01'}]),'Bearer ABCDEFGHIJKL']
    for key in [*graph.META,'unknown','legacy:unknown','api_token']:
        for value in candidates:
            expected=observed(lambda: graph.metadata_value(key,value,LINEAGE));expected.pop('value',None)
            metadata.append(dict(key=key,value=value,expected=expected))
    decimals=[]
    for coefficient in ['0','-0','1','-1','1.00','-0.00','123400.56700','9007199254740993.000000000001']:
        for exponent in range(-32,33):
            token=coefficient+'e'+str(exponent)
            decimals.append(dict(token=token,expected=str(Decimal(token))))
    for coefficient in ['1.00','-0.00','9007199254740993.000000000001']:
        for exponent in [-9007199254740993,-1000000,-1000,1000,1000000,9007199254740993]:
            token=coefficient+'e'+str(exponent)
            decimals.append(dict(token=token,expected=str(Decimal(token))))
    amounts=[];flags=[]
    for token in ['null','true','false','0','-0','1','2','-1','1.0000','-0.00','1e2','1e-7','1.0e-6',
        '9007199254740993','"1"','[]','{}']:
        amounts.append(dict(raw=token,expected=observed(lambda: mirror.amount(decode_json(token.encode())))))
        flags.append(dict(raw=token,expected=observed(lambda: None if mirror.flag(decode_json(token.encode())) is None else str(mirror.flag(decode_json(token.encode()))))))
    assert unicodedata.unidata_version=='15.0.0'
    word_ranges=[];start=None
    for code in range(0x110000):
        word=chr(code).isalnum() or code==95
        if word and start is None:start=code
        if start is not None and (not word or code==0x10ffff):
            word_ranges.append([start,code if word else code-1]);start=None
    return dict(contract_version=1,reference='Actual backend archive screening and Python Decimal/mirror values',
        unicode_version=unicodedata.unidata_version,word_ranges=word_ranges,
        dataset_id=LINEAGE,screening=screening,metadata=metadata,decimals=decimals,amounts=amounts,flags=flags)


if __name__=='__main__':print(json.dumps(generate(),ensure_ascii=False,separators=(',',':')))
