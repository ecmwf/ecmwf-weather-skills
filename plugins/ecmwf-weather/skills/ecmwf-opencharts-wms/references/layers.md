<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)

SPDX-License-Identifier: Apache-2.0
-->

# ecCharts WMS layers

List the live catalogue with `python3 scripts/wms.py layers [--public] [--search TEXT] --json`;
this file explains what the names mean. (To be regenerated weekly from GetCapabilities.)

## Contents
- Public met layers (token=public)
- CAMS composition layers
- Static layers
- Styles and legends
- Dimensions
- GetFeatureInfo output

## Public met layers (token=public)

| Name | Shows | Units in GetFeatureInfo |
|---|---|---|
| `msl_public` | Mean sea level pressure, IFS HRES | Pa |
| `z500_public` | 500 hPa geopotential | m² s⁻² (÷ 9.81 for metres) |
| `t850_public` | 850 hPa temperature | K |
| `ws850_public` | 850 hPa wind speed | m s⁻¹ |
| `*_mean_public` | IFS ENS mean of the above (`msl`, `z500`, `t850`, `ws850`) | as above |
| `*_spread_public` | IFS ENS spread of the above | as above |

Valid times run from ~5 days in the past to +240 h: 6-hourly to +144 h, then 12-hourly.

Tropical cyclones (no `_public` suffix, available with `token=public`):
`genesis_hr`, `genesis_ts`, `genesis_td` (strike probabilities for hurricane / tropical storm /
depression), `aifs_ens_genesis_*` (AIFS ENS versions), `named_cyclones`, `named_cyclones_tracks`.

There are **no public 2 m temperature, precipitation or 10 m wind layers** — use OpenCharts
images (`opencharts.py search "2 m temperature"`) or render Open Data with the `ecmwf-earthkit` skill.

## CAMS composition layers

~90 layers prefixed `composition_` (Copernicus Atmosphere Monitoring Service): `composition_pm2p5`,
`composition_pm10`, `composition_aod550`, `composition_uvindex`, `composition_o3sfc`,
`composition_europe_pol_birch_forecast_surface` (pollen), `composition_co2_totalcolumn`, … Some
are only produced from the 00 UTC run up to +120 h (see each layer's abstract with
`wms.py layers --search NAME --json`). Attribution adds the CAMS sentence.

## Static layers

`background` (styles such as `cream_sky_background`, `grey_background`,
`evergreen_navy_background`), `foreground` (coastlines and borders), `boundaries`, `grid`,
`rivers`. They have no time dimension — never send `TIME` with them.

## Styles and legends

Each layer lists styles (first is the default), e.g. `msl_public`: `ct_blk_i5_t2` (black contours
every 5 hPa), `ct_blk_i5_t1_hilo_label` (with H/L and labels), `ct_red_i5_t2`. Legend image:
`wms.py legend --layer NAME [--style STYLE] -o legend.png` (GetLegend, 350×50 px).

## Dimensions

- `time` — ISO 8601 valid time, list plus `start/end/period` intervals; `default` = latest
  analysis. Expand with `wms.py times --layer NAME`.
- Some layers add `level`, `stream`, `type`, `step_range` — see GetCapabilities.

## GetFeatureInfo output

`wms.py info` (or `info_format=application/json`) returns
`{"Probes": [{"Name", "Grid point latitude", "Grid point longitude", "Distance", "Value": {"Unit", "Data"}}]}`
for the nearest gridpoint. Convert K → °C and Pa → hPa for display.
