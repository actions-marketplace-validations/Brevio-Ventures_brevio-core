"""``brevio-core`` with no command, ``-h``, ``--help`` and ``help``: the short list a newcomer needs (2026-11-12, improvement plan).

The full help names every command of the build, which is more than a person who has just installed the tool can read. The short
help names six, one line each, and says where the rest are: ``brevio-core help --all`` prints the full list and the generated
command reference (``docs/commands.md``) holds every option. A command that the build does not ship is left out of the short
list, so it never names a command that does not run. Standard library only, and it imports nothing of the core, so listing
the commands loads no command module.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Iterable

# (command, what it does). The words are the tool's, kept to what the command does; ``scan`` and ``explain`` are in every build.
NEWCOMER = (
    ("scan", "check files, folders or a diff; scan --diff origin/main reads the lines a change adds"),
    ("init", "set this repository up: a workflow, agent rules and a baseline; init --dry-run shows it first"),
    ("check", "run the pre-PR flight check"),
    ("learn", "turn a fix you committed into a guard; learn --dry-run shows what it would keep"),
    ("explain", "say why a rule exists and how to fix it, for example: explain R9.2"),
    ("hook", "attach the scan to the coding agents on this machine; hook install does it"),
)
ALWAYS = frozenset({"scan", "explain"})
HELP_ALL = "help --all"


def short(available: Iterable[str]) -> str:
    """The short help: the commands of ``NEWCOMER`` that the build has, and the pointer to the full list."""
    have = set(available) | ALWAYS
    rows = [(name, text) for name, text in NEWCOMER if name in have]
    width = max(len(name) for name, _ in rows)
    lines = ["brevio-core: check code against fixed rules, before it is merged.", "", "Start with:"]
    lines += [f"  {name.ljust(width)}  {text}" for name, text in rows]
    lines += ["", f"The full list of commands: brevio-core {HELP_ALL}", "The options of one command: brevio-core <command> --help"]
    return "\n".join(lines) + "\n"


def run(rest: list[str], available: Iterable[str], full: Callable[[], str], forward: Callable[[str], int] | None = None) -> int:
    """``brevio-core help [--all | <command>]``, and the help a bare ``brevio-core`` or ``--help`` prints.

    ``full`` gives the full help; ``forward`` runs ``<command> --help``, so ``help scan`` and ``scan --help`` print the same."""
    if rest == ["--all"]:
        sys.stdout.write(full())
        return 0
    if len(rest) == 1 and forward is not None and not rest[0].startswith("-"):
        return forward(rest[0])
    if rest and rest not in (["-h"], ["--help"]):
        print(f"brevio-core: help takes --all or one command name (got {' '.join(rest)}); use brevio-core <command> --help", file=sys.stderr)
        return 2
    sys.stdout.write(short(available))
    return 0
