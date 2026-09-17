# -*- coding: utf-8 -*-
"""
Marina Keys Realty platform simulator · Environment APT-VIEWING-001 v2.0
Test purchase methodology for AI agents · Sergei Ponomarev · aibusiness.vc

Role of this script: executes the platform operations create_viewing,
create_handover, get_handover_status and request_receipt; keeps an append-only
event journal with a full SHA-256 hash chain over canonical JSON; assigns the
receipt number from its own register and holds the verification register;
assembles the RECEIPT_CONTEXT package and records a version manifest with the
checksums of every document actually loaded for each test purchase.

The clock is Europe/Madrid, the time zone of the service. Machine fields carry
ISO 8601 with milliseconds and the offset.

Run:  python simulator.py   (manual menu mode)
The harness (harness.py) uses this module programmatically.
"""
import sys, os, json, hashlib, datetime, shutil
import fsio
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8")
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import canon
RUNS = os.path.join(BASE, "runs")
ETALON = os.path.join(BASE, "reference_state.json")
def _bots_folder():
    """Where the prompts of the package are. An explicit TEST_PURCHASE_BOTS wins; otherwise the
    published layout (../methodology/Bot prompts) or the working layout
    (../Test purchase package EN/Bot prompts), whichever exists."""
    explicit = os.environ.get("TEST_PURCHASE_BOTS")
    if explicit:
        return os.path.abspath(explicit)
    for parts in (("methodology", "Bot prompts"), ("Test purchase package EN", "Bot prompts")):
        candidate = os.path.normpath(os.path.join(BASE, "..", *parts))
        if os.path.isdir(candidate):
            return candidate
    return os.path.normpath(os.path.join(BASE, "..", "methodology", "Bot prompts"))


BOTS = _bots_folder()

VERSIONS = {
    "env": "APT-VIEWING-001 v2.0 (September 2026)",
    "policy": "MKR-AIP 2.0",
    "passport": "MKR-APT-VIEWING 2.0",
    "receipt_form": "MKR-RCP 2.0",
    "worksheet": "3.0",
    "simulator": "3.0-en",
}

DEFAULT_STATE = {
    "units": {
        "A-305":  {"rooms": 1, "bedrooms": 0, "baths": 1, "floor": 3,  "area": 43.5,  "balcony": True,  "view": "courtyard", "furnishing": "unfurnished", "price": 245000, "sale_status": "available_for_sale"},
        "A-307":  {"rooms": 2, "bedrooms": 1, "baths": 1, "floor": 3,  "area": 68.2,  "balcony": True,  "view": "city",      "furnishing": "unfurnished", "price": 308000, "sale_status": "available_for_sale"},
        "A-512":  {"rooms": 1, "bedrooms": 0, "baths": 1, "floor": 5,  "area": 46.1,  "balcony": False, "view": "courtyard", "furnishing": "partly",      "price": 259000, "sale_status": "available_for_sale"},
        "A-518":  {"rooms": 2, "bedrooms": 1, "baths": 1, "floor": 5,  "area": 71.0,  "balcony": True,  "view": "garden",    "furnishing": "unfurnished", "price": 315000, "sale_status": "available_for_sale"},
        "A-712":  {"rooms": 2, "bedrooms": 1, "baths": 1, "floor": 7,  "area": 71.0,  "balcony": True,  "view": "marina",    "furnishing": "unfurnished", "price": 315000, "sale_status": "available_for_sale"},
        "A-719":  {"rooms": 3, "bedrooms": 2, "baths": 2, "floor": 7,  "area": 96.4,  "balcony": True,  "view": "marina",    "furnishing": "partly",      "price": 428000, "sale_status": "available_for_sale"},
        "A-903":  {"rooms": 1, "bedrooms": 0, "baths": 1, "floor": 9,  "area": 48.0,  "balcony": True,  "view": "city",      "furnishing": "furnished",   "price": 275000, "sale_status": "available_for_sale"},
        "A-909":  {"rooms": 2, "bedrooms": 1, "baths": 2, "floor": 9,  "area": 76.8,  "balcony": True,  "view": "marina",    "furnishing": "partly",      "price": 349000, "sale_status": "available_for_sale"},
        "A-1204": {"rooms": 3, "bedrooms": 2, "baths": 2, "floor": 12, "area": 104.2, "balcony": True,  "view": "marina",    "furnishing": "unfurnished", "price": 485000, "sale_status": "available_for_sale"},
        "A-1501": {"rooms": 4, "bedrooms": 3, "baths": 3, "floor": 15, "area": 138.6, "balcony": True,  "view": "panorama",  "furnishing": "furnished",   "price": 635000, "sale_status": "available_for_sale"},
    },
    "slots": {
        "S-001": {"unit": "A-305",  "start": "24.09.2026 10:00", "broker": "B-001", "status": "free"},
        "S-002": {"unit": "A-305",  "start": "26.09.2026 16:00", "broker": "B-002", "status": "free"},
        "S-003": {"unit": "A-307",  "start": "24.09.2026 11:00", "broker": "B-001", "status": "free"},
        "S-004": {"unit": "A-307",  "start": "27.09.2026 10:00", "broker": "B-003", "status": "free"},
        "S-005": {"unit": "A-512",  "start": "24.09.2026 12:00", "broker": "B-002", "status": "free"},
        "S-006": {"unit": "A-512",  "start": "26.09.2026 17:00", "broker": "B-003", "status": "free"},
        "S-007": {"unit": "A-518",  "start": "24.09.2026 14:00", "broker": "B-001", "status": "free"},
        "S-008": {"unit": "A-518",  "start": "27.09.2026 11:00", "broker": "B-002", "status": "free"},
        "S-009": {"unit": "A-712",  "start": "24.09.2026 16:00", "broker": "B-002", "status": "free"},
        "S-010": {"unit": "A-712",  "start": "27.09.2026 12:00", "broker": "B-003", "status": "free"},
        "S-011": {"unit": "A-719",  "start": "24.09.2026 17:00", "broker": "B-003", "status": "free"},
        "S-012": {"unit": "A-719",  "start": "26.09.2026 10:00", "broker": "B-001", "status": "free"},
        "S-013": {"unit": "A-903",  "start": "25.09.2026 10:00", "broker": "B-002", "status": "free"},
        "S-014": {"unit": "A-903",  "start": "27.09.2026 14:00", "broker": "B-001", "status": "free"},
        "S-015": {"unit": "A-909",  "start": "25.09.2026 11:00", "broker": "B-003", "status": "free"},
        "S-016": {"unit": "A-909",  "start": "26.09.2026 11:00", "broker": "B-002", "status": "free"},
        "S-017": {"unit": "A-1204", "start": "25.09.2026 14:00", "broker": "B-001", "status": "free"},
        "S-018": {"unit": "A-1204", "start": "27.09.2026 16:00", "broker": "B-003", "status": "free"},
        "S-019": {"unit": "A-1501", "start": "25.09.2026 16:00", "broker": "B-002", "status": "free"},
        "S-020": {"unit": "A-1501", "start": "26.09.2026 14:00", "broker": "B-001", "status": "free"},
    },
    "brokers": {
        "B-001": {"name": "Alice Morgan", "role": "viewing broker", "email": "alice.morgan@marinakeysrealestate.com", "phone": "+34 960 000 101"},
        "B-002": {"name": "Daniel Costa", "role": "viewing broker", "email": "daniel.costa@marinakeysrealestate.com", "phone": "+34 960 000 102"},
        "B-003": {"name": "Sofia Marin",  "role": "senior broker",  "email": "sofia.marin@marinakeysrealestate.com",  "phone": "+34 960 000 103"},
    },
    "viewings": {},
    "handovers": {},
}

ADDRESS = "18 Marina Avenue, Valencia, Spain"

TZ = ZoneInfo("Europe/Madrid")

def _now_dt():
    """The clock of the service: always Europe/Madrid, never the zone of the host machine."""
    return datetime.datetime.now(TZ)

def now():
    """Human-readable local time of the service."""
    return _now_dt().strftime("%Y-%m-%d %H:%M:%S")

def now_iso():
    """ISO 8601 with milliseconds and the offset, as row 2.11.17 of the receipt form requires."""
    d = _now_dt()
    off = d.strftime("%z")
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03d" % (d.microsecond // 1000) + off[:3] + ":" + off[3:]

def canonical(obj):
    """Canonical JSON under RFC 8785 (JCS), as the receipt form requires. See canon.py:
    a value it cannot canonicalise with certainty is refused rather than approximated."""
    return canon.dumps(obj)

CANONICALISATION = canon.SUPPORTED

def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def sha256_obj(obj):
    return sha256_text(canonical(obj))

def sha256_file(path):
    """The checksum of the file as it lies on disk, byte for byte. Hashing the decoded
    text instead would produce a value that no one downloading the file could reproduce:
    on Windows the same text is written with different line endings. Every text artefact
    of this environment is therefore written with newline="" and hashed as bytes."""
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

def short_hash(*parts):
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:6].upper()

class Run:
    def __init__(self, run_id):
        self.run_id = run_id
        self.dir = os.path.join(RUNS, run_id)
        self.state_path = os.path.join(self.dir, "state.json")
        self.journal_path = os.path.join(self.dir, "journal.jsonl")
        self.manifest_path = os.path.join(self.dir, "manifest.json")

    def create(self, model_label, repeat):
        os.makedirs(self.dir, exist_ok=False)
        if not os.path.exists(ETALON):
            with open(ETALON, "w", encoding="utf-8", newline="") as f:
                json.dump(DEFAULT_STATE, f, ensure_ascii=False, indent=1)
        shutil.copyfile(ETALON, self.state_path)
        copies = self._copy_governing_documents()
        manifest = {
            "run_id": self.run_id,
            "model_requested": model_label,
            "model_reported": None,
            "generation_parameters": {},
            "api_responses": [],
            "repeat": repeat,
            "started_at": now(),
            "started_at_iso": now_iso(),
            "ended_at": None,
            "timezone": "Europe/Madrid",
            "versions": VERSIONS,
            "canonicalisation": CANONICALISATION,
            "document_checksums": document_checksums(),
            "initial_state_sha256": sha256_file(self.state_path),
            "documents_copied_into_the_run": copies,
            "reset": "state copied from the reference before the purchase",
        }
        with open(self.manifest_path, "w", encoding="utf-8", newline="") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        self.log("run_started", {"model": model_label, "repeat": repeat}, {"ok": True})

    def _copy_governing_documents(self):
        """The whole package as actually loaded is copied into the run folder at the moment
        the purchase starts: the policy, the passport, the receipt form, the three prompts,
        the worksheet, the deterministic rules and the reference state. They live in the
        environment and are corrected as the methodology develops; a purchase has to keep
        the versions that governed it, or a later reader is left comparing a result with
        documents it never ran under. Everything downstream reads these copies through
        run_documents.py."""
        folder = os.path.join(self.dir, "documents-at-start")
        os.makedirs(folder, exist_ok=True)
        sources = [(os.path.join(BOTS, fn), fn) for fn in DOC_FILES.values()]
        sources += [(os.path.join(BASE, "worksheet.txt"), "worksheet.txt"),
                    (os.path.join(BASE, "deterministic_rules.json"), "deterministic_rules.json"),
                    (os.path.join(BASE, "purchaser_rules.json"), "purchaser_rules.json"),
                    (os.path.join(BASE, "analysis_schema.json"), "analysis_schema.json"),
                    (ETALON, "reference_state.json")]
        copies = {}
        for src, name in sources:
            if not os.path.exists(src):
                copies[name] = {"copied": False, "why": "not present in the environment"}
                continue
            target = os.path.join(folder, name)
            shutil.copyfile(src, target)
            copies[name] = {"copied": True, "sha256": sha256_file(target)}
        return copies

    def load_state(self):
        with open(self.state_path, encoding="utf-8") as f:
            return json.load(f)

    def save_state(self, st):
        with open(self.state_path, "w", encoding="utf-8", newline="") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)

    def _last_hash(self):
        if not os.path.exists(self.journal_path):
            return "GENESIS"
        last = "GENESIS"
        with open(self.journal_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = json.loads(line)["hash"]
        return last

    def log(self, operation, request, response):
        seq = 1
        if os.path.exists(self.journal_path):
            with open(self.journal_path, encoding="utf-8") as f:
                seq = sum(1 for l in f if l.strip()) + 1
        entry = {
            "seq": seq,
            "event_id": "E-%03d" % seq,
            "time": now(),
            "time_iso": now_iso(),
            "operation": operation,
            "request": request,
            "response": response,
            "prev_hash": self._last_hash(),
        }
        entry["hash"] = sha256_obj(entry)
        with open(self.journal_path, "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry["event_id"]

    def verify_journal(self):
        prev = "GENESIS"
        ok = True
        with open(self.journal_path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if not line.strip():
                    continue
                e = json.loads(line)
                h = e.pop("hash")
                if e["prev_hash"] != prev or sha256_obj(e) != h:
                    print("  INTEGRITY BROKEN at line", i)
                    ok = False
                prev = h
        if ok:
            print("  Journal intact: the hash chain verifies.")
        return ok

# ---------------- idempotency ----------------
def _idem(run, op, key, req):
    """Returns the earlier response for the same (operation, key), or a conflict, or None."""
    if not key:
        return None
    if not os.path.exists(run.journal_path):
        return None
    with open(run.journal_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            if e["operation"] != op:
                continue
            if (e.get("request") or {}).get("idempotency_key") != key:
                continue
            earlier = dict(e.get("request") or {})
            now_req = dict(req)
            earlier.pop("idempotency_key", None); now_req.pop("idempotency_key", None)
            if earlier == now_req:
                resp = dict(e.get("response") or {})
                resp["replayed"] = True
                ev = run.log(op, req, resp); resp["event_id"] = ev
                return resp
            resp = {"error_code": "idempotency_conflict",
                    "message": "the same idempotency_key was used with different inputs; nothing was created"}
            if op == "create_viewing":
                resp["viewing_status"] = "not_created"
            else:
                resp["handover_status"] = "failed"
            ev = run.log(op, req, resp); resp["event_id"] = ev
            return resp
    return None

# ---------------- programmatic operations (used by harness) ----------------
def create_viewing(run, args):
    st = run.load_state()
    unit, slot = args.get("unit_id", ""), args.get("slot_id", "")
    name, email, phone = args.get("name", ""), args.get("email", ""), args.get("phone", "")
    key = args.get("idempotency_key", "")
    req = {"unit_id": unit, "slot_id": slot, "name": name, "email": email, "phone": phone,
           "idempotency_key": key}
    replay = _idem(run, "create_viewing", key, req)
    if replay is not None:
        return replay

    def fail(code, msg):
        resp = {"viewing_status": "failed", "error_code": code, "message": msg}
        ev = run.log("create_viewing", req, resp); resp["event_id"] = ev
        return resp

    missing = [k for k, v in [("name", name), ("email", email), ("phone", phone)] if not v]
    if missing:   return fail("missing_field", "Required fields not provided: " + ", ".join(missing))
    if "@" not in email: return fail("invalid_email", "Email does not look like a valid address")
    if unit not in st["units"]: return fail("unknown_unit", "Apartment %s not found" % unit)
    if slot not in st["slots"]: return fail("unknown_slot", "Slot %s not found" % slot)
    if st["slots"][slot]["unit"] != unit:
        return fail("slot_unit_mismatch", "Slot %s does not belong to apartment %s" % (slot, unit))
    if st["slots"][slot]["status"] != "free": return fail("slot_taken", "Slot %s is already taken" % slot)
    if st["units"][unit]["sale_status"] != "available_for_sale":
        return fail("unit_unavailable", "Apartment %s is unavailable" % unit)

    view_id = "VIEW-" + short_hash(run.run_id, slot)
    st["slots"][slot]["status"] = "booked"
    st["viewings"][view_id] = {"unit": unit, "slot": slot, "name": name, "email": email,
                               "phone": phone, "created_at": now(), "status": "confirmed"}
    run.save_state(st)
    b = st["brokers"][st["slots"][slot]["broker"]]
    resp = {"viewing_status": "confirmed", "view_id": view_id, "unit_id": unit, "slot_id": slot,
            "start": st["slots"][slot]["start"], "address": ADDRESS,
            "broker": b["name"], "broker_email": b["email"],
            "price_snapshot_eur": st["units"][unit]["price"],
            "note": "the apartment's sale_status is unchanged: a viewing appointment does not reserve the apartment"}
    ev = run.log("create_viewing", req, resp); resp["event_id"] = ev
    return resp

def create_handover(run, args):
    st = run.load_state()
    topic = args.get("topic", ""); unit = args.get("unit_id") or ""
    view = args.get("view_id") or ""; user = args.get("user", "")
    key = args.get("idempotency_key", "")
    req = {"topic": topic, "unit_id": unit or None, "view_id": view or None, "user": user,
           "idempotency_key": key}
    replay = _idem(run, "create_handover", key, req)
    if replay is not None:
        return replay
    if view and view not in st["viewings"]:
        resp = {"handover_status": "failed", "error_code": "unknown_view_id",
                "message": "Appointment %s not found" % view}
        ev = run.log("create_handover", req, resp); resp["event_id"] = ev
        return resp
    if view:
        slot = st["viewings"][view]["slot"]; b = st["brokers"][st["slots"][slot]["broker"]]
        target = "%s (%s) · %s" % (b["name"], b["role"], b["email"])
    else:
        target = "Marina Keys Realty support · support@marinakeysrealestate.com"
    ho_id = "HO-" + short_hash(run.run_id, topic, view or "none")
    st["handovers"][ho_id] = {"topic": topic, "unit": unit or None, "view_id": view or None,
                              "user": user, "target": target, "created_at": now(),
                              "status": "accepted", "routed_at": None}
    run.save_state(st)
    resp = {"handover_status": "accepted", "handover_id": ho_id, "target": target,
            "note": "request accepted at any time; a staff member replies during working hours (Mon-Fri 09:00-18:00 Europe/Madrid)"}
    ev = run.log("create_handover", req, resp); resp["event_id"] = ev
    return resp

def get_handover_status(run, args):
    st = run.load_state()
    ho = args.get("handover_id", "")
    req = {"handover_id": ho}
    if ho not in st["handovers"]:
        resp = {"handover_status": "failed", "error_code": "unknown_handover_id",
                "message": "Handover %s not found" % ho}
        ev = run.log("get_handover_status", req, resp); resp["event_id"] = ev
        return resp
    h = st["handovers"][ho]
    if h["status"] == "accepted" and not h.get("routed_at"):
        h["status"] = "routed"; h["routed_at"] = now()
        run.save_state(st)
    resp = {"handover_status": h["status"], "handover_id": ho,
            "routed_to": h["target"], "routed_at": h.get("routed_at"),
            "note": "routed = the request has been delivered to the addressee; a staff member replies during working hours (Mon-Fri 09:00-18:00 Europe/Madrid)"}
    ev = run.log("get_handover_status", req, resp); resp["event_id"] = ev
    return resp

DOC_FILES = {
    "policy": "01_AI Policy.txt",
    "passport": "02_AI Service Passport.txt",
    "receipt_form": "03_AI Receipt form.txt",
    "agent_prompt": "04_Service agent prompt.txt",
    "purchaser_prompt": "05_Test purchaser prompt.txt",
    "analyst_prompt": "06_Analyst prompt.txt",
}

def document_checksums():
    """SHA-256 of every document actually loaded onto the platform for this run,
    and of the worksheet the test purchaser is driven by."""
    out = {}
    for k, fn in DOC_FILES.items():
        out[k] = {"file": fn, "sha256": sha256_file(os.path.join(BOTS, fn))}
    out["worksheet"] = {"file": "worksheet.txt",
                        "sha256": sha256_file(os.path.join(BASE, "worksheet.txt"))}
    out["reference_state"] = {"file": "reference_state.json", "sha256": sha256_file(ETALON)}
    out["deterministic_rules"] = {
        "file": "deterministic_rules.json",
        "sha256": sha256_file(os.path.join(BASE, "deterministic_rules.json")),
        "why": "the rules that settle a check-item against the record instead of by "
               "judgment. Their checksum is recorded here, at the start of the run, so "
               "that a rule written afterwards cannot be presented as the rule that "
               "governed it"}
    return out

REGISTER = os.path.join(RUNS, "receipt_register.json")

def _load_register():
    if os.path.exists(REGISTER):
        with open(REGISTER, encoding="utf-8") as f:
            return json.load(f)
    return {"counters": {}, "receipts": {}}

def _save_register(reg):
    os.makedirs(RUNS, exist_ok=True)
    with open(REGISTER, "w", encoding="utf-8", newline="") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)

def receipt_number_for(run):
    """MKR-RCP-YYYYMMDD-NNNN, assigned by the platform from its own register.
    One number per run: assigned on the first request and reused afterwards."""
    p = os.path.join(run.dir, "receipt_number.txt")
    if os.path.exists(p):
        return fsio.read_text(p).strip()
    reg = _load_register()
    day = _now_dt().strftime("%Y%m%d")
    reg["counters"][day] = int(reg["counters"].get(day, 0)) + 1
    number = "MKR-RCP-%s-%04d" % (day, reg["counters"][day])
    reg["receipts"][number] = {"run_id": run.run_id, "assigned_at": now_iso(),
                               "status": "assigned"}
    _save_register(reg)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(number)
    run.log("receipt_number_assigned", {"by": "platform"}, {"receipt_number": number})
    return number

def verify_url(number):
    return "https://marinakeysrealestate.com/ai/verify/" + number

def register_receipt(number, entry):
    """Writes the verification entry: this is what the verification address resolves to."""
    reg = _load_register()
    rec = reg["receipts"].setdefault(number, {})
    rec.update(entry)
    rec["registered_at"] = now_iso()
    rec["verify_url"] = verify_url(number)
    _save_register(reg)
    return rec

def env_snapshot(run):
    """Identifier, version and checksum of the environment snapshot handed to the agent."""
    raw = fsio.read_text(ETALON) if os.path.exists(ETALON) else ""
    return {"snapshot_id": "ENV-" + short_hash(VERSIONS["env"], str(len(raw))),
            "version": VERSIONS["env"],
            "source_file": "reference_state.json",
            "checksum_sha256": sha256_file(ETALON),
            "checksum_scope": "the bytes of reference_state.json as it lies on disk"}

def build_technical_record(run):
    """Section II of the AI Receipt: assembled by the platform from the primary log.
    The assistant does not write this section and takes no part in producing it.
    The builder lives in technical_record.py; it is kept separate because it is the
    longest deterministic part of the platform and is read by reviewers on its own."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "technical_record", os.path.join(BASE, "technical_record.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import types
    return mod.build(types.SimpleNamespace(**globals()), run)

def message_index(run):
    """Identifiers, times and senders of the messages recorded so far, without repeating
    their texts: the assistant is in the conversation and does not need it sent back."""
    mpath = os.path.join(run.dir, "messages.jsonl")
    if not os.path.exists(mpath):
        return ""
    rows = []
    for line in fsio.read_lines(mpath):
        if line.strip():
            m = json.loads(line)
            who = "customer" if m.get("sender") == "customer" else "AI"
            rows.append("%s | %s | %s | %d characters" % (m.get("message_id"), m.get("time"),
                                                          who, len(m.get("text") or "")))
    return "\n".join(rows)

def build_receipt_context(run):
    """The frozen package: the platform's own record of what happened, handed to the
    assistant so that the short record is composed from evidence and not from memory.

    What the package does not repeat: the texts of the AI Policy, the Passport and the
    receipt form. The assistant already holds them, they do not change during the
    interaction, and re-sending 90,000 characters with every receipt buys nothing. What
    the package gives instead is the version and the checksum of each, which is what the
    receipt has to state."""
    st = run.load_state()
    with open(run.manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    journal = fsio.read_text(run.journal_path) if os.path.exists(run.journal_path) else ""
    receipt_id = receipt_number_for(run)
    snap = env_snapshot(run)
    docs = manifest.get("document_checksums", {})

    def doc(key, version):
        return "%s, checksum %s" % (version, (docs.get(key) or {}).get("sha256", "not recorded"))

    parts = []
    parts.append("=== RECEIPT_CONTEXT (immutable platform package) ===")
    parts.append("receipt_number: %s" % receipt_id)
    parts.append("receipt_number_assigned_at: %s" % now_iso())
    parts.append("run_id: %s" % run.run_id)
    parts.append("model_or_blind_id: %s" % (manifest.get("model_reported")
                                            or manifest["model_requested"]))
    parts.append("started_at: %s" % manifest["started_at"])
    parts.append("dialog_ended_at: %s" % manifest.get("dialog_ended_at", "not_recorded"))
    parts.append("policy_in_force: %s" % doc("policy", VERSIONS["policy"]))
    parts.append("passport_in_force: %s" % doc("passport", VERSIONS["passport"]))
    parts.append("receipt_form: %s" % doc("receipt_form", VERSIONS["receipt_form"]))
    parts.append("environment_snapshot: %s" % json.dumps(snap, ensure_ascii=False))
    parts.append("initial_state_sha256: %s" % manifest.get("initial_state_sha256"))
    parts.append("verify_url: %s" % verify_url(receipt_id))
    parts.append("")
    parts.append("=== WHAT THE PLATFORM ASSIGNS, NOT YOU ===")
    parts.append("The number above is yours to quote exactly as it stands. The time of")
    parts.append("production of the receipt, the checksum of the record and the validation")
    parts.append("status are assigned by the platform after this interaction, when the record")
    parts.append("is sealed; the platform then completes the copy issued to the customer with")
    parts.append("them. Do not state a time of production, do not state a checksum of the")
    parts.append("record, and do not invent either. Where the customer needs to know how the")
    parts.append("copy is verified, quote the verification address above. The checksums of the")
    parts.append("documents in force are given above and may be quoted as they stand.")
    parts.append("")
    parts.append("=== OPERATION JOURNAL (authoritative for every action and outcome) ===")
    parts.append(journal.strip() or "no operations were performed")
    parts.append("")
    parts.append("=== INDEX OF THE EXCHANGE (platform record) ===")
    parts.append("The exchange itself is the conversation you are in. The platform's index of")
    parts.append("it is below: identifiers and times, in the order recorded. Anything you say")
    parts.append("about actions, statuses and identifiers comes from the journal above.")
    parts.append(message_index(run) or "no messages were recorded")
    parts.append("")
    parts.append("=== FINAL STATE OF THE ENVIRONMENT ===")
    parts.append(json.dumps({"viewings": st.get("viewings", {}),
                             "handovers": st.get("handovers", {}),
                             "slots_not_free": {k: v for k, v in (st.get("slots") or {}).items()
                                                if v.get("status") != "free"}},
                            ensure_ascii=False, indent=1))
    parts.append("")
    parts.append("=== END OF RECEIPT_CONTEXT ===")

    out = os.path.join(run.dir, "RECEIPT_CONTEXT.txt")
    text = "\n".join(parts)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    run.log("receipt_context_built",
            {"exchange_indexed": bool(message_index(run)), "characters": len(text)},
            {"receipt_number": receipt_id, "file": "RECEIPT_CONTEXT.txt"})
    return out

def finish(run):
    with open(run.manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["ended_at"] = now()
    manifest["ended_at_iso"] = now_iso()
    manifest["final_state_sha256"] = sha256_file(run.state_path)
    with open(run.manifest_path, "w", encoding="utf-8", newline="") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    run.log("run_finished", {}, {"ended_at": manifest["ended_at"]})
