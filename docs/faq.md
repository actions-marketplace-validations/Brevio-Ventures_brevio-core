# Questions people ask

| In Plain English (For Anyone) | Technical Contract (For Engineers) |
| --- | --- |
| **What it does:** Answers common questions about how `brevio-core` catches AI coding mistakes, how an agent's ignore comment is treated, and how it fits alongside linters and CI.<br>**When to read it:** When evaluating `brevio-core` for your repository or engineering team. | **Runtime:** Python standard library (`3.10`, `3.11`, `3.12`, `3.13`)<br>**Catalogue:** 129 rules (14 scanned, 115 documented)<br>**Exit Codes:** The exit code is 0 when nothing is found, 1 when not. |

## What is it for?

Coding agents repeat the same mistakes: a silenced type error, an `as any`, an empty `catch`, a hardcoded key, a sleep
in a test. These are easy to see in one line and easy to miss in a long diff. brevio-core checks the lines a change adds
for those mistakes, on the pull request and inside the agent's own loop, and says what to do instead.

## Why Python, when most of the rules are about TypeScript?

Because it has to run everywhere with nothing to install but itself. The package uses only the Python standard
library: no dependency tree to audit, no build step, no native code. Python is installed on the GitHub-hosted runners and on many
developer machines, and `uvx brevio-core` or the single-file `.pyz` from the release runs it without touching your
project's own toolchain. The rules read text, so the language of the scanner does not limit the languages it reads.

## Why only one line at a time?

A rule that reads one added line is fast, gives the same answer on any machine and explains itself in one sentence.
With `scan --diff` it reads only the lines a change adds; a scan of a whole folder reads the old lines too, and a
baseline holds the findings already there. Most of the mistakes agents repeat are visible in one line. A mistake that
needs the whole file, the types or the call graph is a job for your linter, your type checker or a code scanner, and
brevio-core does not try to replace them.

## Can an agent silence it?

Not through the agent hook or the MCP server. A person can silence one finding with an ignore comment that gives a reason, in a scan they run themselves or in
CI. The agent hook and the MCP server do not honour that comment, so an agent that adds one still sees the finding. A
diff scan reports the ignore comments, baseline entry and `brevio-core.toml` change the change adds as a new
suppression, which needs a person: the check stays red until a reviewer allows it with `--allow-new-suppressions`.
Changing what a rule flags is a code change to the rules, reviewed like any other.

## Does it send my code anywhere?

`scan`, the agent hook and the MCP server use no network and send nothing. The rules make no model call and need no API
key. The security review page lists what each command reads and runs, and tests check it.

The optional `brevio-core ai` commands are the exception, and you choose it: they send a request to a model or a
program you connected, after you read the request and answer yes. `explain` and `fix` send the flagged line and the lines
around it that you allow; `summarize` sends rule IDs, titles and counts; `learn` sends the lines a fix removed. A program you
connect receives the request on its standard input and may send it elsewhere, which brevio-core does not see. Nothing goes to
Brevio. See [the `ai` section of the README](https://github.com/Brevio-Ventures/brevio-core/blob/main/README.md#optional-ask-a-model-you-connect).

## How does it compare with a linter, Semgrep or CodeQL?

It sits beside them. A linter checks style and correctness in the language it knows; Semgrep matches patterns you
write; CodeQL follows data through a program. brevio-core is narrower: a fixed set of rules picked from the mistakes
coding agents repeat, run on the added lines only, with the same output in the agent's loop as on the pull request.
Several of its rules flag code that is valid to a linter. Keep your linter; add this for the agent mistakes.

## How does it compare with an AI code reviewer?

A model-based reviewer reads the change and gives an opinion, which can differ from run to run and costs a model
call. brevio-core gives the same finding for the same line, at no cost per run, and a finding can fail a required
check. The two do not exclude each other.

## What does it check, exactly?

A rule has a page with what it catches, why, a bad and a good example, and how it is checked. The
[rule index](rules/README.md) marks which rules `brevio-core scan` runs.

## Will it slow down my pull requests?

The scan reads only the added lines, with fixed patterns, so it finishes quickly; most of a CI run is the checkout and
the install. The agent hook gives up quietly when it runs past its time budget and lets the agent go on.

## What if a finding is wrong?

Open a "False positive" issue with the line and why it is correct. Until it is fixed, a person can silence that one
finding with an ignore comment and a reason. A rule that fires wrongly too often is narrowed or retired; its ID is
not reused.

## Is there a paid version?

The package is free and open source under Apache-2.0, and stays complete on its own. A paid service, planned and opt
in, will sync learned guards and suppression approvals across repositories and teammates; it asks first and shows the
exact files before anything leaves your machine.
