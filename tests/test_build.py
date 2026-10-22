"""Offline tests for scripts/build.py (no network): python3 -m unittest discover tests"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build  # noqa: E402

ISS = ["ISS (ZARYA)",
       "1 25544U 98067A   26294.04791053  .00009575  00000+0  18188-3 0  9996",
       "2 25544  51.6327  37.2623 0006759 289.5321  70.4938 15.48967427591263"]
NOW = dt.datetime(2026, 10, 22, 12, tzinfo=dt.timezone.utc)
COMET = ("    CK25A060  2025 11  8.5222  0.529824  0.995612  132.9563  108.0989  143.6635  20261021   8.6  4.0  "
         "C/2025 A6 (Lemmon)                                       MPC xxxxx")
OLD_COMET = COMET.replace("20261021", "20201021")
CERES = ("00001    3.35  0.15 K25BL 231.53975   73.29974   80.24963   10.58789  0.0795763  0.21429712   2.7656157  0 "
         "MPO950947  7369 126 1801-2025 0.69 M-v 30k MPC        0000              (1) Ceres")


class TleTests(unittest.TestCase):
    def test_checksum_and_epoch(self):
        self.assertTrue(build.tle_checksum_ok(ISS[1]))
        self.assertTrue(build.tle_checksum_ok(ISS[2]))
        self.assertFalse(build.tle_checksum_ok(ISS[1][:-1] + "0"))
        self.assertEqual(build.tle_epoch(ISS[1]).date(), dt.date(2026, 10, 21))

    def test_parse_skips_damaged_sets(self):
        bad = ["BROKEN", ISS[1][:-1] + "0", ISS[2]]
        text = "\r\n".join(ISS + bad + ISS) + "\r\n"
        self.assertEqual(build.parse_tle(text), [ISS, ISS])

    def test_satellites_require_iss_and_recent_epoch(self):
        with mock.patch.object(build, "http_get", return_value="\n".join(ISS)):
            ds = build.build_satellites("stations", NOW, min_count=1, must_have=("25544",))
            self.assertEqual(ds["lines"], ISS)
            with self.assertRaises(build.SourceError):
                build.build_satellites("stations", NOW + dt.timedelta(days=9), min_count=1, must_have=("25544",))
            with self.assertRaises(build.SourceError):
                build.build_satellites("stations", NOW, min_count=1, must_have=("48274",))
        with mock.patch.object(build, "http_get", return_value="No GP data found"):
            with self.assertRaises(build.SourceError):
                build.build_satellites("stations", NOW, min_count=1, must_have=())


class SmallBodyTests(unittest.TestCase):
    def test_epochs(self):
        self.assertAlmostEqual(build.comet_epoch_jd(COMET), build._jd(2026, 10, 21.0))
        self.assertAlmostEqual(build.mpcorb_epoch_jd(CERES), build._jd(2025, 11, 21.0))
        self.assertIsNone(build.comet_epoch_jd("short line"))
        self.assertIsNone(build.mpcorb_epoch_jd("Des'n     H     G   Epoch" + " " * 100))

    def test_comet_window_filter(self):
        text = "\n".join([COMET] * 150 + [OLD_COMET] * 200)
        with mock.patch.object(build, "mpc_get", return_value=(text, "u")):
            ds = build.build_comets(NOW)
        self.assertEqual(ds["count"], 150)

    def test_empty_comet_file_is_an_error(self):
        with mock.patch.object(build, "mpc_get", return_value=("<html>error</html>", "u")):
            with self.assertRaises(build.SourceError):
                build.build_comets(NOW)


class MainTests(unittest.TestCase):
    def _run(self, *args):
        with mock.patch.object(sys, "argv", ["build.py", *args]):
            return build.main()

    def test_failed_source_without_previous_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v1", "orbits.json")
            fail = mock.Mock(side_effect=build.SourceError("down"))
            ok = lambda t: {"fetchedAt": build.iso(t), "count": 1, "lines": ["x"]}
            ds = {k: (s, ok, 0) for k, (s, _, _) in build.DATASETS.items()}
            ds["mpc_comets"] = ("mpc", fail, 0)
            with mock.patch.dict(build.DATASETS, ds):
                self.assertEqual(self._run("--out", out), 1)
            self.assertFalse(os.path.exists(out))

    def test_failed_source_is_carried_over_with_its_old_time(self):
        with tempfile.TemporaryDirectory() as d:
            prev = os.path.join(d, "prev.json")
            old = {"fetchedAt": "2026-10-01T00:00:00Z", "count": 1, "lines": ["old"]}
            with open(prev, "w") as f:
                json.dump({"schema": 1, "datasets": {k: old for k in build.DATASETS}}, f)
            out = os.path.join(d, "orbits.json")
            ok = lambda t: {"fetchedAt": build.iso(t), "count": 1, "lines": ["new"]}
            ds = {k: (s, ok, 0) for k, (s, _, _) in build.DATASETS.items()}
            ds["celestrak_visual"] = ("celestrak", mock.Mock(side_effect=build.SourceError("HTTP 403")), 0)
            with mock.patch.dict(build.DATASETS, ds):
                self.assertEqual(self._run("--out", out, "--previous", prev), 2)
            doc = json.load(open(out))
            self.assertEqual(doc["datasets"]["celestrak_visual"], old)
            self.assertEqual(doc["datasets"]["celestrak_stations"]["lines"], ["new"])
            self.assertEqual(len(doc["errors"]), 1)

    def test_recent_mpc_data_is_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as d:
            prev = os.path.join(d, "prev.json")
            recent = {"fetchedAt": build.iso(build.now_utc() - dt.timedelta(hours=3)), "count": 1, "lines": ["mpc"]}
            with open(prev, "w") as f:
                json.dump({"schema": 1, "datasets": {"mpc_comets": recent, "mpc_bright_asteroids": recent}}, f)
            called = []
            ok = lambda t: called.append(1) or {"fetchedAt": build.iso(t), "count": 1, "lines": ["n"]}
            ds = {k: (s, ok, a) for k, (s, _, a) in build.DATASETS.items()}
            with mock.patch.dict(build.DATASETS, ds):
                self.assertEqual(self._run("--out", os.path.join(d, "o.json"), "--previous", prev), 0)
            self.assertEqual(len(called), 2)  # only the two CelesTrak groups


if __name__ == "__main__":
    unittest.main()
