"""Create local configuration without exposing its token or overwriting a file."""
from backend.config import create_env

try:
    create_env('/workspace/.env')
except FileExistsError:
    raise SystemExit('.env already exists; the file was left unchanged.')
print('Created .env with all settings. Copy API_TOKEN into the extension; set ZENMONEY_TOKEN separately.')
