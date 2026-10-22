#!/usr/bin/env python3
"""Build the SolarX AR orbit-data mirror (one JSON file).

Downloads a few plain public data files, keeps only what the app uses,
validates them and writes site/v1/orbits.json. Standard library only.

Sources (see README.md for terms and attribution):
  * CelesTrak GP element sets, groups "stations" and "visual" (TLE format)
  * IAU Minor Planet Center CometEls.txt and the bright-minor-planet file

Exit codes:
  0  everything fresh
  2  written, but at least one dataset was carried over from the previous
     published file because its source failed (the workflow deploys, then
     fails so the owner gets an e-mail)
  1  nothing usable for at least one dataset: nothing is written, nothing
     is deployed, the previously published file stays live
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

SCHEMA = 1
USER_AGENT = "solarx-data-mirror/1 (+https://dunyakilavuz.com/solarx-data/)"
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024  # the app rejects anything above 4 MB

CELESTRAK_GP = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
# MPC serves the same files from its data host and its main site; try both.
MPC_HOSTS = ("https://data.minorplanetcenter.net", "https://minorplanetcenter.net")
MPC_COMETS = "/iau/MPCORB/CometEls.txt"
MPC_BRIGHT = "/iau/Ephemerides/Bright/{year}/Soft00Bright.txt"

# MPC files change at most daily; don't re-download them on every run.
MPC_MIN_AGE_H = 20
# The app hides comets more than 365 days (asteroids 500) from the orbit's
# osculation epoch; keep a margin for the app's 30-day staleness limit.
COMET_EPOCH_WINDOW_D = 365 + 45
ASTEROID_EPOCH_WINDOW_D = 500 + 45

SOURCES = {
    "celestrak": {
        "name": "CelesTrak",
        "url": "https://celestrak.org",
        "attribution": "Satellite orbital elements from CelesTrak (Dr. T.S. Kelso, https://celestrak.org). "
                       "GP data originate from the 18th Space Defense Squadron, U.S. Space Force, via space-track.org.",
    },
    "mpc": {
        "name": "IAU Minor Planet Center",
        "url": "https://minorplanetcenter.net",
        "attribution": "Comet and minor-planet orbits from the International Astronomical Union's Minor Planet Center "
                       "(https://minorplanetcenter.net). This product has made use of data and/or services provided by "
                       "the International Astronomical Union's Minor Planet Center.",
    },
}


class SourceError(Exception):
    pass


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # CelesTrak answers outdated URLs with 301 and asks clients to stop, not follow.
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def http_get(url: str, *, retries: int = 0, allow_404: bool = False) -> str | None:
    """Plain GET. Any non-200 answer stops (no retry); only network errors
    are retried, and only when [retries] > 0 (never for CelesTrak)."""
    last = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(15 * attempt)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with _opener.open(req, timeout=60) as r:
                if r.status != 200:
                    raise SourceError(f"{url}: HTTP {r.status}")
                body = r.read(MAX_SOURCE_BYTES + 1)
                if len(body) > MAX_SOURCE_BYTES:
                    raise SourceError(f"{url}: larger than {MAX_SOURCE_BYTES} bytes")
                return body.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404 and allow_404:
                return None
            raise SourceError(f"{url}: HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
    raise SourceError(f"{url}: {last}")


# ---------------------------------------------------------------- satellites

def tle_checksum_ok(line: str) -> bool:
    if len(line) < 69 or not line[68].isdigit():
        return False
    s = sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line[:68])
    return s % 10 == int(line[68])


def tle_epoch(line1: str) -> dt.datetime:
    yy = int(line1[18:20])
    doy = float(line1[20:32])
    year = 1900 + yy if yy >= 57 else 2000 + yy
    return dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=doy - 1)


def parse_tle(text: str) -> list[list[str]]:
    """Name/line1/line2 triples with valid checksums and matching numbers."""
    lines = [l.rstrip() for l in text.replace("\r\n", "\n").split("\n") if l.strip()]
    out = []
    i = 0
    while i + 2 < len(lines):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 ") and not name.startswith(("1 ", "2 ")):
            if tle_checksum_ok(l1) and tle_checksum_ok(l2) and l1[2:7] == l2[2:7]:
                out.append([name.strip(), l1, l2])
            i += 3
        else:
            i += 1
    return out


LOCAL_CELESTRAK: str | None = None  # testing: read <dir>/<group>.tle instead of downloading


def build_satellites(group: str, now: dt.datetime, *, min_count: int, must_have: tuple[str, ...]) -> dict:
    url = CELESTRAK_GP.format(group=group)
    if LOCAL_CELESTRAK:
        with open(os.path.join(LOCAL_CELESTRAK, f"{group}.tle"), encoding="utf-8") as f:
            text = f.read()
    else:
        text = http_get(url)  # one request, no retries (CelesTrak policy)
    sats = parse_tle(text)
    if len(sats) < min_count:
        raise SourceError(f"{group}: only {len(sats)} element sets (expected >= {min_count})")
    numbers = {s[1][2:7].strip() for s in sats}
    for n in must_have:
        if n not in numbers:
            raise SourceError(f"{group}: catalog number {n} missing")
    newest = max(tle_epoch(s[1]) for s in sats)
    if now - newest > dt.timedelta(days=5):
        raise SourceError(f"{group}: newest element set is from {iso(newest)}")
    return {
        "source": "celestrak",
        "url": url,
        "fetchedAt": iso(now),
        "format": "tle",
        "count": len(sats),
        "lines": [l for s in sats for l in s],
    }


# ---------------------------------------------------------------- small bodies

def _jd(y: int, m: int, d: float) -> float:
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def _jd_now(t: dt.datetime) -> float:
    return t.timestamp() / 86400.0 + 2440587.5


def _num(line: str, a: int, b: int):
    try:
        return float(line[a - 1:b].strip())
    except ValueError:
        return None


def comet_epoch_jd(line: str) -> float | None:
    """Same columns the app reads (MPC 'Export Format for Comet Orbits')."""
    if len(line) < 103:
        return None
    vals = [_num(line, *c) for c in ((15, 18), (20, 21), (23, 29), (31, 39), (42, 49), (52, 59), (62, 69), (72, 79), (92, 95), (97, 100))]
    if any(v is None for v in vals):
        return None
    y, mo, d, q, e = vals[:5]
    if q <= 0 or e < 0 or not line[102:158].strip():
        return None
    ep = line[81:89].strip()
    if len(ep) == 8 and ep.isdigit():
        return _jd(int(ep[:4]), int(ep[4:6]), float(ep[6:8]))
    return _jd(int(y), int(mo), d)


_PACK = "123456789ABCDEFGHIJKLMNOPQRSTUV"


def mpcorb_epoch_jd(line: str) -> float | None:
    if len(line) < 103:
        return None
    p = line[20:25].strip()
    if len(p) != 5 or p[0] not in "IJKL":
        return None
    cent = {"I": 1800, "J": 1900, "K": 2000, "L": 2100}[p[0]]
    try:
        yy = int(p[1:3])
    except ValueError:
        return None
    mo, d = _PACK.find(p[3]) + 1, _PACK.find(p[4]) + 1
    if not 1 <= mo <= 12 or d < 1:
        return None
    for c in ((9, 13), (27, 35), (38, 46), (49, 57), (60, 68), (71, 79), (81, 91), (93, 103)):
        if _num(line, *c) is None:
            return None
    e, a = _num(line, 71, 79), _num(line, 93, 103)
    if e >= 1 or a <= 0:
        return None
    return _jd(cent + yy, mo, float(d))


def mpc_get(path: str, *, allow_404: bool = False) -> tuple[str | None, str]:
    """(text, url) from the first MPC host that answers; text None on 404."""
    err = None
    for host in MPC_HOSTS:
        try:
            return http_get(host + path, retries=1, allow_404=allow_404), host + path
        except SourceError as e:
            err = e
    raise err


def build_comets(now: dt.datetime) -> dict:
    text, url = mpc_get(MPC_COMETS)
    jd = _jd_now(now)
    lines, readable = [], 0
    for raw in text.replace("\r\n", "\n").split("\n"):
        l = raw.rstrip()
        ep = comet_epoch_jd(l)
        if ep is None:
            continue
        readable += 1
        if abs(jd - ep) <= COMET_EPOCH_WINDOW_D:
            lines.append(l)
    if readable < 300 or len(lines) < 100:
        raise SourceError(f"CometEls.txt: {readable} readable lines, {len(lines)} current (expected >= 300 / 100)")
    return {"source": "mpc", "url": url, "fetchedAt": iso(now), "format": "mpc-comet", "count": len(lines), "lines": lines}


def build_asteroids(now: dt.datetime) -> dict:
    text, url = None, None
    for year in (now.year, now.year - 1):  # next year's file appears late in the year
        text, url = mpc_get(MPC_BRIGHT.format(year=year), allow_404=True)
        if text is not None:
            break
    if text is None:
        raise SourceError("Soft00Bright.txt: not found for this or last year")
    jd = _jd_now(now)
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        l = raw.rstrip()
        ep = mpcorb_epoch_jd(l)
        if ep is not None and abs(jd - ep) <= ASTEROID_EPOCH_WINDOW_D:
            lines.append(l)
    if len(lines) < 50:
        raise SourceError(f"Soft00Bright.txt: only {len(lines)} usable lines")
    return {"source": "mpc", "url": url, "fetchedAt": iso(now), "format": "mpcorb", "count": len(lines), "lines": lines}


# ---------------------------------------------------------------- main

DATASETS = {
    # id (as used by the app): (source key, builder, minimum age before re-download)
    "celestrak_stations": ("celestrak", lambda t: build_satellites("stations", t, min_count=5, must_have=("25544",)), 0),
    "celestrak_visual": ("celestrak", lambda t: build_satellites("visual", t, min_count=50, must_have=()), 0),
    "mpc_comets": ("mpc", build_comets, MPC_MIN_AGE_H),
    "mpc_bright_asteroids": ("mpc", build_asteroids, MPC_MIN_AGE_H),
}


def load_previous(arg: str | None) -> dict:
    if not arg:
        return {}
    try:
        if arg.startswith("http"):
            text = http_get(arg, retries=1, allow_404=True)
            if text is None:
                return {}
        else:
            with open(arg, encoding="utf-8") as f:
                text = f.read()
        prev = json.loads(text)
        if prev.get("schema") != SCHEMA:
            return {}
        return prev.get("datasets", {})
    except Exception as e:  # first run, or the old file is unreadable
        print(f"note: previous file not used ({e})", file=sys.stderr)
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/v1/orbits.json")
    ap.add_argument("--previous", help="URL or path of the currently published file")
    ap.add_argument("--force", action="store_true", help="re-download MPC files even if recent")
    ap.add_argument("--local-celestrak", help="testing only: directory with stations.tle and visual.tle")
    args = ap.parse_args()
    global LOCAL_CELESTRAK
    LOCAL_CELESTRAK = args.local_celestrak

    now = now_utc()
    prev = load_previous(args.previous)
    datasets, errors, carried = {}, [], []
    for ds_id, (_, builder, min_age_h) in DATASETS.items():
        old = prev.get(ds_id)
        if old and min_age_h and not args.force:
            try:
                if now - parse_iso(old["fetchedAt"]) < dt.timedelta(hours=min_age_h):
                    datasets[ds_id] = old
                    print(f"{ds_id}: kept (fetched {old['fetchedAt']})")
                    continue
            except (KeyError, ValueError):
                pass
        try:
            datasets[ds_id] = builder(now)
            print(f"{ds_id}: {datasets[ds_id]['count']} records")
        except SourceError as e:
            errors.append(f"{ds_id}: {e}")
            if old:
                datasets[ds_id] = old
                carried.append(ds_id)
                print(f"{ds_id}: FAILED, carried over data fetched {old.get('fetchedAt')}: {e}", file=sys.stderr)
            else:
                print(f"{ds_id}: FAILED and no previous data: {e}", file=sys.stderr)

    if len(datasets) != len(DATASETS):
        print("not writing anything; the published file stays as it is", file=sys.stderr)
        return 1

    doc = {
        "schema": SCHEMA,
        "generatedAt": iso(now),
        "publisher": "SolarX AR orbit-data mirror, https://dunyakilavuz.com/solarx-data/",
        "about": "Selected public orbital elements, re-published unchanged (only filtered) so the SolarX AR app can "
                 "download one small file instead of querying the original services. Positions are computed on the device.",
        "sources": SOURCES,
        "datasets": datasets,
        "errors": errors,
    }
    data = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    if len(data) > MAX_OUTPUT_BYTES:
        print(f"output is {len(data)} bytes, over the {MAX_OUTPUT_BYTES} limit", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, args.out)
    print(f"wrote {args.out}: {len(data)} bytes")
    return 2 if carried else 0


if __name__ == "__main__":
    sys.exit(main())
