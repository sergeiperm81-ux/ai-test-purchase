# tp-projection/1

Normative specification of the value sent in `llm_prompt` of a NeoMundi observation in the Marina Keys test-purchase pilot. Proposed by Sergei Ponomarev on 21 September 2026. **Frozen**: NeoMundi (Sébastien Favre-Lecca) confirmed the measurement boundary of section 2 by email on 23 September 2026 and asked for it to be frozen for the counted series. Validated live on 22 September 2026: 149 of 149 completed service-agent calls of a round of eight purchases produced an accepted observation and a retrieved contract. The reference implementation is the Python module `projection.py`, shipped in this folder as `projection.py.txt` so that mail filters let it through (rename it back to run it; the bytes are unchanged); its SHA-256 is in `vectors.json`. Where this text and the code disagree, the disagreement is a defect to be settled before the freeze, not a choice left to the implementation.

## 1. Why

NeoMundi accepts at most 10,000 characters in `llm_prompt`. A call of a test purchase is 115,000 to 161,000 characters, because every call carries the documents the agent works under and the whole dialogue so far. The limit is part of the present NeoMundi API (NeoMundi, 21 September 2026).

## 2. Scope of the measurement

`tp-projection/1` is a deterministic projection of the exact provider-request bytes. It preserves the new input verbatim, and `llm_response` remains the exact provider response, unchanged. The fixed system material and the earlier dialogue are represented by integrity commitments only: their hashes allow the retained provider request to be verified later, but do not expose their semantic content to NeoMundi during the observation. The request carries no identifier that lets NeoMundi join observations into one dialogue.

A NeoMundi measurement made under this projection is therefore a measurement of the current step: the verbatim new input and the exact response. It is not a measurement of the response against the full context, and must not be described as one. NeoMundi confirmed this boundary on 23 September 2026 in these terms: NeoMundi measures the verbatim new input and the exact model response; the fixed documents and prior history are not transmitted in full and are represented through their integrity commitments. The independent cryptographic verification of contract signatures is a separate interoperability item: NeoMundi will provide the public key of the signing key so that the contracts can be verified and not only hash-checked locally.

## 3. Input

The exact bytes of the request sent to the provider (UTF-8 JSON) and the limit `L` (10,000). Two request shapes occur:

- OpenAI-compatible: `messages` is a list; system material is the messages with `role` = `system`; tool results are messages with `role` = `tool`.
- Anthropic Messages: system material is the top-level `system` (a string, or a list of blocks); tool results are blocks with `type` = `tool_result` inside `user` messages.

## 4. Definitions

- **canonical JSON** of a value: JSON with keys sorted, UTF-8, non-ASCII characters not escaped, separators `,` and `:` with no insignificant spaces (Python: `json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`).
- **fingerprint** of a value: `{"sha256": SHA-256 of the UTF-8 bytes of its canonical JSON, "chars": number of Unicode characters of its canonical JSON}`.
- **system parts**: in order, the `content` of every `messages` entry with `role` = `system`; then the top-level `system` (each block of it if it is a list, the value itself if it is a non-empty string).
- **dialogue**: the `messages` entries whose `role` is not `system`, in order.
- **last agent reply**: the last dialogue entry with `role` = `assistant`. **history** is the dialogue up to and including it; **new_input** is the dialogue after it. If there is none, history is empty and new_input is the whole dialogue.

## 5. Output

The canonical JSON of this object (key order in the text follows canonical sorting):

| Key | Value |
|---|---|
| `projection` | `"tp-projection/1"` |
| `full_request` | `{"sha256": SHA-256 of the exact request bytes, "chars": number of Unicode characters of the decoded request}` |
| `model` | the `model` value of the request |
| `fixed` | `{"system": [fingerprint of each system part, in order], "tools": fingerprint of the request's tools, or of [] if absent}` |
| `history` | fingerprint of the history list, with `"messages": its length` |
| `new_input` | the new_input entries, verbatim |
| `withheld` | present only if section 6 applied |

## 6. When it does not fit

If the output exceeds `L` characters, tool results in new_input are withheld one at a time, the largest first (by characters of the canonical JSON of their content; ties by message index, then by block index with a whole message first), and the output is recomputed after each, until it fits:

- the tool result's `content` key is removed and `"content_withheld": fingerprint of that content` is added in its place;
- an entry `{"message": index in new_input, "block": index of the block or null, "sha256", "chars"}` is appended to `withheld`.

Nothing else is ever withheld: the customer's words, the agent's earlier words and the response are never shortened. If the output still exceeds `L` when every tool result is withheld, there is no projection and the observation is not sent; the call is recorded on our side as `oversize`.

## 7. Verification

Given the retained provider request, anyone can recompute the projection and compare it byte for byte with the `llm_prompt` NeoMundi received. `vectors.json` gives two test vectors: the exact provider request, the exact projection and their SHA-256. Our own check (`neomundi_client.verify_links`) recomputes the projection of every observation from the retained request, and a purchase whose projections do not match is not frozen.

## 8. Measured on the rehearsal of 17 September 2026

All 170 calls project within the limit: 823 to 8,769 characters, 1,107 on average. A tool result was withheld in 5 calls, always the receipt data package the platform hands the agent once per purchase (9,000 to 13,000 characters). The longest provider response was 6,349 characters.
