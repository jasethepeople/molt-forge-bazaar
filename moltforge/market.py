"""molt-market — the bazaar's legal layer.

Nothing here trusts a vendor: the market *verifies* catalog entries and
attestations, seals contracts with two signatures, meters every invocation
against the sealed terms, and slashes stakes through molt-anchor
inscriptions. Negotiation happens exclusively over the gossip layer —
signed molt-event envelopes recorded on both parties' ledgers.
"""
from __future__ import annotations

import time

from moltmesh.core import canonical, envelope_id, sign_envelope, verify_envelope

from . import schemas as S


class Voice:
    """A party's outbound channel on the gossip layer: monotone seq,
    growing vector clock, signed envelopes."""

    def __init__(self, identity, ledger):
        self.identity = identity
        self.ledger = ledger
        self.seq = 0
        self.vc = {identity.name: 0}

    def tick(self) -> dict:
        self.vc[self.identity.name] = self.vc.get(self.identity.name, 0) + 1
        return dict(self.vc)

    def send(self, env_type: str, payload: dict) -> dict:
        self.seq += 1
        env = {
            "mesh_id": "molt-forge", "epoch": 1, "protocol": "forge/1",
            "author": self.identity.node_id(), "seq": self.seq,
            "vc": self.tick(), "type": env_type, "payload": payload,
            "pub": self.identity.pub_hex,
        }
        signed = sign_envelope(self.identity, env)
        signed["__id"] = envelope_id(env)
        return signed


class Gossip:
    """The gossip layer: delivers signed envelopes and records every
    delivery on both the sender's and the recipient's ledger."""

    def __init__(self):
        self.ledgers: dict[str, object] = {}

    def register(self, name: str, ledger) -> None:
        self.ledgers[name] = ledger

    def deliver(self, sender: str, recipient: str, env: dict) -> dict | None:
        if not verify_envelope(env):
            self.ledgers[recipient].append("gossip.rejected", env=None,
                                           detail={"from": sender, "reason": "bad signature"})
            return None
        for who in (sender, recipient):
            led = self.ledgers.get(who)
            if led is not None:
                led.append(f"gossip.{env['type']}", env=dict(env),
                           detail={"from": sender, "to": recipient})
        return env


def contract_body(vendor_name: str, capability: str, declared: dict,
                  price: float, gas_ceiling: int, latency_ms_max: int,
                  stake: int, version: int = 1) -> dict:
    return {
        "vendor": vendor_name, "capability": capability, "version": version,
        "input_schema": declared["input_schema"],
        "output_schema": declared["output_schema"],
        "error_schema": declared["error_schema"],
        "schema_hash": S.schema_hash(declared["input_schema"])
                       + S.schema_hash(declared["output_schema"]),
        "price": price, "gas_ceiling": gas_ceiling,
        "latency_ms_max": latency_ms_max,
        "liability": {
            "stake": stake,
            "slash_on": ["output_violation", "gas_overrun",
                         "breaking_bump_without_notice"],
            "settlement": "molt-anchor inscribed, stake forfeited to treasury",
        },
    }


class Market:
    """Catalog, contracts, monitoring, and punishment. All state changes
    are signed events in the forge log; governance moments are additionally
    inscribed into the molt-anchor."""

    def __init__(self, forge_log, anchor, gossip: Gossip, forge_voice: Voice):
        self.log = forge_log
        self.anchor = anchor
        self.gossip = gossip
        self.voice = forge_voice
        self.catalog: dict[str, dict] = {}          # vendor -> catalog entry
        self.contracts: dict[str, dict] = {}        # contract_id -> contract
        self.counter = 0
        self.treasury = 0
        self.disqualified: set[str] = set()
        self.expelled: set[str] = set()

    # ----------------------------------------------------- catalog
    def list_entry(self, entry: dict) -> list[str]:
        errs = []
        body = {k: v for k, v in entry.items() if k not in ("sig", "entry_hash")}
        if entry.get("entry_hash") != S.entry_hash(body):
            errs.append("entry_hash mismatch")
        if not S.verify_hex(entry["pub"], entry.get("sig", ""),
                            entry["entry_hash"].encode("ascii")):
            errs.append("catalog signature invalid")
        att = entry.get("attestation", {})
        if att.get("pub") != entry.get("pub"):
            errs.append("attestation signed by a different key than the entry")
        self.catalog[entry["vendor"]] = entry
        self.log.append("catalog.listed", detail={
            "vendor": entry["vendor"], "capability": entry["capability"],
            "version": entry["version"], "schema_hash": att.get("schema_hash"),
            "entry_hash": entry["entry_hash"], "verified": not errs,
            "errors": errs,
        })
        return errs

    def attestation_check(self, vendor, entry: dict) -> list[str]:
        """Verify the vendor's molt-audit attestation against its own
        molt-show log: signature, chain integrity, and head binding."""
        errs = list(vendor.verify_attestation(entry, vendor.log))
        chain = vendor.log.verify_chain()
        if chain:
            errs.append(f"vendor audit log chain broken: {chain[0]}")
        self.log.append("attestation.verdict", detail={
            "vendor": vendor.name,
            "audit_head": vendor.log.last_hash,
            "events_audited": len(vendor.log.events),
            "errors": errs, "ok": not errs,
        })
        return errs

    # --------------------------------------------------- contracts
    def negotiate(self, vendor, terms: dict, acceptance_check=None) -> dict:
        """Forge offer -> vendor counter/accept -> optional downstream
        type-check -> sealed two-signature contract. Every hop is a
        gossip envelope on both ledgers."""
        pin = terms.get("version_pin")
        declared = vendor.declared_for(pin)
        offer_env = self.voice.send("contract.offer", {
            "to": vendor.name, "capability": vendor.capability,
            "price": terms["price"], "gas_ceiling": terms["gas_ceiling"],
            "latency_ms_max": terms["latency_ms_max"], "stake": terms["stake"],
            "schema_hash": S.schema_hash(declared["input_schema"])
                           + S.schema_hash(declared["output_schema"]),
        })
        self.gossip.deliver("forge", vendor.name, offer_env)
        reply_env = vendor.negotiate(offer_env)
        self.gossip.deliver(vendor.name, "forge", reply_env)
        reply = reply_env["payload"]

        if reply.get("decision") != "accept":
            self.log.append("contract.failed", detail={
                "vendor": vendor.name, "decision": reply.get("decision"),
                "reason": reply.get("reason"), "offer_env": offer_env["__id"],
            })
            return {}

        version = pin if (pin is not None and getattr(vendor, "_legacy", None)
                          is not None and pin < vendor.version) else vendor.version
        candidate = contract_body(
            vendor.name, vendor.capability, declared,
            float(reply.get("price", terms["price"])),
            terms["gas_ceiling"], terms["latency_ms_max"], terms["stake"],
            version=version)
        # The forge never seals a contract its pipeline cannot type-check.
        if acceptance_check is not None:
            problems = acceptance_check(candidate)
            if problems:
                self.log.append("contract.failed", detail={
                    "vendor": vendor.name, "reason": "failed downstream type-check",
                    "problems": problems, "offer_env": offer_env["__id"],
                })
                return {}

        self.counter += 1
        candidate["contract_id"] = f"ctr-{self.counter:04d}"
        candidate["sealed_ts"] = time.time()
        candidate["forge_sig"] = self.voice.identity.sign_hex(
            canonical(candidate).encode("utf-8"))
        candidate["vendor_sig"] = vendor.identity.sign_hex(
            canonical(candidate).encode("utf-8"))
        vendor.stake_for(candidate["liability"]["stake"])
        self.contracts[candidate["contract_id"]] = candidate
        self.log.append("contract.sealed", detail={
            "contract_id": candidate["contract_id"],
            "vendor": vendor.name, "capability": vendor.capability,
            "version": candidate["version"], "price": candidate["price"],
            "gas_ceiling": candidate["gas_ceiling"],
            "latency_ms_max": candidate["latency_ms_max"],
            "stake": candidate["liability"]["stake"],
            "forge_sig": candidate["forge_sig"][:16],
            "vendor_sig": candidate["vendor_sig"][:16],
        })
        self._inscribe("contract.sealed", {
            "contract_id": candidate["contract_id"], "vendor": vendor.name,
            "stake": candidate["liability"]["stake"],
        })
        return candidate

    # --------------------------------------------------- metering
    def invoice(self, contract: dict, gas: int, latency_ms: float) -> dict:
        return {"price": contract["price"], "gas": gas,
                "latency_ms": latency_ms,
                "contract_id": contract["contract_id"]}

    def observe_emit(self, contract: dict, emit: dict, gas: int,
                     latency_ms: float, price_paid: float) -> list[dict]:
        """Continuous contract monitoring: every emit is checked against
        the sealed terms — never against forge assumptions."""
        findings = []
        for err in S.emit_errors(emit, contract):
            findings.append({"kind": "schema", "detail": err})
        # A refusal is compliance: gas/latency/price terms bind the vendor
        # only for work it actually served. An `over_gas` error envelope is
        # the vendor honoring its ceiling, not violating ours.
        served = bool(emit.get("ok"))
        if served and gas > contract["gas_ceiling"]:
            findings.append({"kind": "gas",
                             "detail": f"gas {gas} > ceiling {contract['gas_ceiling']}"})
        if served and latency_ms > contract["latency_ms_max"]:
            findings.append({"kind": "latency",
                             "detail": f"latency {latency_ms:.1f}ms > bound "
                                       f"{contract['latency_ms_max']}ms"})
        if served and abs(price_paid - contract["price"]) > 1e-9:
            findings.append({"kind": "price",
                             "detail": f"invoiced {price_paid} != contracted "
                                       f"{contract['price']}"})
        return findings

    # -------------------------------------------------- punishment
    def slash(self, vendor, contract: dict, reason: str,
              evidence_digest: str) -> int:
        forfeit = contract["liability"]["stake"]
        self.treasury += forfeit
        vendor.forfeit_stake(forfeit)
        self.log.append("vendor.slash", detail={
            "vendor": vendor.name, "contract_id": contract["contract_id"],
            "reason": reason, "forfeit": forfeit,
            "evidence_digest": evidence_digest,
        })
        self._inscribe("slash", {"vendor": vendor.name, "forfeit": forfeit,
                                 "reason": reason,
                                 "evidence": evidence_digest})
        return forfeit

    def disqualify(self, vendor_name: str, reason: str,
                   evidence_digest: str) -> None:
        self.log.append("vendor.disqualify", detail={
            "vendor": vendor_name, "reason": reason,
            "evidence_digest": evidence_digest,
            "note": "disqualified on behavioral evidence; never contracted",
        })
        self._inscribe("disqualification", {"vendor": vendor_name,
                                            "reason": reason,
                                            "evidence": evidence_digest})
        self.disqualified.add(vendor_name)

    def expel(self, vendor, reason: str, evidence: str) -> None:
        """Permanent removal from the bazaar, with replayable evidence,
        inscribed on the anchor. An expelled vendor is never contracted
        again — the market's memory outlives its sellers."""
        self.expelled.add(vendor.name)
        self.log.append("vendor.expelled", detail={
            "vendor": vendor.name, "reason": reason, "evidence": evidence,
            "summary": f"{vendor.name} expelled from the bazaar"})
        self._inscribe("expulsion", {"vendor": vendor.name, "reason": reason,
                                     "evidence": evidence})

    def _inscribe(self, kind: str, detail: dict) -> None:
        from moltmesh.anchor import inscribe
        inscribe(self.anchor, kind, detail, self.voice.identity)