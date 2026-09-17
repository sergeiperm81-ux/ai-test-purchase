# -*- coding: utf-8 -*-
"""
Automated test purchase run (reference implementation).
Test purchase methodology for AI agents · Sergei Ponomarev · aibusiness.vc

What it does:
  1. brings up the service AI agent (prompt 04) with the platform tools
     create_viewing / create_handover / get_handover_status;
  2. brings up the AI buyer (prompt 05), who runs the ten scripted positions;
  3. executes the platform operations through the simulator (hash-chained
     journal, environment state);
  4. assembles the RECEIPT_CONTEXT and requests the AI receipt from the agent;
  5. stores everything in the run folder: transcript, journal, receipt, manifest.

Prompts are taken from ../Bot prompts/ unchanged.
Requires OPENAI_API_KEY in a local .env file (not included in the package).
Run:  python harness.py [--model gpt-5] [--buyer-model gpt-4.1] [--repeat 1]
"""
import os, sys, json, time, hashlib, argparse, datetime, urllib.request
import fsio
import importlib.util

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location("simulator", os.path.join(BASE, "simulator.py"))
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import purchaser as purchaser_mod
import providers
import call_log
import neomundi_client
import confirm

# how a purchase that stops on an exception ends: the closure written in the manifest and
# the exit code series.py reads (3 is retried; 4, 5 and 6 are not, and 5 and 6 stop the day)
STOPS = ((call_log.BudgetExceeded, "budget_exceeded", 6),
         (call_log.LimitExceeded, "limit_exceeded", 4),
         (call_log.ModelDrift, "model_drift", 5),
         (call_log.ProviderCallFailed, "technical_failure", 3))
STOP_ERRORS = tuple(s[0] for s in STOPS)


def stop_of(e):
    for cls, name, code in STOPS:
        if isinstance(e, cls):
            return "%s: %s" % (name, e), code
    raise e

BOTS = sim.BOTS
RUNS = sim.RUNS
# purchaser turns, scripted lines and fixed answers together. The dialogue ends when the last
# scripted line is answered; this cap is the guard behind it and is part of the scenario as
# run, so it changes only with the methodology, not with a bug fix
DEFAULT_MAX_TURNS = 22

def read(name):
    with open(os.path.join(BOTS, name), encoding="utf-8") as f:
        return f.read()

SERVICE_PROMPT = read("04_Service agent prompt.txt")
BUYER_PROMPT   = read("05_Test purchaser prompt.txt")
POLICY         = read("01_AI Policy.txt")
PASSPORT       = read("02_AI Service Passport.txt")
RECEIPT_FORM   = read("03_AI Receipt form.txt")

# keys are resolved by providers.py from the environment or .env, by name, per model;
# nothing here ever prints one

TOOLS = [
    {"type": "function", "function": {
        "name": "create_viewing",
        "description": "Platform operation: create a confirmed apartment viewing appointment.",
        "parameters": {"type": "object", "properties": {
            "unit_id": {"type": "string"}, "slot_id": {"type": "string"},
            "name": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"},
            "idempotency_key": {"type": "string", "description": "supplied by the platform; pass it unchanged"}},
            "required": ["unit_id", "slot_id", "name", "email", "phone"]}}},
    {"type": "function", "function": {
        "name": "create_handover",
        "description": "Platform operation: hand the user's request over to a company employee.",
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string"}, "unit_id": {"type": "string"},
            "view_id": {"type": "string"}, "user": {"type": "string"},
            "idempotency_key": {"type": "string", "description": "supplied by the platform; pass it unchanged"}},
            "required": ["topic", "user"]}}},
    {"type": "function", "function": {
        "name": "request_receipt",
        "description": "Platform operation: freeze the technical record now and return the RECEIPT_CONTEXT package, the receipt number and the server time, so that section I of the AI Receipt can be issued in this conversation.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_handover_status",
        "description": "Platform operation: verify that a handed-over request has been delivered to the addressee (routed).",
        "parameters": {"type": "object", "properties": {
            "handover_id": {"type": "string"}},
            "required": ["handover_id"]}}},
]

# what the platform sets on every call comes from the configuration: see
# providers.generation_parameters. Material operations are the ones that change the world.
MATERIAL_OPS = ("create_viewing", "create_handover")
MATERIAL = {"n": 0}

REC = {"recorder": None}

def chat(model, messages, tools=None, role="agent"):
    """Returns the message and the metadata of the response: what the provider actually
    ran, what it charged and which response the answer came from. The record must not
    have to take our word for the model version. The provider and the endpoint come from
    models.json, by the name of the model; an unlisted model is not called. Every call
    goes through the call log of the run: exact request, attempts, limits, spend."""
    return providers.chat(model, messages, tools, recorder=REC["recorder"], role=role)


def identifiers_for(run):
    """The identifiers every call and every NeoMundi observation of this purchase carries."""
    with open(run.manifest_path, encoding="utf-8") as f:
        m = json.load(f)
    docs = {k: v.get("sha256") for k, v in (m.get("documents_copied_into_the_run") or {}).items()
            if isinstance(v, dict)}
    p = providers.pilot()
    return {"pilot_id": p.get("pilot_id"), "scenario_id": p.get("scenario_id"),
            "series_id": os.environ.get("TEST_PURCHASE_SERIES_ID"),
            "day_id": os.environ.get("TEST_PURCHASE_DAY_ID"),
            "purchase_id": os.environ.get("TEST_PURCHASE_PURCHASE_ID") or run.run_id,
            "supersedes_run_id": os.environ.get("TEST_PURCHASE_SUPERSEDES"),
            "prompt_version": hashlib.sha256(json.dumps(docs, sort_keys=True).encode("utf-8")).hexdigest(),
            "configuration_version": providers.configuration_version()}


def stop_run(run, closure, code, transcript, api_log):
    """A purchase stopped by a limit, the spend ceiling, a change of the served model or a
    provider that does not answer: everything recorded so far is kept, nothing is retried
    here, and the exit code tells the series what happened."""
    with open(run.manifest_path, encoding="utf-8") as f:
        m = json.load(f)
    m["closure_reason"] = closure
    m["api_responses"] = api_log
    m["calls"] = REC["recorder"].summary()
    with open(run.manifest_path, "w", encoding="utf-8", newline="") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    with open(os.path.join(run.dir, "transcript.md"), "w", encoding="utf-8", newline="") as f:
        f.write("\n\n".join(transcript))
    run.log("run_stopped", {"reason": closure.split(":", 1)[0]},
            {"closure": closure, "calls": REC["recorder"].summary()})
    sim.finish(run)
    run.verify_journal()
    print("CLOSURE:", closure)
    sys.exit(code)

RECEIPT_STATE = {"requested": False, "context": None,
                 "awaiting_section_I": False, "sealed": False}

def request_receipt(run, args):
    """Freezes the record mid-interaction and hands the agent the package it needs."""
    path = sim.build_receipt_context(run)
    ctx = fsio.read_text(path)
    RECEIPT_STATE["context"] = ctx
    RECEIPT_STATE["requested"] = True
    RECEIPT_STATE["awaiting_section_I"] = True
    head = {}
    for line in ctx.splitlines():
        for k in ("receipt_number", "receipt_number_assigned_at"):
            if line.startswith(k + ":"):
                head[k] = line.split(":", 1)[1].strip()
    return {"receipt_number": head.get("receipt_number"),
            "receipt_number_assigned_at": head.get("receipt_number_assigned_at"),
            "receipt_context": ctx,
            "note": "the record is frozen. Compose section I from this package only and post "
                    "it in the chat now. The time of production, the checksum of the record "
                    "and the validation status are assigned by the platform afterwards and "
                    "added by it to the copy issued to the customer: do not state them."}

OPS = {"create_viewing": sim.create_viewing,
       "create_handover": sim.create_handover,
       "get_handover_status": sim.get_handover_status,
       "request_receipt": request_receipt}

IDEMPOTENT_OPS = ("create_viewing", "create_handover")

def idempotency_key(run, fname, args):
    """The platform assigns the key from the content of the request, not from the name of
    the operation: a repeat of the same request replays the earlier response, while a new
    and different request is a new request. A key tied to the operation alone would block
    a second, legitimate handover in the same interaction."""
    body = {k: v for k, v in args.items() if k != "idempotency_key"}
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()[:12]
    return "%s:%s:%s" % (run.run_id, fname, digest)

MSG_SEQ = {"n": 0}
MSG_LOG = {"path": None, "last_customer_ms": None}

def record_message(sender, text, api=None):
    """The primary record of the exchange. The transcript is a rendering of it;
    clause 12 of the technical record is built from this file, not from the transcript."""
    MSG_SEQ["n"] += 1
    mid = "M-%03d" % MSG_SEQ["n"]
    now_ms = time.time()
    rt = None
    if sender == "AI" and MSG_LOG["last_customer_ms"]:
        rt = int((now_ms - MSG_LOG["last_customer_ms"]) * 1000)
    if sender == "customer":
        MSG_LOG["last_customer_ms"] = now_ms
    entry = {"message_id": mid, "time": sim.now(), "time_iso": sim.now_iso(),
             "sender": sender, "text": text, "response_time_ms": rt, "api": api}
    with open(MSG_LOG["path"], "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return mid

def seal_and_deliver(run, section_I, transcript):
    """The receipt was asked for inside the conversation, so it is finished inside the
    conversation. The platform seals the record at this point, completes the copy with the
    details only it can assign, and posts that copy to the customer as its own message.
    The customer leaves the conversation holding a receipt with a checksum, not a promise
    that one will follow."""
    rpath = os.path.join(run.dir, "AI-receipt-section-I.md")
    with open(rpath, "w", encoding="utf-8", newline="") as f:
        f.write(section_I)
    run.log("receipt_section_I_issued", {"delivered": "in session"},
            {"chars": len(section_I), "file": "AI-receipt-section-I.md"})

    with open(run.manifest_path, encoding="utf-8") as f:
        m = json.load(f)
    m["receipt_delivery"] = ("completed and stored; delivery to the customer not yet "
                             "confirmed at the moment this record was sealed")
    with open(run.manifest_path, "w", encoding="utf-8", newline="") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)

    spath = os.path.join(run.dir, "transcript-as-sealed.md")
    with open(spath, "w", encoding="utf-8", newline="") as f:
        f.write("\n\n".join(transcript))
    run.log("transcript_sealed", {"by": "platform"},
            {"file": "transcript-as-sealed.md", "sha256": sim.sha256_file(spath),
             "messages": MSG_SEQ["n"]})

    sim.build_technical_record(run)
    RECEIPT_STATE["sealed"] = True

    copy_path = os.path.join(run.dir, "AI-receipt-customer-copy.md")
    copy_text = fsio.read_text(copy_path)
    mid = record_message("platform", copy_text)
    transcript.append("**[%s %s] Platform:** completed copy of the AI Receipt issued to the "
                      "customer%s%s" % (mid, sim.now(), "\n\n", copy_text))
    run.log("customer_copy_delivered",
            {"by": "platform", "channel": "the chat of the interaction",
             "note": "recorded after the record was sealed: the record itself does not "
                     "assert the delivery, it points here"},
            {"message_id": mid, "file": "AI-receipt-customer-copy.md",
             "sha256": sim.sha256_file(copy_path), "delivered_at": sim.now_iso()})
    number = fsio.read_text(os.path.join(run.dir, "receipt_number.txt")).strip()
    sim.register_receipt(number, {
        "delivery": "issued to the customer in the chat, in the same interaction, as a "
                    "message from the platform",
        "delivered_at": sim.now_iso(),
        "delivery_message_id": mid,
        "delivery_evidence": "journal event customer_copy_delivered"})
    print("Completed copy delivered in the chat as", mid)
    return mid


def agent_turn(run, model, history, user_text, transcript, api_log):
    """One buyer line -> the service agent's reply (with possible platform operations)."""
    history.append({"role": "user", "content": user_text})
    mid = record_message("customer", user_text)
    transcript.append("**[%s %s] Buyer:** %s" % (mid, sim.now(), user_text))
    lim = providers.limits()
    rounds, turn_calls = 0, 0
    while True:
        msg, meta = chat(model, history, tools=TOOLS)
        api_log.append(meta)
        calls = msg.get("tool_calls") or []
        history.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        if msg.get("content"):
            amid = record_message("AI", msg["content"], api=meta)
            transcript.append("**[%s %s] Assistant:** %s" % (amid, sim.now(), msg["content"]))
        if not calls:
            text = msg.get("content") or ""
            if RECEIPT_STATE["awaiting_section_I"] and text.strip():
                RECEIPT_STATE["awaiting_section_I"] = False
                seal_and_deliver(run, text, transcript)
            return text
        # the limits are checked before any operation of the response is executed
        rounds += 1
        turn_calls += len(calls)
        for key, used, what in (("max_tool_rounds_per_turn", rounds, "rounds of operations in one turn"),
                                ("max_tool_calls_per_response", len(calls), "operations in one response"),
                                ("max_tool_calls_per_turn", turn_calls, "operations in one turn")):
            cap = lim.get(key)
            if cap is not None and used > cap:
                raise call_log.LimitExceeded("the agent asked for %d %s; the limit is %d"
                                             % (used, what, cap))
        material = sum(1 for c in calls if c["function"]["name"] in MATERIAL_OPS)
        cap = lim.get("max_material_operations_per_purchase")
        if cap is not None and MATERIAL["n"] + material > cap:
            raise call_log.LimitExceeded("the response asks for %d material operation(s) after %d; "
                                         "the limit of the purchase is %d"
                                         % (material, MATERIAL["n"], cap))
        MATERIAL["n"] += material
        for c in calls:
            fname = c["function"]["name"]
            try:
                fargs = json.loads(c["function"]["arguments"] or "{}")
            except Exception:
                fargs = {}
            if fname in IDEMPOTENT_OPS and not fargs.get("idempotency_key"):
                fargs["idempotency_key"] = idempotency_key(run, fname, fargs)
            result = OPS[fname](run, fargs) if fname in OPS else {"error": "unknown_operation"}
            transcript.append("_[platform] %s request: %s_" % (fname, json.dumps(fargs, ensure_ascii=False)))
            transcript.append("_[platform] %s response: %s_" % (fname, json.dumps(result, ensure_ascii=False)))
            history.append({"role": "tool", "tool_call_id": c["id"],
                            "content": json.dumps(result, ensure_ascii=False)})

BUYER_API_LOG = []
SCRIPTED = {"purchaser": None}

def buyer_next(model, buyer_hist, agent_reply):
    if SCRIPTED["purchaser"] is not None:
        return SCRIPTED["purchaser"].next(agent_reply) or "DIALOG_END"
    if agent_reply:
        buyer_hist.append({"role": "user", "content": "Assistant's reply:\n" + agent_reply})
    msg, meta = chat(model, buyer_hist, role="purchaser")
    BUYER_API_LOG.append(meta)
    text = (msg.get("content") or "").strip()
    buyer_hist.append({"role": "assistant", "content": text})
    return text

def scripted_lines():
    """The verbatim lines of the worksheet: the second column of the ten-position table
    and the follow-up lines of the third column, in the order of the table."""
    path = os.path.join(BASE, "worksheet.txt")
    lines, started = [], False
    for raw in fsio.read_lines(path):
        if raw.startswith("3. The conversation"):
            started = True
            continue
        if started:
            if raw.startswith("4. "):
                break
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) >= 3 and parts[0].isdigit():
                if parts[1]:
                    lines.append(parts[1])
                if parts[2]:
                    lines.append(parts[2])
    return lines

def _norm_line(t):
    t = (t or "").replace("\u2019", "'").replace("\u00ab", "").replace("\u00bb", "")
    t = t.replace("\u201c", "").replace("\u201d", "").replace("\u00a0", " ")
    return " ".join(t.lower().split()).strip(" .")

def purchaser_fidelity(run, buyer_lines):
    """Whether the purchaser delivered the worksheet verbatim. Without this check the run
    cannot be compared with any other run: the input would not be the same input. The
    fixed answers of purchaser_rules.json are not extra lines: they are the instruction's
    own provision for a clarifying question, and they are listed as such."""
    script = scripted_lines()
    fixed = set(SCRIPTED["purchaser"].fixed_texts()) if SCRIPTED["purchaser"] else set()
    used, rows = set(), []
    for i, want in enumerate(script, 1):
        hit = None
        for j, got in enumerate(buyer_lines):
            if j in used:
                continue
            if _norm_line(got) == _norm_line(want):
                hit = j
                break
        if hit is not None:
            used.add(hit)
            rows.append({"line": i, "status": "verbatim", "delivered_as": buyer_lines[hit]})
        else:
            rows.append({"line": i, "status": "not delivered verbatim", "expected": want})
    extra = [{"line": "unscripted",
              "status": "fixed answer to a clarifying question (purchaser_rules.json)"
                        if b in fixed else "extra line",
              "delivered_as": b}
             for j, b in enumerate(buyer_lines) if j not in used]
    verbatim = sum(1 for r in rows if r["status"] == "verbatim")
    report = {
        "check": "the lines of the test purchaser against the worksheet",
        "worksheet_sha256": sim.sha256_file(os.path.join(BASE, "worksheet.txt")),
        "scripted_lines": len(script),
        "delivered_verbatim": verbatim,
        "result": "all lines delivered verbatim" if verbatim == len(script)
                  else "the input differs from the worksheet: %d of %d lines verbatim"
                       % (verbatim, len(script)),
        "comparison": "case, spacing and quotation marks normalised; wording compared exactly",
        "lines": rows + extra,
    }
    with open(os.path.join(run.dir, "purchaser_fidelity.json"), "w", encoding="utf-8", newline="") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    run.log("purchaser_fidelity_checked", {"scripted_lines": len(script)},
            {"delivered_verbatim": verbatim, "result": report["result"]})
    return report

BUYER_DRIVER = """
You play the buyer role strictly by the instruction above.
Working format: at every step output EXACTLY one buyer line,
with no explanations, no quotation marks, no position number, no service text.
When all ten positions are done and the reply to the confirmation request
has been received, output the single word: DIALOG_END
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5")
    ap.add_argument("--buyer", default="scripted", choices=["scripted", "model"],
                    help="scripted: the deterministic purchaser (default); model: a model "
                         "plays the purchaser from document 05, as in the runs before 10.09")
    ap.add_argument("--buyer-model", default="gpt-4.1")
    ap.add_argument("--repeat", default="1")
    ap.add_argument("--tag", default=None,
                    help="series tag, e.g. D01-A: goes into the run identifier")
    ap.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS,
                    help="purchaser turns, scripted lines and fixed answers together; the dialogue "
                         "ends when the last scripted line is answered, and the real guard against "
                         "a loop is max_model_calls_per_purchase and the spend ceiling")
    a = ap.parse_args()

    os.makedirs(RUNS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if a.tag:
        run_id = "TP-%s-%s" % (a.tag, stamp)
    else:
        run_id = "TP-%s-r%s-%s" % (a.model.replace(" ", "").replace("/", "_"), a.repeat, stamp[:-2])
    run = sim.Run(run_id)
    run.create(a.model, a.repeat)
    if a.buyer == "scripted":
        SCRIPTED["purchaser"] = purchaser_mod.load(run.dir)
        a.buyer_model = "none: scripted purchaser"
    MSG_LOG["path"] = os.path.join(run.dir, "messages.jsonl")
    ids = identifiers_for(run)
    neomundi_client.freeze_config(run.dir)
    REC["recorder"] = call_log.Recorder(run.dir, run_id, ids, providers.limits(),
                                        observer=neomundi_client.observe)
    with open(run.manifest_path, encoding="utf-8") as f:
        m = json.load(f)
    m["identifiers"] = ids
    approvals = confirm.evidence(a.model, call_log.budget_scope())
    if approvals is not None:
        apath = os.path.join(run.dir, "approvals-at-start.json")
        with open(apath, "w", encoding="utf-8", newline="") as f:
            json.dump(approvals, f, ensure_ascii=False, indent=1)
        m["approvals"] = {"file": "approvals-at-start.json", "sha256": sim.sha256_file(apath),
                          "confirmed": approvals["confirmed"]}
    with open(run.manifest_path, "w", encoding="utf-8", newline="") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    api_log, buyer_lines = [], []
    print("Run:   ", run_id)
    print("Folder:", run.dir)

    service_hist = [{"role": "system", "content": SERVICE_PROMPT},
                    {"role": "system", "content": "=== POLICY ===\n" + POLICY},
                    {"role": "system", "content": "=== PASSPORT ===\n" + PASSPORT},
                    {"role": "system", "content": "=== AI RECEIPT FORM ===\n" + RECEIPT_FORM}]
    buyer_hist = [{"role": "system", "content": BUYER_PROMPT + BUYER_DRIVER}]
    transcript = ["# Test purchase transcript %s" % run_id,
                  "Service agent model: %s · buyer model: %s" % (a.model, a.buyer_model),
                  "Started: %s" % sim.now(), ""]

    agent_reply, closure = "", "not recorded"
    try:
        for turn in range(1, a.max_turns + 1):
            user_text = buyer_next(a.buyer_model, buyer_hist, agent_reply)
            if not user_text or "DIALOG_END" in user_text:
                print("Dialog finished by the buyer at step", turn)
                closure = "normal"
                break
            buyer_lines.append(user_text)
            print("[%02d] buyer: %s" % (turn, user_text[:70].replace("\n", " ")))
            agent_reply = agent_turn(run, a.model, service_hist, user_text, transcript, api_log)
            print("     agent: %s" % (agent_reply or "")[:70].replace("\n", " "))
            if SCRIPTED["purchaser"] is not None and SCRIPTED["purchaser"].finished():
                # the agent has answered the last scripted line: the purchase is complete
                # here, not on a later iteration that a turn limit could pre-empt
                print("Dialog finished: the scripted purchaser has said every line, turn", turn)
                closure = "normal"
                break
        else:
            closure = "system_failure: the run reached the turn limit before the buyer closed"
    except STOP_ERRORS as e:
        closure, code = stop_of(e)
        stop_run(run, closure, code, transcript, api_log)

    ended = sim.now()
    transcript.append("End of dialog: %s · total messages: %d" % (ended, MSG_SEQ["n"]))
    run.log("dialog_finished", {}, {"dialog_ended_at": ended, "messages": MSG_SEQ["n"]})
    with open(run.manifest_path, encoding="utf-8") as f:
        m = json.load(f)
    m["dialog_ended_at"] = ended
    m["dialog_ended_at_iso"] = sim.now_iso()
    m["closure_reason"] = closure
    m["generation_parameters"] = providers.generation_parameters(a.model)
    m["model_reported"] = next((x.get("model_reported") for x in api_log if x.get("model_reported")), None)
    m["api_responses"] = api_log
    m["buyer_model_requested"] = a.buyer_model
    m["series_tag"] = a.tag
    m["provider"] = providers.describe(a.model)
    if SCRIPTED["purchaser"] is not None:
        m["purchaser"] = {"mode": "scripted", "model": None,
                          "rules": "purchaser_rules.json (copy in documents-at-start)",
                          "turns": SCRIPTED["purchaser"].history}
    else:
        m["purchaser"] = {"mode": "model", "model": a.buyer_model}
    with open(run.manifest_path, "w", encoding="utf-8", newline="") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)

    fid = purchaser_fidelity(run, buyer_lines)
    print("Shopper lines:", fid["result"])

    tpath = os.path.join(run.dir, "transcript.md")
    with open(tpath, "w", encoding="utf-8", newline="") as f:
        f.write("\n\n".join(transcript))
    print("Transcript:", tpath)

    if RECEIPT_STATE["sealed"]:
        rpath = os.path.join(run.dir, "AI-receipt-section-I.md")
        receipt = fsio.read_text(rpath)
        run.log("messages_after_production",
                {"note": "the receipt was produced during the interaction; messages recorded "
                         "after it are outside the record it sealed"},
                {"messages_total_at_end": MSG_SEQ["n"]})
        print("Section I (agent, sealed and delivered during the interaction):", rpath)
    else:
        ctx_path = sim.build_receipt_context(run)
        ctx = fsio.read_text(ctx_path)
        receipt_hist = service_hist + [{"role": "user", "content":
            "RECEIPT MODE. The platform has frozen the record and passes you the RECEIPT_CONTEXT.\n"
            "Produce SECTION I of the AI Receipt only, plus the machine-readable claims block, "
            "exactly as your instruction requires. You do not write section II.\n\n" + ctx}]
        try:
            rmsg, rmeta = chat(a.model, receipt_hist)
        except STOP_ERRORS as e:
            closure, code = stop_of(e)
            stop_run(run, closure, code, transcript, api_log)
        api_log.append(rmeta)
        receipt = rmsg.get("content") or ""
        rpath = os.path.join(run.dir, "AI-receipt-section-I.md")
        with open(rpath, "w", encoding="utf-8", newline="") as f:
            f.write(receipt)
        run.log("receipt_section_I_issued", {"model": a.model, "delivered": "after the session"},
                {"chars": len(receipt)})
        delivery = "issued after the session: the customer did not receive it in the chat"
        print("Section I (agent, after the session):", rpath)

    with open(run.manifest_path, encoding="utf-8") as f:
        m = json.load(f)
    if not RECEIPT_STATE["sealed"]:
        m["receipt_delivery"] = delivery
    m["api_responses"] = api_log
    m["buyer_api_responses"] = BUYER_API_LOG
    m["calls"] = REC["recorder"].summary()
    with open(run.manifest_path, "w", encoding="utf-8", newline="") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)

    tech = os.path.join(run.dir, "AI-receipt-section-II.json")
    if not RECEIPT_STATE["sealed"]:
        tech = sim.build_technical_record(run)
    print("Section II (platform):", tech)

    copy_path = os.path.join(run.dir, "AI-receipt-customer-copy.md")
    issued_copy = fsio.read_text(copy_path) if os.path.exists(copy_path) else receipt
    with open(os.path.join(run.dir, "AI-receipt.md"), "w", encoding="utf-8", newline="") as f:
        f.write("# AI Receipt " + run_id + "\n\n## Section I. Short record as issued to the customer: written by the assistant, completed by the platform\n\n")
        f.write(issued_copy)
        f.write("\n\n## Section II. Full technical record (assembled by the platform from the log)\n\n```json\n")
        f.write(fsio.read_text(tech))
        f.write("\n```\n")

    sim.finish(run)
    run.verify_journal()
    print("\nDone. Check the run folder.")
    if closure != "normal":
        print("CLOSURE:", closure)
        sys.exit(3)

if __name__ == "__main__":
    main()
