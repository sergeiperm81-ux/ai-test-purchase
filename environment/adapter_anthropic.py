# -*- coding: utf-8 -*-
"""
The native Anthropic Messages API, behind the same interface as the others.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

Anthropic's OpenAI-compatible endpoint does not support prompt caching, ignores strict
and rewrites system messages; Anthropic describes it as a testing aid. The harness keeps
its conversation in the chat-completions shape, so this module translates that shape
into a Messages request and the answer back. The exact request that goes over the wire
is the translated one, and that is what the call log keeps.

Caching (5-minute ephemeral) is part of the frozen configuration: one breakpoint on the
last system block, which holds the policy, the passport and the receipt form, and one on
the last block of the conversation, so that every call reads the conversation so far from
the cache and writes only the new tail.
"""
import json

ENDPOINT = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"
EMPTY_TEXT = "[no text]"


def _blocks(content):
    if isinstance(content, list):
        return [dict(b) for b in content]
    text = content if isinstance(content, str) else ""
    return [{"type": "text", "text": text}] if text else []


def translate_messages(messages):
    """(system blocks, messages) in the Messages API shape. Consecutive messages of the
    same role are merged: the API requires the roles to alternate, and several tool
    results after one assistant turn belong to one user turn."""
    system, out = [], []
    for m in messages:
        role = m.get("role")
        if role == "system":
            system.extend(_blocks(m.get("content")))
            continue
        if role == "tool":
            role, blocks = "user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"],
                                     "content": m.get("content") or ""}]
        elif role == "assistant":
            blocks = _blocks(m.get("content"))
            for c in m.get("tool_calls") or []:
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                blocks.append({"type": "tool_use", "id": c["id"],
                               "name": c["function"]["name"], "input": args})
            if not blocks:
                blocks = [{"type": "text", "text": EMPTY_TEXT}]
        else:
            role, blocks = "user", _blocks(m.get("content")) or [{"type": "text", "text": EMPTY_TEXT}]
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
    return system, out


def translate_tools(tools):
    return [{"name": t["function"]["name"],
             "description": t["function"].get("description", ""),
             "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
            for t in tools or []]


def build_request(model, messages, tools, max_tokens, cache):
    system, msgs = translate_messages(messages)
    if cache:
        mark = {"type": "ephemeral"}
        if system:
            system[-1]["cache_control"] = mark
        if msgs and msgs[-1]["content"]:
            msgs[-1]["content"][-1]["cache_control"] = mark
    payload = {"model": model, "max_tokens": max_tokens, "messages": msgs}
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = translate_tools(tools)
    return payload


def headers(key):
    """The authorisation header is built at the moment of sending and never recorded."""
    return {"x-api-key": key, "anthropic-version": VERSION, "content-type": "application/json"}


def recorded_headers():
    return {"anthropic-version": VERSION, "content-type": "application/json"}


def parse_response(body):
    """(message in the chat-completions shape, finish reason). Raises ValueError when the
    body is not a complete Messages response."""
    if not isinstance(body, dict) or body.get("type") != "message" or "content" not in body:
        raise ValueError("not a complete Messages response")
    text, calls = [], []
    for b in body["content"]:
        if b.get("type") == "text":
            text.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            calls.append({"id": b["id"], "type": "function",
                          "function": {"name": b["name"],
                                       "arguments": json.dumps(b.get("input") or {},
                                                               ensure_ascii=False)}})
    msg = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        msg["tool_calls"] = calls
    # the chat-completions names: a vendor-specific stop reason in the manifest would tell
    # the blinded analyst which provider it is reading
    return msg, STOP_REASONS.get(body.get("stop_reason"), body.get("stop_reason"))


STOP_REASONS = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls",
                "max_tokens": "length"}
