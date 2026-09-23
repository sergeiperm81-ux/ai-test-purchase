# -*- coding: utf-8 -*-
"""
The shorter version of a provider request that goes to NeoMundi in llm_prompt.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

NeoMundi accepts at most 10,000 characters in llm_prompt (confirmed by NeoMundi on
21.09.2026: a limit of the present API, not a setting). A real call of a test purchase is
115,000 to 161,000 characters, because every call carries the documents the agent works
under and the whole dialogue so far. So instead of the request itself NeoMundi receives
its projection, a deterministic function of the exact request bytes, frozen with NeoMundi
(confirmed by email on 23.09.2026) and identified by its name:

  tp-projection/1
    full_request  the SHA-256 and the length of the exact request we sent the provider;
                  the request itself stays in our execution log and is tied to the
                  observation by NeoMundi's request_id
    model         the model named in the request
    fixed         the parts that are the same in every call of a purchase: every system
                  block (the agent's instructions, the AI Policy, the AI Service Passport,
                  the AI Receipt form, the operating data) and the tool definitions, each
                  as its SHA-256 and length
    history       the dialogue before this call, as a count, a SHA-256 and a length. An
                  integrity commitment only: NeoMundi receives the hash, not the content,
                  and the request carries no identifier that would let NeoMundi join earlier
                  observations into one dialogue
    new_input     what reached the agent since its last reply, verbatim: the customer's
                  line, or the results of the tools it called
    withheld      only when the projection would still exceed the limit: the content of
                  tool results in new_input, the largest first, is replaced by its SHA-256
                  and length until it fits. The customer's words are never withheld. If it
                  cannot fit, nothing is projected and the observation is not sent

full_request.sha256 is taken over the exact bytes of the provider request. Every other
SHA-256 (under fixed, history and withheld) is taken over the canonical JSON of the value:
keys sorted, UTF-8, no ASCII escapes, no insignificant spaces. chars is a count of Unicode
characters: of the decoded request for full_request, of the canonical JSON for the others.
The projection itself is canonical JSON, so the same request always gives the same bytes,
and anyone holding the request can recompute it and compare.

What this measures, and what it does not: NeoMundi reads the new input verbatim and the
provider response unchanged. The system material and the earlier dialogue reach it only
as integrity commitments, which let the retained request be verified later and do not
expose its content during the observation. This is a measurement of the current step, not
an equivalent of the full context.
"""
import hashlib
import json

PROJECTION_ID = "tp-projection/1"
KNOWN = (PROJECTION_ID,)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(value):
    text = canonical(value)
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "chars": len(text)}


def _split(req):
    """(system parts, dialogue messages) of an OpenAI-style or an Anthropic request."""
    messages = req.get("messages") or []
    system = [m.get("content") for m in messages if m.get("role") == "system"]
    top = req.get("system")
    if isinstance(top, list):
        system += top
    elif top:
        system.append(top)
    return system, [m for m in messages if m.get("role") != "system"]


def _is_tool_result_message(m):
    return m.get("role") == "tool"


def _tool_result_blocks(m):
    """The tool_result blocks of an Anthropic user message, as (index, block)."""
    content = m.get("content")
    if not isinstance(content, list):
        return []
    return [(i, b) for i, b in enumerate(content) if isinstance(b, dict) and b.get("type") == "tool_result"]


def _withholdable(new_input):
    """Every tool result in new_input as (size, message index, block index or None)."""
    out = []
    for mi, m in enumerate(new_input):
        if _is_tool_result_message(m):
            out.append((len(canonical(m.get("content"))), mi, None))
        for bi, b in _tool_result_blocks(m):
            out.append((len(canonical(b.get("content"))), mi, bi))
    return sorted(out, key=lambda x: (-x[0], x[1], -1 if x[2] is None else x[2]))


def project(request_bytes, limit):
    """The projection of one provider request, as a string, or None when even with every
    tool result withheld it would exceed the limit. Nothing but tool results is withheld."""
    req = json.loads(request_bytes.decode("utf-8"))
    system, dialogue = _split(req)
    last_agent = max([i for i, m in enumerate(dialogue) if m.get("role") == "assistant"], default=-1)
    before, new_input = dialogue[:last_agent + 1], json.loads(json.dumps(dialogue[last_agent + 1:]))
    body = {"projection": PROJECTION_ID,
            "full_request": {"sha256": hashlib.sha256(request_bytes).hexdigest(),
                             "chars": len(request_bytes.decode("utf-8"))},
            "model": req.get("model"),
            "fixed": {"system": [fingerprint(s) for s in system],
                      "tools": fingerprint(req.get("tools") or [])},
            "history": dict(fingerprint(before), messages=len(before)),
            "new_input": new_input}
    text = canonical(body)
    if len(text) <= limit:
        return text
    withheld = []
    for _, mi, bi in _withholdable(new_input):
        m = new_input[mi]
        holder = m if bi is None else m["content"][bi]
        marker = fingerprint(holder.get("content"))
        holder.pop("content", None)
        holder["content_withheld"] = marker
        withheld.append({"message": mi, "block": bi, **marker})
        body["withheld"] = withheld
        text = canonical(body)
        if len(text) <= limit:
            return text
    return None
