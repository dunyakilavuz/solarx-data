#!/usr/bin/env python3
"""Check config/app_config.json and copy it to site/v1/app_config.json.

The app reads this small settings file (ad switches and pacing). It is edited
by hand on GitHub, so it is checked strictly here before it is published:
known keys only, exact types, numbers inside the ranges the app accepts.

Exit codes:
  0  the file is valid and was written
  2  the file is invalid (or missing): the currently published copy is
     written again if it is valid, otherwise no app_config.json is written.
     Either way the orbit data still deploys; the workflow then fails so the
     owner gets an e-mail.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

SCHEMA = 1
BOOLS = ("real_ads", "interstitial", "rewarded", "inline")
# key: (min, max), whole numbers (the app clamps to the same ranges)
RANGES = {
    "interstitial_min_gap_s": (60, 3600),
    "interstitial_max_per_session": (0, 10),
    "interstitial_launch_grace_s": (0, 600),
}
TOP = {"schema", "ads", "min_app_build"}


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check(cfg) -> list[str]:
    """Problems with [cfg] (empty: valid)."""
    if not isinstance(cfg, dict):
        return ["the file must be a JSON object"]
    errs = [f"unknown key {k!r}" for k in cfg if k not in TOP]
    if cfg.get("schema") != SCHEMA or not is_int(cfg.get("schema")):
        errs.append(f"schema must be {SCHEMA}")
    ads = cfg.get("ads")
    if not isinstance(ads, dict):
        errs.append("ads must be an object")
    else:
        for k, v in ads.items():
            if k in BOOLS:
                if not isinstance(v, bool):
                    errs.append(f"ads.{k} must be true or false")
            elif k in RANGES:
                lo, hi = RANGES[k]
                if not is_int(v) or not lo <= v <= hi:
                    errs.append(f"ads.{k} must be a whole number from {lo} to {hi}")
            else:
                errs.append(f"unknown key 'ads.{k}'")
    b = cfg.get("min_app_build")
    if b is not None and (not is_int(b) or b < 1):
        errs.append("min_app_build must be null or a positive whole number")
    return errs


def load_text(src: str) -> str:
    if src.startswith(("http://", "https://")):
        req = urllib.request.Request(src, headers={"User-Agent": "solarx-data-mirror"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(65536).decode("utf-8")
    with open(src, encoding="utf-8") as f:
        return f.read()


def parse(text: str):
    try:
        return json.loads(text)
    except ValueError as e:
        return e


def write(cfg, out: str) -> None:
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="config/app_config.json")
    ap.add_argument("--out", default="site/v1/app_config.json")
    ap.add_argument("--previous", help="URL or path of the currently published file")
    a = ap.parse_args(argv)
    try:
        cfg = parse(load_text(a.src))
    except OSError as e:
        cfg = e
    errs = [f"not readable JSON: {cfg}"] if isinstance(cfg, Exception) else check(cfg)
    if not errs:
        write(cfg, a.out)
        print(f"app_config: {a.src} is valid, written to {a.out}")
        return 0
    for e in errs:
        print(f"::error::app_config.json: {e}")
    if a.previous:
        try:
            prev = parse(load_text(a.previous))
        except Exception as e:  # network, HTTP 404, decoding
            prev = e
        if not isinstance(prev, Exception) and not check(prev):
            write(prev, a.out)
            print("app_config: the currently published copy is published again")
            return 2
    print("app_config: no valid copy, app_config.json is not published (apps keep their cached copy)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
