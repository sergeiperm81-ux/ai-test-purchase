# -*- coding: utf-8 -*-
"""
Canonical JSON under RFC 8785 (JCS), restricted domain, for every checksum here.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

The AI Receipt form requires the checksum to be calculated over the canonical
representation under RFC 8785. Anything less than that has to be declared, and a
record that declares a deviation cannot then call itself valid under its own form.
So the rules are implemented rather than approximated:

  - object keys are sorted by their UTF-16 code units, not by code points;
  - numbers are serialised by the ECMAScript Number::toString rules;
  - strings use the minimal JSON escapes and are emitted as UTF-8;
  - no insignificant whitespace.

Where a value falls outside the domain this serialiser can guarantee, it raises
Unsupported instead of producing a string that only looks canonical. The caller
turns that into receipt_validation_status = not_verified with the reason stated.
"""
import json, math

SUPPORTED = ("restricted-domain RFC 8785 (JCS). The rules of RFC 8785 are implemented as "
             "written for the values this environment produces: objects, arrays, strings, "
             "booleans, null, integers within the safe range of a double, and finite numbers "
             "whose ECMAScript representation needs no exponent. Numbers that JCS allows but "
             "that require exponent notation, such as 1e30 or 1e-27, are refused rather than "
             "approximated, so this is not a general-purpose JCS implementation.")

SAFE_INT = 2 ** 53 - 1


class Unsupported(Exception):
    """A value this serialiser cannot canonicalise with certainty."""


def _number(x):
    """ECMAScript Number::toString, for the range the record actually uses."""
    if isinstance(x, int):
        if abs(x) > SAFE_INT:
            raise Unsupported("integer outside the safe range of a double: %r" % x)
        return str(x)
    if not math.isfinite(x):
        raise Unsupported("non-finite number: %r" % x)
    if x == 0:
        return "0"                       # ECMAScript prints -0 as 0
    if abs(x) >= 1e21 or abs(x) < 1e-6:
        raise Unsupported("number needing exponent notation: %r" % x)
    if float(x).is_integer():
        return str(int(x))
    s = repr(float(x))                   # shortest round-trip, as in ECMAScript
    if "e" in s or "E" in s:
        raise Unsupported("number needing exponent notation: %r" % x)
    return s


def _string(s):
    # json.dumps applies exactly the minimal JSON escapes; ensure_ascii=False keeps UTF-8
    return json.dumps(s, ensure_ascii=False)


def _key_order(k):
    """UTF-16 code units, as RFC 8785 requires. Differs from code-point order only
    for characters outside the basic plane, which is precisely why it is spelled out."""
    if not isinstance(k, str):
        raise Unsupported("object key that is not a string: %r" % k)
    return k.encode("utf-16-be")


def dumps(obj):
    out = []
    _write(obj, out)
    return "".join(out)


def _write(v, out):
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, str):
        out.append(_string(v))
    elif isinstance(v, (int, float)):
        out.append(_number(v))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, item in enumerate(v):
            if i:
                out.append(",")
            _write(item, out)
        out.append("]")
    elif isinstance(v, dict):
        out.append("{")
        for i, k in enumerate(sorted(v.keys(), key=_key_order)):
            if i:
                out.append(",")
            out.append(_string(k))
            out.append(":")
            _write(v[k], out)
        out.append("}")
    else:
        raise Unsupported("value of type %s" % type(v).__name__)


def check(obj):
    """Returns (True, note) where the object can be canonicalised under RFC 8785,
    otherwise (False, the reason). Used before a record calls itself valid."""
    try:
        dumps(obj)
        return True, SUPPORTED
    except Unsupported as e:
        return False, "the record cannot be canonicalised under RFC 8785: %s" % e
