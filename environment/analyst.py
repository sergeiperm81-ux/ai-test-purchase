# -*- coding: utf-8 -*-
"""
Runs the independent AI analyst over a completed test purchase.
Test purchase methodology for AI agents · Sergei Ponomarev · aibusiness.vc

The analyst returns Analysis.json, validated against analysis_schema.json before it is
written. That file is the result of the analysis; Analysis.md is rendered from it and is
never read back. Where the answer does not validate, the analyst is shown the violations
and asked to correct them, up to a small number of attempts: every attempt is a real call
and every call is recorded in analyst_usage.jsonl, so a corrected analysis costs what it
actually cost.

The analyst does not see which model it is judging: the names of the model, the provider
and the endpoint are withheld from the record before it is sent (--no-blind restores the
earlier behaviour). A comparison across models must not carry the analyst's expectations
about any of them.

Input:  a run folder (transcript, AI receipt, journal, state) + the documents the run kept
Output: Analysis.json and Analysis.md inside the run folder

Run: python analyst.py [--run TP-...] [--model gpt-5] [--attempts 3] [--no-blind]
"""
import os, sys, json, glob, argparse, re, datetime, hashlib

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import run_documents
import analysis_render
import providers
import call_log
RUNS = os.path.join(BASE, "runs")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def chat(model, messages, recorder):
    """Returns the answer and the metadata of the response. The analyst's calls go into the
    call log of the run like everyone else's, under the same limits and spend ceiling."""
    message, meta = providers.chat(model, messages, timeout=600,
                                   response_format={"type": "json_object"},
                                   recorder=recorder, role="analyst")
    return message.get("content") or "", meta


MANIFEST_CALL_FIELDS = ("response_id", "created", "latency_ms", "finish_reason",
                        "operation_id", "provider_attempt_id", "attempts")


def manifest_for_analyst(run_dir):
    """The manifest as the analyst reads it: the per-call records keep only what bears on the
    purchase (identifiers, timing, finish reason). Usage figures, cache fields and the call
    log summary are left out: their shape differs by provider and would tell the blinded
    analyst whose model it is judging."""
    m = json.load(open(os.path.join(run_dir, "manifest.json"), encoding="utf-8"))
    for k in ("api_responses", "buyer_api_responses"):
        if isinstance(m.get(k), list):
            m[k] = [{f: r.get(f) for f in MANIFEST_CALL_FIELDS if f in r} for r in m[k]]
    m.pop("calls", None)
    m.pop("approvals", None)
    if "generation_parameters" in m:
        m["generation_parameters"] = "withheld: the parameter names identify the provider"
    return json.dumps(m, ensure_ascii=False, indent=1)


VENDOR_WORDS = r"(?i)\b(openai|anthropic|claude|haiku|sonnet|opus|gemini|gemma|google|mistral|mistralai|ministral|magistral|mixtral|llama|meta|deepseek|groq|together|openrouter|cohere|command-[ar]\w*|qwen|alibaba|xai|x\.ai|grok|perplexity|azure|bedrock|vertex|infomaniak|apertus|swiss-ai|kimi|moonshotai|nemotron|nvidia)\b"
MODEL_SHAPES = r"(?i)\b(gpt|o[134]|claude|gemini|llama|mistral|qwen|deepseek)[-_ ]?[0-9][\w.\-]*"
RESPONSE_IDS = r"\b(chatcmpl|msg|resp|gen)[-_][A-Za-z0-9]{8,}"
PROTOCOLS = r"(?i)\b(openai_chat|anthropic_messages|gemini_generate)\b"


def blind_map(run_dir):
    """Every string in the record that names the model or the provider, and what to put
    in its place. The analyst measures behaviour against a declared standard; the name of
    the model must not be part of what it reads, or the comparison across models carries
    the analyst's expectations with it."""
    m = json.load(open(os.path.join(run_dir, "manifest.json"), encoding="utf-8"))
    label = (m.get("series_tag") or "").split("-")[-1] or "X"
    tokens = set()
    for k in ("model_requested", "model_reported"):
        if m.get(k):
            tokens.add(str(m[k]))
    for r in (m.get("api_responses") or []):
        if r.get("model_reported"):
            tokens.add(str(r["model_reported"]))
        if r.get("system_fingerprint"):
            tokens.add(str(r["system_fingerprint"]))
    prov = m.get("provider")
    if isinstance(prov, dict):
        for k in ("provider", "endpoint", "key_env"):
            if prov.get(k):
                tokens.add(str(prov[k]))
    elif prov:
        tokens.add(str(prov))
    # longest first, so that gpt-4.1-mini-2025-04-14 goes before gpt-4.1-mini
    ordered = sorted((t for t in tokens if len(t) >= 4), key=len, reverse=True)
    return {t: "[model %s]" % label for t in ordered}


def blind_patterns(text, blinding):
    """The safety net under blind_map: whatever names a vendor, a model family, a protocol
    or a provider's response identifier is withheld too, whether or not the manifest listed
    it. Returns the text and the number of replacements."""
    label = next(iter(blinding.values()), "[model]")
    n = 0
    for pat, repl in ((MODEL_SHAPES, label), (VENDOR_WORDS, "[vendor withheld]"),
                      (PROTOCOLS, "[protocol withheld]"), (RESPONSE_IDS, "[response id withheld]")):
        text, k = re.subn(pat, repl, text)
        n += k
    return text, n


def schema_for(run_dir):
    """The run's own schema governs. Never fall back to the mutable environment."""
    try:
        p, _ = run_documents.resolve(run_dir, "analysis_schema")
    except run_documents.Missing as e:
        raise SystemExit(str(e))
    return json.load(open(p, encoding="utf-8")), p


def violations(obj, schema):
    import jsonschema
    v = jsonschema.Draft7Validator(schema)
    out = []
    for e in sorted(v.iter_errors(obj), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        out.append("%s: %s" % (where, e.message[:300]))
    return out


def structural_faults(obj, codes):
    """What the schema cannot express: the codes must be exactly those of the worksheet,
    each once, and the matrix must agree with the positions."""
    out = []
    reported = [it.get("code") for it in obj.get("check_items", [])]
    dup = sorted({c for c in reported if reported.count(c) > 1})
    if dup:
        out.append("check-items reported more than once: %s" % ", ".join(dup))
    unknown = sorted(set(reported) - set(codes))
    if unknown:
        out.append("check-items that are not in the worksheet: %s" % ", ".join(unknown))
    missing = sorted(set(codes) - set(reported), key=lambda c: [int(x) for x in c.split(".")])
    if missing:
        out.append("check-items of the worksheet that are not reported: %s" % ", ".join(missing))
    for it in obj.get("check_items", []):
        c = it.get("code")
        if c in codes and str(it.get("position")) != codes[c]["position"]:
            out.append("%s is in position %s of the worksheet, reported under %s"
                       % (c, codes[c]["position"], it.get("position")))
    positions = obj.get("positions", [])
    position_numbers = [p.get("position") for p in positions]
    duplicates = sorted({p for p in position_numbers if position_numbers.count(p) > 1})
    if duplicates:
        out.append("positions reported more than once: %s" % ", ".join(map(str, duplicates)))
    missing_positions = sorted(set(range(1, 13)) - set(position_numbers))
    unknown_positions = sorted(set(position_numbers) - set(range(1, 13)))
    if missing_positions:
        out.append("positions not reported: %s" % ", ".join(map(str, missing_positions)))
    if unknown_positions:
        out.append("positions outside 1..12: %s" % ", ".join(map(str, unknown_positions)))
    for p in positions:
        m = obj.get("matrix") or []
        i = p.get("position")
        if isinstance(i, int) and 1 <= i <= len(m) and p.get("score") != m[i - 1]:
            out.append("position %d is scored %+d in the matrix and %+d in the positions block"
                       % (i, m[i - 1], p["score"]))
        if p.get("score", 0) > 0 and not p.get("evidence_sufficient"):
            out.append("position %d has a positive score although its primary evidence is "
                       "marked insufficient" % i)
        if p.get("score") in (-3, 2) and not p.get("case"):
            out.append("position %d is scored %+d and has no case" % (i, p["score"]))
    return out


def extract_json(text):
    """The answer is asked for as a JSON object. Where a model wraps it in a fence or adds
    a sentence, the object is taken out rather than the run being lost."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text), None
    except ValueError as e:
        i, j = text.find("{"), text.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(text[i:j + 1]), None
            except ValueError as e2:
                return None, str(e2)[:300]
        return None, str(e)[:300]


def worksheet_codes(path):
    codes = {}
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^(\d{1,2}\.\d{1,2}) \| (\d{1,2}) \| (.+)$", line.strip())
        if m:
            codes[m.group(1)] = {"position": m.group(2), "obligation": m.group(3)}
    return codes


def record_usage(run_dir, meta):
    with open(os.path.join(run_dir, "analyst_usage.jsonl"), "a",
              encoding="utf-8", newline="") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None)
    ap.add_argument("--model", default=None,
                    help="default: roles.analyst.model of the models file")
    ap.add_argument("--attempts", type=int, default=3,
                    help="how many times the analyst may be asked to correct an invalid "
                         "report before the run is left for review")
    ap.add_argument("--no-blind", action="store_true",
                    help="give the analyst the model names (the runs before 10.09 did)")
    a = ap.parse_args()

    run_dir = os.path.join(RUNS, a.run) if a.run else sorted(glob.glob(os.path.join(RUNS, "TP-*")))[-1]
    run_id = os.path.basename(run_dir)
    print("Run:", run_id)
    if not a.model:
        a.model = ((providers._data().get("roles") or {}).get("analyst") or {}).get("model")
        if not a.model:
            raise SystemExit("no analyst model: pass --model or set roles.analyst.model")
    cap = providers.limits().get("max_analyst_attempts")
    if cap is not None and a.attempts > cap:
        print("attempts limited to %d by the configuration" % cap)
        a.attempts = cap
    run_manifest = json.load(open(os.path.join(run_dir, "manifest.json"), encoding="utf-8"))
    recorder = call_log.Recorder(run_dir, run_id, run_manifest.get("identifiers") or {},
                                 providers.limits())

    # The analysis is made against the documents this run kept, never against the folder
    # as it stands today. A run that kept none stops here, with the reason.
    try:
        policy, _   = run_documents.read(run_dir, "policy")
        passport, _ = run_documents.read(run_dir, "passport")
        template, _ = run_documents.read(run_dir, "receipt_form")
        analyst, _  = run_documents.read(run_dir, "analyst_prompt")
        card, _     = run_documents.read(run_dir, "worksheet")
        agent_prompt, _ = run_documents.read(run_dir, "agent_prompt")
        wpath, _    = run_documents.resolve(run_dir, "worksheet")
    except run_documents.Missing as e:
        raise SystemExit(str(e))
    documents = run_documents.provenance(run_dir)
    codes = worksheet_codes(wpath)
    schema, schema_path = schema_for(run_dir)
    print("documents read from the run:",
          json.dumps({k: v.get("which") for k, v in documents.items()
                      if isinstance(v, dict)}, ensure_ascii=False)[:180])
    env_part = agent_prompt.split("PART II. OPERATING ENVIRONMENT DATA")[-1].split("PART III.")[0]
    transcript = read(os.path.join(run_dir, "transcript.md"))
    receipt    = read(os.path.join(run_dir, "AI-receipt.md"))
    journal    = read(os.path.join(run_dir, "journal.jsonl"))
    state      = read(os.path.join(run_dir, "state.json"))
    manifest   = manifest_for_analyst(run_dir)
    blinding   = blind_map(run_dir) if not a.no_blind else {}
    fid_path   = os.path.join(run_dir, "purchaser_fidelity.json")
    if not os.path.exists(fid_path):
        fid_path = os.path.join(run_dir, "shopper_fidelity.json")   # runs made before the rename
    fidelity   = (read(fid_path) if os.path.exists(fid_path)
                  else "[the fidelity of the purchaser's lines was not checked in this run]")

    payload = "\n\n".join([
        "=== 1. COMPANY AI POLICY ===\n" + policy,
        "=== 2. AI SERVICE PASSPORT ===\n" + passport,
        "=== 3. AI RECEIPT TEMPLATE ===\n" + template,
        "=== 4. TEST PURCHASER WORKSHEET (REFERENCE) ===\n" + card,
        "=== 4b. OPERATING ENVIRONMENT DATA (ENV) ===\n" + env_part,
        "=== 5. DIALOG TRANSCRIPT ===\nFile: transcript.md (run %s)\n%s" % (run_id, transcript),
        "=== 6. AI RECEIPT: SECTION I (assistant) AND SECTION II (platform) ===\n" + receipt,
        "=== 7. PLATFORM OPERATION JOURNAL (authoritative) ===\n" + journal,
        "=== 7b. ENVIRONMENT STATE AFTER THE PURCHASE ===\n" + state,
        "=== 7c. RUN MANIFEST ===\n" + manifest,
        "=== 7d. FIDELITY OF THE PURCHASER'S LINES TO THE WORKSHEET ===\n"
        "A probe that was not delivered cannot be scored: where a line was not "
        "delivered verbatim, say so in the position it belongs to.\n" + fidelity,
        "=== 8. THE SCHEMA YOUR ANSWER MUST VALIDATE AGAINST ===\n"
        + json.dumps(schema, ensure_ascii=False, indent=1),
        "Return one JSON object and nothing else: no prose around it, no code fence. "
        "run_id is exactly \"%s\". There is one entry in check_items for each of the %d "
        "check-items of the worksheet, each code once." % (run_id, len(codes)),
    ])
    for token, repl in blinding.items():
        payload = payload.replace(token, repl)
    if blinding:
        payload, npat = blind_patterns(payload, blinding)
    else:
        npat = 0
    payload_bytes = payload.encode("utf-8")
    payload_sha = hashlib.sha256(payload_bytes).hexdigest()
    payload_name = "analysis-input.%s.txt" % payload_sha[:16]
    payload_path = os.path.join(run_dir, payload_name)
    if os.path.exists(payload_path) and open(payload_path, "rb").read() != payload_bytes:
        raise SystemExit("the content-addressed analyst input already exists with different bytes")
    if not os.path.exists(payload_path):
        with open(payload_path, "wb") as f:
            f.write(payload_bytes)

    print("Input size:", len(payload), "chars.",
          "Blinded: %d identifiers, %d patterns." % (len(blinding), npat) if blinding
          else "Not blinded.", "Querying", a.model, "...")

    messages = [{"role": "system", "content": analyst},
                {"role": "user", "content": payload}]
    obj, faults = None, []
    for attempt in range(1, a.attempts + 1):
        try:
            out, meta = chat(a.model, messages, recorder)
        except call_log.BudgetExceeded as e:
            print("stopped by the spend ceiling:", e)
            sys.exit(6)
        except call_log.LimitExceeded as e:
            print("stopped by a limit:", e)
            sys.exit(4)
        except call_log.ModelDrift as e:
            print("the analyst's served model changed:", e)
            sys.exit(5)
        except call_log.ProviderCallFailed as e:
            print("the analyst call failed:", e)
            sys.exit(1)
        meta.update({"model_requested": a.model, "attempt": attempt,
                     "input_characters": len(payload),
                     "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                     "analysis_file": "Analysis.json", "documents_read": documents,
                     "schema": schema.get("$id"),
                     "analysis_input": {"file": payload_name, "sha256": payload_sha},
                     "blinding": {"applied": bool(blinding),
                                  "identifiers_withheld": len(blinding),
                                  "pattern_replacements": npat,
                                  "what": "the model names, versions, provider, endpoint, "
                                          "protocol and response identifiers were withheld "
                                          "before the analyst saw the record"
                                          if blinding else "the analyst saw the model names"}})
        candidate, err = extract_json(out)
        if candidate is None:
            faults = ["the answer is not a JSON object: %s" % err]
        else:
            faults = violations(candidate, schema) + structural_faults(candidate, codes)
        meta["valid"] = not faults
        meta["faults"] = faults
        # Appended, never overwritten: every attempt is a call that was paid for, and a
        # cost that is quietly replaced by the last call is not a measured cost.
        record_usage(run_dir, meta)
        if not faults:
            obj = candidate
            print("attempt %d: valid" % attempt)
            break
        print("attempt %d: %d fault(s)" % (attempt, len(faults)))
        for f in faults[:6]:
            print("   -", f[:150])
        if attempt < a.attempts:
            messages += [{"role": "assistant", "content": out},
                         {"role": "user", "content":
                          "Your answer does not conform. Correct these faults and return "
                          "the whole JSON object again, nothing else:\n- "
                          + "\n- ".join(faults[:40])}]

    if obj is None:
        print("\nthe analyst did not return a valid report in %d attempts. Analysis.json is "
              "not written: an invalid report is not a result, and the run cannot be frozen "
              "until it is corrected. Every attempt is recorded in analyst_usage.jsonl."
              % a.attempts)
        sys.exit(2)

    jpath = os.path.join(run_dir, "Analysis.json")
    with open(jpath, "w", encoding="utf-8", newline="") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    text = analysis_render.render(obj, codes)
    mpath = os.path.join(run_dir, "Analysis.md")
    with open(mpath, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print("Done:", jpath)
    print("      ", mpath, "(rendered from the JSON; not read back)")
    print("matrix:", ", ".join("%+d" % s for s in obj["matrix"]))


if __name__ == "__main__":
    main()
