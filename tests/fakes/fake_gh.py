#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""A fake `gh` for evaluations and tests: answers from a synthetic configuration, touches no
network and never signs in.

  fake_gh.py --config CONFIG.json --log CALLS.jsonl ARGS...

The runner installs a one-line shim named `gh` that runs this script with its own `--config` and
`--log`. Without `--config` the fake looks like a machine that has no `gh`: exit 127 and
"gh: command not found". The call is logged either way.

It answers `--version`, `auth status`, `api user`, `api repos/OWNER/REPO` (with the permissions
of the configured account), `issue list`, `label list` and `issue create`, for the one repository
it knows. Everything else — another repository, another command, an option it does not model —
is refused (exit 1) and logged.

Configuration (JSON object, every key optional):

  login         the signed-in account, "eval-colleague" by default
  signed_in     false: every command but `--version` asks for `gh auth login`
  access        false: the repository answers 404, as a private one does to a stranger
  issues        existing issues: {number, title, body, state, labels, createdAt}
  labels        the repository's labels, ["skill-feedback"] by default
  first_issue   the number of the first issue created, 1 by default (existing issues push it up)
  create        "ok" (default); "error": the create fails and nothing is created; "lost":
                the issue is created but the call fails, as when the answer is lost on the way

Log (JSON lines, appended per call): {"line": "gh <arguments>", "kind": "read" | "write",
"status": ...}. A call that created an issue also logs title, body (read from `--body-file`
when the call ran, since the agent may delete the file) and labels, and is followed by one line
{"line": "CREATED number=N&repo=R&title=T&labels=A,B&body=B", "kind": "result"}, each value
reduced to one line. Count attempts with `^gh issue create`, issues made with `^CREATED `.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

REPO = "ecmwf/ecmwf-weather-skills"
DEFAULT_LOGIN = "eval-colleague"
DEFAULT_LABELS = ("skill-feedback",)
# The `gh` that commands are for: any other repository is a different repository.
UNKNOWN_REPO = "GraphQL: Could not resolve to a Repository with the name '{}'. (repository)"
NOT_LOGGED_IN = (
    "To get started with GitHub CLI, please run:  gh auth login\n"
    "Alternatively, populate the GH_TOKEN environment variable with a GitHub API authentication "
    "token."
)
JSON_ISSUE_FIELDS = (
    "number",
    "title",
    "state",
    "labels",
    "url",
    "body",
    "createdAt",
    "updatedAt",
    "author",
)
JSON_LABEL_FIELDS = ("name", "color", "description")
DEFAULT_CREATED_AT = "2026-10-20T10:00:00Z"
# Same limit as `gh issue list`: the default page, and nothing here needs more.
DEFAULT_LIMIT = 30

# Commands that only read, as ("group", "subcommand"). Anything not listed is a write, so a
# command nobody classified fails a `gh_no_write` expectation rather than slipping through.
READ_COMMANDS = frozenset(
    {
        ("issue", "list"),
        ("issue", "view"),
        ("issue", "status"),
        ("pr", "list"),
        ("pr", "view"),
        ("pr", "status"),
        ("pr", "diff"),
        ("pr", "checks"),
        ("repo", "view"),
        ("repo", "list"),
        ("label", "list"),
        ("release", "view"),
        ("release", "list"),
        ("run", "view"),
        ("run", "list"),
        ("workflow", "view"),
        ("workflow", "list"),
        ("auth", "status"),
    }
)
READ_ONLY_GROUPS = frozenset({"search"})
INFO_ARGS = frozenset({"--version", "--help", "-h", "help"})
# `gh api` options that carry a request body (so the call is a POST unless told otherwise).
API_BODY_OPTIONS = ("-f", "-F", "--field", "--raw-field", "--input")
API_VALUE_OPTIONS = ("-X", "--method", "-H", "--header", "-q", "--jq", "--hostname", "--cache")
API_SHORT_VALUE = "XHFfqt"


class Refused(Exception):
    """A call the fake does not model; the message is what gh would say."""

    def __init__(self, message: str, status: str = "refused", code: int = 1):
        super().__init__(message)
        self.status, self.code = status, code


# --- classifying a call ----------------------------------------------------------------------


def _api_method(args: list[str]) -> str:
    """The HTTP method `gh api` uses: the explicit one (`-X POST`, `-XPOST`, `--method=post`),
    else POST when a body option is given, as gh does, and GET otherwise."""
    method, body, i = None, False, 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg.startswith("--") and len(arg) > 2:
            name, eq, value = arg.partition("=")
            if name in API_VALUE_OPTIONS or name in API_BODY_OPTIONS:
                if not eq:
                    value, i = (args[i] if i < len(args) else ""), i + 1
                if name == "--method":
                    method = value.upper()
                if name in API_BODY_OPTIONS:
                    body = True
        elif arg.startswith("-") and len(arg) > 1:
            for j, letter in enumerate(arg[1:], 1):
                if letter in API_SHORT_VALUE:
                    value = arg[j + 1 :]
                    if not value:
                        value, i = (args[i] if i < len(args) else ""), i + 1
                    if letter == "X":
                        method = value.upper()
                    if letter in "Ff":
                        body = True
                    break
    return method or ("POST" if body else "GET")


def call_kind(argv: list[str]) -> str:
    """ "read" or "write" for a call (the arguments after `gh`)."""
    words = [a for a in argv if not a.startswith("-")]
    if not words:
        return "read" if argv and set(argv) <= INFO_ARGS else "write"
    group, sub = words[0], words[1] if len(words) > 1 else None
    if group == "api":
        return "read" if _api_method(argv[argv.index("api") + 1 :]) == "GET" else "write"
    if group in READ_ONLY_GROUPS or group == "help" or (group, sub) in READ_COMMANDS:
        return "read"
    return "write"


# --- parsing options -------------------------------------------------------------------------


def parse_options(
    args: list[str], valued: dict[str, str], switches: dict[str, str] | None = None
) -> tuple[dict[str, list[str]], list[str]]:
    """(options by name, positional words). `valued` maps an option (`--label`, `-l`) to its
    name; `switches` maps options without a value. A value can be glued on (`--label=x`, `-lx`)
    or follow. An option nobody listed is refused, as gh refuses an unknown flag."""
    switches = switches or {}
    found: dict[str, list[str]] = {}
    words: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            words.extend(args[i:])
            break
        if arg.startswith("--") or (arg.startswith("-") and len(arg) > 1):
            name, eq, value = arg.partition("=") if arg.startswith("--") else (arg[:2], "", arg[2:])
            if name in switches and not (value or eq):
                found.setdefault(switches[name], []).append("")
            elif name in valued:
                if not (eq or value):
                    if i >= len(args):
                        raise Refused(f"flag needs an argument: {name!r}")
                    value, i = args[i], i + 1
                found.setdefault(valued[name], []).append(value)
            else:
                raise Refused(f"unknown flag: {name}")
        else:
            words.append(arg)
    return found, words


def _one(found: dict[str, list[str]], name: str, default: str | None = None) -> str | None:
    return found[name][-1] if name in found else default


# --- the world of one call -------------------------------------------------------------------


def clean(value: object) -> str:
    """A value for the CREATED line: one line, no '&' (the field separator)."""
    return " ".join(str(value).replace("&", "and").split())


def _read_log(log: Path | None) -> list[dict]:
    entries: list[dict] = []
    if log is None or not log.is_file():
        return entries
    for ln in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entries.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return entries


class World:
    """The configuration of the case plus the issues created so far (read from the log, since
    every call is a new process)."""

    def __init__(self, config: dict, log: Path | None):
        self.config = config
        self.login = config.get("login", DEFAULT_LOGIN)
        self.signed_in = config.get("signed_in", True)
        self.access = config.get("access", True)
        self.labels = list(config.get("labels", DEFAULT_LABELS))
        self.issues = [dict(i) for i in config.get("issues", [])]
        for entry in _read_log(log):
            if isinstance(entry.get("created"), dict):
                self.issues.append(entry["created"])

    def next_number(self) -> int:
        taken = [i["number"] for i in self.issues]
        return max([*taken, self.config.get("first_issue", 1) - 1]) + 1

    def require_sign_in(self) -> None:
        if not self.signed_in:
            raise Refused(NOT_LOGGED_IN, "signed-out", 4)

    def require_repo(self, repo: str | None) -> None:
        """The repository a command is for: only the one the fake knows, and only with access."""
        if repo is None:
            raise Refused(
                "failed to run git: fatal: not a git repository (or any of the parent "
                "directories): .git (pass -R OWNER/REPO)"
            )
        if repo != REPO:
            raise Refused(UNKNOWN_REPO.format(repo))
        if not self.access:
            raise Refused(UNKNOWN_REPO.format(repo), "no-access")

    def row(self, issue: dict) -> dict:
        """An issue as `gh issue list --json` names its fields."""
        created = issue.get("createdAt", DEFAULT_CREATED_AT)
        return {
            "number": issue["number"],
            "title": issue.get("title", ""),
            "state": str(issue.get("state", "open")).upper(),
            "labels": [{"name": n} for n in issue.get("labels", [])],
            "url": f"https://github.com/{REPO}/issues/{issue['number']}",
            "body": issue.get("body", ""),
            "createdAt": created,
            "updatedAt": issue.get("updatedAt", created),
            "author": {"login": issue.get("author", self.login)},
        }


# --- commands --------------------------------------------------------------------------------


def _jq(expr: str, value: object) -> str:
    """The `--jq` filters agents use on small answers: `.` and dotted keys such as
    `.permissions.push`; a string prints raw, as gh prints it."""
    result = value
    if expr != ".":
        if not re.fullmatch(r"(\.[A-Za-z_][A-Za-z0-9_]*)+", expr):
            raise Refused(f"the fake gh supports only --jq . and --jq .key.key, not {expr!r}")
        for key in expr[1:].split("."):
            result = result.get(key) if isinstance(result, dict) else None
    return result if isinstance(result, str) else json.dumps(result)


def cmd_api(args: list[str], world: World) -> str:
    found, words = parse_options(
        args,
        {
            "-X": "method",
            "--method": "method",
            "-q": "jq",
            "--jq": "jq",
            "-H": "header",
            "--header": "header",
        },
        {"--paginate": "paginate", "--silent": "silent"},
    )
    if (_one(found, "method") or "GET").upper() != "GET":
        raise Refused("the fake gh used in this evaluation refuses writes (method)")
    if len(words) != 1:
        raise Refused("the fake gh answers `gh api` with exactly one endpoint")
    world.require_sign_in()
    endpoint = words[0].strip("/")
    if endpoint == "user":
        answer: dict = {"login": world.login, "id": 1, "type": "User"}
    elif endpoint.startswith("repos/"):
        repo = endpoint[len("repos/") :]
        if repo != REPO or not world.access:
            raise Refused("gh: Not Found (HTTP 404)", "refused" if repo != REPO else "no-access")
        push = bool(world.access)
        answer = {
            "full_name": REPO,
            "name": REPO.split("/")[1],
            "private": False,
            "has_issues": True,
            "permissions": {
                "admin": False,
                "maintain": False,
                "push": push,
                "triage": push,
                "pull": True,
            },
        }
    else:
        raise Refused(f"the fake gh does not answer the endpoint {endpoint!r}")
    jq = _one(found, "jq")
    return _jq(jq, answer) if jq is not None else json.dumps(answer)


def _search_terms(text: str) -> tuple[list[str], list[str]]:
    """(words, labels) of a GitHub search string: bare words (or "quoted phrases") that must
    occur in title or body, and `label:x`; other qualifiers (`is:open`, `in:title`) are ignored."""
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    words, labels = [], []
    for part in parts:
        qualifier, colon, value = part.partition(":")
        if colon and qualifier.isalpha() and " " not in qualifier:
            if qualifier == "label":
                labels.append(value)
            continue
        words.append(part.lower())
    return words, labels


def cmd_issue_list(args: list[str], world: World) -> str:
    found, words = parse_options(
        args,
        {
            "-R": "repo",
            "--repo": "repo",
            "-l": "label",
            "--label": "label",
            "-S": "search",
            "--search": "search",
            "-s": "state",
            "--state": "state",
            "--json": "json",
            "-L": "limit",
            "--limit": "limit",
            "-q": "jq",
            "--jq": "jq",
        },
    )
    if words:
        raise Refused(f"unknown arguments: {words}")
    world.require_sign_in()
    world.require_repo(_one(found, "repo"))
    fields = [f for f in (_one(found, "json") or "").split(",") if f]
    unknown = [f for f in fields if f not in JSON_ISSUE_FIELDS]
    if unknown:
        raise Refused(
            f'Unknown JSON field: "{unknown[0]}"\nAvailable fields:\n  '
            + "\n  ".join(JSON_ISSUE_FIELDS)
        )
    state = _one(found, "state", "open")
    needed = [x for v in found.get("label", []) for x in v.split(",") if x]
    terms, search_labels = _search_terms(_one(found, "search", "") or "")
    needed += search_labels
    rows = []
    for issue in sorted(world.issues, key=lambda i: i["number"]):
        row = world.row(issue)
        text = f"{row['title']}\n{row['body']}".lower()
        if state not in ("all", row["state"].lower()):
            continue
        if not set(needed) <= {x["name"] for x in row["labels"]}:
            continue
        if not all(t in text for t in terms):
            continue
        rows.append(row)
    rows = rows[: int(_one(found, "limit", str(DEFAULT_LIMIT)) or DEFAULT_LIMIT)]
    if not fields:
        return "\n".join(
            "\t".join(
                [
                    str(r["number"]),
                    r["state"],
                    r["title"],
                    ", ".join(x["name"] for x in r["labels"]),
                    r["createdAt"],
                ]
            )
            for r in rows
        )
    answer = [{f: r[f] for f in fields} for r in rows]
    jq = _one(found, "jq")
    return _jq(jq, answer) if jq is not None else json.dumps(answer)


def cmd_label_list(args: list[str], world: World) -> str:
    found, words = parse_options(
        args,
        {
            "-R": "repo",
            "--repo": "repo",
            "--json": "json",
            "-L": "limit",
            "--limit": "limit",
            "-S": "search",
            "--search": "search",
        },
    )
    if words:
        raise Refused(f"unknown arguments: {words}")
    world.require_sign_in()
    world.require_repo(_one(found, "repo"))
    fields = [f for f in (_one(found, "json") or "").split(",") if f]
    unknown = [f for f in fields if f not in JSON_LABEL_FIELDS]
    if unknown:
        raise Refused(
            f'Unknown JSON field: "{unknown[0]}"\nAvailable fields:\n  '
            + "\n  ".join(JSON_LABEL_FIELDS)
        )
    needle = (_one(found, "search", "") or "").lower()
    names = [n for n in world.labels if needle in n.lower()]
    if not fields:
        return "\n".join(f"{n}\t\t#ededed" for n in names)
    values = {"color": "ededed", "description": ""}
    return json.dumps([{f: (n if f == "name" else values[f]) for f in fields} for n in names])


def _read_body(path: str, stdin) -> str:
    if path == "-":
        return stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as e:
        reason = e.strerror.lower() if e.strerror else "cannot read"
        raise Refused(f"open {path}: {reason}", "error") from None


def cmd_issue_create(args: list[str], world: World, stdin, record: dict) -> str:
    """Create an issue: the body is read from `--body-file` now; the call is recorded in
    `record` (title, body, labels) for the log before anything can refuse it."""
    found, words = parse_options(
        args,
        {
            "-R": "repo",
            "--repo": "repo",
            "-t": "title",
            "--title": "title",
            "-b": "body",
            "--body": "body",
            "-F": "body_file",
            "--body-file": "body_file",
            "-l": "label",
            "--label": "label",
        },
    )
    if words:
        raise Refused(f"unknown arguments: {words}")
    title = _one(found, "title")
    if "body_file" in found and "body" in found:
        raise Refused("specify only one of `--body` or `--body-file`")
    body = _one(found, "body")
    if "body_file" in found:
        body = _read_body(_one(found, "body_file") or "", stdin)
    labels = [x for v in found.get("label", []) for x in v.split(",") if x]
    record.update(title=title, body=body, labels=labels, repo=_one(found, "repo"))
    world.require_sign_in()
    world.require_repo(_one(found, "repo"))
    if title is None or body is None:
        raise Refused(
            "must provide `--title` and `--body` (or `--fill` or `fill-first` or `--fillverbose`) "
            "when not running interactively"
        )
    for label in labels:
        if label not in world.labels:
            raise Refused(f"could not add label: '{label}' not found", "error")
    if world.config.get("create", "ok") == "error":
        raise Refused(
            "GraphQL: Something went wrong while executing your query. Please try again later.",
            "error",
        )
    number = world.next_number()
    record["number"] = number
    record["created"] = {"number": number, "title": title, "body": body, "labels": labels}
    if world.config.get("create", "ok") == "lost":
        raise Refused("error connecting to api.github.com: connection reset by peer", "lost")
    return f"https://github.com/{REPO}/issues/{number}"


def dispatch(args: list[str], world: World, stdin, record: dict) -> str:
    """The output of an answered call; raises Refused for everything else."""
    if args == ["--version"]:
        return "gh version 2.0.0 (fake, evaluation)"
    if args[:2] == ["auth", "status"]:
        _, words = parse_options(args[2:], {"--hostname": "host", "-h": "host"})
        if words:
            raise Refused(f"unknown arguments: {words}")
        if not world.signed_in:
            raise Refused(
                "You are not logged into any GitHub hosts. To log in, run: gh auth login",
                "signed-out",
            )
        return f"github.com\n  - Logged in to github.com account {world.login} (fake, evaluation)"
    if args[:1] == ["api"]:
        return cmd_api(args[1:], world)
    if args[:2] == ["issue", "list"]:
        return cmd_issue_list(args[2:], world)
    if args[:2] == ["label", "list"]:
        return cmd_label_list(args[2:], world)
    if args[:2] == ["issue", "create"]:
        return cmd_issue_create(args[2:], world, stdin, record)
    what = " ".join(a for a in args[:2] if not a.startswith("-")) or "(no command)"
    raise Refused(
        "the fake gh used in this evaluation answers only `--version`, `auth status`, `api user`, "
        f"`api repos/{REPO}`, `issue list`, `label list` and `issue create`; it refuses `{what}`"
    )


def log_line(args: list[str]) -> str:
    return " ".join(["gh", *(shlex.quote(a) for a in args)])


def write_log(log: Path | None, args: list[str], status: str, record: dict) -> None:
    """Append the call, and the issue it made, to the log in one write."""
    if log is None:
        return
    entry = {"line": log_line(args), "kind": call_kind(args), "status": status}
    entry.update(
        {k: v for k, v in record.items() if k in ("title", "body", "labels", "number", "repo")}
    )
    if "created" in record:
        entry["created"] = record["created"]
    text = json.dumps(entry) + "\n"
    if "created" in record:
        c = record["created"]
        line = (
            f"CREATED number={c['number']}&repo={REPO}&title={clean(c['title'])}"
            f"&labels={','.join(c['labels'])}&body={clean(c['body'])}"
        )
        text += json.dumps({"line": line, "kind": "result"}) + "\n"
    with log.open("a", encoding="utf-8") as f:
        f.write(text)


def main(argv: list[str] | None = None, stdin=None, stdout=None, stderr=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    config_path: Path | None = None
    log: Path | None = None
    # Only the leading --config/--log are the fake's own; the rest is the caller's `gh` line.
    while args and args[0].split("=")[0] in ("--config", "--log"):
        name, eq, value = args.pop(0).partition("=")
        if not eq:
            value = args.pop(0)
        if name == "--config":
            config_path = Path(value)
        else:
            log = Path(value)
    record: dict = {}
    if config_path is None:
        write_log(log, args, "absent", record)
        stderr.write("gh: command not found\n")
        return 127
    world = World(json.loads(config_path.read_text(encoding="utf-8")), log)
    try:
        out = dispatch(args, world, stdin, record)
    except Refused as e:
        write_log(log, args, e.status, record)
        stderr.write(f"{e}\n")
        return e.code
    write_log(log, args, "ok", record)
    if out:
        stdout.write(out if out.endswith("\n") else out + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
