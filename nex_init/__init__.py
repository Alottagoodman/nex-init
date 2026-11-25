"""
nex_init - Sovereign Package Root Orchestrator (RCTI v3.0).
Provides runtime routing primitives and self-healing workspace synchronizers.
"""
from __future__ import annotations

from nex_init.router import (
    install_component_router,
    install_parent_proxy,
    install_tier1_router,
    invalidate_rcti_caches,
)

__all__ = [
    "install_component_router",
    "install_parent_proxy",
    "install_tier1_router",
    "invalidate_rcti_caches",
]