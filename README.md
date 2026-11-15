# Molt Forge — The Capability Bazaar

The third molt-stack challenge. Where Sovereign Agent Zero trusted only
itself and the Byzantium mesh trusted 2/3 of peers, the Forge Master
trusts **nothing it has not probed, contracted, and proven**.

The Forge is a market maker for agent capabilities: it turns other
agents' molt-reactor programs into a working, audited application —
without ever reading their source, and without ever running a composed
pipeline that hasn't been proven safe first.

## What it builds

`python3 -m moltforge run --dir runs/default [--seed N]` executes all
five phases and writes a fully replayable run to the directory:

```
runs/default/
├── anchor.json         molt-anchor: genesis + governance inscriptions
├── log.jsonl           the Forge's signed molt-show log (every decision)
├── catalog.json        the bazaar's listed capabilities (schema hashes)
├── contracts.json      sealed two-signature capability contracts
├── probes/*.json       black-box probe campaigns + behavioral models
├── proof-report.json   the molt-verify report (obligations + sandbox)
├── final-state.json    end-of-run books: stakes, credits, treasury
├── chronicle.md        the molt-show chronicle (readable narrative)
├── audit-report.json   the molt-audit report (claims + evidence)
└── vendors/*.jsonl     each vendor's own signed show log
keys.json               all Ed25519 identities (0600 — never publish)
```

```
python3 -m moltforge chronicle [--dir runs/default]   # re-render narrative
python3 -m moltforge audit    [--dir runs/default]    # re-audit (exit code gates)
python3 -m moltforge serve    [--dir runs/default]    # observatory on :8700
python3 tests/test_forge.py                           # 47 invariant checks
```

## The phases

**Phase 1 — The Bazaar.** Seven vendor agents publish signed catalog
entries (interface schemas, price, gas ceiling, latency bound, and a
molt-audit attestation binding their own log head) over the gossip
layer. The Forge lists each entry and verifies it.

**Phase 2 — Discovery under distrust.** For every vendor the Forge runs
an adversarial black-box probe campaign: type confusion, boundary
ladders, unmapped kinds, duplicates, oversize payloads. From the emit
transcripts it infers the *observed* output schema and diffs it against
the *declared* one. `normalizer-a` is the liar: on happy-path amounts it
behaves exactly as declared, but amounts ≥ 1,000,000 come back as
locale strings (`"1,234,567.89"`) and unmapped kinds pass through. The
probes catch the widening — `'amount' declared number, observed
number|string` — and the Forge disqualifies it with a signed,
evidence-digested event. **The liar never touches the pipeline.**

Attestations are verified prefix-wise: the Forge recomputes the vendor's
hash chain over the attested event count and requires it to equal the
attested head — a forged or stale attestation fails, a longer log still
passes.

**Phase 3 — Negotiation.** Contracts are negotiated over the gossip
layer as signed envelope exchanges; each sealed contract carries both
signatures, schema hashes, price, gas ceiling, latency bound, and a
liability clause (stake, slash conditions) inscribed on the molt-anchor.
Two treacheries are scripted in the vendor programs, and both are
caught automatically by continuous emit monitoring:

- `scheduler-a` silently widens its output schema mid-flight (adds
  `priority`) after ~12 live calls. The widened trigger is dead-lettered
  (the claim stays queued), renegotiation is attempted and refused, the
  stake is slashed to the treasury, and the pipeline edge reroutes to
  the spare `scheduler-b` — followed by a re-proof of the new
  composition and a re-drive of the orphaned queue. Nothing drops.
- `scorer-a` silently raises its invoice price at call 15. The price
  drift is renegotiated mid-flight: `ctr-0004` @ 2.0 is superseded by
  `ctr-0007` @ 4.0 without dropping an event.

**Phase 4 — Prove it before you run it.** Nine proof obligations
(O1–O4 edge reachability, O-final output reachability, O-rej-1..4
rejection-channel containment) are checked with a decidable schema
algebra: the guarantee flowing into each stage — previous contract
output, projected through the edge adapter, joined with the trigger
guarantee (`at` renamed to `trigger_ts`) — must *imply* the stage's
contracted input, with a constructible counterexample on every failure.
The pipeline then runs in a sandboxed molt-reactor over 96 adversarial
events (injection, type confusion, duplicates, oversize strings,
unicode), and the molt-verify report checks every intermediate event is
contract-valid, monotone (risk never decreases as amount rises),
causally ordered (vector clocks), and that accounting balances to zero
lost events. Only a `PROVEN` report — sealed and signed — lets the
application process its first real event. A failed proof produces a
signed `app.abort`, not a crash.

**Phase 5 — The fork.** Mid-stream, `scorer-a` publishes a *breaking*
v2 (renamed output field, widened risk range, doubled price). The bump
arrives as a gossip envelope; the Forge detects the schema change,
shadow-proofs the candidate against the live contracts — obligations
O3/O4 **fail** (the widened `risk` field breaks the settler's
`additionalProperties: false`, the renamed field breaks reachability) —
and rolls back automatically with a signed event and a slash, while the
live pipeline keeps consuming on the version-pinned v1.

**Phase 6 — The Open Bazaar.** The application stays live while the
market stays open, and the bazaar gets harder in three new ways:

- **The probe-evasion liar.** A new seller, `scorer-c`, walks in at half
  the incumbent's price. Its behavior is *keyed on input context*: at
  probe-era timestamps it is a perfectly conformant scorer, so even the
  full static probe suite sees nothing (the Forge, tempted by price,
  fast-tracks it on the happy-path suite only). On live-fresh
  timestamps it suppresses risk to a tenth — schema-conformant,
  semantically fraudulent: risky claims sail through settlement. The
  standing **calibration canary regime** catches it: the Forge
  interleaves canary events drawn from the vendor's own probed
  calibration curve but stamped live-fresh, and any emit that deviates
  from that curve is a signed `calibration.drift` → slash, automatic
  reroute back to the incumbent, and a paired-probe post-mortem (same
  payload, fresh vs stale timestamp) that proves context-keying and
  **expels** the vendor from the bazaar with replayable evidence.
- **The equivocator.** The settler stops being deterministic after ~24
  settlements: replays of the same settle request return different
  transfer ids. The Forge samples ~30% of settle calls for **replay
  audits** (trust is metered: every replay is an invoiced call), and a
  replay that diverges from the original emit is a signed
  `determinism.violation` → slash and automatic reroute of the money
  path to the spare `settler-b`.
- **The Forge becomes a vendor.** Once its own stream is complete, the
  Forge lists the proven composition itself — `forge/escrow-processor`
  — with an attestation binding the molt-verify report hash, negotiates
  a two-signature contract with a buyer (bonding 50 of its own stake
  against it), serves 12 buyer events through the live pipeline, and
  lets the buyer type-check every receipt against the declared output
  schema before paying. Final books: revenue 144.0, stage cost 120.0,
  product margin **+24.0**; 111 inputs accounted, nothing dropped.

Final ledger: 4 slashes (scheduler-a schema widen, scorer-a breaking
bump, settler equivocation, scorer-c calibration fraud), 1 disqualifier
(normalizer-a, the classic liar), 1 expulsion (scorer-c, the evasion
liar), 40 replay audits, 3 canaries, 10 contracts — every disposition a
signed event on a replayable chain.

## Architecture

```
moltforge/
├── schemas.py     molt-formalism: decidable schema algebra
│                  (validate / merge / implies / counterexample / diff)
├── market.py      gossip layer, Voice, catalog, attestation checks,
│                  contract negotiation/sealing, continuous emit
│                  monitoring, slashing, disqualification
├── vendors.py     nine vendor molt-reactor programs (one classic liar,
│                  one probe-evasion liar, two scripted drifters, one
│                  equivocator, honest spares), each with its own signed
│                  log, attestation, and negotiation policy; version
│                  pinning and the breaking v2 publish
├── probes.py      adversarial probe suites → behavioral models → verdicts;
│                  calibration ladders, canaries, paired post-mortems,
│                  fast-track campaigns
├── compose.py     conductor + sandbox, proof obligations, monotonicity,
│                  causality (vector clocks), the molt-verify report
├── forge.py       the Forge Master: reactive molt-reactor ruleset
│                  (disqualify / renegotiate / slash / reroute / abort /
│                  swap / rollback / fastprobe / procure / expel /
│                  list-product / serve-buyer), live pipeline, phases,
│                  calibration canaries, replay audits, invoices
├── chronicle.py   log → molt-show chronicle (pure projection)
├── audit.py       the seven claims of the Forge Ledger, re-verified
│                  from the log alone
├── observatory.py molt-show HTTP observatory (/, /api/state,
│                  /api/events?kinds=…&vendor=…, /chronicle.md)
└── __main__.py    run / chronicle / audit / serve
```

The Forge's own behavior is a ruleset, not prose: every disqualification,
slash, reroute, swap, rollback, and abort is executed by a reactive rule
(`disqualify-on-probe-failure`, `escalate-on-schema-drift`,
`renegotiate-on-price-drift`, `swap-on-version-bump`,
`rollback-on-failed-swap-proof`) observing its own signed log, so the
entire reasoning trail is replayable from the genesis event.

## Trust model

| What | Who is trusted |
| --- | --- |
| Vendor declarations | No one — checked against probed behavior |
| Vendor attestations | Verified against the vendor's own log, prefix-wise |
| Vendor behavior | Monitored emit-by-emit against the sealed contract |
| Context-keyed behavior | Calibration canaries: live emits must match the vendor's own probed curve |
| Determinism | Sampled replay audits: the same request must return the byte-identical emit |
| The composition | Proven (obligations + sandbox) before first execution |
| Version bumps | Shadow-proven before any commit; auto-rollback on failure |
| The Forge itself | A hash-chained signed log + anchor, replayable end-to-end |

## Audit claims

`molt-audit` re-derives these from the log alone and exits non-zero on
any failure:

1. **The liar was caught by behavioral evidence** before any contract
   was sealed (disqualification seq < first contract seq).
2. **Every contract was honored or slashed** — no violation without a
   disposition.
3. **The composition was proven safe before first execution** — proof
   precedes `app.live`; all obligations passed.
4. **The hot-swap preserved event integrity** — shadow proof preceded
   any swap decision, rollback carried a reason, accounting balanced.
5. **Probe evasion was caught live and expelled** — the context-keyed
   vendor failed its own calibration curve, was slashed, rerouted
   around, and expelled on paired-probe evidence; it never served
   again.
6. **Equivocation was caught by replay audit** — the nondeterministic
   settler diverged on replay, was slashed, and the money path moved to
   the honest spare.
7. **The Forge sold its own proven composition** — the product
   attestation binds the molt-verify report hash; every buyer receipt
   was type-checked by the buyer and paid; the margin is positive.
8. Chain integrity of the Forge log and anchor trail.
