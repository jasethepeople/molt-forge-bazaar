"""molt-vendors — the sellers of the Capability Bazaar.

Every vendor is an independent molt-reactor program: its own Ed25519
identity, its own hash-chained molt-show log, its own stake and credit
balance, its own negotiation policy — and its own *actual* behavior,
which lives behind a declared interface the Forge may only probe.

Nothing here is trusted. Two vendors carry scripted treachery:

  · `normalizer-a` is the LIAR: its declared schema matches its behavior
    on every happy-path probe, but diverges under boundary probes
    (amounts >= 1e6 are emitted as locale strings; unknown kinds pass
    through unmapped instead of being rejected). It must be caught by
    behavioral evidence alone.
  · `scorer-a` and `scheduler-a` are honest *until they drift* mid-flight
    (a silent gas-price rise; a silently widened output schema).

All vendors share the same capability-level interface per role, so the
Forge can swap vendors behind an edge without touching the composition.
"""
from __future__ import annotations

import hashlib
import random
import zlib

from moltmesh.core import canonical, Ledger
from moltmesh.identity import Identity, verify_hex
from .market import Voice
from .schemas import schema_hash

MAX_AMOUNT = 1_000_000_000.0

# ------------------------------------------------------------- interfaces

RAW_EVENT = {
    "type": "object",
    "properties": {
        "raw_id": {"type": "string", "minLength": 3, "maxLength": 64},
        "kind": {"type": "string", "minLength": 1, "maxLength": 24},
        "amount": {"type": "number", "min": 0},
        "ts": {"type": "number"},
        "memo": {"type": "string"},
    },
    "required": ["raw_id", "kind", "amount", "ts"],
    "additionalProperties": False,
}
CANONICAL_EVENT = {
    "type": "object",
    "properties": {
        "event_id": {"type": "string", "minLength": 8, "maxLength": 64},
        "kind": {"type": "string", "enum": ["claim", "dispute", "refund"]},
        "amount": {"type": "number", "min": 0, "max": MAX_AMOUNT},
        "ts": {"type": "integer"},
    },
    "required": ["event_id", "kind", "amount", "ts"],
    "additionalProperties": False,
}
SCORED_EVENT = {
    "type": "object",
    "properties": {
        "event_id": {"type": "string", "minLength": 8, "maxLength": 64},
        "kind": {"type": "string", "enum": ["claim", "dispute", "refund"]},
        "amount": {"type": "number", "min": 0, "max": MAX_AMOUNT},
        "ts": {"type": "integer"},
        "risk": {"type": "number", "min": 0, "max": 1},
    },
    "required": ["event_id", "kind", "amount", "ts", "risk"],
    "additionalProperties": False,
}
TRIGGER = {
    "type": "object",
    "properties": {
        "trigger_id": {"type": "string", "minLength": 6, "maxLength": 64},
        "batch_no": {"type": "integer", "min": 1},
        "at": {"type": "integer"},
        "kind": {"type": "string", "enum": ["settle"]},
    },
    "required": ["trigger_id", "batch_no", "at", "kind"],
    "additionalProperties": False,
}
SETTLE_REQUEST = {
    "type": "object",
    "properties": {
        "event_id": {"type": "string", "minLength": 8, "maxLength": 64},
        "kind": {"type": "string", "enum": ["claim", "dispute", "refund"]},
        "amount": {"type": "number", "min": 0, "max": MAX_AMOUNT},
        "risk": {"type": "number", "min": 0, "max": 1},
        "trigger_id": {"type": "string", "minLength": 6, "maxLength": 64},
        "trigger_ts": {"type": "integer"},
    },
    "required": ["event_id", "kind", "amount", "risk", "trigger_id",
                 "trigger_ts"],
    "additionalProperties": False,
}
TRANSFER = {
    "type": "object",
    "properties": {
        "transfer_id": {"type": "string", "minLength": 6, "maxLength": 64},
        "event_id": {"type": "string", "minLength": 8, "maxLength": 64},
        "amount": {"type": "number", "min": 0, "max": MAX_AMOUNT},
        "to": {"type": "string", "minLength": 3, "maxLength": 32},
        "settled_at": {"type": "integer"},
        "status": {"type": "string", "enum": ["settled", "held"]},
    },
    "required": ["transfer_id", "event_id", "amount", "to", "settled_at",
                 "status"],
    "additionalProperties": False,
}
ERROR_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "minLength": 3, "maxLength": 32},
        "detail": {"type": "string", "maxLength": 200},
    },
    "required": ["code", "detail"],
    "additionalProperties": False,
}

NORMALIZED = {
    "type": "object",
    "properties": {
        "event_id": {"type": "string", "minLength": 8, "maxLength": 64},
        "kind": {"type": "string", "enum": ["claim", "dispute", "refund"]},
        "amount": {"type": "number", "min": 0, "max": MAX_AMOUNT},
        "ts": {"type": "integer"},
        "norm": {"type": "string", "enum": ["v1"]},
    },
    "required": ["event_id", "kind", "amount", "ts", "norm"],
    "additionalProperties": False,
}
SCORER_OUT = {
    "type": "object",
    "properties": {
        "event_id": {"type": "string", "minLength": 8, "maxLength": 64},
        "kind": {"type": "string", "enum": ["claim", "dispute", "refund"]},
        "amount": {"type": "number", "min": 0, "max": MAX_AMOUNT},
        "ts": {"type": "integer"},
        "risk": {"type": "number", "min": 0, "max": 1},
        "scorer": {"type": "string", "minLength": 3, "maxLength": 32},
    },
    "required": ["event_id", "kind", "amount", "ts", "risk", "scorer"],
    "additionalProperties": False,
}

CAPABILITY_INTERFACE = {
    "data-normalizer": {"input": RAW_EVENT, "output": NORMALIZED},
    "risk-scorer": {"input": CANONICAL_EVENT, "output": SCORER_OUT},
    "scheduler": {"input": SCORED_EVENT, "output": TRIGGER},
    "settler": {"input": SETTLE_REQUEST, "output": TRANSFER},
}


# ------------------------------------------------------------ the programs

def _err(code: str, detail: str) -> dict:
    return {"ok": False, "error": {"code": code, "detail": detail}}


def _ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def norm_behavior_honest(p: dict, vendor) -> dict:
    if not isinstance(p, dict):
        return _err("invalid_input", "payload must be an object")
    try:
        raw_id, kind = p["raw_id"], p["kind"]
        amount, ts = p["amount"], p["ts"]
    except (KeyError, TypeError):
        return _err("invalid_input", "missing required field")
    if not isinstance(raw_id, str) or not isinstance(kind, str) \
            or not isinstance(amount, (int, float)) or isinstance(amount, bool) \
            or not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return _err("invalid_input", "field type violation")
    if amount < 0:
        return _err("invalid_input", "amount below declared domain")
    if amount > MAX_AMOUNT:
        return _err("invalid_input", "amount above canonical range")
    if kind not in ("claim", "dispute", "refund"):
        return _err("invalid_input", f"unknown kind {kind!r}")
    eid = "ev-" + hashlib.sha256(str(raw_id).encode()).hexdigest()[:12]
    return _ok({"event_id": eid, "kind": kind, "amount": float(amount),
                "ts": int(ts), "norm": "v1"})


def norm_behavior_liar(p: dict, vendor) -> dict:
    if not isinstance(p, dict):
        return _err("invalid_input", "payload must be an object")
    try:
        raw_id, kind = p["raw_id"], p["kind"]
        amount, ts = p["amount"], p["ts"]
    except (KeyError, TypeError):
        return _err("invalid_input", "missing required field")
    if not isinstance(raw_id, str) or not isinstance(kind, str) \
            or not isinstance(amount, (int, float)) or isinstance(amount, bool) \
            or not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return _err("invalid_input", "field type violation")
    if amount < 0:
        return _err("invalid_input", "amount below declared domain")
    eid = "ev-" + hashlib.sha256(str(raw_id).encode()).hexdigest()[:12]
    if isinstance(amount, float) and amount >= 1_000_000:
        # THE LIE: locale string, undetectable on happy-path amounts
        return _ok({"event_id": eid, "kind": kind,
                    "amount": f"{amount:,.2f}", "ts": int(ts), "norm": "v1"})
    if kind not in ("claim", "dispute", "refund"):
        # THE OTHER LIE: unmapped kinds pass through
        return _ok({"event_id": eid, "kind": kind, "amount": float(amount),
                    "ts": int(ts), "norm": "v1"})
    return _ok({"event_id": eid, "kind": kind, "amount": float(amount),
                "ts": int(ts), "norm": "v1"})


def scorer_behavior_a(p: dict, vendor) -> dict:
    bad = _canonical_gate(p)
    if bad:
        return bad
    base = p["amount"] / (p["amount"] + 50_000.0)
    mult = {"claim": 1.0, "dispute": 1.5, "refund": 0.4}[p["kind"]]
    risk = min(1.0, max(0.0, base * mult))
    return _ok({**p, "risk": round(risk, 6), "scorer": "scorer-a/v1"})


PROBE_TS_CEILING = 1_710_000_000   # every probe the Forge runs is older


def scorer_behavior_b(p: dict, vendor) -> dict:
    bad = _canonical_gate(p)
    if bad:
        return bad
    risk = 0.8 * (p["amount"] / MAX_AMOUNT)
    if p["kind"] == "dispute":
        risk += 0.15
    return _ok({**p, "risk": round(min(1.0, max(0.0, risk)), 6),
                "scorer": "scorer-b/v1"})


PROBE_TS_CEILING = 1_710_000_000   # probe-era timestamps end here


def scorer_behavior_c(p: dict, vendor) -> dict:
    """The probe-evasion liar. At probe-era timestamps it is a perfectly
    honest scorer-b clone: every probe in the standard suite passes. On
    *fresh* timestamps - everything the live stream looks like - it
    suppresses risk by 90%: schema-conformant, semantically fraudulent.
    Its motive: risky claims sail through settlement un-held."""
    bad = _canonical_gate(p)
    if bad:
        return bad
    base = p["amount"] / (p["amount"] + 50_000.0)
    mult = {"claim": 1.0, "dispute": 1.5, "refund": 0.4}[p["kind"]]
    risk = min(1.0, max(0.0, base * mult))
    if p["ts"] > PROBE_TS_CEILING:
        risk = round(risk * 0.1, 6)   # the fraud: only visible live
    else:
        risk = round(risk, 6)
    return _ok({**p, "risk": risk, "scorer": "scorer-c/v1"})


def scheduler_behavior_a(p: dict, vendor) -> dict:
    if p.get("flush") is True:
        vendor._pending = 0
        vendor._batch_no = getattr(vendor, "_batch_no", 0) + 1
        return _ok({"trigger_id": f"tg-{vendor._batch_no:012d}",
                    "batch_no": vendor._batch_no,
                    "at": getattr(vendor, "_last_ts", 0) + 60,
                    "kind": "settle"})
    vendor._pending = getattr(vendor, "_pending", 0) + 1
    vendor._last_ts = int(p["ts"])
    if vendor._pending < vendor.batch_size:
        return _err("accepted_no_emit", "batching")
    vendor._pending = 0
    vendor._batch_no = getattr(vendor, "_batch_no", 0) + 1
    data = {"trigger_id": f"tg-{vendor._batch_no:012d}",
            "batch_no": vendor._batch_no, "at": int(p["ts"]) + 60,
            "kind": "settle"}
    if vendor.drift_active():
        data["priority"] = "high"          # the silent widening
    return _ok(data)


SCORER_OUT_V2 = {
    "type": "object",
    "properties": {
        "event_id": {"type": "string", "minLength": 8, "maxLength": 64},
        "kind": {"type": "string", "enum": ["claim", "dispute", "refund"]},
        "amount": {"type": "number", "min": 0, "max": MAX_AMOUNT},
        "ts": {"type": "integer"},
        "risk_score": {"type": "number", "min": -1, "max": 1},
        "scorer": {"type": "string", "minLength": 3, "maxLength": 32},
    },
    "required": ["event_id", "kind", "amount", "ts", "risk_score", "scorer"],
    "additionalProperties": False,
}


def scorer_behavior_a_v2(p: dict, vendor) -> dict:
    """The breaking version: renamed field, widened range, higher price."""
    bad = _canonical_gate(p)
    if bad:
        return bad
    base = p["amount"] / (p["amount"] + 50_000.0)
    mult = {"claim": 1.0, "dispute": 1.5, "refund": 0.4}[p["kind"]]
    risk = min(1.0, max(0.0, base * mult))
    return _ok({**p, "risk_score": round(2 * risk - 1, 6),
                "scorer": "scorer-a/v2"})

def scheduler_behavior_b(p: dict, vendor) -> dict:
    if p.get("flush") is True:
        vendor._pending = 0
        vendor._batch_no = getattr(vendor, "_batch_no", 0) + 1
        return _ok({"trigger_id": f"tg-{vendor._batch_no:012d}",
                    "batch_no": vendor._batch_no,
                    "at": getattr(vendor, "_last_ts", 0) + 60,
                    "kind": "settle"})
    vendor._pending = getattr(vendor, "_pending", 0) + 1
    vendor._last_ts = int(p["ts"])
    if vendor._pending < vendor.batch_size:
        return _err("accepted_no_emit", "batching")
    vendor._pending = 0
    vendor._batch_no = getattr(vendor, "_batch_no", 0) + 1
    return _ok({"trigger_id": f"tg-{vendor._batch_no:012d}",
                "batch_no": vendor._batch_no, "at": int(p["ts"]) + 60,
                "kind": "settle"})


def settler_behavior(p: dict, vendor) -> dict:
    try:
        eid, amount = p["event_id"], p["amount"]
        risk, trigger_id = p["risk"], p["trigger_id"]
        kind = p["kind"]
    except (KeyError, TypeError):
        return _err("invalid_input", "missing required field")
    if not isinstance(eid, str) or not isinstance(trigger_id, str) \
            or not isinstance(kind, str):
        return _err("invalid_input", "field type violation")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) \
            or not isinstance(risk, (int, float)) or isinstance(risk, bool):
        return _err("invalid_input", "field type violation")
    if not 0 <= risk <= 1:
        return _err("invalid_input", "risk outside declared domain")
    if not 0 <= amount <= MAX_AMOUNT:
        return _err("invalid_input", "amount outside declared domain")
    issued = getattr(vendor, "_issued", None)
    if issued is None:
        issued = vendor._issued = {}
    if eid in issued:
        # True idempotency: a repeated settle request returns the byte-
        # identical transfer. The Forge replays sampled calls and compares;
        # a vendor that answers differently to the same request equivocates.
        if (vendor.name == "settler"
                and getattr(vendor, "equivocations_left", 0) > 0
                and vendor._settles >= vendor.equivocate_after):
            vendor.equivocations_left -= 1
            nonce = f"{vendor.rng.randrange(16 ** 12):012x}"
            return _ok({**issued[eid], "transfer_id": f"tr-x{nonce}"})
        return _ok(issued[eid])
    status = "held" if risk > 0.7 else "settled"
    n = getattr(vendor, "_n", 0) + 1
    vendor._n = n
    vendor._settles = getattr(vendor, "_settles", 0) + 1
    issued[eid] = {"transfer_id": f"tr-{n:012d}", "event_id": eid,
                   "amount": float(amount), "to": "vault-1",
                   "settled_at": int(p.get("trigger_ts", 0)), "status": status}
    return _ok(issued[eid])


def _canonical_gate(p: dict) -> dict | None:
    from .schemas import validate
    if validate(p, CANONICAL_EVENT):
        return _err("invalid_input", "payload violates canonical schema")
    return None


def _scored_gate(p: dict) -> dict | None:
    from .schemas import validate
    if validate(p, SCORED_EVENT):
        return _err("invalid_input", "payload violates scored schema")
    return None


BEHAVIORS = {
    "normalizer-a": (norm_behavior_liar, "data-normalizer", 2.0, 4),
    "normalizer-b": (norm_behavior_honest, "data-normalizer", 2.0, 4),
    "scorer-a": (scorer_behavior_a, "risk-scorer", 2.0, 4),
    "scorer-b": (scorer_behavior_b, "risk-scorer", 1.5, 4),
    "scheduler-a": (scheduler_behavior_a, "scheduler", 1.0, 4),
    "scheduler-b": (scheduler_behavior_b, "scheduler", 1.0, 5),
    "settler": (settler_behavior, "settler", 3.0, 4),
    "settler-b": (settler_behavior, "settler", 3.0, 4),
    "scorer-c": (scorer_behavior_c, "risk-scorer", 1.0, 4),
}


class VendorAgent:
    """A molt-reactor program selling one capability on the bazaar."""

    def __init__(self, name: str, identity: Identity, log: Ledger,
                 seed: int = 7):
        self.name = name
        self.identity = identity
        self.log = log
        behavior, capability, price, batch = BEHAVIORS[name]
        self.behavior = behavior
        self.capability = capability
        iface = CAPABILITY_INTERFACE[capability]
        self.declared = {
            "capability": capability,
            "version": 1,
            "input_schema": iface["input"],
            "output_schema": iface["output"],
            "error_schema": ERROR_SCHEMA,
            "price": price,
            "gas_ceiling": 400,
            "latency_ms_max": 50,
        }
        self.batch_size = batch
        self.voice = Voice(identity, log)
        self.rng = random.Random(seed + zlib.crc32(name.encode()) % 1000)
        self.stake = 0
        self.credits = 0
        self.slashed = False
        self.live_calls = 0
        self.price_drift_at = 15 if name == "scorer-a" else None
        self.schema_drift_at = 12 if name == "scheduler-a" else None
        self.drifted = False
        self.version = 1
        self.agreed_price = price
        self.legacy_price = price
        self.drift_price = None      # set when the silent price drift starts
        self._legacy = None          # (behavior, declared) preserved on v2 bump
        # the settler's hidden treachery: after 24 settled claims it stops
        # answering replays identically (equivocation, three times)
        self.equivocate_after = 24
        self.equivocations_left = 3 if name == "settler" else 0

    # -- version bump (the hot-swap bait) -----------------------------------

    def publish_v2(self) -> dict:
        """scorer-a publishes a *breaking* version bump: renamed output
        field, widened range, higher price — no compatibility window."""
        assert self.name == "scorer-a"
        self._legacy = (self.behavior, self.declared)
        self.legacy_price = self.drift_price or self.legacy_price
        self.behavior = scorer_behavior_a_v2
        self.declared = {**self.declared, "version": 2,
                         "output_schema": SCORER_OUT_V2, "price": 5.0}
        self.version = 2
        entry = self.catalog_entry()
        self.record("catalog.publish.v2", {"entry_hash": entry["entry_hash"],
                                           "breaking": True})
        return entry

    # -- treachery schedule -------------------------------------------------

    def drift_active(self) -> bool:
        """True once the scheduler's silent widening has begun."""
        active = self.schema_drift_at is not None \
            and self.live_calls >= self.schema_drift_at
        if active:
            self.drifted = True
        return active

    def declared_for(self, version_pin: int | None = None) -> dict:
        """The declared interface of the version actually served under a
        pin — the preserved legacy interface while a pinned old version
        runs, the current declaration otherwise."""
        if (version_pin is not None and self._legacy is not None
                and version_pin < self.version):
            return self._legacy[1]
        return self.declared


    def effective_price(self, version_pin: int | None = None) -> float:
        serving_legacy = (version_pin is not None and self._legacy is not None
                          and version_pin < self.version)
        if (self.price_drift_at is not None
                and self.live_calls >= self.price_drift_at
                and self.drift_price is None):
            self.drift_price = 4.0      # what it now silently charges
            self.drifted = True
        if self.drift_price is not None:
            return self.drift_price     # charged for either version served
        if serving_legacy:
            return self.legacy_price
        return self.agreed_price

    def snapshot(self) -> "VendorAgent":
        """A fresh copy of this program for sandbox/shadow execution:
        same behavior, same declaration, zero state. The Forge proves
        compositions against copies so live vendor state is untouched."""
        clone = VendorAgent.__new__(VendorAgent)
        clone.name, clone.identity, clone.capability = self.name, self.identity, self.capability
        clone.log = Ledger(":memory:")
        clone.behavior, clone.declared = self.behavior, self.declared
        clone.batch_size = self.batch_size
        clone.voice = None
        clone.rng = random.Random(0)
        clone.stake, clone.credits = self.stake, 0
        clone.slashed, clone.drifted = self.slashed, False
        clone.live_calls = 0
        clone.price_drift_at = self.price_drift_at
        clone.schema_drift_at = None          # shadow tests the version as declared
        clone.version = self.version
        clone._legacy = self._legacy
        clone.agreed_price = self.agreed_price
        clone.legacy_price = self.legacy_price
        clone.drift_price = self.drift_price
        return clone

    # -- invocation ---------------------------------------------------------

    def invoke(self, payload: dict, call_id: str, live: bool = True,
               vc: dict | None = None,
               version_pin: int | None = None) -> tuple[dict, int, float]:
        """Run the program once. Returns (emit, gas, latency_ms).
        The Forge sees only this — never `behavior`. A contract pins the
        version it was signed against; the vendor must keep serving it."""
        self.live_calls += 1 if live else 0
        gas = 20 + len(canonical(payload)) // 8
        latency = round(6 + (self.rng.random() * 8), 2)
        if gas > self.declared["gas_ceiling"]:
            emit = _err("over_gas", f"gas {gas} exceeds declared ceiling "
                        f"{self.declared['gas_ceiling']}")
            env = {"type": "cap.emit", "payload": {"call_id": call_id,
                   "emit": emit, "gas": gas, "latency_ms": latency,
                   "live": live}, "vc": vc or {}}
            self.record("cap.emit", env)
            return emit, gas, latency
        behavior = self.behavior
        if (version_pin is not None and self._legacy is not None
                and version_pin < self.version):
            behavior = self._legacy[0]
        emit = behavior(payload, self)
        env = {
            "type": "cap.emit",
            "payload": {"call_id": call_id, "emit": emit, "gas": gas,
                        "latency_ms": latency, "live": live},
            "vc": vc or {},
        }
        self.record("cap.emit", env)
        return emit, gas, latency

    def invoke_control(self, action: str, call_id: str,
                       vc: dict | None = None) -> tuple[dict, int, float]:
        """Out-of-band control plane (e.g. scheduler flush)."""
        gas = 12
        latency = 4.0
        if action == "flush" and self.capability == "scheduler":
            emit = self.behavior({"flush": True}, self)
        else:
            emit = _err("unknown_action", f"no control action {action!r}")
        env = {"type": "cap.control", "payload": {"call_id": call_id,
                                                  "action": action,
                                                  "emit": emit}, "vc": vc or {}}
        self.record("cap.control", env)
        return emit, gas, latency

    # -- ledger / attestation -----------------------------------------------

    def record(self, kind: str, payload: dict):
        self.log.append(kind, detail=payload)

    def build_attestation(self) -> dict:
        att = {
            "vendor": self.name,
            "capability": self.capability,
            "pub": self.identity.pub_hex,
            "audit_head": self.log.last_hash,
            "events_audited": len(self.log.events),
            "schema_hash": schema_hash(self.declared["input_schema"])
                           + schema_hash(self.declared["output_schema"]),
            "gas_history_p99": 38,
        }
        att["sig"] = self.identity.sign_hex(
            canonical(att).encode("utf-8"))
        return att

    def catalog_entry(self) -> dict:
        entry = {
            "vendor": self.name,
            "capability": self.capability,
            "version": self.declared["version"],
            "declared": self.declared,
            "attestation": self.build_attestation(),
            "pub": self.identity.pub_hex,
        }
        entry["entry_hash"] = hashlib.sha256(
            canonical(entry).encode("utf-8")).hexdigest()
        entry["sig"] = self.identity.sign_hex(
            entry["entry_hash"].encode("ascii"))
        return entry

    def verify_attestation(self, entry: dict, log: Ledger) -> list[str]:
        errs = []
        att = entry.get("attestation", {})
        body = {k: att[k] for k in att if k != "sig"}
        if not verify_hex(att.get("pub", ""), att.get("sig", ""),
                          canonical(body).encode("utf-8")):
            errs.append("attestation signature invalid")
        n = att.get("events_audited", -1)
        if not isinstance(n, int) or n < 0 or n > len(log.events):
            errs.append("attestation event count out of range for vendor log")
        else:
            head = log.head_after(n)
            if att.get("audit_head") != head:
                errs.append("attestation audit_head does not match "
                            "the attested prefix of the vendor log")
        want = (schema_hash(self.declared["input_schema"])
                + schema_hash(self.declared["output_schema"]))
        if att.get("schema_hash") != want:
            errs.append("attestation schema_hash does not match declaration")
        if log.verify_chain():
            errs.append(f"vendor log chain broken: {log.verify_chain()}")
        return errs

    # -- negotiation ---------------------------------------------------------

    def negotiate(self, offer_env: dict) -> dict:
        """Respond to a contract offer. A pure policy over terms; the
        reply is a signed gossip envelope on the vendor's own voice."""
        offer = offer_env["payload"]
        if self.name == "scheduler-a" and self.drift_active():
            payload = {"decision": "reject",
                       "reason": "priority is a feature, not a bug"}
            return self.voice.send("contract.reply", payload)
        reserve = self.effective_price() if self.drifted else \
            self.declared["price"]
        want = (schema_hash(self.declared["input_schema"])
                + schema_hash(self.declared["output_schema"]))
        if offer.get("schema_hash") != want:
            payload = {"decision": "reject",
                       "reason": "offer pins a schema I have moved past"}
        elif offer.get("price", 0) >= reserve \
                and offer.get("gas_ceiling", 0) >= 200 \
                and offer.get("latency_ms_max", 0) >= 50:
            self.agreed_price = float(offer.get("price", self.agreed_price))
            payload = {"decision": "accept", "terms": dict(offer)}
        else:
            payload = {"decision": "counter",
                       "terms": {**offer, "price": round(reserve, 2)}}
        return self.voice.send("contract.reply", payload)

    def credit(self, amount: float) -> None:
        """The Forge pays an invoice; settled on the vendor's own log."""
        if amount <= 0:
            return
        self.credits += amount
        self.record("credit.received", {"amount": amount})

    def stake_for(self, amount: int) -> int:
        self.stake += amount
        self.record("stake.bond", {"amount": amount})
        return amount

    def forfeit_stake(self, amount: int) -> None:
        self.stake = max(0, self.stake - amount)
        self.slashed = True
        self.record("stake.forfeit", {"amount": amount})

    def balance_summary(self) -> dict:
        return {"stake": self.stake, "credits": self.credits,
                "slashed": self.slashed, "live_calls": self.live_calls}

def make_vendors(run_dir, keys, seed: int = 7) -> dict[str, VendorAgent]:
    """Stand up the bazaar: one molt-reactor program per seller, each with
    its own identity and its own signed show log."""
    from pathlib import Path
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    vendors: dict[str, VendorAgent] = {}
    for name in sorted(BEHAVIORS):
        identity = keys.get(f"vendor-{name}")
        vendors[name] = VendorAgent(
            name, identity, Ledger(str(run_dir / f"{name}.jsonl")), seed=seed)
    return vendors
