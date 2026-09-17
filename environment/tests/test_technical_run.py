# -*- coding: utf-8 -*-
"""
The technical rehearsal, one round of eight purchases, checked without any provider: the
harness, the analyst and the freeze are stand-ins that leave the files the series layer
expects. What is checked is the arrangement: eight purchases, one per configuration, every
one marked DIAGNOSTIC / NOT COUNTED, nothing sent before every key and confirmation is in
place, a finished purchase never run again, the round spending from its own ledgers, and
one anchor over the whole round.
"""
import os, sys, json, glob, shutil, tempfile, datetime, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
for p in (BASE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import series, technical_run, providers, confirm

KEYS = ("PILOT_OPENAI_API_KEY", "PILOT_ANTHROPIC_API_KEY", "PILOT_GEMINI_API_KEY", "PILOT_XAI_API_KEY",
        "PILOT_MISTRAL_API_KEY", "PILOT_COHERE_API_KEY", "PILOT_INFOMANIAK_API_KEY",
        "PILOT_INFOMANIAK_PRODUCT_ID", "PILOT_DEEPSEEK_API_KEY")


class TestTechnicalRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tech-")
        self.saved = {k: getattr(series, k) for k in ("SERIES", "RUNS", "ANCHORS", "run_cmd")}
        self.saved_providers = (providers.MODELS, providers.BASE)
        providers.BASE = self.tmp          # keys come from the environment of the test only, never from .env
        series.SERIES = os.path.join(self.tmp, "series")
        series.RUNS = os.path.join(self.tmp, "runs")
        series.ANCHORS = os.path.join(self.tmp, "anchors")
        os.makedirs(series.SERIES)
        os.makedirs(series.RUNS)
        self.saved_env = dict(os.environ)
        os.environ["TEST_PURCHASE_APPROVALS"] = os.path.join(self.tmp, "approvals.jsonl")
        self.models = os.path.join(BASE, "models.json")     # the real file, all nine pinned
        self.calls = []
        series.run_cmd = self.fake_run_cmd
        self.n = 0

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(series, k, v)
        providers.MODELS, providers.BASE = self.saved_providers
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

    def plan_today(self, window="00:00-23:59"):
        today = datetime.date.today().isoformat()
        technical_run.plan(self.ns(start=today, seed=7, neomundi=False, window_utc=window,
                                   models=self.models))
        return series.current_series()

    def keys(self, *skip):
        for k in KEYS:
            if k not in skip:
                os.environ[k] = "stand-in"

    def confirm_command_a(self, sid, folder):
        providers.MODELS = os.path.join(folder, "models.json")
        confirm.record("command-a-03-2025", sid, 1, "owner", "10 USD", "yes")
        path = os.environ["TEST_PURCHASE_APPROVALS"]
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
        earlier = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=120)
        rows[0]["recorded_at_utc"] = earlier.isoformat(timespec="seconds").replace("+00:00", "Z")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(rows[0]) + "\n")
        confirm.record("command-a-03-2025", sid, 2, "owner", "10 USD", "yes again")

    def ready(self, window="00:00-23:59"):
        self.keys()
        sid, folder = self.plan_today(window)
        self.confirm_command_a(sid, folder)
        return sid, folder

    def test_one_round_of_eight(self):
        sid, folder = self.ready()
        self.assertTrue(sid.startswith("TECH-"))
        self.assertTrue(os.path.exists(os.path.join(folder, "DIAGNOSTIC.md")))
        pl = series.jload(os.path.join(folder, "plan.json"))
        self.assertFalse(pl["counted"])
        self.assertEqual((pl["days"], pl["models"]), (1, 8))
        derived = series.jload(os.path.join(folder, "models.json"))
        self.assertEqual(derived["limits"]["budget"]["per_scope"], {"USD": 10.0, "CHF": 2.0})
        self.assertEqual((derived["neomundi"]["max_requests_per_scope"],
                          derived["neomundi"]["max_requests_per_purchase"]), (400, 50))
        self.assertFalse(derived["neomundi"]["enabled"])

        technical_run.day(self.ns(override=None, dry=False))
        tags = self.harness_tags()
        self.assertEqual(sorted(tags), ["D01-%s" % c for c in "ABCDEFGH"])
        for run_dir in glob.glob(os.path.join(series.RUNS, "TP-*")):
            with open(os.path.join(run_dir, "RUN_STATUS.md"), encoding="utf-8") as f:
                self.assertIn("DIAGNOSTIC / NOT COUNTED", f.read())
            manifest = series.jload(os.path.join(run_dir, "run_manifest.json"))
            self.assertFalse(manifest["counted"])
            self.assertEqual(manifest["status"], "frozen")
        for name, a, env in self.calls:
            if name == "harness.py":
                seen = dict(os.environ, **env)
                self.assertEqual(seen["TEST_PURCHASE_BUDGET_SCOPE"], sid)
                self.assertIn("technical-%s" % sid, seen["TEST_PURCHASE_LEDGER"])
                self.assertIn("technical-%s-neomundi" % sid, seen["TEST_PURCHASE_NEOMUNDI_LEDGER"])
        # the round again: nothing runs twice
        technical_run.day(self.ns(override=None, dry=False))
        self.assertEqual(len(self.harness_tags()), 8)
        # one anchor over the whole round, final because all eight are frozen
        final = os.path.join(folder, "%s.rehearsal.final.json" % sid)
        anchor = series.jload(final)
        self.assertEqual((anchor["purchases_expected"], anchor["purchases_complete"]), (8, 8))
        self.assertEqual(anchor["unfrozen"], [])
        self.assertFalse(anchor["counted"])
        for key, entry in anchor["runs"].items():
            self.assertTrue(entry["run_manifest.json"] and entry["freeze_manifest.json"], key)

    def test_without_both_confirmations_of_command_a_nothing_is_sent(self):
        self.keys()
        sid, folder = self.plan_today()
        with self.assertRaises(SystemExit) as e:
            technical_run.day(self.ns(override=None, dry=False))
        self.assertIn("command-a-03-2025", str(e.exception))
        self.assertIn("stage 1 of 2", str(e.exception))
        self.assertEqual(self.harness_tags(), [])

    def test_a_missing_key_stops_the_round_before_any_call(self):
        self.keys("PILOT_DEEPSEEK_API_KEY")
        sid, folder = self.plan_today()
        self.confirm_command_a(sid, folder)
        with self.assertRaises(SystemExit) as e:
            technical_run.day(self.ns(override=None, dry=False))
        self.assertIn("PILOT_DEEPSEEK_API_KEY is absent", str(e.exception))
        self.assertEqual(self.harness_tags(), [])

    def test_a_closed_window_starts_nothing(self):
        sid, folder = self.ready(window="00:00-00:01")
        technical_run.day(self.ns(override=None, dry=False))
        self.assertEqual(self.harness_tags(), [])
        self.assertEqual({e["status"] for e in series.registry(folder)}, {"not_started"})

    def test_the_rehearsal_refuses_a_counted_series(self):
        today = datetime.date.today().isoformat()
        series.plan(self.ns(start=today, days=1, seed=1, models=self.models,
                            window_utc="00:00-23:59", technical=False))
        with self.assertRaises(SystemExit) as e:
            technical_run.day(self.ns(override=None, dry=False))
        self.assertIn("not a technical rehearsal", str(e.exception))


if __name__ == "__main__":
    unittest.main()
