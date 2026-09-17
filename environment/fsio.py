# -*- coding: utf-8 -*-
"""
Reading files without leaving them open.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

A bare open(...).read() leaves the closing of the file to the garbage collector. On Windows
that can hold a file for a moment after it was read, and a second process that wants the
same file then meets a transient refusal. Every read of the environment goes through here,
where the file is closed before the function returns.
"""
import json


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_text(path, encoding="utf-8"):
    with open(path, encoding=encoding) as f:
        return f.read()


def read_json(path, encoding="utf-8"):
    with open(path, encoding=encoding) as f:
        return json.load(f)


def read_lines(path, encoding="utf-8"):
    """The lines of a text file, as iteration over the open file would give them."""
    with open(path, encoding=encoding) as f:
        return f.readlines()
