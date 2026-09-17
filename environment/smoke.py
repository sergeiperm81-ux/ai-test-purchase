# -*- coding: utf-8 -*-
"""
The smoke test: one short call per configuration under test, before any purchase.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

Agreed with Codex on 15.09.2026, and run only after the budget owner approves it:
  the eight configurations under test only (never the analyst); one call each, sequential;
  one tool; the model is asked for exactly one tool call; at most 96 output tokens; no
  retry; spend ceiling of the scope 0.04 USD + 0.01 CHF (the conservative reservation of
  the eight calls is about 0.03 USD + 0.001 CHF).
The analyst is checked by a separate command, `--analyst`, with its own scope and approval.

Before the first call, for every configuration: its key and product id are present, the two
confirmations of a configuration that requires them are recorded for this scope, and the
reservations of all the calls together fit the scope ceiling and what is left of the day.
If anything is missing nothing is sent, the folder is removed, and the exit code is 2.
After the calls the exit code is 1 if any configuration did not answer, did not make exactly
the one tool call asked for, or did not report the model it served.

Recorded like any other call, in runs/<scope>/. The confirmations it runs under are written
to approvals-at-start.json after the preflight and before the first request.
--dry builds the requests of every configuration, keys or not, in a temporary folder,
prints the reservation, sends nothing and leaves nothing behind.

Usage: python smoke.py --scope SMOKE-<name> [--only model1,model2] [--dry]
       python smoke.py --analyst --scope SMOKE-ANALYST-<name> [--dry]
"""
import os, sys, json, argparse, datetime, shutil, tempfile
import fsio

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

TOOL = [{"type": "function", "function": {
    "name": "ping", "description": "Returns pong. Call it once with the value given.",
    "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                   "required": ["value"]}}}]
MESSAGES = [{"role": "system", "content": "You are checking that tool calling works."},
            {"role": "user", "content": "Call the tool ping once with value \"ok\". Write nothing else."}]
ANALYST_MESSAGES = [{"role": "system", "content": "Answer with one JSON object and nothing else."},
                    {"role": "user", "content": "Return {\"ok\": true}."}]
SCOPE_CAP = {"USD": 0.04, "CHF": 0.01}
MAX_OUTPUT = 96


def analyst_answer_ok(content, finish_reason):
    """True only for a complete, non-empty JSON object carrying the value asked for."""
    if finish_reason == "length" or not (content or "").strip():
        return False
    try:
        obj = json.loads(content)
    except ValueError:
        return False
    return isinstance(obj, dict) and obj.get("ok") is True


def configurations(data, analyst=False):
    """The eight configurations under test, or only the analyst's."""
    if not analyst:
        return list(data["models"])
    name = data["roles"]["analyst"]["model"]
    return [m for m in data.get("auxiliary_models", []) if m["model"] == name]


def shape(analyst):
    return (ANALYST_MESSAGES, None, {"type": "json_object"}) if analyst else (MESSAGES, TOOL, None)


def requests_for(configs, analyst):
    """The exact request bytes of every configuration, whether or not its key is present."""
    import providers
    messages, tools, fmt = shape(analyst)
    return {m["model"]: json.dumps(providers.build_payload(providers.config_for(m["model"]),
                                                           messages, tools, fmt),
                                   ensure_ascii=False).encode("utf-8")
            for m in configs}


def reservation(configs, requests):
    """The sum of the upper bounds the calls will reserve, per currency."""
    import usage
    total = {}
    for m in configs:
        b = usage.upper_bound(m["rate_key"], len(requests[m["model"]]), m["max_output_tokens"])
        if b:
            total[b["currency"]] = round(total.get(b["currency"], 0.0) + b["amount"], 6)
    return total


def preflight(configs, requests, scope, caps):
    """Everything that must hold before the first call. Returns (problems, reservation)."""
    import providers, confirm, usage, call_log
    problems = []
    for m in configs:
        cid = m.get("configuration_id", m["model"])
        for env in (m.get("key_env"), m.get("product_id_env")):
            if env and not providers.key_present(env):
                problems.append("%s: %s is absent" % (cid, env))
        if m.get("confirmation_required"):
            ok, detail = confirm.status(m["model"], scope)
            if not ok:
                problems.append("%s: %s" % (cid, detail))
        if usage.rate(m.get("rate_key")) is None:
            problems.append("%s: no rate for %s" % (cid, m.get("rate_key")))
    total = reservation(configs, requests)
    today = call_log.spent(utc_date=datetime.datetime.now(datetime.timezone.utc).date().isoformat())
    for cur, amount in total.items():
        scope_cap = (caps.get("per_scope") or {}).get(cur)
        if scope_cap is None or amount > scope_cap:
            problems.append("the reservation of %.6f %s exceeds the scope ceiling %s" % (amount, cur, scope_cap))
        day_cap = (caps.get("per_utc_day") or {}).get(cur)
        if day_cap is None or today.get(cur, 0.0) + amount > day_cap:
            problems.append("the reservation of %.6f %s does not fit what is left of the daily "
                            "ceiling %s (%.6f already counted today)" % (amount, cur, day_cap, today.get(cur, 0.0)))
    return problems, total


def write_approvals(run_dir, configs, scope):
    """The confirmations the smoke test runs under, written after the preflight and before
    the first request, so that they are in the run even if the process stops mid-way.
    Returns the reference kept in the report: file, checksum, verdict."""
    import confirm, hashlib
    evidence = {m["model"]: confirm.evidence(m["model"], scope) for m in configs
                if m.get("confirmation_required")}
    raw = json.dumps(evidence, ensure_ascii=False, indent=1).encode("utf-8")
    with open(os.path.join(run_dir, "approvals-at-start.json"), "xb") as f:
        f.write(raw)
    return {"file": "approvals-at-start.json", "sha256": hashlib.sha256(raw).hexdigest(),
            "confirmed": all(e["confirmed"] for e in evidence.values())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--analyst", action="store_true")
    ap.add_argument("--dry", action="store_true", help="build the requests, send nothing, keep nothing")
    ap.add_argument("--scope", help="the budget scope and folder name, e.g. SMOKE-20260916-A. Named "
                                    "in advance so that confirmations can be recorded for it")
    a = ap.parse_args()

    source = os.environ.get("TEST_PURCHASE_MODELS_FILE", os.path.join(BASE, "models.json"))
    data = fsio.read_json(source)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    run_id = a.scope or (("SMOKE-ANALYST-" if a.analyst else "SMOKE-") + stamp)
    run_dir = tempfile.mkdtemp(prefix="smoke-dry-") if a.dry else os.path.join(BASE, "runs", run_id)
    if not a.dry and os.path.exists(run_dir):
        raise SystemExit("scope %s was already used: a smoke scope is used once" % run_id)
    os.makedirs(run_dir, exist_ok=True)
    for m in data["models"] + data.get("auxiliary_models", []):
        m["max_output_tokens"] = MAX_OUTPUT
    data["limits"]["max_call_retries"] = 0
    caps = {"per_utc_day": data["limits"]["budget"]["per_utc_day"], "per_scope": SCOPE_CAP}
    data["limits"]["budget"] = caps
    data["neomundi"]["enabled"] = False
    frozen = os.path.join(run_dir, "models-smoke.json")
    with open(frozen, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.environ["TEST_PURCHASE_MODELS_FILE"] = frozen
    os.environ["TEST_PURCHASE_BUDGET_SCOPE"] = run_id
    os.environ.pop("TEST_PURCHASE_BUDGET_CAPS", None)
    import providers, call_log, confirm
    configs = configurations(data, a.analyst)
    if a.only:
        configs = [m for m in configs if m["model"] in set(a.only.split(","))]
    requests = requests_for(configs, a.analyst)
    print("scope:", run_id, "| ceiling:", SCOPE_CAP, "| dry" if a.dry else "")

    if a.dry:
        for m in configs:
            print(json.dumps({"configuration_id": m.get("configuration_id"),
                              "result": "dry: request of %d bytes" % len(requests[m["model"]])}))
        print("reservation if run:", reservation(configs, requests))
        shutil.rmtree(run_dir, ignore_errors=True)
        print("dry run: nothing sent, nothing kept")
        return 0

    problems, total = preflight(configs, requests, run_id, caps)
    if problems:
        for p in problems:
            print("NOT READY:", p)
        shutil.rmtree(run_dir, ignore_errors=True)
        print("nothing was sent")
        return 2
    print("reservation:", total)
    approvals = write_approvals(run_dir, configs, run_id)
    rec = call_log.Recorder(run_dir, run_id, {"pilot_id": data["pilot"]["pilot_id"],
                                              "purpose": "smoke test"}, providers.limits())
    messages, tools, fmt = shape(a.analyst)
    rows, failed = [], 0
    for m in configs:
        row = {"configuration_id": m.get("configuration_id"), "provider": m["provider"]}
        try:
            msg, meta = providers.chat(m["model"], messages, tools, timeout=60, response_format=fmt,
                                       recorder=rec, role="smoke")
            calls = msg.get("tool_calls") or []
            row.update(result="answered", observed_model=meta.get("model_reported"),
                       usage=meta.get("usage_normalised"), finish_reason=meta.get("finish_reason"))
            passed = bool(meta.get("model_reported"))
            if not a.analyst:
                correct = len(calls) == 1 and calls[0]["function"]["name"] == "ping"
                row.update(tool_calls=len(calls), tool_called_correctly=correct)
                passed = passed and correct
            else:
                # the analyst is checked for what it is for: a complete, non-empty answer that
                # is the JSON asked for. An answer cut off by the output limit, or empty because
                # every token went into reasoning, is a failure whatever model was reported
                answered = analyst_answer_ok(msg.get("content"), meta.get("finish_reason"))
                row.update(answer_ok=answered)
                passed = passed and answered
        except (call_log.LimitExceeded, call_log.ProviderCallFailed, call_log.ModelDrift) as e:
            row["result"] = "%s: %s" % (type(e).__name__, str(e)[:200])
            passed = False
        row["passed"] = passed
        failed += not passed
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    with open(os.path.join(run_dir, "smoke_report.json"), "w", encoding="utf-8", newline="") as f:
        json.dump({"run_id": run_id, "rows": rows, "failed": failed, "scope_ceiling": SCOPE_CAP,
                   "reservation_before_the_calls": total, "approvals": approvals,
                   "counted": call_log.spent(scope=run_id)}, f, ensure_ascii=False, indent=1)
    print("failed:", failed, "| counted against the ceiling:", call_log.spent(scope=run_id),
          "| report:", run_dir)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
