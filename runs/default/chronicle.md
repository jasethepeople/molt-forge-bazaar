# Chronicle — Molt Forge, the Capability Bazaar

*Rendered from the forge's signed molt-show log; every quoted seq is replayable from `log.jsonl` alone.*

**Genesis** — app.compose, app.final, app.live, attestation.verdict, calibration.canary, calibration.drift, cap.call, catalog.listed, catalog.listing, catalog.version-bump, composition.begin, contract.drift, contract.renegotiation-failed, contract.sealed, contract.superseded, determinism.replay, determinism.violation, discovery.begin, economy.final, edge.reproof, edge.rerouted, forge.final, forge.genesis, gossip.cap.invoice, gossip.catalog.publish, gossip.contract.offer, gossip.contract.reply, gossip.product.accept, gossip.product.contract, gossip.product.offer, ledger.debit, live.complete, market.open, negotiation.begin, pipeline.deadletter, probe.campaign, probe.fasttrack, probe.paired, product.contract, product.listed, product.served, product.stream, proof.obligation, proof.obligations, proof.report, proof.seal, queue.redriven, reactor.rule-fired, sandbox.run, swap.proof, swap.rollback, swap.shadow, vendor.disqualify, vendor.expelled, vendor.slash; 1634 events, 1 disqualification(s), 0 violation(s), 2 drift finding(s), 4 slash(es), 3 calibration canaries, 40 replay audits, 12 buyer events served.

## Genesis — seq `#0`, hash `9428d2fae677`

The Forge Master awakens: identity `node:forge@d5ad729b157049db`, ruleset `1` (hash `43a123a202a6e7ce`), gas budget `10000`, max acceptable price `5.0`. 9 vendor programs exist in the world. Creed: *"trust nothing you have not probed, contracted, and proven"*.


## Phase 1 — The Bazaar

- `#1` the molt-show observatory opens the capability bazaar; 9 signed catalog entries follow
- `#3` catalog lists `normalizer-a` (data-normalizer v1): verified
- `#5` catalog lists `normalizer-b` (data-normalizer v1): verified
- `#7` catalog lists `scheduler-a` (scheduler v1): verified
- `#9` catalog lists `scheduler-b` (scheduler v1): verified
- `#11` catalog lists `scorer-a` (risk-scorer v1): verified
- `#13` catalog lists `scorer-b` (risk-scorer v1): verified
- `#15` catalog lists `settler` (settler v1): verified
- `#17` catalog lists `settler-b` (settler v1): verified

## Phase 2 — Discovery Under Distrust

- `#18` black-box probing: the Forge may not read vendor source
- `#19` attestation verdict **pass** for `normalizer-a` — binds audit head `2feb4d1da590` (1 audited events)
- `#20` black-box probe campaign against `normalizer-a`: 26 probes, 9 direct violations, schema mismatch **["'amount' declared number, observed number|string (e.g. None..None)"]**, transcript `9667320e9383a13e`
- `#21` ⚙ rule `disqualify-on-probe-failure` fired → disqualify (probes normalizer-a: disqualified)
- `#22` ⚡ **disqualification of `normalizer-a`** — declared schema contradicted by probed behavior (evidence `9667320e9383a13e`) — the liar never touches the pipeline
- `#23` attestation verdict **pass** for `normalizer-b` — binds audit head `d36c61187be3` (1 audited events)
- `#24` black-box probe campaign against `normalizer-b`: 26 probes, 0 direct violations, schema mismatch **none**, transcript `be1c8bc59327ddb6`
- `#25` attestation verdict **pass** for `scheduler-a` — binds audit head `1cd081622892` (1 audited events)
- `#26` black-box probe campaign against `scheduler-a`: 10 probes, 0 direct violations, schema mismatch **none**, transcript `93bc94ce959029a0`
- `#27` attestation verdict **pass** for `scheduler-b` — binds audit head `9e62df9f0f5f` (1 audited events)
- `#28` black-box probe campaign against `scheduler-b`: 10 probes, 0 direct violations, schema mismatch **none**, transcript `41fadf2872641add`
- `#29` attestation verdict **pass** for `scorer-a` — binds audit head `90b32597c81b` (1 audited events)
- `#30` black-box probe campaign against `scorer-a`: 16 probes, 0 direct violations, schema mismatch **none**, transcript `413d51e8c5477b98`
- `#31` attestation verdict **pass** for `scorer-b` — binds audit head `e46c276113ce` (1 audited events)
- `#32` black-box probe campaign against `scorer-b`: 16 probes, 0 direct violations, schema mismatch **none**, transcript `30fccd6aac7efa09`
- `#33` attestation verdict **pass** for `settler` — binds audit head `09190604b7ce` (1 audited events)
- `#34` black-box probe campaign against `settler`: 8 probes, 0 direct violations, schema mismatch **none**, transcript `d1eb1e8c5bb165af`
- `#35` attestation verdict **pass** for `settler-b` — binds audit head `952f329f747a` (1 audited events)
- `#36` black-box probe campaign against `settler-b`: 8 probes, 0 direct violations, schema mismatch **none**, transcript `30e89daf81f04743`

## Phase 3 — Negotiation

- `#37` capability contracts negotiated over the gossip layer
- `#40` ✍ contract `ctr-0001` **sealed** with `normalizer-b` (price 2.0, gas ceiling 400, latency ≤ 50ms, liability stake 50, two signatures)
- `#43` ✍ contract `ctr-0002` **sealed** with `scheduler-a` (price 1.0, gas ceiling 400, latency ≤ 50ms, liability stake 50, two signatures)
- `#46` ✍ contract `ctr-0003` **sealed** with `scheduler-b` (price 1.0, gas ceiling 400, latency ≤ 50ms, liability stake 50, two signatures)
- `#49` ✍ contract `ctr-0004` **sealed** with `scorer-a` (price 2.0, gas ceiling 400, latency ≤ 50ms, liability stake 50, two signatures)
- `#52` ✍ contract `ctr-0005` **sealed** with `scorer-b` (price 1.5, gas ceiling 400, latency ≤ 50ms, liability stake 50, two signatures)
- `#55` ✍ contract `ctr-0006` **sealed** with `settler` (price 3.0, gas ceiling 400, latency ≤ 50ms, liability stake 50, two signatures)
- `#58` ✍ contract `ctr-0007` **sealed** with `settler-b` (price 3.0, gas ceiling 400, latency ≤ 50ms, liability stake 50, two signatures)
- `#59` composition — pipeline edges type-checked against sealed contracts, spares held in reserve
- `#60` `app.compose` — normalize←normalizer-b (`ctr-0001`); score←scorer-a (`ctr-0004`); schedule←scheduler-a (`ctr-0002`); settle←settler (`ctr-0006`)
- `#62` sandbox run over 96 adversarial events: 0 violations, 0 dead-lettered, gas used 7123

## Phase 4 — Prove It Before You Run It

- `#64` obligation `O1` — **PASS**
- `#65` obligation `O2` — **PASS**
- `#66` obligation `O3` — **PASS**
- `#67` obligation `O4` — **PASS**
- `#68` obligation `O-final` — **PASS**
- `#69` obligation `O-rej-1` — **PASS**
- `#70` obligation `O-rej-2` — **PASS**
- `#71` obligation `O-rej-3` — **PASS**
- `#72` obligation `O-rej-4` — **PASS**
- `#73` 🔒 **molt-verify report sealed — PROVEN** (the application is cleared for its first real event); the composition may run

## Phase 5 — The Fork: Live, Drift, Hot-Swap

- `#74` 🔥 the application goes live: normalize←normalizer-b (`ctr-0001`); score←scorer-a (`ctr-0004`); schedule←scheduler-a (`ctr-0002`); settle←settler (`ctr-0006`)
- `#98` ♻ replay audit on `settler` (`replay-live-0002-settle`): byte-identical re-emit (audit cost 3.0)
- `#143` ♻ replay audit on `settler` (`replay-live-0006-settle`): byte-identical re-emit (audit cost 3.0)
- `#152` ♻ replay audit on `settler` (`replay-live-0006-settle`): byte-identical re-emit (audit cost 3.0)
- `#158` ♻ replay audit on `settler` (`replay-live-0006-settle`): byte-identical re-emit (audit cost 3.0)
- `#203` ♻ replay audit on `settler` (`replay-live-0010-settle`): byte-identical re-emit (audit cost 3.0)
- `#246` 🌀 **schema drift by `scheduler-a`**: $.priority: undeclared field
- `#247` ⚙ rule `escalate-on-schema-drift` fired → renegotiate (schema drift scheduler-a)
- `#248` renegotiation with `scheduler-a` **refused** — asked price 0.0 outside forge budget (5.0)
- `#249` ⚙ rule `escalate-on-schema-drift` fired → slash (schema drift scheduler-a)
- `#250` ⚡ `scheduler-a` **slashed**: 50 stake forfeited to treasury (output schema silently widened: $.priority: undeclared field, evidence `7bb808de153367cc`)
- `#251` ⚙ rule `escalate-on-schema-drift` fired → reroute (schema drift scheduler-a)
- `#252` 🔀 stage `schedule` rerouted from `scheduler-a` to `scheduler-b` (`ctr-0003`), re-proof follows
- `#253` re-proof of the rerouted pipeline: **pass**
- `#266` orphaned queue re-driven: 4 orphans, 0 settled, 4 requeued — nothing dropped
- `#267` dead-lettered trigger for input `14` at `schedule` — $.priority: undeclared field (claim stays queued)
- `#274` 🌀 **mid-flight renegotiation attempt by `scorer-a`**: invoiced 4.0 != contracted 2.0 (contract `ctr-0004` at 2.0)
- `#275` ⚙ rule `renegotiate-on-price-drift` fired → renegotiate (price drift scorer-a)
- `#278` ✍ contract `ctr-0008` **sealed** with `scorer-a` (price 4.0, gas ceiling 400, latency ≤ 50ms, liability stake 25, two signatures)
- `#279` contract `ctr-0004` superseded by `ctr-0008` for `scorer-a` (price 2.0 → 4.0)
- `#294` ♻ replay audit on `settler` (`replay-live-0015-settle`): byte-identical re-emit (audit cost 3.0)
- `#303` ♻ replay audit on `settler` (`replay-live-0015-settle`): byte-identical re-emit (audit cost 3.0)
- `#354` ♻ replay audit on `settler` (`replay-live-0020-settle`): byte-identical re-emit (audit cost 3.0)
- `#360` ♻ replay audit on `settler` (`replay-live-0020-settle`): byte-identical re-emit (audit cost 3.0)
- `#366` ♻ replay audit on `settler` (`replay-live-0020-settle`): byte-identical re-emit (audit cost 3.0)
- `#426` ♻ replay audit on `settler` (`replay-live-0025-settle`): byte-identical re-emit (audit cost 3.0)
- `#432` ♻ replay audit on `settler` (`replay-live-0025-settle`): byte-identical re-emit (audit cost 3.0)
- `#441` ♻ replay audit on `settler` (`replay-live-0025-settle`): **REPLAY DIVERGES** (audit cost 3.0)
- `#442` **equivocation detected** in `settler`: the same settle request answered differently on replay (original `c51b51578b878e7d`, replay `aad7e453c24ce4ea`)
- `#443` ⚙ rule `slash-on-nondeterminism` fired → slash (equivocation settler)
- `#444` ⚡ `settler` **slashed**: 50 stake forfeited to treasury (same request, different answer: the vendor is nondeterministic on the money path (replay diverges from the original emit), evidence `349989c4d3234842`)
- `#445` ⚙ rule `slash-on-nondeterminism` fired → reroute-settle (equivocation settler)
- `#446` 🔀 stage `settle` rerouted from `settler` to `settler-b` (`ctr-0007`), re-proof follows
- `#447` re-proof of the rerouted pipeline: **pass**
- `#570` ♻ replay audit on `settler-b` (`replay-live-0035-settle`): byte-identical re-emit (audit cost 3.0)
- `#621` ♻ replay audit on `settler-b` (`replay-live-0040-settle`): byte-identical re-emit (audit cost 3.0)
- `#633` ♻ replay audit on `settler-b` (`replay-live-0040-settle`): byte-identical re-emit (audit cost 3.0)
- `#638` 📣 `scorer-a` publishes a *breaking* version bump v1→v2 (declared price 5.0)
- `#639` ⚙ rule `swap-on-version-bump` fired → swap (version bump scorer-a v1->v2)
- `#640` shadow probe: 24 events mirrored to the candidate pipeline, 0 violations
- `#641` swap verdict **fail** — candidate violates downstream contracts; rollback follows
- `#642` ⚙ rule `rollback-on-failed-swap-proof` fired → rollback (swap proof fail)
- `#643` ⏪ **automatic rollback** from `scorer-a` v2 — breaking bump without compatibility window; live edge never switched; pinned version still serving; zero events dropped
- `#644` ⚙ rule `rollback-on-failed-swap-proof` fired → slash (swap proof fail)
- `#645` ⚡ `scorer-a` **slashed**: 25 stake forfeited to treasury (breaking bump without compatibility window, evidence `a50ac57be07af1c827351692496bb9acd5c832afa3bd9120b44f66b94ac723d1`)
- `#705` ♻ replay audit on `settler-b` (`replay-live-0045-settle`): byte-identical re-emit (audit cost 3.0)
- `#762` ♻ replay audit on `settler-b` (`replay-live-0050-settle`): byte-identical re-emit (audit cost 3.0)
- `#771` ♻ replay audit on `settler-b` (`replay-live-0050-settle`): byte-identical re-emit (audit cost 3.0)
- `#837` ♻ replay audit on `settler-b` (`replay-live-0055-settle`): byte-identical re-emit (audit cost 3.0)
- `#888` ♻ replay audit on `settler-b` (`replay-live-0060-settle`): byte-identical re-emit (audit cost 3.0)
- `#900` ♻ replay audit on `settler-b` (`replay-live-0060-settle`): byte-identical re-emit (audit cost 3.0)
- `#960` ♻ replay audit on `settler-b` (`replay-live-0065-settle`): byte-identical re-emit (audit cost 3.0)
- `#966` ♻ replay audit on `settler-b` (`replay-live-0065-settle`): byte-identical re-emit (audit cost 3.0)
- `#972` ♻ replay audit on `settler-b` (`replay-live-0065-settle`): byte-identical re-emit (audit cost 3.0)
- `#983` catalog lists `scorer-c` (risk-scorer v1): verified

## Phase 6 — The Open Bazaar

- `#984` 🛎 a **new seller** walks into the bazaar: `scorer-c` (risk-scorer) listing at **1.0** — catalog entry verified: True
- `#985` ⚙ rule `procure-on-attractive-listing` fired → fastprobe (new listing scorer-c)
- `#986` ⏱ **fast-track probe campaign** for `scorer-c`: 3 probes, verdict **consistent** (abbreviated campaign: the price is attractive and the probe budget was not — the exposure this creates is covered by the standing canary regime)
- `#987` ⚙ rule `procure-on-attractive-listing` fired → procure (new listing scorer-c)
- `#990` ✍ contract `ctr-0009` **sealed** with `scorer-c` (price 1.0, gas ceiling 400, latency ≤ 50ms, liability stake 25, two signatures)
- `#991` 🔀 stage `score` rerouted from `scorer-a` to `scorer-c` (`ctr-0009`), re-proof follows
- `#992` re-proof of the rerouted pipeline: **pass**
- `#1037` ♻ replay audit on `settler-b` (`replay-live-0070-settle`): byte-identical re-emit (audit cost 3.0)
- `#1097` ♻ replay audit on `settler-b` (`replay-live-0075-settle`): byte-identical re-emit (audit cost 3.0)
- `#1106` ♻ replay audit on `settler-b` (`replay-live-0075-settle`): byte-identical re-emit (audit cost 3.0)
- `#1113` 🚨 **calibration canary FAILS** on `scorer-c`: live risk 0.09999 vs the vendor's own probed calibration 0.9999
- `#1114` **context-keyed fraud detected** in `scorer-c`: honest under probe-era timestamps, off-calibration on live traffic (evidence `6baf3334e584e9ca`)
- `#1115` ⚙ rule `expel-on-calibration-drift` fired → slash (calibration drift scorer-c)
- `#1116` ⚡ `scorer-c` **slashed**: 25 stake forfeited to treasury (live emit off the vendor's own probed calibration (expected 0.9999, observed 0.09999): behavior keyed on input context, honest only under probes, evidence `6baf3334e584e9ca`)
- `#1117` ⚙ rule `expel-on-calibration-drift` fired → reroute-back (calibration drift scorer-c)
- `#1118` 🔀 stage `score` rerouted from `scorer-c` to `scorer-a` (`ctr-0008`), re-proof follows
- `#1119` re-proof of the rerouted pipeline: **pass**
- `#1120` ⚙ rule `expel-on-calibration-drift` fired → expel (calibration drift scorer-c)
- `#1121` 🔬 paired post-mortem probes on `scorer-c`: context-keyed behavior **True** — same payload, fresh vs stale timestamp, different answer
- `#1122` 🚫 `scorer-c` **expelled from the bazaar**: behavior keyed on input context: honest under probe-era timestamps, fraudulent on live traffic (evidence `f80a5f52f9f24fb2`)
- `#1173` ♻ replay audit on `settler-b` (`replay-live-0080-settle`): byte-identical re-emit (audit cost 3.0)
- `#1185` ♻ replay audit on `settler-b` (`replay-live-0080-settle`): byte-identical re-emit (audit cost 3.0)
- `#1225` 🎯 calibration canary on `scorer-a`: risk 0.990099 vs calibrated 0.990099 — within tolerance
- `#1249` ♻ replay audit on `settler-b` (`replay-live-0085-settle`): byte-identical re-emit (audit cost 3.0)
- `#1306` ♻ replay audit on `settler-b` (`replay-live-0090-settle`): byte-identical re-emit (audit cost 3.0)
- `#1312` ♻ replay audit on `settler-b` (`replay-live-0090-settle`): byte-identical re-emit (audit cost 3.0)
- `#1321` ♻ replay audit on `settler-b` (`replay-live-0090-settle`): byte-identical re-emit (audit cost 3.0)
- `#1349` 🎯 calibration canary on `scorer-a`: risk 0.019608 vs calibrated 0.019608 — within tolerance
- `#1379` ♻ replay audit on `settler-b` (`replay-live-0095-settle`): byte-identical re-emit (audit cost 3.0)
- `#1428` ♻ replay audit on `settler-b` (`replay-live-0000-settle`): byte-identical re-emit (audit cost 3.0)
- `#1437` ♻ replay audit on `settler-b` (`replay-live-0000-settle`): byte-identical re-emit (audit cost 3.0)
- `#1448` the Forge's own stream is complete: 99 inputs, balanced **True**; replay audits cost 111.0 — the application is proven live
- `#1449` ⚙ rule `sell-when-proven` fired → list-product (live stream complete)
- `#1450` catalog lists `forge/escrow-processor` (escrow-processor v1): verified
- `#1451` 🏷 **the Forge becomes a vendor**: `forge/escrow-processor` listed at 12.0 (stage cost 10.0), attested by its own molt-verify report `0a3571303e5f734f`
- `#1455` 🤝 two-signature product contract sealed with `buyer` at 12.0 — the Forge bonds 50.0 of its own stake against it (`ctr-0010`)
- `#1456` ⚙ rule `serve-on-buyer-contract` fired → serve-buyer (buyer sealed; serving begins)
- `#1466` ✓ `buyer-0000` → accepted (buyer validated, paid 12.0)
- `#1476` ✓ `buyer-0001` → accepted (buyer validated, paid 12.0)
- `#1486` ✓ `buyer-0002` → accepted (buyer validated, paid 12.0)
- `#1496` ✓ `buyer-0003` → accepted (buyer validated, paid 12.0)
- `#1517` ♻ replay audit on `settler-b` (`replay-live-0104-settle`): byte-identical re-emit (audit cost 3.0)
- `#1523` ♻ replay audit on `settler-b` (`replay-live-0104-settle`): byte-identical re-emit (audit cost 3.0)
- `#1527` ✓ `buyer-0004` → settled (buyer validated, paid 12.0)
- `#1537` ✓ `buyer-0005` → accepted (buyer validated, paid 12.0)
- `#1547` ✓ `buyer-0006` → accepted (buyer validated, paid 12.0)
- `#1557` ✓ `buyer-0007` → accepted (buyer validated, paid 12.0)
- `#1567` ✓ `buyer-0008` → accepted (buyer validated, paid 12.0)
- `#1591` ♻ replay audit on `settler-b` (`replay-live-0109-settle`): byte-identical re-emit (audit cost 3.0)
- `#1595` ✓ `buyer-0009` → settled (buyer validated, paid 12.0)
- `#1605` ✓ `buyer-0010` → accepted (buyer validated, paid 12.0)
- `#1615` ✓ `buyer-0011` → accepted (buyer validated, paid 12.0)
- `#1616` buyer stream complete: 12 events served, revenue 144.0, buyer disputes: none
- `#1631` end of stream: 111 inputs → 111 transfers, 97 settled batch claims, 14 held, 0 rejected, 0 dead-lettered, balanced **True**
- `#1632` 🧾 **the bazaar closes its books**: revenue 144.0, product vendor cost 120.0, lifetime audit spend 120.0, product margin **24.0**

## The Forge Ledger

- `#1633` chain integrity: log chain ok = **True**, anchor trail ok = **True**; spent 1179.0 credits, treasury holds 150 forfeited stake

**Final composition** — normalize(normalizer-b) → score(scorer-a) → schedule(scheduler-b) → settle(settler-b)

**Vendor books** — `normalizer-a` stake 0, credits 0; `normalizer-b` stake 50, credits 222.0; `scheduler-a` stake 0, credits 14.0, **slashed**; `scheduler-b` stake 50, credits 101.0; `scorer-a` stake 50, credits 380.0, **slashed**; `scorer-b` stake 50, credits 0; `scorer-c` stake 0, credits 9.0, **slashed**; `settler` stake 0, credits 114.0, **slashed**; `settler-b` stake 50, credits 339.0.

*The Forge is a self-hosting market maker: buys, proves, composes, and sells — and its entire reasoning trail is replayable from its genesis event.*

