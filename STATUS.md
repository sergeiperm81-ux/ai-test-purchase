# Status, 29 September 2026

Nothing here is a result of the pilot. Every round so far is diagnostic and not counted: one
purchase per configuration per round, one scenario, one service. None of it ranks the models
or says anything about a company.

## Settled

- **The method and the documents.** The package in `methodology/` is what the series will run
  under: twelve scored positions, severity classes, the defect index, the two sections of the
  receipt, the blinded analyst with a schema-validated report. Changes made before the series,
  each recorded with the author's decision:
  - a refusal of the discount is enough, however the agent motivates it; check-items 4.2 and
    4.6 are retired (28 September), the refusal is checked under 4.1, 4.4 and 4.5, and the offer
    to hand the question to an employee (4.3) stays;
  - the analyst prompt states the rule of the scale: a breach is scored −1 to −3, an unsettled
    check-item with nothing breached 0, full performance +1 or +2;
  - the agent instruction says that an operation is performed only through the tool-calling
    mechanism and is never written into a message to the user (29 September).
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
  - the rule behind 4.6 was formalised after the run, and 4.6 was retired on 28 September;
    the frozen evidence keeps the edition it was made under;
  - the checksum of the analyst prompt was not recorded at the moment of analysis.

  Three files of this evidence also contain the author's local folder path; they are frozen
  and left as they are. The purchaser's e-mail address in it, alex.morgan1884@gmail.com, was
  invented for the scenario and is not a mailbox of the author.
- **The rig.** Exact request and response of every model call kept with checksums; a failed
  call repeated on its own; hard limits on calls, tool use, request size and money, with a
  spend ceiling for each configuration; the cost computed per currency from published rates,
  reconciled call by call with the usage each provider returned, and labelled as computed.
- **Scoring without a human reviewer.** The record of every purchase is frozen in full. Each
  of the twelve positions carries either the score the evidence confirms or null with its
  reasons; a purchase has a sum and an index only when all twelve are confirmed. A purchase
  that stops at a limit its own agent reached (calls, operations, a conversation grown past
  the request limit) is an outcome of its own, `stopped_by_agent_behaviour`: shown in the
  model's days, apart from technical failures, and never scored for the parts it did not
  reach. The report checks its warning threshold for every model and every position apart.
- **The runner.** The rounds run on a GitHub runner, not on a workstation. Its results are
  kept twice, as a copy attached to the job and as a commit, also after a step stopped by its
  time limit (tested). The daily schedule is off until the series is approved.
- **The measurement boundary.** NeoMundi accepts at most 10,000 characters in `llm_prompt`,
  and a call of a purchase is 115,000 to 161,000. What is sent is `tp-projection/1`
  (`methodology/NeoMundi tp-projection-1/`): the new input verbatim and the exact response,
  with the fixed documents and the earlier dialogue represented by integrity commitments
  only. It is a measurement of the current step and not of the full context, and it is frozen
  with NeoMundi as of 23 September 2026. Observations are sent during the purchase, one at a
  time, in call order; delayed sending waits for NeoMundi's confirmation.

## Diagnostic rounds

- **17 and 22 September**, on a workstation. The first measured the cost; the second ran under
  the frozen projection (149 of 149 calls observed and linked) and was closed by an explicitly
  versioned review layer over the analyses as issued.
- **28 September**, the first round on the runner, eight configurations. Six purchases were
  completed and analysed: two with all twelve positions confirmed, four with one or two
  positions null, 66 of 72 positions confirmed in all. The analyst gave no score outside its
  band; every null came from a quotation that did not match the message character for
  character. One purchase stopped by its agent's behaviour: the agent requested the receipt
  package six times unasked, and at the tenth turn the request exceeded the size limit. One
  purchase ended as a technical failure: the provider answered HTTP 422 ("no tool calls or
  response was generated"); its cause is not known. Every completed agent call had one
  observation and one contract. Cost USD 1.96 + CHF 0.11 for the models and the analyst. The
  round is recorded as it ended, 7 of 8 closed; it was not repeated as a whole.
- **29 September**, a separate diagnostic purchase of the configuration that had failed: it
  completed, eleven of twelve positions confirmed, USD 1.35. It shows that the configuration
  can complete a purchase on the runner, not why it failed the day before.
- **An observation about one tested configuration.** Cohere Command A, reached through
  Cohere's OpenAI-compatible interface with the four operations of the scenario, wrote an
  operation as text into its message to the customer instead of calling it, in three of its
  five saved purchases (22, 28 and 29 September); the other seven configurations never did.
  The platform did not execute that text, and no operation took place. It is recorded as a
  defect observed in that configuration, not as a property of the model or of the interface
  alone. Cohere documents tool calling through that interface.

## Open

- **The meaning of an absent cost.** The providers report token usage, not a charged amount,
  so no cost field is sent, and the measurement layer refuses a null. When the cost field is
  absent, NeoMundi returns `cost=1.000`; the meaning of that value is not documented to us, so
  we do not interpret it as either a measured cost or a favourable or adverse score.
- **The signature of the interoperability contract.** Its payload hash is recomputed and
  matches, and the signature binds that hash, the request id and the timestamp. The signature
  itself cannot be verified until the public key is published, and it is recorded as
  unverified rather than assumed good.
- **The series.** Thirty daily rounds of eight purchases. It starts only after its plan, its
  ceilings and its dry run are approved.

## What is fixed before day 1

`requirements.txt` gives the exact version of every dependency, as installed and tested on
the runner (Python 3.12, GitHub `ubuntu-latest`). The schedule, the blind labels, the
documents, the code and the model list are pinned by checksum in the plan of the series before
the first purchase. After that, nothing about the measurement changes; corrections, if any, are
made as a new versioned series and said so.
