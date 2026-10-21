# pymodularity rule details

The [README](../README.md) lists the rules `check` enforces.
The [showcase](../tests/showcase/README.md) shows each rule as worked cases.
This file holds the definitions neither of them spells out.

## Private items

- private module: a file or a path component named `_x`, with one leading underscore and no dunder.
- package: a directory with an `__init__.py`, or a namespace package directory.
- attribute form: findings from `mod._x` end with ` (via attribute access)`.
- deduplication: an attribute hop that an import statement in the same file already reaches gives no second finding.

## Dependency declarations

- package root: a directory whose `pyproject.toml` has a `[project]` table.
- command: `uv run --directory <pkg> --with deptry==<pinned version> --frozen deptry . --json-output <tmp>`.
- nested package root: `--extend-exclude` removes it from its parent's scan.
- skip: `check` skips a package that declares no runtime dependencies or has no `uv.lock` of its own.
- missing `uv`: `check` exits 2 with an error and never skips silently.
- first run: `check` creates a `.venv` in each scanned package and downloads its dependencies.
- later runs: `check` reuses that `.venv`.

## Structure limits

The limits are fixed constants, with no config and no baseline.
Every breach is a finding, however long it has existed.
A limit is a ceiling, not a target: below it, a smaller interface is still better.

- file: at most 500 physical lines.
- directory: at most 10 entries.
  - entry: a scanned `.py` file directly in the directory, or a subdirectory holding scanned `.py` files at any depth.
  - `__init__.py` and `__main__.py`: no entries, but they still count toward the other limits.
- package root: at most 100 scanned files.
  - each file counts against its innermost package root.
- file: at most 10 public top-level names. A public name has no leading `_`.
  - counted: `def`, `async def`, `class`, a plain or annotated assignment target including tuple unpacking, and a `type X = ...` alias.
  - position: at top level, or inside a top-level `if` or `try` block, including `elif`, `else`, `except` and `finally`.
  - not counted: imports.
  - not counted: a top-level function with any decorator.
  - reason: FastAPI, Typer, Click, Dagster and pytest derive a route `operationId`, a command name, an asset key or a fixture name from the function name. Counting that name would push a rename of a name the framework owns.
  - exception: a decorated class still counts, because `@dataclass` sits on most models.

## Ruff rules for functions and classes

The structure limits stop at file level.
Ruff's size rules cover functions and classes.
4 of these rules are preview rules, so they need `preview = true`.

| Rule | Option | Recommended | Ruff default |
|---|---|---|---|
| `C901` complex-structure | `lint.mccabe.max-complexity` | 10 | 10 |
| `PLR0911` too-many-return-statements | `lint.pylint.max-returns` | 6 | 6 |
| `PLR0912` too-many-branches | `lint.pylint.max-branches` | 12 | 12 |
| `PLR0913` too-many-arguments | `lint.pylint.max-args` | 5 | 5 |
| `PLR0915` too-many-statements | `lint.pylint.max-statements` | 50 | 50 |
| `PLR1702` too-many-nested-blocks (preview) | `lint.pylint.max-nested-blocks` | 3 | 5 |
| `PLR0904` too-many-public-methods (preview) | `lint.pylint.max-public-methods` | 10 | 20 |
| `PLR0914` too-many-locals (preview) | `lint.pylint.max-locals` | 15 | 15 |
| `PLR0916` too-many-boolean-expressions (preview) | `lint.pylint.max-bool-expr` | 5 | 5 |
| `PLR0917` too-many-positional-arguments | `lint.pylint.max-positional-args` | 5 | `max-args` |

- `PLR1702`: recommended below the default, because ruff's default of 5 almost never fires.
- `PLR0904`: recommended at 10, the same ceiling as a file's public names. A class is a smaller unit than a file, so its interface gets no more room.
