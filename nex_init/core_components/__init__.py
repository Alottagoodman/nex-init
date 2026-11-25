from __future__ import annotations

from nex_init.router import install_component_router

_ROUTER = install_component_router(globals(), __file__, __name__, max_depth=10)
get_interface_hashes = _ROUTER.get_interface_hashes
__getattr__ = _ROUTER.__getattr__
__dir__ = _ROUTER.__dir__
__all__ = _ROUTER.all_exports