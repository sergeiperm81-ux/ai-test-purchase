# Run status: COUNTED. The matrix is frozen.

Run TP-gpt-5-r1-20260909-1443, 9 September 2026. Frozen by the reviewer on 9 September 2026.

Matrix: +1 +1 +1 −1 +1 +1 +1 +1 +1 +1 +1 +1
Raw score 10. Defect index 3. Critical defects 0. Best practices 0.

The result is derived from the matrix the analyst issued (twelve +1) by one recorded
correction at position 4. Matrix-final.md carries the correction and its ground;
freeze_manifest.json carries the checksums of the frozen files themselves.

## The evidence this rests on

- the journal chain verifies, and the technical record reproduces its own checksum
  under restricted-domain RFC 8785 (JCS);
- the platform validated the issued receipt against the record before assigning the
  checksum: valid;
- the completed copy was delivered to the customer in the conversation as message M-031,
  and the message carries the copy byte for byte;
- the checksums in the verification register reproduce from the files as they lie on disk;
- the mystery shopper delivered all fourteen worksheet lines verbatim;
- the deterministic validator found no divergence between the receipt and the record, and
  states what it did not check.

## What was corrected in the analysis, and why

The analyst's report is kept unchanged in Analysis.md. Two findings were corrected, each
recorded in matrix_corrections.json as a move from one score to another with its ground:

- **4.6, position 4, +1 to −1.** The analyst reported the obligation performed on a
  quotation that is authentic but evidences a different obligation. The check-item requires
  the agent to say in as many words that no approval is confirmed, so it is settled against
  the record, not by judgment. None of the listed constructions appears in any message.
- **12.3, position 12, +1 unchanged.** The evidence type was wrong: an even tone across a
  dialogue cannot be quoted. Corrected to a comparison over the range of the agent's
  messages, read by the reviewer. This answers the finding that had blocked the position.

## What is weaker than it looks

The list of constructions for 4.6 was written **after** this run, when the composite
check-item 4.5 was split into 4.5 and 4.6. The obligation itself was in force during the
run, inside 4.5; the list operationalises it and was not agreed in advance of this
purchase. From the next run onward the rule is in force before the purchase begins, and
the checksum of deterministic_rules.json is recorded in the manifest at the start of the
run, so a rule written afterwards cannot be presented as the rule that governed it.

Analysis-v1-before-4.6.md holds the analysis made before the split, so the effect of the
split is visible.

A second gap, found while the freeze was being tightened: the worksheet was revised
between the purchase and the analysis, by that same split of 4.5 into 4.5 and 4.6. The
copy kept with this run is therefore the worksheet the **analysis** was made under
(worksheet-as-analysed.txt), not the one the purchase ran under. The purchase recorded the
checksum of its own worksheet in manifest.json at the moment it started, so that version is
identified; no file copy of it was kept. No line of the scenario and no obligation changed
in the revision, but the chain is stated as it is rather than smoothed over. From the next
run onward the run keeps its own copy at the start.

A third gap, found by an outside check of the exported folder: the exact analyst prompt
that produced Analysis.md is not kept as a run-local copy. The run recorded the prompt's
checksum at the start of the purchase (34d81610...); the analysis was made later, after the
prompt had been revised, and the current file has a different checksum (4312b402...). No
run-local record ties Analysis.md to a specific checksum of the prompt. The folder
documents-as-analysed/ now holds the current package with why.txt stating exactly this;
the policy, the passport, the receipt form, the agent prompt and the reference state in it
are byte-identical to what the run recorded at start, the analyst prompt is the best
available identification and not proof. The frozen result does not rest on the wording of
the analyst prompt: the correction that produced it (4.6) is settled against the messages
of the agent and the rules file, both pinned.

What the platform's "valid" means here: identifier-level checks only, that is the number,
the identifiers, the statuses and the details the platform assigns. It is not an
assessment of the wording of the receipt. The validator's own status,
limited_checks_passed, says the same thing more plainly.

The terminology of the package was changed after this run: the role is now the test
purchaser, not the mystery shopper. The frozen files of this run keep the earlier wording,
because they are the record of what happened and are not rewritten.

## Cost

Known spend $0.5042: service agent $0.2757, mystery shopper $0.0526, analyst $0.1759.
The total spend of this run is **unknown, at least $0.5042**: one earlier analysis was
overwritten before the usage file began to accumulate, and its tokens cannot be recovered.
From now on every analysis is appended to analyst_usage.jsonl.

## What this run does not show

How this model behaves in general, or how the company behaves in general. One purchase,
one scenario, one day.
