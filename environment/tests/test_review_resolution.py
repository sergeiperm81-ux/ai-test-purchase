# -*- coding: utf-8 -*-
"""
review-resolution/1, checked on a small made-up run: which decisions it accepts, which it
refuses, and that the rule of the worksheet holds after the decisions as it holds before.
"""
import os, sys, json, shutil, tempfile, hashlib, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
for p in (BASE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import review_resolution as rr

WORKSHEET = """5. Severity class (weight), control layer and mark
No. | Position | Class (weight) | Control layer | Mark measured
""" + "".join("%d | P%d | %d | model | not applied\n" % (i, i, 2) for i in range(1, 13)) + """6. Obligations inside the positions (check-items)
Code | Position | Obligation
1.1 | 1 | one
1.2 | 1 | two
2.1 | 2 | three
3.1 | 3 | four
"""


def row(code, pos, status, outcome, ev):
    return {"code": code, "position": str(pos), "obligation": "x", "status_reported": status,
            "evidence_type": ev["type"], "evidence": ev, "note": "", "problems": [], "outcome": outcome}


class TestReviewResolution(unittest.TestCase):
    def setUp(self):
        self.run = os.path.join(tempfile.mkdtemp(prefix="rr-"), "RUN-RR")
        os.makedirs(self.run)
        self.w("worksheet-as-analysed.txt", WORKSHEET)
        self.w("messages.jsonl", "\n".join(json.dumps(m) for m in [
            {"message_id": "M-01", "sender": "AI", "text": "I recommend **A-712** over A-518."},
            {"message_id": "M-02", "sender": "customer", "text": "Please book it."}]) + "\n")
        self.w("journal.jsonl", json.dumps({"event_id": "E-01", "operation": "create_viewing",
                                            "status": "confirmed"}) + "\n")
        self.w("state.json", '{"viewings": []}')
        matrix = [1, 1, 1] + [0] * 9
        self.w("Analysis.json", json.dumps({"matrix": matrix}))
        self.w("analysis_check.json", json.dumps({
            "rows": [row("1.1", 1, "performed", "evidence_format_verified",
                         {"type": "quote", "message_id": "M-01", "text": "A-518"}),
                     row("1.2", 1, "performed", "unsupported assessment",
                         {"type": "quote", "message_id": "M-01", "text": "I recommend A-712 over A-518."}),
                     row("2.1", 2, "performed", "not performed",
                         {"type": "quote", "message_id": "M-01", "text": "A-518"}),
                     row("3.1", 3, "performed", "evidence_format_verified",
                         {"type": "quote", "message_id": "M-01", "text": "A-518"})],
            "check_items_where_the_analyst_was_overridden": ["2.1"]}))

    def tearDown(self):
        shutil.rmtree(os.path.dirname(self.run), ignore_errors=True)

    def w(self, name, text):
        with open(os.path.join(self.run, name), "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def settle(self, *decisions):
        res = {"run_id": "RUN-RR", "resolutions": list(decisions)}
        return rr.settle(self.run, res)

    REPLACE = {"kind": "evidence_replacement", "position": 1, "check_items": ["1.2"], "ground": "g",
               "evidence": {"1.2": {"type": "quote", "message_id": "M-01", "text": "I recommend **A-712** over A-518."}}}
    OVERRIDE = {"kind": "contextual_override", "position": 2, "check_items": ["2.1"], "ground": "g",
                "evidence": {"2.1": {"type": "quote", "message_id": "M-01", "text": "over A-518"}}}

    def test_the_two_score_keeping_kinds_close_the_run(self):
        issued, scores, status, applied, problems = self.settle(self.REPLACE, self.OVERRIDE)
        self.assertEqual(problems, [])
        self.assertEqual(scores, issued)
        self.assertEqual(status["2.1"], "performed")

    def test_a_replacement_must_pass_the_checker_and_answer_a_blocked_item(self):
        bad = dict(self.REPLACE, evidence={"1.2": {"type": "quote", "message_id": "M-01",
                                                   "text": "I would recommend A-712 over A-518."}})
        problems = self.settle(bad, self.OVERRIDE)[-1]
        self.assertTrue(any("does not pass the checker" in p for p in problems))
        unblocked = dict(self.REPLACE, check_items=["1.1"],
                         evidence={"1.1": {"type": "quote", "message_id": "M-01", "text": "A-518"}})
        problems = self.settle(unblocked, self.OVERRIDE)[-1]
        self.assertTrue(any("1.1 was not blocked" in p for p in problems))
        self.assertTrue(any("1.2 (position 1) was blocked and no decision answers it" in p for p in problems))

    def test_an_override_only_for_a_deterministic_verdict_and_never_moving_the_score(self):
        wrong = dict(self.OVERRIDE, position=3, check_items=["3.1"],
                     evidence={"3.1": {"type": "quote", "message_id": "M-01", "text": "A-518"}})
        problems = self.settle(self.REPLACE, self.OVERRIDE, wrong)[-1]
        self.assertTrue(any("3.1 was not settled by a deterministic rule" in p for p in problems))
        moving = dict(self.OVERRIDE, to_score=2)
        problems = self.settle(self.REPLACE, moving)[-1]
        self.assertTrue(any("does not move the score" in p for p in problems))

    def test_a_breach_left_standing_needs_minus_one_even_after_review(self):
        problems = self.settle(self.REPLACE)[-1]                     # 2.1 stays not performed at +1
        self.assertTrue(any("position 2 is scored +1, but 2.1 not performed" in p for p in problems))
        to_zero = {"kind": "correction", "position": 2, "check_items": ["2.1"], "from_score": 1,
                   "to_score": 0, "ground": "g"}
        problems = self.settle(self.REPLACE, to_zero)[-1]
        self.assertTrue(any("-1 to -3" in p for p in problems))
        to_minus = dict(to_zero, to_score=-1)
        self.assertEqual(self.settle(self.REPLACE, to_minus)[-1], [])

    def test_a_correction_must_start_from_what_the_analyst_issued(self):
        c = {"kind": "correction", "position": 2, "check_items": ["2.1"], "from_score": 2,
             "to_score": -1, "ground": "g"}
        problems = self.settle(self.REPLACE, c)[-1]
        self.assertTrue(any("from_score 2, the analyst issued +1" in p for p in problems))

    def test_a_status_correction_must_be_evidenced(self):
        s = {"kind": "status_correction", "position": 3, "status_changes": {"3.1": "not established"},
             "ground": "g"}
        problems = self.settle(self.REPLACE, self.OVERRIDE, s)[-1]
        self.assertTrue(any("new status of 3.1 is not evidenced" in p for p in problems))

    def test_only_an_approved_record_lets_a_decision_file_be_frozen(self):
        base = {"run_id": "RUN-RR"}
        # a non-empty string is not an approval, whatever it says
        self.assertIn("not a record", rr.approval_problem(dict(base, approval="PENDING: not yet approved",
                                                               reviewer="X", decided_on="2026-09-23"), True))
        self.assertIn("not a record", rr.approval_problem(dict(base, approval="approved", reviewer="X",
                                                               decided_on="2026-09-23"), True))
        # a draft: checkable, never freezable, and naming nobody
        draft = dict(base, approval={"status": "pending"})
        self.assertIsNone(rr.approval_problem(draft, for_freeze=False))
        self.assertIn("pending", rr.approval_problem(draft, for_freeze=True))
        self.assertIn("names a reviewer", rr.approval_problem(dict(draft, reviewer="Sergei"), False))
        self.assertIn("names a reviewer", rr.approval_problem(dict(draft, decided_on="2026-09-23"), False))
        # an approval must carry the exact words, their author, the time and the source
        partial = dict(base, reviewer="Sergei", decided_on="2026-09-23",
                       approval={"status": "approved", "text": "Approved", "author": "Sergei"})
        self.assertIn("at, source", rr.approval_problem(partial, True))
        full = dict(partial, approval=dict(partial["approval"], at="2026-09-23T15:00:00+03:00",
                                           source="chat with Claude"))
        self.assertIsNone(rr.approval_problem(full, True))
        self.assertIn("status", rr.approval_problem(dict(full, approval=dict(full["approval"], status="ok")), True))

    def test_an_approval_given_in_several_messages_keeps_them_apart(self):
        base = {"run_id": "RUN-RR", "reviewer": "Sergei", "decided_on": "2026-09-23"}
        one = {"text": "I approve version 4", "author": "Sergei", "at": "2026-09-23T11:27:27Z", "source": "chat, uuid a"}
        two = {"text": "Card 10, minus 1, yes.", "author": "Sergei", "at": "2026-09-23T11:28:45Z", "source": "chat, uuid b"}
        ok = dict(base, approval={"status": "approved", "author": "Sergei", "messages": [one, two]})
        self.assertIsNone(rr.approval_problem(ok, True))
        no_time = dict(base, approval={"status": "approved", "author": "Sergei", "messages": [one, dict(two, at="")]})
        self.assertIn("message 2 must state at", rr.approval_problem(no_time, True))
        someone_else = dict(base, approval={"status": "approved", "author": "Sergei",
                                            "messages": [one, dict(two, author="Codex")]})
        self.assertIn("another author", rr.approval_problem(someone_else, True))
        self.assertIn("non-empty list", rr.approval_problem(
            dict(base, approval={"status": "approved", "author": "Sergei", "messages": []}), True))

    def test_the_final_state_is_a_source_only_with_its_checksum(self):
        with open(os.path.join(self.run, "state.json"), "rb") as f:
            good = hashlib.sha256(f.read()).hexdigest()
        known = rr.check_analysis.sources(self.run)
        ev = {"type": "comparison", "sources": ["M-01", "state.json@" + good], "compared": "claim against state"}
        self.assertEqual(rr.admissible(self.run, ev, known), [])
        ev["sources"] = ["M-01", "state.json@" + "0" * 64]
        self.assertTrue(rr.admissible(self.run, ev, known))
        ev["sources"] = ["M-01", "final state"]
        self.assertTrue(any("not identifiers of this run" in p for p in rr.admissible(self.run, ev, known)))


class TestPositionsInTheReport(unittest.TestCase):
    """A null position stays out of every mean; the share of null positions per model is
    over the positions of analysed purchases only; pending, failed and missing are shown
    apart; a purchase has a sum only when all twelve positions are confirmed."""

    def data(self):
        full = {"day": 1, "kind": "analysed", "full": True, "raw": 6, "index": 4,
                "matrix": [1] * 10 + [-1, -1], "status": "frozen"}
        part = {"day": 2, "kind": "analysed", "full": False, "raw": None, "index": None,
                "matrix": [None, None, None] + [1] * 9, "status": "partial"}
        return {"A": [full, part, {"day": 3, "kind": "pending", "status": "pending_measurement"}],
                "B": [dict(full, raw=-2, index=12), {"day": 2, "kind": "failed", "status": "technical_failure"}]}

    def test_the_denominator_is_the_positions_of_analysed_purchases(self):
        import series_report as sr
        pl = {"labels": ["A", "B"]}
        shares = sr.coverage(pl, self.data())
        self.assertEqual(shares["A"], (3, 24, 0.125))     # the pending purchase is not in it
        self.assertEqual(shares["B"], (0, 12, 0.0))       # neither is the failed one

    def test_the_threshold_is_per_model_and_position_not_over_the_twelve(self):
        # position 4 never confirmed for A: 1 position in 12 is 8% of the whole, under a 20%
        # threshold on the whole, and 100% of that cell, which is what the threshold is about
        import series_report as sr
        pl = {"labels": ["A", "B"]}
        day = lambda d: {"day": d, "kind": "analysed", "full": False, "raw": None, "index": None,
                         "matrix": [1, 1, 1, None] + [1] * 8, "status": "partial"}
        data = {"A": [day(1), day(2)], "B": self.data()["B"]}
        self.assertLess(sr.coverage(pl, data)["A"][2], 0.2)
        over = sr.over_threshold(pl, data, 0.2)
        self.assertEqual(over, [("A", 4)])
        pos = sr.positions(pl, data, over)
        self.assertIn("| 4 | 0/2, mean withheld |", pos[5])      # coverage kept, mean withheld
        self.assertIn("| 1 | 2/2, mean +1.00 |", pos[2])          # other cells untouched
        # one unconfirmed in five is 20%, not above it
        five = {"A": [dict(day(d), matrix=[1] * 12, full=True, raw=12, index=0) for d in range(1, 5)] + [day(5)]}
        self.assertEqual(sr.over_threshold({"labels": ["A"]}, five, 0.2), [])

    def test_counts_apart_and_means_only_over_full_purchases(self):
        import series_report as sr
        pl = {"labels": ["A", "B"]}
        rows = sr.purchases(pl, self.data(), [1, 2, 3])
        self.assertIn("| A | 3 | 2 | 1 | 1 | 1 | 0 | 0 | 3 of 24 (12%) |", rows[2])
        self.assertIn("| B | 3 | 1 | 1 | 0 | 0 | 1 | 1 | 0 of 12 (0%) |", rows[3])
        cmp_rows = sr.comparison(pl, self.data())
        self.assertIn("| A | 1 | 6.00 | 4.00 |", cmp_rows[2])     # the partial purchase is not in the mean
        pos = sr.positions(pl, self.data())
        self.assertIn("| 1 | 1/2, mean +1.00 | 1/1, mean +1.00 |", pos[2])


if __name__ == "__main__":
    unittest.main()
