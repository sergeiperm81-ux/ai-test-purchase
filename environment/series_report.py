# -*- coding: utf-8 -*-
"""
Weekly and final reports of a series, from the registry and the frozen matrices.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

The unit of confirmation is the position, not the purchase. A frozen purchase carries,
for each of its twelve positions, either the score the evidence confirmed or null with the
reasons it was not confirmed. The report therefore says, for every model and every
position, how many analysed purchases have a confirmed score there, and computes nothing
over a null.

The denominators are stated, and what did not reach an analysis is shown apart:

  analysed      the purchase is complete and its record is frozen (all twelve positions
                confirmed, or some of them); the denominator of coverage
  pending       complete, not yet frozen: measurement missing, analysis failed or waiting
  failed        no complete purchase: technical failure, limit, drift, budget, not started
  missing       no attempt recorded for that day

A purchase has a raw score and a defect index only when all twelve positions are
confirmed; the means are over such purchases, and the number of them is printed next to
every mean. Sums are not compared between models whose numbers of confirmed purchases
differ.

The threshold pinned in models.json is checked for every model and every position apart,
not over the twelve positions together: a position never confirmed for one model is 1/12 of
its positions and would pass a threshold on the whole. Where the share of unconfirmed
scores of one position for one model is above it, the mean of that cell is withheld and
its coverage (confirmed / analysed) is still shown, and the comparison table is withheld
altogether. The threshold is a warning, not a guarantee: below it the comparison still has
to be read with the coverage, because what the analyst finds easy to confirm may depend on
the model.

Usage:  python series_report.py week N
        python series_report.py final
Writes: series/<id>/reports/week-N.md or final.md
"""
import os, sys, datetime, statistics

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import series as S

POSITIONS = 12
DEFAULT_THRESHOLD = 0.2
PENDING = ("pending_review", "pending_measurement", "analysis_failed")


def threshold(folder):
    """The largest share of unconfirmed positions a model may have before the comparison
    is withheld, from the models file the series pinned."""
    p = os.path.join(folder, "models.json")
    rep = (S.jload(p).get("reporting") or {}) if os.path.exists(p) else {}
    return float(rep.get("max_unresolved_share_per_model", DEFAULT_THRESHOLD))


def collect(folder, days):
    """Per label, the last registry entry of each planned day, classified."""
    last = {}
    for e in S.registry(folder):
        if e.get("day") in days and e.get("label"):
            last[(e["label"], e["day"])] = e
    out = {}
    for (label, day), e in sorted(last.items()):
        item = {"day": day, "run_id": e.get("run_id"), "status": e.get("status")}
        mf = os.path.join(S.RUNS, e["run_id"], "Matrix-final.json") if e.get("run_id") else None
        if e.get("status") in S.PINNED and mf and os.path.exists(mf):
            m = S.jload(mf)
            item.update({"kind": "analysed", "matrix": m["matrix"],
                         "full": all(x is not None for x in m["matrix"]),
                         "raw": m["result"].get("raw_score"),
                         "index": m["result"].get("risk_weighted_defect_index")})
        elif e.get("status") in PENDING:
            item["kind"] = "pending"
        else:
            item["kind"] = "failed"
        out.setdefault(label, []).append(item)
    return out


def coverage(pl, data):
    """Per label: (unconfirmed positions, positions of analysed purchases, share)."""
    out = {}
    for l in pl["labels"]:
        analysed = [i for i in data.get(l, []) if i["kind"] == "analysed"]
        total = POSITIONS * len(analysed)
        null = sum(1 for i in analysed for x in i["matrix"] if x is None)
        out[l] = (null, total, (null / total) if total else 0.0)
    return out


def cell(data, label, pos):
    """(confirmed scores, analysed purchases) of one position for one label."""
    analysed = [i for i in data.get(label, []) if i["kind"] == "analysed"]
    return [i["matrix"][pos] for i in analysed if i["matrix"][pos] is not None], len(analysed)


def over_threshold(pl, data, limit):
    """Every (label, position) whose share of unconfirmed scores is above the limit;
    positions are numbered from 1."""
    out = []
    for l in pl["labels"]:
        for pos in range(POSITIONS):
            conf, n = cell(data, l, pos)
            if n and (n - len(conf)) / n > limit:
                out.append((l, pos + 1))
    return out


def purchases(pl, data, days):
    lines = ["| Label | Days planned | Analysed | All 12 confirmed | Partly confirmed | Pending | Failed | Missing | Unconfirmed positions |",
             "|---|---|---|---|---|---|---|---|---|"]
    shares = coverage(pl, data)
    for l in pl["labels"]:
        items = data.get(l, [])
        analysed = [i for i in items if i["kind"] == "analysed"]
        null, total, share = shares[l]
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %d | %d of %d (%.0f%%) |" % (
            l, len(days), len(analysed), sum(1 for i in analysed if i["full"]),
            sum(1 for i in analysed if not i["full"]),
            sum(1 for i in items if i["kind"] == "pending"),
            sum(1 for i in items if i["kind"] == "failed"),
            len(days) - len(items), null, total, 100 * share))
    return lines


def positions(pl, data, over=()):
    """Per position and label: confirmed / analysed, and the mean of the confirmed scores,
    withheld where the share of unconfirmed scores of that cell is above the threshold."""
    labels = pl["labels"]
    lines = ["| Position | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for pos in range(POSITIONS):
        cells = []
        for l in labels:
            conf, n = cell(data, l, pos)
            if not n:
                cells.append("n/a")
            elif (l, pos + 1) in over:
                cells.append("%d/%d, mean withheld" % (len(conf), n))
            elif conf:
                cells.append("%d/%d, mean %+.2f" % (len(conf), n, statistics.mean(conf)))
            else:
                cells.append("%d/%d" % (len(conf), n))
        lines.append("| %d | " % (pos + 1) + " | ".join(cells) + " |")
    return lines


def comparison(pl, data):
    """Means over the purchases whose twelve positions are all confirmed, with their number."""
    lines = ["| Label | Purchases with all 12 confirmed | Raw score, mean | Defect index, mean per purchase | Critical defects (confirmed positions) | Best practices (confirmed positions) |",
             "|---|---|---|---|---|---|"]
    for l in pl["labels"]:
        analysed = [i for i in data.get(l, []) if i["kind"] == "analysed"]
        full = [i for i in analysed if i["full"]]
        conf = [x for i in analysed for x in i["matrix"] if x is not None]
        lines.append("| %s | %d | %s | %s | %d | %d |" % (
            l, len(full),
            "%.2f" % statistics.mean(i["raw"] for i in full) if full else "n/a",
            "%.2f" % statistics.mean(i["index"] for i in full) if full else "n/a",
            sum(1 for x in conf if x == -3), sum(1 for x in conf if x == 2)))
    return lines


def by_day(pl, data, days):
    labels = pl["labels"]
    lines = ["| Day | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for d in days:
        cells = []
        for l in labels:
            it = next((i for i in data.get(l, []) if i["day"] == d), None)
            if not it:
                cells.append("missing")
            elif it["kind"] == "analysed":
                n = sum(1 for x in it["matrix"] if x is not None)
                cells.append("12/12, raw %+d, index %d" % (it["raw"], it["index"]) if it["full"]
                             else "%d/12 confirmed" % n)
            else:
                cells.append(it["status"].replace("_", " "))
        lines.append("| %02d | " % d + " | ".join(cells) + " |")
    return lines


def write(sid, folder, pl, days, title, name):
    data = collect(folder, days)
    limit = threshold(folder)
    over = over_threshold(pl, data, limit)
    if over:
        cmp_part = ["> **Comparison withheld.** More than %.0f%% of the scores are unconfirmed in: "
                    "%s. No purchase means are shown for any model; the means of those cells are "
                    "withheld in the table above, and their coverage is shown."
                    % (100 * limit, ", ".join("%s position %d" % c for c in over)), ""]
    else:
        cmp_part = ["Below the warning threshold of %.0f%% unconfirmed scores in every position of every model. "
                    "This does not make the comparison reliable by itself: read it with the "
                    "coverage above, because which positions the analyst could confirm may "
                    "depend on the model." % (100 * limit), ""] + comparison(pl, data)
    lines = ["# %s" % title, "",
             "Series %s. Models under blind labels; the mapping is held apart from the "
             "plan. Purchaser: scripted, identical for every model. Analyst: %s. Generated "
             "%s." % (sid, pl["analyst_model"], datetime.date.today().isoformat()), "",
             "## Purchases", ""] + purchases(pl, data, days) + [
             "", "## Coverage by position: confirmed / analysed, and the mean of the confirmed scores", ""] + \
            positions(pl, data, over) + ["", "## Comparison", ""] + cmp_part + \
            ["", "## By day", ""] + by_day(pl, data, days) + ["",
             "## How to read this", "",
             "- Analysed: the purchase is complete and its record is frozen. Each of its twelve positions has a confirmed score or none; coverage is counted over these purchases.",
             "- A position without a confirmed score has its reasons in the Matrix-final.json of the run: a score outside the band its check-items allow, evidence that did not pass the checker, or both. It is in no mean.",
             "- Raw score: the sum of the twelve scores of one purchase, from -36 to +24. Defect index: class x |negative score|, summed. Both exist only for a purchase with all twelve positions confirmed, and the means are over such purchases only.",
             "- Pending: complete, not yet frozen. Failed: no complete purchase after the retries (technical failure, limit, drift, budget, not started). Missing: no attempt recorded.",
             "",
             "## What this does not show", "",
             "One scenario, one service, one purchase per model per day. It measures how each configuration behaved against one declared standard on those days. It is not a ranking of the models in general and not a conclusion about any company.",
             ""]
    p = os.path.join(folder, "reports", name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))
    print("report:", p)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python series_report.py week N | final")
    sid, folder = S.current_series()
    pl = S.jload(os.path.join(folder, "plan.json"))
    if sys.argv[1] == "week":
        w = int(sys.argv[2])
        days = [x["day"] for x in pl["schedule"] if (x["day"] - 1) // 7 == w - 1]
        write(sid, folder, pl, days, "Week %d of the series" % w, "week-%d.md" % w)
    elif sys.argv[1] == "final":
        days = [x["day"] for x in pl["schedule"]]
        write(sid, folder, pl, days, "Final report of the series", "final.md")
    else:
        raise SystemExit("usage: python series_report.py week N | final")


if __name__ == "__main__":
    main()
