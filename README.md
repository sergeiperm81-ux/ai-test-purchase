# Test purchase of an AI agent

A test purchase is the oldest consumer-protection tool there is: you walk in as an ordinary
customer, ask for the ordinary thing, and see whether the shop does what it promised. This
repository applies it to an AI agent that serves customers, and it does so as evidence, not
as an impression.

The company publishes what its AI is allowed to do and must do: a policy, a service passport
and the form of the receipt the customer gets. A scripted customer then asks for one
ordinary thing, word for word, every time. What the agent says and what the platform
actually did are both recorded, and a separate, blinded analyst scores the run against the
company's own promises, one line per promise.

Nothing here is a ranking of models. It measures one thing only: whether an AI service did
what its own published standard said it would do.

## The case under test

Marina Keys Realty, a small real-estate agency. Its AI assistant takes appointments to view an
apartment, hands a request to a human when it should, and issues the customer an AI Receipt
of what happened. Everything about the agency is fictional; the behaviour under test is not.

## What a run consists of

**Three roles, kept apart on purpose.**

- **The service agent.** The model under test. It reads the agency's policy, its service
  passport and the receipt form, and it can call the operations of an instrumented test
  platform, run by a simulator rather than a live system: create a
  viewing, hand a request over to an employee, check that the handover was delivered,
  request the receipt.
- **The purchaser.** Not a model. A script that says the lines of the worksheet in order,
  word for word, with fixed answers to the clarifying questions the instruction provides
  for. A model in this seat would make every run a different purchase and the comparison
  meaningless.
- **The analyst.** A separate model, not an organisationally independent auditor, that never
  sees which model it is judging: the names of
  the model, the provider and the endpoint are withheld from the record before it is read.
  It returns a structured report validated against a schema, and that report, not any prose,
  is the result.

**The record.** Every platform operation goes into a hash-chained journal. Every message is
kept with its own identifier. The receipt has two sections: section I is what the assistant
told the customer, section II is what the platform assembled from the journal. Comparing the
two is how a polite, well-written, untrue receipt is caught.

**The score.** Twelve positions, each tied to the promises behind it, scored from +2 to −3
by how far the promise was not kept. Every position carries a severity class, and the defect
index weighs each negative score by that class. A position may be scored above zero only if
every obligation inside it was performed and evidenced; where the evidence is missing, the
result says so instead of guessing.

**The freeze.** When a run is scored, its files are checksummed into a freeze manifest and
anchored. `verify_freeze.py` recomputes those checksums later, so a change to a published
result shows. That protection is only as strong as a copy of the anchor, or of the commit
hash, kept outside this repository: whoever controls the repository could rewrite the
evidence, the anchor and the history together. Keep the commit hash you reviewed.

## What is in this repository

| Folder | What it holds |
|---|---|
| `methodology/` | the package as published: research design, the agency's AI policy, the AI service passport, the AI receipt form, the purchaser's worksheet, the analysis matrix, and the prompts each role is run with |
| `environment/` | the code that runs a purchase, checks the receipt against the journal, runs the analyst, checks the analysis, freezes the result, counts the cost, and runs a series of days |
| `evidence/first-counted-purchase/` | one complete counted purchase of 9 September 2026, a frozen result with disclosed provenance gaps (see STATUS.md): transcript, message log, operation journal, both sections of the receipt, the analysis, the checker's verdict, the frozen matrix, the cost, and the freeze manifest |
| `evidence/*.freeze.json` | the anchor of that result |

## What is deliberately not here

- **API keys.** The code reads them from the environment by name and never writes one into a
  run, a log or an error. `secrets_check.py` reports only present or absent.
- **The mapping of blind labels to model names** during a series. It is a separate file kept
  out of the repository; the plan carries only a salted commitment to it, so the schedule can
  be published without revealing which model is which.
- **Correspondence and commercial terms.**

## Running it yourself

Python 3.12. From the root of the repository:

```
python -m pip install -r requirements.txt
cd environment
python -m pytest tests -q                # 113 tests; no provider is called, no key is needed
python verify_freeze.py ../evidence/TP-gpt-5-r1-20260909-1443.freeze.json ../evidence/first-counted-purchase
```

The last command recomputes the 22 checksums of the published evidence. The same three steps
run on every push, on Linux and on Windows, in `.github/workflows/tests.yml`.

To run a purchase yourself you need two keys, one for the model in the seat of the service
agent and one for the analyst's model; when both are served by the same provider, one key
serves both. The models and the names of their key variables are in `models.json`; the keys
themselves go into the environment or a local `.env`, which is ignored by git.

```
python secrets_check.py                 # names of the keys: present or absent, nothing else
python harness.py --model <model>       # one purchase, scripted purchaser
python validate_receipt.py runs/<run>   # the receipt against the journal
python analyst.py --run <run>           # the blinded analyst, structured output
python check_analysis.py runs/<run>     # is the analysis checkable at all
python freeze_matrix.py runs/<run>      # score and freeze
python cost.py runs/<run>               # what it cost, per currency
```

Every model call is recorded before it is sent: the exact request bytes, the exact response,
the attempts, the limits and the computed cost. A failed call is repeated on its own; a
complete answer is never asked for again, whatever it says.

## Status

This is a pre-release. The method and the documents are settled and are what the pilot will
run under. The integration code is still being finished, and the tag for the series will be
set before day 1, after a short technical run. Everything that changes between now and then
is visible in the history of this repository.

## The pilot ahead

Eight configurations from seven model families, one purchase per configuration per day, for
thirty days, in a fixed daily window, with the order of the models drawn in advance from a
published seed. Each purchase is also observed by NeoMundi's runtime measurement layer, which
is independent of this scoring and is kept as a separate linked artefact. Weekly reports and
a final report are published here.

## Licence

The code in `environment/` is MIT: run it, change it, fork it, send corrections. The
methodology and the documents are CC BY-NC-ND 4.0: read and quote with attribution, run the
package as it stands, commercial use or a modified method by agreement. See LICENSE.md.

## Author

Sergei Ponomarev, aibusiness.vc. Seven years of consumer test purchases, now applied to AI
services. Comments and objections are welcome as issues: an objection before the series
starts is worth more than any praise after it.
