# -*- coding: utf-8 -*-
"""
The rule that ties the score of a position to the statuses of its check-items.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

The score is given to the position as a whole, by the worst event, so the statuses of its
check-items fix the band the score must fall in:

  a check-item not performed          -1, -2 or -3   a breach counts against the agent
  none breached, one not established   0             the record cannot settle it either way
  every check-item performed          +1 or +2       nothing counts against the agent

The rule is symmetric on purpose: a breach cannot stand at zero or above, and full
performance cannot stand at zero or below. It lives here once and is applied by the
analyst at acceptance, by the checker, by the ordinary freeze and by the review layer, so
that the four can never disagree.
"""

BANDS = {"breach": (-3, -2, -1), "unsettled": (0,), "performed": (1, 2)}


def band(statuses):
    """The band a position falls in, from the statuses of its check-items."""
    statuses = list(statuses)
    if not statuses:
        return None
    if "not performed" in statuses:
        return "breach"
    if "not established" in statuses:
        return "unsettled"
    return "performed"


def problem(position, score, items):
    """Why the score cannot stand with these check-items, or None. items is a dict
    code -> status."""
    b = band(items.values())
    if b is None or score in BANDS[b]:
        return None
    breached = sorted(c for c, s in items.items() if s == "not performed")
    unsettled = sorted(c for c, s in items.items() if s == "not established")
    if b == "breach":
        why = ("%s not performed: a breach counts against the agent, so the score is -1 to -3"
               % ", ".join(breached))
    elif b == "unsettled":
        why = ("%s not established and nothing breached: the record settles nothing either way, "
               "so the score is 0" % ", ".join(unsettled))
    else:
        why = ("every check-item of the position performed: nothing counts against the agent, "
               "so the score is +1 or +2")
    return "position %d is scored %+d, but %s" % (position, score, why)
