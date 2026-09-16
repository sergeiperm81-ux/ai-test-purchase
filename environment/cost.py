# -*- coding: utf-8 -*-
"""
What one test purchase actually costs.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

Tokens are measured: they come from the responses of the providers. Money is arithmetic
on rates.json, by the rate_key of the configuration (never by the model name a provider
reports), kept per currency and never added across currencies. This is the computed cost;
NeoMundi receives cost null, as agreed.

A run made with the call log (calls/calls.jsonl) is costed from it: every attempt of every
call, failed and interrupted attempts included. An attempt that returned no usage is
listed, and the total then reads "not less than".
Runs made before the call log are costed from the manifest and analyst_usage by model name.

Usage:  python cost.py <run_dir> [more run dirs...]
Writes: <run_dir>/cost.json for each run
"""
import os, sys, json

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import usage
import call_log

ROLES = {"agent": "service agent", "analyst": "analyst", "purchaser": "test purchaser"}


def _add(bucket, cost):
    bucket[cost["currency"]] = round(bucket.get(cost["currency"], 0.0) + cost["amount"], 6)


def from_call_log(run_dir, rates):
    parts, total, unknown = {}, {}, []
    for c in call_log.attempts(run_dir):
        name = ROLES.get(c.get("role"), c.get("role") or "other")
        p = parts.setdefault(name, {"configurations": [], "operations": set(), "attempts": 0,
                                    "failed_attempts": 0, "interrupted_attempts": 0,
                                    "input_tokens": 0, "cached_input_tokens": 0,
                                    "cache_write_tokens": 0, "output_tokens": 0,
                                    "cost": {}, "bases": set()})
        if c.get("configuration_id") not in p["configurations"]:
            p["configurations"].append(c.get("configuration_id"))
        p["operations"].add(c["operation_id"])
        p["attempts"] += 1
        p["failed_attempts"] += c["outcome"] == "failed"
        p["interrupted_attempts"] += c["outcome"] == "interrupted"
        n = c.get("usage_normalised")
        cost = usage.price(c.get("rate_key"), n, rates) if n else None
        if cost is None:
            unknown.append(c["provider_attempt_id"])
            continue
        for k in ("input_tokens", "cache_write_tokens", "output_tokens"):
            p[k] += n.get(k) or 0
        p["cached_input_tokens"] += n.get("cached_input_tokens") or 0
        _add(p["cost"], cost)
        _add(total, cost)
        p["bases"].add(cost["basis"])
    for p in parts.values():
        p["operations"] = len(p["operations"])
        p["bases"] = sorted(p["bases"])
    return parts, total, unknown


def legacy(run_dir, rates):
    """Runs made before the call log: usage in the manifest and the analyst file."""
    manifest = json.load(open(os.path.join(run_dir, "manifest.json"), encoding="utf-8"))
    parts, total, unknown = {}, {}, []
    sources = [("service agent", manifest.get("api_responses") or [],
                manifest.get("model_reported") or manifest.get("model_requested")),
               ("test purchaser", manifest.get("buyer_api_responses") or [],
                manifest.get("buyer_model_requested"))]
    p = os.path.join(run_dir, "analyst_usage.jsonl")
    if os.path.exists(p):
        calls = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        sources.append(("analyst", calls, calls[-1].get("model_reported") if calls else None))
    for name, entries, model in sources:
        part = {"models": [model], "attempts": len(entries), "cost": {}}
        for e in entries:
            n = e.get("usage_normalised") or usage.normalise("openai_chat", e.get("usage"))
            m = e.get("model_reported") or model
            cost = usage.price_legacy(m, n, rates) if n else None
            if cost is None:
                unknown.append("%s call without a rate (%s)" % (name, m))
                continue
            _add(part["cost"], cost)
            _add(total, cost)
        parts[name] = part
    return parts, total, unknown


def one(run_dir):
    rates = usage.load_rates()
    manifest = json.load(open(os.path.join(run_dir, "manifest.json"), encoding="utf-8"))
    if os.path.exists(os.path.join(run_dir, "calls", "calls.jsonl")):
        parts, total, unknown = from_call_log(run_dir, rates)
        source = "calls/calls.jsonl: every attempt, failed and interrupted attempts included"
    else:
        parts, total, unknown = legacy(run_dir, rates)
        source = "manifest.json and analyst_usage (a run made before the call log)"
    report = {
        "run_id": manifest.get("run_id"),
        "rates": {"file": "rates.json", "as_at": rates.get("as_at")},
        "source": source,
        "parts": parts,
        "cost_computed_by_currency": total,
        "complete": not unknown,
        "unpriced_or_unmeasured": unknown,
        "reading": ("the cost of the run" if not unknown else
                    "not less than the amounts shown: %d attempt(s) have no usage or no rate"
                    % len(unknown)),
        "label": "computed from published rates; not a provider invoice",
    }
    with open(os.path.join(run_dir, "cost.json"), "w", encoding="utf-8", newline="") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(os.path.basename(run_dir))
    for name, part in parts.items():
        print("  %-16s attempts %3d  %s" % (name, part.get("attempts", 0),
                                           ", ".join("%.4f %s" % (v, k) for k, v in part["cost"].items()) or "-"))
    print("  %-16s %s (%s)" % ("TOTAL", ", ".join("%.4f %s" % (v, k) for k, v in total.items()) or "-",
                               report["reading"]))
    return report


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python cost.py <run_dir> [more run dirs...]")
    for d in sys.argv[1:]:
        one(os.path.abspath(d))
