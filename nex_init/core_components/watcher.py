"""
watcher.py - Sub-Millisecond FileSystem Event Daemon for nex-init.
Guards against event flooding by ignoring changes in node_modules, target, lockfiles, and venvs.
"""
from __future__ import annotations

import sys
from pathlib import Path

from nex_init.core_components.ast_analyzer import (
    is_ignored_file,
    is_path_in_ignored_tree,
)
from nex_init.core_components.stub_emitter import sync_workspace


def run_watcher(root_dir: Path) -> int:
    try:
        from watchfiles import watch
    except ImportError:
        sys.stderr.write(
            "[ERROR] watchfiles is not installed. Run `pip install \"nex-init[watch]\"` to enable watch mode.\n"
        )
        return 1

    sys.stdout.write(f"[WATCHER ACTIVE] Monitoring {root_dir} (polyglot noise filters enabled)...\n")
    # Run initial sync
    _, fixed = sync_workspace(root_dir)
    if fixed > 0:
        sys.stdout.write(f"[+] Initial workspace sync healed {fixed} file(s).\n")

    try:
        for changes in watch(root_dir, recursive=True):
            should_sync = False
            for change_type, path_str in changes:
                p = Path(path_str)
                # Sub-microsecond filter: skip lockfiles, .DS_Store, and ignored subtrees
                if is_ignored_file(p.name) or is_path_in_ignored_tree(p):
                    continue

                if p.name.endswith("_components") or (p.parent.name.endswith("_components") and p.suffix == ".py"):
                    should_sync = True
                    break

            if should_sync:
                _, count = sync_workspace(root_dir)
                if count > 0:
                    sys.stdout.write(f"[+] Auto-healed {count} router file(s).\n")
    except KeyboardInterrupt:
        sys.stdout.write("\n[WATCHER STOPPED] Clean teardown.\n")
        return 0

    return 0