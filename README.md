# solarx-data

Orbit-data mirror for the SolarX AR app. A scheduled GitHub Action downloads a
few public data files, keeps only what the app uses, checks them, and publishes
**one static JSON file** to GitHub Pages:

    https://dunyakilavuz.com/solarx-data/v1/orbits.json

It is a plain file, not an API (no accounts, keys or queries). The app downloads
it periodically and computes every position on the device (SGP4 for satellites,
two-body orbits for comets and asteroids). Nothing is committed when the data
changes: each run builds the file and deploys it with `actions/deploy-pages`.

## Sources

| Dataset id | Source | Used for |
|---|---|---|
| `celestrak_stations` | CelesTrak GP `GROUP=stations` (TLE) | ISS (free), Tiangong (Pro) |
| `celestrak_visual` | CelesTrak GP `GROUP=visual` (TLE) | ~150 bright satellites (Pro) |
| `mpc_comets` | MPC `CometEls.txt` | comets (Pro) |
| `mpc_bright_asteroids` | MPC `Ephemerides/Bright/<year>/Soft00Bright.txt` | bright asteroids (Pro) |

Credits and terms: see [NOTICE](NOTICE). In short, MPC data files "may be
redistributed as long as the source for the data is clearly specified";
USSPACECOM gives blanket approval to redistribute TLE/OMM data with citation;
CelesTrak asks for one download per update (every 2 h) and no hammering on errors.

## Schedule and failure handling

* Runs at 05:23 and 17:23 UTC, on manual dispatch, and on pushes that change the
  script, site or workflow.
* CelesTrak: one request per group per run, no retries, no redirects followed.
* MPC: re-downloaded only when the published copy is 20 h or older (`--force`
  on manual runs overrides this); `data.minorplanetcenter.net` first, then
  `minorplanetcenter.net`.
* Each dataset is validated (TLE checksums, ISS present, newest element set
  under 5 days old; enough readable MPC lines). If a source fails, its data from
  the currently published file is carried over **with its original `fetchedAt`**
  so the app still ages it correctly, the file is deployed, and the run is
  marked failed (GitHub e-mails the owner). If there is no earlier data for a
  failed dataset, nothing is deployed and the old file stays live.
* The app stops showing a dataset when its own `fetchedAt` is too old
  (satellites 3 days, comets/asteroids 30 days) and asks for fresh data.
* GitHub disables scheduled workflows after 60 days without repository
  activity; the `keepalive` job re-enables the workflow through the API.

## File format (schema 1)

```json
{
  "schema": 1,
  "generatedAt": "2026-10-22T17:23:05Z",
  "publisher": "...",
  "about": "...",
  "sources": {
    "celestrak": {"name": "CelesTrak", "url": "https://celestrak.org", "attribution": "..."},
    "mpc": {"name": "IAU Minor Planet Center", "url": "https://minorplanetcenter.net", "attribution": "..."}
  },
  "datasets": {
    "celestrak_stations": {"source": "celestrak", "url": "...", "fetchedAt": "...Z", "format": "tle", "count": 17, "lines": ["ISS (ZARYA)", "1 25544U ...", "2 25544 ..."]},
    "celestrak_visual":   {"...": "same shape, format tle (name, line 1, line 2 per satellite)"},
    "mpc_comets":         {"...": "format mpc-comet: one MPC comet export line per comet"},
    "mpc_bright_asteroids": {"...": "format mpcorb: one MPCORB line per asteroid"}
  },
  "errors": []
}
```

Records are the source lines, unchanged apart from trailing spaces, so the app
keeps its validated fixed-column parsers. A breaking format change gets a new
path (`v2/...`); `v1` keeps being published until old app versions are gone.

## Local run

    python3 -m unittest discover tests
    python3 scripts/build.py --out site/v1/orbits.json
    # testing without hitting CelesTrak: --local-celestrak DIR (stations.tle, visual.tle)

Standard library only (Python 3.10+).

## Publishing (one-time)

1. Create the public repo `dunyakilavuz/solarx-data` and push this folder.
2. Settings → Pages → Source: **GitHub Actions**. No custom domain is needed:
   the user site `dunyakilavuz.github.io` already uses `dunyakilavuz.com`, so
   this project site is served at `https://dunyakilavuz.com/solarx-data/`.
3. Actions → "Update orbit data" → Run workflow (or push to main).
