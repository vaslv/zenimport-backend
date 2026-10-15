"""Explicit runtime configuration; secrets never participate in repr/errors."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
from typing import Mapping


def valid_token(value, *, required):
    if not value and not required:
        return
    minimum = 24 if required else 1
    if not isinstance(value, str) or not minimum <= len(value) <= 4096 or any(not 33 <= ord(c) <= 126 for c in value):
        raise RuntimeError('Set valid API_TOKEN (at least 24 characters) and optional ZENMONEY_TOKEN; no whitespace')


@dataclass(frozen=True)
class Settings:
    database_path: Path
    api_token: str = field(repr=False)
    zenmoney_token: str = field(default='', repr=False)

    def __post_init__(self):
        valid_token(self.api_token, required=True)
        valid_token(self.zenmoney_token, required=False)
        if not isinstance(self.database_path, Path) or not self.database_path.is_absolute():
            raise RuntimeError('DATABASE_PATH must be an explicit absolute file path')
        if self.database_path.is_dir():
            raise RuntimeError('DATABASE_PATH must point to a file, not a directory')

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None):
        env = os.environ if environ is None else environ
        raw_path = env.get('DATABASE_PATH', '')
        if not raw_path or raw_path.endswith('/') or raw_path == ':memory:':
            raise RuntimeError('Set DATABASE_PATH to an explicit absolute file path')
        return cls(Path(raw_path), env.get('API_TOKEN', ''), env.get('ZENMONEY_TOKEN', ''))


def create_env(path, template=None):
    template = template or Path(__file__).resolve().parent.parent / '.env.example'
    content = Path(template).read_text().replace('API_TOKEN=\n', f'API_TOKEN={secrets.token_urlsafe(32)}\n', 1)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(content)
