# pymodularity internals

How pymodularity works inside. How to use it: [README](../README.md).

## How an absolute import resolves to a file

- scope: absolute imports, `import a.b.c` and `from a.b import c`.
  - exception: a relative import, `from . import x`, resolves from the importing file's own path.
- import roots: the directories an absolute import resolves through.
  - the repo root.
  - every package root, a directory whose `pyproject.toml` has a `[project]` table.
  - a package root's `src/` directory, where one exists.
- match: `a.b.c` resolves to `<root>/a/b/c.py` or `<root>/a/b/c/__init__.py`, for some import root.
  - pymodularity never matches a module name by its suffix alone.
- several matches: 3 tie-breaks, applied in order.
  1. a regular module beats a namespace-package directory.
  2. the import root that is the deepest ancestor of the importer wins.
     - example: `import app.x` inside `services/webagent` resolves to webagent's own `app`, not to another service's `app`.
  3. still tied: pymodularity prints `warning: ambiguous import` on stderr and draws no edge.
- no match: the import counts as third-party or stdlib, and pymodularity draws no edge and prints no warning.
