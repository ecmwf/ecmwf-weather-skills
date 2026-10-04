# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

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


@pytest.fixture(autouse=True)
def _no_network_unless_live(request, monkeypatch):
    """Offline tests must not touch the network (loopback is allowed for local servers).

    Tests marked `live` may. This keeps `make test` fast and deterministic and catches code
    paths that silently go online (AGENTS.md -> Testing)."""
    if request.node.get_closest_marker("live"):
        return
    import socket

    real = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost") and not str(host).startswith("/"):
            raise RuntimeError(
                f"offline test tried to connect to {host!r}; mark it live or "
                "inject the network call"
            )
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
