# Status, 23 September 2026

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
- **The providers.** All eight answered a live setup check: seven on 15 September, DeepSeek on
  17 September. One call each, one correct tool call each, the requested model identifier
  reported by each API.
- **Two diagnostic rounds of eight purchases, not counted.** 17 and 22 September, one purchase
  per configuration. The first measured the cost (USD 2.83 + CHF 0.10 for the round, so about
  USD 85 + CHF 3 for a series of thirty) and found the limit of the measurement layer. The
  second ran under the frozen projection: every one of the 149 completed service-agent calls
  produced an accepted observation and a retrieved contract, and the governance answers were
  111 ALLOW and 38 FLAG.
- **The measurement boundary.** NeoMundi accepts at most 10,000 characters in `llm_prompt`,
  and a call of a purchase is 115,000 to 161,000. What is sent is `tp-projection/1`
  (`methodology/NeoMundi tp-projection-1/`): the new input verbatim and the exact response,
  with the fixed documents and the earlier dialogue represented by integrity commitments
  only. It is a measurement of the current step and not of the full context, and it is frozen
  with NeoMundi as of 23 September 2026.

## Open, and why the series has not started

- **The review of the second diagnostic round.** Seven of its eight purchases wait for the
  reviewer: the checker blocked a score in each, most often because a quotation of the analyst
  did not match the message character for character, and in some positions because an
  obligation was not performed. The round is closed with an explicitly versioned review layer
  over the analyses as they were issued; nothing of them is rewritten.
- **The meaning of an absent cost.** The providers report token usage, not a charged amount,
  so no cost field is sent, and the measurement layer refuses a null. When the cost field is
  absent, NeoMundi returns `cost=1.000`; the meaning of that value is not documented to us, so
  we do not interpret it as either a measured cost or a favourable or adverse score. The
  question is open with NeoMundi, and no estimate is sent as though it were measured.
- **The signature of the interoperability contract.** Its payload hash is recomputed and
  matches, and the signature binds that hash, the request id and the timestamp. The signature
  itself cannot be verified until the public key is published, and it is recorded as
  unverified rather than assumed good.
- **The scheduled runner.** The series runs one round a day for thirty days, and a round of
  the second rehearsal took 67 minutes of wall-clock time, of which 54 were spent waiting for
  the measurement layer between customer lines. The observations are now built at the moment
  of each call and sent when the purchase is over, and the round runs on a schedule that does
  not depend on a workstation being awake. The final dry run of that arrangement is the last
  step before day 1.

## What is fixed before day 1

The exact versions of Python and of every dependency, the operating system and the
architecture are recorded with the plan; `requirements.txt` gives lower bounds only, which
is enough for review but not for a frozen series. The schedule, the blind labels, the documents, the code and the model list are pinned by
checksum in the plan of the series before the first purchase. After that, nothing about the
measurement changes; corrections, if any, are made as a new versioned series and said so.
