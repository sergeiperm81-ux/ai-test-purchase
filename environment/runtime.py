# -*- coding: utf-8 -*-
"""
The runtime a series is frozen under, and the check that a day runs on it.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

runtime_versions.json records the interpreter, the operating system and the exact version
of every package in requirements.txt, as read on the machine that runs the series (the
GitHub runner). The plan pins that file by checksum; every day, dry or paid, compares the
machine it runs on with it and refuses a difference. A changed runner image, a new patch
release of Python or a library that moved is a change of the stand, made before a plan and
never during a series.

Usage:  python runtime.py show      the runtime of this machine, as JSON
        python runtime.py record    write it to runtime_versions.json
        python runtime.py check     compare this machine with runtime_versions.json
"""
import os, sys, json, platform, datetime
from importlib import metadata

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
FILE = "runtime_versions.json"
FIELDS = ("python", "implementation", "os", "os_release", "architecture", "packages")


def pinned_path():
    return os.environ.get("TEST_PURCHASE_RUNTIME_FILE") or os.path.join(BASE, FILE)


def required_packages():
    """The package names of requirements.txt, each pinned there with ==."""
    names = []
    # next to the code in the runner and the working copy; one level up in the public repository
    here, above = os.path.join(BASE, "requirements.txt"), os.path.join(BASE, "..", "requirements.txt")
    path = here if os.path.exists(here) or not os.path.exists(above) else above
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(line.split("==", 1)[0].strip())
    return names


def os_release():
    """The distribution and its release (Ubuntu 24.04), never the kernel: the kernel of a
    hosted runner changes under the same image, and would stop a series for nothing."""
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path, encoding="utf-8") as f:
                rel = dict(l.strip().split("=", 1) for l in f if "=" in l)
            return ("%s %s" % (rel.get("NAME", "").strip('"'), rel.get("VERSION_ID", "").strip('"'))).strip()
        except OSError:
            continue
    if platform.system() == "Windows":
        return "Windows %s" % platform.release()
    return platform.system()


def current():
    packages = {}
    for name in required_packages():
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "os": platform.system(),
            "os_release": os_release(),
            "architecture": platform.machine(),
            "packages": packages}


def differences(pinned, now):
    out = []
    for k in FIELDS:
        if k == "packages":
            names = sorted(set(pinned.get(k) or {}) | set(now.get(k) or {}))
            for n in names:
                a, b = (pinned.get(k) or {}).get(n), (now.get(k) or {}).get(n)
                if a != b:
                    out.append("package %s: pinned %s, this machine %s" % (n, a, b))
        elif pinned.get(k) != now.get(k):
            out.append("%s: pinned %s, this machine %s" % (k, pinned.get(k), now.get(k)))
    return out


def check():
    """The differences between this machine and the pinned runtime; [] when they agree."""
    p = pinned_path()
    if not os.path.exists(p):
        return ["no %s: the runtime of the series is not pinned" % FILE]
    with open(p, encoding="utf-8") as f:
        return differences(json.load(f), current())


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "show":
        print(json.dumps(current(), ensure_ascii=False, indent=1))
    elif cmd == "record":
        out = dict(current(), recorded_at_utc=datetime.datetime.now(datetime.timezone.utc)
                   .strftime("%Y-%m-%dT%H:%M:%SZ"),
                   note="the runtime the series is frozen under, read on the machine that runs it")
        with open(os.path.join(BASE, FILE), "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print("recorded:", os.path.join(BASE, FILE))
    elif cmd == "check":
        diff = check()
        print("\n".join(diff) or "this machine matches the pinned runtime")
        sys.exit(1 if diff else 0)
    else:
        raise SystemExit("usage: python runtime.py show | record | check")


if __name__ == "__main__":
    main()
