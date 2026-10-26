# Reading the importer distance section

Scope: how to read `pymodularity metrics`'s importer distance section. Not a gate, not a verdict: each listed file is a question for the developer, with the case behind it below.

- Does the file hide a decision from its importers?
  - case: a storage, schema or driver choice lives in one file. Its importers depend on the file without changing when that choice changes, so the file earns its own module.
  - read: its own module says nothing about its directory. A decision that only best dir's code relies on can live inside best dir.
  - example: maistack-research's `services/file_service.py` hides the storage backend from `routes/`. The gain is high, and the file may still rightly stay.
- Are the importers composition root code?
  - case: a module that wires other modules together imports many directories by design. Being wired up from one directory says nothing about where the wired file belongs.
  - test: open the importer. A function that constructs objects and passes them along is a composition root. A function that uses the imported file's behavior is not.
- Is the rel percentage low with a high gain?
  - case: rel 20% to 30% with a high gain count means many directories use the file: a hub, not a misfit in one directory.
  - read: the file's best dir is the biggest consumer among many, not a natural home.
- Do the outside importers belong to a second feature?
  - case: the gain sums up-steps, so 2 importers from another feature lower it little. A move into best dir makes that feature reach into another feature's directory.
  - example: maistack-research's `shared/connector_continuation.py` has 10 importers in `unified_ingestion/resources/connectors/` and 2 in `web_ingestion/`.
  - read: open the importers outside best dir first.
- Does best dir sit below the file?
  - case: a subdirectory importing a module from its parent directory pays up-steps, so best dir points down into that subdirectory.
  - example: `graph_office_adapter/core.py` has 6 of 7 importers in its subdirectory `profiles/`, the plugins it serves.
  - read: a base module that a subdirectory's files build on belongs above them, and the subdirectory's own code belongs inside it.
- Does the gain rest on 1 to 2 edges?
  - case: a gain of 1 or 2 comes from a single importer or two. One new caller in a third directory changes the picture.
  - read: thin evidence, treat as a prompt to look, not as a measured cost.
- Is the best dir only a hint?
  - case: best dir is the candidate with the smallest sum of up-steps. Down-steps cost 0, so best dir tends to sit deep.
  - read: best dir names a direction, not a verified destination. The developer still picks the actual target.
- Can the metric miss a shared decision?
  - case: two files sharing a database table, a schema or a file format belong together even with no import edge between them.
  - read: importer distance sees only import edges. A 0-gain file can still belong elsewhere by this other criterion, and importer distance says nothing about it.
