from pathlib import Path
import pytest
from backend.config import create_env


def test_complete_private_config_and_no_overwrite(tmp_path):
    path = tmp_path / '.env'
    create_env(path)
    values = dict(line.split('=', 1) for line in path.read_text().splitlines() if line and not line.startswith('#'))
    assert set(values) == {'API_TOKEN', 'ZENMONEY_TOKEN', 'ZENMONEY_MODE', 'HTTP_PORT', 'COMPOSE_PROJECT_NAME', 'DATABASE_PATH', 'PORT'}
    assert len(values['API_TOKEN']) >= 24
    assert values['ZENMONEY_TOKEN'] == ''
    assert values['ZENMONEY_MODE'] == 'demo'
    assert path.stat().st_mode & 0o777 == 0o600
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        create_env(path)
    assert path.read_bytes() == original
    other = tmp_path / 'other'
    create_env(other)
    assert other.read_bytes() != original
