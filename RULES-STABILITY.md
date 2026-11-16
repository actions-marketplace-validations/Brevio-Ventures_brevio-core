# Rule stability

A rule ID names one rule for good. Your `brevio-core.toml`, your ignore comments, your baseline and your CI logs all
name rules by ID, so an ID never changes its meaning under you.

## The policy

- **IDs are permanent.** An ID is never reused for another rule and never renumbered. A rule whose meaning changes
  gets a new ID.
- **A title may be reworded to what the scan checks.** When a title promised more than the scan finds, or used an
  absolute word, it is narrowed, and the release notes list it under "Reworded". The ID and the status do not change. A
  rule that finds more or less than before has a note in the release notes that says so.
- **A retired rule keeps its ID** with the status `deprecated`. It reports nothing, and the ID is never brought back.
  An ignore comment or config line that names it stays valid.
- **A new rule ships as `preview`** for one minor release. A preview rule reports its findings, as warnings in SARIF,
  but does not fail a scan, whatever `--fail-on` says. `brevio-core check`, which also runs the preview test-custody
  rules (R20), exits 1 on them. After that release a rule becomes `stable` or is changed.
- **A stable rule fails a scan** under `--fail-on new` and `--fail-on all`, and under `--fail-on error` at level
  `error` (the default level; `brevio-core.toml` can set `warn`).

Each rule page says its status, and `brevio-core config` lists the status of every rule that runs on a path.

## How it is enforced

`brevio/pipeline/code_rules/rule_ids.json` records every rule ID of the last release with its catalogue line and its
status. A test (`tests/pipeline/core_export/test_core_rule_ids.py`) fails when an ID in that file has disappeared from
the catalogue, comes back with another catalogue line, or has left `deprecated`, and when a rule the file does not
hold yet is not `preview`. The release is refused on the same checks.

## What changed in a release

The release notes (`RELEASE.md` and the GitHub release) list the rules added, changed (a status moved) and retired
since the last release's ID file.
