import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import app_config  # noqa: E402

# A fixed sample, not the committed config/app_config.json: a bad hand edit
# of that file must not fail these tests (that would stop the orbit data
# from deploying); scripts/app_config.py handles it at publish time.
SAMPLE = {
    "schema": 1,
    "ads": {"real_ads": False, "interstitial": True, "rewarded": True, "inline": True,
            "interstitial_min_gap_s": 180, "interstitial_max_per_session": 3, "interstitial_launch_grace_s": 60},
    "min_app_build": None,
}


class AppConfigTest(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(SAMPLE)

    def test_sample_is_valid(self):
        self.assertEqual(app_config.check(self.cfg), [])

    def bad(self, change):
        c = copy.deepcopy(self.cfg)
        change(c)
        return app_config.check(c)

    def test_types_ranges_and_keys(self):
        self.assertTrue(self.bad(lambda c: c["ads"].update(real_ads="false")))
        self.assertTrue(self.bad(lambda c: c["ads"].update(inline=1)))
        self.assertTrue(self.bad(lambda c: c["ads"].update(interstitial_min_gap_s=59)))
        self.assertTrue(self.bad(lambda c: c["ads"].update(interstitial_min_gap_s=3601)))
        self.assertTrue(self.bad(lambda c: c["ads"].update(interstitial_min_gap_s=180.5)))
        self.assertTrue(self.bad(lambda c: c["ads"].update(interstitial_max_per_session=True)))
        self.assertTrue(self.bad(lambda c: c["ads"].update(interstitial_max_per_session=11)))
        self.assertTrue(self.bad(lambda c: c["ads"].update(interstitial_launch_grace_s=-1)))
        self.assertTrue(self.bad(lambda c: c["ads"].update(realads=True)))
        self.assertTrue(self.bad(lambda c: c.update(extra=1)))
        self.assertTrue(self.bad(lambda c: c.update(schema=2)))
        self.assertTrue(self.bad(lambda c: c.update(min_app_build=0)))
        self.assertEqual(self.bad(lambda c: c.update(min_app_build=120)), [])
        self.assertEqual(self.bad(lambda c: c["ads"].pop("rewarded")), [], "a missing field is fine (app default)")
        self.assertTrue(app_config.check([]))

    def run_main(self, src_text, previous_text=None):
        d = tempfile.mkdtemp()
        src, out, prev = (os.path.join(d, n) for n in ("src.json", "out.json", "prev.json"))
        with open(src, "w") as f:
            f.write(src_text)
        args = ["--src", src, "--out", out]
        if previous_text is not None:
            with open(prev, "w") as f:
                f.write(previous_text)
            args += ["--previous", prev]
        code = app_config.main(args)
        written = json.load(open(out)) if os.path.exists(out) else None
        return code, written

    def test_valid_is_written(self):
        code, out = self.run_main(json.dumps(self.cfg))
        self.assertEqual((code, out), (0, self.cfg))

    def test_invalid_republishes_previous(self):
        prev = copy.deepcopy(self.cfg)
        prev["ads"]["inline"] = False
        code, out = self.run_main("{ not json", json.dumps(prev))
        self.assertEqual((code, out), (2, prev))

    def test_invalid_without_previous_writes_nothing(self):
        self.assertEqual(self.run_main('{"schema": 1, "ads": {"real_ads": "no"}}'), (2, None))
        self.assertEqual(self.run_main("[]", "also bad"), (2, None))


if __name__ == "__main__":
    unittest.main()
