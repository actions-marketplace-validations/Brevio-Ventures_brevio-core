"""The agent hooks: `cli.py hook` in the harness, `brevio-core hook` in the package.

``install`` writes the hooks, ``status`` and ``detect`` report, and ``run`` is the hook itself, which a coding client
calls with its event on stdin (``core_hook``, the 0.4 set; ROADMAP E34, 2026-11-05). A build without that module still
answers ``run``, with exit 0 and one line, so a hook it finds never blocks an agent.
"""
from __future__ import annotations

import argparse
import json
import sys

from brevio.pipeline import core_cli
from brevio.pipeline.agent_host import client_detect
from brevio.pipeline.gates import agent_hooks


def _run_detect_cmd(args: argparse.Namespace) -> int:
    detected = client_detect.detect_llm_clients(probe_daemons=False)
    if args.json:
        print(json.dumps(detected.to_dict(), indent=2))
        return 0
    print("═══════════════════════════════════════════════════════════════")
    print("  brevio hook detect — Coding Client Detection")
    print("═══════════════════════════════════════════════════════════════")
    print(detected.summary())
    return 0


def _run_install_cmd(args: argparse.Namespace) -> int:
    targets: list[str] | None = None
    if getattr(args, "target", None):
        targets = [t.strip() for t in args.target.split(",") if t.strip()]

    init = core_cli.set_module("pipeline", "core_init")      # absent from the early sets; then the hook is not pinned
    release = init.release_info() if init is not None else None
    version = release.version if release is not None else None
    force_all = getattr(args, "all", False)
    if force_all:
        results = agent_hooks.install_all_hooks(force_all=True, version=version)
    else:
        results = agent_hooks.install_targeted_hooks(targets=targets, version=version)

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    print("═══════════════════════════════════════════════════════════════")
    print("  brevio-core hook install — Agent Hooks (G12)")
    print("═══════════════════════════════════════════════════════════════")
    for agent_name in ("claude_code", "cursor", "hermes", "openclaw"):
        status_val = results.get(agent_name)
        status_text = "✅ Installed" if status_val is True else ("❌ Failed" if status_val is False else "○ Skipped (not detected)")
        print(f"  {agent_name:12}: {status_text}")
    for line in agent_hooks.hook_files(results):
        print(f"  wrote {line}, run by uvx (uvx needs to be on PATH)")
    if version and agent_hooks.hook_files(results):
        print(f"  The hooks run brevio-core {version} until you run `{args.command} install` again.")
    print("  Status: Active agents now check their edits against the rules, with no model call.")
    return 0


def _run_status_cmd(args: argparse.Namespace) -> int:
    status = agent_hooks.get_hooks_status()
    if args.json:
        print(json.dumps(status, indent=2))
        return 0

    print("═══════════════════════════════════════════════════════════════")
    print("  brevio-core hook status — Agent Hooks Status (G12)")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Claude Code:     {'✅ Active' if status.get('claude_code') else '○ Not installed'}")
    print(f"  Cursor:          {'✅ Active' if status.get('cursor') else '○ Not installed'}")
    print(f"  Hermes:          {'✅ Active' if status.get('hermes') else '○ Not installed'}")
    print(f"  OpenClaw:        {'✅ Active' if status.get('openclaw') else '○ Not installed'}")
    print(f"  Run `{args.command} install` to wire detected hooks (or `--all` for all).")
    return 0


HOOK_MODULE = "core_hook"   # the 0.4 set's hook runner; named here so a build without it is detected


def _run_hook(argv: list[str], prog: str) -> int:
    runner = core_cli.set_module("pipeline", HOOK_MODULE)
    if runner is None:
        print(f"{prog} run: this build has no hook runner; skipped (install brevio-core 0.4 or later)", file=sys.stderr)
        return 0
    return runner.main(argv, prog=f"{prog} run")


def main(argv: list[str] | None = None, prog: str = "cli.py hook") -> int:
    """Run the hook command; ``prog`` is how the user types it (the exported package says ``brevio-core hook``)."""
    line = sys.argv[1:] if argv is None else argv
    if line[:1] == ["run"]:     # the runner reads the rest of the line itself
        return _run_hook(line[1:], prog)
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Wire zero-token hooks and progressive rules into coding agents (Claude, Cursor, Hermes).",
    )
    parser.add_argument("--json", action="store_true", help="Output status as JSON")
    subparsers = parser.add_subparsers(dest="subcommand")

    install_parser = subparsers.add_parser("install", help="Install hooks into detected agent configurations")
    install_parser.add_argument("--all", action="store_true", help="Force install into all supported agents")
    install_parser.add_argument("--target", help="Comma-separated agents (claude_code,cursor,hermes,openclaw)")
    install_parser.add_argument("--json", action="store_true", help="Output status as JSON")

    status_parser = subparsers.add_parser("status", help="Check hook installation status")
    status_parser.add_argument("--json", action="store_true", help="Output status as JSON")

    detect_parser = subparsers.add_parser("detect", help="Auto-detect available LLM clients, agents and local daemons")
    detect_parser.add_argument("--json", action="store_true", help="Output status as JSON")

    subparsers.add_parser("run", add_help=False,
                          help="the hook itself: reads a client's event JSON on stdin (run --event post-edit|stop)")

    args = parser.parse_args(line)
    args.command = prog
    cmd = args.subcommand or "status"

    if cmd == "detect":
        return _run_detect_cmd(args)
    if cmd == "install":
        return _run_install_cmd(args)
    return _run_status_cmd(args)


if __name__ == "__main__":
    sys.exit(main())
