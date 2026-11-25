"""
hooks.py - PEP 517 Build-Backend Integration Hooks (Hatchling / Setuptools / Flit).
Guarantees wheels built for PyPI contain all physical __init__.py and .pyi stubs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def hatchling_hook(build_data: dict[str, Any]) -> None:
    """Hatchling build hook invoked immediately prior to wheel archiving."""
    from nex_init.core_components.stub_emitter import sync_workspace
    root_dir = Path.cwd()
    _, count = sync_workspace(root_dir)
    if count > 0:
        print(f"[nex-init] Prepared {count} dynamic package boundary files for wheel inclusion.")