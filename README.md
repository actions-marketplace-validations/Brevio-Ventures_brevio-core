# brevio-core

Catches the mistakes coding agents repeat before they merge: a pull-request gate with no model call and no API key.

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
      - uses: Brevio-Ventures/brevio-core@v0.11.0
        with:
          sarif: true
```

It catches what review keeps finding in agent-written code:

- secrets written into source, `eval` on a non-literal in JavaScript, TypeScript and Python, and `any` casts in TypeScript;
- type errors silenced with `@ts-ignore`, and exceptions dropped by an empty `catch` (JavaScript, TypeScript) or `except: pass` (Python);
- `waitForTimeout` sleeps in end-to-end tests, images without alt text, and `div` click handlers a keyboard cannot reach.

**No API key, no model. `scan`, the agent hook and the MCP server use no network and send nothing.**

These are line-level pattern checks on the lines a change adds, not AST or dataflow analysis. The catalogue has
129 rules: `brevio-core scan` checks 14 scanned rules, and the other 115 are documented rules,
written down for review and for agents, with no scanner in this package yet. Every finding links to its
[rule page](docs/rules/README.md): what it catches, why, a bad and a good example.

## Run it without installing

Version 0.11.0, Python 3.10 or newer, no runtime dependencies. In a repository, scan the lines your change adds:

```sh
uvx brevio-core@0.11.0 scan --diff
uvx brevio-core@0.11.0 score
```

```sh
pipx run --spec brevio-core==0.11.0 brevio-core scan --diff
```

Or install it, then run it the same way:

```sh
pip install brevio-core==0.11.0
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

## GitHub Action

The workflow at the top runs the scan on the lines a pull request adds. It writes the report to the job summary and
marks each finding on the changed file. With `pull-requests: write` it keeps one comment on the pull request up to date
while there are findings, and marks it resolved once they are gone; a pull request from a fork gets the summary only.
With `sarif: true` and `security-events: write` it uploads the findings to code scanning. It needs no secret and no
API key. The tag `v0.11.0` runs exactly brevio-core 0.11.0. To follow the latest release instead, write
`uses: Brevio-Ventures/brevio-core@v0`; to pin a commit, see [SECURITY-REVIEW.md](SECURITY-REVIEW.md). It runs on
`pull_request`, `merge_group` and `push`, and refuses any other event.

## pre-commit

```yaml
repos:
  - repo: https://github.com/Brevio-Ventures/brevio-core
    rev: v0.11.0
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
