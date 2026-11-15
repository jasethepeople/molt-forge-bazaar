"""molt-audit for the Forge — machine-checks the four ledger claims:

1. the liar was detected by behavioral evidence before it ever touched
   the pipeline (disqualification precedes every contract and every
   compose, and carries replayable evidence);
2. every contract was honored or slashed (no unremediated violation /
   drift finding references a contract still in force);
3. the composition was proven safe before first execution
   (proof.seal precedes app.live; every obligation passed);
4. the live hot-swap preserved event integrity end-to-end
   (swap proof precedes swap; rollback or promote follows; accounting
   balances and zero violations in the promoted epoch).

Plus: hash-chain integrity of the forge log and of the anchor trail."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from moltmesh.core import Ledger, canonical
from moltmesh.anchor import load as load_anchor, verify_trail


def _seq_of(events: list[dict], pred) -> int | None:
    for ev in events:
        if pred(ev):
            return ev["seq"]
    return None


def audit(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    log = Ledger(str(run_dir / "log.jsonl"))
    anchor = load_anchor(str(run_dir / "anchor.json"))
    final_state = json.loads((run_dir / "final-state.json").read_text())
    events = log.events
    findings: list[str] = []

    # -- integrity -----------------------------------------------------------
    chain_errs = log.verify_chain()
    if chain_errs:
        findings.append(f"log chain errors: {chain_errs[:3]}")
    trail_errs = verify_trail(anchor)
    if trail_errs:
        findings.append(f"anchor trail errors: {trail_errs[:3]}")

    # -- claim 1: the liar never touched the pipeline ------------------------
    dq = [ev for ev in events if ev["kind"] == "vendor.disqualify"]
    if not dq:
        findings.append("claim 1: no disqualification events; the liar was never caught")
    first_contract = _seq_of(events, lambda e: e["kind"] == "contract.seal")
    compose_seq = _seq_of(events, lambda e: e["kind"] == "app.compose")
    for ev in dq:
        if not ev["detail"].get("evidence_digest"):
            findings.append(f"claim 1: disqualification #{ev['seq']} lacks replayable evidence")
    liar_dq = [ev for ev in dq if "normalizer-a" in ev["detail"].get("vendor", "")]
    if not liar_dq:
        findings.append("claim 1: normalizer-a (the liar) was never disqualified")
    for ev in liar_dq:
        probe_json = run_dir / "probes" / f"{ev['detail']['vendor']}.json"
        if not probe_json.exists():
            findings.append(f"claim 1: no replayable probe transcript for {ev['detail']['vendor']}")
        if first_contract is not None and ev["seq"] > first_contract:
            findings.append(f"claim 1: disqualification #{ev['seq']} postdates first contract #{first_contract}")
        if compose_seq is not None and ev["seq"] > compose_seq:
            findings.append(f"claim 1: disqualification #{ev['seq']} postdates compose #{compose_seq}")

    # liar never appears in a sealed contract or the pipeline
    sealed_vendors = {c["vendor"] for c in
                      json.loads((run_dir / "contracts.json").read_text()).values()}
    if "normalizer-a" in sealed_vendors:
        findings.append("claim 1: a contract exists with the disqualified liar")

    # -- claim 2: every contract honored or slashed --------------------------
    contracts = json.loads((run_dir / "contracts.json").read_text())
    remediated: set[str] = set()
    for ev in events:
        d = ev["detail"]
        if ev["kind"] in ("vendor.slash", "vendor.disqualify"):
            remediated.add(d.get("contract_id") or f"vendor:{d.get('vendor')}")
        if ev["kind"] in ("renegotiate", "contract.seal.amend", "app.reroute",
                          "swap.rollback"):
            remediated.add(f"vendor:{d.get('vendor')}" if d.get("vendor")
                           else d.get("contract_id", ""))
    for ev in events:
        if ev["kind"] in ("contract.violation", "contract.drift"):
            cid = ev["detail"].get("contract_id")
            vendor = ev["detail"].get("vendor")
            if cid in remediated or f"vendor:{vendor}" in remediated:
                continue
            findings.append(f"claim 2: {ev['type']} #{ev['seq']} on {cid} "
                            f"({vendor}) was neither honored nor slashed")

    # -- claim 3: proven safe before first execution -------------------------
    seal = _seq_of(events, lambda e: e["kind"] == "proof.seal"
                   and e["detail"].get("verdict") == "PROVEN")
    live = _seq_of(events, lambda e: e["kind"] == "app.live")
    abort = _seq_of(events, lambda e: e["kind"] == "proof.abort")
    if seal is None:
        findings.append("claim 3: no successful proof.seal — composition never proven")
        if live is not None:
            findings.append("claim 3: application ran live without a proof")
    elif live is not None and seal > live:
        findings.append(f"claim 3: proof.seal #{seal} postdates app.live #{live}")
    obligations = [ev for ev in events if ev["kind"] == "proof.obligation"]
    if not obligations:
        findings.append("claim 3: no proof obligations were ever generated")
    for ev in obligations:
        if ev["detail"].get("verdict") != "PASS":
            findings.append(f"claim 3: obligation {ev['detail'].get('obligation')} "
                            f"was {ev['detail'].get('verdict')}")

    # -- claim 4: hot-swap preserved event integrity -------------------------
    swaps = [ev for ev in events if ev["kind"] == "app.swap"]
    rollbacks = [ev for ev in events if ev["kind"] == "swap.rollback"]
    if not swaps and not rollbacks:
        findings.append("claim 4: no hot-swap was ever exercised")
    for ev in swaps:
        vend = ev["detail"].get("to_vendor")
        swap_verdict = _seq_of(events, lambda e: e["kind"] == "swap.verdict"
                               and e["detail"].get("vendor") == vend
                               and e["detail"].get("verdict") == "PROVEN")
        if swap_verdict is None:
            findings.append(f"claim 4: swap to {vend} had no shadow proof")
        elif ev["seq"] < swap_verdict:
            findings.append(f"claim 4: swap #{ev['seq']} predates its proof #{swap_verdict}")
        if ev["detail"].get("dropped_events", 0) != 0:
            findings.append(f"claim 4: swap #{ev['seq']} dropped events")
    for ev in rollbacks:
        if not ev["detail"].get("reason"):
            findings.append(f"claim 4: rollback #{ev['seq']} lacks a reason")

    acc = final_state.get("accounting") or {}
    if acc:
        accounted = (acc.get("settled", 0) + acc.get("held", 0)
                     + acc.get("rejected", 0) + acc.get("deadletter", 0))
        if accounted != acc.get("inputs", -1):
            findings.append(f"claim 4: accounting imbalance {accounted} != {acc.get('inputs')} inputs")
        if acc.get("violations"):
            findings.append(f"claim 4: {acc['violations']} unremediated live violations")

    # -- claim 5: the probe-evasion liar was caught live and expelled --------
    drifts = [ev for ev in events if ev["kind"] == "calibration.drift"]
    if not drifts:
        findings.append("claim 5: no calibration drift was ever detected")
    expelled = [ev for ev in events if ev["kind"] == "vendor.expelled"]
    if not expelled:
        findings.append("claim 5: no vendor was ever expelled")
    for ev in drifts:
        if not ev["detail"].get("evidence"):
            findings.append(f"claim 5: calibration drift #{ev['seq']} lacks evidence")
    for ev in expelled:
        if not ev["detail"].get("evidence"):
            findings.append(f"claim 5: expulsion #{ev['seq']} lacks evidence")
        exp_seq = ev["seq"]
        after = [e for e in events if e["kind"] == "cap.call"
                 and e["detail"].get("vendor") == ev["detail"].get("vendor")
                 and e["seq"] > exp_seq]
        if after:
            findings.append(f"claim 5: expelled vendor {ev['detail'].get('vendor')} "
                            f"served {len(after)} calls after expulsion")
    canaries = [ev for ev in events if ev["kind"] == "calibration.canary"]
    if canaries and not any(ev["detail"].get("ok") for ev in canaries):
        findings.append("claim 5: canaries fired but never passed for an honest vendor")

    # -- claim 6: the equivocator was caught by replay audits ----------------
    replays = [ev for ev in events if ev["kind"] == "determinism.replay"]
    nondet = [ev for ev in events if ev["kind"] == "determinism.violation"]
    if not replays:
        findings.append("claim 6: the determinism regime never ran a replay audit")
    if not nondet:
        findings.append("claim 6: no equivocation was ever detected")
    for ev in nondet:
        if not (ev["detail"].get("original_sha") and ev["detail"].get("replay_sha")):
            findings.append(f"claim 6: equivocation #{ev['seq']} lacks both digests")
    slash_vendors = {ev["detail"].get("vendor") for ev in events
                     if ev["kind"] == "vendor.slash"}
    for ev in nondet:
        if ev["detail"].get("vendor") not in slash_vendors:
            findings.append(f"claim 6: equivocator {ev['detail'].get('vendor')} "
                            f"was not slashed")
    settler_b = [e for e in events if e["kind"] == "cap.call"
                 and e["detail"].get("stage") == "settle"
                 and e["detail"].get("vendor") == "settler-b"]
    if nondet and not settler_b:
        findings.append("claim 6: settle edge never moved to the spare settler")

    # -- claim 7: the Forge sold its own proven composition ------------------
    listed = [ev for ev in events if ev["kind"] == "product.listed"]
    pcontracts = [ev for ev in events if ev["kind"] == "product.contract"]
    served = [ev for ev in events if ev["kind"] == "product.served"]
    if listed:
        proof_path = run_dir / "proof-report.json"
        want = hashlib.sha256(proof_path.read_bytes()).hexdigest()[:16]
        for ev in listed:
            if ev["detail"].get("proof_sha") != want:
                findings.append(f"claim 7: product attestation #{ev['seq']} does "
                                f"not bind the actual molt-verify report")
            if ev["detail"].get("listing_errors"):
                findings.append(f"claim 7: product listing #{ev['seq']} had "
                                f"catalog errors {ev['detail']['listing_errors']}")
        if not pcontracts:
            findings.append("claim 7: the product was listed but never sold")
        if len(served) < 12:
            findings.append(f"claim 7: buyer stream only served {len(served)} events")
        bad = [ev for ev in served if not ev["detail"].get("validated")]
        if bad:
            findings.append(f"claim 7: {len(bad)} buyer receipts failed validation")
        econ = [ev for ev in events if ev["kind"] == "economy.final"]
        if not econ:
            findings.append("claim 7: no economy.final event; the books never closed")
        elif (econ[-1]["detail"].get("product_margin") or 0) <= 0:
            findings.append("claim 7: product margin is not positive")

    return {
        "ok": not findings,
        "findings": findings,
        "claims": {
            "liar_caught_before_pipeline": not any(
                f.startswith("claim 1") for f in findings),
            "contracts_honored_or_slashed": not any(
                f.startswith("claim 2") for f in findings),
            "proven_before_first_execution": not any(
                f.startswith("claim 3") for f in findings),
            "hot_swap_preserved_integrity": not any(
                f.startswith("claim 4") for f in findings),
            "probe_evasion_caught_live_and_expelled": not any(
                f.startswith("claim 5") for f in findings),
            "equivocation_caught_by_replay_audit": not any(
                f.startswith("claim 6") for f in findings),
            "forge_sold_its_own_proven_composition": not any(
                f.startswith("claim 7") for f in findings),
            "chain_integrity": not chain_errs,
            "anchor_integrity": not trail_errs,
        },
        "stats": {
            "events": len(events),
            "disqualifications": len(dq),
            "expulsions": len(expelled),
            "contracts": len(contracts),
            "replay_audits": len(replays),
            "calibration_canaries": len(canaries),
            "product_events_served": len(served),
            "slashes": sum(1 for e in events if e["kind"] == "vendor.slash"),
            "treasury": final_state.get("treasury", 0),
            "spent": final_state.get("spent", 0),
        },
    }


def write_report(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    report = audit(run_dir)
    out = run_dir / "audit-report.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    return out, report


def render_markdown(run_dir: Path, report: dict) -> str:
    run_dir = Path(run_dir)
    events = [json.loads(line) for line in
              (run_dir / "log.jsonl").read_text().splitlines() if line.strip()]
    c = report["claims"]
    ticks = ["✅" if v else "❌" for v in (
        c["liar_caught_before_pipeline"], c["contracts_honored_or_slashed"],
        c["proven_before_first_execution"], c["hot_swap_preserved_integrity"],
        c["chain_integrity"], c["anchor_integrity"])]
    L = ["# molt-audit — The Forge Ledger\n",
         "*Machine-checked against the signed forge log, the probe "
         "transcripts, and the molt-anchor trail.*\n",
         "| Claim | Verdict |", "|---|---|",
         f"| {ticks[0]} The liar was detected by behavioral evidence before "
         f"it ever touched the pipeline | "
         f"{'PASS' if c['liar_caught_before_pipeline'] else 'FAIL'} |",
         f"| {ticks[1]} Every contract was honored or slashed | "
         f"{'PASS' if c['contracts_honored_or_slashed'] else 'FAIL'} |",
         f"| {ticks[2]} The composition was proven safe before first "
         f"execution | "
         f"{'PASS' if c['proven_before_first_execution'] else 'FAIL'} |",
         f"| {ticks[3]} The live hot-swap preserved event integrity "
         f"end-to-end | "
         f"{'PASS' if c['hot_swap_preserved_integrity'] else 'FAIL'} |",
         f"| {ticks[4]} Forge log hash-chain intact | "
         f"{'PASS' if c['chain_integrity'] else 'FAIL'} |",
         f"| {ticks[5]} Molt-anchor trail intact | "
         f"{'PASS' if c['anchor_integrity'] else 'FAIL'} |", ""]

    st = report["stats"]
    L.append(f"**Scope** — {st['events']} signed events, "
             f"{st['disqualifications']} disqualification(s), "
             f"{st['contracts']} sealed contract(s), {st['slashes']} slash(es); "
             f"treasury holds {st['treasury']} forfeited stake on "
             f"{st['spent']} credits spent.\n")

    for ev in events:
        d = ev["detail"]
        if ev["kind"] == "vendor.disqualify":
            L.append(f"- Disqualification `{d.get('vendor')}` at seq "
                     f"`#{ev['seq']}` — evidence `{d.get('evidence_digest')}`: "
                     f"{d.get('reason')}")
        elif ev["kind"] == "contract.seal":
            L.append(f"- Contract `{d.get('contract_id')}` with "
                     f"`{d.get('vendor')}` sealed at seq `#{ev['seq']}` "
                     f"(price {d.get('price')}, stake {d.get('stake')})")
        elif ev["kind"] == "proof.seal":
            L.append(f"- Proof sealed at seq `#{ev['seq']}` — "
                     f"{d.get('verdict')}: {d.get('note')}")
        elif ev["kind"] == "vendor.slash":
            L.append(f"- Slash `{d.get('vendor')}` at seq `#{ev['seq']}` — "
                     f"{d.get('forfeit')} stake forfeited ({d.get('reason')})")
        elif ev["kind"] == "app.swap":
            L.append(f"- Hot-swap to `{d.get('to_vendor')}` at seq "
                     f"`#{ev['seq']}` — dropped events: "
                     f"{d.get('dropped_events', 0)}")
        elif ev["kind"] == "swap.rollback":
            L.append(f"- Rollback at seq `#{ev['seq']}` — {d.get('reason')}")
    L.append("")
    if report["findings"]:
        L.append("**Findings**\n")
        for f in report["findings"]:
            L.append(f"- ❌ {f}")
        L.append("")
    L.append("*A failed proof is a signed abort-event, not a crash.*\n")
    return "\n".join(L)