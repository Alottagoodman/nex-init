"""
stub_emitter.py - Materializer for Canonical 8-Line Routers and Type Stubs.
Tightened with atomic fsync replace and safe relative package naming.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Tuple

from nex_init.core_components.ast_analyzer import (
    find_component_directories,
    find_tier1_packages,
)

TIER1_INIT_TEMPLATE = """from __future__ import annotations

from nex_init import install_tier1_router

_ROUTER = install_tier1_router(globals(), __file__, __name__)
__getattr__ = _ROUTER.__getattr__
__dir__ = _ROUTER.__dir__
__all__ = _ROUTER.all_exports
"""

COMPONENT_INIT_TEMPLATE = """from __future__ import annotations

from nex_init import install_component_router

_ROUTER = install_component_router(globals(), __file__, __name__, max_depth=10)
get_interface_hashes = _ROUTER.get_interface_hashes
__getattr__ = _ROUTER.__getattr__
__dir__ = _ROUTER.__dir__
__all__ = _ROUTER.all_exports
"""


def _atomic_write_if_changed(target: Path, content: str) -> bool:
    new_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if target.is_file():
        try:
            if hashlib.sha256(target.read_bytes()).hexdigest() == new_digest:
                return False
        except OSError:
            pass
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".nex_", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
        return True
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def sync_workspace(root_dir: Path, check_only: bool = False) -> Tuple[int, int]:
    """Scans and materializes missing __init__.py files. Returns (drift_count, fixed_count)."""
    drift_count = 0
    fixed_count = 0

    # 1. Synchronize Tier-2..10 Component Directories (*_components/)
    for comp_dir in find_component_directories(root_dir):
        init_file = comp_dir / "__init__.py"
        needs_write = False
        if not init_file.is_file():
            needs_write = True
        else:
            try:
                txt = init_file.read_text(encoding="utf-8")
                if "install_component_router" not in txt:
                    needs_write = True
            except OSError:
                needs_write = True

        if needs_write:
            drift_count += 1
            if not check_only:
                if _atomic_write_if_changed(init_file, COMPONENT_INIT_TEMPLATE):
                    fixed_count += 1

    # 2. Synchronize Package Parents
    for t1_dir in find_tier1_packages(root_dir):
        init_file = t1_dir / "__init__.py"
        needs_write = False
        if not init_file.is_file():
            needs_write = True
        else:
            try:
                txt = init_file.read_text(encoding="utf-8")
                # Do not overwrite if package already has custom initialization logic
                if "install_tier1_router" not in txt and "install_component_router" not in txt:
                    needs_write = False
            except OSError:
                needs_write = True

        if needs_write:
            drift_count += 1
            if not check_only:
                if _atomic_write_if_changed(init_file, TIER1_INIT_TEMPLATE):
                    fixed_count += 1

    return drift_count, fixed_count