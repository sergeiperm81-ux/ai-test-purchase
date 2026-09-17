# -*- coding: utf-8 -*-
"""
The technical rehearsal before the series: one daily round, exactly as the series runs it.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

Decided by Sergei on 17.09.2026. A day of the series is one round of eight independent
purchases, one per configuration: eight separate purchaser instances of the same script,
eight journals, eight receipts, eight analyses, eight freezes. The rehearsal is one such
round, planned and run by series.py with its --technical flag, marked DIAGNOSTIC / NOT
COUNTED throughout, under its own budget scope and ledgers. Nothing of the production
logic of a series changes for it; this module only derives its configuration and adds the
checks a paid day must pass before its first call.

Usage:
  python technical_run.py plan --start YYYY-MM-DD --seed N [--neomundi] [--window-utc HH:MM-HH:MM]
  python technical_run.py day [--override "reason"] [--dry]
  python technical_run.py status
"""
import os, sys, argparse, types

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import series

REHEARSAL_DAYS = 1
REHEARSAL_CEILING = {"USD": 10.0, "CHF": 2.0}
# every outgoing NeoMundi request of the rehearsal, observations and contracts, successful or
# not: 50 per purchase (about 20 observations and 20 contracts), 400 for the eight
REHEARSAL_NEOMUNDI_CAPS = {"max_requests_per_scope": 400, "max_requests_per_purchase": 50}


def technical_models(source, neomundi_enabled):
    """The models file of the rehearsal: the environment file as it is, all eight tested
    configurations and the analyst, with the rehearsal spend ceiling and NeoMundi caps."""
    data = series.jload(source)
    out = dict(data)
    out["neomundi"] = dict(data["neomundi"], enabled=bool(neomundi_enabled), **REHEARSAL_NEOMUNDI_CAPS)
    out["limits"] = dict(data["limits"], budget=dict(data["limits"]["budget"],
                                                     per_scope=dict(REHEARSAL_CEILING)))
    out["version"] = data.get("version", "") + " | technical rehearsal derivation"
    out["technical_rehearsal"] = {"rounds": REHEARSAL_DAYS,
                                  "purchases_per_round": len(data["models"]),
                                  "derived_from_sha256": series.sha256_file(source)}
    return out


def ledger_env(sid):
    """The rehearsal spends from its own ledgers, apart from anything counted."""
    return {"TEST_PURCHASE_LEDGER": os.path.join(BASE, "ledger", "technical-%s.jsonl" % sid),
            "TEST_PURCHASE_NEOMUNDI_LEDGER": os.path.join(BASE, "ledger", "technical-%s-neomundi.jsonl" % sid)}


def plan(a):
    source = getattr(a, "models", None) or os.path.join(BASE, "models.json")
    derived = os.path.join(series.SERIES, "technical-models-%s.json" % a.start)
    series.jdump(derived, technical_models(source, a.neomundi))
    ns = types.SimpleNamespace(start=a.start, days=REHEARSAL_DAYS, seed=a.seed, models=derived,
                               window_utc=a.window_utc, technical=True)
    series.plan(ns)
    sid, folder = series.current_series()
    print("technical rehearsal:", sid, "| one round of %d purchases"
          % len(series.jload(derived)["models"]))


def preflight_all(sid, folder):
    """Before the first external call of the round: every configuration of the round has its
    key, its product id, a rate, and, where required, both confirmations for this scope. One
    thing missing means zero calls, not seven purchases and then a stop at the eighth."""
    import providers, confirm, usage
    providers.MODELS = os.path.join(folder, "models.json")     # the frozen file of the rehearsal
    data = series.jload(providers.MODELS)
    problems = []
    for m in data["models"] + data.get("auxiliary_models", []):
        cid = m.get("configuration_id", m["model"])
        for env in (m.get("key_env"), m.get("product_id_env")):
            if env and not providers.key_present(env):
                problems.append("%s: %s is absent" % (cid, env))
        if usage.rate(m.get("rate_key")) is None:
            problems.append("%s: no rate for %s" % (cid, m.get("rate_key")))
        if m.get("confirmation_required"):
            ok, detail = confirm.status(m["model"], sid)
            if not ok:
                problems.append("%s: %s" % (cid, detail))
    if problems:
        raise SystemExit("the rehearsal is not ready; nothing was sent: " + "; ".join(problems))


def day(a):
    sid, folder = series.current_series()
    if not sid.startswith("TECH-"):
        raise SystemExit("the current series %s is not a technical rehearsal" % sid)
    pl = series.jload(os.path.join(folder, "plan.json"))
    os.environ.update(ledger_env(sid))
    os.environ["TEST_PURCHASE_BUDGET_SCOPE"] = sid
    if not a.dry:
        preflight_all(sid, folder)
    ns = types.SimpleNamespace(day=1, date=None, only=None, dry=a.dry, override=a.override)
    series.day(ns)
    if not a.dry:
        rehearsal_anchor(sid, folder, pl)


def rehearsal_anchor(sid, folder, pl):
    """One anchor over the whole rehearsal: the run manifest and the freeze manifest of
    every purchase of the round. Final once all are frozen; provisional until then, and
    written again as things complete."""
    expected = len(pl["schedule"]) * len(pl["labels"])
    runs = {}
    for e in series.registry(folder):
        if e.get("status") in series.COMPLETE and e.get("run_id"):
            runs["D%02d-%s" % (e["day"], e["label"])] = e
    pinned, unfrozen = {}, []
    for key, e in sorted(runs.items()):
        run_dir = os.path.join(series.RUNS, e["run_id"])
        entry = {"run_id": e["run_id"], "status": e["status"]}
        for name in ("run_manifest.json", "freeze_manifest.json"):
            p = os.path.join(run_dir, name)
            entry[name] = series.sha256_file(p) if os.path.exists(p) else None
        if e["status"] != "frozen" or not entry["freeze_manifest.json"]:
            unfrozen.append(key)
        pinned[key] = entry
    out = {"what_this_is": "the anchor of the technical rehearsal: DIAGNOSTIC, NOT COUNTED. It "
                           "pins the run manifest and the freeze manifest of every purchase of "
                           "the round",
           "series": sid, "counted": False, "purchases_expected": expected,
           "purchases_complete": len(pinned), "unfrozen": unfrozen, "runs": pinned}
    complete = len(pinned) == expected and not unfrozen
    series.write_anchor(folder, "%s.rehearsal" % sid, out, final=complete)
    return out


def status(a):
    series.status(a)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--start", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--neomundi", action="store_true", help="observations on in the rehearsal")
    p.add_argument("--window-utc", default="12:00-16:00")
    p.add_argument("--models", default=None, help="default: the environment models.json")
    p.set_defaults(fn=plan)
    p = sub.add_parser("day")
    p.add_argument("--override"); p.add_argument("--dry", action="store_true"); p.set_defaults(fn=day)
    p = sub.add_parser("status"); p.set_defaults(fn=status)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
