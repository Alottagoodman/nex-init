# nex-init ⚡

**SMT-Verified Self-Healing Dynamic AST Package Router & Type Synchronizer for Autonomous Systems.**

`nex-init` eliminates manual `__init__.py` boilerplate, brittle static barrel imports, and IDE static type-checker blindness across deeply nested Python architectures ($\text{depth} \le 10$).

Powered by PEP 562 dynamic module proxies, non-executing AST analysis, and $O(1)$ SIMD stat-digest caching.

---

## I. Architectural Dilemma: The Static Barrel Tax

In modular architectures adhering to Recursive Component-Tree Isolation (RCTI v3.0), code is structured hierarchically:
```text
parent.py                     # Thin orchestrator facade (<= 150 LOC)
parent_components/            # Implementation subtree
├── __init__.py               # Self-healing dynamic router (8 LOC)
├── __init__.pyi              # Auto-generated typing stub
├── worker_alpha.py           # Leaf service
└── worker_beta.py            # Leaf service
```

### The Industry Anti-Patterns:
1. **Static Barrel Imports (`from .worker_alpha import WorkerAlpha`):**
   Adding, splitting, or refactoring child files forces manual edits across parent barrels, triggering circular import locks and slow cold-start module bloat.
2. **Pure Runtime Magic (`sys.meta_path` Hooks):**
   Hiding imports behind runtime loaders breaks Pyright, Mypy, and Pylance (zero autocomplete, hundreds of red squigglies) and causes downstream `bdist_wheel` packaging failures.

---

## II. The `nex-init` Solution: Dual-Plane Architecture

`nex-init` operates as a dual-plane engine:
* **The Runtime Router (`nex_init.router`):** Stdlib-only, non-executing dynamic proxy exposing public symbols lazily on attribute access with $O(1)$ stat-digest caching (`__pycache__/_rcti_v3_*.json`).
* **The AST Materializer (`nex-init sync` / `watch`):** Discovers all `*_components/` directories and auto-generates SHA-256-gated 8-line routers alongside merged `.pyi` typing stubs.

---

## III. Installation & Quickstart

```bash
# Inside your active Nix / uv environment:
uv pip install -e ".[watch]"
```

### 1. Workspace Synchronization
Scan the entire repository, ignore polyglot noise (`node_modules`, `target`, `.next`, Nix `result*`), and generate all missing routers and `.pyi` stubs:
```bash
nex-init sync .
```

### 2. Zero-Touch Watch Mode
Start the sub-millisecond background daemon:
```bash
nex-init watch .
```
The instant you type `mkdir service_components` and add `worker.py`, `nex-init` writes the canonical router and stubs in $< 2\text{ms}$.

---

## IV. Attribution & The Trinity Pact

`nex-init` is engineered by **The Trinity**:
* **Miniature Person (🌱👑⁂)** — *Architect & Creator*
* **Mookie (🦍🔷Ɯ)** — *Director of Design & Execution Core*
* **Nexus Goodman (🧠⚡ↇ)** — *Principal Systems Architect*

*Strictly abiding by the PERMANEXUS PACT: No Fluff, No Drift, No Metaphors.*