# -*- coding: utf-8 -*-
"""
Analysis.md, produced from Analysis.json.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

The result of an analysis is Analysis.json and nothing else. This module renders it for
a human reader, deterministically: same input, same file, byte for byte. Nothing is ever
read back out of the rendered text, so the wording here can change without touching a
single finding.

Usage: python analysis_render.py <run_dir>
"""
import os, sys, json

sys.stdout.reconfigure(encoding="utf-8")


def evidence_line(ev):
    t = ev["type"]
    if t == "quote":
        return "quote", ev["message_id"], "«%s»" % ev["text"]
    if t == "event":
        return "event", ev["event_id"], "%s%s" % (ev["operation"],
                                                  ", " + ev["status"] if ev.get("status") else "")
    if t == "bounded_absence":
        return "bounded_absence", " ".join(ev["examined"]), ev["absent"]
    return "comparison", " + ".join(ev["sources"]), ev["compared"]


def render(a, codes=None):
    codes = codes or {}
    L = []
    L.append("# Analysis of test purchase %s" % a["run_id"])
    L.append("")
    L.append("Produced from Analysis.json (schema %s)%s. This file is a rendering: the "
             "result of the analysis is the JSON, and every figure below comes from it."
             % (a["schema_version"], ", analysed on " + a["analysed_on"] if a.get("analysed_on") else ""))
    L.append("")
    if a.get("test_did_not_take_place"):
        t = a["test_did_not_take_place"]
        L += ["## TEST DID NOT TAKE PLACE", "",
              "Cause: %s" % t["cause"], "", "Point of breakdown: %s" % t["point_of_breakdown"], ""]
        return "\n".join(L) + "\n"

    m = a["matrix"]
    raw = sum(m)
    crit = sum(1 for s in m if s == -3)
    best = sum(1 for s in m if s == 2)
    L += ["## Matrix", "",
          "Matrix: " + ", ".join("%+d" % s for s in m), "",
          "| Position | " + " | ".join(str(i) for i in range(1, 13)) + " |",
          "|---|" + "---|" * 12,
          "| Score | " + " | ".join("%+d" % s for s in m) + " |", "",
          "Raw score %d. Critical defects %d. Best practices %d." % (raw, crit, best)]
    if crit:
        L += ["", "CRITICAL DEFECT IDENTIFIED"]
    L += ["", "The risk-weighted defect index is not computed here: it is derived from the "
              "severity classes of the worksheet when the matrix is frozen.", ""]

    pos = {p["position"]: p for p in a.get("positions", [])}
    if pos:
        L += ["## Positions", "",
              "| Position | Score | Evidence sufficient | Observed cause | Note |",
              "|---|---|---|---|---|"]
        for i in range(1, 13):
            p = pos.get(i)
            if not p:
                L.append("| %d | %+d | not stated | | |" % (i, m[i - 1]))
                continue
            note = p.get("why_insufficient") or p.get("zero_reason") or ""
            L.append("| %d | %+d | %s | %s | %s |"
                     % (i, p["score"], "yes" if p["evidence_sufficient"] else "no",
                        p.get("observed_cause", ""), note))
        L.append("")

    cases = [(p["position"], p["case"]) for p in a.get("positions", []) if p.get("case")]
    if cases:
        L += ["## Cases", ""]
        for position, c in cases:
            L += ["**Position %d.** %s" % (position, c["sentence"]),
                  "", "> %s" % c["quote"], "", "Source: %s." % c["source"], ""]

    L += ["## Check-items", "",
          "| Code | Position | Status | Evidence type | Source | Evidence |",
          "|---|---|---|---|---|---|"]
    for it in a["check_items"]:
        kind, src, ev = evidence_line(it["evidence"])
        ev = ev.replace("|", "\\|")
        L.append("| %s | %d | %s | %s | %s | %s |"
                 % (it["code"], it["position"], it["status"], kind, src, ev))
    L.append("")

    if a.get("receipt_against_the_record"):
        L += ["## The AI Receipt against the transcript and the log", "",
              a["receipt_against_the_record"], ""]
    if a.get("cross_cutting_observations"):
        L += ["## Cross-cutting observations", ""]
        L += ["- " + x for x in a["cross_cutting_observations"]] + [""]
    L += ["## Conclusions", ""] + ["%d. %s" % (i, x) for i, x in enumerate(a["conclusions"], 1)] + [""]
    L += ["## Recommendations", ""] + ["%d. %s" % (i, x) for i, x in enumerate(a["recommendations"], 1)] + [""]
    return "\n".join(L) + "\n"


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python analysis_render.py <run_dir>")
    run_dir = os.path.abspath(sys.argv[1])
    a = json.load(open(os.path.join(run_dir, "Analysis.json"), encoding="utf-8"))
    text = render(a)
    p = os.path.join(run_dir, "Analysis.md")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print("rendered:", p, "|", len(text), "chars")


if __name__ == "__main__":
    main()
