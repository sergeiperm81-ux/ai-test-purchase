# -*- coding: utf-8 -*-
"""
Weekly and final reports of a series, from the registry and the frozen matrices.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

The report counts only scores that are confirmed. A purchase whose record is frozen but
whose score the evidence did not confirm is "unresolved": it is left out of every mean and
sum, and the share of such purchases is shown for every model beside any comparison. When
that share is above the threshold of the plan for any model, the comparison across models
is withheld altogether: a comparison over the scores that happened to be easy to confirm
would say more about the analyst than about the models. A day and a model without a
counted run is listed as missing.
Models appear under their blind labels. The report is arithmetic over Matrix-final.json
and nothing else: no reading of dialogues happens here.

Usage:  python series_report.py week N
        python series_report.py final
Writes: series/<id>/reports/week-N.md or final.md
"""
import os, sys, json, datetime, statistics

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import series as S


def collect(folder, days):
    """Per label: the counted runs of the given days with their frozen result, if any."""
    reg = S.registry(folder)
    out = {}
    for e in reg:
        if e["day"] not in days or e["status"] not in S.COMPLETE:
            continue
        run_dir = os.path.join(S.RUNS, e["run_id"])
        mf = os.path.join(run_dir, "Matrix-final.json")
        item = {"day": e["day"], "run_id": e["run_id"], "status": e["status"]}
        if e["status"] == "frozen" and os.path.exists(mf):
            m = S.jload(mf)
            item.update({"frozen": True, "matrix": m["matrix"], "raw": m["result"]["raw_score"],
                         "index": m["result"]["risk_weighted_defect_index"],
                         "critical": m["result"]["critical_defects"],
                         "best": m["result"]["best_practices"],
                         "corrections": len(m.get("corrections_applied") or [])})
        else:
            item.update({"frozen": False, "unresolved": e["status"] == "unresolved"})
        # the last entry for a day and label wins: a re-analysis supersedes
        lst = out.setdefault(e["label"], [])
        lst[:] = [i for i in lst if i["day"] != e["day"]] + [item]
    return out


DEFAULT_THRESHOLD = 0.2


def threshold(folder):
    """The largest share of unresolved scores a model may have before the comparison is
    withheld, from the models file the series pinned."""
    p = os.path.join(folder, "models.json")
    rep = (S.jload(p).get("reporting") or {}) if os.path.exists(p) else {}
    return float(rep.get("max_unresolved_share_per_model", DEFAULT_THRESHOLD))


def unresolved_shares(pl, data):
    """Per label: (unresolved, counted purchases, share)."""
    out = {}
    for l in pl["labels"]:
        items = data.get(l, [])
        n = sum(1 for i in items if i.get("unresolved"))
        out[l] = (n, len(items), (n / len(items)) if items else 0.0)
    return out


def withheld(shares, limit):
    """The labels whose share of unresolved scores is above the limit: any one of them
    withholds the comparison."""
    return sorted(l for l, (_, counted, share) in shares.items() if counted and share > limit)


def table(pl, data, days, compare=True):
    labels = pl["labels"]
    shares = unresolved_shares(pl, data)
    lines = ["| Label | Days planned | Counted | Score confirmed | Score unresolved | Pending | Missing | Raw score, mean | Defect index, sum | Critical defects | Best practices |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for l in labels:
        items = data.get(l, [])
        frozen = [i for i in items if i["frozen"]]
        pending = [i for i in items if not i["frozen"] and not i.get("unresolved")]
        n, counted, share = shares[l]
        missing = len(days) - len(items)
        if compare and frozen:
            cmp_cells = ("%.2f" % statistics.mean(i["raw"] for i in frozen),
                         sum(i["index"] for i in frozen), sum(i["critical"] for i in frozen),
                         sum(i["best"] for i in frozen))
        else:
            cmp_cells = ("withheld" if not compare else "n/a",) * 4
        lines.append("| %s | %d | %d | %d | %d (%.0f%%) | %d | %d | %s | %s | %s | %s |" % (
            (l, len(days), counted, len(frozen), n, 100 * share, len(pending), missing) + cmp_cells))
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
            elif not it["frozen"]:
                cells.append({"unresolved": "score unresolved",
                              "pending_review": "pending review",
                              "pending_measurement": "pending measurement",
                              "analysis_failed": "analysis failed"}.get(it["status"], "pending"))
            else:
                cells.append("raw %+d, index %d%s" % (it["raw"], it["index"],
                                                       ", CRITICAL" if it["critical"] else ""))
        lines.append("| %02d | " % d + " | ".join(cells) + " |")
    return lines


def positions(pl, data):
    """How often each of the twelve positions scored negative, per label: where the
    defects are, not only how many."""
    labels = pl["labels"]
    lines = ["| Position | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for pos in range(12):
        cells = []
        for l in labels:
            frozen = [i for i in data.get(l, []) if i["frozen"]]
            neg = sum(1 for i in frozen if i["matrix"][pos] < 0)
            cells.append("%d/%d" % (neg, len(frozen)) if frozen else "n/a")
        lines.append("| %d | " % (pos + 1) + " | ".join(cells) + " |")
    return lines


def write(sid, folder, pl, days, title, name):
    data = collect(folder, days)
    limit = threshold(folder)
    over = withheld(unresolved_shares(pl, data), limit)
    banner = []
    if over:
        banner = ["> **Comparison withheld.** The share of purchases whose score the evidence did "
                  "not confirm is above %.0f%% for %s. Means and sums are not shown for any "
                  "model: a comparison over the scores that were easy to confirm would not be a "
                  "comparison of the models. The counts below are complete." % (100 * limit, ", ".join(over)), ""]
    lines = ["# %s" % title, ""] + banner + [
             "Series %s. Models under blind labels; the mapping is held apart from the "
             "plan. Purchaser: scripted, identical for every model. Analyst: %s. Generated "
             "%s." % (sid, pl["analyst_model"], datetime.date.today().isoformat()), "",
             "## By model", ""] + table(pl, data, days, compare=not over) + ["", "## By day", ""] + \
            by_day(pl, data, days) + ["", "## Where the defects are: negative scores per position, over the frozen runs", ""] + \
            positions(pl, data) + ["",
             "## How to read this", "",
             "- Raw score: the sum of the twelve scores of one purchase, from −36 to +24; it says how the interaction went.",
             "- Defect index: class × |negative score|, summed; the weighted severity of the defects observed. Positive scores do not reduce it.",
             "- Critical defects: scores of −3. Best practices: scores of +2.",
             "- Score unresolved: the record of the purchase is frozen, but after the analyst's one automatic repair the evidence still did not confirm the score. It is not in any mean or sum; its share per model is shown so that the comparison can be judged. Above the threshold of the plan for any model, the comparison is withheld.",
             "- Pending: the purchase is complete and its record is not yet frozen. Pending measurement: the NeoMundi measurement file the plan requires is missing or invalid. Analysis failed: the purchase is complete but its analysis did not run; it is redone with series.py analyse.",
             "- Missing: no complete purchase was obtained for that day and model after the retries; the attempts are in the registry.",
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
