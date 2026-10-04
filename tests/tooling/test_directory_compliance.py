# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Requirements of the Anthropic and OpenAI plugin directories for the submitted folder."""

import json
import re
import struct
from urllib.parse import urlparse

from conftest import ROOT

PLUGIN = ROOT / "plugins" / "ecmwf-weather"
CODEX = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())


def _words_outside_code(md: str) -> int:
    return len(re.sub(r"```.*?```", "", md, flags=re.S).split())


def test_no_top_level_bin():
    # claude.ai and Cowork refuse plugins with a top-level bin/
    assert not (PLUGIN / "bin").exists()


def test_plugin_folder_has_readme_and_licence():
    assert _words_outside_code((PLUGIN / "README.md").read_text()) >= 40
    assert (PLUGIN / "LICENSE").read_text() == (ROOT / "LICENSE").read_text()


def test_readme_discloses_every_host_the_scripts_contact():
    readme = (PLUGIN / "README.md").read_text()
    hosts = set()
    for f in (PLUGIN / "skills").rglob("*.py"):
        for url in re.findall(r"https://[A-Za-z0-9.-]+", f.read_text()):
            host = urlparse(url).hostname
            if host and "." in host:
                hosts.add(host)
    missing = sorted(h for h in hosts if h not in readme)
    assert not missing, f"undisclosed hosts in plugins/ecmwf-weather/README.md: {missing}"


def test_square_icon_for_openai():
    icon = PLUGIN / "assets" / "ecmwf-icon.png"
    head = icon.read_bytes()[:24]
    w, h = struct.unpack(">II", head[16:24])
    assert head[:8] == b"\x89PNG\r\n\x1a\n" and w == h >= 48 and icon.stat().st_size < 5 * 2**20
    assert (
        CODEX["interface"]["logo"]
        == CODEX["interface"]["composerIcon"]
        == "./assets/ecmwf-icon.png"
    )


def test_codex_interface_limits_and_urls():
    i = CODEX["interface"]
    assert len(i["displayName"]) <= 30 and len(i["shortDescription"]) <= 30
    assert len(i["longDescription"]) <= 4000 and len(i["developerName"]) <= 80
    assert 1 <= len(i["capabilities"]) <= 20 and len(i["defaultPrompt"]) <= 3
    assert all(len(p) <= 128 for p in i["defaultPrompt"])
    assert i["privacyPolicyURL"] == "https://www.ecmwf.int/en/privacy"
    assert i["termsOfServiceURL"] == "https://www.ecmwf.int/en/terms-use"
    assert i["supportURL"] == "https://support.ecmwf.int"
    assert i["websiteURL"] == "https://www.ecmwf.int"  # the organisation, not the repository
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", i["brandColor"])


def test_skill_text_is_provider_neutral():
    for f in (PLUGIN / "skills").rglob("SKILL.md"):
        assert not re.search(r"\bClaude\b|\bChatGPT\b|\bCodex\b", f.read_text()), f
