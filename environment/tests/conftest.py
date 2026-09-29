# -*- coding: utf-8 -*-
"""The tests run on whatever machine checks the code, not on the pinned runtime of the
series: a day in a test compares the machine with its own runtime, written here. The check
itself is tested against a pinned runtime that differs (test_rig)."""
import os, sys, json, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import runtime

_path = os.path.join(tempfile.mkdtemp(prefix="runtime-"), "runtime_versions.json")
with open(_path, "w", encoding="utf-8") as _f:
    json.dump(runtime.current(), _f)
os.environ["TEST_PURCHASE_RUNTIME_FILE"] = _path
