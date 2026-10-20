"""Generate only synthetic SQLite CHECK expectations for the browser port."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from backend import storage

SCHEMA=Path('fixtures/business/v1/sqlite-v7.json')


def generate():
    schema=json.loads(SCHEMA.read_text())
    vectors=[]
    cases=[(None,None),('',0),(' ',1),('bank',2),('profile',-1),('enabled',12),('manual',13),('attempting',0),
        ('delivered',1),('suppressed',0),('planned',1),('2026-01-01',0),('9007199254740993.123456789012',0),
        ('0',0),('0.000000000001',1),('01',1),('-1',1),('1e3',0),('1'*25,0),('1.1234567890123',0),
        ('null',0),('true',1),('[]',0),('{"number":9007199254740993.123456789012}',1),('bad JSON',0),
        ('null\x00junk',0),('a\x00b',1),('😀'*120,1),('😀'*121,1),('\ue000',0),('\u2000',1),
        ('\t',2),('  label  ',3),('0.0',9223372036854775807),('1',-9223372036854775808)]
    with closing(storage.configured(sqlite3.connect(':memory:'))) as db:
        for name,table in schema['tables'].items():
            if not table['checks']:continue
            columns=table['columns']
            query='SELECT (%s) FROM (SELECT '+','.join('? AS "'+column['name']+'"' for column in columns)+')'
            for expression in table['checks']:
                expression_cases=cases+([('['*1000+'0'+']'*1000,0),('['*1001+'0'+']'*1001,0),
                    ('['*2000+'0'+']'*2000,0),('['*2001+'0'+']'*2001,0),
                    ('"'+('['*2001)+'"',0)] if 'json_valid(' in expression else [])
                for case,(text,integer) in enumerate(expression_cases):
                    fields={column['name']:integer if column['type']=='INTEGER' else text for column in columns}
                    result=db.execute(query%expression,tuple(fields.values())).fetchone()[0]
                    vectors.append(dict(table=name,expression=expression,case=str(case),
                        fields={key:str(value) if type(value)is int else value for key,value in fields.items()},
                        expected='unknown' if result is None else 'true' if result else 'false'))
                # Valid mixed fields exercise the compound account and delivery
                # checks independently of the uniform NULL/string/integer cases.
                fields={column['name']:0 if column['type']=='INTEGER' else 'fixture' for column in columns}
                fields.update({key:value for key,value in dict(identity_scope='bank',profile_key='',scale=0,supported=1,
                    selection='enabled',zenmoney_account_id='fixture-target',import_start='2026-01-01',settings_version=1,
                    state='attempting',ever_attempted=1,needs_rebuild=0,start_date='2026-01-01',end_date='2026-01-02',
                    amount='100',source='manual',label='Fixture',direction='expense',identity_kind='bank',id=1,
                    needs_full=1,ready=0,deleted=0).items() if key in fields})
                # TEXT identifiers remain text despite a generic mixed-case id.
                fields={column['name']:str(fields[column['name']]) if column['type']=='TEXT' and fields[column['name']] is not None else fields[column['name']] for column in columns}
                for column in columns:
                    if column['name'] in [part.removeprefix('json_valid(').removesuffix(')') for part in table['checks'] if part.startswith('json_valid(')]:fields[column['name']]='{}'
                result=db.execute(query%expression,tuple(fields.values())).fetchone()[0]
                vectors.append(dict(table=name,expression=expression,case='mixed',
                    fields={key:str(value) if type(value)is int else value for key,value in fields.items()},
                    expected='unknown' if result is None else 'true' if result else 'false'))
    return dict(contract_version=1,reference='SQLite expressions with the real backend money_valid function',
        reference_sqlite_version=sqlite3.sqlite_version,vectors=vectors)


if __name__=='__main__':print(json.dumps(generate(),ensure_ascii=False,separators=(',',':')))
