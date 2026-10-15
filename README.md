# underscope

The underscore's position in a Python import path sets that import's scope — Rust's `pub(super)`/`pub(crate)`, applied to PEP 8 underscores.

`underscope` draws a repo's real Python imports as mermaid diagrams, checks them against conventions derived from the code itself (no allow-list to maintain), and reports which files and directories carry the most structural coupling. It needs only the standard library to analyze your code — no runtime dependencies. `check`'s dependency-declaration rule additionally needs `uv` on PATH (see below).

## Install and run

```bash
uvx underscope check .
uvx underscope graph . --out docs/imports
uvx underscope metrics .
```

Or, inside a project: `uv add --dev underscope` and run `underscope ...` in that project's venv.

## The rules `check` enforces

Given this tree:

```
shop/
  __init__.py
  billing/
    __init__.py
    invoice.py          # from shop.billing._pricing import compute_total
    _pricing.py          # def compute_total(): ...
  catalog/
    __init__.py
    listing.py           # from shop.billing._pricing import compute_total  <- finding
```

- **No import cycle**, at any directory level: between files, and between directories at every depth. Imports under `if TYPE_CHECKING:` and inside function bodies count. There is no baseline — an existing cycle fails too.
- **A private item stays visible only to its owner and the owner's descendants** (Go's `internal/`, applied as Rust's visibility rule to PEP 8 underscores):
  - a private module — a file, or a path component, named `_x` (single leading underscore, not a dunder) — belongs to the directory holding it; only files under that directory may import it, across package boundaries;
  - a private name imported via `from m import _x` belongs to `m`; if `m` is a single file, no other file may import `_x`, not even a sibling in the same directory;
  - if `m` is a package (an `__init__.py` or a namespace package directory), `_x` belongs to that directory, and files under it may import it.
  - a parent never reaches into a child's private items, and neither do siblings.
  - `shop/billing/invoice.py` above is allowed (it's under `shop/billing`, `_pricing`'s owner); `shop/catalog/listing.py` is a finding.
  - the attribute form (`import shop.billing._pricing as p; p.compute_total`) is out of scope here — ruff's `SLF001` already flags it.

`underscope check .` exits 1 on any finding and prints, per finding, the importing file, the target, and the directory or file that owns it.

- **Dependency declarations are correct**, on by default, skipped with `check --no-deps`: for each package root (a directory with a `pyproject.toml` `[project]` table), runs [deptry](https://github.com/fpgmaas/deptry) (`uv run --directory <pkg> --with deptry==<pinned version> --frozen deptry . --json-output <tmp>`, a nested package root excluded from its parent's scan via `--extend-exclude`) and reports its findings (unused, missing, and transitive dependencies) the same way as the other two rules. A package is skipped, not scanned, if it declares no runtime dependencies, or has no `uv.lock` of its own. This rule needs `uv` on PATH — `check` exits 2 with an error if it is missing, never a silent skip. The first run creates a `.venv` inside each scanned package and downloads its dependencies; later runs reuse it.

## The 3 subcommands

- `underscope check <repo> [--no-deps]` — run all three rules above, print findings, exit 1 on any finding (`--no-deps` skips the dependency-declaration rule).
- `underscope graph <repo> --out DIR [--html]` — write one mermaid view per directory under `DIR`: `index.md` everywhere, plus a clickable `index.html` per view with `--html` (needs network access in the browser to load mermaid from a CDN).
- `underscope metrics <repo>` — print a coupling report to stdout: reach (dependents/dependencies, transitive) and a `score` per file and per directory; interface width (names other code imports from a unit); exposure share (how much of what a file defines actually gets used elsewhere); entry files (how much of a directory outside code actually reaches into); misplaced files (files whose closest partners sit mostly in another directory). Always exits 0 — a report, never a gate.

## Blind spots

`underscope` shows import coupling only, a lower bound on logical coupling ("if A changes, must B?"). Coupling that bypasses Python imports stays invisible:

- HTTP APIs, shared database schemas and tables, config keys and env vars, message and file formats, string-keyed registries, runtime `sys.path` edits.
- Two files with no edge here can still force each other's edits. **Never read a missing edge as independence.**

Other limits:

- a `warning: ambiguous import` line (printed to stderr) means several files in the repo match one import; no edge is drawn for it.
- an import that only resolves through a runtime `sys.path` edit is matched by file path, which can pick the wrong file.
- a third-party name that happens to equal a package folder elsewhere in the repo draws a false edge.
- test directories (`tests/`, `test/`) and test files (`test_*.py`, `*_test.py`, `conftest.py`) are always skipped, no flag — a test importing a sibling would otherwise draw a production dependency that production never has.
- `metrics` is a report on numbers that keep moving even on a repo that passes `check` clean; gating on it rewards gaming the number (for example, replacing an import with a string-keyed registry) instead of actually lowering coupling.
