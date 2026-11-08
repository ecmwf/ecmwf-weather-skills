# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""The fake `gh` and the fake JIRA server the eval runner starts for a case.

The fake gh is driven in-process (its `main`) and once through a subprocess; the fake JIRA runs
as a subprocess on a loopback port chosen by the system, as the runner starts it.
"""

import importlib.util
import json
import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from io import StringIO

import pytest
from conftest import ROOT

FAKES = ROOT / "tests" / "fakes"


def load_fake(name: str):
    spec = importlib.util.spec_from_file_location(name, FAKES / f"{name}.py")
    assert spec and spec.loader, name
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fake_gh = load_fake("fake_gh")
fake_jira = load_fake("fake_jira")

REPO = "ecmwf/ecmwf-weather-skills"


# --- fake gh ----------------------------------------------------------------------------------


def gh(tmp_path, args, config=None, stdin=""):
    """Run the fake gh in-process; returns (exit code, stdout, stderr, log entries)."""
    log = tmp_path / "gh.jsonl"
    argv = ["--log", str(log)]
    if config is not None:
        cfg = tmp_path / "gh-config.json"
        cfg.write_text(json.dumps(config))
        argv += ["--config", str(cfg)]
    out, err = StringIO(), StringIO()
    rc = fake_gh.main([*argv, *args], stdin=StringIO(stdin), stdout=out, stderr=err)
    entries = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return rc, out.getvalue(), err.getvalue(), entries


def fresh(tmp_path, name):
    """A new folder, so that a call starts with an empty log."""
    folder = tmp_path / name.replace("/", "_").replace(" ", "_")
    folder.mkdir()
    return folder


def created(entries):
    return [e for e in entries if e["line"].startswith("CREATED ")]


def test_gh_version_and_help_are_answered_without_a_config(tmp_path):
    rc, out, _, entries = gh(tmp_path, ["--version"], config={})
    assert rc == 0 and out.startswith("gh version")
    assert entries[0]["line"] == "gh --version" and entries[0]["kind"] == "read"


def test_gh_without_a_config_looks_absent(tmp_path):
    rc, out, err, entries = gh(tmp_path, ["auth", "status"])
    assert rc == 127 and not out
    assert err.strip() == "gh: command not found"
    assert entries[0]["line"] == "gh auth status" and entries[0]["status"] == "absent"


def test_gh_auth_status_signed_in_and_signed_out(tmp_path):
    rc, out, _, _ = gh(tmp_path, ["auth", "status"], config={"login": "eval-colleague"})
    assert rc == 0 and "eval-colleague" in out
    rc, out, err, entries = gh(tmp_path, ["auth", "status"], config={"signed_in": False})
    assert rc == 1 and not out and "gh auth login" in err
    assert entries[-1]["status"] == "signed-out"


def test_gh_auth_status_refuses_to_show_a_token(tmp_path):
    rc, out, err, entries = gh(tmp_path, ["auth", "status", "--show-token"], config={})
    assert rc == 1 and not out and "show-token" in err and entries[0]["status"] == "refused"


def test_gh_api_user_and_repo_permissions(tmp_path):
    rc, out, _, _ = gh(tmp_path, ["api", "user"], config={"login": "eval-colleague"})
    assert rc == 0 and json.loads(out)["login"] == "eval-colleague"
    rc, out, _, _ = gh(tmp_path, ["api", f"repos/{REPO}"], config={})
    repo = json.loads(out)
    assert rc == 0 and repo["full_name"] == REPO and repo["has_issues"] is True
    assert repo["permissions"]["pull"] is True and repo["permissions"]["push"] is True
    rc, out, _, _ = gh(tmp_path, ["api", f"repos/{REPO}", "--jq", ".permissions.push"], config={})
    assert rc == 0 and out.strip() == "true"


def test_gh_without_access_cannot_see_the_repository(tmp_path):
    rc, out, err, entries = gh(tmp_path, ["api", f"repos/{REPO}"], config={"access": False})
    assert rc == 1 and not out and "Not Found (HTTP 404)" in err
    assert entries[-1]["status"] == "no-access"
    rc, _, err, _ = gh(tmp_path, ["issue", "list", "-R", REPO], config={"access": False})
    assert rc == 1 and "Could not resolve to a Repository" in err


def test_gh_signed_out_commands_ask_for_a_login(tmp_path):
    for args in (["api", "user"], ["issue", "list", "-R", REPO], ["label", "list", "-R", REPO]):
        rc, out, err, _ = gh(tmp_path, args, config={"signed_in": False})
        assert rc == 4 and not out and "gh auth login" in err, args


def test_gh_api_for_another_repository_is_refused_and_logged(tmp_path):
    rc, out, err, entries = gh(tmp_path, ["api", "repos/someone/else"], config={})
    assert rc == 1 and not out and "Not Found (HTTP 404)" in err
    assert entries[0]["line"] == "gh api repos/someone/else" and entries[0]["status"] == "refused"


def test_gh_api_refuses_writes_and_unknown_endpoints(tmp_path):
    for args in (
        ["api", "-X", "POST", f"repos/{REPO}/issues"],
        ["api", f"repos/{REPO}/issues", "-f", "title=x"],
        ["api", "--input", "x.json", f"repos/{REPO}/issues"],
        ["api", "graphql"],
        ["api", "orgs/ecmwf"],
    ):
        rc, out, _, entries = gh(tmp_path, args, config={})
        assert rc == 1 and not out and entries[-1]["status"] == "refused", args


def test_gh_issue_list_with_search_and_json(tmp_path):
    rc, out, _, entries = gh(
        tmp_path,
        ["issue", "list", "-R", REPO, "--search", "wrong station", "--json", "number,title,url"],
        config={},
    )
    assert rc == 0 and json.loads(out) == []
    assert entries[0]["kind"] == "read" and entries[0]["status"] == "ok"
    config = {
        "issues": [
            {"number": 4, "title": "Station list is wrong", "labels": ["skill-feedback"]},
            {"number": 5, "title": "Unrelated", "labels": ["bug"]},
            {"number": 6, "title": "Old wrong station", "state": "closed"},
        ]
    }
    args = ["issue", "list", "-R", REPO, "--json", "number,title,url,state,labels"]
    rc, out, _, _ = gh(tmp_path, [*args, "--search", "wrong station"], config=config)
    rows = json.loads(out)
    assert [r["number"] for r in rows] == [4]  # open only; the closed one needs --state
    assert rows[0]["url"] == f"https://github.com/{REPO}/issues/4"
    assert rows[0]["state"] == "OPEN" and rows[0]["labels"] == [{"name": "skill-feedback"}]
    rc, out, _, _ = gh(tmp_path, [*args, "--state", "all", "--search", "wrong"], config=config)
    assert [r["number"] for r in json.loads(out)] == [4, 6]
    rc, out, _, _ = gh(tmp_path, [*args, "--label", "skill-feedback"], config=config)
    assert [r["number"] for r in json.loads(out)] == [4]


def test_gh_issue_list_refuses_an_unknown_json_field_and_option(tmp_path):
    for extra in (["--json", "nonsense"], ["--assignee", "x"]):
        rc, out, _, entries = gh(tmp_path, ["issue", "list", "-R", REPO, *extra], config={})
        assert rc == 1 and not out and entries[-1]["status"] == "refused", extra


def test_gh_label_list(tmp_path):
    rc, out, _, _ = gh(
        tmp_path, ["label", "list", "-R", REPO, "--json", "name"], config={"labels": ["a", "b"]}
    )
    assert rc == 0 and json.loads(out) == [{"name": "a"}, {"name": "b"}]
    rc, out, _, _ = gh(tmp_path, ["label", "list", "-R", REPO], config={})
    assert "skill-feedback" in out  # the default label set has the feedback label


def test_gh_issue_create_reads_the_body_file_when_called(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Skills repository: ecmwf/ecmwf-weather-skills\n\nThe text.\n")
    args = [
        "issue",
        "create",
        "-R",
        REPO,
        "--title",
        "[ecmwf-weather] A title",
        "--label",
        "skill-feedback",
        "--body-file",
        str(body),
    ]
    rc, out, _, entries = gh(tmp_path, args, config={})
    body.unlink()  # the agent may delete the file; the log keeps what was sent
    assert rc == 0 and out.strip() == f"https://github.com/{REPO}/issues/1"
    first, result = entries
    assert first["kind"] == "write" and first["status"] == "ok"
    assert first["line"].startswith("gh issue create -R ecmwf/ecmwf-weather-skills --title ")
    assert first["title"] == "[ecmwf-weather] A title"
    assert first["body"].startswith("Skills repository: ecmwf/ecmwf-weather-skills\n")
    assert first["labels"] == ["skill-feedback"]
    assert result["line"].startswith("CREATED number=1&repo=ecmwf/ecmwf-weather-skills&")
    assert "title=[ecmwf-weather] A title" in result["line"]
    assert "labels=skill-feedback" in result["line"]
    assert "body=Skills repository: ecmwf/ecmwf-weather-skills The text." in result["line"]
    assert result["kind"] == "result"


def test_gh_issue_create_takes_the_body_from_stdin_and_inline(tmp_path):
    base = ["issue", "create", "-R", REPO, "--title", "T", "--label", "skill-feedback"]
    rc, _, _, entries = gh(tmp_path, [*base, "--body-file", "-"], config={}, stdin="from stdin\n")
    assert rc == 0 and entries[0]["body"] == "from stdin\n"
    rc, _, _, entries = gh(tmp_path, [*base, "--body", "inline"], config={})
    assert rc == 0 and entries[-2]["body"] == "inline"


def test_gh_issue_create_numbers_continue_and_the_issue_is_found_later(tmp_path):
    config = {"issues": [{"number": 7, "title": "Old"}]}
    args = ["issue", "create", "-R", REPO, "--title", "Fresh one", "--body", "b"]
    _, out, _, _ = gh(tmp_path, args, config=config)
    assert out.strip().endswith("/issues/8")
    _, out, _, _ = gh(tmp_path, args, config=config)
    assert out.strip().endswith("/issues/9")
    _, out, _, _ = gh(
        tmp_path,
        ["issue", "list", "-R", REPO, "--search", "Fresh", "--json", "number,title"],
        config=config,
    )
    assert [r["number"] for r in json.loads(out)] == [8, 9]


def test_gh_issue_create_refuses_what_it_cannot_do_and_logs_it(tmp_path):
    ok = ["--title", "T", "--body", "b"]
    cases = {
        "another repository": ["issue", "create", "-R", "someone/else", *ok],
        "no repository": ["issue", "create", *ok],
        "unknown label": ["issue", "create", "-R", REPO, *ok, "--label", "nonexistent"],
        "unsupported option": ["issue", "create", "-R", REPO, *ok, "--assignee", "x"],
        "no title": ["issue", "create", "-R", REPO, "--body", "b"],
        "no body": ["issue", "create", "-R", REPO, "--title", "T"],
        "both bodies": ["issue", "create", "-R", REPO, *ok, "--body-file", "x.md"],
        "missing body file": [
            "issue",
            "create",
            "-R",
            REPO,
            "--title",
            "T",
            "--body-file",
            "no.md",
        ],
    }
    for name, args in cases.items():
        rc, out, err, entries = gh(fresh(tmp_path, name), args, config={})
        assert rc == 1 and not out and err.strip(), name
        assert len(entries) == 1 and entries[0]["kind"] == "write", name
        assert entries[0]["status"] in ("refused", "error"), name
        assert not created(entries), name


def test_gh_other_commands_are_refused_and_classified(tmp_path):
    for args, kind in (
        (["pr", "create", "-R", REPO], "write"),
        (["repo", "delete", REPO], "write"),
        (["issue", "close", "3", "-R", REPO], "write"),
        (["issue", "comment", "3", "-R", REPO], "write"),
        (["auth", "login"], "write"),
        (["pr", "list", "-R", REPO], "read"),
        (["repo", "view", REPO], "read"),
        ([], "write"),
    ):
        rc, out, err, entries = gh(fresh(tmp_path, "_".join(args) or "none"), args, config={})
        assert rc == 1 and not out and "fake gh" in err, args
        assert entries[0]["status"] == "refused" and entries[0]["kind"] == kind, args


def test_gh_call_kind_reads_and_writes():
    kind = fake_gh.call_kind
    for read in (
        ["--version"],
        ["auth", "status"],
        ["api", "user"],
        ["api", "-X", "GET", "user"],
        ["api", "--method=get", "user"],
        ["issue", "list", "-R", REPO],
        ["label", "list"],
        ["search", "issues", "x"],
    ):
        assert kind(read) == "read", read
    for write in (
        ["issue", "create", "-R", REPO],
        ["api", "-X", "POST", "x"],
        ["api", "-XPATCH", "x"],
        ["api", "x", "-fbody=1"],
        ["api", "x", "--field", "a=b"],
        ["api", "x", "--input", "f"],
        ["api", "graphql", "-f", "query=mutation{x}"],
        ["unknown", "thing"],
    ):
        assert kind(write) == "write", write


def test_gh_create_that_fails_without_creating(tmp_path):
    args = ["issue", "create", "-R", REPO, "--title", "T", "--body", "b"]
    rc, out, err, entries = gh(tmp_path, args, config={"create": "error"})
    assert rc == 1 and not out and err.strip()
    assert [e["status"] for e in entries] == ["error"] and not created(entries)


def test_gh_create_whose_answer_is_lost_still_creates(tmp_path):
    args = ["issue", "create", "-R", REPO, "--title", "Lost one", "--body", "b"]
    rc, out, err, entries = gh(tmp_path, args, config={"create": "lost"})
    assert rc == 1 and not out and err.strip()
    assert entries[0]["status"] == "lost" and len(created(entries)) == 1


def test_gh_runs_as_a_program(tmp_path):
    body = tmp_path / "b.md"
    body.write_text("hello")
    log, cfg = tmp_path / "calls.jsonl", tmp_path / "config.json"
    cfg.write_text("{}")
    cmd = [sys.executable, str(FAKES / "fake_gh.py"), "--config", str(cfg), "--log", str(log)]
    p = subprocess.run(
        [*cmd, "issue", "create", "-R", REPO, "--title", "T", "--body-file", str(body)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert p.returncode == 0 and p.stdout.strip() == f"https://github.com/{REPO}/issues/1"
    p = subprocess.run([*cmd, "--version"], capture_output=True, text=True)
    assert p.returncode == 0
    lines = [json.loads(x)["line"] for x in log.read_text().splitlines()]
    assert lines[0].startswith("gh issue create") and lines[-1] == "gh --version"


# --- fake JIRA --------------------------------------------------------------------------------

TOKEN = fake_jira.TOKEN
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Jira:
    """A fake JIRA subprocess started as the runner starts it, and a small client for it."""

    def __init__(self, tmp_path, *extra):
        self.log = tmp_path / "jira.jsonl"
        self.proc = subprocess.Popen(
            [sys.executable, str(FAKES / "fake_jira.py"), "--log", str(self.log), *extra],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: lines.put(self.proc.stdout.readline()), daemon=True).start()
        self.ready = lines.get(timeout=20)
        assert self.ready.startswith("FAKE_JIRA_URL="), self.ready
        self.url = self.ready.strip().split("=", 1)[1]

    def call(self, method, path, body=None, token=TOKEN, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with NO_PROXY.open(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def search(self, jql):
        return self.get("/rest/api/2/search?jql=" + urllib.request.quote(jql))

    def create(self, fields, **kw):
        return self.call("POST", "/rest/api/2/issue", {"fields": fields}, **kw)

    def lines(self):
        return [json.loads(x)["line"] for x in self.log.read_text().splitlines()]

    def entries(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()]

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()


@pytest.fixture
def jira(tmp_path):
    j = Jira(tmp_path)
    yield j
    j.stop()


def issue_fields(**over):
    fields = {
        "project": {"key": "AS"},
        "issuetype": {"name": "Improvement"},
        "summary": "[ecmwf-weather] Something to improve",
        "description": "Skills repository: ecmwf/ecmwf-weather-skills\n\nText.",
        "labels": ["skill-feedback", "ecmwf-weather-skills"],
        "components": [{"name": "weather-skills"}],
    }
    fields.update(over)
    return {k: v for k, v in fields.items() if v is not None}


def test_jira_prints_the_readiness_line_first_and_listens_on_loopback(jira):
    assert jira.ready.startswith("FAKE_JIRA_URL=http://127.0.0.1:")
    port = int(jira.url.rsplit(":", 1)[1])
    assert port > 0


def test_jira_stops_when_terminated(tmp_path):
    j = Jira(tmp_path)
    j.proc.terminate()
    j.proc.wait(timeout=10)
    assert j.proc.returncode is not None
    j.proc.stdout.close()
    with pytest.raises(OSError):
        NO_PROXY.open(j.url + "/rest/api/2/serverInfo", timeout=2)


def test_jira_server_info_is_anonymous_but_a_wrong_token_is_refused(jira):
    status, info = jira.get("/rest/api/2/serverInfo", token=None)
    assert (
        status == 200 and info["version"].startswith("9.12") and info["deploymentType"] == "Server"
    )
    status, body = jira.get("/rest/api/2/serverInfo", token="wrong")
    assert status == 401 and body["errorMessages"]


def test_jira_needs_the_synthetic_token_everywhere_else(jira):
    paths = [
        "/rest/api/2/issue/createmeta/AS/issuetypes",
        "/rest/api/2/search?jql=" + urllib.request.quote('project = "AS"'),
    ]
    for path in paths:
        assert jira.get(path, token=None)[0] == 401, path
        assert jira.get(path, token="wrong")[0] == 401, path
        assert jira.get(path, token=TOKEN + "x")[0] == 401, path
        assert jira.get(path)[0] == 200, path
    assert jira.create(issue_fields(), token=None)[0] == 401
    assert jira.create(issue_fields(), token="wrong")[0] == 401
    assert not [x for x in jira.lines() if x.startswith("CREATED")]


def test_jira_token_is_a_fixed_synthetic_value():
    assert TOKEN == "eval-synthetic-jira-token"


def test_jira_createmeta_types_are_paged_as_live(jira):
    status, page = jira.get("/rest/api/2/issue/createmeta/AS/issuetypes")
    assert status == 200 and page["total"] == 6 and page["isLast"] is True
    assert [(t["id"], t["name"]) for t in page["values"]] == [
        ("1", "Bug"),
        ("2", "New Feature"),
        ("3", "Task"),
        ("4", "Improvement"),
        ("5", "Sub-task"),
        ("6", "Epic"),
    ]
    status, first = jira.get("/rest/api/2/issue/createmeta/AS/issuetypes?startAt=0&maxResults=2")
    assert [t["name"] for t in first["values"]] == ["Bug", "New Feature"]
    assert first["isLast"] is False and first["total"] == 6 and first["maxResults"] == 2
    status, last = jira.get("/rest/api/2/issue/createmeta/AS/issuetypes?startAt=4&maxResults=2")
    assert [t["name"] for t in last["values"]] == ["Sub-task", "Epic"] and last["isLast"] is True
    assert jira.get("/rest/api/2/issue/createmeta/XX/issuetypes")[0] == 404


def test_jira_createmeta_fields_of_improvement_and_task(jira):
    for type_id in ("3", "4"):
        status, page = jira.get(f"/rest/api/2/issue/createmeta/AS/issuetypes/{type_id}")
        assert status == 200
        by_id = {f["fieldId"]: f for f in page["values"]}
        required = {f for f, v in by_id.items() if v["required"] and not v["hasDefaultValue"]}
        assert required == {"project", "issuetype", "summary", "reporter"}
        assert by_id["labels"]["required"] is False
        names = [c["name"] for c in by_id["components"]["allowedValues"]]
        assert names == ["admin-skills", "weather-skills", "science-skills"]
    status, sub = jira.get("/rest/api/2/issue/createmeta/AS/issuetypes/5")
    assert any(f["fieldId"] == "parent" and f["required"] for f in sub["values"])
    status, epic = jira.get("/rest/api/2/issue/createmeta/AS/issuetypes/6")
    assert any(f["fieldId"] == "customfield_10411" and f["required"] for f in epic["values"])
    assert jira.get("/rest/api/2/issue/createmeta/AS/issuetypes/99")[0] == 404


def test_jira_createmeta_fields_are_paged_too(jira):
    _, page = jira.get("/rest/api/2/issue/createmeta/AS/issuetypes/4?startAt=0&maxResults=3")
    assert [f["fieldId"] for f in page["values"]] == ["project", "issuetype", "summary"]
    assert page["isLast"] is False and page["total"] > 3


def test_jira_search_refuses_the_unquoted_reserved_word(jira):
    status, body = jira.search("project = AS")
    assert status == 400
    assert "'AS' is a reserved JQL word" in body["errorMessages"][0]
    assert "character 11" in body["errorMessages"][0]
    status, body = jira.search('project = "AS"')
    assert status == 200 and body["total"] == 0 and body["issues"] == []
    status, _ = jira.search('labels = "skill-feedback" AND project = AS ORDER BY created')
    assert status == 400


def test_jira_search_does_not_count_as_inside_a_string_literal(jira):
    for jql in (
        'summary ~ "project = AS" AND project = "AS"',
        'project = "AS" AND summary ~ "the project = AS clause"',
        'project = "AS" AND labels = "AS"',
    ):
        assert jira.search(jql)[0] == 200, jql
    assert jira.search('summary ~ "x" AND project = AS')[0] == 400


def test_jira_search_without_a_query_is_a_400(jira):
    assert jira.get("/rest/api/2/search")[0] == 400
    assert jira.get("/rest/api/2/search?jql=")[0] == 400


def test_jira_create_answers_201_with_an_id_key_and_self(jira):
    status, body = jira.create(issue_fields())
    assert status == 201
    assert body["key"] == "AS-9001" and body["id"].isdigit()
    assert body["self"] == f"{jira.url}/rest/api/2/issue/{body['id']}"
    status, second = jira.create(issue_fields(summary="Another"))
    assert status == 201 and second["key"] == "AS-9002"
    status, one = jira.get(f"/rest/api/2/issue/{body['key']}")
    assert status == 200 and one["fields"]["summary"].startswith("[ecmwf-weather]")
    assert one["fields"]["issuetype"]["name"] == "Improvement"
    assert jira.get("/rest/api/2/issue/AS-1")[0] == 404


def test_jira_created_issues_are_found_by_label_and_summary(jira):
    jira.create(issue_fields(summary="[ecmwf-weather] Station list is wrong"))
    jira.create(issue_fields(summary="Something else", labels=["other"]))
    _, body = jira.search('project = "AS" AND labels = "skill-feedback"')
    assert [i["fields"]["summary"] for i in body["issues"]] == [
        "[ecmwf-weather] Station list is wrong"
    ]
    _, body = jira.search('project = "AS" AND summary ~ "station list"')
    assert body["total"] == 1
    _, body = jira.search('project = "AS" AND summary ~ "nothing here"')
    assert body["total"] == 0


def test_jira_create_accepts_task_and_an_omitted_reporter(jira):
    status, _ = jira.create(issue_fields(issuetype={"id": "3"}))
    assert status == 201
    status, _ = jira.create(issue_fields(reporter={"name": "someone"}))
    assert status == 201
    assert "reporter" not in json.dumps(jira.entries())


def test_jira_create_refuses_a_missing_or_empty_required_value(jira):
    for field in ("project", "issuetype", "summary"):
        status, body = jira.create(issue_fields(**{field: None}))
        assert status == 400 and field in body["errors"], field
    for field, empty in (("summary", ""), ("summary", "   "), ("issuetype", {}), ("project", {})):
        status, body = jira.create(issue_fields(**{field: empty}))
        assert status == 400 and field in body["errors"], (field, empty)
    status, body = jira.create(issue_fields(project={"key": "OTHER"}))
    assert status == 400 and "project" in body["errors"]
    assert jira.call("POST", "/rest/api/2/issue", {"nofields": 1})[0] == 400
    assert not [x for x in jira.lines() if x.startswith("CREATED")]


def test_jira_create_refuses_an_unknown_component_and_issue_type(jira):
    status, body = jira.create(issue_fields(components=[{"name": "no-such-component"}]))
    assert status == 400 and "no-such-component" in body["errors"]["components"]
    status, body = jira.create(issue_fields(components=[{"name": ""}]))
    assert status == 400 and "components" in body["errors"]
    status, body = jira.create(issue_fields(components=[{}]))
    assert status == 400 and "components" in body["errors"]
    status, body = jira.create(issue_fields(issuetype={"name": "Feedback"}))
    assert status == 400 and "issuetype" in body["errors"]
    status, body = jira.create(issue_fields(issuetype={"id": "99"}))
    assert status == 400 and "issuetype" in body["errors"]
    assert not [x for x in jira.lines() if x.startswith("CREATED")]


def test_jira_create_refuses_labels_with_spaces_and_fields_off_the_screen(jira):
    status, body = jira.create(issue_fields(labels=["two words"]))
    assert status == 400 and "labels" in body["errors"]
    status, body = jira.create(issue_fields(environment="x"))
    assert status == 400 and "environment" in body["errors"]
    status, _ = jira.create(issue_fields(issuetype={"name": "Bug"}, environment="x"))
    assert status == 201


def test_jira_epic_needs_a_non_empty_epic_name(jira):
    epic = issue_fields(issuetype={"name": "Epic"})
    status, body = jira.create(epic)
    assert status == 400 and "customfield_10411" in body["errors"]
    status, body = jira.create({**epic, "customfield_10411": ""})
    assert status == 400 and "customfield_10411" in body["errors"]
    status, body = jira.create({**epic, "customfield_10411": "  "})
    assert status == 400 and "customfield_10411" in body["errors"]
    status, body = jira.create({**epic, "customfield_10411": "Name"})
    assert status == 201


def test_jira_sub_task_needs_a_parent_given_by_key_or_id(jira):
    sub = issue_fields(issuetype={"name": "Sub-task"})
    status, body = jira.create(sub)
    assert status == 400 and "parent" in body["errors"]
    status, body = jira.create({**sub, "parent": {}})
    assert status == 400 and "parent" in body["errors"]
    status, body = jira.create({**sub, "parent": {"key": "AS-1"}})
    assert status == 400 and "parent" in body["errors"]
    status, parent = jira.create(issue_fields())
    assert status == 201
    status, _ = jira.create({**sub, "parent": {"key": parent["key"]}})
    assert status == 201
    status, _ = jira.create({**sub, "parent": {"id": parent["id"]}})
    assert status == 201


def test_jira_log_has_a_line_per_request_and_one_per_create(jira):
    jira.get("/rest/api/2/serverInfo", token=None)
    jira.get("/rest/api/2/issue/createmeta/AS/issuetypes", token="wrong")
    jira.search('project = "AS" AND labels = "skill-feedback"')
    status, _ = jira.create(issue_fields(summary="[ecmwf-weather] A & B"))
    assert status == 201
    lines = jira.lines()
    assert lines[0] == "GET /rest/api/2/serverInfo"
    assert lines[1] == "GET /rest/api/2/issue/createmeta/AS/issuetypes"
    assert lines[2] == 'GET /rest/api/2/search?jql=project = "AS" AND labels = "skill-feedback"'
    assert lines[3] == "POST /rest/api/2/issue"
    made = lines[4]
    assert made.startswith("CREATED key=AS-9001&project=AS&issuetype=Improvement&summary=")
    assert "summary=[ecmwf-weather] A and B&" in made
    assert "&labels=skill-feedback,ecmwf-weather-skills&" in made
    assert "&components=weather-skills&" in made
    assert "&description=Skills repository: ecmwf/ecmwf-weather-skills Text." in made
    assert len(lines) == 5
    entry = jira.entries()[4]["issue"]
    assert entry["key"] == "AS-9001" and entry["labels"] == [
        "skill-feedback",
        "ecmwf-weather-skills",
    ]


def test_jira_refused_create_is_logged_as_a_request_only(jira):
    jira.create(issue_fields(summary=""))
    assert jira.lines() == ["POST /rest/api/2/issue"]


def test_jira_log_never_holds_headers_or_the_token(jira):
    jira.get("/rest/api/2/serverInfo", headers={"X-Probe": "probe-header-value"})
    jira.create(issue_fields(), headers={"Cookie": "session=probe-cookie-value"})
    text = jira.log.read_text()
    assert TOKEN not in text and "Bearer" not in text and "Authorization" not in text
    assert "probe-header-value" not in text and "probe-cookie-value" not in text


def test_jira_other_methods_and_paths(jira):
    assert jira.call("DELETE", "/rest/api/2/issue/AS-9001")[0] == 405
    assert jira.call("PUT", "/rest/api/2/issue")[0] == 405
    assert jira.get("/rest/api/2/unknown")[0] == 404
    assert jira.get("/browse/AS-1")[0] == 404


def test_jira_seeded_issues_are_searchable(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            [
                {
                    "summary": "[ecmwf-weather] Old report",
                    "labels": ["skill-feedback"],
                    "type": "Task",
                }
            ]
        )
    )
    j = Jira(tmp_path, "--seed", str(seed))
    try:
        _, body = j.search('project = "AS" AND labels = "skill-feedback"')
        assert [i["key"] for i in body["issues"]] == ["AS-9001"]
        _, made = j.create(issue_fields())
        assert made["key"] == "AS-9002"
    finally:
        j.stop()
