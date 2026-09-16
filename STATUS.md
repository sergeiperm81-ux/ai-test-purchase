# Status, 16 September 2026

## Settled

- **The method and the documents.** The package in `methodology/` is what the series will run
  under: twelve scored positions, severity classes, the defect index, the two sections of the
  receipt, the blinded analyst with a schema-validated report.
- **One counted purchase: a frozen result with disclosed provenance gaps.** 9 September 2026,
  in `evidence/first-counted-purchase/`: eleven positions at +1, one at −1 (the agent did not
  say that the director's approval was not confirmed), defect index 3, no critical defect.
  The frozen files reproduce their 22 checksums. What cannot be reproduced is the whole
  process that led to them, and the run says so itself
  (`documents-as-analysed/why.txt`, `Matrix-final.json`):
  - the run was made before the harness copied the package into every run folder, so its
    documents were identified afterwards by checksum;
  - the purchase and the analysis used different editions of the worksheet: check-item 4.5
    was split into 4.5 and 4.6 after the purchase;
  - the rule behind 4.6 was formalised after the run;
  - the checksum of the analyst prompt was not recorded at the moment of analysis.

  The next clean run will not have these gaps: every run now keeps its documents and records
  what the analyst read. Three files of this evidence also contain the author's local folder
  path; they are frozen and left as they are. The purchaser's e-mail address in it,
  alex.morgan1884@gmail.com, was invented for the scenario and is not a mailbox of the
  author.
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

The exact versions of Python and of every dependency, the operating system and the
architecture are recorded with the plan; `requirements.txt` gives lower bounds only, which
is enough for review but not for a frozen series. The schedule, the blind labels, the documents, the code and the model list are pinned by
checksum in the plan of the series before the first purchase. After that, nothing about the
measurement changes; corrections, if any, are made as a new versioned series and said so.
