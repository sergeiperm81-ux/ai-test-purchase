# Status, 16 September 2026

## Settled

- **The method and the documents.** The package in `methodology/` is what the series will run
  under: twelve scored positions, severity classes, the defect index, the two sections of the
  receipt, the blinded analyst with a schema-validated report.
- **One counted purchase.** 9 September 2026, in `evidence/first-counted-purchase/`: eleven
  positions at +1, one at −1 (the agent did not say that the director's approval was not
  confirmed), defect index 3, no critical defect. Frozen and reproducible.
- **The rig.** Exact request and response of every model call kept with checksums; a failed
  call repeated on its own; hard limits on calls, tool use and money; the cost computed per
  currency from published rates and labelled as computed.
- **The providers.** Seven of the eight answered a live setup check on 15 September: one call
  each, one correct tool call each, the requested model identifier reported by each API. The
  check cost USD 0.0015 and CHF 0.00003 in total.

## Open, and why the series has not started

- **DeepSeek.** The eighth configuration; the credential has not arrived yet.
- **An unavailable cost is currently measured as a zero cost.** The measurement layer refuses
  a null cost, and omitting the field produces a perfect cost score. Neither is honest for a
  provider that reports no cost. Waiting for the correct representation.
- **The signature of the interoperability contract.** Its payload hash is recomputed and
  matches, and the signature binds that hash, the request id and the timestamp. The signature
  itself cannot be verified until the public key is published, and it is recorded as
  unverified rather than assumed good.
- **A technical run.** Two or three configurations over two or three days, not counted, to
  measure what the series will actually cost and to pin the model identifiers each provider
  serves. Then the configuration is frozen and day 1 begins.

## What is fixed before day 1

The schedule, the blind labels, the documents, the code and the model list are pinned by
checksum in the plan of the series before the first purchase. After that, nothing about the
measurement changes; corrections, if any, are made as a new versioned series and said so.
