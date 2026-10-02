import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "ecmwf-weather" / "skills"
FIXTURES = Path(__file__).parent / "fixtures"


def load_script(skill: str, name: str):
    """Import plugins/ecmwf-weather/skills/<skill>/scripts/<name>.py as a module."""
    path = SKILLS / skill / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
