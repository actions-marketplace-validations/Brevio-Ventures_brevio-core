# brevio-core with Hermes

Hermes loads skills from the project, so brevio-core reaches it as a skill, and as an MCP server if you add one.

## Set it up

In the repository:

```sh
brevio-core hook install --target hermes
brevio-core init
```

`hook install` writes the agent skill to `.hermes/skills/brevio-core/SKILL.md`: when to run the commands, the commands,
and how to read a finding. Running it again replaces an older copy and changes nothing else. `init` writes the
pull-request workflow, the `AGENTS.md` block and a baseline.

## The MCP server

Add brevio-core where your Hermes configuration lists MCP servers, with this command and these arguments:

```yaml
brevio-core:
  command: uvx
  args: ["brevio-core==0.11.0", "mcp"]
```

Start Hermes in the repository: the server reads only the folder it was started in. `scan`, the agent hook and the
MCP server use no network and send nothing.

## What the agent sees

The skill tells Hermes to scan the files it changed with `brevio-core scan` and to fix each finding in the code rather
than silence it. brevio-core installs no hook after each edit for Hermes, so the pull-request check is the backstop.
