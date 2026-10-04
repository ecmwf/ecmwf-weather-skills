<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)

SPDX-License-Identifier: Apache-2.0
-->

# Web map — customise, deploy, other libraries

## Contents
- Files written by `webmap.py create`
- Customise
- Serve and deploy
- MapLibre GL snippet
- OpenLayers snippet

## Files written by `webmap.py create`

| File | Role |
|---|---|
| `index.html` | Layout: map + side panel (layer select, time slider, legend, click info, meteogram) |
| `app.js` | Leaflet WMS layers, GetCapabilities parsing, slider, GetFeatureInfo, meteogram fetch |
| `style.css` | Layout; responsive below 800 px |
| `config.json` | `wms`, `token` (always `public`), `layers`, `center`, `zoom` |

## Customise

- Layers / start view: edit `config.json` or re-run `create` with `--layers`, `--center`, `--zoom`.
- Base map: `app.js` uses the WMS `background` layer (no third-party tiles, no extra attribution).
  To use OpenStreetMap instead, replace it with
  `L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {attribution: '© OpenStreetMap contributors'})`
  and respect the OSM tile usage policy.
- Style of a layer: add `styles: 'STYLE'` to the data layer options (names from
  `wms.py layers --search NAME --json`).
- Keep the attribution line; add the CAMS sentence if you add `composition_*` layers (app.js does
  this automatically when such a layer is selected).

## Serve and deploy

- Local: `python3 scripts/webmap.py serve DIR [--port 8000] [--host 0.0.0.0]`. The server caches
  the latest IFS run's surface fields in `DIR/.cache/` (~300 MB, replaced when a new run appears)
  and one JSON + PNG per 0.25° cell.
- Static hosting (GitHub Pages, S3): copy `index.html`, `app.js`, `style.css`, `config.json`.
  Everything works except meteograms; point `api/` at a host running `webmap.py serve` behind a
  reverse proxy if needed.
- The meteogram endpoints: `GET /api/meteogram.png?lat=&lon=` (PNG) and
  `GET /api/point.json?lat=&lon=` (the open-data `odpoint.py --json` output).

## MapLibre GL snippet

```js
map.addSource('ecmwf', {
  type: 'raster', tileSize: 256,
  tiles: ['https://eccharts.ecmwf.int/wms/?token=public&service=WMS&version=1.3.0&request=GetMap' +
          '&layers=msl_public&styles=&format=image/png&transparent=true&crs=EPSG:3857' +
          '&width=256&height=256&bbox={bbox-epsg-3857}&time=2026-10-03T12:00:00Z'],
  attribution: '© ECMWF, CC BY 4.0'
});
map.addLayer({ id: 'ecmwf', type: 'raster', source: 'ecmwf' });
// change time: map.getSource('ecmwf').setTiles([url with new time])
```

## OpenLayers snippet

```js
new ol.layer.Tile({
  source: new ol.source.TileWMS({
    url: 'https://eccharts.ecmwf.int/wms/?token=public',
    params: { LAYERS: 't850_public', TIME: '2026-10-03T12:00:00Z', TRANSPARENT: true },
    attributions: '© ECMWF, CC BY 4.0',
  }),
});
// change time: source.updateParams({ TIME: newTime })
```
