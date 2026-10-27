# Reading the name groups section

Scope: how to read `pymodularity metrics`'s name groups section. Not a gate, not a verdict: a listed file is a split candidate for the developer to judge.

Parent: [reading the metrics report](metrics.md), which places name groups among the 5 metrics.

Covered below: the join rules, the constant filter, the re-export rule, how to read a finding, and the known blind spots.

## What it measures

Per file, `__init__.py` excluded: the file's top-level names, clustered into groups.

reason for the exclusion: a package's `__init__.py` is a re-export surface, not a unit of its own.

A group is a split candidate when it holds names no other group shares an importer or an internal reference with.

reason: the groups inside one file then behave like 2 or more unrelated files glued together.

## Join rules

Two top-level names join into the same group when:

- one importer imports both of them together, whether by `from file import A, B`, by 2 separate import statements in the same importing file, or by attribute access (`import pkg; pkg.a(); pkg.b()`), or
- one name's body (its def/class body, decorators, bases, defaults, annotations, or an assignment's right-hand side) references the other by name.

Joining is transitive (union-find): if A joins B and B joins C, all 3 land in one group.

## The constant filter

A group counts only if it has >= 1 importer and holds at least one `def`, `async def` or `class` name. A group made only of constants, config values or other plain assignments is dropped before counting.

reason: a constant references nothing, so every constant forms a group of its own, and that floods the output with groups that carry no def or class.

A file is listed when it has >= 2 counting groups.

## The re-export rule

An import of a name `N` reached through a package's `__init__.py` is attributed to the file that actually defines `N`, followed through the chain to that file, not to the `__init__.py` itself.

reason: without this, a package that re-exports its public names from `__init__.py` makes every real importer look like it imports the `__init__.py` instead of the defining file, so the join never happens.

Example: a package `pkg` re-exports `Subject` from `pkg/acl.py` in its `pkg/__init__.py`. A caller imports `Subject` via `pkg` and `acl_sql` directly from `pkg.acl`.

Without the fix, the import of `Subject` counts against `pkg/__init__.py`, never against `pkg/acl.py`. `pkg/acl.py`'s 2 groups never join, so `pkg/acl.py` shows up as a false positive.

## How to read a finding

A listed file holds 2 or more groups with no caller and no body in common.

A finding is a hit when a code change exists that improves the code and removes the finding. 3 such changes exist:

- **Split.** The groups are separate units. One group's names move to another module, the file itself stays.
  - reason: no caller uses names from both groups, so the move splits no caller's imports.
- **Name the shared decision.** The groups share a decision that lives only in strings, comments or call order.
  - kinds: one database table, one global state, the phases of one protocol.
  - repair: one constant, function or type that both groups reference names the decision and joins the groups.
  - example: 2 functions that repeat the same SQL predicate text join once both use one named predicate.
- **Delete.** A group is speculative code, such as a no-op hook for a feature that does not exist yet. Deleting the group and its calls removes the finding.

A finding is a false positive when only a worse change removes it, such as an artificial reference between the groups.

A listed bucket module (`models.py`, `errors.py`, a module of constants) is a finding, not a false positive.

reason: a bucket module that bundles unrelated names is the smell this metric looks for.

## Known blind spots

- **Names that only share an imported name do not join.** A type or a module-level object such as `settings`, imported by many unrelated functions, does not by itself join their names.
  - design decision: an earlier prototype joined names on any shared imported name, including a type and `settings`.
  - That join merged splits that held in practice.
- **Test files are not importers.** Tests are excluded from the whole graph `pymodularity` builds.
  - A group whose only importer is a test file, or that has no importer at all, is dropped before reporting.
  - A name used only by tests never shows up as a group.
- **A star re-export (`from .m import *`) loses the importer.** The re-export rule follows a name by name, so a wildcard import leaves no name to follow, and the names it would have joined show up separately.
- **An aliased re-export (`from .m import a as alpha`) loses the importer.** The outside caller imports `alpha`, a name `m.py` never defines, so the import never resolves back to `a`.
- **A name an `__init__.py` both defines and re-exports loses the importer.** `try: from .m import a / except ImportError: def a(): ...` makes `__init__.py` itself the definer the resolver stops at, so the real name in `m.py` never gets the import.
- **A re-export through a non-`__init__` facade module loses the importer.** `pkg/facade.py: from pkg.m import a` re-exports `a` exactly like an `__init__.py` would, but the re-export rule only follows `__init__.py` files, so the facade's own importers never count against `m.py`.
