# Reading the metrics report

Scope: how to read the 5 sections `pymodularity metrics` prints. Not a gate: a high row is a question for the developer, never a verdict.

Name groups has its own guide: [name_groups.md](name_groups.md).

## What all 5 metrics share

- input: the import graph `pymodularity check` builds, tests excluded.
- coupling seen: through Python imports only. Coupling through tables, config keys, HTTP or strings stays invisible, see "Blind spots" in the [README](../README.md).
- `<repo>`: the tree holding every consumer of the code under study.
  - reason: an importer outside `<repo>` is missing from every count. The 4 coupling metrics come out too low, and name groups splits groups that only an outside importer would join.
- a name counts as 1, whatever it carries. A base class with default methods is 1 name, though its importers depend on its behaviour.
- a directory with no Python file of its own and a single child directory repeats that child's numbers. Only the deepest directory of such a chain gets a row.

## How the 5 metrics relate

| metric | question | counts | boundary |
|---|---|---|---|
| reach | how far does a change spread? | files, transitive | file, directory |
| interface width | how big is the surface a change must keep stable? | names, direct | file, directory |
| exposure share | how much of a file stays hidden? | names used / names defined | file |
| entry files | how many doors does a directory have? | files reached / files | directory |
| name groups | which of a file's names belong together? | groups of names | file |

The first 4 measure coupling. Name groups measures cohesion.

- **Reach and interface width:** reach counts the files a change can reach. Width counts the names a change must keep stable, direct importers only.
  - example: a file exposing 1 name to 1 importer has width 1. Its reach is large when that importer is a base module.
- **Interface width and exposure share:** width is the size of a file's surface. Share is that size divided by everything the file defines.
  - wide and shallow: the costly case, a large surface with little hidden behind it.
  - wide and deep: a large surface in front of a larger hidden inside.
  - why both: width alone can't tell deep from shallow. Share alone ranks a 3-of-3 file next to a 42-of-42 file.
- **Exposure share and entry files:** both ask what crosses a boundary. They differ in boundary and in unit.
  - exposure share: names, at the file boundary. A sibling in the same directory counts as an importer.
  - entry files: files, at the directory boundary. A sibling never counts.
  - consequence: a package can show 1 entry file while every file inside it has a high exposure share. Siblings using each other's names is normal inside a package.
- **Directory width and entry files:** both measure the directory boundary. Width counts the names that cross it, entry files counts the files they come from.
  - few entry files, wide: a front door with a large surface.
  - many entry files, narrow: no front door, but little crosses.
  - many entry files, wide: no front door and a large surface, the costly case.
- **Exposure share and name groups:** both read which importer uses which name. Share counts the used names, name groups clusters them by who uses them together.
  - shared result: a bucket module of unrelated exceptions or types tops the share table and is listed by name groups.

## Reach

- per file:
  - dependents: the files that import it, directly or transitively.
  - dependencies: the files it imports, directly or transitively.
  - reach-score: dependents times dependencies, the pairs of files linked through this file.
- per directory: in counts the outside files reaching into it, out the outside files it reaches. Reach-score is in times out.

Reading a row:

- high reach-score: a file in the middle that passes change in both directions.
- high dependents: a base. A change to it is expensive.
- high dependencies: a fragile file. A change in any file below it can break it.
- a block of rows with identical dependents and dependencies: the files of 1 import cycle. A change to any of them reaches the same files, so read the block as 1 unit.

False readings:

- a base file that imports nothing has a reach-score of 0. Its risk shows in the dependents list.
- an entry point tops the dependencies list, because importing everything is its job.
- a pair as linked only through this file: another import path can link the same pair, so cutting this file need not separate them.
- a cycle file's reach-score as an exact pair count: the other cycle files sit in both lists, so the product also counts each of them paired with itself.

## Interface width

- per file: the distinct names other files import from it, plus the number of importing files.
  - a nameless import (`import x`) counts the target as 1 name.
  - a sibling and a re-exporting `__init__.py` count as importers.
- per directory: the distinct names files outside the directory import from inside it, plus the number of those outside files.

Reading a row: each name is a promise a change must keep. The importer count says how many files break when a promise breaks.

False reading: a shared library has a wide interface, because sharing is its job.

## Exposure share

- per file, `__init__.py` excluded:
  - defined: its top-level names, imports excluded.
  - used: the defined names at least one other file imports, a sibling included.
  - share: used divided by defined.
  - importing files: the distinct files importing at least 1 of its names.
- covered: files defining at least 10 names.
- relation to width: used equals the file's interface width. exception: width also counts nameless imports and names the file imports and passes on.

Reading a row: a high share marks a shallow file that hides little.

False readings:

- the metric counts names, never what a name carries. So it can't tell a file with internals to hide from a file without any.
- a file of exceptions or plain types tops the table because it exposes every name it defines. Its defect is low cohesion, not missing hiding. Name groups lists it when its importers use disjoint subsets of its names.
- a file of only constants tops the table too. Name groups never lists it, because a group needs a `def` or `class`.
- a high share with 1 importing file marks a file split off its only importer, not a shallow module. Read the file and its importer as 1 unit.

## Entry files

- per directory with at least 5 files:
  - entry files: the files inside it that at least 1 file outside it imports directly.
  - share: entry files divided by files.
  - outside importers: the distinct outside files importing anything inside.
- a file counts toward every directory above it.
- a package that outside code reaches only through its `__init__.py` shows 1 entry file.

Reading a row: a high share marks a directory without a front door. Outside code reaches into many of its files.

False reading: many entry files with few outside importers usually marks a registry collecting all files, not a missing front door.

## Name groups

Per file, its top-level names grouped by shared importers and internal references. A file with 2 or more groups is listed.

Join rules, re-exports, how to read a finding and blind spots: [name_groups.md](name_groups.md).
