"""Format-only tests. These archives intentionally have no verified financial graph."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import stat
import zipfile

import pytest

from backend import portable_codec as codec

LINEAGE = '00000000-0000-4000-8000-000000000001'
SESSION = '00000000-0000-4000-8000-000000000002'
ARCHIVE = '00000000-0000-4000-8000-000000000003'


def tables():
    rows = {name: [] for name in codec.TABLES}
    rows['dataset_metadata'] = [['instance_id', LINEAGE], ['sync_ever_started', '1']]
    rows['source_schema_migrations'] = [[2, 9007199254740993, None], [10, 9007199254740994, None]]
    rows['zen_entities'] = [['user', '9007199254740993', '{"id":9007199254740993,"exact":1.00000000000000000001,"note":"café"}', None, 0, 'run']]
    return rows


def make(tmp_path, *, name='source.zip', **changes):
    parameters = dict(dataset_id=LINEAGE, owners=['9007199254740993'], session_id=SESSION,
        archive_id=ARCHIVE, created_at='2026-10-20T00:00:00.000Z', version='test-only')
    parameters.update(changes)
    path = tmp_path/name
    return path, codec.create_archive(path, tables(), **parameters)


def rewrite(tmp_path, source, change, *, name='changed.zip', metadata=True):
    with zipfile.ZipFile(source) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(contents['manifest.json'])
    change(contents, manifest)
    if metadata:
        for item in manifest['tables']:
            data = contents.get(item['filename'], b'')
            item.update(rows=data.count(b'\n'), bytes=len(data), sha256=sha256(data).hexdigest())
        manifest['dataset_digest'] = codec.dataset_digest(manifest)
        contents['manifest.json'] = codec.canonical(manifest)
    path = tmp_path/name
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return path


def test_runtime_definition_exactly_matches_the_pinned_logical_contract():
    from scripts.build_portable_contract import build
    assert codec.CONTRACT_BYTES == build()
    source = Path(__file__).resolve().parents[1]/'fixtures/business/v1'
    for key, filename in [('schema', 'sqlite-v7.json'), ('policy', 'portable-policy.json'), ('manifest', 'portable-v2.schema.json'), ('format', 'portable-format-v2.json'), ('validation', 'portable-validation-v2.json')]:
        assert codec.CONTRACT[key] == json.loads((source/filename).read_text())
    assert len(codec.TABLES) == 49


def test_format_roundtrip_preserves_raw_text_large_integers_and_null(tmp_path):
    source, manifest = make(tmp_path)
    stage = codec.read_archive(source, tmp_path/'stage')
    assert stage.manifest == manifest
    assert len(manifest['tables']) == 49
    for name, values in tables().items():
        assert list(stage.rows(name)) == values
    assert source.stat().st_mode & 0o777 == 0o600
    assert stage.root.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in (stage.root/'tables').iterdir())
    copy = stage.manifest
    copy['dataset_id'] = 'modified'
    assert stage.manifest['dataset_id'] == LINEAGE
    # This proves format preservation only, not financial provenance/eligibility.
    golden = json.loads((Path(__file__).resolve().parents[1]/'fixtures/business/v1/portable-golden-v2.json').read_text())
    assert golden['format_only'] is True and golden['activation_permitted'] is False
    assert manifest['tables'] == golden['expected_tables']
    assert manifest['dataset_digest'] == golden['expected_digest']
    for name, rows in golden['rows'].items():
        assert list(stage.rows(name)) == [[codec.unscalar(value) for value in row] for row in rows]


def test_dataset_digest_excludes_producer_time_and_session_but_binds_financial_bytes(tmp_path):
    path, first = make(tmp_path)
    identical, _ = make(tmp_path, name='identical.zip')
    assert identical.read_bytes() == path.read_bytes()
    _, second = make(tmp_path, name='second.zip', archive_id=SESSION, session_id=ARCHIVE,
        created_at='2026-10-21T00:00:00Z', version='another-runtime')
    assert first['dataset_digest'] == second['dataset_digest']
    modified = deepcopy(first)
    modified['dataset_id'] = ARCHIVE
    assert codec.dataset_digest(modified) != first['dataset_digest']
    modified = deepcopy(first)
    modified['tables'][0]['sha256'] = '0'*64
    assert codec.dataset_digest(modified) != first['dataset_digest']


@pytest.mark.parametrize('value', [None, '', 'café', '9007199254740993', 9007199254740993, b'\0\xff'])
def test_scalar_tags_are_lossless_and_do_not_guess_text_types(value):
    assert codec.unscalar(codec.scalar(value)) == value


@pytest.mark.parametrize('tag', [['integer', '01'], ['integer', '-0'], ['integer', '1e3'], ['integer', 3],
    ['null', None], ['bool', '1'], ['blob', 'AB=='], ['text', '\ud800']])
def test_malformed_scalars_fail_without_coercion(tag):
    with pytest.raises(codec.PortableError):
        codec.unscalar(tag)


@pytest.mark.parametrize('value', [True, 0.1, float('nan'), object()])
def test_untyped_nonfinite_or_binary_float_values_are_rejected(value):
    with pytest.raises(codec.PortableError):
        codec.scalar(value)


@pytest.mark.parametrize('raw', [b'[["integer","-0"],["integer","1"],["null"]]\n',
    b'[["integer","2"], ["integer","1"],["null"]]\n',
    b'[["integer","2"],["text","1"],["null"]]\n',
    b'[["null"],["integer","1"],["null"]]\n',
    b'[["integer","2"],["integer","9223372036854775808"],["null"]]\n',
    b'[["integer","2"],["integer","1"],["null"]]', b'[]\n'])
def test_self_consistent_hashes_do_not_bypass_column_and_canonical_checks(tmp_path, raw):
    source, _ = make(tmp_path)
    changed = rewrite(tmp_path, source, lambda contents, _: contents.update({'tables/source_schema_migrations.ndjson': raw}))
    with pytest.raises(codec.PortableError):
        codec.read_archive(changed, tmp_path/'stage')
    assert not (tmp_path/'stage').exists()
    assert source.exists()


def test_duplicate_or_unordered_primary_keys_are_rejected_before_installation(tmp_path):
    rows = tables()
    rows['source_schema_migrations'].reverse()
    with pytest.raises(codec.PortableError, match='duplicate_or_unordered_key'):
        codec.create_archive(tmp_path/'result.zip', rows, dataset_id=LINEAGE, owners=['1'],
            session_id=SESSION, archive_id=ARCHIVE, created_at='2026-10-20T00:00:00Z', version='test')
    assert not (tmp_path/'result.zip').exists()
    source, _ = make(tmp_path)
    changed = rewrite(tmp_path, source, lambda contents, _: contents.update({
        'tables/source_schema_migrations.ndjson': contents['tables/source_schema_migrations.ndjson']*2}))
    with pytest.raises(codec.PortableError, match='duplicate_or_unordered_key'):
        codec.read_archive(changed, tmp_path/'stage')


@pytest.mark.parametrize('kind', ['unknown_file', 'traversal', 'digest', 'unknown_feature', 'boolean_counter', 'extra_field', 'owner_order'])
def test_invalid_manifest_or_member_set_leaves_no_stage(tmp_path, kind):
    source, _ = make(tmp_path)
    def change(contents, manifest):
        if kind == 'unknown_file': contents['unexpected.txt'] = b'inert'
        elif kind == 'traversal': contents['../escape'] = b'inert'
        elif kind == 'digest': manifest['dataset_id'] = ARCHIVE
        elif kind == 'unknown_feature': manifest['required_features'].append('future-feature')
        elif kind == 'boolean_counter': manifest['tables'][0]['rows'] = False
        elif kind == 'extra_field': manifest['token'] = 'synthetic-not-a-token'
        elif kind == 'owner_order': manifest['zenmoney_identity']['owner_ids'] = ['b', 'a']
        contents['manifest.json'] = codec.canonical(manifest)
    changed = rewrite(tmp_path, source, change, metadata=False)
    with pytest.raises(codec.PortableError): codec.read_archive(changed, tmp_path/'stage')
    assert not (tmp_path/'stage').exists() and not (tmp_path/'escape').exists()


@pytest.mark.parametrize('kind', ['duplicate', 'symlink', 'compression', 'duplicate_json_key'])
def test_zip_and_json_container_attacks_are_rejected(tmp_path, kind):
    source, _ = make(tmp_path)
    with zipfile.ZipFile(source) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    path = tmp_path/'bad.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in contents.items():
            info = zipfile.ZipInfo(name)
            if name == 'manifest.json':
                if kind == 'symlink': info.external_attr = (stat.S_IFLNK | 0o777) << 16
                if kind == 'compression': info.compress_type = zipfile.ZIP_BZIP2
                if kind == 'duplicate_json_key': data = data.replace(b'{', b'{"format":"duplicate",', 1)
            archive.writestr(info, data)
        if kind == 'duplicate': archive.writestr('manifest.json', contents['manifest.json'])
    with pytest.raises(codec.PortableError): codec.read_archive(path, tmp_path/'stage')
    assert not (tmp_path/'stage').exists()


def test_existing_destinations_are_never_overwritten(tmp_path):
    source, _ = make(tmp_path)
    original = source.read_bytes()
    with pytest.raises(codec.PortableError, match='destination_exists'): make(tmp_path)
    assert source.read_bytes() == original
    destination = tmp_path/'stage'
    destination.mkdir()
    (destination/'sentinel').write_text('keep')
    with pytest.raises(codec.PortableError, match='destination_exists'): codec.read_archive(source, destination)
    assert (destination/'sentinel').read_text() == 'keep'


def test_actual_uncompressed_and_compressed_limits_are_enforced_without_a_stage(tmp_path, monkeypatch):
    source, _ = make(tmp_path)
    original = dict(codec.LIMITS)
    monkeypatch.setitem(codec.LIMITS, 'compressed_bytes', source.stat().st_size - 1)
    with pytest.raises(codec.PortableError, match='archive_too_large'):
        codec.read_archive(source, tmp_path/'stage')
    monkeypatch.setitem(codec.LIMITS, 'compressed_bytes', original['compressed_bytes'])
    monkeypatch.setitem(codec.LIMITS, 'uncompressed_bytes', 10)
    with pytest.raises(codec.PortableError):
        codec.read_archive(source, tmp_path/'stage')
    assert not (tmp_path/'stage').exists()


def test_text_keys_use_utf8_order_instead_of_browser_utf16_order(tmp_path):
    rows = tables()
    rows['dataset_metadata'] = [['😀', 'first'], ['\ue000', 'second']]
    parameters = dict(dataset_id=LINEAGE, owners=['1'], session_id=SESSION,
        archive_id=ARCHIVE, created_at='2026-10-20T00:00:00Z', version='test')
    with pytest.raises(codec.PortableError, match='duplicate_or_unordered_key'):
        codec.create_archive(tmp_path/'invalid.zip', rows, **parameters)
    rows['dataset_metadata'].reverse()
    codec.create_archive(tmp_path/'valid.zip', rows, **parameters)
    # A graph validator must still reject these unclassified metadata keys.


def test_crc_corruption_never_leaves_an_import_candidate(tmp_path):
    source, _ = make(tmp_path)
    data = bytearray(source.read_bytes())
    with zipfile.ZipFile(source) as archive:
        item = archive.getinfo('manifest.json')
        # Local file header is 30 bytes plus its UTF-8 filename and extra fields.
        position = item.header_offset + 30 + len(item.filename.encode()) + len(item.extra)
    data[position + 3] ^= 0xff
    changed = tmp_path/'corrupt.zip'
    changed.write_bytes(data)
    with pytest.raises(codec.PortableError):
        codec.read_archive(changed, tmp_path/'stage')
    assert not (tmp_path/'stage').exists()


def test_bounded_writer_removes_only_its_own_incomplete_archive(tmp_path, monkeypatch):
    monkeypatch.setitem(codec.LIMITS, 'compressed_bytes', 80)
    with pytest.raises(codec.PortableError, match='archive_too_large'):
        make(tmp_path)
    assert not (tmp_path/'source.zip').exists()
    assert not list(tmp_path.glob('.zenimport-portable-*'))
