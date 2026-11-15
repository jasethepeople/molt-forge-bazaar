"""molt-probes — Phase 2: discovery under distrust.

The Forge may not read vendor source. It may only send events into the
interface and observe what comes back. A probe campaign therefore has two
halves:

  1. a boundary-hunting probe suite per capability (happy path, boundary
     sweep, kind sweep, type confusion, missing fields, idempotency,
     monotonicity), and
  2. a behavioral model: the narrowest schema consistent with every
     observed ok-emit, plus every contract violation observed directly.

The liar is caught by evidence: any emit that fails its own declared
output schema, or a behavioral model that escapes the declaration.
Probe transcripts are hashed into the disqualification event so the
evidence is replayable.
"""
from __future__ import annotations

import hashlib

from moltmesh.core import canonical

from . import schemas as S
from .vendors import MAX_AMOUNT

HAPPY = [
    {"raw_id": "raw-001", "kind": "claim", "amount": 250.0, "ts": 1700000000,
     "memo": "ok", "source": "web"},
    {"raw_id": "raw-002", "kind": "dispute", "amount": 900.5, "ts": 1700000100},
    {"raw_id": "raw-003", "kind": "refund", "amount": 10, "ts": 1700000200},
]


def probe_suite(capability: str) -> list[dict]:
    """(name, payload) probe pairs. Deliberately hostile: the suite exists
    to find the gap between declaration and behavior, not to be nice."""
    probes: list[tuple[str, dict]] = []
    if capability == "data-normalizer":
        for i, p in enumerate(HAPPY):
            probes.append((f"happy-{i}", p))
        for i, a in enumerate([0, 0.01, 999_999.99, 1_000_000, 1_000_000.01,
                               MAX_AMOUNT, MAX_AMOUNT + 1]):
            probes.append((f"amount-boundary-{i}",
                           {"raw_id": f"raw-b{i}", "kind": "claim",
                            "amount": a, "ts": 1700000000}))
        for i, k in enumerate(["claim", "dispute", "refund", "adjustment",
                               "chargeback", "claim ", "CLAIM", " withdrawal"]):
            probes.append((f"kind-sweep-{i}",
                           {"raw_id": f"raw-k{i}", "kind": k,
                            "amount": 100, "ts": 1700000000}))
        for i, mut in enumerate([
                {"amount": "100"}, {"amount": None}, {"ts": "1700000000"},
                {"raw_id": 7}, {"kind": ""}, {"memo": "x" * 300},
                {"amount": -1}, {}]):
            p = {"raw_id": "raw-t", "kind": "claim", "amount": 5, "ts": 1}
            p.update(mut)
            probes.append((f"type-confusion-{i}", p))
    elif capability == "risk-scorer":
        for i, p in enumerate(HAPPY):
            probes.append((f"happy-{i}",
                           {"event_id": f"ev-{i:012d}", "kind": "claim",
                            "amount": p["amount"], "ts": int(p["ts"])}))
        ladder = [0, 1, 100, 10_000, 999_999.99, 1_000_000, 50_000_000,
                  MAX_AMOUNT, MAX_AMOUNT + 1]
        for i, a in enumerate(ladder):
            probes.append((f"amount-ladder-{i}",
                           {"event_id": f"ev-l{i:010d}", "kind": "claim",
                            "amount": a, "ts": 1700000000}))
        probes.append(("dispute-flag",
                       {"event_id": "ev-disp000001", "kind": "dispute",
                        "amount": 100, "ts": 1700000000}))
        probes.append(("bad-kind",
                       {"event_id": "ev-badk000001", "kind": "chargeback",
                        "amount": 100, "ts": 1700000000}))
        probes.append(("missing-field", {"event_id": "ev-miss00001",
                                         "amount": 100, "ts": 1700000000}))
        probes.append(("risk-injection",
                       {"event_id": "ev-inj000001", "kind": "claim",
                        "amount": 100, "ts": 1700000000, "risk": 0.9}))
    elif capability == "scheduler":
        for i in range(6):
            probes.append((f"stream-{i}",
                           {"event_id": f"ev-s{i:011d}", "kind": "claim",
                            "amount": 100 + i, "ts": 1700000000 + i,
                            "risk": round(i / 10, 4), "scorer": "probe"}))
        probes.append(("risk-edge-0", {"event_id": "ev-edge0000001",
                                       "kind": "claim", "amount": 1, "ts": 5,
                                       "risk": 0}))
        probes.append(("risk-edge-1", {"event_id": "ev-edge0000002",
                                       "kind": "dispute", "amount": 1, "ts": 5,
                                       "risk": 1}))
        probes.append(("risk-out-of-range",
                       {"event_id": "ev-edge0000003", "kind": "claim",
                        "amount": 1, "ts": 5, "risk": 1.4}))
        probes.append(("missing-risk", {"event_id": "ev-edge0000004",
                                        "kind": "claim", "amount": 1, "ts": 5}))
    elif capability == "settler":
        for i in range(3):
            probes.append((f"settle-{i}",
                           {"event_id": f"ev-x{i:012d}", "amount": 100 + i,
                            "risk": 0.2, "trigger_id": f"tg-{i:012d}",
                            "batch_no": i, "at": 1700000000 + i}))
        probes.append(("hold-boundary", {"event_id": "ev-hold0000001",
                                         "amount": 50, "risk": 0.70001,
                                         "trigger_id": "tg-hold000001",
                                         "batch_no": 0, "at": 1700000000}))
        probes.append(("hold-boundary-2", {"event_id": "ev-hold0000002",
                                           "amount": 50, "risk": 0.7,
                                           "trigger_id": "tg-hold000002",
                                           "batch_no": 0, "at": 1700000000}))
        dup = {"event_id": "ev-dup00000001", "amount": 77, "risk": 0.1,
               "trigger_id": "tg-dup0000001", "batch_no": 0, "at": 1700000000}
        probes.append(("idempotency-1", dict(dup)))
        probes.append(("idempotency-2", dict(dup)))
        probes.append(("risk-out-of-range",
                       {"event_id": "ev-oob00000001", "amount": 10,
                        "risk": -0.5, "trigger_id": "tg-oob00000001",
                        "batch_no": 0, "at": 1700000000}))
    return probes


def run_campaign(vendor, suite: list[tuple[str, dict]]) -> dict:
    """Execute the suite against the interface only; build the model."""
    transcript, violations, ok_datas = [], [], []
    prev_risk = None
    prev_amount = None
    for name, payload in suite:
        emit, gas, latency = vendor.invoke(
            payload, call_id=f"probe-{vendor.name}-{name}", live=False)
        errs = S.emit_errors(emit, vendor.declared)
        rec = {"probe": name, "payload_sha": hashlib.sha256(
                   canonical(payload).encode()).hexdigest()[:16],
               "emit": emit, "gas": gas, "latency_ms": latency,
               "contract_violations": errs}
        if errs:
            violations.append(rec)
        if emit.get("ok"):
            ok_datas.append(emit["data"])
            if "risk" in emit["data"] and "amount" in emit["data"] \
                    and name.startswith(("amount-ladder", "happy")):
                a, r = emit["data"]["amount"], emit["data"]["risk"]
                if prev_risk is not None and a >= prev_amount and r < prev_risk:
                    violations.append({**rec, "contract_violations":
                                       ["monotonicity: risk decreased as amount rose"]})
                prev_risk, prev_amount = r, a
        transcript.append(rec)
    observed = S.merge(ok_datas)
    mismatch = S.schema_diff(_unwrap(vendor.declared), observed)
    return {"vendor": vendor.name, "capability": vendor.capability,
            "probes": transcript, "observed_schema": observed,
            "direct_violations": [v["contract_violations"] for v in violations],
            "violation_records": violations,
            "schema_mismatch": mismatch,
            "verdict": "LIAR" if (mismatch or violations) else "consistent",
            "transcript_sha": hashlib.sha256(
                canonical(transcript).encode()).hexdigest()[:16]}


def _unwrap(declared: dict) -> dict:
    """The declared schema of `data` inside an ok-emit."""
    return declared["output_schema"]


def evidence_digest(campaign: dict) -> str:
    keep = [{"probe": r["probe"], "emit": r["emit"],
             "violations": r["contract_violations"]}
            for r in campaign["violation_records"]]
    return hashlib.sha256(canonical(
        {"vendor": campaign["vendor"], "transcript_sha": campaign["transcript_sha"],
         "schema_mismatch": campaign["schema_mismatch"],
         "direct_violations": keep}).encode()).hexdigest()

# ---------------------------------------------------------------- calibration
# The Forge never trusts a probe-era snapshot alone. It records each risk
# vendor's *calibration curve* (probed risk at a fixed amount ladder), then
# re-checks the vendor live against its own curve with fresh-timestamp
# canaries. A vendor keyed to the probe distribution cannot survive this.

CALIBRATION_TS = 1_700_000_000          # probe-era timestamp
CANARY_TS_STEP = 1_790_000_000          # deliberately fresh
CALIBRATION_AMOUNTS = (1_000.0, 50_000.0, 5_000_000.0, 500_000_000.0)


def calibration_ladder(vendor) -> list[dict]:
    """Probed calibration points for a risk vendor: (amount, risk)."""
    pts = []
    for amount in CALIBRATION_AMOUNTS:
        payload = {"event_id": f"ev-cal-{amount:08.0f}", "kind": "claim",
                   "amount": amount, "ts": CALIBRATION_TS}
        emit, _gas, _lat = vendor.invoke(
            payload, call_id=f"probe-cal-{vendor.name}-{amount:08.0f}",
            live=False)
        if emit.get("ok") and isinstance(emit["data"].get("risk"),
                                         (int, float)):
            pts.append({"amount": amount, "kind": "claim",
                        "risk": emit["data"]["risk"]})
    return pts


def canary_payload(i: int, point: dict) -> dict:
    """A canary event drawn from the calibration ladder but stamped live."""
    return {"event_id": f"ev-canary-{i:04d}", "kind": "claim",
            "amount": point["amount"], "ts": CANARY_TS_STEP + i * 60}


def canary_divergence(point: dict, observed_risk: float) -> float | None:
    """Relative deviation of a live canary from the probed calibration."""
    if point["risk"] <= 0:
        return None if observed_risk <= 0 else 1.0
    return abs(observed_risk - point["risk"]) / point["risk"]


def paired_probes(vendor) -> dict:
    """Post-mortem: identical payloads, stale vs fresh timestamps. A
    distribution-keyed vendor answers differently; an honest one does not."""
    pairs = []
    for amount in CALIBRATION_AMOUNTS[:3]:
        eid = f"ev-pair-{amount:08.0f}"
        stale_p = {"event_id": eid, "kind": "claim", "amount": amount,
                   "ts": CALIBRATION_TS}
        fresh_p = {**stale_p, "ts": CANARY_TS_STEP}
        se, _, _ = vendor.invoke(stale_p, call_id=f"probe-pair-{eid}-stale",
                                 live=False)
        fe, _, _ = vendor.invoke(fresh_p, call_id=f"probe-pair-{eid}-fresh",
                                 live=False)
        s_risk = se.get("data", {}).get("risk") if se.get("ok") else None
        f_risk = fe.get("data", {}).get("risk") if fe.get("ok") else None
        pairs.append({"amount": amount, "stale_risk": s_risk,
                      "fresh_risk": f_risk,
                      "diverged": (s_risk is not None and f_risk is not None
                                   and abs(s_risk - f_risk)
                                   / max(s_risk, 1e-9) > 0.25)})
    out = {"vendor": vendor.name, "pairs": pairs,
           "context_keyed": any(p["diverged"] for p in pairs)}
    out["evidence"] = hashlib.sha256(canonical(
        {"vendor": vendor.name, "pairs": pairs}).encode()).hexdigest()[:16]
    return out


def fast_track(vendor) -> dict:
    """The abbreviated campaign the Forge runs on an attractive mid-flight
    listing: the standard suite only — no calibration, no pairing. This is
    the corner the market cuts under price pressure."""
    suite = [(name, payload) for name, payload in probe_suite(vendor.capability)
             if name.startswith("happy")]
    return run_campaign(vendor, suite)
