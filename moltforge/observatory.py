"""molt-forge observatory — the bazaar's public molt-show.

A small read-only HTTP surface over the run artifacts: catalog,
contracts, the proof report, the Forge ledger, and the audit claims.
Serves JSON for machines and one HTML dashboard for humans. It renders
state; it never mutates anything.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def load_state(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    state: dict = {"run_dir": str(run_dir)}
    for name, path in (("final_state", "final-state.json"),
                       ("catalog", "catalog.json"),
                       ("contracts", "contracts.json"),
                       ("anchor", "anchor.json"),
                       ("proof_report", "proof-report.json"),
                       ("audit_report", "audit-report.json")):
        p = run_dir / path
        state[name] = json.loads(p.read_text()) if p.exists() else None
    log_path = run_dir / "log.jsonl"
    state["events"] = [json.loads(line) for line in
                       log_path.read_text().splitlines() if line.strip()] \
        if log_path.exists() else []
    state["event_count"] = len(state["events"])
    state["kinds"] = sorted({e["kind"] for e in state["events"]})
    econ = next((e["detail"] for e in reversed(state["events"])
                 if e["kind"] == "economy.final"), None)
    state["economy"] = econ
    state["product"] = (next(
        ({"id": e["detail"].get("product"),
          "price": e["detail"].get("price"),
          "proof_sha": e["detail"].get("proof_sha")}
         for e in reversed(state["events"])
         if e["kind"] == "product.listed"), None))
    state["replay_audits"] = sum(
        1 for e in state["events"] if e["kind"] == "determinism.replay")
    try:
        from .audit import audit
        state["audit"] = audit(run_dir)
    except Exception as exc:                       # pragma: no cover
        state["audit"] = {"ok": None, "findings": [f"audit unavailable: {exc}"]}
    return state


def _pipeline_rows(final_state: dict) -> str:
    rows = ""
    for p in (final_state or {}).get("pipeline", []):
        rows += (f"<tr><td>{p['stage']}</td><td>{p['vendor']}</td>"
                 f"<td><code>{p['contract_id']}</code></td>"
                 f"<td>{p['price']}</td></tr>")
    return rows or "<tr><td colspan=4>—</td></tr>"


def _vendor_rows(final_state: dict) -> str:
    rows = ""
    for n, s in sorted(((final_state or {}).get("vendors", {})).items()):
        badge = " ⚡slashed" if s["slashed"] else ""
        rows += (f"<tr><td>{n}</td><td>{s['stake']}</td>"
                 f"<td>{s['credits']}</td><td>{s['live_calls']}{badge}</td></tr>")
    return rows or "<tr><td colspan=4>—</td></tr>"


def _dashboard(state: dict) -> str:
    fs, pr = state["final_state"] or {}, state["proof_report"] or {}
    audit = state.get("audit") or {}
    verdict = pr.get("verdict", "?")
    acc = fs.get("accounting", {}) or {}
    findings = audit.get("findings", [])
    audit_line = ("✅ all claims verified" if audit.get("ok")
                  else ("⚠️ " + "; ".join(findings) if findings else "—"))
    events = state["events"]
    drift = [e for e in events if e["kind"] == "contract.drift"]
    slashes = [e for e in events if e["kind"] == "vendor.slash"]
    dq = [e for e in events if e["kind"] == "vendor.disqualify"]
    exp = [e for e in events if e["kind"] == "vendor.expelled"]
    dr = [e for e in events if e["kind"] == "determinism.violation"]
    ck = [e for e in events if e["kind"] == "calibration.canary"]
    served = [e for e in events if e["kind"] == "product.served"]
    economy = state.get("economy") or {}
    product = state.get("product") or {}
    drift_rows = "".join(
        f"<tr><td>#{e['seq']}</td><td>{e['detail'].get('vendor')}</td>"
        f"<td>{e['detail'].get('kind')}</td>"
        f"<td>{e['detail'].get('summary', '')}</td></tr>"
        for e in drift[-8:])
    slash_rows = "".join(
        f"<tr><td>#{e['seq']}</td><td>{e['detail'].get('vendor')}</td>"
        f"<td>{e['detail'].get('forfeit')}</td>"
        f"<td>{e['detail'].get('reason')}</td></tr>" for e in slashes)
    dq_rows = "".join(
        f"<tr><td>#{e['seq']}</td><td>{e['detail'].get('vendor')}</td>"
        f"<td>{e['detail'].get('reason')}</td></tr>" for e in dq)
    exp_rows = "; ".join(
        f"#{e['seq']} <b>{e['detail'].get('vendor')}</b> "
        f"({e['detail'].get('reason')})" for e in exp) or ""
    kind_rows = "".join(
        f"<span class=chip>{k}</span>" for k in state["kinds"])
    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>Molt Forge — the Capability Bazaar</title><style>
body{{font:14px/1.5 ui-monospace,monospace;margin:2rem auto;max-width:1100px;
padding:0 1rem;background:#10100f;color:#e8e4d8}}
h1{{color:#d8a657}} h2{{color:#d8a657;border-bottom:1px solid #333;margin-top:2rem}}
table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #333;padding:4px 8px;text-align:left}}
th{{background:#1c1a16}} code{{color:#a9b665}}
.chip{{display:inline-block;background:#1c1a16;border:1px solid #333;
border-radius:3px;padding:1px 6px;margin:2px;font-size:11px}}
.ok{{color:#a9b665}} .bad{{color:#ea6962}}
.big{{font-size:18px}}
</style></head><body>
<h1>🔥 Molt Forge — the Capability Bazaar</h1>
<p>{state['event_count']} signed events · ruleset hash
<code>{(fs.get('ruleset_hash') or '?')[:16]}</code> · spent
{fs.get('spent', '?')} credits · treasury {fs.get('treasury', '?')} forfeited stake</p>
<p>proof verdict: <b class="{'ok' if verdict == 'pass' else 'bad'}">{verdict}</b>
 · aborted: {fs.get('aborted')} · accounting balanced: {acc.get('balanced')}
 · audit: {audit_line}</p>

<h2>Pipeline (sealed contracts only)</h2>
<table><tr><th>stage</th><th>vendor</th><th>contract</th><th>price</th></tr>
{_pipeline_rows(fs)}</table>

<h2>Phase 6 — the open bazaar</h2>
<p>expulsions: {exp_rows or '<i>none</i>'} ·
calibration canaries: {len(ck)} (failures: {sum(1 for e in ck if e['detail'].get('ok') is False)}) ·
replay audits: {state.get('replay_audits', '?')} (equivocations caught: {len(dr)}) ·
product served: {len(served)} buyer events</p>
<h2>Vendor books</h2>
<table><tr><th>vendor</th><th>stake</th><th>credits</th><th>live calls</th></tr>
{_vendor_rows(fs)}</table>

<h2>Disqualifications (behavioral evidence, before any contract)</h2>
<table><tr><th>seq</th><th>vendor</th><th>reason</th></tr>{dq_rows or '<tr><td colspan=3>none</td></tr>'}</table>

<h2>Contract drift observed (automatic)</h2>
<table><tr><th>seq</th><th>vendor</th><th>kind</th><th>summary</th></tr>
{drift_rows or '<tr><td colspan=4>none</td></tr>'}</table>

<h2>Slashes (liability enforced by molt-anchor)</h2>
<table><tr><th>seq</th><th>vendor</th><th>forfeit</th><th>reason</th></tr>
{slash_rows or '<tr><td colspan=4>none</td></tr>'}</table>

<h2>Event kinds</h2><div>{kind_rows}</div>
<p><a href=/api/state>/api/state</a> · <a href=/api/events?limit=50>/api/events</a>
 · <a href=/chronicle.md>/chronicle.md</a> · <a href=/audit.md>/audit.md</a></p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    run_dir: Path = Path("runs/default")
    state: dict = {}

    def log_message(self, *args):                  # quiet
        pass

    def _send(self, body: str, ctype: str = "text/html; charset=utf-8",
              code: int = 200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            self._send(_dashboard(Handler.state))
        elif u.path == "/api/state":
            self._send(json.dumps(
                {k: v for k, v in Handler.state.items() if k != "events"},
                indent=2) + "\n", "application/json")
        elif u.path == "/api/events":
            kinds = (q.get("kinds", [""])[0].split(",")
                     if q.get("kinds") else None)
            vendor = (q.get("vendor", [None])[0] or None)
            limit = int(q.get("limit", ["100"])[0])
            evs = Handler.state["events"]
            if kinds:
                evs = [e for e in evs if e["kind"] in kinds]
            if vendor:
                evs = [e for e in evs
                       if e["detail"].get("vendor") == vendor
                       or e["detail"].get("to") == vendor]
            self._send(json.dumps(evs[-limit:], indent=2) + "\n",
                       "application/json")
        elif u.path == "/chronicle.md":
            p = Handler.run_dir / "chronicle.md"
            self._send(p.read_text() if p.exists() else "not found",
                       "text/markdown; charset=utf-8", 404 if not p.exists() else 200)
        elif u.path == "/audit.md":
            p = Handler.run_dir / "audit-report.md"
            self._send(p.read_text() if p.exists() else "not found",
                       "text/markdown; charset=utf-8", 404 if not p.exists() else 200)
        else:
            self._send("not found\n", "text/plain", 404)


def serve(run_dir, port: int = 8700):
    run_dir = Path(run_dir)
    Handler.run_dir = run_dir
    Handler.state = load_state(run_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"molt-forge observatory → http://localhost:{port}/ "
          f"({Handler.state['event_count']} events)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
