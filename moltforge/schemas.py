"""molt-formalism — the Forge's schema algebra.

A deliberately small, decidable language of event schemas:

    schema := {"type": "object",
               "properties": {name: prop, ...},
               "required": [name, ...],
               "additionalProperties": bool}
    prop   := {"type": "string"|"number"|"integer"|"boolean"}
              + optional "min" / "max"          (numbers)
              + optional "minLength" / "maxLength"  (strings)
              + optional "enum": [v, ...]

Three operations carry the whole proof system:

  validate(obj, schema)        — is this event conformant?
  implies(guarantee, req)      — does every event satisfying `guarantee`
                                 also satisfy `req`? (edge type-checking)
  merge(instances)             — the narrowest schema consistent with a
                                 set of observed events (black-box
                                 behavioral modeling)

`schema_diff` turns a divergence between a declaration and a behavioral
model into human-readable, replayable evidence.
"""
from __future__ import annotations

import hashlib
import json
import math

from moltmesh.core import canonical


def schema_hash(declared: dict) -> str:
    return hashlib.sha256(canonical(declared).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- validate

def _type_ok(v, t) -> bool:
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool) \
            and math.isfinite(v)
    if t == "object":
        return isinstance(v, dict)
    return True


def _prop_errors(v, prop, where: str) -> list[str]:
    errs = []
    t = prop.get("type")
    if t and not _type_ok(v, t):
        return [f"{where}: expected {t}, got {type(v).__name__}"]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "min" in prop and v < prop["min"]:
            errs.append(f"{where}: {v} < min {prop['min']}")
        if "max" in prop and v > prop["max"]:
            errs.append(f"{where}: {v} > max {prop['max']}")
    if isinstance(v, str):
        if "minLength" in prop and len(v) < prop["minLength"]:
            errs.append(f"{where}: length {len(v)} < minLength {prop['minLength']}")
        if "maxLength" in prop and len(v) > prop["maxLength"]:
            errs.append(f"{where}: length {len(v)} > maxLength {prop['maxLength']}")
    if "enum" in prop and v not in prop["enum"]:
        errs.append(f"{where}: {v!r} not in enum {prop['enum']}")
    return errs


def validate(obj, schema: dict, where: str = "$") -> list[str]:
    errs = []
    if schema.get("type") == "object":
        if not isinstance(obj, dict):
            return [f"{where}: expected object"]
        for f in schema.get("required", []):
            if f not in obj:
                errs.append(f"{where}.{f}: required field missing")
        props = schema.get("properties", {})
        for f, v in obj.items():
            if f in props:
                errs += _prop_errors(v, props[f], f"{where}.{f}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{where}.{f}: undeclared field")
    return errs


# ---------------------------------------------------------------- implies

_TYPE_ORDER = {"number": {"number", "integer"}, "integer": {"integer"},
               "string": {"string"}, "boolean": {"boolean"}, "object": {"object"}}

def _type_implies(g_type: str, r_type: str) -> bool:
    return g_type in _TYPE_ORDER.get(r_type, {r_type})


def _prop_implies(g: dict, r: dict) -> bool:
    if r.get("type") and (not g.get("type") or not _type_implies(g["type"], r["type"])):
        return False
    if "min" in r and not (g.get("type") in ("number", "integer")
                           and "min" in g and g["min"] >= r["min"]):
        return False
    if "max" in r and not (g.get("type") in ("number", "integer")
                           and "max" in g and g["max"] <= r["max"]):
        return False
    if "minLength" in r and not ("minLength" in g and g["minLength"] >= r["minLength"]):
        return False
    if "maxLength" in r and not ("maxLength" in g and g["maxLength"] <= r["maxLength"]):
        return False
    if "enum" in r and not (g.get("enum") is not None
                            and set(g["enum"]) <= set(r["enum"])):
        return False
    return True


def implies(guarantee: dict, requirement: dict) -> tuple[bool, list[str]]:
    """True iff every object satisfying `guarantee` satisfies `requirement`.
    Returns (verdict, reasons)."""
    if guarantee.get("type") != "object" or requirement.get("type") != "object":
        return False, ["non-object schemas unsupported"]
    reasons = []
    gp, greq = guarantee.get("properties", {}), set(guarantee.get("required", []))
    rp, rreq = requirement.get("properties", {}), set(requirement.get("required", []))
    for f in sorted(rreq - greq):
        reasons.append(f"requirement needs '{f}' guaranteed-present; "
                       f"guarantee does not require it")
    for f in sorted(rreq & greq):
        if f not in gp or f not in rp:
            reasons.append(f"required field '{f}' lacks a property constraint")
            continue
        if not _prop_implies(gp[f], rp[f]):
            reasons.append(f"guarantee for '{f}' does not imply requirement: "
                           f"guarantee={_brief(gp[f])} requirement={_brief(rp[f])}")
    # optional declared fields: only a problem if requirement forbids extras
    if requirement.get("additionalProperties") is False \
            and guarantee.get("additionalProperties") is not False:
        reasons.append("requirement forbids additional fields; "
                       "guarantee does not")
    return (not reasons), reasons


def _brief(prop: dict) -> str:
    bits = [prop.get("type", "?")]
    for k in ("min", "max", "minLength", "maxLength", "enum"):
        if k in prop:
            bits.append(f"{k}={prop[k]}")
    return ",".join(bits)


# ----------------------------------------------------------------- merge

def merge(instances: list[dict]) -> dict:
    """Behavioral model: narrowest schema consistent with all instances."""
    if not instances:
        return {"type": "object", "properties": {}, "required": [],
                "additionalProperties": False}
    keys = sorted({k for d in instances for k in d})
    props, required = {}, []
    for k in keys:
        vals = [d[k] for d in instances if k in d]
        types = sorted({_stype(v) for v in vals})
        t = types[0] if len(types) == 1 else (
            "number" if set(types) == {"integer", "number"} else _widen(types))
        prop: dict = {}
        if t:
            prop["type"] = t
        if t in ("number", "integer"):
            nums = [v for v in vals if isinstance(v, (int, float))
                    and not isinstance(v, bool)]
            if nums:
                prop["min"], prop["max"] = min(nums), max(nums)
        if t == "string":
            prop["minLength"] = min(len(v) for v in vals)
            prop["maxLength"] = max(len(v) for v in vals)
        distinct = {canonical(v) for v in vals}
        if len(distinct) <= 6 and t in ("string", "integer", "boolean"):
            prop["enum"] = sorted(_plain(vals))
        props[k] = prop
        if all(k in d for d in instances):
            required.append(k)
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


def _stype(v) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (list, dict)):
        return "object" if isinstance(v, dict) else "array"
    return type(v).__name__


def _widen(types: list[str]) -> str:
    return "|".join(types)   # un-satisfiable marker; diff will flag it


def _plain(vals: list) -> list:
    out = []
    for v in vals:
        if v not in out:
            out.append(v)
    return sorted(out, key=lambda x: (str(type(x)), str(x)))


# ------------------------------------------------------------------- diff

def schema_diff(declared: dict, observed: dict) -> list[str]:
    """Human-readable divergence between declaration and behavior."""
    diffs: list[str] = []
    dp, op = declared.get("properties", {}), observed.get("properties", {})
    for f in sorted(set(op) - set(dp)):
        diffs.append(f"observed field '{f}' is not declared")
    for f in sorted(set(dp) & set(op)):
        dprop, oprop = dp[f], op[f]
        dt, ot = dprop.get("type"), oprop.get("type")
        if dt and ot and not _type_implies(ot, dt):
            diffs.append(f"'{f}' declared {dt}, observed {ot} "
                         f"(e.g. {oprop.get('min')}..{oprop.get('max')})")
        if dt in ("number", "integer") and ot in ("number", "integer"):
            if "min" in dprop and "min" in oprop and oprop["min"] < dprop["min"]:
                diffs.append(f"'{f}' observed below declared min: "
                             f"{oprop['min']} < {dprop['min']}")
            if "max" in dprop and "max" in oprop and oprop["max"] > dprop["max"]:
                diffs.append(f"'{f}' observed above declared max: "
                             f"{oprop['max']} > {dprop['max']}")
        if "enum" in dprop and oprop.get("enum") is not None:
            extra = sorted(set(oprop["enum"]) - set(dprop["enum"]),
                           key=str)
            if extra:
                diffs.append(f"'{f}' observed enum values outside declaration: "
                             f"{extra}")
    return diffs



def emit_errors(emit, declared: dict) -> list[str]:
    """Conformance of a full emit envelope against a vendor declaration:
    ok-emits validate `data` against output_schema, error-emits validate
    `error` against error_schema."""
    if not isinstance(emit, dict) or "ok" not in emit:
        return ["emit envelope missing 'ok'"]
    if emit["ok"]:
        return validate(emit.get("data"), declared["output_schema"])
    return validate(emit.get("error"), declared["error_schema"])


def entry_hash(entry: dict) -> str:
    return hashlib.sha256(canonical(entry).encode("utf-8")).hexdigest()


def verify_hex(pub_hex: str, sig_hex: str, message: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex)).verify(
            bytes.fromhex(sig_hex), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def counterexample(guarantee: dict, requirement: dict) -> dict | None:
    """A witness object satisfying `guarantee` but violating `requirement`.

    Constructed from the constraint differences implies() reports: widen a
    required field past its requirement bound, or inject a field the
    requirement forbids. Returns None when no witness is constructible
    from the schema diff alone (conservative: proof stands on reasons)."""
    ok, reasons = implies(guarantee, requirement)
    if ok:
        return None
    gp = guarantee.get("properties", {})
    rp = requirement.get("properties", {})
    witness = {f: _seed(gp[f]) for f in guarantee.get("required", []) if f in gp}
    if requirement.get("additionalProperties") is False \
            and guarantee.get("additionalProperties") is not False:
        for f in sorted(set(rp) - set(gp)):
            if f not in guarantee.get("required", []):
                witness[f] = "injected"
                return witness
    for f in sorted(set(guarantee.get("required", [])) & set(rp)):
        gprop, rprop = gp.get(f, {}), rp.get(f, {})
        if "max" in rprop and "max" in gprop and gprop["max"] > rprop["max"]:
            witness[f] = rprop["max"] + (1 if rprop.get("type") == "integer" else 0.5)
            return witness
        if "min" in rprop and "min" in gprop and gprop["min"] < rprop["min"]:
            witness[f] = rprop["min"] - (1 if rprop.get("type") == "integer" else 0.5)
            return witness
        if "enum" in rprop and gprop.get("enum"):
            outside = [v for v in gprop["enum"] if v not in rprop["enum"]]
            if outside:
                witness[f] = outside[0]
                return witness
        if rprop.get("type") and gprop.get("type") and \
                gprop["type"] != rprop["type"] and gprop["type"] not in rprop["type"]:
            witness[f] = "wrong-type"
            return witness
    return witness


def _seed(prop: dict):
    t = prop.get("type", "string")
    if t == "integer":
        return max(int(prop.get("min", 0)), 1)
    if t == "number":
        return max(float(prop.get("min", 0.0)), 0.5)
    if t == "boolean":
        return False
    return (prop.get("enum") or ["seeded"])[0] if prop.get("enum") else "seeded"


def rename_prop(schema: dict, old: str, new: str) -> dict:
    """Schema surgery for proof experiments (never mutates the input)."""
    out = json.loads(json.dumps(schema))
    props = out.get("properties", {})
    if old in props:
        props[new] = props.pop(old)
    req = [new if r == old else r for r in out.get("required", [])]
    out["required"] = req
    return out
