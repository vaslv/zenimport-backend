"""Portable-v2 format primitives; graph/identity/activation validation is separate.

These functions never install a database, authenticate an owner, clear a hold or
grant financial write authority. A structurally valid archive is not a verified
financial dataset. The migration service must perform those later gates.
"""
from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat
from tempfile import TemporaryDirectory
from uuid import UUID
import zipfile
import zlib

CONTRACT_BYTES = Path(__file__).with_name('portable_contract.json').read_bytes()
CONTRACT_REVISION = sha256(CONTRACT_BYTES).hexdigest()
CONTRACT = json.loads(CONTRACT_BYTES)
SCHEMA, POLICY = CONTRACT['schema'], CONTRACT['policy']
TABLES = POLICY['logical_tables']
LIMITS = POLICY['limits']
FEATURES = tuple(CONTRACT['format']['required_features'])
CHUNK = 65536


class PortableError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code='invalid_archive'):
    if not condition:
        raise PortableError(code)


def canonical(value):
    try:
        return (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise PortableError('invalid_json') from None


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def _no_float(value):
    raise PortableError('unexpected_json_number')


def load_json(raw):
    try:
        require(not raw.startswith(b'\xef\xbb\xbf'), 'invalid_utf8')
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_float=_no_float, parse_constant=_no_float)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, PortableError):
            raise
        raise PortableError('invalid_json') from None


def scalar(value):
    if value is None:
        return ['null']
    if type(value) is int:
        return ['integer', str(value)]
    if type(value) is str:
        try:
            value.encode('utf-8')
        except UnicodeError:
            raise PortableError('invalid_utf8') from None
        return ['text', value]
    if type(value) is bytes:
        return ['blob', b64encode(value).decode('ascii')]
    raise PortableError('invalid_scalar')


def unscalar(value):
    require(type(value) is list and bool(value), 'invalid_scalar')
    if value == ['null']:
        return None
    require(len(value) == 2 and type(value[1]) is str, 'invalid_scalar')
    kind, text = value
    if kind == 'integer':
        require(len(text) <= 256 and re.fullmatch(r'0|-?[1-9][0-9]*', text), 'invalid_integer')
        return int(text)
    if kind == 'text':
        try:
            text.encode('utf-8')
        except UnicodeError:
            raise PortableError('invalid_utf8') from None
        return text
    if kind == 'blob':
        try:
            result = b64decode(text, validate=True)
        except ValueError:
            raise PortableError('invalid_blob') from None
        require(b64encode(result).decode('ascii') == text, 'invalid_blob')
        return result
    raise PortableError('invalid_scalar')


def columns(name):
    policy = TABLES[name]
    by_name = {column['name']: column for column in SCHEMA['tables'][policy['source']]['columns']}
    return [by_name[key] for key in policy['columns']]


def validate_row(name, values):
    definition = columns(name)
    require(type(values) in (list, tuple) and len(values) == len(definition), 'invalid_row')
    for value, column in zip(values, definition):
        if value is None:
            require(not column['logical_required'], 'required_value')
        elif column['type'] == 'INTEGER':
            require(type(value) is int and -(2**63) <= value < 2**63, 'invalid_column_type')
        else:
            require(type(value) is str, 'invalid_column_type')
            try:
                value.encode('utf-8')
            except UnicodeError:
                raise PortableError('invalid_utf8') from None
    keys = sorted(((column['pk'], index) for index, column in enumerate(definition) if column['pk']))
    require(bool(keys), 'unsupported_table')
    return tuple(values[index].encode('utf-8') if type(values[index]) is str else values[index] for _, index in keys)


def row_bytes(name, values):
    validate_row(name, values)
    result = canonical([scalar(value) for value in values])
    require(len(result) <= LIMITS['row_bytes'], 'row_too_large')
    return result


def _descriptor(item):
    return {key: item[key] for key in ('name', 'filename', 'columns', 'rows', 'bytes', 'sha256')}


def dataset_digest(manifest):
    descriptor = dict(financial_schema_version=1, dataset_id=manifest['dataset_id'],
        zenmoney_identity=dict(owner_ids=manifest['zenmoney_identity']['owner_ids'], proof_version=1),
        required_features=manifest['required_features'], tables=[_descriptor(item) for item in manifest['tables']])
    return sha256(canonical(descriptor)).hexdigest()


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except ValueError:
        return False


def validate_manifest(value):
    require(type(value) is dict and set(value) == set(CONTRACT['manifest']['required']), 'invalid_manifest')
    require(value['format'] == 'zenimport-portable' and type(value['archive_version']) is int and value['archive_version'] == 2, 'unsupported_format')
    require(type(value['financial_schema_version']) is int and value['financial_schema_version'] == 1, 'unsupported_schema')
    for key in ('archive_id', 'dataset_id', 'export_session_id'):
        require(_uuid(value[key]), 'invalid_manifest')
    stamp = value['created_at']
    require(type(stamp) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z', stamp), 'invalid_timestamp')
    try:
        datetime.fromisoformat(stamp)
    except ValueError:
        raise PortableError('invalid_timestamp') from None
    producer = value['producer']
    require(type(producer) is dict and set(producer) == {'version', 'engine', 'contract_revision'}, 'invalid_manifest')
    require(type(producer['version']) is str and producer['engine'] in ('sqlite', 'indexeddb'), 'invalid_manifest')
    require(type(producer['contract_revision']) is str and re.fullmatch('[0-9a-f]{64}', producer['contract_revision']), 'invalid_manifest')
    identity = value['zenmoney_identity']
    require(type(identity) is dict and set(identity) == {'owner_ids', 'proof_version'}, 'invalid_identity')
    require(type(identity['proof_version']) is int and identity['proof_version'] == 1, 'invalid_identity')
    owners = identity['owner_ids']
    require(type(owners) is list and 1 <= len(owners) <= 200 and all(type(owner) is str and bool(owner) for owner in owners), 'invalid_identity')
    try:
        require(owners == sorted(set(owners), key=lambda item: item.encode('utf-8')), 'invalid_identity')
    except UnicodeError:
        raise PortableError('invalid_utf8') from None
    features = value['required_features']
    require(type(features) is list and features == list(FEATURES), 'unsupported_features')
    entries = value['tables']
    require(type(entries) is list and len(entries) == len(TABLES), 'invalid_tables')
    expected = sorted(TABLES, key=lambda name: name.encode('utf-8'))
    for name, item in zip(expected, entries):
        require(type(item) is dict and set(item) == {'name', 'filename', 'columns', 'rows', 'bytes', 'sha256'}, 'invalid_tables')
        require(item['name'] == name and item['filename'] == 'tables/' + name + '.ndjson' and item['columns'] == TABLES[name]['columns'], 'invalid_tables')
        for key in ('rows', 'bytes'):
            require(type(item[key]) is int and 0 <= item[key] <= LIMITS['uncompressed_bytes'], 'invalid_tables')
        require(type(item['sha256']) is str and re.fullmatch('[0-9a-f]{64}', item['sha256']), 'invalid_tables')
    require(sum(item['bytes'] for item in entries) + len(canonical(value)) <= LIMITS['uncompressed_bytes'], 'dataset_too_large')
    require(len(canonical(value)) <= LIMITS['manifest_bytes'], 'manifest_too_large')
    require(value['dataset_digest'] == dataset_digest(value), 'digest_mismatch')
    return value


def _scan_table(path, name, *, expected=None):
    previous = None
    count = size = 0
    digest = sha256()
    with path.open('rb') as source:
        while raw := source.readline(LIMITS['row_bytes'] + 1):
            require(len(raw) <= LIMITS['row_bytes'] and raw.endswith(b'\n'), 'invalid_row')
            tagged = load_json(raw)
            require(type(tagged) is list, 'invalid_row')
            values = [unscalar(value) for value in tagged]
            key = validate_row(name, values)
            require(previous is None or previous < key, 'duplicate_or_unordered_key')
            require(row_bytes(name, values) == raw, 'noncanonical_row')
            previous = key
            count += 1
            size += len(raw)
            require(size <= LIMITS['uncompressed_bytes'], 'dataset_too_large')
            digest.update(raw)
    item = dict(name=name, filename='tables/' + name + '.ndjson', columns=TABLES[name]['columns'],
                rows=count, bytes=size, sha256=digest.hexdigest())
    require(expected is None or item == expected, 'table_integrity_mismatch')
    return item


@dataclass(frozen=True)
class StagedDataset:
    root: Path
    manifest_bytes: bytes

    @property
    def manifest(self):
        return load_json(self.manifest_bytes)

    def rows(self, name):
        require(name in TABLES, 'unsupported_table')
        with (self.root/'tables'/f'{name}.ndjson').open('rb') as source:
            for raw in source:
                yield [unscalar(value) for value in load_json(raw)]


class _BoundedWriter:
    def __init__(self, target):
        self.target = target

    def write(self, value):
        require(self.target.tell() + len(value) <= LIMITS['compressed_bytes'], 'archive_too_large')
        return self.target.write(value)

    def __getattr__(self, name):
        return getattr(self.target, name)


def _zip_info(name):
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = 0o100600 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def create_archive(destination, tables, *, dataset_id, owners, session_id, archive_id, created_at, version):
    """Encode format-valid sorted rows, without claiming graph/owner verification."""
    require(not destination.exists(), 'destination_exists')
    require(set(tables) == set(TABLES), 'invalid_tables')
    created = False
    try:
        with TemporaryDirectory(prefix='.zenimport-portable-', dir=destination.parent) as work:
            root = Path(work)
            (root/'tables').mkdir(mode=0o700)
            entries, total = [], 0
            for name in sorted(TABLES, key=lambda name: name.encode('utf-8')):
                path = root/'tables'/f'{name}.ndjson'
                previous = None
                with path.open('xb') as output:
                    os.chmod(path, 0o600)
                    for values in tables[name]:
                        key = validate_row(name, values)
                        require(previous is None or previous < key, 'duplicate_or_unordered_key')
                        previous = key
                        raw = row_bytes(name, values)
                        total += len(raw)
                        require(total <= LIMITS['uncompressed_bytes'], 'dataset_too_large')
                        output.write(raw)
                entries.append(_scan_table(path, name))
            manifest = dict(format='zenimport-portable', archive_version=2, financial_schema_version=1,
                archive_id=archive_id, created_at=created_at,
                producer=dict(version=version, engine='sqlite', contract_revision=CONTRACT_REVISION),
                dataset_id=dataset_id, export_session_id=session_id,
                zenmoney_identity=dict(owner_ids=owners, proof_version=1), tables=entries,
                dataset_digest='', required_features=list(FEATURES))
            manifest['dataset_digest'] = dataset_digest(manifest)
            validate_manifest(manifest)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(descriptor, 'wb') as output:
                with zipfile.ZipFile(_BoundedWriter(output), 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                    archive.writestr(_zip_info('manifest.json'), canonical(manifest))
                    for entry in entries:
                        with (root/entry['filename']).open('rb') as source, archive.open(_zip_info(entry['filename']), 'w') as target:
                            shutil.copyfileobj(source, target, CHUNK)
                output.flush()
                os.fsync(output.fileno())
            descriptor = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return manifest
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def read_archive(source, destination):
    """Extract into a NEW private stage, with format/integrity checks only."""
    require(not destination.exists(), 'destination_exists')
    require(source.stat().st_size <= LIMITS['compressed_bytes'], 'archive_too_large')
    destination.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(source) as archive:
            items = archive.infolist()
            names = [item.filename for item in items]
            expected = {'manifest.json', *('tables/' + name + '.ndjson' for name in TABLES)}
            require(len(names) == len(expected) and set(names) == expected, 'unexpected_zip_member')
            require(not archive.comment and min(item.header_offset for item in items) == 0, 'invalid_archive')
            for item in items:
                maximum = LIMITS['manifest_bytes'] if item.filename == 'manifest.json' else LIMITS['uncompressed_bytes']
                mode = stat.S_IFMT(item.external_attr >> 16)
                require(item.orig_filename == item.filename and not item.is_dir() and mode in (0, stat.S_IFREG), 'invalid_zip_member')
                require(not item.flag_bits & 1 and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'invalid_zip_member')
                require(0 <= item.file_size <= maximum, 'zip_member_too_large')
            require(sum(item.file_size for item in items) <= LIMITS['uncompressed_bytes'], 'dataset_too_large')
            raw_manifest = archive.read('manifest.json')
            manifest = validate_manifest(load_json(raw_manifest))
            (destination/'tables').mkdir(mode=0o700)
            emitted = len(raw_manifest)
            for entry in manifest['tables']:
                path = destination/entry['filename']
                count = 0
                with archive.open(entry['filename']) as incoming, path.open('xb') as output:
                    os.chmod(path, 0o600)
                    while data := incoming.read(CHUNK):
                        count += len(data)
                        emitted += len(data)
                        require(count <= entry['bytes'] and emitted <= LIMITS['uncompressed_bytes'], 'dataset_too_large')
                        output.write(data)
                _scan_table(path, entry['name'], expected=entry)
        return StagedDataset(destination, raw_manifest)
    except Exception as error:
        shutil.rmtree(destination)
        if isinstance(error, PortableError):
            raise
        if isinstance(error, (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError, OverflowError, EOFError, zipfile.BadZipFile, NotImplementedError, zlib.error)):
            raise PortableError('invalid_archive') from None
        raise
