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

import series, technical_run, providers


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

    def test_five_one_offs_plus_three_by_two(self):
        sid, folder = self.confirmed_and_keyed()
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

    def plan_today(self, **patch):
        today = datetime.date.today().isoformat()
        technical_run.plan(self.ns(start=today, seed=7, neomundi=False,
                                   window_utc=patch.get("window", "00:00-23:59"), models=self.models))
        return series.current_series()

    def test_without_both_confirmations_of_command_a_nothing_is_sent(self):
        os.environ["TEST_PURCHASE_APPROVALS"] = os.path.join(self.tmp, "approvals.jsonl")
        for k in ("PILOT_OPENAI_API_KEY", "PILOT_ANTHROPIC_API_KEY", "PILOT_GEMINI_API_KEY",
                  "PILOT_XAI_API_KEY", "PILOT_MISTRAL_API_KEY", "PILOT_COHERE_API_KEY",
                  "PILOT_INFOMANIAK_API_KEY", "PILOT_INFOMANIAK_PRODUCT_ID", "PILOT_DEEPSEEK_API_KEY"):
            os.environ[k] = "stand-in"
        sid, folder = self.plan_today()
        with self.assertRaises(SystemExit) as e:
            technical_run.day(self.ns(day=1, override=None, dry=False))
        self.assertIn("command-a-03-2025", str(e.exception))
        self.assertIn("stage 1 of 2", str(e.exception))
        self.assertEqual(self.harness_tags(), [])            # zero external calls

    def test_a_missing_key_of_a_one_off_stops_the_day_before_any_call(self):
        os.environ["TEST_PURCHASE_APPROVALS"] = os.path.join(self.tmp, "approvals.jsonl")
        for k in ("PILOT_OPENAI_API_KEY", "PILOT_ANTHROPIC_API_KEY", "PILOT_INFOMANIAK_API_KEY",
                  "PILOT_INFOMANIAK_PRODUCT_ID"):
            os.environ[k] = "stand-in"
        os.environ.pop("PILOT_DEEPSEEK_API_KEY", None)
        sid, folder = self.plan_today()
        with self.assertRaises(SystemExit) as e:
            technical_run.day(self.ns(day=1, override=None, dry=False))
        self.assertIn("PILOT_DEEPSEEK_API_KEY is absent", str(e.exception))
        self.assertEqual(self.harness_tags(), [])

    def confirmed_and_keyed(self):
        import confirm
        os.environ["TEST_PURCHASE_APPROVALS"] = os.path.join(self.tmp, "approvals.jsonl")
        for k in ("PILOT_OPENAI_API_KEY", "PILOT_ANTHROPIC_API_KEY", "PILOT_GEMINI_API_KEY",
                  "PILOT_XAI_API_KEY", "PILOT_MISTRAL_API_KEY", "PILOT_COHERE_API_KEY",
                  "PILOT_INFOMANIAK_API_KEY", "PILOT_INFOMANIAK_PRODUCT_ID", "PILOT_DEEPSEEK_API_KEY"):
            os.environ[k] = "stand-in"
        sid, folder = self.plan_today()
        # two confirmations, the first backdated so that the second is allowed
        providers.MODELS = os.path.join(folder, "models.json")
        confirm.record("command-a-03-2025", sid, 1, "owner", "10 USD", "yes")
        rows = [json.loads(l) for l in open(os.environ["TEST_PURCHASE_APPROVALS"], encoding="utf-8")]
        earlier = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=120)
        rows[0]["recorded_at_utc"] = earlier.isoformat(timespec="seconds").replace("+00:00", "Z")
        with open(os.environ["TEST_PURCHASE_APPROVALS"], "w", encoding="utf-8") as f:
            f.write(json.dumps(rows[0]) + "\n")
        confirm.record("command-a-03-2025", sid, 2, "owner", "10 USD", "yes again")
        return sid, folder

    def test_one_offs_do_not_start_after_the_series_part_stopped(self):
        sid, folder = self.confirmed_and_keyed()
        original = self.fake_run_cmd

        def harness_hits_the_ceiling(args, timeout=1800, extra_env=None):
            if args[0] == "harness.py" and "D01-" in args[args.index("--tag") + 1]:
                self.calls.append((args[0], list(args), dict(extra_env or {})))
                return 6, "Run:    TP-%s-x\n" % args[args.index("--tag") + 1], "CLOSURE: budget_exceeded"
            return original(args, timeout, extra_env)

        series.run_cmd = harness_hits_the_ceiling
        technical_run.day(self.ns(day=1, override=None, dry=False))
        tags = self.harness_tags()
        self.assertTrue(all(t.startswith("D01-") for t in tags))      # no DIAG-* at all
        self.assertEqual(technical_run.oneoffs(folder), [])

    def test_one_offs_respect_the_window(self):
        sid, folder = self.confirmed_and_keyed()
        pl = series.jload(os.path.join(folder, "plan.json"))
        pl["window_utc"] = "00:00-00:01"
        series.jdump(os.path.join(folder, "plan.json"), pl)
        technical_run.day(self.ns(day=1, override=None, dry=False))
        self.assertEqual(self.harness_tags(), [])
        statuses = {e["configuration_id"]: e["status"] for e in technical_run.oneoffs(folder)}
        self.assertEqual(set(statuses.values()), {"not_started"})
        self.assertEqual(len(statuses), 5)

    def test_the_rehearsal_anchor_pins_all_eleven(self):
        sid, folder = self.confirmed_and_keyed()
        technical_run.day(self.ns(day=1, override=None, dry=False))
        provisional = glob.glob(os.path.join(folder, "%s.rehearsal.*.json" % sid))
        self.assertTrue(provisional)
        anchor = series.jload(provisional[-1])
        self.assertEqual((anchor["purchases_expected"], anchor["purchases_complete"]), (11, 8))
        self.assertEqual(anchor["state"], "provisional")
        technical_run.day(self.ns(day=2, override="mock: day 2 on the same date", dry=False))
        final = os.path.join(folder, "%s.rehearsal.final.json" % sid)
        self.assertTrue(os.path.exists(final))
        anchor = series.jload(final)
        self.assertEqual(anchor["purchases_complete"], 11)
        self.assertEqual(anchor["unfrozen"], [])
        self.assertFalse(anchor["counted"])
        for key, entry in anchor["runs"].items():
            self.assertTrue(entry["run_manifest.json"] and entry["freeze_manifest.json"], key)
        self.assertEqual(sum(1 for k in anchor["runs"] if "ONEOFF" in k), 5)

    def test_the_rehearsal_refuses_a_counted_series(self):
        today = datetime.date.today().isoformat()
        series.plan(self.ns(start=today, days=1, seed=1, models=self.models,
                            window_utc="00:00-23:59", technical=False))
        with self.assertRaises(SystemExit) as e:
            technical_run.day(self.ns(day=1, override=None, dry=False))
        self.assertIn("not a technical rehearsal", str(e.exception))


if __name__ == "__main__":
    unittest.main()
