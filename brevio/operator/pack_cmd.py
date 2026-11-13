"""Community guard packs: `cli.py pack` in the harness, `brevio-core add` in the package."""
from __future__ import annotations

import argparse
import json
import sys

from brevio.pipeline.agent_host import config
from brevio.pipeline.gates import defect_compiler, guard_packs, guard_proof


def _replays(installed: list[str]) -> dict[str, dict]:
    """What each installed guard would have flagged in the recent history of this repository (2026-11-13, packs): a pack
    is hand-written, so the count is the only evidence of how it fits here."""
    guards = defect_compiler.guard_table(defect_compiler.load_guards(config.WORKSPACE_DIR))
    return {gid: guard_proof.replay(guards[gid], config.WORKSPACE_DIR) for gid in installed if isinstance(guards.get(gid), dict)}


def main(argv: list[str] | None = None, prog: str = "cli.py pack") -> int:
    """List or add guard packs; ``prog`` is how the user types the command (``brevio-core`` for ``add``)."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Install curated zero-token community guard packs (Next.js, Stripe, FastAPI, EU AI Act, Supabase).",
    )
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    subparsers = parser.add_subparsers(dest="subcommand")
    list_p = subparsers.add_parser("list", help="List available community guard packs")
    list_p.add_argument("--json", action="store_true", help="Output result as JSON")
    add_p = subparsers.add_parser("add", help="Add a community guard pack",
                                  description="Install the pack called NAME. `brevio-core add` and `brevio-core pack` with no name list the packs.")
    add_p.add_argument("pack", help="Name of the pack to install")
    add_p.add_argument("--json", action="store_true", help="Output result as JSON")

    args = parser.parse_args(argv)
    cmd = args.subcommand or "list"

    if cmd == "list":
        packs = guard_packs.list_packs()
        if args.json:
            print(json.dumps(packs, indent=2))
            return 0
        print("═══════════════════════════════════════════════════════════════")
        print("  brevio-core packs — Community Zero-Token Guard Packs (G14)")
        print("═══════════════════════════════════════════════════════════════")
        for p in packs:
            print(f"  • {p['name']:<24} ({p['guard_count']} guards)")
            print(f"    {p['description']}")
        print(f"\nInstall any pack with `{prog} add <name>` (adds 0 prompt tokens).")
        return 0

    if cmd == "add":
        try:
            installed = guard_packs.install_pack(args.pack)
            note = guard_packs.requirement_note(args.pack)
            replays = _replays(installed)
            if args.json:
                print(json.dumps({"pack": args.pack, "installed_guards": installed, "status": "ok", "note": note,
                                  "replay": {g: {"checked": r["checked"], "flagged": len({h["commit"] for h in r["hits"]})}
                                             for g, r in replays.items()}}))
                return 0
            print(f"✅ Successfully installed pack '{args.pack}' ({len(installed)} guards, active as warnings):")
            for gid in installed:
                print(f"  • {gid}")
                if gid in replays:
                    print("      " + guard_proof.describe(replays[gid])[0])
            if note:
                print(f"note: {note}")
            print("The guards are in `.brevio/guards.json` and `.agents/rules/`. A pack guard is hand-written and starts "
                  "active, not on trial: it reports and does not fail a run unless brevio-core.toml raises it to an error.")
            return 0
        except KeyError as err:
            print(f"Error: {err}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
