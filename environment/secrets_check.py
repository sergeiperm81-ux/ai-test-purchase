# -*- coding: utf-8 -*-
"""
Pre-flight check of the keys a series needs. Prints present or absent per variable name
and nothing else: no value, no length, no prefix. Run it before a day starts.

Checked: the key of every configuration, the product id of an endpoint that needs one,
the analyst's key, and the NeoMundi key when observations are enabled.

Usage: python secrets_check.py [models.json]
"""
import os, sys, json
import fsio

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import providers


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "models.json")
    data = fsio.read_json(path)
    names = []

    def need(n):
        if n and n not in names:
            names.append(n)

    configurations = list(data.get("models", [])) + list(data.get("auxiliary_models", []))
    for m in configurations:
        need(m.get("key_env"))
        need(m.get("product_id_env"))
    analyst = (data.get("roles") or {}).get("analyst") or {}
    if isinstance(analyst, dict) and analyst.get("model"):
        selected = next((m for m in configurations if m.get("model") == analyst["model"]), None)
        if not selected:
            print("analyst model %s has no configuration" % analyst["model"])
            sys.exit(1)
        need(selected.get("key_env"))
    nm = data.get("neomundi") or {}
    if nm.get("enabled"):
        if not nm.get("key_env"):
            print("neomundi is enabled but names no key_env")
            sys.exit(1)
        need(nm["key_env"])
    missing = 0
    for n in names:
        ok = providers.key_present(n)
        print("%-30s %s" % (n, "present" if ok else "ABSENT"))
        missing += 0 if ok else 1
    gi = os.path.join(BASE, ".gitignore")
    ignored = os.path.exists(gi) and ".env" in fsio.read_text(gi)
    print(".env in .gitignore:", "yes" if ignored else "NO: fix this before anything is published")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
