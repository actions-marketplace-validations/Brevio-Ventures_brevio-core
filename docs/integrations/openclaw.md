# brevio-core with OpenClaw

OpenClaw loads skills from the workspace's `skills` folder, so brevio-core reaches it as a skill, and as an MCP server
if you add one.

## Set it up

In the repository:

```sh
brevio-core hook install --target openclaw
brevio-core init
```

`hook install` writes the agent skill to `skills/brevio-core/SKILL.md`. Its front matter asks for the `brevio-core`
command, so OpenClaw shows the skill only where the package is installed. `init` writes the pull-request workflow, the
`AGENTS.md` block and a baseline.

The same skill is published to the skill registry with each release, so it can be installed from there too.

## The MCP server

Add brevio-core where your OpenClaw configuration lists MCP servers, with this command and these arguments:

```json
{ "brevio-core": { "command": "uvx", "args": ["brevio-core==0.10.0", "mcp"] } }
```

`scan`, the agent hook and the MCP server use no network and send nothing.

## What the agent sees

The skill tells the agent to scan the files it changed with `brevio-core scan` and to fix each finding in the code
rather than silence it. brevio-core installs no hook after each edit for OpenClaw, so the pull-request check is the backstop.
