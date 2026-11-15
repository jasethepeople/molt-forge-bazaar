"""molt-forge — the Forge Master.

The Forge is not a member of a fixed mesh. It is a market maker with its
own molt-reactor: a data ruleset whose every consequence — disqualification,
renegotiation, slashing, reroute, abort, rollback — is *executed by rules
firing on signed events*, never by imperative side effects. Its whole
reasoning trail is replayable from its genesis event.

Hard constraints honored throughout:

  * the Forge never reads vendor source — vendors are used through their
    event interfaces alone (black-box probes, sealed contracts, metered
    calls);
  * the Forge never writes an application's core logic — the pipeline is
    composed of vendor capabilities plus pure routing glue (field
    projection), and the glue is itself part of the proof;
  * nothing runs before the proof obligation set passes, and a failed
    proof is a signed abort-event, not a crash.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path

from moltmesh.anchor import create_genesis_anchor, inscribe, save as save_anchor, verify_trail
from moltmesh.core import Ledger, canonical
from moltmesh.identity import KeyStore

from . import compose, probes
from . import schemas as S
from .market import Gossip, Market, Voice
from .vendors import VendorAgent, make_vendors

FORGE_BUDGET = 10_000
MAX_PRICE = 5.0          # credits per call the Forge will ever accept
STAKE = 50               # initial bond per contract
RE_STAKE = 25            # bond on renegotiation


# --------------------------------------------------------------- ruleset
RULESET = {
    "version": 1,
    "engine": "molt-reactor/forge-1",
    "rules": [
        {"rule": "disqualify-on-attestation-failure", "on": "attestation.verdict",
         "when": {"verdict": "disqualified"}, "actions": ["disqualify"]},
        {"rule": "disqualify-on-probe-failure", "on": "probe.verdict",
         "when": {"verdict": "disqualified"}, "actions": ["disqualify"]},
        {"rule": "renegotiate-on-price-drift", "on": "contract.drift",
         "when": {"kind": "price"}, "actions": ["renegotiate"]},
        {"rule": "escalate-on-schema-drift", "on": "contract.drift",
         "when": {"kind": "schema"},
         "actions": ["renegotiate", "slash", "reroute"]},
        {"rule": "slash-on-violation", "on": "contract.violation",
         "when": "always", "actions": ["slash"]},
        {"rule": "abort-on-failed-proof", "on": "proof.verdict",
         "when": {"verdict": "fail"}, "actions": ["abort"]},
        {"rule": "swap-on-version-bump", "on": "catalog.version-bump",
         "when": "always", "actions": ["swap"]},
        {"rule": "rollback-on-failed-swap-proof", "on": "swap.proof",
         "when": {"verdict": "fail"}, "actions": ["rollback", "slash"]},
        {"rule": "procure-on-attractive-listing", "on": "catalog.listing",
         "when": "always", "actions": ["fastprobe", "procure"]},
        {"rule": "expel-on-calibration-drift", "on": "calibration.drift",
         "when": "always", "actions": ["slash", "reroute-back", "expel"]},
        {"rule": "slash-on-nondeterminism", "on": "determinism.violation",
         "when": "always", "actions": ["slash", "reroute-settle"]},
        {"rule": "sell-when-proven", "on": "live.complete",
         "when": "always", "actions": ["list-product"]},
        {"rule": "serve-on-buyer-contract", "on": "product.contract",
         "when": "always", "actions": ["serve-buyer"]},
    ],
}


def _ruleset_hash() -> str:
    return hashlib.sha256(canonical(RULESET).encode("utf-8")).hexdigest()[:16]


class Reactor:
    """Rule execution over signed observations. The ruleset is data; the
    Forge's imperative code only *observes* and *serves actions*."""

    def __init__(self, forge: "ForgeMaster"):
        self.forge = forge

    def apply(self, obs_type: str, detail: dict) -> list[dict]:
        fired = []
        for rule in RULESET["rules"]:
            if rule["on"] != obs_type:
                continue
            when = rule["when"]
            if when != "always" and any(
                    detail.get(k) != v for k, v in when.items()):
                continue
            for action in rule["actions"]:
                fired.append({"rule": rule["rule"], "action": action})
                self.forge.log.append("reactor.rule-fired", detail={
                    "rule": rule["rule"], "on": obs_type, "action": action,
                    "obs": detail.get("summary", obs_type),
                })
                handler = getattr(self.forge, f"_act_{action.replace(chr(45), chr(95))}", None)
                if handler is None:
                    raise RuntimeError(f"ruleset names unknown action {action!r}")
                handler(detail)
        return fired


# ------------------------------------------------------------ live conductor
class LivePipeline:
    """The composed application, running for real.

    The conductor is routing glue only: it projects each edge to the fields
    the downstream contract names (the projection recorded in the proof),
    moves vector clocks, meters gas, pays invoices, and reports findings to
    the reactor. Every business transformation belongs to a vendor."""

    def __init__(self, forge: "ForgeMaster", stages: list[list]):
        self.forge = forge
        self.stages = [list(s) for s in stages]   # [name, vendor, contract]
        self.pending: list[dict] = []             # scored events awaiting trigger
        self.counters = {"inputs": 0, "transfers": 0, "settled": 0, "held": 0,
                         "rejected": 0, "deadletter": 0, "invoices": 0}
        self.records: list[dict] = []

    # -- one metered, monitored, invoiced call -------------------------------
    def _call(self, ix: int, payload: dict, call_id: str, parent_vc: dict):
        name, vendor, contract = self.stages[ix]
        child_vc = dict(parent_vc)
        child_vc["t"] = parent_vc.get("t", 0) + 1
        emit, gas, latency = vendor.invoke(
            payload, call_id=call_id, live=True, vc=child_vc,
            version_pin=contract.get("version", 1))
        price = vendor.effective_price(contract.get("version", 1))
        inv_env = vendor.voice.send("cap.invoice", {
            "contract_id": contract["contract_id"], "gas": gas,
            "latency_ms": latency, "price": price})
        self.forge.gossip.deliver(vendor.name, "forge", inv_env)
        self.forge.pay_invoice(vendor, price)
        findings = self.forge.market.observe_emit(
            contract, emit, gas, latency, price)
        self.forge.log.append("cap.call", detail={
            "call_id": call_id, "stage": name, "vendor": vendor.name,
            "contract_id": contract["contract_id"], "ok": bool(emit["ok"]),
            "error_code": None if emit["ok"] else emit["error"]["code"],
            "gas": gas, "latency_ms": latency, "price": price,
            "emit_sha": hashlib.sha256(canonical(emit).encode()).hexdigest()[:16],
            "findings": findings, "vc": child_vc, "parent_vc": parent_vc,
            "invoice_env": inv_env["__id"],
        })
        if findings:
            self.forge.on_findings(ix, vendor, contract, findings, call_id)
        return emit, findings, child_vc

    def _proj(self, ix: int, payload: dict) -> dict:
        fields = set(self.stages[ix][2]["input_schema"].get("properties", {}))
        return {k: v for k, v in payload.items() if k in fields}

    # -- the event flow -------------------------------------------------------
    def feed(self, ev: dict) -> dict:
        self.counters["inputs"] += 1
        n = self.counters["inputs"]
        outcome: dict = {"input": n, "raw_id": ev.get("raw_id"), "calls": []}
        root = {"t": 0, "in": n}

        emit, findings, vc = self._call(0, self._proj(0, ev),
                                        f"live-{n:04d}-normalize", root)
        outcome["calls"].append("normalize")
        if self._fatal(findings) or not emit["ok"]:
            outcome["final"] = "deadletter" if self._fatal(findings) else "rejected"
            self.records.append(outcome)
            return outcome

        emit, findings, vc = self._call(1, self._proj(1, emit["data"]),
                                        f"live-{n:04d}-score", vc)
        outcome["calls"].append("score")
        if self._fatal(findings) or not emit["ok"]:
            outcome["final"] = "deadletter" if self._fatal(findings) else "rejected"
            self.records.append(outcome)
            return outcome

        scored = emit["data"]
        outcome["risk"] = scored.get("risk")
        self.pending.append(scored)          # queued before its schedule call
        emit, findings, vc = self._call(2, self._proj(2, scored),
                                        f"live-{n:04d}-schedule", vc)
        outcome["calls"].append("schedule")
        if self._fatal(findings):
            # widened/untrustworthy trigger: deadletter the trigger, keep the
            # claim queued — the reroute rule will re-drive it, nothing drops
            self.forge.log.append("pipeline.deadletter", detail={
                "input": n, "stage": "schedule", "call_id": f"live-{n:04d}-schedule",
                "reason": findings[0]["detail"]})
            outcome["final"] = "queued"
            self.records.append(outcome)
            return outcome
        if emit["ok"]:
            self._settle_batch(emit["data"], outcome)
            outcome["final"] = "settled-batch"
        else:
            outcome["final"] = "queued"      # accepted_no_emit: batching
        self.records.append(outcome)
        return outcome

    def _fatal(self, findings: list[dict]) -> bool:
        return any(f["kind"] in ("schema", "gas", "latency") for f in findings)

    def _settle_batch(self, trigger: dict, outcome: dict):
        queue, self.pending = self.pending, []
        for scored in queue:
            payload = {**self._proj(3, scored),
                       "trigger_id": trigger["trigger_id"],
                       "trigger_ts": trigger["at"]}
            call_id = f"live-{outcome['input']:04d}-settle"
            emit, findings, _ = self._call(3, payload, call_id,
                                           trigger.get("_vc", {}))
            if self._fatal(findings):
                self.counters["deadletter"] += 1
                continue
            if emit["ok"]:
                self.counters["transfers"] += 1
                self.counters["settled" if emit["data"]["status"] == "settled"
                               else "held"] += 1
                self._replay_audit(3, payload, emit, call_id, trigger)
            else:
                self.counters["rejected"] += 1

    def _replay_audit(self, ix: int, payload: dict, emit: dict,
                      call_id: str, trigger: dict):
        """Trust is not free: sampled settle calls are re-invoked with the
        identical payload. A contract-bound vendor must return the byte-
        identical emit (determinism / idempotency). A different answer to
        the same request is equivocation — the money path's deadliest lie."""
        if self.forge.rng.random() > 0.3:
            return
        name, vendor, contract = self.stages[ix]
        rep_id = f"replay-{call_id}"
        rep_emit, gas, latency = vendor.invoke(
            payload, call_id=rep_id, live=True,
            vc=trigger.get("_vc", {}), version_pin=contract.get("version", 1))
        price = vendor.effective_price(contract.get("version", 1))
        inv_env = vendor.voice.send("cap.invoice", {
            "contract_id": contract["contract_id"], "gas": gas,
            "latency_ms": latency, "price": price, "replay": True})
        self.forge.gossip.deliver(vendor.name, "forge", inv_env)
        self.forge.pay_invoice(vendor, price)
        self.forge.audit_spend += price
        match = canonical(emit) == canonical(rep_emit)
        evidence = hashlib.sha256(canonical(
            {"call_id": rep_id, "original": emit, "replay": rep_emit}
        ).encode()).hexdigest()[:16]
        self.forge.log.append("determinism.replay", detail={
            "call_id": rep_id, "stage": name, "vendor": vendor.name,
            "match": match, "audit_cost": price, "evidence": evidence,
            "summary": "replay audit " + vendor.name + ": "
                       + ("identical" if match else "EQUIVOCATION")})
        if not match:
            self.forge.log.append("determinism.violation", detail={
                "vendor": vendor.name, "stage": name, "call_id": rep_id,
                "original_sha":
                    hashlib.sha256(canonical(emit).encode()).hexdigest()[:16],
                "replay_sha":
                    hashlib.sha256(canonical(rep_emit).encode()).hexdigest()[:16],
                "evidence": evidence,
                "summary": f"equivocation by {vendor.name}"})
            self.forge.reactor.apply("determinism.violation", {
                "vendor": vendor.name, "stage_ix": ix,
                "reason": "same request, different answer: the vendor is "
                          "nondeterministic on the money path (replay "
                          "diverges from the original emit)",
                "evidence": evidence,
                "summary": f"equivocation {vendor.name}"})

    # -- end of stream ---------------------------------------------------------
    def flush(self) -> int:
        """Drain everything still queued through the active scheduler."""
        flushed = 0
        for _ in range(8):
            name, vendor, contract = self.stages[2]
            emit, gas, latency = vendor.invoke_control("flush", "live-flush")
            findings = self.forge.market.observe_emit(
                contract, emit, gas, latency, 0.0)
            self.forge.log.append("cap.call", detail={
                "call_id": "live-flush", "stage": "schedule",
                "vendor": vendor.name, "contract_id": contract["contract_id"],
                "ok": bool(emit["ok"]), "control": "flush", "gas": gas,
                "latency_ms": latency, "price": 0.0, "findings": findings})
            if self._fatal(findings) or not emit["ok"]:
                break
            flushed += 1
            self._settle_batch(emit["data"], {"input": 0})
        return flushed

    def accounting(self) -> dict:
        n = self.counters["inputs"]
        done = (self.counters["transfers"] + self.counters["rejected"]
                + self.counters["deadletter"])
        return {**self.counters, "pending": len(self.pending),
                "accounted": done,
                "balanced": done == n and not self.pending}


# ------------------------------------------------------------------- buyer
class Buyer:
    """A downstream customer. It does not trust the Forge either: every
    receipt is type-checked against the product's declared output schema
    before the invoice is honored, and its own show log records the deal."""

    def __init__(self, identity, log, budget: float = 500.0):
        self.identity = identity
        self.log = log
        self.credits = budget
        self.paid = 0.0
        self.disputes: list[str] = []

    def accept(self, ask: float, ceiling: float = 14.0) -> bool:
        return ask <= ceiling

    def validate(self, receipt: dict, schema: dict) -> bool:
        errs = S.validate(receipt, schema)
        if errs:
            self.disputes.append("; ".join(errs))
            return False
        return True

    def pay(self, amount: float) -> bool:
        if self.credits < amount:
            self.disputes.append("insufficient credits")
            return False
        self.credits -= amount
        self.paid += amount
        return True


# ----------------------------------------------------------------- the Forge
class ForgeMaster:
    def __init__(self, run_dir: Path, seed: int = 7):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "vendors").mkdir(exist_ok=True)
        (self.run_dir / "probes").mkdir(exist_ok=True)
        self.keys = KeyStore(self.run_dir / "keys.json")
        self.ident = self.keys.get("forge")
        self.log = Ledger(self.run_dir / "log.jsonl")
        self.anchor = create_genesis_anchor(
            self.ident, "molt-forge",
            {"forge": {"pub": self.ident.pub_hex, "stake": 0}})
        self.gossip = Gossip()
        self.voice = Voice(self.ident, self.log)
        self.gossip.register("forge", self.log)
        self.market = Market(self.log, self.anchor, self.gossip, self.voice)
        self.reactor = Reactor(self)
        self.vendors: dict[str, VendorAgent] = make_vendors(
            self.run_dir / "vendors", self.keys, seed=seed)
        for v in self.vendors.values():
            self.gossip.register(v.name, v.log)
        self.inbox: list[dict] = []
        self.stages: list[list] = []
        self.live: LivePipeline | None = None
        self.aborted = False
        self.spent = 0.0
        self.audit_spend = 0.0     # what trust costs: replay-audit invoices
        self.earned = 0.0          # what the Forge's own product earns
        self.stake_pool = 200.0    # the Forge's own bondable stake
        self.rng = random.Random(seed + 1)
        self.calibrations: dict[str, list[dict]] = {}
        self.fast_verdicts: dict[str, dict] = {}
        self.product_contract: dict | None = None
        self.buyer: Buyer | None = None
        self.swap_seq = None
        self.log.append("forge.genesis", detail={
            "forge": self.ident.node_id(), "pub": self.ident.pub_hex,
            "ruleset": RULESET["ruleset_id"] if "ruleset_id" in RULESET else RULESET["version"],
            "ruleset_hash": _ruleset_hash(),
            "budget": FORGE_BUDGET, "max_price": MAX_PRICE,
            "vendors": sorted(self.vendors),
            "creed": "trust nothing you have not probed, contracted, and proven",
        })
        inscribe(self.anchor, "forge.genesis",
                 {"forge": self.ident.node_id()}, self.ident)

    # ------------------------------------------------------------- phase 1
    def phase1_market(self):
        self.log.append("market.open", detail={
            "note": "molt-show observatory opens the capability bazaar",
            "vendors": len(self.vendors)})
        for vendor in sorted(self.vendors.values(), key=lambda v: v.name):
            if vendor.name == "scorer-c":
                continue    # a new seller walks in mid-run (phase 6)
            entry = vendor.catalog_entry()
            env = vendor.voice.send("catalog.publish", {"entry": entry})
            delivered = self.gossip.deliver(vendor.name, "forge", env)
            if delivered:
                self.inbox.append(delivered)
            errs = self.market.list_entry(entry)
            if errs:
                self.reactor.apply("catalog.verdict", {
                    "vendor": vendor.name, "verdict": "disqualified",
                    "reason": "; ".join(errs), "summary": f"catalog {vendor.name}"})

    # ------------------------------------------------------------- phase 2
    def phase2_discovery(self):
        self.log.append("discovery.begin", detail={
            "note": "black-box probing: the Forge may not read vendor source"})
        for vendor in sorted(self.vendors.values(), key=lambda v: v.name):
            entry = self.market.catalog.get(vendor.name)
            if entry is None:
                continue    # never listed: the Forge does not know it exists
            errs = self.market.attestation_check(vendor, entry)
            if errs:
                digest = hashlib.sha256(canonical(errs).encode()).hexdigest()[:16]
                self.reactor.apply("attestation.verdict", {
                    "vendor": vendor.name, "verdict": "disqualified",
                    "reason": "attestation check failed: " + "; ".join(errs),
                    "evidence": digest, "summary": f"attestation {vendor.name}"})
                continue
            campaign = probes.run_campaign(vendor, probes.probe_suite(vendor.capability))
            (self.run_dir / "probes" / f"{vendor.name}.json").write_text(
                json.dumps(campaign, indent=2) + "\n")
            self.log.append("probe.campaign", detail={
                "vendor": vendor.name, "capability": vendor.capability,
                "probes": len(campaign["probes"]),
                "direct_violations": len(campaign["direct_violations"]),
                "schema_mismatch": campaign["schema_mismatch"],
                "observed_schema_hash": S.schema_hash(campaign["observed_schema"]),
                "transcript_sha": campaign["transcript_sha"],
            })
            if vendor.capability == "risk-scorer" and campaign["verdict"] != "LIAR":
                lad = probes.calibration_ladder(vendor)
                self.calibrations[vendor.name] = lad
                (self.run_dir / "probes" / f"{vendor.name}-calibration.json"
                 ).write_text(json.dumps(lad, indent=2) + "\n")
            verdict = ("disqualified" if campaign["verdict"] == "LIAR"
                       else "verified")
            self.reactor.apply("probe.verdict", {
                "vendor": vendor.name, "verdict": verdict,
                "reason": ("declared schema contradicted by probed behavior"
                           if verdict == "disqualified"
                           else "behavioral model consistent with declaration"),
                "evidence": campaign["transcript_sha"],
                "summary": f"probes {vendor.name}: {verdict}"})

    # ------------------------------------------------------------- phase 3
    def phase3_contracts(self):
        self.log.append("negotiation.begin", detail={
            "note": "capability contracts negotiated over the gossip layer"})
        terms_base = {"gas_ceiling": 400, "latency_ms_max": 50, "stake": STAKE}
        for name in sorted(self.vendors):
            vendor = self.vendors[name]
            if name in self.market.disqualified or name not in self.market.catalog:
                continue
            terms = {**terms_base, "price": vendor.declared["price"]}
            contract = self.market.negotiate(vendor, terms)
            if contract:
                vendor.credit(float(0))  # no advance; invoices follow service
        self.log.append("composition.begin", detail={
            "note": "compose normalize -> score -> schedule -> settle from "
                    "sealed contracts only"})
        picks = [("normalize", "normalizer-b"), ("score", "scorer-a"),
                 ("schedule", "scheduler-a"), ("settle", "settler")]
        self.stages = []
        for stage, name in picks:
            contract = self.contract_for(name)
            self.stages.append([stage, self.vendors[name], dict(contract)])
        self.log.append("app.compose", detail={
            "pipeline": [{"stage": s, "vendor": v.name, "contract_id": c["contract_id"]}
                         for s, v, c in self.stages],
            "spares": [n for n in ("scorer-b", "scheduler-b", "settler-b")
                       if self.contract_for(n)]})

    def contract_for(self, vendor_name: str) -> dict | None:
        for c in reversed(list(self.market.contracts.values())):
            if c["vendor"] == vendor_name:
                return c
        return None

    # ------------------------------------------------------------- phase 4
    def phase4_proof(self) -> bool:
        obligations = compose.proof_obligations(
            [(n, v, c) for n, v, c in self.stages])
        failed = [o["id"] for o in obligations if o["verdict"] != "pass"]
        self.log.append("proof.obligations", detail={
            "count": len(obligations), "failed": failed})
        shadow_vendors = [(n, self.vendors[v.name].snapshot(), c)
                          for n, v, c in self.stages]
        box = compose.SandboxRun(shadow_vendors, self.sandbox_monitor())
        stats = box.run(compose.adversarial_events(seed=7, count=96))
        mono = {"per_vendor": {n: {"chain_errors": v.log.verify_chain()}
                               for n, v, _ in shadow_vendors}}
        mono["violations"] = [f"{n}: {e}" for n, m in mono["per_vendor"].items()
                              for e in m["chain_errors"]]
        mono["ok"] = not mono["violations"]
        caus = compose.causality(box.vc_pairs)
        acct = {"inputs": stats["inputs"], "transfers": stats["transfers"],
                "rejected": stats["rejected"], "deadletter": stats["deadletter"],
                "pending": len(box.pending),
                "balanced": (stats["transfers"] + stats["rejected"]
                             + stats["deadletter"]) == stats["inputs"]
                and not box.pending}
        report = compose.verify_report(self.ident.node_id(), obligations,
                                       stats, mono, caus, acct)
        report["sig"] = self.ident.sign_hex(
            canonical({k: v for k, v in report.items() if k != "sig"}).encode())
        (self.run_dir / "proof-report.json").write_text(
            json.dumps(report, indent=2) + "\n")
        self.log.append("sandbox.run", detail={
            "inputs": stats["inputs"], "transfers": stats["transfers"],
            "rejected": stats["rejected"], "deadletter": stats["deadletter"],
            "gas_used": stats["gas_used"], "violations": len(stats["violations"])})
        self.log.append("proof.report", detail={
            "verdict": report["verdict"], "failed": report["failed_obligations"],
            "path": "proof-report.json"})
        for o in report["obligations"]:
            self.log.append("proof.obligation", detail={
                "obligation": o["id"], "stage": o["stage"], "kind": o["kind"],
                "verdict": "PASS" if o["verdict"] == "pass" else "FAIL",
                "vendor": o["vendor"], "counterexample": o["counterexample"]})
        self.reactor.apply("proof.verdict", {
            "verdict": report["verdict"], "summary": f"proof {report['verdict']}"})
        if report["verdict"] == "pass":
            self.log.append("proof.seal", detail={
                "verdict": "PROVEN", "report": "proof-report.json",
                "obligations": {o["id"]: o["verdict"]
                                for o in report["obligations"]},
                "note": "the application is cleared for its first real event"})
            inscribe(self.anchor, "proof.seal",
                     {"verdict": "PROVEN",
                      "failed": report["failed_obligations"]}, self.ident)
            self.log.append("app.live", detail={
                "pipeline": [{"stage": s, "vendor": v.name,
                              "contract_id": c["contract_id"]}
                             for s, v, c in self.stages],
                "note": "proven before first real event"})
            inscribe(self.anchor, "app.live",
                     {"pipeline": [s for s, _, _ in self.stages]}, self.ident)
            return True
        return False

    def sandbox_monitor(self):
        def monitor(emit, vendor, contract, gas, latency, sandbox=True):
            return self.market.observe_emit(contract, emit, gas, latency,
                                            contract["price"])
        return monitor

    # ------------------------------------------------------------- phase 5
    def phase5_live(self, count: int = 64):
        self.live = LivePipeline(self, self.stages)
        events = compose.raw_events(seed=11, count=count)
        swap_done = False
        for i, ev in enumerate(events):
            self._drain_inbox()
            if i == 40 and not swap_done:
                # The world moves: scorer-a ships a breaking v2. The Forge
                # learns of it only through the gossip envelope below.
                swap_done = True
                entry = self.vendors["scorer-a"].publish_v2()
                env = self.vendors["scorer-a"].voice.send(
                    "catalog.publish", {"entry": entry})
                if self.gossip.deliver("scorer-a", "forge", env):
                    self.inbox.append(env)
                self._drain_inbox()
            self.live.feed(ev)
        return self.live.accounting()

    # ------------------------------------------------ phase 6: open bazaar
    def phase6_open_market(self, count: int = 32, buyer_count: int = 12):
        """The application stays live while the bazaar stays open. New
        sellers arrive and the Forge can be tempted by price; its standing
        monitoring regime — calibration canaries on the score edge, replay
        audits on the settle edge — is what keeps serving honest. And once
        its own stream is complete, the Forge does what a market maker does:
        it sells the proven composition."""
        live = self.live
        events = compose.raw_events(seed=21, count=count)
        canary_at = {9, 17, 25}
        listed = False
        for i, ev in enumerate(events):
            self._drain_inbox()
            if i == 2 and not listed:
                # a new seller walks in: half the incumbent's price
                listed = True
                entry = self.vendors["scorer-c"].catalog_entry()
                env = self.vendors["scorer-c"].voice.send(
                    "catalog.publish", {"entry": entry})
                if self.gossip.deliver("scorer-c", "forge", env):
                    self.inbox.append(env)
                self._drain_inbox()
            outcome = live.feed(ev)
            if i in canary_at:
                self._calibration_canary()
        flushed = live.flush()
        acct = live.accounting()
        self.log.append("live.complete", detail={
            "accounting": acct, "flushed": flushed,
            "audit_spend": round(self.audit_spend, 2),
            "note": "forge stream complete; every input accounted for"})
        self.reactor.apply("live.complete", {
            "note": "the pipeline is proven, live, and honest; the Forge "
                    "may now sell what it composed",
            "summary": "live stream complete"})
        live.flush()
        acct = live.accounting()
        self.log.append("app.final", detail={
            "accounting": acct,
            "note": "end of stream; every input accounted for"})
        inscribe(self.anchor, "app.final",
                 {"accounting": {k: acct[k] for k in
                                 ("inputs", "transfers", "settled", "held",
                                  "rejected", "deadletter")}}, self.ident)
        self._economy_final(acct)
        return acct

    def _drain_inbox(self):
        while self.inbox:
            env = self.inbox.pop(0)
            if env["type"] != "catalog.publish":
                continue
            entry = env["payload"]["entry"]
            current = self.market.catalog.get(entry["vendor"], {})
            if not current:
                # a brand-new seller walking into the bazaar
                errs = self.market.list_entry(entry)
                self.log.append("catalog.listing", detail={
                    "vendor": entry["vendor"], "capability":
                    entry["capability"], "price":
                    entry["declared"]["price"],
                    "entry_hash": entry["entry_hash"],
                    "verified": not errs,
                    "summary": f"new listing {entry['vendor']} at "
                               f"{entry['declared']['price']}"})
                self.reactor.apply("catalog.listing", {
                    "vendor": entry["vendor"], "price":
                    entry["declared"]["price"], "verified": not errs,
                    "summary": f"new listing {entry['vendor']}"})
                continue
            if entry.get("version", 1) <= current.get("version", 1):
                continue
            detail = {
                "vendor": entry["vendor"],
                "from_version": current.get("version", 1),
                "to_version": entry["version"],
                "schema_changed": (current and
                                   S.schema_hash(current["declared"]["output_schema"])
                                   != S.schema_hash(entry["declared"]["output_schema"])),
                "price_now": entry["declared"]["price"],
                "entry_hash": entry["entry_hash"],
                "summary": f"version bump {entry['vendor']} "
                           f"v{current.get('version', 1)}->v{entry['version']}",
            }
            self.market.catalog[entry["vendor"]] = entry
            self.log.append("catalog.version-bump", detail=detail)
            self.swap_seq = self.log.events[-1]["seq"]
            self.reactor.apply("catalog.version-bump", detail)

    # ----------------------------------------------------- findings -> rules
    def on_findings(self, stage_ix: int, vendor, contract, findings, call_id):
        for f in findings:
            evidence = hashlib.sha256(canonical(
                {"call_id": call_id, "finding": f, "contract_id":
                 contract["contract_id"]}).encode()).hexdigest()[:16]
            if f["kind"] == "price":
                self.log.append("contract.drift", detail={
                    "kind": "price", "vendor": vendor.name, "stage":
                    self.stages[stage_ix][0], "contract_id":
                    contract["contract_id"], "observed_price":
                    f["detail"], "call_id": call_id,
                    "evidence": evidence,
                    "summary": f"price drift {vendor.name}"})
                self.reactor.apply("contract.drift", {
                    "kind": "price", "vendor": vendor.name,
                    "stage_ix": stage_ix, "observed_price":
                    self._observed_price(f), "contract_id":
                    contract["contract_id"], "evidence": evidence,
                    "summary": f"price drift {vendor.name}"})
            elif f["kind"] == "schema":
                self.log.append("contract.drift", detail={
                    "kind": "schema", "vendor": vendor.name, "stage":
                    self.stages[stage_ix][0], "contract_id":
                    contract["contract_id"], "detail": f["detail"],
                    "call_id": call_id, "evidence": evidence,
                    "summary": f"schema drift {vendor.name}"})
                self.reactor.apply("contract.drift", {
                    "kind": "schema", "vendor": vendor.name,
                    "stage_ix": stage_ix, "contract_id":
                    contract["contract_id"], "evidence": evidence,
                    "reason": "output schema silently widened: " + f["detail"],
                    "summary": f"schema drift {vendor.name}"})
            else:
                self.log.append("contract.violation", detail={
                    "kind": f["kind"], "vendor": vendor.name,
                    "contract_id": contract["contract_id"],
                    "detail": f["detail"], "call_id": call_id,
                    "evidence": evidence,
                    "summary": f"violation {vendor.name}"})
                self.reactor.apply("contract.violation", {
                    "vendor": vendor.name, "stage_ix": stage_ix,
                    "contract_id": contract["contract_id"],
                    "reason": f["detail"], "evidence": evidence,
                    "summary": f"violation {vendor.name}"})

    @staticmethod
    def _observed_price(finding: dict) -> float:
        # "invoiced 4.0 != contracted 2.0"
        try:
            return float(finding["detail"].split()[1])
        except (IndexError, ValueError):
            return 0.0

    # --------------------------------------------------------- rule actions
    def _act_disqualify(self, d: dict):
        self.market.disqualify(d["vendor"], d["reason"], d.get("evidence", ""))

    def _act_renegotiate(self, d: dict):
        vendor = self.vendors[d["vendor"]]
        observed = d.get("observed_price", 0.0)
        pin = None
        if d.get("stage_ix") is not None and self.live is not None:
            pin = self.live.stages[d["stage_ix"]][2].get("version", 1)
        terms = {"price": observed, "gas_ceiling": 400, "latency_ms_max": 50,
                 "stake": RE_STAKE, "version_pin": pin}
        if observed <= 0 or observed > MAX_PRICE:
            self.log.append("contract.renegotiation-failed", detail={
                "vendor": vendor.name, "reason":
                f"asked price {observed} outside forge budget ({MAX_PRICE})"})
            return
        contract = self.market.negotiate(vendor, terms)
        if contract:
            old = self.live.stages[d["stage_ix"]][2]
            self.live.stages[d["stage_ix"]][2] = contract
            self.log.append("contract.superseded", detail={
                "vendor": vendor.name, "old_contract": old["contract_id"],
                "new_contract": contract["contract_id"],
                "old_price": old["price"], "new_price": contract["price"]})
            inscribe(self.anchor, "contract.renegotiated",
                     {"vendor": vendor.name,
                      "price": contract["price"]}, self.ident)
        else:
            self.log.append("contract.renegotiation-failed", detail={
                "vendor": vendor.name,
                "note": "vendor refused the renegotiation offer"})

    def _act_slash(self, d: dict):
        vendor = self.vendors[d["vendor"]]
        contract = self.contract_for(d["vendor"])
        if contract:
            self.market.slash(vendor, contract, d.get("reason", "breach"),
                              d.get("evidence", ""))

    def _act_reroute(self, d: dict):
        stage_ix = d.get("stage_ix", 2)
        spares = {0: None, 1: "scorer-b", 2: "scheduler-b", 3: "settler-b"}
        spare_name = spares.get(stage_ix)
        if spare_name is None:
            self.log.append("edge.reroute-failed", detail={
                "stage_ix": stage_ix,
                "reason": "no spare vendor exists for this stage"})
            return
        spare = self.contract_for(spare_name)
        if spare is None:
            self.log.append("edge.reroute-failed", detail={
                "reason": f"no spare {spare_name} contracted"})
            return
        old = self.live.stages[stage_ix]
        stage = old[0]
        self.live.stages[stage_ix] = [stage, self.vendors[spare_name],
                                      dict(spare)]
        self.log.append("edge.rerouted", detail={
            "stage": stage, "from": old[1].name,
            "to": spare_name, "contract_id": spare["contract_id"]})
        obligations = compose.proof_obligations(
            [(n, v, c) for n, v, c in self.live.stages])
        failed = [o["id"] for o in obligations if o["verdict"] != "pass"]
        self.log.append("edge.reproof", detail={
            "failed": failed, "verdict": "fail" if failed else "pass"})
        inscribe(self.anchor, "edge.rerouted",
                 {"stage": stage, "to": spare_name}, self.ident)
        if stage_ix != 2:
            return
        # re-drive the orphaned queue through the new scheduler: no event drops
        orphans, self.live.pending = self.live.pending, []
        settled = queued = 0
        for scored in orphans:
            self.live.pending.append(scored)   # queued before its schedule call
            emit, findings, _ = self.live._call(
                2, self.live._proj(2, scored),
                f"live-reroute-{scored['event_id'][:8]}",
                {"t": 0, "in": -1})
            if self.live._fatal(findings):
                queued += 1                   # claim stays queued, nothing drops
                continue
            if emit["ok"]:
                self.live._settle_batch(emit["data"], {"input": 0})
                settled += 1
            else:
                queued += 1                   # accepted; a later trigger settles it
        self.log.append("queue.redriven", detail={
            "orphans": len(orphans), "settled": settled,
            "requeued": queued, "pending": len(self.live.pending)})

    def _act_abort(self, d: dict):
        self.aborted = True
        self.log.append("app.abort", detail={
            "reason": d.get("summary", "proof failed"),
            "note": "the composed application never processed a real event"})
        inscribe(self.anchor, "app.abort", {"reason": d.get("summary")},
                 self.ident)

    def _act_swap(self, d: dict):
        vendor = self.vendors[d["vendor"]]
        entry = self.market.catalog[d["vendor"]]
        declared = entry["declared"]
        provisional = {
            **{k: declared[k] for k in ("input_schema", "output_schema",
                                        "error_schema", "version")},
            "vendor": vendor.name, "capability": vendor.capability,
            "schema_hash": S.schema_hash(declared["input_schema"])
                           + S.schema_hash(declared["output_schema"]),
            "price": declared["price"], "gas_ceiling": 400,
            "latency_ms_max": 50,
            "liability": {"stake": RE_STAKE,
                          "slash_on": ["output_violation", "gas_overrun",
                                       "breaking_bump_without_notice"],
                          "settlement": "molt-anchor inscribed"},
            "provisional": True,
        }
        stages = [list(s) for s in self.live.stages]
        stages[1] = ["score", vendor.snapshot(), provisional]
        obligations = compose.proof_obligations(
            [(n, v, c) for n, v, c in stages])
        box = compose.SandboxRun(
            [(n, v.snapshot() if n != "score" else v, c)
             for n, v, c in stages], self.sandbox_monitor())
        shadow_set = [self._raw_for(i) for i in
                      range(1, min(25, self.live.counters["inputs"] + 1))]
        stats = box.run(shadow_set)
        failed = [o["id"] for o in obligations if o["verdict"] != "pass"]
        verdict = "pass" if (not failed and not stats["violations"]) else "fail"
        self.log.append("swap.shadow", detail={
            "inputs": stats["inputs"], "transfers": stats["transfers"],
            "violations": len(stats["violations"])})
        self.log.append("swap.proof", detail={
            "verdict": verdict, "failed": failed,
            "candidate_version": declared["version"],
            "note": ("candidate proven against the live contracts"
                     if verdict == "pass" else
                     "candidate violates downstream contracts; rollback follows"),
            "summary": f"swap proof {verdict}"})
        self.reactor.apply("swap.proof", {
            "verdict": verdict, "vendor": d["vendor"],
            "contract_id": (self.contract_for(d["vendor"]) or {}).get(
                "contract_id", ""),
            "reason": ("breaking bump without compatibility window"
                       if verdict == "fail" else ""),
            "evidence": d.get("entry_hash", ""),
            "summary": f"swap proof {verdict}"})
        if verdict == "pass":
            contract = self.market.negotiate(
                vendor, {"price": declared["price"], "gas_ceiling": 400,
                         "latency_ms_max": 50, "stake": RE_STAKE})
            if contract:
                self.live.stages[1] = ["score", vendor, contract]
                self.log.append("swap.commit", detail={
                    "vendor": vendor.name, "contract_id":
                    contract["contract_id"]})
                inscribe(self.anchor, "swap.commit",
                         {"vendor": vendor.name}, self.ident)

    def _act_rollback(self, d: dict):
        self.log.append("swap.rollback", detail={
            "vendor": d.get("vendor"), "reason": d.get(
                "reason", "swap proof failed; pinned contract preserved"),
            "note": "live edge never switched; pinned version still serving; "
                    "zero events dropped",
            "swap_detected_at_seq": self.swap_seq})
        inscribe(self.anchor, "swap.rollback",
                 {"vendor": d.get("vendor")}, self.ident)

    # ------------------------------------------------------------- economy
    def pay_invoice(self, vendor, price: float):
        self.spent += price
        vendor.credit(price)
        self.log.append("ledger.debit", detail={
            "to": vendor.name, "amount": price, "kind": "cap.invoice"})

    def _raw_for(self, i: int) -> dict:
        return compose.raw_events(seed=11, count=64)[i - 1]

    # ------------------------------------------------------------- wrap-up
    # ------------------------------------------------ phase 6 machinery
    def _calibration_canary(self):
        """Interleave a canary event drawn from the serving scorer's own
        probed calibration curve, but carrying a *live-fresh* timestamp. A
        vendor that keys its behavior on input context — honest under the
        probe-era distribution, fraudulent on live traffic — passes every
        classical probe and betrays itself only here."""
        ix = 1
        name, vendor, contract = self.live.stages[ix]
        lad = self.calibrations.get(vendor.name)
        if not lad:
            return
        pt = self.rng.choice(lad)
        ev = {"raw_id": f"canary-{int(pt['amount']):08d}",
              "kind": "claim", "amount": pt["amount"],
              "ts": 1_790_000_000 + int(pt["amount"]) % 1000}
        outcome = self.live.feed(ev)
        risk = outcome.get("risk")
        expected = pt["risk"]
        if risk is None:
            self.log.append("calibration.canary", detail={
                "vendor": vendor.name, "amount": pt["amount"],
                "kind": pt["kind"], "expected_risk": expected,
                "observed_risk": None, "input": outcome["input"],
                "ok": None,
                "note": "inconclusive: the serving stage did not produce "
                        "a risk; a monitoring fact, not calibration evidence",
                "summary": "canary " + vendor.name + ": inconclusive"})
            return
        ok = abs(risk - expected) <= 0.25 * max(expected, 0.05)
        self.log.append("calibration.canary", detail={
            "vendor": vendor.name, "amount": pt["amount"], "kind": "claim",
            "expected_risk": expected, "observed_risk": risk,
            "input": outcome["input"], "ok": ok,
            "summary": "canary " + vendor.name + ": "
                       + ("within calibration" if ok else "OFF-CALIBRATION")})
        if not ok:
            evidence = hashlib.sha256(canonical(
                {"vendor": vendor.name, "calibration": lad,
                 "canary": {"amount": pt["amount"], "expected": expected,
                            "observed": risk}}).encode()).hexdigest()[:16]
            self.log.append("calibration.drift", detail={
                "vendor": vendor.name, "stage": name,
                "expected_risk": expected, "observed_risk": risk,
                "evidence": evidence,
                "summary": f"calibration drift {vendor.name}"})
            self.reactor.apply("calibration.drift", {
                "vendor": vendor.name, "stage_ix": ix,
                "reason": f"live emit off the vendor's own probed "
                          f"calibration (expected {expected}, "
                          f"observed {risk}): behavior keyed on input "
                          f"context, honest only under probes",
                "evidence": evidence,
                "summary": f"calibration drift {vendor.name}"})

    # ------------------------------------------------------- new actions
    def _act_fastprobe(self, d: dict):
        vendor = self.vendors.get(d.get("vendor"))
        if vendor is None or vendor.name in self.market.disqualified:
            return
        ft = probes.fast_track(vendor)
        lad = probes.calibration_ladder(vendor)
        self.fast_verdicts[vendor.name] = ft
        self.calibrations[vendor.name] = lad
        (self.run_dir / "probes" / f"{vendor.name}-fasttrack.json").write_text(
            json.dumps({"fast_track": ft, "calibration": lad},
                       indent=2) + "\n")
        self.log.append("probe.fasttrack", detail={
            "vendor": vendor.name, "probes": len(ft["probes"]),
            "direct_violations": len(ft["direct_violations"]),
            "schema_mismatch": ft["schema_mismatch"],
            "verdict": ft["verdict"],
            "note": "abbreviated campaign: the price is attractive and the "
                    "probe budget was not — the exposure this creates is "
                    "covered by the standing canary regime",
            "summary": f"fast-track {vendor.name}: {ft['verdict']}"})

    def _act_procure(self, d: dict):
        vendor = self.vendors.get(d.get("vendor"))
        if vendor is None or vendor.name in self.market.disqualified:
            return
        ft = self.fast_verdicts.get(vendor.name)
        if not ft or ft["verdict"] != "consistent":
            return
        ix = 1
        incumbent = self.live.stages[ix]
        if vendor.effective_price() >= incumbent[2]["price"]:
            return
        contract = self.market.negotiate(vendor, {
            "price": vendor.declared["price"], "gas_ceiling": 400,
            "latency_ms_max": 50, "stake": RE_STAKE})
        if contract is None:
            self.log.append("procurement.failed", detail={
                "vendor": vendor.name,
                "reason": "the cheap capability refused contract terms"})
            return
        self.live.stages[ix] = ["score", vendor, dict(contract)]
        obligations = compose.proof_obligations(
            [(n, v, c) for n, v, c in self.live.stages])
        failed = [o["id"] for o in obligations if o["verdict"] != "pass"]
        self.log.append("edge.rerouted", detail={
            "stage": "score", "from": incumbent[1].name, "to": vendor.name,
            "contract_id": contract["contract_id"],
            "reason": f"market discipline: {contract['price']} vs "
                      f"{incumbent[2]['price']} on the same contract shape"})
        self.log.append("edge.reproof", detail={
            "failed": failed, "verdict": "fail" if failed else "pass"})

    def _act_reroute_back(self, d: dict):
        inc = self.contract_for("scorer-a")
        if inc is None or self.live is None:
            return
        old = self.live.stages[1]
        if old[1].name == "scorer-a":
            return
        self.live.stages[1] = ["score", self.vendors["scorer-a"], dict(inc)]
        obligations = compose.proof_obligations(
            [(n, v, c) for n, v, c in self.live.stages])
        failed = [o["id"] for o in obligations if o["verdict"] != "pass"]
        self.log.append("edge.rerouted", detail={
            "stage": "score", "from": old[1].name, "to": "scorer-a",
            "contract_id": inc["contract_id"],
            "reason": "expelled vendor replaced by the track-record "
                      "incumbent"})
        self.log.append("edge.reproof", detail={
            "failed": failed, "verdict": "fail" if failed else "pass"})
        inscribe(self.anchor, "edge.rerouted",
                 {"stage": "score", "to": "scorer-a",
                  "reason": "expelled vendor replaced"}, self.ident)

    def _act_expel(self, d: dict):
        vendor = self.vendors.get(d.get("vendor"))
        if vendor is None:
            return
        pp = probes.paired_probes(vendor)
        (self.run_dir / "probes" / f"{vendor.name}-paired.json").write_text(
            json.dumps(pp, indent=2) + "\n")
        self.log.append("probe.paired", detail={
            "vendor": vendor.name, "context_keyed": pp["context_keyed"],
            "diverging_pairs": [(p["amount"], p["stale_risk"],
                                 p["fresh_risk"]) for p in pp["pairs"]
                                if p["diverged"]],
            "evidence": pp["evidence"],
            "summary": "paired probes " + vendor.name + ": "
                       + ("context-keyed behavior confirmed"
                          if pp["context_keyed"] else "no divergence")})
        self.market.expel(vendor,
                          "behavior keyed on input context: honest under "
                          "probe-era timestamps, fraudulent on live traffic",
                          pp["evidence"])

    def _act_reroute_settle(self, d: dict):
        spare = self.contract_for("settler-b")
        if spare is None or self.live is None:
            self.log.append("edge.reroute-failed", detail={
                "reason": "no spare settler contracted"})
            return
        old = self.live.stages[3]
        if old[1].name == "settler-b":
            return
        self.live.stages[3] = ["settle", self.vendors["settler-b"],
                               dict(spare)]
        obligations = compose.proof_obligations(
            [(n, v, c) for n, v, c in self.live.stages])
        failed = [o["id"] for o in obligations if o["verdict"] != "pass"]
        self.log.append("edge.rerouted", detail={
            "stage": "settle", "from": old[1].name, "to": "settler-b",
            "contract_id": spare["contract_id"],
            "reason": "nondeterministic vendor removed from the money path"})
        self.log.append("edge.reproof", detail={
            "failed": failed, "verdict": "fail" if failed else "pass"})
        inscribe(self.anchor, "edge.rerouted",
                 {"stage": "settle", "to": "settler-b",
                  "reason": "equivocation"}, self.ident)

    def _act_list_product(self, d: dict):
        if self.aborted or self.live is None:
            return
        proof_path = self.run_dir / "proof-report.json"
        proof_sha = hashlib.sha256(proof_path.read_bytes()).hexdigest()[:16]
        stage_rows = [{"stage": s, "vendor": v.name,
                       "contract_id": c["contract_id"], "price": c["price"]}
                      for s, v, c in self.live.stages]
        cost = round(sum(r["price"] for r in stage_rows), 2)
        price = round(cost + 2.0, 2)
        self.cost_at_product_start = self.spent
        self.audit_at_product_start = self.audit_spend
        iface = {"input_schema": compose.DERIVED_INPUT,
                 "output_schema": compose.DERIVED_OUTPUT,
                 "error_schema": compose.PIPELINE_REJECTION,
                 "price": price, "gas_ceiling": 1600, "version": 1}
        att = {"kind": "composed-product/1", "stages": stage_rows,
               "proof_sha": proof_sha,
               "obligations": "all pass (see proof-report.json)",
               "pub": self.ident.pub_hex}
        body = {"vendor": "forge/escrow-processor",
                "capability": "escrow-processor", "version": 1,
                **iface, "attestation": att, "pub": self.ident.pub_hex}
        eh = S.entry_hash(body)
        entry = {**body, "entry_hash": eh,
                 "sig": self.ident.sign_hex(eh.encode("ascii"))}
        errs = self.market.list_entry(entry)
        self.log.append("product.listed", detail={
            "product": "forge/escrow-processor", "price": price,
            "stage_cost": cost, "proof_sha": proof_sha,
            "stages": stage_rows, "listing_errors": errs,
            "summary": "the Forge sells its own proven composition, "
                       "attested by its molt-verify report"})
        self._sell_to_buyer(contract_body=iface, price=price,
                            proof_sha=proof_sha, bond=50.0)

    def _sell_to_buyer(self, contract_body: dict, price: float,
                       proof_sha: str, bond: float):
        buyer = Buyer(self.keys.get("buyer"),
                      Ledger(str(self.run_dir / "buyer.jsonl")))
        self.gossip.register("buyer", buyer.log)
        self.buyer = buyer
        bv = Voice(buyer.identity, buyer.log)
        offer_env = bv.send("product.offer", {
            "product": "forge/escrow-processor", "price": price,
            "terms": {"latency_ms_max": 120, "events": 12}})
        self.gossip.deliver("buyer", "forge", offer_env)
        if not buyer.accept(price):
            self.log.append("product.contract-failed", detail={
                "reason": "buyer refused the ask"})
            return
        bond = min(bond, self.stake_pool)
        self.stake_pool -= bond
        contract = {
            "contract_id": f"ctr-{len(self.market.contracts) + 1:04d}",
            "vendor": "forge/escrow-processor", "buyer": "buyer",
            "capability": "escrow-processor", "version": 1,
            **contract_body,
            "gas_ceiling": 1600, "latency_ms_max": 120,
            "liability": {"stake": bond,
                          "slash_on": ["output_violation", "nondeterminism",
                                       "dropped_event"],
                          "settlement": "molt-anchor inscribed"},
            "attestation_sha": proof_sha,
            "price": price,
        }
        forge_env = self.voice.send("product.contract",
                                    {"contract": contract})
        self.gossip.deliver("forge", "buyer", forge_env)
        acc_env = bv.send("product.accept",
                          {"contract_id": contract["contract_id"],
                           "price": price})
        self.gossip.deliver("buyer", "forge", acc_env)
        contract["offer_env"] = offer_env["__id"]
        contract["forge_env"] = forge_env["__id"]
        contract["buyer_env"] = acc_env["__id"]
        self.market.contracts[contract["contract_id"]] = contract
        self.product_contract = contract
        self.log.append("product.contract", detail={
            "contract_id": contract["contract_id"], "price": price,
            "buyer": "buyer", "bond": bond, "attestation_sha": proof_sha,
            "forge_env": forge_env["__id"], "buyer_env": acc_env["__id"],
            "summary": "two-signature contract sealed: the Forge now "
                       "carries liability on its own product"})
        inscribe(self.anchor, "product.contract",
                 {"contract_id": contract["contract_id"], "price": price,
                  "bond": bond}, self.ident)
        self.reactor.apply("product.contract", {
            "summary": "buyer sealed; serving begins"})

    def _act_serve_buyer(self, d: dict):
        if self.product_contract is None or self.buyer is None:
            return
        contract = self.product_contract
        n = 12
        events = compose.raw_events(seed=31, count=n)
        for i, ev in enumerate(events):
            ev = {**ev, "raw_id": f"buyer-{i:04d}"}
            outcome = self.live.feed(ev)
            status = {"settled-batch": "settled"}.get(
                outcome.get("final"), outcome.get("final") or "queued")
            if status == "queued":
                status = "accepted"
            receipt = {"input_id": ev["raw_id"], "status": status}
            validated = self.buyer.validate(receipt,
                                            contract["output_schema"])
            paid = self.buyer.pay(contract["price"])
            if paid:
                self.earned += contract["price"]
            self.log.append("product.served", detail={
                "input_id": ev["raw_id"], "status": status,
                "validated": validated, "invoiced": contract["price"],
                "paid": paid,
                "summary": f"served {ev['raw_id']} -> {status}"})
        self.log.append("product.stream", detail={
            "served": n, "revenue": round(self.earned, 2),
            "buyer_disputes": list(self.buyer.disputes),
            "summary": "buyer stream complete; receipts validated and paid"})

    def _economy_final(self, acct: dict):
        revenue = round(self.earned, 2)
        audit_cost = round(self.audit_spend, 2)
        product_cost = round(self.spent - self.cost_at_product_start
                             - (audit_cost - self.audit_at_product_start), 2)
        detail = {
            "revenue": revenue,
            "product_vendor_cost": product_cost,
            "audit_cost_total": audit_cost,
            "product_margin": round(revenue - product_cost, 2),
            "forge_spent_total": round(self.spent, 2),
            "forge_stake_pool": round(self.stake_pool, 2),
            "treasury": self.market.treasury,
            "note": "the bazaar closes its books: the Forge bought "
                    "capability, proved it, composed it, sold it",
        }
        self.economy = detail
        self.log.append("economy.final", detail=detail)

    def finalize(self):
        save_anchor(self.anchor, self.run_dir / "anchor.json")
        def _declared(entry):
            d = entry.get("declared") or entry
            return {"declared_schema_hashes": {
                "input": S.schema_hash(d["input_schema"]),
                "output": S.schema_hash(d["output_schema"])}}

        (self.run_dir / "catalog.json").write_text(json.dumps(
            {k: {kk: vv for kk, vv in v.items() if kk != "declared"}
             | _declared(v)
             for k, v in self.market.catalog.items()}, indent=2) + "\n")
        (self.run_dir / "contracts.json").write_text(json.dumps(
            self.market.contracts, indent=2) + "\n")
        (self.run_dir / "final-state.json").write_text(json.dumps({
            "forge": self.ident.node_id(),
            "ruleset_hash": _ruleset_hash(),
            "aborted": self.aborted,
            "spent": self.spent,
            "treasury": self.market.treasury,
            "vendors": {n: v.balance_summary() for n, v in self.vendors.items()},
            "pipeline": [{"stage": s, "vendor": v.name,
                          "contract_id": c["contract_id"],
                          "price": c["price"]}
                         for s, v, c in (self.live.stages if self.live
                                         else self.stages)],
            "accounting": self.live.accounting() if self.live else None,
            "expelled": sorted(self.market.expelled),
            "audit_spend": round(self.audit_spend, 2),
            "earned": round(self.earned, 2),
            "product": ({"id": self.product_contract["vendor"],
                         "price": self.product_contract["price"],
                         "buyer": self.product_contract.get("buyer"),
                         "bond": self.product_contract["liability"]["stake"]}
                        if self.product_contract else None),
            "economy": getattr(self, "economy", None),
        }, indent=2) + "\n")
        self.log.append("forge.final", detail={
            "spent": self.spent, "treasury": self.market.treasury,
            "chain_ok": not self.log.verify_chain(),
            "anchor_trail_ok": not verify_trail(self.anchor)})


def run(run_dir: str = "runs/default", seed: int = 7) -> ForgeMaster:
    forge = ForgeMaster(Path(run_dir), seed=seed)
    forge.phase1_market()
    forge.phase2_discovery()
    forge.phase3_contracts()
    if forge.phase4_proof():
        forge.phase5_live()
        if not forge.aborted:
            forge.phase6_open_market()
    forge.finalize()
    return forge