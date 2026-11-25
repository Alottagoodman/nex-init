"""
ast_analyzer.py - Non-Executing AST Analyzer for nex_init.
Polyglot Monorepo Hardened: Fully prunes node_modules, target, .next, .wrangler,
Nix result/direnv symlinks, lockfiles, and virtualenvs.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Set

# Concrete transient directory names across Python, JS/TS, Rust, Nix, Cloudflare, and Git
_IGNORED_EXACT_DIRS: Set[str] = {
    # Version control & OS
    ".git",
    ".hg",
    ".svn",
    ".Trash",
    # JavaScript / TypeScript / Frontend runtimes
    "node_modules",
    ".next",
    ".nuxt",
    ".turbo",
    ".svelte-kit",
    ".parcel-cache",
    ".npm",
    ".yarn",
    ".pnpm-store",
    # Python virtualenvs & test/type caches
    ".venv",
    "venv",
    "env",
    ".env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".tox",
    ".nox",
    "coverage",
    ".coverage",
    # Rust & compiled artifacts
    "target",
    # Distribution & build outputs
    "dist",
    "build",
    "out",
    ".eggs",
    # Nix environment derivations & direnv
    ".direnv",
    # Cloudflare Edge, sandboxes & internal state vaults
    ".wrangler",
    "vfs_persistence",
    ".nexbox",
    ".nex_aiu_vault",
}

# Standalone files that must never trigger scans or package resolution
_IGNORED_EXACT_FILES: Set[str] = {
    ".DS_Store",
    "bun.lock",
    "bun.lockb",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "poetry.lock",
    "uv.lock",
    "flake.lock",
    "Pipfile.lock",
}


def is_ignored_directory(dir_name: str) -> bool:
    """Evaluates whether a directory name matches ignored sets or dynamic patterns."""
    if dir_name in _IGNORED_EXACT_DIRS:
        return True
    # Nix derivation build symlinks (e.g. result, result-bin, result-man)
    if dir_name == "result" or dir_name.startswith("result-"):
        return True
    # Python egg/dist metadata directories
    if dir_name.endswith((".egg-info", ".dist-info")):
        return True
    return False


def is_ignored_file(file_name: str) -> bool:
    """Evaluates whether a file should be bypassed."""
    if file_name in _IGNORED_EXACT_FILES:
        return True
    if file_name.startswith(".#") or file_name.endswith((".swp", ".tmp", "~")):
        return True
    return False


def is_path_in_ignored_tree(path: Path) -> bool:
    """Checks if any ancestor segment belongs to an ignored directory."""
    for part in path.parts:
        if is_ignored_directory(part):
            return True
    return False


def is_python_package(path: Path) -> bool:
    return path.is_dir() and not is_path_in_ignored_tree(path) and (path / "__init__.py").is_file()


def find_component_directories(root_dir: Path) -> List[Path]:
    """Recursively finds all directories ending with *_components, strictly pruning ignored trees in-place."""
    results: List[Path] = []
    for root, dirs, _ in os.walk(root_dir, followlinks=False):
        # In-place slice modification stops os.walk from descending into multi-GB trees
        dirs[:] = [d for d in dirs if not is_ignored_directory(d)]
        for d in dirs:
            if d.endswith("_components"):
                results.append(Path(root) / d)
    return sorted(results)


def find_tier1_packages(root_dir: Path) -> List[Path]:
    """
    Finds Python package roots across arbitrary monorepo nesting.
    Identifies any directory containing .py files or *_components that is NOT inside a component directory.
    """
    pkgs: Set[Path] = set()
    for comp_dir in find_component_directories(root_dir):
        parent = comp_dir.parent
        if not is_path_in_ignored_tree(parent):
            pkgs.add(parent)
            if parent.parent != root_dir and not is_path_in_ignored_tree(parent.parent):
                if any(p.suffix == ".py" and not is_ignored_file(p.name) and p.name != "__init__.py" for p in parent.parent.iterdir()):
                    pkgs.add(parent.parent)

    for base in [root_dir, root_dir / "src"]:
        if base.is_dir():
            for item in base.iterdir():
                if item.is_dir() and not is_ignored_directory(item.name) and not item.name.endswith("_components"):
                    has_py = any(
                        f.suffix == ".py" and not is_ignored_file(f.name)
                        for f in item.iterdir()
                        if f.is_file()
                    )
                    if (item / "__init__.py").is_file() or has_py:
                        pkgs.add(item)

    return sorted(pkgs)


def inspect_parent_file_needs_proxy(parent_py: Path) -> bool:
    """Verifies whether an orchestrator shell lacks install_parent_proxy binding."""
    if not parent_py.is_file() or is_ignored_file(parent_py.name):
        return False
    try:
        content = parent_py.read_text(encoding="utf-8")
        return "install_parent_proxy" not in content
    except OSError:
        return False