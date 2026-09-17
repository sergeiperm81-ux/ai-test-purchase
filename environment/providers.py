# -*- coding: utf-8 -*-
"""
Model providers for the harness.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

models.json names each configuration once: its configuration_id, provider, endpoint,
protocol, the NAME of the environment variable that holds its key, its rate_key, the output
limit and the model ids the provider is expected to report. The key itself lives in the
environment or in .env and is read at the moment of the call. Nothing in this module
prints, logs or returns a key, and no recorded file receives one.

Protocols:
  openai_chat         OpenAI, xAI, Mistral, Gemini, Cohere (compatibility API), Infomaniak,
                      DeepSeek
  anthropic_messages  the native Anthropic Messages API (adapter_anthropic.py)

Every call goes through a call_log.Recorder. For every attempt, in this order: the most it
could cost is reserved against the spend ceiling; the exact request is written; the request
is sent; the result is recorded and the reservation settled. A technical failure (429, 5xx,
a timeout, an incomplete body) repeats only this call, after Retry-After when the provider
sends one, and each repeat needs its own reservation. A complete answer is returned and
never asked for again, whatever it says.
"""
import os, json, time, datetime, email.utils, urllib.request, urllib.error, http.client
import fsio

import call_log
import adapter_anthropic

BASE = os.path.dirname(os.path.abspath(__file__))
# A series points this at its frozen copy.
MODELS = os.environ.get("TEST_PURCHASE_MODELS_FILE", os.path.join(BASE, "models.json"))

PROTOCOLS = ("openai_chat", "anthropic_messages")
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
BACKOFF_S = (5, 20, 60)
MAX_WAIT_S = 120
KEEP_RESPONSE_HEADERS = ("date", "retry-after", "x-request-id", "request-id",
                         "openai-processing-ms", "x-ratelimit-remaining-requests",
                         "x-ratelimit-remaining-tokens", "anthropic-ratelimit-requests-remaining")

ProviderCallFailed = call_log.ProviderCallFailed
ModelDrift = call_log.ModelDrift
LimitExceeded = call_log.LimitExceeded


def _data():
    if not os.path.exists(MODELS):
        return {}
    with open(MODELS, encoding="utf-8") as f:
        return json.load(f)


def _config():
    data = _data()
    entries = list(data.get("models", [])) + list(data.get("auxiliary_models", []))
    return {m["model"]: m for m in entries}


def limits():
    return dict(_data().get("limits") or {})


def neomundi_config():
    return dict(_data().get("neomundi") or {})


def pilot():
    return dict(_data().get("pilot") or {})


def configuration_version():
    if not os.path.exists(MODELS):
        return None
    return call_log.sha256_bytes(fsio.read_bytes(MODELS))


def config_for(model):
    cfg = _config().get(model)
    if cfg is None:
        raise SystemExit("model %s is not configured in %s: no call is made to an unlisted "
                         "model" % (model, os.path.basename(MODELS)))
    return dict(cfg)


def _endpoint_template(c):
    tail = "/messages" if c.get("protocol") == "anthropic_messages" else "/chat/completions"
    return c["base_url"].rstrip("/") + tail


def describe(model):
    """What the record says about the provider: name, endpoint, protocol. No key, and the
    endpoint as a template: an account number in it is not written into the record."""
    c = config_for(model)
    return {"provider": c.get("provider"), "configuration_id": c.get("configuration_id"),
            "protocol": c.get("protocol", "openai_chat"),
            "endpoint": _endpoint_template(c), "key_env": c.get("key_env"),
            "listed_in_models_json": True}


def generation_parameters(model):
    """What the platform sets on every call of this configuration. Everything else is the
    provider's default."""
    c = config_for(model)
    anthropic = c.get("protocol") == "anthropic_messages"
    chosen = {("max_tokens" if anthropic else c.get("max_tokens_param", "max_tokens")):
              c.get("max_output_tokens")}
    for k in ("reasoning_effort", "temperature"):
        if k in c:
            chosen[k] = c[k]
    if c.get("cache"):
        chosen["prompt_cache"] = c["cache"].get("mode")
    return {"set_by_the_configuration": chosen, "everything_else": "the provider's defaults"}


def _key(env_name):
    if os.environ.get(env_name):
        return os.environ[env_name]
    env = os.path.join(BASE, ".env")
    if os.path.exists(env):
        with open(env, encoding="utf-8-sig") as f:
            for line in f:
                if line.startswith(env_name + "="):
                    value = line.split("=", 1)[1].strip()
                    if value:
                        return value
    raise SystemExit("%s not found (environment variable or .env). The key is never "
                     "written into the run; put it where the harness reads it." % env_name)


def key_present(env_name):
    """True or False, and nothing else: for the pre-flight check."""
    try:
        _key(env_name)
        return True
    except SystemExit:
        return False


def _url(c):
    url = _endpoint_template(c)
    if "{product_id}" in url:
        url = url.replace("{product_id}", _key(c["product_id_env"]))
    return url


def _confirmed(c):
    if not c.get("confirmation_required"):
        return
    import confirm
    ok, detail = confirm.status(c["model"], call_log.budget_scope())
    if not ok:
        raise LimitExceeded("model %s requires confirmation before it is called: %s. Record the "
                            "owner's confirmations with confirm.py" % (c["model"], detail))


def build_payload(c, messages, tools, response_format):
    limit = c.get("max_output_tokens")
    if c.get("protocol") == "anthropic_messages":
        if response_format:
            raise SystemExit("response_format is not supported on the Messages adapter")
        return adapter_anthropic.build_request(c["model"], messages, tools, limit,
                                               cache=(c.get("cache") or {}).get("mode") == "ephemeral_5m")
    payload = {"model": c["model"], "messages": messages}
    if tools:
        payload["tools"] = tools
    if response_format:
        payload["response_format"] = response_format
    if limit:
        payload[c.get("max_tokens_param", "max_tokens")] = limit
    for k in ("temperature", "reasoning_effort"):
        if k in c:
            payload[k] = c[k]
    return payload


def _wait_s(headers, attempt):
    ra = headers.get("retry-after")
    wait = None
    if ra:
        try:
            wait = float(ra)
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(ra)
                wait = (when - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                wait = None
    if wait is None:
        wait = BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)]
    return max(0.0, min(wait, MAX_WAIT_S))


def _send(url, headers, body, timeout):
    """(status, body bytes, headers lower-cased, error name). Never raises for HTTP."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}, None
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}, None
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException,
            OSError) as e:
        return None, None, {}, type(e).__name__


def _parse(c, raw):
    """(message, finish_reason, body) of a complete answer; raises ValueError otherwise."""
    body = json.loads(raw)
    if c.get("protocol") == "anthropic_messages":
        msg, finish = adapter_anthropic.parse_response(body)
        return msg, finish, body
    choice = body["choices"][0]
    msg = choice["message"]
    if not isinstance(msg, dict):
        raise ValueError("no message in the choice")
    return msg, choice.get("finish_reason"), body


def chat(model, messages, tools=None, timeout=180, response_format=None, recorder=None,
         role="agent"):
    """One model call. Returns (message, meta). Raises LimitExceeded (BudgetExceeded),
    ProviderCallFailed or ModelDrift; every attempt made is recorded in every case."""
    if recorder is None:
        raise SystemExit("providers.chat needs a call_log.Recorder: no call is made unrecorded")
    c = config_for(model)
    if c.get("protocol", "openai_chat") not in PROTOCOLS:
        raise SystemExit("model %s is listed with protocol %s, and no adapter for it exists"
                         % (model, c.get("protocol")))
    _confirmed(c)
    payload = build_payload(c, messages, tools, response_format)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    op = recorder.begin(role, c)
    url = _url(c)
    key = _key(c["key_env"])
    if c.get("protocol") == "anthropic_messages":
        send_headers, rec_headers = adapter_anthropic.headers(key), adapter_anthropic.recorded_headers()
    else:
        send_headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        rec_headers = {"Content-Type": "application/json"}
    del key
    retries = int(recorder.limits.get("max_call_retries", 3))
    scale = float(os.environ.get("TEST_PURCHASE_RETRY_SCALE", "1"))
    last_error = None
    for attempt in range(1, retries + 2):
        base = {"operation_id": op, "attempt": attempt, "role": role, "model": model,
                "configuration_id": c.get("configuration_id"), "rate_key": c.get("rate_key"),
                "provider": c.get("provider"), "protocol": c.get("protocol", "openai_chat"),
                "endpoint": _endpoint_template(c), "request_bytes": body,
                "request_headers": rec_headers}
        recorder.reserve(recorder.attempt_id(op, attempt), role, c, len(body))
        recorder.prepare(base)
        started = call_log.utc_now()
        status, raw, headers, err = _send(url, send_headers, body, timeout)
        ended = call_log.utc_now()
        rec = dict(base, started=started, ended=ended, http_status=status, response_bytes=raw,
                   response_headers={k: headers[k] for k in KEEP_RESPONSE_HEADERS if k in headers})
        retryable = status is None or status in RETRYABLE_STATUS
        if status == 200:
            try:
                msg, finish, parsed = _parse(c, raw)
            except (ValueError, KeyError, IndexError, TypeError) as e:
                err, retryable = "incomplete body: %s" % type(e).__name__, True
            else:
                rec.update(outcome="completed", usage_raw=parsed.get("usage"),
                           observed_model=parsed.get("model"), finish_reason=finish,
                           response_id=parsed.get("id"))
                line = recorder.complete(rec)
                expected = c.get("expected_observed_models")
                if expected and parsed.get("model") not in expected:
                    raise ModelDrift("model %s: the provider reported %r, the configuration "
                                     "pins %s" % (model, parsed.get("model"), expected))
                # usage goes into the manifest normalised only: the raw field names and the
                # rate identify the provider. Both are in calls/calls.jsonl.
                meta = {"response_id": parsed.get("id"), "model_reported": parsed.get("model"),
                        "provider": c.get("provider"), "endpoint": _endpoint_template(c),
                        "created": parsed.get("created"),
                        "usage_normalised": line["usage_normalised"],
                        "system_fingerprint": parsed.get("system_fingerprint"),
                        "finish_reason": finish, "latency_ms": line["latency_ms"],
                        "operation_id": op, "provider_attempt_id": line["provider_attempt_id"],
                        "attempts": attempt, "request_sha256": line["request_sha256"]}
                return msg, meta
        elif status is not None and err is None:
            err = "HTTP %d" % status
        final = (not retryable) or attempt == retries + 1
        wait = None if final else _wait_s(headers, attempt) * scale
        rec.update(outcome="failed", error=err, wait_before_next_s=wait)
        recorder.complete(rec)
        last_error = err
        if final:
            break
        time.sleep(wait)
    raise ProviderCallFailed("provider %s, model %s: no complete answer after %d attempt(s), "
                             "last error %s" % (c.get("provider"), model, attempt, last_error))
