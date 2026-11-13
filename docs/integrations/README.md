# Coding agent integrations

| In Plain English (For Anyone) | Technical Contract (For Engineers) |
| --- | --- |
| **What it does:** Connects `brevio-core` directly to Claude Code, Cursor, Codex, Hermes, and OpenClaw so your coding agent has its edits checked as it works. The hooks report findings; they do not rewrite code.<br>**When to use it:** Run `brevio-core init` or `brevio-core hook install` in any git repository. | **Commands:** `brevio-core init`, `brevio-core hook install`, `brevio-core mcp`<br>**Catalogue:** 129 rules (14 scanned, 115 documented)<br>**Exit Codes:** The exit code is 0 when nothing is found, 1 when not. |

brevio-core checks an agent's work in three places. Use the three together: a later one can catch what an earlier one let through.

1. **In the agent's loop.** A hook scans a file right after the agent edits it, and the uncommitted changes when
   the agent is about to stop, and hands the findings back to the agent to fix. Where a client has no hook, its rules
   file and the MCP server do the same job less directly.
2. **On the pull request.** The [Action](../action.md) scans the lines the change adds and fails the check on a
   finding, whoever or whatever wrote them.
3. **In the agent's instructions.** `brevio-core init` adds a short block of rules to `AGENTS.md`, which most agents
   read before they start.

The hook and the MCP server do not honour an ignore comment, so an agent that adds one still sees the finding; a person silences a finding in a scan they run.
A client marked "no" or "not set up" in the second column has no brevio-core hook; the table does not say what the client
itself offers.

| Client | Hook after each edit | Skill | MCP server | Instructions |
| --- | --- | --- | --- | --- |
| [Claude Code](claude-code.md) | yes | yes | yes | `AGENTS.md` through `CLAUDE.md` |
| [Cursor](cursor.md) | yes | a rule file | yes | a rule file |
| [Codex](codex.md) | not set up by brevio-core | no | yes | `AGENTS.md` |
| [Hermes](hermes.md) | no | yes | yes | the skill |
| [OpenClaw](openclaw.md) | no | yes | yes | the skill |

To start a new repository with the check and the rules in place, use the agent-safe starter template
([templates/agent-safe-starter](https://github.com/Brevio-Ventures/brevio-core/tree/main/templates/agent-safe-starter)).
