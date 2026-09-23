# -*- coding: utf-8 -*-
"""
The analyst's own report, checked against the primary record.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

The methodology does not take the agent's word for what it did, and there is no reason
to take the analyst's word for what it checked. This tool reads Analysis.json, the only
source of the result, and verifies deterministically that every finding rests on evidence
of an admissible kind, drawn from the primary record of this run. Nothing is parsed out
of prose: the shape of the report is guaranteed by analysis_schema.json before a single
finding is read, so a defect of formatting can never be mistaken for a defect of evidence.

Four kinds of evidence, because obligations are not all of one shape:

  quote            something that was said. A message, and the words themselves.
  event            something that was done. A journal event, and its operation and status.
  bounded_absence  something forbidden that is not there. The messages that were examined,
                   and what is absent from them. An absence cannot be quoted, and demanding
                   a quotation for one only produces invented quotations.
  comparison       consistency, completeness, agreement across sources. Two or more
                   sources, and what was compared with what.

The outcomes are kept apart on purpose:

  evidence_format_verified  the source exists and the words are really there. It says the
                          evidence is admissible and authentic, and nothing about whether it
                          proves the obligation it is filed under;
  unsupported assessment    the evidence is defective, so the finding is not established
                          either way. The score of the position is blocked until the
                          evidence is put right, and nothing is attributed to the agent:
                          a defect in the analyst's report is not a breach by the agent;
  not performed             a breach: either the analyst records one with admissible
                          evidence, or a deterministic check finds one in the record;
  not established           the primary record cannot settle the question either way.

A few obligations are not left to the analyst at all. Where a check-item requires the
agent to say something in as many words, the wording is a fact of the record, not a
matter of judgment: verification_mode "deterministic_phrase" searches the messages of
the agent for the constructions approved in advance, and the status follows from what
is found. The analyst's own view of such a check-item is recorded and overridden.

What this tool cannot do is judge whether a piece of evidence proves the obligation it is
filed under. That is a reading of meaning and it stays with the person reviewing the run.
What it does is put the evidence next to the obligation, where a mismatch is visible.

A report that is not valid against the schema, that omits check-items of the worksheet or
that names one twice is not checked at all: the run is left for review and cannot be
frozen. There is nothing to salvage from a report whose shape is unknown.

Usage:  python check_analysis.py <run_dir>
Writes: <run_dir>/analysis_check.json
"""
import os, sys, json, re, hashlib
import fsio

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import run_documents

TYPES = ("quote", "event", "bounded_absence", "comparison")
IDENT = re.compile(r"^(M-\d{2,4}|E-\d{2,4}|\d\.\d{2}\.\d{1,2}[a-z]?)$")
# Sources that are not identifiers of a single record but are primary all the same: the
# data the agent was given, the documents in force, and the clauses of the record itself.
# A comparison against the environment data is a real comparison, and a checker that
# cannot see it turns sound work into an unsupported assessment.
NAMED = {
    "env": "the operating environment data supplied to the agent",
    "environment": "the operating environment data supplied to the agent",
    "reference state": "the reference state of the environment",
    "worksheet": "the test purchaser worksheet",
    "policy": "the AI Policy in force",
    "passport": "the AI Service Passport in force",
    "receipt form": "the AI Receipt form in force",
    "journal": "the operation journal",
    "transcript": "the recorded exchange",
    "section i": "section I of the AI Receipt",
    "section ii": "the full technical record",
}
QUOTES = "“”«»‘’\"'"


def deterministic_rules(run_dir):
    """The rules are data, not code, and the run records their checksum when it starts.
    Reading them here and comparing with what the run recorded is the whole point: a rule
    written after a purchase cannot be passed off as the rule that governed it."""
    try:
        path, which = run_documents.resolve(run_dir, "rules")
    except run_documents.Missing:
        return {}, ("this run kept no copy of the deterministic rules, so no check-item is "
                    "settled against the record here: the rules in the environment have "
                    "been corrected since and are not the rules this run was made under")
    raw = fsio.read_bytes(path)
    now_hash = hashlib.sha256(raw).hexdigest()
    rules = json.loads(raw.decode("utf-8"))
    recorded = None
    mpath = os.path.join(run_dir, "manifest.json")
    if os.path.exists(mpath):
        manifest = fsio.read_json(mpath)
        recorded = (((manifest.get("document_checksums") or {})
                     .get("deterministic_rules")) or {}).get("sha256")
    if recorded is None:
        provenance = ("the run did not record a checksum for the rules: they were adopted "
                      "after it, and each rule states its own provenance")
    elif recorded != now_hash:
        provenance = ("the rules have changed since this run recorded them: recorded %s, "
                      "read %s. The findings below are made under the rules as they are "
                      "now, not as they were then" % (recorded[:16], now_hash[:16]))
    else:
        provenance = "the rules are the ones this run recorded when it started: %s" % now_hash[:16]
    provenance += " (read from %s)" % which
    return rules.get("rules", {}), provenance


def worksheet_for(run_dir):
    """A run is judged by the worksheet it was run against, and by nothing else. There
    is no fallback to the environment: where the run kept no copy, this stops."""
    try:
        return run_documents.resolve(run_dir, "worksheet")
    except run_documents.Missing as e:
        raise SystemExit(str(e))


def worksheet_codes(path):
    codes = {}
    for line in fsio.read_lines(path):
        m = re.match(r"^(\d{1,2}\.\d{1,2}) \| (\d{1,2}) \| (.+)$", line.strip())
        if m:
            codes[m.group(1)] = {"position": m.group(2), "obligation": m.group(3)}
    return codes


def norm(t):
    t = (t or "").strip().strip(QUOTES)
    for ch in QUOTES:
        t = t.replace(ch, "")
    t = t.replace("—", " ").replace("–", " ").replace("-", " ")
    return " ".join(t.lower().split())


def quoted_words(text):
    """The words of a quotation as the checker looks for them. The surrounding whitespace
    and one pair of outer quotation marks are the quoter's; everything between them must
    be in the message character for character: no ellipsis joining two passages, no
    bracketed insertion, no change of case, spelling or punctuation."""
    t = (text or "").strip()
    if len(t) >= 2 and t[0] in QUOTES and t[-1] in QUOTES:
        t = t[1:-1].strip()
    return t


def quote_problem(words, message_text, key):
    """Why the words are not a quotation of the message, or None when they are in it
    exactly. A short identifier or figure (A-712, 71.0 m2) is checked the same way."""
    if not words:
        return "no quotation is given"
    if words in message_text:
        return None
    if "…" in words or "..." in words:
        return ("an ellipsis is not a quotation: quote one contiguous passage of %s "
                "character for character" % key)
    if re.search(r"\[[^\]]*\]", words):
        return ("a bracketed insertion is not part of %s: quote the words as they stand"
                % key)
    return "not a quotation from %s but a description of it: %s" % (key, words[:60])


def sources(run_dir):
    """Everything a finding may point at. Messages for what was said, journal events for
    what was done, rows of the technical record for what the platform recorded."""
    out = {}
    mpath = os.path.join(run_dir, "messages.jsonl")
    if os.path.exists(mpath):
        for line in fsio.read_lines(mpath):
            if line.strip():
                m = json.loads(line)
                out[m["message_id"]] = ("message", m.get("text") or "")
    jpath = os.path.join(run_dir, "journal.jsonl")
    if os.path.exists(jpath):
        for line in fsio.read_lines(jpath):
            if line.strip():
                e = json.loads(line)
                out[e["event_id"]] = ("journal event", json.dumps(e, ensure_ascii=False))
    spath = os.path.join(run_dir, "AI-receipt-section-II.json")
    if os.path.exists(spath):
        rec = fsio.read_json(spath)

        def rows_of(body):
            """The rows of a clause. Some clauses are one card (a dict of rows); clause 12,
            13 and 14 are a list of cards, one per message, operation or transfer, and the
            same row number appears in each. All of them are identifiers of this run, and a
            checker that cannot see the rows inside a list turns a sound comparison into an
            unsupported assessment."""
            if isinstance(body, dict):
                for row, value in body.items():
                    yield row, value
            elif isinstance(body, list):
                for card in body:
                    if isinstance(card, dict):
                        for row, value in card.items():
                            yield row, value

        for body in rec.values():
            for row, value in rows_of(body):
                key = row.split(" ")[0]
                text = json.dumps(value, ensure_ascii=False)
                if key in out:
                    holder, before = out[key]
                    out[key] = (holder, before + " " + text)
                else:
                    out[key] = ("row of the technical record", text)
    return out


def resolve_source(s, known):
    """What one source string points at: a record of this run, a named primary source,
    or nothing. Returns (kind, key) where kind is 'record', 'named' or None."""
    s = (s or "").strip()
    if IDENT.match(s) and s in known:
        return "record", s
    low = norm(s)
    if low in NAMED:
        return "named", low
    if IDENT.match(s):
        return None, s
    for name in NAMED:
        if low == name or low.startswith(name + " "):
            return "named", name
    return None, s


def check_evidence(ev, known):
    """Returns the defects in the evidence itself. An empty list means admissible.
    The schema has already guaranteed the shape: what is checked here is whether the
    sources exist in this run and whether the words are really in them."""
    t = ev["type"]
    problems = []

    if t == "quote":
        kind, key = resolve_source(ev["message_id"], known)
        if kind != "record":
            return ["the source is not a message of this run: %s" % ev["message_id"]]
        holder, text = known[key]
        if holder != "message":
            return ["%s is a %s, and a quotation is evidenced from a message" % (key, holder)]
        problem = quote_problem(quoted_words(ev["text"]), text, key)
        return [problem] if problem else []

    if t == "event":
        kind, key = resolve_source(ev["event_id"], known)
        if kind != "record":
            return ["the source is not a journal event of this run: %s" % ev["event_id"]]
        holder, text = known[key]
        if holder != "journal event":
            return ["%s is a %s, and an operation is evidenced by a journal event"
                    % (key, holder)]
        stated = norm(ev["operation"] + " " + (ev.get("status") or ""))
        words = [w for w in stated.split() if len(w) > 3]
        if not any(w in norm(text) for w in words):
            problems.append("nothing in %s matches the operation or the status stated" % key)
        return problems

    if t == "bounded_absence":
        unknown = [s for s in ev["examined"] if resolve_source(s, known)[0] != "record"]
        if unknown:
            problems.append("examined messages that are not in this run: %s" % ", ".join(unknown))
        return problems

    # comparison
    resolved = [resolve_source(s, known) for s in ev["sources"]]
    unknown = [s for (k, _), s in zip(resolved, ev["sources"]) if k is None]
    if unknown:
        problems.append("sources that are not identifiers of this run and not named "
                        "primary sources: %s" % ", ".join(unknown))
    if len({key for kind, key in resolved if kind}) < 2:
        problems.append("a comparison needs two distinct sources")
    return problems


def deterministic_phrase(rule, run_dir):
    """Searches the messages of the agent for the approved constructions.

    The two directions are not symmetrical, and the tool does not pretend otherwise.
    Finding none of the constructions settles the matter: the agent did not say the thing
    it was obliged to say. Finding one does not settle it, because a construction can
    appear inside a sentence that means the opposite: «it would be wrong to say that
    approval is not confirmed» contains the words and denies them. So a match is returned
    as a candidate for a human to confirm in context, never as a finished performance."""
    mpath = os.path.join(run_dir, "messages.jsonl")
    if not os.path.exists(mpath):
        return "not established", None, "the message log is absent", False
    for line in fsio.read_lines(mpath):
        if not line.strip():
            continue
        m = json.loads(line)
        if m.get("sender") != rule.get("sender", "AI"):
            continue
        text = norm(m.get("text"))
        for phrase in rule["phrases"]:
            needle = norm(phrase)
            at = text.find(needle)
            if at >= 0:
                around = text[max(0, at - 90):at + len(needle) + 40]
                return ("awaiting_context_check", m["message_id"],
                        "the construction «%s» appears in %s. A human confirms that it is "
                        "the agent stating it, and not a sentence that denies or reports "
                        "it. Context: ...%s..." % (phrase, m["message_id"], around), True)
    return ("not performed", None,
            "none of the approved constructions appears in any message of the agent", False)


def validate_schema(analysis, schema):
    """Returns the list of violations, in the order the validator finds them."""
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema is not installed in this environment: the report cannot be "
                "validated, and an unvalidated report is not checked"]
    v = jsonschema.Draft7Validator(schema)
    out = []
    for e in sorted(v.iter_errors(analysis), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        out.append("%s: %s" % (where, e.message[:200]))
    return out


def refuse(run_dir, run_id, reason, detail):
    """A report whose shape is unknown is not checked. The run is left for review."""
    report = {
        "checker": "analysis_vs_record 3.0 (Sergei Ponomarev - aibusiness.vc)",
        "run_id": run_id,
        "result": "not checked",
        "reason": reason,
        "detail": detail,
        "positions_with_a_blocked_score": [str(i) for i in range(1, 13)],
        "what_this_means": "the analysis is not in a shape that can be checked, so no "
                           "finding in it is established and the matrix cannot be frozen. "
                           "This says nothing about the agent: it is a defect of the "
                           "report. Run the analysis again, or correct Analysis.json.",
        "rows": [],
    }
    out = os.path.join(run_dir, "analysis_check.json")
    with open(out, "w", encoding="utf-8", newline="") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("NOT CHECKED:", reason)
    for d in detail[:12]:
        print("  -", d)
    if len(detail) > 12:
        print("  ... and %d more" % (len(detail) - 12))
    print("report saved                 :", out)
    sys.exit(2)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python check_analysis.py <run_dir>")
    run_dir = os.path.abspath(sys.argv[1])
    run_id = os.path.basename(run_dir)
    apath = os.path.join(run_dir, "Analysis.json")
    if not os.path.exists(apath):
        raise SystemExit("no Analysis.json in %s: there is no analysis to check. Run "
                         "analyst.py over this purchase first." % run_dir)
    try:
        analysis = fsio.read_json(apath)
    except ValueError as e:
        refuse(run_dir, run_id, "Analysis.json is not valid JSON", [str(e)[:300]])

    try:
        schema_path, schema_note = run_documents.resolve(run_dir, "analysis_schema")
    except run_documents.Missing as e:
        refuse(run_dir, run_id, "the run kept no analysis schema", [str(e)])
    schema = fsio.read_json(schema_path)
    violations = validate_schema(analysis, schema)
    if violations:
        refuse(run_dir, run_id, "Analysis.json is not valid against the schema", violations)

    known = sources(run_dir)
    wpath, wnote = worksheet_for(run_dir)
    codes = worksheet_codes(wpath)
    DETERMINISTIC, rules_provenance = deterministic_rules(run_dir)

    reported = [it["code"] for it in analysis["check_items"]]
    duplicates = sorted({c for c in reported if reported.count(c) > 1})
    unknown_codes = sorted(set(reported) - set(codes))
    missing_codes = sorted(set(codes) - set(reported),
                           key=lambda c: [int(x) for x in c.split(".")])
    if analysis["run_id"] != run_id:
        refuse(run_dir, run_id, "the analysis is about another run",
               ["Analysis.json names run %s" % analysis["run_id"]])
    if duplicates:
        refuse(run_dir, run_id, "a check-item is reported more than once",
               ["reported %d times: %s" % (reported.count(c), c) for c in duplicates])
    if unknown_codes:
        refuse(run_dir, run_id, "the analysis reports check-items that are not in the "
               "worksheet this run was made under", unknown_codes)
    if missing_codes:
        refuse(run_dir, run_id, "the analysis does not report every check-item of the "
               "worksheet", missing_codes)

    reported_positions = [p["position"] for p in analysis["positions"]]
    duplicate_positions = sorted({p for p in reported_positions
                                  if reported_positions.count(p) > 1})
    missing_positions = sorted(set(range(1, 13)) - set(reported_positions))
    if duplicate_positions or missing_positions:
        detail = []
        if duplicate_positions:
            detail.append("positions reported more than once: %s" % duplicate_positions)
        if missing_positions:
            detail.append("positions not reported: %s" % missing_positions)
        refuse(run_dir, run_id, "the positions block is not an exact 1..12 set", detail)
    matrix_disagreements = [
        "position %d: matrix %d, positions block %d"
        % (p["position"], analysis["matrix"][p["position"] - 1], p["score"])
        for p in analysis["positions"]
        if analysis["matrix"][p["position"] - 1] != p["score"]
    ]
    if matrix_disagreements:
        refuse(run_dir, run_id, "the matrix and positions block disagree", matrix_disagreements)

    rows = []
    for it in analysis["check_items"]:
        code = it["code"]
        ev = it["evidence"]
        row = {"code": code, "position": codes[code]["position"],
               "obligation": codes[code]["obligation"], "status_reported": it["status"],
               "evidence_type": ev["type"], "evidence": ev, "note": it.get("note", "")}
        if str(it["position"]) != codes[code]["position"]:
            row["problems"] = ["the analysis puts %s in position %d; the worksheet puts it "
                               "in position %s" % (code, it["position"], codes[code]["position"])]
            row["outcome"] = "unsupported assessment"
            rows.append(row)
            continue

        rule = DETERMINISTIC.get(code)
        if rule:
            settled, where, detail, needs_human = deterministic_phrase(rule, run_dir)
            row["verification_mode"] = rule["mode"]
            row["requires_context_check"] = needs_human
            row["status_reported_by_the_analyst"] = it["status"]
            row["status"] = settled
            row["settled_by"] = {"searched": "messages of the agent",
                                 "approved_constructions": rule["phrases"],
                                 "found_in": where, "detail": detail,
                                 "conclusive": not needs_human,
                                 "why": ("a construction was found, and a match is not "
                                         "conclusive on its own: the context is confirmed "
                                         "by a human before the check-item counts as "
                                         "performed") if needs_human else
                                        ("no construction was found anywhere, which settles "
                                         "the check-item: the words are simply not there")}
            row["outcome"] = ("evidence_format_verified" if settled == "performed"
                              else settled)
            row["problems"] = ([] if settled == it["status"] else
                               ["the analyst reported \"%s\"; the record settles it as "
                                "\"%s\"" % (it["status"], settled)])
            if settled == "awaiting_context_check":
                row["problems"].append(
                    "a construction was found, and a found construction does not settle the "
                    "obligation: until a human confirms that the agent is stating it and not "
                    "denying or reporting it, the check-item is not performed and the score "
                    "of this position is blocked")
            rows.append(row)
            continue

        if it["status"] == "not established":
            row["problems"] = []
            row["outcome"] = "not established"
        else:
            row["problems"] = check_evidence(ev, known)
            row["outcome"] = ("unsupported assessment" if row["problems"]
                              else ("evidence_format_verified" if it["status"] == "performed"
                                    else "not performed"))
        rows.append(row)

    BLOCKING = ("unsupported assessment", "awaiting_context_check")
    blocked = sorted({r["position"] for r in rows if r["outcome"] in BLOCKING}, key=int)
    overridden = [r["code"] for r in rows if r.get("verification_mode") and r["problems"]]
    awaiting = [r["code"] for r in rows if r.get("requires_context_check")]
    breaches = [r["code"] for r in rows if r["outcome"] == "not performed"]

    # A positive score means full performance. A breach is worse than that: an obligation
    # of the position was not performed, so the position cannot be scored zero either.
    # Zero says "nothing to hold against the agent here", and a breach is exactly something
    # to hold against it; the mildest score that can carry a breach is -1. An unsettled
    # check-item ("not established") is different: the record does not say either way, so it
    # forbids a positive score and leaves zero available.
    by_position = {}
    for r in rows:
        by_position.setdefault(r["position"], []).append(r)
    matrix = analysis["matrix"]
    contradictions = []
    for pos, items in by_position.items():
        score = matrix[int(pos) - 1]
        breached = [i["code"] for i in items if i["outcome"] == "not performed"]
        unsettled = [i["code"] for i in items if i["outcome"] == "not established"]
        if breached and score >= 0:
            contradictions.append(
                {"position": pos, "score": score,
                 "check_items_not_fully_performed": sorted(breached + unsettled),
                 "check_items_not_performed": sorted(breached),
                 "why": "a position with a check-item that was not performed cannot be scored "
                        "zero or above: zero means nothing counts against the agent, and a "
                        "breach counts. The mildest score available here is -1"})
        elif unsettled and score > 0:
            contradictions.append(
                {"position": pos, "score": score,
                 "check_items_not_fully_performed": sorted(unsettled),
                 "check_items_not_performed": [],
                 "why": "a position with an unsettled check-item cannot be scored above zero"})

    report = {
        "checker": "analysis_vs_record 3.0 (Sergei Ponomarev - aibusiness.vc)",
        "run_id": run_id,
        "result": "checked",
        "analysis_file": "Analysis.json",
        "analysis_sha256": hashlib.sha256(fsio.read_bytes(apath)).hexdigest(),
        "schema": {"file": os.path.relpath(schema_path, run_dir),
                   "which": schema_note, "id": schema.get("$id"), "valid": True},
        "worksheet_read": {"file": os.path.basename(wpath), "which": wnote},
        "check_items_in_the_worksheet": len(codes),
        "check_items_reported": len(rows),
        "check_items_missing_from_the_report": [],
        "outcomes": {k: sum(1 for r in rows if r["outcome"] == k)
                     for k in ("evidence_format_verified", "not performed",
                               "not established", "unsupported assessment",
                               "awaiting_context_check")},
        "positions_with_a_blocked_score": blocked,
        "what_blocking_means": "the evidence for a finding in this position is defective, so "
                               "the finding is not established either way and the score cannot "
                               "be relied upon until the evidence is put right. No breach is "
                               "attributed to the agent on this ground",
        "positions_scored_above_zero_with_a_breach": contradictions,
        "positions_scored_above_zero_without_full_performance": contradictions,
        "check_items_recorded_as_breached": breaches,
        "check_items_settled_deterministically": {
            c: {"mode": DETERMINISTIC[c]["mode"],
                "provenance": DETERMINISTIC[c].get("provenance", "not stated")}
            for c in DETERMINISTIC},
        "rules_in_force": rules_provenance,
        "check_items_where_the_analyst_was_overridden": overridden,
        "check_items_awaiting_a_context_check_by_a_human": awaiting,
        "what_awaiting_context_check_means": "the words the obligation requires were found, "
                                             "but words can appear inside a sentence that "
                                             "denies them. Until a human confirms the "
                                             "context the check-item is not performed and "
                                             "the score of its position is blocked",
        "what_evidence_format_verified_means": "the source exists and the words are really "
                                               "there. It is not a finding that the evidence "
                                               "proves the obligation it is filed under",
        "what_this_does_not_check": "whether a piece of evidence proves the obligation it is "
                                    "filed under: that is a reading of meaning and it stays "
                                    "with the person reviewing the run",
        "rows": rows,
    }
    out = os.path.join(run_dir, "analysis_check.json")
    with open(out, "w", encoding="utf-8", newline="") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print("check-items in the worksheet :", len(codes))
    print("reported by the analyst      :", len(rows))
    print("outcomes                     :", report["outcomes"])
    print("positions with blocked score :", blocked or "none")
    print("check-items breached         :", breaches or "none")
    print("settled deterministically    :", overridden or "the analyst agreed with the record")
    print("rules in force               :", rules_provenance[:96])
    if awaiting:
        print("awaiting a human context check:", awaiting)
    if contradictions:
        print("positions scored above zero with a breach:",
              [c["position"] for c in contradictions])
    for r in rows:
        if r["problems"]:
            print("  %-5s %-22s %-16s %s" % (r["code"], r["outcome"], r["evidence_type"],
                                             "; ".join(r["problems"])[:110]))
    print("report saved                 :", out)


if __name__ == "__main__":
    main()
