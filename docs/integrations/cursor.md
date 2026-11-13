# brevio-core with Cursor

Cursor gets a hook after each file write, a hook before it stops, rule files and the MCP server.

## Set it up

In the repository:

```sh
brevio-core init --with hook,mcp
```

It shows every file it would write as a diff and asks first (`--dry-run` writes nothing). For Cursor it writes:

| File | What it holds |
| --- | --- |
| `.cursor/hooks.json` | a `postToolUse` hook on `Write` and a `stop` hook, both running `brevio-core hook run --client cursor` |
| `.cursor/rules/brevio-core.mdc` | when to run brevio-core and how to read a finding |
| `.cursor/rules/brevio-core-agents.mdc` | the same rules as the `AGENTS.md` block, applied to every request |
| `.cursor/mcp.json` | the MCP server, `uvx brevio-core==0.10.0 mcp` |

It also writes the pull-request workflow, the `AGENTS.md` block and a baseline, as for every client. The hook and MCP
files are personal: `init` lists them in git's local exclude file, so they are not committed. To wire only the hook,
run `brevio-core hook install --target cursor`. Other hooks in `.cursor/hooks.json` are kept.

## What the agent sees

After each write, the hook scans the lines that file now adds and returns the findings as additional context, one
short line each (`path:line [RULE] message -> fix`). When the agent is about to stop, the hook scans the uncommitted
changes and, on a finding, sends one follow-up message asking for the fix; the stop hook is limited to one follow-up,
so the agent cannot loop. If the hook fails or runs past its time budget, it lets the agent go on.

## The MCP server

The tools are `scan`, `explain`, `list_rules`, `learn` and, from release 0.3, `fix`. To add the server by hand, put
this in `.cursor/mcp.json`:

```json
{ "mcpServers": { "brevio-core": { "command": "uvx", "args": ["brevio-core==0.10.0", "mcp"] } } }
```

`scan`, the agent hook and the MCP server use no network and send nothing.

## Check it

```sh
brevio-core hook status
```
