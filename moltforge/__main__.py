"""Molt Forge — The Capability Bazaar. CLI.

    python -m moltforge run         [--dir runs/default] [--seed N]
    python -m moltforge chronicle   [--dir runs/default]
    python -m moltforge audit       [--dir runs/default]
    python -m moltforge serve       [--dir runs/default] [--port 8700]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .chronicle import write_chronicle
from .forge import run as forge_run


def _dir(args) -> Path:
    return Path(args.dir)


def main() -> None:
    ap = argparse.ArgumentParser(prog="moltforge")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run")
    p.add_argument("--dir", default="runs/default")
    p.add_argument("--seed", type=int, default=7)

    for name in ("chronicle", "audit"):
        p = sub.add_parser(name)
        p.add_argument("--dir", default="runs/default")

    p = sub.add_parser("serve")
    p.add_argument("--dir", default="runs/default")
    p.add_argument("--port", type=int, default=8700)

    args = ap.parse_args()
    if args.cmd == "run":
        forge = forge_run(args.dir, seed=args.seed)
        chronicle_path = write_chronicle(_dir(args))
        print(f"forge complete → {_dir(args)}")
        print(f"  aborted {forge.aborted} | spent {forge.spent} | "
              f"treasury {forge.market.treasury}")
        print(f"  chronicle → {chronicle_path}")
    elif args.cmd == "chronicle":
        print(write_chronicle(_dir(args)))
    elif args.cmd == "audit":
        from . import audit as audit_mod
        out, report = audit_mod.write_report(_dir(args))
        print(json.dumps(report, indent=2))
        print(f"report → {out}")
        raise SystemExit(0 if report["ok"] else 1)
    elif args.cmd == "serve":
        from .observatory import serve
        serve(_dir(args), port=args.port)


if __name__ == "__main__":
    main()