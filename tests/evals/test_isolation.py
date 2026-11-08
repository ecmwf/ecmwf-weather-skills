# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Eval isolation: no eval agent and no test can file a real issue.

Filing needs a credential (a GitHub login, a JIRA token), so the agent of every eval case runs in
a bubblewrap sandbox that hides the places credentials live, gets an allow-listed environment
without issue-service variables, and cannot reach the issue-service hosts. The runner proves the
sandbox before the first case and refuses to run without it; the tests' own environment is kept
clean the same way.

Tests that start a real sandbox plant canaries in temporary directories and a temporary HOME —
never a real credential file.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import run_evals as re_

HOSTS = ("github.com", "api.github.com", "uploads.github.com", "jira.ecmwf.int")
GH_TOKEN_NAMES = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
FORBIDDEN = (
    "JIRA_PAT",
    "JIRA_URL",
    "ECMWF_JIRA_PAT",
    *GH_TOKEN_NAMES,
    "GH_HOST",
    "GH_REPO",
    "SSH_AUTH_SOCK",
    "DBUS_SESSION_BUS_ADDRESS",
)
SYNTHETIC = "eval-synthetic-jira-token"
# What the planted files hold: never a real credential, and not a word the paths contain.
CONTENT = "planted-content-7f3a"


def scratch_for(tmp_path):
    return re_.make_scratch(tmp_path / "scratch", tmp_path / "gh-absent.jsonl")


def env_for(case, tmp_path, base=None, fakes=None):
    return re_.case_env(case, scratch_for(tmp_path), base=base or {"PATH": "/usr/bin"}, fakes=fakes)


# --- the environment ----------------------------------------------------------------------------


def test_the_allow_list_keeps_what_agents_and_data_cases_need(tmp_path):
    base = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/h",
        "LANG": "C.UTF-8",
        "LC_ALL": "C",
        "TERM": "xterm",
        "USER": "u",
        "LOGNAME": "u",
        "SHELL": "/bin/sh",
        "TMPDIR": "/tmp/x",
        "HTTPS_PROXY": "http://proxy:3128",
        "https_proxy": "http://proxy:3128",
        "CLAUDE_CODE_OAUTH_TOKEN": "agent-login",
        "ANTHROPIC_API_KEY": "agent-key",
        "OPENAI_API_KEY": "agent-key",
        "ECMWF_SKILLS_CACHE": "/c",
        "MIR_CACHE_PATH": "/m",
        "CDSAPI_RC": "/rc",
        "ECMWF_API_KEY": "data-key",
        "XDG_DATA_HOME": "/d",
    }
    env = env_for({}, tmp_path, base)
    for name, value in base.items():
        if name == "PATH":
            assert env["PATH"].endswith(value)
        else:
            assert env[name] == value, name


def test_the_allow_list_leaves_everything_else_behind(tmp_path):
    base = {"PATH": "/bin", "SOME_NEW_SECRET": "x", "AWS_SECRET_ACCESS_KEY": "y", "FOO": "z"}
    env = env_for({}, tmp_path, base)
    assert not {"SOME_NEW_SECRET", "AWS_SECRET_ACCESS_KEY", "FOO"} & set(env)


@pytest.mark.parametrize("name", FORBIDDEN)
def test_a_forbidden_variable_is_dropped_from_the_caller_environment(tmp_path, name):
    env = env_for({}, tmp_path, {"PATH": "/bin", name: "real-looking-value"})
    assert name not in env


@pytest.mark.parametrize("name", FORBIDDEN)
def test_a_case_cannot_re_add_a_forbidden_variable(tmp_path, name):
    case = {"setup": {"env": {name: "from-the-case", "CDSAPI_RC": "/missing"}}}
    env = env_for(case, tmp_path)
    assert name not in env and "from-the-case" not in env.values()
    assert env["CDSAPI_RC"] == "/missing"  # the rest of setup.env still applies


def test_forbidden_names_are_matched_without_regard_to_case(tmp_path):
    case = {"setup": {"env": {"jira_pat": "x", "Gh_Token": "y"}}}
    env = env_for(case, tmp_path, {"PATH": "/bin", "github_token": "z"})
    assert not {"jira_pat", "Gh_Token", "github_token"} & set(env)


def test_a_fake_jira_alone_brings_the_synthetic_token(tmp_path):
    case = {"setup": {"fakes": {"jira": {}}}}
    with re_.FakeServices(case, tmp_path, "c") as fakes:
        env = env_for(case, tmp_path, {"PATH": "/bin"}, fakes)
        assert env["JIRA_PAT"] == SYNTHETIC == re_.fake_jira_token()
        assert env["ECMWF_WEATHER_JIRA_URL"] == fakes.jira_url
    for case in ({}, {"setup": {"fakes": {"gh": {}}}}):
        assert "JIRA_PAT" not in env_for(case, tmp_path)


def test_the_synthetic_token_cannot_be_replaced_or_blanked(tmp_path):
    case = {"setup": {"fakes": {"jira": {}}, "env": {"JIRA_PAT": "from-the-case"}}}
    with re_.FakeServices(case, tmp_path, "c") as fakes:
        base = {"PATH": "/bin", "JIRA_PAT": "inherited-real", "ECMWF_JIRA_PAT": "inherited-real"}
        env = env_for(case, tmp_path, base, fakes)
    assert env["JIRA_PAT"] == SYNTHETIC
    assert "inherited-real" not in env.values() and "from-the-case" not in env.values()
    assert "ECMWF_JIRA_PAT" not in env


def test_every_case_has_gh_sign_in_hidden_and_no_prompts(tmp_path):
    case = {"setup": {"env": {"GH_CONFIG_DIR": "/real/gh", "GH_PROMPT_DISABLED": "0"}}}
    scratch = scratch_for(tmp_path)
    env = re_.case_env(case, scratch, base={"PATH": "/bin", "GH_CONFIG_DIR": "/also/real"})
    assert env["GH_CONFIG_DIR"] == str(scratch.gh_config)
    assert scratch.gh_config.is_dir() and not list(scratch.gh_config.iterdir())
    assert env["GH_PROMPT_DISABLED"] == "1" and env["GH_NO_UPDATE_NOTIFIER"] == "1"


def test_a_case_without_a_gh_fake_gets_a_gh_that_looks_absent(tmp_path):
    scratch = scratch_for(tmp_path)
    env = re_.case_env({}, scratch, base={"PATH": "/usr/bin:/bin"})
    assert env["PATH"].split(os.pathsep)[0] == str(scratch.gh_bin)
    assert env["PATH"].endswith("/usr/bin:/bin")
    p = subprocess.run(["gh", "auth", "status"], env=env, capture_output=True, text=True)
    assert p.returncode == 127 and "command not found" in p.stderr
    assert (tmp_path / "gh-absent.jsonl").read_text().strip(), "the call is logged"


def test_a_gh_fake_replaces_the_absent_gh_on_path(tmp_path):
    case = {"setup": {"fakes": {"gh": {}}}}
    scratch = scratch_for(tmp_path)
    with re_.FakeServices(case, tmp_path, "c") as fakes:
        env = re_.case_env(case, scratch, base={"PATH": "/usr/bin"}, fakes=fakes)
        first, second = env["PATH"].split(os.pathsep)[:2]
        assert first == str(fakes.gh_bin) and second != str(scratch.gh_bin)
        assert subprocess.run(["gh", "auth", "status"], env=env).returncode == 0


def test_a_case_without_a_jira_fake_gets_a_closed_loopback_url(tmp_path):
    case = {"setup": {"env": {"ECMWF_WEATHER_JIRA_URL": "https://jira.ecmwf.int"}}}
    env = env_for(case, tmp_path, {"PATH": "/bin", "ECMWF_WEATHER_JIRA_URL": "http://elsewhere"})
    url = env["ECMWF_WEATHER_JIRA_URL"]
    assert url.startswith("http://127.0.0.1:")
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", int(url.rsplit(":")[-1]))):
        pass


def test_a_gh_only_case_still_gets_the_closed_jira_url(tmp_path):
    case = {"setup": {"fakes": {"gh": {}}}}
    with re_.FakeServices(case, tmp_path, "c") as fakes:
        env = env_for(case, tmp_path, fakes=fakes)
    assert env["ECMWF_WEATHER_JIRA_URL"].startswith("http://127.0.0.1:") and "JIRA_PAT" not in env


def test_the_issue_hosts_bypass_any_proxy(tmp_path):
    env = env_for({}, tmp_path, {"PATH": "/bin", "NO_PROXY": "localhost", "no_proxy": "localhost"})
    for var in ("NO_PROXY", "no_proxy"):
        entries = env[var].split(",")
        assert "localhost" in entries and set(HOSTS) <= set(entries)
    assert set(HOSTS) <= set(env_for({}, tmp_path)["NO_PROXY"].split(","))


def test_case_env_needs_its_scratch_folder():
    with pytest.raises(TypeError):
        re_.case_env({}, base={"PATH": "/bin"})


# --- the sandbox command line -------------------------------------------------------------------


def plant_home(home: Path, runtime: Path | None = None) -> None:
    """Every credential location the sandbox hides, with a canary in it."""
    for d in (".config/gh", ".ssh", ".local/share/keyrings"):
        (home / d).mkdir(parents=True)
        (home / d / "canary").write_text(CONTENT)
    for f in (".env", ".git-credentials", ".netrc", ".config/git/credentials"):
        (home / f).parent.mkdir(parents=True, exist_ok=True)
        (home / f).write_text(CONTENT)
    if runtime is not None:
        (runtime / "keyring").mkdir(parents=True)
        (runtime / "bus").write_text("")


def sandbox_for(tmp_path, environ=None, bwrap="/usr/bin/bwrap", real_gh=()):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return re_.Sandbox(
        bwrap, home, tmp_path / "hosts", environ or {"HOME": str(home)}, real_gh=real_gh
    )


def a_gh(directory: Path, text: str = "#!/bin/sh\necho REAL-GH\nexit 0\n") -> Path:
    """A stand-in for a real gh binary in `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    gh = directory / "gh"
    gh.write_text(text)
    gh.chmod(0o755)
    return gh


def pairs(args):
    """The (option, value...) groups of a bwrap argument list, for readable assertions."""
    groups, i = [], 0
    arity = {"--bind": 2, "--ro-bind": 2, "--tmpfs": 1, "--dev": 1, "--proc": 1, "--chdir": 1}
    while i < len(args):
        n = arity.get(args[i], 0)
        groups.append(tuple(args[i : i + 1 + n]))
        i += 1 + n
    return groups


def test_the_command_line_hides_every_credential_location(tmp_path):
    runtime = tmp_path / "run"
    runtime.mkdir()
    plant_home(tmp_path / "home", runtime)
    box = sandbox_for(tmp_path, {"HOME": str(tmp_path / "home"), "XDG_RUNTIME_DIR": str(runtime)})
    groups = pairs(box.args(tmp_path))
    home = tmp_path / "home"
    for d in (".config/gh", ".ssh", ".local/share/keyrings"):
        assert ("--tmpfs", str(home / d)) in groups, d
    for f in (".env", ".git-credentials", ".netrc", ".config/git/credentials"):
        assert ("--ro-bind", "/dev/null", str(home / f)) in groups, f
    assert ("--tmpfs", str(runtime / "keyring")) in groups
    assert ("--ro-bind", "/dev/null", str(runtime / "bus")) in groups


def test_the_command_line_keeps_the_file_system_and_binds_the_hosts_file(tmp_path):
    box = sandbox_for(tmp_path)
    args = box.args(tmp_path)
    groups = pairs(args)
    assert args[0] == "/usr/bin/bwrap"
    assert ("--bind", "/", "/") in groups
    assert ("--dev", "/dev") in groups and ("--proc", "/proc") in groups
    assert ("--ro-bind", str(tmp_path / "hosts"), "/etc/hosts") in groups
    assert "--die-with-parent" in args
    assert ("--chdir", str(tmp_path)) in groups
    # the hiding comes after the file system bind, or it would be covered by it
    order = [g[0] for g in groups]
    assert order.index("--bind") < order.index("--ro-bind")


def seals(args):
    """The paths a command line binds read-only over themselves."""
    return [g[1] for g in pairs(args) if g[0] == "--ro-bind" and g[1] == g[2]]


def test_the_command_line_seals_the_hosts_file_the_shim_and_the_case_folders(tmp_path):
    (tmp_path / "hosts").write_text("127.0.0.1 localhost\n")
    shim = a_gh(tmp_path / "case" / "bin")
    config = tmp_path / "case" / "config"
    config.mkdir()
    folder = tmp_path / "fakes"
    folder.mkdir()
    box = sandbox_for(tmp_path)
    args = box.args(tmp_path, shim, sealed=[tmp_path / "case", folder])
    assert seals(args) == [str(tmp_path / "hosts"), str(shim), str(tmp_path / "case"), str(folder)]
    groups = pairs(args)
    # after the file system bind (or it would be covered by it) and before the files are used
    sealing = groups.index(("--ro-bind", str(shim), str(shim)))
    assert groups.index(("--bind", "/", "/")) < sealing
    assert sealing < groups.index(("--ro-bind", str(tmp_path / "hosts"), "/etc/hosts"))


def test_a_sealed_path_that_does_not_exist_is_left_out(tmp_path):
    # bubblewrap would fail on a missing source
    shim = a_gh(tmp_path / "bin")
    args = sandbox_for(tmp_path).args(tmp_path, shim, sealed=[tmp_path / "nowhere"])
    assert str(tmp_path / "nowhere") not in args
    assert seals(args) == [str(shim)], "no hosts file written: nothing to seal"


def test_the_sealed_paths_are_the_ones_the_probe_checks(tmp_path):
    (tmp_path / "hosts").write_text("")
    shim = a_gh(tmp_path / "bin")
    box = sandbox_for(tmp_path)
    paths = box.sealed_paths(shim, [tmp_path / "bin"])
    assert paths == [tmp_path / "hosts", shim, tmp_path / "bin"]
    assert [Path(p) for p in seals(box.args(tmp_path, shim, sealed=[tmp_path / "bin"]))] == paths


def test_the_case_folders_are_the_scratch_folder_and_the_fakes_folder(tmp_path):
    scratch = scratch_for(tmp_path)
    assert scratch.folder == tmp_path / "scratch" and scratch.gh_bin.parent == scratch.folder
    assert scratch.gh_config.parent == scratch.folder
    assert re_.case_sealed(scratch) == [scratch.folder]
    case = {"setup": {"fakes": {"gh": {}}}}
    with re_.FakeServices(case, tmp_path, "c") as fakes:
        assert fakes.folder == fakes.gh_bin.parent
        assert re_.case_sealed(scratch, fakes) == [scratch.folder, fakes.folder]
    jira_only = re_.FakeServices({"setup": {"fakes": {"jira": {}}}}, tmp_path, "j")
    assert re_.case_sealed(scratch, jira_only) == [scratch.folder], "not started: no folder yet"


def test_the_gh_shim_runs_its_own_copy_of_the_fake(tmp_path):
    # the fake script in the repository stays writable; the copy beside the shim does not
    shim = re_.write_gh_shim(tmp_path / "bin", tmp_path / "log.jsonl")
    copy = tmp_path / "bin" / "fake_gh.py"
    assert copy.read_bytes() == re_.FAKE_GH.read_bytes()
    text = shim.read_text()
    assert str(copy) in text and str(re_.FAKE_GH) not in text
    other = tmp_path / "other.py"
    other.write_text("print('custom')\n")
    custom = re_.write_gh_shim(tmp_path / "bin2", tmp_path / "log.jsonl", script=other)
    assert (tmp_path / "bin2" / "fake_gh.py").read_text() == "print('custom')\n"
    assert str(tmp_path / "bin2" / "fake_gh.py") in custom.read_text()


def test_the_host_process_list_is_not_visible_inside(tmp_path):
    # the runner's own environment (its tokens) is readable through /proc of its process
    args = sandbox_for(tmp_path).args(tmp_path)
    assert "--unshare-pid" in args


def test_the_agent_command_follows_the_options(tmp_path):
    box = sandbox_for(tmp_path)
    shim = a_gh(tmp_path / "shim")
    cmd = box.wrap(["claude", "-p", "hello --tmpfs"], tmp_path, gh_shim=shim, sealed=[])
    assert cmd[-3:] == ["claude", "-p", "hello --tmpfs"] and cmd[-4] == "--"
    assert cmd[: len(box.args(tmp_path, shim))] == box.args(tmp_path, shim)


def test_an_agent_command_needs_its_gh_shim_and_its_sealed_folders(tmp_path):
    # forgetting either is an error, not a sandbox that leaves the real gh reachable or the
    # files it relies on writable
    shim = a_gh(tmp_path / "shim")
    with pytest.raises(TypeError):
        sandbox_for(tmp_path).wrap(["claude"], tmp_path)
    with pytest.raises(TypeError):
        sandbox_for(tmp_path).wrap(["claude"], tmp_path, gh_shim=shim)
    with pytest.raises(TypeError):
        sandbox_for(tmp_path).wrap(["claude"], tmp_path, sealed=[])


def shim_binds(groups, shim):
    """The shim bound over some other path (not the shim bound read-only over itself)."""
    return [g for g in groups if g[:2] == ("--ro-bind", str(shim)) and g[2] != str(shim)]


def test_the_shim_is_bound_over_each_real_gh_that_exists_and_only_those(tmp_path):
    shim = a_gh(tmp_path / "shim")
    usr, local, missing = (tmp_path / d / "gh" for d in ("usr", "local", "missing"))
    a_gh(tmp_path / "usr")
    a_gh(tmp_path / "local")
    box = sandbox_for(tmp_path, real_gh=[usr, local, missing])
    groups = pairs(box.args(tmp_path, shim))
    bound = shim_binds(groups, shim)
    assert sorted(g[2] for g in bound) == sorted([str(usr), str(local)])
    assert not [g for g in groups if g[-1] == str(missing)], (
        "bwrap would create a missing mount point"
    )
    # after the file system bind, or it would be covered by it
    assert groups.index(("--bind", "/", "/")) < groups.index(bound[0])


def test_the_shim_is_bound_over_the_target_of_a_link_once(tmp_path):
    shim = a_gh(tmp_path / "shim")
    real = a_gh(tmp_path / "real")
    link = tmp_path / "bin" / "gh"
    link.parent.mkdir()
    link.symlink_to(real)
    box = sandbox_for(tmp_path, real_gh=[link, real, shim])
    bound = shim_binds(pairs(box.args(tmp_path, shim)), shim)
    # every route through the link ends at the target; the shim itself is not covered by itself
    assert bound == [("--ro-bind", str(shim), str(real.resolve()))]


def test_no_gh_is_bound_when_there_is_no_real_one(tmp_path):
    shim = a_gh(tmp_path / "shim")
    groups = pairs(sandbox_for(tmp_path).args(tmp_path, shim))
    assert not shim_binds(groups, shim)


# --- finding the real gh binaries ---------------------------------------------------------------


def login_shell_says(path):
    """A stand-in for the one login-shell call: `command -v gh` answers `path`."""

    def run(cmd, **kw):
        assert cmd[0].endswith("bash") and cmd[1:] == ["-lc", "command -v gh"], cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{path}\n", stderr="")

    return run


def test_the_real_gh_binaries_are_the_known_ones_the_path_one_and_the_login_shells(tmp_path):
    known = a_gh(tmp_path / "known")
    on_path = a_gh(tmp_path / "onpath")
    login = a_gh(tmp_path / "login")
    absent = tmp_path / "nowhere" / "gh"
    found = re_.real_gh_paths(
        {"PATH": f"{tmp_path / 'empty'}{os.pathsep}{on_path.parent}", "HOME": str(tmp_path)},
        run=login_shell_says(login),
        candidates=(str(known), str(absent)),
    )
    assert found == [known, on_path, login]


def test_a_gh_found_twice_is_listed_once(tmp_path):
    gh = a_gh(tmp_path / "x")
    found = re_.real_gh_paths(
        {"PATH": str(gh.parent)}, run=login_shell_says(gh), candidates=(str(gh),)
    )
    assert found == [gh]


@pytest.mark.parametrize("said", ["", "gh: aliased to something", "alias gh='x'", "relative/gh"])
def test_a_login_shell_answer_that_is_not_a_file_is_ignored(tmp_path, said):
    found = re_.real_gh_paths({"PATH": ""}, run=login_shell_says(said), candidates=())
    assert found == []


def test_a_login_shell_that_fails_or_hangs_leaves_the_other_paths(tmp_path):
    known = a_gh(tmp_path / "known")

    def hangs(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    def breaks(cmd, **kw):
        raise OSError("no bash")

    for run in (hangs, breaks):
        assert re_.real_gh_paths({"PATH": ""}, run=run, candidates=(str(known),)) == [known]


def test_make_sandbox_lists_the_real_gh_binaries(tmp_path, monkeypatch):
    on_path = a_gh(tmp_path / "onpath")
    monkeypatch.setattr(re_, "GH_BINARY_CANDIDATES", ())
    box = re_.make_sandbox(
        tmp_path / "box",
        {"HOME": str(tmp_path), "PATH": str(on_path.parent)},
        which=lambda name: "/usr/bin/bwrap",
        run=login_shell_says(on_path),
    )
    assert box.real_gh == (on_path,)


def test_the_known_real_gh_locations_include_the_package_homebrew_and_local_ones():
    assert {"/usr/bin/gh", "/usr/local/bin/gh", "/opt/homebrew/bin/gh"} <= set(
        re_.GH_BINARY_CANDIDATES
    )


def test_locations_that_do_not_exist_are_left_out(tmp_path):
    # bubblewrap would create a missing mount point in the real home
    box = sandbox_for(tmp_path)
    assert not [a for a in box.args(tmp_path) if str(tmp_path / "home") in a]
    (tmp_path / "home" / ".netrc").write_text(CONTENT)
    groups = pairs(box.args(tmp_path))
    assert ("--ro-bind", "/dev/null", str(tmp_path / "home" / ".netrc")) in groups
    assert not [g for g in groups if g[-1] == str(tmp_path / "home" / ".ssh")]


def test_the_callers_gh_configuration_folders_are_hidden_too(tmp_path):
    custom, xdg = tmp_path / "custom-gh", tmp_path / "xdg"
    (xdg / "gh").mkdir(parents=True)
    custom.mkdir()
    environ = {"HOME": str(tmp_path / "home"), "GH_CONFIG_DIR": str(custom)}
    environ["XDG_CONFIG_HOME"] = str(xdg)
    groups = pairs(sandbox_for(tmp_path, environ).args(tmp_path))
    assert ("--tmpfs", str(custom)) in groups and ("--tmpfs", str(xdg / "gh")) in groups


def test_a_location_inside_another_is_listed_once(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "gh").mkdir(parents=True)
    environ = {"HOME": str(home), "GH_CONFIG_DIR": str(home / ".config" / "gh")}
    args = sandbox_for(tmp_path, environ).args(tmp_path)
    assert args.count(str(home / ".config" / "gh")) == 1


# --- the hosts file -----------------------------------------------------------------------------


def test_the_hosts_file_maps_the_issue_hosts_and_keeps_the_system_entries(tmp_path):
    system = "127.0.0.1 localhost\n10.1.2.3 build.example\n# comment\n::1 ip6-localhost\n"
    path = tmp_path / "hosts"
    re_.write_hosts_file(path, system)
    lines = path.read_text().splitlines()
    mapped = {}
    for ln in lines:
        addr, *names = ln.split("#")[0].split() or [""]
        mapped.update({n: addr for n in names})
    for host in HOSTS:
        assert mapped[host] == re_.UNUSED_ADDRESS == "127.0.0.9", host
    for ln in system.splitlines():
        assert ln in lines
    # glibc answers from the first matching line: the block comes before the system entries,
    # or a system line for github.com would win
    assert max(lines.index(ln) for ln in lines if ln.startswith("127.0.0.9")) < lines.index(
        "127.0.0.1 localhost"
    )


def test_a_system_entry_for_an_issue_host_does_not_win(tmp_path):
    path = tmp_path / "hosts"
    re_.write_hosts_file(path, "140.82.121.4 github.com\n")
    first = next(ln for ln in path.read_text().splitlines() if "github.com" in ln.split("#")[0])
    assert first.startswith("127.0.0.9")


def test_the_hosts_file_is_made_from_the_systems_by_default(tmp_path):
    path = tmp_path / "hosts"
    re_.write_hosts_file(path)
    assert "localhost" in path.read_text() and "jira.ecmwf.int" in path.read_text()


# --- the probe (what the self-test runs inside the sandbox) -------------------------------------


def test_the_probe_flags_a_hidden_location_that_is_readable(tmp_path):
    canary = tmp_path / "hidden"
    canary.mkdir()
    (canary / "hosts.yml").write_text(CONTENT)
    secret = tmp_path / "token"
    secret.write_text(CONTENT)
    check = re_.check_hidden([canary, secret, tmp_path / "absent"])
    assert not check["ok"] and str(canary) in check["detail"] and str(secret) in check["detail"]
    assert str(tmp_path / "absent") not in check["detail"]


def test_the_probe_accepts_empty_unreadable_and_absent_locations(tmp_path):
    empty_dir, empty_file = tmp_path / "d", tmp_path / "f"
    empty_dir.mkdir()
    empty_file.write_text("")
    locked = tmp_path / "locked"
    locked.write_text(CONTENT)
    locked.chmod(0)
    try:
        check = re_.check_hidden([empty_dir, empty_file, locked, tmp_path / "absent"])
        if os.geteuid() == 0:  # root reads a mode 0 file: not a case for this test
            check = re_.check_hidden([empty_dir, empty_file, tmp_path / "absent"])
    finally:
        locked.chmod(0o600)
    assert check["ok"], check


def test_the_probe_flags_a_host_that_resolves_elsewhere_or_accepts_a_connection():
    def resolve(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("140.82.121.4", port))]

    def refuse(address):
        raise ConnectionRefusedError

    check = re_.check_hosts(HOSTS, re_.UNUSED_ADDRESS, resolve=resolve, connect=refuse)
    assert not check["ok"] and "github.com" in check["detail"]

    def right(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (re_.UNUSED_ADDRESS, port))]

    assert re_.check_hosts(HOSTS, re_.UNUSED_ADDRESS, resolve=right, connect=refuse)["ok"]
    check = re_.check_hosts(HOSTS, re_.UNUSED_ADDRESS, resolve=right, connect=lambda a: None)
    assert not check["ok"] and "connection" in check["detail"]
    slow = re_.check_hosts(HOSTS, re_.UNUSED_ADDRESS, resolve=right, connect=_raise_timeout)
    assert not slow["ok"], "a timeout is not a refusal"


def _raise_timeout(address):
    raise TimeoutError


def test_the_probe_flags_a_sealed_path_that_can_be_written(tmp_path):
    hosts, folder = tmp_path / "hosts", tmp_path / "case"
    hosts.write_text("127.0.0.9 github.com\n")
    folder.mkdir()
    check = re_.check_sealed([hosts, folder])
    assert not check["ok"]
    assert f"{hosts} can be written" in check["detail"]
    assert f"{folder} can be written" in check["detail"]
    assert hosts.read_text() == "127.0.0.9 github.com\n", "the probe changes nothing"
    assert list(folder.iterdir()) == []
    only = re_.check_sealed([hosts, folder], writable=lambda p: p == folder)
    assert not only["ok"] and str(hosts) not in only["detail"] and str(folder) in only["detail"]
    assert re_.check_sealed([hosts, folder], writable=lambda p: False)["ok"]


def test_the_probe_flags_a_sealed_path_that_is_missing(tmp_path):
    check = re_.check_sealed([tmp_path / "gone"], writable=lambda p: False)
    assert not check["ok"] and str(tmp_path / "gone") in check["detail"]


def test_the_probe_sees_a_read_only_file_as_sealed(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("x\n")
    hosts.chmod(0o444)
    try:
        # root writes a mode 444 file, so for root the probe must say so
        assert re_.check_sealed([hosts])["ok"] == (os.geteuid() != 0)
    finally:
        hosts.chmod(0o644)


def test_the_probe_flags_a_forbidden_variable_but_not_the_ones_the_runner_sets():
    runner_set = {
        "PATH": "/bin",
        "GH_CONFIG_DIR": "/e",
        "GH_PROMPT_DISABLED": "1",
        "GH_NO_UPDATE_NOTIFIER": "1",
        "ECMWF_WEATHER_JIRA_URL": "http://127.0.0.1:1",
    }
    assert re_.check_environment(runner_set)["ok"]
    for name in ("GH_TOKEN", "JIRA_PAT", "SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS", "GH_HOST"):
        check = re_.check_environment({**runner_set, name: "a-value"})
        assert not check["ok"] and name in check["detail"] and "a-value" not in check["detail"]


def test_the_probe_flags_a_gh_that_reports_a_login(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\nexit 0\n")
    gh.chmod(0o755)
    check = re_.check_gh([str(gh)], dict(os.environ))
    assert not check["ok"] and str(gh) in check["detail"]
    gh.write_text("#!/bin/sh\necho 'not logged in' >&2\nexit 1\n")
    assert re_.check_gh([str(gh)], dict(os.environ))["ok"]
    assert re_.check_gh([], {})["ok"]


def test_the_probe_flags_a_gh_that_is_not_the_shim(tmp_path):
    real = a_gh(tmp_path / "real")
    shim = re_.write_gh_shim(tmp_path / "shim", tmp_path / "log.jsonl")
    env = dict(os.environ)
    check = re_.check_gh_shim([str(real)], env, login_shell=False)
    assert not check["ok"] and str(real) in check["detail"]
    assert re_.check_gh_shim([str(shim)], env, login_shell=False)["ok"]
    assert re_.check_gh_shim([], env, login_shell=False)["ok"]


def test_the_probe_flags_a_gh_that_only_looks_like_the_shim(tmp_path):
    # exit 127 alone is not enough: the shim also says why
    quiet = a_gh(tmp_path / "quiet", "#!/bin/sh\nexit 127\n")
    assert not re_.check_gh_shim([str(quiet)], dict(os.environ), login_shell=False)["ok"]


def test_the_probe_flags_a_gh_the_login_shell_finds(tmp_path):
    real = a_gh(tmp_path / "real")
    profile_home = tmp_path / "home"
    profile_home.mkdir()
    (profile_home / ".bash_profile").write_text(f'export PATH="{real.parent}:$PATH"\n')
    env = {**os.environ, "HOME": str(profile_home)}
    check = re_.check_gh_shim([], env)
    assert not check["ok"] and str(real) in check["detail"] and "login shell" in check["detail"]


def test_the_probe_never_reports_a_token_value(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\necho SECRET-TOKEN-VALUE\nexit 0\n")
    gh.chmod(0o755)
    assert "SECRET-TOKEN-VALUE" not in json.dumps(re_.check_gh([str(gh)], dict(os.environ)))


# --- the self-test refuses to go on -------------------------------------------------------------


CHECK_NAMES = ("hidden", "gh", "gh-shim", "hosts", "sealed", "environment")


def good_report():
    return [{"name": n, "ok": True, "detail": ""} for n in CHECK_NAMES]


def completed(stdout="", rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


def run_self_test(tmp_path, run, which=lambda name: "/usr/bin/bwrap"):
    return re_.sandbox_self_test(
        sandbox_for(tmp_path), scratch_for(tmp_path), {"PATH": "/bin"}, run=run, which=which
    )


def test_the_self_test_refuses_when_bwrap_is_missing(tmp_path):
    def run(*a, **k):
        raise AssertionError("nothing may be started without bwrap")

    checks = run_self_test(tmp_path, run, which=lambda name: None)
    bad = [c for c in checks if not c["ok"]]
    assert bad and "bubblewrap" in bad[0]["detail"] and "not installed" in bad[0]["detail"]


def test_the_self_test_refuses_when_bwrap_cannot_start(tmp_path):
    msg = "bwrap: No permissions to create new namespace"
    checks = run_self_test(tmp_path, lambda *a, **k: completed("", 1, msg))
    bad = [c for c in checks if not c["ok"]]
    assert bad and msg in bad[0]["detail"]


def test_the_self_test_refuses_when_the_probe_found_a_readable_hidden_path(tmp_path):
    report = good_report()
    report[0] = {"name": "hidden", "ok": False, "detail": "/home/x/.env is readable"}
    checks = run_self_test(tmp_path, lambda *a, **k: completed(json.dumps(report)))
    assert [c["name"] for c in checks if not c["ok"]] == ["hidden"]


def test_the_self_test_refuses_an_answer_it_cannot_read(tmp_path):
    for out in ("", "not json", json.dumps({"ok": True}), json.dumps([])):
        checks = run_self_test(tmp_path, lambda *a, _o=out, **k: completed(_o))
        assert any(not c["ok"] for c in checks), out


def test_the_self_test_passes_a_clean_report_and_runs_the_probe_in_the_sandbox(tmp_path):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return completed(json.dumps(good_report()))

    checks = run_self_test(tmp_path, run)
    assert all(c["ok"] for c in checks)
    box = sandbox_for(tmp_path)
    assert seen["cmd"][: len(box.args(tmp_path))][0] == "/usr/bin/bwrap"
    assert "--sandbox-probe" in seen["cmd"] and "--die-with-parent" in seen["cmd"]
    # the probe sees the environment an agent gets, not the caller's
    assert seen["env"]["GH_PROMPT_DISABLED"] == "1"


def test_the_self_test_does_not_hang_on_a_stuck_probe(tmp_path):
    def run(cmd, **kw):
        assert kw["timeout"] > 0
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    checks = run_self_test(tmp_path, run)
    assert any(not c["ok"] and "time" in c["detail"] for c in checks)


def isolated_main(monkeypatch, tmp_path, checks):
    ran = []
    monkeypatch.setattr(re_, "ROOT", tmp_path)
    (tmp_path / "evals" / "cases").mkdir(parents=True)
    case = {"id": "c", "prompt": "p", "expect": [{"kind": "final", "pattern": "x"}]}
    (tmp_path / "evals" / "cases" / "c.json").write_text(json.dumps(case))
    monkeypatch.setattr(re_, "make_sandbox", lambda *a, **k: sandbox_for(tmp_path))
    monkeypatch.setattr(re_, "sandbox_self_test", lambda *a, **k: checks)
    monkeypatch.setattr(re_, "run_case", lambda *a, **k: ran.append(a) or pytest.fail("ran"))
    return ran


def test_the_runner_refuses_to_run_a_case_when_the_self_test_fails(monkeypatch, tmp_path, capsys):
    bad = [{"name": "hidden", "ok": False, "detail": "/home/x/.env is readable"}]
    ran = isolated_main(monkeypatch, tmp_path, bad)
    assert re_.main(["--case", "c"]) != 0
    assert ran == []
    err = capsys.readouterr()
    assert "/home/x/.env is readable" in err.out + err.err and "refus" in (err.out + err.err)


def test_the_runner_refuses_to_run_without_bwrap(monkeypatch, tmp_path, capsys):
    ran = isolated_main(monkeypatch, tmp_path, [])

    def missing(*a, **k):
        raise re_.SandboxError("bubblewrap (bwrap) is not installed")

    monkeypatch.setattr(re_, "make_sandbox", missing)
    assert re_.main(["--case", "c"]) != 0 and ran == []
    assert "bubblewrap (bwrap) is not installed" in capsys.readouterr().err


def test_make_sandbox_says_why_bwrap_is_missing(tmp_path):
    with pytest.raises(re_.SandboxError, match="not installed"):
        re_.make_sandbox(tmp_path, {"HOME": str(tmp_path)}, which=lambda name: None)


def test_the_runner_prints_each_self_test_line_before_the_first_case(monkeypatch, tmp_path, capsys):
    isolated_main(monkeypatch, tmp_path, good_report())
    passed = {
        "id": "c",
        "passed": True,
        "error": None,
        "seconds": 0,
        "cost_usd": 0,
        "skills": [],
        "mcp_calls": [],
        "web": [],
        "results": [],
    }
    monkeypatch.setattr(re_, "run_case", lambda *a, **k: passed)
    assert re_.main(["--case", "c"]) == 0
    out = capsys.readouterr().out
    assert out.index("sandbox") < out.index("PASS")
    for name in CHECK_NAMES:
        assert name in out


# --- every agent starts inside the sandbox -----------------------------------------------------


@pytest.mark.parametrize("agent", ["claude", "codex", "opencode", "pi", "gemini"])
def test_run_case_starts_every_agent_inside_the_sandbox(monkeypatch, tmp_path, agent):
    seen = {}
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"], cwd=kw["cwd"])
        # the agent calls gh: it finds the absent one, and the call is logged
        gh = real_run(["gh", "issue", "list"], env=kw["env"], capture_output=True, text=True)
        seen["gh"] = gh.returncode
        seen["config"] = Path(kw["env"]["GH_CONFIG_DIR"])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(re_.subprocess, "run", fake_run)
    out = tmp_path / "out"
    out.mkdir()
    real = a_gh(tmp_path / "real")
    box = sandbox_for(tmp_path, real_gh=[real])
    case = {"id": "c", "prompt": "hello", "expect": []}
    re_.run_case(agent, case, out, None, 1, sandbox=box)
    cmd = seen["cmd"]
    split = cmd.index("--")
    # the case's absent gh is bound over the real one; the shim sits in the case's scratch folder
    bound = [g for g in pairs(cmd[:split]) if g[0] == "--ro-bind" and g[2] == str(real)]
    assert len(bound) == 1 and bound[0][1].endswith("/bin/gh")
    # the shim and the case's folder (shim, gh configuration) are sealed: bound over themselves
    sealed = {g[1] for g in pairs(cmd[:split]) if g[0] == "--ro-bind" and g[1] == g[2]}
    shim = Path(bound[0][1])
    assert {str(shim), str(shim.parent.parent)} <= sealed
    assert cmd[split + 1] == agent and "hello" in cmd[split:]
    assert seen["gh"] == 127, "no gh fake: gh looks absent"
    assert not seen["config"].exists(), "the case's scratch folder is removed afterwards"
    assert seen["env"]["PWD"] == str(seen["cwd"])
    assert (out / f"{case['id']}.{agent}.1.gh-absent.jsonl").read_text().strip()


def test_a_case_with_a_gh_fake_binds_the_fake_over_the_real_gh(monkeypatch, tmp_path):
    seen = {}
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        bound = [g for g in pairs(cmd) if g[0] == "--ro-bind" and g[2] == str(real)]
        assert len(bound) == 1
        # what the sandbox mounts at the real gh's path is run here as the fake gh answers it
        seen["gh"] = real_run([bound[0][1], "auth", "status"], env=kw["env"])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    real = a_gh(tmp_path / "real")
    monkeypatch.setattr(re_.subprocess, "run", fake_run)
    out = tmp_path / "out"
    out.mkdir()
    case = {"id": "c", "prompt": "p", "expect": [], "setup": {"fakes": {"gh": {}}}}
    re_.run_case("codex", case, out, None, 1, sandbox=sandbox_for(tmp_path, real_gh=[real]))
    assert seen["gh"].returncode == 0, "the case's gh fake, not the absent gh (exit 127)"
    assert not list(out.glob("*.gh-absent.jsonl"))
    assert (out / "c.codex.1.gh.jsonl").read_text().strip()


def test_a_case_nobody_ran_gh_in_leaves_no_absent_gh_log(monkeypatch, tmp_path):
    monkeypatch.setattr(
        re_.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", "")
    )
    out = tmp_path / "out"
    out.mkdir()
    re_.run_case(
        "claude",
        {"id": "c", "prompt": "p", "expect": []},
        out,
        None,
        1,
        sandbox=sandbox_for(tmp_path),
    )
    assert not list(out.glob("*.gh-absent.jsonl"))


def test_no_case_runs_without_a_sandbox():
    # the sandbox is a required keyword: forgetting it is an error, not an unsandboxed run
    with pytest.raises(TypeError):
        re_.run_case("claude", {"id": "c", "prompt": "p", "expect": []}, Path("."), None, 1)


# --- the whole thing, in a real sandbox ---------------------------------------------------------


def bwrap_problem():
    """Why a real sandbox cannot run here, or None."""
    if not sys.platform.startswith("linux"):
        return "bubblewrap runs on Linux only"
    path = shutil.which("bwrap")
    if path is None:
        return "bubblewrap (bwrap) is not installed"
    probe = [path, "--bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-pid", "true"]
    p = subprocess.run(probe, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        return f"bwrap cannot start here: {p.stderr.strip()}"
    return None


@pytest.fixture
def real_bwrap():
    why = bwrap_problem()
    if why:
        print(f"SKIP real sandbox tests: {why}", file=sys.stderr)
        pytest.skip(why)
    return shutil.which("bwrap")


class Inside:
    """Run commands in a real sandbox over a temporary HOME holding canaries."""

    def __init__(self, bwrap, tmp_path, extra_env=None, base=None, login_path=None):
        """`login_path`: a folder the user's login shell puts first on PATH, as a machine whose
        profile rewrites PATH does."""
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        plant_home(self.home)
        if login_path is not None:
            (self.home / ".bash_profile").write_text(f'export PATH="{login_path}:$PATH"\n')
        environ = {"HOME": str(self.home), "PATH": os.environ["PATH"], **(extra_env or {})}
        self.box = re_.make_sandbox(tmp_path / "box", environ, which=lambda name: bwrap)
        self.scratch = scratch_for(tmp_path)
        self.shim = self.scratch.gh_bin / "gh"
        self.sealed = re_.case_sealed(self.scratch)
        self.env = re_.case_env(
            {},
            self.scratch,
            base={"PATH": os.environ["PATH"], "HOME": str(self.home), **(base or {})},
        )

    def use_fakes(self, case, fakes):
        """The case's fakes, as run_case sets them up: their gh answers, their folder is sealed."""
        self.env = re_.case_env(case, self.scratch, base=self.env, fakes=fakes)
        self.shim = re_.case_gh_shim(self.scratch, fakes)
        self.sealed = re_.case_sealed(self.scratch, fakes)

    def run(self, cmd, **kw):
        return subprocess.run(
            self.box.wrap(cmd, self.tmp, gh_shim=self.shim, sealed=self.sealed),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
            **kw,
        )


def test_a_canary_in_a_hidden_location_is_unreadable_inside(real_bwrap, tmp_path):
    hidden = tmp_path / "hidden-config"
    hidden.mkdir()
    (hidden / "hosts.yml").write_text(CONTENT)
    inside = Inside(real_bwrap, tmp_path, {"GH_CONFIG_DIR": str(hidden)})
    p = inside.run(["sh", "-c", f"cat {hidden}/hosts.yml; ls -A {hidden}"])
    assert p.stdout == "" and CONTENT not in p.stderr
    assert (hidden / "hosts.yml").read_text() == CONTENT, "hidden inside only, not removed"


def test_the_home_credential_files_read_empty_inside(real_bwrap, tmp_path):
    inside = Inside(real_bwrap, tmp_path)
    assert (inside.home / ".env").read_text() == CONTENT
    for f in (".env", ".git-credentials", ".netrc", ".config/git/credentials"):
        p = inside.run(["sh", "-c", f'cat "$HOME/{f}"'])
        assert p.stdout == "", f
    for d in (".config/gh", ".ssh", ".local/share/keyrings"):
        p = inside.run(["sh", "-c", f'ls -A "$HOME/{d}"'])
        assert p.stdout == "", d
    # a hidden file cannot be rewritten through the sandbox either
    inside.run(["sh", "-c", 'echo changed > "$HOME/.env"'])
    assert (inside.home / ".env").read_text() == CONTENT


@pytest.mark.skipif(not Path("/usr/bin/gh").exists(), reason="gh is not installed at /usr/bin/gh")
def test_the_real_gh_reports_no_login_inside(real_bwrap, tmp_path):
    inside = Inside(real_bwrap, tmp_path)
    p = inside.run(["/usr/bin/gh", "auth", "status"])
    assert p.returncode != 0
    # and with gh's own configuration folder variable gone, gh falls back to the hidden ~/.config/gh
    inside.env.pop("GH_CONFIG_DIR")
    assert inside.run(["/usr/bin/gh", "auth", "token"]).returncode != 0


def test_the_absent_gh_is_what_the_agent_finds_on_path(real_bwrap, tmp_path):
    inside = Inside(real_bwrap, tmp_path)
    p = inside.run(["sh", "-c", "gh auth status"])
    assert p.returncode == 127 and "command not found" in p.stderr


def test_a_login_shell_that_resets_path_still_finds_the_shim_inside(real_bwrap, tmp_path):
    real = a_gh(tmp_path / "realbin")
    inside = Inside(real_bwrap, tmp_path, login_path=real.parent)
    outside = subprocess.run(
        ["bash", "-lc", "command -v gh"],
        env={**os.environ, "HOME": str(inside.home)},
        capture_output=True,
        text=True,
    )
    assert outside.stdout.strip() == str(real), "the profile puts the real gh first, as intended"
    for cmd in (["bash", "-lc", "gh auth status"], ["sh", "-c", "gh auth status"]):
        p = inside.run(cmd)
        assert p.returncode == 127 and "command not found" in p.stderr, (cmd, p.stdout, p.stderr)
    found = inside.run(["bash", "-lc", "command -v gh"]).stdout.strip()
    assert found == str(real)
    p = inside.run([found, "auth", "status"])
    assert p.returncode == 127 and "command not found" in p.stderr and "REAL-GH" not in p.stdout
    assert "REAL-GH" in subprocess.run([real], capture_output=True, text=True).stdout, (
        "the real gh is replaced inside only"
    )
    assert (tmp_path / "gh-absent.jsonl").read_text().strip(), "the calls reach the shim's log"


@pytest.mark.skipif(not Path("/usr/bin/gh").exists(), reason="gh is not installed at /usr/bin/gh")
def test_the_gh_package_binary_is_the_shim_inside(real_bwrap, tmp_path):
    inside = Inside(real_bwrap, tmp_path)
    p = inside.run(["/usr/bin/gh", "auth", "status"])
    assert p.returncode == 127 and "command not found" in p.stderr, (p.stdout, p.stderr)
    p = inside.run(["sh", "-c", "exec $(readlink -f /usr/bin/gh) --version"])
    assert p.returncode == 127, "a link to gh leads to the shim as well"


def test_a_case_gh_fake_answers_a_real_gh_path_inside(real_bwrap, tmp_path):
    real = a_gh(tmp_path / "realbin")
    case = {"setup": {"fakes": {"gh": {}}}}
    inside = Inside(real_bwrap, tmp_path, login_path=real.parent)
    with re_.FakeServices(case, tmp_path, "c") as fakes:
        inside.env = re_.case_env(case, inside.scratch, base=inside.env, fakes=fakes)
        shim = fakes.gh_bin / "gh"
        sealed = re_.case_sealed(inside.scratch, fakes)
        cmd = inside.box.wrap(
            ["bash", "-lc", "gh auth status"], tmp_path, gh_shim=shim, sealed=sealed
        )
        p = subprocess.run(cmd, env=inside.env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and "REAL-GH" not in p.stdout, (p.stdout, p.stderr)
    assert (tmp_path / "c.gh.jsonl").read_text().strip(), "the fake logged the login-shell call"


# --- the sandbox's own files cannot be changed from inside --------------------------------------

# Ways a file could be rewritten through its real path; each runs as `sh -c OP sh PATH`.
FILE_OPS = {
    "append": 'echo planted >> "$1"',
    "truncate": ': > "$1"',
    "remove": 'rm "$1"',
    "rename": 'mv "$1" "$1.moved"',
    "replace": 'echo planted > "$1.new" && mv "$1.new" "$1"',
    "hard-link": 'ln "$1" "$1.link" && echo planted >> "$1.link"',
    "chmod": 'chmod 666 "$1"',
    "edit-in-place": 'sed -i "s/./X/" "$1"',
}
DIR_OPS = {
    "create": 'touch "$1/planted"',
    "mkdir": 'mkdir "$1/planted"',
    "rename": 'mv "$1" "$1.moved"',
    "remove": 'rm -rf "$1"',
    "replace": 'mv "$1" "$1.moved" && mkdir "$1" && touch "$1/planted"',
}


def tree(path: Path):
    """What a file or folder holds, for comparing before and after."""
    if path.is_dir():
        return sorted((str(p.relative_to(path)), tree(p)) for p in path.rglob("*"))
    return path.read_bytes()


class SealedWorld:
    """A real sandbox for a case with a gh fake, and the files its agent must not change."""

    def __init__(self, bwrap, tmp_path):
        self.case = {"setup": {"fakes": {"gh": {}}}}
        self.inside = Inside(bwrap, tmp_path)
        self.fakes = re_.FakeServices(self.case, tmp_path, "c")

    def __enter__(self):
        self.fakes.start()
        self.inside.use_fakes(self.case, self.fakes)
        scratch, fakes = self.inside.scratch, self.fakes
        self.files = {
            "hosts-file": self.inside.box.hosts_file,
            "absent-gh-shim": scratch.gh_bin / "gh",
            "absent-gh-script": scratch.gh_bin / "fake_gh.py",
            "fake-gh-shim": fakes.gh_bin / "gh",
            "fake-gh-script": fakes.gh_bin / "fake_gh.py",
            "fake-gh-config": fakes.folder / "gh-config.json",
        }
        self.dirs = {
            "scratch-folder": scratch.folder,
            "absent-gh-bin": scratch.gh_bin,
            "gh-config-folder": scratch.gh_config,
            "fakes-folder": fakes.folder,
            "fake-gh-bin": fakes.gh_bin,
        }
        return self

    def __exit__(self, *exc):
        self.fakes.stop()

    def seen(self):
        """What the agent sees: /etc/hosts and what `gh` answers (exit code and output)."""
        hosts = self.inside.run(["cat", "/etc/hosts"]).stdout
        gh = self.inside.run(["gh", "auth", "status"])
        return hosts, gh.returncode, gh.stdout

    def snapshot(self):
        return {name: tree(p) for name, p in {**self.files, **self.dirs}.items()}


@pytest.mark.parametrize("name", ["hosts-file", "absent-gh-shim", "absent-gh-script"])
@pytest.mark.parametrize("op", FILE_OPS)
def test_a_file_the_sandbox_relies_on_cannot_be_changed_through_its_real_path(
    real_bwrap, tmp_path, name, op
):
    with SealedWorld(real_bwrap, tmp_path) as world:
        path = world.files[name]
        before, seen = world.snapshot(), world.seen()
        p = world.inside.run(["sh", "-c", FILE_OPS[op], "sh", str(path)])
        assert p.returncode != 0, (op, name, p.stdout, p.stderr)
        assert world.snapshot() == before, f"{op} changed {name}"
        assert world.seen() == seen, f"{op} on {name} changed what the agent sees"


@pytest.mark.parametrize("name", ["fake-gh-shim", "fake-gh-script", "fake-gh-config"])
@pytest.mark.parametrize("op", FILE_OPS)
def test_a_file_the_fake_gh_relies_on_cannot_be_changed_through_its_real_path(
    real_bwrap, tmp_path, name, op
):
    with SealedWorld(real_bwrap, tmp_path) as world:
        path = world.files[name]
        before, seen = world.snapshot(), world.seen()
        assert seen[1] == 0, "the fake is signed in"
        p = world.inside.run(["sh", "-c", FILE_OPS[op], "sh", str(path)])
        assert p.returncode != 0, (op, name, p.stdout, p.stderr)
        assert world.snapshot() == before, f"{op} changed {name}"
        assert world.seen() == seen, f"{op} on {name} changed what gh answers"


@pytest.mark.parametrize("name", ["scratch-folder", "absent-gh-bin", "gh-config-folder"])
@pytest.mark.parametrize("op", DIR_OPS)
def test_a_folder_the_sandbox_relies_on_cannot_be_changed_through_its_real_path(
    real_bwrap, tmp_path, name, op
):
    with SealedWorld(real_bwrap, tmp_path) as world:
        path = world.dirs[name]
        before, seen = world.snapshot(), world.seen()
        p = world.inside.run(["sh", "-c", DIR_OPS[op], "sh", str(path)])
        assert p.returncode != 0, (op, name, p.stdout, p.stderr)
        assert world.snapshot() == before, f"{op} changed {name}"
        assert world.seen() == seen


@pytest.mark.parametrize("name", ["fakes-folder", "fake-gh-bin"])
@pytest.mark.parametrize("op", DIR_OPS)
def test_a_folder_the_fake_gh_relies_on_cannot_be_changed_through_its_real_path(
    real_bwrap, tmp_path, name, op
):
    with SealedWorld(real_bwrap, tmp_path) as world:
        path = world.dirs[name]
        before, seen = world.snapshot(), world.seen()
        p = world.inside.run(["sh", "-c", DIR_OPS[op], "sh", str(path)])
        assert p.returncode != 0, (op, name, p.stdout, p.stderr)
        assert world.snapshot() == before, f"{op} changed {name}"
        assert world.seen() == seen


def test_the_fake_gh_cannot_be_signed_out_from_inside(real_bwrap, tmp_path):
    with SealedWorld(real_bwrap, tmp_path) as world:
        config = world.files["fake-gh-config"]
        write = 'echo \'{"signed_in": false}\' > "$1"'
        assert world.inside.run(["sh", "-c", write, "sh", str(config)]).returncode != 0
        assert world.inside.run(["gh", "auth", "status"]).returncode == 0
        assert json.loads(config.read_text()) == {}


def test_nothing_in_the_path_first_folder_can_be_added_inside(real_bwrap, tmp_path):
    # a program planted next to `gh` would run in its place, or shadow another command
    with SealedWorld(real_bwrap, tmp_path) as world:
        plant = 'printf "#!/bin/sh\\necho planted\\n" > "$1/curl" && chmod +x "$1/curl"'
        first = world.inside.env["PATH"].split(os.pathsep)[0]
        assert first == str(world.fakes.gh_bin)
        assert world.inside.run(["sh", "-c", plant, "sh", first]).returncode != 0
        assert not (world.fakes.gh_bin / "curl").exists()


def test_the_hosts_file_seen_at_etc_hosts_still_maps_the_issue_hosts_after_an_attack(
    real_bwrap, tmp_path
):
    with SealedWorld(real_bwrap, tmp_path) as world:
        hosts = world.inside.box.hosts_file
        attack = 'echo "140.82.121.4 github.com api.github.com jira.ecmwf.int" > "$1"'
        assert world.inside.run(["sh", "-c", attack, "sh", str(hosts)]).returncode != 0
        mapped = world.inside.run(["cat", "/etc/hosts"]).stdout
        assert mapped == hosts.read_text()
        assert mapped.splitlines()[1].startswith("127.0.0.9 github.com")
        assert "140.82.121.4" not in mapped
        # and /etc/hosts itself is read-only
        assert world.inside.run(["sh", "-c", "echo x >> /etc/hosts"]).returncode != 0


def test_the_fake_gh_logs_stay_writable_from_the_shim_inside(real_bwrap, tmp_path):
    with SealedWorld(real_bwrap, tmp_path) as world:
        assert world.inside.run(["gh", "auth", "status"]).returncode == 0
        assert (tmp_path / "c.gh.jsonl").read_text().strip()


def test_the_issue_hosts_resolve_to_the_unused_address_and_refuse_inside(real_bwrap, tmp_path):
    inside = Inside(real_bwrap, tmp_path)
    code = (
        "import socket, sys\n"
        "for h in sys.argv[1:]:\n"
        "    addrs = {a[4][0] for a in socket.getaddrinfo(h, 443)}\n"
        "    s = socket.socket(); s.settimeout(5)\n"
        "    print(h, sorted(addrs), s.connect_ex((sorted(addrs)[0], 443)))\n"
    )
    p = inside.run([sys.executable, "-c", code, *HOSTS])
    assert p.returncode == 0, p.stderr
    lines = p.stdout.strip().splitlines()
    assert len(lines) == len(HOSTS)
    for host, line in zip(HOSTS, lines, strict=True):
        assert line == f"{host} ['127.0.0.9'] 111", line  # ECONNREFUSED


def test_no_forbidden_variable_is_visible_inside(real_bwrap, tmp_path):
    forbidden = {name: "a-real-looking-value" for name in FORBIDDEN}
    inside = Inside(real_bwrap, tmp_path, base=forbidden)
    case = {"setup": {"env": {"JIRA_PAT": "from-the-case"}}}
    inside.env = re_.case_env(case, inside.scratch, base={**inside.env, **forbidden})
    p = inside.run(["env"])
    names = {ln.split("=", 1)[0] for ln in p.stdout.splitlines()}
    assert not names & set(FORBIDDEN), names & set(FORBIDDEN)
    assert "a-real-looking-value" not in p.stdout and "from-the-case" not in p.stdout


def test_the_host_process_environments_are_not_readable_inside(real_bwrap, tmp_path):
    inside = Inside(real_bwrap, tmp_path)
    here = os.getppid()  # a process outside, whose environment holds whatever it was given
    p = inside.run(
        ["sh", "-c", f"cat /proc/{here}/environ | wc -c; ls /proc/1/ >/dev/null && echo ok"]
    )
    assert p.stdout.split()[0] == "0"


def test_the_self_test_passes_in_a_real_sandbox(real_bwrap, tmp_path):
    inside = Inside(real_bwrap, tmp_path)
    checks = re_.sandbox_self_test(inside.box, inside.scratch, inside.env)
    assert [c["name"] for c in checks] == list(CHECK_NAMES)
    assert all(c["ok"] for c in checks), checks


def test_the_self_test_passes_when_a_login_shell_finds_another_gh(real_bwrap, tmp_path):
    real = a_gh(tmp_path / "realbin")
    inside = Inside(real_bwrap, tmp_path, login_path=real.parent)
    assert real in inside.box.real_gh
    checks = re_.sandbox_self_test(inside.box, inside.scratch, inside.env)
    assert all(c["ok"] for c in checks), checks


def test_the_self_test_fails_when_a_gh_path_is_not_covered(real_bwrap, tmp_path):
    real = a_gh(tmp_path / "realbin")
    inside = Inside(real_bwrap, tmp_path, login_path=real.parent)
    # a sandbox that does not know the gh its user's login shell finds
    blind = re_.Sandbox(real_bwrap, inside.home, inside.box.hosts_file, inside.box.environ)
    checks = re_.sandbox_self_test(blind, inside.scratch, inside.env)
    bad = {c["name"]: c["detail"] for c in checks if not c["ok"]}
    assert "gh-shim" in bad and str(real) in bad["gh-shim"] and "login shell" in bad["gh-shim"]


def test_the_self_test_fails_when_a_known_gh_binary_is_not_covered(
    real_bwrap, tmp_path, monkeypatch
):
    real = a_gh(tmp_path / "usr-bin")
    monkeypatch.setattr(re_, "GH_BINARY_CANDIDATES", (str(real),))
    inside = Inside(real_bwrap, tmp_path)
    blind = re_.Sandbox(real_bwrap, inside.home, inside.box.hosts_file, inside.box.environ)
    checks = re_.sandbox_self_test(blind, inside.scratch, inside.env)
    bad = {c["name"]: c["detail"] for c in checks if not c["ok"]}
    assert "gh-shim" in bad and str(real) in bad["gh-shim"]


def test_the_self_test_fails_when_a_hidden_location_is_not_hidden(real_bwrap, tmp_path):
    inside = Inside(real_bwrap, tmp_path)
    # a sandbox that forgets to hide: same probe, no hiding options
    leaky = re_.Sandbox(
        real_bwrap,
        inside.home,
        inside.box.hosts_file,
        inside.box.environ,
        real_gh=inside.box.real_gh,
    )
    leaky.hidden = lambda: []
    checks = re_.sandbox_self_test(leaky, inside.scratch, inside.env)
    bad = [c for c in checks if not c["ok"]]
    assert [c["name"] for c in bad] == ["hidden"] and ".env" in bad[0]["detail"]
    assert CONTENT not in bad[0]["detail"]


def test_the_self_test_fails_when_a_file_the_sandbox_relies_on_is_writable(real_bwrap, tmp_path):
    inside = Inside(real_bwrap, tmp_path)
    hosts, shim = inside.box.hosts_file, inside.shim
    before = (hosts.read_bytes(), shim.read_bytes())
    # a sandbox that forgets to seal: same probe, no sealing options
    leaky = re_.Sandbox(
        real_bwrap, inside.home, hosts, inside.box.environ, real_gh=inside.box.real_gh
    )
    leaky.seal_args = lambda *a, **k: []
    checks = re_.sandbox_self_test(leaky, inside.scratch, inside.env)
    bad = {c["name"]: c["detail"] for c in checks if not c["ok"]}
    assert list(bad) == ["sealed"], checks
    for path in (hosts, shim, inside.scratch.folder, inside.scratch.gh_config):
        assert f"{path} can be written" in bad["sealed"], bad["sealed"]
    assert (hosts.read_bytes(), shim.read_bytes()) == before, "the probe changes nothing"
    assert not [p for p in inside.scratch.folder.rglob("*") if "probe" in p.name]


def test_the_self_test_passes_with_every_sandbox_file_sealed(real_bwrap, tmp_path):
    inside = Inside(real_bwrap, tmp_path)
    checks = re_.sandbox_self_test(inside.box, inside.scratch, inside.env)
    sealed = next(c for c in checks if c["name"] == "sealed")
    assert sealed["ok"], sealed


# --- the tests' own environment -----------------------------------------------------------------


def test_every_test_runs_without_issue_service_credentials():
    assert not [n for n in os.environ if re_.env_name_forbidden(n) and n not in re_.RUNNER_SET_ENV]


def test_every_test_has_a_temporary_home_and_an_empty_gh_configuration(tmp_path_factory):
    home = Path(os.environ["HOME"])
    assert home == Path.home() and home.is_dir()
    assert tmp_path_factory.getbasetemp() in home.parents, home
    config = Path(os.environ["GH_CONFIG_DIR"])
    assert config.is_dir() and not list(config.iterdir())
    assert os.environ["GH_PROMPT_DISABLED"] == "1"


def test_a_gh_found_by_a_test_looks_absent():
    gh = shutil.which("gh")
    assert gh is not None and Path(gh).parent == Path(os.environ["PATH"].split(os.pathsep)[0])
    p = subprocess.run([gh, "auth", "status"], capture_output=True, text=True)
    assert p.returncode == 127 and "command not found" in p.stderr


def test_jira_in_a_test_points_at_a_closed_loopback_port():
    url = os.environ["ECMWF_WEATHER_JIRA_URL"]
    assert url.startswith("http://127.0.0.1:")
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", int(url.rsplit(":")[-1]))):
        pass


def test_the_test_environment_reaches_subprocesses():
    p = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ['HOME'], os.environ['GH_CONFIG_DIR'])"],
        capture_output=True,
        text=True,
    )
    assert p.stdout.split() == [os.environ["HOME"], os.environ["GH_CONFIG_DIR"]]
