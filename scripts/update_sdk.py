"""Stage a previously verified release wheel; never fetch or execute its code."""
import argparse
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path
import re
import tomllib
from zipfile import ZipFile


def stage(source, expected_hash, root):
    data = source.read_bytes()
    if not re.fullmatch('[0-9a-f]{64}', expected_hash) or sha256(data).hexdigest() != expected_hash:
        raise ValueError('Wheel SHA256 does not match the verified release manifest')
    with ZipFile(source) as archive:
        names = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(names) != 1:
            raise ValueError('Expected one wheel distribution')
        metadata = BytesParser().parsebytes(archive.read(names[0]))
    version = metadata['Version']
    expected = f'zenmoney-python=={version}'
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    if (metadata['Name'] != 'zenmoney-python' or expected not in project['dependencies'] or
            not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version or '') or
            source.name != f'zenmoney_python-{version}-py3-none-any.whl'):
        raise ValueError('Wheel name/version must match the canonical dependency in pyproject.toml')
    vendor = root / 'vendor'
    vendor.mkdir(exist_ok=True)
    (vendor / source.name).write_bytes(data)
    (vendor / 'SHA256SUMS').write_text(expected_hash + '  ' + source.name + '\n')
    (vendor / 'requirements-sdk.lock.txt').write_text(expected + ' --hash=sha256:' + expected_hash + '\n')
    for old in vendor.glob('*.whl'):
        if old.name != source.name:
            old.unlink()
    print('Verified and staged ' + expected + '; run Docker tests before committing')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    stage(args.wheel, args.sha256, Path(__file__).resolve().parents[1])
