# -*- coding: utf-8 -*-
"""
review-resolution/1: the reviewer's decisions over an analysis, applied and frozen.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

freeze_matrix.py accepts only corrections that move a score. That is right for a result,
and it leaves the reviewer no honest way to say "the analyst was right, the quotation was
badly copied" or "the automatic rule missed an equivalent phrase". This layer adds exactly
those, and nothing else, as separate kinds of decision:

  evidence_replacement  a check-item the checker blocked is evidenced again. The score is
                        unchanged. The new evidence must pass the checker's own formal test.
  status_correction     the status of one check-item was wrong. The score is unchanged; the
                        new status must be evidenced, and the evidence must pass the test.
  correction            the score of the position moves (from_score must be the analyst's),
                        with the statuses and evidence that justify it and a ground.
  contextual_override   a check-item settled "not performed" by a deterministic rule is found
                        equivalent by a human reading. The score is unchanged; the automatic
                        verdict is kept next to the decision, and the list of constructions
                        is not extended.

Nothing that was issued is rewritten: Analysis.json, the checker's report as it was on the
day and as re-checked under the current rule stay as they are and are pinned. After the
decisions every blocked check-item must be answered, and every position must satisfy the
symmetric rule of scale_rule.py as the decisions leave it: a breach -1 to -3, an unsettled
item with no breach 0, full performance +1 or +2.

A frozen result is never frozen again. When a review has to be revised, the revision is a
new layer beside it (--revision r2 and so on): its own decision file, which carries every
decision that still stands and the new ones, its own frozen matrix and manifest, and the
checksum of the manifest it supersedes. The superseded files stay as they are.

  revision   layer                  decisions                   writes
  (none)     review-resolution/1    review_resolutions.json     Matrix-final.json, .md, freeze_manifest.json
  r2         review-resolution/2    review_resolutions.r2.json  Matrix-final.r2.json, .md, freeze_manifest.r2.json

Usage:
  python review_resolution.py [--revision r2] [--check] <run_dir>
"""
import os, sys, json, glob, hashlib, argparse

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import fsio
import check_analysis
import freeze_matrix
import scale_rule

LAYER = "review-resolution/1"


def names(rev=None):
    """The files of one revision of the review layer."""
    if not rev:
        return {"rev": None, "layer": LAYER, "decisions": "review_resolutions.json",
                "matrix": "Matrix-final.json", "md": "Matrix-final.md",
                "manifest": "freeze_manifest.json", "supersedes": None}
    n = int(rev.lstrip("r"))
    prev = names(None if n == 2 else "r%d" % (n - 1))
    return {"rev": rev, "layer": "review-resolution/%d" % n,
            "decisions": "review_resolutions.%s.json" % rev,
            "matrix": "Matrix-final.%s.json" % rev, "md": "Matrix-final.%s.md" % rev,
            "manifest": "freeze_manifest.%s.json" % rev, "supersedes": prev}


KINDS = ("evidence_replacement", "status_correction", "correction", "contextual_override")
STATUSES = ("performed", "not performed", "not established")
BLOCKING = ("unsupported assessment", "awaiting_context_check")
FILE = "review_resolutions.json"


def sha(path):
    return freeze_matrix.sha256_file(path)


def admissible(run_dir, ev, known):
    """The checker's formal test of one piece of evidence, with one addition this layer
    makes: a comparison may name the final state of the run as `state.json@<sha256>`, and
    it counts as a source only if that file of this run has exactly that checksum. The
    abstract words "final state" are not a source."""
    if ev.get("type") != "comparison":
        return check_analysis.check_evidence(ev, known)
    problems, kept, pinned = [], [], 0
    for s in ev.get("sources") or []:
        if isinstance(s, str) and s.startswith("state.json@"):
            if sha(os.path.join(run_dir, "state.json")) == s.split("@", 1)[1]:
                pinned += 1
            else:
                problems.append("%s does not match state.json of this run" % s)
        else:
            kept.append(s)
    resolved = [check_analysis.resolve_source(s, known) for s in kept]
    unknown = [s for (k, _), s in zip(resolved, kept) if k is None]
    if unknown:
        problems.append("sources that are not identifiers of this run and not named primary "
                        "sources: %s" % ", ".join(unknown))
    if len({key for k, key in resolved if k}) + min(pinned, 1) < 2:
        problems.append("a comparison needs two distinct sources")
    if not str(ev.get("compared") or "").strip():
        problems.append("a comparison says what was compared")
    return problems


def load(run_dir, N=None, check_only=False):
    N = N or names()
    run_id = os.path.basename(os.path.abspath(run_dir))
    r = fsio.read_json(os.path.join(run_dir, N["decisions"]))
    if r.get("run_id") != run_id:
        raise SystemExit("%s names run %s, the folder is %s" % (N["decisions"], r.get("run_id"), run_id))
    if r.get("review_layer") != N["layer"]:
        raise SystemExit("%s is for %s, this revision is %s" % (N["decisions"], r.get("review_layer"), N["layer"]))
    if N["supersedes"]:
        prev = os.path.join(run_dir, N["supersedes"]["manifest"])
        want = (r.get("supersedes") or {}).get("sha256")
        if not os.path.exists(prev):
            raise SystemExit("revision %s supersedes %s, which is not in the run" % (N["rev"], N["supersedes"]["manifest"]))
        if want != sha(prev):
            raise SystemExit("%s must name the checksum of the %s it supersedes" % (N["decisions"], N["supersedes"]["manifest"]))
    problem = approval_problem(r, for_freeze=not check_only)
    if problem:
        raise SystemExit("%s: %s" % (N["decisions"], problem))
    return r


APPROVAL_FIELDS = ("text", "author", "at", "source")


def approval_problem(r, for_freeze):
    """Who approved what, as a record, or why the file cannot be frozen. A draft carries
    {"status": "pending"} and names neither a reviewer nor a date: a decision nobody has
    taken yet is not attributed to anybody. A freeze requires {"status": "approved"} with
    the exact words of the approval, their author, the time and where they were given. A
    non-empty string such as "PENDING" is not an approval."""
    a = r.get("approval")
    if not isinstance(a, dict):
        return ("the approval is not a record: it is {\"status\": \"pending\"} in a draft and "
                "{\"status\": \"approved\", \"text\", \"author\", \"at\", \"source\"} for a freeze")
    status = a.get("status")
    if status == "pending":
        if r.get("reviewer") or r.get("decided_on"):
            return "a pending draft names a reviewer or a date of decision: nobody has decided yet"
        return "the approval is pending: nothing is frozen until it is given" if for_freeze else None
    if status != "approved":
        return "the approval status is %r: it is pending or approved" % status
    missing = [k for k in ("reviewer", "decided_on") if not str(r.get(k) or "").strip()]
    messages = a.get("messages")
    if messages is not None:
        # the approval as given: every message on its own, verbatim, with its own time and
        # source. Nothing written by anyone else is put between them
        if not isinstance(messages, list) or not messages:
            return "approval.messages is a non-empty list of the reviewer's own messages"
        for i, m in enumerate(messages, 1):
            gap = [k for k in APPROVAL_FIELDS if not str((m or {}).get(k) or "").strip()]
            if gap:
                return "approval message %d must state %s" % (i, ", ".join(gap))
            if m.get("author") != a.get("author", m.get("author")):
                return "approval message %d has another author than the approval" % i
    else:
        missing += [k for k in APPROVAL_FIELDS if not str(a.get(k) or "").strip()]
    if missing:
        return "an approved decision must state %s" % ", ".join(missing)
    return None


def settle(run_dir, res):
    """Applies the decisions to the analysis as issued and returns (scores, statuses,
    applied, problems). Nothing is written."""
    analysis = fsio.read_json(os.path.join(run_dir, "Analysis.json"))
    check = fsio.read_json(os.path.join(run_dir, "analysis_check.json"))
    wpath, _, _ = freeze_matrix.worksheet_for(run_dir)
    where = freeze_matrix.check_item_positions(wpath)
    known = check_analysis.sources(run_dir)
    scores = {i + 1: int(s) for i, s in enumerate(analysis["matrix"])}
    issued = dict(scores)
    rows = {r["code"]: r for r in check["rows"]}
    # the status each check-item stands at before the reviewer: the checker's reading,
    # which already carries the deterministic rules
    status = {c: ("not performed" if r["outcome"] == "not performed" else
                  "not established" if r["outcome"] == "not established" else
                  r["status_reported"]) for c, r in rows.items()}
    blocked = {c for c, r in rows.items() if r["outcome"] in BLOCKING}
    overridden = set(check.get("check_items_where_the_analyst_was_overridden") or [])
    answered, moved, applied, problems = set(), set(), [], []

    for n, d in enumerate(res.get("resolutions") or [], 1):
        kind, pos = d.get("kind"), d.get("position")
        tag = "decision %d (%s, position %s)" % (n, kind, pos)
        if kind not in KINDS:
            problems.append("%s: unknown kind" % tag); continue
        if not isinstance(pos, int) or not 1 <= pos <= freeze_matrix.POSITIONS:
            problems.append("%s: no such position" % tag); continue
        if not str(d.get("ground") or "").strip():
            problems.append("%s: no ground" % tag)
        items = d.get("check_items") or ([d["check_item"]] if d.get("check_item") else [])
        for c in items:
            if where.get(c) != pos:
                problems.append("%s: check-item %s is not in this position" % (tag, c))
        new_status = d.get("status_changes") or {}
        new_evidence = d.get("evidence") or {}
        for c, st in new_status.items():
            if where.get(c) != pos:
                problems.append("%s: status change of %s, which is not in this position" % (tag, c))
            if st not in STATUSES:
                problems.append("%s: %s is not a status" % (tag, st))
            if c not in new_evidence and kind != "contextual_override":
                problems.append("%s: the new status of %s is not evidenced" % (tag, c))
        for c, ev in new_evidence.items():
            if where.get(c) != pos:
                problems.append("%s: evidence for %s, which is not in this position" % (tag, c))
                continue
            p = admissible(run_dir, ev, known)
            if p:
                problems.append("%s: the evidence for %s does not pass the checker: %s"
                                % (tag, c, "; ".join(p)[:240]))

        if kind == "evidence_replacement":
            for c in items:
                if c not in blocked:
                    problems.append("%s: %s was not blocked by the checker" % (tag, c))
                if c not in new_evidence:
                    problems.append("%s: no replacement evidence for %s" % (tag, c))
            if new_status:
                problems.append("%s: an evidence replacement changes no status" % tag)
            answered |= set(items)
        elif kind == "status_correction":
            if not new_status:
                problems.append("%s: no status is changed" % tag)
            answered |= set(new_status) & blocked
        elif kind == "correction":
            if pos in moved:
                problems.append("%s: the score of this position is decided twice" % tag)
            moved.add(pos)
            if d.get("from_score") != issued[pos]:
                problems.append("%s: from_score %s, the analyst issued %+d"
                                % (tag, d.get("from_score"), issued[pos]))
            if d.get("to_score") not in freeze_matrix.SCORES or d.get("to_score") == d.get("from_score"):
                problems.append("%s: to_score must be another score of the scale" % tag)
            else:
                scores[pos] = d["to_score"]
            answered |= (set(new_status) | set(new_evidence)) & blocked
        elif kind == "contextual_override":
            if pos in moved:
                problems.append("%s: the score of this position is decided twice" % tag)
            moved.add(pos)
            for c in items:
                if c not in overridden:
                    problems.append("%s: %s was not settled by a deterministic rule" % (tag, c))
            if d.get("from_score", issued[pos]) != issued[pos] or d.get("to_score", issued[pos]) != issued[pos]:
                problems.append("%s: a contextual override does not move the score" % tag)
            if not new_evidence:
                problems.append("%s: the reading the override rests on is not evidenced" % tag)
            for c in items:
                new_status.setdefault(c, "performed")
        for c, st in new_status.items():
            status[c] = st
        applied.append(dict(d, number=n))

    for c in sorted(blocked - answered):
        problems.append("check-item %s (position %s) was blocked and no decision answers it"
                        % (c, where.get(c)))
    for pos in range(1, freeze_matrix.POSITIONS + 1):
        mine = {c: status[c] for c, p in where.items() if p == pos and c in status}
        why = scale_rule.problem(pos, scores[pos], mine)
        if why:
            problems.append(why)
    return issued, scores, status, applied, problems


def freeze(run_dir, res, issued, scores, status, applied, N=None):
    N = N or names()
    wpath, wname, wnote = freeze_matrix.worksheet_for(run_dir)
    cls = freeze_matrix.classes(wpath)
    P = range(1, freeze_matrix.POSITIONS + 1)
    raw = sum(scores[p] for p in P)
    index = sum(cls[p] * abs(scores[p]) for p in P if scores[p] < 0)
    critical = sum(1 for p in P if scores[p] == -3)
    best = sum(1 for p in P if scores[p] == 2)
    tool = sha(os.path.abspath(__file__))
    kept = sorted(os.path.basename(p) for p in glob.glob(os.path.join(run_dir, "analysis_check.*.json")))
    result = {
        "run_id": res["run_id"],
        "review_layer": N["layer"],
        "review_layer_tool_sha256": tool,
        "how_this_was_made": "the purchase and its analysis were made under the code snapshot "
                             "of the plan of the series; the review and this freeze were made "
                             "under %s, which applies the reviewer's decisions listed below over "
                             "the analysis as issued and rewrites none of it" % N["layer"],
        "supersedes": ({"layer": N["supersedes"]["layer"], "manifest": N["supersedes"]["manifest"],
                        "sha256": sha(os.path.join(run_dir, N["supersedes"]["manifest"])),
                        "why": res.get("why_superseded")} if N["supersedes"] else None),
        "frozen_on": res["decided_on"],
        "reviewer": res["reviewer"],
        "approval": res["approval"],
        "matrix_as_issued_by_the_analyst": [issued[p] for p in P],
        "matrix": [scores[p] for p in P],
        "severity_classes": [cls[p] for p in P],
        "result": {"raw_score": raw, "risk_weighted_defect_index": index,
                   "critical_defects": critical, "best_practices": best,
                   "flag": "CRITICAL DEFECT IDENTIFIED" if critical else "no critical defect",
                   "how_the_index_is_built": "class x |negative score|, summed: the weighted "
                                             "severity of the defects observed, not a cost"},
        "check_items_not_performed_after_review": sorted(c for c, s in status.items() if s == "not performed"),
        "check_items_not_established_after_review": sorted(c for c, s in status.items() if s == "not established"),
        "decisions_applied": applied,
        "checker_reports_pinned": ["analysis_check.json"] + kept,
        "worksheet_the_classes_come_from": {"file": wname, "which": wnote},
        "what_one_purchase_shows": "how this configuration behaved in this scenario on this "
                                   "day; a diagnostic purchase, not a result of the pilot",
    }
    fsio_write(os.path.join(run_dir, N["matrix"]), json.dumps(result, ensure_ascii=False, indent=1))
    lines = ["# Frozen matrix, run %s" % res["run_id"], "",
             "Frozen under %s. %s" % (N["layer"], result["how_this_was_made"]), ""]
    if N["supersedes"]:
        lines += ["This supersedes the freeze under %s (%s, sha256 %s): %s"
                  % (N["supersedes"]["layer"], N["supersedes"]["manifest"],
                     result["supersedes"]["sha256"], res.get("why_superseded") or ""), ""]
    lines += [
             "Reviewer: %s. Frozen on %s." % (res["reviewer"], res["decided_on"]), "",
             "Approved by %s at %s (%s): «%s»" % (res["approval"]["author"], res["approval"]["at"],
                                                      res["approval"]["source"], res["approval"]["text"]), "",
             "| Position | " + " | ".join(str(p) for p in P) + " |",
             "|---|" + "---|" * len(P),
             "| Issued | " + " | ".join("%+d" % issued[p] for p in P) + " |",
             "| Final | " + " | ".join("%+d" % scores[p] for p in P) + " |",
             "| Class | " + " | ".join(str(cls[p]) for p in P) + " |", "",
             "Raw score %d. Defect index %d. Critical defects %d. Best practices %d."
             % (raw, index, critical, best), "", "## Decisions of the reviewer", ""]
    for d in applied:
        items = d.get("check_items") or ([d.get("check_item")] if d.get("check_item") else [])
        head = "**%d. %s**, position %d%s" % (d["number"], d["kind"], d["position"],
                                              (", " + ", ".join(items)) if items else "")
        if d["kind"] == "correction":
            head += ": score %+d to %+d" % (d["from_score"], d["to_score"])
        lines += [head, "", d["ground"], ""]
    fsio_write(os.path.join(run_dir, N["md"]), "\n".join(lines))

    manifest = {"what_this_is": "the checksums of the frozen files, under %s" % N["layer"],
                "run_id": res["run_id"], "frozen_on": res["decided_on"],
                "reviewer": res["reviewer"], "approval": res["approval"], "review_layer": N["layer"],
                "review_layer_tool_sha256": tool, "supersedes": result["supersedes"], "files": {}}
    pinned = [N["matrix"], N["md"], N["decisions"]]
    prev = N["supersedes"]
    while prev:
        pinned += [prev["matrix"], prev["md"], prev["decisions"], prev["manifest"]]
        prev = prev["supersedes"]
    names_ = pinned + ["Analysis.json", "Analysis.md",
             "analysis_check.json", "validation_report.json", "AI-receipt-section-II.json",
             "AI-receipt-customer-copy.md", "journal.jsonl", "messages.jsonl", "manifest.json",
             "state.json", "RUN_STATUS.md", "worksheet-as-analysed.txt",
             "deterministic_rules-as-analysed.json"] + kept
    names_ += [os.path.basename(p) for p in sorted(glob.glob(os.path.join(run_dir, "analysis-input.*.txt")))]
    for name in names_:
        manifest["files"][name] = sha(os.path.join(run_dir, name))
    for folder in ("documents-as-analysed", "documents-at-start"):
        fp = os.path.join(run_dir, folder)
        if os.path.isdir(fp):
            for name in sorted(os.listdir(fp)):
                manifest["files"][folder + "/" + name] = sha(os.path.join(fp, name))
    manifest["environment_files_at_the_time_of_review"] = {
        "check_analysis.py": sha(os.path.join(BASE, "check_analysis.py")),
        "scale_rule.py": sha(os.path.join(BASE, "scale_rule.py")),
        "review_resolution.py": tool}
    fsio_write(os.path.join(run_dir, N["manifest"]), json.dumps(manifest, ensure_ascii=False, indent=1))
    return result


def fsio_write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    ap.add_argument("--revision", default=None, help="r2, r3 ...: a new layer that supersedes the last")
    a = ap.parse_args()
    run_dir = os.path.abspath(a.run_dir)
    N = names(a.revision)
    if os.path.exists(os.path.join(run_dir, N["manifest"])):
        raise SystemExit("%s already exists: a frozen result is not frozen again" % N["manifest"])
    res = load(run_dir, N, check_only=a.check)
    issued, scores, status, applied, problems = settle(run_dir, res)
    if problems:
        print("NOT FROZEN, %d problem(s):" % len(problems))
        for p in problems:
            print("  -", p)
        raise SystemExit(2)
    print("issued :", " ".join("%+d" % issued[p] for p in sorted(issued)))
    print("final  :", " ".join("%+d" % scores[p] for p in sorted(scores)))
    if a.check:
        print("valid; nothing written (--check)")
        return
    r = freeze(run_dir, res, issued, scores, status, applied, N)
    print("frozen : raw %d, defect index %d, critical %d, best %d"
          % (r["result"]["raw_score"], r["result"]["risk_weighted_defect_index"],
             r["result"]["critical_defects"], r["result"]["best_practices"]))


if __name__ == "__main__":
    main()
