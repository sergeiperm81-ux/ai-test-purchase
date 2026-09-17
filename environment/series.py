# -*- coding: utf-8 -*-
"""
The series layer: one purchase per model per day, for N days.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

What this adds on top of one purchase, and nothing else:

  plan      the models under blind labels A, B, C..., the start date, the number of days
            and the order of the models on every day, drawn in advance from a published
            seed, so that the time of day is not a hidden variable. The plan is written
            once. It carries the checksums of models.json, of every script and rules file
            of the environment, and of the NeoMundi package once one is declared. The
            mapping of labels to model names is a separate file, kept out of the
            repository, so that the plan can be published without it.
  neomundi  declares the measurement package of NeoMundi: version, schema, required keys.
            From then on a run without a valid measurement file is not frozen.
  day       runs the purchases of one day in the planned order, on the planned date; any
            other date is a diagnostic override and is recorded as such. Every purchase
            starts from the reference state. A technical failure is retried and every
            attempt is in the registry; the first complete purchase is the one that
            counts. Then, per run: validation, analysis, checker, freeze, cost, and the
            manifest of the run. Then the anchor of the day.
  week      the anchor of a week over its day anchors and the frozen matrices.
  final     the anchor of the whole series.
  status    the registry as a table.

Anchors are never overwritten. Every write produces a file named by the checksum of its
canonical payload. A day, a week or the series gets a "final" anchor exactly once, when it
is complete. Repeating the same final write is idempotent; a different final payload is
refused. Until then the anchors are provisional and say so.

Statuses of a run in the registry:
  technical_failure    the harness did not complete the purchase; retried
  analysis_failed      the purchase is complete; validation, analysis or checker failed
  pending_review       analysed; the checker blocked a position; the reviewer must record
                       a correction before the matrix can be frozen
  pending_measurement  analysed; the NeoMundi measurement file is required and missing
                       or invalid
  frozen               the matrix is frozen: the run is scored

Usage:
  python series.py plan --start 2026-09-15 --days 30 --seed 20260915 [--models models.json]
  python series.py neomundi --version V --schema <file.json> --required-keys a,b,c
  python series.py day [--day N | --date YYYY-MM-DD] [--only A,B] [--dry] [--override "reason"]
  python series.py analyse --run <run_id>          (resume checking and freezing; the
                                                     existing Analysis.json is retained)
  python series.py analyse --run <run_id> --rerun-analyst   (replace the analysis)
  python series.py week N
  python series.py final
  python series.py status
"""
import os, sys, json, time, random, hashlib, argparse, datetime, subprocess, shutil, glob, secrets
import fsio

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import providers
import usage
import neomundi_client
RUNS = os.path.join(BASE, "runs")
SERIES = os.path.join(BASE, "series")
ANCHORS = os.path.normpath(os.path.join(BASE, "..", "Frozen results", "series"))
PY = sys.executable

LABELS = "ABCDEFGHIJKL"
STOP_DAY = ("budget_exceeded", "model_drift")
COMPLETE = ("frozen", "pending_review", "pending_measurement", "analysis_failed")
# exit codes of harness.py and analyst.py: 3 is retried; 4, 5, 6 are not; 5 and 6 stop the day
HARNESS_STOPS = {3: "technical", 4: "limit", 5: "drift", 6: "budget"}
NOT_COUNTED = {"technical": "technical_failure", "limit": "limit_exceeded",
               "drift": "model_drift", "budget": "budget_exceeded"}
LIMIT_KEYS = ("max_model_calls_per_purchase", "max_tool_rounds_per_turn",
              "max_tool_calls_per_response", "max_tool_calls_per_turn",
              "max_material_operations_per_purchase", "max_call_retries", "max_analyst_attempts")

RUN_FILES = ["manifest.json", "journal.jsonl", "messages.jsonl", "transcript.md",
             "transcript-as-sealed.md", "AI-receipt-section-I.md",
             "AI-receipt-section-II.json", "AI-receipt-customer-copy.md",
             "AI-receipt-archive.json", "purchaser_fidelity.json", "validation_report.json",
             "Analysis.json", "Analysis.md", "analysis_check.json", "analyst_usage.jsonl",
             "cost.json",
             "matrix_corrections.json", "Matrix-final.json", "Matrix-final.md",
             "freeze_manifest.json", "state.json", "receipt_number.txt",
             "approvals-at-start.json"]


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def sha256_obj(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def jdump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def jload(path):
    return fsio.read_json(path)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def code_versions():
    """The checksum of every script and rules file of the environment: the code a series
    ran under is part of what the plan pins."""
    out = {}
    for pattern in ("*.py", "*.json", "worksheet.txt"):
        for p in sorted(glob.glob(os.path.join(BASE, pattern))):
            if os.path.basename(p) == "models.json":
                continue
            out[os.path.basename(p)] = sha256_file(p)
    return out


def snapshot_code(folder):
    """Keep the exact code and data files whose checksums the plan records."""
    target = os.path.join(folder, "code-at-start")
    os.makedirs(target, exist_ok=False)
    out = {}
    for pattern in ("*.py", "*.json", "worksheet.txt"):
        for source in sorted(glob.glob(os.path.join(BASE, pattern))):
            name = os.path.basename(source)
            if name == "models.json":
                continue
            dest = os.path.join(target, name)
            shutil.copyfile(source, dest)
            out[name] = sha256_file(dest)
    return out


def verify_plan_code(pl):
    """A plan that pins code is meaningful only if a day refuses changed code."""
    expected = pl.get("code_versions") or {}
    actual = code_versions()
    changed = sorted(name for name in set(expected) | set(actual)
                     if expected.get(name) != actual.get(name))
    if changed:
        raise SystemExit("the execution environment no longer matches the code snapshot "
                         "pinned by the series plan: %s. Start a new plan (or deliberately "
                         "version the series); do not mix harness versions inside one series"
                         % ", ".join(changed))


def model_configuration_commitment(path, nonce):
    with open(path, "rb") as f:
        raw = f.read()
    return hashlib.sha256(nonce.encode("ascii") + b"\0" + raw).hexdigest()


def verify_model_configuration(folder, pl):
    """The private label mapping and provider configuration are immutable after plan."""
    key_path = os.path.join(folder, "model_key.json")
    models_path = os.path.join(folder, "models.json")
    if sha256_file(key_path) != pl.get("model_key_sha256"):
        raise SystemExit("the private model-key file no longer matches the plan commitment")
    key = jload(key_path)
    nonce = key.get("commitment_nonce")
    if not nonce or model_configuration_commitment(models_path, nonce) != \
            pl.get("models_configuration_commitment_sha256"):
        raise SystemExit("the frozen provider/model configuration no longer matches the "
                         "salted commitment in the plan")


# ------------------------------------------------------------------ anchors

def write_anchor(folder, base, obj, final):
    """Write a content-addressed payload and, when complete, one immutable final alias."""
    body = dict(obj)
    final_paths = [os.path.join(root, "%s.final.json" % base)
                   for root in (folder, ANCHORS)]

    def logical(record):
        return {k: v for k, v in record.items()
                if k not in ("state", "written_at", "payload_sha256",
                             "payload_checksum_scope")}

    def verified(record, path):
        payload = {k: v for k, v in record.items()
                   if k not in ("payload_sha256", "payload_checksum_scope")}
        actual = sha256_obj(payload)
        if record.get("payload_sha256") != actual:
            raise SystemExit("anchor %s has an invalid payload checksum" % path)
        return actual

    if final:
        existing = [(p, jload(p)) for p in final_paths if os.path.exists(p)]
        if existing:
            digests = {verified(record, path) for path, record in existing}
            if len(digests) != 1 or any(logical(record) != body for _, record in existing):
                raise SystemExit("final anchor %s already exists with a different payload; "
                                 "a final result is not rewritten" % base)
            record = existing[0][1]
            digest = next(iter(digests))
            name = "%s.%s.json" % (base, digest[:12])
            for root in (folder, ANCHORS):
                hashed = os.path.join(root, name)
                alias = os.path.join(root, "%s.final.json" % base)
                if not os.path.exists(hashed):
                    jdump(hashed, record)
                if not os.path.exists(alias):
                    jdump(alias, record)
            print("  final anchor %s already exists with the same payload: %s"
                  % (base, digest[:12]))
            return digest, []

    payload = dict(body)
    payload["state"] = "final" if final else "provisional"
    payload["written_at"] = now()
    digest = sha256_obj(payload)
    record = dict(payload)
    record["payload_sha256"] = digest
    record["payload_checksum_scope"] = ("SHA-256 of the canonical JSON payload excluding "
                                         "payload_sha256 and payload_checksum_scope")
    name = "%s.%s.json" % (base, digest[:12])
    written = []
    for root in (folder, ANCHORS):
        p = os.path.join(root, name)
        if not os.path.exists(p):
            jdump(p, record)
            written.append(p)
    if final:
        for p in final_paths:
            jdump(p, record)
            written.append(p)
    print("  %s anchor %s: %s" % ("final" if final else "provisional", base, digest[:12]))
    return digest, written


# ------------------------------------------------------------------ the plan

def current_series():
    p = os.path.join(SERIES, "CURRENT")
    if not os.path.exists(p):
        raise SystemExit("no series planned: run series.py plan first")
    sid = fsio.read_text(p).strip()
    return sid, os.path.join(SERIES, sid)


def validate_configuration(models, configurations):
    """What a series may not start without: an adapter, an output limit, a rate and a pinned
    served model for every configuration, the analyst's included, and the hard limits."""
    required = ("configuration_id", "model", "provider", "protocol", "base_url", "key_env",
                "rate_key", "max_output_tokens")
    ids = [cfg.get("configuration_id") for cfg in configurations]
    if len(set(ids)) != len(ids):
        raise SystemExit("configuration_id values are not unique")
    for cfg in configurations:
        missing = [k for k in required if not cfg.get(k)]
        if missing:
            raise SystemExit("model configuration %s lacks %s"
                             % (cfg.get("model", "(unnamed)"), ", ".join(missing)))
        if cfg["protocol"] not in providers.PROTOCOLS:
            raise SystemExit("model %s uses protocol %s, for which providers.py has no "
                             "adapter. Add and test the adapter before planning the series"
                             % (cfg["model"], cfg["protocol"]))
        if usage.rate(cfg["rate_key"]) is None:
            raise SystemExit("configuration %s: rate_key %s has no rate in rates.json, so the "
                             "spend ceiling cannot be enforced" % (cfg["configuration_id"], cfg["rate_key"]))
        if "{product_id}" in cfg["base_url"] and not cfg.get("product_id_env"):
            raise SystemExit("model %s has {product_id} in its endpoint and no product_id_env"
                             % cfg["model"])
        if not cfg.get("expected_observed_models"):
            raise SystemExit("configuration %s has no expected_observed_models. Pin the model ids "
                             "the provider reports (from the technical run) before planning: a "
                             "change of the served model, the analyst's included, must stop the "
                             "series" % cfg["configuration_id"])
    lim = models.get("limits") or {}
    for k in LIMIT_KEYS:
        if not isinstance(lim.get(k), int):
            raise SystemExit("limits.%s is not set in the models file" % k)
    budget = lim.get("budget") or {}
    if not budget.get("per_utc_day") or not budget.get("per_scope"):
        raise SystemExit("limits.budget needs per_utc_day and per_scope ceilings")


def in_window(window, when=None):
    """Whether the UTC time is inside "HH:MM-HH:MM"."""
    start, end = [datetime.time.fromisoformat(x) for x in window.split("-")]
    t = (when or datetime.datetime.now(datetime.timezone.utc)).time()
    return start <= t < end


def plan(a):
    models = jload(a.models)
    names = [m["model"] for m in models["models"]]
    if not 1 <= len(names) <= len(LABELS):
        raise SystemExit("between 1 and %d models, %d given" % (len(LABELS), len(names)))
    if len(set(names)) != len(names):
        raise SystemExit("the tested model list contains duplicates: every blind label must "
                         "identify a different configured model")
    configurations = list(models.get("models", [])) + list(models.get("auxiliary_models", []))
    validate_configuration(models, configurations)
    labels = list(LABELS[:len(names)])
    start = datetime.date.fromisoformat(a.start)
    technical = bool(getattr(a, "technical", False))
    sid = "%s-%s-%dx%d" % ("TECH" if technical else "S", start.strftime("%Y%m%d"), len(names), a.days)
    folder = os.path.join(SERIES, sid)
    if os.path.exists(folder):
        raise SystemExit("series %s already planned; a plan is written once" % sid)
    rng = random.Random(a.seed)
    days = []
    for d in range(1, a.days + 1):
        order = labels[:]
        rng.shuffle(order)
        days.append({"day": d, "date": (start + datetime.timedelta(days=d - 1)).isoformat(),
                     "order": order})
    nonce = secrets.token_hex(32)
    key = {"what_this_is": "the mapping of the blind labels of the series to the model "
                           "names and providers. Kept apart from the plan and out of the "
                           "repository (.gitignore); its salted commitment is in the plan",
           "series": sid,
           "commitment_nonce": nonce,
           "labels": {lab: {"model": m["model"], "provider": m.get("provider")}
                      for lab, m in zip(labels, models["models"])}}
    key_path = os.path.join(folder, "model_key.json")
    jdump(key_path, key)
    shutil.copyfile(a.models, os.path.join(folder, "models.json"))
    frozen_code = snapshot_code(folder)
    analyst = ((models.get("roles") or {}).get("analyst") or {}).get("model")
    if a.window_utc:
        in_window(a.window_utc)   # refuses a malformed window before anything is written
    if analyst not in {c["model"] for c in configurations}:
        raise SystemExit("the analyst model %s has no frozen provider configuration. Put it "
                         "in models or auxiliary_models" % analyst)
    pl = {"what_this_is": "the plan of the series: labels, days and the order of the "
                          "models on every day, drawn before day 1 from the seed below. "
                          "Written once; nothing here changes after day 1 except the "
                          "declaration of the NeoMundi package, which must precede day 1",
          "series": sid, "start": a.start, "days": a.days, "models": len(names),
          "labels": labels, "seed": a.seed,
          "order_rule": "random.Random(seed).shuffle(labels) once per day, in day order",
          "purchaser": "scripted (purchaser.py, purchaser_rules.json); no model",
          "analyst_model": analyst,
          "analyst_blinded": True,
          "one_counted_run_per_day_and_model": True,
          "runs_only_on_the_planned_date": True,
          "retry_rule": "a failed provider call is repeated by the harness itself, that call "
                        "only, up to limits.max_call_retries times; a complete answer is never "
                        "asked for again. A purchase that still cannot complete stays a "
                        "technical_failure. It is repeated only by an operator (series.py retry), "
                        "with a reason, a reference to the failed run and a recorded approval of "
                        "the spend; never automatically. A limit, the spend ceiling or a change "
                        "of the served model is not repeated; the last two stop the day",
          "window_utc": a.window_utc,
          "limits": models.get("limits"),
          "counted": not technical,
          "purpose": ("technical rehearsal: DIAGNOSTIC, NOT COUNTED. Every purchase is run, "
                      "analysed, checked and frozen exactly as in a counted series, and every "
                      "one is marked so" if technical else "the counted series"),
          "model_key_sha256": sha256_file(key_path),
          "models_configuration_commitment_sha256":
              model_configuration_commitment(os.path.join(folder, "models.json"), nonce),
          "model_commitment_note": "the commitment is SHA-256(nonce + NUL + exact bytes "
                                   "of models.json). The random nonce is kept only in "
                                   "model_key.json until labels are revealed, so the "
                                   "mapping cannot be recovered by permuting a known list",
          "code_versions": frozen_code,
          "code_snapshot": "code-at-start",
          "neomundi": {"required": False, "package_version": None, "schema_sha256": None,
                       "schema_object_sha256": None, "schema": None,
                       "required_keys": [], "declared_at": None,
                       "note": "no measurement package declared yet; declare it with "
                               "series.py neomundi before day 1"},
          "planned_at": now(),
          "schedule": days}
    plan_path = os.path.join(folder, "plan.json")
    jdump(plan_path, pl)
    if technical:
        with open(os.path.join(folder, "DIAGNOSTIC.md"), "w", encoding="utf-8", newline="") as f:
            f.write("# DIAGNOSTIC / NOT COUNTED\n\nSeries %s is a technical rehearsal. Its purchases "
                    "exercise the whole pipeline and are frozen like counted ones, so that the "
                    "pipeline is tested to the end, but none of them is a result of the pilot.\n" % sid)
    with open(os.path.join(SERIES, "CURRENT"), "w", encoding="utf-8", newline="") as f:
        f.write(sid)
    print("series  :", sid)
    print("plan    :", plan_path, "sha256", sha256_file(plan_path))
    print("key     :", key_path, "(not for publication; ignored by git)")
    for d in days[:3]:
        print("  day %02d %s order %s" % (d["day"], d["date"], " ".join(d["order"])))
    print("  ...")


def neomundi(a):
    sid, folder = current_series()
    pl = jload(os.path.join(folder, "plan.json"))
    if registry(folder):
        raise SystemExit("the series has started: the measurement package is declared "
                         "before day 1, not during the series")
    import jsonschema
    schema = jload(a.schema)
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    schema_copy = os.path.join(folder, "neomundi_schema.json")
    shutil.copyfile(a.schema, schema_copy)
    schema_sha = sha256_file(schema_copy)
    cfg = dict(jload(os.path.join(folder, "models.json")).get("neomundi") or {}, enabled=True)
    cfg["base_url"] = a.base_url
    cfg["observe_path"] = a.observe_path
    cfg["contract_path"] = a.contract_path
    cfg["identifiers_field"] = a.identifiers_field or None
    problems = neomundi_config_problems(cfg)
    if problems:
        raise SystemExit("the NeoMundi configuration cannot be declared: " + "; ".join(problems))
    cfg_path = os.path.join(folder, "neomundi-config.json")
    jdump(cfg_path, cfg)
    pl["neomundi"] = {"required": True, "package_version": a.version,
                      "config_file": "neomundi-config.json",
                      "config_sha256": sha256_file(cfg_path),
                       "schema_sha256": schema_sha,
                       "schema_object_sha256": sha256_obj(schema),
                       "schema": schema,
                      "required_keys": [k for k in (a.required_keys or "").split(",") if k],
                      "declared_at": now(),
                      "note": "a run whose neomundi/ folder holds no valid measurement "
                              "file is not frozen: pending_measurement"}
    jdump(os.path.join(folder, "plan.json"), pl)
    print("NeoMundi package declared in the plan:", pl["neomundi"])


def anchor_plan(folder, sid):
    """The plan is anchored at the first day, when it can no longer change."""
    pl = jload(os.path.join(folder, "plan.json"))
    write_anchor(folder, "%s.plan" % sid, pl, final=True)


# ------------------------------------------------------------------ the registry

def registry_path(folder):
    return os.path.join(folder, "registry.jsonl")


def registry(folder):
    p = registry_path(folder)
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in fsio.read_lines(p) if l.strip()]


def record(folder, entry):
    entry = dict(entry)
    entry["recorded_at"] = now()
    with open(registry_path(folder), "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def latest(folder, day, label):
    """The last registry entry of a complete purchase for that day and label."""
    out = None
    for e in registry(folder):
        if e["day"] == day and e["label"] == label and e["status"] in COMPLETE:
            out = e
    return out


# ------------------------------------------------------------------ one purchase

def run_cmd(args, timeout=1800, extra_env=None):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    p = subprocess.run([PY] + args, cwd=BASE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return p.returncode, p.stdout, p.stderr


def purchase(model, tag, models_file, ids):
    """One attempt. Returns (run_id or None, kind, note); kind is complete, technical,
    limit, drift or budget. A run folder that was started is kept and returned in every
    case, so that the next attempt can reference it."""
    env = {"TEST_PURCHASE_MODELS_FILE": models_file}
    env.update(ids)
    code, out, err = run_cmd(["harness.py", "--model", model, "--buyer", "scripted",
                              "--tag", tag], extra_env=env)
    run_id = None
    for line in out.splitlines():
        if line.startswith("Run:"):
            run_id = line.split(":", 1)[1].strip()
    if code == 0 and run_id:
        return run_id, "complete", "complete"
    tail = (err or out).strip().splitlines()[-3:]
    return run_id, HARNESS_STOPS.get(code, "technical"), "harness exit %d: %s" % (code, " | ".join(tail))


def measurement(run_dir, pl):
    """The NeoMundi observations of a run, checked one to one against its completed agent
    calls and against the schema the plan declares. Returns (ok, detail)."""
    req = pl.get("neomundi") or {}
    linked, link_detail = neomundi_client.verify_links(run_dir)
    if not req.get("required"):
        return True, {"required": False, "completed_calls": link_detail["completed_calls"],
                      "observed": link_detail["observed"], "problems": link_detail["problems"]}
    if not linked:
        return False, {"required": True,
                       "why": "; ".join(link_detail["problems"][:5]) or "no completed agent call"}
    frozen = os.path.join(run_dir, "neomundi", "config-at-start.json")
    if not os.path.exists(frozen) or sha256_file(frozen) != req.get("config_sha256"):
        return False, {"required": True, "why": "the run was not observed under the NeoMundi "
                                                "configuration declared in the plan"}
    files = [os.path.join(run_dir, f) for f in sorted(link_detail["files"])]
    schema = req.get("schema")
    if not isinstance(schema, dict) or sha256_obj(schema) != req.get("schema_object_sha256"):
        return False, {"required": True, "why": "the plan has no intact NeoMundi schema"}
    import jsonschema
    validator_class = jsonschema.validators.validator_for(schema)
    validator = validator_class(schema)
    out = {"required": True, "files": {}, "package_version": req.get("package_version"),
           "schema_sha256": req.get("schema_sha256")}
    for f in files:
        try:
            data = fsio.read_json(f)
        except Exception as e:
            return False, {"required": True, "why": "not valid JSON: %s" % os.path.basename(f)}
        missing = [k for k in req.get("required_keys", []) if k not in data]
        if missing:
            return False, {"required": True, "why": "%s lacks %s" % (os.path.basename(f), missing)}
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
        if errors:
            e = errors[0]
            where = "/".join(str(x) for x in e.absolute_path) or "(root)"
            return False, {"required": True,
                           "why": "%s fails the declared schema at %s: %s"
                                  % (os.path.basename(f), where, e.message[:180])}
        out["files"][os.path.relpath(f, run_dir).replace(os.sep, "/")] = sha256_file(f)
    out["completed_calls"] = link_detail["completed_calls"]
    out["observed"] = link_detail["observed"]
    return True, out


def analyse(run_id, pl, run_analyst=True):
    """Validation, analysis, checker, freeze, cost. Returns (status, notes)."""
    run_dir = os.path.join(RUNS, run_id)
    notes = []
    # exit 2 from the analyst or the checker means the report is not in a shape that can
    # be checked: that is a defect of the report, not a failure of the tool, and the run
    # waits for review rather than being written off as a technical failure
    steps = [["validate_receipt.py", run_dir]]
    if run_analyst:
        steps.append(["analyst.py", "--run", run_id, "--model", pl["analyst_model"]])
    steps.append(["check_analysis.py", run_dir])
    series_models = os.path.join(SERIES, pl["series"], "models.json")
    for s in steps:
        code, out, err = run_cmd(
            s, extra_env={"TEST_PURCHASE_MODELS_FILE": series_models,
                          "TEST_PURCHASE_BUDGET_SCOPE": pl["series"]})
        if code == 2:
            notes.append("%s: the analysis is not in a checkable shape: %s"
                         % (s[0], (err or out).strip().splitlines()[-1][:200]))
            return "pending_review", notes
        if s[0] == "analyst.py" and code in (4, 5, 6):
            last = ((err or out).strip().splitlines() or [""])[-1]
            notes.append("analyst stopped: %s" % last[:200])
            return NOT_COUNTED[HARNESS_STOPS[code]], notes
        if code != 0:
            notes.append("%s failed: %s" % (s[0], (err or out).strip().splitlines()[-1:]))
            return "analysis_failed", notes
    corr = os.path.join(run_dir, "matrix_corrections.json")
    if not os.path.exists(corr):
        jdump(corr, {"run_id": run_id,
                     "decided_on": datetime.date.today().isoformat(),
                     "reviewer": "automatic freeze of the series: no reviewer correction "
                                 "recorded. A position the checker leaves blocked stays "
                                 "pending until the reviewer records one",
                     "corrections": []})
    ok, detail = measurement(run_dir, pl)
    if not ok:
        notes.append("measurement: " + detail.get("why", "missing"))
        return "pending_measurement", notes
    if pl.get("counted") is False:
        # written before the freeze, so that the freeze pins the mark itself
        status_path = os.path.join(run_dir, "RUN_STATUS.md")
        if not os.path.exists(status_path):
            with open(status_path, "w", encoding="utf-8", newline="") as f:
                f.write("# RUN_STATUS: DIAGNOSTIC / NOT COUNTED\n\nRun %s belongs to the technical "
                        "rehearsal %s. It is analysed, checked and frozen like a counted purchase, "
                        "and it is not a result of the pilot.\n" % (run_id, pl["series"]))
    code, out, err = run_cmd(["freeze_matrix.py", run_dir, "--no-anchor"])
    run_cmd(["cost.py", run_dir])
    if code != 0:
        notes.append("freeze pending: " + (err or out).strip().splitlines()[-1][:200])
        return "pending_review", notes
    notes.append("frozen")
    return "frozen", notes


def run_manifest(sid, day, label, model, run_id, status, pl, override):
    """The manifest of one run: the checksums of everything the run holds, the NeoMundi
    measurement as a linked artifact, and the status."""
    run_dir = os.path.join(RUNS, run_id)
    files = {}
    for name in RUN_FILES:
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            files[name] = sha256_file(p)
    for p in sorted(glob.glob(os.path.join(run_dir, "analysis-input.*.txt"))):
        files[os.path.basename(p)] = sha256_file(p)
    for folder in ("documents-at-start", "calls", "neomundi"):
        for root, dirs, names in os.walk(os.path.join(run_dir, folder)):
            dirs.sort()
            for name in sorted(names):
                full = os.path.join(root, name)
                files[os.path.relpath(full, run_dir).replace(os.sep, "/")] = sha256_file(full)
    m = jload(os.path.join(run_dir, "manifest.json"))
    prov = m.get("provider")
    ok, detail = measurement(run_dir, pl)
    out = {"series": sid, "day": day, "label": label, "run_id": run_id,
           "counted": pl.get("counted", True),
           "model_requested": model, "model_reported": m.get("model_reported"),
           "provider": prov.get("provider") if isinstance(prov, dict) else prov,
           "status": status,
           "diagnostic_override": override,
           "neomundi_measurement": {"valid": ok, **detail,
                                    "role": "a separate linked measurement artifact; "
                                            "not part of the verdict"},
           "files": files,
           "written_at": now()}
    jdump(os.path.join(run_dir, "run_manifest.json"), out)
    return out


# ------------------------------------------------------------------ a day

def date_override(d, reason):
    """None on the planned date; otherwise the recorded diagnostic override, or a refusal."""
    today = datetime.date.today().isoformat()
    if d["date"] == today:
        return None
    if not reason:
        raise SystemExit("day %02d is planned for %s and today is %s. A day runs on its "
                         "date; to run it anyway for a diagnostic reason, pass "
                         "--override \"reason\", which is recorded" % (d["day"], d["date"], today))
    return {"reason": reason, "planned_date": d["date"], "actual_date": today}


def neomundi_config_problems(cfg):
    """What a NeoMundi configuration must hold to be declared or used."""
    problems = []
    if cfg.get("enabled") is not True:
        problems.append("observations are not enabled")
    base = cfg.get("base_url") or ""
    if not (base.startswith("https://") or base.startswith("http://127.0.0.1")):
        problems.append("the base URL is not https")
    if not (cfg.get("observe_path") or "").startswith("/"):
        problems.append("observe_path is not set")
    for k in ("key_env", "mode"):
        if not isinstance(cfg.get(k), str) or not cfg.get(k):
            problems.append("%s is not set" % k)
    if not isinstance(cfg.get("observe_roles"), list) or not cfg.get("observe_roles"):
        problems.append("observe_roles is empty")
    if not isinstance(cfg.get("max_attempts"), int) or cfg["max_attempts"] < 1:
        problems.append("max_attempts is not a positive integer")
    for k in ("max_requests_per_scope", "max_requests_per_purchase"):
        if not isinstance(cfg.get(k), int) or cfg[k] < 1:
            problems.append("%s is not a positive integer: every outgoing request must be capped" % k)
    return problems


def neomundi_ready(folder, pl):
    """[] when the plan does not require the measurement, or when the declared configuration
    is intact, enabled, complete, has its key and its schema; the problems otherwise."""
    req = pl.get("neomundi") or {}
    if not req.get("required"):
        return []
    path = os.path.join(folder, req.get("config_file") or "neomundi-config.json")
    if not os.path.exists(path):
        return ["the declared NeoMundi configuration file is missing"]
    if sha256_file(path) != req.get("config_sha256"):
        return ["the NeoMundi configuration no longer matches the checksum declared in the plan"]
    cfg = jload(path)
    problems = neomundi_config_problems(cfg)
    if not providers.key_present(cfg.get("key_env") or ""):
        problems.append("%s is absent" % cfg.get("key_env"))
    schema = req.get("schema")
    if not isinstance(schema, dict) or sha256_obj(schema) != req.get("schema_object_sha256"):
        problems.append("the declared NeoMundi schema is not intact")
    return problems


def preflight(folder, pl):
    models_path = os.path.join(folder, "models.json")
    code, out, err = run_cmd(["secrets_check.py", models_path],
                             extra_env={"TEST_PURCHASE_MODELS_FILE": models_path})
    if code != 0:
        raise SystemExit("API-key pre-flight failed before any purchase:\n" + (out or err))
    problems = neomundi_ready(folder, pl)
    if problems:
        raise SystemExit("NeoMundi pre-flight failed before any purchase: " + "; ".join(problems))


def attempt_purchase(folder, sid, pl, d, label, model, override, supersedes=None, retry=None):
    """Exactly one purchase attempt, then its analysis if it completed. Nothing here repeats
    a purchase. Returns (status, run_id, note)."""
    tag = "D%02d-%s" % (d["day"], label)
    attempt = 1 + sum(1 for e in registry(folder)
                      if e["day"] == d["day"] and e["label"] == label and e.get("attempt"))
    started = now()
    print("  %s -> %s, attempt %d%s" % (label, model, attempt,
                                        " (operator retry of %s)" % supersedes if retry else ""), flush=True)
    ids = {"TEST_PURCHASE_SERIES_ID": sid, "TEST_PURCHASE_DAY_ID": "D%02d" % d["day"],
           "TEST_PURCHASE_PURCHASE_ID": "%s.%s" % (sid, tag), "TEST_PURCHASE_BUDGET_SCOPE": sid}
    if supersedes:
        ids["TEST_PURCHASE_SUPERSEDES"] = supersedes
    if (pl.get("neomundi") or {}).get("required"):
        ids["TEST_PURCHASE_NEOMUNDI_CONFIG"] = os.path.join(folder, pl["neomundi"]["config_file"])
    run_id, kind, note = purchase(model, tag, os.path.join(folder, "models.json"), ids)
    entry = {"series": sid, "day": d["day"], "date": d["date"], "label": label,
             "attempt": attempt, "run_id": run_id, "supersedes": supersedes, "retry": retry,
             "started_at": started, "override": override}
    if kind != "complete":
        record(folder, dict(entry, status=NOT_COUNTED[kind], note=note))
        print("     %s: %s" % (NOT_COUNTED[kind], note[:160]))
        return NOT_COUNTED[kind], run_id, note
    status, notes = analyse(run_id, pl)
    rm = run_manifest(sid, d["day"], label, model, run_id, status, pl, override)
    record(folder, dict(entry, status=status, model_reported=rm["model_reported"],
                        note="; ".join(notes)))
    print("     %s: %s (%s)" % (run_id, status, "; ".join(notes)))
    return status, run_id, "; ".join(notes)


def retry(a):
    """The operator's repeat of a purchase that ended as a technical failure: one new
    attempt, with its reason, a reference to the failed run and the recorded approval of the
    spend."""
    sid, folder = current_series()
    pl = jload(os.path.join(folder, "plan.json"))
    verify_plan_code(pl)
    verify_model_configuration(folder, pl)
    reg = registry(folder)
    prior = next((e for e in reversed(reg) if e.get("run_id") == a.run), None)
    if not prior:
        raise SystemExit("run %s is not in the current series registry" % a.run)
    if prior["status"] != "technical_failure":
        raise SystemExit("only a technical failure is repeated; %s is %s" % (a.run, prior["status"]))
    if latest(folder, prior["day"], prior["label"]):
        raise SystemExit("day %02d label %s already has a complete purchase" % (prior["day"], prior["label"]))
    last = [e for e in reg if e["day"] == prior["day"] and e["label"] == prior["label"] and e.get("attempt")]
    if last and last[-1].get("run_id") != a.run:
        raise SystemExit("%s is not the latest attempt of day %02d label %s"
                         % (a.run, prior["day"], prior["label"]))
    d = next(x for x in pl["schedule"] if x["day"] == prior["day"])
    override = date_override(d, a.override)
    window = pl.get("window_utc")
    if window and not override and not in_window(window):
        raise SystemExit("the UTC window %s of the plan is closed" % window)
    preflight(folder, pl)
    model = jload(os.path.join(folder, "model_key.json"))["labels"][prior["label"]]["model"]
    decision = {"reason": a.reason, "spend_approved_by": a.approved_by,
                "approved_estimate": a.approved_estimate, "requested_at": now()}
    attempt_purchase(folder, sid, pl, d, prior["label"], model, override,
                     supersedes=a.run, retry=decision)
    day_anchor(sid, folder, pl, d)


def day(a):
    sid, folder = current_series()
    pl = jload(os.path.join(folder, "plan.json"))
    verify_plan_code(pl)
    verify_model_configuration(folder, pl)
    key = jload(os.path.join(folder, "model_key.json"))["labels"]
    if a.day:
        d = next((x for x in pl["schedule"] if x["day"] == a.day), None)
    else:
        date = a.date or datetime.date.today().isoformat()
        d = next((x for x in pl["schedule"] if x["date"] == date), None)
    if not d:
        raise SystemExit("no such day in the plan")
    override = date_override(d, a.override)
    if not a.dry:
        preflight(folder, pl)
    only = set(a.only.split(",")) if a.only else None
    print("series %s, day %02d (%s), order %s%s" % (sid, d["day"], d["date"], " ".join(d["order"]),
          " [DIAGNOSTIC OVERRIDE: %s]" % a.override if override else ""))
    if not a.dry and not registry(folder):
        anchor_plan(folder, sid)
    window = pl.get("window_utc")
    stop_day = None
    for label in d["order"]:
        if only and label not in only:
            continue
        model = key[label]["model"]
        if latest(folder, d["day"], label):
            print("  %s already has a complete purchase, skipped" % label)
            continue
        if any(e["day"] == d["day"] and e["label"] == label and e.get("attempt")
               for e in registry(folder)):
            print("  %s already has an attempt on this day; a new one only through "
                  "series.py retry" % label)
            continue
        if a.dry:
            print("  %s -> %s (dry run, nothing executed)" % (label, model))
            continue
        if not stop_day and window and not override and not in_window(window):
            stop_day = "the UTC window %s of the plan is closed" % window
        if stop_day:
            record(folder, {"series": sid, "day": d["day"], "date": d["date"], "label": label,
                            "attempt": None, "run_id": None, "status": "not_started",
                            "started_at": now(), "override": override, "note": stop_day})
            print("  %s not started: %s" % (label, stop_day))
            continue
        status, run_id, note = attempt_purchase(folder, sid, pl, d, label, model, override)
        if status in STOP_DAY:
            stop_day = "%s at label %s: %s" % (status, label, note[:160])
    if not a.dry:
        day_anchor(sid, folder, pl, d)


def day_anchor(sid, folder, pl, d):
    """The anchor of the day pins the run manifest of every complete purchase of the day
    and lists every attempt. It is final when every label of the day has a complete
    purchase. It is final only when every purchase is frozen; pending analysis,
    measurement or review must remain capable of producing a later provisional anchor."""
    runs, attempts = {}, []
    for e in registry(folder):
        if e["day"] != d["day"]:
            continue
        attempts.append({k: e.get(k) for k in ("label", "attempt", "run_id", "status",
                                               "override", "note")})
        if e["status"] in COMPLETE:
            rm = os.path.join(RUNS, e["run_id"], "run_manifest.json")
            if os.path.exists(rm):
                runs[e["label"]] = {"run_id": e["run_id"], "status": e["status"],
                                    "run_manifest_sha256": sha256_file(rm)}
    missing = [l for l in d["order"] if l not in runs]
    pending = [l for l in d["order"]
               if l in runs and runs[l]["status"] != "frozen"]
    out = {"what_this_is": "the anchor of one day of the series: it pins the manifest of "
                           "every complete purchase of the day and lists every attempt. "
                           "The matrices are pinned by the week anchor, once the reviewer "
                           "has settled any blocked position",
           "series": sid, "day": d["day"], "date": d["date"], "order": d["order"],
           "runs": runs, "labels_without_a_complete_purchase": missing,
           "labels_with_an_unfrozen_purchase": pending, "attempts": attempts}
    write_anchor(os.path.join(folder, "days"), "%s.D%02d" % (sid, d["day"]), out,
                 final=not missing and not pending)
    if missing:
        print("  labels without a complete purchase:", " ".join(missing))
    if pending:
        print("  labels with an unfrozen purchase:", " ".join(pending))


# ------------------------------------------------------------------ weeks and the end

def final_anchor(folder_sub, base):
    p = os.path.join(folder_sub, base + ".final.json")
    return jload(p) if os.path.exists(p) else None


def week(a):
    sid, folder = current_series()
    pl = jload(os.path.join(folder, "plan.json"))
    days = [x for x in pl["schedule"] if (x["day"] - 1) // 7 == a.week - 1]
    if not days:
        raise SystemExit("no such week")
    anchors, matrices, pending, complete = {}, {}, [], True
    for d in days:
        base = "%s.D%02d" % (sid, d["day"])
        fa = final_anchor(os.path.join(folder, "days"), base)
        if not fa:
            anchors[base] = "no final day anchor"
            complete = False
            continue
        anchors[base] = fa["payload_sha256"]
        for label, r in fa["runs"].items():
            fm = os.path.join(RUNS, r["run_id"], "freeze_manifest.json")
            if os.path.exists(fm):
                matrices[r["run_id"]] = sha256_file(fm)
            else:
                pending.append(r["run_id"])
                complete = False
    out = {"what_this_is": "the anchor of one week: the checksums of its final day anchors "
                           "and of the freeze manifests of its runs. A run without a "
                           "frozen matrix is listed as pending",
           "series": sid, "week": a.week, "days": [d["day"] for d in days],
           "day_anchors": anchors, "frozen_matrices": matrices, "pending": pending}
    write_anchor(os.path.join(folder, "weeks"), "%s.W%d" % (sid, a.week), out, final=complete)
    print("  frozen matrices:", len(matrices), "| pending:", len(pending))


def final(a):
    sid, folder = current_series()
    pl = jload(os.path.join(folder, "plan.json"))
    n_weeks = (pl["days"] + 6) // 7
    weeks, complete = {}, True
    for w in range(1, n_weeks + 1):
        fa = final_anchor(os.path.join(folder, "weeks"), "%s.W%d" % (sid, w))
        if fa:
            weeks["W%d" % w] = fa["payload_sha256"]
        else:
            weeks["W%d" % w] = "no final week anchor"
            complete = False
    out = {"what_this_is": "the anchor of the whole series over its final week anchors, "
                           "plus the plan and the registry as they stand",
           "series": sid, "week_anchors": weeks,
           "plan_sha256": sha256_file(os.path.join(folder, "plan.json")),
           "registry_sha256": sha256_file(registry_path(folder)) if os.path.exists(registry_path(folder)) else None}
    write_anchor(folder, sid, out, final=complete)


def status(a):
    sid, folder = current_series()
    pl = jload(os.path.join(folder, "plan.json"))
    reg = registry(folder)
    short = {"frozen": " ok ", "pending_review": " rv ", "pending_measurement": " nm ",
             "analysis_failed": " an "}
    print("series", sid, "| labels", " ".join(pl["labels"]), "| NeoMundi required:",
          (pl.get("neomundi") or {}).get("required"))
    print("day  date        " + "  ".join("%-4s" % l for l in pl["labels"]))
    for d in pl["schedule"]:
        cells = []
        for l in pl["labels"]:
            es = [e for e in reg if e["day"] == d["day"] and e["label"] == l]
            last = next((e for e in reversed(es) if e["status"] in COMPLETE), None)
            if not es:
                cells.append("  . ")
            elif last:
                cells.append(short[last["status"]])
            else:
                cells.append(" x%d " % len(es))
        if any(c.strip() != "." for c in cells) or d["date"] <= datetime.date.today().isoformat():
            print("%3d  %s  %s" % (d["day"], d["date"], "  ".join(cells)))
    print("ok = frozen, rv = pending review, nm = pending measurement, "
          "an = analysis failed, xN = N technical failures")


def analyse_cmd(a):
    sid, folder = current_series()
    pl = jload(os.path.join(folder, "plan.json"))
    verify_plan_code(pl)
    verify_model_configuration(folder, pl)
    prior = next((e for e in reversed(registry(folder)) if e.get("run_id") == a.run), None)
    if not prior:
        raise SystemExit("run %s is not in the current series registry" % a.run)
    d = next(x for x in pl["schedule"] if x["day"] == prior["day"])
    base = "%s.D%02d" % (sid, d["day"])
    if final_anchor(os.path.join(folder, "days"), base):
        raise SystemExit("day %02d already has its final anchor. Its frozen runs are not "
                         "re-analysed in place; a changed analysis requires a new, versioned "
                         "series" % d["day"])
    analysis_exists = os.path.exists(os.path.join(RUNS, a.run, "Analysis.json"))
    rerun = bool(a.rerun_analyst or not analysis_exists)
    st, notes = analyse(a.run, pl, run_analyst=rerun)
    notes.insert(0, "analyst rerun" if rerun else
                 "existing Analysis.json retained; validation/check/freeze resumed")
    print(a.run, st, "; ".join(notes))
    key = jload(os.path.join(folder, "model_key.json"))["labels"]
    model = key[prior["label"]]["model"]
    run_manifest(sid, prior["day"], prior["label"], model, a.run, st, pl,
                 prior.get("override"))
    record(folder, {**{k: prior[k] for k in ("series", "day", "date", "label", "attempt")},
                    "run_id": a.run, "status": st, "started_at": now(),
                    "override": prior.get("override"),
                    "note": "re-analysis: " + "; ".join(notes)})
    day_anchor(sid, folder, pl, d)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--start", required=True)
    p.add_argument("--days", type=int, default=30); p.add_argument("--seed", type=int, required=True)
    p.add_argument("--models", default=os.path.join(BASE, "models.json"))
    p.add_argument("--window-utc", default="12:00-16:00",
                   help="the UTC window in which every purchase of a day starts")
    p.add_argument("--technical", action="store_true",
                   help="a technical rehearsal: the series id starts with TECH, the plan says "
                        "counted: false, and every run is marked DIAGNOSTIC / NOT COUNTED")
    p.set_defaults(fn=plan)
    p = sub.add_parser("neomundi"); p.add_argument("--version", required=True)
    p.add_argument("--schema", required=True); p.add_argument("--required-keys")
    # the base URL is required: a declaration must not adopt a value silently
    p.add_argument("--base-url", required=True, help="the base URL NeoMundi confirmed")
    p.add_argument("--observe-path", default="/v1/govern")
    p.add_argument("--contract-path", default="/v1/rgc/contracts/{request_id}",
                   help="where the signed interoperability contract of an observation is retrieved")
    p.add_argument("--identifiers-field", default=None,
                   help="only if NeoMundi asks for our identifiers in the body; by default they "
                        "stay on our side and are tied to the observation by its request_id")
    p.set_defaults(fn=neomundi)
    p = sub.add_parser("day"); p.add_argument("--day", type=int); p.add_argument("--date")
    p.add_argument("--only"); p.add_argument("--dry", action="store_true")
    p.add_argument("--override"); p.set_defaults(fn=day)
    p = sub.add_parser("retry"); p.add_argument("--run", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--approved-by", required=True)
    p.add_argument("--approved-estimate", required=True)
    p.add_argument("--override"); p.set_defaults(fn=retry)
    p = sub.add_parser("analyse"); p.add_argument("--run", required=True)
    p.add_argument("--rerun-analyst", action="store_true",
                   help="replace Analysis.json with a new paid analyst call; without this, "
                        "an existing analysis is retained")
    p.set_defaults(fn=analyse_cmd)
    p = sub.add_parser("week"); p.add_argument("week", type=int); p.set_defaults(fn=week)
    p = sub.add_parser("final"); p.set_defaults(fn=final)
    p = sub.add_parser("status"); p.set_defaults(fn=status)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
