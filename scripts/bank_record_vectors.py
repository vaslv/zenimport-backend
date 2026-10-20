"""Synthetic observations from actual Pydantic bank records and legacy clocks."""
from copy import deepcopy
from datetime import datetime,timedelta
import json
from hashlib import sha256
from zoneinfo import ZoneInfo

from backend.sync_contracts import BankRecord,CapturePage,MAX_BODY_BYTES
from backend.capture_policy import canonical
from backend.migrate_legacy import occurred


def generate():
    base=dict(external_id='fixture-reference',identity_kind='bank',identity_version='fixture-v1',
        occurred_at='2026-10-20T12:34:56+07:00',local_date='2026-10-20',direction='expense',amount='1234',
        currency='VND',description='Fixture expense',details_state='complete',details={'remark':'Fixture'})
    records=[]
    def observe(value):
        try:return dict(accepted=True,value=BankRecord.model_validate(value).model_dump(mode='json'))
        except (ValueError,TypeError,OverflowError):return dict(accepted=False)
    def add(value):records.append(dict(input=value,expected=observe(value)))
    add(base)
    for field,values in {
        'external_id':['','😀'*240,'😀'*241,None,1,True],
        'identity_kind':['fingerprint','unknown',None],
        'identity_version':['','x'*40,'x'*41,3],
        'occurred_at':['2026-10-20T12:34:56Z','2026-10-20T12:34:56+00:00','2026-10-20T12:34:56-00:00',
            '2026-10-20T12:34:56.1+07:00','2026-10-20T12:34:56.000001+07:00','2026-10-20T12:34:56.000000+07:00',
            '0001-01-01T00:00:00+23:59','9999-12-31T23:59:59.999999-23:59','2026-10-20T12:34:56',
            '2026-02-29T12:34:56Z','2026-10-20T24:00:00Z','2026-10-20T12:34:60Z','2026-10-20T12:34:56.1234567Z',
            '2026-10-20T12:34:56+24:00',0,None],
        'local_date':['2024-02-29','2026-02-29','0001-01-01','9999-12-31','2026-1-1',None],
        'direction':['income','transfer',None],
        'amount':['0','0.00','-1','01','1.0','1.000000000001','1.0000000000001','9'*24,'1'+'0'*24,'1e3','1\n',1,True,None],
        'currency':['USD','ABCDEFABCDEF','ABCDEFABCDEFZ','vnd','VN','VND\n',None],
        'description':['','😀'*2000,'😀'*2001,None,2],
        'beneficiary_account':['','x'*240,'x'*241,None,1],
        'beneficiary_name':['','😀'*2000,'😀'*2001,None,False],
        'balance':['-0','-0.00','-1234.50','0','01','1e3','1\n',None,2],
        'details_state':['unavailable','partial',None],
        'details':[{},None,[],{'':'value'},{'x'*240:'value'},{'x'*241:'value'},{'remark':'😀'*2000},{'remark':'😀'*2001},
            {'remark':1},{'remark':None},{str(i):'value' for i in range(64)},{str(i):'value' for i in range(65)}],
    }.items():
        for value in values:add(dict(base,**{field:value}))
    for field in base:
        value=deepcopy(base);del value[field];add(value)
    add(dict(base,unknown='forbidden'))
    pairs=[]
    for left,right in [
        ('2026-10-20T12:34:56.123456+07:00','2026-10-20T05:34:56.123456Z'),
        ('2026-10-20T12:34:56.123456+07:00','2026-10-20T12:34:56.123457+07:00'),
        ('2026-10-20T12:34:56.1+07:00','2026-10-20T12:34:56.100000+07:00'),
        ('0001-01-02T00:00:00+07:00','0001-01-01T17:00:00Z'),
        ('9999-12-30T23:59:59.999999+07:00','9999-12-31T00:59:59.999999+08:00'),
    ]:pairs.append(dict(left=left,right=right,equal=datetime.fromisoformat(left)==datetime.fromisoformat(right)))
    legacy=[]
    for year in [1,1800,1890,1906,1911,1943,1945,1947,1954,1960,1968,1975,2026,9999]:
        day=f'{year:04d}-10-10';raw={'date':f'10/10/{year:04d} 19:19:33'}
        legacy.append(dict(raw=raw,bank='vietinbank',local_date=day,expected=occurred(raw,'vietinbank',day)))
    for raw,bank,day in [
        ({'date':'1/2/2026 1:2:3'},'vietinbank','2026-02-01'),
        ({'date':' 1/2/2026\t1:2:3'},'vietinbank','2026-02-01'),
        ({'date':'01-02-2026 01:02:03'},'vietinbank','2026-02-01'),
        ({'date':'01/02/2026 01:02:03','details':{'processDate':'01/02/2026 04:05:06'}},'vietinbank','2026-02-01'),
        ({'date':'01/02/2026 01:02:03','details':{'processDate':'02/02/2026 04:05:06'}},'vietinbank','2026-02-01'),
        ({'date':'01/02/2026 01:02:03'},'bidv','2026-02-01'),
        ({'date':'29/02/2026 01:02:03'},'vietinbank','2026-02-28'),
        ({'date':'01/02/2026 24:00:00'},'vietinbank','2026-02-01'),
    ]:legacy.append(dict(raw=raw,bank=bank,local_date=day,expected=occurred(raw,bank,day)))
    # Observe both sides of every historical Vietnam offset change. This does
    # not infer behavior from browser ICU or silently assume a permanent UTC+7.
    zone=ZoneInfo('Asia/Ho_Chi_Minh');cursor=datetime(1800,1,1);previous=cursor.replace(tzinfo=zone).utcoffset()
    while cursor<datetime(1980,1,1):
        current=cursor.replace(tzinfo=zone).utcoffset()
        if current!=previous:
            for delta in range(-2,3):
                day=cursor+timedelta(days=delta)
                for hour in [0,1,2,3,21,22,23]:
                    value=day.replace(hour=hour);raw={'date':value.strftime('%d/%m/%Y %H:%M:%S')};local=value.date().isoformat()
                    legacy.append(dict(raw=raw,bank='vietinbank',local_date=local,expected=occurred(raw,'vietinbank',local)))
        previous=current;cursor+=timedelta(days=1)
    wide=dict(count=100,description='😀',repeat=2000,record_base=base)
    request=dict(worker_id='00000000-0000-4000-8000-000000000000',lease_epoch=1,page_number=1,total_pages=1,total_records=wide['count'],
        records=[dict(base,external_id='wide-'+str(i),description=wide['description']*wide['repeat']) for i in range(wide['count'])])
    wire=json.dumps(request,ensure_ascii=False,separators=(',',':')).encode()
    stored=canonical(CapturePage.model_validate(request).model_dump(mode='json',exclude={'worker_id','lease_epoch'})).encode()
    assert len(wire)<MAX_BODY_BYTES<len(stored)
    wide.update(wire_bytes=len(wire),stored_bytes=len(stored),stored_sha256=sha256(stored).hexdigest())
    return dict(contract_version=1,reference='Actual BankRecord model, aware datetime equality and legacy occurred projection',
        records=records,timestamp_pairs=pairs,legacy_timestamps=legacy,wide_capture=wide)


if __name__=='__main__':print(json.dumps(generate(),ensure_ascii=False,separators=(',',':')))
