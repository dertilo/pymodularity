# pymodularity

The underscore's position in a Python import path sets the import's scope.
model: Rust's `pub(super)` and `pub(crate)`, applied to PEP 8 underscores.

`pymodularity` does 3 jobs on a repo's real Python imports:

- it draws them as mermaid diagrams.
- it checks them against conventions derived from the code itself, with no allow-list to maintain.
- it reports which files and directories carry the most structural coupling.

The analysis needs only the standard library, so `pymodularity` has no runtime dependencies.
exception: `check`'s dependency rule needs `uv` on PATH.

## Install and run

```bash
uvx pymodularity check .
uvx pymodularity graph . --out docs/imports
uvx pymodularity metrics .
```

Or, inside a project: `uv add --dev pymodularity` and run `pymodularity ...` in that project's venv.

## The rules `check` enforces

`pymodularity check .` exits 1 on any finding.
Each finding names the importing file, the target and the owner.

- **No import cycles:** between files, and between directories at every depth.
  - `TYPE_CHECKING` imports and in-function imports count.
  - no baseline: an existing cycle fails too.
- **Private items stay inside their owner:** only code inside the owner may import a `_`-prefixed module or name.
  - private module: the owner is the directory holding it.
  - private name from a package: the owner is the package directory.
  - private name from a regular file: the owner is that file alone, so even a sibling file is a finding.
  - attribute access such as `mod._x` counts like an import.
- **Declared dependencies match the imports:** [deptry](https://github.com/fpgmaas/deptry) reports unused, missing and transitive dependencies per package root.
  - requirement: `uv` on PATH. Without it, `check` exits 2.
  - `--no-deps` skips this rule.
- **Structure limits:** at most 500 lines per file, 10 entries per directory, 100 files per package root and 10 public names per file.
  - no config, no baseline.
  - ceiling: a limit marks the size that is clearly too much. It is no target.
  - below a limit: a smaller interface is still better. `metrics` reports interface width and exposure share.
- **Names carry a scope:** a scanned file's stem, and a directory holding scanned code, need at least 2 `_`-separated words.
  - exempt: `__init__`, `__main__`, `conftest`, `setup`, `wsgi`, `asgi`, `_version`, a package root, `src`, `tests`, `migrations`, `alembic`.
  - details: [docs/rules.md](docs/rules.md#names-carry-a-scope).

Worked cases: the [showcase](tests/showcase/README.md) shows each rule except the dependency rule as allowed and finding cases.
Definitions: [docs/rules.md](docs/rules.md) holds the counting rules, the deptry run, and the ruff rules for functions and classes.

## The 3 subcommands

All 3 also take a repeatable `--exclude PATTERN`, merged with `[tool.pymodularity] exclude` in `<repo>`'s own pyproject.toml.
Pattern: `fnmatch` against the POSIX path relative to `<repo>`, matched on the path itself or any parent directory.
Use case: generated code (an OpenAPI client, protobuf stubs) and vendored code.
Effect: an excluded tree is invisible to every rule and report, except deptry's scan of a kept package that contains it.
An import from kept code into it behaves like an import of code `pymodularity` can't resolve.
Details: [docs/rules.md](docs/rules.md#excluding-code).

- `pymodularity check <repo> [--no-deps]`: runs the 5 rules above and prints the findings.
  - exit code: 1 on any finding.
  - `--no-deps`: skips the dependency rule.
- `pymodularity graph <repo> --out DIR [--html]`: writes one mermaid view per directory under `DIR`.
  - `index.md`: written for every view.
  - `--html`: adds a clickable `index.html` per view. The browser needs network access to load mermaid from a CDN.
- `pymodularity metrics <repo>`: prints a coupling report to stdout.
  - `<repo>`: the tree holding every consumer of the code under study, never a library package alone.
  - reason: a package measured alone misses its importers outside it. Reach, interface width and exposure share come out too low, and files its consumers use show as misplaced.
  - reach: transitive dependents and dependencies, plus a `score` per file and per directory.
  - interface width: the names other code imports from a unit.
  - exposure share: how much of what a file defines other code uses.
  - entry files: how much of a directory outside code reaches into.
  - misplaced files: files whose closest partners sit mostly in another directory.
  - exit code: always 0, because the report is no gate.

## Blind spots

Worked cases: the [showcase](tests/showcase/README.md) gap cases G1 to G8, each tagged with its outcome.

`pymodularity` shows import coupling only.
Import coupling is a lower bound on logical coupling: "if A changes, must B change?"
Coupling that bypasses Python imports stays invisible:

- HTTP APIs, shared database schemas and tables, config keys and env vars, message and file formats, string-keyed registries, runtime `sys.path` edits.
- Two files with no edge can still force each other's edits. **Never read a missing edge as independence.**

Other limits:

- ambiguous import: `check` prints `warning: ambiguous import` to stderr and draws no edge.
  - cause: several [import roots](docs/internals.md#how-an-absolute-import-resolves-to-a-file) provide the module, and none is an ancestor of the importer.
- runtime `sys.path` edit: an import that resolves only through such an edit gets an edge only if the inserted directory is an import root.
- script sibling: `python main.py` importing its sibling `execution.py`, with no package root nearby, gets no edge.
  - reason: resolution looks only through import roots, never through the importer's own directory.
- tests: `pymodularity` always skips test directories (`tests/`, `test/`) and test files (`test_*.py`, `*_test.py`, `conftest.py`).
  - reason: a test importing a sibling would draw a production dependency that production never has.
- `metrics` as a gate: its numbers move even on a repo that passes `check`.
  - consequence: a gate on them rewards gaming the number, for example replacing an import with a string-keyed registry.
- attribute form, missed cases:
  - an instance attribute such as `obj._y`, because `pymodularity` has no type information.
  - complement for that case: ruff's `SLF001`. It has no owner scope, so it also flags access from inside the owner.
  - a base name bound to a third-party module, or to a module outside the repo.
  - `getattr(mod, "_x")` and other `importlib` access.
  - a name the file also rebinds anywhere: a parameter, an assignment target, a `for`, `with`, `except` or comprehension target, a nested `def` or `class`.
  - reason for the last case: a gate must trade false findings for missed ones, so `pymodularity` skips such a name.
