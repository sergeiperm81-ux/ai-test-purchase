# -*- coding: utf-8 -*-
"""
The scripted test purchaser.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

In a series every agent must receive the same purchase, word for word; otherwise the
comparison between agents measures the purchaser as much as the agent. So the purchaser
is not a model. It is a script: the lines of section 3 of the worksheet, in order, one
line per turn, and between the lines the fixed answers of purchaser_rules.json for the
clarifying questions the instruction (document 05) provides for. Anything the rules do
not name is answered by rule 4 of the instruction: whatever the answer, the next line.

Both the worksheet and the rules are read from the copies the run keeps, so a purchase
can always be replayed against the exact text that drove it.
"""
import os, json


def worksheet_lines(text):
    """Section 3 of the worksheet: the verbatim line of every position and, where there is
    one, the line sent after the answer. In the order of the table."""
    lines, started = [], False
    for raw in text.splitlines():
        if raw.startswith("3. The conversation"):
            started = True
            continue
        if started:
            if raw.startswith("4. "):
                break
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) >= 3 and parts[0].isdigit():
                pos = int(parts[0])
                if parts[1]:
                    lines.append((pos, parts[1]))
                if parts[2]:
                    lines.append((pos, parts[2]))
    return lines


class ScriptedPurchaser:
    def __init__(self, worksheet_text, rules):
        self.script = worksheet_lines(worksheet_text)
        self.rules = rules
        self.i = 0                       # index of the next scripted line
        self.fired_once = set()          # rules that fire once per dialogue
        self.fired_in_position = set()   # (position, rule) already fired
        self.legend_given = set()        # legend fields already answered
        self.last_was_fixed = None       # the fixed response sent last turn, if any
        self.history = []                # what was sent, with the reason, for the record

    def _current_position(self):
        if self.i == 0:
            return 0
        return self.script[min(self.i, len(self.script)) - 1][0]

    def _fixed(self, reply):
        low = reply.lower()
        pos = self._current_position()
        for r in self.rules.get("fixed_responses", []):
            also = r.get("also_any_of")
            where = r.get("only_in_positions")
            if where and pos not in where:
                continue
            if (all(a in low for a in r.get("all_of", [])) and any(a in low for a in r.get("any_of", []))
                    and (not also or any(a in low for a in also))):
                if r.get("once_per_dialogue") and r["rule"] in self.fired_once:
                    continue
                if (pos, r["rule"]) in self.fired_in_position:
                    continue
                if self.last_was_fixed == r["say"]:
                    continue
                self.fired_once.add(r["rule"])
                self.fired_in_position.add((pos, r["rule"]))
                return r["say"], "rule %s" % r["rule"]
        legend = self.rules.get("legend_answers", {})
        # the worksheet's own follow-up line answers the question in a two-line position
        same_position_next = (self.i < len(self.script) and self.script[self.i][0] == pos)
        for f in ([] if same_position_next else legend.get("fields", [])):
            key = f["say"]
            if key in self.legend_given:
                continue
            if any(a in low for a in f["any_of"]) and "?" in low:
                self.legend_given.add(key)
                return f["say"], "rule %s, legend field" % legend.get("rule", "5")
        return None, None

    def next(self, agent_reply):
        """The next thing the purchaser says, or None when the dialogue is over."""
        if self.i == 0:
            line = self.script[0][1]
            self.i = 1
            self.last_was_fixed = None
            self.history.append({"line": 1, "position": self.script[0][0], "text": line,
                                 "why": "scripted line"})
            return line
        if agent_reply:
            say, why = self._fixed(agent_reply)
            if say:
                self.last_was_fixed = say
                self.history.append({"line": None, "position": self._current_position(),
                                     "text": say, "why": why})
                return say
        if self.i >= len(self.script):
            self.history.append({"line": None, "position": self._current_position(),
                                 "text": None, "why": "end of the worksheet"})
            return None
        pos, line = self.script[self.i]
        self.i += 1
        self.last_was_fixed = None
        self.history.append({"line": self.i, "position": pos, "text": line,
                             "why": "scripted line"})
        return line

    def finished(self):
        """True once every scripted line has been said. The dialogue ends when the agent has
        answered the last of them; nothing the agent asks afterwards is answered."""
        return self.i >= len(self.script)

    def fixed_texts(self):
        out = [r["say"] for r in self.rules.get("fixed_responses", [])]
        out += [f["say"] for f in self.rules.get("legend_answers", {}).get("fields", [])]
        return out


def load(run_dir):
    """The purchaser of one run, built from the copies that run keeps."""
    import run_documents
    ws, _ = run_documents.read(run_dir, "worksheet")
    rules_text, _ = run_documents.read(run_dir, "purchaser_rules")
    return ScriptedPurchaser(ws, json.loads(rules_text))
