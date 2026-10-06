"""Create local configuration without exposing its token or overwriting a file."""
from backend.config import create_env

try:
    create_env('/workspace/.env')
except FileExistsError:
    raise SystemExit('.env уже существует; файл оставлен без изменений.')
print('Создан .env со всеми настройками. API_TOKEN скопируйте в расширение; ZENMONEY_TOKEN заполните отдельно.')
