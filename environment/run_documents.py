# -*- coding: utf-8 -*-
"""
The documents a run was actually made under.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

Every document of the package lives in the environment and is corrected as the
methodology develops. A purchase, an analysis and a frozen result must each be readable
against the versions that governed them, never against whatever the folder holds today.
So a run keeps its own copies and everything downstream reads them through here.

The order is fixed and there is no silent fallback:

  documents-as-analysed/   a later edition, created deliberately when an analysis has to
                           be made under revised documents. Whoever creates it states why;
  documents-at-start/      the package as loaded when the purchase began. This is the
                           normal case;
  the legacy flat copies   worksheet-as-analysed.txt and the like, kept working for the
                           runs made before the folders existed;
  nothing                  and then the caller stops and says which document is missing.
                           Reading the environment instead would mean judging a purchase
                           by rules it never saw, and that is the failure this module
                           exists to prevent.
"""
import os

NAMES = {
    "policy": "01_AI Policy.txt",
    "passport": "02_AI Service Passport.txt",
    "receipt_form": "03_AI Receipt form.txt",
    "agent_prompt": "04_Service agent prompt.txt",
    "purchaser_prompt": "05_Test purchaser prompt.txt",
    "analyst_prompt": "06_Analyst prompt.txt",
    "worksheet": "worksheet.txt",
    "rules": "deterministic_rules.json",
    "reference_state": "reference_state.json",
    "purchaser_rules": "purchaser_rules.json",
    "analysis_schema": "analysis_schema.json",
}

# A scripted-purchaser rules file did not exist in the earliest, model-purchaser runs.
# The analysis schema is not optional: without the run's own copy there is no stable
# definition of what Analysis.json was required to contain.
OPTIONAL = ("purchaser_rules",)

AS_ANALYSED = "documents-as-analysed"
AT_START = "documents-at-start"

LEGACY = {
    "worksheet": [(AS_ANALYSED, "worksheet-as-analysed.txt"),
                  (AT_START, "worksheet-at-start.txt")],
    "rules": [(AS_ANALYSED, "deterministic_rules-as-analysed.json"),
              (AT_START, "deterministic_rules-at-start.json")],
}

WHICH = {
    AS_ANALYSED: "the edition the analysis was made under",
    AT_START: "the package as loaded when the purchase began",
}


class Missing(Exception):
    """A document the run never kept. The caller stops rather than reading the environment."""


def resolve(run_dir, key):
    """Returns (path, provenance) for one document of the run."""
    name = NAMES[key]
    for folder in (AS_ANALYSED, AT_START):
        p = os.path.join(run_dir, folder, name)
        if os.path.exists(p):
            return p, WHICH[folder]
    for folder, flat in LEGACY.get(key, []):
        p = os.path.join(run_dir, flat)
        if os.path.exists(p):
            return p, WHICH[folder] + ", kept under its earlier file name"
    raise Missing(
        "this run kept no copy of %s. It cannot be analysed or frozen against the file in "
        "the environment: that file has been corrected since, and a purchase judged by "
        "documents it never saw is not evidence of anything. Either use a run that kept its "
        "documents, or place the version that governed this one in %s/ and say in "
        "%s/why.txt why a later edition is being used."
        % (name, AT_START, AS_ANALYSED))


def read(run_dir, key):
    path, provenance = resolve(run_dir, key)
    with open(path, encoding="utf-8") as f:
        return f.read(), provenance


def provenance(run_dir):
    """What every document of this run resolves to, for the record."""
    out = {}
    for key in NAMES:
        try:
            path, which = resolve(run_dir, key)
            out[key] = {"file": os.path.relpath(path, run_dir), "which": which}
        except Missing as e:
            if key in OPTIONAL:
                out[key] = {"file": None, "which": "not part of the package when this run "
                                                   "was made", "optional": True}
            else:
                out[key] = {"file": None, "which": str(e)}
    reason = os.path.join(run_dir, AS_ANALYSED, "why.txt")
    if os.path.exists(reason):
        out["why_a_later_edition_is_used"] = open(reason, encoding="utf-8").read().strip()
    return out
