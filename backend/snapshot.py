"""Export a consistent owned SQLite snapshot or restore it to a NEW file."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile

from . import storage


def copy_snapshot(source: Path, destination: Path, *, legacy=False):
    if not source.is_absolute() or not destination.is_absolute() or destination.exists():
        raise RuntimeError('Use absolute paths and a new destination file')
    descriptor, temporary = tempfile.mkstemp(prefix='.zenimport-snapshot-', dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        with closing(storage.connect_readonly(source)) as original, closing(storage.configured(sqlite3.connect(temporary))) as target:
            original.execute('BEGIN')
            validate_snapshot(original, legacy)
            storage.check_integrity(original)
            original.backup(target)
            target.execute('PRAGMA journal_mode=DELETE')
            validate_snapshot(target, legacy)
            storage.check_integrity(target)
        with temporary.open('rb') as snapshot:
            os.fsync(snapshot.fileno())
        # Atomic no-clobber publication, even if another exporter raced us.
        os.link(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def validate_snapshot(db, legacy):
    if legacy:
        if db.execute('PRAGMA application_id').fetchone()[0] != 0 or db.execute('PRAGMA user_version').fetchone()[0] != 12:
            raise RuntimeError('Only an explicit legacy schema 12 source is supported')
    else:
        storage.validate_owner(db, allow_upgrade=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('export', 'restore', 'export-legacy'))
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    arguments = parser.parse_args()
    try:
        copy_snapshot(arguments.source, arguments.destination, legacy=arguments.action == 'export-legacy')
    except (OSError, sqlite3.Error, RuntimeError):
        parser.exit(1, 'Snapshot failed; source unchanged, destination must be new and writable.\n')
    print('Snapshot created and verified; source unchanged.')


if __name__ == '__main__':
    main()
