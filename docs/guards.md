# The guard format

| In Plain English (For Anyone) | Technical Contract (For Engineers) |
| --- | --- |
| **What it does:** Turns a bug fix you commit into a check that reports the same lines if they come back. `brevio-core scan`, the agent hook, the MCP server and the pull-request Action report a hit.<br>**When to use it:** Right after fixing a bug (`brevio-core learn`), or when installing a starter rule pack (`brevio-core add`). | **Command:** `brevio-core learn`, `learn --dry-run`, `learn list`, `learn show <id>`, `learn import`, `learn --check`<br>**Artifacts:** `.brevio/guards.json` and `.agents/rules/<guard-id>.md`<br>**Exit Codes:** The exit code is 0 when a guard is kept or a file is clean, 1 when no guard is kept or a file holds a pattern, 2 for a usage error. |

`brevio-core learn` turns a fixed defect into a guard: the lines the fix removed become patterns to look for. Guards
live in `.brevio/guards.json` in your repository, next to an agent rule in `.agents/rules/<guard id>.md`.
Commit both, so your team and their agents get them. `brevio-core add <pack>` writes guards in the same format.

A guard is text, not an understanding of the bug. A short or common line can match in many places. That is why a guard
is tried on your history before it is kept, why a new one only reports, and why a person decides when it counts.

## What `learn` checks before it keeps a guard

- **The control run.** The guard must match the lines the fix removed, and must not match the file the fix produced. A
  guard that fails is not written, and the reason is printed. A pattern that looks like a credential also fails, and
  the text is hidden in what is printed, because the guard file is committed.
- **The replay.** The guard is run over the lines the last commits added (`--replay N` sets how many; `learn --help`
  shows the default) and the changes it would have flagged are listed by commit and file. The first hit may be the
  change that brought the defect in. The rest are what you judge: if they are not the defect, the guard is too broad.
- **Incidental lines.** A removed line that the fixed file holds as well, such as an import the fix moved, is dropped from the
  guard, and `learn` prints `dropped: <pattern> (also in the fixed file)`. The guard is refused only when no pattern is left.
  The title of the guard comes from the first pattern that is kept.
- **The call, not only the line.** A removed `return yaml.load(f)` or `cfg = yaml.load(f)` is kept as the call, `yaml.load(`,
  when that passes the control run and the replay can be read, so the same call in another assignment is found. `learn`
  prints both counts, the replay and the lines that match today, for the call and for the line alone.
- **The second line of `learn`** names the change that brought the line in, with its date, and the files the guard reports in.
  The title is the subject of that change unless `--name` gives one. The guard ID does not follow the subject of that change, so the
  same fix makes the same guard.
- **`learn --dry-run`** prints the guard, the control run, the replay and the lines of the current tree that match, and
  writes nothing.

`learn` does not overwrite a guard that is in the file already, so a promotion, the wrong marks and a switch-off stay;
`--name` gives a new guard another ID. The last line of `learn` names the files to commit.

## How a guard is reported

- A hit is a finding with the rule `guard:<id>`, in the same lines, SARIF, Markdown and agent format as the findings of a
  rule. A SARIF result carries a fingerprint made of the path, the rule and the text of the line, so a hit that moves is
  the same hit.
- **On trial:** the finding says so and is reported only. A trial guard does not fail a run.
- **Active:** the finding is a warning, and a warning does not fail a run either, whatever `--fail-on` says. `learn --check`
  fails on a hit, and `scan` does not unless the guard is raised. To make an active guard fail, raise it
  in `brevio-core.toml`:

  ```toml
  [rules."guard:guard-fix-await-sleep-in-handler-ts"]
  level = "error"
  ```

- The baseline, an ignore comment (`brevio-core: ignore guard:<id> -- reason`) and the `ignore` list of
  `brevio-core.toml` treat a guard hit like a rule's finding. The agent hook does not honour an ignore comment.
- After an edit the hook shows the agent a hit. When the agent is about to stop, a hit of a guard on trial, or of a
  guard that is a warning, does not keep it working; a guard raised to an error does.
- The scan reads the file and writes nothing. A hit does not change a counter. The guard file and the agent rules
  under `.agents/rules/` are not scanned for guard hits, because they hold the patterns.
- A scan of a whole folder reads the code files it recognises. A guard for another kind of file (a Dockerfile, say) is
  reported by `scan --diff`, by a file you name, and by `learn --check`, not by a folder scan.

## The life of a guard

- **Trial.** A new guard is on trial until the date in `trial_ends`, written when it was kept. After that date it stays on
  trial, and reported, until a person promotes or dismisses it. `brevio-core learn list` shows the date.
- **Promotion (preview).** `brevio-core learn promote <id>` makes a trial guard active. It needs a trial that has ended,
  no hit marked wrong, and a replay of recent history that flags nothing the guard did not flag when it was kept.
  Silence does not promote a guard: a run with no hit is the usual run. A guard without `trial_ends`, written by the first
  release, gets the end date from its `created_at` plus the trial length of a new guard.
- **Accepting hits (preview).** When the replay flags a real return of the defect, read the changes with `git show`, then run
  `brevio-core learn promote <id> --accept` at a terminal. The hits join `replay.accepted` and the guard is promoted, if
  the trial has ended and no hit is marked wrong. Plain `learn promote` is unchanged.
- **Show (preview).** `brevio-core learn show <id>` prints the title, what the guard watches and looks for, the trial end, the
  counts, what the replay flags now by commit with its subject, and the lines of the repository that match today. It
  writes nothing. `learn list` gives the hits `learn --check` found.
- **Wrong hits (preview).** `brevio-core learn wrong <id> path:line` marks the hit on that line wrong. The line must be a hit
  of the guard. A hit is the path, the rule and the text of the line, so the same hit marked twice, or moved to another
  line, counts once; `wrong_hits` in the file keeps them. Without `path:line` the call counts one more mark, so a repeated
  call adds up. The command prints how many wrong hits switch the guard off. When it reaches that number the guard goes to
  `off`, the command says so, and `status_note` in the file says why.
- **Dismiss (preview).** `brevio-core learn dismiss <id> [--reason TEXT]` switches a guard off. `dismiss` and `wrong` change
  what the gate reports, so they run only where a person is typing: they are refused when input or output is not a
  terminal. The agent hook runs neither, and the MCP server has no tool for either. An agent that
  drives a real terminal is not stopped by that check.
- An off guard stays in the file with a `dismissed` record: how it was switched off, when, the reason, and a hash
  (`was`) of the patterns and paths it had. Its agent rule file is deleted, so the agent is not told to avoid lines a
  person decided to allow; that deletion is part of what you commit. To switch the guard on again, set `status` back by
  hand and restore the rule file from git.
- **Review.** `brevio-core scan --diff` (the Action runs it) reports a `suppression` finding for a guard that a change
  removes, switches off without the `dismissed` record that matches the guard it had, narrows (a pattern or a watched path
  taken out), or puts back on trial from active. The agent hook reports the same for the uncommitted changes when the
  agent is about to stop, and after an edit of the guard file. `--allow-new-suppressions` hides them for a change a person
  reviewed. The record is part of the file: whoever can write the file can write the record, so this is a prompt to
  review, not a lock.

The older status `retired` is still read as `off`.

## Starter packs

`brevio-core add <pack>` installs a hand-written pack. A pack guard is installed as an active warning, not on trial: it
reports and does not fail a run unless `brevio-core.toml` raises it to an error. A pack guard says "pack guard" in a
finding, and a `label` field says in words what it looks for.

- On `add`, the guards are replayed over the recent history of your repository, and the command prints how many past
  changes it would have flagged. A count that looks high is a sign the pack does not fit.
- A pack can name a dependency it is for. When `pyproject.toml`, a `requirements` file or `package.json` does not hold
  it, `add` prints a note. The pack still installs, and its guards flag matching lines in the files they watch.
- A guard may carry `file_contains`, a literal checked once against the whole file before the line patterns run. The
  Next.js pack uses it to flag an async page or a `metadata` export only in a file marked `use client`.
  A guard file without the field works as before.
- A guard file written by an earlier release keeps its old pack guards. Run `brevio-core add <pack>` again to refresh them;
  that also resets the status and counters of the pack's guards.

## `learn import` (preview)

`brevio-core learn import <file>` brings the guards of a guards file from another repository. It reads a path on your
machine and nothing else. It takes no address and no git remote. It does not contact a service.

- A guard is rebuilt from the fields the tool knows. Its status, counters and replay in the other repository are not
  taken. A pattern that looks like a credential, a pattern that is not run as the expression it reads as, and an ID that
  is not plain text are refused.
- A guard is replayed over the recent history of this repository, and the command prints how many past changes it
  would have flagged here.
- The command needs a terminal and a typed yes. The guards are written on trial: they report and do not fail a run.
- A guard ID that is in your file already is skipped. `learn import` does not overwrite a guard.
- No agent rule is written at import, because an agent reads that file as an instruction. `learn promote` writes it, and
  for an imported guard it needs a terminal too.

Sharing across an organisation from one place is not built.

## `.brevio/guards.json`

```json
{
  "version": 1,
  "guards": {
    "guard-fix-await-sleep-in-handler-ts": {
      "id": "guard-fix-await-sleep-in-handler-ts",
      "title": "Fix await sleep in handler.ts",
      "category": "robustness",
      "source_file": "src/api/handler.ts",
      "file_patterns": ["src/api/**/*.ts", "**/*.ts"],
      "patterns": ["await sleep(SECONDS * MS);"],
      "status": "trial",
      "trial_ends": "2026-11-26",
      "replay": {"checked": 40, "limit": 50, "flagged": 1, "accepted": ["a1b2c3d4e5f6:src/api/handler.ts"], "at": "2026-11-12"},
      "runs_evaluated": 0,
      "false_fails": 0,
      "intercepted": 0,
      "rule_file": ".agents/rules/guard-fix-await-sleep-in-handler-ts.md",
      "created_at": "2026-11-12T09:30:00Z",
      "updated_at": "2026-11-12T09:30:00Z"
    }
  }
}
```

The file format is a preview and may change; `version` says which one a file has.

| Field | What it holds |
| --- | --- |
| `id` | `guard-` and a slug of the title; the key of the entry |
| `title` | `--name`, or "Fix <first removed line, cut at a word> in <file name>" |
| `category` | `--category`: `security`, `robustness`, `functional`, `compliance` or `finops` |
| `source_file` | the file the fix changed |
| `file_patterns` | the globs of the files the guard watches: the fixed file's folder and its extension; for a file at the root, the extension |
| `patterns` | up to a few removed lines, whitespace folded. A guard hits when a line contains one of them as text. A pattern that starts with `re:`, or holds a regular-expression token such as `.*` or `\s`, is read as a regular expression (the starter packs use them); a regular expression is not run on a long line, and one that repeats a repeat or holds more than a few unbounded repeats is read as plain text, with a warning from the scan |
| `match` | `"all"` when a hit needs the whole set of patterns in the lines of one file (the patterns came from one hunk); a guard without the field hits on one pattern, as guards of the first release do |
| `wrong_hits` | fingerprints of the hits a person named with `learn wrong <id> path:line` |
| `status` | `trial`, `active` or `off` |
| `trial_ends` | the date the trial ends; a person promotes after it |
| `replay` | what the replay looked at when the guard was kept: commits checked, the limit, how many changes it flagged, and which (`commit:file`) |
| `runs_evaluated`, `false_fails`, `intercepted` | counters: runs of `learn --check` that saw a watched file, hits marked wrong, and hits found by `learn --check` |
| `status_note`, `dismissed` | present once a guard is switched off or promoted: why, and the record |
| `rule_file` | the agent rule written beside it |
| `created_at`, `updated_at` | UTC times |

`learn --check` and a scan that records a hit write `.brevio/stats.json`, a count with times that `gain` reads. It stays on
your machine: the first time it is written inside a git work tree, its path goes into `.git/info/exclude`, so it is not
offered for a commit.

## The agent rule

`.agents/rules/<guard id>.md` tells a coding agent which lines to leave out. Its front matter names the files it applies
to, so an agent that reads rules by path loads it only for those files:

```markdown
---
description: "Learned Guard: Fix await sleep in handler.ts"
paths: ["src/api/**/*.ts", "**/*.ts"]
---
# Invariant: Fix await sleep in handler.ts (robustness)
```

## Editing guards by hand

The file is plain JSON; edit it like a file in review. Narrow `file_patterns` or remove a pattern that is too general
(a change that does is reported for review, as above), or delete the entry and its rule file to remove a guard. To
switch one off with a record, use `learn dismiss`.
