# pymodularity rule details

The [README](../README.md) lists the rules `check` enforces.
The [showcase](../tests/showcase/README.md) shows each rule as worked cases.
This file holds the definitions neither of them spells out.

## Private items

- private module: a file or a path component named `_x`, with one leading underscore and no dunder.
- package: a directory with an `__init__.py`, or a namespace package directory.
- attribute form: findings from `mod._x` end with ` (via attribute access)`.
- deduplication: an attribute hop that an import statement in the same file already reaches gives no second finding.

## Excluding code

- config: `exclude = [...]` under `[tool.pymodularity]` in the repo root's own `pyproject.toml`. A missing file or table means no excludes.
- CLI: a repeatable `--exclude PATTERN` on `check`, `graph` and `metrics`. CLI values merge with the config list, never replace it.
- matching: each entry is an `fnmatch` pattern against the POSIX path relative to the repo root. A file is excluded if its own path or any parent directory's path matches.
- depth: `mcp-poi/ds1_client` excludes that one tree.
- depth, nested: `*/generated` excludes every `generated` directory below the repo root. A `generated` directory at the root needs the plain pattern `generated`.
- effect: an excluded tree is invisible to cycles, private items, structure, metrics and graph.
- effect on deptry: a package root under an excluded tree gets no deptry run. deptry still scans an excluded subtree inside a kept package root, because that subtree's imports are runtime dependencies of the package.
- imports: an import from kept code into excluded code behaves like an import of code the tool can't resolve.
- use cases: generated code (an OpenAPI client, protobuf stubs) and vendored code.

## Dependency declarations

- package root: a directory whose `pyproject.toml` has a `[project]` table.
- command: `uv run --isolated --directory <pkg> --with deptry==<pinned version> --frozen deptry . --known-first-party <name> --json-output <tmp>`.
- `--known-first-party`: one per top-level package/module under `<pkg>` (or `<pkg>/src` if that exists), so deptry does not mistake a hatch-autodetected src-layout package for a transitive dependency of itself (DEP003).
- merge, not replace: deptry's own `--known-first-party` on the command line would drop a `[tool.deptry] known_first_party` already set in the package's pyproject.toml, so `check` unions the two before building the command.
- nested package root: `--extend-exclude` removes it from its parent's scan.
- deptry's own config: deptry reads the `[tool.deptry]` table of each package's `pyproject.toml`, so every setting there applies.
- used without an import: a CLI tool, a type stub, a driver named in a URL or a plugin loaded by config gets a false DEP002.
  - fix: list it under `[tool.deptry.per_rule_ignores]` in that package.
- dev code outside `tests/`: an `eval/` or `scripts/` directory importing dev dependencies gets DEP004.
  - fix: add the directory to `[tool.deptry] extend_exclude`.
- skip: `check` skips a package that declares no runtime dependencies or has no `uv.lock` of its own.
- missing `uv`: `check` exits 2 with an error and never skips silently.
- isolation: `--isolated` runs each deptry invocation in a throwaway environment.
- no `.venv` side effect: `check` never creates or changes a `.venv` in the scanned repo. A pre-existing `.venv` is untouched.

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

### A finding is a symptom, not a target

- a finding names where to look, never what to do.
  - reason: a limit counts lines, entries or names. The cause sits in the dependencies between the code behind that count.
- anti-pattern: moving, splitting or regrouping files until the count passes.
  - example: claudia's `claudia_core/services/` held 21 entries. 3 layouts moved files into subdirectories: 4 files into `segment_lifecycle/`, a variant also moving `agents.py` and `TurnHandle`, and a whole-`services/` layout of 4 to 6 folders.
  - outcome: each layout closed a directory cycle or produced bucket names such as `segment_decisions` and `turn_support`. Some files sat where they did only to dodge a cycle.
  - cause: about 16 of those files import each other in both directions. Inside such a block a move only shifts where the cycle closes, so no layout meets the limit.
- response: read the imports at symbol level, in a few files at a time.
  - per edge: which function, class or variable causes it, and why the importer needs that name.
  - then remove or invert one dependency, and rerun `check`.
  - unit of change: a function, class or variable, not a file.
- an open finding is acceptable. Progress is fewer two-way dependencies, not a lower count, and one redesign need not reach 0.

## Names carry a scope

- rule: a scanned `.py` file's stem, and a directory holding scanned code, must have at least 2 words. Fixed, no config, no baseline, like the structure limits above.
- word: a non-empty run between `_`, after stripping leading underscores. `models` is 1 word, `_common` is 1 (the leading underscore is stripped first, then the rest has no `_` left to split on), `s3_client` is 2, `github_pagination` is 2.
- exempt files: `__init__`, `__main__`, `conftest`, `setup`, `wsgi`, `asgi`, `_version`. Each is a language or tool contract, not an author's choice: `__init__`/`__main__` are Python's package and entry-point hooks, `conftest` is pytest's fixture file, `setup` is `setuptools`'s build-script name, `wsgi`/`asgi` are the names application servers look for, `_version` is the single-source-of-truth file most build backends read. `main.py` is not on the list and is flagged like any other file.
- exempt directories: a package root (the directory of the nearest ancestor `pyproject.toml` with a `[project]` table, the same anchor `find_package_roots` uses elsewhere), `src`, `tests`, `migrations`, `alembic`, and any directory whose parent is named `src`. A package root is exempt because a PyPI distribution name is conventionally one word; `tests`, `migrations` and `alembic` hold files a framework or tool names, not the author.
- the `src` exemption and its one-word import package: `src` itself is the src-layout convention, and a directory directly under `src` is exempt too, because the src layout is how a project gets a one-word *import* package name (`src/my_tool/`) that differs from the package root. A flat layout has no such escape: `webagent/webagent/` is flagged, because `webagent/` (the inner one) sits under the package root, not under a directory named `src`. A directory nested deeper than one level under `src`, such as `src/pkg/sub/`, is flagged too: its parent is `pkg`, not `src`.
- accepted hole: `src/shared/` passes the rule. `shared` is one word, but every top-level entry directly under `src` is treated like a distribution name, and PyPI distribution names are one word by convention, so the rule does not look past that.
- not read: this rule never reads a project's `hatch`/`setuptools` package list (`[tool.hatch.build]`, `[tool.setuptools.packages]`) to learn the actual import package name. Consequence: an import package named after neither the project directory nor anything under `src/` is flagged on its own merits, word count only. `exclude` is no escape from a flagged directory: it hides that directory's contents from every rule, not only this one, so excluding it to silence this finding also hides it from cycles, privacy and the other structure limits.
- why: a single word names one scope dimension, and one dimension admits the whole of something: all models, all of GitHub, all chunking. A file named for one dimension grows with its directory, not with one concept, and every importer receives all of it, whether it needed one model or every model. Two words are a conjunction: `github_pagination` is GitHub *and* pagination, narrower than either word alone by construction.
- measured, from a real FastAPI monorepo (name withheld): a `models.py` holding 61 public names across 1160 lines; a second `models.py` elsewhere with only 11 names but 255 files depending on it, because "models" is the one place every caller expects to find a model. A `github.py` of 728 lines holding an HTTP client, a reconciliation record and a connector class with 20 methods covering repository sync, file documents and thread documents: five responsibilities, one vendor word.
- why a ban on one word, not a list of banned words: a list grows by one word per sighting, and its escape hides -- `domain_objects.py` reads like a real name and slips through unless that exact phrase is already on the list. A ban on word count has one escape, and the escape exposes itself: `model_classes.py` still names no concept, and any reviewer can see that without consulting a list.
- why counted per path segment, not per whole path: `connectors/github.py` and `app/models.py` each have 2 words somewhere in their path, but counting the full path would pass both while missing the point -- the file itself is still 1 word, and every 1-word file observed in practice sits inside a 1-word directory, so a whole-path count never fires where the rule is needed.
- the second word must set a scope, not restate the kind: `project_models.py` passes and is progress, because "project" narrows which models. `model_classes.py` passes the word count and is not progress, because "classes" restates that it is Python code, not which models.
- renaming is not the fix, the thought is: a finding asks what the unit holds that must sit together, and what in it does not belong. The answer decides the action, and a new name is only one of four: split the file along the answer, move the part that belongs elsewhere, merge it with the neighbour it changes with, or rename, when the unit already holds one thing and only the name hid it. A name found that way has two words by itself.
  - from this repo's own rollout: `cli.py` held three subcommands, so the answer was a split into `check_command.py`, `graph_command.py`, `metrics_command.py` and the dispatcher in `__main__.py`. The split then tripped the entries-per-directory limit, and the rule files moved into `_check_rules/`, a second action the name question caused. `_parse/` already held one thing, parsing imports, so `_import_parse/` was a pure rename.
  - why the rule fires on the name and not on a line count or an import metric: a line count fires once the file has grown, an import metric once the file has importers. The name question fires at 0 lines, when the split is free.

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
