"""What differs between Windows and POSIX, in one place, for the ``brevio-core`` command line.

Standard library only. Two things a user on Windows hits (the first Windows run of the exported tree, 2026-11-12):
standard output and input are the legacy code page when they are a pipe or a file, so a non-ASCII character raises
``UnicodeEncodeError``; and a path on another drive has no
relative form, where ``os.path.relpath`` raises ``ValueError``; and the null device counts as a terminal. (What git prints is read with ``encoding="utf-8"`` at
each call, which the no-network check keeps as direct ``subprocess.run`` calls that start git.)
"""
from __future__ import annotations

import os
import sys


def use_utf8_streams() -> None:
    """Make standard input, output and error UTF-8. The command-line entry point calls this once, never an import.

    A pipe or a file gets the code page of the system (``cp1252`` on a western Windows), which cannot hold the box
    lines, the check marks or the dashes the commands print. A stream that already is UTF-8 is left as it is."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)     # a stream a test swapped in has none
        if reconfigure is None or (getattr(stream, "encoding", "") or "").lower().replace("-", "") == "utf8":
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):       # a stream that was already read from keeps the encoding it has
            continue


def shown(path: str, start: str | None = None) -> str:
    """``path`` as the tool prints and matches it: relative to ``start`` (the current folder) and with ``/``.

    A path on another drive than ``start`` has no relative form on Windows, so it is shown in full."""
    try:
        rel = os.path.relpath(path, start)
    except ValueError:
        rel = os.path.abspath(path)
    return rel.replace(os.sep, "/")


def _is_console(stream) -> bool:
    """Windows only: True when ``stream`` reads or writes a real console, not a pipe, a file or the null device.

    Python opens a console with its own console reader or writer and anything else as a plain file, so the type of
    the raw stream tells them apart without a call into the system (``ctypes`` is not allowed in the core)."""
    if sys.platform != "win32":
        return True
    raw = getattr(getattr(stream, "buffer", None), "raw", None)
    return raw is None or type(raw).__name__ in ("_WindowsConsoleIO", "WindowsConsoleIO")


def is_terminal(stream) -> bool:
    """True when a person can type at ``stream``.

    On Windows ``isatty`` is also true for the null device (``NUL``), so a run with its input closed that way
    would wait at a prompt that nothing answers (first Windows run, 2026-11-12); the console itself is asked."""
    return bool(stream.isatty()) and _is_console(stream)
