# -*- coding: utf-8 -*-
"""
Two-stage confirmation of a model that may not be called on a flag.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

A configuration marked confirmation_required (Command A, a flagship-tier model) is called
only when the owner of the budget has confirmed it in separate stages, for the budget scope
of the call (the smoke test, the technical run, a series). Each stage is recorded when the
owner gives it, with who gave it, the estimate they were shown and their words. A stage is
refused if the previous one is missing or was recorded less than min_seconds_between ago.

  approvals/confirmations.jsonl   append-only

Usage:
  python confirm.py record --model M --scope S --stage 1 --by "name" --estimate "..." --note "..."
  python confirm.py status --model M --scope S
"""
import os, sys, json, argparse, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)


def path():
    return os.environ.get("TEST_PURCHASE_APPROVALS") or os.path.join(BASE, "approvals", "confirmations.jsonl")


def _records(model, scope):
    if not os.path.exists(path()):
        return []
    with open(path(), encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    return [r for r in rows if r["model"] == model and r["scope"] == scope]


def _when(r):
    return datetime.datetime.fromisoformat(r["recorded_at_utc"].replace("Z", "+00:00"))


def requirement(model):
    import providers
    req = providers.config_for(model).get("confirmation_required")
    if not req:
        return None
    if not isinstance(req, dict):
        req = {"why": str(req)}
    return {"stages": int(req.get("stages", 2)),
            "min_seconds_between": int(req.get("min_seconds_between", 60)),
            "why": req.get("why")}


def status(model, scope):
    """(confirmed, detail)."""
    req = requirement(model)
    if not req:
        return True, "no confirmation required"
    stages = {}
    for r in _records(model, scope):
        stages.setdefault(r["stage"], r)
    for n in range(1, req["stages"] + 1):
        if n not in stages:
            return False, "stage %d of %d is not recorded for scope %s" % (n, req["stages"], scope)
        if n > 1 and (_when(stages[n]) - _when(stages[n - 1])).total_seconds() < req["min_seconds_between"]:
            return False, "stage %d was recorded too soon after stage %d" % (n, n - 1)
    return True, "confirmed in %d stages for scope %s" % (req["stages"], scope)


def evidence(model, scope):
    """What a run keeps of its confirmations: the requirement, the verdict and the records.
    None for a model that requires none."""
    req = requirement(model)
    if not req:
        return None
    ok, detail = status(model, scope)
    return {"model": model, "scope": scope, "requirement": req, "confirmed": ok,
            "detail": detail, "records": _records(model, scope)}


def record(model, scope, stage, by, estimate, note):
    req = requirement(model)
    if not req:
        raise SystemExit("model %s does not require confirmation" % model)
    if not 1 <= stage <= req["stages"]:
        raise SystemExit("stage must be between 1 and %d" % req["stages"])
    existing = {r["stage"]: r for r in _records(model, scope)}
    if stage in existing:
        raise SystemExit("stage %d is already recorded for %s in scope %s" % (stage, model, scope))
    now = datetime.datetime.now(datetime.timezone.utc)
    if stage > 1:
        prev = existing.get(stage - 1)
        if not prev:
            raise SystemExit("stage %d cannot be recorded before stage %d" % (stage, stage - 1))
        if (now - _when(prev)).total_seconds() < req["min_seconds_between"]:
            raise SystemExit("stage %d must be a separate confirmation, at least %d s after "
                             "stage %d" % (stage, req["min_seconds_between"], stage - 1))
    row = {"model": model, "scope": scope, "stage": stage,
           "recorded_at_utc": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
           "by": by, "estimate_shown": estimate, "words": note}
    os.makedirs(os.path.dirname(path()), exist_ok=True)
    with open(path(), "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record")
    for k in ("--model", "--scope", "--by", "--estimate", "--note"):
        p.add_argument(k, required=True)
    p.add_argument("--stage", type=int, required=True)
    p = sub.add_parser("status")
    p.add_argument("--model", required=True)
    p.add_argument("--scope", required=True)
    a = ap.parse_args()
    if a.cmd == "record":
        print(json.dumps(record(a.model, a.scope, a.stage, a.by, a.estimate, a.note), ensure_ascii=False))
    else:
        ok, detail = status(a.model, a.scope)
        print(detail)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
