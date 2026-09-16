# -*- coding: utf-8 -*-
"""
Checks a frozen result against the files it pins.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

A freeze manifest is only worth what someone can do with it, so this is the tool that
does it: take the anchor kept outside the run folder, recompute every checksum, and say
plainly which files still reproduce and which do not. It reads nothing from the frozen
result except the manifest itself, and it changes nothing.

A file that no longer reproduces is not by itself proof of anything improper. It means
the frozen result and the file on disk are no longer the same thing, and the difference
has to be explained before the result is quoted again.

Usage:  python verify_freeze.py <anchor.freeze.json | run_dir>
"""
import os, sys, json, hashlib

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))


def sha256_file(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def locate(argument):
    """Either the anchor itself, or a run folder whose manifest is inside it."""
    if os.path.isdir(argument):
        return os.path.join(argument, "freeze_manifest.json"), argument
    manifest = os.path.abspath(argument)
    run_id = json.load(open(manifest, encoding="utf-8")).get("run_id")
    return manifest, os.path.join(BASE, "runs", run_id)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python verify_freeze.py <anchor.freeze.json | run_dir>")
    manifest_path, run_dir = locate(sys.argv[1])
    if not os.path.exists(manifest_path):
        raise SystemExit("no freeze manifest at %s" % manifest_path)
    freeze = json.load(open(manifest_path, encoding="utf-8"))

    print("run          :", freeze.get("run_id"))
    print("frozen on    :", freeze.get("frozen_on"), "by", freeze.get("reviewer"))
    print("manifest     :", manifest_path)
    print("run folder   :", run_dir)
    print()

    ok, changed, missing = [], [], []
    for name, recorded in (freeze.get("files") or {}).items():
        path = os.path.join(run_dir, name)
        actual = sha256_file(path)
        if recorded is None:
            continue
        if actual is None:
            missing.append(name)
        elif actual == recorded:
            ok.append(name)
        else:
            changed.append((name, recorded, actual))

    for name in ok:
        print("  reproduces      ", name)
    for name in missing:
        print("  MISSING         ", name)
    for name, recorded, actual in changed:
        print("  DOES NOT MATCH  ", name)
        print("      recorded    ", recorded)
        print("      on disk     ", actual)

    env = freeze.get("environment_files_at_the_time_of_freezing") or {}
    if env:
        print()
        print("environment files, recorded for information only:")
        for name, recorded in env.items():
            if name == "note":
                continue
            actual = sha256_file(os.path.join(BASE, name))
            state = ("unchanged" if actual == recorded else
                     ("absent" if actual is None else "changed since the freeze"))
            print("  %-28s %s" % (name, state))
        print(" ", env.get("note", ""))

    print()
    if changed or missing:
        print("VERDICT: the frozen result and the files no longer agree. %d file(s) differ, "
              "%d missing. The difference has to be explained before the result is quoted "
              "again." % (len(changed), len(missing)))
        sys.exit(1)
    print("VERDICT: every file the freeze pins reproduces its checksum. The result stands "
          "as frozen on %s." % freeze.get("frozen_on"))


if __name__ == "__main__":
    main()
