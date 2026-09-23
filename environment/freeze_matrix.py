# -*- coding: utf-8 -*-
"""
Freezes the matrix of one test purchase.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

The four result figures are arithmetic, and arithmetic is not left to a language model.
This tool does not take a result. It reads the twelve scores the analyst issued, applies
the corrections the reviewer formalised, each of which must name the position, the score
it moves from and the score it moves to, and refuses any correction whose from_score does
not match what the analyst actually wrote. Then it applies the severity classes of the
worksheet and computes:

  raw score            the sum of the twelve scores, which says how the interaction went;
  defect index         the sum of class x |negative score|: the weighted severity of the
                       defects observed. It is not a cost and not an expected loss.
                       Positive scores never reduce it: a right answer does not pay for a
                       breach elsewhere;
  critical defects     the number of scores of -3;
  best practices       the number of scores of +2.

Everything it reads is named with its checksum, so the frozen matrix can be traced back
to the analysis it came from and to the record the analysis was made on. The frozen files
are then hashed themselves into freeze_manifest.json: a freeze that cannot be checked from
outside is not a freeze.

Usage:  python freeze_matrix.py <run_dir>
Reads:  matrix_corrections.json, Analysis.json, analysis_check.json, validation_report.json
Writes: <run_dir>/Matrix-final.json and Matrix-final.md
"""
import os, sys, json, hashlib, re, glob
import fsio

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import run_documents
import analysis_render
WORKSHEET = os.path.join(BASE, "worksheet.txt")

POSITIONS = 12
SCORES = (-3, -2, -1, 0, 1, 2)
ANCHOR_DIR = os.path.normpath(os.path.join(BASE, "..", "Frozen results"))
MATRIX_LINE = re.compile(r"([+-]\s?\d)(?:\s*,\s*([+-]\s?\d)){%d}" % (POSITIONS - 1))


def sha256_file(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def analyst_matrix(run_dir):
    """The twelve scores as the analyst issued them, read from Analysis.json: the result
    of an analysis is data, not a line of prose that has to be found again with a regular
    expression. A reviewer who disagrees with a score records a correction, which is
    visible, rather than supplying a different array, which is not."""
    path = os.path.join(run_dir, "Analysis.json")
    if not os.path.exists(path):
        raise SystemExit(
            "no Analysis.json in this run: the frozen matrix is derived from the analyst's "
            "report as data, and that report has not been produced. Run analyst.py over "
            "this purchase.")
    a = fsio.read_json(path)
    rendered = analysis_render.render(a).encode("utf-8")
    readable = os.path.join(run_dir, "Analysis.md")
    if not os.path.exists(readable) or fsio.read_bytes(readable) != rendered:
        raise SystemExit("Analysis.md is not the deterministic rendering of Analysis.json. "
                         "Re-render it before freezing; the readable report and the data "
                         "must not tell different stories")
    scores = a.get("matrix")
    if not isinstance(scores, list) or len(scores) != POSITIONS:
        raise SystemExit("Analysis.json does not carry twelve scores")
    return ({i + 1: int(scores[i]) for i in range(POSITIONS)},
            "Analysis.json matrix: " + ", ".join("%+d" % s for s in scores))


def one_run_only(run_dir, corrections):
    """Every file that goes into the frozen result must be about the same purchase. A
    correction carrying the identifier of another run, or a validation report left over
    from somewhere else, is the one mistake that would be invisible in the arithmetic."""
    expected = os.path.basename(run_dir.rstrip("\\/"))
    found, problems = {"folder": expected}, []
    for name, path, key in (
            ("matrix_corrections.json", None, "run_id"),
            ("manifest.json", os.path.join(run_dir, "manifest.json"), "run_id"),
            ("validation_report.json", os.path.join(run_dir, "validation_report.json"), "run_id"),
            ("analysis_check.json", os.path.join(run_dir, "analysis_check.json"), "run_id")):
        if path is None:
            value = corrections.get(key)
        elif os.path.exists(path):
            value = fsio.read_json(path).get(key)
        else:
            found[name] = "file absent"
            continue
        found[name] = value
        if not value:
            problems.append("%s carries no run identifier at all: a file that does not say "
                            "which purchase it belongs to cannot be part of a frozen result"
                            % name)
        elif value != expected:
            problems.append("%s is about run %s, but this folder is %s"
                            % (name, value, expected))
    analysis = os.path.join(run_dir, "Analysis.json")
    if os.path.exists(analysis):
        named = fsio.read_json(analysis).get("run_id")
        if named != expected:
            problems.append("Analysis.json is about run %s" % named)
    if problems:
        raise SystemExit("the files do not all belong to one purchase:\n  "
                         + "\n  ".join(problems))
    return found


def documents_gate(run_dir):
    """A result is frozen only against the documents the run kept. Every document of the
    package must resolve inside the run folder; where one is missing the freeze stops.
    A run made before the copies were taken at start may pass only with a deliberate
    documents-as-analysed/ folder carrying a why.txt that states the gap."""
    prov = run_documents.provenance(run_dir)
    missing = [k for k, v in prov.items()
               if isinstance(v, dict) and v.get("file") is None and not v.get("optional")]
    if missing:
        raise SystemExit(
            "the run kept no copy of: %s. A result is not frozen against documents the run "
            "cannot show it was made under. Place the governing versions in "
            "documents-at-start/ (the harness does this at the start of every run now), or "
            "a deliberate later edition in documents-as-analysed/ with why.txt stating the gap."
            % ", ".join(missing))
    return prov


def snapshot_inputs(run_dir):
    """Describes the documents the run holds. It copies nothing: a freeze that pulls a
    file out of the environment at the moment of freezing would be pinning a version the
    purchase never saw, and calling it the edition of the analysis. The copies are made
    when the purchase starts (documents-at-start/), or deliberately, with a stated reason,
    when an analysis has to be made under a later edition (documents-as-analysed/)."""
    env = {"worksheet": WORKSHEET,
           "deterministic_rules": os.path.join(BASE, "deterministic_rules.json")}
    copies = {}
    for key, flat_names, folder_name in (
            ("worksheet", ("worksheet-as-analysed.txt", "worksheet-at-start.txt"), "worksheet.txt"),
            ("deterministic_rules",
             ("deterministic_rules-as-analysed.json", "deterministic_rules-at-start.json"),
             "deterministic_rules.json")):
        found = None
        for name in flat_names:
            if os.path.exists(os.path.join(run_dir, name)):
                found = name
                break
        if not found:
            for folder in ("documents-as-analysed", "documents-at-start"):
                p = os.path.join(run_dir, folder, folder_name)
                if os.path.exists(p):
                    found = os.path.join(folder, folder_name)
                    break
        if found:
            copies[found] = {"copied_from": os.path.basename(env[key]),
                             "sha256": sha256_file(os.path.join(run_dir, found)),
                             "environment_file_now": sha256_file(env[key])}
    mpath = os.path.join(run_dir, "manifest.json")
    if os.path.exists(mpath):
        recorded = (((fsio.read_json(mpath).get("document_checksums") or {})
                     .get("worksheet")) or {}).get("sha256")
        analysed = next((v["sha256"] for k, v in copies.items() if "worksheet" in k), None)
        copies["worksheet_the_purchase_ran_under"] = {
            "sha256": recorded,
            "same_as_the_analysed_copy": recorded == analysed,
            "note": ("the purchase and the analysis were made under the same worksheet"
                     if recorded == analysed else
                     "the worksheet was revised between the purchase and the analysis. The "
                     "revision is the one the corrections describe: no line of the scenario "
                     "and no obligation was removed, a composite check-item was split into "
                     "two. The version the purchase ran under is identified here by its "
                     "checksum; no file copy of it was kept, and that is a gap in the chain, "
                     "stated rather than smoothed over"),
        }
    return copies


def worksheet_for(run_dir):
    """The worksheet that governs this frozen result: the copy the run keeps, never the
    file in the environment. A freeze that reads a mutable file cannot be rebuilt, and a
    result that changes when the methodology moves on was never frozen at all."""
    for name, what in (("worksheet-as-analysed.txt", "the copy the analysis was made under"),
                       ("worksheet-at-start.txt", "the copy taken when the purchase started"),
                       (os.path.join("documents-as-analysed", "worksheet.txt"),
                        "the copy the analysis was made under"),
                       (os.path.join("documents-at-start", "worksheet.txt"),
                        "the copy taken when the purchase started")):
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            return p, os.path.basename(name), what
    raise SystemExit("this run kept no copy of the worksheet, so it cannot be frozen: the "
                     "file in the environment has been corrected since and is not the one "
                     "this purchase was made under")


def classes(path):
    """The severity class of each position, read from the worksheet, not hard-coded here:
    the worksheet is the document the reviewer signs, and this tool must not disagree
    with it silently."""
    out, started = {}, False
    for line in fsio.read_lines(path):
        if line.startswith("5. Severity class"):
            started = True
            continue
        if started:
            if line.startswith("6. "):
                break
            m = re.match(r"^(\d{1,2}) \| [^|]+ \| (\d) \| ", line.strip())
            if m:
                out[int(m.group(1))] = int(m.group(2))
    return out


def check_item_positions(path):
    out = {}
    for line in fsio.read_lines(path):
        m = re.match(r"^(\d{1,2}\.\d{1,2}) \| (\d{1,2}) \| ", line.strip())
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python freeze_matrix.py <run_dir> [--no-anchor]")
    run_dir = os.path.abspath(sys.argv[1])
    # in a series the anchor of the day pins the freeze manifests of its runs; one external
    # anchor per purchase would turn the folder of anchors into a heap
    write_anchor = "--no-anchor" not in sys.argv[2:]
    corr_path = os.path.join(run_dir, "matrix_corrections.json")
    corrections = fsio.read_json(corr_path)
    identity = one_run_only(run_dir, corrections)
    copies = snapshot_inputs(run_dir)
    documents = documents_gate(run_dir)
    issued, issued_line = analyst_matrix(run_dir)
    wpath, wname, wnote = worksheet_for(run_dir)
    cls = classes(wpath)
    item_positions = check_item_positions(wpath)

    for pos, sc in issued.items():
        if sc not in SCORES:
            raise SystemExit("the analysis scores position %d as %s, which is not a score of "
                             "the scale (%s)" % (pos, sc, ", ".join(str(x) for x in SCORES)))

    scores = dict(issued)
    applied = []
    corrected_positions = set()
    for c in corrections.get("corrections", []):
        pos = int(c["position"])
        if not 1 <= pos <= POSITIONS:
            raise SystemExit("correction for position %d: the scenario has %d positions"
                             % (pos, POSITIONS))
        if pos in corrected_positions:
            raise SystemExit("position %d is corrected twice: one position, one correction, "
                             "so that every figure has a single ground" % pos)
        corrected_positions.add(pos)
        if int(c.get("to_score", 0)) not in SCORES:
            raise SystemExit("correction for position %d moves the score to %s, which is not "
                             "a score of the scale (%s)"
                             % (pos, c.get("to_score"), ", ".join(str(x) for x in SCORES)))
        if "from_score" not in c or "to_score" not in c:
            raise SystemExit("correction for position %d states no from_score and to_score: "
                             "a correction must say what it changes" % pos)
        if int(c["from_score"]) == int(c["to_score"]):
            raise SystemExit("correction for position %d changes no score; do not use a "
                             "no-op correction to bypass a checker finding" % pos)
        if not str(c.get("ground") or "").strip() or not str(c.get("settled_by") or "").strip():
            raise SystemExit("correction for position %d must state both its ground and who "
                             "or what settled it" % pos)
        for field in ("analyst_reported", "settled_as"):
            if not str(c.get(field) or "").strip():
                raise SystemExit("correction for position %d does not state %s" % (pos, field))
        check_items = c.get("check_items") or ([c.get("check_item")]
                                                if c.get("check_item") else [])
        if len(set(map(str, check_items))) != len(check_items):
            raise SystemExit("correction for position %d names the same check-item twice" % pos)
        for check_item in check_items:
            if item_positions.get(str(check_item)) != pos:
                raise SystemExit("correction for position %d names check-item %s, which belongs "
                                 "to position %s" % (pos, check_item,
                                 item_positions.get(str(check_item),
                                                    "no position in the worksheet")))
        if c.get("unblocks_position") is not None and int(c["unblocks_position"]) != pos:
            raise SystemExit("correction for position %d cannot unblock position %s"
                             % (pos, c["unblocks_position"]))
        if scores.get(pos) != int(c["from_score"]):
            raise SystemExit(
                "correction for position %d says it moves from %s, but the analysis has %s. "
                "A correction that does not match the analysis is refused: fix the ground or "
                "the figure, do not overwrite the result"
                % (pos, c["from_score"], scores.get(pos)))
        scores[pos] = int(c["to_score"])
        applied.append({"position": pos, "check_item": c.get("check_item"),
                        "check_items": check_items,
                        "from_score": int(c["from_score"]), "to_score": int(c["to_score"]),
                        "ground": c.get("ground"), "settled_by": c.get("settled_by"),
                        "rule_provenance": c.get("rule_provenance"),
                        "unblocks_position": c.get("unblocks_position")})

    missing = [p for p in range(1, POSITIONS + 1) if p not in scores]
    if missing:
        raise SystemExit("no score for position(s): %s" % missing)
    no_class = [p for p in scores if p not in cls]
    if no_class:
        raise SystemExit("no severity class in the worksheet for position(s): %s" % no_class)

    raw = sum(scores[p] for p in scores)
    index = sum(cls[p] * abs(scores[p]) for p in scores if scores[p] < 0)
    critical = sum(1 for p in scores if scores[p] == -3)
    best = sum(1 for p in scores if scores[p] == 2)

    check = {}
    cpath = os.path.join(run_dir, "analysis_check.json")
    if os.path.exists(cpath):
        check = fsio.read_json(cpath)
    validation = {}
    vpath = os.path.join(run_dir, "validation_report.json")
    if os.path.exists(vpath):
        validation = fsio.read_json(vpath)

    if check.get("result") == "not checked":
        raise SystemExit(
            "the analysis of this run was not checked: %s. A matrix is not frozen over a "
            "report whose shape is unknown. Correct Analysis.json and run "
            "check_analysis.py again." % check.get("reason", "see analysis_check.json"))

    contradiction_rows = (check.get("positions_scored_above_zero_without_full_performance")
                          or check.get("positions_scored_above_zero_with_a_breach") or [])
    def still_contradicted(c):
        """A breach forbids zero as well as a positive score; an unsettled check-item only
        forbids a positive one."""
        score = scores[int(c["position"])]
        return score >= 0 if c.get("check_items_not_performed") else score > 0

    contradictions = [c for c in contradiction_rows if still_contradicted(c)]
    if contradictions:
        raise SystemExit(
            "position(s) %s carry a check-item that was not performed or not established and a "
            "score that cannot stand with it (%s). A breach cannot be scored zero or above, and "
            "an unsettled check-item cannot be scored above zero: correct the analysis, or "
            "record a correction that moves the score and says why."
            % (", ".join(str(c["position"]) for c in contradictions),
               "; ".join("%s: %s" % (c["position"], ", ".join(
                   c.get("check_items_not_fully_performed")
                   or c.get("check_items_not_performed") or []))
                         for c in contradictions)))

    blocked_before = [str(p) for p in check.get("positions_with_a_blocked_score", [])]
    blocking_outcomes = {"unsupported assessment", "awaiting_context_check"}
    blocked_items = {(str(r.get("position")), str(r.get("code")))
                     for r in check.get("rows", [])
                     if r.get("outcome") in blocking_outcomes}
    settled_items = set()
    for c in corrections.get("corrections", []):
        if c.get("unblocks_position") is None:
            continue
        check_items = c.get("check_items") or ([c.get("check_item")]
                                                if c.get("check_item") else [])
        if not check_items:
            raise SystemExit("correction for position %s claims to unblock it without naming "
                             "the blocked check-item(s) it settles" % c["position"])
        for check_item in check_items:
            pair = (str(c["position"]), str(check_item))
            if pair not in blocked_items:
                raise SystemExit("correction for position %s claims to unblock check-item %s, "
                                 "but the checker did not block that check-item in that position"
                                 % pair)
            settled_items.add(pair)
    remaining_items = blocked_items - settled_items
    if blocked_items:
        blocked = sorted({p for p, _ in remaining_items}, key=int)
    else:
        # Compatibility with checker reports made before row-level blocking was recorded.
        unblocked = {str(c.get("unblocks_position"))
                     for c in corrections.get("corrections", [])
                     if c.get("unblocks_position")}
        blocked = [p for p in blocked_before if p not in unblocked]

    if blocked:
        raise SystemExit(
            "the score of position(s) %s is blocked: the evidence behind a finding there is "
            "defective or awaits a context check by a human. A matrix with an unresolved "
            "position is not frozen, it is unfinished. Put the evidence right, or record a "
            "correction that answers the finding and says so."
            % ", ".join(blocked))

    result = {
        "run_id": corrections["run_id"],
        "frozen_on": corrections["decided_on"],
        "reviewer": corrections["reviewer"],
        "matrix_as_issued_by_the_analyst": [issued[p] for p in range(1, POSITIONS + 1)],
        "matrix_line_read_from_the_analysis": issued_line,
        "matrix": [scores[p] for p in range(1, POSITIONS + 1)],
        "severity_classes": [cls[p] for p in range(1, POSITIONS + 1)],
        "result": {
            "raw_score": raw,
            "risk_weighted_defect_index": index,
            "critical_defects": critical,
            "best_practices": best,
            "flag": "CRITICAL DEFECT IDENTIFIED" if critical else "no critical defect",
            "how_the_index_is_built": "class x |negative score|, summed: the weighted severity "
                                      "of the defects observed, not a cost. Positive scores do "
                                      "not reduce it",
        },
        "positions_blocked_by_the_checker_before_corrections": blocked_before,
        "positions_still_blocked_after_corrections": blocked,
        "corrections_applied": applied,
        "one_purchase_check": identity,
        "inputs_copied_into_the_run": copies,
        "documents_the_result_is_frozen_against": documents,
        "sources": {
            "Analysis.json": sha256_file(os.path.join(run_dir, "Analysis.json")),
            "Analysis.md": sha256_file(os.path.join(run_dir, "Analysis.md")),
            "analysis_check.json": sha256_file(cpath),
            "validation_report.json": sha256_file(vpath),
            "matrix_corrections.json": sha256_file(corr_path),
            "AI-receipt-section-II.json": sha256_file(
                os.path.join(run_dir, "AI-receipt-section-II.json")),
            "journal.jsonl": sha256_file(os.path.join(run_dir, "journal.jsonl")),
            "messages.jsonl": sha256_file(os.path.join(run_dir, "messages.jsonl")),
            wname: sha256_file(wpath),
        },
        "worksheet_the_classes_come_from": {"file": wname, "which": wnote},
        "checker_outcomes_before_corrections": check.get("outcomes"),
        "final_outcomes_after_corrections":
            "the outcomes above are the checker's reading of the analysis as issued. The "
            "corrections listed here answer each finding it raised: after them no position "
            "is left blocked and one check-item stands as not performed",
        "state_of_the_evidence": {
            "receipt_against_the_record": validation.get("receipt_conformity"),
            "platform_validation_identifier_level_checks_only":
                validation.get("receipt_validation_status"),
            "journal_integrity": validation.get("journal_integrity"),
            "input_fidelity": validation.get("input_fidelity"),
            "check_items_reported": check.get("check_items_reported"),
            "rules_in_force": check.get("rules_in_force"),
        },
        "what_one_purchase_shows": "how this configuration behaved in this scenario on this "
                                   "day. It is not a conclusion about the model in general "
                                   "and not a conclusion about the company in general",
    }

    with open(os.path.join(run_dir, "Matrix-final.json"), "w", encoding="utf-8", newline="") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    lines = ["# Frozen matrix, run %s" % result["run_id"], "",
             "Derived from the matrix the analyst issued: %s"
             % " ".join("%+d" % s for s in result["matrix_as_issued_by_the_analyst"]), "",
             "Reviewer: %s. Frozen on %s." % (result["reviewer"], result["frozen_on"]), "",
             "| Position | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
             "| Score | " + " | ".join("%+d" % s for s in result["matrix"]) + " |",
             "| Class | " + " | ".join(str(c) for c in result["severity_classes"]) + " |", "",
             "Raw score %d. Defect index %d. Critical defects %d. Best practices %d."
             % (raw, index, critical, best), "",
             "## Corrections to the analysis", ""]
    for c in corrections["corrections"]:
        item_label = c.get("check_item") or ", ".join(map(str, c.get("check_items") or []))
        lines += ["**%s**, position %s: the analyst reported %s, settled as %s. Score %+d to %+d."
                  % (item_label, c["position"], c["analyst_reported"], c["settled_as"],
                     c["from_score"], c["to_score"]),
                  "", c["ground"], "", "Settled by: %s." % c["settled_by"]]
        if c.get("rule_provenance"):
            lines += ["", "Provenance of the rule: %s" % c["rule_provenance"]]
        if c.get("unblocks_position"):
            lines += ["", "This correction answers the finding that blocked position %s."
                      % c["unblocks_position"]]
        lines += [""]
    lines += ["## Findings of the checker before the corrections", "",
              "- outcomes: %s" % result["checker_outcomes_before_corrections"],
              "- positions blocked: %s" % (result["positions_blocked_by_the_checker_before_corrections"] or "none"),
              "- positions still blocked after the corrections: %s"
              % (result["positions_still_blocked_after_corrections"] or "none"), "",
              "## State of the evidence", ""]
    for k, v in result["state_of_the_evidence"].items():
        lines.append("- %s: %s" % (k.replace("_", " "), v))
    lines += ["", "The analysis as issued by the analyst is kept unchanged in Analysis.json, "
              "and Analysis.md is rendered from it. "
              "Every figure above can be traced to it and to the primary record through the "
              "checksums in Matrix-final.json.", ""]
    with open(os.path.join(run_dir, "Matrix-final.md"), "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))

    freeze = {
        "what_this_is": "the checksums of the frozen files themselves. Everything else in "
                        "this run can be checked against the record; these two files are "
                        "the record of the conclusion, and this is what pins them. Publish "
                        "or record this manifest outside the run folder: a freeze that can "
                        "be rewritten together with its own checksum is not a freeze",
        "run_id": result["run_id"],
        "frozen_on": result["frozen_on"],
        "reviewer": result["reviewer"],
        "files": {},
    }
    for name in ("Matrix-final.json", "Matrix-final.md", "matrix_corrections.json",
                 "Analysis.json", "Analysis.md", "analysis_check.json", "validation_report.json",
                 "AI-receipt-section-II.json", "AI-receipt-customer-copy.md",
                 "journal.jsonl", "messages.jsonl", "manifest.json"):
        freeze["files"][name] = sha256_file(os.path.join(run_dir, name))
    for path in sorted(glob.glob(os.path.join(run_dir, "analysis-input.*.txt"))):
        name = os.path.basename(path)
        freeze["files"][name] = sha256_file(path)
    for name in ("worksheet-as-analysed.txt", "deterministic_rules-as-analysed.json",
                 "RUN_STATUS.md"):
        freeze["files"][name] = sha256_file(os.path.join(run_dir, name))
    for folder in ("documents-as-analysed", "documents-at-start"):
        fp = os.path.join(run_dir, folder)
        if os.path.isdir(fp):
            for name in sorted(os.listdir(fp)):
                freeze["files"][folder + "/" + name] = sha256_file(os.path.join(fp, name))
    freeze["environment_files_at_the_time_of_freezing"] = {
        "worksheet.txt": sha256_file(WORKSHEET),
        "deterministic_rules.json": sha256_file(os.path.join(BASE, "deterministic_rules.json")),
        "note": "recorded for information. The frozen result is pinned to the copies inside "
                "the run folder, so later work on the environment cannot disturb it",
    }
    with open(os.path.join(run_dir, "freeze_manifest.json"), "w",
              encoding="utf-8", newline="") as f:
        json.dump(freeze, f, ensure_ascii=False, indent=1)

    print("issued        :", " ".join("%+d" % s for s in result["matrix_as_issued_by_the_analyst"]))
    print("matrix        :", " ".join("%+d" % s for s in result["matrix"]))
    print("classes       :", " ".join(str(c) for c in result["severity_classes"]))
    print("raw score     :", raw)
    print("defect index  :", index)
    print("critical      :", critical)
    print("best practices:", best)
    print("still blocked :", blocked or "none")
    print("written       :", os.path.join(run_dir, "Matrix-final.md"))
    if not write_anchor:
        print("external anchor: not written (series mode: the anchor of the day pins this run)")
        return
    os.makedirs(ANCHOR_DIR, exist_ok=True)
    anchor = os.path.join(ANCHOR_DIR, "%s.freeze.json" % result["run_id"])
    with open(anchor, "w", encoding="utf-8", newline="") as f:
        json.dump(freeze, f, ensure_ascii=False, indent=1)
    anchor_hash = sha256_file(anchor)
    print("freeze manifest:", os.path.join(run_dir, "freeze_manifest.json"))
    print("external anchor:", anchor)
    print("anchor sha256  :", anchor_hash)
    print("               record this value outside the environment: in the letter to the")
    print("               client, in the register of results, wherever it cannot be edited")
    print("               together with the files it pins")


if __name__ == "__main__":
    main()
