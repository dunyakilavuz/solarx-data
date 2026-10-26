# solarx-data

Orbit-data mirror for the SolarX AR app. A scheduled GitHub Action downloads a
few public data files, keeps only what the app uses, checks them, and publishes
**one static JSON file** to GitHub Pages:

    https://dunyakilavuz.com/solarx-data/v1/orbits.json

The same deploy also publishes a small hand-edited settings file, see
[App config](#app-config-v1app_configjson):

    https://dunyakilavuz.com/solarx-data/v1/app_config.json

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
  scripts, site, config or workflow.
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

## App config (`v1/app_config.json`)

A small settings file for the app's ads, edited by hand in
[`config/app_config.json`](config/app_config.json). Like `orbits.json` it is a
plain public file: the app downloads it with one GET, sends nothing about the
user, and makes no request at all for Pro users.

```json
{
  "schema": 1,
  "ads": {
    "real_ads": false,
    "interstitial": true,
    "rewarded": true,
    "inline": true,
    "interstitial_min_gap_s": 180,
    "interstitial_max_per_session": 3,
    "interstitial_launch_grace_s": 60
  },
  "min_app_build": null
}
```

| Key | Meaning | Allowed |
|---|---|---|
| `schema` | file format version (a breaking change gets a new path, `v2/...`) | `1` |
| `ads.real_ads` | `false`: release builds use Google's **test** ads. `true` (or the key missing): release builds use the real AdMob units. Debug builds always use test ads. | `true` / `false` |
| `ads.interstitial` | full-screen ads at natural pauses | `true` / `false` |
| `ads.rewarded` | "watch an ad to unlock for tonight" offers | `true` / `false` |
| `ads.inline` | the one inline ad card on Explore | `true` / `false` |
| `ads.interstitial_min_gap_s` | minimum seconds between two full-screen ads | 60 – 3600 |
| `ads.interstitial_max_per_session` | full-screen ads per app session (0 = none) | 0 – 10 |
| `ads.interstitial_launch_grace_s` | no full-screen ad this long after launch | 0 – 600 |
| `min_app_build` | reserved (not used by the app yet) | `null` or a build number |

Publishing: `scripts/app_config.py` checks the file strictly (known keys,
exact types, ranges) and copies it to `site/v1/app_config.json` in every run of
the workflow; a push that changes `config/**` starts a run. If the file is
invalid, the currently published copy is published again (or none, if there is
no valid one), the orbit data still deploys, and the run is marked failed.

How the app uses it: it downloads the file once per launch and again on resume
when the last try is 6 h old, keeps the last good copy, and falls back to it
(or to built-in defaults, which equal the values above except `real_ads`) when
the file is missing, unreachable or unusable. The app ignores unknown or
ill-typed fields one by one and clamps numbers to the ranges above. Pages serves
the file with a 10-minute cache, so a change reaches apps within about
10 minutes plus the next launch (or the next resume after 6 h).

Each push that changes the file also rebuilds `orbits.json` (one CelesTrak
download per group). CelesTrak asks for at most one download per 2-hour update,
so avoid editing the file within 2 hours after a scheduled run (or in quick
succession).

## Local run

    python3 -m unittest discover tests
    python3 scripts/build.py --out site/v1/orbits.json
    python3 scripts/app_config.py   # checks config/app_config.json -> site/v1/app_config.json
    # testing without hitting CelesTrak: --local-celestrak DIR (stations.tle, visual.tle)

Standard library only (Python 3.10+).

## Publishing (one-time)

1. Create the public repo `dunyakilavuz/solarx-data` and push this folder.
2. Settings → Pages → Source: **GitHub Actions**. No custom domain is needed:
   the user site `dunyakilavuz.github.io` already uses `dunyakilavuz.com`, so
   this project site is served at `https://dunyakilavuz.com/solarx-data/`.
3. Actions → "Update orbit data" → Run workflow (or push to main).
