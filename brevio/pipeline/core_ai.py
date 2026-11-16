"""``brevio-core ai``: ask a model, or a program, you connected (the 0.7 set, owner decision D24, 2026-11-11).

The gate (``scan``, the hook, the MCP server, ``check``, ``baseline``) never loads this module: ``core_cli`` registers ``ai``
by name and imports it only when ``ai`` is the command being run. It makes one call for one thing, to the address the user
connected, and only after the user has seen the exact request and said yes::

    brevio-core ai connect --local 11434 --model NAME     an endpoint on this machine; no key
    brevio-core ai connect --url ADDRESS --model NAME --key-env VARIABLE
    brevio-core ai status                                 what is connected, today's count, the limits, the consent
    brevio-core ai explain "src/a.ts:3: [R9.2] ..."       or --last for the first finding of a fresh scan
    brevio-core ai fix FINDING [--apply]                  the reply is checked by the rules before it is shown
    brevio-core ai connect --program PATH [--arg=TEXT]    a program you name instead of an endpoint (preview): it is started
                                                          once, with the request on its standard input
    brevio-core ai summarize --diff BASE                  one call: what kinds of finding a change has (preview)
    brevio-core ai learn [--staged|--worktree|--diff REF] a model's patterns for a guard, kept only if they pass (preview)

Before every call: the kill switch (``BREVIO_AI=off``, which stops ``connect``, ``explain`` and ``fix``; ``status`` still says
so), a settings file only you can write, what the repository's ``brevio-core.toml`` narrows, the limits, a stricter secret
check over every line to be sent, and a refusal of secret files (links followed) and the repository's ``deny_paths``. Then
the request is printed in full (the exact bytes, their size, the header names, the proxy if there is one) and ``Send it?
[y/N]`` asks (``--yes`` answers that one, never the first-use agreement and never ``--apply``). There is no loop, no retry and no second destination, and what the model returns is data: it is printed only
between an opening and a closing line the text cannot contain, ``fix`` accepts it only through ``core_ai_verify`` and
writes nothing without a second yes at a terminal. Nothing is sent to Brevio and nothing is counted anywhere but in the
user's own ledger (2026-11-12, claims check).

Exit 0: done. 1: the call failed, or the fix was refused. 2: usage or a settings problem. 3: nothing was sent, or nothing
was written, because a check, a limit or the user said no. Standard library only; the transport is loaded by name, only
when a request is sent, so no other command of the package ever loads a network module.
"""
from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

from brevio.pipeline import core_ai_state as st
from brevio.pipeline import core_ai_text as words
from brevio.pipeline import core_ai_verify as verify
from brevio.pipeline import core_cli, core_patch, core_platform
from brevio.pipeline.code_rules import PR_RULES_122
from brevio.pipeline.code_rules.details import DETAILS

HELP = core_cli.AI_HELP        # the loader holds the text, so listing commands does not import this module
NET_MODULE = "core_ai_net"      # the transport, loaded by name when a request is sent: it is the one module that may reach the network
EXEC_MODULE = "core_ai_exec"    # the command connector, loaded by name when a program is run: the one module that may start one
MORE_MODULES = {"summarize": "core_ai_summarize", "learn": "core_ai_learn"}     # the two later commands, loaded when they run
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_NOT_SENT = 0, 1, 2, 3
DEFAULT_CONTEXT = {"explain": 0, "fix": 5}
MAX_GOOD = 600                  # characters of a rule's good example that go into a request
FIX_SYSTEM = ("You correct one place in code that a static checker flagged. Reply with the corrected lines in one fenced code "
              "block and nothing else of substance. Never add a comment that switches a check off.")
FIRST_USE = """First use of brevio-core ai in this repository. What each command sends, once you say yes to a request:
  explain  the file's extension, the rule's text and the flagged line; lines around it only if you pass --context N
  fix      the same, with 5 lines either side of the flagged one unless you pass --context 0, and it asks for replacement lines
Anything written in those lines goes with them: a comment, a name or an address in the code is sent as it stands. brevio-core
adds no file name or path of its own, and sends no other file.
Nothing at all is sent when a line to be sent looks like a secret (the scanner's R13.1 rule, and a stricter check: key
blocks, passwords in addresses, bearer tokens, long quoted tokens; it does not catch every secret), when the file is one that
holds secrets (.env*, .netrc, *.pem, *.key, id_* and similar, or a link to one), when the file has a second name (a hard link), or when the repository's brevio-core.toml
denies the path. Each request is printed in full first (the exact bytes, their size, the header names and any proxy your
environment sends it through) and asks "Send it?"; --yes answers that for you, at a terminal too, but never this agreement
and never --apply. It goes to the address you connected, through that proxy if there is one; nothing goes to Brevio.
"""
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u2028\u2029]|\r(?!\n)")
_HIDDEN = frozenset({"Cf", "Cs", "Co", "Cn"})     # Unicode categories that are spelled out for the eye: format, surrogate, private use, unassigned
GUTTER = "| "                 # in front of every line a model wrote, so none of them can pass for a line of this program
_FENCE = re.compile(r"^(?P<fence>`{3,}|~{3,})[^\n`]*\n(?P<body>(?:.*?\n)?)(?P=fence)[ \t]*$", re.M | re.S)


class Stop(Exception):
    """The command ends here with this message and exit code."""

    def __init__(self, message: str, code: int = EXIT_NOT_SENT) -> None:
        super().__init__(message)
        self.code = code


class Prepared(NamedTuple):
    cfg: st.AiConfig
    connector: st.Connector
    root: str
    window: verify.Window | None        # ``summarize`` and ``learn`` are not about a window of one file
    prompt: str
    system: str

    @property
    def chars(self) -> int:
        return len(self.prompt) + len(self.system)


def terminal() -> Callable[[str], str] | None:
    """``input`` when a person is at a terminal, else ``None``: nothing then answers a question."""
    return input if core_platform.is_terminal(sys.stdin) and core_platform.is_terminal(sys.stdout) else None


def _yes(ask: Callable[[str], str], question: str) -> bool:
    try:
        return ask(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


ask_yes = _yes      # the name the commands that live in their own modules use


def _net() -> Any:
    mod = core_cli.set_module("pipeline", NET_MODULE)
    if mod is None:
        raise Stop("this build has no transport, so brevio-core ai cannot send", EXIT_USAGE)
    return mod


def _exec() -> Any:
    mod = core_cli.set_module("pipeline", EXEC_MODULE)
    if mod is None:
        raise Stop("this build has no command runner, so brevio-core ai cannot start a program", EXIT_USAGE)
    return mod


def transport(connector: st.Connector) -> Any:
    """The module that carries a request for ``connector``: the endpoint's or the command runner's, with the same functions
    (``problems``, ``destination``, ``preview``, ``key_of``, ``complete``, ``ERROR``)."""
    return _exec() if connector.kind == "command" else _net()


def extra_module(command: str) -> Any:
    """The module of ``summarize`` or ``learn``, loaded when one of them runs."""
    mod = core_cli.set_module("pipeline", MORE_MODULES[command])
    if mod is None:
        raise Stop(f"this build has no {command} command", EXIT_USAGE)
    return mod


def project_root(start: str = ".") -> str:
    """The folder with ``.git`` that holds ``start``, or ``start`` itself: the repository the consent and the narrowing are for."""
    here = cur = os.path.realpath(start)
    while not os.path.exists(os.path.join(cur, ".git")):
        parent = os.path.dirname(cur)
        if parent == cur:
            return here
        cur = parent
    return cur


def _load(env: Mapping[str, str]) -> st.AiConfig:
    try:
        return st.load(env=env)
    except st.AiConfigError as exc:
        raise Stop(str(exc), EXIT_USAGE) from exc


def _policy(root: str) -> st.RepoPolicy:
    try:
        return st.repo_policy(root)
    except st.AiConfigError as exc:     # a routing key in a repository file included
        raise Stop(str(exc), EXIT_USAGE) from exc


# ---- connect ------------------------------------------------------------------------------------------------------

def _program_connector(args: argparse.Namespace, env: Mapping[str, str]) -> st.Connector:
    """A command connector: the program is resolved to an absolute path now and stored, so a later change of ``PATH`` or of
    a repository's files does not change which program runs."""
    if args.url or args.key_env or args.local is not None or args.model:
        raise Stop("--program names a program: it takes no --url, --key-env, --local or --model", EXIT_USAGE)
    try:
        path = _exec().resolve(args.program, env)
    except _exec().ERROR as exc:
        raise Stop(str(exc), EXIT_USAGE) from exc
    return st.Connector(args.name, "command", argv=(path, *args.arg), env_pass=tuple(args.pass_env))


def _connector_of(args: argparse.Namespace, env: Mapping[str, str]) -> st.Connector:
    if args.program:
        return _program_connector(args, env)
    if args.arg or args.pass_env:
        raise Stop("--arg and --pass-env go with --program", EXIT_USAGE)
    if args.local is not None:
        if args.url or args.key_env:
            raise Stop("--local is an endpoint on this machine: it takes no --url and no --key-env", EXIT_USAGE)
        if not 1 <= args.local <= 65535:
            raise Stop("--local takes a port from 1 to 65535", EXIT_USAGE)
        path = "/v1/messages" if args.format == "messages" else "/v1/chat/completions"
        scheme = "http"        # plain http is for this machine only; the transport refuses it anywhere else
        return st.Connector(args.name, "endpoint", f"{scheme}://localhost:{args.local}{path}", args.format, args.model or "")
    return st.Connector(args.name, "endpoint", args.url or "", args.format, args.model or "", args.key_env or "")


def _not_off(env: Mapping[str, str]) -> None:
    """``connect``, ``explain`` and ``fix`` do nothing while ``BREVIO_AI`` says off; ``status`` still runs and says so."""
    if st.killed(env):
        raise Stop(f"off: {st.ENV_KILL} is set to turn every ai command off, so nothing was done (status still runs)")


def _write_settings(path: str, change: Callable[[st.AiConfig], st.AiConfig]) -> st.AiConfig:
    """Read the settings, change them and write them back under the lock on their folder, so a ``connect`` and a first-use
    agreement at the same moment do not undo each other (2026-11-12, claims check)."""
    try:
        with st.ledger_lock(st.ledger_path(path)):
            cfg = change(st.load(path))
            st.save(cfg)
    except st.AiConfigError as exc:
        raise Stop(str(exc), EXIT_USAGE) from exc
    except OSError as exc:
        raise Stop(f"the settings file could not be written: {exc.strerror or exc}", EXIT_USAGE) from exc
    return cfg


def _connect(args: argparse.Namespace, env: Mapping[str, str], _ask: Callable[[str], str] | None) -> int:
    _not_off(env)
    connector = _connector_of(args, env)
    found = list(dict.fromkeys(st.connector_problems(connector) + transport(connector).problems(connector, env)))
    if found:
        raise Stop("not connected: " + "; ".join(found), EXIT_USAGE)
    commands = tuple(c.strip() for c in (args.use or ",".join(st.DEFAULT_USE)).split(",") if c.strip())

    def change(cfg: st.AiConfig) -> st.AiConfig:
        kept = {c: n for c, n in cfg.use.items() if n != connector.name or c in commands}    # --use lists exactly what this one serves
        return st.with_connector(cfg._replace(use=kept), connector, commands)

    cfg = _write_settings(_load(env).path, change)
    print(f"connected {connector.name}: " + _described(connector))
    print(f"on for: {', '.join(commands) or 'nothing yet'}   settings: {cfg.path} (only you can read it)")
    loose = st.settings_problems(cfg.path)
    print(f"WARNING: explain and fix will send nothing until this is fixed (nor will summarize or learn): {'; '.join(loose)}" if loose
          else "Nothing is sent until you run one of the `brevio-core ai` commands, read the request it prints and answer y.")
    if connector.kind == "command":
        print("A program you named receives the request and may itself send it elsewhere: brevio-core cannot see what it does.")
    return EXIT_OK


def _described(c: st.Connector) -> str:
    """One line about a connector, for ``connect`` and ``status``."""
    if c.kind == "command":
        listed = f", passes {', '.join(c.env_pass)}" if c.env_pass else ""
        return f"runs {c.argv[0]} with {len(c.argv) - 1} argument(s) (a program you named; no model name is kept){listed}"
    return f"{_net().destination(c)}, {c.format} shape, model {c.model}"


# ---- status -------------------------------------------------------------------------------------------------------

def _status_lines(cfg: st.AiConfig, env: Mapping[str, str], root: str) -> list[str]:
    _net()      # status names an address as a request would, so it loads the transport whether or not an endpoint is connected
    out = [f"settings: {cfg.path}" + ("  WARNING: other users can read it; run chmod 600" if st.loose_permissions(cfg.path) else "")]
    loose = st.settings_problems(cfg.path)
    out += [f"WARNING: explain and fix send nothing until this is fixed (nor do summarize and learn): {'; '.join(loose)}"] if loose else []
    if st.killed(env):
        out.append(f"{st.ENV_KILL} is set to off: ai is off, and connect, explain and fix do nothing, nor do summarize and "
                   "learn, until it is not")
    out.append("connected: " + ("" if cfg.connectors else "nothing; see `brevio-core ai connect --help`"))
    for name, c in sorted(cfg.connectors.items()):
        key = f", key from {c.key_env} ({'set' if env.get(c.key_env, '').strip() else 'NOT set'})" if c.key_env else ", no key (this machine)"
        out.append(f"  {name}: {_described(c)}" + ("" if c.kind == "command" else key))
    out += [f"  {command}: " + (f"on, uses {cfg.use[command]}" if command in cfg.use else "off") for command in st.COMMANDS]
    out.append(_usage_line(cfg))
    lim = cfg.limits
    told = " (an endpoint's setting; a program is not told it)" if any(c.kind == "command" for c in cfg.connectors.values()) else ""
    out.append(f"limits: {lim.max_calls_per_day} calls a day, {lim.max_input_chars} characters "
               f"in, {lim.max_output_tokens} tokens out{told}, {lim.timeout_s} s")
    key = st.repo_key(root)
    agreed = [c for c in st.COMMANDS if st.consented(cfg, key, c)]
    out.append(f"this repository: agreed to {', '.join(agreed) or 'nothing yet'}; " + _narrowing(root))
    return out


def _usage_line(cfg: st.AiConfig) -> str:
    try:
        used = st.usage_on(st.ledger_path(cfg.path))
    except st.AiConfigError as exc:
        return f"today: {exc}"
    tokens = f", {used.input_tokens} tokens in and {used.output_tokens} out as the endpoint reported" if used.input_tokens or used.output_tokens else ""
    return f"today: {used.calls} of {cfg.limits.max_calls_per_day} calls, {used.chars} characters sent{tokens}"


def _narrowing(root: str) -> str:
    try:
        policy = st.repo_policy(root)
    except st.AiConfigError as exc:
        return str(exc)
    parts = [] if policy.enabled else ["ai is turned off here"]
    parts += [f"allows only {', '.join(policy.allow) or 'nothing'}"] if policy.allow is not None else []
    parts += [f"{len(policy.deny_paths)} path pattern(s) never sent"] if policy.deny_paths else []
    return "brevio-core.toml " + ("narrows it: " + "; ".join(parts) if parts else "narrows nothing")


def _status(_args: argparse.Namespace, env: Mapping[str, str], _ask: Callable[[str], str] | None) -> int:
    print(safe("\n".join(_status_lines(_load(env), env, project_root()))))
    return EXIT_OK


# ---- what is sent ---------------------------------------------------------------------------------------------------

def secret_lines(rows: list[tuple[int, str]]) -> list[int]:
    """The line numbers among ``rows`` (``(number, text)``) that the stricter check flags: the scanner's own and more."""
    return verify.secret_lines(rows)


def fence_for(lines: list[str]) -> str:
    longest = max((len(m) for x in lines for m in re.findall(r"`+", x)), default=0)
    return "`" * max(3, longest + 1)


def _rule_text(rule: str) -> str:
    detail = DETAILS.get(rule)
    out = [f"Rule {rule}: {PR_RULES_122[rule]}"]
    if detail and detail.why:
        out.append(f"Why it matters: {detail.why.strip()}")
    if detail and detail.good.strip():
        out.append(f"A better way to write it:\n{detail.good.strip()[:MAX_GOOD]}")
    return "\n".join(out)


def build_prompt(command: str, window: verify.Window) -> tuple[str, str]:
    """``(prompt, system line)``: the rule's text, which line of the window is flagged and the window. No path, no name."""
    ext = posixpath.splitext(window.path)[1] or "no extension"
    n, flagged = len(window.lines), window.line - window.start + 1
    ask = ("Explain in a few plain sentences why that line breaks the rule and what to write instead."
           if command == "explain" else
           f"Reply with the corrected version of all {n} line(s), in one fenced code block with the same indentation style. "
           "Change only what the rule needs. Do not add an ignore comment or any comment that switches a check off: "
           "it would be refused.")
    fence = fence_for(window.lines)
    head = (f"A static checker flagged one line of a {ext} file.\n{_rule_text(window.rule)}\n"
            f"Line {flagged} of the {n} line(s) below is the flagged one. {ask}\n")
    system = FIX_SYSTEM if command == "fix" else words.EXPLAIN_SYSTEM
    return head + fence + "\n" + "\n".join(window.lines) + "\n" + fence + "\n", system


def first_finding() -> str:
    """The first finding of a fresh scan: of the uncommitted changes when there is a commit, else of every file."""
    try:
        lines = core_cli.select_lines([], "HEAD" if core_cli.has_head() else None)
        shown = core_cli.triage(core_cli.scan(lines, core_cli.project(lines)), "new", True)[0]
    except core_cli.UsageError as exc:
        raise Stop(str(exc), EXIT_USAGE) from exc
    if not shown:
        raise Stop("the scan finds nothing, so there is nothing to ask about", EXIT_OK)
    return shown[0]


def _locate(args: argparse.Namespace, root: str, policy: st.RepoPolicy) -> core_patch.Located:
    if bool(args.finding) == bool(args.last):
        raise Stop("name one finding (as scan prints it) or pass --last", EXIT_USAGE)
    try:
        found = core_patch.locate(args.finding or first_finding(), args.context, root, base=os.path.realpath("."), deny=policy.denies,
                                 single_name=True)
    except core_patch.Denied as exc:        # the name or the place a link leads to
        raise Stop(str(exc)) from exc
    except core_patch.PatchError as exc:
        raise Stop(str(exc), EXIT_USAGE) from exc
    if found.rule not in PR_RULES_122:
        raise Stop(f"{found.rule} is not a rule of the catalogue: there is no rule text to send", EXIT_USAGE)
    return found


def _refuse_secret(p: Prepared) -> None:
    """One line of the window that looks like a secret refuses the whole call; the line number is named, the line never."""
    if p.window is None:
        return      # ``summarize`` and ``learn`` check their own lines
    hit = secret_lines([(p.window.start + i, text) for i, text in enumerate(p.window.lines)])
    if hit:
        many = len(hit) > 1
        raise Stop(f"not sent: line{'s' if many else ''} {', '.join(map(str, hit))} of {p.window.path} look{'' if many else 's'} "
                   "like a secret (the stricter check ai makes: the scanner's R13.1 rule, key blocks, passwords in addresses, "
                   "bearer tokens, long quoted tokens); the line is not shown here. Narrow it with --context 0 or keep that "
                   "file out of ai")


def _refuse_unsafe(command: str, p: Prepared) -> None:
    """Stop before anything is shown or sent: a secret in the window, a protected file or a limit."""
    _refuse_secret(p)
    why = (core_patch.blocked(p.window.path, p.root) or core_patch.link_reason(p.window.path, p.root)) if command == "fix" and p.window else ""
    if why and p.window:
        raise Stop(f"{p.window.path} is not for ai to change ({why}); nothing was sent")
    refuse_limits(p)


def refuse_limits(p: Prepared) -> None:
    """Stop before anything is sent when today's count or the size of the request is past a limit."""
    try:
        used = st.usage_on(st.ledger_path(p.cfg.path))
    except st.AiConfigError as exc:
        raise Stop(str(exc), EXIT_USAGE) from exc
    over = st.day_problems(p.cfg.limits, used, p.chars)
    if over:
        raise Stop("not sent: " + "; ".join(over) + " (limits are in the settings file; see `brevio-core ai status`)")


def _refuse_loose_settings(path: str) -> None:
    """Nothing is sent while another user could have written the settings file (the address and the key variable are in it)."""
    loose = st.settings_problems(path)
    if loose:
        raise Stop("not sent: " + "; ".join(loose))


def session(command: str, env: Mapping[str, str]) -> tuple[st.AiConfig, st.Connector, str, st.RepoPolicy]:
    """``(settings, connector, repository root, what the repository narrows)`` for ``command``, or a ``Stop``: the kill
    switch, a command that is off, loose settings and the repository's own refusal come first. Sends nothing."""
    _not_off(env)
    cfg = _load(env)
    connector = st.use_for(cfg, command)
    if connector is None:
        raise Stop(f"{command} is off: no connector is turned on for it (see `brevio-core ai connect --help`)")
    _refuse_loose_settings(cfg.path)
    root = project_root()
    policy = _policy(root)
    refusal = policy.refusal(command)
    if refusal:
        raise Stop(refusal)
    return cfg, connector, root, policy


def check_connector(connector: st.Connector, env: Mapping[str, str]) -> None:
    """Stop when the connector cannot carry a request now (an address that is not allowed, a key that is not set, a program
    that is gone, a CI variable)."""
    problems = transport(connector).problems(connector, env)
    if problems:
        raise Stop(f"cannot send to {connector.name}: " + "; ".join(problems))


def _prepare(command: str, args: argparse.Namespace, env: Mapping[str, str]) -> Prepared:
    """Every check that comes before a request is shown. Raises ``Stop`` with the reason; sends nothing."""
    cfg, connector, root, policy = session(command, env)
    found = _locate(args, root, policy)
    check_connector(connector, env)
    window = verify.window_of(found, args.context)
    prompt, system = build_prompt(command, window)
    prepared = Prepared(cfg, connector, root, window, prompt, system)
    _refuse_unsafe(command, prepared)
    return prepared


# ---- asking and sending ----------------------------------------------------------------------------------------------

def _show(command: str, p: Prepared, env: Mapping[str, str]) -> None:
    """Print the request as it will go: the address, the proxy if any, the header names, the size in bytes and the exact body.
    ``preview`` and the send build the bytes from one function, so this is what leaves; control and format characters are
    spelled out here for the eye only (2026-11-12, claims check)."""
    net = transport(p.connector)
    print(f"brevio-core ai {command} would send this request to {net.destination(p.connector)}:\n")
    keyed = bool(net.key_of(p.connector, env))     # a variable that is empty sends no key header, and the list says so
    shown = (net.preview(p.connector, p.prompt, p.cfg.limits.max_output_tokens, p.system, keyed, env=env)
             if p.connector.kind == "command" else net.preview(p.connector, p.prompt, p.cfg.limits.max_output_tokens, p.system, keyed))
    print(safe(shown))
    back = ("What it writes back is read up to a size cap." if p.connector.kind == "command"
            else f"At most {p.cfg.limits.max_output_tokens} tokens come back.")
    print(f"That is the whole request, byte for byte: one call, no retry, no other address, nothing to Brevio. {back} "
          "brevio-core adds no file name or path to it; anything written in the lines it holds goes with them.\n")


def extra_numbers(command: str) -> tuple[int, int]:
    """The two numbers the first-use text of ``learn`` quotes, read from the command's own constants."""
    return extra_module("learn").numbers() if command == "learn" else (0, 0)


def _consent(command: str, p: Prepared, ask: Callable[[str], str] | None) -> None:
    """A repository's first use of ``command`` needs the person's yes at a terminal; ``--yes`` does not stand in for it."""
    key = st.repo_key(p.root)       # the root commit is read once per command
    if st.consented(p.cfg, key, command):
        return
    if ask is None:
        raise Stop(f"first use of `ai {command}` in this repository: it needs your yes at a terminal, and --yes does not stand in "
                   "for it. Run it once by hand")
    print(FIRST_USE if command in ("explain", "fix") and p.connector.kind == "endpoint"
          else words.first_use(command, p.connector.kind, *extra_numbers(command)))
    if not _yes(ask, f"Agree to ai {command} in this repository? [y/N] "):
        raise Stop("not agreed; nothing was sent")
    _write_settings(p.cfg.path, lambda cfg: st.with_consent(cfg, key, (command,)))


def _confirm(yes: bool, ask: Callable[[str], str] | None) -> None:
    """``Send it?``; ``--yes`` answers it, at a terminal as well."""
    if yes:
        return
    if ask is None:
        raise Stop("not sent: there is no terminal to ask; pass --yes to send it")
    if not _yes(ask, "Send it? [y/N] "):
        raise Stop("not sent")


def _send(p: Prepared, env: Mapping[str, str]) -> str:
    """The one request. The settings are checked again and the count is written first, in one locked step with the limits, so
    a call that fails after it left still counts and two commands at once cannot both pass a limit of one."""
    net, ledger = transport(p.connector), st.ledger_path(p.cfg.path)
    _refuse_loose_settings(p.cfg.path)
    try:
        st.reserve_call(ledger, p.cfg.limits, p.chars)
    except st.LimitError as exc:
        raise Stop(f"not sent: {exc} (limits are in the settings file; see `brevio-core ai status`)") from exc
    except (st.AiConfigError, OSError) as exc:
        raise Stop(f"nothing was sent: the count could not be written ({getattr(exc, 'strerror', None) or exc})", EXIT_USAGE) from exc
    try:
        text, usage = net.complete(p.connector, p.prompt, p.cfg.limits.max_output_tokens, env=env,
                                   timeout=p.cfg.limits.timeout_s, system=p.system)
    except net.ERROR as exc:
        raise Stop(str(exc), EXIT_FAILED) from exc
    if usage:
        try:
            st.record_tokens(ledger, usage["input_tokens"], usage["output_tokens"])
        except (st.AiConfigError, OSError) as exc:
            print(f"brevio-core ai: the token count could not be written ({getattr(exc, 'strerror', None) or exc})", file=sys.stderr)
    return str(text)


def _spelled(ch: str) -> str:
    n = ord(ch)
    return f"\\x{n:02x}" if n < 0x100 else f"\\u{n:04x}" if n < 0x10000 else f"\\U{n:08x}"


def safe(text: str) -> str:
    """``text`` with each control character spelled out (as ``\\x1b``), and each line or paragraph separator and each format
    character (Unicode category Cf: bidirectional marks, zero-width joiners, tag characters) as ``\\u2028`` and the like: what
    a model wrote must not move the cursor, clear the screen, reorder what follows or break a line the program did not. A
    lone surrogate (it cannot be printed), a private-use and an unassigned character are spelled out too (2026-11-12)."""
    spelled = _CONTROL.sub(lambda m: _spelled(m.group(0)), text)
    return spelled if spelled.isascii() else "".join(_spelled(c) if unicodedata.category(c) in _HIDDEN else c for c in spelled)


def _opening(p: Prepared, what: str) -> str:
    who = (f"written by the program of {safe(p.connector.name)}" if p.connector.kind == "command"
           else f"written by {safe(p.connector.name)}, model {safe(p.connector.model)}")
    return f"{what} ({who}; brevio-core did not write it):"


def _closing(what: str, shape: str) -> str:
    return f"end of the {what.lower()}: {shape}; whatever follows is brevio-core's own"


def labelled(p: Prepared, what: str, text: str) -> str:
    """What a model wrote, between an opening line that says so and a closing line it cannot imitate: every one of its lines
    is printed behind a gutter, so no line of it can read as one of this program's own."""
    lines = [GUTTER + line for line in safe(text).split("\n")]
    whose = "the program's" if p.connector.kind == "command" else "the model's"      # a program wrote it, not a model brevio-core knows
    return "\n".join([_opening(p, what), *lines, _closing(what, f'every line above starts with "{GUTTER}" and is {whose}')])


def labelled_diff(p: Prepared, what: str, diff: str) -> str:
    """A unified diff of a verified change in the same frame. It has no gutter, so it can be piped to ``patch``: each of its
    lines starts with a space, ``+``, ``-``, ``@`` or a backslash, so none can be the closing line."""
    return "\n".join([_opening(p, what), safe(diff).rstrip("\n"), _closing(what, "a unified diff, no line of which starts with a letter")])


def extract_block(text: str) -> tuple[list[str] | None, str]:
    """``(lines, "")`` of the one fenced block in ``text``, or ``(None, why not)``."""
    blocks = list(_FENCE.finditer(text))
    if len(blocks) != 1:
        return None, f"the reply must hold exactly one fenced block of lines; it holds {len(blocks)}"
    body = blocks[0]["body"]
    return [x.rstrip("\r") for x in (body[:-1].split("\n") if body else [""])], ""


def _refused(p: Prepared, reasons: list[str], text: str, show: bool) -> int:
    """A refused reply shows its reasons and no change. Its text only with ``--show-refused``, framed as refused model output."""
    print("refused by the rules; nothing was written and it is not retried:\n"
          + "\n".join("- " + safe(r).replace("\n", "\n  ") for r in reasons))
    if show:
        print(labelled(p, "Refused model output", text))
    else:
        print("The reply is not shown. Add --show-refused to see it (it is the reply's text, spelled out and framed).")
    return EXIT_FAILED


def _write_it(p: Prepared, verdict: verify.Verdict, ask: Callable[[str], str] | None) -> int:
    if ask is None or not _yes(ask, "Write this change to the working tree? It is not committed. [y/N] "):
        print("not written")
        return EXIT_NOT_SENT
    try:
        written = core_patch.write_changes(verdict.changes, p.root)
    except core_patch.PatchError as exc:
        raise Stop(str(exc), EXIT_FAILED) from exc
    print(f"wrote {', '.join(written)} (nothing is committed)")
    return EXIT_OK


def _fix_outcome(p: Prepared, text: str, apply: bool, ask: Callable[[str], str] | None, show: bool = False) -> int:
    window = p.window
    if window is None:
        raise Stop("a fix is about the window of one finding", EXIT_USAGE)
    reply, why = extract_block(text)
    if reply is None:
        return _refused(p, [why], text, show)
    verdict = verify.check(p.root, window, reply)
    if not verdict.ok:
        return _refused(p, verdict.reasons, text, show)
    print(labelled_diff(p, "Proposed change", verdict.diff))
    print(f"verified: [{window.rule}] no longer flags the line, no finding is new, no check is switched off, only "
          f"{window.path} changes.")
    if not apply:
        print("Nothing was written. Add --apply to write it to the working tree (after a second yes), or apply the diff yourself.")
        return EXIT_OK
    return _write_it(p, verdict, ask)


def exchange(command: str, p: Prepared, env: Mapping[str, str], ask: Callable[[str], str] | None, dry_run: bool = False,
             yes: bool = False) -> str | None:
    """The steps every call shares: print the request, stop there for ``--dry-run`` (``None``), a program is started only for
    a person at a terminal, the first-use agreement, ``Send it?`` and the one request; returns the reply's text."""
    _show(command, p, env)
    if dry_run:
        print("--dry-run: nothing was sent.")
        return None
    if p.connector.kind == "command" and ask is None:
        raise Stop("not started: a program you named is started only for a person at a terminal, and --yes does not stand in "
                   "for that")
    _consent(command, p, ask)
    _confirm(yes, ask)
    return _send(p, env)


def _ask_model(command: str, args: argparse.Namespace, env: Mapping[str, str], ask: Callable[[str], str] | None) -> int:
    _not_off(env)
    args.context = DEFAULT_CONTEXT[command] if args.context is None else args.context
    apply = bool(getattr(args, "apply", False))
    if apply and ask is None and not args.dry_run:
        raise Stop("not sent: --apply writes a file, so it needs a terminal to ask at, and --yes does not stand in for that")
    p = _prepare(command, args, env)
    text = exchange(command, p, env, ask, args.dry_run, args.yes and not apply)     # --yes is never honoured for --apply
    if text is None:
        return EXIT_OK
    if command == "explain":
        print(labelled(p, "Answer", text))
        return EXIT_OK
    return _fix_outcome(p, text, apply, ask, bool(getattr(args, "show_refused", False)))


def _explain(args: argparse.Namespace, env: Mapping[str, str], ask: Callable[[str], str] | None) -> int:
    return _ask_model("explain", args, env, ask)


def _fix(args: argparse.Namespace, env: Mapping[str, str], ask: Callable[[str], str] | None) -> int:
    return _ask_model("fix", args, env, ask)


# ---- the command line ------------------------------------------------------------------------------------------------

def _add_ask_arguments(p: argparse.ArgumentParser, what: str) -> None:
    p.add_argument("finding", nargs="?", default="", help="one finding as scan prints it: path:line: [Rn.n] message")
    p.add_argument("--last", action="store_true", help="use the first finding of a fresh scan instead")
    p.add_argument("--context", type=int, default=None, metavar="N",
                   help=f"lines either side of the flagged one that are sent (default {DEFAULT_CONTEXT[what]}; 0 sends the line alone)")
    p.add_argument("--dry-run", action="store_true", help="print the request and stop before anything goes out")
    p.add_argument("--yes", action="store_true",
                   help="answer the Send it? question for you, at a terminal too; the request is still printed. It never "
                        "answers the first-use agreement (a yes at a terminal) and never --apply")


def _add_more_arguments(sub: Any) -> None:
    """``summarize`` and ``learn`` (previews): the flags only; the commands live in their own modules."""
    summ = sub.add_parser("summarize", help="(preview) one call: what kinds of finding a change has, from rule IDs, titles and "
                                            "counts only")
    summ.add_argument("--diff", required=True, metavar="BASE", help="the base to scan the changes against, as `scan --diff` takes it")
    summ.add_argument("--format", choices=("text", "markdown"), default="text", help="how the model-written answer is framed")
    summ.add_argument("--dry-run", action="store_true", help="print the request and stop before anything goes out")
    summ.add_argument("--yes", action="store_true", help="answer the Send it? question for you, at a terminal too; the request "
                                                         "is still printed. It does not answer the first-use agreement")
    learn = sub.add_parser("learn", help="(preview) a model's patterns for a guard from a fix, kept only if the checks of "
                                         "`brevio-core learn` pass; it sends and writes only at a terminal")
    where = learn.add_mutually_exclusive_group()
    where.add_argument("--staged", action="store_true", help="the staged fix (git diff --cached)")
    where.add_argument("--worktree", action="store_true", help="the uncommitted fix (the working tree against HEAD)")
    learn.add_argument("--diff", default="HEAD~1", metavar="REF", help="a commit or range to learn from (default HEAD~1)")
    learn.add_argument("--dry-run", action="store_true", help="print the request and stop before anything goes out")


def parser(prog: str = "brevio-core ai") -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=prog, description=HELP[0].upper() + HELP[1:] + ". Off until you connect a model or a "
                                 "program. One call for one thing, after you have read the request and said yes. Nothing "
                                 "goes to Brevio.",
                                 epilog="exit 0: done, 1: the call failed or the fix was refused, 2: usage or settings, "
                                        "3: nothing was sent or written")
    sub = ap.add_subparsers(dest="action", required=True)
    con = sub.add_parser("connect", help="write your own settings file: which endpoint, which model, which commands")
    con.add_argument("--name", default="default", help="a name for this connection (default: default)")
    con.add_argument("--url", help="the endpoint's address; https, or http for this machine only")
    con.add_argument("--format", choices=st.FORMATS, default="chat", help="the shape of the request (default chat)")
    con.add_argument("--model", help="the model name the endpoint expects")
    con.add_argument("--key-env", help="the NAME of the environment variable that holds your key (the key is never stored)")
    con.add_argument("--local", type=int, metavar="PORT", help="an endpoint on this machine at PORT: no key, nothing leaves it")
    con.add_argument("--program", metavar="PROGRAM", help="(preview) a program you name instead of an endpoint: its absolute path "
                     "is found now and stored. It is started once per call with the request on its standard input; it may "
                     "send the request elsewhere, which brevio-core cannot see. Use --arg and --pass-env with it")
    con.add_argument("--arg", action="append", default=[], metavar="TEXT",
                     help="an argument for the program, written --arg=TEXT, repeated for more; it is kept in your settings "
                          "file, which a repository's brevio-core.toml cannot set")
    con.add_argument("--pass-env", action="append", default=[], metavar="NAME",
                     help="the NAME of an environment variable the program is started with, besides the few it needs to "
                          "start; repeated for more")
    con.add_argument("--use", default=None, metavar="LIST",
                     help=f"the commands this connection serves, comma separated (default {','.join(st.DEFAULT_USE)}; the "
                          f"others are {', '.join(c for c in st.COMMANDS if c not in st.DEFAULT_USE)}, previews); "
                          "one it served before and you leave out is turned off")
    sub.add_parser("status", help="what is connected, which commands are on, today's calls, the limits and the consent here")
    _add_ask_arguments(sub.add_parser("explain", help="a model's explanation of one finding"), "explain")
    fix = sub.add_parser("fix", help="a model's fix for one finding, checked by the rules before it is shown")
    _add_ask_arguments(fix, "fix")
    fix.add_argument("--show-refused", action="store_true", help="when the rules refuse the reply, also show its text, spelled out "
                                                                 "and framed as refused model output (without it only the reasons)")
    fix.add_argument("--apply", action="store_true", help="write the verified change to the working tree after a second yes "
                                                          "at a terminal; it is never committed")
    _add_more_arguments(sub)
    return ap


def _summarize(args: argparse.Namespace, env: Mapping[str, str], ask: Callable[[str], str] | None) -> int:
    return int(extra_module("summarize").run(args, env, ask))


def _learn(args: argparse.Namespace, env: Mapping[str, str], ask: Callable[[str], str] | None) -> int:
    return int(extra_module("learn").run(args, env, ask))


ACTIONS: dict[str, Callable[[argparse.Namespace, Mapping[str, str], Callable[[str], str] | None], int]] = {
    "connect": _connect, "status": _status, "explain": _explain, "fix": _fix, "summarize": _summarize, "learn": _learn}


def main(argv: list[str] | None = None, prog: str = "brevio-core ai", env: Mapping[str, str] | None = None,
         ask: Callable[[str], str] | None = None) -> int:
    args = parser(prog).parse_args(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):     # a terminal that cannot show a character gets an escape, not a crash after the call left
            stream.reconfigure(errors="backslashreplace")
    try:
        return ACTIONS[args.action](args, os.environ if env is None else env, ask or terminal())
    except Stop as stop:
        print(f"brevio-core ai: {safe(str(stop))}", file=sys.stderr)       # a message may quote a key a repository's file made up
        return stop.code


if __name__ == "__main__":
    raise SystemExit(main())
