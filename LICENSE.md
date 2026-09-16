# Licence

Copyright 2026 Sergei Ponomarev, aibusiness.vc. Two licences, because the code and the
method are different things, and the boundary between them runs through `environment/`.

## The code: MIT

The program code in `environment/` is under the **MIT licence** (`environment/LICENSE`):
every `*.py` file, `environment/tests/`, and the configuration and tooling files that describe
how the code reaches providers and counts money (`models.json`, `rates.json`,
`neomundi_schema_v1.json`, `.gitignore`), plus `requirements.txt`, `.gitattributes` and
`.github/`. Run it, change it, fork it, send corrections.

## The methodology and the documents: CC BY-NC-ND 4.0

Everything that states the method itself is under
**Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International**:

- `methodology/`, the documents and the prompts of each role;
- the methodological data files that live inside `environment/` because the code reads them:
  - `environment/worksheet.txt`, the purchaser's worksheet and the check-items;
  - `environment/purchaser_rules.json`, the fixed answers of the scripted purchaser;
  - `environment/deterministic_rules.json`, the rules the receipt is checked by;
  - `environment/reference_state.json`, the reference state of the test environment;
  - `environment/analysis_schema.json`, the shape the analysis must take;
- `README.md`, `STATUS.md`, `LICENSE.md`, the reports published here, and `evidence/`.

The MIT licence of the code does not extend to these files. You may read them, quote them
with attribution, and run the package with them unchanged against your own AI service.
Commercial use, or publishing a modified version of the method, needs a separate written
agreement.

Full text: https://creativecommons.org/licenses/by-nc-nd/4.0/legalcode

## Participants in the pilot

Participants in the joint pilot hold a non-exclusive right to use this package for the pilot
itself. Commercial use beyond the pilot is by separate agreement.

## Attribution

> Test purchase methodology for AI agents. Sergei Ponomarev, aibusiness.vc, 2026.

## Contact

info@aibusiness.vc
