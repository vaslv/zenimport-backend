"""Bootstrap image-only deployment; do not print tokens or replace a file."""
import os
from pathlib import Path
from backend.config import create_env


def main():
    image = os.environ.get('ZENIMPORT_IMAGE', '')
    if not image or any(c.isspace() or c in '\"\'$`' for c in image):
        raise SystemExit('Set a valid ZENIMPORT_IMAGE reference')
    try:
        create_env('/workspace/.env', Path('/app/.env.server.example'))
    except FileExistsError:
        raise SystemExit('.env already exists; left unchanged') from None
    path = Path('/workspace/.env')
    path.write_text(path.read_text().replace('ZENIMPORT_IMAGE=\n', 'ZENIMPORT_IMAGE=' + image + '\n'))
    print('Created private .env. Set ZENMONEY_TOKEN on the server; use API_TOKEN in Chrome.')


if __name__ == '__main__':
    main()
