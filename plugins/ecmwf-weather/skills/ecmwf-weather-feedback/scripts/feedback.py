#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Draft and privacy-scan ONE skill-feedback issue for ecmwf/ecmwf-weather-skills — standard
library only. RUN it, don't read it: SKILL.md says when and how.

  python3 feedback.py routes [--json]
  python3 feedback.py draft FILE [--title TEXT] [--accept TEXT]... [--json]
  python3 feedback.py scan FILE [--accept TEXT]... [--json]
  python3 feedback.py jira-draft FILE [--title TEXT] [--accept TEXT]... [--json]
  python3 feedback.py github-file FILE [--title TEXT] [--accept TEXT]... [--file-anyway] [--json]
  python3 feedback.py jira-check [--json]
  python3 feedback.py jira-file FILE [--title TEXT] [--accept TEXT]... [--file-anyway] [--json]

FILE is the user's own Markdown text; its first line may be `# Title`. Nothing is written and
nothing is sent by `routes`, `draft`, `scan` and `jira-draft`: they print what would be filed.

routes     which way the issue can go: GitHub (gh installed, signed in, can reach the repository),
           JIRA project AS with the user's token (JIRA_PAT is set — the name is reported, never
           the value), or the paste route.
draft      the GitHub issue: summary with the repository's prefix, label and description, whose
           first line says which skills repository it comes from. An unclean draft is refused.
scan       the privacy scan alone. It lists the kinds of detail found and the lines, never the
           values. Errors must be rewritten; the warnings (name-like pairs, GUIDs) can be
           accepted with --accept once checked, when they are a product, a place or a label.
jira-draft the paste route for ECMWF's JIRA project AS: project link, summary, labels, component,
           issue type and the description in JIRA wiki markup. The privacy scan runs again on
           the converted text.

github-file files the same draft as an issue on GitHub with the user's own signed-in `gh` (no token
            is handled here). It checks that gh is signed in, can reach the repository and that
            the label exists, searches for duplicates (a likely duplicate stops it unless
            --file-anyway; a search that fails stops it too, exit 2, nothing filed), runs ONE
            `gh issue create` and prints the issue's link. After a
            timeout or any unclear answer it never creates a second time: it searches by the
            exact title and reports. Run it once, only after the user's "yes, file it". gh's own
            output is never printed: it may hold an account name.
jira-check read-only: asks JIRA whether project AS can take the issue (issue types, required
           fields, labels, the component) and prints structure only — never issue contents.
jira-file  files the same issue in JIRA with the user's personal access token, read from the
           environment variable JIRA_PAT only (never an option, never printed). It checks the
           create screen, searches for duplicates (a likely duplicate stops it unless
           --file-anyway; a search that fails stops it too, exit 2, nothing filed), sends ONE
           create request and reads the issue back. After a timeout,
           a server error or an unclear answer it never sends a second create: it searches for
           the issue and reports. Run it once, only after the user's "yes, file it".
           ECMWF_WEATHER_JIRA_URL points both commands at a local fake (https or loopback only).

Exit codes: 0 ok/clean; 1 privacy findings or likely duplicates (nothing is filed; fix the draft
or look at the duplicates); 2 bad input, a service that cannot be reached, a duplicate search that
failed (nothing is filed) or an unclear answer;
3 BLOCKED by a missing dependency; 4 BLOCKED by access.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote, urlencode, urlsplit

# Never write __pycache__ into the installed skill (it may be read-only, and skills must not be
# modified).
sys.dont_write_bytecode = True

# --- the skills repository this copy belongs to ---------------------------------------------------
# Every issue says which skills repository it comes from, because JIRA project AS is shared by
# several. A sibling skills repository copies this block and changes these values only.
REPO = "ecmwf/ecmwf-weather-skills"
REPO_LABEL = "ecmwf-weather-skills"  # the JIRA label that marks the repository
LABEL = "skill-feedback"  # the GitHub label of every issue, and the first JIRA label
SUMMARY_PREFIX = "[ecmwf-weather]"  # starts every GitHub title and JIRA summary
JIRA_URL = "https://jira.ecmwf.int"  # ECMWF_WEATHER_JIRA_URL overrides it (tests and evals)
# The host override: https only, or loopback. It lets tests and eval runs point the filing
# commands at a local fake instead of the real server.
JIRA_URL_VAR = "ECMWF_WEATHER_JIRA_URL"
JIRA_PROJECT = "AS"  # the shared project "Agent Skills"
# The JIRA component of this repository: AS has one per skills repository, named after the
# repository without the `ecmwf-` prefix (`ecmwf-<x>-skills` is component `<x>-skills`).
JIRA_COMPONENT = "weather-skills"
# The issue type is read from the project at run time: the first of these that the project offers
# is used. Improvement first: users are asked to help improve the skills (workflow, wording and
# code, not only feedback). Task is the fallback should AS stop offering Improvement. AS has no
# Feedback type and will not get one: its issue type scheme is shared and managed by
# administrators.
JIRA_ISSUE_TYPES = ("Improvement", "Task")
# The user's own personal access token, read from this environment variable only: never an
# option, never printed, never in argv or a file.
JIRA_TOKEN_VAR = "JIRA_PAT"

# The sentence that tells the user what the draft leaves out. It is fixed so that evals and
# documentation tests can check it; it names kinds, never a name or a possessive.
LEFT_OUT_SENTENCE = (
    "The draft leaves out names, e-mail addresses, user names in file paths, keys and tokens."
)
# GitHub shows long titles cut off, and JIRA summaries stop at 255 characters including the prefix.
MAX_TITLE = 100
# gh talks to github.com; a minute would leave the agent waiting on a dead network.
GH_TIMEOUT_S = 30

EXIT_FINDINGS = 1
EXIT_BAD_INPUT = 2
EXIT_DEPENDENCY = 3
EXIT_ACCESS = 4


class InputError(Exception):
    """Bad input: exit code 2 with the message."""


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


# --- BLOCKED reports ------------------------------------------------------------------------------


def blocked(b: dict, as_json: bool = False, code: int = EXIT_ACCESS) -> int:
    """Report a barrier: what blocks, why, what the user must do, what the agent does next.

    Exit 4 for access, 3 for a missing dependency, 2 for an unreachable service (AGENTS.md
    "When access is blocked"). The report goes to stderr, and with `as_json` to stdout too."""
    lines = [f"BLOCKED: {b['blocked']}", f"Why: {b['why']}", "What the user needs to do:"]
    lines += [f"  {s}" for s in b["user_steps"]]
    lines.append(f"For the agent: {b['agent']}")
    print("\n".join(lines), file=sys.stderr)
    if as_json:
        print(json.dumps({"blocked": {**b, "code": code}}, indent=2))
    return code


def jira_project_url(host: str = JIRA_URL) -> str:
    """The project's page the user opens to paste an issue (the real host unless told otherwise)."""
    return f"{host}/projects/{JIRA_PROJECT}/"


def jira_paste_steps(draft_file: str | Path) -> list[str]:
    """The steps of the paste route, for every BLOCKED report on the JIRA route."""
    return [
        "Or, without a token, file it by hand:",
        f"  a. Run: python3 feedback.py jira-draft {shlex.quote(str(draft_file))} — it prints the "
        "summary, the labels, the component, the issue type and the description.",
        f"  b. Signed in to Jira on the ECMWF network or VPN, open {jira_project_url()}, create "
        f"an issue of the {JIRA_ISSUE_TYPES[0]} type ({JIRA_ISSUE_TYPES[1]} if the project has "
        f"none), paste them in and set the component {JIRA_COMPONENT}.",
    ]


# --- privacy scan ---------------------------------------------------------------------------------
# Feedback about a forecast needs coordinates, places and parameters, so those pass. What must not
# leave the user's machine is who they are and what they sign in with.

# Plain words for the user and the agent, one per kind: a refusal names kinds, never values.
PLAIN = {
    "email": "e-mail address",
    "credential": "key, token or password",
    "session-link": "sign-in or session link",
    "path": "user name in a file path",
    "name": "name",
    "identifier": "GUID",
}
WHY = {
    "email": "an e-mail address identifies a person — leave it out",
    "credential": "a key, token, password or API-key line — never in an issue; if it is real, "
    "revoke it (a commit or file hash: shorten it to 7-12 characters)",
    "session-link": "a link with a sign-in or session query string — drop the query string",
    "path": "a local path with a user name — use a generic path such as ~/data",
    "name": "looks like a person's name — describe the person by role; a product, place or "
    "label can be accepted with --accept once checked",
    "identifier": "a GUID can point at a person's records — drop it (accept it only when it is "
    "clearly not a person's)",
}
SEVERITY = {
    "email": "error",
    "credential": "error",
    "session-link": "error",
    "path": "error",
    "name": "warn",
    "identifier": "warn",
}

EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
# A value that is a placeholder, not a secret: <your key>, …, ***.
_VALUE = r"(?![<…*\s])"
CREDENTIALS = (
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),  # JWT
    re.compile(r"\b[0-9A-Fa-f]{32,}\b"),  # ECMWF/CDS API keys, session ids, hashes
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}=*"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # (A Jira personal access token and other bare base64 values: see `_bare_tokens`.)
    # ~/.cdsapirc and ~/.adsapirc: `key: <uid>:<uuid>` (or a bare uuid), with a digit in it.
    re.compile(rf"(?im)^[ \t]*key[ \t]*:[ \t]*{_VALUE}(?=\S*\d)\S{{8,}}"),
    re.compile(r"(?im)^[ \t]*key[ \t]*:[ \t]*(?:\d+:)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-\S+"),
    re.compile(
        r"\b\d{3,}:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    ),
    # ~/.ecmwfapirc and ~/.polytopeapirc style JSON lines: "key": "…", "email": "…".
    re.compile(
        r'(?i)"(?:key|api_key|user_key|apikey|token|access_token|password)"[ \t]*:[ \t]*"'
        rf'{_VALUE}[^"]{{6,}}"'
    ),
    re.compile(r'(?i)"(?:email|user_email)"[ \t]*:[ \t]*"[^"\s]+"'),
    # NAME=value and name: value for names that carry a secret (JIRA_PAT=…, ECMWF_API_KEY=…).
    re.compile(
        r"\b[A-Za-z][A-Za-z0-9_]*(?:KEY|TOKEN|PAT|SECRET|PASSWORD|PASSWD)[ \t]*=[ \t]*[\"']?"
        r"(?![<…*\s\"'$])[^\s\"',;]+"
    ),
    re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|token|pat|cookie)\b"
        r"[ \t]*[:=][ \t]*(?![<…*])(?=[^\s,;]*\d)(?=[^\s,;]*[A-Za-z])[^\s,;]{6,}"
    ),
)
# A run of the standard (`+` `/`) and URL-safe (`-` `_`) base64 alphabets, with its `=` padding.
TOKEN_RUN = re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}")
# A path or a URL is also a long run of these characters, but made of words: its letters rarely
# change case or turn into digits. Measured: random 44-character tokens change between about two
# neighbouring letters in three (a share of 0.65, spread 0.075), while paths and URLs with
# camel-case file names and dates stay at 0.2 or below. The threshold sits between them,
# four spreads under the tokens: of random 44-character tokens with a separator about one in
# twenty thousand falls below it and passes this detector (a token in a `key=` or `Bearer` form
# is caught by the others). It applies only when `/`, `-` or `_` is in the run: the alphabet
# without them has no path to tell apart.
TOKEN_MIN_CHANGES = 0.3


def _class_changes(run: str) -> float:
    """The share of neighbouring letters and digits that differ in kind (lower case, upper case,
    digit). `+` and the separators sit between and are not counted."""
    kinds = [
        ("U" if c.isupper() else "L" if c.islower() else "D") if c.isalnum() else "" for c in run
    ]
    # (itertools.pairwise and zip(strict=) need Python 3.10; this script also runs on 3.9.)
    pairs = [(kinds[i], kinds[i + 1]) for i in range(len(kinds) - 1) if kinds[i] and kinds[i + 1]]
    return sum(a != b for a, b in pairs) / len(pairs) if pairs else 0.0


def _bare_tokens(text: str) -> Iterable[tuple[int, int, str]]:
    """(start, end, text) of each bare token: 40 characters or more of base64 mixing upper case,
    lower case and digits, such as a Jira personal access token. Paths and URLs, which are made
    of words, are told apart by `_class_changes` and pass. A token-shaped part of a URL is
    found like any other run: it is bounded by the `.` or `:` that end the host."""
    for m in TOKEN_RUN.finditer(text):
        run = m.group(0).rstrip("=").strip("/-_")
        if len(run) < 40:
            continue
        if not (any(c.isupper() for c in run) and any(c.islower() for c in run)):
            continue
        if not any(c.isdigit() for c in run):
            continue
        if any(c in run for c in "/-_") and _class_changes(run) < TOKEN_MIN_CHANGES:
            continue
        yield m.start(), m.end(), m.group(0)


SESSION_LINK = re.compile(
    r"(?i)https?://[^\s)>\]]+\?[^\s)>\]]*\b(?:code|token|state|session|sessionid|sid|sig|"
    r"signature|samlrequest|samlresponse|access_token|id_token|returnurl|wctx|wresult|"
    r"key|api_key|apikey|auth|password)=[^\s)>\]]*"
)
# /home/<user>, /Users/<user>, C:\Users\<user>; a placeholder such as <user> or $USER passes.
PATHS = (
    re.compile(r"(?<![\w.-])(?:/home/|/Users/)(?![<$%{~])[^/\s]+"),
    re.compile(r"(?i)(?<![\w])[A-Z]:\\Users\\(?![<$%{~])[^\\\s]+"),
)
GUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
HONORIFIC = re.compile(r"\b(?:Mr|Mrs|Ms|Mx|Dr|Prof|Sir|Dame)\.?\s+[A-Z][^\W\d_]+")
WORD = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]{2,})?(?:-[^\W\d_]+)*")  # O'Brien, not 's
WORD_GAP = re.compile(r"[ \t]+|[ \t]*\r?\n[ \t]*")
# Capitalised words that are not names: sentence starters, the draft's own headings, the
# vocabulary of ECMWF data and of weather, and places. Lower case. A miss only costs an --accept.
# One string per line of words, so the list stays readable and easy to extend.
_NOT_NAME_LINES = (
    "a an the this that these those it its i we you he she they my our your his her their",
    "when where what which who why how if then else after before during while until on in at by",
    "for from to with without of and or but not no yes also only each every all any some one two",
    "three first second last next new old same other both there here now today yesterday",
    "tomorrow please note see use using used run runs running ran open opened opening select",
    "selected choose chose press enter type save saved send sent submit show shown view search",
    "find found check checked expected actual instead however steps step result results error",
    "errors warning success failed fails failure fix fixed proposed evidence lesson skill skills",
    "happened wrong stale fact faster path case user missing",
    "monday tuesday wednesday thursday friday saturday sunday mon tue wed thu fri sat sun",
    "january february march april may june july august september october november december",
    "jan feb mar apr jun jul aug sep sept oct nov dec week month year day days hour hours",
    "mr mrs ms mx dr prof sir dame",
    "ecmwf ifs aifs hres ens era reanalysis analysis open data climate store copernicus",
    "atmosphere monitoring service destination earth destine european centre medium range weather",
    "forecasts forecast ensemble control high resolution operational archive catalogue catalog",
    "request requests retrieval retrieve download downloads mars polytope earthkit python plugin",
    "agent claude codex opencode github jira task issue improvement label template",
    "sea level pressure temperature precipitation wind speed total cloud cover humidity",
    "geopotential height surface wave waves point model models field fields step grid gridpoint",
    "chart charts meteogram meteograms map maps plot plots parameter parameters time times",
    "stream streams type types levels layer layers product products licence terms access key",
    "token script scripts documentation reference references",
    "north south east west northern southern eastern western atlantic pacific indian arctic",
    "antarctic mediterranean baltic europe european africa asia america american australia",
    "united kingdom states germany italy france belgium spain portugal netherlands switzerland",
    "austria denmark sweden norway finland ireland poland greece turkey china japan india brazil",
    "canada mexico argentina chile",
    "york angeles francisco diego jose vegas orleans buenos aires janeiro paulo sao rio cape town",
    "hong kong city saint san santa los las rome madrid lisbon vienna berlin munich paris london",
    "reading bonn bologna brussels geneva darmstadt toulouse exeter oxford cambridge cologne",
    "dusseldorf düsseldorf heathrow gatwick amsterdam copenhagen oslo stockholm helsinki dublin",
    "athens istanbul beijing tokyo delhi mumbai sydney melbourne toronto",
)
NOT_NAMES = frozenset(word for line in _NOT_NAME_LINES for word in line.split())


def _example_domain(domain: str) -> bool:
    """True for a reserved example domain or one of its subdomains. A plain suffix test would
    also pass `notexample.com`, a real domain that identifies a person."""
    host = domain.lower()
    return any(host == d or host.endswith("." + d) for d in EXAMPLE_DOMAINS)


def _name_like(w: str) -> bool:
    return len(w) >= 2 and w[0].isupper() and any(c.islower() for c in w)


def _candidates(text: str) -> Iterable[tuple[str, int, int, str]]:
    """(kind, start, end, matched text) in priority order: errors first, then warnings."""
    # A link is one finding even when its query string holds a token.
    for m in SESSION_LINK.finditer(text):
        yield "session-link", m.start(), m.end(), m.group(0)
    for rx in CREDENTIALS:
        for m in rx.finditer(text):
            yield "credential", m.start(), m.end(), m.group(0)
    for start, end, shown in _bare_tokens(text):
        yield "credential", start, end, shown
    for rx in PATHS:
        for m in rx.finditer(text):
            yield "path", m.start(), m.end(), m.group(0)
    for m in EMAIL.finditer(text):
        if not _example_domain(m.group(0).split("@", 1)[1]):
            yield "email", m.start(), m.end(), m.group(0)
    for m in GUID.finditer(text):
        yield "identifier", m.start(), m.end(), m.group(0)
    for m in HONORIFIC.finditer(text):
        yield "name", m.start(), m.end(), " ".join(m.group(0).split())
    words = list(WORD.finditer(text))
    # (itertools.pairwise needs Python 3.10; this script also runs on the system Python 3.9.)
    for i in range(len(words) - 1):
        w1, w2 = words[i], words[i + 1]
        a, b = w1.group(0), w2.group(0)
        if (
            WORD_GAP.fullmatch(text[w1.end() : w2.start()])
            and _name_like(a)
            and _name_like(b)
            and a.lower() not in NOT_NAMES
            and b.lower() not in NOT_NAMES
        ):
            yield "name", w1.start(), w2.end(), f"{a} {b}"


def scan_text(text: str, accept: Iterable[str] = ()) -> list[dict]:
    """Privacy findings, one per span (a span is reported once, by its most severe detector).

    A finding carries its kind, plain-word kind, severity, line and why — never the matched
    text. Warnings whose text is in `accept` are dropped; errors never are."""
    accepted = set(accept)
    claimed: list[tuple[int, int]] = []
    out = []
    for kind, start, end, shown in _candidates(text):
        if any(start < e and s < end for s, e in claimed):
            continue
        claimed.append((start, end))
        if SEVERITY[kind] == "warn" and shown in accepted:
            continue
        out.append(
            {
                "kind": kind,
                "plain": PLAIN[kind],
                "severity": SEVERITY[kind],
                "line": text.count("\n", 0, start) + 1,
                "why": WHY[kind],
            }
        )
    return sorted(out, key=lambda f: (f["line"], f["severity"] != "error", f["kind"]))


def kinds_found(findings: Sequence[dict]) -> list[str]:
    """The plain-word kinds in `findings`, each once, in order of first appearance."""
    seen: list[str] = []
    for f in findings:
        if f["plain"] not in seen:
            seen.append(f["plain"])
    return seen


FIX_HINT = (
    "Rewrite the draft without those details (by role, not by name; a generic path), then run "
    'it again. A warning you checked that is a product, place or label: --accept "<text>". '
    "Errors cannot be accepted."
)


def _print_findings(findings: Sequence[dict], stream) -> None:
    """Kinds and lines, never the values."""
    for f in findings:
        where = f"line {f['line']}" if f["line"] else "title"
        print(f"  {where:<8} {f['severity']:<5} {f['plain']} — {f['why']}", file=stream)


def _refuse(label: str, findings: Sequence[dict]) -> int:
    """Print a refusal that lists the kinds found (never the values); returns exit code 1."""
    print(
        f"{label}: not clean — found: {', '.join(kinds_found(findings))}. Nothing is shown "
        "until it is fixed:",
        file=sys.stderr,
    )
    _print_findings(findings, sys.stderr)
    print(FIX_HINT, file=sys.stderr)
    return EXIT_FINDINGS


# --- the draft ------------------------------------------------------------------------------------


class Issue(NamedTuple):
    summary: str
    description: str
    labels: list[str]
    components: list[str]


def _read(path: str) -> tuple[Path, str]:
    p = Path(path).expanduser().resolve()
    try:
        return p, p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"cannot read {path}: {e}") from e


_TITLE_LINE = re.compile(r"\A\s*#[ \t]+(.+?)[ \t]*(?:\r?\n|\Z)")
_REPO_LINE = re.compile(r"\A\s*Skills repository:[^\n]*\n?")


def split_draft(text: str, title: str | None = None) -> tuple[str, str]:
    """(title, body) of the user's Markdown: `--title`, else a first line `# Title`, which is
    then not part of the body. A leading `Skills repository:` line is dropped (it is added)."""
    body = text
    found = _TITLE_LINE.match(text)
    if found and not title:
        title, body = found.group(1), text[found.end() :]
    title = " ".join((title or "").split())
    if not title:
        raise InputError("a title is required — start the file with `# Title` or give --title")
    if len(title) > MAX_TITLE:
        raise InputError(f"the title is {len(title)} characters — keep it within {MAX_TITLE}")
    body = _REPO_LINE.sub("", body, count=1).strip()
    if not body:
        raise InputError("the draft has no text under its title")
    return title, body


def github_issue(title: str, body: str) -> Issue:
    """The GitHub issue: prefixed title, the label, the repository line first."""
    return Issue(f"{SUMMARY_PREFIX} {title}", f"Skills repository: {REPO}\n\n{body}\n", [LABEL], [])


def _clean_text(file: str, title: str | None, accept: Iterable[str]) -> tuple[Path, str, str] | int:
    """(path, title, body) of a clean draft, or exit code 1 after printing the findings.

    The file is scanned as written, so line numbers match it; a --title is scanned as well."""
    path, text = _read(file)
    found = scan_text(text, accept)
    if title:
        found += [dict(f, line=0) for f in scan_text(title, accept)]
    if found:
        return _refuse(str(path), found)
    ttl, body = split_draft(text, title)
    return path, ttl, body


# --- JIRA wiki markup -----------------------------------------------------------------------------
# JIRA Server (REST API v2) takes the description as wiki markup, not Markdown, so the draft is
# converted. The repository is named in the summary prefix, in a label, in the component and in a
# line of the description, because AS is shared by several skills repositories.

_WIKI_SPECIAL = re.compile(r"[{}\[\]*_|^~+!]")
# A hyphen is markup (strike-through, dashes) unless it sits between two word characters.
_WIKI_HYPHEN = re.compile(r"(?<!\w)-|-(?!\w)")
# Emoticons such as (/) (x) (on) are text between parentheses of at most four characters.
_WIKI_EMOTICON = re.compile(r"\((?=[^\s()]{1,4}\))")
_WIKI_CODE_SPAN = re.compile(r"(`[^`\n]+`)")
_WIKI_HEADING = re.compile(r"(#{1,6})[ \t]+(.*)")
_WIKI_BULLET = re.compile(r"([ \t]*)[-*][ \t]+(.*)")
_WIKI_NUMBERED = re.compile(r"[ \t]*\d+[.)][ \t]+(.*)")
_WIKI_FENCE = re.compile(r"[ \t]*```")


def _wiki_escape(text: str) -> str:
    """`text` with the characters JIRA would read as markup made literal (backslash escape)."""
    text = _WIKI_SPECIAL.sub(lambda m: "\\" + m.group(), text)
    text = _WIKI_HYPHEN.sub(lambda m: "\\" + m.group(), text)
    text = text.replace("??", "\\?\\?")
    return _WIKI_EMOTICON.sub("\\\\(", text)


def _wiki_inline(text: str) -> str:
    parts = _WIKI_CODE_SPAN.split(text)
    return "".join(
        "{{" + _wiki_escape(p[1:-1]) + "}}" if i % 2 else _wiki_escape(p)
        for i, p in enumerate(parts)
    )


def _wiki_line(line: str) -> str:
    heading = _WIKI_HEADING.fullmatch(line)
    if heading:
        return f"h{len(heading.group(1))}. {_wiki_escape(heading.group(2))}"
    bullet = _WIKI_BULLET.fullmatch(line)
    if bullet:
        depth = len(bullet.group(1).replace("\t", "  ")) // 2 + 1
        rest = bullet.group(2)
        # A task-list box: a tick or a cross, not a link to "x".
        for box, symbol in (("[x] ", "(/) "), ("[ ] ", "(x) ")):
            if rest.startswith(box):
                return "*" * depth + " " + symbol + _wiki_inline(rest[len(box) :])
        return "*" * depth + " " + _wiki_inline(rest)
    numbered = _WIKI_NUMBERED.fullmatch(line)
    if numbered:
        return "# " + _wiki_inline(numbered.group(1))
    out = _wiki_inline(line)
    # A line that starts with "#" would become a numbered list item.
    return re.sub(r"\A([ \t]*)#", lambda m: m.group(1) + "\\#", out)


def to_wiki(markdown: str) -> str:
    """The draft's Markdown (# headings, `- ` bullets, `code`, ``` fences) as JIRA wiki markup.
    Everything else is escaped, so braces, brackets, asterisks and the like stay literal; fenced
    text goes into {noformat}, which ends only with its own closing tag."""
    out: list[str] = []
    in_code = False
    for line in markdown.splitlines():
        if _WIKI_FENCE.match(line):
            out.append("{noformat}")
            in_code = not in_code
        elif in_code:
            out.append(line.replace("{noformat}", "{ noformat}"))
        else:
            out.append(_wiki_line(line))
    if in_code:  # an unclosed fence must not swallow the sections after it
        out.append("{noformat}")
    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def jira_issue(title: str, body: str) -> Issue:
    """The JIRA issue: prefixed summary, wiki description with the repository line first, the
    labels and the component."""
    description = f"Skills repository: {REPO}\n\n{to_wiki(body).strip()}\n"
    return Issue(f"{SUMMARY_PREFIX} {title}", description, [LABEL, REPO_LABEL], [JIRA_COMPONENT])


def _clean_jira(file: str, title: str | None, accept: Iterable[str]) -> tuple[Path, Issue] | int:
    """(path, issue) for a clean draft, or exit code 1 after printing the findings. The privacy
    scan runs on the draft, as for GitHub, and again on the text that will be sent."""
    got = _clean_text(file, title, accept)
    if isinstance(got, int):
        return got
    path, ttl, body = got
    issue = jira_issue(ttl, body)
    found = scan_text(f"{issue.summary}\n{issue.description}", accept)
    if found:
        return _refuse(f"{path} (the JIRA text)", found)
    return path, issue


def jira_type_line() -> str:
    """The issue type to use, from JIRA_ISSUE_TYPES: the first, then the fallback. Part of the
    text the user sees before the yes, and the line the eval cases grade."""
    first, *fallbacks = JIRA_ISSUE_TYPES
    fallback = f" ({fallbacks[0]} if {JIRA_PROJECT} stops offering {first})" if fallbacks else ""
    return f"Issue type: {first}{fallback}"


# --- commands: draft, scan, jira-draft ------------------------------------------------------------


def cmd_draft(a: argparse.Namespace) -> int:
    got = _clean_text(a.file, a.title, a.accept)
    if isinstance(got, int):
        return got
    path, title, body = got
    issue = github_issue(title, body)
    if a.json:
        report = {
            "summary": issue.summary,
            "description": issue.description,
            "labels": issue.labels,
            "repository": REPO,
            "file": str(path),
        }
        print(json.dumps(report, indent=2))
    else:
        print(f"Summary: {issue.summary}")
        print(f"Labels: {', '.join(issue.labels)}")
        print("\nDescription:")
        print("-" * 72)
        print(issue.description.rstrip("\n"))
        print("-" * 72)
    print(
        "Show this to the user verbatim and file only after their explicit 'yes, file it' for "
        f"exactly this text. Tell them what it leaves out, in these words: {LEFT_OUT_SENTENCE}",
        file=sys.stderr,
    )
    return 0


def cmd_scan(a: argparse.Namespace) -> int:
    path, text = _read(a.file)
    found = scan_text(text, a.accept)
    if a.json:
        report = {"file": str(path), "clean": not found, "kinds": kinds_found(found)}
        print(json.dumps({**report, "findings": found}, indent=2))
    elif found:
        print(f"{path}: {len(found)} finding(s) — {', '.join(kinds_found(found))}")
        _print_findings(found, sys.stdout)
    else:
        print(f"{path}: clean")
    if found:
        print(FIX_HINT, file=sys.stderr)
        return EXIT_FINDINGS
    print("Next: show the full draft to the user and ask before filing.", file=sys.stderr)
    return 0


def cmd_jira_draft(a: argparse.Namespace) -> int:
    got = _clean_jira(a.file, a.title, a.accept)
    if isinstance(got, int):
        return got
    path, issue = got
    project_url = jira_project_url()
    if a.json:
        report = {
            "summary": issue.summary,
            "description": issue.description,
            "labels": issue.labels,
            "components": issue.components,
            "issue_types": list(JIRA_ISSUE_TYPES),
            "issue_type_line": jira_type_line(),
            "project": JIRA_PROJECT,
            "project_url": project_url,
            "repository": REPO,
            "file": str(path),
        }
        print(json.dumps(report, indent=2))
    else:
        print(f"Project: {JIRA_PROJECT}  {project_url}")
        print(f"Summary: {issue.summary}")
        print(f"Labels: {', '.join(issue.labels)}")
        print(f"Component: {', '.join(issue.components)}")
        print(jira_type_line())
        print("\nDescription (JIRA wiki markup — paste it as text, not into the visual editor):")
        print("-" * 72)
        print(issue.description.rstrip("\n"))
        print("-" * 72)
    print(
        "Show this to the user verbatim and file only after their explicit 'yes, file it' for "
        f"exactly this text: they paste it into a new issue in project {JIRA_PROJECT} "
        f"({project_url}). Tell them what it leaves out, in these words: {LEFT_OUT_SENTENCE}",
        file=sys.stderr,
    )
    return 0


# --- the JIRA host --------------------------------------------------------------------------------
# The token is sent to one host only. That is the real server, or the address in JIRA_URL_VAR when
# it is https (a staging server) or plain http on this machine (a local fake used by tests and
# evals). Any other address is refused before a request is made, so a mistyped or planted value
# cannot send the token over plain http or to a name that merely looks like loopback.

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class JiraHostRefused(Exception):
    """The JIRA address is not acceptable. The message never repeats the address: it may carry
    credentials."""


def jira_host(env: Mapping[str, str]) -> str:
    """The JIRA address to talk to: JIRA_URL, or the override variable when it is acceptable."""
    raw = (env.get(JIRA_URL_VAR) or "").strip()
    if not raw:
        return JIRA_URL
    try:
        parts = urlsplit(raw)
        hostname, _port = parts.hostname, parts.port  # a malformed port raises ValueError here
    except ValueError:
        raise JiraHostRefused("it is not a valid address") from None
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http"):
        raise JiraHostRefused("it must start with https:// (or http:// for this machine)")
    if not hostname:
        raise JiraHostRefused("it names no host")
    if parts.username is not None or parts.password is not None:
        raise JiraHostRefused("it must not contain a user name or password")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise JiraHostRefused("it must be a bare address, without a path or query")
    if scheme == "http" and hostname not in LOOPBACK_HOSTS:
        raise JiraHostRefused("plain http is allowed only for 127.0.0.1, localhost or ::1")
    return f"{scheme}://{parts.netloc.lower()}"


# --- the JIRA client ------------------------------------------------------------------------------
# One client, one network call (jira_call). The token is read from the environment only, sent as a
# Bearer header to the host above and kept out of everything printed; a redirect is never
# followed, so the token cannot go to another host and a redirect to a sign-in page reads as
# "token refused". What JIRA answers is data: it is shown, never obeyed.

# A Jira Data Center under load can take several seconds to create an issue; a minute would leave
# the agent waiting on a dead VPN. The timeout covers connecting and each read.
JIRA_TIMEOUT_S = 30
JIRA_MAX_BYTES = 4_000_000  # no JIRA answer this script reads is larger; a bigger body is cut off
JIRA_PAGE = 50  # createmeta pages (its own maximum is higher; 50 is its default)
JIRA_MAX_PAGES = 20  # a loop guard for a server that never says it is on its last page
JIRA_USER_AGENT = "ecmwf-weather-skills/feedback.py"
JIRA_KEY = re.compile(r"[A-Z][A-Z0-9_]+-\d+")
# Fields the request fills itself. `reporter` is required on the create screen without a default,
# but the request never sets it and a create without it succeeds: the server fills in the token's
# owner (seen on the live server), so it is not a gap the script has to refuse.
JIRA_FILLED = (
    "project",
    "issuetype",
    "summary",
    "description",
    "labels",
    "components",
    "reporter",
)
JIRA_SEARCH_LIMIT = 10
JIRA_WORDS = 6  # summary words used for the duplicate search
SHOWN_TEXT = 200  # characters of any text from JIRA that is shown
# Words of four letters or more that say nothing about a lesson; they would match everything.
JIRA_SHORT_WORDS = frozenset(
    (
        *("about", "after", "also", "been", "does", "from", "have", "into", "just", "more"),
        *("only", "over", "some", "than", "that", "them", "then", "this", "were", "when"),
        *("with", "would"),
    )
)
WINDOWS_JIRA_STORE = [
    "     $s = Read-Host 'Paste the token' -AsSecureString",
    f"     [Environment]::SetEnvironmentVariable('{JIRA_TOKEN_VAR}', "
    "[System.Net.NetworkCredential]::new('', $s).Password, 'User')",
]


class JiraReply(NamedTuple):
    status: int
    data: object  # the parsed JSON body, or None when there is none


class JiraUnreachable(Exception):
    """No usable answer: timeout, connection failure, or a body cut off."""


class JiraStop(Exception):
    """A barrier or an outcome to report: `_stop_report` turns it into a BLOCKED report."""

    def __init__(
        self,
        what: str,
        why: str,
        steps: list[str],
        agent: str,
        code: int,
        paste: bool = True,
        **extra: Any,
    ):
        super().__init__(what)
        self.what, self.why, self.steps, self.agent = what, why, steps, agent
        self.code, self.paste, self.extra = code, paste, extra


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect is an answer (often to a sign-in page); never follow it


def jira_default_opener(host: str = JIRA_URL) -> Callable[..., Any]:
    """urllib's opener without redirects. A request to this machine never goes through a proxy
    from the environment: a proxy would see the token of a request that never left."""
    handlers: list[Any] = [_NoRedirect()]
    if urlsplit(host).hostname in LOOPBACK_HOSTS:
        handlers.insert(0, urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers).open


def _failure_text(error: BaseException, token: str) -> str:
    """What went wrong, in words that cannot carry the token: a timeout, or the exception's class
    and, for an operating-system error, the system's own description."""
    reason = getattr(error, "reason", error)
    # socket.timeout is a separate class before Python 3.10, where this script also runs.
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timed out"
    text = type(reason).__name__
    detail = getattr(reason, "strerror", None) if getattr(reason, "errno", None) else None
    if isinstance(detail, str):
        text += ": " + (detail.replace(token, "…") if token else detail)
    return text[:120]


def jira_call(
    opener: Callable[..., Any],
    method: str,
    url: str,
    token: str,
    payload: dict | None = None,
) -> JiraReply:
    """The only network call of this script. A JiraReply for any HTTP answer (error statuses
    included); JiraUnreachable for anything else. The token appears in no message."""
    headers = {
        "Accept": "application/json",
        "User-Agent": JIRA_USER_AGENT,
        "Authorization": f"Bearer {token}",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener(request, timeout=JIRA_TIMEOUT_S) as answer:
            status, raw = answer.status, answer.read(JIRA_MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            raw = e.read(JIRA_MAX_BYTES + 1)
        except (OSError, http.client.HTTPException):
            raw = b""
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        raise JiraUnreachable(_failure_text(e, token)) from None
    try:
        parsed = None if len(raw) > JIRA_MAX_BYTES else json.loads(raw.decode("utf-8"))
    except (ValueError, AttributeError):
        parsed = None
    return JiraReply(status, parsed)


def jira_token_steps(host: str) -> list[str]:
    return [
        f"1. Signed in to Jira in your browser (ECMWF network or VPN), open your profile at "
        f"{host}/secure/ViewProfile.jspa (profile picture > Profile), choose Personal Access "
        "Tokens and click Create token.",
        "2. Name it (e.g. 'skill feedback'), set an expiry date (90 days is plenty), click Create "
        "and copy the token — Jira shows it only once.",
        f"3. Make it available as {JIRA_TOKEN_VAR} in YOUR OWN terminal, never in the chat. "
        f"macOS/Linux: `read -rs {JIRA_TOKEN_VAR} && export {JIRA_TOKEN_VAR}` (paste, Enter), or "
        "load it from your password manager or keychain. Windows (Windows PowerShell 5.1 or "
        "PowerShell 7; nothing is shown or kept in the command history), two commands:",
        *WINDOWS_JIRA_STORE,
        "4. Windows: a user variable is seen only by programs started afterwards — close the "
        "whole terminal app (every window), open a new one and start the agent from it. "
        "macOS/Linux: start the agent again from the terminal that holds the variable.",
        "5. Never paste the token into the chat or onto a command line. If it was ever pasted, "
        "revoke it on the same page and create a new one.",
        "6. Ask the agent to file the issue again.",
    ]


def host_refused(reason: JiraHostRefused) -> JiraStop:
    return JiraStop(
        f"the JIRA address in {JIRA_URL_VAR} is not accepted",
        f"{reason}. The token is sent only to {JIRA_URL}, or to the address in {JIRA_URL_VAR} "
        "when that is https or plain http on this machine. Nothing was sent.",
        [
            f"1. Unset {JIRA_URL_VAR} in your terminal (it is for tests), or set it to an https "
            "address, or to http://127.0.0.1:<port> for a local server.",
            "2. Then ask the agent to run the command again.",
        ],
        f"Do not change {JIRA_URL_VAR} yourself and do not send the request by other means.",
        EXIT_ACCESS,
    )


@dataclass
class JiraSession:
    """One run: the host, the token, the injected opener and the draft's path (None for a check)."""

    host: str
    token: str
    opener: Callable[..., Any]
    path: Path | None

    def clean(self, value: object) -> str:
        """Text from JIRA as one short line, without the token: it is data, shown as data."""
        text = " ".join(str(value or "").split())
        return (text.replace(self.token, "…") if self.token else text)[:SHOWN_TEXT]

    def call(
        self,
        method: str,
        api: str,
        query: Mapping[str, object] | None = None,
        payload: dict | None = None,
    ) -> JiraReply:
        url = f"{self.host}{api}"
        if query:
            url += "?" + urlencode(query, quote_via=quote)
        try:
            return jira_call(self.opener, method, url, self.token, payload)
        except JiraUnreachable as e:
            raise self.unreachable(str(e)) from None

    # -- barriers
    def no_token(self) -> JiraStop:
        without_draft = (
            "Relay these steps and the paste route. Never ask for the token in chat, never set "
            "the variable yourself, and do not use a token the user pasted into chat (tell them "
            "to revoke it). Without a token, run `jira-draft` and give the user its output."
            if self.path
            else "Relay these steps. Never ask for the token in chat, never set the variable "
            "yourself, and do not use a token the user pasted into chat (tell them to revoke it)."
        )
        return JiraStop(
            f"no JIRA personal access token ({JIRA_TOKEN_VAR} is not set)",
            "filing in JIRA needs the user's own personal access token, read from the "
            "environment; nobody but the user may create or handle it.",
            jira_token_steps(self.host),
            without_draft,
            EXIT_ACCESS,
        )

    def refused(self, status: int, created: bool = False) -> JiraStop:
        seen = "a redirect to a sign-in page" if 300 <= status < 400 else f"HTTP {status}"
        return JiraStop(
            f"JIRA rejected the personal access token ({seen})",
            f"the token in {JIRA_TOKEN_VAR} is expired, revoked or mistyped (a token stops "
            "working on the expiry date chosen when it was created)."
            + (" The issue has not been created." if created else ""),
            [
                f"1. Open {self.host}/secure/ViewProfile.jspa > Personal Access Tokens; revoke the "
                "old token if it is still listed.",
                *jira_token_steps(self.host)[1:],
            ],
            "Do not retry until the user says the token is replaced; offer the paste route "
            "meanwhile. Never ask for the token in chat.",
            EXIT_ACCESS,
        )

    def not_visible(self) -> JiraStop:
        return JiraStop(
            f"the account behind the token cannot browse JIRA project {JIRA_PROJECT}",
            f"JIRA answered that project {JIRA_PROJECT} does not exist or is not visible to this "
            "account.",
            [
                f"1. Open {jira_project_url(self.host)} in your browser; if it does not open, "
                "ask the project's administrators for access.",
                "2. Meanwhile use the paste route below if you can open the project.",
            ],
            "Report it; do not retry with other means.",
            EXIT_ACCESS,
        )

    def no_create(self, created: bool = False) -> JiraStop:
        return JiraStop(
            f"the account behind the token has no permission to create issues in {JIRA_PROJECT}",
            f"JIRA gave this account no issue types to create in project {JIRA_PROJECT}."
            + (" The issue has not been created." if created else ""),
            [
                f"1. Ask the administrators of project {JIRA_PROJECT} to give your account the "
                "Create Issues permission.",
                "2. Meanwhile use the paste route below if someone can create it for you.",
            ],
            "Report it; do not retry with other means.",
            EXIT_ACCESS,
        )

    def unreachable(self, detail: str) -> JiraStop:
        return JiraStop(
            f"cannot reach JIRA at {self.host} ({detail})",
            "JIRA answers only from the ECMWF network or VPN (or the service is down).",
            [
                "1. Connect to the ECMWF network or VPN (on site, or start the VPN client).",
                "2. Check that the JIRA address opens in your browser.",
                "3. Then ask the agent to run the command again.",
            ],
            "Stop network work and relay the steps; do not retry until the user confirms the "
            "VPN is up.",
            EXIT_BAD_INPUT,
        )

    def server_error(self, status: int) -> JiraStop:
        return JiraStop(
            f"JIRA answered with an unexpected result (HTTP {status})",
            "nothing has been created; the service may be failing or a proxy answered.",
            ["1. Try again in a few minutes.", "2. Or use the paste route below."],
            "Relay it; do not retry in a loop.",
            EXIT_BAD_INPUT,
        )

    # -- reads
    def read(self, api: str, query: Mapping[str, object] | None = None) -> dict:
        """A createmeta page; a barrier raises JiraStop."""
        reply = self.call("GET", api, query)
        status = reply.status
        if status == 200 and isinstance(reply.data, dict):
            return reply.data
        if status == 401 or 300 <= status < 400:
            raise self.refused(status)
        if status == 403:
            raise self.no_create()
        if status in (400, 404):
            raise self.not_visible()
        raise self.server_error(status)

    def server_info(self) -> tuple[int, str | None]:
        """(HTTP status, version) of the server. serverInfo is open to everyone, so only a
        refused token is a barrier here."""
        reply = self.call("GET", "/rest/api/2/serverInfo")
        if reply.status == 401 or 300 <= reply.status < 400:
            raise self.refused(reply.status)
        version = reply.data.get("version") if isinstance(reply.data, dict) else None
        return reply.status, self.clean(version) if version else None

    def paged(self, api: str) -> list[dict]:
        items: list[dict] = []
        for _ in range(JIRA_MAX_PAGES):
            page = self.read(api, {"startAt": len(items), "maxResults": JIRA_PAGE})
            chunk = page.get("values")
            if chunk is None and isinstance(page.get("fields"), dict):
                chunk = [{"fieldId": k, **v} for k, v in page["fields"].items()]
            chunk = [c for c in chunk or [] if isinstance(c, dict)]
            items += chunk
            total = page.get("total")
            more = page.get("isLast") is False or (isinstance(total, int) and len(items) < total)
            if not chunk or not more:
                break
        return items

    def issue_types(self) -> list[dict]:
        offered = self.paged(f"/rest/api/2/issue/createmeta/{JIRA_PROJECT}/issuetypes")
        if not offered:
            raise self.no_create()
        return [t for t in offered if not t.get("subtask") and t.get("id") and t.get("name")]

    def issue_type(self, usable: list[dict]) -> dict:
        for wanted in JIRA_ISSUE_TYPES:
            for t in usable:
                if str(t["name"]).lower() == wanted.lower():
                    return t
        names = ", ".join(self.clean(t["name"]) for t in usable) or "none"
        raise JiraStop(
            f"project {JIRA_PROJECT} offers no issue type this script can use",
            f"it looks for one of {', '.join(JIRA_ISSUE_TYPES)} (in that order); the project "
            f"offers: {names}. Nothing was sent.",
            [
                f"1. Ask the administrators of project {JIRA_PROJECT} which issue type takes "
                f"skill improvements (the script looks for {', then '.join(JIRA_ISSUE_TYPES)}).",
                "2. Meanwhile use the paste route below.",
            ],
            "Report which types the project offers; do not pick another type yourself.",
            EXIT_ACCESS,
        )

    def count(self, jql: str) -> tuple[int, int | None]:
        """(HTTP status, number of issues) of a search. Nothing but the number is kept."""
        reply = self.call(
            "GET", "/rest/api/2/search", {"jql": jql, "fields": "key", "maxResults": 1}
        )
        if reply.status == 401 or 300 <= reply.status < 400:
            raise self.refused(reply.status)
        total = reply.data.get("total") if isinstance(reply.data, dict) else None
        return reply.status, total if isinstance(total, int) else None

    def search(self, jql: str) -> list[dict] | None:
        """Issues matching `jql`, or None when the search did not work. A refused token and an
        unreachable JIRA still raise JiraStop."""
        reply = self.call(
            "GET",
            "/rest/api/2/search",
            {"jql": jql, "fields": "summary,status", "maxResults": JIRA_SEARCH_LIMIT},
        )
        if reply.status == 401 or 300 <= reply.status < 400:
            raise self.refused(reply.status)
        issues = reply.data.get("issues") if isinstance(reply.data, dict) else None
        if reply.status != 200 or not isinstance(issues, list):
            return None
        out = []
        for issue in issues:
            fields = issue.get("fields") if isinstance(issue, dict) else None
            key = issue.get("key") if isinstance(issue, dict) else None
            if (
                not isinstance(fields, dict)
                or not isinstance(key, str)
                or not JIRA_KEY.fullmatch(key)
            ):
                continue
            status = fields.get("status")
            out.append(
                {
                    "key": key,
                    "url": f"{self.host}/browse/{key}",
                    "status": self.clean(status.get("name")) if isinstance(status, dict) else "",
                    "summary": self.clean(fields.get("summary")),
                }
            )
        return out

    def read_back(self, key: str) -> dict | None:
        """What JIRA holds for the issue just created, or None when it cannot be read."""
        try:
            reply = self.call(
                "GET", f"/rest/api/2/issue/{key}", {"fields": "summary,issuetype,components,labels"}
            )
        except JiraStop:
            return None
        fields = reply.data.get("fields") if isinstance(reply.data, dict) else None
        if reply.status != 200 or not isinstance(fields, dict):
            return None
        kind = fields.get("issuetype")
        parts = fields.get("components")
        labels = fields.get("labels")
        return {
            "issue_type": self.clean(kind.get("name")) if isinstance(kind, dict) else "",
            "components": [self.clean(c.get("name")) for c in parts if isinstance(c, dict)]
            if isinstance(parts, list)
            else [],
            "labels": [self.clean(x) for x in labels] if isinstance(labels, list) else [],
        }

    # -- the create screen
    def inspect_screen(self) -> tuple[list[dict], dict, list[dict]]:
        """(usable issue types, the chosen type, its create-screen fields)."""
        usable = self.issue_types()
        chosen = self.issue_type(usable)
        fields = self.paged(
            f"/rest/api/2/issue/createmeta/{JIRA_PROJECT}/issuetypes/{chosen['id']}"
        )
        return usable, chosen, fields

    def problem(self, type_name: str, facts: dict, issue: Issue) -> JiraStop | None:
        """The barrier a create screen puts up for this issue, or None when it can be filed.
        The labels and the component mark the repository, and both duplicate searches filter on
        a label, so an issue filed without them could not be found again."""
        screen = f"the {type_name} create screen of {JIRA_PROJECT}"
        for needed in ("summary", "description"):
            if not facts[f"{needed}_field"]:
                return JiraStop(
                    f"{screen} has no {needed} field",
                    "the script cannot fill the issue without it. Nothing was sent.",
                    [
                        f"1. Ask the administrators of project {JIRA_PROJECT} to put {needed} "
                        "on the create screen.",
                        "2. Meanwhile use the paste route below.",
                    ],
                    "Report it; do not retry with other means.",
                    EXIT_ACCESS,
                )
        gaps = facts["required_gaps"]
        if gaps:
            return JiraStop(
                f"project {JIRA_PROJECT} requires a field this script cannot fill: "
                + ", ".join(gaps),
                f"the {type_name} create screen marks it required and gives it no default; the "
                "script never guesses a value. Nothing was sent.",
                [
                    f"1. Ask the administrators of project {JIRA_PROJECT} what to enter in "
                    + ", ".join(gaps)
                    + " for skill feedback, or to give it a default.",
                    "2. Meanwhile use the paste route below and fill that field by hand.",
                ],
                "Name the field to the user; do not choose a value.",
                EXIT_ACCESS,
            )
        if not facts["labels_field"]:
            return JiraStop(
                f"{screen} has no Labels field",
                f"every issue must carry the labels {', '.join(issue.labels)}, and a duplicate "
                "search needs the repository label to find the issue again. Nothing was sent.",
                [
                    "1. Use the paste route below, adding the labels "
                    f"{', '.join(issue.labels)} by hand if the screen allows it; otherwise keep "
                    f"the summary prefix {SUMMARY_PREFIX} and the first description line "
                    f"'Skills repository: {REPO}'.",
                    f"2. Tell the skills' maintainer that {screen} has no Labels field.",
                ],
                "Report it; do not file the issue without its labels or with other means.",
                EXIT_ACCESS,
            )
        if not facts["component_allowed"]:
            wanted = ", ".join(issue.components)
            return JiraStop(
                f"{screen} has no Component/s field"
                if not facts["components_field"]
                else f"project {JIRA_PROJECT} offers no component {wanted} on the {type_name} "
                "create screen",
                f"every issue must carry the component {wanted}, which marks the skills "
                "repository it comes from. Nothing was sent.",
                [
                    f"1. Use the paste route below, setting the component {wanted} by hand if the "
                    f"screen allows it; otherwise keep the labels {', '.join(issue.labels)}, the "
                    f"summary prefix {SUMMARY_PREFIX} and the first description line "
                    f"'Skills repository: {REPO}'.",
                    f"2. Tell the skills' maintainer that project {JIRA_PROJECT} has no component "
                    f"{wanted} on the {type_name} create screen.",
                ],
                "Report it; do not file the issue without its component or under another one.",
                EXIT_ACCESS,
            )
        return None


def required_gaps(fields: list[dict]) -> list[str]:
    """'Name (id)' of each field the create screen requires without a default that the script
    does not fill. Values are never read, let alone printed."""
    gaps = []
    for meta in fields:
        fid = str(meta.get("fieldId") or "")
        if meta.get("required") and not meta.get("hasDefaultValue") and fid not in JIRA_FILLED:
            gaps.append(f"{meta.get('name') or fid} ({fid})")
    return gaps


def screen_facts(fields: list[dict], issue: Issue) -> dict:
    """What the create screen offers for `issue`: flags and names, never values."""
    ids = {str(f.get("fieldId")) for f in fields}
    screen = next((f for f in fields if str(f.get("fieldId")) == "components"), None)
    offered = screen.get("allowedValues") if screen else None
    names = {str(v.get("name")) for v in offered or [] if isinstance(v, dict)}
    return {
        "summary_field": "summary" in ids,
        "description_field": "description" in ids,
        "required_gaps": required_gaps(fields),
        "labels_field": "labels" in ids,
        "components_field": screen is not None,
        "component_allowed": screen is not None and set(issue.components) <= names,
    }


def jql_quote(value: str) -> str:
    """A JQL string literal: control characters become spaces, backslash and quote are escaped,
    so a value can never end the literal early."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _jql_base() -> str:
    """The project and the repository label. The project key is quoted: the key `AS` is a
    reserved JQL word, which JIRA refuses unquoted."""
    return f"project = {jql_quote(JIRA_PROJECT)} AND labels = {jql_quote(REPO_LABEL)}"


def summary_words(summary: str) -> list[str]:
    """The distinctive words of a title, in order and without repeats: four letters or more,
    not in JIRA_SHORT_WORDS. Both duplicate searches (JIRA and GitHub) use them."""
    title = summary[len(SUMMARY_PREFIX) :] if summary.startswith(SUMMARY_PREFIX) else summary
    words: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", title):
        if word.lower() in JIRA_SHORT_WORDS or word.lower() in (w.lower() for w in words):
            continue
        words.append(word)
    return words


# Short words that say nothing about a lesson, for titles that have few longer ones.
SHORT_NOISE = frozenset(
    ("a", "an", "and", "at", "by", "in", "is", "of", "on", "or", "the", "to", "vs")
)


def search_words(summary: str) -> list[str]:
    """The words a duplicate search uses. The distinctive words (four letters or more) when the
    title has two or more. Otherwise every word of two characters or more, short ones such as
    `2t` or `10u` included: a title made of parameter names must still be searched, because a
    search that is skipped lets a duplicate through unseen."""
    words = summary_words(summary)
    if len(words) >= 2:
        return words
    title = summary[len(SUMMARY_PREFIX) :] if summary.startswith(SUMMARY_PREFIX) else summary
    every: list[str] = []
    for word in re.findall(r"[A-Za-z0-9]{2,}", title):
        low = word.lower()
        if low in SHORT_NOISE or low in JIRA_SHORT_WORDS or low in (w.lower() for w in every):
            continue
        every.append(word)
    return every


def duplicate_jql(summary: str) -> str:
    """Project, repository label and the title's words (any of them, in the summary)."""
    words = search_words(summary)
    jql = _jql_base()
    if words:
        jql += " AND (" + " OR ".join(f"summary ~ {jql_quote(w)}" for w in words[:JIRA_WORDS]) + ")"
    return jql + " ORDER BY created DESC"


def exact_summary_jql(summary: str) -> str:
    """The summary as a phrase: quote and backslash are dropped from it, then the phrase is
    wrapped in the escaped quotes JQL's phrase search needs."""
    phrase = " ".join(summary.replace('"', " ").replace("\\", " ").split())
    quoted = jql_quote('"' + phrase + '"')
    return f"{_jql_base()} AND summary ~ {quoted} ORDER BY created DESC"


def _unclear(session: JiraSession, issue: Issue, detail: str) -> JiraStop:
    """The outcome of the one creation is unknown: look for the issue it may have made, report
    what is there and say how to check. Nothing here creates anything."""
    jql = exact_summary_jql(issue.summary)
    check = f"{session.host}/issues/?" + urlencode({"jql": jql}, quote_via=quote)
    try:
        found = session.search(jql)
    except JiraStop:
        found = None
    why = (
        f"the request to create the issue ended without a clear answer ({detail}), so it may or "
        "may not have reached JIRA. Creating it again could file a duplicate."
    )
    if found is None:
        steps = [
            "1. The script could not search JIRA for the issue either.",
            f"2. Open {check} (signed in on the ECMWF network or VPN) and look for this summary "
            "before any retry.",
        ]
    elif found:
        listing = [
            f"   {f['key']} ({f['status']}) {f['url']}"
            + ("  — exact summary" if f["summary"] == issue.summary else "")
            for f in found
        ]
        steps = [
            "1. JIRA lists these issues matching the summary:",
            *listing,
            "2. If one of them is the user's issue, it WAS created: do not create it again; give "
            "the user its link. If none is, check by hand before any retry:",
            f"   {check}",
        ]
    else:
        steps = [
            "1. The search found no issue with this summary (JIRA may be slow to show a new one).",
            f"2. Wait a minute, then open {check} and check before any retry; ask the agent to "
            "run jira-file again only if the issue is still not there.",
        ]
    results = [
        {"key": f["key"], "url": f["url"], "exact": f["summary"] == issue.summary}
        for f in found or []
    ]
    return JiraStop(
        "JIRA did not confirm that the issue was created",
        why,
        steps,
        "Do not run jira-file again. Relay the steps; retry only after the user has checked and "
        "confirmed the issue does not exist.",
        EXIT_BAD_INPUT,
        paste=False,
        found=results,
        check_url=check,
    )


def _create(session: JiraSession, issue: Issue, type_id: str) -> str:
    """Create the issue, labels included, with ONE request. Returns its key, or raises JiraStop.
    This is the only place that sends a create request, and nothing here retries it."""
    fields: dict = {
        "project": {"key": JIRA_PROJECT},
        "issuetype": {"id": type_id},
        "summary": issue.summary,
        "description": issue.description,
        "labels": issue.labels,
        "components": [{"name": name} for name in issue.components],
    }
    try:
        reply = jira_call(
            session.opener,
            "POST",
            f"{session.host}/rest/api/2/issue",
            session.token,
            {"fields": fields},
        )
    except JiraUnreachable as e:
        raise _unclear(session, issue, str(e)) from None
    status, data = reply.status, reply.data
    if status in (200, 201):
        key = data.get("key") if isinstance(data, dict) else None
        if isinstance(key, str) and JIRA_KEY.fullmatch(key):
            return key
        raise _unclear(session, issue, f"HTTP {status} without an issue key")
    if status == 401:
        raise session.refused(status, created=True)
    if status == 403:
        raise session.no_create(created=True)
    if status == 400:
        errors = data.get("errors") if isinstance(data, dict) else None
        names = (
            ", ".join(sorted(session.clean(k) for k in errors))
            if isinstance(errors, dict)
            else "none named"
        )
        raise JiraStop(
            "JIRA refused the issue's content (HTTP 400)",
            f"the create request was rejected, so no issue exists. Fields JIRA names: {names}.",
            [
                "1. Tell the maintainers which fields JIRA named (their values are not shown).",
                "2. Use the paste route below meanwhile.",
            ],
            "Do not retry; relay the field names.",
            EXIT_BAD_INPUT,
        )
    if 400 < status < 500:
        raise JiraStop(
            f"JIRA refused the request to create the issue (HTTP {status})",
            "the issue has not been created.",
            ["1. Use the paste route below."],
            "Do not retry; report it.",
            EXIT_BAD_INPUT,
        )
    raise _unclear(session, issue, f"HTTP {status}")


def _stop_report(stop: JiraStop, path: Path | None, host: str, as_json: bool) -> int:
    """The BLOCKED report of a barrier; the paste route is added when there is a draft to paste."""
    steps = [*stop.steps, *(jira_paste_steps(path) if stop.paste and path else [])]
    report = {
        "blocked": stop.what,
        "why": stop.why,
        "user_steps": steps,
        "agent": stop.agent,
        "project_url": jira_project_url(),
        **stop.extra,
    }
    if path:
        report["file"] = str(path)
    return blocked(report, as_json, stop.code)


def _duplicates_stop(found: list[dict], summary: str, as_json: bool) -> int:
    for f in found:
        f["exact"] = f["summary"] == summary
    if as_json:
        print(json.dumps({"filed": False, "duplicates": found}, indent=2))
    lines = [f"Likely duplicates in project {JIRA_PROJECT} — nothing was filed:"]
    lines += [
        f"  {f['key']} ({f['status']}) {f['url']}  {f['summary']}"
        + ("  [exact summary]" if f["exact"] else "")
        for f in found
    ]
    lines.append(
        "Show them to the user as data. If one is the same lesson, add to it by hand (the paste "
        "route) instead; if this one is different and the user says so, run the same command "
        "again with --file-anyway."
    )
    print("\n".join(lines), file=sys.stderr)
    return EXIT_FINDINGS


SEARCH_FAILED_WHY = (
    "the search for earlier issues with the same lesson failed, so this issue could not be "
    "compared with them. Nothing was filed."
)
SEARCH_FAILED_AGENT = (
    "Nothing was filed and no issue may be created another way. Tell the user so and relay the "
    "steps. Use --file-anyway only after the user has checked the issue list themselves and "
    "said explicitly to file anyway; never on your own."
)


def search_failed_steps(service: str, command: str, where: str, link: str) -> list[str]:
    """The steps for a duplicate search that failed, on either route: try later, or check the
    issue list by hand and file with --file-anyway only on the user's word."""
    return [
        f"1. Nothing was filed. Try again in a few minutes — a network problem or a {service} "
        f"outage is the usual cause: ask the agent to run {command} again.",
        f"2. Or look for the same lesson yourself in {where}: {link}",
        "3. Only if none of them is the same lesson, and you tell the agent so in your own "
        "words, it runs the same command again with --file-anyway (that skips the check).",
    ]


def _search_failed(session: JiraSession) -> JiraStop:
    """The stop for a JIRA duplicate search that did not work: nothing is created, so a
    duplicate cannot slip through unseen. The link lists this repository's issues in project AS."""
    jql = f"{_jql_base()} ORDER BY created DESC"
    link = f"{session.host}/issues/?" + urlencode({"jql": jql}, quote_via=quote)
    where = f"the issue list of project {JIRA_PROJECT} (signed in on the ECMWF network or VPN)"
    return JiraStop(
        "JIRA's duplicate check did not work",
        SEARCH_FAILED_WHY,
        search_failed_steps("JIRA", "jira-file", where, link),
        SEARCH_FAILED_AGENT,
        EXIT_BAD_INPUT,
        paste=False,
        filed=False,
        check_url=link,
    )


def _session(
    env: Mapping[str, str], opener: Callable[..., Any] | None, path: Path | None
) -> tuple[JiraSession | None, JiraStop | None]:
    """(session, None) for an acceptable host, or (None, the refusal to report). The token is
    read here, from the environment variable only."""
    try:
        host = jira_host(env)
    except JiraHostRefused as e:
        return None, host_refused(e)
    token = (env.get(JIRA_TOKEN_VAR) or "").strip()
    return JiraSession(host, token, opener or jira_default_opener(host), path), None


def cmd_jira_check(a: argparse.Namespace, env: Mapping[str, str], opener) -> int:
    """Read-only: GET requests only. Prints names, flags and counts, never issue contents."""
    session, refusal = _session(env, opener, None)
    if session is None:
        return _stop_report(refusal, None, JIRA_URL, a.json)
    probe = jira_issue("check", "check")
    try:
        if not session.token:
            raise session.no_token()
        info_status, version = session.server_info()
        usable, chosen, fields = session.inspect_screen()
        facts = screen_facts(fields, probe)
        count_status, count = session.count(f"project = {jql_quote(JIRA_PROJECT)}")
        problem = session.problem(str(chosen["name"]), facts, probe)
    except JiraStop as stop:
        return _stop_report(stop, None, session.host, a.json)
    report = {
        "host": session.host,
        "project": JIRA_PROJECT,
        "server_version": version,
        "issue_types": [session.clean(t["name"]) for t in usable],
        "issue_type": session.clean(chosen["name"]),
        "labels_field": facts["labels_field"],
        "components_field": facts["components_field"],
        "component": JIRA_COMPONENT,
        "component_allowed": facts["component_allowed"],
        "required_gaps": facts["required_gaps"],
        "issue_count": count,
        "ok": problem is None,
    }
    if problem:
        report["problem"] = problem.what
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        server = version or f"no version in the serverInfo answer (HTTP {info_status})"
        print(f"JIRA host: {session.host}")
        print(f"Server: {server}")
        print(f"Project {JIRA_PROJECT} issue types: {', '.join(report['issue_types'])}")
        print(f"Issue type to use: {report['issue_type']}")
        gaps = ", ".join(facts["required_gaps"]) or "none"
        print(f"Required fields it cannot fill: {gaps}")
        print(f"Labels field: {_yes(facts['labels_field'])}")
        print(f"Component/s field: {_yes(facts['components_field'])}")
        print(f"Component {JIRA_COMPONENT} allowed: {_yes(facts['component_allowed'])}")
        shown = count if count is not None else f"not counted (HTTP {count_status})"
        print(f"Issues in project {JIRA_PROJECT} this token can see: {shown}")
    if problem:
        return _stop_report(problem, None, session.host, False)
    return 0


def cmd_jira_file(a: argparse.Namespace, env: Mapping[str, str], opener) -> int:
    """File the draft: createmeta checks, duplicate search, ONE create, read-back."""
    got = _clean_jira(a.file, a.title, a.accept)
    if isinstance(got, int):
        return got
    path, issue = got
    session, refusal = _session(env, opener, path)
    if session is None:
        return _stop_report(refusal, path, JIRA_URL, a.json)
    try:
        if not session.token:
            raise session.no_token()
        _, chosen, fields = session.inspect_screen()
        type_name = str(chosen["name"])
        problem = session.problem(type_name, screen_facts(fields, issue), issue)
        if problem:
            raise problem
        print(
            f"Issue type: {type_name} (the first of {', '.join(JIRA_ISSUE_TYPES)} that project "
            f"{JIRA_PROJECT} offers)",
            file=sys.stderr,
        )
        if not a.file_anyway:
            found = session.search(duplicate_jql(issue.summary))
            if found is None:
                raise _search_failed(session)
            if found:
                return _duplicates_stop(found, issue.summary, a.json)
        key = _create(session, issue, str(chosen["id"]))
    except JiraStop as stop:
        return _stop_report(stop, path, session.host, a.json)
    return _report_filed(session, key, type_name, issue, a.json)


def _report_filed(session: JiraSession, key: str, type_name: str, issue: Issue, as_json: bool):
    """Print the created issue as JIRA holds it (read back), or as sent when it cannot be read."""
    held = session.read_back(key)
    shown = held or {
        "issue_type": type_name,
        "components": issue.components,
        "labels": issue.labels,
    }
    url = f"{session.host}/browse/{key}"
    if as_json:
        report = {"key": key, "url": url, **shown, "read_back": held is not None}
        print(json.dumps(report, indent=2))
    else:
        print(f"Filed {key}: {url}")
        print(f"Issue type: {shown['issue_type']}")
        print(f"Component: {', '.join(shown['components'])}")
        print(f"Labels: {', '.join(shown['labels'])}")
    if held is None:
        print(
            "The issue was created but JIRA could not be read back: open the link to check its "
            "type, component and labels (shown above as sent).",
            file=sys.stderr,
        )
    else:
        differs = [
            name
            for name, sent, got in (
                ("issue type", type_name, held["issue_type"]),
                ("components", set(issue.components), set(held["components"])),
                ("labels", set(issue.labels), set(held["labels"])),
            )
            if sent != got
        ]
        if differs:
            print(
                f"WARNING: what JIRA holds differs from what was sent ({', '.join(differs)}): "
                "open the link and check the issue.",
                file=sys.stderr,
            )
    print("Give the user this link; offer to delete the local draft.", file=sys.stderr)
    return 0


# --- routes ---------------------------------------------------------------------------------------


def run_process(argv: Sequence[str]) -> CommandResult:
    """Run a command without a shell. A missing program reads as exit 127, a timeout as 124."""
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=GH_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return CommandResult(127, "", f"{argv[0]}: command not found")
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", f"{argv[0]}: timed out")
    except OSError as e:
        return CommandResult(126, "", f"{argv[0]}: {type(e).__name__}")
    return CommandResult(done.returncode, done.stdout, done.stderr)


def classify_gh_failure(result: CommandResult) -> str:
    """What a failed gh call means: network, notfound, forbidden, signin or other."""
    text = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode == 124 or re.search(
        r"could not resolve host|dial tcp|timed? ?out|connection (?:refused|reset)|unreachable"
        r"|no such host|network|error connecting to",
        text,
    ):
        return "network"
    if re.search(r"not logged in|gh auth login|authentication required|bad credentials", text):
        return "signin"
    if "404" in text or "not found" in text or "could not resolve to a repository" in text:
        return "notfound"
    if "403" in text or "forbidden" in text or "saml" in text:
        return "forbidden"
    return "other"


def detect_routes(
    which: Callable[[str], str | None],
    runner: Callable[[Sequence[str]], CommandResult],
    env: Mapping[str, str],
) -> dict:
    """Which way the issue can go. gh is asked only whether it is installed, signed in and can
    reach the repository; the answers are never printed. The JIRA token is reported by its
    name only."""
    gh = {"installed": bool(which("gh")), "signed_in": False, "access": False, "problem": None}
    if gh["installed"]:
        signed = runner(["gh", "auth", "status"])
        gh["signed_in"] = signed.returncode == 0
        if not gh["signed_in"]:
            gh["problem"] = "signin" if classify_gh_failure(signed) != "network" else "network"
        else:
            reach = runner(["gh", "api", f"repos/{REPO}"])
            gh["access"] = reach.returncode == 0
            if not gh["access"]:
                gh["problem"] = classify_gh_failure(reach)
    else:
        gh["problem"] = "missing"
    token_set = bool(env.get(JIRA_TOKEN_VAR))
    if gh["access"]:
        route, why = "github", f"gh is signed in and can reach {REPO}"
    elif token_set:
        route, why = "jira-file", f"{JIRA_TOKEN_VAR} is set (ECMWF staff: project {JIRA_PROJECT})"
    else:
        route, why = "jira-draft", "no usable GitHub route and no JIRA token: the paste route"
    return {
        "gh": gh,
        "jira_token": {"variable": JIRA_TOKEN_VAR, "set": token_set},
        "route": route,
        "why": why,
    }


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def cmd_routes(
    a: argparse.Namespace,
    which: Callable[[str], str | None],
    runner: Callable[[Sequence[str]], CommandResult],
    env: Mapping[str, str],
) -> int:
    report = detect_routes(which, runner, env)
    if a.json:
        print(json.dumps(report, indent=2))
        return 0
    gh = report["gh"]
    print(f"gh installed: {_yes(gh['installed'])}")
    print(f"gh signed in: {_yes(gh['signed_in'])}")
    print(f"gh can reach {REPO}: {_yes(gh['access'])}")
    if gh["problem"]:
        print(f"gh problem: {gh['problem']}")
    print(f"{JIRA_TOKEN_VAR} set: {_yes(report['jira_token']['set'])}")
    print(f"Route: {report['route']} — {report['why']}")
    return 0


# --- github-file ----------------------------------------------------------------------------------
# The user's own `gh` files the issue with its own sign-in, so this script never sees a token.
# gh's words are never shown: they may hold an account name or a host. Only what this script made
# of them is printed — a kind of failure, or an issue's number, title, state and link.

GH_WORDS = 3  # title words in the duplicate search; GitHub's search needs all of them to match
GH_ISSUES_URL = f"https://github.com/{REPO}/issues"
GH_ISSUE_URL = re.compile(re.escape(GH_ISSUES_URL) + r"/(\d+)")
GH_ISSUE_FIELDS = "number,title,state,url"
# What gh says when the label cannot be added: no issue exists then (the label is resolved before
# the issue is made), so it is a refusal and not an unclear answer.
GH_LABEL_REFUSAL = re.compile(r"could not add label|label[^\n]*not found", re.IGNORECASE)
# A refusal that comes with the create request means GitHub made no issue.
GH_DEFINITE_REFUSALS = ("signin", "notfound", "forbidden")
GH_AGENT_STOP = (
    "Relay the steps and the other routes in full. Never ask for a token in chat, never handle "
    "one, and do not run github-file again until the user says the cause is fixed."
)


def _gh_route_steps(path: Path) -> list[str]:
    """The other two ways to send the draft, named in every BLOCKED report of this route."""
    q = shlex.quote(str(path))
    return [
        "Or use another route:",
        f"  JIRA route (ECMWF staff): with a Jira personal access token in {JIRA_TOKEN_VAR}, run "
        f"python3 feedback.py jira-file {q} (it explains how to get a token); without one, "
        f"python3 feedback.py jira-draft {q} prints what to paste into project {JIRA_PROJECT}.",
        f"  paste route: run python3 feedback.py draft {q}, open {GH_ISSUES_URL}/new, paste the "
        f"summary as the title and the description as the text, and add the label {LABEL} if "
        "the form offers it.",
    ]


def _gh_blocked(
    what: str,
    why: str,
    steps: list[str],
    code: int,
    path: Path,
    as_json: bool,
    agent: str = GH_AGENT_STOP,
    other_routes: bool = True,
    **extra: Any,
) -> int:
    report = {
        "blocked": what,
        "why": why,
        "user_steps": [*steps, *(_gh_route_steps(path) if other_routes else [])],
        "agent": agent,
        "repository": REPO,
        "file": str(path),
        **extra,
    }
    return blocked(report, as_json, code)


def _gh_barrier(kind: str, path: Path, as_json: bool, created: bool = False) -> int:
    """The BLOCKED report for what stopped gh: `missing` (not installed) or a kind named by
    classify_gh_failure. `created` says the refusal came with the create request."""
    made = " GitHub refused the create request, so no issue was made." if created else ""
    again = "Then ask the agent to run github-file again."
    if kind == "missing":
        return _gh_blocked(
            "the GitHub CLI (gh) is not installed",
            "the GitHub route files the issue through the user's own gh.",
            [
                "1. Install gh (the GitHub CLI, cli.github.com), then run `gh auth login`.",
                f"2. {again}",
            ],
            EXIT_DEPENDENCY,
            path,
            as_json,
        )
    if kind == "signin":
        return _gh_blocked(
            "gh is not signed in to GitHub",
            f"gh has no usable sign-in.{made}",
            [
                "1. In a terminal run `gh auth login`, choose GitHub.com and follow the prompts "
                "(the sign-in is yours; never paste a token into the chat).",
                f"2. {again}",
            ],
            EXIT_ACCESS,
            path,
            as_json,
        )
    if kind in ("notfound", "forbidden"):
        return _gh_blocked(
            f"gh cannot reach {REPO} with this sign-in",
            f"GitHub refused or hid the repository from the signed-in account.{made}",
            [
                "1. Run `gh auth status` yourself to see which account gh uses; `gh auth login` "
                "switches it.",
                "2. If your organisation requires it, authorise gh's token for single sign-on "
                "(GitHub, Settings, Applications).",
                f"3. {again}",
            ],
            EXIT_ACCESS,
            path,
            as_json,
        )
    if kind == "network":
        return _gh_blocked(
            "GitHub could not be reached",
            "gh got no answer from GitHub (no connection, a proxy, or an outage).",
            [f"1. Check the network connection, then try again. {again}"],
            EXIT_BAD_INPUT,
            path,
            as_json,
        )
    return _gh_blocked(
        "gh gave an answer this script cannot use",
        f"gh failed or answered in a form the script does not recognise.{made}",
        [f"1. Run `gh auth status` yourself and check it works. {again}"],
        EXIT_BAD_INPUT,
        path,
        as_json,
    )


def _gh_label_barrier(path: Path, as_json: bool) -> int:
    return _gh_blocked(
        f"the label {LABEL} does not exist on {REPO}",
        "issues of this kind carry that label, and the script does not create labels or issues "
        "without it.",
        [
            "1. The maintainers need to create the label "
            f"(`gh label create {LABEL} -R {REPO}`); tell them if you can.",
            "2. Use another route meanwhile.",
        ],
        EXIT_ACCESS,
        path,
        as_json,
    )


def _one_line(value: object) -> str:
    """Text from GitHub as one short line: it is data, shown as data."""
    return " ".join(str(value or "").split())[:SHOWN_TEXT]


def _gh_rows(answer: CommandResult) -> list[dict] | None:
    """The JSON list gh printed, or None when gh failed or printed something else."""
    if answer.returncode != 0:
        return None
    try:
        data = json.loads(answer.stdout)
    except ValueError:
        return None
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        return None
    return data


def _shown_issue(item: dict) -> dict:
    number = item.get("number")
    number = number if isinstance(number, int) and not isinstance(number, bool) else None
    url = item.get("url")
    if not (isinstance(url, str) and GH_ISSUE_URL.fullmatch(url)):
        url = f"{GH_ISSUES_URL}/{number}" if number is not None else ""
    return {
        "number": number,
        "title": _one_line(item.get("title")),
        "state": _one_line(item.get("state")),
        "url": url,
    }


def _gh_issue_search(runner: Callable[[Sequence[str]], CommandResult], text: str):
    """The issues with the label that match `text` (any state), or None when the search failed."""
    rows = _gh_rows(
        runner(
            [
                *("gh", "issue", "list", "-R", REPO, "--label", LABEL, "--state", "all"),
                *("--search", text, "--json", GH_ISSUE_FIELDS),
            ]
        )
    )
    return None if rows is None else [_shown_issue(r) for r in rows]


def _gh_phrase(summary: str) -> str:
    """The title as text GitHub's search takes as one phrase: quotes and backslashes dropped."""
    return " ".join(summary.replace('"', " ").replace("\\", " ").split())


def _gh_search_text(summary: str) -> str:
    """The duplicate search for a title: its words (GitHub needs all of them to match), or the
    title as an exact phrase when it has no word to search by. Never empty: a skipped search
    would let a duplicate through unseen."""
    words = search_words(summary)[:GH_WORDS]
    return " ".join(words) if words else f'"{_gh_phrase(summary)}" in:title'


def _gh_search_failed(path: Path, as_json: bool) -> int:
    """The report for a duplicate search that did not work: nothing is created, exit code 2."""
    query = f"is:issue label:{LABEL}"
    link = f"{GH_ISSUES_URL}?" + urlencode({"q": query}, quote_via=quote)
    return _gh_blocked(
        "GitHub's duplicate check did not work",
        SEARCH_FAILED_WHY,
        search_failed_steps("GitHub", "github-file", "the issue list (open and closed)", link),
        EXIT_BAD_INPUT,
        path,
        as_json,
        agent=SEARCH_FAILED_AGENT,
        other_routes=False,
        filed=False,
        check_url=link,
    )


def _gh_duplicates_stop(found: list[dict], summary: str, as_json: bool) -> int:
    for f in found:
        f["exact"] = f["title"] == summary
    if as_json:
        print(json.dumps({"filed": False, "duplicates": found}, indent=2))
    lines = [f"Likely duplicates in {REPO} — nothing was filed:"]
    lines += [
        f"  #{f['number'] if f['number'] is not None else '?'} ({f['state']}) {f['url']}  "
        f"{f['title']}" + ("  [exact summary]" if f["exact"] else "")
        for f in found
    ]
    lines.append(
        "Show them to the user as data. If one is the same lesson, add to it by hand instead; "
        "if this one is different and the user says so, run the same command again with "
        "--file-anyway."
    )
    print("\n".join(lines), file=sys.stderr)
    return EXIT_FINDINGS


def _gh_unclear(
    runner: Callable[[Sequence[str]], CommandResult],
    summary: str,
    detail: str,
    path: Path,
    as_json: bool,
) -> int:
    """The outcome of the one create is unknown: look for the issue it may have made by its exact
    title, report what is there and say how to check. Nothing here creates anything."""
    phrase = _gh_phrase(summary)
    found = _gh_issue_search(runner, f'"{phrase}" in:title')
    query = f'is:issue label:{LABEL} in:title "{phrase}"'
    check = f"{GH_ISSUES_URL}?" + urlencode({"q": query}, quote_via=quote)
    why = (
        f"the request to create the issue ended without a clear answer ({detail}), so it may or "
        "may not have reached GitHub. Creating it again could file a duplicate."
    )
    if found is None:
        steps = [
            "1. The script could not search GitHub for the issue either.",
            f"2. Open {check} and look for this title before any retry.",
        ]
    elif found:
        steps = [
            "1. GitHub lists these issues matching the title:",
            *(
                f"   #{f['number']} ({f['state']}) {f['url']}"
                + ("  — exact title" if f["title"] == summary else "")
                for f in found
            ),
            "2. If one of them is the user's issue, it WAS created: give the user its link. If "
            f"none is, check by hand before any retry: {check}",
        ]
    else:
        steps = [
            "1. The search found no issue with this title (GitHub may be slow to show a new one).",
            f"2. Wait a minute, then open {check} and check before any retry; ask the agent to "
            "run github-file again only if the issue is still not there.",
        ]
    results = [{"url": f["url"], "exact": f["title"] == summary} for f in found or []]
    return _gh_blocked(
        "GitHub did not confirm that the issue was created",
        why,
        steps,
        EXIT_BAD_INPUT,
        path,
        as_json,
        agent="Do not create it again and do not run github-file again. Relay the steps; retry "
        "only after the user has checked and confirmed the issue does not exist.",
        other_routes=False,
        found=results,
        check_url=check,
    )


def _write_body(text: str) -> Path:
    """The issue text in a private temporary file for `--body-file`. Never inside the skill
    directory: skills must not be modified, and the file holds the user's text."""
    folder = Path(tempfile.gettempdir()).resolve()
    skill_dir = Path(__file__).resolve().parent.parent
    if folder == skill_dir or skill_dir in folder.parents:
        raise InputError(
            f"the temporary directory {folder} is inside the skill directory — set TMPDIR to a "
            "directory elsewhere"
        )
    handle, name = tempfile.mkstemp(prefix="skill-feedback-", suffix=".md", dir=folder)
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return Path(name)


def _gh_create(runner: Callable[[Sequence[str]], CommandResult], issue: Issue) -> CommandResult:
    """Create the issue with ONE gh call. This is the only place that creates, and nothing
    retries it. The temporary body file is removed whatever happens."""
    body_file = _write_body(issue.description)
    try:
        return runner(
            [
                *("gh", "issue", "create", "-R", REPO, "--title", issue.summary),
                *("--label", LABEL, "--body-file", str(body_file)),
            ]
        )
    finally:
        with contextlib.suppress(OSError):  # a file already gone is what is wanted
            body_file.unlink()


def _created_issue(answer: CommandResult) -> tuple[str, int] | None:
    """(link, number) of the issue gh reports, or None. Only a line that is exactly an issue link
    of this repository counts; gh's other words are ignored."""
    for line in answer.stdout.splitlines():
        found = GH_ISSUE_URL.fullmatch(line.strip())
        if found:
            return found.group(0), int(found.group(1))
    return None


def cmd_github_file(
    a: argparse.Namespace,
    which: Callable[[str], str | None],
    runner: Callable[[Sequence[str]], CommandResult],
) -> int:
    """File the draft with gh: sign-in and access, label, duplicate search, ONE create."""
    got = _clean_text(a.file, a.title, a.accept)
    if isinstance(got, int):
        return got
    path, title, body = got
    issue = github_issue(title, body)
    if not which("gh"):
        return _gh_barrier("missing", path, a.json)
    signed = runner(["gh", "auth", "status"])
    if signed.returncode != 0:
        network = classify_gh_failure(signed) == "network"
        return _gh_barrier("network" if network else "signin", path, a.json)
    reach = runner(["gh", "api", f"repos/{REPO}"])
    if reach.returncode != 0:
        return _gh_barrier(classify_gh_failure(reach), path, a.json)
    labels = runner(["gh", "label", "list", "-R", REPO, "--search", LABEL, "--json", "name"])
    rows = _gh_rows(labels)
    if rows is None:
        kind = classify_gh_failure(labels) if labels.returncode != 0 else "other"
        return _gh_barrier(kind, path, a.json)
    if not any(r.get("name") == LABEL for r in rows):
        return _gh_label_barrier(path, a.json)
    if not a.file_anyway:
        found = _gh_issue_search(runner, _gh_search_text(issue.summary))
        if found is None:
            return _gh_search_failed(path, a.json)
        if found:
            return _gh_duplicates_stop(found, issue.summary, a.json)
    done = _gh_create(runner, issue)
    if done.returncode == 0:
        created = _created_issue(done)
        if created is None:
            return _gh_unclear(runner, issue.summary, "no issue link in the answer", path, a.json)
        return _gh_report_filed(created[0], created[1], issue, a.json)
    text = f"{done.stdout}\n{done.stderr}"
    if GH_LABEL_REFUSAL.search(text):
        return _gh_label_barrier(path, a.json)
    kind = classify_gh_failure(done)
    if kind in GH_DEFINITE_REFUSALS:
        return _gh_barrier(kind, path, a.json, created=True)
    detail = "gh timed out" if done.returncode == 124 else f"gh exited with {done.returncode}"
    return _gh_unclear(runner, issue.summary, detail, path, a.json)


def _gh_report_filed(url: str, number: int, issue: Issue, as_json: bool) -> int:
    if as_json:
        report = {
            "filed": True,
            "url": url,
            "number": number,
            "summary": issue.summary,
            "labels": issue.labels,
            "repository": REPO,
        }
        print(json.dumps(report, indent=2))
    else:
        print(f"Filed #{number}: {url}")
        print(f"Summary: {issue.summary}")
        print(f"Labels: {', '.join(issue.labels)}")
    print("Give the user this link; offer to delete the local draft.", file=sys.stderr)
    return 0


# --- command line ---------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="feedback.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("routes", help="which routes are open: GitHub, JIRA with a token, paste")
    p.add_argument("--json", action="store_true")
    for name, helptext in (
        ("draft", "the GitHub issue text (sends nothing)"),
        ("scan", "privacy scan: exit 0 clean, 1 findings"),
        ("jira-draft", f"summary, wiki description and labels for project {JIRA_PROJECT}"),
        (
            "github-file",
            f"file the draft on GitHub ({REPO}) with the user's gh — once, after the user's yes",
        ),
        (
            "jira-file",
            f"file the draft in JIRA project {JIRA_PROJECT} — once, after the user's yes",
        ),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("file")
        if name != "scan":
            p.add_argument("--title", help="the title, when the file has no `# Title` first line")
        p.add_argument(
            "--accept",
            action="append",
            default=[],
            metavar="TEXT",
            help="a checked warning that is not about a person (repeatable)",
        )
        p.add_argument("--json", action="store_true")
        if name in ("github-file", "jira-file"):
            p.add_argument(
                "--file-anyway",
                action="store_true",
                help="file although the duplicate search found likely duplicates (the user's word)",
            )
    p = sub.add_parser(
        "jira-check", help=f"read-only check that project {JIRA_PROJECT} can take the issue"
    )
    p.add_argument("--json", action="store_true")
    return ap


def main(
    argv: list[str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[[Sequence[str]], CommandResult] | None = None,
    env: Mapping[str, str] | None = None,
    opener: Callable[..., Any] | None = None,
) -> int:
    """`which` finds gh, `runner` runs it, `env` holds JIRA_PAT and the host override, and
    `opener` makes the JIRA requests (all injectable for tests)."""
    a = _parser().parse_args(argv)
    environ = os.environ if env is None else env
    try:
        if a.cmd == "routes":
            return cmd_routes(a, which, runner or run_process, environ)
        if a.cmd == "scan":
            return cmd_scan(a)
        if a.cmd == "jira-draft":
            return cmd_jira_draft(a)
        if a.cmd == "github-file":
            return cmd_github_file(a, which, runner or run_process)
        if a.cmd == "jira-check":
            return cmd_jira_check(a, environ, opener)
        if a.cmd == "jira-file":
            return cmd_jira_file(a, environ, opener)
        return cmd_draft(a)
    except InputError as e:
        print(f"feedback.py {a.cmd}: error: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT


def utf8_output() -> None:
    """Write UTF-8 to stdout and stderr: piped output on Windows otherwise uses the ANSI code
    page, which has no em dash. errors="replace" keeps one bad character from stopping the run."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    utf8_output()
    sys.exit(main())
