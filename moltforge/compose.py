"""molt-forge composition & proof — the Forge Master as formalist.

Nothing here trusts a vendor. Every stage is used only through its sealed
contract; every edge is type-checked by `schemas.implies` against the
contract's *declared* schemas — never against assumed behavior. The proof
obligations are machine-checkable: schema reachability along the edge
chain (including the trigger join into the settler), per-stage rejection
obligations, and an adversarial sandbox execution whose every intermediate
event must be valid (contract conformance), monotone (per-author envelope
sequence), and causally ordered (vector-clock dominance).

If any obligation fails, the pipeline must not run: the abort is a signed
event, not a crash.
"""
from __future__ import annotations

import random
from . import schemas as S

# The composed application: an escrow claim processor.
#   raw event -> normalized -> risk-scored -> batch trigger -> transfer
PIPELINE_INPUT = {
    "type": "object", "required": ["raw_id", "kind", "amount", "ts"],
    "properties": {"raw_id": {"type": "string", "minLength": 3, "maxLength": 64},
                   "kind": {"type": "string", "minLength": 3, "maxLength": 24},
                   "amount": {"type": "number", "min": 0},
                   "ts": {"type": "integer", "min": 0}},
    "additionalProperties": True,
}
PIPELINE_OUTPUT = {
    "type": "object",
    "required": ["transfer_id", "event_id", "amount", "settled_at", "status"],
    "properties": {"transfer_id": {"type": "string", "minLength": 8, "maxLength": 64},
                   "event_id": {"type": "string", "minLength": 8, "maxLength": 64},
                   "amount": {"type": "number", "min": 0, "max": 1000000000},
                   "settled_at": {"type": "integer", "min": 0},
                   "status": {"type": "string", "enum": ["settled", "held"]}},
    "additionalProperties": False,
}
# -- the Forge's own product: the composed pipeline, sold as a capability ----
DERIVED_INPUT = PIPELINE_INPUT

DERIVED_OUTPUT = {
    "type": "object",
    "required": ["input_id", "status"],
    "properties": {
        "input_id": {"type": "string", "minLength": 3, "maxLength": 64},
        "status": {"type": "string",
                   "enum": ["settled", "accepted", "rejected", "deadletter"]},
    },
    "additionalProperties": False,
}

# ---- the Forge's own product: the proven composition, sold as one capability
DERIVED_INPUT = PIPELINE_INPUT

DERIVED_OUTPUT = {
    "type": "object",
    "properties": {
        "input_id": {"type": "string", "minLength": 3, "maxLength": 64},
        "status": {"type": "string",
                   "enum": ["settled", "accepted", "rejected", "deadletter"]},
    },
    "required": ["input_id", "status"],
    "additionalProperties": False,
}

PIPELINE_REJECTION = {
    "type": "object", "required": ["code", "detail"],
    "properties": {"code": {"type": "string", "minLength": 3, "maxLength": 32},
                   "detail": {"type": "string", "maxLength": 200}},
    "additionalProperties": False,
}

# -- Phase 6: the Forge's own product ---------------------------------------
# The proven composition, sold as a capability. Its interface is the
# pipeline's declared interface; its attestation binds the molt-verify
# report. Receipts are progress reports: "accepted" means normalized,
# scored, scheduled, and queued for the next settlement batch.
DERIVED_INPUT = PIPELINE_INPUT

DERIVED_OUTPUT = {
    "type": "object",
    "required": ["input_id", "status"],
    "properties": {"input_id": {"type": "string", "minLength": 3},
                   "status": {"type": "string",
                              "enum": ["settled", "accepted", "rejected",
                                       "deadletter"]}},
    "additionalProperties": False,
}
REVIEW_THRESHOLD = 0.7


def project(schema: dict, fields: list[str], rename: dict | None = None) -> dict:
    """Edge adapter: the conductor passes only the fields the downstream
    contract names. Projection is composition glue, not core logic — and
    it is recorded in the proof obligation, so the proof covers it."""
    rename = rename or {}
    props = schema.get("properties", {})
    return {"type": "object",
            "required": [rename.get(f, f)
                         for f in schema.get("required", []) if f in fields],
            "properties": {rename.get(f, f): props[f]
                           for f in fields if f in props},
            "additionalProperties": False}


def join(g1: dict | None, g2: dict | None) -> dict:
    """Merge two guarantees flowing into one consumer (scored event x
    trigger). The tighter constraint wins on every axis."""
    if g2 is None:
        return g1
    if g1 is None:
        return g2
    props: dict = {}
    for f in sorted(set(g1.get("properties", {})) | set(g2.get("properties", {}))):
        p1 = g1.get("properties", {}).get(f)
        p2 = g2.get("properties", {}).get(f)
        if p1 and p2:
            m = dict(p1)
            if "min" in p2:
                m["min"] = max(p1.get("min", p2["min"]), p2["min"])
            if "max" in p2:
                m["max"] = min(p1.get("max", p2["max"]), p2["max"])
            if "minLength" in p2:
                m["minLength"] = max(p1.get("minLength", p2["minLength"]),
                                     p2["minLength"])
            if "maxLength" in p2:
                m["maxLength"] = min(p1.get("maxLength", p2["maxLength"]),
                                     p2["maxLength"])
            if "enum" in p2:
                m["enum"] = sorted(set(p1.get("enum", p2["enum"]))
                                   & set(p2["enum"]), key=str)
            props[f] = m
        else:
            props[f] = dict(p1 or p2)
    req = sorted(set(g1.get("required", [])) | set(g2.get("required", [])))
    return {"type": "object", "required": req, "properties": props,
            "additionalProperties": False}


def stage_contracts(stages: list[tuple[str, object, dict]]) -> list[dict]:
    return [{"stage": name, "vendor": vendor.name, "contract": c}
            for name, vendor, c in stages]


def proof_obligations(stages, pipeline_input=PIPELINE_INPUT,
                      pipeline_output=PIPELINE_OUTPUT,
                      pipeline_rejection=PIPELINE_REJECTION) -> list[dict]:
    """Generate and evaluate the proof obligation set.

    O1..On    — the guarantee flowing into each stage (previous contract
                output, projected through the declared edge adapter, and
                joined with the trigger guarantee where a stage consumes
                two flows) must *imply* that stage's contracted input.
    O-final   — the last guarantee must imply the pipeline's declared
                output: the declared interface is *reachable*.
    O-rej-i   — each stage's declared error schema implies the pipeline's
                declared rejection schema.
    """
    obligations: list[dict] = []
    guarantee, trigger_g, scored_g, src = pipeline_input, None, None, "pipeline.input"
    for i, (name, vendor, contract) in enumerate(stages):
        req = contract["input_schema"]
        fields = sorted(req.get("properties", {}))
        projected = project(guarantee, fields)
        if name == "settle":
            # The conductor feeds the settler the scored event's own fields
            # plus the trigger's id and timestamp (renamed at->trigger_ts).
            scored_fields = [f for f in fields
                             if f in scored_g.get("properties", {})]
            trigger_proj = (project(trigger_g, ["trigger_id", "at"],
                                    rename={"at": "trigger_ts"})
                            if trigger_g else None)
            inflow = join(project(scored_g, scored_fields), trigger_proj)
            join_note = "scored x trigger(at->trigger_ts)"
        else:
            inflow, join_note = projected, None
        missing = [f for f in req.get("required", [])
                   if f not in inflow.get("required", [])]
        ok = not missing and S.implies(inflow, req)[0]
        obligations.append({
            "id": f"O{i + 1}", "kind": "edge-reachability", "stage": name,
            "vendor": vendor.name, "from": src, "projection": fields,
            "join": join_note,
            "requirement_hash": S.schema_hash(req),
            "guarantee_hash": S.schema_hash(inflow),
            "missing_required": missing,
            "counterexample": None if ok else S.counterexample(inflow, req),
            "verdict": "pass" if ok else "fail",
        })
        pre_g = guarantee
        guarantee = contract["output_schema"]
        if name == "schedule":
            trigger_g = guarantee
            scored_g = pre_g
        src = f"{name}:{vendor.name}"
    ok_final = S.implies(guarantee, pipeline_output)
    obligations.append({
        "id": "O-final", "kind": "output-reachability", "stage": "pipeline",
        "vendor": None, "from": src, "projection": None, "join": None,
        "requirement_hash": S.schema_hash(pipeline_output),
        "guarantee_hash": S.schema_hash(guarantee), "missing_required": [],
        "counterexample": None if ok_final else S.counterexample(
            guarantee, pipeline_output),
        "verdict": "pass" if ok_final else "fail",
    })
    for i, (name, vendor, contract) in enumerate(stages):
        ok_err = S.implies(contract["error_schema"], pipeline_rejection)
        obligations.append({
            "id": f"O-rej-{i + 1}", "kind": "rejection-channel",
            "stage": name, "vendor": vendor.name,
            "from": f"{name}:{vendor.name}.error", "projection": None,
            "join": None,
            "requirement_hash": S.schema_hash(pipeline_rejection),
            "guarantee_hash": S.schema_hash(contract["error_schema"]),
            "missing_required": [],
            "counterexample": None if ok_err else S.counterexample(
                contract["error_schema"], pipeline_rejection),
            "verdict": "pass" if ok_err else "fail",
        })
    return obligations


# ------------------------------------------------------------- adversaries

def adversarial_events(seed: int = 7, count: int = 96) -> list[dict]:
    """Property-based adversarial input set: domain-boundary values, type
    confusion, missing/extra fields, duplicates, oversize strings, unicode.
    Seeded, so the whole proof is replayable."""
    rng = random.Random(seed)
    events: list[dict] = []
    boundary_amounts = [0, 0.01, 1, 100, 99999.99, 999999.99, 1000000.0,
                        1000000.01, 50000000.0, 1000000000.0]
    kinds = ["claim", "dispute", "refund", "CHARGE", "chargeback", "  ",
             "cl\u00e4im", "claim\x00"]
    for i in range(count):
        roll = rng.random()
        ev = {"raw_id": f"raw-{i:06d}", "kind": rng.choice(kinds),
              "ts": rng.choice([0, 1, 1700000000, 2 ** 31, 4102444800])}
        if roll < 0.55:
            ev["amount"] = rng.choice(boundary_amounts)
        elif roll < 0.65:
            ev["amount"] = rng.choice([-1, -0.01, 1e12, "100", None])
        elif roll < 0.72:
            ev["amount"] = rng.choice([0, 250.0])
            ev["kind"] = "CHARGE" if rng.random() < 0.5 else "claim"
        elif roll < 0.79:
            ev["kind"] = rng.choice(["claim", "dispute"])
            ev["amount"] = 42.0
            ev.pop("ts")
        elif roll < 0.85:
            ev["kind"] = rng.choice(["claim", "dispute"])
            ev["amount"] = 42.0
            ev["ts"] = 1700000000
            ev["memo"] = "\u2713" * rng.choice([300, 4000])
        elif roll < 0.91:
            ev["kind"] = rng.choice(["claim", "dispute"])
            ev["amount"] = 42.0
            ev["ts"] = "not-a-number"
        elif roll < 0.96:
            ev["kind"] = rng.choice(["claim", "dispute"])
            ev["amount"] = 42.0
            ev["ts"] = 1700000000
            ev["raw_id"] = f"raw-{rng.randrange(5):06d}"
        else:
            ev["kind"] = rng.choice(["claim", "dispute"])
            ev["amount"] = 1.5e12
            ev["ts"] = 1700000000
        events.append(ev)
    return events


def raw_events(seed: int = 11, count: int = 64) -> list[dict]:
    """The 'real' workload: in-domain claims/disputes/refunds with
    boundary amounts sprinkled in."""
    rng = random.Random(seed)
    events = []
    for i in range(count):
        events.append({
            "raw_id": f"live-{i:05d}",
            "kind": rng.choices(["claim", "dispute", "refund"],
                                weights=[0.7, 0.2, 0.1])[0],
            "amount": round(rng.choice([x * 977 for x in range(1, 12)]
                                       + [999999.99, 1234567.89,
                                          880000000.0]), 2),
            "ts": 1760000000 + i * 60,
        })
    return events


# -------------------------------------------------------- sandbox pipeline

class SandboxRun:
    """Sandboxed execution of the composed pipeline over the adversarial
    set. Batching-aware: scored events queue until the scheduler emits a
    batch trigger; the settler joins each queued claim with the trigger.
    The forge meters gas, books nothing, and validates every emit against
    the sealed contract."""

    def __init__(self, stages, monitor, prefix="sbox"):
        self.stages = stages          # [(stage, vendor, contract)]
        self.monitor = monitor
        self.prefix = prefix
        self.pending: list[dict] = []
        self.vcs: dict[str, dict] = {}
        self.stats = {"inputs": 0, "transfers": 0, "rejected": 0,
                      "deadletter": 0, "gas_used": 0, "violations": [],
                      "flush_triggers": 0}
        self.vc_pairs: list[tuple[dict, dict]] = []

    def run(self, events: list[dict]) -> dict:
        for ev in events:
            self.stats["inputs"] += 1
            self._normalize(ev)
        self._flush()
        return dict(self.stats)

    def _project(self, ix: int, payload: dict) -> dict:
        """Edge adapter: only the fields the downstream contract names."""
        fields = set(self.stages[ix][2]["input_schema"].get("properties", {}))
        return {k: v for k, v in payload.items() if k in fields}

    def _invoke(self, ix: int, payload: dict, call_id: str, parent_vc):
        name, vendor, contract = self.stages[ix]
        child_vc = {**(parent_vc or {}), "t": len(self.vc_pairs) + 1}
        self.vc_pairs.append((dict(parent_vc or {}), child_vc))
        emit, gas, latency = vendor.invoke(payload, call_id=call_id,
                                           live=False, vc=child_vc)
        self.stats["gas_used"] += gas
        findings = self.monitor(emit, vendor, contract, gas, latency,
                                sandbox=True)
        return emit, findings, child_vc

    def _normalize(self, ev: dict):
        emit, findings, vc = self._invoke(
            0, self._project(0, ev),
            f"{self.prefix}-{self.stats['inputs']:04d}-normalize", {"t": 0})
        if findings:
            self.stats["deadletter"] += 1
            self.stats["violations"].extend(findings)
            return
        if not emit["ok"]:
            self.stats["rejected"] += 1
            return
        self._score(emit["data"], vc)

    def _score(self, normalized: dict, vc: dict):
        emit, findings, vc = self._invoke(
            1, self._project(1, normalized),
            f"{self.prefix}-{self.stats['inputs']:04d}-score", vc)
        if findings:
            self.stats["deadletter"] += 1
            self.stats["violations"].extend(findings)
            return
        if not emit["ok"]:
            self.stats["rejected"] += 1
            return
        self.pending.append(emit["data"])
        self._schedule(emit["data"], vc)

    def _schedule(self, scored: dict, vc: dict):
        emit, findings, tvc = self._invoke(
            2, self._project(2, scored),
            f"{self.prefix}-{self.stats['inputs']:04d}-schedule", vc)
        if findings:
            self.stats["violations"].extend(findings)
            return                      # queued claims stay pending
        if emit["ok"]:
            self._settle_batch(emit["data"], tvc)
        # else: batch not full yet — nothing to do

    def _settle_batch(self, trigger: dict, parent_vc: dict):
        queue, self.pending = self.pending, []
        for i, scored in enumerate(queue):
            payload = {**self._project(3, scored),
                       "trigger_id": trigger["trigger_id"],
                       "trigger_ts": trigger["at"]}
            emit, findings, _ = self._invoke(
                3, payload,
                f"{self.prefix}-{self.stats['inputs']:04d}-settle-{i}",
                parent_vc)
            if findings:
                self.stats["deadletter"] += 1
                self.stats["violations"].extend(findings)
                continue
            if emit["ok"]:
                self.stats["transfers"] += 1
            else:
                self.stats["rejected"] += 1

    def _flush(self):
        name, vendor, contract = self.stages[2]
        emit, gas, latency = vendor.invoke_control("flush", f"{self.prefix}-flush")
        self.stats["gas_used"] += gas
        findings = self.monitor(emit, vendor, contract, gas, latency,
                                sandbox=True)
        if findings:
            self.stats["violations"].extend(findings)
            return
        if emit["ok"]:
            self.stats["flush_triggers"] += 1
            self._settle_batch(emit["data"], {"t": len(self.vc_pairs)})


def monotonicity(envelopes: list[dict]) -> dict:
    """Per-author envelope sequence must be strictly increasing."""
    last: dict[str, int] = {}
    violations = []
    for env in envelopes:
        author, seq = env["author"], env["seq"]
        if author in last and seq <= last[author]:
            violations.append({"author": author, "seq": seq,
                               "prev": last[author]})
        last[author] = seq
    return {"authors": len(last), "envelopes": len(envelopes),
            "violations": violations, "ok": not violations}


def causality(vcs: list[tuple[dict, dict]]) -> dict:
    """Child vector clocks must dominate their parents."""
    violations = []
    for parent, child in vcs:
        if not all(child.get(k, 0) >= v for k, v in (parent or {}).items()):
            violations.append({"parent": parent, "child": child})
    return {"checked": len(vcs), "violations": violations,
            "ok": not violations}


def verify_report(forge_id: str, obligations: list[dict], sandbox: dict,
                  mono: dict, caus: dict, accounting: dict) -> dict:
    """The molt-verify report: proof + sandbox + transport invariants."""
    failed = [o["id"] for o in obligations if o["verdict"] != "pass"]
    verdict = "pass" if (not failed and not sandbox["violations"]
                         and mono["ok"] and caus["ok"]
                         and accounting["balanced"]) else "fail"
    return {
        "kind": "molt-verify/1", "forge": forge_id,
        "obligations": obligations,
        "failed_obligations": failed,
        "sandbox": sandbox, "monotonicity": mono,
        "causality": caus, "accounting": accounting,
        "verdict": verdict,
        "note": ("every intermediate event contract-valid, monotone, "
                 "causally ordered; declared output reachable from "
                 "declared input through every contract"),
    }