"""Durable migration participant. Internal primitives, not an HTTP authority API.

The coordinator verifies peer receipts through authenticated participant status.
Their hashes detect substitution/corruption, not malicious administrator forgery.
All transitions, installation fences and immutable replies commit together.
"""
from hashlib import sha256
import json
import re
from uuid import UUID

from .capture_policy import SyncError

LIMIT = 64 * 1024 * 1024
COMMON = {'request_id', 'switch_id', 'expected_epoch'}
FIELDS = {
    'source_start': {'peer_id'},
    'source_seal': {'dataset_digest', 'archive_sha256', 'archive_size'},
    'destination_start': {'peer_receipt'},
    'destination_arm': set(),
    'source_handoff': {'peer_receipt'},
    'source_cancel': set(),
    'destination_discard': {'peer_receipt'},
    'source_resume': {'peer_receipt'},
    'destination_activate': {'peer_receipt'},
}


def require(condition, code='migration_invalid'):
    if not condition:
        status={'migration_invalid':422,'not_found':404,'zenmoney_unavailable':503}.get(code,409)
        raise SyncError(code,status,retryable=code=='zenmoney_unavailable')


def canonical(value):
    def check(item, depth=0):
        require(depth <= 32)
        if item is None or type(item) in (str, bool):
            return
        if type(item) is dict:
            require(all(type(key) is str for key in item))
            for child in item.values(): check(child, depth + 1)
        elif type(item) is list:
            for child in item: check(child, depth + 1)
        else:
            require(False)  # All protocol integer values are canonical strings.
    check(value)
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (UnicodeError, ValueError, RecursionError):
        raise SyncError('migration_invalid', 422) from None
    require(len(raw) <= 16384)
    return raw


def uuid(value):
    try:
        require(type(value) is str and str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError):
        raise SyncError('migration_invalid', 422) from None
    return value


def integer(value, *, maximum=2**63-1, minimum=1):
    require(type(value) is str and len(value) <= 19 and re.fullmatch(r'0|[1-9][0-9]*', value))
    result = int(value)
    require(minimum <= result <= maximum)
    return result


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value))
    return value


def installation(db):
    row = db.execute('SELECT * FROM migration_installation WHERE id=1').fetchone()
    require(row is not None)
    return row


def blocked(db, *, worker=False):
    row = installation(db)
    return row['state'] != 'active' or worker and bool(row['awaiting_sync'])


def guard_legacy(db, *, transfer=False):
    require(not blocked(db), 'migration_paused')
    if transfer:
        # Format 1 has no participant protocol and must not carry or replace its
        # permanent authority history. Ordinary legacy clients remain supported.
        require(not db.execute('SELECT 1 FROM migration_operations LIMIT 1').fetchone(), 'migration_paused')


def status(db, switch_id=None):
    current = installation(db)
    value = dict(installation_id=current['installation_id'], participant_epoch=str(current['participant_epoch']),
        state=current['state'], operation_id=current['operation_id'], awaiting_sync=bool(current['awaiting_sync']))
    if switch_id is not None:
        operation = db.execute('SELECT * FROM migration_operations WHERE switch_id=?', (uuid(switch_id),)).fetchone()
        value['operation'] = dict(operation) if operation else None
        value['receipts'] = []
        if operation:
            value['operation']['validated'] = bool(operation['validated'])
            value['operation']['archive_size'] = str(operation['archive_size']) if operation['archive_size'] is not None else None
            value['operation']['peer_epoch'] = str(operation['peer_epoch']) if operation['peer_epoch'] is not None else None
            for field in ('created_at', 'updated_at'): value['operation'][field] = str(operation[field])
            value['receipts'] = [json.loads(row[0]) for row in db.execute(
                'SELECT receipt_json FROM migration_receipts WHERE switch_id=? ORDER BY rowid', (switch_id,))]
    return value


def _proof(current, operation, request_id, command):
    value = dict(receipt_version='1', request_id=request_id, command=command,
        installation_id=current['installation_id'], participant_epoch=str(current['participant_epoch']),
        switch_id=operation['switch_id'], role=operation['role'], peer_id=operation['peer_id'],
        peer_epoch=str(operation['peer_epoch']) if operation['peer_epoch'] is not None else None,
        dataset_id=operation['dataset_id'], state=operation['state'], dataset_digest=operation['dataset_digest'],
        archive_sha256=operation['archive_sha256'], archive_size=str(operation['archive_size']) if operation['archive_size'] is not None else None)
    return {**value, 'receipt_sha256': sha256(canonical(value)).hexdigest()}


def verify_receipt(value, *, command, current, operation=None):
    require(type(value) is dict and set(value) == {'receipt_version','request_id','command','installation_id',
        'participant_epoch','switch_id','role','peer_id','peer_epoch','dataset_id','state','dataset_digest',
        'archive_sha256','archive_size','receipt_sha256'})
    plain = {key:item for key,item in value.items() if key != 'receipt_sha256'}
    require(value['receipt_version'] == '1' and value['command'] == command)
    require(digest(value['receipt_sha256']) == sha256(canonical(plain)).hexdigest())
    uuid(value['request_id']); uuid(value['switch_id']); uuid(value['installation_id']); uuid(value['peer_id']); uuid(value['dataset_id'])
    integer(value['participant_epoch'])
    if value['peer_epoch'] is not None: integer(value['peer_epoch'])
    require(value['peer_id'] == current['installation_id'] and value['installation_id'] != current['installation_id'])
    states = {'source_seal':('source','export_hold'), 'destination_arm':('destination','armed'),
        'source_handoff':('source','handed_off'), 'source_cancel':('source','cancel_pending'),
        'destination_discard':('destination','discarded')}
    require((value['role'],value['state']) == states[command])
    if value['dataset_digest'] is not None:
        digest(value['dataset_digest']); digest(value['archive_sha256']); integer(value['archive_size'], maximum=LIMIT)
    else:
        require(value['archive_sha256'] is None and value['archive_size'] is None)
    if operation is not None:
        require(value['switch_id'] == operation['switch_id'] and value['installation_id'] == operation['peer_id'])
        for key in ('dataset_id','dataset_digest','archive_sha256'):
            require(value[key] == operation[key])
        require(value['archive_size'] == (str(operation['archive_size']) if operation['archive_size'] is not None else None))
    return value


def _eligible(db, current, *, state='active'):
    from .migration import started, meta
    require(current['state'] == state and not current['ever_activated'] and not current['awaiting_sync'], 'migration_not_empty')
    require(not started(db) and not meta(db, 'migration_import_sha256') and not meta(db,'migration_hold'), 'migration_not_empty')


def _insert(db, request, role, peer_id, dataset_id, state, now, *, peer=None):
    descriptor = (peer['dataset_digest'], peer['archive_sha256'], integer(peer['archive_size'], maximum=LIMIT)) if peer and peer['dataset_digest'] is not None else (None,None,None)
    db.execute('INSERT INTO migration_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (request['switch_id'],role,peer_id,dataset_id,state,*descriptor,
         integer(peer['participant_epoch']) if peer else None,0,now,now))


def set_validated(db, switch_id, *, dataset_digest):
    """Internal service call only, after graph and fresh owner verification."""
    row = db.execute('SELECT * FROM migration_operations WHERE switch_id=?', (switch_id,)).fetchone()
    require(row is not None and row['role'] == 'destination' and row['state'] == 'import_hold')
    require(row['dataset_digest'] == digest(dataset_digest))
    require(archive_details(db, switch_id) == (row['archive_sha256'], row['archive_size']), 'migration_incomplete')
    db.execute('UPDATE migration_operations SET validated=1 WHERE switch_id=?', (switch_id,))


def put_chunk(db, switch_id, *, expected_epoch, number, data, sha256_digest):
    """Persist before ACK. Chunks do not advance the participant decision epoch."""
    uuid(switch_id)
    require(type(number) is int and 0 <= number <= 255 and type(data) is bytes and 1 <= len(data) <= 262144)
    require(digest(sha256_digest) == sha256(data).hexdigest())
    control = installation(db)
    operation = db.execute('SELECT * FROM migration_operations WHERE switch_id=?', (switch_id,)).fetchone()
    require(control['participant_epoch'] == integer(expected_epoch) and control['operation_id'] == switch_id, 'migration_paused')
    require(operation is not None and operation['state'] in ('export_hold','import_hold') and not operation['validated'], 'migration_paused')
    require(operation['role'] != 'source' or operation['dataset_digest'] is None, 'migration_paused')
    if operation['role'] == 'destination':
        require(number*262144 < operation['archive_size'] and len(data) == min(262144,operation['archive_size']-number*262144))
    previous = db.execute('SELECT data,sha256 FROM migration_chunks WHERE switch_id=? AND number=?', (switch_id,number)).fetchone()
    if previous:
        require(previous['data'] == data and previous['sha256'] == sha256_digest)
    else:
        db.execute('INSERT INTO migration_chunks VALUES (?,?,?,?)', (switch_id,number,data,sha256_digest))
    return dict(switch_id=switch_id, number=str(number), bytes=str(len(data)), sha256=sha256_digest)


def archive_details(db, switch_id):
    total, count, checksum, last = 0, 0, sha256(), None
    for row in db.execute('SELECT number,data,sha256 FROM migration_chunks WHERE switch_id=? ORDER BY number', (switch_id,)):
        require(row['number'] == count and (last is None or last == 262144), 'migration_incomplete')
        require(sha256(row['data']).hexdigest() == row['sha256'])
        checksum.update(row['data']); total += len(row['data']); count += 1; last = len(row['data'])
    require(count and total <= LIMIT, 'migration_incomplete')
    return checksum.hexdigest(), total


def transition(db, command, request, now, *, install=None):
    """Run within one BEGIN IMMEDIATE transaction; caller commits before ACK."""
    require(command in FIELDS and type(request) is dict and set(request) == COMMON | FIELDS[command])
    require(type(now) is int and 0 <= now < 2**63)
    uuid(request['request_id']); uuid(request['switch_id'])
    epoch = integer(request['expected_epoch'])
    request_digest = sha256(canonical({'command':command, 'request':request})).hexdigest()
    previous = db.execute('SELECT * FROM migration_receipts WHERE request_id=?', (request['request_id'],)).fetchone()
    if previous:
        require(previous['request_digest'] == request_digest)
        return json.loads(previous['receipt_json'])
    current = installation(db)
    require(current['participant_epoch'] == epoch and epoch < 2**63-1, 'migration_busy')
    operation = db.execute('SELECT * FROM migration_operations WHERE switch_id=?', (request['switch_id'],)).fetchone()
    if command == 'destination_discard' and operation is None:
        # Persist a tombstone even when upload never started. An old delayed
        # destination_start can no longer consume the cancelled source snapshot.
        require(current['state'] == 'active' and not current['awaiting_sync'], 'migration_paused')
        peer = verify_receipt(request['peer_receipt'], command='source_cancel', current=current)
        require(peer['switch_id'] == request['switch_id'])
        _insert(db, request, 'destination', peer['installation_id'], peer['dataset_id'], 'discarded', now, peer=peer)
        next_state = 'discarded'
    elif command in ('source_start','destination_start'):
        require(operation is None and current['state'] == 'active' and not current['awaiting_sync'], 'migration_paused')
        if command == 'source_start':
            from .migration import meta
            require(meta(db, 'migration_hold') is None, 'migration_paused')
            peer_id = uuid(request['peer_id'])
            require(peer_id != current['installation_id'])
            dataset = db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
            _insert(db, request, 'source', peer_id, uuid(dataset), 'export_hold', now)
            next_state = 'export_hold'
        else:
            _eligible(db, current)
            peer = verify_receipt(request['peer_receipt'], command='source_seal', current=current)
            require(peer['switch_id'] == request['switch_id'] and peer['dataset_digest'] is not None)
            _insert(db, request, 'destination', peer['installation_id'], peer['dataset_id'], 'import_hold', now, peer=peer)
            next_state = 'import_hold'
    else:
        require(operation is not None and current['operation_id'] == request['switch_id'], 'migration_paused')
        state, role = operation['state'], operation['role']
        require(current['state'] == state, 'migration_paused')
        if command == 'source_seal':
            require(role == 'source' and state == 'export_hold' and operation['dataset_digest'] is None)
            require(archive_details(db, request['switch_id']) == (request['archive_sha256'], integer(request['archive_size'], maximum=LIMIT)), 'migration_incomplete')
            db.execute('UPDATE migration_operations SET dataset_digest=?,archive_sha256=?,archive_size=? WHERE switch_id=?',
                (digest(request['dataset_digest']),digest(request['archive_sha256']),integer(request['archive_size'], maximum=LIMIT),request['switch_id']))
            next_state = state
        elif command == 'source_cancel':
            require(role == 'source' and state == 'export_hold', 'migration_paused')
            next_state = 'cancel_pending'
        elif command == 'destination_arm':
            require(role == 'destination' and state == 'import_hold' and operation['validated'], 'migration_incomplete')
            next_state = 'armed'
        else:
            peer_command = {'source_handoff':'destination_arm', 'destination_discard':'source_cancel',
                'source_resume':'destination_discard', 'destination_activate':'source_handoff'}[command]
            peer = verify_receipt(request['peer_receipt'], command=peer_command, current=current, operation=operation)
            if command == 'source_handoff':
                require(role == 'source' and state == 'export_hold' and peer['peer_epoch'] == str(epoch), 'migration_paused')
                next_state = 'handed_off'
            elif command == 'destination_discard':
                require(role == 'destination' and state in ('import_hold','armed'), 'migration_paused')
                require(integer(peer['participant_epoch']) > operation['peer_epoch'])
                next_state = 'discarded'
            elif command == 'source_resume':
                require(role == 'source' and state == 'cancel_pending' and peer['peer_epoch'] == str(epoch), 'migration_paused')
                next_state = 'resumed'
            else:
                require(role == 'destination' and state == 'armed' and peer['peer_epoch'] == str(epoch), 'migration_paused')
                _eligible(db, current, state='armed')
                require(callable(install), 'migration_incomplete')
                # Installing rows and consuming activation rights are atomic.
                install(db, operation)
                next_state = 'activated'
            db.execute('UPDATE migration_operations SET peer_epoch=? WHERE switch_id=?', (integer(peer['participant_epoch']),request['switch_id']))
        db.execute('UPDATE migration_operations SET state=?,updated_at=? WHERE switch_id=?', (next_state,now,request['switch_id']))
    active = next_state in ('resumed','discarded','activated')
    db.execute('UPDATE migration_installation SET participant_epoch=participant_epoch+1,state=?,operation_id=?,awaiting_sync=?,ever_activated=max(ever_activated,?) WHERE id=1',
        ('active' if active else next_state, None if active else request['switch_id'],int(next_state == 'activated'),int(next_state == 'activated')))
    current = installation(db)
    operation = db.execute('SELECT * FROM migration_operations WHERE switch_id=?', (request['switch_id'],)).fetchone()
    result = _proof(current, operation, request['request_id'], command)
    raw = canonical(result).decode()
    db.execute('INSERT INTO migration_receipts VALUES (?,?,?,?,?,?)',
        (request['request_id'],request['switch_id'],command,request_digest,raw,result['receipt_sha256']))
    return result
