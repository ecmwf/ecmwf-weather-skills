# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0
"""feedback.py: the repository marking, the draft, the privacy scan, the JIRA paste route and the
route check. Every credential-shaped sample is assembled at run time, so no value that looks
like a secret sits in this file; gh and the JIRA token are injected, nothing touches a network."""

import ast
import json
import subprocess
import sys

import pytest
from conftest import SKILLS, load_script

fb = load_script("ecmwf-weather-feedback", "feedback")
SCRIPT = SKILLS / "ecmwf-weather-feedback" / "scripts" / "feedback.py"

# Synthetic samples, one per detector. The values are made up.
HEXKEY = "ab" * 16
UUID = "-".join(["a" * 8, "b" * 4, "c" * 4, "d" * 4, "e" * 12])
JWT = "eyJ" + "a" * 30 + "." + "b" * 30 + "." + "c" * 20
BEARER = "Bearer " + "Zx9" * 10
JIRA_PAT_SHAPE = "Nz" + "Ab3x" * 10 + "Qw"
DUMMY_PAT = "dummy" + "Pat" + "Value" + "123"
SAMPLES = {
    "email": ("Please write to alice@university.edu about it.", "e-mail address"),
    "unix home path": ("It failed in /home/alice/data/run1.", "user name in a file path"),
    "macos home path": ("It failed in /Users/bob/data/run1.", "user name in a file path"),
    "windows home path": (r"It failed in C:\Users\carol\data\run1.", "user name in a file path"),
    "ecmwfapirc key line": (f'  "key": "{HEXKEY}",', "key, token or password"),
    "ecmwfapirc email line": ('  "email": "someone"', "key, token or password"),
    "cdsapirc key line": (
        f"key: 12345:{UUID}\nurl: https://cds.example.org/api",
        "key, token or password",
    ),
    "adsapirc key line": (f"key: {UUID}", "key, token or password"),
    "polytope key line": ('{"user_key": "abc123def456"}', "key, token or password"),
    "destine token": ("DESTINE_TOKEN=abc123def456", "key, token or password"),
    "JWT": (f"The answer carried {JWT} in it.", "key, token or password"),
    "bearer value": (f"Authorization: {BEARER}", "key, token or password"),
    "Jira token shape": (f"I pasted {JIRA_PAT_SHAPE} by mistake.", "key, token or password"),
    "JIRA_PAT assignment": ("JIRA_PAT=abc123def456", "key, token or password"),
    "session link": ("See https://example.org/page?token=abc123&x=1.", "sign-in or session link"),
    "GUID": (f"The record {UUID} was missing.", "GUID"),
    "name pair": ("I asked Alice Johnson about the step.", "name"),
    "honorific": ("Dr Smith said so.", "name"),
}
# Text a weather user must be able to file: coordinates, places, parameters, runs.
CLEAN = (
    "Point forecast for 51.4500N, -0.9700E (Reading) and 44.49, 11.34 (Bologna); 2t and tp from "
    "IFS HRES run 2026-11-01 00z, step 24. Compared with Buenos Aires and New York.\n"
    "Open Data returned HTTP 404 for the second file; the path was ~/data and /home/<user>/x.\n"
    "Write to someone@example.com. Set JIRA_PAT in your terminal; key: <your key>.\n"
)


def run(args, **kwargs):
    return fb.main([str(a) for a in args], **kwargs)


def write(tmp_path, text, name="feedback.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


DRAFT = (
    "# 2t from Open Data differs from the WMS chart\n\n"
    "### What happened\n\nThe 2t value at 51.45N, -0.97E was 281.2 K, the chart said 8 C.\n\n"
    "### Proposed fix\n\n- Say that the chart is in degrees Celsius, `2t` is in kelvin.\n"
)


# --- the marking constants ------------------------------------------------------------------------


def test_marking_constants():
    assert fb.REPO == "ecmwf/ecmwf-weather-skills"
    assert fb.REPO_LABEL == "ecmwf-weather-skills"
    assert fb.LABEL == "skill-feedback"
    assert fb.SUMMARY_PREFIX == "[ecmwf-weather]"
    assert fb.JIRA_URL == "https://jira.ecmwf.int"
    assert fb.JIRA_URL_VAR == "ECMWF_WEATHER_JIRA_URL"
    assert fb.JIRA_PROJECT == "AS"
    assert fb.JIRA_COMPONENT == "weather-skills"
    assert fb.JIRA_ISSUE_TYPES == ("Improvement", "Task")
    assert fb.JIRA_TOKEN_VAR == "JIRA_PAT"


def test_left_out_sentence_is_the_fixed_one():
    assert fb.LEFT_OUT_SENTENCE == (
        "The draft leaves out names, e-mail addresses, user names in file paths, keys and tokens."
    )


# --- draft ----------------------------------------------------------------------------------------


def test_draft_prints_prefixed_summary_label_and_repository_line_first(tmp_path, capsys):
    path = write(tmp_path, DRAFT)
    assert run(["draft", path]) == 0
    out = capsys.readouterr().out
    assert "Summary: [ecmwf-weather] 2t from Open Data differs from the WMS chart" in out
    assert "Labels: skill-feedback" in out
    body = out.split("-" * 72)[1].strip("\n")
    assert body.splitlines()[0] == "Skills repository: ecmwf/ecmwf-weather-skills"
    assert "### What happened" in body
    assert "# 2t from Open Data" not in body  # the title line is the summary, not the body


def test_draft_json(tmp_path, capsys):
    path = write(tmp_path, DRAFT)
    assert run(["draft", path, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"] == "[ecmwf-weather] 2t from Open Data differs from the WMS chart"
    assert data["labels"] == ["skill-feedback"]
    assert data["repository"] == "ecmwf/ecmwf-weather-skills"
    first = data["description"].splitlines()[0]
    assert first == "Skills repository: ecmwf/ecmwf-weather-skills"
    assert "The 2t value" in data["description"]


def test_draft_title_option_and_no_duplicate_repository_line(tmp_path, capsys):
    text = "Skills repository: ecmwf/ecmwf-weather-skills\n\nPlain text without a heading.\n"
    path = write(tmp_path, text)
    assert run(["draft", path, "--title", "A short title", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"] == "[ecmwf-weather] A short title"
    assert data["description"].count("Skills repository:") == 1


def test_draft_needs_a_title_and_a_body(tmp_path, capsys):
    assert run(["draft", write(tmp_path, "Just text.\n")]) == 2
    assert "title is required" in capsys.readouterr().err
    assert run(["draft", write(tmp_path, "# Only a title\n", "b.md")]) == 2
    assert run(["draft", write(tmp_path, "# " + "x" * 101 + "\n\ntext\n", "c.md")]) == 2
    assert run(["draft", tmp_path / "missing.md"]) == 2


def test_draft_writes_nothing(tmp_path):
    path = write(tmp_path, DRAFT)
    before = sorted(tmp_path.iterdir())
    assert run(["draft", path]) == 0
    assert sorted(tmp_path.iterdir()) == before


def test_draft_refuses_an_unclean_draft_and_never_shows_values(tmp_path, capsys):
    path = write(tmp_path, DRAFT + "\nMail me at alice@university.edu\n")
    assert run(["draft", path]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing is shown
    assert "e-mail address" in captured.err
    assert "alice" not in captured.err


def test_draft_scans_a_title_given_as_an_option(tmp_path, capsys):
    path = write(tmp_path, "text\n")
    assert run(["draft", path, "--title", "Mail alice@university.edu"]) == 1
    assert "alice" not in capsys.readouterr().err


# --- the privacy scan -----------------------------------------------------------------------------


@pytest.mark.parametrize("name", SAMPLES)
def test_scan_refuses_each_detectors_sample(name, tmp_path, capsys):
    text, kind = SAMPLES[name]
    path = write(tmp_path, f"# Title\n\n{text}\n")
    assert run(["scan", path]) == 1
    captured = capsys.readouterr()
    assert kind in captured.out
    assert "line 3" in captured.out
    # the refusal names kinds and lines, never the matched value
    for secret in (HEXKEY, UUID, JWT, BEARER, JIRA_PAT_SHAPE, "alice", "bob", "carol", "Johnson"):
        assert secret not in captured.out + captured.err


@pytest.mark.parametrize("name", SAMPLES)
def test_findings_in_json_carry_no_values(name):
    text, kind = SAMPLES[name]
    findings = fb.scan_text(text)
    assert findings and kind in {f["plain"] for f in findings}
    assert all(set(f) == {"kind", "plain", "severity", "line", "why"} for f in findings)


def test_scan_passes_coordinates_places_and_placeholders(tmp_path, capsys):
    assert fb.scan_text(CLEAN) == []
    assert run(["scan", write(tmp_path, CLEAN)]) == 0
    assert "clean" in capsys.readouterr().out


def test_scan_json_lists_kinds(tmp_path, capsys):
    text = f"Contact alice@university.edu\n{JWT}\n"
    assert run(["scan", write(tmp_path, text), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["clean"] is False
    assert data["kinds"] == ["e-mail address", "key, token or password"]
    assert [f["line"] for f in data["findings"]] == [1, 2]


def test_warnings_can_be_accepted_errors_cannot(tmp_path):
    assert fb.scan_text("The Foo Bar product.")  # a name-like pair
    assert fb.scan_text("The Foo Bar product.", accept=["Foo Bar"]) == []
    assert fb.scan_text("mail alice@university.edu", accept=["alice@university.edu"])
    assert fb.scan_text(f"key: 12345:{UUID}", accept=[f"key: 12345:{UUID}"])


def test_one_span_is_reported_once():
    # a hex key in a "key" line is one finding, not two
    assert len(fb.scan_text(f'"key": "{HEXKEY}"')) == 1


# --- e-mail addresses: only the reserved example domains pass -------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "someone@example.com",
        "someone@example.org",
        "someone@example.net",
        "someone@EXAMPLE.COM",
        "someone@mail.example.org",
        "someone@a.b.example.net",
    ],
)
def test_the_reserved_example_domains_and_their_subdomains_are_not_flagged(address):
    assert fb.scan_text(f"Write to {address} please.") == []


@pytest.mark.parametrize(
    "address",
    [
        "alice@notexample.com",
        "alice@myexample.org",
        "alice@badexample.net",
        "alice@example.com.university.edu",
        "alice@example.co",
        "alice@xexample.com",
        "alice@mail.notexample.org",
    ],
)
def test_a_domain_that_only_ends_like_an_example_domain_is_flagged(address):
    findings = fb.scan_text(f"Write to {address} please.")
    assert [f["plain"] for f in findings] == ["e-mail address"]


# --- bare tokens: base64 alphabets, not paths and not URLs ----------------------------------------

# Synthetic 40+ character tokens in every base64 alphabet. Built from short blocks so no value
# that looks like a secret sits in this file; every block mixes upper case, lower case and digits.
TOKEN_ALPHABETS = {
    "slash": "Nz" + "Ab3/" * 10 + "Qw",
    "plus": "Nz" + "Ab3+" * 10 + "Qw",
    "padding": "Nz" + "Ab3x" * 10 + "Qw" + "==",
    "slash plus and padding": "Nz" + "Ab3/Cd9+" * 5 + "Qw=",
    "url-safe hyphen": "Nz" + "Ab3-" * 10 + "Qw",
    "url-safe underscore": "Nz" + "Ab3_" * 10 + "Qw",
    "real-looking slash in the middle": "Xk7Qp2Lm9ZtR4vB8nW3/cY6hJ1dF5gS0aE2uIoPq",
}


@pytest.mark.parametrize("name", TOKEN_ALPHABETS)
@pytest.mark.parametrize(
    "template",
    [
        "I pasted {t} by mistake.",
        "{t}",
        "Authorization was {t}.",
        "data={t}",
        "See https://example.org/api/{t} for it.",
    ],
)
def test_a_bare_token_in_any_base64_alphabet_is_an_error(name, template):
    token = TOKEN_ALPHABETS[name]
    findings = fb.scan_text(template.format(t=token))
    assert findings, f"{name} scanned clean"
    assert {f["plain"] for f in findings} == {"key, token or password"}
    assert {f["severity"] for f in findings} == {"error"}


LONG_PATHS_AND_URLS = [
    "plugins/ecmwf-weather/skills/ecmwf-open-data/scripts/odCatalog2.py",
    "/home/<user>/projects/ecmwf-weather-skills/plugins/ecmwf-weather/skills/ecmwf-earthkit",
    "~/data/ERA5/2026/Reanalysis-t2m-Step12/Total_Precipitation_Accumulation_v2",
    "https://github.com/ecmwf/ecmwf-weather-skills/issues/1234/Some-Page-Name",
    "https://apps.ecmwf.int/webapps/opencharts/products/medium-z500-t850/MapForecast2",
    "https://data.ecmwf.int/forecasts/20261101/00z/ifs/0p25/oper/20261101000000-24h-oper-fc.grib2",
    "C:/Projects/Weather2026/ecmwf-open-data/scripts/Download-Latest-Run-2t.py",
]


@pytest.mark.parametrize("text", LONG_PATHS_AND_URLS)
def test_long_paths_and_urls_are_not_tokens(text):
    assert max(map(len, text.split("//"))) >= 40
    assert fb.scan_text(f"The file was {text} on disk.") == []


# --- jira-draft: the paste route ------------------------------------------------------------------


def test_jira_draft_prints_the_marking_and_the_wiki_description(tmp_path, capsys):
    assert run(["jira-draft", write(tmp_path, DRAFT)]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "Project: AS  https://jira.ecmwf.int/projects/AS/"
    assert "Summary: [ecmwf-weather] 2t from Open Data differs from the WMS chart" in lines
    assert "Labels: skill-feedback, ecmwf-weather-skills" in lines
    assert "Component: weather-skills" in lines
    assert "Issue type: Improvement (Task if AS stops offering Improvement)" in lines
    body = out.split("-" * 72)[1].strip("\n")
    assert body.splitlines()[0] == "Skills repository: ecmwf/ecmwf-weather-skills"
    assert "h3. What happened" in body  # wiki heading, not ###
    assert "* Say that the chart is in degrees Celsius, {{2t}} is in kelvin." in body


def test_issue_type_line_is_built_from_the_issue_types(monkeypatch):
    assert fb.jira_type_line() == "Issue type: Improvement (Task if AS stops offering Improvement)"
    monkeypatch.setattr(fb, "JIRA_ISSUE_TYPES", ("Story", "Bug"))
    assert fb.jira_type_line() == "Issue type: Story (Bug if AS stops offering Story)"
    monkeypatch.setattr(fb, "JIRA_ISSUE_TYPES", ("Story",))
    assert fb.jira_type_line() == "Issue type: Story"


def test_jira_draft_json(tmp_path, capsys):
    assert run(["jira-draft", write(tmp_path, DRAFT), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["project"] == "AS"
    assert data["project_url"] == "https://jira.ecmwf.int/projects/AS/"
    assert data["labels"] == ["skill-feedback", "ecmwf-weather-skills"]
    assert data["components"] == ["weather-skills"]
    assert data["issue_types"] == ["Improvement", "Task"]
    assert data["issue_type_line"].startswith("Issue type: Improvement")
    assert data["description"].splitlines()[0] == "Skills repository: ecmwf/ecmwf-weather-skills"


def test_jira_draft_link_is_the_real_host_even_with_the_override_set(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(fb.JIRA_URL_VAR, "http://127.0.0.1:9")
    assert run(["jira-draft", write(tmp_path, DRAFT)]) == 0
    assert "https://jira.ecmwf.int/projects/AS/" in capsys.readouterr().out


def test_jira_draft_refuses_an_unclean_draft_listing_kinds(tmp_path, capsys):
    path = write(tmp_path, DRAFT + f"\nPath /home/alice/x and {JWT}\n")
    assert run(["jira-draft", path]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "user name in a file path" in captured.err
    assert "key, token or password" in captured.err
    assert "alice" not in captured.err and JWT not in captured.err


def test_jira_text_is_scanned_again_after_the_conversion(tmp_path, capsys, monkeypatch):
    # the draft itself is clean; a conversion that produced a finding must be refused
    monkeypatch.setattr(fb, "to_wiki", lambda md: md + "\nwrite to alice@university.edu\n")
    assert run(["jira-draft", write(tmp_path, DRAFT)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "the JIRA text" in captured.err and "e-mail address" in captured.err
    assert "alice" not in captured.err


def test_wiki_conversion_escapes_markup():
    wiki = fb.to_wiki("### Head\n\n- item with {braces} and *stars* and `code_1`\n1. first\n")
    assert wiki.splitlines()[0] == "h3. Head"
    assert "* item with \\{braces\\} and \\*stars\\* and {{code\\_1}}" in wiki
    assert "# first" in wiki
    fenced = fb.to_wiki("```\n{noformat} *x*\n```\n")
    assert fenced.splitlines() == ["{noformat}", "{ noformat} *x*", "{noformat}"]
    assert fb.to_wiki("```\nunclosed\n").splitlines()[-1] == "{noformat}"


# --- routes ---------------------------------------------------------------------------------------


class Gh:
    """An injected runner: records the calls and answers like gh does."""

    def __init__(self, signed_in=True, reach=0, reach_err=""):
        self.signed_in, self.reach, self.reach_err = signed_in, reach, reach_err
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:3] == ["gh", "auth", "status"]:
            if self.signed_in:
                return fb.CommandResult(0, "", "Logged in to github.com account somebody")
            return fb.CommandResult(
                1, "", "You are not logged in to any GitHub hosts. gh auth login"
            )
        if argv[:2] == ["gh", "api"]:
            return fb.CommandResult(self.reach, '{"permissions": {}}', self.reach_err)
        raise AssertionError(f"unexpected command {argv}")


def test_routes_github_when_signed_in_with_access(capsys):
    gh = Gh()
    code = run(["routes", "--json"], which=lambda n: "/usr/bin/gh", runner=gh, env={})
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["gh"] == {"installed": True, "signed_in": True, "access": True, "problem": None}
    assert data["route"] == "github"
    assert data["jira_token"] == {"variable": "JIRA_PAT", "set": False}
    assert ["gh", "api", "repos/ecmwf/ecmwf-weather-skills"] in gh.calls


def test_routes_without_gh_is_the_paste_route(capsys):
    gh = Gh()
    code = run(["routes", "--json"], which=lambda n: None, runner=gh, env={})
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["gh"] == {
        "installed": False,
        "signed_in": False,
        "access": False,
        "problem": "missing",
    }
    assert data["route"] == "jira-draft"
    assert gh.calls == []  # gh is never run when it is not installed


def test_routes_gh_not_signed_in(capsys):
    gh = Gh(signed_in=False)
    run(["routes", "--json"], which=lambda n: "/usr/bin/gh", runner=gh, env={})
    data = json.loads(capsys.readouterr().out)
    assert data["gh"]["installed"] and not data["gh"]["signed_in"]
    assert data["gh"]["problem"] == "signin"
    assert data["route"] == "jira-draft"
    assert not any(c[:2] == ["gh", "api"] for c in gh.calls)


@pytest.mark.parametrize(
    ("err", "problem"),
    [
        ("gh: Not Found (HTTP 404)", "notfound"),
        ("gh: Forbidden (HTTP 403)", "forbidden"),
        ("dial tcp: lookup api.github.com: no such host", "network"),
        ("something else", "other"),
    ],
)
def test_routes_gh_without_access(err, problem, capsys):
    gh = Gh(reach=1, reach_err=err)
    run(["routes", "--json"], which=lambda n: "/usr/bin/gh", runner=gh, env={})
    data = json.loads(capsys.readouterr().out)
    assert data["gh"]["signed_in"] and not data["gh"]["access"]
    assert data["gh"]["problem"] == problem
    assert data["route"] == "jira-draft"


def test_routes_token_set_means_jira_file_when_github_is_closed(capsys):
    env = {"JIRA_PAT": DUMMY_PAT}
    run(["routes", "--json"], which=lambda n: None, runner=Gh(), env=env)
    data = json.loads(capsys.readouterr().out)
    assert data["jira_token"] == {"variable": "JIRA_PAT", "set": True}
    assert data["route"] == "jira-file"


def test_routes_github_wins_over_the_token(capsys):
    run(
        ["routes", "--json"],
        which=lambda n: "/usr/bin/gh",
        runner=Gh(),
        env={"JIRA_PAT": DUMMY_PAT},
    )
    assert json.loads(capsys.readouterr().out)["route"] == "github"


def test_routes_empty_token_counts_as_not_set(capsys):
    run(["routes", "--json"], which=lambda n: None, runner=Gh(), env={"JIRA_PAT": ""})
    assert json.loads(capsys.readouterr().out)["jira_token"]["set"] is False


def test_routes_text_output(capsys):
    run(["routes"], which=lambda n: "/usr/bin/gh", runner=Gh(), env={"JIRA_PAT": DUMMY_PAT})
    out = capsys.readouterr().out
    assert "gh installed: yes" in out and "gh signed in: yes" in out
    assert "gh can reach ecmwf/ecmwf-weather-skills: yes" in out
    assert "JIRA_PAT set: yes" in out
    assert "Route: github" in out


def test_no_output_ever_holds_the_token_value(tmp_path, capsys):
    env = {"JIRA_PAT": DUMMY_PAT}
    draft = write(tmp_path, DRAFT)
    seen = []
    for args in (
        ["routes"],
        ["routes", "--json"],
        ["draft", draft],
        ["draft", draft, "--json"],
        ["jira-draft", draft],
        ["jira-draft", draft, "--json"],
        ["scan", draft],
    ):
        for gh in (Gh(), Gh(signed_in=False), Gh(reach=1, reach_err="boom")):
            run(args, which=lambda n: "/usr/bin/gh", runner=gh, env=env)
            captured = capsys.readouterr()
            seen.append(captured.out + captured.err)
    run(["routes"], which=lambda n: None, runner=Gh(), env=env)
    captured = capsys.readouterr()
    seen.append(captured.out + captured.err)
    assert all(DUMMY_PAT not in text for text in seen)


def test_the_real_runner_reads_a_missing_program_as_exit_127():
    result = fb.run_process(["no-such-program-for-feedback-tests"])
    assert result.returncode == 127


# --- BLOCKED reports ------------------------------------------------------------------------------

BARRIER = {
    "blocked": "the GitHub CLI (gh) is not installed",
    "why": "filing on GitHub needs gh signed in",
    "user_steps": ["1. Install gh.", "2. Run `gh auth login` yourself."],
    "agent": "offer the JIRA or paste route; do not retry.",
}


@pytest.mark.parametrize("code", [2, 3, 4])
def test_blocked_report_and_exit_code(code, capsys):
    assert fb.blocked(BARRIER, code=code) == code
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert lines[0] == "BLOCKED: the GitHub CLI (gh) is not installed"
    assert lines[1].startswith("Why: ")
    assert lines[2] == "What the user needs to do:"
    assert lines[3] == "  1. Install gh."
    assert lines[-1].startswith("For the agent: ")


def test_blocked_json_goes_to_stdout(capsys):
    assert fb.blocked(BARRIER, as_json=True) == 4
    data = json.loads(capsys.readouterr().out)
    assert data["blocked"]["code"] == 4 and data["blocked"]["user_steps"][0] == "1. Install gh."


def test_paste_steps_name_the_project_component_and_type():
    text = "\n".join(fb.jira_paste_steps("/tmp/my draft.md"))
    assert "https://jira.ecmwf.int/projects/AS/" in text
    assert "weather-skills" in text and "Improvement" in text and "Task" in text
    assert "'/tmp/my draft.md'" in text


# --- the script as a program ----------------------------------------------------------------------


def test_script_is_standard_library_only_and_parses_with_python_3_9():
    tree = ast.parse(SCRIPT.read_text(), feature_version=(3, 9))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names)
    assert "# /// script" not in SCRIPT.read_text()


def test_script_has_the_spdx_header_and_a_shebang():
    head = SCRIPT.read_text().splitlines()[:6]
    assert head[0] == "#!/usr/bin/env python3"
    # REUSE-IgnoreStart — asserts on the script's header
    assert any("SPDX-License-Identifier: Apache-2.0" in line for line in head)
    assert any("SPDX-FileCopyrightText: 2026" in line for line in head)
    # REUSE-IgnoreEnd


def test_cli_exit_codes_and_clean_json_on_stdout(tmp_path):
    clean = write(tmp_path, DRAFT)
    dirty = write(tmp_path, DRAFT + "\nalice@university.edu\n", "dirty.md")
    ok = subprocess.run(
        [sys.executable, str(SCRIPT), "draft", str(clean), "--json"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert ok.returncode == 0
    assert json.loads(ok.stdout)["labels"] == ["skill-feedback"]
    bad = subprocess.run(
        [sys.executable, str(SCRIPT), "scan", str(dirty)], capture_output=True, text=True
    )
    assert bad.returncode == 1 and "e-mail address" in bad.stdout and "alice" not in bad.stdout
