# -*- coding: utf-8 -*-
"""
The technical rehearsal before the series: the scheme "5 one-off + 3 x 2".
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

Agreed with Codex on 17.09.2026. Nothing of the production logic of a series changes for
the rehearsal; this module only arranges it:

  a technical series of three configurations over two days, planned and run by series.py
  with its --technical flag: OpenAI (the reference), Anthropic (its own adapter and cache)
  and Infomaniak (CHF, a product id in the endpoint). Day 2 continues day 1 as a series;

  on day 1, five one-off purchases of the other configurations (Google, xAI, Mistral,
  Cohere, DeepSeek): the same harness, the same analysis, checker and freeze, under the
  same budget scope, each marked DIAGNOSTIC / NOT COUNTED, recorded in oneoffs.jsonl of
  the technical series. A one-off that already completed is not run again.

The technical series gets its own models file: the three under "models", the analyst and
the five others under "auxiliary_models", so that every configuration is frozen with the
plan and the harness can reach all eight. Its spend is a separate ledger and scope.

Usage:
  python technical_run.py plan --start YYYY-MM-DD --seed N [--neomundi] [--window-utc HH:MM-HH:MM]
  python technical_run.py day --day 1|2 [--override "reason"] [--dry]
  python technical_run.py status
"""
import os, sys, json, argparse, datetime, types

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import series

SERIES_CONFIGURATIONS = ("openai/gpt-4.1-mini-2025-04-14",
                         "anthropic/claude-haiku-4-5-20251001",
                         "infomaniak/mistralai/Mistral-Small-4-119B-2603")
ONEOFF_DAY = 1


REHEARSAL_CEILING = {"USD": 10.0, "CHF": 2.0}


def technical_models(source, neomundi_enabled):
    """The models file of the technical series, derived from the environment's file. The
    spend ceiling of the scope is the rehearsal's own, far below the ceiling of the series."""
    data = series.jload(source)
    tested = [m for m in data["models"] if m["configuration_id"] in SERIES_CONFIGURATIONS]
    others = [m for m in data["models"] if m["configuration_id"] not in SERIES_CONFIGURATIONS]
    if len(tested) != len(SERIES_CONFIGURATIONS):
        raise SystemExit("the environment's models file lacks one of %s" % (SERIES_CONFIGURATIONS,))
    out = dict(data)
    out["models"] = tested
    out["auxiliary_models"] = list(data.get("auxiliary_models", [])) + others
    out["neomundi"] = dict(data["neomundi"], enabled=bool(neomundi_enabled))
    out["limits"] = dict(data["limits"], budget=dict(data["limits"]["budget"],
                                                     per_scope=dict(REHEARSAL_CEILING)))
    out["version"] = data.get("version", "") + " | technical rehearsal derivation"
    out["technical_rehearsal"] = {
        "series_configurations": list(SERIES_CONFIGURATIONS),
        "one_off_configurations": [m["configuration_id"] for m in others],
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
    ns = types.SimpleNamespace(start=a.start, days=2, seed=a.seed, models=derived,
                               window_utc=a.window_utc, technical=True)
    series.plan(ns)
    sid, folder = series.current_series()
    with open(os.path.join(folder, "oneoffs.jsonl"), "a", encoding="utf-8"):
        pass
    print("technical series:", sid, "| one-offs on day %d:" % ONEOFF_DAY,
          ", ".join(m["configuration_id"] for m in series.jload(derived)["auxiliary_models"][1:]))


def oneoffs(folder):
    path = os.path.join(folder, "oneoffs.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def oneoff_done(folder, configuration_id):
    return any(e.get("configuration_id") == configuration_id and e.get("status") in series.COMPLETE
               for e in oneoffs(folder))


def run_oneoffs(sid, folder, pl, d, override, dry):
    models_path = os.path.join(folder, "models.json")
    data = series.jload(models_path)
    analyst = pl["analyst_model"]
    for m in data["auxiliary_models"]:
        if m["model"] == analyst:
            continue
        cid = m["configuration_id"]
        if oneoff_done(folder, cid):
            print("  one-off %s already complete, skipped" % cid)
            continue
        short = cid.split("/")[0].upper()
        tag = "DIAG-%s" % short
        if dry:
            print("  one-off %s -> %s (dry run, nothing executed)" % (cid, m["model"]))
            continue
        window = pl.get("window_utc")
        if window and not override and not series.in_window(window):
            _append(os.path.join(folder, "oneoffs.jsonl"),
                    {"series": sid, "day": d["day"], "date": d["date"], "configuration_id": cid,
                     "model": m["model"], "run_id": None, "status": "not_started",
                     "note": "the UTC window %s of the plan is closed" % window,
                     "recorded_at": series.now()})
            print("  one-off %s not started: the UTC window %s is closed" % (cid, window))
            continue
        ids = {"TEST_PURCHASE_SERIES_ID": sid, "TEST_PURCHASE_DAY_ID": "D%02d" % d["day"],
               "TEST_PURCHASE_PURCHASE_ID": "%s.%s" % (sid, tag), "TEST_PURCHASE_BUDGET_SCOPE": sid}
        ids.update(ledger_env(sid))
        if (pl.get("neomundi") or {}).get("required"):
            ids["TEST_PURCHASE_NEOMUNDI_CONFIG"] = os.path.join(folder, pl["neomundi"]["config_file"])
        started = series.now()
        print("  one-off %s -> %s" % (cid, m["model"]), flush=True)
        run_id, kind, note = series.purchase(m["model"], tag, models_path, ids)
        entry = {"series": sid, "day": d["day"], "date": d["date"], "configuration_id": cid,
                 "model": m["model"], "run_id": run_id, "started_at": started, "override": override}
        if kind != "complete":
            status = series.NOT_COUNTED[kind]
        else:
            status, notes = series.analyse(run_id, pl)
            note = "; ".join(notes)
            series.run_manifest(sid, d["day"], "ONEOFF:" + cid, m["model"], run_id, status, pl, override)
        _append(os.path.join(folder, "oneoffs.jsonl"),
                dict(entry, status=status, note=note, recorded_at=series.now()))
        print("     %s: %s (%s)" % (run_id, status, note[:160]))
        if status in series.STOP_DAY:
            print("  stopping the one-offs: %s" % status)
            break


def _append(path, obj):
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def preflight_all(sid, folder):
    """Before the first external call of a day: every configuration the day may reach, the
    three of the series and the five one-offs alike, has its key, its product id, a rate,
    and, where required, both confirmations for this scope. One thing missing means zero
    calls, not six purchases and then a stop at the seventh."""
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


def day_stopped(folder, day_no):
    """The status that stopped the series part of the day, or None."""
    for e in series.registry(folder):
        if e.get("day") == day_no and e.get("status") in series.STOP_DAY:
            return e["status"]
    return None


def day(a):
    sid, folder = series.current_series()
    if not sid.startswith("TECH-"):
        raise SystemExit("the current series %s is not a technical rehearsal" % sid)
    pl = series.jload(os.path.join(folder, "plan.json"))
    os.environ.update(ledger_env(sid))
    os.environ["TEST_PURCHASE_BUDGET_SCOPE"] = sid
    if not a.dry:
        preflight_all(sid, folder)
    ns = types.SimpleNamespace(day=a.day, date=None, only=None, dry=a.dry, override=a.override)
    series.day(ns)
    if a.day == ONEOFF_DAY:
        d = next(x for x in pl["schedule"] if x["day"] == a.day)
        override = series.date_override(d, a.override)
        stopped = day_stopped(folder, a.day)
        if stopped:
            print("one-off purchases not started: the series part of the day stopped with %s" % stopped)
        else:
            print("one-off purchases of day %d:" % a.day)
            run_oneoffs(sid, folder, pl, d, override, a.dry)
    if not a.dry:
        rehearsal_anchor(sid, folder, pl)


def rehearsal_anchor(sid, folder, pl):
    """One anchor over the whole rehearsal: the run manifest and the freeze manifest of
    every purchase, the three-by-two of the series and the five one-offs alike. Final once
    all eleven are frozen; provisional until then, and written again as things complete."""
    expected = len(pl["schedule"]) * len(pl["labels"]) + len(
        [m for m in series.jload(os.path.join(folder, "models.json"))["auxiliary_models"]
         if m["model"] != pl["analyst_model"]])
    runs = {}
    for e in series.registry(folder):
        if e.get("status") in series.COMPLETE and e.get("run_id"):
            runs["D%02d-%s" % (e["day"], e["label"])] = e
    for e in oneoffs(folder):
        if e.get("status") in series.COMPLETE and e.get("run_id"):
            runs["D%02d-ONEOFF-%s" % (e["day"], e["configuration_id"])] = e
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
                           "the rehearsal, the series purchases and the one-offs alike",
           "series": sid, "counted": False, "purchases_expected": expected,
           "purchases_complete": len(pinned), "unfrozen": unfrozen, "runs": pinned}
    complete = len(pinned) == expected and not unfrozen
    series.write_anchor(folder, "%s.rehearsal" % sid, out, final=complete)
    return out


def status(a):
    sid, folder = series.current_series()
    series.status(a)
    print("one-offs:")
    for e in oneoffs(folder):
        print("  D%02d %-46s %-20s %s" % (e["day"], e["configuration_id"], e["status"], e.get("run_id")))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--start", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--neomundi", action="store_true", help="observations on in the rehearsal")
    p.add_argument("--window-utc", default="12:00-16:00")
    p.add_argument("--models", default=None, help="default: the environment's models.json")
    p.set_defaults(fn=plan)
    p = sub.add_parser("day"); p.add_argument("--day", type=int, required=True, choices=(1, 2))
    p.add_argument("--override"); p.add_argument("--dry", action="store_true"); p.set_defaults(fn=day)
    p = sub.add_parser("status"); p.set_defaults(fn=status)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
