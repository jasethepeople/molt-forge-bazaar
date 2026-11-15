# AGENTS.md — Molt Forge

Project-specific implementation rules for `Projects/molt-forge/`.

## What this is

A self-hosting capability market built on the molt-stack primitives
(`Projects/molt-mesh/`): the Forge Master composes untrusted vendor
molt-reactor programs into a proven pipeline. Read `README.md` for the
phase walkthrough and run artifacts.

## Non-negotiable invariants

- **The Forge never reads vendor source.** All vendor knowledge must
  come from probe transcripts, attestations, catalog entries, and gossip
  envelopes. Any check that needs `vendor.behavior` directly (outside
  `invoke`) is a design bug.
- **A failed proof must never run.** `phase4_proof` gates `phase5_live`;
  the failure path is a signed `app.abort`, never an exception.
- **No event may be dropped.** Every raw feed must be accounted:
  transfers + rejected + deadletter == inputs, pending drained. The
  reroute and swap paths must re-drive or requeue orphans. Canary and
  buyer feeds go through the same accounting.
- **Everything is an event.** Disqualifications, slashes, reroutes,
  swaps, rollbacks, aborts — each is a signed log event with an evidence
  digest, executed by a reactor rule, and governance moments are
  inscribed on the anchor.
- **Phase 6 trust regime is part of the contract.** A new listing is
  fast-tracked (standard suite only) — the exposure is deliberate and
  covered by the standing calibration canary regime and sampled replay
  audits; a canary mismatch or replay divergence must end in slash +
  reroute (and expulsion with paired-probe evidence for the former),
  never in a silent acceptance.
- **Proofs cover edges, not vibes.** Every pipeline edge is
  type-checked against sealed contracts via `schemas.implies`, with the
  projection/join recorded in the obligation. The trigger rename
  (`at`→`trigger_ts`) and the settle join are part of the proof.

## File conventions

- `runs/<dir>/` holds all artifacts of a run; `keys.json` is 0600 and
  must never be committed, moved, or published.
- Ledger entries use `kind` (moltmesh Ledger convention) — never `type`.
- Canonical JSON everywhere (`moltmesh.core.canonical`); digests are
  `sha256(canonical(...))[:16]`.
- The chronicle and audit are pure projections of `log.jsonl` — if you
  add an event kind, extend both renderers and the test suite.

## Testing

`python3 tests/test_forge.py` from the project root (63 checks, exit
code non-zero on failure). It covers the schema algebra, the liar
detection, attestation prefix verification, the full six-phase run,
the poison-proof abort path, the reroute/queue-integrity path, and the
phase-6 open-bazaar invariants (canary catches the evasion liar,
replay audit catches the equivocator, the Forge's own product sells
with a proof-bound attestation, everything accounted).
Run it after any change to `schemas.py`, `compose.py`, `forge.py`,
`market.py`, or `probes.py`.

## Phase 6 operational gotchas

- `scorer-c` (probe-evasion liar) must NOT be listed in Phase 1 — it
  arrives mid-run; phase2/phase3 skip vendors without a catalog entry.
- `contract_for` returns the LAST-sealed contract for a vendor.
  `market.negotiate` honors `version_pin` and must seal the schema of
  the version actually served (`vendor.declared_for(pin)`), never a
  post-bump declaration with a v1 pin.
- Trust is metered: replay audits and canaries bill real invoices
  (`forge.audit_spend`); the buyer stream bills the buyer
  (`forge.earned`, margin in `economy.final`).
- `python3 -m moltforge run --dir runs/default` APPENDS to an existing
  `log.jsonl` (event-sourced). Delete the run dir for a clean run.
