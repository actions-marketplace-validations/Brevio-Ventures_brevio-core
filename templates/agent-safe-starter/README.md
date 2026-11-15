# Agent-safe starter

A starting point for a repository where coding agents write code. It catches the mistakes coding agents repeat before
they merge, with brevio-core 0.11.0: no model call and no API key.

## What is in it

| File | What it does |
| --- | --- |
| `.github/workflows/brevio-core.yml` | scans the lines each pull request adds, on pull requests and in a merge queue, and comments on the findings. The Action is pinned to a release and checkout to a commit |
| `AGENTS.md` | rules most coding agents read before they start: scan what you changed, fix findings in the code, never silence one, ask a person when stuck |

Add your own instructions to `AGENTS.md` above or below the marked block. `brevio-core init` recognises the block and
leaves it alone.

## Use it

Press "Use this template" on GitHub, or copy the two files into an existing repository. Then, on your machine:

```sh
pip install brevio-core==0.11.0
brevio-core init --with hook,mcp
```

`init` sees the workflow and the `AGENTS.md` block already here and keeps them. It adds the hook and the MCP server
for the coding agents it finds, and a baseline once there is code. See the
[integration pages](https://brevio-ventures.github.io/brevio-core/integrations/) for each client.

## Keep it pinned

The workflow names exact versions. To update, change `v0.11.0` and `package-version` together, or let Dependabot's
`github-actions` updates do it. To pin the Action to a commit instead of a tag, see the
[Action guide](https://brevio-ventures.github.io/brevio-core/action/).

## For the brevio-core maintainers

This folder is the template's content. To publish it: create a new public repository (for example
`Brevio-Ventures/agent-safe-starter`), copy this folder's files to its root, tick "Template repository" in its settings, and
update the pinned versions with each release.
