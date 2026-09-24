import os
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent))


@pytest.fixture()
def profiles_dir(tmp_path) -> Path:
    d = tmp_path / "profiles"
    shutil.copytree(FIXTURES / "profiles", d, dirs_exist_ok=True)
    return d


@pytest.fixture()
def env(tmp_path, profiles_dir, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("PROFILES_DIR", str(profiles_dir))
    monkeypatch.setenv("ORCA_BIN", str(HERE / "fake_orca.py"))
    monkeypatch.setenv("ORCA_SYSTEM_PROFILES", str(tmp_path / "nonexistent"))
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    os.chmod(HERE / "fake_orca.py", 0o755)
    # reload config-dependent modules so env is picked up
    for m in [k for k in list(sys.modules) if k.startswith("app")]:
        del sys.modules[m]
    yield {"data": data, "profiles": profiles_dir}
