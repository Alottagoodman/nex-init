"""
router.py - Universal Dynamic AST Engine for nex_init.
Lightweight, stdlib-only runtime executing PEP 562 proxies, stat-digest caching, and lazy loading.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import json
import os
import pkgutil
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

MAX_NESTING_DEPTH = 10
_UPPER_CONST_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_EXTRA_PUBLIC_IDENTIFIERS: Set[str] = {"logger", "client"}
_SKIP_NATIVE_STUB_NAMES: Set[str] = {"__getattr__", "__dir__", "__all__"}
_TIER1_SKIP_FLAT_MODULES: Set[str] = {"router"}
_TYPING_ROOTS: Set[str] = {
    "Union", "Optional", "Annotated", "List", "Dict", "Tuple", "Set",
    "Callable", "Type", "Literal", "Final", "TypeAlias",
}

_ACTIVE_COMPONENT_ROUTERS: List["ComponentRouter"] = []
_ACTIVE_TIER1_ROUTERS: List["Tier1PackageRouter"] = []


def _atomic_write_if_changed(target: Path, content: str) -> None:
    new_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if target.is_file():
        try:
            if hashlib.sha256(target.read_bytes()).hexdigest() == new_digest:
                return
        except OSError:
            pass
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".rcti_", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
    except OSError:
        pass


def _is_public_typing_assign(value_node: Optional[ast.expr]) -> bool:
    if value_node is None:
        return False
    if isinstance(value_node, ast.Subscript):
        root = value_node.value
        if isinstance(root, ast.Name) and root.id in _TYPING_ROOTS:
            return True
        if isinstance(root, ast.Attribute) and root.attr in _TYPING_ROOTS:
            return True
    if isinstance(value_node, ast.BinOp) and isinstance(value_node.op, ast.BitOr):
        return True
    if isinstance(value_node, ast.Call) and isinstance(value_node.func, ast.Name):
        if value_node.func.id in ("TypeVar", "NewType", "ParamSpec"):
            return True
    return False


def _extract_literal_all(tree: ast.Module) -> Optional[List[str]]:
    for stmt in tree.body:
        target_name, value_node = None, None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target_name, value_node = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target_name, value_node = stmt.target.id, stmt.value
        if target_name == "__all__" and isinstance(value_node, (ast.List, ast.Tuple)):
            return [e.value for e in value_node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return None


def _extract_module_public_symbols(py_file: Path) -> Tuple[List[str], str]:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (SyntaxError, OSError):
        return [], "0" * 16
    explicit_all = _extract_literal_all(tree)
    symbols: List[str] = []
    sig_parts: List[str] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and not stmt.name.startswith("_"):
            symbols.append(stmt.name)
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args_repr = ",".join(a.arg for a in stmt.args.args)
                sig_parts.append(f"FN:{stmt.name}({args_repr})")
            else:
                sig_parts.append(f"CLS:{stmt.name}")
        elif isinstance(stmt, ast.Assign):
            is_typing = _is_public_typing_assign(stmt.value)
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name) and not tgt.id.startswith("_") and (
                    _UPPER_CONST_RE.match(tgt.id) or tgt.id in _EXTRA_PUBLIC_IDENTIFIERS or is_typing
                ):
                    symbols.append(tgt.id)
                    sig_parts.append(f"CONST:{tgt.id}")
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            tid = stmt.target.id
            is_alias = isinstance(stmt.annotation, ast.Name) and stmt.annotation.id == "TypeAlias"
            if not tid.startswith("_") and (
                _UPPER_CONST_RE.match(tid) or tid in _EXTRA_PUBLIC_IDENTIFIERS or is_alias or _is_public_typing_assign(stmt.value)
            ):
                symbols.append(tid)
                sig_parts.append(f"CONST:{tid}")
    public_list = explicit_all if explicit_all is not None else symbols
    sig_hash = hashlib.sha256("|".join(sorted(sig_parts)).encode("utf-8")).hexdigest()[:16]
    return public_list, sig_hash


def _scan_directory_tree(
    root_dir: Path,
    root_pkg_name: str,
    max_depth: int,
    skip_flat_stems: Set[str] = frozenset(),
) -> Tuple[List[Tuple[str, str]], Set[str], List[str], Dict[str, str]]:
    visited: Set[Path] = set()
    top_submodules: Set[str] = set()
    top_subpackages: List[str] = []
    top_hashes: Dict[str, str] = {}

    def _walk(cur_dir: Path, cur_pkg_name: str, depth: int) -> List[Tuple[str, str]]:
        real = cur_dir.resolve()
        if depth > max_depth or real in visited or not cur_dir.is_dir():
            return []
        visited.add(real)
        try:
            entries = sorted(cur_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            return []
        py_files = [
            e for e in entries
            if e.is_file() and e.suffix == ".py" and e.name != "__init__.py" and not e.name.startswith("_")
        ]
        py_stems = {p.stem for p in py_files}
        collected: List[Tuple[str, str]] = []
        for py_file in py_files:
            mod_path = f"{cur_pkg_name}.{py_file.stem}"
            syms, sig_hash = _extract_module_public_symbols(py_file)
            if depth == 1:
                top_submodules.add(py_file.stem)
                top_hashes[py_file.stem] = sig_hash
            promote_flat = not (depth == 1 and (py_file.stem in skip_flat_stems or py_file.stem.startswith("run_")))
            if promote_flat:
                for sym in syms:
                    collected.append((sym, mod_path))
            owned_sub = cur_dir / f"{py_file.stem}_components"
            if owned_sub.is_dir() and (owned_sub / "__init__.py").is_file() and depth < max_depth:
                sub_items = _walk(owned_sub, f"{cur_pkg_name}.{owned_sub.name}", depth + 1)
                if promote_flat:
                    for sym, leaf_mod in sub_items:
                        if sym not in syms:
                            collected.append((sym, leaf_mod))
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("_") or not (entry / "__init__.py").is_file():
                continue
            if depth == 1:
                top_submodules.add(entry.name)
                top_subpackages.append(entry.name)
            if entry.name.endswith("_components") and entry.name[:-len("_components")] in py_stems:
                continue
            if depth < max_depth:
                collected.extend(_walk(entry, f"{cur_pkg_name}.{entry.name}", depth + 1))
        return collected

    pairs = _walk(root_dir, root_pkg_name, 1)
    return pairs, top_submodules, top_subpackages, top_hashes


def _compute_subtree_stat_digest(root_dir: Path, max_depth: int, include_parent_py: bool = False) -> str:
    hasher = hashlib.sha256()
    visited: Set[Path] = set()

    def _walk(cur_dir: Path, prefix: str, depth: int) -> None:
        real = cur_dir.resolve()
        if depth > max_depth or real in visited or not cur_dir.is_dir():
            return
        visited.add(real)
        try:
            entries = sorted(cur_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for entry in entries:
            if entry.name == "__init__.py" or entry.name.startswith("_") or entry.suffix == ".pyi":
                continue
            if entry.is_file() and entry.suffix == ".py":
                st = entry.stat()
                hasher.update(f"F:{prefix}{entry.name}:{st.st_mtime_ns}:{st.st_size}|".encode("utf-8"))
            elif entry.is_dir() and (entry / "__init__.py").is_file():
                st = (entry / "__init__.py").stat()
                hasher.update(f"D:{prefix}{entry.name}:{st.st_mtime_ns}|".encode("utf-8"))
                _walk(entry, f"{prefix}{entry.name}/", depth + 1)

    _walk(root_dir, "", 1)
    if include_parent_py and root_dir.name.endswith("_components"):
        parent_stem = root_dir.name[:-len("_components")]
        parent_py = root_dir.parent / f"{parent_stem}.py"
        if parent_py.is_file():
            st = parent_py.stat()
            hasher.update(f"P:{parent_py.name}:{st.st_mtime_ns}:{st.st_size}|".encode("utf-8"))
    return hasher.hexdigest()


class ComponentRouter:
    def __init__(
        self,
        pkg_globals: Dict[str, Any],
        init_file: str,
        pkg_name: str,
        max_depth: int = MAX_NESTING_DEPTH,
    ) -> None:
        self.pkg_globals = pkg_globals
        self.pkg_dir = Path(init_file).resolve().parent
        self.pkg_name = pkg_name
        self.max_depth = max(1, min(max_depth, MAX_NESTING_DEPTH))
        self.cache_file = self.pkg_dir / "__pycache__" / "_rcti_v3_ast_index.json"
        self.stub_file = self.pkg_dir / "__init__.pyi"
        self.stat_digest: str = ""
        self.symbol_to_module: Dict[str, str] = {}
        self.ambiguous_symbols: Dict[str, List[str]] = {}
        self.interface_hashes: Dict[str, str] = {}
        self.submodules: Set[str] = set()
        self.subpackages: List[str] = []
        self.refresh()
        _ACTIVE_COMPONENT_ROUTERS.append(self)

    def _register_symbol(self, sym: str, mod_path: str) -> None:
        if sym in self.ambiguous_symbols:
            if mod_path not in self.ambiguous_symbols[sym]:
                self.ambiguous_symbols[sym].append(mod_path)
            return
        if sym in self.symbol_to_module:
            prev = self.symbol_to_module[sym]
            if prev == mod_path:
                return
            self.symbol_to_module.pop(sym)
            self.ambiguous_symbols[sym] = [prev, mod_path]
            return
        self.symbol_to_module[sym] = mod_path

    def _discover_nuitka_frozen(self) -> None:
        for mod_info in pkgutil.iter_modules([str(self.pkg_dir)]):
            if mod_info.name.startswith("_"):
                continue
            self.submodules.add(mod_info.name)
            if mod_info.ispkg:
                self.subpackages.append(mod_info.name)
            mod_path = f"{self.pkg_name}.{mod_info.name}"
            try:
                mod = importlib.import_module(mod_path)
                exported = getattr(mod, "__all__", None) or [k for k in dir(mod) if not k.startswith("_")]
                for sym in exported:
                    if sym != "get_interface_hashes":
                        self._register_symbol(sym, mod_path)
            except Exception:
                pass

    def _write_stubs(self) -> None:
        lines = [
            "# AUTO-GENERATED BY nex-init (RCTI v3.0) — DO NOT EDIT",
            "from __future__ import annotations",
            "",
        ]
        by_rel_mod: Dict[str, List[str]] = {}
        prefix = f"{self.pkg_name}."
        for sym, mod_path in sorted(self.symbol_to_module.items()):
            rel_mod = mod_path[len(prefix):] if mod_path.startswith(prefix) else mod_path.rsplit(".", 1)[-1]
            by_rel_mod.setdefault(rel_mod, []).append(sym)
        for rel_mod, syms in sorted(by_rel_mod.items()):
            lines.append(f"from .{rel_mod} import {', '.join(f'{s} as {s}' for s in sorted(syms))}")
        for subpkg in sorted(self.subpackages):
            lines.append(f"from . import {subpkg} as {subpkg}")
        all_list = sorted(set(self.symbol_to_module.keys()) | self.submodules | {"get_interface_hashes"})
        lines.extend(["", "def get_interface_hashes() -> dict[str, str]: ...", f"__all__ = {all_list!r}", ""])
        _atomic_write_if_changed(self.stub_file, "\n".join(lines))

    def refresh(self) -> None:
        self.symbol_to_module.clear()
        self.ambiguous_symbols.clear()
        self.interface_hashes.clear()
        self.submodules.clear()
        self.subpackages.clear()
        self.stat_digest = _compute_subtree_stat_digest(self.pkg_dir, self.max_depth, include_parent_py=True)
        if self.cache_file.is_file():
            try:
                cached = json.loads(self.cache_file.read_text(encoding="utf-8"))
                if cached.get("stat_digest") == self.stat_digest:
                    self.symbol_to_module.update(cached["symbol_to_module"])
                    self.ambiguous_symbols.update(cached["ambiguous_symbols"])
                    self.interface_hashes.update(cached["interface_hashes"])
                    self.submodules.update(cached["submodules"])
                    self.subpackages.extend(cached["subpackages"])
                    if not self.stub_file.is_file():
                        self._write_stubs()
                    return
            except (OSError, json.JSONDecodeError, KeyError):
                pass
        has_sources = self.pkg_dir.is_dir() and any(
            (p.is_file() and p.suffix == ".py" and p.name != "__init__.py")
            or (p.is_dir() and (p / "__init__.py").is_file())
            for p in self.pkg_dir.iterdir()
        )
        if has_sources:
            pairs, subs, subpkgs, hashes = _scan_directory_tree(self.pkg_dir, self.pkg_name, self.max_depth)
            self.submodules.update(subs)
            self.subpackages.extend(subpkgs)
            self.interface_hashes.update(hashes)
            for sym, mod_path in pairs:
                self._register_symbol(sym, mod_path)
        else:
            self._discover_nuitka_frozen()
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps(
                    {
                        "stat_digest": self.stat_digest,
                        "symbol_to_module": self.symbol_to_module,
                        "ambiguous_symbols": self.ambiguous_symbols,
                        "interface_hashes": self.interface_hashes,
                        "submodules": sorted(self.submodules),
                        "subpackages": self.subpackages,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        self._write_stubs()

    @property
    def all_exports(self) -> List[str]:
        return sorted(set(self.symbol_to_module.keys()) | self.submodules | {"get_interface_hashes"})

    def get_interface_hashes(self) -> Dict[str, str]:
        return dict(self.interface_hashes)

    def _resolve_internal(self, name: str) -> Any:
        if name in self.ambiguous_symbols:
            raise AttributeError(
                f"Ambiguous symbol {name!r} defined in multiple modules {self.ambiguous_symbols[name]}; "
                "import directly from the specific submodule."
            )
        if name in self.symbol_to_module:
            return getattr(importlib.import_module(self.symbol_to_module[name]), name)
        if name in self.submodules:
            return importlib.import_module(f"{self.pkg_name}.{name}")
        for subpkg_name in self.subpackages:
            subpkg = importlib.import_module(f"{self.pkg_name}.{subpkg_name}")
            if hasattr(subpkg, name):
                return getattr(subpkg, name)
        raise KeyError(name)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._resolve_internal(name)
        except KeyError:
            if _compute_subtree_stat_digest(self.pkg_dir, self.max_depth, include_parent_py=True) != self.stat_digest:
                self.refresh()
                try:
                    return self._resolve_internal(name)
                except KeyError:
                    pass
            raise AttributeError(f"module {self.pkg_name!r} has no attribute {name!r}")

    def __dir__(self) -> List[str]:
        return sorted(set(self.pkg_globals.keys()) | set(self.all_exports))


class Tier1PackageRouter:
    def __init__(
        self,
        pkg_globals: Dict[str, Any],
        init_file: str,
        pkg_name: str,
        max_depth: int = MAX_NESTING_DEPTH,
    ) -> None:
        self.pkg_globals = pkg_globals
        self.pkg_dir = Path(init_file).resolve().parent
        self.pkg_name = pkg_name
        self.max_depth = max(1, min(max_depth, MAX_NESTING_DEPTH))
        self.cache_file = self.pkg_dir / "__pycache__" / "_rcti_v3_tier1_index.json"
        self.stub_file = self.pkg_dir / "__init__.pyi"
        self.stat_digest: str = ""
        self.top_modules: List[str] = []
        self.symbol_to_module: Dict[str, str] = {}
        self.ambiguous_symbols: Dict[str, List[str]] = {}
        self.refresh()
        _ACTIVE_TIER1_ROUTERS.append(self)

    def _register_symbol(self, sym: str, mod_path: str) -> None:
        if sym in self.ambiguous_symbols:
            if mod_path not in self.ambiguous_symbols[sym]:
                self.ambiguous_symbols[sym].append(mod_path)
            return
        if sym in self.symbol_to_module:
            prev = self.symbol_to_module[sym]
            if prev == mod_path:
                return
            self.symbol_to_module.pop(sym)
            self.ambiguous_symbols[sym] = [prev, mod_path]
            return
        self.symbol_to_module[sym] = mod_path

    def _write_stub(self) -> None:
        lines = [
            "# AUTO-GENERATED BY nex-init (RCTI v3.0) — DO NOT EDIT",
            "from __future__ import annotations",
            "",
        ]
        for mod_name in self.top_modules:
            lines.append(f"from . import {mod_name} as {mod_name}")
        by_rel_mod: Dict[str, List[str]] = {}
        prefix = f"{self.pkg_name}."
        for sym, mod_path in sorted(self.symbol_to_module.items()):
            if sym in self.top_modules:
                continue
            rel_mod = mod_path[len(prefix):] if mod_path.startswith(prefix) else mod_path.rsplit(".", 1)[-1]
            by_rel_mod.setdefault(rel_mod, []).append(sym)
        for rel_mod, syms in sorted(by_rel_mod.items()):
            lines.append(f"from .{rel_mod} import {', '.join(f'{s} as {s}' for s in sorted(syms))}")
        lines.extend(["", f"__all__ = {self.all_exports!r}", ""])
        _atomic_write_if_changed(self.stub_file, "\n".join(lines))

    def refresh(self) -> None:
        self.top_modules.clear()
        self.symbol_to_module.clear()
        self.ambiguous_symbols.clear()
        self.stat_digest = _compute_subtree_stat_digest(self.pkg_dir, self.max_depth, include_parent_py=False)
        if self.cache_file.is_file():
            try:
                cached = json.loads(self.cache_file.read_text(encoding="utf-8"))
                if cached.get("stat_digest") == self.stat_digest:
                    self.top_modules.extend(cached["top_modules"])
                    self.symbol_to_module.update(cached["symbol_to_module"])
                    self.ambiguous_symbols.update(cached["ambiguous_symbols"])
                    if not self.stub_file.is_file():
                        self._write_stub()
                    return
            except (OSError, json.JSONDecodeError, KeyError):
                pass
        has_sources = self.pkg_dir.is_dir() and any(
            (p.is_file() and p.suffix == ".py" and p.name != "__init__.py")
            or (p.is_dir() and (p / "__init__.py").is_file())
            for p in self.pkg_dir.iterdir()
        )
        if has_sources:
            pairs, subs, _, _ = _scan_directory_tree(
                self.pkg_dir,
                self.pkg_name,
                self.max_depth,
                skip_flat_stems=_TIER1_SKIP_FLAT_MODULES,
            )
            self.top_modules = sorted(subs)
            for sym, mod_path in pairs:
                self._register_symbol(sym, mod_path)
        else:
            self.top_modules = sorted(
                m.name for m in pkgutil.iter_modules([str(self.pkg_dir)]) if not m.name.startswith("_")
            )
            for m_name in self.top_modules:
                if m_name in _TIER1_SKIP_FLAT_MODULES or m_name.startswith("run_"):
                    continue
                mod_path = f"{self.pkg_name}.{m_name}"
                try:
                    mod = importlib.import_module(mod_path)
                    exported = getattr(mod, "__all__", None) or [k for k in dir(mod) if not k.startswith("_")]
                    for sym in exported:
                        self._register_symbol(sym, mod_path)
                except Exception:
                    pass
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps(
                    {
                        "stat_digest": self.stat_digest,
                        "top_modules": self.top_modules,
                        "symbol_to_module": self.symbol_to_module,
                        "ambiguous_symbols": self.ambiguous_symbols,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        self._write_stub()

    @property
    def all_exports(self) -> List[str]:
        return sorted(set(self.top_modules) | set(self.symbol_to_module.keys()))

    def _resolve_internal(self, name: str) -> Any:
        if name in self.top_modules:
            return importlib.import_module(f"{self.pkg_name}.{name}")
        if name in self.ambiguous_symbols:
            raise AttributeError(
                f"Ambiguous symbol {name!r} defined in multiple modules {self.ambiguous_symbols[name]}; "
                "import via the explicit submodule."
            )
        if name in self.symbol_to_module:
            return getattr(importlib.import_module(self.symbol_to_module[name]), name)
        raise KeyError(name)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._resolve_internal(name)
        except KeyError:
            if _compute_subtree_stat_digest(self.pkg_dir, self.max_depth, include_parent_py=False) != self.stat_digest:
                self.refresh()
                try:
                    return self._resolve_internal(name)
                except KeyError:
                    pass
            raise AttributeError(f"module {self.pkg_name!r} has no attribute {name!r}")

    def __dir__(self) -> List[str]:
        return sorted(set(self.pkg_globals.keys()) | set(self.all_exports))


def install_component_router(
    pkg_globals: Dict[str, Any],
    init_file: str,
    pkg_name: str,
    max_depth: int = MAX_NESTING_DEPTH,
) -> ComponentRouter:
    return ComponentRouter(pkg_globals, init_file, pkg_name, max_depth=max_depth)


def install_parent_proxy(
    parent_globals: Dict[str, Any],
    parent_file: str,
    parent_mod_name: str,
) -> Tuple[Callable[[str], Any], Callable[[], List[str]], List[str]]:
    p_path = Path(parent_file).resolve()
    comp_pkg_name = (
        f"{parent_mod_name.rsplit('.', 1)[0]}.{p_path.stem}_components"
        if "." in parent_mod_name
        else f"{p_path.stem}_components"
    )

    def _load_comp() -> Any:
        return importlib.import_module(comp_pkg_name)

    def _parent_getattr(name: str) -> Any:
        pkg = _load_comp()
        if hasattr(pkg, name):
            return getattr(pkg, name)
        raise AttributeError(f"module {parent_mod_name!r} has no attribute {name!r}")

    def _parent_dir() -> List[str]:
        pkg = _load_comp()
        comp_all = [s for s in getattr(pkg, "__all__", []) if s != "get_interface_hashes"]
        native = [
            k for k in parent_globals.keys()
            if not k.startswith("_") and k not in _SKIP_NATIVE_STUB_NAMES
        ]
        return sorted(set(native) | set(comp_all))

    pkg = _load_comp()
    comp_exports = [s for s in getattr(pkg, "__all__", []) if s != "get_interface_hashes"]
    native_exports = [
        k for k in parent_globals.keys()
        if not k.startswith("_") and k not in _SKIP_NATIVE_STUB_NAMES
    ]
    return _parent_getattr, _parent_dir, sorted(set(native_exports) | set(comp_exports))


def install_tier1_router(
    pkg_globals: Dict[str, Any],
    init_file: str,
    pkg_name: str,
    max_depth: int = MAX_NESTING_DEPTH,
) -> Tier1PackageRouter:
    return Tier1PackageRouter(pkg_globals, init_file, pkg_name, max_depth=max_depth)


def invalidate_rcti_caches() -> None:
    for router in _ACTIVE_COMPONENT_ROUTERS:
        try:
            if router.cache_file.is_file():
                router.cache_file.unlink(missing_ok=True)
            router.refresh()
        except Exception:
            pass
    for t1 in _ACTIVE_TIER1_ROUTERS:
        try:
            if t1.cache_file.is_file():
                t1.cache_file.unlink(missing_ok=True)
            t1.refresh()
        except Exception:
            pass