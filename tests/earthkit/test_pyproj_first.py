# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Scripts load pyproj before ecCodes.

With earthkit-data 1.3 the ecCodes wheels bundle their own libproj; pyproj imported after them
aborts the process at exit (status 134). Scripts whose bundle can contain pyproj load it first
(only when installed, and not at module level, so they stay importable without earthkit)."""

import ast
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pytest
from conftest import SKILLS, load_script


class Stop(BaseException):
    """Ends main() once the first piece of real work starts."""


# skill, script, argv for main(), the function main() calls first once it has parsed arguments
SCRIPTS = [
    ("ecmwf-earthkit", "ekplot", ["meteogram", "x.json", "-o", "x.png"], "plot_meteogram"),
    ("ecmwf-open-data", "odpoint", ["--lat", "38.7", "--lon", "-9.1"], "request_params"),
    ("ecmwf-observations", "obs", ["stations", "lisbon"], "cmd_stations"),
]
IDS = [s[1] for s in SCRIPTS]


@pytest.fixture
def imports(monkeypatch):
    """Record import_module calls for pyproj; find_spec answers for pyproj as set by the test."""
    calls = []
    state = SimpleNamespace(installed=True)
    real_find_spec = importlib.util.find_spec
    real_import = importlib.import_module

    def find_spec(name, *args, **kwargs):
        if name == "pyproj":
            return object() if state.installed else None
        return real_find_spec(name, *args, **kwargs)

    def import_module(name, *args, **kwargs):
        if name == "pyproj":
            calls.append(name)
            return None
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(importlib, "import_module", import_module)
    return SimpleNamespace(calls=calls, state=state)


@pytest.mark.parametrize(("skill", "name", "argv", "first"), SCRIPTS, ids=IDS)
def test_pyproj_is_loaded_when_installed(skill, name, argv, first, imports):
    m = load_script(skill, name)
    m.load_pyproj_first()
    assert imports.calls == ["pyproj"]


@pytest.mark.parametrize(("skill", "name", "argv", "first"), SCRIPTS, ids=IDS)
def test_nothing_is_loaded_when_pyproj_is_not_installed(skill, name, argv, first, imports):
    m = load_script(skill, name)
    imports.state.installed = False
    m.load_pyproj_first()
    assert imports.calls == []


@pytest.mark.parametrize(("skill", "name", "argv", "first"), SCRIPTS, ids=IDS)
def test_main_loads_pyproj_before_any_work(
    skill, name, argv, first, imports, monkeypatch, tmp_path
):
    m = load_script(skill, name)
    if name == "ekplot":  # main() opens the JSON before plotting
        (tmp_path / "x.json").write_text(json.dumps({}))
        argv = ["meteogram", str(tmp_path / "x.json"), "-o", str(tmp_path / "x.png")]
    events = imports.calls

    def work(*args, **kwargs):
        events.append("work")
        raise Stop

    monkeypatch.setattr(m, first, work)
    with pytest.raises(Stop):
        m.main(argv)
    assert events == ["pyproj", "work"]


@pytest.mark.parametrize(("skill", "name", "argv", "first"), SCRIPTS, ids=IDS)
def test_pyproj_is_not_imported_at_module_level(skill, name, argv, first):
    """Scripts import without earthkit in the environment, so nothing heavy at import time."""
    tree = ast.parse((SKILLS / skill / "scripts" / f"{name}.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != "pyproj" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "pyproj"
