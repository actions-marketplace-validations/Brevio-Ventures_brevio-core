"""The command connector of ``brevio-core ai``: a program you name receives the request (the 0.7 set, improvement plan,
2026-11-12). It is the only shipped module that starts a program other than ``git``; the security test names it as the
exception, as it names ``core_ai_net`` for the network.

The program and its arguments are an array of strings in your own settings file, never read from a repository. One call
starts the program once:

- no shell: the program is started from its absolute path, found when you connect, with the arguments as separate strings;
- the request goes to its standard input, and is not put into its arguments;
- its working folder is a new empty temporary folder, removed afterwards (on Windows a folder the program still holds stays);
- its environment holds what a program needs to start on this platform, plus the variables you named (``env_pass``);
- it is stopped after the timeout, and stopped when it writes more than a size cap;
- nothing is started when a CI variable is set (the command module also asks for a terminal).

What it writes to standard output is the reply, and is treated as an endpoint's reply is: data, printed inside a closed block,
a fix checked by the rules. The program may itself send the request elsewhere: brevio-core cannot see what it does. Where the
system has process groups, the program and the processes that share its group are stopped together, at the timeout and when
the program ends (what it left running would hold the pipes open); a process that starts a group of its own is not stopped,
and on Windows only the program is. Standard library only; it imports neither
the transport nor the network.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from io import BufferedReader, BufferedWriter
from typing import Any

from brevio.pipeline.core_ai_state import Connector
from brevio.pipeline.core_ai_text import EXPLAIN_SYSTEM

SYSTEM = EXPLAIN_SYSTEM       # the same line as the endpoint's
TIMEOUT_S = 60
MAX_REPLY_BYTES = 1_000_000     # a program that writes more is stopped: the reply is read into memory
MAX_ERROR_BYTES = 400           # of its error output, kept to say why it failed
ERROR_SHOWN = 200               # characters of the program's error output that an error message holds
# What a program is started with besides the names the user lists: what starting one needs on the platform.
BASE_ENV = {"posix": ("PATH", "LANG", "LC_ALL"),
            "windows": ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP")}
CI_VARIABLES = ("CI", "CONTINUOUS_INTEGRATION", "BUILD_NUMBER")     # a build system sets one of them; others are not recognised
_BATCH = (".bat", ".cmd")       # run through the command interpreter, which reads the arguments again: refused on Windows
_TOO_LONG, _TOO_SLOW = "wrote more than the size cap", "did not finish in time"


class ExecError(RuntimeError):
    """The program was refused before it started, or it failed."""


ERROR = ExecError       # the name the command module catches, the same for the transport and for this runner


def platform_names() -> tuple[str, ...]:
    """The variables a program is started with besides the user's list, for this platform."""
    return BASE_ENV["windows" if sys.platform == "win32" else "posix"]


def ci_reason(env: Mapping[str, str]) -> str:
    """Why a CI variable stops the call, or ``""``. A variable that holds a value stops it, whatever the value: ``CI=false`` is
    set as well (2026-11-12, review: the texts say "is set", so the code does)."""
    for name in CI_VARIABLES:
        if env.get(name, "").strip():
            return f"the variable {name} is set, which a build system does: a program is started only for a person at a terminal"
    return ""


def resolve(program: str, env: Mapping[str, str] | None = None) -> str:
    """The absolute path of ``program``: as given when it has a folder part, else found on ``PATH``. Raises ``ExecError`` when
    it is not an executable file. ``connect`` stores this path, so the program is not looked up again at each call."""
    env = os.environ if env is None else env
    named = os.path.basename(program) != program
    found = os.path.abspath(program) if named else (shutil.which(program, path=env.get("PATH")) or "")
    if not found:
        raise ExecError("no program of that name is on PATH; give its path")
    found = os.path.abspath(found)
    if not (os.path.isfile(found) and os.access(found, os.X_OK)):
        raise ExecError(f"{found} is not an executable file")
    if not named and _only_here(found, env):
        raise ExecError("that name is found only in the current folder, which a repository can fill: give the program's full path")
    return found


def _only_here(found: str, env: Mapping[str, str]) -> bool:
    """``found`` sits in the current folder and ``PATH`` does not name that folder: on Windows a lookup by name looks there
    first, so a program of a repository could stand in for the one meant (2026-11-12, review)."""
    here = os.path.realpath(os.getcwd())
    listed = {os.path.realpath(entry or os.curdir) for entry in env.get("PATH", "").split(os.pathsep)}
    return os.path.realpath(os.path.dirname(found)) == here and here not in listed


def problems(c: Connector, env: Mapping[str, str] | None = None) -> list[str]:
    """What keeps ``c`` from running, in plain words; empty when it may."""
    env = os.environ if env is None else env
    out = [ci_reason(env)] if ci_reason(env) else []
    if not c.argv or not os.path.isabs(c.argv[0]):
        return out + ["the connector holds no absolute program path"]
    if not (os.path.isfile(c.argv[0]) and os.access(c.argv[0], os.X_OK)):
        out.append(f"{c.argv[0]} is no longer an executable file")
    if sys.platform == "win32" and c.argv[0].lower().endswith(_BATCH):
        out.append("a batch file runs through the command interpreter, which reads its arguments again: name the program it starts")
    return out


def destination(c: Connector) -> str:
    """Where the request goes, for the user to read."""
    return f"the program {c.argv[0] if c.argv else '(none)'}"


def key_of(c: Connector, env: Mapping[str, str]) -> str:
    """No key is read here: a program that needs one gets the variables its connector lists (``env_pass``)."""
    return ""


def request_text(prompt: str, system: str = SYSTEM) -> str:
    """What the program reads on standard input: the instruction line, an empty line, the request."""
    return f"{system}\n\n{prompt}"


def child_env(c: Connector, env: Mapping[str, str]) -> dict[str, str]:
    """The environment the program starts with: the platform's start-up names and the listed names, where they are set."""
    return {n: env[n] for n in (*platform_names(), *c.env_pass) if n in env}


def preview(c: Connector, prompt: str, max_output: int = 0, system: str = SYSTEM, keyed: bool | None = None,
            env: Mapping[str, str] | None = None) -> str:
    """What ``complete`` would do, as text: the program, its arguments, the environment names, and the exact input."""
    env = os.environ if env is None else env
    listed = ", ".join(f"{n} ({'set' if n in env else 'not set, so not passed'})" for n in c.env_pass) or "none"
    data = request_text(prompt, system)
    return (f"RUN {destination(c)}\n(arguments: {list(c.argv[1:])!r}; no shell, started once, from a new empty temporary folder)\n"
            f"(environment: {', '.join(platform_names())} where set, and the names you listed: {listed})\n"
            f"(the request goes on its standard input, {len(data.encode('utf-8'))} bytes; its output is read up to a size cap "
            f"and it is stopped after the timeout)\n(brevio-core cannot see what the program does with it: it may send "
            f"the request elsewhere)\n\n{data}\n")


def _drain(stream: BufferedReader, keep: int, kept: bytearray, over: threading.Event | None) -> None:
    """Read ``stream`` to its end, keep the first ``keep`` bytes in ``kept`` (and one more, to see it was longer) and set
    ``over`` when more than ``keep`` arrived. Reading on, rather than stopping, keeps the program from blocking on a full
    pipe; ``kept`` fills as it goes, so what arrived is there even when a program the first one started holds the pipe open."""
    total = 0
    while True:
        try:
            chunk = stream.read1(65536)       # what has arrived, not a wait for a full block
        except (OSError, ValueError):
            break
        if not chunk:
            break
        total += len(chunk)
        kept += chunk[: max(0, keep + 1 - len(kept))]
        if total > keep and over is not None:
            over.set()


def _feed(stream: BufferedWriter, data: bytes) -> None:
    try:
        stream.write(data)
    except OSError:     # the program closed its input before reading it all
        pass
    try:
        stream.close()
    except OSError:     # closing flushes what is left, which fails when the program has gone
        pass


def _stop(proc: Any) -> None:
    """Stop the program, and the processes it started where the system has process groups (also after it ended: a process it
    left running would hold its output pipes open)."""
    killpg = getattr(os, "killpg", None)
    try:
        if killpg is not None:
            killpg(proc.pid, signal.SIGKILL)     # the program leads its own group (``start_new_session``)
        else:
            proc.kill()     # Windows: the program only
    except OSError:
        pass


def _wait(proc: Any, deadline: float, over: threading.Event) -> str:
    """``""`` when the program ended by itself, else why it was stopped."""
    while proc.poll() is None:
        if over.is_set() or time.monotonic() > deadline:
            _stop(proc)
            return _TOO_LONG if over.is_set() else _TOO_SLOW
        time.sleep(0.01)
    return ""


@contextlib.contextmanager
def _scratch() -> Iterator[str]:
    """A new empty folder, removed afterwards as far as the system lets it. Windows keeps a folder a program (or a process it
    left running) still holds as its working folder; that must not fail a call that has its reply, and
    ``TemporaryDirectory(ignore_cleanup_errors=True)`` recurses without end on Python 3.10 there (2026-11-13, Windows CI)."""
    work = tempfile.mkdtemp(prefix="brevio-ai-")
    try:
        yield work
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run(argv: list[str], data: bytes, env: dict[str, str], timeout: float) -> tuple[int, bytes, bytes, str]:
    """``(exit code, output, error output, why it was stopped or "")`` of one run in a new empty temporary folder."""
    out, err = bytearray(), bytearray()
    over = threading.Event()
    with _scratch() as work:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=work,
                                    env=env, shell=False, close_fds=True, start_new_session=True)
        except OSError as exc:
            raise ExecError(f"the program could not be started: {exc.strerror or type(exc).__name__}") from exc
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        deadline = time.monotonic() + timeout
        threads = [threading.Thread(target=_feed, args=(proc.stdin, data), daemon=True),
                   threading.Thread(target=_drain, args=(proc.stdout, MAX_REPLY_BYTES, out, over), daemon=True),
                   threading.Thread(target=_drain, args=(proc.stderr, MAX_ERROR_BYTES, err, None), daemon=True)]
        for t in threads:
            t.start()
        why = _wait(proc, deadline, over)
        if not why:
            _stop(proc)     # what the program left running goes with it: it would hold the pipes open (2026-11-12, review)
        for t in threads:
            t.join(2)       # a process the program started that keeps a pipe open is not waited for
        why = why or (_TOO_LONG if over.is_set() else "")
        try:
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = -1
        for pipe, reader in ((proc.stdout, threads[1]), (proc.stderr, threads[2])):
            if not reader.is_alive():       # closing a pipe a thread is still reading blocks until that read ends
                pipe.close()
    return code, bytes(out), bytes(err), why


def _failure(code: int, error: bytes, why: str, timeout: float) -> str:
    """The message of a run that did not give a reply."""
    if why == _TOO_SLOW:
        return f"the program did not finish within {timeout:g} seconds and was stopped"
    if why:
        return f"the program {why} and was stopped"
    shown = " ".join(error.decode("utf-8", "replace").split())[:ERROR_SHOWN]
    return f"the program exited with code {code}" + (f"; its error output starts: {shown}" if shown else "")


def complete(c: Connector, prompt: str, max_output: int = 0, *, env: Mapping[str, str] | None = None,
             timeout: float = TIMEOUT_S, system: str = SYSTEM) -> tuple[str, None]:
    """Start the program of ``c`` once with ``prompt`` on its standard input and return ``(its output, None)``.

    Raises ``ExecError`` before anything starts when ``problems`` finds any, and after it when the program fails, is stopped,
    or writes nothing. There is no retry. ``max_output`` is kept for the transport's signature: the program is not told it."""
    env = os.environ if env is None else env
    found = problems(c, env)
    if found:
        raise ExecError("; ".join(found))
    code, out, err, why = _run(list(c.argv), request_text(prompt, system).encode("utf-8"), child_env(c, env), timeout)
    if why or code != 0:
        raise ExecError(_failure(code, err, why, timeout))
    text = out.decode("utf-8", "replace").replace("\r\n", "\n").strip()      # a Windows program writes text with CRLF line ends
    if not text:
        raise ExecError("the program wrote nothing")
    return text, None
