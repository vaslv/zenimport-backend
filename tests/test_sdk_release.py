"""Dependency changes must fail before modifying the public PyPI lock file."""
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile
import importlib.util
import pytest

spec = importlib.util.spec_from_file_location('sdk_update', Path('scripts/update_sdk.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def wheel(tmp_path, name='zenmoney-python'):
    path = tmp_path/'zenmoney_python-0.7.0-py3-none-any.whl'
    with ZipFile(path, 'w') as archive:
        archive.writestr('zenmoney_python-0.7.0.dist-info/METADATA', f'Name: {name}\nVersion: 0.7.0\n')
    return path, sha256(path.read_bytes()).hexdigest()


def project(tmp_path):
    root = tmp_path/'project';root.mkdir()
    (root/'pyproject.toml').write_text('[project]\ndependencies=["zenmoney-python==0.7.0"]\n')
    return root


def test_unverified_digest_cannot_replace_pin(tmp_path):
    path, digest = wheel(tmp_path);root = project(tmp_path)
    with pytest.raises(ValueError, match='SHA256'):
        module.pin(path, '0'*64, root)
    assert not (root/'requirements.sdk.lock.txt').exists()


def test_other_distribution_cannot_occupy_sdk_namespace(tmp_path):
    path, digest = wheel(tmp_path, 'another-package');root = project(tmp_path)
    with pytest.raises(ValueError, match='name/version'):
        module.pin(path, digest, root)
    assert not (root/'requirements.sdk.lock.txt').exists()


def test_verified_release_records_the_package_pin_without_vendoring_code(tmp_path):
    path, digest = wheel(tmp_path);root = project(tmp_path)
    module.pin(path, digest, root)
    assert not (root/'vendor').exists()
    assert (root/'requirements.sdk.lock.txt').read_text().splitlines()[-1] == f'zenmoney-python==0.7.0 --hash=sha256:{digest}'
