"""molt-forge chronicle — render the Forge's signed log into a readable
molt-show chronicle. Pure projection: reads the log, writes markdown,
changes nothing."""

from __future__ import annotations

import json
from pathlib import Path


def _h(ev: dict) -> str:
    return ev["hash"][:12]


def _detail(ev: dict) -> dict:
    return ev.get("detail") or {}


def render(forge_log_events: list[dict], final_state: dict,
           contracts: dict, anchor: dict) -> str:
    L: list[str] = []
    a = L.append
    a("# Chronicle — Molt Forge, the Capability Bazaar\n")
    a("*Rendered from the forge's signed molt-show log; every quoted seq "
      "is replayable from `log.jsonl` alone.*\n")

    kinds = {ev["kind"] for ev in forge_log_events}
    n_viol = sum(1 for ev in forge_log_events if ev["kind"] == "contract.violation")
    n_drift = sum(1 for ev in forge_log_events if ev["kind"] == "contract.drift")
    n_slash = sum(1 for ev in forge_log_events if ev["kind"] == "vendor.slash")
    n_dq = sum(1 for ev in forge_log_events if ev["kind"] == "vendor.disqualify")

    n_canary = sum(1 for ev in forge_log_events if ev["kind"] == "calibration.canary")
    n_replay = sum(1 for ev in forge_log_events if ev["kind"] == "determinism.replay")
    n_served = sum(1 for ev in forge_log_events if ev["kind"] == "product.served")
    a("**Genesis** — " + ", ".join(sorted(kinds)) + f"; {len(forge_log_events)} "
      f"events, {n_dq} disqualification(s), {n_viol} violation(s), "
      f"{n_drift} drift finding(s), {n_slash} slash(es), {n_canary} calibration "
      f"canaries, {n_replay} replay audits, {n_served} buyer events served.\n")

    phase = 0

    def phase_header(n: int, title: str) -> None:
        nonlocal phase
        if phase < n:
            phase = n
            a(f"\n## {title}\n")

    for ev in forge_log_events:
        d = _detail(ev)
        t = ev["kind"]
        if t == "forge.genesis":
            a(f"## Genesis — seq `#{ev['seq']}`, hash `{_h(ev)}`\n")
            a(f"The Forge Master awakens: identity `{d.get('forge')}`, "
              f"ruleset `{d.get('ruleset')}` (hash `{d.get('ruleset_hash')}`), "
              f"gas budget `{d.get('budget')}`, max acceptable price "
              f"`{d.get('max_price')}`. {len(d.get('vendors', []))} vendor "
              f"programs exist in the world. Creed: *\"{d.get('creed')}\"*.\n")
        elif t == "market.open":
            phase_header(1, "Phase 1 — The Bazaar")
            a(f"- `#{ev['seq']}` the molt-show observatory opens the "
              f"capability bazaar; {d.get('vendors')} signed catalog "
              f"entries follow")
        elif t == "catalog.publish":
            e = d.get("entry", {})
            a(f"- `#{ev['seq']}` **{e.get('vendor')}** publishes "
              f"`{e.get('capability')}` v{e.get('version')} "
              f"(price {e.get('declared', {}).get('price')}, entry "
              f"`{str(e.get('entry_hash'))[:16]}`)")
        elif t == "catalog.listed":
            errs = d.get("errors") or []
            a(f"- `#{ev['seq']}` catalog lists `{d.get('vendor')}` "
              f"({d.get('capability')} v{d.get('version')}): "
              f"{'verified' if d.get('verified') else 'rejected — ' + '; '.join(errs)}")
        elif t == "attestation.verdict":
            verdict = "pass" if d.get("ok") else "failed"
            errs = d.get("errors") or []
            a(f"- `#{ev['seq']}` attestation verdict **{verdict}** "
              f"for `{d.get('vendor')}` — binds audit head "
              f"`{str(d.get('audit_head'))[:12]}` "
              f"({d.get('events_audited')} audited events)"
              + ("" if d.get("ok") else " — " + "; ".join(errs)))
        elif t == "discovery.begin":
            phase_header(2, "Phase 2 — Discovery Under Distrust")
            a(f"- `#{ev['seq']}` {d.get('note')}")
        elif t == "probe.campaign":
            mismatch = d.get("schema_mismatch") or []
            a(f"- `#{ev['seq']}` black-box probe campaign against "
              f"`{d.get('vendor')}`: {d.get('probes')} probes, "
              f"{d.get('direct_violations')} direct violations, "
              f"schema mismatch **{mismatch if mismatch else 'none'}**, "
              f"transcript `{d.get('transcript_sha')}`")
        elif t == "vendor.disqualify":
            a(f"- `#{ev['seq']}` ⚡ **disqualification of `{d.get('vendor')}`** "
              f"— {d.get('reason')} (evidence `{d.get('evidence_digest')}`) "
              f"— the liar never touches the pipeline")
        elif t == "negotiation.begin":
            phase_header(3, "Phase 3 — Negotiation")
            a(f"- `#{ev['seq']}` {d.get('note')}")
        elif t == "gossip.contract.offer":
            pass
        elif t == "gossip.contract.reply":
            pass
        elif t == "contract.sealed":
            a(f"- `#{ev['seq']}` ✍ contract `{d.get('contract_id')}` **sealed** "
              f"with `{d.get('vendor')}` (price {d.get('price')}, gas ceiling "
              f"{d.get('gas_ceiling')}, latency ≤ {d.get('latency_ms_max')}ms, "
              f"liability stake {d.get('stake')}, two signatures)")
        elif t == "contract.renegotiation-failed":
            a(f"- `#{ev['seq']}` renegotiation with `{d.get('vendor')}` "
              f"**refused** — {d.get('reason')}")
        elif t == "composition.begin":
            a(f"- `#{ev['seq']}` composition — pipeline edges type-checked "
              f"against sealed contracts, spares held in reserve")
        elif t == "app.compose":
            a(f"- `#{ev['seq']}` `app.compose` — " + "; ".join(
                f"{s['stage']}←{s['vendor']} (`{s['contract_id']}`)"
                for s in d.get("pipeline", [])))
        elif t == "proof.obligation":
            phase_header(4, "Phase 4 — Prove It Before You Run It")
            a(f"- `#{ev['seq']}` obligation `{d.get('obligation')}` — "
              f"**{d.get('verdict')}**" + (f" ({d.get('note')})"
                                           if d.get("note") else ""))
        elif t == "sandbox.run":
            a(f"- `#{ev['seq']}` sandbox run over {d.get('inputs')} "
              f"adversarial events: {d.get('violations')} violations, "
              f"{d.get('deadletter')} dead-lettered, gas used "
              f"{d.get('gas_used')}")
        elif t == "proof.seal":
            a(f"- `#{ev['seq']}` 🔒 **molt-verify report sealed — "
              f"{d.get('verdict')}** ({d.get('note')}); the composition may run")
        elif t == "proof.abort":
            a(f"- `#{ev['seq']}` 🛑 **signed abort — the proof failed, the "
              f"application must not run** ({d.get('note')})")
        elif t == "app.live":
            phase_header(5, "Phase 5 — The Fork: Live, Drift, Hot-Swap")
            a(f"- `#{ev['seq']}` 🔥 the application goes live: " + "; ".join(
                f"{s['stage']}←{s['vendor']} (`{s['contract_id']}`)"
                for s in d.get("pipeline", [])))
        elif t == "contract.drift":
            if d.get("kind") == "price":
                contracted = (contracts.get(d.get("contract_id"), {})
                              .get("price"))
                a(f"- `#{ev['seq']}` 🌀 **mid-flight renegotiation attempt by "
                  f"`{d.get('vendor')}`**: {d.get('observed_price')} "
                  f"(contract `{d.get('contract_id')}` at {contracted})")
            else:
                a(f"- `#{ev['seq']}` 🌀 **schema drift by `{d.get('vendor')}`**: "
                  f"{d.get('detail') or d.get('reason')}")
        elif t == "vendor.slash":
            a(f"- `#{ev['seq']}` ⚡ `{d.get('vendor')}` **slashed**: {d.get('forfeit')} "
              f"stake forfeited to treasury ({d.get('reason')}, evidence "
              f"`{d.get('evidence_digest')}`)")
        elif t == "pipeline.deadletter":
            a(f"- `#{ev['seq']}` dead-lettered trigger for input "
              f"`{d.get('input')}` at `{d.get('stage')}` — {d.get('reason')} "
              f"(claim stays queued)")
        elif t == "edge.rerouted":
            a(f"- `#{ev['seq']}` 🔀 stage `{d.get('stage')}` rerouted from "
              f"`{d.get('from')}` to `{d.get('to')}` "
              f"(`{d.get('contract_id')}`), re-proof follows")
        elif t == "edge.reproof":
            a(f"- `#{ev['seq']}` re-proof of the rerouted pipeline: "
              f"**{d.get('verdict')}**")
        elif t == "queue.redriven":
            a(f"- `#{ev['seq']}` orphaned queue re-driven: {d.get('orphans')} "
              f"orphans, {d.get('settled')} settled, {d.get('requeued')} "
              f"requeued — nothing dropped")
        elif t == "catalog.version-bump":
            a(f"- `#{ev['seq']}` 📣 `{d.get('vendor')}` publishes a "
              f"{'*breaking* ' if d.get('schema_changed') else ''}version "
              f"bump v{d.get('from_version')}→v{d.get('to_version')} "
              f"(declared price {d.get('price_now')})")
        elif t == "swap.shadow":
            a(f"- `#{ev['seq']}` shadow probe: {d.get('inputs')} events "
              f"mirrored to the candidate pipeline, "
              f"{d.get('violations')} violations")
        elif t == "swap.proof":
            a(f"- `#{ev['seq']}` swap verdict **{d.get('verdict')}** — "
              f"{d.get('note')}")
        elif t == "swap.rollback":
            a(f"- `#{ev['seq']}` ⏪ **automatic rollback** from "
              f"`{d.get('vendor')}` v2 — {d.get('reason')}; {d.get('note')}")
        elif t == "swap.commit":
            a(f"- `#{ev['seq']}` ✅ **live swap complete**: `{d.get('vendor')}` "
              f"now serves the score stage (`{d.get('contract_id')}`)")
        elif t == "contract.superseded":
            a(f"- `#{ev['seq']}` contract `{d.get('old_contract')}` superseded "
              f"by `{d.get('new_contract')}` for `{d.get('vendor')}` "
              f"(price {d.get('old_price')} → {d.get('new_price')})")
        elif t == "catalog.listing":
            phase_header(6, "Phase 6 — The Open Bazaar")
            a(f"- `#{ev['seq']}` 🛎 a **new seller** walks into the bazaar: "
              f"`{d.get('vendor')}` ({d.get('capability')}) listing at "
              f"**{d.get('price')}** — catalog entry verified: "
              f"{d.get('verified')}")
        elif t == "probe.fasttrack":
            a(f"- `#{ev['seq']}` ⏱ **fast-track probe campaign** for "
              f"`{d.get('vendor')}`: {d.get('probes')} probes, verdict "
              f"**{d.get('verdict')}** ({d.get('note')})")
        elif t == "calibration.canary":
            if d.get("ok"):
                a(f"- `#{ev['seq']}` 🎯 calibration canary on "
                  f"`{d.get('vendor')}`: risk {d.get('observed_risk')} vs "
                  f"calibrated {d.get('expected_risk')} — within tolerance")
            else:
                a(f"- `#{ev['seq']}` 🚨 **calibration canary FAILS** on "
                  f"`{d.get('vendor')}`: live risk {d.get('observed_risk')} "
                  f"vs the vendor's own probed calibration "
                  f"{d.get('expected_risk')}")
        elif t == "calibration.drift":
            a(f"- `#{ev['seq']}` **context-keyed fraud detected** in "
              f"`{d.get('vendor')}`: honest under probe-era timestamps, "
              f"off-calibration on live traffic (evidence `{d.get('evidence')}`)")
        elif t == "probe.paired":
            a(f"- `#{ev['seq']}` 🔬 paired post-mortem probes on "
              f"`{d.get('vendor')}`: context-keyed behavior "
              f"**{d.get('context_keyed')}** — same payload, fresh vs stale "
              f"timestamp, different answer")
        elif t == "vendor.expelled":
            a(f"- `#{ev['seq']}` 🚫 `{d.get('vendor')}` **expelled from the "
              f"bazaar**: {d.get('reason')} (evidence `{d.get('evidence')}`)")
        elif t == "determinism.replay":
            a(f"- `#{ev['seq']}` ♻ replay audit on `{d.get('vendor')}` "
              f"(`{d.get('call_id')}`): "
              + ("byte-identical re-emit" if d.get("match")
                 else "**REPLAY DIVERGES**") + f" (audit cost {d.get('audit_cost')})")
        elif t == "determinism.violation":
            a(f"- `#{ev['seq']}` **equivocation detected** in "
              f"`{d.get('vendor')}`: the same settle request answered "
              f"differently on replay (original `{d.get('original_sha')}`, "
              f"replay `{d.get('replay_sha')}`)")
        elif t == "live.complete":
            acc = d.get("accounting", {})
            a(f"- `#{ev['seq']}` the Forge's own stream is complete: "
              f"{acc.get('inputs')} inputs, balanced "
              f"**{acc.get('balanced')}**; replay audits cost "
              f"{d.get('audit_spend')} — the application is proven live")
        elif t == "product.listed":
            a(f"- `#{ev['seq']}` 🏷 **the Forge becomes a vendor**: "
              f"`{d.get('product')}` listed at {d.get('price')} (stage cost "
              f"{d.get('stage_cost')}), attested by its own molt-verify "
              f"report `{d.get('proof_sha')}`")
        elif t == "product.contract":
            a(f"- `#{ev['seq']}` 🤝 two-signature product contract sealed "
              f"with `buyer` at {d.get('price')} — the Forge bonds "
              f"{d.get('bond')} of its own stake against it "
              f"(`{d.get('contract_id')}`)")
        elif t == "product.served":
            if d.get("validated"):
                a(f"- `#{ev['seq']}` ✓ `{d.get('input_id')}` → "
                  f"{d.get('status')} (buyer validated, paid {d.get('invoiced')})")
            else:
                a(f"- `#{ev['seq']}` ✗ `{d.get('input_id')}` receipt "
                  f"REJECTED by the buyer")
        elif t == "product.stream":
            a(f"- `#{ev['seq']}` buyer stream complete: {d.get('served')} "
              f"events served, revenue {d.get('revenue')}, buyer disputes: "
              f"{d.get('buyer_disputes') or 'none'}")
        elif t == "economy.final":
            a(f"- `#{ev['seq']}` 🧾 **the bazaar closes its books**: revenue "
              f"{d.get('revenue')}, product vendor cost "
              f"{d.get('product_vendor_cost')}, lifetime audit spend "
              f"{d.get('audit_cost_total')}, product margin "
              f"**{d.get('product_margin')}**")
        elif t == "app.final":
            acc = d.get("accounting", {})
            a(f"- `#{ev['seq']}` end of stream: {acc.get('inputs')} inputs → "
              f"{acc.get('transfers')} transfers, {acc.get('settled')} "
              f"settled batch claims, {acc.get('held')} held, "
              f"{acc.get('rejected')} rejected, {acc.get('deadletter')} "
              f"dead-lettered, balanced **{acc.get('balanced')}**")
        elif t == "catalog.listing":
            phase_header(6, "Phase 6 — The Open Bazaar: Evasion, Equivocation, Sale")
            a(f"- `#{ev['seq']}` 🛒 **new seller walks in**: `{d.get('vendor')}` "
              f"({d.get('capability')}) listing at price {d.get('price')} — "
              f"entry hash `{d.get('entry_hash')}`")
        elif t == "probe.fasttrack":
            a(f"- `#{ev['seq']}` fast-track probe campaign against "
              f"`{d.get('vendor')}`: {d.get('probes')} probes, verdict "
              f"**{d.get('verdict')}** ({d.get('note')})")
        elif t == "calibration.canary":
            a(f"- `#{ev['seq']}` 🐦 calibration canary vs `{d.get('vendor')}`: "
              f"amount {d.get('amount')}, expected risk {d.get('expected_risk')}, "
              f"observed {d.get('observed_risk')} → "
              f"{'within calibration' if d.get('ok') else '**OFF-CALIBRATION**'}")
        elif t == "calibration.drift":
            a(f"- `#{ev['seq']}` 🚨 **calibration drift by `{d.get('vendor')}`** — "
              f"live emit contradicts the vendor's own probed calibration "
              f"(behavior keyed on input context)")
        elif t == "probe.paired":
            a(f"- `#{ev['seq']}` paired post-mortem probes vs `{d.get('vendor')}`: "
              f"context-keyed behavior **{d.get('context_keyed')}**")
        elif t == "vendor.expelled":
            a(f"- `#{ev['seq']}` ⛔ `{d.get('vendor')}` **expelled from the "
              f"bazaar** — {d.get('reason')} (evidence `{d.get('evidence')}`)")
        elif t == "determinism.replay":
            if not d.get("match"):
                pass   # the violation event tells the story
            else:
                pass   # routine audits stay out of the narrative
        elif t == "determinism.violation":
            a(f"- `#{ev['seq']}` 🎭 **equivocation by `{d.get('vendor')}`** — the "
              f"same settle request answered two different ways "
              f"(`{d.get('original_sha')}` vs `{d.get('replay_sha')}`)")
        elif t == "live.complete":
            acc = d.get("accounting", {})
            a(f"- `#{ev['seq']}` the Forge's own stream is complete: "
              f"{acc.get('inputs')} inputs, balanced **{acc.get('balanced')}**, "
              f"{d.get('audit_spend')} spent on trust (replay audits)")
        elif t == "product.listed":
            a(f"- `#{ev['seq']}` 🏷️ **the Forge lists its own product** "
              f"`{d.get('product')}` at price {d.get('price')} (stage cost "
              f"{d.get('stage_cost')}), attested by molt-verify report "
              f"`{d.get('proof_sha')}`")
        elif t == "product.contract":
            a(f"- `#{ev['seq']}` 🤝 **two-signature product contract sealed** with "
              f"`{d.get('buyer')}` (`{d.get('contract_id')}`, price "
              f"{d.get('price')}, the Forge bonds {d.get('bond')} stake against "
              f"its own liability)")
        elif t == "product.served":
            a(f"- `#{ev['seq']}` served `{d.get('input_id')}` → {d.get('status')} "
              f"({'receipt validated, paid' if d.get('validated') and d.get('paid') else 'VALIDATION FAILED'})")
        elif t == "product.stream":
            a(f"- `#{ev['seq']}` buyer stream complete: {d.get('served')} events, "
              f"revenue {d.get('revenue')}, disputes {d.get('buyer_disputes')}")
        elif t == "economy.final":
            a(f"- `#{ev['seq']}` 💰 **the books close**: revenue {d.get('revenue')}, "
              f"product vendor cost {d.get('product_vendor_cost')}, product margin "
              f"**{d.get('product_margin')}**, audit cost {d.get('audit_cost_total')}, "
              f"treasury {d.get('treasury')}")
        elif t == "ledger.debit":
            pass
        elif t == "gossip.cap.invoice":
            pass
        elif t == "cap.call":
            pass
        elif t == "reactor.rule-fired":
            a(f"- `#{ev['seq']}` ⚙ rule `{d.get('rule')}` fired → "
              f"{d.get('action')} ({d.get('obs')})")
        elif t == "forge.final":
            a("\n## The Forge Ledger\n")
            a(f"- `#{ev['seq']}` chain integrity: log chain ok = "
              f"**{d.get('chain_ok')}**, anchor trail ok = "
              f"**{d.get('anchor_trail_ok')}**; spent {d.get('spent')} "
              f"credits, treasury holds {d.get('treasury')} forfeited stake")
        # other kinds are rendered by the audit report, not the chronicle

    pipe = final_state.get("pipeline", [])
    if pipe:
        a("\n**Final composition** — " + " → ".join(
            f"{p['stage']}({p['vendor']})" for p in pipe) + "\n")
    vend = final_state.get("vendors", {})
    if vend:
        rows = [f"`{n}` stake {s['stake']}, credits {s['credits']}"
                + (", **slashed**" if s["slashed"] else "")
                for n, s in sorted(vend.items())]
        a("**Vendor books** — " + "; ".join(rows) + ".\n")
    a("*The Forge is a self-hosting market maker: buys, proves, composes, "
      "and sells — and its entire reasoning trail is replayable from its "
      "genesis event.*\n")
    return "\n".join(L) + "\n"


def write_chronicle(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    final_state = json.loads((run_dir / "final-state.json").read_text())
    contracts = json.loads((run_dir / "contracts.json").read_text())
    anchor = json.loads((run_dir / "anchor.json").read_text())
    events = [json.loads(line) for line in
              (run_dir / "log.jsonl").read_text().splitlines() if line.strip()]
    text = render(events, final_state, contracts, anchor)
    out = run_dir / "chronicle.md"
    out.write_text(text)
    return out
