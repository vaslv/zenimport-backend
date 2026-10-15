from pathlib import Path
import sys

if sys.argv[1:] == ['--init']:
    from .config import create_env
    create_env(Path('.env'))
    print('Создан .env. Токены остаются в файле; заполните настройки перед запуском.')
else:
    import uvicorn
    uvicorn.run('backend.app:create_app', factory=True, host='127.0.0.1', port=8000,
                workers=1, access_log=False)
