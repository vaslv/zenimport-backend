import os
import sys
from pathlib import Path
import uvicorn
from .config import create_env

if '--init' in sys.argv:
    create_env(Path('.env'))
    print('Создан .env. API_TOKEN скопируйте в настройки расширения.')
else:
    if Path('.env').exists():
        for line in Path('.env').read_text().splitlines():
            if line.strip() and not line.startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())
    uvicorn.run('backend.app:create_app', factory=True, host='127.0.0.1', port=int(os.getenv('PORT', '8000')), workers=1)
