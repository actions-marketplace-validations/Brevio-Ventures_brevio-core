# Security review of brevio-core

This page is for a security team deciding whether brevio-core may run on its code and in its CI. Each statement says
where it is tested or how to check it yourself. It describes release 0.11.0. To report a vulnerability, see
[SECURITY.md](SECURITY.md).

## In short

- **`scan`, the agent hook and the MCP server use no network and send nothing.** No command of this release opens a
  connection; the table below lists each one. No module imports `socket`, `ssl`, `urllib`, `http`, `ftplib`,
  `smtplib`, `telnetlib` or the connection helpers of `asyncio`, and none uses `eval`, `exec`, dynamic imports from
  outside the package, `pickle` or `ctypes`. The only program it starts is `git`. The test
  [`tests/pipeline/core_export/test_core_security.py`](tests/pipeline/core_export/test_core_security.py) reads
  every shipped module with Python's `ast` module and fails on any of these, then runs `scan`, the agent hook command,
  the MCP server and `up` with `socket` replaced by a stub that fails the run on any use. A second test runs more of the
  commands the same way: [below](#run-the-commands-with-the-network-refused).
- **No runtime dependencies.** `pyproject.toml` lists none, so installing brevio-core installs nothing else.
- **Licence:** Apache-2.0, the full text in [LICENSE](LICENSE).
- **No model, no API key, no telemetry.** No command counts its use or reports it anywhere, and there is no setting
  to turn that on.

## Threat model

| What runs | Token it holds | What it reads | What it writes |
|---|---|---|---|
| The GitHub Action | The job's `GITHUB_TOKEN`, with only the permissions your workflow grants | The checkout, its git history and the event payload | The job summary, warning annotations, one pull request comment, the SARIF upload, and `brevio-gate.sarif` in the workspace (removed unless `sarif: true`) |
| `brevio-core scan` (the command, the pre-commit hook) | None | The files you name, or the lines a diff adds, and `.brevio/guards.json` when it exists | Nothing, unless you pass `--sarif FILE` or `--fix` |
| The agent hook (`brevio-core hook run`) | None | The client's event on standard input, then the lines the edit changed in the one file it names, or the uncommitted changes when the agent stops; after a shell command, only the command's text, to see whether it was a fix commit | One reply in the client's own JSON format on standard output when there is a finding, or one line suggesting `learn` after a fix commit; nothing else |
| `brevio-core mcp` | None | The folder the server was started in | Only through `fix` with `write: true`, and `learn` (a guard under `.brevio/` and `.agents/rules/`) |
| `brevio-core hook install`, `init`, `baseline`, `learn`, `add` | None | The project folder and its git history | Only the files each command lists when it runs, inside the project folder |

The Action needs these permissions, and works with fewer:

- `contents: read` to check out the code;
- `pull-requests: write` for the comment. Without it, or on a pull request from a fork (whose token is read-only), the
  Action writes the job summary only, says why, and does not fail;
- `security-events: write` for the SARIF upload. A repository without code scanning rejects the upload, and the Action
  goes on.

The Action runs on `pull_request`, `merge_group` and `push` only, and refuses any other event. It never supports
`pull_request_target` or `workflow_run`: those run with a write token, and the scan reads the code under review, so
code from a fork would run next to that token. The same test fails if either name appears in `action.yml` or in a
workflow this repository ships. The Action needs no secret.

## What each command sends over the network

| Command | Network |
|---|---|
| `scan`, the agent hook, `mcp`, `learn`, `gain`, `hook install`, `init`, `baseline` | None |
| `add` | None: the packs it installs ship inside the package |
| `up` | None: it lists the files a team sync would take, with their sizes and fields. The sync is not in this release |
| `share`, the `up` sync | Not in this release. Once they ship, only after you answer yes to the prompt in [PRIVACY.md](PRIVACY.md) |

Tools you run around brevio-core can download it: `uvx`, `pipx` and `pip` fetch the package from PyPI, and the Action
installs it with `pip`. Those downloads are the tools' own; the commands in the table make none.

## Run the commands with the network refused

The test [`tests/pipeline/core_export/test_core_no_network.py`](tests/pipeline/core_export/test_core_no_network.py)
starts `scan`, `scan --diff`, `check`, the agent hook (`hook run`), one MCP request, `baseline`, `learn` and `gain` in a
child process, under `python -I`, in which opening a socket raises (`socket.socket` and `socket.create_connection` are
replaced by stubs that raise and say so). The test checks that each of them finishes with the exit code it is expected
to give and a line of its usual output, and it fails if one of them tries to open a socket. Another test in the same file
checks that the stubs do stop a connection. To run it from a checkout of this repository:

```sh
python -m unittest tests.pipeline.core_export.test_core_no_network -v
```

What it does not cover:

- The refusal is in the Python process. `git`, which these commands start, is a separate program, and so are `pip`,
  `uvx` and `pipx`, which fetch the package. A network policy or a firewall around the machine covers those; this test
  does not.
- It runs the commands listed above, and not `up`, `add`, `init`, `heal`, `patch` or the Action.

Two more pages: [what the gate can and cannot see](docs/what-the-gate-sees.md), and
[install from a mirror or a downloaded file](docs/install-from-a-mirror.md).

## Git

brevio-core runs `git` with a fixed set of read-only subcommands: `check-ignore`, `diff`, `ls-files`, `ls-tree`,
`rev-parse` and `show`. Every
call sets `core.hooksPath` to `/dev/null` and turns `core.fsmonitor` off, so a hook or a file-system monitor that a
scanned repository sets up never runs. The same test checks that every program started is `git`, and that every
subcommand is on this list.

## The MCP server

`brevio-core mcp` speaks JSON-RPC on standard input and output and opens no port. It serves the folder it was started
in:

- `scan`, `explain` and `list_rules` only read. A path a client names is resolved through symlinks and refused when it
  lies outside that folder. Inside a named folder the scan reads the code files it finds and follows a symlink there;
  a finding names the rule, the path, the line and the rule's message. A finding of a learned guard also quotes the
  pattern the guard holds, cut to a set length: text from your repository that `learn` stored in `.brevio/guards.json`.
- The MCP `scan` does not honour an ignore comment, so an agent that adds one still sees its finding.
- `fix` returns a diff and writes the files only when the call sets `write: true`.
- `learn` compiles the last fix into a guard and writes it under `.brevio/` and `.agents/rules/`, when the guard passes a
  control run. It reads git history for the replay (`git show`). The MCP server has no tool that switches a guard off, and
  `learn dismiss` and `learn wrong` are refused where input or output is not a terminal.
- A diff base must be a git revision; a value that starts with `-` is refused, so it cannot pass an option to `git`.

## The agent hook

`brevio-core hook install` adds `brevio-core hook run --event post-edit` and `--event stop` to Claude Code
(`.claude/settings.json`) and Cursor (`.cursor/hooks.json`). The client passes its event as JSON on standard input;
the hook reads only the path of the edited file from it, never the edit's text or the conversation. After an edit
it scans the lines that file now adds against HEAD; when the agent is about to stop it scans the uncommitted
changes, and it lets the agent stop when a stop hook has already sent it back once. It never honours an ignore
comment. Findings go back as one JSON reply on standard output, which the client shows to the agent. It writes no
file. Its exit code is 0 every time.
On its own error, or when the scan runs past its time budget, it prints one line on standard error and lets the agent
go on, so a broken hook never blocks an agent.

## Provenance

- **PyPI.** Releases are published by trusted publishing (OIDC) from this repository's release workflow, with no
  stored token. PyPI keeps an attestation for each file, shown on the project page. To check one yourself:

  ```sh
  pip download --no-deps brevio-core==0.11.0
  pipx run pypi-attestations verify pypi --repository https://github.com/Brevio-Ventures/brevio-core pypi:brevio_core-0.11.0-py3-none-any.whl
  ```

- **The `.pyz`.** Each GitHub release carries `brevio-core.pyz` with a build-provenance attestation. Check it before
  you run it:

  ```sh
  gh attestation verify brevio-core.pyz --repo Brevio-Ventures/brevio-core
  ```

## Pinning the Action

`@v0.11.0` installs exactly brevio-core 0.11.0 (`package-version` defaults to the release of the Action). For a
pin that cannot move, use the full commit SHA of the tag. Find it with:

```sh
git ls-remote https://github.com/Brevio-Ventures/brevio-core 'refs/tags/v0.11.0^{}' 'refs/tags/v0.11.0'
```

The line that ends in `^{}` is the commit of an annotated tag; a lightweight tag has only the other line. Then write
that SHA in the workflow, with the version as a comment:

```yaml
- uses: Brevio-Ventures/brevio-core@<the full commit SHA> # v0.11.0
```

The Action pins the third-party actions it uses (`actions/setup-python`, `github/codeql-action/upload-sarif`) to full
commit SHAs, and the same test fails on any `uses:` in `action.yml` that is not pinned that way.

## Supported versions and reporting

Only the latest release gets fixes. Report a vulnerability privately to security@brevio.pro or through GitHub's
private vulnerability reporting on this repository, never in a public issue. We acknowledge a report within a week,
keep you told of the fix, and agree a disclosure date with you.
