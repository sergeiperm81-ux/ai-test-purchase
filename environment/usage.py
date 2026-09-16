# -*- coding: utf-8 -*-
"""
Token usage in one shape, whatever the provider, and what it costs.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

Every provider reports usage in its own fields, and the cached part of the input in
different ones again, or not at all. So the raw usage is always kept as received, and next
to it one normalised record:

  input_tokens        every input token of the call, cached or not
  cached_input_tokens the part read from the provider's cache; None when the provider
                      does not report it (then the call is priced as uncached, and the
                      cost says so)
  cache_write_tokens  the part written to the cache (Anthropic); 0 where there is none
  output_tokens       every output token, reasoning included
  reasoning_tokens    the reasoning part of the output, when reported

A price is looked up by the rate_key of the configuration, exactly, never by the model name
the provider reports: the same model is sold by two providers in two currencies. The name
tables of rates.json ("legacy_by_model_name") serve only runs made before configurations
had rate keys. Money is never added across currencies.
"""
import os, json, math

BASE = os.path.dirname(os.path.abspath(__file__))
RATES_FILE = os.path.join(BASE, "rates.json")
LONG_CONTEXT = 200000
# Every token of a request carries at least one byte of its text; the chat template and the
# rendering of tool definitions add tokens with no bytes in the request, bounded here.
TEMPLATE_TOKENS = 4096


def _int(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def normalise(protocol, raw):
    """The usage of one response in one shape, or None when the input and output counts are
    not both in fields this module recognises. An unrecognised shape is unknown usage, never
    zero: zero would release the reservation of a call that may have been charged."""
    u = raw if isinstance(raw, dict) else {}
    if protocol == "anthropic_messages":
        if _int(u.get("input_tokens")) is None or _int(u.get("output_tokens")) is None:
            return None
        plain = _int(u.get("input_tokens")) or 0
        read = _int(u.get("cache_read_input_tokens")) or 0
        write = _int(u.get("cache_creation_input_tokens")) or 0
        return {"input_tokens": plain + read + write, "cached_input_tokens": read,
                "cache_write_tokens": write, "output_tokens": _int(u.get("output_tokens")) or 0,
                "reasoning_tokens": None, "cache_reported": True}
    has_input = _int(u.get("prompt_tokens")) is not None or (
        _int(u.get("prompt_cache_hit_tokens")) is not None and _int(u.get("prompt_cache_miss_tokens")) is not None)
    if not has_input or _int(u.get("completion_tokens")) is None:
        return None
    prompt = _int(u.get("prompt_tokens")) or 0
    out = _int(u.get("completion_tokens")) or 0
    details = u.get("prompt_tokens_details") or {}
    cached = _int(details.get("cached_tokens")) if isinstance(details, dict) else None
    if "prompt_cache_hit_tokens" in u:                       # DeepSeek
        cached = _int(u.get("prompt_cache_hit_tokens")) or 0
        miss = _int(u.get("prompt_cache_miss_tokens"))
        if miss is not None and not prompt:
            prompt = cached + miss
    cdet = u.get("completion_tokens_details") or {}
    reasoning = _int(cdet.get("reasoning_tokens")) if isinstance(cdet, dict) else None
    return {"input_tokens": prompt, "cached_input_tokens": cached, "cache_write_tokens": 0,
            "output_tokens": out, "reasoning_tokens": reasoning,
            "cache_reported": cached is not None}


def load_rates(path=None):
    with open(path or RATES_FILE, encoding="utf-8") as f:
        return json.load(f)


def rate(rate_key, rates=None):
    """The rate of a configuration, by its exact rate_key, or None."""
    if not rate_key:
        return None
    return (rates or load_rates()).get("configurations", {}).get(rate_key)


def _tier(r, input_tokens):
    if input_tokens >= LONG_CONTEXT and r.get("over_200k"):
        return dict(r, **r["over_200k"])
    return r


def _amount(r, norm, basis):
    cached = norm.get("cached_input_tokens")
    if cached is None:
        cached = 0
        basis.append("the provider did not report cached input: priced as uncached")
    elif cached and r.get("cached_input") is None:
        basis.append("no cached-input rate: cached input priced as uncached")
        cached = 0
    write = norm.get("cache_write_tokens") or 0
    plain = max(norm["input_tokens"] - cached - write, 0)
    return (plain * r["input"] + cached * (r.get("cached_input") or 0)
            + write * r.get("cache_write", r["input"]) + norm["output_tokens"] * r["output"]) / 1e6


def price(rate_key, norm, rates=None):
    """Computed cost of one call: {"amount", "currency", "rate_key", "basis"} or None."""
    r = rate(rate_key, rates)
    if not r:
        return None
    r = _tier(r, norm["input_tokens"])
    basis = []
    amount = _amount(r, norm, basis)
    return {"amount": round(amount, 6), "currency": r["currency"], "rate_key": rate_key,
            "basis": "; ".join(basis) or "as reported"}


def price_legacy(model, norm, rates=None):
    """For runs made before rate keys: the longest listed name the model equals or extends
    with a dated suffix (gpt-4.1-mini-2025-04-14 is priced as gpt-4.1-mini)."""
    table = (rates or load_rates()).get("legacy_by_model_name", {})
    best = None
    for name in table:
        if model and (model == name or model.startswith(name + "-")):
            if best is None or len(name) > len(best):
                best = name
    if not best:
        return None
    r = _tier(table[best], norm["input_tokens"])
    basis = ["run made before rate keys: priced by the model name %s" % best]
    return {"amount": round(_amount(r, norm, basis), 6), "currency": r["currency"],
            "rate_key": None, "basis": "; ".join(basis)}


def upper_bound(rate_key, request_bytes, max_output_tokens, rates=None):
    """The most one attempt can cost, for the reservation made before it is sent: every
    byte of the request a token, plus the template allowance, all uncached, at the long
    context tier when it could apply, plus the full output allowance."""
    r = rate(rate_key, rates)
    if not r:
        return None
    tokens_in = request_bytes + TEMPLATE_TOKENS
    r = _tier(r, tokens_in)
    amount = (tokens_in * r["input"] + (max_output_tokens or 0) * r["output"]) / 1e6
    # rounded up, never down: a reservation below the bound would not be a bound
    return {"amount": math.ceil(round(amount * 1e6, 6)) / 1e6, "currency": r["currency"],
            "basis": "upper bound: %d request bytes + %d template tokens uncached, %d output "
                     "tokens" % (request_bytes, TEMPLATE_TOKENS, max_output_tokens or 0)}
