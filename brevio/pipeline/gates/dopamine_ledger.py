"""The local ledger behind `cli.py gain` (Master Growth Plan G13 / S7).

Counts the hits a learned guard recorded in this repository (``guard_scan`` writes them). A hit is a count. Tokens or money saved are recorded only when the caller passes a measured value (for
example the real token cost of the retry the hook prevented); there is no per-interception estimate
(owner decision, 2026-11-04). It builds no receipt link: the signed link of this module's first version used a key
that sat in the source, so anyone could have made one (2026-11-12, improvement plan).
"""
from __future__ import annotations

import datetime
import os
import re
import subprocess
from typing import Any

from brevio.pipeline.agent_host import config, gitsafe, jsonstore
from brevio.pipeline.code_rules import PR_RULES_122
from brevio.pipeline.code_rules.details import scanned_by
from brevio.pipeline.gates import defect_compiler

STATS_DIR = ".brevio"
STATS_FILE = "stats.json"


def get_stats_path(cwd: str | None = None) -> str:
    root = cwd or config.WORKSPACE_DIR
    return os.path.join(root, STATS_DIR, STATS_FILE)


def load_stats(cwd: str | None = None) -> dict[str, Any]:
    path = get_stats_path(cwd)
    data = jsonstore.read_or(path, {
        "version": 2,
        "total_interceptions": 0,
        "measured_events": 0,
        "tokens_saved": 0,
        "cost_saved_usd": 0.0,
        "events": [],
    })
    if data.get("version", 1) < 2:      # v1 files carried a fixed estimate per interception: drop it
        data = data | {"version": 2, "measured_events": 0, "tokens_saved": 0, "cost_saved_usd": 0.0}
    return data


def _keep_out_of_commits(cwd: str | None) -> None:
    """Add the stats file to git's local exclude file (``.git/info/exclude``), as ``init --personal`` does: it is this
    machine's count with timestamps, and an untracked file invites a commit by accident (2026-11-13, D52). Nothing happens
    outside a git work tree, and nothing is written when git cannot say where the file is."""
    root = cwd or config.WORKSPACE_DIR
    try:
        proc = subprocess.run([*gitsafe.GIT, "rev-parse", "--git-path", "info/exclude", "--show-prefix"], cwd=root,
                              capture_output=True, encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return
    where = proc.stdout.splitlines()
    if proc.returncode != 0 or len(where) < 2:
        return
    exclude = os.path.join(root, where[0])
    # the exclude file reads ``[`` ``*`` ``?`` as patterns: a folder such as ``app/[id]`` is written with them escaped
    entry = "/" + re.sub(r"([\\\[\]*?])", r"\\\1", f"{where[1]}{STATS_DIR}/{STATS_FILE}") + "\n"
    try:
        with open(exclude, encoding="utf-8", errors="replace") as fh:
            held = fh.read()
    except FileNotFoundError:
        held = ""
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
    except OSError:
        return
    if entry.strip() in held.splitlines():
        return
    try:
        with open(exclude, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(("" if not held or held.endswith("\n") else "\n")
                     + f"# brevio-core: a count kept on this machine, not for commits\n{entry}")
    except OSError:
        return


def save_stats(data: dict[str, Any], cwd: str | None = None) -> None:
    path = get_stats_path(cwd)
    first = not os.path.exists(path)
    jsonstore.write(path, data, indent=2, lock=False)
    if first:
        _keep_out_of_commits(cwd)


def record_interception(
    rule_id: str,
    file_path: str,
    tokens_saved: int | None = None,
    cost_saved_usd: float | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Record a defect intercepted by zero-token rules/guards.

    ``tokens_saved`` and ``cost_saved_usd`` are the caller's measured values; left out, only the
    count moves.
    """
    data = load_stats(cwd)
    events = data.setdefault("events", [])

    event = {
        "rule_id": rule_id,
        "file": file_path,
        "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    data["total_interceptions"] = int(data.get("total_interceptions", 0)) + 1
    if tokens_saved is not None or cost_saved_usd is not None:
        event["tokens_saved"] = int(tokens_saved or 0)
        event["cost_saved_usd"] = float(cost_saved_usd or 0.0)
        data["measured_events"] = int(data.get("measured_events", 0)) + 1
        data["tokens_saved"] = int(data.get("tokens_saved", 0)) + event["tokens_saved"]
        data["cost_saved_usd"] = round(float(data.get("cost_saved_usd", 0.0)) + event["cost_saved_usd"], 2)
    events.append(event)

    save_stats(data, cwd)
    return event


def get_gain_summary(cwd: str | None = None) -> dict[str, Any]:
    """The counts ``gain`` prints: what the ledger recorded, the guards on file, the rules the scan checks. Savings are
    given only when a caller measured them. Nothing here is a score, and the counts of rules are the code's own."""
    stats = load_stats(cwd)
    guards = defect_compiler.load_guards(cwd).get("guards", {})
    statuses = [g.get("status") for g in guards.values() if isinstance(g, dict)]
    interceptions = int(stats.get("total_interceptions", 0))
    measured = int(stats.get("measured_events", 0)) > 0   # v1 files held estimates: they never count
    return {
        "total_interceptions": interceptions,
        "active_learned_guards": statuses.count("active"),
        "trial_learned_guards": statuses.count("trial"),
        "rules_scanned": len(scanned_by("code")),
        "rules_documented": len(PR_RULES_122),
        "savings_measured": measured,
        "tokens_saved": int(stats.get("tokens_saved", 0)) if measured else 0,
        "cost_saved_usd": float(stats.get("cost_saved_usd", 0.0)) if measured else 0.0,
    }
