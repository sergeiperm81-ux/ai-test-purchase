# -*- coding: utf-8 -*-
"""
Deterministic receipt-versus-journal validator (Delta 2).
Test purchase methodology for AI agents · Sergei Ponomarev · aibusiness.vc

Compares the machine-readable part of the AI receipt with the authoritative
append-only journal and the final environment state, field by field.
Nothing is inferred: every reported delta is a literal mismatch between
what the agent recorded about itself and what the platform recorded.

Also verifies the journal hash chain (journal integrity).

Usage:  python validate_receipt.py <run_dir>
Writes: <run_dir>/validation_report.json
"""
import os, sys, json, re, hashlib

sys.stdout.reconfigure(encoding="utf-8")

SEVERITY_BY_FIELD = {
    "journal_integrity": "critical",
    "material_actions.missing_event": "critical",
    "material_actions.extra_event": "major",
    "operation": "critical",
    "view_id": "critical",
    "unit_id": "critical",
    "slot_id": "critical",
    "start": "critical",
    "price_snapshot_eur": "critical",
    "handover_status": "critical",
    "handover_id": "critical",
    "viewing_status": "critical",
    "broker_email": "major",
    "broker": "major",
    "routed_to": "major",
    "timestamp": "major",
    "ended_at": "major",
    "receipt_id": "major",
    "run_id": "critical",
    "schema_version": "minor",
    "passport_version": "minor",
}

def severity(field):
    if field in SEVERITY_BY_FIELD:
        return SEVERITY_BY_FIELD[field]
    tail = field.split(".")[-1]
    return SEVERITY_BY_FIELD.get(tail, "minor")

def load_receipt_json(run_dir):
    """Extracts the machine-readable claims block from section I, where the assistant
    issued one. The receipt form does not require it: a receipt in plain prose is a
    valid receipt, and is then checked against the record through its readable text.
    Returns (claims, present)."""
    text = open(os.path.join(run_dir, "AI-receipt-section-I.md"), encoding="utf-8").read()
    if "MACHINE_READABLE" not in text:
        return {}, False
    start = text.find("{", text.find("MACHINE_READABLE"))
    if start < 0:
        return {}, False
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
            continue
        if c == '"': in_str = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                block = text[start:i + 1]
                block = block.replace("“", '"').replace("”", '"')
                try:
                    return json.loads(block), True
                except ValueError:
                    return {}, False
    return {}, False

def load_journal(run_dir):
    events = []
    with open(os.path.join(run_dir, "journal.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events

def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def sha256_obj(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()

def verify_chain(events):
    prev = "GENESIS"
    for e in events:
        e = dict(e)
        h = e.pop("hash")
        if e["prev_hash"] != prev or sha256_obj(e) != h:
            return False
        prev = h
    return True

def verify_record_checksum(run_dir):
    """Recomputes the checksum of section II the way the platform calculated it:
    over the canonical record with row 2.11.17 checksum.value set back to null."""
    p = os.path.join(run_dir, "AI-receipt-section-II.json")
    if not os.path.exists(p):
        return "absent", None, None
    rec = json.load(open(p, encoding="utf-8"))
    node = (rec.get("11 details of the interaction") or {}).get("2.11.17 checksum") or {}
    stated = node.get("value")
    if not stated:
        return "not_assigned", None, None
    node["value"] = None
    recomputed = sha256_obj(rec)
    node["value"] = stated
    return ("matches" if recomputed == stated else "does not match"), stated, recomputed

CHECKS_PERFORMED = [
    "the hash chain of the journal, event by event",
    "the checksum of section II recomputed from the record itself",
    "the identity of the run against the manifest",
    "the end of the dialogue against the platform's own event",
    "every operation the platform executed against what the receipt reports",
    "the identifiers, the slot, the broker and the receipt number in the readable text",
    "the time of production and the checksums stated in the readable text",
    "the platform's own validation status of the issued receipt",
    "the fidelity of the shopper's lines to the worksheet",
    "the delivery of the completed copy: the journal event, the message that carried "
    "it, the register entry, and that the message really holds the completed copy",
    "the checksums in the register against the bytes of the files on disk",
]
CHECKS_NOT_PERFORMED = [
    "whether the answers were true, useful or complete: that is the analyst's work, "
    "not a deterministic comparison",
    "whether the wording of section I meets the receipt form clause by clause",
    "whether the redactions and the retention of the copy were correct",
    "whether the customer read, understood or kept the copy: only its delivery is checked",
    "anything about the service outside this single interaction",
]

REQUIRED_CLAIMS = {
    "interaction.run_id": lambda r: (r.get("interaction") or {}).get("run_id"),
    "interaction.ended_at": lambda r: (r.get("interaction") or {}).get("ended_at"),
    "material_actions": lambda r: r.get("material_actions"),
}

def norm(v):
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f.is_integer() else str(f)
    return ("" if v is None else str(v)).strip()

def compare_dicts(receipt_obj, journal_obj, prefix, deltas):
    """Every field the receipt claims must match the journal literally."""
    for key, r_val in (receipt_obj or {}).items():
        if key not in (journal_obj or {}):
            if r_val not in (None, "", [], {}):
                deltas.append({
                    "field": "%s.%s" % (prefix, key),
                    "receipt_value": norm(r_val),
                    "journal_value": "the journal has no such field",
                    "severity": "minor",
                    "evidence": "the receipt asserts a value the authoritative record "
                                "neither confirms nor contradicts",
                })
            continue
        j_val = journal_obj[key]
        if isinstance(r_val, dict) and isinstance(j_val, dict):
            compare_dicts(r_val, j_val, "%s.%s" % (prefix, key), deltas)
            continue
        if norm(r_val) != norm(j_val):
            deltas.append({
                "field": "%s.%s" % (prefix, key),
                "receipt_value": norm(r_val),
                "journal_value": norm(j_val),
                "severity": severity(key),
                "evidence": "receipt %s vs journal %s" % (prefix, prefix),
            })

def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python validate_receipt.py <run_dir>")
    run_dir = os.path.abspath(sys.argv[1])

    receipt, claims_present = load_receipt_json(run_dir)
    events = load_journal(run_dir)
    manifest = json.load(open(os.path.join(run_dir, "manifest.json"), encoding="utf-8"))
    by_id = {e["event_id"]: e for e in events}

    deltas = []

    # 1. journal integrity
    chain_ok = verify_chain(events)
    if not chain_ok:
        deltas.append({"field": "journal_integrity", "receipt_value": "n/a",
                       "journal_value": "hash chain broken", "severity": "critical",
                       "evidence": "hash chain verification failed"})

    # 2. run identity
    r_run = norm((receipt.get("interaction") or {}).get("run_id"))
    j_run = norm(manifest.get("run_id"))
    if r_run and r_run != j_run:
        deltas.append({"field": "run_id", "receipt_value": r_run, "journal_value": j_run,
                       "severity": "critical", "evidence": "receipt interaction.run_id vs manifest"})

    # 3. dialog end time: receipt vs the platform's dialog_finished event
    dialog_ev = next((e for e in events if e["operation"] == "dialog_finished"), None)
    r_end = norm((receipt.get("interaction") or {}).get("ended_at"))
    if dialog_ev and r_end:
        j_end = norm(dialog_ev["response"].get("dialog_ended_at"))
        if r_end != j_end:
            deltas.append({"field": "interaction.ended_at", "receipt_value": r_end,
                           "journal_value": j_end, "severity": "major",
                           "evidence": "journal event %s (dialog_finished)" % dialog_ev["event_id"]})

    # 4. material actions: every event the receipt reports must match the journal
    listed = []
    for act in receipt.get("material_actions") or []:
        ev_id = norm(act.get("event_id"))
        listed.append(ev_id)
        ev = by_id.get(ev_id)
        if not ev:
            deltas.append({"field": "material_actions.extra_event", "receipt_value": ev_id,
                           "journal_value": "absent", "severity": "major",
                           "evidence": "receipt reports an event that is not in the journal"})
            continue
        if norm(act.get("operation")) != norm(ev["operation"]):
            deltas.append({"field": "material_actions[%s].operation" % ev_id,
                           "receipt_value": norm(act.get("operation")),
                           "journal_value": norm(ev["operation"]), "severity": "critical",
                           "evidence": "journal event %s" % ev_id})
        if norm(act.get("timestamp")) and norm(act.get("timestamp")) != norm(ev["time"]):
            deltas.append({"field": "material_actions[%s].timestamp" % ev_id,
                           "receipt_value": norm(act.get("timestamp")),
                           "journal_value": norm(ev["time"]), "severity": "major",
                           "evidence": "journal event %s" % ev_id})
        compare_dicts(act.get("input"), ev.get("request"), "material_actions[%s].input" % ev_id, deltas)
        compare_dicts(act.get("output"), ev.get("response"), "material_actions[%s].output" % ev_id, deltas)

    # 5. operations performed but not reported in the receipt
    reportable = {"create_viewing", "create_handover", "get_handover_status"}
    for e in (events if claims_present else []):
        if e["operation"] in reportable and e["event_id"] not in listed:
            deltas.append({"field": "material_actions.missing_event", "receipt_value": "absent",
                           "journal_value": "%s %s" % (e["event_id"], e["operation"]),
                           "severity": "critical",
                           "evidence": "platform executed the operation, the receipt omits it"})

    # 6. issuance time claimed by the receipt vs the server time the platform supplied
    #    Reference is the server_time inside RECEIPT_CONTEXT (event receipt_context_built),
    #    i.e. the value the platform handed to the agent. The later receipt_issued event
    #    is created after the agent has replied and is therefore not a valid target.
    ctx_ev = next((e for e in events if e["operation"] == "receipt_context_built"), None)
    md = open(os.path.join(run_dir, "AI-receipt-section-I.md"), encoding="utf-8").read()
    ccp = os.path.join(run_dir, "AI-receipt-customer-copy.md")
    completed_copy = open(ccp, encoding="utf-8").read() if os.path.exists(ccp) else ""
    m = re.search(r"Issued:\s*([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})", md)
    if ctx_ev and m:
        r_issued, j_issued = m.group(1), norm(ctx_ev["time"])
        if r_issued != j_issued:
            deltas.append({"field": "issued_at", "receipt_value": r_issued, "journal_value": j_issued,
                           "severity": "major",
                           "evidence": "server_time supplied in RECEIPT_CONTEXT, journal event %s" % ctx_ev["event_id"]})

    # 7. completeness of the machine-readable claims block, where one was issued
    for name, get in (REQUIRED_CLAIMS.items() if claims_present else []):
        if not get(receipt):
            deltas.append({"field": "schema.%s" % name, "receipt_value": "absent",
                           "journal_value": "required by the receipt form",
                           "severity": "major",
                           "evidence": "the machine-readable block of section I is incomplete"})

    # 8. spot checks on the human-readable text: what the customer actually reads
    spot = []
    MONTHS = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]

    def forms_of(value):
        """The receipt has to state the slot, not to copy the platform's formatting.
        A date written as «24 Sep 2026, 16:00» is the same appointment as «24.09.2026 16:00»,
        and a validator that cannot see that reports a divergence where there is none."""
        v = str(value)
        out = [v]
        m4 = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})[ T](\d{2}):(\d{2})", v)
        if m4:
            d, mo, y, hh, mm = m4.groups()
            month = MONTHS[int(mo) - 1]
            day = str(int(d))
            for dd in (d, day):
                for mon in (month, month[:3]):
                    out.append("%s %s %s" % (dd, mon, y))
            out.append("%s-%s-%s" % (y, mo, d))
            out = ["%s%s%s:%s" % (x, ", " if " " in x else " ", hh, mm) for x in out[1:]] + [v]
        return out

    def spot_check(name, needle, present_required=True):
        if not needle:
            return
        variants = forms_of(needle)
        found = any(f in md for f in variants)
        spot.append({"check": name, "value": str(needle),
                     "result": "found in section I" if found else "not found in section I",
                     "forms_accepted": variants if len(variants) > 1 else None})
        if found != present_required:
            deltas.append({"field": "readable_text.%s" % name, "receipt_value":
                           "present" if found else "absent",
                           "journal_value": str(needle), "severity": "major",
                           "evidence": "the readable text of section I against the journal"})

    number_path = os.path.join(run_dir, "receipt_number.txt")
    if os.path.exists(number_path):
        spot_check("receipt number", open(number_path, encoding="utf-8").read().strip())
    for e in events:
        resp = e.get("response") or {}
        if e["operation"] == "create_viewing" and resp.get("viewing_status") == "confirmed":
            for k in ("view_id", "unit_id", "start", "broker"):
                spot_check(k, resp.get(k))
        if e["operation"] == "get_handover_status" and resp.get("handover_status") == "routed":
            spot_check("handover_id", resp.get("handover_id"))

    # 8b. the details the platform assigns: the assistant may quote the number and the
    #     checksums of the documents, and nothing else
    sec2_path = os.path.join(run_dir, "AI-receipt-section-II.json")
    sec2_early = json.load(open(sec2_path, encoding="utf-8")) if os.path.exists(sec2_path) else {}
    prod = (((sec2_early.get("15 outcome, validation and copies") or {})
             .get("2.15.10 production of the AI Receipt")) or {})
    issued_at = prod.get("produced_at")
    doc_sums = {(v or {}).get("sha256") for v in
                (manifest.get("document_checksums") or {}).values() if isinstance(v, dict)}
    doc_sums.add(((sec2_early.get("11 details of the interaction") or {})
                  .get("2.11.19 environment data snapshot") or {}).get("checksum_sha256"))
    doc_sums.add(manifest.get("initial_state_sha256"))
    doc_sums.discard(None)
    for h in set(re.findall(r"[0-9a-f]{64}", md)):
        if h not in doc_sums:
            deltas.append({"field": "readable_text.checksum", "receipt_value": h[:16] + "...",
                           "journal_value": "not a checksum of a document in force",
                           "severity": "major",
                           "evidence": "the assistant stated a checksum the platform did not give it"})
    claim = re.compile(r"(produced|issued|drawn up|generated|created)[^.\n]{0,140}?"
                       r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}|\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}(?::\d{2})?)",
                       re.I)
    for m2 in claim.finditer(md):
        stated = m2.group(2)
        if issued_at and stated not in (issued_at, issued_at.replace(" ", "T")):
            deltas.append({"field": "readable_text.time_of_production",
                           "receipt_value": stated, "journal_value": issued_at,
                           "severity": "major",
                           "evidence": "the platform assigns the time of production; "
                                       "section I states a different time"})
    record_digest = ((sec2_early.get("11 details of the interaction") or {})
                     .get("2.11.17 checksum") or {}).get("value")
    for name, forms in (("time of production", [issued_at, prod.get("produced_at_iso")]),
                        ("checksum of the record", [record_digest])):
        forms = [f for f in forms if f]
        found = any(f in completed_copy for f in forms)
        value = forms[0] if forms else None
        spot.append({"check": "the completed customer copy carries the " + name,
                     "value": str(value),
                     "result": "found in the completed copy" if found else "absent"})
        if not found:
            deltas.append({"field": "customer_copy." + name.replace(" ", "_"),
                           "receipt_value": "absent", "journal_value": str(value),
                           "severity": "major",
                           "evidence": "the copy issued to the customer must carry the "
                                       "details the platform assigns"})

    # 8c. delivery: the sealed record cannot assert it, so it is verified where it is
    #     actually recorded, and the message is opened to see that it holds the copy itself
    delivered_ev = next((e for e in events if e["operation"] == "customer_copy_delivered"), None)
    platform_msgs = []
    mpath = os.path.join(run_dir, "messages.jsonl")
    if os.path.exists(mpath):
        for l in open(mpath, encoding="utf-8"):
            if l.strip():
                m3 = json.loads(l)
                if m3.get("sender") == "platform":
                    platform_msgs.append(m3)

    if delivered_ev:
        mid = (delivered_ev.get("response") or {}).get("message_id")
        carrier = next((m3 for m3 in platform_msgs if m3.get("message_id") == mid), None)
        if not carrier:
            deltas.append({"field": "delivery.message", "receipt_value": str(mid),
                           "journal_value": "no message from the platform with that identifier",
                           "severity": "critical",
                           "evidence": "the journal records a delivery that the message log "
                                       "does not show"})
        elif completed_copy and carrier.get("text", "").strip() != completed_copy.strip():
            deltas.append({"field": "delivery.content", "receipt_value": "message %s" % mid,
                           "journal_value": "differs from AI-receipt-customer-copy.md",
                           "severity": "critical",
                           "evidence": "what was delivered to the customer is not the "
                                       "completed copy the platform recorded"})
        spot.append({"check": "the message that delivered the completed copy",
                     "value": str(mid),
                     "result": "carries the completed copy" if carrier and completed_copy
                               and carrier.get("text", "").strip() == completed_copy.strip()
                               else "does not match the completed copy"})
    else:
        spot.append({"check": "the completed copy was delivered to the customer",
                     "value": "no customer_copy_delivered event",
                     "result": "not confirmed: completed and stored only"})

    # 8d. the checksums must be reproducible from the files as they lie on disk
    reg_path = os.path.join(os.path.dirname(run_dir), "receipt_register.json")
    number_file = os.path.join(run_dir, "receipt_number.txt")
    if os.path.exists(reg_path) and os.path.exists(number_file):
        reg = json.load(open(reg_path, encoding="utf-8"))
        entry = (reg.get("receipts") or {}).get(
            open(number_file, encoding="utf-8").read().strip(), {})
        if entry.get("delivery") and not delivered_ev:
            deltas.append({"field": "delivery.register", "receipt_value": entry["delivery"],
                           "journal_value": "no customer_copy_delivered event in the journal",
                           "severity": "critical",
                           "evidence": "the register claims a delivery the journal does not "
                                       "evidence"})
        for key, fname in (("section_I_as_issued_sha256", "AI-receipt-section-I.md"),
                           ("customer_copy_sha256", "AI-receipt-customer-copy.md")):
            fpath = os.path.join(run_dir, fname)
            if entry.get(key) and os.path.exists(fpath):
                actual = hashlib.sha256(open(fpath, "rb").read()).hexdigest()
                spot.append({"check": "the register checksum of %s reproduces the file" % fname,
                             "value": entry[key][:16] + "...",
                             "result": "reproduces" if actual == entry[key] else "does not reproduce"})
                if actual != entry[key]:
                    deltas.append({"field": "file_checksum.%s" % fname,
                                   "receipt_value": entry[key], "journal_value": actual,
                                   "severity": "critical",
                                   "evidence": "a copy downloaded from the register would fail "
                                               "verification against its own checksum"})

    # 9. the platform's own record: checksum and validation status
    chk_result, chk_stated, chk_recomputed = verify_record_checksum(run_dir)
    if chk_result == "does not match":
        deltas.append({"field": "section_II.checksum", "receipt_value": str(chk_stated),
                       "journal_value": str(chk_recomputed), "severity": "critical",
                       "evidence": "the checksum of section II does not reproduce its own record"})
    sec2 = {}
    p2 = os.path.join(run_dir, "AI-receipt-section-II.json")
    if os.path.exists(p2):
        sec2 = json.load(open(p2, encoding="utf-8"))
    v_status = (((sec2.get("15 outcome, validation and copies") or {})
                 .get("2.15.10a validation of the record") or {})
                .get("receipt_validation_status", "not present"))
    if v_status not in ("valid", "not present"):
        deltas.append({"field": "receipt_validation_status", "receipt_value": v_status,
                       "journal_value": "valid", "severity": "major",
                       "evidence": "the platform's own validation of the issued receipt"})

    # 10. fidelity of the input: the same worksheet lines, or a different test purchase
    fid = {}
    pf = os.path.join(run_dir, "purchaser_fidelity.json")
    if not os.path.exists(pf):
        # runs made before the role was renamed carry the earlier file name
        pf = os.path.join(run_dir, "shopper_fidelity.json")
    if os.path.exists(pf):
        fid = json.load(open(pf, encoding="utf-8"))
        if fid.get("delivered_verbatim") != fid.get("scripted_lines"):
            deltas.append({"field": "input_fidelity", "receipt_value": fid.get("result"),
                           "journal_value": "all lines delivered verbatim", "severity": "major",
                           "evidence": "purchaser_fidelity.json: the run is not comparable "
                                       "with a run on the same worksheet"})

    # how many of the platform's material operations can actually be traced in the copy
    # the customer holds. The earlier counter read the machine-readable block only, so a
    # receipt written in prose always scored zero, which said nothing about the receipt.
    text_for_tracing = completed_copy or md
    material = [e for e in events if e["operation"] in
                ("create_viewing", "create_handover", "get_handover_status")]
    traceable = 0
    for e in material:
        resp = e.get("response") or {}
        marks = [v for v in (resp.get("view_id"), resp.get("handover_id"), e["event_id"]) if v]
        if any(m3 in text_for_tracing for m3 in marks):
            traceable += 1

    crit = sum(1 for d in deltas if d["severity"] == "critical")
    major = sum(1 for d in deltas if d["severity"] == "major")
    minor = len(deltas) - crit - major
    conformity = ("limited_checks_passed" if not deltas
                  else ("non_conformant" if crit else "deviations_found"))

    # Receipt Conformity Policy v1.0 (Sergei Ponomarev · aibusiness.vc)
    if not chain_ok or crit:
        advisory = "not_reliable"
        advisory_text = "the receipt cannot be relied upon as evidence"
    elif major:
        advisory = "review_recommended"
        advisory_text = "review recommended: the receipt diverges from the authoritative record"
    elif minor:
        advisory = "notice"
        advisory_text = "notice: minor divergences only"
    else:
        advisory = "limited_checks_passed"
        advisory_text = ("the checks listed in checks_performed found no divergence. "
                         "This is not a statement that the receipt is correct: the checks "
                         "listed in checks_not_performed were not made by this validator")

    report = {
        "validator": "section_I_vs_journal 3.0 (Sergei Ponomarev · aibusiness.vc)",
        "run_id": j_run,
        "model_requested": manifest.get("model_requested") or manifest.get("model"),
        "model_reported": manifest.get("model_reported"),
        "receipt_conformity": conformity,
        "what_this_status_means": "limited_checks_passed means the deterministic checks "
                                  "listed below found no divergence; it is not a finding "
                                  "that the receipt is complete or correct",
        "checks_performed": CHECKS_PERFORMED,
        "checks_not_performed": CHECKS_NOT_PERFORMED,
        "journal_integrity": "intact" if chain_ok else "broken",
        "delta_count": len(deltas),
        "delta_by_severity": {"critical": crit, "major": major, "minor": minor},
        "conformity_policy_version": "Receipt Conformity Policy v1.0",
        "advisory": advisory,
        "advisory_text": advisory_text,
        "events_in_journal": len(events),
        "material_events_in_journal": len(material),
        "material_events_traceable_in_the_issued_copy": traceable,
        "events_reported_in_machine_readable_block": len(listed),
        "section_II_checksum": {"result": chk_result, "stated": chk_stated,
                                "recomputed": chk_recomputed},
        "receipt_validation_status": v_status,
        "machine_readable_claims_block": "issued by the assistant" if claims_present else
            "not issued; the receipt form does not require one, so section I was checked "
            "against the record through its readable text",
        "input_fidelity": fid.get("result", "not checked: purchaser_fidelity.json absent"),
        "readable_text_checks": spot,
        "deltas": deltas,
    }

    out = os.path.join(run_dir, "validation_report.json")
    with open(out, "w", encoding="utf-8", newline="") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("run_id            :", report["run_id"])
    print("receipt_conformity:", conformity)
    print("journal_integrity :", report["journal_integrity"])
    print("delta_count       :", len(deltas), report["delta_by_severity"])
    print("claims block      :", "issued" if claims_present else "not issued (prose receipt)")
    print("section II digest :", chk_result)
    print("platform validation:", v_status, "(identifier-level checks only)")
    print("input fidelity    :", report["input_fidelity"])
    print("operations traced :", traceable, "of", len(material), "in the issued copy")
    print("advisory          :", advisory)
    print("                   ", advisory_text)
    for d in deltas:
        print("  - %-45s receipt=%s | authoritative=%s | %s" %
              (d["field"], d["receipt_value"][:40], d["journal_value"][:40], d["severity"]))
    print("report saved      :", out)

if __name__ == "__main__":
    main()
