import os
import secrets
import sys
from pathlib import Path
import uvicorn

if '--init' in sys.argv:
    path = Path('.env')
    with path.open('x') as file:
        file.write(f'API_TOKEN={secrets.token_urlsafe(32)}\nZENMONEY_MODE=demo\nDATABASE_PATH=data/demo.sqlite3\n')
    path.chmod(0o600)
    print('Создан .env. API_TOKEN скопируйте в настройки расширения.')
else:
    if Path('.env').exists():
        for line in Path('.env').read_text().splitlines():
            if line.strip() and not line.startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())
    uvicorn.run('backend.app:create_app', factory=True, host='127.0.0.1', port=int(os.getenv('PORT', '8000')), workers=1)
