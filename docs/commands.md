# Command reference

Every `brevio-core` command and option, generated from the argument definitions in the code; do not edit by hand. Unless a command says otherwise, the exit code is 0 for success, 1 for findings and 2 for a usage error. `brevio-core <command> --help` prints the same text.

| Command | What it does |
| --- | --- |
| [`brevio-core scan`](#brevio-core-scan) | scan files, folders or a diff against the 14 rules the scanner checks (of 129 documented) |
| [`brevio-core baseline`](#brevio-core-baseline) | record the current findings in .brevio/baseline.json, so scan reports only new ones |
| [`brevio-core config`](#brevio-core-config) | show the brevio-core.toml settings that apply to a file or folder |
| [`brevio-core explain`](#brevio-core-explain) | explain a rule: why it exists, how to fix it, and its measured precision |
| [`brevio-core rules`](#brevio-core-rules) | list or query documented rules |
| [`brevio-core leaks`](#brevio-core-leaks) | check a folder for stray URLs, emails and paths that should not be public |
| [`brevio-core help`](#brevio-core-help) | show the short list of commands to start with |
| [`brevio-core learn`](#brevio-core-learn) | compile a fixed defect (a git diff) into a local guard and an agent rule |
| [`brevio-core hook`](#brevio-core-hook) | install the scan hook into the coding agents found here (hook install \| status \| detect) |
| [`brevio-core gain`](#brevio-core-gain) | print what the local ledger recorded: hits found by learn --check, learned guards, savings only when measured |
| [`brevio-core add`](#brevio-core-add) | add a hand-written starter guard pack by name (no name lists the packs) |
| [`brevio-core pack`](#brevio-core-pack) | install or list hand-written starter guard packs |
| [`brevio-core mcp`](#brevio-core-mcp) | serve scan, explain, list_rules and learn to an agent over MCP on stdin and stdout |
| [`brevio-core init`](#brevio-core-init) | set this repository up in one step: the Action, agent rules and a baseline (init --undo takes it back) |
| [`brevio-core aeo`](#brevio-core-aeo) | score a folder of built HTML pages, with its robots.txt and llms.txt, against the AEO rubric |
| [`brevio-core check`](#brevio-core-check) | run the pre-PR flight check (rules, test custody, test imports, conflict markers and complexity) |
| [`brevio-core brief`](#brevio-core-brief) | print the rule & trap briefing for the files you are about to edit (no model call) |
| [`brevio-core score`](#brevio-core-score) | preview: count the findings of a scan by rule and say what to do next |
| [`brevio-core roast`](#brevio-core-roast) | preview: the same counts as score, under its earlier name |
| [`brevio-core map`](#brevio-core-map) | build a Personalized PageRank repository symbol map and index (no model call) |
| [`brevio-core heal`](#brevio-core-heal) | scan, apply the deterministic fixes, and print what still needs a person (exit 1 while anything remains) |
| [`brevio-core shield`](#brevio-core-shield) | answer a maintainer's pull-request comment (fix, explain, roast, triage, heal) from the command line |
| [`brevio-core patch`](#brevio-core-patch) | apply SEARCH/REPLACE blocks or a unified diff, only when the rules find the changed lines clean |
| [`brevio-core connect`](#brevio-core-connect) | detect stack and coding clients and write CLAUDE.md, CONSTITUTION.md & roadmap.json (--dry-run lists them first) |
| [`brevio-core plan`](#brevio-core-plan) | create a single-task implementation plan (.brevio/task_plan.json & .brevio/TASK_PLAN.md) |
| [`brevio-core build`](#brevio-core-build) | execute single-task build preparation and deterministic auto-fixes |
| [`brevio-core qa`](#brevio-core-qa) | run the single-task pre-PR quality gates and count the findings (of the task's diff; --all counts the whole repository) |
| [`brevio-core pr`](#brevio-core-pr) | run the QA gates and write a pull-request body from what they found (.brevio/PR_BODY.md) |
| [`brevio-core task`](#brevio-core-task) | run the single-task local workflow (connect -&gt; plan -&gt; build -&gt; qa -&gt; pr) |
| [`brevio-core up`](#brevio-core-up) | show what a team sync would take from this repository (uploads nothing) |

## `brevio-core scan`

Scan files, folders or a diff against the 14 rules the scanner checks (of 129 documented).

```
brevio-core scan [options] [paths ...]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[paths ...]` |  | files or folders to scan (default: the current folder) |
| `--file PATH` |  | a file to scan (repeatable) |
| `--diff [BASE]` |  | scan only the lines added against BASE (default origin/main, else main, else HEAD); a BASE the checkout does not have exits 2 |
| `--sarif FILE` |  | also write the findings to FILE as SARIF |
| `--forbid-ignores` |  | never honour an ignore comment (the agent hook runs with this, so an agent cannot silence a rule) |
| `--surface` |  | also run the 9 surface-scoped rules (UI blur, SSR reveal, GTM deferral, webhook HMAC, integer cents, self-mocking tests, weak assertions, contract purity, package imports) |
| `--fix` |  | rewrite in place what a rule has one correct fix for, then scan again |
| `--dry-run` |  | with --fix: print the fixes as a unified diff and write nothing |
| `--fail-on {new,all,none,error}` | `new` | new (default): baselined findings are hidden and pass; all: ignore the baseline, any finding fails; none: report what is not baselined and always exit 0; error: like new, but only error-level findings fail (warn-level ones are reported). A preview rule does not fail a run; a learned guard fails one only at error level |
| `--allow-new-suppressions` |  | with --diff: do not report the ignore comments and baseline entries the change adds (for a change a person has reviewed) |
| `--format {text,markdown,agent,json,github}` | `text` | how to print the findings (agent: one short line each with a fix hint, grouped by rule, at most 20) |
| `--rules-url BASE` |  | with --format markdown: link each rule ID to BASE&lt;ID&gt; |
| `--facts` |  | with --format markdown: end the report with one line of the facts of this run (version, rules that ran, files read, base, head, time) |
| `--no-reproduce` |  | with --facts: leave out the command that repeats the scan (the Action sets it when it installs from a source) |
| `--no-upgrade-line` |  | with --format markdown: leave out the closing Teams line (the Action's footer: false) |

## `brevio-core baseline`

Record the current findings in .brevio/baseline.json, so scan reports only new ones.

```
brevio-core baseline [options] [paths ...]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[paths ...]` |  | files or folders (default: the current folder) |
| `--diff [BASE]` |  | record only the findings on the lines added against BASE (default origin/main, else main, else HEAD); a BASE the checkout does not have exits 2 |
| `--prune` |  | only drop the entries no finding matches any more, and list them |

## `brevio-core config`

Show the brevio-core.toml settings that apply to a file or folder.

```
brevio-core config [options] [path]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[path]` |  | the file or folder (default: the current folder) |
| `--json` |  | print it as JSON |

## `brevio-core explain`

Explain a rule: why it exists, how to fix it, and its measured precision.

```
brevio-core explain rule_id
```

| Argument | Default | What it does |
| --- | --- | --- |
| `rule_id` |  | the rule id (e.g. R13.1) |

## `brevio-core rules`

List or query documented rules.

```
brevio-core rules [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--json` |  | print rules catalog as JSON |
| `-q, --query QUERY` |  | filter rules by text query |
| `--scanned` |  | list only the rules that scan checks (--surface adds more) |

## `brevio-core leaks`

Check a folder for stray URLs, emails and paths that should not be public.

```
brevio-core leaks [root]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[root]` |  | the folder to check (default: the current folder) |

## `brevio-core help`

Show the short list of commands to start with.

```
brevio-core help [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--all` |  | show the full list of commands |

## `brevio-core learn`

Compile a fixed defect (a git diff) into a local guard and an agent rule.

Compile fixed defects into guards and progressive agent rules. A guard is checked before it is kept (a control run, and a replay over recent commits) and starts on trial: it reports and does not fail a run until a person promotes it. ACTION (preview): list shows the guards; show prints one in full; promote makes a trial guard active; wrong marks one hit wrong; dismiss switches a guard off (wrong and dismiss run only at a terminal); import FILE brings the guards of a guards file on this machine, on trial, after a replay on this repository and a typed yes.

```
brevio-core learn [options] [ACTION] [GUARD_ID] [PATH:LINE]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[ACTION]` |  | list \| show GUARD_ID \| promote GUARD_ID [--accept] \| wrong GUARD_ID [PATH:LINE] \| dismiss GUARD_ID [--reason TEXT] \| import FILE (preview); leave out to compile a guard |
| `[GUARD_ID]` |  | the guard an ACTION acts on, or the file `import` reads |
| `[PATH:LINE]` |  | with wrong (preview): the hit that is wrong; a named hit is counted once; a call that names none adds one mark |
| `--accept` |  | with promote (preview): take in the replay hits that were not there when the guard was kept, then promote (at a terminal only) |
| `--reason REASON` |  | with dismiss (preview): why the guard is switched off, kept in the file |
| `--diff DIFF` | `HEAD~1` | Git diff commit or range (default: HEAD~1) |
| `--staged` |  | compile the staged fix (git diff --cached) instead of --diff |
| `--worktree` |  | compile the uncommitted fix (the working tree against HEAD) |
| `--dry-run` |  | print the guard, its control run, what it would have flagged in recent commits and how many lines of the repository it matches; write nothing |
| `--replay N` | `50` | how many recent commits the replay looks at (preview; default: 50) |
| `--file FILE` |  | Specific file to inspect or extract diff from |
| `--name NAME` |  | Human-readable title for the synthesized guard |
| `--category {security,robustness,functional,compliance,finops}` | `robustness` |  |
| `--json` |  | Output result as JSON |
| `--check FILE` |  | Run the learned guards over these files instead of compiling one |

## `brevio-core hook`

Install the scan hook into the coding agents found here (hook install | status | detect).

Wire hooks and progressive rules into coding agents (Claude, Cursor, Hermes); the hooks make no model call.

```
brevio-core hook [options] {install,status,detect,run} ...
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--json` |  | Output status as JSON |

### `brevio-core hook install`

Install hooks into detected agent configurations.

```
brevio-core hook install [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--all` |  | Force install into all supported agents |
| `--target TARGET` |  | Comma-separated agents (claude_code,cursor,hermes,openclaw) |
| `--json` |  | Output status as JSON |

### `brevio-core hook status`

Check hook installation status.

```
brevio-core hook status [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--json` |  | Output status as JSON |

### `brevio-core hook detect`

Auto-detect available LLM clients, agents and local daemons.

```
brevio-core hook detect [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--json` |  | Output status as JSON |

### `brevio-core hook run`

The hook itself: reads a client's event JSON on stdin (run --event post-edit|stop).

The hook a coding client calls: reads its event JSON on stdin and answers with the findings in that client's format. Exits 0, and 2 at stop when it keeps the agent working (the reason is on stderr too).

```
brevio-core hook run [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--event {post-edit,post-command,stop}` |  | post-edit: after a file edit; post-command: after a shell command (a fix commit gets a `learn` suggestion); stop: before the agent finishes |
| `--client {claude-code,cursor}` | `claude-code` | the client whose config holds this hook (the reply follows the client that sent the event) |
| `--budget SECONDS` | `2.0` | give up quietly after this long (default 2) |

## `brevio-core gain`

Print what the local ledger recorded: hits found by learn --check, learned guards, savings only when measured.

Show what the local ledger recorded: the hits `learn --check` found, learned guards, and savings when measured.

```
brevio-core gain [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--json` |  | Output metrics as JSON |
| `--no-upgrade-line` |  | leave out the closing Teams line |

## `brevio-core add`

Add a hand-written starter guard pack by name (no name lists the packs).

Install the pack called NAME. `brevio-core add` and `brevio-core pack` with no name list the packs.

```
brevio-core add [options] pack
```

| Argument | Default | What it does |
| --- | --- | --- |
| `pack` |  | Name of the pack to install |
| `--json` |  | Output result as JSON |

## `brevio-core pack`

Install or list hand-written starter guard packs.

Install hand-written starter guard packs (Next.js, Stripe, FastAPI, EU AI Act, Supabase).

```
brevio-core pack [options] {list,add} ...
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--json` |  | Output result as JSON |

### `brevio-core pack list`

List the starter guard packs.

```
brevio-core pack list [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--json` |  | Output result as JSON |

### `brevio-core pack add`

Add a starter guard pack.

Install the pack called NAME. `brevio-core add` and `brevio-core pack` with no name list the packs.

```
brevio-core pack add [options] pack
```

| Argument | Default | What it does |
| --- | --- | --- |
| `pack` |  | Name of the pack to install |
| `--json` |  | Output result as JSON |

## `brevio-core mcp`

Serve scan, explain, list_rules and learn to an agent over MCP on stdin and stdout.

It takes no arguments.

## `brevio-core init`

Set this repository up in one step: the Action, agent rules and a baseline (init --undo takes it back).

```
brevio-core init [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--with PARTS` |  | also write these, comma-separated: precommit, hook, mcp |
| `--without PARTS` |  | leave these out: action, agents-md, baseline |
| `--dry-run` |  | print the plan and write nothing |
| `--yes, -y` |  | write without asking |
| `--undo` |  | remove what init wrote and is unchanged since |
| `--personal` |  | install personal agent hooks and MCP config only, git-excluded via .git/info/exclude (touches no tracked files) |
| `--specs` |  | also write starter CLAUDE.md, CONSTITUTION.md and roadmap.json |
| `--wizard` |  | run the interactive onboarding wizard |

## `brevio-core aeo`

Score a folder of built HTML pages, with its robots.txt and llms.txt, against the AEO rubric.

It reads local files only; save a page before you score it.

```
brevio-core aeo [options] target
```

| Argument | Default | What it does |
| --- | --- | --- |
| `target` |  | a folder of built pages (with robots.txt and llms.txt at its root), or one .html page |
| `--min SCORE` | `0` | exit 1 when a page scores below SCORE, out of 100 (default 0: report only) |
| `--format {text,markdown,json}` | `text` | how to print the scorecard |
| `--generate-llms` |  | write a missing llms.txt at the target folder's root from its HTML pages before scoring |

## `brevio-core check`

Run the pre-PR flight check (rules, test custody, test imports, conflict markers and complexity).

```
brevio-core check [options] [paths ...]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--base BASE` |  | git base ref to compare against (default: the base of scan --diff: origin/main, main or HEAD) |
| `--json` |  | output JSON |
| `--format {text,markdown,json}` | `text` | output format |
| `[paths ...]` |  | optional paths to check |

## `brevio-core brief`

Print the rule & trap briefing for the files you are about to edit (no model call).

```
brevio-core brief [options] [files ...]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[files ...]` |  | files to generate briefing for |
| `--json` |  | output briefing as JSON |
| `--record ('RULE', 'TRAP', 'FIX')` |  | record a Trap -&gt; Fix episode for the first named file |

## `brevio-core score`

Preview: count the findings of a scan by rule and say what to do next.

```
brevio-core score [options] [paths ...]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[paths ...]` |  | files or folders to count (default: the current folder) |
| `--diff [BASE]` |  | count only the lines added against BASE (default origin/main) |
| `--format {text,markdown,json}` | `text` | output format (default: text) |
| `--markdown` |  | alias for --format markdown |
| `--json` |  | output the counts as JSON |
| `--no-upgrade-line` |  | leave out the closing Teams line |

## `brevio-core roast`

Preview: the same counts as score, under its earlier name.

preview: count the findings of a scan by rule and say what to do next

```
brevio-core roast [options] [paths ...]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[paths ...]` |  | files or folders to count (default: the current folder) |
| `--diff [BASE]` |  | count only the lines added against BASE (default origin/main) |
| `--format {text,markdown,json}` | `text` | output format (default: text) |
| `--markdown` |  | alias for --format markdown |
| `--json` |  | output the counts as JSON |
| `--no-upgrade-line` |  | leave out the closing Teams line |

## `brevio-core map`

Build a Personalized PageRank repository symbol map and index (no model call).

Zero-Dependency Personalized PageRank Repo Map

```
brevio-core map [options] [positional_files ...]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[positional_files ...]` |  | Seed files for Personalized PageRank (positional) |
| `--root ROOT` | `.` | Root directory of the repository (default: '.') |
| `--files FILES` |  | Seed files for Personalized PageRank (comma-separated or multiple) |
| `--budget BUDGET` | `4000` | Token budget for output map (default: 4000) |
| `--json` |  | Output map as JSON |
| `--format {json,text}` |  | Output format (json or text) |

## `brevio-core heal`

Scan, apply the deterministic fixes, and print what still needs a person (exit 1 while anything remains).

Autonomous self-healing loop: rewrites files with deterministic fixes (--no-fix only reports), then prints a repair packet

```
brevio-core heal [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--root ROOT` | `.` | project root directory |
| `--diff DIFF` |  | diff base commit or branch |
| `--no-fix` |  | only report: without it heal rewrites files with the rules' deterministic fixes and lists the lines it changed |
| `--no-compile-guards` |  | skip compiling defect guards |
| `--json` |  | output report as JSON |
| `--format {text,json}` |  | output format |
| `--max-chars MAX_CHARS` | `6000` | max chars for heal packet |

## `brevio-core shield`

Answer a maintainer's pull-request comment (fix, explain, roast, triage, heal) from the command line.

```
brevio-core shield [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--comment COMMENT` |  | PR comment body |
| `--root ROOT` | `.` | project root directory |
| `--json` |  | output result as JSON |

## `brevio-core patch`

Apply SEARCH/REPLACE blocks or a unified diff, only when the rules find the changed lines clean.

```
brevio-core patch [options] FILE
```

| Argument | Default | What it does |
| --- | --- | --- |
| `FILE` |  | the patch file, or - for standard input |
| `--root ROOT` | `.` | the project folder (default: .) |
| `--dry-run` |  | print the verdict and the diff; write nothing |
| `--path PATH` |  | the file a SEARCH/REPLACE block applies to when it names none |
| `--json` |  | print the verdict as JSON |

## `brevio-core connect`

Detect stack and coding clients and write CLAUDE.md, CONSTITUTION.md & roadmap.json (--dry-run lists them first).

```
brevio-core connect [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--root ROOT` | `.` | repository root (default: .) |
| `--force` |  | overwrite existing spec files |
| `--dry-run` |  | list the files it would write and write nothing |
| `--yes` |  | write without asking (a terminal is asked first, a pipe is not) |
| `--json` |  | output JSON |

## `brevio-core plan`

Create a single-task implementation plan (.brevio/task_plan.json & .brevio/TASK_PLAN.md).

```
brevio-core plan [options] [task]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[task]` |  | task description or id |
| `--task TASK_FLAG` |  | task description or id |
| `--root ROOT` | `.` | repository root (default: .) |
| `--budget BUDGET` | `2000` | token budget for repo map |
| `--json` |  | output JSON |

## `brevio-core build`

Execute single-task build preparation and deterministic auto-fixes.

```
brevio-core build [options] [task]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[task]` |  | task description or id |
| `--task TASK_FLAG` |  | task description or id |
| `--root ROOT` | `.` | repository root (default: .) |
| `--no-fix` |  | do not auto-apply deterministic fixes |
| `--json` |  | output JSON |

## `brevio-core qa`

Run the single-task pre-PR quality gates and count the findings (of the task's diff; --all counts the whole repository).

```
brevio-core qa [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--root ROOT` | `.` | repository root (default: .) |
| `--diff DIFF_BASE` |  | base ref to diff against |
| `--all` |  | count the whole repository, not only the task's changes (the 0.9.1 count) |
| `--json` |  | output JSON |

## `brevio-core pr`

Run the QA gates and write a pull-request body from what they found (.brevio/PR_BODY.md).

```
brevio-core pr [options] [title]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[title]` |  | PR title |
| `--title TITLE_FLAG` |  | PR title |
| `--root ROOT` | `.` | repository root (default: .) |
| `--diff DIFF_BASE` |  | base ref to diff against |
| `--all` |  | count the whole repository, not only the task's changes |
| `--json` |  | output JSON |

## `brevio-core task`

Run the single-task local workflow (connect -> plan -> build -> qa -> pr).

```
brevio-core task [options] [task]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `[task]` |  | task description or id |
| `--task TASK_FLAG` |  | task description or id |
| `--root ROOT` | `.` | repository root (default: .) |
| `--no-fix` |  | do not auto-apply deterministic fixes |
| `--diff DIFF_BASE` |  | base ref to diff against |
| `--json` |  | output JSON |

## `brevio-core up`

Show what a team sync would take from this repository (uploads nothing).

```
brevio-core up [options]
```

| Argument | Default | What it does |
| --- | --- | --- |
| `--json` |  | print the files, sizes and fields as JSON |
