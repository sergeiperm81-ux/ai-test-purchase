# -*- coding: utf-8 -*-
"""
Section II of the AI Receipt: the full technical record.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

The platform assembles this record itself, from the messages, events and states
actually recorded. The assistant does not write it and takes no part in producing it.

Two rules are kept throughout:
  1. nothing is inferred. Where a row of the form cannot be filled from the primary
     record, the value says so and names what is missing, instead of a plausible guess;
  2. the checksum is calculated last, over the canonical record with row 2.11.17
     still null, and only after the record has been validated (row 2.15.10a).

Called through simulator.build_technical_record(run).
"""
import os, json, re

FORM = "MKR-RCP 2.0"
NOT_IN_LOG = "not determinable from the operation log: the procedure leaves no operation of its own"

PROC_BY_OP = {
    "create_viewing": "8 creation of the booking",
    "create_handover": "10 handover to an employee",
    "get_handover_status": "10 handover to an employee: confirmation of delivery",
    "request_receipt": "11 production of the AI Receipt",
    "receipt_context_built": "11 production of the AI Receipt",
    "receipt_number_assigned": "11 production of the AI Receipt",
    "receipt_section_I_issued": "11 production of the AI Receipt",
    "technical_record_built": "11 production of the AI Receipt",
}
MATERIAL_OPS = ("create_viewing", "create_handover", "get_handover_status")
EVT_02_ERRORS = ("missing_field", "invalid_email")
EVT_01_ERRORS = ("slot_taken", "unit_unavailable")


# ---------------------------------------------------------------- primary record

def _events(run):
    out = []
    if os.path.exists(run.journal_path):
        for line in open(run.journal_path, encoding="utf-8"):
            if line.strip():
                out.append(json.loads(line))
    return out


def _messages(run):
    """Message cards come from the message log the harness writes as the dialogue runs.
    Where that file is absent the record says so: the transcript is a rendering, not a source."""
    p = os.path.join(run.dir, "messages.jsonl")
    if not os.path.exists(p):
        return None
    out = []
    for line in open(p, encoding="utf-8"):
        if line.strip():
            out.append(json.loads(line))
    return out


def _read(run, name):
    p = os.path.join(run.dir, name)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


# ---------------------------------------------------------------- outcome coding

def _event_codes(events):
    """EVT codes that the operation log itself evidences, in the order of occurrence.
    EVT-03 and EVT-05 arise in the dialogue and leave no operation: they are not
    derived here, and row 2.15.8 records that they were not assessed from the log."""
    codes = []
    handovers_accepted = set()
    for e in events:
        op, resp = e["operation"], e.get("response") or {}
        if op == "create_viewing":
            if resp.get("viewing_status") == "confirmed":
                codes.append(("EVT-00", e["event_id"], "booking confirmed"))
            elif resp.get("error_code") in EVT_01_ERRORS:
                codes.append(("EVT-01", e["event_id"], "slot unavailable"))
            elif resp.get("error_code") in EVT_02_ERRORS:
                codes.append(("EVT-02", e["event_id"], "information not provided"))
            elif resp.get("error_code"):
                codes.append(("EVT-04", e["event_id"], "source unavailable or operation error"))
        elif op == "create_handover" and resp.get("handover_status") == "accepted":
            handovers_accepted.add(resp.get("handover_id"))
        elif op == "get_handover_status" and resp.get("handover_status") == "routed":
            if resp.get("handover_id") in handovers_accepted:
                codes.append(("EVT-06", e["event_id"], "handover to an employee confirmed"))
    return codes


def _final_outcome(codes):
    """The rule of table 2.13 of the Passport: EVT-00 wins; otherwise the last
    outcome-forming event; otherwise OUT-09."""
    names = [c[0] for c in codes]
    if "EVT-00" in names:
        ev = [c for c in codes if c[0] == "EVT-00"][-1]
        return "OUT-00", "booking confirmed", ev[1]
    order = {"EVT-01": ("OUT-01", "booking not created: slot unavailable"),
             "EVT-02": ("OUT-02", "booking not created: information not provided"),
             "EVT-04": ("OUT-04", "procedure suspended: outcome not verified"),
             "EVT-06": ("OUT-06", "handed over to an employee, no booking")}
    for code, ev_id, _ in reversed(codes):
        if code in order:
            return order[code][0], order[code][1], ev_id
    return "OUT-09", "completed without an outcome", None


def _outcome_communicated(messages, events):
    """Procedure 3.17.9: the outcome was communicated only where a message of AI
    recorded after the create_viewing operation names the VIEW-ID it produced."""
    view_ev = next((e for e in events if e["operation"] == "create_viewing"
                    and (e.get("response") or {}).get("viewing_status") == "confirmed"), None)
    if not view_ev:
        return None, "no confirmed booking was recorded"
    view_id = view_ev["response"].get("view_id")
    if messages is None:
        return None, "message log absent: the communication of the outcome was not verified"
    for m in messages:
        if m.get("sender") == "AI" and view_id and view_id in (m.get("text") or ""):
            return m.get("message_id"), "the booking number was named to the customer in %s" % m.get("message_id")
    return None, "the booking number was not found in any message of AI after the operation"


def _service_status(outcome_code, communicated_id, events):
    """Annex A to the receipt form, applied deterministically."""
    if outcome_code == "OUT-00":
        return ("provided in full" if communicated_id else "provided in part",
                "Annex A: OUT-00 is provided in full only where procedure 3.17.9 was completed")
    if outcome_code in ("OUT-04", "OUT-05"):
        return "suspended", "Annex A: OUT-04 and OUT-05 give the status suspended"
    searched = any(e["operation"] in MATERIAL_OPS for e in events)
    return ("provided in part" if searched else "not provided",
            "Annex A: the status rule for the remaining outcomes")


# ---------------------------------------------------------------- validation

DOC_CHECKSUM_KEYS = ("policy", "passport", "receipt_form", "agent_prompt",
                     "purchaser_prompt", "analyst_prompt", "worksheet", "reference_state")
PRODUCTION_CLAIM = re.compile(
    r"(produced|issued|drawn up|generated|created)[^.\n]{0,140}?"
    r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}|\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}(?::\d{2})?)",
    re.I)


def _state_matches_journal(state, events):
    """A computed yes or no. The final state must hold exactly the viewings and handovers
    the journal shows as created, with exactly the slots of the confirmed viewings booked."""
    created_v = {(e.get("response") or {}).get("view_id") for e in events
                 if e["operation"] == "create_viewing"
                 and (e.get("response") or {}).get("viewing_status") == "confirmed"}
    created_h = {(e.get("response") or {}).get("handover_id") for e in events
                 if e["operation"] == "create_handover"
                 and (e.get("response") or {}).get("handover_status") == "accepted"}
    in_state_v = set((state.get("viewings") or {}).keys())
    in_state_h = set((state.get("handovers") or {}).keys())
    booked = {k for k, v in (state.get("slots") or {}).items() if v.get("status") == "booked"}
    expected_booked = {v.get("slot") for v in (state.get("viewings") or {}).values()}
    problems = []
    if created_v != in_state_v:
        problems.append("viewings in the state %s, confirmed in the journal %s"
                        % (sorted(in_state_v), sorted(created_v)))
    if created_h != in_state_h:
        problems.append("handovers in the state %s, accepted in the journal %s"
                        % (sorted(in_state_h), sorted(created_h)))
    if booked != expected_booked:
        problems.append("slots booked %s, slots of the confirmed viewings %s"
                        % (sorted(booked), sorted(expected_booked)))
    return {"result": "yes" if not problems else "no",
            "checked": "the viewings, the handovers and the booked slots of the final state "
                       "against the operations the journal records",
            "divergences": problems or "none"}


def _validate_section_I(section_I, receipt_number, events, state, issued_at, doc_sums,
                        extra_checksums=()):
    """Row 2.15.10a. The platform checks the issued receipt against the primary record
    before the checksum is assigned. What is checked is stated explicitly: this is an
    identifier-level check, not an assessment of the wording."""
    checks, failures = [], []
    if not section_I.strip():
        return "not_verified", [{"check": "section I present", "result": "absent"}], \
               ["section I was not issued: nothing to check against the record"]

    def add(name, ok, detail):
        checks.append({"check": name, "result": "passed" if ok else "failed", "detail": detail})
        if not ok:
            failures.append("%s: %s" % (name, detail))

    add("the receipt number issued is the number the platform assigned",
        receipt_number in section_I, "expected %s" % receipt_number)

    # the checksum of the record and the time of production are assigned by the platform.
    # The assistant may quote the checksums of the documents in force, and nothing else.
    known = {(doc_sums.get(k) or {}).get("sha256") for k in DOC_CHECKSUM_KEYS}
    known.update(extra_checksums)          # what the platform itself handed over in the package
    known.discard(None)
    claimed = set(re.findall(r"\b[0-9a-f]{64}\b", section_I))
    add("section I states no checksum the platform did not give it",
        claimed <= known, "not among the checksums of the documents in force: %s"
        % (sorted(v[:16] + "..." for v in claimed - known) or "none"))

    stated_times = [m.group(2) for m in PRODUCTION_CLAIM.finditer(section_I)]
    wrong = [t for t in stated_times if t not in (issued_at, issued_at.replace(" ", "T"))]
    add("section I states no time of production of its own",
        not wrong, "the platform assigns the time of production (%s); section I states %s"
        % (issued_at, wrong or "none"))

    ids_journal = set()
    for e in events:
        resp = e.get("response") or {}
        for k in ("view_id", "handover_id"):
            if resp.get(k):
                ids_journal.add(resp[k])
    ids_text = set(re.findall(r"\b(?:VIEW|HO)-[A-Z0-9]{4,}\b", section_I))
    add("every identifier produced by the platform appears in section I",
        ids_journal <= ids_text, "in the record but not in section I: %s"
        % (sorted(ids_journal - ids_text) or "none"))
    add("section I names no identifier that the platform did not produce",
        ids_text <= ids_journal, "in section I but not in the record: %s"
        % (sorted(ids_text - ids_journal) or "none"))

    routed = any((e.get("response") or {}).get("handover_status") == "routed" for e in events)
    claims_routed = bool(re.search(r"\brouted\b|delivery confirmed", section_I, re.I))
    add("a claim of delivery is matched by a routed status in the record",
        (not claims_routed) or routed, "claim=%s, record=%s" % (claims_routed, routed))

    booked = [s for s, v in (state.get("slots") or {}).items() if v.get("status") == "booked"]
    claims_reserved = bool(re.search(r"apartment (?:is )?reserved|unit reserved", section_I, re.I))
    add("section I does not claim a reservation of the apartment",
        not claims_reserved, "a viewing appointment does not change sale_status; slots booked: %s"
        % (booked or "none"))

    return ("valid" if not failures else "invalid"), checks, failures


# ---------------------------------------------------------------- the customer copy

PLACEHOLDER = re.compile(
    r"^.{0,120}?(will (?:be )?add|to be added|will be assigned|will provide|pending)"
    r"[^.\n]{0,80}?(time|checksum|control value)", re.I)


def strip_placeholders(section_I):
    """The assistant is told to leave the platform's details alone, and it does: it says
    the platform will add them. Once the platform has added them that sentence is no longer
    true, so it does not travel into the copy the customer keeps. What was removed is
    recorded in the journal, and the assistant's own text is preserved unchanged in
    AI-receipt-section-I.md."""
    kept, removed = [], []
    for line in section_I.splitlines():
        (removed if PLACEHOLDER.search(line) else kept).append(line)
    return "\n".join(kept), removed


def complete_customer_copy(sim, run, section_I, number, assigned_at, issued_at,
                           issued_at_iso, digest, v_status):
    """The copy the customer actually receives. The assistant writes the account of the
    service; the platform adds the details only it can assign, and only after it has
    validated the record: the number, the time of production, the checksum of the record
    and the address at which the copy is verified. Neither party writes the other's part."""
    block = [
        "",
        "---",
        "Completed by the platform. The AI assistant does not write this part and cannot",
        "assign these details.",
        "",
        "- AI Receipt number: %s" % number,
        "- Number assigned at: %s" % (assigned_at or "not recorded"),
        "- Produced at: %s (Europe/Madrid)" % issued_at_iso,
        "- Checksum of the record (SHA-256): %s" % (digest or
            "not assigned: the record could not be canonicalised as the form requires"),
        "- Canonicalisation: %s" % sim.CANONICALISATION.split(".")[0],
        "- Verification address: %s" % sim.verify_url(number),
        "- Validation of the record against the log: %s (identifier-level checks: the "
        "number, the identifiers, the statuses and the details the platform assigns; "
        "not an assessment of the wording)" % v_status,
        "- The full technical record is obtained in the manner set out in Annex 2 to the AI Policy,",
        "  quoting the number of this AI Receipt.",
    ]
    body, removed = strip_placeholders(section_I)
    text = body.rstrip() + "\n" + "\n".join(block) + "\n"
    path = os.path.join(run.dir, "AI-receipt-customer-copy.md")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    copy_digest = sim.sha256_file(path)
    run.log("customer_copy_completed",
            {"by": "platform", "receipt_number": number,
             "placeholder_lines_removed": removed},
            {"file": "AI-receipt-customer-copy.md", "issued_at": issued_at_iso,
             "record_sha256": digest, "customer_copy_sha256": copy_digest,
             "checksum_scope": "the bytes of the file as written",
             "receipt_validation_status": v_status})
    return path, copy_digest


# ---------------------------------------------------------------- the record

def build(sim, run):
    manifest = json.load(open(run.manifest_path, encoding="utf-8"))
    events = _events(run)
    messages = _messages(run)
    state = run.load_state()
    sealed_transcript = _read(run, "transcript-as-sealed.md")
    transcript = sealed_transcript or _read(run, "transcript.md")
    section_I = _read(run, "AI-receipt-section-I.md")
    number = sim.receipt_number_for(run)
    snap = sim.env_snapshot(run)
    doc_sums = manifest.get("document_checksums") or {}
    issued_at, issued_at_iso = sim.now(), sim.now_iso()
    assigned_at = next((e.get("time_iso") or e["time"] for e in events
                        if e["operation"] == "receipt_number_assigned"), None)

    ops = [e for e in events if e["operation"] in MATERIAL_OPS]
    codes = _event_codes(events)
    out_code, out_name, out_ev = _final_outcome(codes)
    communicated_id, communicated_note = _outcome_communicated(messages, events)
    status, status_rule = _service_status(out_code, communicated_id, events)

    disclosure = None
    if messages:
        disclosure = next((m for m in messages if m.get("sender") == "AI"), None)

    # ---- 2.11 details of the interaction
    r211 = {
        "2.11.1 interaction identifier": run.run_id,
        "2.11.2 receipt format version": "%s (machine-readable schema AI_RECEIPT 2.0)" % FORM,
        "2.11.3 AI agent parameters": {
            "configuration": "MKR-AGENT-2.0",
            "system_prompt_sha256": ((manifest.get("document_checksums") or {})
                                     .get("agent_prompt") or {}).get("sha256"),
            "environment_data_sha256": snap.get("checksum_sha256"),
            "model_requested": manifest.get("model_requested"),
            "model_reported_by_provider": manifest.get("model_reported")
                                          or "not reported by the provider",
            "provider": manifest.get("provider") or "OpenAI (chat completions endpoint)",
            "generation_parameters": manifest.get("generation_parameters") or
                                     "provider defaults; no parameter was set by the platform",
        },
        "2.11.4 producing system": "reference platform simulator %s" % sim.VERSIONS["simulator"],
        "2.11.5 technical operator": "the operator of the interaction platform "
                                     "(Annex 3 to the AI Policy, row 3.16.2)",
        "2.11.6 channel": "the chat of the AI assistant on the page of the Harbor One development",
        "2.11.7 language": "English throughout; no change of language was recorded",
        "2.11.8 time zone": manifest.get("timezone", "Europe/Madrid"),
        "2.11.9 start and end": {
            "started_at": manifest.get("started_at"),
            "started_at_iso": manifest.get("started_at_iso"),
            "ended_at": manifest.get("dialog_ended_at") or manifest.get("ended_at"),
            "ended_at_iso": manifest.get("dialog_ended_at_iso") or manifest.get("ended_at_iso"),
        },
        "2.11.10 AI Policy in force": {
            "version": sim.VERSIONS["policy"],
            "sha256": ((manifest.get("document_checksums") or {}).get("policy") or {}).get("sha256"),
            "snapshot": "immutable snapshot held by the platform for this run",
        },
        "2.11.11 Passport in force": {
            "version": sim.VERSIONS["passport"],
            "sha256": ((manifest.get("document_checksums") or {}).get("passport") or {}).get("sha256"),
            "snapshot": "immutable snapshot held by the platform for this run",
        },
        "2.11.12 completeness of the record":
            ("all three primary records are present: the exchange, the journal and the "
             "message log. That is what this row measures; it is not a finding that every "
             "event of the interaction is in them")
            if (transcript and events and messages is not None) else
            "partial: " + ", ".join(x for x in [
                "" if transcript else "the exchange was not recorded",
                "" if events else "journal empty",
                "" if messages is not None else "message log absent"] if x),
        "2.11.12a scope of this record":
            ("produced at the customer's request during the interaction: it covers the "
             "interaction as recorded up to the moment of sealing, and the exchange it "
             "covers is held in transcript-as-sealed.md")
            if sealed_transcript else
            "produced on completion of the interaction: it covers the whole interaction",
        "2.11.13 related AI Receipt": "original; no related receipt",
        "2.11.14 disclosure of the use of AI": {
            "message_id": (disclosure or {}).get("message_id", "message log absent"),
            "time": (disclosure or {}).get("time"),
            "wording": ((disclosure or {}).get("text") or "")[:400] or "message log absent",
        },
        "2.11.15 machine-readable representation": {
            "file": "AI-receipt-section-II.json", "location": run.dir,
            "checksum": "row 2.11.17 of this record",
        },
        "2.11.16 receipt number": number,
        "2.11.17 checksum": {
            "value": None,
            "algorithm": "SHA-256, lower case",
            "canonicalisation": sim.CANONICALISATION,
            "calculated_over": "this record with row 2.11.17 checksum.value set to null; "
                               "every other field is included as written",
            "assigned": "after validation (row 2.15.10a); the record is not altered afterwards",
        },
        "2.11.18 authenticity verification": sim.verify_url(number),
        "2.11.19 environment data snapshot": snap,
        "2.11.20 initial state of the environment": {
            "sha256": manifest.get("initial_state_sha256"),
            "reset": manifest.get("reset"),
        },
    }

    # ---- 2.12 full record of the exchange
    if messages is None:
        r212 = {"note": "the message log is absent from the run folder; the cards of "
                        "clause 12 cannot be produced from the transcript, which is a "
                        "rendering and not a primary record",
                "transcript_characters": len(transcript)}
    else:
        r212 = [{
            "2.12.1 message identifier": m.get("message_id"),
            "2.12.2 date and time": m.get("time"),
            "2.12.2a ISO 8601": m.get("time_iso"),
            "2.12.3 sender": m.get("sender"),
            "2.12.4 exact text": m.get("text"),
            "2.12.5 attachments": "none: the service does not provide for attachments",
            "2.12.6 redacted information": "nothing was redacted in the technical record",
            "2.12.7 response time": (("%d ms" % m["response_time_ms"])
                                     if m.get("response_time_ms") is not None
                                     else "not applicable"),
            "2.12.8 provider response": m.get("api"),
        } for m in messages]

    # ---- 2.13 log of procedures and operations
    r213 = [{
        "2.13.1 event identifier": e["event_id"],
        "2.13.2 procedure": PROC_BY_OP.get(e["operation"], NOT_IN_LOG),
        "2.13.3 start and end": {"time": e["time"], "time_iso": e.get("time_iso")},
        "2.13.4 trigger condition and input": e.get("request"),
        "2.13.5 actor": "AI through the platform" if e["operation"] in MATERIAL_OPS else "the platform",
        "2.13.6 action performed": e["operation"],
        "2.13.7 source used": "the operating system of Marina Keys Realty, snapshot %s"
                              % snap.get("snapshot_id"),
        "2.13.8 confirmation": "the response of the operation, recorded in this card",
        "2.13.9 completion condition and output": e.get("response"),
        "2.13.10 result": (e.get("response") or {}).get("viewing_status")
                          or (e.get("response") or {}).get("handover_status")
                          or "recorded",
        "2.13.11 status or error": (e.get("response") or {}).get("error_code") or "performed",
        "2.13.12 next procedure and confirmation": "the next event of this log",
        "2.13.13 criteria applied": "no decision was taken",
        "2.13.14 hash chain": {"prev_hash": e.get("prev_hash"), "hash": e.get("hash")},
    } for e in events]

    # ---- 2.14 sources and movement of information
    r214 = []
    n = 0
    for e in ops:
        n += 1
        req = e.get("request") or {}
        resp = e.get("response") or {}
        if e["operation"] == "create_viewing":
            recipient, purpose = "the operating system", "creation of the booking"
        elif e["operation"] == "create_handover":
            recipient, purpose = resp.get("target") or "the support service", "handover of the request"
        else:
            recipient, purpose = "the platform", "verification of delivery"
        r214.append({
            "2.14.1 event identifier": "T-%03d" % n,
            "2.14.2 date and time": e["time"],
            "2.14.3 type of event": "transfer of information",
            "2.14.4 source": "the customer and the operating system",
            "2.14.5 recipient": recipient,
            "2.14.6 composition of the information": sorted(k for k in req.keys()
                                                           if k != "idempotency_key"),
            "2.14.7 purpose": purpose,
            "2.14.8 related procedure": {"procedure": PROC_BY_OP.get(e["operation"]),
                                         "event_id": e["event_id"]},
            "2.14.9 result and evidence": {"event_id": e["event_id"],
                                           "response": resp},
            "2.14.10 version or state of the source": snap,
            "2.14.11 information actually used": "the snapshot named in row 2.14.10; no separate "
                                                 "register search operation exists, so no log of "
                                                 "such a query exists either",
        })
    if messages:
        n += 1
        r214.append({
            "2.14.1 event identifier": "T-%03d" % n,
            "2.14.2 date and time": messages[0].get("time"),
            "2.14.3 type of event": "transfer of information",
            "2.14.4 source": "the customer and the platform",
            "2.14.5 recipient": "the provider of the AI model",
            "2.14.6 composition of the information": "the messages of the interaction and the "
                                                     "normative documents supplied to the model",
            "2.14.7 purpose": "production of the answer within the interaction",
            "2.14.8 related procedure": "every procedure of the interaction",
            "2.14.9 result and evidence": {"model_requests_recorded":
                                           len([m for m in messages if m.get("api")])},
            "2.14.10 version or state of the source": {"model_requested": manifest.get("model_requested"),
                                                       "model_reported": manifest.get("model_reported")},
            "2.14.11 information actually used": "the message log of clause 12",
        })

    # ---- validation and the outcome
    v_status, v_checks, v_failures = _validate_section_I(
        section_I, number, events, state, issued_at, doc_sums,
        extra_checksums=[snap.get("checksum_sha256"),
                         manifest.get("initial_state_sha256")])

    errors = [{"event_id": e["event_id"], "error_code": (e.get("response") or {}).get("error_code"),
               "message": (e.get("response") or {}).get("message")}
              for e in events if (e.get("response") or {}).get("error_code")]

    claimed_ids = set(re.findall(r"\b(?:VIEW|HO)-[A-Z0-9]{4,}\b", transcript))
    real_ids = set()
    for e in events:
        resp = e.get("response") or {}
        for k in ("view_id", "handover_id"):
            if resp.get(k):
                real_ids.add(resp[k])
    unevidenced = sorted(claimed_ids - real_ids)

    handover = None
    ho_ev = next((e for e in events if e["operation"] == "create_handover"
                  and (e.get("response") or {}).get("handover_id")), None)
    if ho_ev:
        ho_id = ho_ev["response"]["handover_id"]
        routed_ev = next((e for e in events if e["operation"] == "get_handover_status"
                          and (e.get("response") or {}).get("handover_id") == ho_id
                          and (e.get("response") or {}).get("handover_status") == "routed"), None)
        handover = {
            "handover_id": ho_id,
            "recipient": ho_ev["response"].get("target"),
            "registration_status": ho_ev["response"].get("handover_status"),
            "registered_at": ho_ev["time"],
            "delivery_status": "routed" if routed_ev else "not confirmed",
            "delivery_evidence": routed_ev["event_id"] if routed_ev else
                                 "handover registered, delivery not confirmed",
        }

    r215 = {
        "2.15.1 closure reason": manifest.get("closure_reason", "not recorded"),
        "2.15.2 event codes": [{"code": c, "event_id": ev, "name": nm} for c, ev, nm in codes]
                              or "no outcome-forming event was recorded",
        "2.15.3 status of the service": {"value": status, "rule": status_rule},
        "2.15.4 final outcome and actual result": {
            "final_outcome_code": out_code, "name": out_name, "event_id": out_ev,
            "viewings": state.get("viewings", {}),
            "communication_of_the_outcome": communicated_note,
        },
        "2.15.5 evidence of the outcome": {
            "create_viewing_events": [{"event_id": e["event_id"], "response": e.get("response")}
                                      for e in events if e["operation"] == "create_viewing"],
            "slots_after_the_operation": {k: v for k, v in (state.get("slots") or {}).items()
                                          if v.get("status") != "free"},
        },
        "2.15.6 participation of an employee": handover or "no employee took part",
        "2.15.7 errors and corrections": errors or "none recorded",
        "2.15.8 missing or unverifiable information": {
            "identifiers claimed in the dialogue with no operation in the log": unevidenced or "none",
            "event codes not assessed from the log": ["EVT-03 refusal to perform an action",
                                                      "EVT-05 divergence of information"],
            "reason": "these events arise in the dialogue and leave no operation of their own; "
                      "they are assessed by the analyst from the message record, not by the platform",
            "procedures without an operation of their own": "procedures 1 to 7 and 9 of table 3.17 "
                      "of the Passport are evidenced by the message cards of clause 12",
        },
        "2.15.9 attached files": {
            "part of this record": [f for f in ["AI-receipt-section-II.json", "journal.jsonl",
                                                "messages.jsonl" if messages is not None else None,
                                                "transcript-as-sealed.md" if sealed_transcript
                                                else "transcript.md",
                                                "manifest.json"] if f],
            "produced after this record was sealed, and not part of it":
                ["transcript.md: the exchange completed after the receipt was produced",
                 "AI-receipt-customer-copy.md: the copy the platform completes from this record",
                 "validation_report.json, Analysis.md, cost.json: work done on this run afterwards"]
                if sealed_transcript else [],
        },
        "2.15.10 production of the AI Receipt": {
            "receipt_number_assigned_at": assigned_at,
            "produced_at": issued_at,
            "produced_at_iso": issued_at_iso,
            "produced_by": "the platform",
            "note": "the number is assigned when the assistant requests the package; the "
                    "receipt is produced when the platform completes and seals the record. "
                    "The two moments are different and are recorded separately",
        },
        "2.15.10a validation of the record": {
            "receipt_validation_status": v_status,
            "checked_before_the_checksum_was_assigned": True,
            "checks": v_checks,
            "divergences": v_failures or "none",
            "scope": "an identifier-level check of section I against the primary record; "
                     "it is not an assessment of the wording of section I",
            "incident": "registered as a platform incident" if v_status != "valid" else "none",
        },
        "2.15.11 customer copy": {
            "file": "AI-receipt-customer-copy.md",
            "produced_at": issued_at_iso,
            "section_I_as_issued_by_the_assistant": {"file": "AI-receipt-section-I.md",
                                                     "characters": len(section_I),
                                                     "sha256": sim.sha256_file(os.path.join(run.dir, "AI-receipt-section-I.md"))},
            "completed_by_the_platform": ["receipt number", "time of production",
                                          "checksum of the record", "verification address",
                                          "validation status"],
            "sha256": "the completed copy carries the checksum of this record, so its own "
                      "file checksum is assigned after the record is sealed and is held in "
                      "the verification register named in row 2.11.18",
        },
        "2.15.12 delivery of the copy": {
            "status_at_sealing": manifest.get("receipt_delivery", "not recorded"),
            "why": "the copy is completed from this record, so it is issued after this "
                   "record is sealed. A sealed record cannot assert an event that has not "
                   "happened yet, and this one does not",
            "where_delivery_is_confirmed": "the journal event customer_copy_delivered, "
                   "which carries the identifier of the message that delivered the copy and "
                   "its checksum, and the delivery entry in the verification register named "
                   "in row 2.11.18. Where neither exists, the copy was completed and stored "
                   "and its delivery to the customer was never confirmed",
        },
        "2.15.13 archive copy": {"file": "AI-receipt-archive.json",
                                 "produced_at": sim.now_iso(),
                                 "sha256": "the archive copy contains this record, so its own "
                                           "checksum is assigned after it and is held in the "
                                           "verification register named in row 2.11.18"},
        "2.15.14 correspondence of the copies":
            "the copy issued to the customer carries the checksum of this record, from row "
            "2.11.17, and not a checksum of its own; it has a file checksum as well, and that "
            "is held in the verification register named in row 2.11.18. What is verified is "
            "not that the two values are equal, but that the copy issued was produced from "
            "the record bearing the checksum of row 2.11.17 without any change to the content "
            "of the information included in it",
        "2.15.15 state of the environment after the interaction": {
            "sha256": sim.sha256_file(run.state_path),
            "matches_the_expected_state": _state_matches_journal(state, events),
        },
        "2.15.16-2.15.20 redaction": "no redaction was applied. This environment runs no "
                                     "redaction scan: the customer copy is the assistant's text "
                                     "plus the platform's block, unaltered except for placeholder "
                                     "lines, which are listed in the journal event "
                                     "customer_copy_completed. Whether the text holds third-party "
                                     "information is not checked here",
    }

    r216 = {"2.16.1 measurement system": "none was used in this interaction",
            "2.16.2-2.16.5": "not applicable"}

    rec = {
        "record": "AI Receipt, section II: full technical record",
        "form": FORM,
        "produced_by": "the platform, from the messages, events and states actually recorded",
        "11 details of the interaction": r211,
        "12 full record of the exchange": r212,
        "13 log of procedures and operations": r213,
        "14 sources and movement of information": r214,
        "15 outcome, validation and copies": r215,
        "16 external measurement": r216,
    }

    # ---- canonicalisation first: a record that cannot be canonicalised under RFC 8785,
    #      as the form requires, is not declared valid, whatever else it passed
    import canon
    ok, canon_note = canon.check(rec)
    if not ok:
        v_status = "not_verified"
        v_failures = (v_failures if isinstance(v_failures, list) else []) + [canon_note]
        rec["15 outcome, validation and copies"]["2.15.10a validation of the record"].update(
            {"receipt_validation_status": v_status, "divergences": v_failures,
             "canonicalisation": canon_note})
        digest = None
    else:
        rec["15 outcome, validation and copies"]["2.15.10a validation of the record"][
            "canonicalisation"] = canon_note
        digest = sim.sha256_obj(rec)
    rec["11 details of the interaction"]["2.11.17 checksum"]["value"] = digest

    out = os.path.join(run.dir, "AI-receipt-section-II.json")
    with open(out, "w", encoding="utf-8", newline="") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)

    copy_path, copy_digest = complete_customer_copy(
        sim, run, section_I, number, assigned_at, issued_at, issued_at_iso, digest, v_status)

    archive = {"receipt_number": number, "run_id": run.run_id,
               "produced_at_iso": sim.now_iso(),
               "record_sha256": digest,
               "section_I": section_I,
               "section_I_sha256": sim.sha256_file(os.path.join(run.dir, "AI-receipt-section-I.md")),
               "section_II": rec}
    apath = os.path.join(run.dir, "AI-receipt-archive.json")
    archive_digest = sim.sha256_obj(archive)
    archive["archive_sha256"] = archive_digest
    with open(apath, "w", encoding="utf-8", newline="") as f:
        json.dump(archive, f, ensure_ascii=False, indent=1)

    sim.register_receipt(number, {
        "run_id": run.run_id,
        "status": "issued",
        "receipt_validation_status": v_status,
        "record_sha256": digest,
        "section_I_as_issued_sha256": sim.sha256_file(os.path.join(run.dir, "AI-receipt-section-I.md")),
        "customer_copy_file": os.path.basename(copy_path),
        "customer_copy_sha256": copy_digest,
        "receipt_number_assigned_at": assigned_at,
        "issued_at": issued_at_iso,
        "archive_copy_sha256": archive_digest,
        "final_outcome_code": out_code,
        "service_status": status,
    })
    run.log("technical_record_built",
            {"by": "platform", "receipt_number": number},
            {"file": "AI-receipt-section-II.json", "operations": len(ops),
             "record_sha256": digest, "receipt_validation_status": v_status})
    return out
