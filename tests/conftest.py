# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
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


def strict_script_env() -> dict[str, str]:
    """Environment for running a skill script or recipe as a subprocess: a deprecated API
    called by that code is an error.

    Libraries warn with stacklevel=2, so the warning is attributed to the calling module — the
    script's `__main__`. Deprecations raised inside third-party modules are left alone."""
    env = dict(os.environ)
    rule = "error::DeprecationWarning:__main__"
    env["PYTHONWARNINGS"] = f"{env['PYTHONWARNINGS']},{rule}" if env.get("PYTHONWARNINGS") else rule
    return env


@pytest.fixture(autouse=True)
def _no_issue_service_access(monkeypatch, tmp_path_factory):
    """No test, and no subprocess it starts, can file a real issue or find a login to do it.

    Filing needs a credential, so every test gets the eval agents' hygiene (the eval runner's
    own functions): the issue-service variables removed, a temporary HOME and an empty gh
    configuration folder (a real `gh` finds no login), a `gh` that looks absent first on PATH,
    and the JIRA host variable on a port nothing listens on. A test that needs a fake gh or JIRA
    starts one from tests/fakes and sets these itself, after this fixture. Tests run outside
    bubblewrap (CI runners may not allow it); this is their whole isolation."""
    import run_evals

    for name in [n for n in os.environ if run_evals.env_name_forbidden(n)]:
        monkeypatch.delenv(name)
    scratch = tmp_path_factory.mktemp("isolation")
    home = scratch / "home"
    home.mkdir()
    gh_config = scratch / "gh-config"
    gh_config.mkdir()
    gh_bin = scratch / "bin"
    run_evals.write_gh_shim(gh_bin, scratch / "gh-absent.jsonl")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GH_CONFIG_DIR", str(gh_config))
    monkeypatch.setenv("GH_PROMPT_DISABLED", "1")
    monkeypatch.setenv("GH_NO_UPDATE_NOTIFIER", "1")
    monkeypatch.setenv("PATH", os.pathsep.join([str(gh_bin), os.environ.get("PATH", "")]))
    monkeypatch.setenv("ECMWF_WEATHER_JIRA_URL", run_evals.closed_loopback_url())


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
