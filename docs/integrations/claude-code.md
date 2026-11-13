# brevio-core with Claude Code

Claude Code gets the full loop: a hook after each edit, a hook before it stops, the agent skill, the MCP server and
the rules in `AGENTS.md`.

## Set it up

In the repository:

```sh
brevio-core init --with hook,mcp
```

It shows every file it would write as a diff and asks first (`--dry-run` writes nothing). It writes:

| File | What it holds |
| --- | --- |
| `.github/workflows/brevio-core.yml` | the pull-request check, pinned to this release |
| `AGENTS.md` | a marked block of rules for agents; an `@AGENTS.md` line is added to `CLAUDE.md` if you have one, so Claude Code reads it |
| `.claude/settings.json` | a `PostToolUse` hook on `Edit`, `Write` and `MultiEdit`, and a `Stop` hook, both running `brevio-core hook run` |
| `.claude/skills/brevio-core/SKILL.md` | the agent skill: when to run the commands and how to read a finding |
| `.mcp.json` | the MCP server, `uvx brevio-core==0.10.0 mcp` |
| `.brevio/baseline.json` | the findings the repository has today, so only new ones are reported |

The hook and MCP files are personal: `init` lists them in git's local exclude file, so they are not committed and a
teammate's checkout is not changed. `brevio-core init --undo` removes what `init` wrote and you have not changed
since. To wire only the hook into an existing setup, run `brevio-core hook install --target claude_code`.

## What the agent sees

After each edit, the hook scans the lines that file now adds. On a finding it answers Claude Code with
`decision: block` and one short line per finding (`path:line [RULE] message -> fix`), so the agent fixes it in the
same turn. When the agent is about to stop, the hook scans the uncommitted changes once; it does not block a second
time in the same stop, so the agent cannot loop. If the hook fails or runs past its time budget, it lets the agent go
on: a broken hook never stops your work.

## The MCP server

The tools are `scan`, `explain` (a rule's page), `list_rules`, `learn` and, from release 0.3, `fix`. To add the server
by hand instead of with `init`:

```json
{ "mcpServers": { "brevio-core": { "command": "uvx", "args": ["brevio-core==0.10.0", "mcp"] } } }
```

`scan`, the agent hook and the MCP server use no network and send nothing. The server reads only the folder it was
started in.

## Check it

```sh
brevio-core hook status
```
