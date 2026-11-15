# Changelog

## 0.11.0

### Added

#### The facts of a run

- `scan --format markdown --facts` ends the report with one line: the version, the rules that ran after `brevio-core.toml`, the files read, base and head, and how long this run took. The rule count falls when the config file switches a rule off. It is off unless you ask for it.
- The time differs per runner and per run. Read it as this run's time, not as a figure to compare.
- With a base, the line also gives the command that repeats the scan. `--no-reproduce` leaves that command out. The Action leaves it out when `source` is set.
- The Action input `facts` (default `false`) passes the flag. An older package that does not know `--facts` gets a notice and no flag, so it does not end with exit code 2.
- A pull request from a fork gets no comment, whatever `facts` says. The job summary carries the line there.
- The flag names `--facts` and `--no-reproduce`, the input name `facts`, and the fields of the line stay as they are from this release, because later tools may read the line.

### Changed

#### Memory

- `scan` of a folder and `check` now read files one at a time. Before, they read the lines of all the files when they collected them. Measured on synthetic trees, the peak Python memory of `scan` fell to about half on a tree of some hundreds of files. It fell to about a third on a tree of some thousands. For `check` on a new repository it fell to about a quarter on the larger of two trees.
- On one machine, a scan of the larger tree took a few per cent longer, and `scan --surface` took about a third longer on the smaller one. A diff scan keeps the lines the change adds in memory, as before.
- A file that reports no size and never ends, such as a link to a device, now reads as too big instead of being read without limit. A file over one million bytes reads as empty in `check`, as it already did in `scan`.

#### Guards

- A guard can now list paths it should not watch, with the optional key `exclude_file_patterns` in `.brevio/guards.json`. A test under an API folder is no longer flagged by a guard meant for application code.
- Matching of the exclude list is case-sensitive on Linux, macOS and Windows alike.
- `learn import` accepts and checks the new list. It refuses an absolute path, a path that leaves the repository, or a very long or wildcard-heavy glob, and says why.
- A change that adds an excluded path to a live guard is reported for review, like any other narrowing of a guard.
- The FastAPI starter pack's blocking-call guard now leaves out tests and `conftest.py`. Run `brevio-core add fastapi-async` again to get it. That resets the pack's status and counters.
- A list that leaves the guard nothing to watch is not used, and `learn import` refuses it. `scan` says so, so such a list does not switch a guard off.
- Older releases ignore the new key without a message, so they still report the excluded paths.
- A guard that `learn` compiles from a `.ts` fix now also watches `.tsx` files in the same folder. A guard written by an older release keeps its own list.
- A guard whose bounded repeats and alternatives multiply into very many ways to match is no longer run as a regular expression. It matches as plain text, as a risky pattern already did. Sixteen `x?` in a row is one example.
- `learn --diff` refuses a value that starts with a hyphen. Git would read it as an option.
- The agent skill file now has one sentence that tells agents not to run the optional model command, which sends code to a model the person has connected.

#### Windows

- The call-count ledger of the optional model command is now locked on Windows too, with a lock file beside the ledger, so two commands at once take turns. Before, Windows had no lock.
- The check that the settings file is yours alone still runs on systems with user ids only. Windows file permissions are not read.

#### The optional plugin

- If you installed `brevio-core-assist`, remove it with `pip uninstall brevio-core-assist` before you upgrade. The plugin was last built for 0.9.1 and pins that core version exactly, so pip warns of a conflict when the core moves. The core no longer reads its `BREVIO_ASSIST_*` variables.

#### Pull request body and check heading

- The pull-request body of the task commands no longer holds a fixed five-step list. A step is read from the file it wrote (`.brevio/connection.json`, `task_plan.json`, `BUILD_PACKET.md`, `qa_report.json`), or it reads "not run". The words "receipt" and "verified" are gone from the body, the help text and the command page.
- The `check` output and the MCP tool are headed "Pre-PR flight check", not "Pre-PR Flight Check Receipt". The counts are unchanged.
- `check` fails and says why when git could not give it the diff. Before, a failed or timed-out git call read as an empty diff and `check` printed PASSED. The output now says that no check ran and lists no check as OK.

#### Starter packs

- The packs are called hand-written starter packs. The words "curated" and "community" and the line about prompt tokens are gone, because the packs are typed in the code and the figure was never measured.
- The `eu-ai-act` pack's description says what its guard looks for, a JSON response on a chat, generate or llm route with no `X-AI-Generated` header, and makes no legal claim.
- The `pack-billing-idempotency-key` guard never matched: its pattern held a repeated group, which the scan runs as text. It now flags a payment `create` call that closes after one object on its line, with no options argument for a key. A call that spans lines is not read.
- The `pack-billing-webhook-raw-body` guard also flags `constructEvent(JSON.parse(...))` and `constructEvent(JSON.stringify(...))`.
- Run `brevio-core add stripe-polar-billing` again to get both guards. That resets the pack's status and counters, and findings can change for a project that installed the pack.
- The ten pack guards are now run on a line they must flag and a line they must not.

#### Privacy text

- The text on `share` says it would turn the counts that `gain` prints into a public link. `gain` prints no link, and `share` is not part of this release.

#### Leaks

- `leaks` no longer stops with an error on a link whose target is gone. It skips that link and reads the other files.

#### Precision

- 0.11.0 publishes no new precision figure. The TypeScript and JavaScript figures stay as recorded on 2026-11-06, and the page says so. Python reads "not measured".
- You can now keep several precision corpora, one per language, in `tools/precision_corpus.json`. A rule page shows the precision measured for the languages it reads and says "not measured" for a language with no corpus.
- The [precision page](precision.md) and the rule pages now give counts with a Wilson score interval, not a bare percentage. They name the corpus, the scanner and the date, and the reviewer once one is recorded.
- The recorded TypeScript verdicts name no reviewer, and the page says so.
- Hit counts per repository are not published from 0.11.0 on: totals only. The counts stay in a local file that a release does not ship.
- `tools/precision_run.py` has new options: `--corpus-name`, `--pin` (pins repositories to a commit and records their licence), `--detail`, `--reviewer` and `--publish`. `--publish` refuses a sample that has an unjudged hit, no reviewer, or rows added or removed.

## 0.10.0

### Changed: what a 0.9.1 user must know

#### Pull requests and checks

- `check`, `qa` and `pr` now compare with the base `scan --diff` uses (origin/main, else main, else HEAD), not HEAD. A branch with committed findings no longer passes by default.
- Use `check --base HEAD` for the 0.9.1 comparison. The receipt names the base, and a `--base` the checkout lacks still runs against the next base and says so on standard error.
- `qa` and `pr` count the task's own changes, not the whole repository, and print what they counted. Use `qa --all` or `pr --all` for the 0.9.1 count.
- With no commit to compare with, `qa` and `pr` count the whole repository and say so.
- `qa` and `pr` print and return no score or grade. A failed `qa` or `pr` lists its findings, and `pr` prints no `gh pr create` command when it fails.
- `scan --diff BASE`, `baseline --diff BASE` and `scan --fix --diff BASE` now end with exit code 2 when BASE is not in the checkout. 0.9.1 reported no findings. Fetch more history or name a base that exists.
- In a diff scan and in `check`, a new copy of a baselined line is reported: the baseline hides as many copies as it recorded. `check` reported no such copy in 0.9.1.
- A diff scan no longer reports the baseline that `brevio-core init` wrote in the same change as a new suppression, so the adoption pull request can pass. Entries added later are still reported.
- The agent hook reports what the uncommitted changes add to the baseline, to `brevio-core.toml` and as ignore comments as a new suppression, so running `baseline` does not silence it.
- The baseline is found from any folder of the repository. A `.brevio/baseline.json` in the current folder is still read first, and `baseline` writes to the file it located, or to the git root.
- The Action job summary lists the findings and prints no score, grade, badge or playground link.
- Action annotations and `scan --format github` are errors for findings that fail the check and warnings otherwise. They were all warnings before.
- The workflow `init` writes pins the checkout action to v5.1.0 and uploads to code scanning (`sarif: true`, `security-events: write`), as the README does.
- SARIF declares the `suppression` and `guard:<id>` rules, gives results a `ruleIndex`, and names the version in a release and a rule-page link in a GitHub release.
- The Markdown report of a diff scan says that its table lists added lines only.

#### Scores, badges and JSON keys

- `brevio-core score` and `brevio-core roast` still accept `--min`, `--badge` and `--roast`, which are deprecated. They print no score, grade or badge, so a flag only prints a notice on standard error. `--min` still fails a CI step when an error-level finding is counted, whatever number it is given. Gate with `brevio-core scan --fail-on new` instead.
- `brevio-core score` and `roast` are previews and print the findings counted by rule and what to run next. They print no score, no letter grade and no verdict line.
- `score --json` and `roast --json` lose score, grade, color, badge_markdown, repo_badge_markdown, scorecard_markdown, playground_links, receipt_url, repo, rules_encyclopedia_url and roast. Read finding_count, errors and warnings instead.
- `qa --json` loses score and grade and gains scope. `pr --json` loses score and grade and gains findings.
- `gain` prints what the local ledger recorded and the number of rules the scan checks. It prints no receipt link, badge, AEO score or count of the whole catalogue.
- `gain --json` loses base_rules_enforced, aeo_score and receipt_url, and gains trial_learned_guards, rules_scanned and rules_documented.
- The MCP `score` tool and the `brevio://scorecard` resource report "score (preview): N finding(s)" instead of a score and grade. If you parse the old keys or text, stay on 0.9.1.
- `aeo` no longer scores a one-click hand-off link. The four other pillars are rescaled, so a page's score can differ from 0.9.1, and `aeo --format json` no longer has `pillars.ux_bridge`.

#### Rules and findings

- Paths that only contain the letters "test" (latest.py, contest_utils.py, attestation/) are no longer skipped, so the secret, shell, requests and eval checks find things there that were silent before. The baseline absorbs them.
- Names such as a.test.d.ts, unittest/, pytest_helpers.py and testdata/ are still test paths.
- A bare `except:` that ends in a plain `raise` is no longer reported by R5.4, and `scan --fix` leaves it as it is.
- R5.4 also reports `except Exception:` handlers that only pass, in Python.
- `scan --fix`, `heal`, `build` and `task` rewrite a bare `except:` to `except Exception:`, not `except BaseException:`. It no longer catches Ctrl-C or a system exit.
- A bare `except:` whose body is only `pass` or `...` is no longer rewritten by `scan --fix` or `heal`, and it stays reported. The rewrite would still be a finding, so a person decides what the handler does.
- The "N fixable" line and the fix report count a pass-only bare `except:` as not fixable.
- R10.1 now names Python requests only. The fetch and axios check it did not make is the new preview rule R10.9.
- R2.8 now reports eval and exec on a non-literal in Python, and eval in .mjs and .cjs files.
- A folder scan and a diff scan now read package.json, and R16.2 reads a minified package.json.
- R13.1 says a secret-named NEXT_PUBLIC_, VITE_ or REACT_APP_ variable is sent to the browser, instead of calling it hardcoded.
- The titles of R1.2, R1.4, R1.5, R2.1, R2.2, R2.8, R4.5, R5.1, R5.4, R5.5, R6.3, R6.5, R8.2, R9.2, R9.3, R10.1, R10.8, R13.1, R14.5, R16.1, R16.2, R17.5, R18.1, R18.2 and R18.5 are reworded to say what the scan checks.
- Rule IDs and statuses are unchanged by the new titles. The sentences above say where the findings changed.
- The test-custody findings of `brevio-core check` have rule IDs R20.1 to R20.5, in `preview`. Earlier builds printed them as R6.5, and the empty-test check as R6.1.
- An ignore comment naming R6.1 still silences an empty test (now R20.5) for one release, and `scan` asks you to rename it.
- The scan and the repository map skip the .agents and .brevio folders, so new findings can appear in the map where it read those folders before.
- The rule pages in `docs/rules` and the findings a scan prints link to no page on a website. `brevio-core explain <rule>` prints the same page. [Links the tool prints](links-the-tool-prints.md) lists the addresses the released files can show.
- A full scan notes baseline entries that match no finding and ignore comments that silence nothing.

#### Commands and output

- `brevio-core` with no arguments, `-h`, `--help` and `help` print a short list of the commands to start with, and `help --all` prints the full list. A bare run at a terminal no longer opens a menu or a setup wizard.
- The `rules` text lines gain a `[scan]` or `[scan --surface]` marker after the summary of a scanned rule.
- `brevio-core leaks` prints "no leaks found in <folder>" for a clean folder, where it printed an empty line. A path that is not a folder still prints the empty line, with a note on standard error.
- `brevio-core shield` without `--comment` says what it needs, where it printed an empty line. The exit code is unchanged.
- `heal`, `build` and `task` rewrite files with deterministic fixes as before, and now list the lines they rewrote. Use `heal --no-fix` to only report.
- `connect` asks before writing on a terminal. `--yes` skips the question, `--dry-run` lists the files and writes nothing, and a pipe still writes without asking.
- `connect` detects Python, TypeScript and Node code instead of "generic".
- `scan --diff` without a base says on standard error when it fell back to HEAD, and the Markdown report says "No lines were read." when it read none.
- A stop hook that asks the agent to go on has exit code 2 and now also writes its reason to standard error.
- The hook's `--help` no longer says that the exit code is 0 at the stop.
- `hook install` writes the hook as `uvx brevio-core==<version> hook run ...`, so the hook stays on that version until you run `hook install` again. Hooks written by 0.9.1 still work and are replaced, not duplicated.
- After an edit, a hit of a learned guard on trial, or of a guard that is a warning, is a note to the agent. It does not stop the edit.
- The MCP `scan` and the other tools return an error for an argument they do not take, such as `diff` for `diff_base`, instead of ignoring it.
- The Teams line says that central sharing of guards across an organisation is not built yet.
- The README has a new first screen and no empty section on what other linters do not cover. The FAQ and the docs say how far `scan --diff`, `scan --fix`, the agent hooks and ignore comments reach.
- The precision samples no longer quote third-party code. An entry holds the repository, commit, path, line, rule, verdict, reason and a hash of the line, and a re-run keeps a verdict only when the hash still matches.
- The precision page shows judged hits that were real as counts, not percentages.

#### Learned guards and packs

- A new learned guard watches the folder of the fix, not any file of that kind in the repository. Guards already written keep their paths.
- A new guard from a fix with several removed lines in one hunk needs all of them in a file to report. Guards without the `match` field still report on any one.
- A new guard is titled with the subject of the commit that brought the line in. `learn` prints that commit and where the guard reports as its second line, and the guard id is unchanged.
- A learned guard fails a scan only when `brevio-core.toml` sets it to `error`, and the exit code of `check` is 1 when it reports a preview test-custody rule.
- Pack guards are reported as "pack guard" with a plain-words label instead of the raw expression.
- The fastapi-async pack watches API modules instead of all Python files, and the supabase-rls pack finds the service key outside three fixed folders. Run `brevio-core add <pack>` again to refresh guards installed by 0.9.1. This resets the status and counters of that pack's guards.

#### The optional plugin

- The optional plugin `brevio-core-assist` is not built after 0.9.1 and stays installable. The core does not read its
  `BREVIO_ASSIST_*` variables.

### Added

#### Rules and scanning

- New preview rule R10.9 reports fetch and axios calls that have no options argument.
- Rule pages say which languages a rule reads.
- A rule page and `brevio-core explain` show the rule's judged precision: the share of judged hits that were real, the number judged, the corpus and the date. A rule that was not measured says "not measured".
- `brevio-core explain` says when `scan` does not run a rule, and `explain R2.8` and `explain R5.4` list the fix for the kinds of finding and the languages.
- `brevio-core rules` marks the rules that `scan` checks, with a note on standard error. `rules --json` gains `scanned`, `surface` and `status` fields, and `rules --scanned` lists only the rules `scan` runs.
- `scan` warns on standard error when the select and ignore settings leave no rule that the scanner checks.
- After a text-format scan, a line on standard error says how many findings `scan --fix` would rewrite and prints the command to preview it.
- `baseline` and `init` name the baselined secrets (R13.x) and say to rotate it.
- `baseline --prune` removes the dead entries and lists them.

#### Commands

- `brevio-core --version` (and `-V`) prints the installed version.
- `brevio-core help scan`, or help for any command, prints the same text as `scan --help`.
- `hook install` says which file it wrote and that uvx must be on PATH.
- The MCP server reports the package version in `serverInfo`.
- The fix hint on a finding fits the language of the file. Python SQL strings and a bare `except:` get Python hints, and a finding with no example in that language carries no hint.
- The self-heal packet shown at stop leaves out an edit with no rule id or a path outside the project.

#### Learned guards and packs

- `scan`, the agent hook, the MCP `scan` tool and the Action report a hit of a guard in `.brevio/guards.json` as a finding with the rule `guard:<id>`. A guard on trial is reported only, an active guard is a warning, and a person raises a guard to an error in `brevio-core.toml`.
- The baseline, ignore comments and the config treat a guard hit like the finding of a rule. SARIF carries a fingerprint that stays the same when the line moves.
- `learn` runs a control run before it keeps a guard: it must match the lines the fix removed and not the fixed file. `learn --dry-run` replays the guard over recent commits and lists what it would have flagged, and `--replay N` sets how many.
- `learn list`, `learn promote`, `learn wrong` and `learn dismiss` (preview) manage a guard. A trial has an end date, promotion needs a clean replay, hits a person marked wrong switch a guard off, and `dismiss` is a person's switch-off.
- `dismiss` and `wrong` are refused where input or output is not a terminal. A change that removes or weakens a guard without the record `dismiss` leaves is reported as a new suppression.
- `learn` does not overwrite a guard that is in the file already, and keeps a pattern that looks like a credential out of a guard. The scan reads a guard's regular expression as plain text, with a warning, when it repeats a repeat.
- `init --wizard` lists the guards its warm start would keep and drop, with the reason, and writes them only after a yes.
- A guard can keep the call, such as `yaml.load(`, when the removed line was a `return` or an assignment of it, if the control run and replay allow it.
- A pattern that is also in the fixed file, such as a moved import, is dropped with a note instead of refusing the whole guard.
- `learn wrong <id> path:line` marks one named hit, and a hit is counted once.
- `learn promote <id> --accept` takes in replay hits you have read, at a terminal, and promotes.
- `learn show <id>` prints a guard in full: patterns, trial end, replay by commit and the lines it matches now. `learn list` shows hits found by `learn --check`.
- `learn` says why it could not extract a pattern and what to do next.
- A guard from the first release without a `trial_ends` date gets its trial end from the day it was made, so it is not promoted on that day.
- `.brevio/stats.json` is added to `.git/info/exclude` when first written inside a git work tree, so it is not offered for a commit.
- A guard may carry `file_contains`, a literal checked once against the whole file before its line patterns run.
- `brevio-core learn import <file>` reads a guards file on your machine, replays the guards on this repository, and adds it on trial after a typed yes (preview). The rule file for an agent is written only when you promote the guard.
- `brevio-core add` prints how many recent changes a pack guard would have flagged here, and notes when the library the pack is for is not found.

#### Action

- Action input `allow-new-suppressions` (default false) is for a change a person reviewed.
- Action input `custody` (off, report or fail; default off, preview) runs the test-custody check on the pull request and adds its R20 findings to the comment. In fail mode a check that did not run also fails.
- Action inputs `paths` (spaces, or one per line when a name holds spaces) and `working-directory`, and the output `custody-findings`, are new.

#### Docs and release

- The README shows a throwaway repository with the commands for `scan --diff` and `scan --diff --fix --dry-run`. `docs/try-it.md` holds the real output and `docs/example-comment.md` shows the Markdown report.
- The precision measurement can be run again: `tools/precision_run.py`, the corpus and the judged results ship in the tree, and the page has the commands. An issue form, "Dispute a precision verdict", takes a disagreement with one verdict.
- A test starts `scan`, `scan --diff`, `check`, the agent hook, one MCP request, `baseline`, `learn` and `gain` in a child process in which opening a socket raises. It checks that they finish, and the security review lists it with its command.
- Two pages explain what the gate can and cannot see, and how to install from a mirror or a downloaded file.

### Fixed

- `qa` and `pr` could show findings only in a JSON file. They now print them, which makes findings visible and adds no new ones.
- `task` no longer adds guards to `.brevio/guards.json` from its own outputs, and `heal` leaves `.brevio/` and `.agents/` out of the diff it learns from.
- To remove guards an earlier `task` added, run `brevio-core learn dismiss <id>` for the guard-fix-* and guard-r5-* entries, or `git checkout .brevio/guards.json`.
- The repair packet from `heal` and the stop hook holds only edits the scan re-checks. A finding with no verified edit is listed with its one-line reason and an example in the file's own language, and the packet is cut between edits.
- `learn --check` with an absolute path inside the repository now finds the guard's files, and a guard for a fix in a folder such as `app/[id]` watches that folder.
- Output piped to a command that closes early (`explain R2.8 | head`) no longer ends in a traceback. The exit code is 141 when the pipe was closed before the output was written.
- `brevio-core add --help` shows the usage of `add`, and `pack --help` names the `pack` command.
- The Next.js pack guards matched nothing on real files. They now flag an async default export or a metadata export in a file marked use client, including when only the changed lines are scanned.

## 0.9.1

- The public repository describes this release as a single-task local command-line workflow (connect, plan, build, qa, pr) with starter specs. It also describes a documentation hub of five tracks with progressive depth and dual-audience contracts.
- The release notes of 0.9.1 are on its GitHub release.

## 0.9.0

- The release notes of 0.9.0 are on its GitHub release.

## 0.8.1

- The release notes of 0.8.1 are on its GitHub release.

## 0.8.0

- The release notes of 0.8.0 are on its GitHub release.

## 0.7.0

### Rules and fixers

- `brevio-core scan` now checks 13 scanned rules of the 123 in the catalogue (110 documented): R5.5 flags `subprocess`
  calls with `shell=True` and `os.system` calls with interpolated strings in Python; R10.1 flags single-line `requests`
  calls without `timeout=` in Python; R16.2 flags `"*"` and `"latest"` dependency ranges in `package.json`; and R18.2
  flags single-line `target="_blank"` anchor tags without `rel="noopener noreferrer"`.
- R2.8 also flags raw SQL string interpolation in `.execute(f"SELECT ...")` and `.$queryRawUnsafe` template literals,
  as well as hidden directional and Unicode tag control characters.
- R13.1 also flags secret-named environment variables exposed to client bundles (`NEXT_PUBLIC_`, `VITE_`, `REACT_APP_`).
- `brevio-core scan --fix` and the MCP `fix` tool now rewrite `target="_blank"` anchor tags that have no `rel=`
  attribute to add `rel="noopener noreferrer"` (R18.2).
- The rule pages in `docs/rules` now link to the interactive rule playground on `brevio.pro`.

## 0.6.0

### Adopt it on a repository that has findings

- `brevio-core baseline` records the findings a repository has today in `.brevio/baseline.json`, so a scan reports
  only new ones. An entry names the file, the rule and a hash of the line, so moving a line does not make it new.
- `scan --fail-on` chooses what fails: `new` (the default), `all`, `none` for a trial, or `error` for error-level
  findings only.
- `brevio-core init` sets a repository up in one step: the pull-request workflow, a block of agent rules in
  `AGENTS.md` and a baseline, with `--with precommit,hook,mcp` for more. It shows a diff and asks first, never rewrites
  a file you have, and `init --undo` takes back what it wrote.

### Only a person can silence it

- An ignore comment with a reason (`brevio-core: ignore R9.2 -- reason`) silences one finding in a scan a person
  runs. Without a reason it does not count.
- The agent hook and the MCP server never honour an ignore comment, and a diff scan reports every ignore comment,
  baseline entry and config change it adds as a new suppression until a reviewer allows it.
- SARIF keeps each silenced finding as a suppression with its reason.

### Configure it for a team

- `brevio-core.toml` at the root and in any folder chooses the rules, leaves paths out, and sets `warn` or `error` per
  rule and per path. `extends` reads an organisation policy from a file in the repository or a checkout pinned to a
  commit; it never fetches one.
- `brevio-core config` shows what applies to a file or folder.
- The README has a rollout guide: observe, warn on new findings, block them, then tighten.

### Stable rule IDs

- Every rule has a status: `stable`, `preview` (reports, never fails) or `deprecated` (reports nothing, keeps its ID).
- A rule ID is never removed, reused or renumbered; the release refuses one that is.
- The release notes list the rules added, changed and retired.

### Rules

- R13.1 (a secret in code) flags a known credential format, a JWT, or a random-looking value bound to a secret, token,
  password or API key name. It no longer flags placeholders or keys that are public by design, such as Algolia search
  keys and Stripe `pk_` keys.
- Every scan skips vendored folders, minified files and bundles. Exclude more with `exclude` in `brevio-core.toml`.
- No rule reads a TypeScript declaration file (`.d.ts`, `.d.mts`, `.d.cts`): it holds types, no code that runs.
- The `eval`, `new Function` and `dangerouslySetInnerHTML` findings have their own rule, R2.8. Earlier builds reported
  them as R2.5, the SSRF and timeout guard.
- R6.3 (a sleep in a test) is `preview`: it reports and never fails a scan.

### In the agent's loop

- `brevio-core hook run` is the hook a coding client calls after an edit and before it stops. It reads the client's
  event, scans the changed lines and hands the findings back in that client's own format, so the agent fixes them
  without a human turn. It gives up quietly on an error or past its time budget.
- `hook install` writes the hooks for Claude Code and Cursor, and the agent skill for the clients that load skills.
- `scan --format agent` prints one short line per finding with a fix hint, as the hook and the MCP server do.
- `learn --staged`, `learn --worktree` and `learn --dry-run` compile a guard from a fix that is not committed yet,
  and show what it would match before anything is written.

### On the pull request

- `scan --format markdown` prints the findings as an escaped table, with links to the rule pages.
- The Action writes the job summary and annotations, keeps one pull request comment up to date and marks it resolved
  once the findings are gone, and can upload SARIF to code scanning. It runs on pull requests, merge queues and
  pushes only, and pins every action it uses to a commit.

### Scoring a site for answer engines

- `brevio-core aeo` scores a folder of built HTML pages, with its `robots.txt` and `llms.txt`, against the AEO
  rubric, as text, Markdown or JSON, and fails below `--min`. It reads local files only and refuses a URL with a hint
  to save the page first.

### Docs

- A command reference generated from the code, a guide to the Action, a guide to writing a rule, the guard and SARIF
  formats, a FAQ and this changelog.
- One page per coding agent: Claude Code, Cursor, Codex, Hermes and OpenClaw.
- Each rule page leads with a short answer, has a search-friendly title, links related rules and carries structured
  data; the docs site has a sitemap.
- An agent-safe starter template: the workflow, the `AGENTS.md` block and a README, ready to become a template
  repository.

### Releases and contributions

- Each release has a `MANIFEST.json` that maps every file to its source, and a `.pyz` single-file build with its SBOM
  and checksum in the release notes.
- Releases are published by a workflow with approvals, from a signed tag.
- The README is tested: its marked examples run in CI.
- The security review page states what the package reads and runs, and tests check each claim.
- Governance, support, a rule-acceptance rubric, a rule-test template and issue forms for bugs, false positives and new
  rules.
- A trademark brand-use section and a privacy notice.
