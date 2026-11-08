#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""A fake JIRA server (Jira Server 9.12, REST API v2) with one project, AS, for evaluations and
tests. Standard library only; it serves nothing but loopback.

  fake_jira.py --log REQUESTS.jsonl [--seed ISSUES.json]

The first line on stdout is `FAKE_JIRA_URL=http://127.0.0.1:<port>` (the port is chosen by the
system), printed once the server listens. The issue types, create screens and components are
those of the live AS project, checked read-only against Jira Server 9.12.

  GET  /rest/api/2/serverInfo                          anonymous when no Authorization is sent
  GET  /rest/api/2/issue/createmeta/AS/issuetypes[/ID] paged `values`, as JIRA 9 pages them
  GET  /rest/api/2/search?jql=...                      a small subset of JQL (see search())
  GET  /rest/api/2/issue/KEY-OR-ID                     one issue the server holds
  POST /rest/api/2/issue                               201 {id, key, self}, or a JIRA-shaped 400

Everything but an anonymous serverInfo needs `Authorization: Bearer <TOKEN>`; TOKEN is a fixed
synthetic value the runner hands to the agent as JIRA_PAT. Any other token, or none, is a 401.

Log (JSON lines, one flush per line): `{"line": "<METHOD> <path>?<query>"}` for every request,
refused ones included, with the query percent-decoded so a pattern reads the JQL as written; and,
when a create was accepted, one more line whose text is
`CREATED key=AS-9001&project=AS&issuetype=Improvement&summary=S&labels=a,b&components=c&...`
ending in `description=D` (each value on one line, '&' written "and"), with the structured
`issue` beside it. Count attempts with `^POST /rest/api/2/issue`, issues made with
`^CREATED `. No header, no token and no sender is ever logged.

`--seed` is a JSON list of issues the server already holds (`summary`, `labels`, `components`,
`type`, `description`), keyed AS-9001... in order, so a case can have duplicates to find.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlsplit

# The one token the fake accepts. Plainly synthetic: it is not a secret and opens nothing real.
TOKEN = "eval-synthetic-jira-token"
API = "/rest/api/2/"
PROJECT = {"id": "10100", "key": "AS", "name": "Agent Skills (synthetic)"}
TYPES = (
    {"id": "1", "name": "Bug", "subtask": False},
    {"id": "2", "name": "New Feature", "subtask": False},
    {"id": "3", "name": "Task", "subtask": False},
    {"id": "4", "name": "Improvement", "subtask": False},
    {"id": "5", "name": "Sub-task", "subtask": True},
    {"id": "6", "name": "Epic", "subtask": False},
)
# One component per skills repository, named after it without the `ecmwf-` prefix; the ids are
# synthetic. Every create screen lists them as the allowed values of `components`.
COMPONENTS = (
    {"id": "10200", "name": "admin-skills"},
    {"id": "10201", "name": "weather-skills"},
    {"id": "10202", "name": "science-skills"},
)
# Keys and ids of the issues the server holds start here, far from the live project's numbers.
FIRST_KEY = 9001
FIRST_ID = 20001
FIELDS = (
    # (fieldId, name, required, hasDefaultValue, schema)
    ("project", "Project", True, False, {"type": "project"}),
    ("issuetype", "Issue Type", True, False, {"type": "issuetype"}),
    ("summary", "Summary", True, False, {"type": "string"}),
    # Required with no default, yet a create without it succeeds live: the server sets the
    # token's user. Listed in SERVER_FILLED so a create may leave it out.
    ("reporter", "Reporter", True, False, {"type": "user"}),
    ("components", "Component/s", False, False, {"type": "array", "items": "component"}),
    ("description", "Description", False, False, {"type": "string"}),
    ("fixVersions", "Fix Version/s", False, False, {"type": "array", "items": "version"}),
    ("priority", "Priority", False, True, {"type": "priority"}),
    ("labels", "Labels", False, False, {"type": "array", "items": "string"}),
    ("timetracking", "Time Tracking", False, False, {"type": "timetracking"}),
    ("attachment", "Attachment", False, False, {"type": "array", "items": "attachment"}),
    ("duedate", "Due Date", False, False, {"type": "date"}),
    ("issuelinks", "Linked Issues", False, False, {"type": "array", "items": "issuelinks"}),
    ("customfield_10410", "Epic Link", False, False, {"type": "any"}),
    ("assignee", "Assignee", False, True, {"type": "user"}),
)
SERVER_FILLED = ("reporter",)
# What a type adds to FIELDS, by type id: a Bug has environment and versions, a Sub-task needs a
# parent and an Epic an Epic Name.
TYPE_FIELDS = {
    "1": (
        ("environment", "Environment", False, False, {"type": "string"}),
        ("versions", "Affects Version/s", False, False, {"type": "array", "items": "version"}),
    ),
    "5": (("parent", "Parent", True, False, {"type": "issuelink"}),),
    "6": (("customfield_10411", "Epic Name", True, False, {"type": "string"}),),
}
SUMMARY_MAX = 255  # JIRA's limit on a summary
# A create body is a few KB; this keeps a runaway client from filling memory.
MAX_BODY = 1024 * 1024
# JIRA 9 serves at most this many values per page of createmeta, whatever maxResults asks.
MAX_PAGE = 100
DEFAULT_PAGE = 50


def screen(type_id: str) -> tuple:
    """The create screen of an AS issue type: FIELDS plus what the type adds."""
    return FIELDS + TYPE_FIELDS.get(type_id, ())


def blank(value) -> bool:
    """A required field's value that JIRA treats as missing: nothing, white space only, an empty
    list or object, or (a parent) an object with neither a key nor an id."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict) and ("key" in value or "id" in value):
        return not any(isinstance(value.get(k), str) and value[k].strip() for k in ("key", "id"))
    return isinstance(value, (list, dict)) and not value


def clean(value) -> str:
    """A value for a CREATED line: one line, no '&' (the field separator)."""
    return " ".join(str(value).replace("&", "and").split())


class State:
    """The issues the server holds, and the request log. One per server."""

    def __init__(self, log: Path | None, seed: list[dict]):
        self.lock = threading.Lock()
        self.log = log
        self.issues: list[dict] = []
        for item in seed:
            self.add(
                item["summary"],
                item.get("type", "Improvement"),
                list(item.get("labels", [])),
                list(item.get("components", [])),
                item.get("description", ""),
            )

    def add(self, summary, type_name, labels, components, description) -> dict:
        n = len(self.issues)
        issue = {
            "id": str(FIRST_ID + n),
            "key": f"{PROJECT['key']}-{FIRST_KEY + n}",
            "summary": summary.strip(),
            "type": type_name,
            "labels": labels,
            "components": components,
            "description": description,
        }
        self.issues.append(issue)
        return issue

    def write(self, *entries: dict) -> None:
        if self.log is None:
            return
        with self.log.open("a", encoding="utf-8") as f:
            f.write("".join(json.dumps(e) + "\n" for e in entries))


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeJira/1"
    state: State  # set on the subclass made in make_server

    def log_message(self, *_args) -> None:
        """The base class logs to stderr with the client address; the request log is ours."""

    # --- plumbing ---------------------------------------------------------------------------

    def send_json(self, doc, status: int = 200) -> None:
        data = json.dumps(doc).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def error(self, status: int, message: str = "", errors: dict | None = None) -> None:
        self.send_json(
            {"errorMessages": [message] if message else [], "errors": errors or {}}, status
        )

    def handle_any(self) -> None:
        url = urlsplit(self.path)
        query = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.state.write({"line": f"{self.command} {url.path}"})
            return self.error(413, "Request body too large")
        body = self.rfile.read(length) if length else b""
        decoded = unquote_plus(url.query)
        self.state.write(
            {"line": f"{self.command} {url.path}" + (f"?{decoded}" if decoded else "")}
        )
        route = url.path[len(API) :].strip("/").split("/") if url.path.startswith(API) else None
        auth = self.headers.get("Authorization")
        if auth is not None and auth.strip() != f"Bearer {TOKEN}":
            return self.error(401, "You are not authenticated. Authentication required.")
        if route == ["serverInfo"] and self.command == "GET":
            return self.send_json(
                {
                    "baseUrl": f"http://{self.headers.get('Host')}",
                    "version": "9.12.40",
                    "versionNumbers": [9, 12, 40],
                    "deploymentType": "Server",
                    "serverTitle": "Fake JIRA (synthetic)",
                }
            )
        if auth is None:
            return self.error(401, "You are not authenticated. Authentication required.")
        if route is None:
            return self.error(404, "not found")
        if route == ["issue"]:
            if self.command != "POST":
                return self.error(405, "Method not allowed")
            return self.create(body)
        if self.command not in ("GET", "HEAD"):
            return self.error(405, "Method not allowed")
        if route == ["search"]:
            return self.search(query)
        if len(route) == 2 and route[0] == "issue" and route[1] != "createmeta":
            return self.show(route[1])
        if (
            route[:2] == ["issue", "createmeta"]
            and len(route) in (4, 5)
            and route[3] == "issuetypes"
        ):
            return self.createmeta(route, query)
        return self.error(404, "not found")

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = handle_any

    # --- createmeta -------------------------------------------------------------------------

    def component(self, c: dict) -> dict:
        return {
            "self": f"http://{self.headers.get('Host')}{API}component/{c['id']}",
            "id": c["id"],
            "name": c["name"],
        }

    def page(self, values: list, query: dict) -> None:
        """A createmeta page, honouring startAt and maxResults as JIRA 9 does."""
        try:
            start = max(0, int(query.get("startAt") or 0))
            size = max(1, min(int(query.get("maxResults") or DEFAULT_PAGE), MAX_PAGE))
        except ValueError:
            return self.error(400, "startAt and maxResults must be integers")
        chunk = values[start : start + size]
        self.send_json(
            {
                "maxResults": size,
                "startAt": start,
                "total": len(values),
                "isLast": start + len(chunk) >= len(values),
                "values": chunk,
            }
        )

    def createmeta(self, route: list[str], query: dict) -> None:
        if route[2] not in (PROJECT["key"], PROJECT["id"]):
            return self.error(404, f"No project could be found with key or id '{route[2]}'.")
        if len(route) == 4:
            return self.page([dict(t) for t in TYPES], query)
        if route[4] not in {t["id"] for t in TYPES}:
            return self.error(404, f"No issue type with id '{route[4]}' in project AS.")
        fields = []
        for fid, name, required, default, schema in screen(route[4]):
            field = {
                "required": required,
                "schema": dict(schema),
                "name": name,
                "fieldId": fid,
                "hasDefaultValue": default,
                "operations": ["set"],
            }
            if fid == "components":
                field["allowedValues"] = [self.component(c) for c in COMPONENTS]
            fields.append(field)
        self.page(fields, query)

    # --- search and read --------------------------------------------------------------------

    def search(self, query: dict) -> None:
        """`jql` as the skill writes it: `project = "AS"`, `labels = "x"` (every one must be on
        the issue) and `summary ~ "words"` (every word must occur; a `\\"quoted phrase\\"` must
        occur whole). Other clauses are ignored. The key `AS` is a reserved JQL word: unquoted
        after `project =` it is a 400, as on the live host."""
        jql = query.get("jql", "").strip()
        if not jql:
            return self.error(400, "The JQL query is required.", {"jql": "The JQL is empty"})
        # String literals blanked to equal-length spaces: a word inside one is not JQL, and the
        # column of a reserved word stays that of the query as written.
        bare = re.sub(r'"(?:[^"\\]|\\.)*"', lambda m: " " * len(m.group()), jql)
        reserved = re.search(r"\bproject\s*=\s*(AS)\b", bare)
        if reserved:
            return self.error(
                400,
                "Error in the JQL Query: 'AS' is a reserved JQL word. You must surround it in "
                "quotation marks to use it in a query. "
                f"(line 1, character {reserved.start(1) + 1})",
            )
        # The project the query names, found outside string literals, read from the query itself.
        clause = re.search(r"\bproject\s*=", bare)
        project = re.match(r'\s*"?(\w+)"?', jql[clause.end() :]) if clause else None
        labels = re.findall(r'\blabels\s*=\s*"([^"]+)"', jql)
        texts = [
            m.replace('\\"', '"').lower()
            for m in re.findall(r'\bsummary\s*~\s*"((?:[^"\\]|\\.)*)"', jql)
        ]
        found = []
        for issue in self.state.issues:
            if project and project.group(1).upper() != PROJECT["key"]:
                continue
            if any(lb not in issue["labels"] for lb in labels):
                continue
            summary = issue["summary"].lower()
            if not all(self.text_matches(t, summary) for t in texts):
                continue
            found.append(
                {
                    "id": issue["id"],
                    "key": issue["key"],
                    "self": f"http://{self.headers.get('Host')}{API}issue/{issue['id']}",
                    "fields": {
                        "summary": issue["summary"],
                        "status": {"name": "To Do"},
                        "labels": issue["labels"],
                    },
                }
            )
        try:
            limit = max(1, min(int(query.get("maxResults") or DEFAULT_PAGE), MAX_PAGE))
        except ValueError:
            limit = DEFAULT_PAGE
        self.send_json(
            {"startAt": 0, "maxResults": limit, "total": len(found), "issues": found[:limit]}
        )

    @staticmethod
    def text_matches(text: str, summary: str) -> bool:
        """A quoted phrase (`"exact words"`, written with escaped quotes) occurs whole; plain
        text matches when every word occurs."""
        if len(text) > 1 and text.startswith('"') and text.endswith('"'):
            return text.strip('"') in summary
        return all(word in summary for word in text.split())

    def show(self, key_or_id: str) -> None:
        for issue in self.state.issues:
            if key_or_id in (issue["key"], issue["id"]):
                return self.send_json(
                    {
                        "id": issue["id"],
                        "key": issue["key"],
                        "self": f"http://{self.headers.get('Host')}{API}issue/{issue['id']}",
                        "fields": {
                            "summary": issue["summary"],
                            "description": issue["description"],
                            "labels": issue["labels"],
                            "components": [{"name": n} for n in issue["components"]],
                            "issuetype": {"name": issue["type"]},
                            "project": {"key": PROJECT["key"]},
                            "status": {"name": "To Do"},
                        },
                    }
                )
        self.error(404, "Issue Does Not Exist")

    # --- create -----------------------------------------------------------------------------

    def create(self, raw: bytes) -> None:
        """`POST /issue`: validate the way JIRA does against the create screen of the issue
        type; on success hold the issue and log one CREATED line."""
        try:
            doc = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            doc = None
        fields = doc.get("fields") if isinstance(doc, dict) else None
        if not isinstance(fields, dict):
            return self.error(400, "Request body must be a JSON object with `fields`.")
        errors: dict[str, str] = {}
        itype = fields.get("issuetype")
        issue_type = None
        if isinstance(itype, dict):
            for t in TYPES:
                if str(itype.get("id")) == t["id"] or (
                    "id" not in itype and str(itype.get("name", "")).lower() == t["name"].lower()
                ):
                    issue_type = t
                    break
        if issue_type is None:
            errors["issuetype"] = (
                "issue type is required" if not itype else "issue type is not valid"
            )
        fields_on_screen = screen(issue_type["id"] if issue_type else "")
        on_screen = {fid for fid, *_ in fields_on_screen}
        for name in fields:
            if name not in on_screen:
                errors[str(name)] = (
                    f"Field '{name}' cannot be set. It is not on the appropriate screen, "
                    "or unknown."
                )
        project = fields.get("project")
        if not (
            isinstance(project, dict)
            and (project.get("key") == PROJECT["key"] or project.get("id") == PROJECT["id"])
        ):
            errors["project"] = "project is required" if not project else "project is not valid"
        summary = fields.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            errors["summary"] = "You must specify a summary of the issue."
        elif len(summary) > SUMMARY_MAX:
            errors["summary"] = f"Summary must be less than {SUMMARY_MAX} characters."
        if "description" in fields and not isinstance(fields["description"], str):
            errors["description"] = "description must be a string"
        labels = fields.get("labels", [])
        if not isinstance(labels, list) or not all(
            isinstance(x, str) and x and not any(c.isspace() for c in x) for x in labels
        ):
            errors["labels"] = "labels must be a list of words without spaces"
        components = self.components(fields, errors)
        # What the type's screen requires on top: not the fields checked above, and not what
        # the server fills in itself (the reporter). A required field needs a value, not a key.
        checked = ("project", "issuetype", "summary", *SERVER_FILLED)
        for fid, name, required, default, _ in fields_on_screen:
            if required and not default and fid not in checked and blank(fields.get(fid)):
                errors[fid] = (
                    "Issue type is a sub-task but parent issue key or id not specified."
                    if fid == "parent"
                    else f"{name} is required."
                )
        parent = fields.get("parent")
        if "parent" not in errors and parent is not None:
            # The parent is an issue the server holds, given by its key or its id.
            if not isinstance(parent, dict):
                errors["parent"] = "parent must be an object with the issue key or id"
            elif not any(
                (isinstance(parent.get("key"), str) and parent["key"] == i["key"])
                or (isinstance(parent.get("id"), str) and parent["id"] == i["id"])
                for i in self.state.issues
            ):
                errors["parent"] = "Could not find issue by id or key."
        if errors:
            return self.error(400, errors=errors)
        assert issue_type is not None and isinstance(summary, str)
        with self.state.lock:
            issue = self.state.add(
                summary, issue_type["name"], list(labels), components, fields.get("description", "")
            )
            line = "CREATED " + "&".join(
                f"{k}={clean(v)}"
                for k, v in (
                    ("key", issue["key"]),
                    ("project", PROJECT["key"]),
                    ("issuetype", issue["type"]),
                    ("summary", issue["summary"]),
                    ("labels", ",".join(issue["labels"])),
                    ("components", ",".join(issue["components"])),
                    ("description", issue["description"]),
                )
            )
            self.state.write({"line": line, "issue": issue})
        self.send_json(
            {
                "id": issue["id"],
                "key": issue["key"],
                "self": f"http://{self.headers.get('Host')}{API}issue/{issue['id']}",
            },
            201,
        )

    @staticmethod
    def components(fields: dict, errors: dict) -> list[str]:
        """The names of the components a create names (by name, or by id when no name is
        given); an unknown one is an error."""
        names: list[str] = []
        if "components" not in fields:
            return names
        given = fields["components"]
        shape = "components must be a list of objects with a name or an id"
        if not isinstance(given, list) or not all(isinstance(c, dict) for c in given):
            errors["components"] = shape
            return names
        for c in given:
            known = next(
                (
                    k
                    for k in COMPONENTS
                    if ("name" in c and c["name"] == k["name"])
                    or ("name" not in c and "id" in c and c["id"] == k["id"])
                ),
                None,
            )
            if known:
                names.append(known["name"])
            elif "name" in c:
                errors["components"] = f"Component name '{c['name']}' is not valid"
            elif "id" in c:
                errors["components"] = f"Component id '{c['id']}' is not valid"
            else:
                errors["components"] = shape
        return names


def make_server(log: Path | None = None, seed: list[dict] | None = None) -> ThreadingHTTPServer:
    """A server on 127.0.0.1 with a port chosen by the system (not yet serving)."""
    handler = type("FakeJiraHandler", (Handler,), {"state": State(log, seed or [])})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--log", type=Path, help="request log (JSON lines), created empty")
    ap.add_argument("--seed", type=Path, help="JSON list of issues the server already holds")
    a = ap.parse_args(argv)
    if a.log:
        a.log.parent.mkdir(parents=True, exist_ok=True)
        a.log.touch()
    seed = json.loads(a.seed.read_text(encoding="utf-8")) if a.seed else []
    server = make_server(a.log, seed)
    print(f"FAKE_JIRA_URL=http://127.0.0.1:{server.server_address[1]}", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
