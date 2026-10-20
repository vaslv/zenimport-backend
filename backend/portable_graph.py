"""Financial graph checks in private staging. Never installs or activates data."""
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import ValidationError
from zenmoney_api import ResponseError
from zenmoney_api.protocol import decode_json, entity_key

from . import capture, legacy_archive, mirror, storage
from .capture_policy import SyncError, canonical as capture_canonical, validate_record
from .portable_codec import CONTRACT, TABLES, PortableError, _scan_table, columns, require, validate_manifest
from .sync_contracts import BankRecord, CapturePage, StartSync

RULES = CONTRACT['validation']
SECRET_KEYS = frozenset('password passwd token accesstoken refreshtoken apitoken clientsecret authorization auth cookie cookies sessioncookie zenmoneytoken backendtoken servertoken'.split())
SECRET_TEXT = re.compile(r'\bbearer\s+[A-Za-z0-9._~+/-]{12,}|\b(?:access_token|api[_-]?token|password|authorization|cookie)\s*[:=]\s*\S+', re.I)
META = RULES['metadata']


def calendar_day(value):
    require(type(value) is str and re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}', value), 'invalid_bank_date')
    require(date.fromisoformat(value).isoformat() == value, 'invalid_bank_date')
    return date.fromisoformat(value)


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def inspect_json(raw):
    """Screen known credential markers, including nested JSON stored as text."""
    try:
        value = decode_json(raw.encode('utf-8'))
        stack, visited = [(value, 0)], 0
        while stack:
            item, depth = stack.pop()
            visited += 1
            require(depth <= 128 and visited <= 5_000_000, 'json_budget_exceeded')
            if isinstance(item, dict):
                for key, child in item.items():
                    require(re.sub('[^a-z0-9]', '', key.lower()) not in SECRET_KEYS, 'credential_material')
                    stack.append((child, depth + 1))
            elif isinstance(item, list):
                stack.extend((child, depth + 1) for child in item)
            elif isinstance(item, str):
                require(not SECRET_TEXT.search(item), 'credential_material')
                if item.lstrip().startswith(('{', '[')):
                    try:
                        nested = decode_json(item.encode('utf-8'))
                    except (ResponseError, ValueError, RecursionError):
                        continue  # An ordinary note need not itself be JSON.
                    stack.append((nested, depth + 1))
        return value
    except PortableError:
        raise
    except (ResponseError, ValueError, UnicodeError, RecursionError):
        raise PortableError('invalid_financial_json') from None


def metadata_value(key, value, dataset_id):
    require(key in META and type(value) is str, 'unclassified_metadata')
    kind = META[key]
    require(not SECRET_TEXT.search(value), 'credential_material')
    if kind == 'dataset_uuid':
        require(value == dataset_id, 'lineage_mismatch')
    elif kind == 'legacy_hold_audit':
        require(value in ('exported', 'imported'), 'invalid_metadata')
    elif kind == 'sha256':
        require(re.fullmatch('[0-9a-f]{64}', value), 'invalid_metadata')
    elif kind == 'started_flag':
        require(value == '1', 'invalid_metadata')
    elif kind == 'counter_audit':
        require(len(value) <= 19 and re.fullmatch(r'0|[1-9][0-9]*', value) and int(value) < 2**63, 'invalid_metadata')
    elif kind == 'boolean_audit':
        require(value in ('0', '1'), 'invalid_metadata')
    elif kind == 'mode_audit':
        require(value in ('demo', 'live'), 'invalid_metadata')
    else:
        data = inspect_json(value)
        def ids(items):
            return type(items) is list and all(type(item) is str and 0 < len(item) <= 240 for item in items)
        if kind == 'id_list_audit':
            require(ids(data), 'invalid_metadata')
        elif kind == 'catalog_ids_audit':
            require(type(data) is dict and set(data) == {'account', 'tag', 'instrument'} and all(ids(items) for items in data.values()), 'invalid_metadata')
        elif kind == 'portable_import_history':
            require(type(data) is list and len(data)<=10000,'invalid_import_provenance')
            seen=set()
            for item in data:
                require(type(item) is dict and set(item)=={'archive_id','archive_sha256','dataset_digest','export_session_id','accepted_at'},'invalid_import_provenance')
                for field in ('archive_id','export_session_id'):
                    require(type(item[field]) is str and str(UUID(item[field]))==item[field],'invalid_import_provenance')
                for field in ('archive_sha256','dataset_digest'):
                    require(type(item[field]) is str and re.fullmatch('[0-9a-f]{64}',item[field]),'invalid_import_provenance')
                stamp=item['accepted_at']
                require(type(stamp) is str and len(stamp)<=19 and re.fullmatch(r'0|[1-9][0-9]*',stamp) and int(stamp)<2**63,'invalid_import_provenance')
                require(item['archive_id'] not in seen,'invalid_import_provenance');seen.add(item['archive_id'])
        else:
            raise PortableError('unclassified_metadata')


def source_tables(db, dataset_id):
    """Return lazy, sorted financial projections under the caller's stable hold."""
    expected = {TABLES[name]['source'] for name in TABLES} | set(RULES.get('runtime_only_tables', []))
    actual = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'")}
    require(actual == expected, 'unclassified_source_table')
    utf8 = db.execute('PRAGMA encoding').fetchone()[0].upper() == 'UTF-8'
    if not utf8:
        # Unicode scalar order equals UTF-8 byte order, independent of the
        # source file's UTF-16/BINARY collation. Existing UTF-8 indexes stay fast.
        db.create_collation('PORTABLE_UTF8', lambda left, right: (left > right) - (left < right))
    report = {'source_schema_migrations.backup_path': 0, 'capture_jobs.worker_id': 0, 'capture_jobs.lease_expires_at': 0}
    def rows(name):
        policy, definition = TABLES[name], columns(name)
        source = policy['source']
        if name == 'source_schema_migrations' and db.execute('SELECT 1 FROM portable_schema_audit LIMIT 1').fetchone():
            source = 'portable_schema_audit'
        actual = [row[1] for row in db.execute('PRAGMA table_info(' + quote(source) + ')')]
        require(actual == [column['name'] for column in CONTRACT['schema']['tables'][policy['source']]['columns']], 'unclassified_source_column')
        names = policy['columns']
        if name == 'dataset_metadata':
            values = []
            for key,value in db.execute('SELECT key,value FROM service_meta'):
                key = RULES.get('physical_metadata_aliases',{}).get(key,key)
                metadata_value(key,value,dataset_id)
                values.append([key,value])
            require(len({row[0] for row in values}) == len(values),'duplicate_metadata_alias')
            yield from sorted(values,key=lambda row:row[0].encode('utf-8'))
            return
        keys = [column for column in sorted(definition, key=lambda column: column['pk']) if column['pk']]
        order = [quote(column['name']) + (' COLLATE PORTABLE_UTF8' if not utf8 and column['type'] == 'TEXT' else '') for column in keys]
        query = 'SELECT ' + ','.join(map(quote, names)) + ' FROM ' + quote(source) + ' ORDER BY ' + ','.join(order)
        for original in db.execute(query):
            row = list(original)
            if name == 'source_capture_leases':
                raise PortableError('capture_not_quiescent')
            if name == 'dataset_metadata':
                metadata_value(row[0], row[1], dataset_id)
            elif name == 'source_schema_migrations':
                index = names.index('backup_path')
                report['source_schema_migrations.backup_path'] += row[index] is not None
                row[index] = None
            elif name == 'capture_jobs':
                require(row[names.index('capture_state')] not in capture.ACTIVE, 'capture_not_quiescent')
                for field in ('worker_id', 'lease_expires_at'):
                    index = names.index(field)
                    report['capture_jobs.' + field] += row[index] is not None
                    row[index] = None
            yield row
    return {name: rows(name) for name in TABLES}, report


def _load_graph(db, stage):
    definitions = CONTRACT['schema']['tables']
    for table in definitions.values():
        db.execute(table['sql'])  # Packaged trusted definitions, never archived SQL.
    for table in definitions.values():
        for index in table['indexes']:
            if index['sql']:
                db.execute(index['sql'])
    db.execute('PRAGMA defer_foreign_keys=ON')
    for entry in stage.manifest['tables']:
        _scan_table(stage.root/entry['filename'], entry['name'], expected=entry)
        policy = TABLES[entry['name']]
        query = 'INSERT INTO ' + quote(policy['source']) + ' (' + ','.join(map(quote, policy['columns'])) + ') VALUES (' + ','.join('?' for _ in policy['columns']) + ')'
        for row in stage.rows(entry['name']):
            db.execute(query, row)
    require(db.execute('PRAGMA foreign_key_check').fetchone() is None, 'invalid_relationship')
    # Historical INSERT proofs cannot be bypassed by loading children before
    # parents. Re-evaluate the packaged predicates against the completed graph.
    for name, table in definitions.items():
        for trigger in table['triggers']:
            if not re.search(r'BEFORE\s+INSERT\s+ON', trigger['sql'], re.I):
                continue
            match = re.search(r'\bWHEN\s+(.*?)\s+BEGIN\b', trigger['sql'], re.I | re.S)
            require(match is not None, 'unsupported_invariant')
            predicate = re.sub(r'\bNEW\.', 'candidate.', match[1], flags=re.I)
            require(db.execute('SELECT 1 FROM ' + quote(name) + ' candidate WHERE ' + predicate + ' LIMIT 1').fetchone() is None, 'invalid_insert_proof')


def _metadata_and_legacy(db, manifest):
    # A converted legacy projection can legitimately differ from today's API
    # shape (including string money). Compare it with its archived source facts,
    # rather than weakening validation for current SDK responses.
    db.execute('CREATE TEMP TABLE legacy_movement_proofs (source_digest TEXT,kind TEXT,id TEXT,data TEXT,projection TEXT,PRIMARY KEY(source_digest,kind,id,data,projection))')
    db.execute('CREATE INDEX temp.legacy_movement_lookup ON legacy_movement_proofs(kind,id,data,projection)')
    db.execute('CREATE TEMP TABLE legacy_tag_proofs (source_digest TEXT,kind TEXT,entity_id TEXT,tag_id TEXT,position INTEGER,PRIMARY KEY(source_digest,kind,entity_id,tag_id))')
    db.execute('CREATE TEMP TABLE legacy_bank_proofs (id TEXT,data TEXT,PRIMARY KEY(id,data))')
    for key, value in db.execute('SELECT key,value FROM service_meta'):
        metadata_value(key, value, manifest['dataset_id'])
    require(db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone() is not None, 'lineage_mismatch')
    require(not db.execute('SELECT 1 FROM schema_migrations WHERE version<1 OR applied_at<0 OR backup_path IS NOT NULL LIMIT 1').fetchone(), 'invalid_schema_audit')
    known = {(row['type'], row['name']): row for row in RULES['legacy_objects']}
    for row in db.execute('SELECT * FROM legacy_objects'):
        reference = known.get((row['type'], row['name']))
        require(reference is not None and row['table_name'] == reference['table_name'] and row['sql'] == reference['sql'], 'unclassified_legacy_object')
        names = json.loads(row['columns_json']) if row['columns_json'] is not None else None
        require(names == reference['columns'], 'unclassified_legacy_column')
    for row in db.execute('SELECT * FROM legacy_rows'):
        reference = known.get(('table', row['table_name']))
        require(reference is not None, 'unclassified_legacy_object')
        values = inspect_json(row['values_json'])
        require(type(values) is list and len(values) == len(reference['columns']), 'invalid_legacy_row')
        if row['table_name'] == 'bank_transactions':
            original = {}
            for name,cell in zip(reference['columns'],values):
                require(type(cell) is list and len(cell)==2 and cell[0] in ('null','text','integer'),'invalid_legacy_row')
                tag,value=cell
                require(value is None if tag=='null' else type(value) is str,'invalid_legacy_row')
                if tag=='integer':
                    require(re.fullmatch(r'0|-?[1-9][0-9]*',value),'invalid_legacy_row')
                    value=int(value)
                original[name]=value
            db.execute('INSERT OR IGNORE INTO legacy_bank_proofs VALUES (?,?)',(original['id'],json.dumps(original)))
        if row['table_name'] in ('zen_transaction', 'zen_reminder', 'zen_reminder_marker',
                                'zen_transaction_tags','zen_reminder_tags','zen_reminder_marker_tags'):
            fields = dict(zip(reference['columns'], values))
            def archived(name):
                tag, value = fields[name]
                require(tag in ('null', 'text', 'integer'), 'invalid_legacy_row')
                if tag == 'null':
                    require(value is None, 'invalid_legacy_row')
                    return None
                require(type(value) is str, 'invalid_legacy_row')
                if tag == 'integer':
                    require(re.fullmatch(r'0|-?[1-9][0-9]*', value), 'invalid_legacy_row')
                    return int(value)
                return value
            source_table=row['table_name'].removesuffix('_tags')
            kind = {'zen_transaction':'transaction','zen_reminder':'reminder','zen_reminder_marker':'reminderMarker'}[source_table]
            if row['table_name'].endswith('_tags'):
                db.execute('INSERT INTO legacy_tag_proofs VALUES (?,?,?,?,?)',
                    (row['source_digest'],kind,archived('transaction_id' if kind=='transaction' else 'owner_id'),archived('tag_id'),archived('position')))
                continue
            names = ['date' if kind != 'reminder' else 'start_date', 'income_account', 'outcome_account',
                'income_instrument', 'outcome_instrument', 'income', 'outcome']
            projection = [archived(name) for name in names]
            projection.extend(archived(name) if kind == 'transaction' else None for name in ('viewed','hold'))
            db.execute('INSERT OR IGNORE INTO legacy_movement_proofs VALUES (?,?,?,?,?)',
                (row['source_digest'],kind, archived('id'), archived('data'), json.dumps(projection)))
        # Legacy cells are retained as inert tagged data, including opaque
        # financial blobs. They never become executable SQL or runtime settings.
        if row['table_name'] == 'meta':
            require(len(values) == 2 and all(type(cell) is list and len(cell) == 2 and cell[0] == 'text' for cell in values), 'unclassified_metadata')
            metadata_value('legacy:' + values[0][1], values[1][1], manifest['dataset_id'])
    for row in db.execute('SELECT * FROM legacy_imports'):
        counts = inspect_json(row['manifest_json'])
        require(type(counts) is dict and all(type(value) is int and value >= 0 for value in counts.values()), 'invalid_legacy_manifest')
        try:
            legacy_archive.verify_archive(db, row['digest'], counts)
        except RuntimeError:
            raise PortableError('invalid_legacy_audit') from None


def _records(db):
    require(not db.execute('SELECT 1 FROM bank_transactions b LEFT JOIN bank_processing p ON p.bank_id=b.id WHERE p.bank_id IS NULL LIMIT 1').fetchone(), 'missing_processing_state')
    require(not db.execute('SELECT 1 FROM bank_transactions b LEFT JOIN bank_versions v ON v.bank_id=b.id AND v.revision=b.revision WHERE v.bank_id IS NULL OR v.raw_json<>b.raw_json LIMIT 1').fetchone(), 'invalid_current_revision')
    require(not db.execute('''SELECT 1 FROM bank_transactions b JOIN bank_versions v ON v.bank_id=b.id
        GROUP BY b.id HAVING min(v.revision)<>1 OR max(v.revision)<>b.revision OR count(*)<>b.revision LIMIT 1''').fetchone(),'incomplete_revision_history')
    require(not db.execute('''SELECT 1 FROM bank_transactions b WHERE NOT EXISTS
        (SELECT 1 FROM bank_aliases a WHERE a.bank_id=b.id AND a.account_id=b.account_id AND a.external_id=b.external_id) LIMIT 1''').fetchone(), 'missing_bank_alias')
    for bank in db.execute('SELECT b.*,a.scale,a.bank AS account_bank,a.currency AS account_currency FROM bank_transactions b JOIN bank_accounts a ON a.id=b.account_id'):
        require(bank['currency'] == bank['account_currency'] and len(bank['amount'].partition('.')[2]) <= bank['scale'], 'invalid_bank_currency')
        calendar_day(bank['local_date'])
        if bank['identity_version'] == 'legacy-v12':
            from .migrate_legacy import money,occurred
            version=db.execute('SELECT * FROM bank_versions WHERE bank_id=? AND revision=?',(bank['id'],bank['revision'])).fetchone()
            require(version['parser_version']=='legacy-v12','invalid_legacy_proof')
            original=_legacy_bank_version(db,bank,version)
            raw=inspect_json(original['raw'])
            require(type(raw) is dict and type(original.get('amount_minor')) is int and original['amount_minor']!=0,'invalid_legacy_proof')
            details=raw.get('details') or {}
            require(type(details) is dict,'invalid_legacy_proof')
            expected=[original['bank_account'],original['external_id'],original['date'],original['description'],
                original['raw'],money(original['amount_minor'],bank['scale']),'income' if original['amount_minor']>0 else 'expense',
                raw.get('reference_kind','bank'),occurred(raw,bank['account_bank'],original['date']),
                details.get('corresponsiveAccount'),details.get('corresponsiveName')]
            actual=[bank[key] for key in ('account_id','external_id','local_date','description','raw_json','amount','direction','identity_kind','occurred_at','beneficiary_account','beneficiary_name')]
            require(actual==expected,'invalid_legacy_bank_projection')
        else:
            record = BankRecord.model_validate_json(bank['raw_json'])
            for field in ('identity_kind', 'identity_version', 'direction', 'amount', 'currency', 'description', 'beneficiary_account', 'beneficiary_name'):
                require(getattr(record, field) == bank[field], 'bank_raw_mismatch')
            require(record.local_date.isoformat() == bank['local_date'] and record.occurred_at == datetime.fromisoformat(bank['occurred_at']), 'bank_raw_mismatch')
            require(db.execute('SELECT 1 FROM bank_aliases WHERE bank_id=? AND account_id=? AND external_id=?', (bank['id'], bank['account_id'], record.external_id)).fetchone(), 'missing_bank_alias')
    for version in db.execute('''SELECT v.*,b.account_id,a.bank AS account_bank,a.currency AS account_currency FROM bank_versions v
        JOIN bank_transactions b ON b.id=v.bank_id JOIN bank_accounts a ON a.id=b.account_id'''):
        require(version['observed_at'] >= 0, 'invalid_revision_time')
        if version['parser_version'] == 'legacy-v12':
            original=_legacy_bank_version(db,dict(version)|{'id':version['bank_id']},version)
            require(original['raw']==version['raw_json'],'invalid_legacy_proof')
        else:
            require(version['data_json']==version['raw_json'],'invalid_revision_projection')
            record = BankRecord.model_validate_json(version['raw_json'])
            require(db.execute('SELECT 1 FROM bank_aliases WHERE bank_id=? AND account_id=? AND external_id=?', (version['bank_id'], version['account_id'], record.external_id)).fetchone(), 'missing_bank_alias')
    for account in db.execute('SELECT * FROM bank_accounts'):
        for field in ('import_start', 'last_complete_end'):
            if account[field] is not None:
                calendar_day(account[field])
        end = capture.frontier(db, account['id'], date.fromisoformat(account['import_start'])) if account['import_start'] else None
        require(account['last_complete_end'] == (end.isoformat() if end else None), 'invalid_coverage_frontier')


def _legacy_bank_version(db,bank,version):
    original=inspect_json(version['data_json'])
    require(type(original) is dict and original.get('id')==bank['id'] and original.get('bank_account')==bank['account_id'],'invalid_legacy_proof')
    candidates=db.execute('SELECT data FROM legacy_bank_proofs WHERE id=?',(bank['id'],))
    require(any(original==dict(json.loads(row[0]),bank=bank['account_bank'],currency=bank['account_currency']) for row in candidates),'invalid_legacy_proof')
    return original


def _capture_proofs(db):
    require(not db.execute('SELECT 1 FROM account_capture_locks LIMIT 1').fetchone(), 'capture_not_quiescent')
    for job in db.execute('SELECT * FROM capture_jobs'):
        require(job['capture_state'] in ('complete', 'failed', 'cancelled') and job['delivery_state'] in ('waiting', 'running', 'complete', 'blocked'), 'capture_not_quiescent')
        require(job['worker_id'] is None and job['lease_expires_at'] is None, 'executable_source_lease')
        require(date.fromisoformat(job['end_date']).isoformat() == job['end_date'], 'invalid_bank_date')
        states = [row[0] for row in db.execute('SELECT state FROM capture_intervals WHERE job_id=?', (job['id'],))]
        require(all(state in ('complete', 'failed', 'cancelled') for state in states) and capture.aggregate(states) == job['capture_state'], 'invalid_capture_state')
        command = StartSync.model_validate_json(job['request_json'])
        require(str(command.connection_id) == job['connection_id'] and str(command.request_id) == job['request_id'], 'invalid_job_identity')
        require(db.execute('SELECT 1 FROM job_requests WHERE request_id=? AND job_id=? AND request_json=?', (job['request_id'], job['id'], job['request_json'])).fetchone(), 'missing_job_request')
    for request in db.execute('SELECT r.*,j.connection_id FROM job_requests r JOIN capture_jobs j ON j.id=r.job_id'):
        command = StartSync.model_validate_json(request['request_json'])
        require(str(command.request_id) == request['request_id'] and str(command.connection_id) == request['connection_id'], 'invalid_job_identity')
    for page in db.execute('SELECT * FROM capture_pages'):
        require(sha256(page['payload_json'].encode()).hexdigest() == page['digest'], 'invalid_page_digest')
        # Lease fields were deliberately excluded from the stored page. Dummy
        # validation-only values are never persisted or used to resume a job.
        payload = CapturePage.model_validate({**inspect_json(page['payload_json']), 'worker_id': '00000000-0000-4000-8000-000000000000', 'lease_epoch': 1})
        require(payload.page_number == page['page_number'] and len(payload.records) == page['records_count'], 'invalid_page_evidence')
        require(capture_canonical(payload.model_dump(mode='json', exclude={'worker_id', 'lease_epoch'})) == page['payload_json'], 'invalid_page_evidence')
        interval = db.execute('SELECT * FROM capture_intervals WHERE id=?', (page['interval_id'],)).fetchone()
        account = db.execute('SELECT * FROM bank_accounts WHERE id=?', (interval['account_id'],)).fetchone()
        context = db.execute('SELECT * FROM job_context WHERE job_id=?', (interval['job_id'],)).fetchone()
        require(context is not None, 'missing_job_context')
        require((payload.total_pages, payload.total_records) == (interval['total_pages'], interval['total_records']), 'invalid_page_evidence')
        witnessed = set()
        for record in payload.records:
            validate_record(record, account, context, interval['start_date'], interval['end_date'])
            proof = db.execute('''SELECT r.bank_id,v.raw_json FROM captured_records r
                JOIN bank_versions v ON v.bank_id=r.bank_id AND v.revision=r.revision
                JOIN bank_aliases a ON a.bank_id=r.bank_id AND a.account_id=? AND a.external_id=?
                WHERE r.interval_id=? AND r.page_number=?''', (interval['account_id'], record.external_id, interval['id'], page['page_number'])).fetchone()
            require(proof is not None and proof['bank_id'] not in witnessed and proof['raw_json'] == record.model_dump_json(), 'invalid_captured_revision')
            witnessed.add(proof['bank_id'])
        require(len(witnessed) == db.execute('SELECT count(*) FROM captured_records WHERE interval_id=? AND page_number=?', (interval['id'], page['page_number'])).fetchone()[0], 'invalid_captured_revision')
    for interval in db.execute('SELECT * FROM capture_intervals'):
        calendar_day(interval['start_date']); calendar_day(interval['end_date'])
        if interval['state'] == 'complete':
            pages = list(db.execute('SELECT page_number,records_count FROM capture_pages WHERE interval_id=? ORDER BY page_number', (interval['id'],)))
            require(type(interval['total_pages']) is int and [row[0] for row in pages] == list(range(1, interval['total_pages'] + 1)), 'incomplete_coverage')
            require(sum(row[1] for row in pages) == interval['total_records'], 'incomplete_coverage')
    require(not db.execute('''SELECT 1 FROM coverage c JOIN capture_intervals i ON i.id=c.interval_id
        WHERE i.state<>'complete' OR c.account_id<>i.account_id OR c.start_date<>i.start_date OR c.end_date<>i.end_date LIMIT 1''').fetchone(), 'invalid_coverage')


def _history(db):
    require(not db.execute("SELECT 1 FROM deliveries WHERE state='attempting' LIMIT 1").fetchone(), 'delivery_not_quiescent')
    for table in ('deliveries', 'delivery_attempts'):
        for row in db.execute('SELECT * FROM ' + table):
            payload = inspect_json(row['payload'])
            require(isinstance(payload, dict) and str(payload.get('id')) == row['zen_id'], 'invalid_attempt_identity')
    require(not db.execute('''SELECT 1 FROM deliveries d WHERE d.ever_attempted=1 AND d.origin NOT LIKE 'legacy:%' AND NOT EXISTS
        (SELECT 1 FROM delivery_attempts a WHERE a.bank_id=d.bank_id AND a.zen_id=d.zen_id AND a.payload=d.payload) LIMIT 1''').fetchone(), 'missing_attempt_evidence')
    require(not db.execute('''SELECT 1 FROM deliveries d WHERE d.ever_attempted=1 AND d.origin NOT LIKE 'legacy:%' AND NOT EXISTS
        (SELECT 1 FROM delivery_authorizations a WHERE a.bank_id=d.bank_id AND a.job_id=d.authorized_job_id) LIMIT 1''').fetchone(), 'missing_attempt_authorization')
    require(not db.execute('''SELECT 1 FROM delivery_authorizations a JOIN capture_intervals i ON i.id=a.interval_id
        WHERE a.job_id<>i.job_id LIMIT 1''').fetchone(), 'invalid_authorization_job')
    require(not db.execute('''SELECT 1 FROM bank_processing p WHERE p.state IN ('pending','prepared','uncertain') AND
        (EXISTS(SELECT 1 FROM processing_receipts r WHERE r.bank_id=p.bank_id)
         OR EXISTS(SELECT 1 FROM link_bank_members b WHERE b.bank_id=p.bank_id)) LIMIT 1''').fetchone(), 'terminal_history_conflict')
    require(not db.execute('''SELECT 1 FROM bank_processing p WHERE p.state='delivered' AND NOT
        (EXISTS(SELECT 1 FROM processing_receipts r WHERE r.bank_id=p.bank_id)
         OR EXISTS(SELECT 1 FROM link_bank_members b WHERE b.bank_id=p.bank_id)) LIMIT 1''').fetchone(), 'missing_terminal_proof')


def _mirror_projections(db):
    require(db.execute('SELECT count(*) FROM zen_mirror_state WHERE id=1').fetchone()[0] == 1, 'missing_mirror_context')
    require(not db.execute('''SELECT 1 FROM zen_entities e LEFT JOIN zen_movements m ON m.kind=e.kind AND m.id=e.id
        WHERE e.kind IN ('transaction','reminder','reminderMarker') AND m.id IS NULL LIMIT 1''').fetchone(), 'missing_mirror_projection')
    for row in db.execute('''SELECT m.*,e.data,e.run_id FROM zen_movements m JOIN zen_entities e ON e.kind=m.kind AND e.id=m.id'''):
        data = inspect_json(row['data'])
        require(entity_key(row['kind'], data) == row['id'], 'invalid_entity_identity')
        actual = [row[field] for field in ('date', 'income_account', 'outcome_account', 'income_instrument', 'outcome_instrument', 'income', 'outcome', 'viewed', 'hold')]
        proofs=list(db.execute('SELECT source_digest FROM legacy_movement_proofs WHERE kind=? AND id=? AND data=? AND projection=?',
            (row['kind'], row['id'], row['data'], json.dumps(actual))))
        legacy = row['run_id'] is not None and row['run_id'].startswith('legacy:')
        if row['run_id'] is None:
            legacy = bool(db.execute('SELECT 1 FROM legacy_movement_proofs WHERE kind=? AND id=? AND data=?',
                (row['kind'],row['id'],row['data'])).fetchone())
        if proofs or legacy:
            require(proofs,'invalid_legacy_projection')
            tags=[tuple(tag) for tag in db.execute('SELECT tag_id,position FROM zen_tags WHERE kind=? AND entity_id=? ORDER BY position,tag_id',(row['kind'],row['id']))]
            require(any(tags==[tuple(tag) for tag in db.execute('SELECT tag_id,position FROM legacy_tag_proofs WHERE source_digest=? AND kind=? AND entity_id=? ORDER BY position,tag_id',
                (proof[0],row['kind'],row['id']))] for proof in proofs),'invalid_legacy_tags')
            continue
        expected = [data.get('startDate' if row['kind'] == 'reminder' else 'date')]
        if expected[0] is not None:
            calendar_day(expected[0])
        expected.extend(str(data[field]) if data.get(field) is not None else None
                        for field in ('incomeAccount', 'outcomeAccount', 'incomeInstrument', 'outcomeInstrument'))
        expected.extend(mirror.amount(data.get(field)) for field in ('income', 'outcome'))
        expected.extend(mirror.flag(data.get(field)) if row['kind'] == 'transaction' else None for field in ('viewed', 'hold'))
        require(expected == actual, 'invalid_mirror_projection')
        tags = [tuple(tag) for tag in db.execute('SELECT tag_id,position FROM zen_tags WHERE kind=? AND entity_id=? ORDER BY position', (row['kind'], row['id']))]
        require(tags == [(tag,i) for i,tag in enumerate(data.get('tag') or [])], 'invalid_mirror_tags')
    for row in db.execute('SELECT * FROM zen_tombstones'):
        data = inspect_json(row['data'])
        require(type(data) is dict, 'invalid_tombstone')
        if data.get('id') is not None:
            require(str(data['id']) == row['entity_id'], 'invalid_tombstone')
        if data.get('object') is not None:
            require(data['object'] == row['kind'], 'invalid_tombstone')


@dataclass(frozen=True)
class GraphSummary:
    dataset_id: str
    dataset_digest: str
    declared_owners: tuple[str, ...]
    counts: tuple[tuple[str, int], ...]


def validate_stage(stage, *, expected_owners=None):
    """Validate stored facts. Fresh token identity and handoff remain mandatory."""
    manifest = validate_manifest(stage.manifest)
    try:
        with TemporaryDirectory(prefix='.graph-validation-', dir=stage.root) as work:
            path = Path(work)/'graph.sqlite3'
            storage.create_private(path)
            with closing(storage.configured(sqlite3.connect(path))) as db:
                db.execute('BEGIN')
                _load_graph(db, stage)
                for name, table in CONTRACT['schema']['tables'].items():
                    json_columns = re.findall(r'json_valid\((\w+)\)', table['sql'])
                    for column in set(json_columns):
                        for row in db.execute('SELECT ' + quote(column) + ' FROM ' + quote(name) + ' WHERE ' + quote(column) + ' IS NOT NULL'):
                            inspect_json(row[0])
                _metadata_and_legacy(db, manifest)
                _records(db)
                _capture_proofs(db)
                _history(db)
                _mirror_projections(db)
                owners = tuple(row[0] for row in db.execute("SELECT id FROM zen_entities WHERE kind='user' AND deleted=0 ORDER BY id"))
                require(owners == tuple(manifest['zenmoney_identity']['owner_ids']), 'declared_owner_mismatch')
                for row in db.execute("SELECT * FROM zen_entities WHERE deleted=0"):
                    require(entity_key(row['kind'], inspect_json(row['data'])) == row['id'], 'invalid_entity_identity')
                if expected_owners is not None:
                    require(owners == tuple(expected_owners), 'destination_owner_mismatch')
                db.commit()
        return GraphSummary(manifest['dataset_id'], manifest['dataset_digest'], owners,
            tuple((entry['name'], entry['rows']) for entry in manifest['tables']))
    except PortableError:
        raise
    except (sqlite3.Error, ValidationError, SyncError, ResponseError, ValueError, TypeError, KeyError, OverflowError):
        raise PortableError('invalid_financial_graph') from None
