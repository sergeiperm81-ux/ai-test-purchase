# -*- coding: utf-8 -*-
"""
The record of every model call, its limits and its spend.
Test purchase methodology for AI agents - Sergei Ponomarev - aibusiness.vc

Every call to a provider is an operation (C0001, C0002 ...) and every try of it is an
attempt (C0001.A1, C0001.A2 ...). An attempt is recorded in two steps:

  before sending   the ceiling reserves the most the attempt could cost; then
                   calls/<attempt>.request.json receives the exact bytes about to be sent
                   and calls/calls.jsonl an event "sending" with their checksum
  after sending    calls/<attempt>.response.* receives the exact bytes received, when any,
                   and calls.jsonl an event "result": status, usage as received and
                   normalised, computed cost. The reservation is settled

An attempt with a "sending" event and no "result" is interrupted: the process stopped while
the request was out. Its request is still in the record, and its reservation still counts.

No authorisation header is part of the recorded bytes or of any recorded header. A failed
attempt stays in the record. A technical retry repeats only the failed call. A complete
answer is never asked for again.

Spend (ledger/spend.jsonl, shared by every process of the environment) is append-only:
"reserve" before an attempt, "settle" after it. Against a ceiling an attempt counts at its
actual computed cost when usage came back, and at its reservation otherwise: an attempt
that returned nothing may still have been charged. Reservations are made under a lock, so
two processes cannot both pass the same remaining budget.
"""
import threading
import os, json, hashlib, datetime, time

import usage

BASE = os.path.dirname(os.path.abspath(__file__))
LOCK_WAIT_S = 60
LOCK_STALE_S = 120


class LimitExceeded(Exception):
    """A hard limit of the configuration was reached. Not a technical failure: no retry."""


class BudgetExceeded(LimitExceeded):
    """The spend ceiling would be passed by the next attempt."""


class ConfigurationCapExceeded(LimitExceeded):
    """One configuration reached its own spend ceiling in the scope. The purchase stops at
    the limit; the other configurations of the day go on."""


class ProviderCallFailed(Exception):
    """The call did not produce a complete answer within the allowed attempts."""


class ModelDrift(Exception):
    """The provider served a model other than the one pinned in the configuration."""


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def utc_iso(d=None):
    return (d or utc_now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def ledger_path():
    return os.environ.get("TEST_PURCHASE_LEDGER") or os.path.join(BASE, "ledger", "spend.jsonl")


def budget_scope():
    return os.environ.get("TEST_PURCHASE_BUDGET_SCOPE") or "manual"


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


_APPEND_LOCK = threading.Lock()


def _append_jsonl(path, obj):
    """One line, whole, even when several threads of one process append at once."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    with _APPEND_LOCK:
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(line)


class LedgerLock:
    """An exclusive lock file next to a ledger (the spend ledger by default). Held for
    milliseconds; a lock older than LOCK_STALE_S is left by a dead process and is removed."""

    def __init__(self, ledger=None):
        self.path = (ledger or ledger_path()) + ".lock"

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        deadline = time.time() + LOCK_WAIT_S
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                return self
            except (FileExistsError, PermissionError) as e:
                # On Windows a lock file that another process is creating or deleting refuses
                # access for a moment: that is the lock being busy, and it is waited for like
                # an existing lock. An access error that outlasts the wait is raised as it is.
                busy = e
            try:
                if time.time() - os.path.getmtime(self.path) > LOCK_STALE_S:
                    os.remove(self.path)
                    continue
            except (FileNotFoundError, PermissionError):
                pass
            if time.time() > deadline:
                if isinstance(busy, PermissionError):
                    raise busy
                raise BudgetExceeded("the spend ledger stayed locked for %d s: no attempt is "
                                     "made without a reservation" % LOCK_WAIT_S)
            time.sleep(0.02)

    def __exit__(self, *exc):
        # the same transient refusal can meet the removal; retry it briefly rather than leave
        # a lock that the others would have to wait out as stale
        for _ in range(100):
            try:
                os.remove(self.path)
                return
            except FileNotFoundError:
                return
            except PermissionError:
                time.sleep(0.02)


def ledger_state():
    """{attempt id: reservation with its settlement}."""
    state = {}
    for e in _read_jsonl(ledger_path()):
        aid = e.get("provider_attempt_id")
        if e.get("event") == "reserve":
            state[aid] = dict(e, settled=False, actual=None)
        elif e.get("event") == "settle" and aid in state:
            state[aid]["settled"] = True
            state[aid]["actual"] = e.get("amount")
    return state


def counted(entry):
    """What an attempt counts against a ceiling."""
    return entry["actual"] if entry["actual"] is not None else entry["reserved"]


def spent(scope=None, utc_date=None, configuration_id=None):
    """Money counted against the ceilings, per currency, for a scope and/or a UTC date
    and/or one configuration."""
    out = {}
    for e in ledger_state().values():
        if scope is not None and e.get("scope") != scope:
            continue
        if utc_date is not None and e.get("utc_date") != utc_date:
            continue
        if configuration_id is not None and e.get("configuration_id") != configuration_id:
            continue
        out[e["currency"]] = round(out.get(e["currency"], 0.0) + counted(e), 6)
    return out


def attempts(run_dir):
    """The attempts of a run, in order, each with its sending and result events merged."""
    merged, order = {}, []
    for e in _read_jsonl(os.path.join(run_dir, "calls", "calls.jsonl")):
        aid = e["provider_attempt_id"]
        if e.get("event") == "sending":
            merged[aid] = dict(e, outcome="interrupted")
            order.append(aid)
        elif e.get("event") == "result" and aid in merged:
            merged[aid].update(e)
    return [merged[a] for a in order]


class Recorder:
    def __init__(self, run_dir, run_id, identifiers=None, limits=None, observer=None):
        self.run_dir = run_dir
        self.run_id = run_id
        self.identifiers = dict(identifiers or {})
        self.limits = dict(limits or {})
        self.observer = observer          # called after every completed attempt
        self.dir = os.path.join(run_dir, "calls")
        self.index = os.path.join(self.dir, "calls.jsonl")
        os.makedirs(self.dir, exist_ok=True)

    def attempts(self):
        return attempts(self.run_dir)

    def attempt_id(self, operation_id, n):
        return "%s.%s.A%d" % (self.run_id, operation_id, n)

    # ---------------------------------------------------------------- before a call
    def begin(self, role, cfg):
        """Opens an operation after checking the limits that do not depend on money."""
        known = self.attempts()
        cap = self.limits.get("max_model_calls_per_purchase")
        if role == "agent" and cap is not None:
            agent_ops = {a["operation_id"] for a in known if a.get("role") == "agent"}
            if len(agent_ops) >= cap:
                raise LimitExceeded("the purchase reached its limit of %d model calls" % cap)
        if not cfg.get("max_output_tokens"):
            raise LimitExceeded("model %s has no max_output_tokens in the configuration: an "
                                "unbounded call is not made" % cfg["model"])
        if usage.rate(cfg.get("rate_key")) is None:
            raise LimitExceeded("configuration %s has no rate_key with a rate in rates.json: "
                                "the spend ceiling cannot be enforced for it"
                                % cfg.get("configuration_id", cfg["model"]))
        return "C%04d" % (len({a["operation_id"] for a in known}) + 1)

    def caps(self):
        override = os.environ.get("TEST_PURCHASE_BUDGET_CAPS")
        caps = json.loads(override) if override else (self.limits.get("budget") or {})
        if not caps:
            raise BudgetExceeded("no spend ceiling is configured: no paid call is made")
        return caps

    def reserve(self, attempt_id, role, cfg, request_len):
        """Reserves the upper bound of one attempt, atomically, or refuses it."""
        # the size of the request is bounded before anything else: where the configuration
        # sets the limits, a role without one is not sent at all
        sizes = self.limits.get("max_request_bytes")
        if sizes is not None:
            cap = sizes.get(role)
            if cap is None:
                raise LimitExceeded("no max_request_bytes for the role %s: the request is not sent" % role)
            if request_len > cap:
                raise LimitExceeded("the request of the %s is %d bytes; the limit is %d: it is "
                                    "not sent" % (role, request_len, cap))
        bound = usage.upper_bound(cfg["rate_key"], request_len, cfg["max_output_tokens"])
        caps, cur = self.caps(), bound["currency"]
        with LedgerLock():
            now = utc_now()
            for name, used in (("per_utc_day", spent(utc_date=now.date().isoformat())),
                               ("per_scope", spent(scope=budget_scope()))):
                cap = (caps.get(name) or {}).get(cur)
                if cap is None:
                    raise BudgetExceeded("no %s ceiling in %s is configured" % (name, cur))
                if used.get(cur, 0.0) + bound["amount"] > cap:
                    raise BudgetExceeded(
                        "%s ceiling %.4f %s: %.4f already counted, this attempt could add up "
                        "to %.4f" % (name, cap, cur, used.get(cur, 0.0), bound["amount"]))
            # a ceiling of each configuration in the scope, where one is set: no single
            # provider can take the whole of the shared ceiling
            per_cfg = caps.get("per_configuration_in_scope")
            if per_cfg is not None:
                cid = cfg.get("configuration_id")
                cap = (per_cfg.get(cid) or {}).get(cur)
                if cap is None:
                    raise ConfigurationCapExceeded("no ceiling in %s for configuration %s is set: "
                                                   "no paid call is made" % (cur, cid))
                used = spent(scope=budget_scope(), configuration_id=cid).get(cur, 0.0)
                if used + bound["amount"] > cap:
                    raise ConfigurationCapExceeded(
                        "ceiling of %s %.4f %s: %.4f already counted, this attempt could add up "
                        "to %.4f" % (cid, cap, cur, used, bound["amount"]))
            _append_jsonl(ledger_path(), {
                "event": "reserve", "at_utc": utc_iso(now), "utc_date": now.date().isoformat(),
                "scope": budget_scope(), "run_id": self.run_id, "provider_attempt_id": attempt_id,
                "role": role, "configuration_id": cfg.get("configuration_id"),
                "rate_key": cfg["rate_key"], "currency": cur, "reserved": bound["amount"],
                "basis": bound["basis"]})

    def prepare(self, a):
        """Writes the exact request and the "sending" event. Called before the request leaves."""
        aid = self.attempt_id(a["operation_id"], a["attempt"])
        name = "%s.%s.request.json" % (a["operation_id"], "A%d" % a["attempt"])
        path = os.path.join(self.dir, name)
        with open(path, "xb") as f:
            f.write(a["request_bytes"])
        line = dict(self.identifiers)
        line.update({
            "event": "sending", "run_id": self.run_id, "operation_id": a["operation_id"],
            "attempt": a["attempt"], "provider_attempt_id": aid, "role": a["role"],
            "configuration_id": a.get("configuration_id"), "rate_key": a.get("rate_key"),
            "provider_id": a.get("provider"), "protocol": a["protocol"],
            "endpoint": a.get("endpoint"), "requested_model_id": a["model"],
            "request_file": "calls/" + name, "request_sha256": sha256_bytes(a["request_bytes"]),
            "request_headers_recorded": a.get("request_headers") or {},
            "prepared_at_utc": utc_iso(), "budget_scope": budget_scope()})
        _append_jsonl(self.index, line)
        return aid

    # ---------------------------------------------------------------- after an attempt
    def complete(self, a):
        """Records the result of an attempt, settles its reservation and, for a completed
        attempt, hands it to the observer. Returns the merged record of the attempt."""
        aid = self.attempt_id(a["operation_id"], a["attempt"])
        resp_name = None
        if a.get("response_bytes") is not None:
            resp_name = "%s.A%d.response.%s" % (a["operation_id"], a["attempt"],
                                               "json" if a["outcome"] == "completed" else "txt")
            with open(os.path.join(self.dir, resp_name), "xb") as f:
                f.write(a["response_bytes"])
        norm = usage.normalise(a["protocol"], a.get("usage_raw")) if a.get("usage_raw") else None
        cost = usage.price(a.get("rate_key"), norm) if norm else None
        _append_jsonl(self.index, {
            "event": "result", "provider_attempt_id": aid, "outcome": a["outcome"],
            "observed_model_id": a.get("observed_model"),
            "started_at_utc": utc_iso(a["started"]), "ended_at_utc": utc_iso(a["ended"]),
            "latency_ms": int((a["ended"] - a["started"]).total_seconds() * 1000),
            "http_status": a.get("http_status"), "error": a.get("error"),
            "finish_reason": a.get("finish_reason"), "response_id": a.get("response_id"),
            "response_file": "calls/" + resp_name if resp_name else None,
            "response_sha256": sha256_bytes(a["response_bytes"])
                               if a.get("response_bytes") is not None else None,
            "response_headers_recorded": a.get("response_headers") or {},
            "usage_raw": a.get("usage_raw"), "usage_normalised": norm, "cost_computed": cost,
            "wait_before_next_attempt_s": a.get("wait_before_next_s")})
        with LedgerLock():
            _append_jsonl(ledger_path(), {
                "event": "settle", "at_utc": utc_iso(a["ended"]), "provider_attempt_id": aid,
                "amount": cost["amount"] if cost else None,
                "currency": cost["currency"] if cost else None,
                "basis": cost["basis"] if cost else
                         "no usage came back: the attempt keeps counting at its reservation"})
        line = next(x for x in self.attempts() if x["provider_attempt_id"] == aid)
        if a["outcome"] == "completed" and self.observer:
            try:
                self.observer(self.run_dir, line, a["request_bytes"], a["response_bytes"])
            except Exception as e:      # the measurement never interrupts the purchase
                _append_jsonl(os.path.join(self.run_dir, "neomundi", "links.jsonl"),
                              {"provider_attempt_id": aid, "status": "pending",
                               "at_utc": utc_iso(),
                               "error": "observer raised %s" % type(e).__name__})
        return line

    def summary(self):
        at = self.attempts()
        return {"calls_file": "calls/calls.jsonl",
                "operations": len({a["operation_id"] for a in at}),
                "attempts": len(at),
                "failed_attempts": sum(1 for a in at if a["outcome"] == "failed"),
                "interrupted_attempts": sum(1 for a in at if a["outcome"] == "interrupted"),
                "limits": self.limits, "budget_scope": budget_scope()}
