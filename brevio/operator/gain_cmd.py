"""The ledger counts: `cli.py gain` in the harness, `brevio-core gain` in the package."""
from __future__ import annotations

import argparse
import json
import sys

from brevio.pipeline import core_cli
from brevio.pipeline.gates import dopamine_ledger


def main(argv: list[str] | None = None, prog: str = "cli.py gain") -> int:
    """Print the counts; ``prog`` is how the user types the command.

    Until 2026-11-12 this printed a signed receipt link, a badge, an AEO score of 100 that was typed in the code, and a count of
    "PR rules" that was the catalogue size, not the number of rules the scan checks. It now prints what the ledger holds and
    the scanned count (improvement plan, 2026-11-12)."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Show what the local ledger recorded: the hits `learn --check` found, learned guards, and savings when measured.",
    )
    parser.add_argument("--json", action="store_true", help="Output metrics as JSON")
    if core_cli.up_tools() is not None:     # the 0.6 set: the closing Teams line (ROADMAP E37, 2026-11-05)
        parser.add_argument("--no-upgrade-line", action="store_true", help="leave out the closing Teams line")

    args = parser.parse_args(argv)
    summary = dopamine_ledger.get_gain_summary()

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print("brevio-core gain: what this repository's ledger recorded")
    print(f"  Learned-guard hits recorded: {summary['total_interceptions']:,}")
    print(f"  Learned guards: {summary['active_learned_guards']:,} active, {summary['trial_learned_guards']:,} on trial")
    print(f"  The scan checks {summary['rules_scanned']} rules (of {summary['rules_documented']} documented).")
    if summary["savings_measured"]:
        print(f"  Tokens saved (measured): {summary['tokens_saved']:,}")
        print(f"  Spend saved (measured):  ${summary['cost_saved_usd']:,.2f} USD")
    print("  A hit is recorded when `learn --check` finds a guard's line; a hit that `scan` or the agent hook reports, and a finding of the built-in rules, is not.")
    teams = core_cli.upgrade_line("gain", not getattr(args, "no_upgrade_line", False))
    if teams:
        print(teams)
    return 0


if __name__ == "__main__":
    sys.exit(main())
