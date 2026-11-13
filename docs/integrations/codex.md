# brevio-core with Codex

Codex reads `AGENTS.md` before it starts and can call MCP servers. brevio-core installs no hook for Codex: `hook install`
writes none, and nothing in this repository tests one. Whether Codex offers hooks, and in which version, is not verified
here; read Codex's own documentation before relying on either answer. The pull-request check is the backstop.

## Set it up

In the repository:

```sh
brevio-core init
```

It writes the pull-request workflow, a baseline, and a marked block of rules in `AGENTS.md`: scan the files you
changed with `brevio-core scan` (the block also names the pinned `uvx` command to run when the tool is not installed), fix a
finding in the code, never add an ignore comment or edit the baseline, stop and tell the human when a finding cannot be
fixed, and run `brevio-core learn` after fixing a bug. Codex reads `AGENTS.md` in the folder it works in. `init` asks
before it writes, `--dry-run` shows the diff only, and `brevio-core init --undo` takes it back.

## The MCP server

Add the server to your Codex configuration (`~/.codex/config.toml`):

```toml
[mcp_servers.brevio-core]
command = "uvx"
args = ["brevio-core==0.10.0", "mcp"]
```

Start Codex in the repository: the server reads only the folder it was started in. The tools are `scan`, `explain`,
`list_rules`, `learn` and, from release 0.3, `fix`. `scan`, the agent hook and the MCP server use no network and send
nothing.

## What the agent sees

With no hook installed, findings reach Codex when it runs the scan itself, as `AGENTS.md` asks it to, or through the MCP
`scan` tool. What it does not run, the Action reports on the pull request.
