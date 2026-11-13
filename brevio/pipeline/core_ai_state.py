"""The user-level state of ``brevio-core ai``: the connectors, the limits, the consent and the ledger (the 0.7 set,
owner decision D24, 2026-11-11).

Everything here is local and none of it touches the network. The file is ``ai.toml`` in the user's config folder
(``$XDG_CONFIG_HOME/brevio-core/``, default ``~/.config/brevio-core/``; ``BREVIO_AI_CONFIG`` names another file, for
tests), written with mode 0600 and never read from a repository, so a pull request cannot point the tool anywhere::

    [connector.work]              # kind, url, format ("chat" or "messages"), model, key_env, header
    kind = "endpoint"
    url = "..."
    key_env = "NAME_OF_A_VARIABLE"  # the name only: the key is never stored

    [connector.mine]              # a program you name (2026-11-12, improvement plan): kind, argv, env_pass
    kind = "command"
    argv = ["/absolute/path/to/program", "first-argument"]   # the path is resolved when you connect
    env_pass = ["NAME_OF_A_VARIABLE"]   # names of variables the program is started with, besides the few it needs to start

    [ai.use]                    # a command with no line here is off
    explain = "work"

    [ai.limits]                   # max_calls_per_day, max_input_chars, max_output_tokens, timeout_s

    [repos."<sha256 of the repository's real path and its root commit>"]
    allow = ["explain"]           # the commands the user agreed to in that repository

A repository's ``brevio-core.toml`` may add an ``[ai]`` table that only narrows (``enabled = false``, ``allow``,
``deny_paths``); a url, key, model or any other routing key there is refused ("routing is user-level only"), and the file
is read with the same unknown-key check the gate applies to it, so a key or table the tool does not know is refused too.
The ledger (calls, characters and, when the endpoint reports them, tokens, per local calendar day) sits beside the config
file, is updated under a file lock where the system has one, and is written atomically. ``BREVIO_AI=off`` turns every
``ai`` command that sends off. The settings file is checked before a call (``settings_problems``). Standard library only;
this module never imports the transport.
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from collections.abc import Generator, Mapping
from typing import Any, NamedTuple

from brevio.pipeline import core_config
from brevio.pipeline.rule_host import GIT

try:
    import fcntl as _fcntl
except ImportError:         # a system without flock (Windows): the ledger is then not locked
    _fcntl = None

ENV_KILL, ENV_CONFIG = "BREVIO_AI", "BREVIO_AI_CONFIG"
CONFIG_NAME, LEDGER_NAME = "ai.toml", "ai-ledger.json"
COMMANDS = ("explain", "fix", "summarize", "learn")     # the commands that can be turned on; one with no ``[ai.use]`` line is off
DEFAULT_USE = ("explain", "fix")      # what ``connect`` turns on unless told otherwise: the two later commands are previews, so opt-in
KINDS = ("endpoint", "command")
MAX_ARGV, MAX_ENV_PASS, MAX_ARG_CHARS = 32, 16, 1024
FORMATS = ("chat", "messages")
KEEP_DAYS = 31
_OFF = frozenset({"off", "0", "false", "no", "disabled"})
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_VAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
# What a key looks like when it is typed where a variable name belongs (2026-11-12, claims check): well-known key prefixes, a
# long word with digits and no underscore, or a long mixed-case word with digits.
_KEY_PREFIX = re.compile(r"^(?:sk_|pk_|rk_|hf_|gsk_|r8_|npm_|shpat_|whsec_|ghp_|gho_|ghs_|ghu_|github_pat_|glpat|xox|AKIA|ASIA|AIza|ya29|eyJ)")
_KEY_SHAPED = re.compile(r"[A-Za-z0-9_+/=-]{24,}")
_AUTHORITY = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://([^/?#]*)")
_HEADER = re.compile(r"^[A-Za-z0-9-]{1,64}: ?[^\x00-\x1f\x7f]{0,512}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_NUMBER_LINE = re.compile(r'^(\s*[A-Za-z0-9_"-]+\s*=\s*)(-?\d+)(\s*(?:#.*)?)$', re.M)
# Keys of a repository's [ai] table that name where a request goes or how it is made: user-level only.
ROUTING_KEYS = frozenset({"url", "key", "key_env", "model", "format", "kind", "header", "argv", "connector", "use", "limits",
                          "endpoint", "token", "api_key", "env", "env_pass"})
_POLICY_KEYS = ("enabled", "allow", "deny_paths")
ROUTING_MESSAGE = "routing is user-level only"


class AiConfigError(ValueError):
    """The user-level file, a repository's [ai] table or the ledger cannot be read or does not say what it means."""


class RoutingError(AiConfigError):
    """A repository's ``brevio-core.toml`` names where ``ai`` would send: only the user's own file may."""


class Connector(NamedTuple):
    name: str
    kind: str = "endpoint"
    url: str = ""
    format: str = "chat"
    model: str = ""
    key_env: str = ""       # the NAME of the variable that holds the key; empty for an endpoint on this machine
    header: str = ""        # one more header, "Name: value"
    argv: tuple[str, ...] = ()          # a command connector: the program (an absolute path) and its arguments
    env_pass: tuple[str, ...] = ()      # a command connector: the NAMES of the variables the program is started with


class Limits(NamedTuple):
    max_calls_per_day: int = 20     # the one kind of limit per day: a run makes one call for one finding
    max_input_chars: int = 6000
    max_output_tokens: int = 600
    timeout_s: int = 60

    @property
    def max_calls_per_run(self) -> int:
        """Gone (2026-11-12, claims check): a run makes one call. Read only for the command module until it stops asking."""
        return 1


LIMIT_BOUNDS = {"max_calls_per_day": (1, 1000), "max_calls_per_run": (1, 10), "max_input_chars": (200, 100_000),
                "max_output_tokens": (16, 8000), "timeout_s": (1, 300)}
LEGACY_LIMITS = ("max_calls_per_run",)     # an old settings file may still hold it: it is read and ignored


class AiConfig(NamedTuple):
    path: str
    connectors: dict[str, Connector] = {}
    use: dict[str, str] = {}                    # command -> connector name
    limits: Limits = Limits()
    repos: dict[str, tuple[str, ...]] = {}      # sha256 of a repository's real path and root commit -> the commands agreed to


class Usage(NamedTuple):
    calls: int = 0
    chars: int = 0
    input_tokens: int = 0       # counted only when the endpoint reports them
    output_tokens: int = 0


# --- where things are ------------------------------------------------------------------------------------------------

def killed(env: Mapping[str, str] | None = None) -> bool:
    """``BREVIO_AI=off`` (or 0, false, no, disabled): every ``ai`` command is off."""
    env = os.environ if env is None else env
    return env.get(ENV_KILL, "").strip().lower() in _OFF


def config_path(env: Mapping[str, str] | None = None) -> str:
    """The user-level file: ``BREVIO_AI_CONFIG``, else under an absolute ``XDG_CONFIG_HOME``, else ``~/.config``."""
    env = os.environ if env is None else env
    named = env.get(ENV_CONFIG, "").strip()
    if named:
        return os.path.abspath(os.path.expanduser(named))
    xdg = env.get("XDG_CONFIG_HOME", "").strip()
    base = xdg if os.path.isabs(xdg) else os.path.join(os.path.expanduser("~"), ".config")    # a relative value is invalid
    return os.path.join(base, "brevio-core", CONFIG_NAME)


def ledger_path(cfg_path: str) -> str:
    return os.path.join(os.path.dirname(cfg_path), LEDGER_NAME)


def _rev(root: str, back: int) -> str:
    """The commit ``back`` first-parent steps behind HEAD of the repository at ``root``, or ``""`` (read-only ``rev-parse``)."""
    try:
        r = subprocess.run([*GIT, "-C", root, "rev-parse", "--verify", "-q", f"HEAD~{back}"],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def repo_identity(root: str) -> str:
    """The root commit of the repository at ``root`` (the first-parent chain's oldest commit), read with ``git rev-parse``
    only; ``""`` when there is no commit yet or no git. A search that doubles and then halves, so a long history is a few
    dozen calls. A shallow clone has a younger root: it is asked again, which is safe."""
    if not _rev(root, 0):
        return ""
    low, high = 0, 1
    while high <= 1 << 22 and _rev(root, high):
        low, high = high, high * 2
    while high - low > 1:
        mid = (low + high) // 2
        low, high = (mid, high) if _rev(root, mid) else (low, mid)
    return _rev(root, low)


def repo_key(root: str, identity: str | None = None) -> str:
    """What the consent is filed under: the sha256 of the repository's real path and its root commit (``identity``, read
    from git by default), so no path is stored and a different repository placed at the same path is asked again. With no
    commit yet it is the path alone (2026-11-12, claims check)."""
    real = os.path.normcase(os.path.realpath(os.path.abspath(root)))
    ident = repo_identity(real) if identity is None else identity
    text = real if not ident else f"{real}\0{ident}"
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def loose_permissions(path: str) -> bool:
    """The file exists and group or others can read or write it (it should be 0600)."""
    try:
        return bool(os.stat(path).st_mode & 0o077)
    except OSError:
        return False


def settings_problems(path: str) -> list[str]:
    """Why the settings file at ``path`` cannot be trusted to be yours alone, in plain words (empty when it can, or when there
    is no file yet): it is not owned by you, group or others can read or write it, or its folder can be written to by group
    or others (or belongs to someone else), so another user could swap it for one that points elsewhere. Links are followed,
    and the folder of the link is checked as well. POSIX only: on a system without user ids it finds nothing."""
    if not hasattr(os, "getuid"):
        return []
    real = os.path.realpath(path)
    try:
        info = os.stat(real)
    except FileNotFoundError:
        return []
    except OSError as exc:
        return [f"{path} cannot be inspected ({exc.strerror or type(exc).__name__})"]
    me = os.getuid()
    out = ["the settings file is not owned by you"] if info.st_uid != me else []
    if info.st_mode & 0o077:
        out.append("group or others can read or write the settings file (run chmod 600 on it)")
    for folder in dict.fromkeys((os.path.dirname(real), os.path.dirname(os.path.abspath(path)))):
        try:
            fi = os.stat(folder)
        except OSError:
            continue
        if fi.st_mode & 0o022:
            out.append(f"the folder {folder} can be written to by group or others (run chmod go-w on it)")
        elif fi.st_uid not in (me, 0):
            out.append(f"the folder {folder} belongs to another user")
    return out


# --- the connectors ------------------------------------------------------------------------------------------------------

def _authority(url: str) -> str:
    """The part of ``url`` between ``scheme://`` and the first ``/``, ``?`` or ``#`` (where a user name and password sit)."""
    m = _AUTHORITY.match(url)
    return m[1] if m else ""


def urlsplit_path(url: str) -> str:
    """The path of ``url`` (what follows its authority, up to a ``?`` or ``#``), by pattern: this module imports no ``urllib``."""
    m = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*([^?#]*)", url)
    return m[1] if m else ""


def key_shaped(text: str) -> bool:
    """``text`` reads like a key or a token rather than a name or a plain value (callers never print it)."""
    if _KEY_PREFIX.match(text.strip()):
        return True
    for word in _KEY_SHAPED.findall(text):
        mixed = any(ch.isupper() for ch in word) and any(ch.islower() for ch in word)
        if any(ch.isdigit() for ch in word) and (mixed or "_" not in word):
            return True
    return False


def _path_key_shaped(part: str) -> bool:
    """A part of an address path that reads like a key: a known prefix, or a run of 24 or more letters and digits with a digit
    in it and no hyphen (a model or deployment name is hyphenated words, a key is not)."""
    if _KEY_PREFIX.match(part):
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9_]{24,}", part)) and any(ch.isdigit() for ch in part) and (
        (any(ch.isupper() for ch in part) and any(ch.islower() for ch in part)) or "_" not in part)


def path_problem(path: str) -> str:
    """Why the path of an address cannot hold only a path: a parameter (``;name=value``) or a part that reads like a key (some
    services put the key in the path). The message never holds the part (2026-11-12, claims check)."""
    if ";" in path:
        return "url has a ';' parameter in its path: a key there would be stored and sent; keep it in an environment variable"
    if any(_path_key_shaped(part) for part in path.split("/")):
        return "a part of the url path reads like a key: a key there would be stored and sent; keep it in an environment variable"
    return ""


def key_env_problem(name: str) -> str:
    """Why ``name`` cannot be the name of the environment variable that holds the key, or ``""``. The message never holds
    ``name``: a key typed here by mistake is not echoed (2026-11-12, claims check)."""
    if not _VAR.match(name):
        return "key_env is the NAME of an environment variable (letters, digits and '_', not starting with a digit, at most 64), not a key"
    if _KEY_PREFIX.match(name) or (len(name) >= 20 and key_shaped(name)):
        return "key_env is the NAME of an environment variable, and what was given looks like a key: put the key in the environment and name the variable"
    return ""


def _command_problems(c: Connector) -> list[str]:
    """What is wrong with the shape of a command connector. Whether the program exists is the runner's check."""
    out = ["a command connector names a program, not an address, a model, a key or a header"] if (
        c.url or c.model or c.key_env or c.header) else []
    if not c.argv or len(c.argv) > MAX_ARGV:
        return out + [f"argv is the program and its arguments, at most {MAX_ARGV} strings"]
    if not os.path.isabs(c.argv[0]):
        out.append("argv[0] must be the absolute path of the program (connect resolves it for you)")
    if any(not a or len(a) > MAX_ARG_CHARS or _CONTROL.search(a) for a in c.argv):
        out.append(f"each argument is 1 to {MAX_ARG_CHARS} characters with no control character")
    if any(_KEY_PREFIX.match(a.split("=", 1)[-1]) for a in c.argv[1:]):
        out.append("an argument starts like a key: other users of this machine can read a program's arguments, so pass a "
                   "key through an environment variable and name it in env_pass")
    if len(c.env_pass) > MAX_ENV_PASS or len(set(c.env_pass)) != len(c.env_pass):
        out.append(f"env_pass is at most {MAX_ENV_PASS} different variable names")
    named = [why for why in map(key_env_problem, c.env_pass) if why]
    return out + [w.replace("key_env", "an env_pass entry") for w in named[:1]]


def connector_problems(c: Connector) -> list[str]:
    """What is wrong with the shape of ``c``, in plain words. The address itself (https, loopback) is the transport's check."""
    out = []
    if not _NAME.match(c.name):
        out.append("the name is 1 to 32 letters, digits, '_' or '-'")
    if c.kind not in KINDS:
        return out + [f"kind must be {' or '.join(KINDS)}, not {c.kind!r}"]
    return out + (_command_problems(c) if c.kind == "command" else _endpoint_problems(c))


def _endpoint_problems(c: Connector) -> list[str]:
    out = []
    if not c.url or len(c.url) > 2048 or re.search(r"[\s\x00-\x1f\x7f]", c.url):
        out.append("url must be one address of at most 2048 characters, with no spaces")
    elif "?" in c.url or "#" in c.url or "@" in _authority(c.url):
        out.append("url must be an address only: no user name or password, no query string and no fragment (a key there "
                   "would be stored and sent; keep it in an environment variable and name that in key_env)")
    elif path_problem(urlsplit_path(c.url)):
        out.append(path_problem(urlsplit_path(c.url)))
    if c.format not in FORMATS:
        out.append(f"format must be {' or '.join(FORMATS)}, not {c.format!r}")
    if not c.model or len(c.model) > 200 or _CONTROL.search(c.model):
        out.append("model is the model name the endpoint expects (at most 200 characters)")
    if c.key_env and key_env_problem(c.key_env):
        out.append(key_env_problem(c.key_env))
    if c.header and not _HEADER.match(c.header):
        out.append("header reads 'Name: value'")
    elif c.header and key_shaped(c.header.split(":", 1)[1]):
        out.append("header is for a plain extra header such as a version; its value looks like a key, and a key belongs "
                   "in an environment variable named in key_env")
    if c.argv or c.env_pass:
        out.append("argv and env_pass belong to a command connector")
    return out


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise AiConfigError(f"{where} must be a string")
    return value


def _unknown(table: Mapping[str, Any], allowed: tuple[str, ...], where: str) -> None:
    extra = sorted(set(table) - set(allowed))
    if extra:
        raise AiConfigError(f"{where}: unknown key {', '.join(extra)} (known: {', '.join(allowed)})")


def _tables(doc: Any, where: str) -> dict[str, Any]:
    if doc is None:
        return {}
    if not isinstance(doc, dict) or not all(isinstance(v, dict) for v in doc.values()):
        raise AiConfigError(f"{where} must hold tables, as in [{where}.name]")
    return doc


def _strings(value: Any, where: str) -> tuple[str, ...]:
    if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        raise AiConfigError(f"{where} must be a list of strings")
    return tuple(value)


def _connector(name: str, table: Mapping[str, Any]) -> Connector:
    where = f"connector.{name}"
    _unknown(table, ("kind", "url", "format", "model", "key_env", "header", "argv", "env_pass"), where)
    def field(key: str, default: str) -> str:
        return _text(table[key], f"{where}.{key}") if key in table else default

    c = Connector(name, field("kind", "endpoint"), field("url", ""), field("format", "chat"), field("model", ""),
                  field("key_env", ""), field("header", ""), _strings(table.get("argv", []), f"{where}.argv"),
                  _strings(table.get("env_pass", []), f"{where}.env_pass"))
    problems = connector_problems(c)
    if problems:
        raise AiConfigError(f"{where}: {'; '.join(problems)}")
    return c


# --- the limits, the uses and the consent -----------------------------------------------------------------------------

def _limit(value: Any, name: str) -> int:
    lo, hi = LIMIT_BOUNDS[name]
    text = str(value).strip() if isinstance(value, (int, str)) and not isinstance(value, bool) else ""
    if not (text.isdigit() and lo <= int(text) <= hi):
        raise AiConfigError(f"ai.limits.{name} must be a whole number from {lo} to {hi}")
    return int(text)


def _limits(table: Any) -> Limits:
    table = {} if table is None else table
    if not isinstance(table, dict):
        raise AiConfigError("ai.limits must be a table")
    _unknown(table, (*Limits._fields, *LEGACY_LIMITS), "ai.limits")
    for old in LEGACY_LIMITS:       # read, checked and dropped: an old file still loads
        if old in table:
            _limit(table[old], old)
    return Limits(**{name: _limit(table[name], name) for name in Limits._fields if name in table})


def _uses(table: Any, connectors: Mapping[str, Connector]) -> dict[str, str]:
    table = {} if table is None else table
    if not isinstance(table, dict):
        raise AiConfigError("ai.use must be a table")
    _unknown(table, COMMANDS, "ai.use")
    out = {}
    for command, name in table.items():
        if _text(name, f"ai.use.{command}") not in connectors:
            raise AiConfigError(f"ai.use.{command} names {name!r}, which is no [connector.{name}]")
        out[command] = name
    return out


def _commands(value: Any, where: str) -> tuple[str, ...]:
    if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        raise AiConfigError(f"{where} must be a list of strings")
    unknown = [v for v in value if v not in COMMANDS]
    if unknown:
        raise AiConfigError(f"{where}: unknown command {', '.join(unknown)} (known: {', '.join(COMMANDS)})")
    return tuple(dict.fromkeys(value))


def _repos(table: Any) -> dict[str, tuple[str, ...]]:
    out = {}
    for key, entry in _tables(table, "repos").items():
        if not _SHA.match(key):
            raise AiConfigError(f"repos.{key[:20]}: the key is the sha256 of a repository's real path")
        _unknown(entry, ("allow",), f"repos.{key[:12]}")
        out[key] = _commands(entry.get("allow", []), f"repos.{key[:12]}.allow")
    return out


# --- reading and writing the file ------------------------------------------------------------------------------------------

def _parse(text: str, where: str) -> dict[str, Any]:
    """The TOML of ``text``. A whole number is read as a string first: the reader of Python 3.10 takes strings only."""
    try:
        return core_config.parse(_NUMBER_LINE.sub(r'\1"\2"\3', text))
    except core_config.ConfigError as exc:
        raise AiConfigError(f"{where}: {exc}") from exc


def parse_config(text: str, path: str = "") -> AiConfig:
    """The user-level file ``text``, checked: an unknown key, a wrong type or a connector that does not exist names itself."""
    doc = _parse(text, path or CONFIG_NAME)
    _unknown(doc, ("connector", "ai", "repos"), path or CONFIG_NAME)
    connectors = {name: _connector(name, table) for name, table in _tables(doc.get("connector"), "connector").items()}
    ai = doc.get("ai", {})
    if not isinstance(ai, dict):
        raise AiConfigError("ai must be a table")
    _unknown(ai, ("use", "limits"), "ai")
    return AiConfig(path, connectors, _uses(ai.get("use"), connectors), _limits(ai.get("limits")), _repos(doc.get("repos")))


def load(path: str | None = None, env: Mapping[str, str] | None = None) -> AiConfig:
    """The user-level config; a missing file is an empty one (nothing connected, every command off)."""
    path = config_path(env) if path is None else path
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return AiConfig(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise AiConfigError(f"{path} cannot be read: {getattr(exc, 'strerror', None) or exc}") from exc
    return parse_config(text, path)


def _q(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_q(v) for v in values) + "]"


def render(cfg: AiConfig) -> str:
    """The file text of ``cfg``. It holds names and numbers only, never a key."""
    out = ["# brevio-core ai: your own settings. `brevio-core ai connect` writes this file; a repository never reads it.",
           "# It holds the NAME of the variable that keeps your key, never the key."]
    for name, c in sorted(cfg.connectors.items()):
        out += ["", f"[connector.{name}]"]
        out += [f"{k} = " + (_list(v) if isinstance(v, tuple) else _q(v)) for k, v in c._asdict().items()
                if k != "name" and (v or k == "kind") and not (k == "format" and c.kind == "command")]
    if cfg.use:
        out += ["", "[ai.use]", *(f"{command} = {_q(name)}" for command, name in sorted(cfg.use.items()))]
    out += ["", "[ai.limits]", *(f"{k} = {v}" for k, v in cfg.limits._asdict().items())]
    for key, allowed in sorted(cfg.repos.items()):
        out += ["", f'[repos."{key}"]', "allow = [" + ", ".join(_q(c) for c in allowed) + "]"]
    return "\n".join(out) + "\n"


def write_private(path: str, text: str) -> None:
    """Write ``text`` to ``path`` with mode 0600, through a temporary file that is created exclusively and never through a
    link, so a crash never leaves half a file and no one can plant a link at its name (``OSError`` on failure)."""
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    for _try in range(8):
        tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        try:
            fd = os.open(tmp, flags, 0o600)
        except FileExistsError:
            continue
        break
    else:
        raise OSError("no free name for a temporary file")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        if os.path.lexists(tmp):
            os.remove(tmp)
        raise


def save(cfg: AiConfig) -> None:
    """Write ``cfg`` to its own path (0600, atomic)."""
    write_private(cfg.path, render(cfg))


def with_connector(cfg: AiConfig, connector: Connector, commands: tuple[str, ...] = ()) -> AiConfig:
    """``cfg`` with ``connector`` added (or replaced) and ``commands`` pointed at it; refuses a connector of the wrong shape."""
    problems = connector_problems(connector)
    unknown = [c for c in commands if c not in COMMANDS]
    if problems or unknown:
        raise AiConfigError("; ".join(problems + [f"unknown command {c}" for c in unknown]))
    return cfg._replace(connectors={**cfg.connectors, connector.name: connector},
                        use={**cfg.use, **{c: connector.name for c in commands}})


def use_for(cfg: AiConfig, command: str) -> Connector | None:
    """The connector ``command`` is turned on for, or ``None`` when it is off."""
    return cfg.connectors.get(cfg.use.get(command, ""))


def consented(cfg: AiConfig, key: str, command: str) -> bool:
    """The user agreed to ``command`` in the repository filed under ``key``."""
    return command in cfg.repos.get(key, ())


def with_consent(cfg: AiConfig, key: str, commands: tuple[str, ...]) -> AiConfig:
    """``cfg`` with ``commands`` recorded as agreed to in the repository filed under ``key``."""
    if not _SHA.match(key) or any(c not in COMMANDS for c in commands):
        raise AiConfigError("consent is filed under repo_key() and names known commands")
    return cfg._replace(repos={**cfg.repos, key: tuple(dict.fromkeys((*cfg.repos.get(key, ()), *commands)))})


# --- what a repository may say: narrowing only ----------------------------------------------------------------------------------

class RepoPolicy(NamedTuple):
    enabled: bool = True
    allow: tuple[str, ...] | None = None     # None: every command the user turned on
    deny_paths: tuple[str, ...] = ()         # path globs whose files are never sent

    def refusal(self, command: str) -> str | None:
        """Why this repository keeps ``command`` off, or ``None``."""
        if not self.enabled:
            return "this repository turns ai off ([ai] enabled = false in brevio-core.toml)"
        if self.allow is not None and command not in self.allow:
            return f"this repository allows only: {', '.join(self.allow) or 'nothing'} ([ai] allow in brevio-core.toml)"
        return None

    def denies(self, rel: str) -> bool:
        """``rel`` (relative to the repository, ``/``-separated) matches a ``deny_paths`` glob."""
        return any(core_config.path_matches(p, rel) for p in self.deny_paths)


def _narrow(table: Any, where: str) -> RepoPolicy:
    if table is None:
        return RepoPolicy()
    if not isinstance(table, dict):
        raise AiConfigError(f"{where}: ai must be a table")
    named = sorted(k for k, v in table.items() if k in ROUTING_KEYS or isinstance(v, dict))
    if named:
        raise RoutingError(f"{where}: [ai] {', '.join(named)}: {ROUTING_MESSAGE}; "
                           "a repository may only set enabled = false, allow and deny_paths")
    _unknown(table, _POLICY_KEYS, f"{where}: [ai]")
    if not isinstance(table.get("enabled", True), bool):
        raise AiConfigError(f"{where}: [ai] enabled must be true or false")
    allow = _commands(table["allow"], f"{where}: [ai] allow") if "allow" in table else None
    deny = table.get("deny_paths", [])
    if not (isinstance(deny, list) and all(isinstance(p, str) for p in deny)):
        raise AiConfigError(f"{where}: [ai] deny_paths must be a list of strings")
    return RepoPolicy(table.get("enabled", True), allow, tuple(deny))


def _meet(a: RepoPolicy, b: RepoPolicy) -> RepoPolicy:
    """Both narrowings at once: a policy it extends can only be tightened by the file that extends it."""
    allow = b.allow if a.allow is None else a.allow if b.allow is None else tuple(c for c in a.allow if c in b.allow)
    return RepoPolicy(a.enabled and b.enabled, allow, tuple(dict.fromkeys((*a.deny_paths, *b.deny_paths))))


def _policy_of(path: str, root: str, chain: tuple[str, ...]) -> RepoPolicy:
    real = os.path.realpath(path)
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    if real in chain or len(chain) >= core_config.MAX_EXTENDS:
        raise AiConfigError(f"{rel}: extends goes round in a loop or is too deep")
    try:
        with open(path, encoding="utf-8") as fh:
            doc = core_config.parse(fh.read())
    except (OSError, UnicodeDecodeError) as exc:
        raise AiConfigError(f"{rel} cannot be read: {getattr(exc, 'strerror', None) or exc}") from exc
    except core_config.ConfigError as exc:
        raise AiConfigError(f"{rel}: {exc}") from exc
    own = _narrow(doc.get("ai"), rel)
    try:        # the gate's own check of this file: a table or key the tool does not know is refused here too (2026-11-12)
        core_config.layer(doc, rel, os.path.dirname(rel))
    except core_config.ConfigError as exc:
        raise AiConfigError(f"{rel}: {exc}") from exc
    spec = doc.get("extends")
    if not (isinstance(spec, str) and spec):
        return own
    try:
        parent = core_config.resolve_extends(spec, os.path.dirname(path), root)
    except core_config.ConfigError as exc:
        raise AiConfigError(f"{rel}: {exc}") from exc
    return _meet(_policy_of(parent, root, (*chain, real)), own)


def repo_policy(root: str = ".") -> RepoPolicy:
    """What the repository's ``brevio-core.toml`` (and the policy it extends) narrows; no file narrows nothing.

    A routing key in an ``[ai]`` table raises ``RoutingError`` ("routing is user-level only")."""
    path = os.path.join(root, core_config.CONFIG_NAME)
    return _policy_of(path, root, ()) if os.path.isfile(path) else RepoPolicy()


# --- the ledger -------------------------------------------------------------------------------------------------------------

def today(now: datetime.datetime | None = None) -> str:
    """The user's calendar day (local time), as the ledger keys it; a time that carries a zone is read in the local one."""
    now = now or datetime.datetime.now()
    return (now.astimezone() if now.tzinfo is not None else now).date().isoformat()


def _read_days(path: str) -> dict[str, dict[str, int]]:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise AiConfigError(f"{path} cannot be read ({exc.__class__.__name__}); delete it to start the count again") from exc
    days = doc.get("days") if isinstance(doc, dict) else None
    fields = Usage._fields
    if not (isinstance(days, dict) and all(
            isinstance(d, dict) and all(type(d.get(f, 0)) is int and d.get(f, 0) >= 0 for f in fields)
            for d in days.values())):
        raise AiConfigError(f"{path} is not a ledger; delete it to start the count again")     # failing closed keeps a limit a limit
    return {day: {f: d.get(f, 0) for f in fields} for day, d in days.items()}


def usage_on(path: str, day: str | None = None) -> Usage:
    """The calls, characters and tokens recorded on ``day`` (today by default) in the ledger at ``path``."""
    return Usage(**_read_days(path).get(day or today(), {}))


LOCK_WAIT_S = 10


@contextlib.contextmanager
def ledger_lock(path: str) -> Generator[None, None, None]:
    """An exclusive lock for the ledger at ``path`` (``fcntl.flock`` on its folder, so no lock file is left lying about), so
    two commands at once take turns; it waits up to ``LOCK_WAIT_S`` seconds. On a system without ``flock`` nothing is locked."""
    if _fcntl is None:
        yield
        return
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    fd = os.open(os.path.dirname(path) or ".", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise AiConfigError("the ledger is in use by another command; try again in a moment") from None
                time.sleep(0.02)
        yield
    finally:
        os.close(fd)        # closing the file releases the lock


def _add(path: str, day: str | None, **add: int) -> Usage:
    """Add to the day's count and write the ledger; the caller holds the lock."""
    day = day or today()
    days = _read_days(path)
    entry = days.setdefault(day, dict.fromkeys(Usage._fields, 0))
    for field, n in add.items():
        entry[field] += max(0, int(n))
    kept = dict(sorted(days.items())[-KEEP_DAYS:])
    write_private(path, json.dumps({"version": 1, "days": kept}, indent=1, sort_keys=True) + "\n")
    return Usage(**entry)


def _bump(path: str, day: str | None, **add: int) -> Usage:
    with ledger_lock(path):
        return _add(path, day, **add)


class LimitError(AiConfigError):
    """A call would pass a limit; the message says which."""


def reserve_call(path: str, limits: Limits, chars: int, day: str | None = None) -> Usage:
    """Check today's limits and count one call of ``chars`` characters in one step, under the ledger lock, just BEFORE it is
    sent: two commands at once cannot both pass a limit of one. Raises ``LimitError`` (nothing is counted) when a limit
    is passed; a call that fails after it left still counts."""
    with ledger_lock(path):
        used = Usage(**_read_days(path).get(day or today(), {}))
        over = day_problems(limits, used, chars)
        if over:
            raise LimitError("; ".join(over))
        return _add(path, day, calls=1, chars=chars)


def record_call(path: str, chars: int, day: str | None = None) -> Usage:
    """Count one call of ``chars`` characters, just BEFORE it is sent: a call that fails after leaving still counts. It checks
    no limit: ``reserve_call`` does."""
    return _bump(path, day, calls=1, chars=chars)


def record_tokens(path: str, input_tokens: int, output_tokens: int, day: str | None = None) -> Usage:
    """Add the tokens the endpoint reported for a reply (only when it reports them)."""
    return _bump(path, day, input_tokens=input_tokens, output_tokens=output_tokens)


def day_problems(limits: Limits, used: Usage, chars: int) -> list[str]:
    """Which limit a call of ``chars`` characters would pass, given today's ``used``."""
    out = []
    if used.calls >= limits.max_calls_per_day:
        out.append(f"today's {limits.max_calls_per_day} calls are used (max_calls_per_day)")
    if chars > limits.max_input_chars:
        out.append(f"{chars} characters is more than max_input_chars ({limits.max_input_chars})")
    return out


def limit_problems(limits: Limits, used: Usage, calls_this_run: int, chars: int) -> list[str]:
    """``day_problems``, under its old name and arguments (``calls_this_run`` is ignored: there is no per-run limit now)."""
    return day_problems(limits, used, chars)
