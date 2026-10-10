<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# Filing the earthkit issues — execution guide

Approved by the maintainer (Tiago Quintino) on 2026-10-08: file all nine drafts in
`issues.toml`, one GitHub issue each, from his account (`gh`, logged in as `tlmquintino`).

## Rules

1. **One issue per draft, created exactly once.** Before creating, check for an existing issue
   (yours or anyone's): `gh issue list -R <repo> --state all --search "<keywords>"`. If one
   exists, don't file; report its URL instead.
2. **Re-run the reproducer first** (`uv run --script docs/upstream/<script>`) against the latest
   release. If it no longer reproduces, don't file; report that.
3. **No data uploads.** No GRIB, NetCDF, images or attachments. Every example builds its data
   in the code (small numpy/xarray arrays) or fetches it with earthkit itself
   (`ekd.from_source("sample", "test.grib")`), so a reader runs one script.
4. **Reproduce with code.** A short, self-contained script in the body with pinned versions in
   PEP 723 metadata (`uv run --script repro.py`), plus the exact output or traceback (trimmed to
   the relevant lines). Rewrite the draft reproducer into a clean example: no `REPRODUCED`
   markers, no try/except unless it shows the point, comments only where they help.
5. **Tone: Tiago's.** Plain, direct, polite, short sentences, British spelling ("behaviour",
   "licence"). Write as a colleague reporting what they found. No AI tells: no "I hope this
   helps", "Great question", "delve", "robust", "seamless", "comprehensive", no bold-heavy
   formatting, no emojis, no em-dash chains, no bullet lists where a sentence works. Never
   mention AI, agents writing the report, or tools used to find it. A one-line "Thanks!" at the
   end is fine.
6. Don't invent effort or history ("I spent days…"). Stick to facts in the draft and what the
   reproducer shows.

## Body template

```markdown
Hi,

<One or two sentences: what I was doing and what goes wrong.>

To reproduce (earthkit-X <version>, Python 3.x, Linux):

​```python
# /// script
# dependencies = ["earthkit-X==<version>", ...]
# ///
<clean minimal code>
​```

Running it with `uv run --script repro.py` gives:

​```
<trimmed output / traceback>
​```

I'd expect <expected>.

<Suggested fix, one or two sentences, phrased as a suggestion ("Looks like … — maybe …").>
For now <workaround> works around it.

Thanks!
```

Title: the `title` from `issues.toml`, reworded only if it reads unnaturally.

## After filing

Record in `issues.toml`: `status = "filed"` and `url = "<issue URL>"`; then
`python3 scripts/upstream_drafts.py` to regenerate `README.md`.

## Assignment

| Repository | Drafts (`script`) |
|---|---|
| ecmwf/earthkit-meteo | meteo-wet-bulb-bisect-2d.py, meteo-scores-extra-name.py, meteo-xarray-output-keeps-input-name.py |
| ecmwf/earthkit-plots | plots-spaghetti-xarray.py, plots-quickplot-doc-ekp-plot.py, plots-timeseries-quantiles-xarray.py |
| ecmwf/earthkit-transforms | transforms-spatial-reduce-0-360.py, transforms-spatial-reduce-return-as-pandas.py |
| ecmwf/earthkit-utils | utils-convert-units-fieldlist-noop.py |

## Log

| Script | URL | Notes |
|---|---|---|
| meteo-wet-bulb-bisect-2d.py | https://github.com/ecmwf/earthkit-meteo/issues/212 | filed 2026-10-08 |
| meteo-scores-extra-name.py | https://github.com/ecmwf/earthkit-meteo/issues/213 | filed 2026-10-08 |
| meteo-xarray-output-keeps-input-name.py | https://github.com/ecmwf/earthkit-meteo/issues/214 | filed 2026-10-08 |
| plots-spaghetti-xarray.py | https://github.com/ecmwf/earthkit-plots/issues/260 | filed 2026-10-08 |
| plots-quickplot-doc-ekp-plot.py | https://github.com/ecmwf/earthkit-plots/issues/261 | filed 2026-10-08 |
| plots-timeseries-quantiles-xarray.py | https://github.com/ecmwf/earthkit-plots/issues/262 | filed 2026-10-08 |
| transforms-spatial-reduce-0-360.py | https://github.com/ecmwf/earthkit-transforms/issues/133 | filed 2026-10-08 |
| transforms-spatial-reduce-return-as-pandas.py | https://github.com/ecmwf/earthkit-transforms/issues/134 | filed 2026-10-08 |
| utils-convert-units-fieldlist-noop.py | https://github.com/ecmwf/earthkit-utils/issues/88 | filed 2026-10-08 |
| (fix) earthkit-data XArrayGeography message | https://github.com/ecmwf/earthkit-data/pull/1181 | PR opened 2026-10-08, merged 2026-10-09 |
| data-mars-relative-date-string.py | https://github.com/ecmwf/earthkit-data/issues/1185 | filed 2026-10-10 |
| (docs) thermofeel examples | https://github.com/ecmwf/thermofeel/pull/59 | PR opened 2026-10-10 |
