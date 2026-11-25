"""
cli.py [ORCHESTRATOR SHELL <= 150 LOC]
Sovereign CLI for nex-init workspace synchronization, validation, and fs watching.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import nex_init.core_components as _core


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nex-init",
        description="nex-init: Autonomous RCTI v3.0 Package Router & Stub Synchronizer",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sync", help="Scan workspace and materialize all missing/drifted __init__.py and .pyi stubs.")
    s.add_argument("target", nargs="?", default=".", help="Target directory root (default: current directory).")
    s.add_argument("--check", action="store_true", help="Dry-run audit; exit non-zero if drift is detected.")

    w = sub.add_parser("watch", help="Start background daemon to synthesize __init__.py files as soon as directories/files are created.")
    w.add_argument("target", nargs="?", default=".", help="Target directory root to watch.")

    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target_path = Path(args.target).resolve()

    if not target_path.is_dir():
        sys.stderr.write(f"[ERROR] Target path does not exist or is not a directory: {target_path}\n")
        return 1

    if args.command == "sync":
        drift_count, fixed_count = _core.sync_workspace(target_path, check_only=args.check)
        if args.check:
            if drift_count > 0:
                sys.stdout.write(f"[DRIFT DETECTED] {drift_count} package boundary files require synchronization.\n")
                return 1
            sys.stdout.write("[VERIFIED] Workspace RCTI v3.0 package topology is 100% synchronized.\n")
            return 0
        sys.stdout.write(f"[DONE] Successfully synchronized workspace ({fixed_count} files written/healed).\n")
        return 0

    if args.command == "watch":
        return _core.run_watcher(target_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())