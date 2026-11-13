# Agent tools (the 0.2 set)

| In Plain English (For Anyone) | Technical Contract (For Engineers) |
| --- | --- |
| **What it does:** Gives your coding agent local hooks, learned bug guards, and an MCP server so it hears about its own mistakes while editing; the hooks report findings and do not rewrite code.<br>**When to use it:** Inside any git checkout alongside Claude Code, Cursor, Codex, Hermes, or OpenClaw. | **Commands:** `brevio-core learn`, `brevio-core hook install`, `brevio-core gain`, `brevio-core add`, `brevio-core mcp`<br>**Artifacts:** `.brevio/guards.json`, `.agents/rules/`, `skills/brevio-core/SKILL.md`<br>**Exit Codes:** The exit code is 0 when nothing is found, 1 when not. |

Five commands and an agent skill for the project you run them in. All are local, with no model call and no API key;
`scan`, the agent hook and the MCP server use no network and send nothing. They work in the current folder, which
should be a git checkout.

## `brevio-core learn`

Turns a fixed defect into a guard.

```
brevio-core learn                  # from the last commit (git diff HEAD~1)
brevio-core learn --diff main~3    # from another commit or range
brevio-core learn --name "no sleep in handlers" --category robustness
brevio-core learn --check src/a.py src/b.py
brevio-core learn --worktree --dry-run      # a fix not committed yet: print the guard and its hits, write nothing
brevio-core learn --staged                  # the staged fix (git diff --cached)
```

It reads the lines the fix removed and writes the guard to `.brevio/guards.json`, plus an agent rule to
`.agents/rules/<id>.md`, when the guard passes a control run: it must match the lines the fix removed and not the file
the fix produced. A guard that fails is not written, and the reason is printed. A new guard is on trial: `scan`, the
agent hook, the MCP `scan` tool and the Action report its hits, and a hit of a guard on trial does not fail a run.
`--check` runs the live guards over whole files and exits 1 on a hit. `--dry-run` prints the guard, the control run, the
recent commits it would have flagged (`--replay N` sets how many are looked at) and how many lines of the repository it
matches today, so you can see whether a guard is too broad before you keep one. The last line of `learn` names the files
to commit. How a guard is promoted, marked wrong or switched off (preview) is on the [Guards](guards.md) page.

## `brevio-core hook install`

Installs the scan hook for the coding agents found in the folder or on the machine.

```
brevio-core hook status
brevio-core hook detect
brevio-core hook install                       # only the agents it finds
brevio-core hook install --target claude_code  # named agents
brevio-core hook install --all
```

It writes `.claude/settings.json` (Claude Code: `PostToolUse` on `Edit|Write|MultiEdit`, and `Stop`),
`.cursor/hooks.json` (Cursor: `postToolUse` on `Write`, and `stop`), `.cursor/rules/`, and the agent skill (see below)
into the skills folders of the agents it wires. Running it twice changes nothing, and other hooks in those files are
kept.

A hook runs `brevio-core hook run --event post-edit` or `--event stop` (from release 0.4). The client passes the
event as JSON on standard input. After an edit the hook scans the lines that file now adds; when the agent is about to
stop it scans the uncommitted changes, once. Findings go back through the client's own channel, one short line each
(`path:line [RULE] message -> fix`), so the agent fixes them without a human turn. An ignore comment does not count here.
The hook does not stop an agent on its own failure: on an error or past its time budget it lets the agent go on.

## The agent skill

`skills/brevio-core/SKILL.md` in this repository is the skill file: YAML frontmatter with a `name` and a
`description`, then when to run the commands, the commands, and how to read a finding. `hook install` writes the same
text to `.claude/skills/brevio-core/SKILL.md`, `.hermes/skills/brevio-core/SKILL.md` and `skills/brevio-core/SKILL.md`
and replaces any older skill text it finds there. A test checks that the commands the skill names exist.

## `brevio-core gain`

Prints what the local ledger recorded: how many hits `brevio-core learn --check` has found in this repository, how many learned
guards are active and how many are on trial, and how many rules the scan checks. A hit that `scan` or the agent hook reports,
and a finding of the built-in rules, is not recorded.

```
brevio-core gain
brevio-core gain --json
```

Tokens and money are shown only when a caller recorded a measured value for the event. There is no estimate per
recorded hit, so a project with no measured value shows counts and nothing else. It prints no receipt link, badge or score.

## `brevio-core add`

Adds a curated guard pack by name.

```
brevio-core add                    # list the packs
brevio-core add fastapi-async
```

A pack writes its guards to `.brevio/guards.json` and its rules to `.agents/rules/`, active at once: a hit is a warning in
`scan`, as the [Guards](guards.md) page describes. A pack's patterns are written by hand and have not been through the control
run or the replay.

## `brevio-core mcp`

An MCP server over stdio for any agent client that can start one. It speaks newline-delimited JSON-RPC 2.0, answers
`initialize`, `ping`, the tool list and the tool call, and needs no package beyond the standard library. It makes no model call,
and it reads only the folder it was started in: a path outside that folder is refused.

```json
{ "mcpServers": { "brevio-core": { "command": "brevio-core", "args": ["mcp"] } } }
```

| Tool | Arguments | Returns |
|---|---|---|
| `scan` | `paths` (files or folders), `diff_base` (scan only the lines added against a git revision) | the findings, each with path, line, rule id and message; a long list is cut and says so |
| `explain` | `rule` (an id such as `R2.1`) | the rule page: what it catches, why, a bad and a good example |
| `list_rules` | `query` (optional word) | the rule ids, each with a one-line summary |
| `learn` | `diff_base` (default `HEAD~1`), `name` | the compiled guard, when it passes the control run; writes `.brevio/guards.json` and `.agents/rules/`. The server has no tool that switches a guard off |

A tool that cannot do what was asked returns an error result with the reason; the session carries on.
