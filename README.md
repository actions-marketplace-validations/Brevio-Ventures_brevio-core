# brevio-core

Checks the lines a coding agent adds against fixed rules, and turns a fix you committed into a guard that it tries on your
recent commits before it keeps it.

Save this as `.github/workflows/brevio-core.yml`:

```yaml
name: brevio-core
on:
  pull_request:
  merge_group:
permissions:
  contents: read
  pull-requests: write     # the comment on the pull request
  security-events: write   # the upload to code scanning
jobs:
  brevio-core:
    name: brevio-core
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5.1.0
        with:
          fetch-depth: 0
      - uses: Brevio-Ventures/brevio-core@v0.10.0
        with:
          sarif: true
```

What it does:

- `brevio-core scan --diff` checks the lines a change adds against the 14 scanned rules. The page of a rule says what it
  catches, and how often its hits were real in a public sample, or that it was not measured.
- `brevio-core learn` turns a fix or a revert you committed into a guard stored in your repository, and
  `brevio-core learn --dry-run` first shows which past changes the guard would have flagged.
- The agent hook, the MCP server and the pull-request check run the same rules, so an agent can fix a finding before a reviewer
  sees it.

These are line-level pattern checks, not AST or dataflow analysis, and the rules are TypeScript and React first. The catalogue
has 129 rules: 14 scanned, and the other 115 documented for review and for agents. `learn` works from the diff, not
from the rule list, so a fix in Python becomes a guard as well.

**`scan`, the agent hook and the MCP server use no network and send nothing.**

`brevio-core ai` calls a model only when you connect one and answer yes.

How often the hits of a rule were real, measured on public code that people wrote and judged by one reviewer, is on the
[precision page](docs/precision.md). It is not a measure of agent-written code.

## Run it without installing

Version 0.10.0, Python 3.10 or newer, no runtime dependencies. In a repository, scan the lines your change adds:

```sh
uvx brevio-core@0.10.0 scan --diff
uvx brevio-core@0.10.0 score
```

```sh
pipx run --spec brevio-core==0.10.0 brevio-core scan --diff
```

Or install it, then run it the same way:

```sh
pip install brevio-core==0.10.0
```

<!-- readme-test -->
```sh
brevio-core scan --diff
```

Or download `brevio-core.pyz` from the [GitHub release](https://github.com/Brevio-Ventures/brevio-core/releases), check where it
was built, and run it with any `python3`:

```sh
gh attestation verify brevio-core.pyz --repo Brevio-Ventures/brevio-core
python3 brevio-core.pyz scan --diff
```

To scan whole files instead, name them, or nothing for the current folder:

<!-- readme-test -->
```sh
brevio-core scan
```

## See it work

In a folder you can throw away, with git and brevio-core installed, make a branch with two mistakes in it and scan it:

```sh
git init -q -b main demo && cd demo
git commit -q --allow-empty -m base && git switch -q -c change
printf 'def load(path):\n    try:\n        return open(path).read()\n    except:\n        return None\n' > app.py
printf 'const n = JSON.parse(raw);\n// @ts-ignore\nconst x: number = n.name;\n' > app.ts
git add -A && git commit -q -m change
brevio-core scan --diff main
brevio-core scan --diff main --fix --dry-run
```

The first scan reports the two mistakes, a bare `except:` and a `@ts-ignore` comment. The second shows the rewrites that have
one correct fix as a diff and changes nothing. [What they print](docs/try-it.md) comes from a run of this release, and the
[pull request comment](docs/example-comment.md) is the same report in Markdown.

## Set it up in one step

<!-- readme-test -->
```sh
brevio-core init --dry-run
```

`--dry-run` shows, as a diff, what `brevio-core init` would write, and writes nothing. `init` writes a pull-request
workflow like the one above (pinned to this release), a block of agent rules in `AGENTS.md` and a baseline, so the
first local run is quiet. `--with precommit,hook,mcp` adds the pre-commit entry, the agent hook and the MCP server
config. It asks before it writes (`--yes` skips the question), does not rewrite a file you have, and records what it
wrote in `.brevio/init.json`; `brevio-core init --undo` removes what it wrote and you have not changed since.

## Adopt it on a repository that already has findings

```
brevio-core baseline                 # record the findings the repository has now in .brevio/baseline.json
brevio-core scan                     # report only the findings that are not in the baseline
brevio-core scan --fail-on none      # report them and do not fail, for a trial week
brevio-core scan --fail-on all       # leave the baseline out: any finding fails
```

A baseline entry names the file, the rule and a hash of the line's text, not its line number, so moving a line does
not make it new. Commit the file. Run `baseline` again after a fix and the fixed entries drop out. The file holds one
entry per line, sorted, so branches that add entries merge cleanly.

`scan` reads the baseline in the current folder first, then the nearest one in the folders above up to the git root, so
it gives the same answer from a subfolder. `baseline` writes to that file, or to the git root when there is none yet.

A full scan of the folder that holds the baseline prints a note when entries match no finding any more, or when an
ignore comment silences nothing. The note changes no exit code. `brevio-core baseline --prune` removes only those
entries and lists them. A baselined `R13.1` finding hides a secret that is still in the history: `baseline` names
the finding, and the key needs rotating.

`scan --diff BASE` stops with a usage error when `BASE` is not in the checkout (a shallow clone has no `origin/main`; fetch it, or use
`fetch-depth: 0` in the Action). Without `BASE` it tries `origin/main`, then `main`, then `HEAD`, and says on stderr
when it fell back to `HEAD`. In a diff scan a copy of a baselined line is reported, because the baseline counts the
line once.

## Silence one finding, with a reason

```ts
// brevio-core: ignore R9.2 -- the generated client has no type for this field
// @ts-ignore
```

The comment goes on the line or the line above, and silences that rule there. Without `-- reason` it does not count,
and the scan warns about it and about a rule ID that does not exist. The summary line counts what was silenced, and
SARIF carries them as suppressions with their reasons, which code scanning shows.

A person silences a finding in a scan they run. The agent hook and the MCP tools do not honour an ignore comment
(`--forbid-ignores`), and a diff scan reports the ignore comments and baseline entries the change adds as findings,
"new suppression: needs a human review", until a reviewer allows it with `--allow-new-suppressions`.

## A report for the pull request

`brevio-core scan --diff origin/main --format markdown` prints the findings as a Markdown table of rule, file and line,
and why, for a job summary or a pull request comment. `--rules-url BASE` links a rule ID to its page at `BASE`
followed by the ID. Paths and lines from the repository are escaped, so a file name cannot add a mention or a link.

## Configure it for a team

Put a `brevio-core.toml` at the root of the repository, and another in any folder that needs its own settings. For a
file, the files from the root down to its folder apply in that order, so the nearest one wins.

```toml
extends = "policy/brevio-core.toml"   # an org policy, read first (see below)
select = ["R9", "R13.1"]              # the rules that run: an ID, a group or a glob (default: all rules)
ignore = ["R6.3"]                     # rules that do not run
exclude = ["generated/**", "*.pb.ts"] # paths not scanned, relative to this file's folder
level = "warn"                        # the level of all rules (default: error)

[rules."R13.1"]
level = "error"                       # the level of one rule

[[overrides]]
paths = ["legacy/**"]                 # relative to this file's folder
ignore = ["R9.1"]                     # rules that do not run on these paths
rules = ["R9"]                        # the rules the level applies to (default: all rules)
level = "warn"
```

A nearer file replaces `select` and `ignore`; `exclude` and `[[overrides]]` add up; for a level, the last setting that
names the rule wins. An unknown key, a wrong value or a broken file stops the scan as a usage error, and a rule pattern
that matches no rule is a warning. Write a rule ID in quotes as a table name, as in `[rules."R13.1"]`.

A scan already skips code a project keeps but did not write: dependencies and build output (`node_modules/`,
`dist/`, `build/`, `.next/`, virtual environments), vendored folders (`vendor/`, `third_party/`, `third-party/`),
minified files (`*.min.js`, `*.min.mjs`, `*.min.cjs`, `*.min.css`), bundle chunks (`*.chunk.js`, `*.bundle.js`,
`chunk-*.js`, and names that end in a hex hash, such as `index-<hash>.js`) and any file whose first two lines are
longer than five hundred characters. A file named on the command line is scanned as asked. No rule reads a TypeScript
declaration file (`*.d.ts`, `*.d.mts`, `*.d.cts`), named or not: it holds types, no code that runs. Exclude more, such
as generated code or a third-party folder with another name, with `exclude`.

`brevio-core config src/app.ts` shows what applies to a file or folder: the files it comes from, whether it is
excluded, and the rules that run there with their level and status (`--json` for a tool).

`brevio-core scan --fail-on error` (in the Action, `fail-on: error`) fails only on error-level findings and reports the
warn-level ones; the default, `--fail-on new`, fails on a new finding whatever its level. A preview rule does not
fail a scan (see [RULES-STABILITY.md](RULES-STABILITY.md)), and a learned guard fails one only when `brevio-core.toml`
sets it to `error` (see [docs/guards.md](docs/guards.md)). A config can turn rules off, so a diff scan reports a
change that adds or edits a `brevio-core.toml` as a new suppression, as it does an ignore comment.

### An org policy with `extends`

`extends` reads a policy file before the file that names it; the policy's paths are relative to that file's folder.

- `extends = "policy/brevio-core.toml"`: a file vendored into the repository, pinned by your own history.
- `extends = "policy@<commit SHA>"`: a folder that is a git checkout, such as a submodule, at that commit.
- `extends = "<git URL>@<commit SHA>"`: the submodule whose URL in `.gitmodules` is that URL, at that commit.

`extends` reads only files already in your checkout: brevio-core does not fetch a policy (`scan`, the agent hook and
the MCP server use no network and send nothing). When the checkout is missing or at another commit, the scan stops and
says so, and `git submodule update --init` fixes it. To move to a new policy, update the submodule and the
SHA in one pull request.

### Roll it out

1. **Observe.** Run `brevio-core scan --fail-on none` in CI for a week: findings are reported and none fails.
2. **Warn on new.** Record what is there with `brevio-core baseline`, set `level = "warn"` in `brevio-core.toml` and
   run `--fail-on error`: new findings show on the pull request and none fails the check.
3. **Block on new.** Raise the rules you trust to `level = "error"`, one by one or with `[[overrides]]` for the paths
   that are ready. Their new findings now fail the check.
4. **Tighten.** Fix baseline entries and run `brevio-core baseline` again so they drop out, raise the last rules to
   error, then go back to the default `--fail-on new`.

### GitHub Enterprise Server

It works with the `.pyz` from the GitHub release, vendored into your repository, and with a PyPI mirror for `pip`.
This is untested: no one has run it on GitHub Enterprise Server yet.

<!-- mcp-name: io.github.Brevio-Ventures/brevio-core -->

## Use it from your agent

Three ways, all local: no model call and no API key.

**Wire the scan into your coding agents.** In the project folder:

```
brevio-core hook install
```

It finds the coding agents set up in the folder or on the machine and attaches the scan to each, so an edit is
checked against the rules. It also installs the agent skill ([skills/brevio-core/SKILL.md](skills/brevio-core/SKILL.md)),
which tells the agent when to run the commands and how to read a finding. It replaces any older skill text and a second
run changes nothing.

**Run it as an MCP server.** Add this to the `mcpServers` block of any client that supports MCP servers over stdio
(the settings file differs by client, the shape does not):

```json
{
  "mcpServers": {
    "brevio-core": {
      "command": "brevio-core",
      "args": ["mcp"]
    }
  }
}
```

Without a global install, use `"command": "uvx"` and `"args": ["brevio-core@0.10.0", "mcp"]`. Start the client in the project
folder: the server reads that folder and nothing else. The tools are `scan` (paths, or a diff base), `explain` (a rule
id, returns the rule page), `list_rules` and `learn` (compiles a fixed defect into a guard).

**Learn from a fix.** `brevio-core learn` turns the last fix into a guard. See [docs/agent-tools.md](docs/agent-tools.md)
for `learn`, `hook install`, `gain`, `add` and `mcp`.

## Compared with ESLint, Semgrep and CodeQL

brevio-core complements them; keep them.

- **Where they win.** They parse the code, so they see types, the syntax tree and, in Semgrep and CodeQL, data flowing
  across lines and files. They have far more rules and editor integration. brevio-core matches patterns line by line on
  the lines you add, so it misses a mistake spread over several lines and can flag a line that is fine in context.
- **What this adds.** Rules picked from the mistakes coding agents repeat, an agent hook that scans after each edit, an
  MCP server an agent can call, `learn` to turn one fix into a local guard, and a page per rule written for people and
  agents. Nothing to configure and no dependency.

## Use

```sh
brevio-core scan src/                 # files or folders
brevio-core scan --diff origin/main   # only the lines added against a base
brevio-core scan --diff --sarif out.sarif
```

The exit code is 0 when nothing is found, 1 when there are findings and 2 for a usage error.

Additional commands for local workflows:

- `brevio-core score` (preview): count the findings of a scan by rule and say what to run next (`--markdown`, `--json`).
- `brevio-core check`: pre-PR flight check (rules, test custody, test imports, conflict markers and complexity).
- `brevio-core brief`: file-scoped rules & trap memory briefing before agent edits.
- `brevio-core init --personal`: personal stealth mode via `.git/info/exclude` without touching tracked files.
- `brevio-core aeo --generate-llms`: score built HTML pages and generate missing `llms.txt`.

## Fix what has one correct fix

```
brevio-core scan --fix --dry-run    # print the fixes as a unified diff, write nothing
brevio-core scan --fix              # rewrite the files in place, then scan again
brevio-core scan --fix --diff main  # fix only the lines your change adds
```

A rule gets a fixer only when it has one correct rewrite, applied by code and not by a model: a `@ts-ignore` comment
becomes `@ts-expect-error`, and a bare Python `except:` becomes `except Exception:`, which no longer swallows Ctrl-C or a system exit.
A bare `except:` whose body is only `pass` or `...` is left as it is and stays reported, because `except Exception:` with that body is still a finding; it needs a person to decide. The other findings, a hardcoded secret above all, are reported and left for you. The command prints what it fixed and
what remains, and fails while anything remains. Running it twice changes nothing the second time. The MCP server has
a `fix` tool that returns the same diff and writes only when asked.

## Optional: ask a model you connect

The gate above calls no model. `brevio-core ai` is a separate, optional command group that asks a model you chose about
one finding: an endpoint on this machine, or at an https address you name, with your key kept in an environment variable
of your own. A program you name can stand in for the endpoint (a command connector, a preview).

```sh
brevio-core ai connect --local PORT --model NAME   # an endpoint on this machine: no key
brevio-core ai status                              # what is connected, today's count, the limits
brevio-core ai explain --last                      # one finding, one call
brevio-core ai fix --last                          # a fix the rules re-check before it is shown
brevio-core ai connect --program PATH --arg=TEXT   # preview: a program you name instead of an endpoint
brevio-core ai summarize --diff BASE               # preview: one call about a change, from rule IDs and counts
brevio-core ai learn --diff REF                    # preview: a model's patterns for a guard, checked before they are kept
```

A call is one request. `explain` and `fix` are about one finding. Before it goes, the command prints the address, the names of the headers, the
size of the body in bytes and the body itself, and asks `Send it? [y/N]`. An https request goes through the proxy your
environment names, if it names one, and the command prints that proxy's host. `--yes` answers the question for you, at a
terminal too. It does not answer the first-use agreement or `--apply`. Without a terminal a call needs `--yes`, and a yes
you gave at a terminal for that repository. The agreement is kept under the repository's path and its first commit (the path alone until it has one): a copy
or a clone at another path asks again, and so does a different history at the same path. The same history put back at the
same path does not.

The request holds the rule's text, the flagged line and the file's extension. `explain` adds lines around the flagged one
only if you pass `--context N`. `fix` sends five lines either side, and `--context N` changes that, down to none. Anything
written in those lines goes with them, a comment or a name for instance. brevio-core adds no file name or path of its own,
and sends no other file. No request is made when a line in it looks like a secret (the scanner's secret rule and a stricter check; they know a
set of formats and miss others, and one hit stops the call), when the file has a secret-looking name (`.env*`, `.netrc`,
`*.pem`, `*.key`, `id_*` and the like), is a link to one or has a second name (a hard link), or when its path, or the path a
link leads to, is listed under `deny_paths`.

`fix` runs the reply through the rules before it shows a change. It says `verified` only when the rule no longer flags the
line, no finding is new, the reply adds none of the ignore comments brevio-core knows (its own and a listed set of other
tools' markers), and a `.py` file still compiles. Other file types get no syntax check. This shows the rule is quiet, not
that the change is right: read the diff. A reply the rules refuse is not shown as a change and is not retried. The command
prints the reasons, and the reply itself only with `--show-refused`. `fix` writes your working tree only with `--apply` and
a second yes at a terminal. It commits nothing.

### A program you name (preview)

With `connect --program` the request goes to the standard input of a program on your machine, not to an address. brevio-core
finds the program's full path when you connect and keeps it, with your arguments, in your own settings file. A repository's
`brevio-core.toml` that names a program in an `[ai]` table is refused. A call starts the program once. It starts without a shell, in a new empty temporary
folder, with the variables a program needs to start and the ones you listed with `--pass-env`. brevio-core stops it after the
timeout, and when it writes more than a size cap. It starts the program only for a person at a terminal, and not when `CI`,
`CONTINUOUS_INTEGRATION` or `BUILD_NUMBER` is set; other build systems are not recognised. What the program writes is treated
as an endpoint's reply is: printed in a closed block, and a fix is checked by the rules. The program receives the request and may
itself send it elsewhere, and brevio-core does not see what it does. It is not a sandbox: the program runs with your
permissions.

### One call about a change: `summarize` (preview)

`brevio-core ai summarize --diff BASE` asks the model what kinds of mistake a change has. The request holds the rule IDs, the
rule titles and the number of findings per rule from a scan of the diff, and no source line and no file name. The answer is
escaped as inert Markdown and printed in a block that says it is model-written and not part of the gate; `--format markdown`
prints it as a quoted block. It does not change a finding or the exit code of `scan`. The agreement, the limits and the secret
check are those of the other commands.

### Patterns for a guard: `learn` (preview)

`brevio-core ai learn` takes the lines a fix removed and asks the model for up to three patterns that generalise them. It sends
those lines with runs of spaces folded to one, up to a cap, and the file's extension, and no file name. A pattern is kept only when it passes a check of
the regular-expression forms it may use (no backreference, no repeat of a group that holds a repeat, a length cap), the control
run of `brevio-core learn` (it matches the removed side and not the fixed file) and the replay over recent history, which is
printed. A kept pattern becomes a guard on trial, as one made by `learn` does, and its origin is recorded as `model-proposed`.
It takes no `--yes`, needs a terminal and writes nothing before a yes. The agent hook and the MCP server do not run it.

Your settings sit in your own config folder. brevio-core writes the file readable by you only, and before a send it checks,
on POSIX systems, that the file and its folder are not open to others. The settings file and the count of calls beside it are
your own files, which a program running as you can change, so the limits guard against runaway use and not against someone
with your account. A repository's `brevio-core.toml` can only narrow them, with an `[ai]` table of `enabled = false`, `allow`
or `deny_paths`; an `[ai]` table with an address, a key or a model is refused, and so is a key or table the gate does not
know. `BREVIO_AI=off` stops `connect`, `explain` and `fix`; `status` still runs and says it is off. It stops `summarize` and
`learn` as well. The gate commands do not load this code, and nothing goes to Brevio.

### If you used `brevio-core-assist`

The plugin `brevio-core-assist` is not built after 0.9.1. It stays installable from PyPI, and a copy you already have keeps
working. It pins `brevio-core==0.9.1`, so installing it next to a newer core takes the core back to that version. The core does
not read its `BREVIO_ASSIST_*` variables.
`brevio-core ai connect --url URL --key-env NAME` sets up the same kind of connection. `ai explain` does what its `explain` does, and `ai fix` what its `suggest-fix` and
`verify-fix` do; the plugin's `heal` has no counterpart.

## GitHub Action

The workflow at the top runs the scan on the lines a pull request adds. It writes the report to the job summary and
marks each finding on the changed file. With `pull-requests: write` it keeps one comment on the pull request up to date
while there are findings, and marks it resolved once they are gone; a pull request from a fork gets the summary only.
With `sarif: true` and `security-events: write` it uploads the findings to code scanning. It needs no secret and no
API key. The tag `v0.10.0` runs exactly brevio-core 0.10.0. To follow the latest release instead, write
`uses: Brevio-Ventures/brevio-core@v0`; to pin a commit, see [SECURITY-REVIEW.md](SECURITY-REVIEW.md). It runs on
`pull_request`, `merge_group` and `push`, and refuses any other event.

## pre-commit

```yaml
repos:
  - repo: https://github.com/Brevio-Ventures/brevio-core
    rev: v0.10.0
    hooks:
      - id: brevio-core
```

## Rules

A rule has a page under [`docs/rules`](docs/rules/README.md): what it catches, why, and what to write instead.

## What it does not claim

This release publishes no benchmark. When a reproducible one exists, its method and data will be published with the
number.

## Questions and support

Ask questions and propose rules in [GitHub Discussions](https://github.com/Brevio-Ventures/brevio-core/discussions); report bugs
and false positives as issues, with the forms; a verdict on the [precision page](docs/precision.md) has a form of its
own. Support is best effort, with a first response within a week
([SUPPORT.md](SUPPORT.md)).

## Project

- [Security review](SECURITY-REVIEW.md): what it runs, reads and writes, and the test that runs the commands with the
  network refused, for a security team
- [What the gate can and cannot see](docs/what-the-gate-sees.md), and
  [installing from a mirror or a downloaded file](docs/install-from-a-mirror.md)
- [Contributing](CONTRIBUTING.md) (sign-off required), with how this repository is exported and how contributions come back
- [Governance](GOVERNANCE.md): what stays open, and how maintainers are added; the [roadmap](ROADMAP.md) for the next release
- [Security](SECURITY.md)
- [Privacy](PRIVACY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Trademark policy](TRADEMARK.md)

Made by [Brevio](https://brevio.pro).
