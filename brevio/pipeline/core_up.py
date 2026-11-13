"""``brevio-core up``: what a team sync would take from this repository (ROADMAP E37, 2026-11-05).

The 0.6 set ships ``up`` as a preview. It lists each file a sync would take (the learned guards, the savings counts and
the agent rules ``learn`` writes), with its size and the fields taken from it. It uploads nothing, makes no link that
carries data, and holds no network code, so it is under the same no-network test as ``scan``. The sending ``up`` is not
part of this release: brevio.pro has no page that receives a sync yet (``/onboard`` ignores the import,
``/teams`` is not there; checked 2026-11-05). Until a page exists that this repository can show, no link to it is printed
(2026-11-12, improvement plan). When it exists, the sending command can ship here with the consent prompt PRIVACY.md documents.

This module also holds the one labelled upgrade line that ``scan --format markdown`` (the pull request comment) and
``gain`` end with, unless ``--no-upgrade-line`` is given. It never reaches an agent: the hook and the MCP server print
the agent lines only. Standard library only, and only builds that ship ``up`` print the line.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

from brevio.pipeline import agent_host
from brevio.pipeline.core_memory import MEMORY_DIR, MEMORY_FILE
from brevio.pipeline.gates import defect_compiler, dopamine_ledger
from brevio.pipeline.rule_host import GIT

HELP = "show what a team sync would take from this repository (uploads nothing)"
# What a sync would take from the savings ledger: counts, no events.
STATS_FIELDS = ("total_interceptions", "savings_measured", "tokens_saved", "cost_saved_usd")
WHOLE_TEXT = "the whole text"


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run([*GIT, "-C", cwd, *args],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def recent_authors(workspace: str) -> int:
    """Distinct author emails across the last 5 commits, or 0 if not a git repo."""
    authors: set[str] = set()
    for ref in ("HEAD", "HEAD~1", "HEAD~2", "HEAD~3", "HEAD~4", "HEAD^2"):
        if not _git(workspace, "rev-parse", "--verify", "-q", ref).strip():
            continue
        email = _git(workspace, "show", "-s", "--format=%ae", ref).strip().lower()
        if email:
            authors.add(email)
    return len(authors)


def upgrade_line(ref: str) -> str:
    """The one labelled line that says what Teams would be and that it is not built. It holds no link: no page that this
    repository can show exists yet, and no number or absolute claim. ``ref`` says where it is shown and is not printed."""
    return ("Teams: central sharing of learned guards and suppression approvals across an organisation is not built yet; "
            "`brevio-core up` lists what it would take.")


def _guard_fields(path: str) -> str:
    """The fields of the stored guards in ``path``, as a sync would take them."""
    try:
        with open(path, encoding="utf-8") as fh:
            guards = json.load(fh).get("guards", {})
    except (OSError, ValueError, AttributeError):
        guards = {}
    keys = sorted({k for g in guards.values() if isinstance(g, dict) for k in g}) if isinstance(guards, dict) else []
    return "each learned guard as stored" + (f": {', '.join(keys)}" if keys else "")


def planned_files(workspace: str) -> list[dict[str, Any]]:
    """Each file a sync would take from ``workspace``: ``path``, ``bytes`` and the ``fields`` taken. Sends nothing."""
    guards = os.path.join(defect_compiler.GUARDS_DIR, defect_compiler.GUARDS_FILE)
    stats = os.path.join(dopamine_ledger.STATS_DIR, dopamine_ledger.STATS_FILE)
    memory = os.path.join(MEMORY_DIR, MEMORY_FILE).replace(os.sep, "/")
    wanted = [
        (guards, _guard_fields(os.path.join(workspace, guards))),
        (stats, ", ".join(STATS_FIELDS)),
        (memory, "each recorded file trap-to-fix episode"),
    ]
    rules_dir = os.path.join(workspace, defect_compiler.RULES_DIR)
    if os.path.isdir(rules_dir):
        wanted += [(os.path.join(defect_compiler.RULES_DIR, f), WHOLE_TEXT)
                   for f in sorted(os.listdir(rules_dir)) if f.endswith(".md")]
    return [{"path": p.replace(os.sep, "/"), "bytes": os.path.getsize(os.path.join(workspace, p)), "fields": fields}
            for p, fields in wanted if os.path.isfile(os.path.join(workspace, p))]


def render(files: list[dict[str, Any]], workspace: str | None = None) -> str:
    lines = ["brevio-core up shows what a team sync would take from this repository. Team sync is not available yet:",
             "this run uploads nothing and makes no link.", "", "A sync would take these files:", ""]
    lines += [f"  {f['path']} ({f['bytes']:,} bytes): {f['fields']}" for f in files]
    if not files:
        lines.append("  none yet (`brevio-core learn` writes the guards a sync would share)")
    if workspace:
        authors = recent_authors(workspace)
        if authors > 1:
            lines += ["", f"Note: {authors} recent contributors are active on this repository without shared guard sync."]
    lines += ["", "and the repository name only as a salted hash. When the sync ships, it asks before anything is sent,",
              "as PRIVACY.md describes."]
    return "\n".join(lines) + "\n"


def main(argv: list[str], prog: str = "brevio-core up") -> int:
    ap = argparse.ArgumentParser(prog=prog, description=HELP[0].upper() + HELP[1:] + ".")
    ap.add_argument("--json", action="store_true", help="print the files, sizes and fields as JSON")
    args = ap.parse_args(argv)
    ws = agent_host.config.WORKSPACE_DIR     # read now: the harness binds its own workspace
    files = planned_files(ws)
    if args.json:
        print(json.dumps({"available": False, "files": files, "recent_authors": recent_authors(ws)}, indent=2))
    else:
        sys.stdout.write(render(files, workspace=ws))
    return 0
