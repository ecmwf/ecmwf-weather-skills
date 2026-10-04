# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Deterministic release steps (scripts/release.py, driven by the Makefile)."""

import pytest
import release

CL = """# Changelog

Intro.

## [Unreleased]

### Added

- A thing.

## [0.1.7] - 2026-10-04

### Fixed

- Old.

[Unreleased]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.7...HEAD
[0.1.7]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.6...0.1.7
"""


def test_move_unreleased():
    out = release.move_unreleased(CL, "0.1.8", "2026-10-05")
    assert "## [Unreleased]\n\n## [0.1.8] - 2026-10-05\n\n### Added\n\n- A thing.\n" in out
    assert (
        "[Unreleased]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.8...HEAD\n"
        "[0.1.8]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.7...0.1.8"
    ) in out


def test_move_unreleased_refuses_empty():
    empty = CL.replace("### Added\n\n- A thing.\n\n", "")
    with pytest.raises(release.ReleaseError, match="Unreleased"):
        release.move_unreleased(empty, "0.1.8", "2026-10-05")


def test_notes():
    out = release.move_unreleased(CL, "0.1.8", "2026-10-05")
    assert release.notes(out, "0.1.8").strip() == "### Added\n\n- A thing."
    with pytest.raises(release.ReleaseError):
        release.notes(out, "9.9.9")


@pytest.mark.parametrize(
    "new, ok",
    [
        ("0.1.8", True),
        ("0.2.0", True),
        ("0.1.7", False),
        ("0.1.6", False),
        ("v0.1.8", False),
        ("1.0.0", False),
    ],
)
def test_version_rules(new, ok):
    if ok:
        release.check_version("0.1.7", new)
    else:
        with pytest.raises(release.ReleaseError):
            release.check_version("0.1.7", new)


def test_major_bump_needs_explicit_permission():
    release.check_version("0.1.7", "1.0.0", allow_major=True)


def test_preflight_reports_every_problem():
    git = {"branch": "feat/x", "dirty": True, "behind": True, "tag_exists": True}
    probs = release.preflight_problems(git, CL, "0.1.7", "0.1.8")
    text = "\n".join(probs)
    for must in ("main", "uncommitted", "origin/main", "already exists"):
        assert must in text, must
    ok = {"branch": "main", "dirty": False, "behind": False, "tag_exists": False}
    assert release.preflight_problems(ok, CL, "0.1.7", "0.1.8") == []
