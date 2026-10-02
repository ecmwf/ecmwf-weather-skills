# Attribution — ECMWF Open Data

Source: ECMWF Terms of Use, https://apps.ecmwf.int/datasets/licences/general/ (CC-BY-4.0).

## Service built on the data (apps, websites, agents)

```
This service is based on data and products of the European Centre for Medium-Range Weather Forecasts (ECMWF).
Source: www.ecmwf.int
This ECMWF data is published under a Creative Commons Attribution 4.0 International (CC BY 4.0). https://creativecommons.org/licenses/by/4.0/
ECMWF does not accept any liability whatsoever for any error or omission in the data, their availability, or for any loss or damage arising from their use.
```

## Data products (files, datasets, figures)

```
© <year> European Centre for Medium-Range Weather Forecasts (ECMWF).
Source: www.ecmwf.int
This data is published under a Creative Commons Attribution 4.0 International (CC BY 4.0). https://creativecommons.org/licenses/by/4.0/
ECMWF does not accept any liability whatsoever for any error or omission in the data, their availability, or for any loss or damage arising from their use.
```

Add a line stating modifications where applicable, e.g. "Values interpolated to a point and
converted to °C."

## Short form

`Data: © <year> ECMWF, CC BY 4.0` — for chat answers, chart footers and map attribution controls,
linking to the full notice where the medium allows.

## HTML snippet

```html
<div class="ecmwf-attribution">
  Data: © <span class="year"></span> <a href="https://www.ecmwf.int">ECMWF</a>,
  <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>.
  ECMWF accepts no liability for errors or omissions.
</div>
<script>document.querySelectorAll('.ecmwf-attribution .year').forEach(e => e.textContent = new Date().getFullYear());</script>
```

## Logo

The ECMWF logo is not covered by CC-BY. Do not place it in apps or maps except as a link to
www.ecmwf.int.

Best-guess wording pending ECMWF confirmation — see `docs/research/open-questions.md` in the repo.
