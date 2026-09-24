# -*- coding: utf-8 -*-
"""
Tests of the call log, the adapters, the limits, the spend ceiling and the NeoMundi link.
No paid call is made: every provider and NeoMundi are a local mock server.

Run: python -m pytest tests -q      (or: python -m unittest discover tests)
"""
import os, sys, json, glob, shutil, tempfile, subprocess, threading, datetime, unittest
import fsio
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
for p in (BASE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import usage, call_log, providers, neomundi_client, adapter_anthropic, confirm
from mock_server import MockServer, ok, openai_body

SECRET = "not-a-real-key-4f9a2b"   # a stand-in, never a key shape, so scanners stay calm
SECRETS = {"TESTKEY_OA": SECRET, "TESTKEY_AN": SECRET + "-an", "TESTKEY_NM": SECRET + "-nm",
           "TESTPID": "777"}

RATES = {"as_at": "test", "configurations": {
    "oa:oa-model": {"currency": "USD", "input": 0.40, "cached_input": 0.10, "output": 1.60},
    "an:anth-model": {"currency": "USD", "input": 1.00, "cached_input": 0.10, "cache_write": 1.25, "output": 5.00},
    "in:info-model": {"currency": "CHF", "input": 0.20, "cached_input": None, "output": 0.75},
    "mistral:mistral-small-2603": {"currency": "USD", "input": 0.15, "cached_input": None, "output": 0.60},
    "co:conf-model": {"currency": "USD", "input": 2.50, "cached_input": None, "output": 10.00},
    "oa:e2e-model": {"currency": "USD", "input": 0.40, "cached_input": 0.10, "output": 1.60},
    "g:gemini-like": {"currency": "USD", "input": 0.30, "cached_input": 0.03, "output": 2.50},
    "x:long": {"currency": "USD", "input": 1.25, "cached_input": 0.20, "output": 2.50,
               "over_200k": {"input": 2.50, "cached_input": 0.40, "output": 5.00}}},
    "legacy_by_model_name": {
        "gpt-4.1": {"currency": "USD", "input": 2, "output": 8},
        "gpt-4.1-mini": {"currency": "USD", "input": 0.4, "output": 1.6}}}


def cfg(model, rate_key, url, path, **extra):
    base = {"configuration_id": "test/" + model, "model": model, "provider": "P",
            "rate_key": rate_key, "protocol": "openai_chat", "base_url": url + path,
            "key_env": "TESTKEY_OA", "max_output_tokens": 100}
    base.update(extra)
    return base


def models_file(folder, url, **over):
    data = {
        "pilot": {"pilot_id": "P-TEST", "scenario_id": "S-TEST"},
        "limits": {"max_model_calls_per_purchase": 50, "max_tool_rounds_per_turn": 2,
                   "max_tool_calls_per_response": 2, "max_tool_calls_per_turn": 4,
                   "max_material_operations_per_purchase": 5,
                   "max_call_retries": 3, "max_analyst_attempts": 3,
                   "budget": {"per_utc_day": {"USD": 100, "CHF": 100},
                              "per_scope": {"USD": 100, "CHF": 100}}},
        "neomundi": {"enabled": False, "base_url": url + "/nm", "observe_path": "/v1/govern",
                     "contract_path": "/v1/rgc/contracts/{request_id}", "key_env": "TESTKEY_NM",
                     "mode": "OBS", "observe_roles": ["agent"], "max_attempts": 3,
                     "max_observations_per_scope": 100, "max_observations_per_purchase": 50,
                     "max_contracts_per_scope": 100, "max_contracts_per_purchase": 50,
                     "max_prompt_chars": 200000},
        "models": [
            cfg("oa-model", "oa:oa-model", url, "/oa/v1", max_tokens_param="max_completion_tokens"),
            cfg("anth-model", "an:anth-model", url, "/anth/v1", protocol="anthropic_messages",
                key_env="TESTKEY_AN", max_output_tokens=200, cache={"mode": "ephemeral_5m"}),
            cfg("info-model", "in:info-model", url, "/info/2/ai/{product_id}/openai/v1",
                product_id_env="TESTPID"),
            cfg("conf-model", "co:conf-model", url, "/oa/v1",
                confirmation_required={"stages": 2, "min_seconds_between": 60}),
            cfg("nomax-model", "oa:oa-model", url, "/oa/v1", max_output_tokens=None),
            cfg("e2e-model", "oa:e2e-model", url, "/oa/v1", max_output_tokens=500,
                reasoning_effort="none")],
        "roles": {"analyst": {"model": "oa-model"}}}
    for k, v in over.items():
        if k == "models_patch":
            for m in data["models"]:
                m.update(v.get(m["model"], {}))
        else:
            data[k].update(v)
    path = os.path.join(folder, "models.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def all_bytes(folder):
    out = b""
    for root, _, names in os.walk(folder):
        for n in names:
            with open(os.path.join(root, n), "rb") as f:
                out += f.read()
    return out


def copy_environment(tmp):
    env_dir = os.path.join(tmp, "env")
    os.makedirs(env_dir)
    for pattern in ("*.py", "*.json", "*.txt"):
        for p in glob.glob(os.path.join(BASE, pattern)):
            if not os.path.basename(p).startswith(("run_log", "analyst_log")):
                shutil.copy(p, env_dir)
    import simulator    # the prompts are wherever the environment itself finds them
    shutil.copytree(simulator.BOTS, os.path.join(tmp, "methodology", "Bot prompts"))
    shutil.copy(usage.RATES_FILE, os.path.join(env_dir, "rates.json"))
    return env_dir


class RigCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rig-")
        self.mock = MockServer()
        self.saved_env = dict(os.environ)
        os.environ.update(SECRETS)
        os.environ["TEST_PURCHASE_LEDGER"] = os.path.join(self.tmp, "ledger", "spend.jsonl")
        os.environ["TEST_PURCHASE_NEOMUNDI_LEDGER"] = os.path.join(self.tmp, "ledger", "neomundi.jsonl")
        os.environ["TEST_PURCHASE_APPROVALS"] = os.path.join(self.tmp, "approvals", "confirmations.jsonl")
        os.environ["TEST_PURCHASE_RETRY_SCALE"] = "0"
        os.environ["TEST_PURCHASE_BUDGET_SCOPE"] = "test-scope"
        os.environ.pop("TEST_PURCHASE_BUDGET_CAPS", None)
        rates = os.path.join(self.tmp, "rates.json")
        with open(rates, "w", encoding="utf-8") as f:
            json.dump(RATES, f)
        self._rates, usage.RATES_FILE = usage.RATES_FILE, rates
        self._models = providers.MODELS
        self.use_models()
        self.run_dir = os.path.join(self.tmp, "RUN-T")
        os.makedirs(self.run_dir)

    def use_models(self, **over):
        providers.MODELS = models_file(self.tmp, self.mock.url, **over)

    def recorder(self):
        neomundi_client.freeze_config(self.run_dir)
        return call_log.Recorder(self.run_dir, "RUN-T", {"pilot_id": "P-TEST"},
                                 providers.limits(), observer=neomundi_client.observe)

    def tearDown(self):
        self.mock.stop()
        usage.RATES_FILE = self._rates
        providers.MODELS = self._models
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def calls(self):
        return call_log.attempts(self.run_dir)

    def chat(self, model="oa-model", content="x", rec=None, **kw):
        return providers.chat(model, [{"role": "user", "content": content}],
                              recorder=rec or self.recorder(), **kw)


class TestCallLog(RigCase):
    def test_exact_request_bytes_recorded_and_no_secret_anywhere(self):
        self.mock.route("/oa/", ok(openai_body()))
        msg, meta = self.chat(content="héllo")
        self.assertEqual(msg["content"], "ok")
        sent = self.mock.to("/oa/")[0]
        line = self.calls()[0]
        saved = fsio.read_bytes(os.path.join(self.run_dir, line["request_file"]))
        self.assertEqual(saved, sent["body"])
        self.assertEqual(line["request_sha256"], call_log.sha256_bytes(sent["body"]))
        self.assertEqual(json.loads(saved)["max_completion_tokens"], 100)
        self.assertEqual(sent["headers"]["authorization"], "Bearer " + SECRET)
        self.assertNotIn(SECRET.encode(), all_bytes(self.tmp))
        self.assertEqual(meta["usage_normalised"]["cached_input_tokens"], 40)
        self.assertNotIn("usage", meta)

    def test_request_is_on_disk_before_sending_and_an_interrupted_attempt_keeps_counting(self):
        seen = {}

        def killed(url, headers, body, timeout):
            last = call_log.attempts(self.run_dir)[-1]
            seen["outcome"] = last["outcome"]
            seen["same_bytes"] = fsio.read_bytes(os.path.join(self.run_dir, last["request_file"])) == body
            raise RuntimeError("the process dies while the request is out")

        original, providers._send = providers._send, killed
        try:
            with self.assertRaises(RuntimeError):
                self.chat()
        finally:
            providers._send = original
        self.assertEqual(seen, {"outcome": "interrupted", "same_bytes": True})
        state = list(call_log.ledger_state().values())
        self.assertEqual(len(state), 1)
        self.assertFalse(state[0]["settled"])
        self.assertGreater(call_log.spent(scope="test-scope")["USD"], 0)

    def test_retry_repeats_only_the_failed_call_after_retry_after(self):
        self.mock.route("/oa/", [(503, {"Retry-After": "0"}, b"overloaded"), ok(openai_body())])
        self.chat()
        lines = self.calls()
        self.assertEqual([l["outcome"] for l in lines], ["failed", "completed"])
        self.assertEqual({l["operation_id"] for l in lines}, {"C0001"})
        self.assertEqual(lines[0]["http_status"], 503)
        self.assertEqual(lines[0]["wait_before_next_attempt_s"], 0.0)
        self.assertEqual(lines[0]["request_sha256"], lines[1]["request_sha256"])
        self.assertTrue(os.path.exists(os.path.join(self.run_dir, lines[0]["response_file"])))
        state = list(call_log.ledger_state().values())
        self.assertIsNone(state[0]["actual"])          # keeps counting at its reservation
        self.assertIsNotNone(state[1]["actual"])

    def test_no_retry_on_a_client_error(self):
        self.mock.route("/oa/", (400, {}, b"bad request"))
        with self.assertRaises(call_log.ProviderCallFailed):
            self.chat()
        self.assertEqual(len(self.mock.to("/oa/")), 1)

    def test_retries_are_bounded(self):
        self.mock.route("/oa/", (503, {}, b"down"))
        with self.assertRaises(call_log.ProviderCallFailed):
            self.chat()
        self.assertEqual(len(self.mock.to("/oa/")), 4)
        self.assertTrue(all(l["outcome"] == "failed" for l in self.calls()))

    def test_incomplete_body_is_a_technical_failure(self):
        self.mock.route("/oa/", [ok(b'{"choices": []}'), ok(openai_body())])
        self.chat()
        self.assertEqual([l["outcome"] for l in self.calls()], ["failed", "completed"])

    def test_budget_ceiling_refuses_before_sending(self):
        os.environ["TEST_PURCHASE_BUDGET_CAPS"] = json.dumps(
            {"per_utc_day": {"USD": 0.0000001}, "per_scope": {"USD": 1}})
        with self.assertRaises(call_log.BudgetExceeded):
            self.chat()
        self.assertEqual(self.mock.to("/oa/"), [])
        self.assertEqual(self.calls(), [])

    def test_a_request_over_its_size_limit_is_not_sent(self):
        rec = self.recorder()
        rec.limits = dict(rec.limits, max_request_bytes={"agent": 10, "analyst": 10})
        with self.assertRaises(call_log.LimitExceeded) as e:
            self.chat(rec=rec, content="x" * 50, role="agent")
        self.assertIn("the limit is 10", str(e.exception))
        self.assertEqual(self.mock.to("/oa/"), [])
        rec.limits = dict(rec.limits, max_request_bytes={"analyst": 10**6})
        with self.assertRaises(call_log.LimitExceeded) as e:           # no limit for the role
            self.chat(rec=rec, role="agent")
        self.assertIn("no max_request_bytes for the role agent", str(e.exception))
        self.assertEqual(self.mock.to("/oa/"), [])
        self.assertEqual(call_log.spent(scope="test-scope"), {})      # nothing was reserved

    def test_the_models_file_sets_the_request_size_limits(self):
        lim = fsio.read_json(os.path.join(BASE, "models.json"))["limits"]
        self.assertEqual(lim["max_request_bytes"], {"agent": 250000, "analyst": 500000})

    def test_one_configuration_cannot_take_the_shared_ceiling(self):
        # the shared ceiling admits the call, the configuration's own does not: nothing is
        # sent, and it is a limit of that purchase, not a stop of the day
        cid = providers.config_for("oa-model").get("configuration_id")
        os.environ["TEST_PURCHASE_BUDGET_CAPS"] = json.dumps(
            {"per_utc_day": {"USD": 1}, "per_scope": {"USD": 1},
             "per_configuration_in_scope": {cid: {"USD": 0.0000001}}})
        with self.assertRaises(call_log.ConfigurationCapExceeded) as e:
            self.chat()
        self.assertNotIsInstance(e.exception, call_log.BudgetExceeded)
        self.assertEqual(self.mock.to("/oa/"), [])
        # a configuration without a ceiling of its own is refused too, when the ceilings are set
        os.environ["TEST_PURCHASE_BUDGET_CAPS"] = json.dumps(
            {"per_utc_day": {"USD": 1}, "per_scope": {"USD": 1}, "per_configuration_in_scope": {}})
        with self.assertRaises(call_log.ConfigurationCapExceeded):
            self.chat()
        # and within its ceiling the call goes
        os.environ["TEST_PURCHASE_BUDGET_CAPS"] = json.dumps(
            {"per_utc_day": {"USD": 1}, "per_scope": {"USD": 1},
             "per_configuration_in_scope": {cid: {"USD": 1}}})
        self.mock.route("/oa/", ok(openai_body()))
        self.chat()
        self.assertEqual(len(self.mock.to("/oa/")), 1)

    def test_every_attempt_reserves_before_it_is_sent(self):
        # one attempt reserves about 0.0018 USD; the ceiling admits one reservation only, and a
        # failed attempt with no usage keeps counting at its reservation
        self.mock.route("/oa/", [(503, {"Retry-After": "0"}, b"x"), ok(openai_body())])
        os.environ["TEST_PURCHASE_BUDGET_CAPS"] = json.dumps(
            {"per_utc_day": {"USD": 0.003}, "per_scope": {"USD": 0.003}})
        with self.assertRaises(call_log.BudgetExceeded):
            self.chat()
        self.assertEqual(len(self.mock.to("/oa/")), 1)
        self.assertEqual([l["outcome"] for l in self.calls()], ["failed"])

    def test_budget_counts_actual_cost_once_settled(self):
        self.mock.route("/oa/", ok(openai_body(usage={"prompt_tokens": 1000000, "completion_tokens": 0})))
        os.environ["TEST_PURCHASE_BUDGET_CAPS"] = json.dumps(
            {"per_utc_day": {"USD": 0.42}, "per_scope": {"USD": 0.42}})
        rec = self.recorder()
        self.chat(rec=rec)                                   # settles at 0.40 USD
        self.assertAlmostEqual(call_log.spent(scope="test-scope")["USD"], 0.40)
        with self.assertRaises(call_log.BudgetExceeded):     # the next bound is about 0.0018
            self.chat(rec=rec, content="x" * 60000)
        self.assertEqual(len(self.mock.to("/oa/")), 1)

    def test_reservations_are_atomic_across_concurrent_writers(self):
        os.environ["TEST_PURCHASE_BUDGET_CAPS"] = json.dumps(
            {"per_utc_day": {"USD": 0.003}, "per_scope": {"USD": 0.003}})
        rec, c = self.recorder(), providers.config_for("oa-model")
        results, unexpected = [], []

        def worker(i):
            try:
                rec.reserve("RUN-T.C9999.A%d" % i, "agent", c, 90)
                results.append(True)
            except call_log.BudgetExceeded:
                results.append(False)
            except BaseException as e:          # anything else is a defect, not a refusal
                unexpected.append(repr(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(unexpected, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(results.count(True), 1)

    def test_call_cap_of_a_purchase(self):
        self.use_models(limits={"max_model_calls_per_purchase": 2})
        self.mock.route("/oa/", ok(openai_body()))
        rec = self.recorder()
        for _ in range(2):
            self.chat(rec=rec)
        with self.assertRaises(call_log.LimitExceeded):
            self.chat(rec=rec)
        self.assertEqual(len(self.mock.to("/oa/")), 2)

    def test_no_output_limit_no_call(self):
        with self.assertRaises(call_log.LimitExceeded):
            self.chat("nomax-model")
        self.assertEqual(self.mock.to("/oa/"), [])

    def test_unrecorded_call_is_refused(self):
        with self.assertRaises(SystemExit):
            providers.chat("oa-model", [{"role": "user", "content": "x"}])

    def test_two_separate_confirmations(self):
        self.mock.route("/oa/", ok(openai_body(model="conf-model")))
        with self.assertRaises(call_log.LimitExceeded):
            self.chat("conf-model")
        confirm.record("conf-model", "test-scope", 1, "owner", "0.001 USD", "yes")
        with self.assertRaises(call_log.LimitExceeded):
            self.chat("conf-model")
        with self.assertRaises(SystemExit):                   # too soon after stage 1
            confirm.record("conf-model", "test-scope", 2, "owner", "0.001 USD", "yes again")
        path = os.environ["TEST_PURCHASE_APPROVALS"]
        row = json.loads(fsio.read_text(path))
        earlier = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=120)
        row["recorded_at_utc"] = earlier.isoformat(timespec="seconds").replace("+00:00", "Z")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        confirm.record("conf-model", "test-scope", 2, "owner", "0.001 USD", "yes again")
        os.environ["TEST_PURCHASE_BUDGET_SCOPE"] = "other-scope"
        with self.assertRaises(call_log.LimitExceeded):       # confirmations are per scope
            self.chat("conf-model")
        self.assertEqual(self.mock.to("/oa/"), [])
        os.environ["TEST_PURCHASE_BUDGET_SCOPE"] = "test-scope"
        self.chat("conf-model")
        self.assertEqual(len(self.mock.to("/oa/")), 1)

    def test_product_id_goes_into_the_url_not_into_the_record(self):
        self.mock.route("/info/2/ai/777/openai/v1/chat/completions", ok(openai_body(model="info-model")))
        self.chat("info-model")
        self.assertEqual(len(self.mock.to("/info/2/ai/777/")), 1)
        self.assertIn("{product_id}", self.calls()[0]["endpoint"])

    def test_price_follows_the_configuration_not_the_reported_model(self):
        self.mock.route("/info/", ok(openai_body(model="mistral-small-2603")))
        self.chat("info-model")
        cost = self.calls()[0]["cost_computed"]
        self.assertEqual((cost["currency"], cost["rate_key"]), ("CHF", "in:info-model"))

    def test_model_drift_keeps_the_answer_and_stops(self):
        self.use_models(models_patch={"oa-model": {"expected_observed_models": ["oa-model"]}})
        self.mock.route("/oa/", ok(openai_body(model="oa-model-v2")))
        with self.assertRaises(call_log.ModelDrift):
            self.chat()
        lines = self.calls()
        self.assertEqual((len(lines), lines[0]["outcome"], lines[0]["observed_model_id"]),
                         (1, "completed", "oa-model-v2"))
        self.assertEqual(len(self.mock.to("/oa/")), 1)


class TestAnthropic(RigCase):
    CONVERSATION = [
        {"role": "system", "content": "prompt"}, {"role": "system", "content": "policy"},
        {"role": "user", "content": "book"},
        {"role": "assistant", "content": "checking",
         "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "a", "arguments": "{\"x\": 1}"}},
                        {"id": "t2", "type": "function", "function": {"name": "b", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "r1"},
        {"role": "tool", "tool_call_id": "t2", "content": "r2"}]

    def test_translation_alternates_roles_and_marks_the_cache(self):
        p = adapter_anthropic.build_request("anth-model", self.CONVERSATION, [
            {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}}], 200, cache=True)
        self.assertEqual([b["text"] for b in p["system"]], ["prompt", "policy"])
        self.assertEqual(p["system"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("cache_control", p["system"][0])
        self.assertEqual([m["role"] for m in p["messages"]], ["user", "assistant", "user"])
        results = p["messages"][-1]["content"]
        self.assertEqual([b["tool_use_id"] for b in results], ["t1", "t2"])
        self.assertEqual(results[-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(p["messages"][1]["content"][1]["input"], {"x": 1})
        self.assertEqual(p["tools"][0]["input_schema"], {"type": "object"})

    def test_native_call_roundtrip(self):
        self.mock.route("/anth/v1/messages", ok({
            "type": "message", "id": "msg_TEST12345678", "model": "anth-model",
            "content": [{"type": "text", "text": "hi"},
                        {"type": "tool_use", "id": "toolu_1", "name": "ping", "input": {"value": "ok"}}],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000,
                      "cache_creation_input_tokens": 200, "output_tokens": 20}}))
        msg, meta = providers.chat("anth-model", self.CONVERSATION[:3], recorder=self.recorder())
        self.assertEqual(msg["tool_calls"][0]["function"]["arguments"], "{\"value\": \"ok\"}")
        self.assertEqual(meta["finish_reason"], "tool_calls")
        sent = self.mock.to("/anth/")[0]
        self.assertEqual(sent["headers"]["x-api-key"], SECRET + "-an")
        self.assertNotIn("authorization", sent["headers"])
        line = self.calls()[0]
        self.assertNotIn("x-api-key", json.dumps(line["request_headers_recorded"]).lower())
        n = line["usage_normalised"]
        self.assertEqual((n["input_tokens"], n["cached_input_tokens"], n["cache_write_tokens"]), (1210, 1000, 200))
        self.assertAlmostEqual(line["cost_computed"]["amount"], (10 + 100 + 250 + 100) / 1e6)
        self.assertNotIn((SECRET + "-an").encode(), all_bytes(self.tmp))


class TestUsageAndCost(unittest.TestCase):
    def test_normalisation_by_provider(self):
        ds = usage.normalise("openai_chat", {"prompt_tokens": 100, "completion_tokens": 5,
                                             "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20})
        self.assertEqual((ds["input_tokens"], ds["cached_input_tokens"]), (100, 80))
        gem = usage.normalise("openai_chat", {"prompt_tokens": 50, "completion_tokens": 5})
        self.assertIsNone(gem["cached_input_tokens"])
        self.assertFalse(gem["cache_reported"])
        oa = usage.normalise("openai_chat", {"prompt_tokens": 50, "completion_tokens": 9,
                                             "completion_tokens_details": {"reasoning_tokens": 7}})
        self.assertEqual(oa["reasoning_tokens"], 7)

    def test_unreported_cache_is_priced_as_uncached_and_says_so(self):
        n = usage.normalise("openai_chat", {"prompt_tokens": 1000000, "completion_tokens": 0})
        c = usage.price("g:gemini-like", n, RATES)
        self.assertAlmostEqual(c["amount"], 0.30)
        self.assertIn("did not report cached input", c["basis"])

    def test_long_context_tier_starts_at_200000(self):
        at = usage.normalise("openai_chat", {"prompt_tokens": 200000, "completion_tokens": 0})
        below = usage.normalise("openai_chat", {"prompt_tokens": 199999, "completion_tokens": 0})
        self.assertAlmostEqual(usage.price("x:long", at, RATES)["amount"], 200000 * 2.50 / 1e6, places=6)
        self.assertAlmostEqual(usage.price("x:long", below, RATES)["amount"], 199999 * 1.25 / 1e6, places=6)
        self.assertAlmostEqual(usage.upper_bound("x:long", 196000, 0, RATES)["amount"],
                               (196000 + usage.TEMPLATE_TOKENS) * 2.50 / 1e6, places=6)

    def test_upper_bound_counts_every_byte_as_a_token_and_rounds_up(self):
        b = usage.upper_bound("oa:oa-model", 3000, 100, RATES)
        exact = ((3000 + usage.TEMPLATE_TOKENS) * 0.40 + 100 * 1.60) / 1e6     # 0.0029984
        self.assertGreaterEqual(b["amount"], exact)
        self.assertAlmostEqual(b["amount"], 0.002999)

    def test_upper_bound_takes_the_dearest_input_rate(self):
        # Claude Haiku: a cache write costs 1.25 per million against 1.00 for plain input. A
        # call whose whole input is written to the cache must never cost more than its bound
        rates = usage.load_rates()
        b = usage.upper_bound("anthropic:claude-haiku-4-5", 100000, 4096, rates)
        worst = usage.price("anthropic:claude-haiku-4-5",
                            {"input_tokens": 100000 + usage.TEMPLATE_TOKENS, "cached_input_tokens": 0,
                             "cache_write_tokens": 100000 + usage.TEMPLATE_TOKENS,
                             "output_tokens": 4096}, rates)
        self.assertGreaterEqual(b["amount"], worst["amount"])
        self.assertIn("dearest input rate 1.25", b["basis"])
        # every configuration of the models file, the same way
        for m in fsio.read_json(os.path.join(BASE, "models.json"))["models"]:
            r = usage.rate(m["rate_key"], rates)
            dearest = max(r["input"], r.get("cache_write") or 0, r.get("cached_input") or 0)
            self.assertIn("dearest input rate %s" % dearest,
                          usage.upper_bound(m["rate_key"], 1000, 10, rates)["basis"])

    def test_no_prefix_matching_for_configurations(self):
        self.assertIsNone(usage.rate("oa:oa-model-2025", RATES))

    def test_legacy_runs_are_priced_by_the_longest_model_name(self):
        n = usage.normalise("openai_chat", {"prompt_tokens": 1000000, "completion_tokens": 0})
        self.assertAlmostEqual(usage.price_legacy("gpt-4.1-mini-2025-04-14", n, RATES)["amount"], 0.4)


class TestNeoMundi(RigCase):
    GOVERN, CONTRACTS = "/nm/v1/govern", "/nm/v1/rgc/contracts/"

    def neomundi_up(self):
        self.mock.route(self.GOVERN, ok({"request_id": "nm-1", "mode": "OBS",
                                         "governance": {"decision": "ALLOW"},
                                         "audit": {"trace_id": "trace-nm-1"}}))
        self.mock.route(self.CONTRACTS, ok(self.signed_contract("nm-1")))

    def observed_run(self):
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        self.chat()
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertTrue(ok_, detail)
        return detail

    def test_the_observation_carries_no_identifiers_of_ours(self):
        self.observed_run()
        saved = fsio.read_bytes(os.path.join(self.run_dir, "neomundi", "requests", "RUN-T.C0001.A1.json"))
        body = json.loads(saved)
        self.assertEqual(set(body), {"source_type", "mode", "llm_prompt", "llm_response", "raw_metrics"})
        self.assertEqual((body["source_type"], body["mode"]), ("llm", "OBS"))
        self.assertNotIn("cost", body["raw_metrics"])   # we measure no cost, and null is refused
        self.assertEqual(self.mock.to(self.GOVERN)[0]["body"], saved)
        self.assertEqual(self.mock.to(self.GOVERN)[0]["headers"]["x-api-key"], SECRET + "-nm")
        self.assertNotIn((SECRET + "-nm").encode(), all_bytes(self.tmp))

    def test_the_signed_contract_is_retrieved_by_request_id_and_linked(self):
        detail = self.observed_run()
        self.assertEqual(detail["request_ids"], {"RUN-T.C0001.A1": "nm-1"})
        self.assertEqual([q["path"] for q in self.mock.to(self.CONTRACTS)],
                         [self.CONTRACTS + "nm-1"])
        line = [l for l in neomundi_client.links(self.run_dir) if l["status"] == "contract"][0]
        raw = fsio.read_bytes(os.path.join(self.run_dir, line["contract_file"]))
        self.assertEqual(call_log.sha256_bytes(raw), line["contract_sha256"])
        self.assertEqual(json.loads(raw)["identity"]["request_id"], "nm-1")

    def test_a_missing_contract_fails_verification_and_flush_fetches_it(self):
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        self.mock.route(self.CONTRACTS, (500, {}, b"later"))
        self.chat()
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertFalse(ok_)
        self.assertTrue(any("0 interoperability contracts" in p for p in detail["problems"]))
        self.neomundi_up()
        self.assertEqual(neomundi_client.flush(self.run_dir), {"RUN-T.C0001.A1": "contract: contract"})
        self.assertTrue(neomundi_client.verify_links(self.run_dir)[0])
        self.assertEqual(len(self.mock.to(self.GOVERN)), 1)   # the observation is not repeated
        self.assertEqual(len(self.mock.to("/oa/")), 1)

    def test_failure_of_neomundi_never_repeats_the_model_call_and_flush_completes_it(self):
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", ok(openai_body()))
        self.mock.route("/nm/", (500, {}, b"down"))
        self.chat()
        self.assertEqual((len(self.mock.to("/oa/")), len(self.mock.to(self.GOVERN))), (1, 3))
        self.assertFalse(neomundi_client.verify_links(self.run_dir)[0])
        # the models file changes after the run started: flush still uses the frozen copy
        self.use_models(neomundi={"enabled": True, "base_url": self.mock.url + "/wrong"})
        self.neomundi_up()
        self.assertEqual(neomundi_client.flush(self.run_dir), {"RUN-T.C0001.A1": "observed"})
        self.assertEqual(self.mock.to("/wrong/"), [])
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertTrue(ok_, detail)
        self.assertEqual(len(self.mock.to("/oa/")), 1)
        sent = self.mock.to(self.GOVERN)[-1]
        saved = fsio.read_bytes(os.path.join(self.run_dir, "neomundi", "requests", "RUN-T.C0001.A1.json"))
        self.assertEqual(sent["body"], saved)

    def test_one_to_one_is_enforced(self):
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", [(503, {}, b"x"), ok(openai_body())])
        self.neomundi_up()
        self.chat()
        self.assertEqual(len(self.mock.to(self.GOVERN)), 1)   # the failed attempt is kept locally
        self.assertTrue(neomundi_client.verify_links(self.run_dir)[0])
        links = neomundi_client.links(self.run_dir)
        observed = [l for l in links if l["status"] == "observed"][0]
        with open(os.path.join(self.run_dir, "neomundi", "links.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(observed) + "\n")
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertFalse(ok_)
        self.assertTrue(any("2 observations" in p for p in detail["problems"]))

    def tamper(self, rel):
        path = os.path.join(self.run_dir, rel)
        with open(path, "ab") as f:
            f.write(b" ")
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertFalse(ok_)
        return " | ".join(detail["problems"])

    def test_a_changed_neomundi_request_breaks_the_chain(self):
        self.observed_run()
        self.assertIn("request to NeoMundi", self.tamper("neomundi/requests/RUN-T.C0001.A1.json"))

    def test_a_changed_provider_response_breaks_the_chain(self):
        self.observed_run()
        self.assertIn("provider response", self.tamper(self.calls()[0]["response_file"]))

    def test_a_changed_frozen_configuration_breaks_the_chain(self):
        self.observed_run()
        self.assertIn("frozen copy", self.tamper("neomundi/config-at-start.json"))

    def test_llm_prompt_must_be_the_exact_provider_request(self):
        self.observed_run()
        path = os.path.join(self.run_dir, "neomundi", "requests", "RUN-T.C0001.A1.json")
        body = json.loads(fsio.read_bytes(path))
        body["llm_prompt"] = body["llm_prompt"].replace("x", "y")
        raw = json.dumps(body).encode("utf-8")
        with open(path, "wb") as f:
            f.write(raw)
        links_path = os.path.join(self.run_dir, "neomundi", "links.jsonl")
        rows = neomundi_client.links(self.run_dir)
        for r in rows:
            r["neomundi_request_sha256"] = call_log.sha256_bytes(raw)
        with open(links_path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertFalse(ok_)
        self.assertTrue(any("llm_prompt" in p for p in detail["problems"]))

    @staticmethod
    def signed_contract(request_id="nm-1", tamper=False):
        import base64
        contract = {"identity": {"schema_version": "0.1.0", "request_id": request_id,
                                 "timestamp": "2026-09-16T06:18:49.249599Z", "mode": "OBS"},
                    "observation": {"measurement_status": "complete"}}
        payload_hash = neomundi_client.canonical_payload_hash(contract)

        def b64(o):
            return base64.urlsafe_b64encode(json.dumps(o).encode("utf-8")).decode().rstrip("=")

        contract["integrity"] = {
            "payload_hash": payload_hash, "hash_algorithm": "sha256",
            "canonicalization": "sorted-json-utf8",
            "signature": "%s.%s.signature" % (b64({"alg": "EdDSA", "kid": "neomundi-rgc-2026-01"}),
                                              b64({"payload_hash": payload_hash, "request_id": request_id,
                                                   "timestamp": contract["identity"]["timestamp"]}))}
        if tamper:
            contract["observation"]["measurement_status"] = "changed after it was signed"
        return contract

    def test_the_contract_is_checked_as_far_as_we_can_without_the_public_key(self):
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        self.mock.route(self.CONTRACTS, ok(self.signed_contract()))
        self.chat()
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertTrue(ok_, detail)
        integrity = detail["contract_integrity"]["RUN-T.C0001.A1"]
        self.assertTrue(integrity["payload_hash_matches"])
        self.assertTrue(integrity["signature_binds_the_payload_hash"])
        self.assertFalse(integrity["signature_verified"])
        self.assertEqual(integrity["key_id"], "neomundi-rgc-2026-01")

    def test_a_contract_changed_after_signing_is_refused(self):
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        self.mock.route(self.CONTRACTS, ok(self.signed_contract(tamper=True)))
        self.chat()
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertFalse(ok_)
        self.assertTrue(any("payload hash" in p for p in detail["problems"]))

    def test_a_body_neomundi_refuses_is_never_sent_again(self):
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", ok(openai_body()))
        self.mock.route(self.GOVERN, (422, {}, json.dumps({"error": "validation_error"}).encode()))
        self.chat()
        self.assertEqual(len(self.mock.to(self.GOVERN)), 1)
        self.assertEqual([l["status"] for l in neomundi_client.links(self.run_dir)],
                         ["failed_try", "invalid_request"])
        self.assertEqual(neomundi_client.flush(self.run_dir),
                         {"RUN-T.C0001.A1": "refused by NeoMundi: not sent again"})
        self.assertEqual(len(self.mock.to(self.GOVERN)), 1)
        self.assertFalse(neomundi_client.verify_links(self.run_dir)[0])
        opened = neomundi_client.breaker_open("test-scope")
        self.assertIn("HTTP 422 on RUN-T.C0001.A1", opened["reason"])
        self.chat()                                             # the next call is not observed at all
        self.assertEqual(len(self.mock.to(self.GOVERN)), 1)
        self.assertEqual(neomundi_client.links(self.run_dir)[-1]["status"], "circuit_open")

    def test_an_unrecoverable_4xx_on_a_contract_opens_the_breaker_too(self):
        # a wrong contract address answers 404 for every contract of the round
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", ok(openai_body()))
        self.mock.route(self.GOVERN, ok({"request_id": "nm-1", "mode": "OBS",
                                         "governance": {"decision": "ALLOW"}}))
        self.mock.route(self.CONTRACTS, (404, {}, b"not found"))
        rec = self.recorder()
        self.chat(rec=rec)
        self.assertEqual([l["status"] for l in neomundi_client.links(self.run_dir)],
                         ["observed", "contract_pending"])
        opened = neomundi_client.breaker_open("test-scope")
        self.assertIn("HTTP 404 on the contract of RUN-T.C0001.A1", opened["reason"])
        self.chat(rec=rec)
        self.assertEqual(len(self.mock.to(self.GOVERN)), 1)
        self.assertEqual(neomundi_client.links(self.run_dir)[-1]["status"], "circuit_open")
        # a rate limit on a contract is not unrecoverable: no breaker
        neomundi_client.reset_breaker("test-scope")
        self.mock.route(self.CONTRACTS, (429, {}, b"slow down"))
        self.chat(rec=rec)
        self.assertIsNone(neomundi_client.breaker_open("test-scope"))

    def test_a_rejected_key_on_an_observation_opens_the_breaker(self):
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", ok(openai_body()))
        self.mock.route(self.GOVERN, (401, {}, b"bad key"))
        self.chat()
        self.assertEqual(len(self.mock.to(self.GOVERN)), 1)
        self.assertEqual(neomundi_client.links(self.run_dir)[-1]["status"], "pending")
        self.assertIn("HTTP 401 on the observation of RUN-T.C0001.A1",
                      neomundi_client.breaker_open("test-scope")["reason"])

    def test_an_observation_without_a_request_id_is_a_problem(self):
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", ok(openai_body()))
        self.mock.route(self.GOVERN, ok({"mode": "OBS", "governance": {"decision": "ALLOW"}}))
        self.chat()
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertFalse(ok_)
        self.assertTrue(any("without a request_id" in p for p in detail["problems"]))
        self.assertEqual(self.mock.to(self.CONTRACTS), [])

    def test_every_outgoing_request_counts_against_the_scope_cap(self):
        # cap 2 for the scope: two failed tries leave, the third is not sent at all
        self.use_models(neomundi={"enabled": True, "max_observations_per_scope": 2})
        self.mock.route("/oa/", ok(openai_body()))
        self.mock.route(self.GOVERN, (500, {}, b"down"))
        self.chat()
        self.assertEqual(len(self.mock.to(self.GOVERN)), 2)
        self.assertEqual(neomundi_client.requests_made(scope="test-scope"), 2)
        last = neomundi_client.links(self.run_dir)[-1]
        self.assertEqual(last["status"], "pending")
        self.assertIn("cap of 2", last["error"])
        self.assertEqual(len(self.mock.to("/oa/")), 1)          # the model is not re-called
        self.assertFalse(neomundi_client.verify_links(self.run_dir)[0])

    def test_contract_requests_have_their_own_purchase_cap(self):
        # contracts capped at 1 per purchase, observations not: the second observation leaves,
        # its contract request does not
        self.use_models(neomundi={"enabled": True, "max_contracts_per_purchase": 1})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        rec = self.recorder()
        self.chat(rec=rec)
        self.chat(rec=rec)
        self.assertEqual(len(self.mock.to(self.GOVERN)), 2)
        self.assertEqual(len(self.mock.to(self.CONTRACTS)), 1)
        self.assertEqual([l["status"] for l in neomundi_client.links(self.run_dir)],
                         ["observed", "contract", "observed", "contract_pending"])
        self.assertIn("cap of 1 NeoMundi contracts", neomundi_client.links(self.run_dir)[-1]["error"])
        self.assertEqual(neomundi_client.requests_made(run_id="RUN-T"), 3)
        self.assertEqual(neomundi_client.requests_made(run_id="RUN-T", kind="observation"), 2)
        self.assertEqual(neomundi_client.requests_made(run_id="RUN-T", kind="contract"), 1)

    def test_a_purchase_of_twenty_calls_fits_under_the_purchase_cap(self):
        # 20 observations and 20 contracts, under the caps of 50 of each per purchase
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        rec = self.recorder()
        for _ in range(20):
            self.chat(rec=rec)
        self.assertEqual(len(self.mock.to(self.GOVERN)), 20)
        self.assertEqual(len(self.mock.to(self.CONTRACTS)), 20)
        self.assertEqual(neomundi_client.requests_made(run_id="RUN-T"), 40)
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertTrue(ok_, detail["problems"][:3])
        self.assertEqual((detail["observed"], detail["contracts"]), (20, 20))

    def test_without_caps_nothing_is_sent(self):
        self.use_models(neomundi={"enabled": True, "max_observations_per_scope": None})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        self.chat()
        self.assertEqual(self.mock.to("/nm/"), [])
        self.assertIn("no observation caps", neomundi_client.links(self.run_dir)[-1]["error"])

    def test_an_oversize_body_is_not_sent_and_opens_the_breaker(self):
        # the limit NeoMundi enforces on llm_prompt is checked before anything leaves; the
        # context is never cut down to fit. The breaker then stops every later observation
        # of the scope, and a reset lets them through again
        self.use_models(neomundi={"enabled": True, "max_prompt_chars": 10})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        rec = self.recorder()
        self.chat(rec=rec)
        self.assertEqual(self.mock.to("/nm/"), [])
        first = neomundi_client.links(self.run_dir)[-1]
        self.assertEqual(first["status"], "oversize")
        self.assertGreater(first["llm_prompt_chars"], 10)
        self.assertIn("the limit is 10", first["error"])
        self.assertTrue(os.path.exists(os.path.join(self.run_dir, "neomundi", "requests", "RUN-T.C0001.A1.json")))
        opened = neomundi_client.breaker_open("test-scope")
        self.assertEqual(opened["run_id"], "RUN-T")
        self.assertIn("oversize on RUN-T.C0001.A1", opened["reason"])
        self.assertEqual(neomundi_client.requests_made(run_id="RUN-T"), 0)
        self.use_models(neomundi={"enabled": True})
        self.chat(rec=rec)
        self.assertEqual(self.mock.to("/nm/"), [])
        self.assertEqual(neomundi_client.links(self.run_dir)[-1]["status"], "circuit_open")
        self.assertEqual(len(self.mock.to("/oa/")), 2)          # the model is still called
        self.assertFalse(neomundi_client.verify_links(self.run_dir)[0])
        self.assertTrue(neomundi_client.reset_breaker("test-scope"))
        self.assertIsNone(neomundi_client.breaker_open("test-scope"))
        # a flush sends under the frozen configuration of the run: the same limit, the same
        # bodies, so nothing leaves and the breaker opens again. Only a new configuration
        # agreed with NeoMundi changes that, never a shorter body made here
        self.assertEqual(neomundi_client.flush(self.run_dir),
                         {"RUN-T.C0001.A1": "oversize", "RUN-T.C0002.A1": "circuit_open"})
        self.assertEqual(self.mock.to("/nm/"), [])
        self.assertIn("oversize on RUN-T.C0001.A1", neomundi_client.breaker_open("test-scope")["reason"])

    def test_an_agreed_projection_is_sent_instead_of_the_full_request(self):
        # a short limit that the full request exceeds and its projection does not
        import projection
        self.use_models(neomundi={"enabled": True, "max_prompt_chars": 2000,
                                  "prompt_projection": "tp-projection/1"})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        providers.chat("oa-model", [{"role": "system", "content": "policy " * 400},
                                    {"role": "user", "content": "I want the flat on the seventh floor."}],
                       recorder=self.recorder(), role="agent")
        self.assertEqual(len(self.mock.to(self.GOVERN)), 1)
        sent = json.loads(self.mock.to(self.GOVERN)[0]["body"])
        body = json.loads(sent["llm_prompt"])
        self.assertLessEqual(len(sent["llm_prompt"]), 2000)
        self.assertEqual(body["projection"], "tp-projection/1")
        self.assertEqual(body["new_input"], [{"role": "user", "content": "I want the flat on the seventh floor."}])
        self.assertEqual(body["fixed"]["system"][0]["chars"], len(json.dumps("policy " * 400)))
        call = call_log.attempts(self.run_dir)[-1]
        self.assertEqual(body["full_request"]["sha256"], call["request_sha256"])
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertTrue(ok_, detail["problems"][:3])
        # a projection that does not match the kept request breaks the chain
        path = os.path.join(self.run_dir, "neomundi", "requests", call["provider_attempt_id"] + ".json")
        saved = json.loads(fsio.read_bytes(path))
        saved["llm_prompt"] = saved["llm_prompt"].replace("seventh", "eighth")
        raw = json.dumps(saved, ensure_ascii=False).encode("utf-8")
        os.chmod(path, 0o666)
        with open(path, "wb") as f:
            f.write(raw)
        rows = neomundi_client.links(self.run_dir)
        for r in rows:
            if r.get("neomundi_request_sha256"):
                r["neomundi_request_sha256"] = call_log.sha256_bytes(raw)
        with open(os.path.join(self.run_dir, "neomundi", "links.jsonl"), "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertFalse(ok_)
        self.assertTrue(any("tp-projection/1 projection" in p for p in detail["problems"]))

    def test_the_projection_withholds_only_tool_results_and_says_so(self):
        import projection
        big = "x" * 5000
        req = {"model": "m", "tools": [], "messages": [
            {"role": "system", "content": "s"}, {"role": "user", "content": "hello"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "t1"}]},
            {"role": "tool", "tool_call_id": "t1", "content": big}]}
        raw = json.dumps(req).encode("utf-8")
        full = json.loads(projection.project(raw, 100000))
        self.assertEqual(full["new_input"][0]["content"], big)
        self.assertNotIn("withheld", full)
        short = json.loads(projection.project(raw, 1500))
        self.assertEqual(short["new_input"][0]["content_withheld"], projection.fingerprint(big))
        self.assertEqual(short["withheld"][0]["message"], 0)
        self.assertEqual(projection.project(raw, 1500), projection.project(raw, 1500))
        # the customer's words are never withheld: a line that cannot fit is not projected
        req["messages"] = req["messages"][:2]
        req["messages"][1]["content"] = "y" * 5000
        self.assertIsNone(projection.project(json.dumps(req).encode("utf-8"), 1500))
        # Anthropic shape: tool results are blocks of a user message, the system is top level
        anth = {"model": "m", "system": [{"type": "text", "text": "s"}], "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": big}]}]}
        short = json.loads(projection.project(json.dumps(anth).encode("utf-8"), 1500))
        self.assertEqual(len(short["fixed"]["system"]), 1)
        self.assertEqual(short["new_input"][0]["content"][0]["content_withheld"]["chars"], len(json.dumps(big)))
        self.assertEqual(short["history"]["messages"], 2)

    def test_an_unknown_projection_is_refused(self):
        with self.assertRaises(ValueError):
            neomundi_client.prompt_for(b"{}", {"prompt_projection": "made-up/9"})
        import series
        self.assertIn("not a known projection", " ".join(series.neomundi_config_problems(
            {"enabled": True, "prompt_projection": "made-up/9"})))

    def two_call_run(self):
        """A purchase of two completed agent calls with no NeoMundi configuration yet."""
        self.use_models(neomundi={"enabled": False, "prompt_projection": "tp-projection/1"})
        self.mock.route("/oa/", ok(openai_body()))
        rec = call_log.Recorder(self.run_dir, "RUN-T", {"pilot_id": "P-TEST"}, providers.limits())
        providers.chat("oa-model", [{"role": "user", "content": "one"}], recorder=rec, role="agent")
        providers.chat("oa-model", [{"role": "user", "content": "two"}], recorder=rec, role="agent")
        shutil.rmtree(os.path.join(self.run_dir, "neomundi"), ignore_errors=True)
        os.environ["TEST_PURCHASE_LEDGER_DIR"] = os.path.join(self.tmp, "ledger")   # never the real one
        return [c["provider_attempt_id"] for c in call_log.attempts(self.run_dir)]

    def test_a_probe_sends_one_observation_at_most_and_checks_only_that_call(self):
        first, second = self.two_call_run()
        self.mock.route(self.GOVERN, (503, {}, b"busy"))
        before = dict(os.environ)
        try:
            self.assertEqual(neomundi_client.probe(self.run_dir, first), "pending")
            self.assertEqual(os.environ["TEST_PURCHASE_BUDGET_SCOPE"], "probe-" + os.path.basename(self.run_dir))
        finally:
            os.environ.clear()
            os.environ.update(before)
        self.assertEqual(len(self.mock.to(self.GOVERN)), 1)       # a 503 is not retried in a probe
        frozen = neomundi_client.run_config(self.run_dir)
        for k, v in neomundi_client.PROBE_LIMITS.items():
            self.assertEqual(frozen[k], v)
        self.assertTrue(frozen["enabled"])
        # a flush later cannot exceed the one paid observation either
        self.neomundi_up()
        with unittest.mock.patch.dict(os.environ, neomundi_client.probe_env(self.run_dir)):
            self.assertEqual(neomundi_client.flush(self.run_dir), {first: "pending"})
        self.assertEqual(len(self.mock.to(self.GOVERN)), 1)
        # a second probe on the same copy is refused before anything is sent
        with self.assertRaises(SystemExit):
            neomundi_client.probe(self.run_dir, second)

    def test_verify_attempt_checks_one_call_where_verify_links_checks_the_purchase(self):
        first, second = self.two_call_run()
        self.neomundi_up()
        before = dict(os.environ)
        try:
            self.assertEqual(neomundi_client.probe(self.run_dir, first), "observed")
        finally:
            os.environ.clear()
            os.environ.update(before)
        self.assertEqual((len(self.mock.to(self.GOVERN)), len(self.mock.to(self.CONTRACTS))), (1, 1))
        ok_, detail = neomundi_client.verify_attempt(self.run_dir, first)
        self.assertTrue(ok_, detail["problems"])
        self.assertEqual((detail["request_id"], detail["tries"], detail["prompt_projection"]),
                         ("nm-1", 1, "tp-projection/1"))
        self.assertFalse(neomundi_client.verify_links(self.run_dir)[0])      # the second call is unobserved
        self.assertFalse(neomundi_client.verify_attempt(self.run_dir, second)[0])
        self.assertFalse(neomundi_client.verify_attempt(self.run_dir, "RUN-T.C0099.A1")[0])

    def test_deferred_observations_leave_only_when_the_purchase_is_over(self):
        # in OBS mode NeoMundi does not answer the agent, so the observation waits for the
        # end of the purchase instead of costing 20 seconds between two customer lines
        self.use_models(neomundi={"enabled": True, "send": "deferred"})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        rec = self.recorder()
        self.chat(rec=rec)
        self.chat(rec=rec)
        self.assertEqual(self.mock.to("/nm/"), [])                       # nothing yet
        self.assertEqual([l["status"] for l in neomundi_client.links(self.run_dir)],
                         ["queued", "queued"])
        self.assertEqual(len(glob.glob(os.path.join(self.run_dir, "neomundi", "requests", "*.json"))), 2)
        self.assertEqual(neomundi_client.requests_made(run_id="RUN-T"), 0)
        out = neomundi_client.flush(self.run_dir)
        self.assertEqual(sorted(out.values()), ["observed", "observed"])
        self.assertEqual((len(self.mock.to(self.GOVERN)), len(self.mock.to(self.CONTRACTS))), (2, 2))
        ok_, detail = neomundi_client.verify_links(self.run_dir)
        self.assertTrue(ok_, detail["problems"][:3])
        # the bodies are the same bytes an inline send would have produced
        for p in glob.glob(os.path.join(self.run_dir, "neomundi", "requests", "*.json")):
            body = fsio.read_json(p)
            self.assertEqual(sorted(body), ["llm_prompt", "llm_response", "mode", "raw_metrics", "source_type"])

    def test_a_deferred_flush_may_send_several_at_once_and_still_respects_the_caps(self):
        self.use_models(neomundi={"enabled": True, "send": "deferred", "max_parallel_sends": 4,
                                  "max_observations_per_purchase": 3})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        rec = self.recorder()
        for _ in range(5):
            self.chat(rec=rec)
        self.assertEqual(self.mock.to("/nm/"), [])
        out = neomundi_client.flush(self.run_dir)
        self.assertEqual(sorted(out.values()).count("observed"), 3)       # the cap of 3 holds
        self.assertEqual(sorted(out.values()).count("pending"), 2)
        self.assertEqual(len(self.mock.to(self.GOVERN)), 3)
        self.assertEqual(neomundi_client.requests_made(run_id="RUN-T", kind="observation"), 3)
        rows = neomundi_client.links(self.run_dir)
        self.assertEqual(len([r for r in rows if r["status"] == "observed"]), 3)
        self.assertTrue(all(json.dumps(r) for r in rows))                 # every line is whole JSON

    def test_the_send_mode_and_the_parallelism_are_checked_in_the_plan(self):
        import series
        self.assertIn("send is 'now'", " ".join(series.neomundi_config_problems(
            {"enabled": True, "send": "now"})))
        self.assertIn("max_parallel_sends", " ".join(series.neomundi_config_problems(
            {"enabled": True, "send": "deferred", "max_parallel_sends": 0})))
        self.assertEqual([p for p in series.neomundi_config_problems(
            dict(fsio.read_json(providers.MODELS)["neomundi"], enabled=True, send="deferred",
                 max_parallel_sends=4)) if "send" in p or "parallel" in p], [])

    def test_without_a_size_limit_nothing_is_sent(self):
        self.use_models(neomundi={"enabled": True, "max_prompt_chars": None})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        self.chat()
        self.assertEqual(self.mock.to("/nm/"), [])
        self.assertEqual(neomundi_client.links(self.run_dir)[-1]["status"], "oversize")
        self.assertIn("no max_prompt_chars", neomundi_client.links(self.run_dir)[-1]["error"])
        self.assertIsNotNone(neomundi_client.breaker_open("test-scope"))

    def test_analyst_calls_are_not_observed(self):
        self.use_models(neomundi={"enabled": True})
        self.mock.route("/oa/", ok(openai_body()))
        self.neomundi_up()
        self.chat(role="analyst")
        self.assertEqual(self.mock.to("/nm/"), [])


class TestHarnessLimits(RigCase):
    def agent(self, tool_calls):
        import harness
        self.mock.route("/oa/", ok(openai_body(content=None, tool_calls=tool_calls)))
        harness.REC["recorder"] = self.recorder()
        harness.MSG_LOG["path"] = os.path.join(self.run_dir, "messages.jsonl")
        harness.MATERIAL["n"] = 0
        transcript = []
        with self.assertRaises(call_log.LimitExceeded):
            harness.agent_turn(None, "oa-model", [{"role": "system", "content": "s"}], "hi", transcript, [])
        return harness, transcript

    @staticmethod
    def call(name, i=1):
        return {"id": "c%d" % i, "type": "function", "function": {"name": name, "arguments": "{}"}}

    def test_tool_rounds_in_one_turn_are_bounded(self):
        self.agent([self.call("noop")])
        self.assertEqual(len(self.mock.to("/oa/")), 3)   # limit 2: the third round is refused

    def test_tool_calls_in_one_response_are_bounded_before_any_runs(self):
        _, transcript = self.agent([self.call("noop", i) for i in range(3)])
        self.assertEqual(len(self.mock.to("/oa/")), 1)
        self.assertEqual(transcript[1:], [])              # only the customer line, no operation

    def test_tool_calls_in_one_turn_are_bounded(self):
        self.use_models(limits={"max_tool_rounds_per_turn": 10, "max_tool_calls_per_turn": 3})
        self.agent([self.call("noop", 1), self.call("noop", 2)])
        self.assertEqual(len(self.mock.to("/oa/")), 2)   # 2 + 2 > 3

    def test_material_limit_is_checked_for_the_whole_response_before_any_runs(self):
        self.use_models(limits={"max_material_operations_per_purchase": 1, "max_tool_calls_per_response": 5})
        _, transcript = self.agent([self.call("create_viewing", 1), self.call("create_handover", 2)])
        self.assertEqual(transcript[1:], [])              # neither operation was executed

    def test_material_operations_of_a_purchase_are_bounded(self):
        self.use_models(limits={"max_material_operations_per_purchase": 0})
        _, transcript = self.agent([self.call("create_viewing")])
        self.assertEqual(transcript[1:], [])


class TestSeriesRules(RigCase):
    def test_a_technical_failure_is_attempted_once(self):
        import series
        folder = os.path.join(self.tmp, "series")
        os.makedirs(folder)
        calls = []

        def fake_purchase(model, tag, models_file, ids):
            calls.append(tag)
            return "TP-FAILED", "technical", "harness exit 3: provider down"

        original, series.purchase = series.purchase, fake_purchase
        try:
            status, run_id, _ = series.attempt_purchase(
                folder, "S", {"series": "S"}, {"day": 1, "date": "2026-09-15"}, "A", "m", None)
        finally:
            series.purchase = original
        self.assertEqual((status, run_id, calls), ("technical_failure", "TP-FAILED", ["D01-A"]))
        self.assertEqual([e["status"] for e in series.registry(folder)], ["technical_failure"])

    def test_the_analyst_must_be_pinned_too(self):
        import series
        data = fsio.read_json(providers.MODELS)
        tested = [dict(m, expected_observed_models=[m["model"]]) for m in data["models"]
                  if m.get("max_output_tokens")]
        analyst = cfg("analyst-model", "oa:oa-model", self.mock.url, "/oa/v1")
        with self.assertRaises(SystemExit) as e:
            series.validate_configuration(dict(data, models=tested), tested + [analyst])
        self.assertIn("test/analyst-model", str(e.exception))

    def test_the_analyst_smoke_fails_on_an_empty_or_cut_answer(self):
        import smoke
        self.assertTrue(smoke.analyst_answer_ok('{"ok": true}', "stop"))
        self.assertFalse(smoke.analyst_answer_ok("", "length"))          # every token went to reasoning
        self.assertFalse(smoke.analyst_answer_ok('{"ok": true}', "length"))
        self.assertFalse(smoke.analyst_answer_ok("not json", "stop"))
        self.assertFalse(smoke.analyst_answer_ok('{"ok": false}', "stop"))
        self.assertFalse(smoke.analyst_answer_ok(None, "stop"))

    def test_smoke_takes_the_eight_configurations_only(self):
        import smoke
        data = {"models": [{"model": "a"}, {"model": "b"}],
                "auxiliary_models": [{"model": "analyst"}], "roles": {"analyst": {"model": "analyst"}}}
        self.assertEqual([m["model"] for m in smoke.configurations(data)], ["a", "b"])
        self.assertEqual([m["model"] for m in smoke.configurations(data, analyst=True)], ["analyst"])


class TestFinalPatch(RigCase):
    def test_unrecognised_usage_is_unknown_and_keeps_the_reservation(self):
        self.assertIsNone(usage.normalise("openai_chat", {"total_tokens": 100}))
        self.assertIsNone(usage.normalise("openai_chat", {"prompt_tokens": 100}))
        self.assertIsNone(usage.normalise("anthropic_messages", {"output_tokens": 5}))
        self.mock.route("/oa/", ok(openai_body(usage={"total_tokens": 100})))
        self.chat()
        line = self.calls()[0]
        self.assertEqual((line["usage_normalised"], line["cost_computed"]), (None, None))
        state = list(call_log.ledger_state().values())[0]
        self.assertIsNone(state["actual"])
        self.assertEqual(call_log.spent(scope="test-scope")["USD"], state["reserved"])

    def smoke_configs(self, *names):
        data = fsio.read_json(providers.MODELS)
        return [m for m in data["models"] if m["model"] in names]

    def test_smoke_preflight_refuses_before_any_call(self):
        import smoke
        configs = self.smoke_configs("oa-model", "conf-model", "info-model")
        requests = smoke.requests_for(configs, False)
        os.environ.pop("TESTPID")
        problems, _ = smoke.preflight(configs, requests, "SMOKE-T",
                                      {"per_utc_day": {"USD": 1, "CHF": 1},
                                       "per_scope": {"USD": 0.0001, "CHF": 1}})
        text = " | ".join(problems)
        self.assertIn("TESTPID is absent", text)
        self.assertIn("stage 1 of 2", text)
        self.assertIn("exceeds the scope ceiling", text)
        self.assertEqual(self.mock.requests, [])

    def test_smoke_dry_builds_every_request_without_keys(self):
        import smoke
        for k in SECRETS:
            os.environ.pop(k)
        configs = self.smoke_configs("oa-model", "anth-model", "info-model", "conf-model")
        self.assertEqual(len(smoke.requests_for(configs, False)), 4)

    def test_declared_neomundi_configuration_is_what_the_run_freezes(self):
        import series
        cfg = dict(fsio.read_json(providers.MODELS)["neomundi"], enabled=True)
        path = os.path.join(self.tmp, "neomundi-config.json")
        series.jdump(path, cfg)
        pl = {"neomundi": {"required": True, "config_file": "neomundi-config.json",
                           "config_sha256": series.sha256_file(path), "schema": {"type": "object"},
                           "schema_object_sha256": series.sha256_obj({"type": "object"})}}
        self.assertEqual(series.neomundi_ready(self.tmp, pl), [])
        os.environ["TEST_PURCHASE_NEOMUNDI_CONFIG"] = path
        self.assertTrue(neomundi_client.freeze_config(self.run_dir)["enabled"])
        frozen = os.path.join(self.run_dir, "neomundi", "config-at-start.json")
        self.assertEqual(series.sha256_file(frozen), pl["neomundi"]["config_sha256"])
        series.jdump(path, dict(cfg, enabled=False))
        self.assertIn("checksum", " ".join(series.neomundi_ready(self.tmp, pl)))
        self.assertIn("not enabled", " ".join(series.neomundi_config_problems(dict(cfg, enabled=False))))
        self.assertEqual(series.neomundi_ready(self.tmp, {"neomundi": {"required": False}}), [])

    def test_smoke_writes_its_approvals_before_the_first_request(self):
        import smoke
        confirm.record("conf-model", "SMOKE-T", 1, "owner", "0.02 USD", "yes")
        ref = smoke.write_approvals(self.run_dir, self.smoke_configs("oa-model", "conf-model"), "SMOKE-T")
        raw = fsio.read_bytes(os.path.join(self.run_dir, "approvals-at-start.json"))
        self.assertEqual(ref["sha256"], call_log.sha256_bytes(raw))
        self.assertFalse(ref["confirmed"])
        self.assertEqual(list(json.loads(raw)), ["conf-model"])
        self.assertEqual(self.mock.requests, [])

    def test_neomundi_declaration_requires_the_confirmed_base_url(self):
        p = subprocess.run([sys.executable, "series.py", "neomundi", "--version", "v", "--schema", "s.json"],
                           cwd=BASE, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--base-url", p.stderr)

    def test_confirmations_travel_with_the_run(self):
        confirm.record("conf-model", "test-scope", 1, "owner", "0.01 USD", "yes")
        e = confirm.evidence("conf-model", "test-scope")
        self.assertEqual((e["confirmed"], len(e["records"])), (False, 1))
        self.assertIsNone(confirm.evidence("oa-model", "test-scope"))


class TestRealConfiguration(unittest.TestCase):
    def setUp(self):
        self.data = fsio.read_json(os.path.join(BASE, "models.json"))

    def test_smoke_reservation_fits_its_ceiling(self):
        import smoke
        tmp = tempfile.mkdtemp(prefix="rig-real-")
        for m in self.data["models"]:
            m["max_output_tokens"] = smoke.MAX_OUTPUT
        path = os.path.join(tmp, "models.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        saved, providers.MODELS = providers.MODELS, path
        try:
            total = smoke.reservation(self.data["models"], smoke.requests_for(self.data["models"], False))
        finally:
            providers.MODELS = saved
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertGreater(total["USD"], 0.012)       # Command A alone reserves more than 0.01
        for cur, amount in total.items():
            self.assertLessEqual(amount, smoke.SCOPE_CAP[cur])

    def test_thinking_is_off_where_the_default_would_change_the_configuration(self):
        by = {m["provider"]: m for m in self.data["models"]}
        self.assertEqual(by["Google"].get("reasoning_effort"), "none")
        self.assertEqual(by["DeepSeek"].get("reasoning_effort"), "none")


class TestEndToEnd(RigCase):
    """A whole scripted purchase through harness.py, on a copy of the environment, against
    the mock provider and the mock NeoMundi."""

    def test_purchase_is_recorded_observed_and_costed(self):
        env_dir = copy_environment(self.tmp)
        models = models_file(self.tmp, self.mock.url, neomundi={"enabled": True},
                             models_patch={"e2e-model": {"expected_observed_models": ["e2e-model"]}})
        self.mock.route("/oa/", ok(openai_body(content="Thank you, noted.", model="e2e-model",
                                               usage={"prompt_tokens": 2000, "completion_tokens": 30})))
        self.mock.route("/nm/v1/govern", ok({"request_id": "nm", "mode": "OBS",
                                             "governance": {"decision": "ALLOW"},
                                             "audit": {"trace_id": "trace-nm"}}))
        self.mock.route("/nm/v1/rgc/contracts/", ok(TestNeoMundi.signed_contract("nm")))
        env = dict(os.environ, TEST_PURCHASE_MODELS_FILE=models,
                   TEST_PURCHASE_PURCHASE_ID="S-TEST.D01-A", TEST_PURCHASE_DAY_ID="D01",
                   PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, "harness.py", "--model", "e2e-model", "--buyer",
                            "scripted", "--tag", "D01-A"], cwd=env_dir, env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout[-2000:] + p.stderr[-2000:])
        run_dir = glob.glob(os.path.join(env_dir, "runs", "TP-D01-A-*"))[0]
        calls = call_log.attempts(run_dir)
        manifest = fsio.read_json(os.path.join(run_dir, "manifest.json"))
        self.assertEqual(len(calls), len(self.mock.to("/oa/")))
        self.assertEqual(len(calls), len(manifest["api_responses"]))
        self.assertTrue(all(c["outcome"] == "completed" for c in calls))
        self.assertEqual(manifest["identifiers"]["purchase_id"], "S-TEST.D01-A")
        self.assertEqual(manifest["generation_parameters"]["set_by_the_configuration"],
                         {"max_tokens": 500, "reasoning_effort": "none"})
        self.assertEqual(len(self.mock.to("/nm/v1/govern")), len(calls))
        self.assertEqual(len(self.mock.to("/nm/v1/rgc/contracts/")), len(calls))
        ok_, detail = neomundi_client.verify_links(run_dir)
        self.assertTrue(ok_, detail)
        sys.path.insert(0, env_dir)
        import series
        declared = {"required": True, "schema": {"type": "object"},
                    "schema_object_sha256": series.sha256_obj({"type": "object"}), "required_keys": [],
                    "config_sha256": series.sha256_file(os.path.join(run_dir, "neomundi", "config-at-start.json"))}
        good, d = series.measurement(run_dir, {"neomundi": declared})
        self.assertTrue(good, d)
        bad, d = series.measurement(run_dir, {"neomundi": dict(declared, config_sha256="0" * 64)})
        self.assertFalse(bad)
        self.assertIn("configuration declared in the plan", d["why"])
        c = subprocess.run([sys.executable, "cost.py", run_dir], cwd=env_dir, env=env,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(c.returncode, 0, c.stderr)
        cost = fsio.read_json(os.path.join(run_dir, "cost.json"))
        expected = len(calls) * (2000 * 0.40 + 30 * 1.60) / 1e6
        self.assertAlmostEqual(cost["cost_computed_by_currency"]["USD"], expected, places=6)
        self.assertNotIn(SECRET.encode(), all_bytes(self.tmp))

    def test_provider_down_stops_the_purchase_with_the_technical_exit_code(self):
        env_dir = copy_environment(self.tmp)
        models = models_file(self.tmp, self.mock.url)
        self.mock.route("/oa/", (503, {}, b"down"))
        env = dict(os.environ, TEST_PURCHASE_MODELS_FILE=models, PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, "harness.py", "--model", "e2e-model", "--tag", "D01-B"],
                           cwd=env_dir, env=env, capture_output=True, text=True,
                           encoding="utf-8", timeout=300)
        self.assertEqual(p.returncode, 3, p.stdout[-1500:] + p.stderr[-1500:])
        self.assertEqual(len(self.mock.to("/oa/")), 4)
        run_dir = glob.glob(os.path.join(env_dir, "runs", "TP-D01-B-*"))[0]
        manifest = fsio.read_json(os.path.join(run_dir, "manifest.json"))
        self.assertTrue(manifest["closure_reason"].startswith("technical_failure"))


if __name__ == "__main__":
    unittest.main()
