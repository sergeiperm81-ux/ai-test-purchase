# -*- coding: utf-8 -*-
"""
The scheme of the technical rehearsal, "5 one-off + 3 x 2", checked without any provider:
the harness, the analyst and the freeze are stand-ins that leave the files the series layer
expects. What is checked is the arrangement: which purchases run on which day, that every
one is marked DIAGNOSTIC / NOT COUNTED, that a finished purchase is never run again, and
that the rehearsal spends from its own ledgers.
"""
import os, sys, json, glob, shutil, tempfile, datetime, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
for p in (BASE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import series, technical_run


class TestTechnicalRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tech-")
        self.saved = {k: getattr(series, k) for k in ("SERIES", "RUNS", "ANCHORS", "run_cmd")}
        series.SERIES = os.path.join(self.tmp, "series")
        series.RUNS = os.path.join(self.tmp, "runs")
        series.ANCHORS = os.path.join(self.tmp, "anchors")
        os.makedirs(series.SERIES)
        os.makedirs(series.RUNS)
        self.saved_env = dict(os.environ)
        # the environment's real models file, with the analyst pinned as the rehearsal requires
        data = json.load(open(os.path.join(BASE, "models.json"), encoding="utf-8"))
        for m in data["auxiliary_models"]:
            m["expected_observed_models"] = [m["model"]]
        self.models = os.path.join(self.tmp, "models.json")
        json.dump(data, open(self.models, "w", encoding="utf-8"))
        self.calls = []
        series.run_cmd = self.fake_run_cmd
        self.n = 0

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(series, k, v)
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_run_cmd(self, args, timeout=1800, extra_env=None):
        """Stands in for every subprocess of the series layer and leaves what it would leave."""
        self.calls.append((args[0], list(args), dict(extra_env or {})))
        if args[0] == "harness.py":
            self.n += 1
            tag = args[args.index("--tag") + 1]
            model = args[args.index("--model") + 1]
            run_id = "TP-%s-%04d" % (tag, self.n)
            run_dir = os.path.join(series.RUNS, run_id)
            os.makedirs(run_dir)
            series.jdump(os.path.join(run_dir, "manifest.json"),
                         {"run_id": run_id, "model_requested": model, "model_reported": model,
                          "provider": {"provider": "stub"}})
            return 0, "Run:    %s\n" % run_id, ""
        if args[0] == "freeze_matrix.py":
            series.jdump(os.path.join(args[1], "freeze_manifest.json"), {"files": {}})
        return 0, "", ""

    def harness_tags(self):
        return [a[a.index("--tag") + 1] for name, a, _ in self.calls if name == "harness.py"]

    def ns(self, **kw):
        return types.SimpleNamespace(**kw)

    def test_five_one_offs_plus_three_by_two(self):
        today = datetime.date.today().isoformat()
        technical_run.plan(self.ns(start=today, seed=7, neomundi=False,
                                   window_utc="00:00-23:59", models=self.models))
        sid, folder = series.current_series()
        self.assertTrue(sid.startswith("TECH-"))
        self.assertTrue(os.path.exists(os.path.join(folder, "DIAGNOSTIC.md")))
        pl = series.jload(os.path.join(folder, "plan.json"))
        self.assertFalse(pl["counted"])
        derived = series.jload(os.path.join(folder, "models.json"))
        self.assertEqual(len(derived["models"]), 3)
        self.assertEqual(len(derived["auxiliary_models"]), 6)      # the analyst and the five others
        self.assertEqual(derived["limits"]["budget"]["per_scope"], {"USD": 10.0, "CHF": 2.0})
        self.assertFalse(derived["neomundi"]["enabled"])

        technical_run.day(self.ns(day=1, override=None, dry=False))
        tags = self.harness_tags()
        self.assertEqual(len(tags), 8)
        self.assertEqual(sorted(t for t in tags if t.startswith("D01-")), ["D01-A", "D01-B", "D01-C"])
        self.assertEqual(sorted(t for t in tags if t.startswith("DIAG-")),
                         ["DIAG-COHERE", "DIAG-DEEPSEEK", "DIAG-GOOGLE", "DIAG-MISTRAL", "DIAG-XAI"])
        # every purchase of the rehearsal is marked, frozen, and spends from its own ledger
        for run_dir in glob.glob(os.path.join(series.RUNS, "TP-*")):
            status = open(os.path.join(run_dir, "RUN_STATUS.md"), encoding="utf-8").read()
            self.assertIn("DIAGNOSTIC / NOT COUNTED", status)
            manifest = series.jload(os.path.join(run_dir, "run_manifest.json"))
            self.assertFalse(manifest["counted"])
            self.assertEqual(manifest["status"], "frozen")
        for name, a, env in self.calls:
            if name == "harness.py":
                seen = dict(os.environ, **env)      # what the subprocess would actually receive
                self.assertEqual(seen["TEST_PURCHASE_BUDGET_SCOPE"], sid)
                self.assertIn("technical-%s" % sid, seen["TEST_PURCHASE_LEDGER"])
                self.assertIn("technical-%s-neomundi" % sid, seen["TEST_PURCHASE_NEOMUNDI_LEDGER"])
        self.assertEqual(sum(1 for e in technical_run.oneoffs(folder) if e["status"] == "frozen"), 5)

        # day 1 again: nothing runs twice
        before = len(self.harness_tags())
        technical_run.day(self.ns(day=1, override=None, dry=False))
        self.assertEqual(len(self.harness_tags()), before)

        # day 2: the three of the series only, no one-offs
        technical_run.day(self.ns(day=2, override="mock: day 2 run on the same date", dry=False))
        tags = self.harness_tags()
        self.assertEqual(sorted(t for t in tags if t.startswith("D02-")), ["D02-A", "D02-B", "D02-C"])
        self.assertEqual(len(tags), 11)
        reg = series.registry(folder)
        self.assertEqual(sorted(e["status"] for e in reg if e["day"] == 2), ["frozen"] * 3)

    def test_the_rehearsal_refuses_a_counted_series(self):
        today = datetime.date.today().isoformat()
        series.plan(self.ns(start=today, days=1, seed=1, models=self.models,
                            window_utc="00:00-23:59", technical=False))
        with self.assertRaises(SystemExit) as e:
            technical_run.day(self.ns(day=1, override=None, dry=False))
        self.assertIn("not a technical rehearsal", str(e.exception))


if __name__ == "__main__":
    unittest.main()
