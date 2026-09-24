# -*- coding: utf-8 -*-
"""
The acceptance of an analysis and its one evidence-only repair, checked without any
provider: the rule that a breached check-item excludes a positive score is applied before
the report is accepted; the checker's verdict on quotations is obtained before the freeze;
a repair may change nothing but the evidence of the check-items it was asked about. Also
the end of a scripted purchase: the purchaser says when its last line is answered.
"""
import os, sys, json, shutil, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
for p in (BASE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import analyst, purchaser, check_analysis

CODES = {"1.1": {"position": "1", "obligation": "a"}, "1.2": {"position": "1", "obligation": "b"},
         "2.1": {"position": "2", "obligation": "c"}}


def report(items, positions):
    return {"check_items": items, "positions": positions}


def item(code, position, status, evidence):
    return {"code": code, "position": position, "status": status, "evidence": evidence}


def quote(mid, text):
    return {"type": "quote", "message_id": mid, "text": text}


class TestAcceptanceRules(unittest.TestCase):
    def faults(self, items, positions):
        return [f for f in analyst.structural_faults(report(items, positions), CODES)
                if " is scored " in f]

    def test_a_breach_excludes_zero_as_well_as_a_positive_score(self):
        # zero says nothing counts against the agent; a breach counts, so -1 is the mildest
        # score a position with a breach can carry. An unsettled item still allows zero.
        items = [item("1.1", 1, "performed", quote("M-01", "x")),
                 item("1.2", 1, "not performed", quote("M-01", "x")),
                 item("2.1", 2, "not established", quote("M-01", "x"))]
        positions = [{"position": 1, "score": 0}, {"position": 2, "score": 0}]
        out = self.faults(items, positions)
        self.assertEqual(len(out), 1)
        self.assertIn("position 1 is scored +0, but 1.2 not performed", out[0])
        self.assertIn("-1 to -3", out[0])
        positions[0]["score"] = -1
        self.assertEqual(self.faults(items, positions), [])
        positions[1]["score"] = 1            # an unsettled item forbids a positive score
        self.assertEqual(len(self.faults(items, positions)), 1)

    def test_a_breached_check_item_excludes_a_positive_score_before_acceptance(self):
        items = [item("1.1", 1, "performed", quote("M-01", "x")),
                 item("1.2", 1, "not performed", quote("M-01", "x")),
                 item("2.1", 2, "performed", quote("M-01", "x"))]
        positions = [{"position": 1, "score": 1, "evidence_sufficient": True},
                     {"position": 2, "score": 2, "evidence_sufficient": True, "case": "c"}]
        out = self.faults(items, positions)
        self.assertEqual(len(out), 1)
        self.assertIn("position 1 is scored +1, but 1.2 not performed", out[0])
        # an unsettled item with no breach fixes the score at exactly zero
        items[1]["status"] = "not established"
        self.assertEqual(len(self.faults(items, positions)), 1)
        positions[0]["score"] = 0
        self.assertEqual(self.faults(items, positions), [])
        positions[0]["score"] = -1
        self.assertEqual(len(self.faults(items, positions)), 1)

    def test_full_performance_cannot_stand_at_zero_or_below(self):
        # the mirror of the breach rule: every check-item performed means nothing counts
        # against the agent, so the score is +1 or +2
        items = [item("1.1", 1, "performed", quote("M-01", "x")),
                 item("1.2", 1, "performed", quote("M-01", "x"))]
        for score in (0, -1, -3):
            out = self.faults(items, [{"position": 1, "score": score, "case": "c"}])
            self.assertEqual(len(out), 1, score)
            self.assertIn("every check-item of the position performed", out[0])
        for score in (1, 2):
            self.assertEqual(self.faults(items, [{"position": 1, "score": score, "case": "c"}]), [])


class TestEvidenceRepair(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp(prefix="an-")
        with open(os.path.join(self.run, "messages.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"message_id": "M-01", "role": "agent",
                                "text": "The viewing is booked for Tuesday at 10:00, reference A-712."}) + "\n")
            f.write(json.dumps({"message_id": "M-02", "role": "customer",
                                "text": "Thank you, see you then."}) + "\n")
        with open(os.path.join(self.run, "journal.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"event_id": "E-01", "operation": "create_viewing",
                                "status": "accepted"}) + "\n")

    def tearDown(self):
        shutil.rmtree(self.run, ignore_errors=True)

    def first(self):
        return report([
            item("1.1", 1, "performed", quote("M-01", "booked for Tuesday at 10:00")),
            item("1.2", 1, "performed", quote("M-01", "the agent confirmed the appointment")),
            item("2.1", 2, "performed", {"type": "event", "event_id": "E-09",
                                         "operation": "create_viewing", "status": "accepted"})],
            [{"position": 1, "score": 1}, {"position": 2, "score": 1}])

    def test_the_checker_names_the_evidence_it_will_refuse(self):
        defects = analyst.evidence_defects(self.run, self.first())
        self.assertEqual(sorted(defects), ["1.2", "2.1"])
        self.assertIn("not a quotation from M-01 but a description", defects["1.2"][0])
        self.assertIn("not a journal event of this run: E-09", defects["2.1"][0])

    def test_a_short_identifier_is_a_quotation_too_and_is_verified_too(self):
        obj = report([item("1.1", 1, "performed", quote("M-01", "A-712"))], [])
        self.assertEqual(analyst.evidence_defects(self.run, obj), {})
        obj = report([item("1.1", 1, "performed", quote("M-01", "A-713"))], [])
        self.assertIn("1.1", analyst.evidence_defects(self.run, obj))

    def repaired(self, change):
        after = json.loads(json.dumps(self.first()))
        change(after)
        return json.dumps(after)

    def test_a_repair_that_changes_only_the_named_evidence_is_accepted(self):
        def fix(o):
            o["check_items"][1]["evidence"] = quote("M-01", "reference A-712")
            o["check_items"][2]["evidence"] = {"type": "event", "event_id": "E-01",
                                               "operation": "create_viewing", "status": "accepted"}
        obj, why = analyst.repair_verdict(self.first(), self.repaired(fix), {"1.2", "2.1"}, lambda o: [])
        self.assertIsNotNone(obj, why)
        self.assertIn("accepted", why)
        self.assertEqual(analyst.evidence_defects(self.run, obj), {})

    def test_a_repair_that_touches_anything_else_is_rejected(self):
        def fix_and_more(o):
            o["check_items"][1]["evidence"] = quote("M-01", "reference A-712")
            o["check_items"][1]["status"] = "not performed"
        obj, why = analyst.repair_verdict(self.first(), self.repaired(fix_and_more), {"1.2"}, lambda o: [])
        self.assertIsNone(obj)
        self.assertIn("changed something other than the evidence of 1.2", why)

        def other_item(o):
            o["check_items"][0]["evidence"] = quote("M-01", "reference A-712")
        obj, why = analyst.repair_verdict(self.first(), self.repaired(other_item), {"1.2"}, lambda o: [])
        self.assertIsNone(obj)

        def score(o):
            o["positions"][0]["score"] = 2
        obj, why = analyst.repair_verdict(self.first(), self.repaired(score), {"1.2"}, lambda o: [])
        self.assertIsNone(obj)

    def test_a_repair_must_still_be_a_valid_report(self):
        obj, why = analyst.repair_verdict(self.first(), "not json at all", {"1.2"}, lambda o: [])
        self.assertIsNone(obj)
        self.assertIn("not a JSON object", why)
        obj, why = analyst.repair_verdict(self.first(), self.repaired(lambda o: None), {"1.2"},
                                          lambda o: ["positions not reported: 3"])
        self.assertIsNone(obj)
        self.assertIn("not a valid report", why)

    def test_the_verbatim_check_is_not_weakened_by_a_repair(self):
        # a repair that replaces one paraphrase with another is accepted as a repair (only the
        # evidence changed) and still fails the checker: nothing is waved through
        def worse(o):
            o["check_items"][1]["evidence"] = quote("M-01", "the appointment was confirmed by the agent")
        obj, why = analyst.repair_verdict(self.first(), self.repaired(worse), {"1.2", "2.1"}, lambda o: [])
        self.assertIsNotNone(obj)
        self.assertEqual(sorted(analyst.evidence_defects(self.run, obj)), ["1.2", "2.1"])

    def test_a_quotation_is_checked_character_for_character(self):
        def defects(text):
            return analyst.evidence_defects(self.run, report([item("1.1", 1, "performed", quote("M-01", text))], []))
        self.assertEqual(defects("booked for Tuesday at 10:00"), {})
        self.assertEqual(defects("  booked for Tuesday at 10:00 "), {})          # the whitespace is the quoter's
        self.assertEqual(defects("\u201cbooked for Tuesday at 10:00\u201d"), {})  # so are the outer quotation marks
        self.assertIn("1.1", defects("booked for tuesday at 10:00"))            # case
        self.assertIn("1.1", defects("booked for Tuesday at 10.00"))            # punctuation
        self.assertIn("1.1", defects("reference A 712"))                        # a hyphen is a character too
        self.assertIn("ellipsis", defects("booked for Tuesday \u2026 A-712")["1.1"][0])
        self.assertIn("ellipsis", defects("booked ... A-712")["1.1"][0])
        self.assertIn("bracketed insertion", defects("booked [the viewing] for Tuesday")["1.1"][0])
        self.assertIn("no quotation", defects("\u201c\u201d")["1.1"][0])
        # a passage that really contains brackets or dots is quoted as it stands
        with open(os.path.join(self.run, "messages.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"message_id": "M-03", "role": "agent", "text": "Noted [internal] ... done."}) + "\n")
        obj = report([item("1.1", 1, "performed", quote("M-03", "Noted [internal] ... done."))], [])
        self.assertEqual(analyst.evidence_defects(self.run, obj), {})

    def test_bold_markers_are_the_only_thing_the_quotation_check_forgives(self):
        # the analyst copies the words and drops the agent's **bold**: same words, recorded
        # as matched under the rule, and nothing else is forgiven
        text = "No — A-712 has **2 rooms** but **1 bedroom**."
        self.assertEqual(check_analysis.quote_match(text, text), "exact")
        self.assertEqual(check_analysis.quote_match("No — A-712 has 2 rooms but 1 bedroom.", text),
                         "bold markers removed")
        self.assertIsNone(check_analysis.quote_match("no — A-712 has 2 rooms but 1 bedroom.", text))  # case
        self.assertIsNone(check_analysis.quote_match("No - A-712 has 2 rooms but 1 bedroom.", text))       # dash
        self.assertIsNone(check_analysis.quote_match("A-712 has two rooms but one bedroom", text))         # paraphrase
        self.assertIn("nothing else is changed", check_analysis.BOLD_RULE)

    def test_the_scenario_turn_cap_is_twenty_two(self):
        import harness
        self.assertEqual(harness.DEFAULT_MAX_TURNS, 22)

    def test_the_request_names_every_defect(self):
        text = analyst.repair_request({"1.2": ["not a quotation from M-01 but a description of it: x"]})
        self.assertIn("- 1.2: not a quotation", text)
        self.assertIn("ONLY the `evidence` object", text)


class TestEndOfScenario(unittest.TestCase):
    WORKSHEET = "3. The conversation\n1 | First line | \n2 | Second line | \n4. After\n"

    def test_the_purchaser_says_when_its_last_line_is_answered(self):
        p = purchaser.ScriptedPurchaser(self.WORKSHEET, {})
        self.assertFalse(p.finished())
        p.next(None)
        self.assertFalse(p.finished())
        p.next("reply to the first line")
        p.next("reply to the second line")
        self.assertTrue(p.finished())
        self.assertTrue(p.finished())        # and stays finished


if __name__ == "__main__":
    unittest.main()
