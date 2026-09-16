# -*- coding: utf-8 -*-
"""
Weekly and final reports of a series, from the registry and the frozen matrices.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

The report counts only what is frozen. A run whose matrix is pending review is listed
as pending, not scored; a day and a model without a counted run is listed as missing.
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
            item.update({"frozen": False})
        # the last entry for a day and label wins: a re-analysis supersedes
        lst = out.setdefault(e["label"], [])
        lst[:] = [i for i in lst if i["day"] != e["day"]] + [item]
    return out


def table(pl, data, days):
    labels = pl["labels"]
    lines = ["| Label | Days planned | Counted | Frozen | Pending review | Missing | Raw score, mean | Defect index, sum | Critical defects | Best practices |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for l in labels:
        items = data.get(l, [])
        frozen = [i for i in items if i["frozen"]]
        pending = [i for i in items if not i["frozen"]]
        missing = len(days) - len(items)
        raw = "%.2f" % statistics.mean(i["raw"] for i in frozen) if frozen else "n/a"
        lines.append("| %s | %d | %d | %d | %d | %d | %s | %s | %s | %s |" % (
            l, len(days), len(items), len(frozen), len(pending), missing, raw,
            sum(i["index"] for i in frozen) if frozen else "n/a",
            sum(i["critical"] for i in frozen) if frozen else "n/a",
            sum(i["best"] for i in frozen) if frozen else "n/a"))
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
                cells.append({"pending_review": "pending review",
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
    lines = ["# %s" % title, "",
             "Series %s. Models under blind labels; the mapping is held apart from the "
             "plan. Purchaser: scripted, identical for every model. Analyst: %s. Generated "
             "%s." % (sid, pl["analyst_model"], datetime.date.today().isoformat()), "",
             "## By model", ""] + table(pl, data, days) + ["", "## By day", ""] + \
            by_day(pl, data, days) + ["", "## Where the defects are: negative scores per position, over the frozen runs", ""] + \
            positions(pl, data) + ["",
             "## How to read this", "",
             "- Raw score: the sum of the twelve scores of one purchase, from −36 to +24; it says how the interaction went.",
             "- Defect index: class × |negative score|, summed; the weighted severity of the defects observed. Positive scores do not reduce it.",
             "- Critical defects: scores of −3. Best practices: scores of +2.",
             "- Pending review: the analysis was made but the checker blocked a position on defective evidence and the reviewer has not yet recorded a correction; the run is not scored until then. Pending measurement: the NeoMundi measurement file the plan requires is missing or invalid. Analysis failed: the purchase is complete but its analysis did not run; it is redone with series.py analyse.",
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
