# Security review of brevio-core

This page is for a security team deciding whether brevio-core may run on its code and in its CI. Each statement says
where it is tested or how to check it yourself. It describes release 0.10.0. To report a vulnerability, see
[SECURITY.md](SECURITY.md).

## In short

- **`scan`, the agent hook and the MCP server use no network and send nothing.** No command of this release, other than `ai`, which opens one only to an address you connected and only after you answer yes, opens a
  connection; the table below lists each one. No module, other than `core_ai_net.py` for `urllib` and `http`, imports `socket`, `ssl`, `urllib`, `http`, `ftplib`,
  `smtplib`, `telnetlib` or the connection helpers of `asyncio`, and none uses `eval`, `exec`, dynamic imports from
  outside the package, `pickle` or `ctypes`. The only program it starts is `git`, other than the program you name for an `ai` command connector, which only `core_ai_exec.py` starts. The test
  [`tests/pipeline/core_export/test_core_security.py`](tests/pipeline/core_export/test_core_security.py) reads
  every shipped module with Python's `ast` module and fails on any of these, then runs `scan`, the agent hook command,
  the MCP server and `up` with `socket` replaced by a stub that fails the run on any use. A second test runs more of the
  commands the same way: [below](#run-the-commands-with-the-network-refused).
- **No runtime dependencies.** `pyproject.toml` lists none, so installing brevio-core installs nothing else.
- **Licence:** Apache-2.0, the full text in [LICENSE](LICENSE).
- **No model, no API key, no telemetry.** No command counts its use or reports it anywhere, and there is no setting
  to turn that on. The one exception to the model part is `brevio-core ai`, which calls only a model you connect yourself; see [the `ai` commands](#the-ai-commands-optional).

## Threat model

| What runs | Token it holds | What it reads | What it writes |
|---|---|---|---|
| The GitHub Action | The job's `GITHUB_TOKEN`, with only the permissions your workflow grants | The checkout, its git history and the event payload | The job summary, warning annotations, one pull request comment, the SARIF upload, and `brevio-gate.sarif` in the workspace (removed unless `sarif: true`) |
| `brevio-core scan` (the command, the pre-commit hook) | None | The files you name, or the lines a diff adds, and `.brevio/guards.json` when it exists | Nothing, unless you pass `--sarif FILE` or `--fix` |
| The agent hook (`brevio-core hook run`) | None | The client's event on standard input, then the lines the edit changed in the one file it names, or the uncommitted changes when the agent stops; after a shell command, only the command's text, to see whether it was a fix commit | One reply in the client's own JSON format on standard output when there is a finding, or one line suggesting `learn` after a fix commit; nothing else |
| `brevio-core mcp` | None | The folder the server was started in | Only through `fix` with `write: true`, and `learn` (a guard under `.brevio/` and `.agents/rules/`) |
| `brevio-core hook install`, `init`, `baseline`, `learn`, `add` | None | The project folder and its git history | Only the files each command lists when it runs, inside the project folder |
| `brevio-core ai` (optional) | The key from the environment variable you named, read for the one call | Your settings file, the flagged line and the lines you allow around it, the rule's text and `brevio-core.toml` | Your settings file (`connect`, the first-use agreement), a count of calls beside it and, with `fix --apply` and your second yes, the one file the finding names |
| `brevio-core ai` with a command connector (optional, preview) | The variables you listed with `--pass-env`, passed to the program | Your settings file, the lines or the rule IDs the request holds, and `brevio-core.toml` | A temporary folder that is removed afterwards, your settings file and the count; what the program writes elsewhere is its own doing |

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
| `ai` | Only to the address you connected, after you read the request and answer yes: one request, no retry, no redirect followed, plain http only to `localhost` or a loopback address. An https request goes through the proxy your environment names, and the command prints that proxy's host. Until you run `ai connect` there is nowhere to send. With a command connector brevio-core opens no connection of its own: it starts the program you named, which can make its own |
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
- It runs the commands listed above, and not `up`, `add`, `init`, `heal`, `patch` or the Action. The optional `ai` group is the one part of this package that makes a request or starts a program other than `git`, and this test does not run it; [its section](#the-ai-commands-optional) says where a request goes.

Two more pages: [what the gate can and cannot see](docs/what-the-gate-sees.md), and
[install from a mirror or a downloaded file](docs/install-from-a-mirror.md).

## The `ai` commands (optional)

`brevio-core ai` is the one part of this package that can call a model or start a program other than `git`, and only the
endpoint or the program you connect, after you answer yes. The gate does not load it: `scan`, the agent hook, the MCP server, `check`, `init`, `baseline`, `heal`, `patch`, `up`,
`rules`, `learn`, `--help` and `help --all` run without loading its modules. A test runs each of them in a fresh interpreter and
lists what was loaded. A second test reads the source of the other modules and fails when one imports an `ai` module, calls
the loader's table of `ai`, or loads a module by a name that is not written out in the source. `help --all` lists `ai` from a
one-line text held in the loader; the module is imported only when `ai` is the command being run.

- **Where a request goes.** Only to the address, or the program, in your own settings file, which brevio-core does not read from a repository.
  A `brevio-core.toml` may only narrow (`enabled = false`, `allow`, `deny_paths`). `ai` refuses an `[ai]` table with an
  address, a key or a model, and, as the gate does, a key or table it does not know. The address must use https, or plain
  http to `localhost` or a loopback address, which needs no key and does not go through a proxy. An https request goes
  through the proxy your environment names (`HTTPS_PROXY`, with `NO_PROXY` honoured), and the command prints that proxy's
  host before it asks. The proxy carries the request and sees where it goes. A redirect is not followed, because it would
  carry your key to another host. One module, `core_ai_net.py`, imports `urllib` and `http`. The test lists the network
  modules (such as the sockets, TLS, HTTP, mail and server modules of the standard library, `logging.handlers`, and common
  HTTP clients from outside it) and fails on another module that imports one of them, under an alias or not, or that starts
  a process other than a read-only `git` call by its plain name; `core_ai_exec.py` is the one named exception, for the program
  you named. It reads the source, so it does not see code built at run time.
- **The key.** The settings file holds the name of an environment variable, not a key. `connect` refuses a name that is not a
  plain variable name or that looks like a key, and an address with a user name, a password, a query string, a fragment, a
  `;` parameter or a part of its path that looks like a key. The check on the shape knows common key formats and misses others. The key is read at the moment of the call and is not
  stored. It is not printed: a key a header will not accept, such as one with a line break, is refused with a message
  that does not hold it.
- **Your settings and the count.** brevio-core writes the settings file readable by you only. Before a send it checks, on
  POSIX systems, that the file is yours and closed to group and others, and that its folder is not writable by them; if not,
  it says why and makes no request. The settings file and the count of calls beside it are your own files. A program running
  as you can change both, so the limits guard against runaway use and not against someone with your account.
- **What leaves.** The rule's text, the flagged line, the file's extension (the text after the last dot of its name), and
  the lines around the flagged one: none for `explain` and five either side for `fix`; `--context N` changes either, down
  to none. Anything written in those lines goes with them. `summarize` sends the rule IDs, the rule titles and the number of
  findings per rule from a scan of the diff, and no source line and no file name. `learn` sends the lines a fix
  removed with runs of spaces folded to one and up to a cap, and the file's extension. brevio-core adds no file name or path of its own, and sends no
  other file. It makes no request when a line in the request looks like a secret. The check is the scanner's secret
  rule plus private-key markers, a password in an address, a written-out bearer token, a JSON web token and a long quoted token assigned to a
  name that holds key, token, secret, password or credential. It knows a set of formats and misses others, and one hit stops
  the call. For `explain` and `fix` it makes no request for a file named `.env`, `.env.*`, `.envrc`, `.netrc`, `.npmrc`, `.pypirc`, `credentials*`,
  `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `*.keystore`, `*.kdbx`, `*.tfvars`, `.git-credentials`, `.htpasswd`,
  `.pgpass`, `.my.cnf`, `.s3cfg`, `.dockercfg`, `kubeconfig`, `service-account*.json`, a `secrets` or `secret` file of
  a data format (`secrets.yml`, `secret.json`) or `id_*`, or for a link to one, or for a file with a second name (a hard link,
  which no check of names can see through), or for a path under `deny_paths` by its name or by the path a link leads to.
  `deny_paths` matching is case-sensitive. `learn` applies the secret-looking names and `deny_paths` to the file of the fix,
  and not the checks of links.
- **Each call.** The command prints the address, the proxy's host when there is one, the names of the headers, the size of
  the body in bytes and the body. The body it prints is the bytes it sends. Control characters, line separators and
  bidirectional or other format characters are spelled out on the screen only. Then it asks `Send it? [y/N]`. `--yes`
  answers that question, at a terminal too, and not the first-use agreement or `--apply`; `learn` takes no `--yes`. The agreement is recorded for
  the repository at its path and its first commit (the path alone until it has one), so a copy or a clone at another path, or a different history at the same
  path, asks again, and the same history put back at the same path does not. There is no retry and no second
  address. A limit on calls per local calendar day, counted in a ledger next to your settings file, stops further calls. The
  check and the count are one locked step where the system has file locks (POSIX), so two commands at once do not both pass
  a limit of one. Without locks they can. A run makes one call, so there is no per-run limit. Deleting the ledger starts the
  count again. `BREVIO_AI=off` stops `connect`, `explain` and `fix`; `status` still runs and says it is off. It stops
  `summarize` and `learn` as well.
- **What comes back is data.** `explain` prints it between an opening line that names the connection and the model and says
  brevio-core did not write it, and a closing line. Each line of it starts with `| `, and control characters, line and
  paragraph separators and format characters are spelled out, so text after the closing line is brevio-core's own. `fix`
  applies the reply in memory and shows a change only after the rules have checked it: the rule no longer flags the line, no
  finding is new, the reply adds no ignore comment of brevio-core or of the other tools in a listed set on a line that did
  not have it, one file changes (a link to a secret file, or into a folder that sets what the agents do, is refused, and so is
  a fix for the name of a link, which would put a copy in its place: name the file it leads to), a
  `.py` file still compiles, and a test keeps its assertions (the same checks as `patch`). Other file types get no syntax
  check. A reply the rules refuse is shown as its reasons, which can quote a short stretch of the reply, and not as a
  change. `--show-refused` adds the reply, framed as refused model output. The checks show the rule is quiet, not that the
  change is right. `fix` writes the working tree only with `--apply` and a second yes at a terminal, and commits nothing. `summarize`
  escapes the answer as inert Markdown, and prints it in a block that says it is model-written and not part of the gate.
  `learn` reads the reply as patterns to check, not as text to keep: a pattern must pass the regular-expression check, the
  control run and the replay of `brevio-core learn` before it is written, and the guard is written after a second yes at a
  terminal, on trial, with its origin recorded as `model-proposed`.
- **The command connector (preview).** `connect --program PATH` stores the program's absolute path, found when you connect, and
  its arguments as separate strings in your own settings file; an `[ai]` table in a repository's `brevio-core.toml` that names
  a program is refused as routing. A call starts the program once, from its absolute path and with no shell. The request goes
  to its standard input and not into its arguments; an argument that starts like one of the common key formats is refused when you connect (the
  check misses others), because other users of the machine can read a program's arguments. Its working folder is a new empty temporary folder, removed
  afterwards. Its environment holds `PATH`, `LANG` and `LC_ALL` on POSIX systems, and on Windows `PATH`, `PATHEXT`,
  `SYSTEMROOT`, `SYSTEMDRIVE`, `WINDIR`, `COMSPEC`, `TEMP` and `TMP`, where they are set, and the variables you listed with
  `--pass-env`. It is stopped after the timeout, and when it writes more than a size cap. Where the system has process
  groups, the program and the processes that share its group are stopped together, at the timeout and when the program
  ends; a process that starts a group of its own is not stopped, and on Windows only the program is. A batch file is refused on
  Windows. brevio-core starts it only for a person at a terminal, and not when `CI`, `CONTINUOUS_INTEGRATION` or
  `BUILD_NUMBER` is set; other build systems are not recognised. What it writes is treated as an endpoint's reply is. The
  program receives the request and may itself send it elsewhere, and brevio-core does not see what it does. It is not a
  sandbox: the program runs with your permissions and can read your files, so connect a program you trust with the request.
  One module, `core_ai_exec.py`, starts it. The security test names that module as the one exception to the rule that a module
  starts only a read-only `git` call, and still refuses `os.system` and a network import in it; a test of that module
  refuses a shell. A test runs the
  commands of the gate in a fresh interpreter and lists what was loaded, which does not include this module.
- **Nothing goes to Brevio.** There is no telemetry, and the ledger is a local file that no command uploads.

## Git

brevio-core runs `git` with a fixed set of read-only subcommands: `check-ignore`, `diff`, `ls-files`, `ls-tree`,
`rev-parse` and `show`. Every
call sets `core.hooksPath` to `/dev/null` and turns `core.fsmonitor` off, so a hook or a file-system monitor that a
scanned repository sets up never runs. The same test checks that every program started is `git`, and that every
subcommand is on this list. The one exception is `core_ai_exec.py`, which starts the program you named for an `ai` command connector and nothing else; the security test names that module.

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
  pip download --no-deps brevio-core==0.10.0
  pipx run pypi-attestations verify pypi --repository https://github.com/Brevio-Ventures/brevio-core pypi:brevio_core-0.10.0-py3-none-any.whl
  ```

- **The `.pyz`.** Each GitHub release carries `brevio-core.pyz` with a build-provenance attestation. Check it before
  you run it:

  ```sh
  gh attestation verify brevio-core.pyz --repo Brevio-Ventures/brevio-core
  ```

## Pinning the Action

`@v0.10.0` installs exactly brevio-core 0.10.0 (`package-version` defaults to the release of the Action). For a
pin that cannot move, use the full commit SHA of the tag. Find it with:

```sh
git ls-remote https://github.com/Brevio-Ventures/brevio-core 'refs/tags/v0.10.0^{}' 'refs/tags/v0.10.0'
```

The line that ends in `^{}` is the commit of an annotated tag; a lightweight tag has only the other line. Then write
that SHA in the workflow, with the version as a comment:

```yaml
- uses: Brevio-Ventures/brevio-core@<the full commit SHA> # v0.10.0
```

The Action pins the third-party actions it uses (`actions/setup-python`, `github/codeql-action/upload-sarif`) to full
commit SHAs, and the same test fails on any `uses:` in `action.yml` that is not pinned that way.

## Supported versions and reporting

Only the latest release gets fixes. Report a vulnerability privately to security@brevio.pro or through GitHub's
private vulnerability reporting on this repository, never in a public issue. We acknowledge a report within a week,
keep you told of the fix, and agree a disclosure date with you.
