"""Create local configuration without exposing its token or overwriting a file."""
import os
import secrets
from pathlib import Path

path = Path('/workspace/.env')
try:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit('.env уже существует; файл оставлен без изменений.')
with os.fdopen(descriptor, 'w') as file:
    file.write(f'API_TOKEN={secrets.token_urlsafe(32)}\nZENMONEY_MODE=demo\nHTTP_PORT=18791\n')
print('Создан .env для DEMO. API_TOKEN скопируйте в расширение из файла, не из логов.')
