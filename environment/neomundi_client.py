# -*- coding: utf-8 -*-
"""
One NeoMundi observation for every completed service-agent call, linked one to one.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

The rules NeoMundi confirmed on 16.09.2026:
  base https://api.neomundi.io, POST /v1/govern, header X-API-Key, mode OBS;
  one completed service-agent model call = one observation;
  the purchaser makes no model call and is not observed;
  the analyst calls stay outside this population;
  a provider call that produced no usable answer is kept in our execution log as a failed
  call and is not submitted as an observation;
  no NeoMundi-specific purchase or call fields are invented in the body: our identifiers
  stay on our side, and the two records are tied together by the request_id NeoMundi
  returns and by the signed interoperability contract retrieved with it
  (POST /v1/rgc/contracts/{request_id}).

What is observed is the exact API request payload sent to the provider and the exact
response body that came back. A provider may transform the messages internally, so this is
called the exact request payload, not the context the model received.

  neomundi/config-at-start.json            the NeoMundi configuration frozen when the run
                                           began (no key); every send and flush of the run
                                           uses this copy, never the current models file
  neomundi/requests/<attempt>.json         the exact bytes sent to NeoMundi (no API key)
  neomundi/responses/<attempt>.<n>.json    the exact bytes NeoMundi returned
  neomundi/contracts/<attempt>.json        the signed interoperability contract
  neomundi/links.jsonl                     append-only: one line per try, carrying the
                                           checksums of the provider request, the provider
                                           response, the NeoMundi request, the contract and
                                           the frozen configuration

A failure of NeoMundi never causes a model call to be repeated. The observation is kept as
pending with its request saved, and `flush` sends the same bytes again later; a contract
that was not retrieved is fetched again the same way.

Usage:
  python neomundi_client.py flush <run_dir>
  python neomundi_client.py verify <run_dir>
"""
import os, sys, json, time, base64, hashlib, urllib.request, urllib.error
import fsio

import call_log
import projection

RETRYABLE = {408, 425, 429, 500, 502, 503, 504, 529}
# a body NeoMundi refuses is refused for good: resending the same bytes is pointless
REFUSED = {400, 409, 413, 415, 422}
CONFIG_FILE = "config-at-start.json"
BASE = os.path.dirname(os.path.abspath(__file__))


def request_ledger_path():
    return os.environ.get("TEST_PURCHASE_NEOMUNDI_LEDGER") or \
        os.path.join(BASE, "ledger", "neomundi_requests.jsonl")


def requests_made(scope=None, run_id=None, kind=None):
    """Outgoing NeoMundi requests recorded so far, every outcome; every kind unless one is
    named (observation or contract)."""
    n = 0
    for r in call_log._read_jsonl(request_ledger_path()):
        if scope is not None and r.get("scope") != scope:
            continue
        if run_id is not None and r.get("run_id") != run_id:
            continue
        if kind is not None and r.get("kind") != kind:
            continue
        n += 1
    return n


CAPS = {"observation": ("max_observations_per_scope", "max_observations_per_purchase"),
        "contract": ("max_contracts_per_scope", "max_contracts_per_purchase")}


def reserve_request(run_dir, cfg, attempt_id, kind):
    """Counts one outgoing request of its kind against the caps of the budget scope and of
    the purchase, atomically and before it is sent. Observations (paid) and contract
    retrievals (free, as seen in the portal) have separate caps. Every POST counts whatever
    NeoMundi answers: what is capped is what leaves, not what succeeds. Without a cap in the
    configuration nothing is sent. Returns (allowed, detail)."""
    scope_key, run_key = CAPS[kind]
    cap_scope, cap_run = cfg.get(scope_key), cfg.get(run_key)
    if not isinstance(cap_scope, int) or not isinstance(cap_run, int):
        return False, "the NeoMundi configuration sets no %s caps: nothing is sent" % kind
    scope, run_id = call_log.budget_scope(), os.path.basename(os.path.abspath(run_dir))
    path = request_ledger_path()
    with call_log.LedgerLock(path):
        rows = [r for r in call_log._read_jsonl(path) if r.get("kind") == kind]
        in_scope = sum(1 for r in rows if r.get("scope") == scope)
        in_run = sum(1 for r in rows if r.get("run_id") == run_id)
        if in_scope >= cap_scope:
            return False, "the cap of %d NeoMundi %ss for scope %s is reached" % (cap_scope, kind, scope)
        if in_run >= cap_run:
            return False, "the cap of %d NeoMundi %ss for this purchase is reached" % (cap_run, kind)
        call_log._append_jsonl(path, {"at_utc": call_log.utc_iso(), "scope": scope, "run_id": run_id,
                                      "provider_attempt_id": attempt_id, "kind": kind})
    return True, {"scope_%ss" % kind: in_scope + 1, "purchase_%ss" % kind: in_run + 1}


# ---------------------------------------------------------------- the circuit breaker

def breaker_path(scope=None):
    return "%s.breaker.%s.json" % (request_ledger_path(), scope or call_log.budget_scope())


def breaker_open(scope=None):
    """The reason the breaker of this scope is open, or None. It opens on the first refusal
    NeoMundi cannot be expected to withdraw (an oversize body, a 4xx that is not a rate
    limit): from then on nothing is sent in the scope until an operator resets it, because
    every further request would be refused for the same reason and the round would spend
    its money on purchases that cannot be measured."""
    p = breaker_path(scope)
    return fsio.read_json(p) if os.path.exists(p) else None


def open_breaker(run_dir, reason, scope=None):
    p = breaker_path(scope)
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            json.dump({"scope": scope or call_log.budget_scope(), "opened_at_utc": call_log.utc_iso(),
                       "run_id": os.path.basename(os.path.abspath(run_dir)), "reason": reason,
                       "how_to_reset": "python neomundi_client.py breaker --reset, after the cause is settled"},
                      f, ensure_ascii=False, indent=1)


def reset_breaker(scope=None):
    p = breaker_path(scope)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


def _folder(run_dir):
    return os.path.join(run_dir, "neomundi")


def _links_path(run_dir):
    return os.path.join(_folder(run_dir), "links.jsonl")


def links(run_dir):
    return call_log._read_jsonl(_links_path(run_dir))


def freeze_config(run_dir):
    """Copies the NeoMundi configuration into the run when it starts. Written once. In a
    series whose plan declares the measurement, the source is the declared
    neomundi-config.json (TEST_PURCHASE_NEOMUNDI_CONFIG), copied byte for byte so that its
    checksum is the one in the plan; otherwise it is the block of the models file."""
    import providers
    path = os.path.join(_folder(run_dir), CONFIG_FILE)
    if not os.path.exists(path):
        os.makedirs(_folder(run_dir), exist_ok=True)
        source = os.environ.get("TEST_PURCHASE_NEOMUNDI_CONFIG")
        raw = fsio.read_bytes(source) if source else \
            json.dumps(providers.neomundi_config(), ensure_ascii=False, indent=1).encode("utf-8")
        with open(path, "xb") as f:
            f.write(raw)
    return run_config(run_dir)


def run_config(run_dir):
    path = os.path.join(_folder(run_dir), CONFIG_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _config_sha(run_dir):
    path = os.path.join(_folder(run_dir), CONFIG_FILE)
    return call_log.sha256_bytes(fsio.read_bytes(path)) if os.path.exists(path) else None


def url_for(cfg, path):
    return (cfg.get("base_url") or "").rstrip("/") + path


def build_body(line, request_bytes, response_bytes, cfg):
    """The observation as NeoMundi expects it. Our own identifiers are not put in it: they
    stay in the execution log and are tied to the observation by its request_id. A field for
    them is sent only if the configuration names one."""
    norm = line.get("usage_normalised") or {}
    # No cost is reported to NeoMundi: the providers do not return one, and the API refuses a
    # null in this field (HTTP 422), so it is left out rather than filled with a number we
    # did not measure. Our own computed cost stays in cost.json, labelled as computed.
    body = {"source_type": "llm", "mode": cfg.get("mode", "OBS"),
            "llm_prompt": prompt_for(request_bytes, cfg),
            "llm_response": response_bytes.decode("utf-8", "replace"),
            "raw_metrics": {"token_count": (norm.get("input_tokens") or 0) + (norm.get("output_tokens") or 0),
                            "latency_ms": line.get("latency_ms"),
                            "model_name": line.get("observed_model_id") or line.get("requested_model_id")}}
    field = cfg.get("identifiers_field")
    if field:
        body[field] = {k: line.get(k) for k in (
            "pilot_id", "scenario_id", "series_id", "day_id", "purchase_id", "run_id",
            "operation_id", "provider_attempt_id", "configuration_id", "provider_id",
            "requested_model_id", "observed_model_id", "prompt_version",
            "configuration_version", "started_at_utc", "ended_at_utc")}
    return body


def prompt_for(request_bytes, cfg):
    """What goes into llm_prompt: the exact provider request, or, where the configuration
    names an agreed projection, that projection of it. A projection that cannot fit leaves
    the full request in place, so that the size check refuses it rather than anything here
    cutting it down by a rule nobody agreed."""
    name = cfg.get("prompt_projection")
    if not name:
        return request_bytes.decode("utf-8")
    if name not in projection.KNOWN:
        raise ValueError("unknown prompt projection %r: known are %s" % (name, ", ".join(projection.KNOWN)))
    limit = cfg.get("max_prompt_chars")
    projected = projection.project(request_bytes, limit) if isinstance(limit, int) else None
    return projected if projected is not None else request_bytes.decode("utf-8")


USER_AGENT = "aibusiness-test-purchase/3.0 (+https://aibusiness.vc)"


def _post(cfg, url, payload, key):
    """(status, body bytes, headers, error name). Never raises for HTTP.
    The client names itself: the gateway in front of the API refuses a request that does
    not (HTTP 403, code 1010), and an unnamed client is bad manners in any case."""
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={"X-API-Key": key, "Content-Type": "application/json",
                                          "Accept": "application/json",
                                          "User-Agent": cfg.get("user_agent") or USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=int(cfg.get("timeout_s", 60))) as r:
            return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}, None
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}, None
    except Exception as e:
        return None, None, {}, type(e).__name__


def _ids(resp_bytes, headers):
    rid = tid = None
    try:
        obj = json.loads(resp_bytes)
        if isinstance(obj, dict):
            rid = obj.get("request_id") or obj.get("id")
            audit = obj.get("audit")
            tid = audit.get("trace_id") if isinstance(audit, dict) else None
            tid = tid or obj.get("trace_id")
    except ValueError:
        pass
    rid = rid or headers.get("x-request-id") or headers.get("request-id")
    tid = tid or headers.get("x-trace-id")
    return rid, tid


def _key_of(run_dir, cfg, attempt_id, hashes):
    """The key named by the frozen configuration, or a pending link and None."""
    import providers
    key_env = cfg.get("key_env")          # named explicitly by the configuration; no default
    if key_env and providers.key_present(key_env):
        return providers._key(key_env)
    call_log._append_jsonl(_links_path(run_dir), dict(hashes, **{
        "provider_attempt_id": attempt_id, "status": "pending", "at_utc": call_log.utc_iso(),
        "error": "%s is absent: the observation is kept and can be flushed"
                 % (key_env or "the key_env of the frozen configuration")}))
    return None


def _send(run_dir, attempt_id, request_bytes, cfg, hashes):
    """Sends one observation, retrying only NeoMundi, then retrieves its contract. Appends
    one link line per try and returns the final status."""
    os.makedirs(os.path.join(_folder(run_dir), "responses"), exist_ok=True)
    key = _key_of(run_dir, cfg, attempt_id, hashes)
    if key is None:
        return "pending"
    # the size limit NeoMundi enforces on llm_prompt is checked here, before anything
    # leaves, on every send and every flush: an oversize body is refused deterministically,
    # so it is not sent, and the breaker opens because every body of a purchase is about
    # as long as this one. The context is never cut down here to fit: what to send instead
    # is agreed with NeoMundi and comes as a new configuration
    opened = breaker_open()
    if opened:
        call_log._append_jsonl(_links_path(run_dir), dict(hashes, **{
            "provider_attempt_id": attempt_id, "status": "circuit_open", "at_utc": call_log.utc_iso(),
            "error": "the NeoMundi breaker of this scope is open: %s" % opened.get("reason")}))
        return "circuit_open"
    limit = cfg.get("max_prompt_chars")
    size = len(json.loads(request_bytes).get("llm_prompt") or "")
    if not isinstance(limit, int) or size > limit:
        why = ("no max_prompt_chars in the NeoMundi configuration" if not isinstance(limit, int)
               else "llm_prompt is %d characters, the limit is %d" % (size, limit))
        call_log._append_jsonl(_links_path(run_dir), dict(hashes, **{
            "provider_attempt_id": attempt_id, "status": "oversize",
            "at_utc": call_log.utc_iso(), "llm_prompt_chars": size, "error": why}))
        open_breaker(run_dir, "oversize on %s: %s" % (attempt_id, why))
        return "oversize"
    tries = int(cfg.get("max_attempts", 3))
    scale = float(os.environ.get("TEST_PURCHASE_RETRY_SCALE", "1"))
    n_prev = sum(1 for l in links(run_dir) if l["provider_attempt_id"] == attempt_id and l.get("try"))
    for t in range(1, tries + 1):
        n = n_prev + t
        opened = breaker_open()
        if opened:
            call_log._append_jsonl(_links_path(run_dir), dict(hashes, **{
                "provider_attempt_id": attempt_id, "status": "circuit_open", "at_utc": call_log.utc_iso(),
                "error": "the NeoMundi breaker of this scope is open: %s" % opened.get("reason")}))
            return "circuit_open"
        allowed, why = reserve_request(run_dir, cfg, attempt_id, "observation")
        if not allowed:
            call_log._append_jsonl(_links_path(run_dir), dict(hashes, **{
                "provider_attempt_id": attempt_id, "status": "pending", "at_utc": call_log.utc_iso(),
                "error": why + "; the request is saved and can be flushed under a new cap"}))
            return "pending"
        started = call_log.utc_now()
        status, body, headers, error = _post(
            cfg, url_for(cfg, cfg.get("observe_path", "/v1/govern")), request_bytes, key)
        ok = status is not None and 200 <= status < 300
        if ok:
            try:
                json.loads(body)
            except ValueError:
                ok, error = False, "the response is not JSON"
        entry = dict(hashes, **{"provider_attempt_id": attempt_id, "try": n,
                                "at_utc": call_log.utc_iso(started), "http_status": status,
                                "error": error, "status": "observed" if ok else "failed_try"})
        if body is not None:
            name = "%s.%d.json" % (attempt_id, n)
            with open(os.path.join(_folder(run_dir), "responses", name), "xb") as f:
                f.write(body)
            entry["response_file"] = "neomundi/responses/" + name
            entry["response_sha256"] = call_log.sha256_bytes(body)
        if ok:
            entry["neomundi_request_id"], entry["neomundi_trace_id"] = _ids(body, headers)
        call_log._append_jsonl(_links_path(run_dir), entry)
        if ok:
            if entry["neomundi_request_id"]:
                fetch_contract(run_dir, attempt_id, entry["neomundi_request_id"], cfg, hashes, key)
            return "observed"
        if status in REFUSED:
            # the body itself is refused: sending the same bytes again can only be refused
            # again, so this is terminal and flush leaves it alone. The breaker opens: the
            # next bodies of the scope would be refused for the same reason
            detail = (body or b"")[:300].decode("utf-8", "replace")
            call_log._append_jsonl(_links_path(run_dir), dict(hashes, **{
                "provider_attempt_id": attempt_id, "status": "invalid_request",
                "at_utc": call_log.utc_iso(), "http_status": status,
                "error": "NeoMundi refused this body; it is not sent again. The call stays "
                         "unobserved until a corrected observation is made deliberately"}))
            open_breaker(run_dir, "HTTP %d on %s: %s" % (status, attempt_id, detail))
            return "invalid_request"
        if status is not None and status not in RETRYABLE:
            if 400 <= status < 500:
                # not the body but the address or the key: the same answer for every body
                open_breaker(run_dir, "HTTP %d on the observation of %s: %s"
                             % (status, attempt_id, (body or b"")[:300].decode("utf-8", "replace")))
            break
        if t < tries:
            time.sleep(min(5 * t, 30) * scale)
    call_log._append_jsonl(_links_path(run_dir), dict(hashes, **{
        "provider_attempt_id": attempt_id, "status": "pending", "at_utc": call_log.utc_iso(),
        "error": "no observation after %d tries; the request is saved and can be flushed" % tries}))
    return "pending"


def fetch_contract(run_dir, attempt_id, request_id, cfg, hashes, key=None):
    """The signed interoperability contract of one observation. Kept beside the run; a
    failure is recorded and can be retried by flush, and never touches the purchase."""
    path_template = cfg.get("contract_path")
    if not path_template:
        return None
    if key is None:
        key = _key_of(run_dir, cfg, attempt_id, hashes)
        if key is None:
            return "pending"
    allowed, why = reserve_request(run_dir, cfg, attempt_id, "contract")
    if not allowed:
        call_log._append_jsonl(_links_path(run_dir), dict(hashes, **{
            "provider_attempt_id": attempt_id, "at_utc": call_log.utc_iso(),
            "neomundi_request_id": request_id, "status": "contract_pending", "error": why}))
        return "contract_pending"
    url = url_for(cfg, path_template.replace("{request_id}", request_id))
    status, body, headers, error = _post(cfg, url, b"{}", key)
    ok = status is not None and 200 <= status < 300 and body is not None
    if ok:
        try:
            json.loads(body)
        except ValueError:
            ok, error = False, "the contract is not JSON"
    if status is not None and 400 <= status < 500 and status not in RETRYABLE:
        # a wrong contract address or format answers the same for every contract of the
        # round: the breaker opens exactly as it does for an observation
        open_breaker(run_dir, "HTTP %d on the contract of %s: %s"
                     % (status, attempt_id, (body or b"")[:300].decode("utf-8", "replace")))
    entry = dict(hashes, **{"provider_attempt_id": attempt_id, "at_utc": call_log.utc_iso(),
                            "neomundi_request_id": request_id, "http_status": status,
                            "error": error, "status": "contract" if ok else "contract_pending"})
    if ok:
        os.makedirs(os.path.join(_folder(run_dir), "contracts"), exist_ok=True)
        name = "%s.json" % attempt_id
        with open(os.path.join(_folder(run_dir), "contracts", name), "xb") as f:
            f.write(body)
        entry["contract_file"] = "neomundi/contracts/" + name
        entry["contract_sha256"] = call_log.sha256_bytes(body)
    call_log._append_jsonl(_links_path(run_dir), entry)
    return entry["status"]


def observe(run_dir, line, request_bytes, response_bytes):
    """Called by the call log after every completed attempt."""
    cfg = run_config(run_dir)
    if not cfg or not cfg.get("enabled") or line.get("role") not in cfg.get("observe_roles", ["agent"]):
        return None
    body = build_body(line, request_bytes, response_bytes, cfg)
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    folder = os.path.join(_folder(run_dir), "requests")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, line["provider_attempt_id"] + ".json"), "xb") as f:
        f.write(raw)
    hashes = {"provider_request_sha256": line["request_sha256"],
              "provider_response_sha256": line["response_sha256"],
              "neomundi_request_sha256": call_log.sha256_bytes(raw),
              "config_sha256": _config_sha(run_dir)}
    return _send(run_dir, line["provider_attempt_id"], raw, cfg, hashes)


def _state(run_dir):
    """Per attempt: the last status, the request id and whether a contract was retrieved."""
    out = {}
    for l in links(run_dir):
        aid = l["provider_attempt_id"]
        s = out.setdefault(aid, {"status": None, "request_id": None, "contract": False,
                                 "hashes": {k: l.get(k) for k in (
                                     "provider_request_sha256", "provider_response_sha256",
                                     "neomundi_request_sha256", "config_sha256")}})
        if l["status"] == "observed":
            s["status"] = "observed"
            s["request_id"] = l.get("neomundi_request_id") or s["request_id"]
        elif l["status"] == "contract":
            s["contract"] = True
            s["request_id"] = l.get("neomundi_request_id") or s["request_id"]
        elif s["status"] != "observed":
            s["status"] = l["status"]
    return out


def flush(run_dir):
    """Sends again, byte for byte and under the run's frozen configuration, every
    observation that is not yet observed, and retrieves every contract still missing."""
    cfg = run_config(run_dir)
    if not cfg or not cfg.get("enabled"):
        raise SystemExit("this run has no frozen NeoMundi configuration with observations "
                         "enabled: nothing is sent")
    out = {}
    for aid, s in _state(run_dir).items():
        if s["status"] == "invalid_request":
            out[aid] = "refused by NeoMundi: not sent again"
            continue
        if s["status"] == "observed":
            if cfg.get("contract_path") and not s["contract"] and s["request_id"]:
                out[aid] = "contract: %s" % fetch_contract(run_dir, aid, s["request_id"], cfg, s["hashes"])
            continue
        p = os.path.join(_folder(run_dir), "requests", aid + ".json")
        if not os.path.exists(p):
            out[aid] = "no saved request"
            continue
        raw = fsio.read_bytes(p)
        if call_log.sha256_bytes(raw) != s["hashes"].get("neomundi_request_sha256"):
            out[aid] = "the saved request no longer matches its checksum: not sent"
            continue
        out[aid] = _send(run_dir, aid, raw, cfg, s["hashes"])
    return out


def _file_sha(run_dir, rel):
    p = os.path.join(run_dir, rel or "")
    return call_log.sha256_bytes(fsio.read_bytes(p)) if rel and os.path.isfile(p) else None


def _check_observation(run_dir, l, call, cfg_sha):
    """The problems of one observed link, [] when the chain holds."""
    aid, problems = l["provider_attempt_id"], []
    if not l.get("response_file") or _file_sha(run_dir, l["response_file"]) != l.get("response_sha256"):
        problems.append("%s: NeoMundi response missing or changed" % aid)
    rel = "neomundi/requests/%s.json" % aid
    raw_sha = _file_sha(run_dir, rel)
    if raw_sha is None or raw_sha != l.get("neomundi_request_sha256"):
        return problems + ["%s: request to NeoMundi missing or changed" % aid]
    if l.get("config_sha256") != cfg_sha:
        problems.append("%s: sent under a NeoMundi configuration other than the run's frozen copy" % aid)
    if call is None:
        return problems
    if _file_sha(run_dir, call.get("request_file")) != call["request_sha256"]:
        problems.append("%s: provider request missing or changed" % aid)
    if _file_sha(run_dir, call.get("response_file")) != call.get("response_sha256"):
        problems.append("%s: provider response missing or changed" % aid)
    if (l.get("provider_request_sha256"), l.get("provider_response_sha256")) != \
            (call["request_sha256"], call.get("response_sha256")):
        problems.append("%s: the link names other provider files than the call log" % aid)
    body = json.loads(fsio.read_bytes(os.path.join(run_dir, rel)))
    cfg = run_config(run_dir) or {}
    if cfg.get("prompt_projection"):
        # recomputed from the provider request we kept: the projection is a function of
        # those bytes, so anyone holding them gets the same llm_prompt
        request = fsio.read_bytes(os.path.join(run_dir, call.get("request_file") or ""))
        if body.get("llm_prompt", "") != prompt_for(request, cfg):
            problems.append("%s: llm_prompt is not the %s projection of the provider request"
                            % (aid, cfg["prompt_projection"]))
    elif call_log.sha256_bytes(body.get("llm_prompt", "").encode("utf-8")) != call["request_sha256"]:
        problems.append("%s: llm_prompt is not the exact provider request" % aid)
    if call_log.sha256_bytes(body.get("llm_response", "").encode("utf-8")) != call.get("response_sha256"):
        problems.append("%s: llm_response is not the exact provider response" % aid)
    return problems


def canonical_payload_hash(contract):
    """The payload hash of a contract, recomputed. The rule was derived from the contract of
    16.09.2026 and reproduces its value: SHA-256 over the contract without its integrity
    block, as JSON with sorted keys, ASCII escapes and no spaces (sorted-json-utf8)."""
    payload = {k: v for k, v in contract.items() if k != "integrity"}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def contract_integrity(contract):
    """What we can and cannot check about a signed contract on our own. The signature itself
    stays unverified until NeoMundi publishes the public key of its kid: that is recorded as
    a limitation, never as a pass."""
    integ = contract.get("integrity") or {}
    ident = contract.get("identity") or {}
    out = {"hash_algorithm": integ.get("hash_algorithm"),
           "canonicalization": integ.get("canonicalization"),
           "payload_hash_matches": None, "signature_binds_the_payload_hash": None,
           "signature_verified": False,
           "signature_check": "not performed: the public key of the signing kid is not "
                              "published to us"}
    if integ.get("hash_algorithm") == "sha256" and integ.get("payload_hash"):
        out["payload_hash_matches"] = canonical_payload_hash(contract) == integ["payload_hash"]
    parts = (integ.get("signature") or "").split(".")
    if len(parts) == 3:
        def decode(part):
            return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        try:
            header, claims = decode(parts[0]), decode(parts[1])
        except Exception:
            header, claims = {}, {}
        out["algorithm"], out["key_id"] = header.get("alg"), header.get("kid")
        out["signature_binds_the_payload_hash"] = bool(claims) and \
            claims.get("payload_hash") == integ.get("payload_hash") and \
            claims.get("request_id") == ident.get("request_id") and \
            claims.get("timestamp") == ident.get("timestamp")
    return out


def _check_contract(run_dir, aid, contracts, request_id):
    lines = contracts.get(aid, [])
    if len(lines) != 1:
        return ["%s: %d interoperability contracts, exactly 1 required" % (aid, len(lines))]
    l = lines[0]
    if _file_sha(run_dir, l.get("contract_file")) != l.get("contract_sha256"):
        return ["%s: interoperability contract missing or changed" % aid]
    if request_id and l.get("neomundi_request_id") != request_id:
        return ["%s: the contract belongs to another request_id" % aid]
    contract = json.loads(fsio.read_bytes(os.path.join(run_dir, l["contract_file"])))
    if (contract.get("identity") or {}).get("request_id") != l.get("neomundi_request_id"):
        return ["%s: the contract names another request_id than the observation" % aid]
    integ = contract_integrity(contract)
    problems = []
    if integ["payload_hash_matches"] is False:
        problems.append("%s: the payload hash of the contract does not match its content" % aid)
    if integ["signature_binds_the_payload_hash"] is False:
        problems.append("%s: the signature does not bind this payload hash, request id and time" % aid)
    return problems


def verify_links(run_dir, roles=("agent",)):
    """Every completed call of the observed roles has exactly one observation and, where the
    configuration retrieves them, exactly one signed contract; no observation points at a
    call that does not exist or did not complete; the provider request and response, the
    NeoMundi request and response, the contract and the frozen configuration all still have
    their checksums; llm_prompt and llm_response are exactly the provider bytes.
    Returns (ok, detail)."""
    calls = call_log.attempts(run_dir)
    known = {c["provider_attempt_id"]: c for c in calls}
    completed = {aid for aid, c in known.items()
                 if c["outcome"] == "completed" and c.get("role") in roles}
    cfg, cfg_sha = run_config(run_dir) or {}, _config_sha(run_dir)
    observed, contracts, problems = {}, {}, []
    for l in links(run_dir):
        if l["status"] == "contract":
            contracts.setdefault(l["provider_attempt_id"], []).append(l)
        if l["status"] != "observed":
            continue
        observed.setdefault(l["provider_attempt_id"], []).append(l)
        problems += _check_observation(run_dir, l, known.get(l["provider_attempt_id"]), cfg_sha)
    for aid in sorted(completed):
        n = len(observed.get(aid, []))
        if n != 1:
            problems.append("%s: %d observations, exactly 1 required" % (aid, n))
            continue
        # NeoMundi guarantees a request_id for an accepted observation; it is what ties the
        # observation to our own record. trace_id is kept when it comes and never required.
        request_id = observed[aid][0].get("neomundi_request_id")
        if not request_id:
            problems.append("%s: the observation came back without a request_id" % aid)
        if cfg.get("contract_path"):
            problems += _check_contract(run_dir, aid, contracts, request_id)
    for aid in sorted(observed):
        if aid not in completed:
            why = "no such call" if aid not in known else "the call did not complete"
            problems.append("%s: observation without a completed call (%s)" % (aid, why))
    files = {l["response_file"]: l["response_sha256"]
             for ls in observed.values() for l in ls if l.get("response_file")}
    files.update({l["contract_file"]: l["contract_sha256"]
                  for ls in contracts.values() for l in ls if l.get("contract_file")})
    integrity = {}
    for aid, ls in contracts.items():
        p = os.path.join(run_dir, ls[0].get("contract_file") or "")
        if os.path.isfile(p):
            integrity[aid] = contract_integrity(json.loads(fsio.read_bytes(p)))
    detail = {"completed_calls": len(completed), "observed": len(observed),
              "contracts": len(contracts), "problems": problems, "files": files,
              "config_at_start_sha256": cfg_sha,
              "request_ids": {aid: ls[0].get("neomundi_request_id") for aid, ls in observed.items()},
              "contract_integrity": integrity}
    return (not problems and bool(completed)), detail


def verify_attempt(run_dir, attempt_id):
    """The chain of one call only: exactly one observation and, where contracts are
    retrieved, exactly one signed contract, every file with its checksum, llm_prompt and
    llm_response exactly what the configuration of the run makes of the provider bytes.
    For a probe of a single call in a copy of a whole purchase, where verify_links would
    rightly report every other call as unobserved. Returns (ok, detail)."""
    call = next((c for c in call_log.attempts(run_dir) if c["provider_attempt_id"] == attempt_id), None)
    if call is None or call["outcome"] != "completed":
        return False, {"attempt": attempt_id, "problems": ["no completed attempt %s in this run" % attempt_id]}
    cfg, cfg_sha = run_config(run_dir) or {}, _config_sha(run_dir)
    mine = [l for l in links(run_dir) if l["provider_attempt_id"] == attempt_id]
    observed = [l for l in mine if l["status"] == "observed"]
    contracts = {attempt_id: [l for l in mine if l["status"] == "contract"]}
    others = sorted({l["provider_attempt_id"] for l in links(run_dir)} - {attempt_id})
    problems = []
    if others:
        problems.append("the links name other calls too: %s" % ", ".join(others[:5]))
    if len(observed) != 1:
        problems.append("%s: %d observations, exactly 1 required" % (attempt_id, len(observed)))
        return False, {"attempt": attempt_id, "problems": problems}
    l = observed[0]
    problems += _check_observation(run_dir, l, call, cfg_sha)
    if not l.get("neomundi_request_id"):
        problems.append("%s: the observation came back without a request_id" % attempt_id)
    if cfg.get("contract_path"):
        problems += _check_contract(run_dir, attempt_id, contracts, l.get("neomundi_request_id"))
    return not problems, {"attempt": attempt_id, "problems": problems,
                          "request_id": l.get("neomundi_request_id"),
                          "http_status": l.get("http_status"),
                          "prompt_projection": cfg.get("prompt_projection"),
                          "tries": sum(1 for x in mine if x.get("try"))}


# a probe sends exactly one observation and asks for exactly one contract, whatever
# happens: no retry, and caps of one that no later flush can exceed
PROBE_LIMITS = {"max_attempts": 1,
                "max_observations_per_scope": 1, "max_observations_per_purchase": 1,
                "max_contracts_per_scope": 1, "max_contracts_per_purchase": 1}


def probe_env(run_dir):
    """The scope and the request ledger of a probe: its own, named after its folder, so
    that its one request is counted apart from every series and rehearsal."""
    run_id = os.path.basename(os.path.abspath(run_dir))
    return {"TEST_PURCHASE_BUDGET_SCOPE": "probe-" + run_id,
            "TEST_PURCHASE_NEOMUNDI_LEDGER": os.path.join(
                os.environ.get("TEST_PURCHASE_LEDGER_DIR") or os.path.join(BASE, "ledger"),
                "probe-%s-neomundi.jsonl" % run_id)}


def probe(run_dir, attempt_id):
    """One live observation of a call that was already made and recorded, to see what
    NeoMundi actually returns before a round is spent on it. No model is called: the
    request and the response come from the call log. The run must not have a NeoMundi
    configuration yet: the probe freezes its own, the current one with PROBE_LIMITS, so
    that one paid observation is all it can ever send. Run deliberately, with the owner's
    approval, in its own scope and ledger (probe_env)."""
    import providers
    if run_config(run_dir) is not None:
        raise SystemExit("this run already has a frozen NeoMundi configuration: a probe needs a "
                         "copy of the purchase without its neomundi folder")
    for k, v in probe_env(run_dir).items():
        os.environ[k] = v
    cfg = dict(providers.neomundi_config(), enabled=True, **PROBE_LIMITS)
    os.makedirs(_folder(run_dir), exist_ok=True)
    with open(os.path.join(_folder(run_dir), CONFIG_FILE), "xb") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=1).encode("utf-8"))
    cfg = run_config(run_dir)
    line = next((a for a in call_log.attempts(run_dir)
                 if a["provider_attempt_id"] == attempt_id), None)
    if line is None or line["outcome"] != "completed":
        raise SystemExit("no completed attempt %s in this run" % attempt_id)
    req = fsio.read_bytes(os.path.join(run_dir, line["request_file"]))
    resp = fsio.read_bytes(os.path.join(run_dir, line["response_file"]))
    return observe(run_dir, dict(line, role=cfg.get("observe_roles", ["agent"])[0]), req, resp)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) == 4 and sys.argv[1] == "probe":
        print(probe(os.path.abspath(sys.argv[2]), sys.argv[3]))
        raise SystemExit(0)
    if len(sys.argv) == 4 and sys.argv[1] == "verify-attempt":
        ok_, detail = verify_attempt(os.path.abspath(sys.argv[2]), sys.argv[3])
        print(json.dumps(detail, ensure_ascii=False, indent=1))
        raise SystemExit(0 if ok_ else 1)
    if len(sys.argv) >= 2 and sys.argv[1] == "breaker":
        if "--reset" in sys.argv:
            print("breaker reset" if reset_breaker() else "no breaker was open")
        else:
            print(json.dumps(breaker_open(), ensure_ascii=False, indent=1) or "breaker closed")
        raise SystemExit(0)
    if len(sys.argv) != 3 or sys.argv[1] not in ("flush", "verify"):
        raise SystemExit("usage: python neomundi_client.py flush|verify <run_dir>\n"
                         "       python neomundi_client.py probe <run_dir> <provider_attempt_id>\n"
                         "       python neomundi_client.py breaker [--reset]   (scope from TEST_PURCHASE_BUDGET_SCOPE)")
    run_dir = os.path.abspath(sys.argv[2])
    if sys.argv[1] == "flush":
        print(json.dumps(flush(run_dir), ensure_ascii=False, indent=1))
    else:
        ok, d = verify_links(run_dir)
        print(json.dumps(d, ensure_ascii=False, indent=1))
        sys.exit(0 if ok else 1)
