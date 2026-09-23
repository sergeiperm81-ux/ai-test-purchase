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
        matrix = [1, 1, 0] + [0] * 9
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
                                                   "text": "I recommend A-712 over A-518."}})
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
        self.assertTrue(any("position 2 stands at +1 with 2.1 not performed" in p for p in problems))
        to_zero = {"kind": "correction", "position": 2, "check_items": ["2.1"], "from_score": 1,
                   "to_score": 0, "ground": "g"}
        problems = self.settle(self.REPLACE, to_zero)[-1]
        self.assertTrue(any("needs -1 or below" in p for p in problems))
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


if __name__ == "__main__":
    unittest.main()
