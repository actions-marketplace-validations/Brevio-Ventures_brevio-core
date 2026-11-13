# brevio-core

Catches the mistakes coding agents repeat before they merge: a pull-request gate with no model call and no API key.
`scan`, the agent hook and the MCP server use no network and send nothing.

The optional `brevio-core ai` commands call a model or a program you connect, after you answer yes. They send the
request they print, lines of your code or rule IDs and counts, to that address or to the standard input of that program, and
nothing goes to Brevio: see the [README](https://github.com/Brevio-Ventures/brevio-core/blob/main/README.md#optional-ask-a-model-you-connect) and the
[security review](https://github.com/Brevio-Ventures/brevio-core/blob/main/SECURITY-REVIEW.md#the-ai-commands-optional).

| In Plain English (For Anyone) | Technical Contract (For Engineers) |
| --- | --- |
| **What it does:** Checks code changes for common AI coding mistakes before a pull request merges. `scan --fix` rewrites the few rules that have one correct fix (`--dry-run` shows the diff first).<br>**When to use it:** On your laptop next to your coding agent, or in GitHub Actions on a pull request. | **CLI:** `brevio-core scan`, `brevio-core check`, `brevio-core task` (`connect`, `plan`, `build`, `qa`, `pr`)<br>**Catalogue:** 129 rules (14 scanned, 115 documented)<br>**Exit Codes:** The exit code is 0 when nothing is found, 1 when not. |

## Track 1: Start & Single-Task Workflow

- [Command reference](commands.md): every command and option (`scan`, `check`, `connect`, `plan`, `build`, `qa`, `pr`, `task`, `init --specs`), generated from the code.
- [The GitHub Action](action.md): inputs, permissions, events, merge queues and pinning to a commit.
- [Questions people ask](faq.md): why Python, why one line at a time, why an agent's ignore comment does not count, how it compares.
- [See it work](try-it.md): the README's throwaway repository with what the commands print, and [the report the Action posts](example-comment.md).
- [Changelog](CHANGELOG.md)

## Track 2: Rules & Auto-Fix

- [Rules](rules/README.md): one page per rule, saying what it catches, why, a bad and a good example, and how it is checked.
- [Writing a rule](writing-rules.md): where a rule lives and what a pull request for one needs.

## Track 3: Coding Agents & MCP

- [Integrations](integrations/README.md): what a client gets and how to set it up.
- [Agent tools](agent-tools.md): `learn`, `hook install`, `gain`, `add`, `mcp` and the agent skill.
- [Claude Code](integrations/claude-code.md), [Cursor](integrations/cursor.md), [Codex](integrations/codex.md),
  [Hermes](integrations/hermes.md), [OpenClaw](integrations/openclaw.md)

## Track 4: Formats & Guardrails

- [Guards](guards.md): `.brevio/guards.json` and the agent rules `learn` writes.
- [SARIF](sarif.md): what `scan --sarif` writes for code scanning.
- [What the gate can and cannot see](what-the-gate-sees.md): what it reads, what it leaves out, and what covers the rest.
- [Install from a mirror or a downloaded file](install-from-a-mirror.md): a package mirror, a wheel, the `.pyz` and the Action.
- [Roll out to many repositories](roll-out.md): a required check, the agent hook on the machines, one policy file, and what a policy can switch off.
- [Release integrity checklist](release-integrity.md): steps for the people who publish, and the steps when a release is wrong.
- [Links the tool prints](links-the-tool-prints.md): the web addresses the released files can show, for a check by hand.

## Track 5: Precision, AEO & Releases

- [Precision on open-source repositories](precision.md): sampled precision across 10 repositories and 19,126 files, the commands to run it again and a form to dispute a verdict.
- [Scoring a site for answer engines](aeo.md): `brevio-core aeo`, a separate command.
- [Releases and listings](LISTINGS.md): what a release does by itself, and the maintainers' steps.
- [Source and install instructions](https://github.com/Brevio-Ventures/brevio-core)
