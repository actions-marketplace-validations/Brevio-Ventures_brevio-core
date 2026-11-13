"""The agent-tool commands of the ``brevio-core`` package: learn, hook, gain, add and mcp (ROADMAP E16, the 0.2 set, 2026-11-04).

``core_cli`` loads this table when the build ships it (the 0.2 export set) and shows no such commands when it does not.
Standard library only, so it exports with the commands it calls. The cloud bridge (``up``) is not here: it stays in the
harness.
"""
from __future__ import annotations

from collections.abc import Callable

from brevio.operator import gain_cmd, hook_cmd, learn_cmd, mcp_cmd, pack_cmd

HELP = {
    "learn": "compile a fixed defect (a git diff) into a local guard and an agent rule",
    "hook": "install the scan hook into the coding agents found here (hook install | status | detect)",
    "gain": "print what the local ledger recorded: hits found by learn --check, learned guards, savings only when measured",
    "add": "add a curated guard pack by name (no name lists the packs)",
    "pack": "install or list community zero-token guard packs",
    "mcp": "serve scan, explain, list_rules and learn to an agent over MCP on stdin and stdout",
}


def _add(argv: list[str], name: str = "add") -> int:
    """``pack`` or ``add``: installs a pack or lists available packs. ``add --help`` shows the usage of ``add``, not of ``list``."""
    if not argv:
        return pack_cmd.main(["list"], prog="brevio-core")
    first = argv[0]
    if first == "list":
        return pack_cmd.main(argv, prog="brevio-core")
    if first == "add":
        return pack_cmd.main(argv, prog="brevio-core")
    non_flags = [a for a in argv if not a.startswith("-")]
    if not non_flags:
        asked = any(a in ("-h", "--help") for a in argv)
        if asked:      # the usage line names the command that was typed: ``pack --help`` is not ``brevio-core {list,add}``
            return pack_cmd.main(["add", *argv] if name == "add" else argv, prog="brevio-core" if name == "add" else "brevio-core pack")
        return pack_cmd.main(["list", *argv], prog="brevio-core")
    return pack_cmd.main(["add", *argv], prog="brevio-core")


COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "learn": lambda argv: learn_cmd.main(argv, prog="brevio-core learn"),
    "hook": lambda argv: hook_cmd.main(argv, prog="brevio-core hook"),
    "gain": lambda argv: gain_cmd.main(argv, prog="brevio-core gain"),
    "add": _add,
    "pack": lambda argv: _add(argv, "pack"),
    "mcp": lambda argv: mcp_cmd.main(argv, prog="brevio-core mcp"),
}
