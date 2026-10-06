"""Generate all documented application settings from one template."""
import os
import secrets
from pathlib import Path


def create_env(path, template=None):
    template = template or Path(__file__).resolve().parent.parent / '.env.example'
    content = Path(template).read_text().replace('API_TOKEN=\n', f'API_TOKEN={secrets.token_urlsafe(32)}\n', 1)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as file:
        file.write(content)
