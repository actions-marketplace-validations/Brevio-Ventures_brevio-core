# What the gate can and cannot see

For a security team that wants to write the gap into its own risk register. This page lists what `scan`, `check`, the
agent hook, the MCP server, `patch` and `learn` read, and what they leave out. Where a line can be checked, the page says how. The gate
reads text that a change adds. A sandbox for the agent's process, a network policy, your client's permission settings and
its own monitoring are the layers that cover what the gate does not read; this package holds none of them.

## What it reads

| It reads | How | Check it |
|---|---|---|
| The lines a change adds | `scan --diff` reads the lines added against a base (`origin/main` unless you name one), `check` and the agent hook read them against `HEAD`, and `patch` reads the lines an edit would change | `brevio-core scan --diff HEAD` on a change. The [rule page](rules/README.md) of a scanned rule says a scan reads only the lines you add |
| Files you name, or a folder | `scan PATH` reads whole files. A folder scan reads files with a code extension, and skips dependencies, build output, vendored folders, minified files and bundles | `brevio-core config PATH` shows what applies to a file or folder |
| Test files a change touches | `check`, and the hook when the agent stops, compare them with the base: a deleted test file, a test file renamed to a file that is no test, fewer tests or assertions, an added skip or focus marker, a tautological assertion, and (in `check`) an empty test. They are the rules `R20.1` to `R20.5`, in preview | `brevio-core explain R20.1` |
| Git history, read only | `learn` reads the last fix and recent commits, and a diff scan reads its base. The git subcommands are `check-ignore`, `diff`, `ls-files`, `ls-tree`, `rev-parse` and `show` | [SECURITY-REVIEW.md](https://github.com/Brevio-Ventures/brevio-core/blob/main/SECURITY-REVIEW.md) lists them, and a test reads the source for them |
| Files in the project that set what it does | `brevio-core.toml`, `.brevio/baseline.json`, `.brevio/guards.json` and the agent rules under `.agents/rules/` | `brevio-core config` |
| What the client tells the hook | The path of the edited file, and the text of a shell command, which it reads only to see whether the command was a fix commit | The hook section of [SECURITY-REVIEW.md](https://github.com/Brevio-Ventures/brevio-core/blob/main/SECURITY-REVIEW.md) |

## What it does not read

- **Network traffic.** The gate watches none: not the agent's, not that of code the agent runs, not that of a program a
  shell command starts. [Run the commands with the network refused](https://github.com/Brevio-Ventures/brevio-core/blob/main/SECURITY-REVIEW.md#run-the-commands-with-the-network-refused)
  shows which commands were run that way, and which were not.
- **What the agent reads or asks for.** `brevio-core hook install` connects the hook to the edit tools (`Edit`, `Write`
  and `MultiEdit` in Claude Code), to the shell tool (`Bash`) and to the stop event. A file the agent opens, a search, a
  web fetch, a call to another tool or server, a request to a model and the conversation itself are not sent to the gate.
  Look in `.claude/settings.json` or `.cursor/hooks.json` after `hook install` to see the events.
- **What a shell command does.** The hook reads the command's text to see whether it was a fix commit, and does nothing
  else with it. A `curl`, a `pip install`, a deletion or a script a command starts is not read and not stopped.
- **Anything outside the repository.** Other folders, environment variables and the machine's settings are not read. The
  MCP server resolves a path a client names through symlinks and refuses one outside the folder it was started in.
- **Lines that were already there.** A diff scan reads the lines a change adds, so a change that makes old code wrong
  is not reported unless the new lines show it.
- **A mistake no rule names.** The rules are line-level pattern checks, not syntax-tree or dataflow analysis, and a rule
  reads the file types its rule page names. A learned guard matches text taken from a past fix, not what the code means.
- **Files it skips.** Dependencies, build output, vendored folders, minified files, bundles and TypeScript declaration
  files. A folder scan also skips files that have no code extension; a file you name is scanned as you asked.
- **A reason to stop.** The hook is not a barrier. On its own error, or when the scan runs past its time budget, it
  prints one line on standard error and lets the agent go on, and it lets the agent stop once it has sent the agent
  back once. A rule in preview reports and does not fail a scan.
- **A person's decision.** A person can silence one finding with an ignore comment and a reason, record findings in
  the baseline or turn a rule off in `brevio-core.toml`. The agent hook and the MCP server do not honour an ignore
  comment, and a change that adds one, or edits the baseline or the config, is reported for a reviewer.

## Where the rest is covered

Network policy, a sandbox for the agent's process, the client's own permission prompts and its monitoring or
telemetry features, and your usual code review and CI sit beside this gate. brevio-core does not replace them, and its
output can feed them: `scan --sarif FILE` writes a SARIF file, and `scan --format markdown` writes a comment.
