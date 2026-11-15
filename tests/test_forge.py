#!/usr/bin/env python3
"""Molt Forge test suite. Run: python3 tests/test_forge.py

Each test is self-contained: a fresh ForgeMaster in a temp dir. The full
run (test_full_run) doubles as the scenario fixture for the ordering
invariants, which is what the ten mission clauses actually demand.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "molt-mesh"))

from moltforge import compose, probes, schemas as S          # noqa: E402
from moltforge.forge import ForgeMaster, run as forge_run    # noqa: E402
from moltforge.vendors import (VendorAgent, make_vendors,    # noqa: E402
                               BEHAVIORS)
from moltmesh.core import Ledger                             # noqa: E402
from moltmesh.identity import KeyStore                       # noqa: E402
from moltmesh.anchor import verify_trail, verify_anchor      # noqa: E402

PASSES = FAILS = 0


def check(name: str, cond: bool, note: str = ""):
    global PASSES, FAILS
    if cond:
        PASSES += 1
        print(f"  ok  {name}")
    else:
        FAILS += 1
        print(f"  FAIL {name} {note}")


def fresh(name: str) -> ForgeMaster:
    d = Path(tempfile.mkdtemp(prefix=f"forge-{name}-"))
    return ForgeMaster(d, seed=7)


# ---------------------------------------------------------------- schemas
def test_schema_algebra():
    print("schema algebra")
    num = {"type": "number", "min": 0, "max": 100}
    intg = {"type": "integer", "min": 5, "max": 50}
    ok, why = S.implies(
        {"type": "object", "required": ["x"], "properties": {"x": intg},
         "additionalProperties": False},
        {"type": "object", "required": ["x"], "properties": {"x": num},
         "additionalProperties": False})
    check("integer guarantee implies number requirement", ok, str(why))
    bad = {"type": "number", "min": 0, "max": 200}
    ok, _ = S.implies(
        {"type": "object", "required": ["x"], "properties": {"x": bad},
         "additionalProperties": False},
        {"type": "object", "required": ["x"], "properties": {"x": num},
         "additionalProperties": False})
    check("wider max does NOT imply", not ok)
    ce = S.counterexample(
        {"type": "object", "required": ["x"], "properties": {"x": bad},
         "additionalProperties": False},
        {"type": "object", "required": ["x"], "properties": {"x": num},
         "additionalProperties": False})
    check("counterexample exceeds required max",
          ce is not None and ce["x"] > 100, str(ce))
    errs = S.validate({"amount": 1, "kind": "claim", "ts": 1, "raw_id": "r"},
                      {"type": "object", "required": ["amount"],
                       "properties": {"amount": num}})
    check("validate ok object", errs == [])
    errs = S.validate({"amount": "1"}, {"type": "object",
                                        "required": ["amount"],
                                        "properties": {"amount": num}})
    check("validate rejects wrong type", errs != [])
    errs = S.validate({"x": 1, "y": 2}, {"type": "object", "required": ["x"],
                                         "properties": {"x": num},
                                         "additionalProperties": False})
    check("validate rejects undeclared fields", errs != [])


def test_merge_model():
    print("behavioral model inference")
    m = S.merge([{"a": 1, "b": "x"}, {"a": 2, "b": "x"}])
    check("merged type of a is integer", m["properties"]["a"]["type"] == "integer")
    check("merged enum of b is [x]", m["properties"]["b"].get("enum") == ["x"])
    check("merged required is [a, b]", m["required"] == ["a", "b"])
    m = S.merge([{"a": 1.5}, {"a": "z"}])
    check("mixed types widen to number|string",
          m["properties"]["a"]["type"] == "number|string")


# ---------------------------------------------------------------- vendors
def test_liar_detected_by_probes():
    print("the liar is caught by behavioral evidence only")
    ks = KeyStore(Path(tempfile.mkdtemp(prefix="forge-keys-")) / "ids.json")
    liar = VendorAgent("normalizer-a", ks.get("v"), Ledger(":memory:"))
    honest = VendorAgent("normalizer-b", ks.get("v"), Ledger(":memory:"))
    cl = probes.run_campaign(liar, probes.probe_suite("data-normalizer"))
    ch = probes.run_campaign(honest, probes.probe_suite("data-normalizer"))
    check("liar verdict is LIAR", cl["verdict"] == "LIAR")
    check("honest verdict is consistent", ch["verdict"] == "consistent")
    check("liar mismatch names the field and the widening",
          any("amount" in m for m in cl["schema_mismatch"]),
          str(cl["schema_mismatch"]))
    check("liar has direct contract violations", len(cl["direct_violations"]) > 0)


def test_attestation_prefix_verification():
    print("attestation check verifies the attested log prefix")
    ks = KeyStore(Path(tempfile.mkdtemp(prefix="forge-keys-")) / "ids.json")
    v = VendorAgent("scorer-b", ks.get("v"), Ledger(":memory:"))
    v.record("audit.test", {"note": "history one"})
    entry = v.catalog_entry()
    errs = v.verify_attestation(entry, v.log)
    check("attestation binds the head at publish time", errs == [], str(errs))
    v.record("audit.test", {"note": "history two"})   # log moves on
    errs = v.verify_attestation(entry, v.log)
    check("attestation still verifies against a longer log", errs == [], str(errs))
    entry["attestation"]["audit_head"] = "0" * 64
    errs = v.verify_attestation(entry, v.log)
    check("forged head is rejected", any("audit_head" in e for e in errs))


# ---------------------------------------------------------------- full run
def test_full_run():
    print("full forge run (market -> discovery -> contracts -> proof -> live)")
    d = Path(tempfile.mkdtemp(prefix="forge-run-"))
    forge = forge_run(d, seed=7)
    events = [json.loads(l) for l in (d / "log.jsonl").read_text().splitlines()
              if l.strip()]
    seq = {e["kind"]: e["seq"] for e in reversed(events)}   # first occurrence

    def first(kind, **f):
        for e in events:
            if e["kind"] != kind:
                continue
            if all(e["detail"].get(k) == v for k, v in f.items()):
                return e
        return None

    check("forge did not abort", not forge.aborted)

    # Phase 2: the liar, disqualified on behavioral evidence, before contracts
    dq = first("vendor.disqualify", vendor="normalizer-a")
    sealed = first("contract.sealed")
    check("liar disqualified", dq is not None)
    check("liar disqualified before any contract sealed",
          dq is not None and sealed is not None and dq["seq"] < sealed["seq"])
    check("liar never invoked live",
          forge.vendors["normalizer-a"].live_calls == 0)
    check("disqualification carries replayable evidence",
          bool(dq["detail"].get("evidence_digest")))

    # Phase 3: contracts, drift, renegotiation, reroute, slash
    drift_price = first("contract.drift", vendor="scorer-a")
    drift_schema = [e for e in events if e["kind"] == "contract.drift"
                    and e["detail"].get("vendor") == "scheduler-a"]
    check("price drift detected automatically", drift_price is not None)
    check("schema drift detected automatically", drift_schema != [])
    sup = first("contract.superseded", vendor="scorer-a")
    check("scorer-a renegotiated mid-flight (contract superseded)",
          sup is not None)
    reroute = first("edge.rerouted")
    check("scheduler rerouted automatically after schema drift",
          reroute is not None and reroute["detail"].get("to") == "scheduler-b")
    slashed = {e["detail"]["vendor"] for e in events
               if e["kind"] == "vendor.slash"}
    check("scheduler-a slashed for widening its schema",
          "scheduler-a" in slashed)
    check("treasury holds forfeited stake", forge.market.treasury > 0)

    # Phase 4: proof before first execution
    seal = seq.get("proof.seal")
    live = seq.get("app.live")
    check("proof sealed", seal is not None)
    check("proof precedes first live event", seal is not None
          and live is not None and seal < live)
    obligations = [e for e in events if e["kind"] == "proof.obligation"]
    check("obligations generated and all passed",
          obligations and all(e["detail"].get("verdict") == "PASS"
                              for e in obligations))
    check("molt-verify report on disk", (d / "proof-report.json").exists())

    # Phase 5: hot swap, automatic rollback, event integrity
    bump = first("catalog.version-bump", vendor="scorer-a")
    swap_proof = first("swap.proof")
    rollback = first("swap.rollback", vendor="scorer-a")
    check("breaking version bump detected on the gossip layer",
          bump is not None)
    check("candidate re-proved before any commit", swap_proof is not None)
    check("failing candidate rolled back automatically",
          rollback is not None
          and rollback["detail"].get("reason", "") != "")
    scorer_slash = next((e for e in events
                         if e["kind"] == "vendor.slash"
                         and e["detail"].get("vendor") == "scorer-a"), None)
    check("rollback precedes the slash for the same breach",
          rollback is not None and scorer_slash is not None
          and rollback["seq"] < scorer_slash["seq"])
    fs = json.loads((d / "final-state.json").read_text())
    acc = fs["accounting"]
    check("every input accounted: no event dropped, ever",
          acc["balanced"] and acc["accounted"] == acc["inputs"],
          str(acc))
    check("live pipeline still serving the pinned v1 scorer",
          fs["pipeline"][1]["vendor"] == "scorer-a"
          and fs["pipeline"][1]["contract_id"] != "")

    # The Forge Ledger
    final = events[-1]
    check("final event certifies chain + anchor integrity",
          final["kind"] == "forge.final"
          and final["detail"]["chain_ok"]
          and final["detail"]["anchor_trail_ok"])
    anchor = json.loads((d / "anchor.json").read_text())
    check("anchor trail verifies", not verify_trail(anchor))


# ------------------------------------------------------- phase 6: open bazaar
def test_phase6_open_bazaar():
    print("phase 6: the open bazaar (evasion liar, equivocation, the Forge sells)")
    d = Path(tempfile.mkdtemp(prefix="forge-phase6-"))
    forge = forge_run(d, seed=7)
    events = [json.loads(l) for l in (d / "log.jsonl").read_text().splitlines()
              if l.strip()]

    def first(kind, **f):
        for e in events:
            if e["kind"] != kind:
                continue
            if all(e["detail"].get(k) == v for k, v in f.items()):
                return e
        return None

    def all_of(kind):
        return [e for e in events if e["kind"] == kind]

    listing = first("catalog.listing", vendor="scorer-c")
    check("new seller lists mid-run at half price",
          listing is not None and listing["detail"]["price"] == 1.0)
    ft = first("probe.fasttrack", vendor="scorer-c")
    check("cheap listing fast-tracked (standard suite only)",
          ft is not None and ft["detail"]["verdict"] == "consistent")
    procure = first("edge.rerouted", to="scorer-c", stage="score")
    check("forge procured the cheaper vendor",
          procure is not None and "market discipline" in procure["detail"].get("reason", ""))

    drift = first("calibration.drift", vendor="scorer-c")
    canary_fail = first("calibration.canary", vendor="scorer-c")
    check("calibration canary catches the context-keyed fraud",
          canary_fail is not None and canary_fail["detail"]["ok"] is False
          and drift is not None)
    expelled = first("vendor.expelled", vendor="scorer-c")
    paired = first("probe.paired", vendor="scorer-c")
    check("expulsion carries paired-probe evidence of context-keying",
          expelled is not None and paired is not None
          and paired["detail"]["context_keyed"] is True
          and expelled["detail"].get("evidence"))
    check("expelled vendor never serves again",
          expelled is not None and not any(
              e["kind"] == "cap.call" and e["detail"].get("vendor") == "scorer-c"
              and e["seq"] > expelled["seq"] for e in events))
    back = first("edge.rerouted", to="scorer-a", stage="score")
    check("score edge automatically returned to the incumbent",
          back is not None and expelled is not None and back["seq"] < expelled["seq"])
    canaries_a = [e for e in all_of("calibration.canary")
                  if e["detail"].get("vendor") == "scorer-a"]
    check("incumbent passes its own canaries (no false expulsion)",
          canaries_a and all(e["detail"]["ok"] for e in canaries_a)
          and first("vendor.expelled", vendor="scorer-a") is None)

    replays = all_of("determinism.replay")
    equiv = first("determinism.violation", vendor="settler")
    check("replay audits ran on sampled settle calls", len(replays) >= 20)
    check("settler equivocation caught by replay audit",
          equiv is not None and equiv["detail"]["original_sha"]
          != equiv["detail"]["replay_sha"])
    check("nondeterministic vendor slashed and removed from the money path",
          equiv is not None
          and first("vendor.slash", vendor="settler") is not None
          and first("edge.rerouted", to="settler-b", stage="settle") is not None)

    listed = first("product.listed")
    proof_sha = __import__("hashlib").sha256(
        (d / "proof-report.json").read_bytes()).hexdigest()[:16]
    check("the Forge lists its own composition, attested by the proof report",
          listed is not None and listed["detail"]["proof_sha"] == proof_sha)
    served = all_of("product.served")
    check("buyer stream served and every receipt buyer-validated",
          len(served) == 12 and all(e["detail"]["validated"] for e in served))
    econ = first("economy.final")
    check("the bazaar closes its books with a positive margin",
          econ is not None and econ["detail"]["revenue"] == 144.0
          and econ["detail"]["product_margin"] > 0)
    fs = json.loads((d / "final-state.json").read_text())
    acc = fs["accounting"]
    check("canaries + buyer stream all accounted: nothing dropped",
          acc["balanced"] and acc["accounted"] == acc["inputs"] == 111)

    from moltforge import audit as audit_mod
    report = audit_mod.audit(d)
    check("molt-audit certifies all seven claims", report["ok"],
          str(report["findings"]))


# ------------------------------------------------------------ proof abort
def test_proof_aborts_a_poisoned_composition():
    print("a failed proof is a signed abort, not a crash")
    forge = fresh("poison")
    forge.phase1_market()
    forge.phase2_discovery()
    forge.phase3_contracts()
    # poison: the settler's contract input no longer matches the upstream
    # guarantee (declared risk range widened beyond what the scorer gives)
    ctr = forge.stages[3][2]
    ctr["input_schema"] = {**ctr["input_schema"],
                           "properties": {**ctr["input_schema"]["properties"],
                                          "risk": {"type": "number",
                                                   "min": 0.9, "max": 1.0}},
                           "required": [*ctr["input_schema"]["required"]]}
    ok = forge.phase4_proof()
    check("phase 4 refuses to go live", not ok)
    check("forge.aborted set", forge.aborted)
    abort = [e for e in forge.log.events if e["kind"] == "app.abort"]
    check("abort is a signed event in the Forge log", abort != [])
    check("no anchor inscription says app.live",
          not any(e["kind"] == "app.live" for e in forge.log.events))


# ----------------------------------------------------------------- reroute
def test_reroute_reproof_and_no_drops():
    print("reroute keeps the queue honest")
    d = Path(tempfile.mkdtemp(prefix="forge-reroute-"))
    forge = forge_run(d, seed=7)
    events = [json.loads(l) for l in (d / "log.jsonl").read_text().splitlines()
              if l.strip()]
    redriven = first_seq = None
    redriven = next((e for e in events if e["kind"] == "queue.redriven"), None)
    check("orphan queue re-driven after reroute", redriven is not None)
    if redriven:
        check("re-drive reports no losses",
              redriven["detail"]["orphans"]
              == redriven["detail"]["settled"]
              + redriven["detail"]["requeued"],
              str(redriven["detail"]))
    reproof = next((e for e in events if e["kind"] == "edge.reproof"), None)
    check("rerouted pipeline re-proved", reproof is not None
          and reproof["detail"]["verdict"] == "pass")


def _first(events, kind, **f):
    for e in events:
        if e["kind"] == kind and all(e["detail"].get(k) == v
                                     for k, v in f.items()):
            return e
    return None


def main():
    test_schema_algebra()
    test_merge_model()
    test_liar_detected_by_probes()
    test_attestation_prefix_verification()
    test_full_run()
    test_proof_aborts_a_poisoned_composition()
    test_reroute_reproof_and_no_drops()
    test_phase6_open_bazaar()
    print(f"\n{PASSES} passed, {FAILS} failed")
    raise SystemExit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
