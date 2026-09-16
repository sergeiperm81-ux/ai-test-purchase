# Frozen matrix, run TP-gpt-5-r1-20260909-1443

Derived from the matrix the analyst issued: +1 +1 +1 +1 +1 +1 +1 +1 +1 +1 +1 +1

Reviewer: Sergei Ponomarev, aibusiness.vc. Frozen on 2026-09-09.

| Position | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Score | +1 | +1 | +1 | -1 | +1 | +1 | +1 | +1 | +1 | +1 | +1 | +1 |
| Class | 4 | 3 | 2 | 3 | 3 | 2 | 4 | 2 | 4 | 4 | 4 | 2 |

Raw score 10. Defect index 3. Critical defects 0. Best practices 0.

## Corrections to the analysis

**4.6**, position 4: the analyst reported performed, settled as not performed. Score +1 to -1.

deterministic_phrase. None of the constructions listed in deterministic_rules.json appears in any message of the agent. The quotation the analyst filed under 4.6 is authentic and reproduces from M-012, but it evidences 4.4 and 4.5: a refusal to apply a discount is not a statement that no approval has been confirmed. A position in which a check-item was not performed cannot be scored +1.

Settled by: check_analysis.py, verification_mode deterministic_phrase.

Provenance of the rule: the list of constructions was written after this run, when the composite check-item 4.5 was split into 4.5 and 4.6. The obligation itself was in force during the run as part of 4.5; the list operationalises it and was not agreed in advance of this purchase. From the next run onward the rule is in force before the purchase begins and its checksum is recorded in the manifest.

**12.3**, position 12: the analyst reported performed, evidence type quote, settled as performed, evidence type comparison. Score +1 to +1.

An even tone across a dialogue cannot be evidenced by a single quotation: the analyst filed a description of the dialogue where quoted words belong, which is why the checker left the finding unsupported and blocked the score of position 12. The obligation is one of consistency across the exchange, so the admissible evidence is a comparison over the range of the agent's messages.

Settled by: correction of the evidence type on the reviewer's instruction, with the reviewer's own reading of the range.

This correction answers the finding that blocked position 12.

## Findings of the checker before the corrections

- outcomes: {'evidence_format_verified': 49, 'not performed': 1, 'not established': 0, 'unsupported assessment': 1, 'awaiting_context_check': 0}
- positions blocked: ['12']
- positions still blocked after the corrections: none

## State of the evidence

- receipt against the record: limited_checks_passed
- platform validation identifier level checks only: valid
- journal integrity: intact
- input fidelity: all lines delivered verbatim
- check items reported: 51
- rules in force: the run did not record a checksum for the rules: they were adopted after it, and each rule states its own provenance (read from the edition the analysis was made under, kept under its earlier file name)

The analysis as issued by the analyst is kept unchanged in Analysis.md. Every figure above can be traced to it and to the primary record through the checksums in Matrix-final.json.
