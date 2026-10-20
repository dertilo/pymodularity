# pymodularity

The underscore's position in a Python import path sets that import's scope — Rust's `pub(super)`/`pub(crate)`, applied to PEP 8 underscores.

`pymodularity` draws a repo's real Python imports as mermaid diagrams, checks them against conventions derived from the code itself (no allow-list to maintain), and reports which files and directories carry the most structural coupling. It needs only the standard library to analyze your code — no runtime dependencies. `check`'s dependency-declaration rule additionally needs `uv` on PATH (see below).

## Install and run

```bash
uvx pymodularity check .
uvx pymodularity graph . --out docs/imports
uvx pymodularity metrics .
```

Or, inside a project: `uv add --dev pymodularity` and run `pymodularity ...` in that project's venv.

## The rules `check` enforces

Every rule below has a worked example, finding or allowed, in
[`tests/showcase/`](tests/showcase/README.md), a committed fixture repo
that also serves as a golden test for the whole rule set at once.

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
  - the same scope rule also covers the attribute form — a module or name reached through a dotted attribute chain from a name an import bound, rather than named directly in an import statement: `import shop.billing.pricing; shop.billing.pricing._x`, `import shop.billing.pricing as p; p._x`, `from shop.billing import pricing; pricing._x`, and a private module reached deeper in the chain (`import shop; shop._internal.x` applies the private-*module* rule to `shop._internal`, not the private-name rule). The attribute form only reports what the import form doesn't: a hop that lands on the same module or name an import statement in the same file already reaches — including an intermediate hop on a plain `import a.b.c`'s own dotted path — is not reported a second time. Findings from this form print with a ` (via attribute access)` suffix.

`pymodularity check .` exits 1 on any finding and prints, per finding, the importing file, the target, and the directory or file that owns it.

- **Dependency declarations are correct**, on by default, skipped with `check --no-deps`: for each package root (a directory with a `pyproject.toml` `[project]` table), runs [deptry](https://github.com/fpgmaas/deptry) (`uv run --directory <pkg> --with deptry==<pinned version> --frozen deptry . --json-output <tmp>`, a nested package root excluded from its parent's scan via `--extend-exclude`) and reports its findings (unused, missing, and transitive dependencies) the same way as the other rules. A package is skipped, not scanned, if it declares no runtime dependencies, or has no `uv.lock` of its own. This rule needs `uv` on PATH — `check` exits 2 with an error if it is missing, never a silent skip. The first run creates a `.venv` inside each scanned package and downloads its dependencies; later runs reuse it.

- **Structure limits**, fixed constants, no config, no baseline — every one of these is a finding regardless of how long it's been true:
  - a scanned file has at most 500 physical lines.
  - a directory has at most 10 entries, where an entry is a scanned `.py` file directly in it or a subdirectory holding scanned `.py` files, transitively; `__init__.py` and `__main__.py` don't count as entries (they're still scanned and still count toward the other limits).
  - a package root (the same `pyproject.toml` `[project]`-table directory the privacy and dependency rules anchor on) has at most 100 scanned files, each file counted against its nearest (innermost) package root.
  - a file has at most 15 public top-level names (no leading `_`): `def`/`async def`/`class`, a plain or annotated assignment target (including tuple-unpacking), or a `type X = ...` alias, at top level or nested in a top-level `if`/`try` block (including `elif`/`else`, `except` handlers, `finally`). Imports never count. A top-level function (not a class — `@dataclass` sits on most models) a decorator registers doesn't count either: FastAPI, Typer/Click, Dagster, pytest and the like derive part of their contract (route `operationId`, CLI command name, asset key, fixture name) from the function's name, so counting it toward the width cap would pressure someone into renaming or restructuring around a name the framework, not this tool, owns. Any decorator counts — there's no list of framework names.

## The 3 subcommands

- `pymodularity check <repo> [--no-deps]` — run all four rules above, print findings, exit 1 on any finding (`--no-deps` skips the dependency-declaration rule).
- `pymodularity graph <repo> --out DIR [--html]` — write one mermaid view per directory under `DIR`: `index.md` everywhere, plus a clickable `index.html` per view with `--html` (needs network access in the browser to load mermaid from a CDN).
- `pymodularity metrics <repo>` — print a coupling report to stdout: reach (dependents/dependencies, transitive) and a `score` per file and per directory; interface width (names other code imports from a unit); exposure share (how much of what a file defines actually gets used elsewhere); entry files (how much of a directory outside code actually reaches into); misplaced files (files whose closest partners sit mostly in another directory). Always exits 0 — a report, never a gate.

## Blind spots

Every blind spot below has a worked example, each tagged "missed" or
"false finding", in
[`tests/showcase/`](tests/showcase/README.md)'s gap cases (G1-G8).

`pymodularity` shows import coupling only, a lower bound on logical coupling ("if A changes, must B?"). Coupling that bypasses Python imports stays invisible:

- HTTP APIs, shared database schemas and tables, config keys and env vars, message and file formats, string-keyed registries, runtime `sys.path` edits.
- Two files with no edge here can still force each other's edits. **Never read a missing edge as independence.**

Other limits:

- a `warning: ambiguous import` line (printed to stderr) means several files in the repo match one import; no edge is drawn for it.
- an import that only resolves through a runtime `sys.path` edit is matched by file path, which can pick the wrong file.
- a third-party name that happens to equal a package folder elsewhere in the repo draws a false edge.
- test directories (`tests/`, `test/`) and test files (`test_*.py`, `*_test.py`, `conftest.py`) are always skipped, no flag — a test importing a sibling would otherwise draw a production dependency that production never has.
- `metrics` is a report on numbers that keep moving even on a repo that passes `check` clean; gating on it rewards gaming the number (for example, replacing an import with a string-keyed registry) instead of actually lowering coupling.
- the attribute form's blind spots: an instance attribute (`obj._y`) needs type information this tool doesn't have — ruff's `SLF001` is the complement there, though unlike this rule it carries no owner/descendant scope, so it also flags access from inside the owning file or directory; a base name bound to a third-party module, or one that doesn't resolve inside the repo; `getattr(mod, "_x")` or other `importlib`-style access; and a name the file also rebinds locally anywhere (a parameter, an assignment target, a `for`/`with`/`except`/comprehension target, a nested `def`/`class`) isn't checked at all through that name — conservative on purpose: a false finding would become a missed one, which is the right trade for a gate.
