"""Rule IDs are permanent (ROADMAP E33, the 0.6 set, 2026-11-05).

``rule_ids.json`` beside this file records each rule ID of the last release with its catalogue line (its title) and its
status. Against it, an ID may never disappear (a retired rule stays in the catalogue as ``deprecated``), never come back
with another title (that would be a new rule under an old ID), and never leave ``deprecated``; a rule the file does not
hold yet must ship as ``preview``. The release notes list what was added, changed and retired since that file. A title that promised more than its scan checks
may be reworded through ``REWORDED`` below, which names the recorded line it replaces. After a
release, record the new IDs with ``--write`` and commit the file. Standard library only, so it exports with the rules.

    python -m brevio.pipeline.code_rules.stability --check   list a removed, reused or revived ID
    python -m brevio.pipeline.code_rules.stability --write   record the current IDs (after a release); it refuses a
                                                             catalogue that breaks the policy, unless --force
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from brevio.pipeline.code_rules import DEPRECATED, PR_RULES_122, PREVIEW, RULE_NOTES, STATUSES, rule_status
from brevio.pipeline.code_rules.docs import rule_ids

IDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rule_ids.json")
FORMAT = 1
REWORDED_HEADING = "Reworded (the ID and the status are unchanged; a note below says where the findings changed)"
Rules = dict[str, dict[str, str]]        # rule ID -> {"title": catalogue line, "status": status}
# A title that promised more than its scan checks, reworded to what the scan does (2026-11-13). Rule ID -> the line the
# ID file holds, which is the line the last release published. ``problems`` accepts the new title only against exactly
# that old line, and the entry stops mattering once ``--write`` records the new title; the test of the ID file fails
# while an entry outlives that. The ID and the status do not change; a rule that finds more or less than the last release
# says so in ``RULE_NOTES``, and one whose findings change meaning gets a new ID (RULES-STABILITY.md). Empty since 0.10.0
# was released and recorded (2026-11-13): its 25 rewordings are the recorded lines now.
REWORDED: dict[str, str] = {}


def current() -> Rules:
    """Every rule of the catalogue with its title and status, in catalogue order."""
    return {rid: {"status": rule_status(rid), "title": PR_RULES_122[rid]} for rid in rule_ids()}


def load(path: str = IDS_FILE) -> Rules:
    """The rules a recorded ID file holds; a broken file raises ``ValueError``."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    rules = doc.get("rules") if isinstance(doc, dict) and doc.get("format") == FORMAT else None
    if not isinstance(rules, dict) or not all(isinstance(v, dict) and isinstance(v.get("title"), str)
                                              and v.get("status") in STATUSES for v in rules.values()):
        raise ValueError(f"{path} is not a format {FORMAT} rule ID file")
    return rules


def render(rules: Rules) -> str:
    """The ID file: one rule per line, in catalogue order, so a change reads as a one-line diff."""
    rows = [f"  {json.dumps(rid)}: {json.dumps(entry, sort_keys=True)}" for rid, entry in rules.items()]
    body = ",\n ".join(rows)
    return f'{{\n "format": {FORMAT},\n "rules": {{\n {body}\n }}\n}}\n'


def problems(before: Rules, now: Rules) -> list[str]:
    """What breaks the ID policy between the recorded rules ``before`` and the catalogue ``now``."""
    out = [f"{rid}: has no valid status ({entry['status']!r})" for rid, entry in now.items() if entry["status"] not in STATUSES]
    for rid, old in before.items():
        new = now.get(rid)
        if new is None:
            out.append(f"{rid}: removed; a retired rule stays in the catalogue with the status deprecated")
        elif new["title"] != old["title"] and REWORDED.get(rid) != old["title"]:
            out.append(f"{rid}: reused with another meaning: was {old['title']!r}, now {new['title']!r}; "
                       "give the new rule a new ID")
        elif old["status"] == DEPRECATED and new["status"] != DEPRECATED:
            out.append(f"{rid}: was deprecated; a retired ID never comes back")
    out += [f"{rid}: a new rule ships as preview first, not {entry['status']}"
            for rid, entry in now.items() if rid not in before and entry["status"] != PREVIEW]
    return out


def changes(before: Rules, now: Rules) -> dict[str, list[str]]:
    """``added``, ``changed`` (a status moved, other than to deprecated) and ``retired``, one line per rule."""
    added = [f"{rid} {entry['title']} ({entry['status']})" for rid, entry in now.items() if rid not in before]
    changed, retired = [], []
    for rid, entry in now.items():
        old = before.get(rid)
        if old is None or old["status"] == entry["status"]:
            continue
        if entry["status"] == DEPRECATED:
            retired.append(f"{rid} {entry['title']} (deprecated; the ID stays reserved)")
        else:
            changed.append(f"{rid} {entry['title']} ({old['status']} -> {entry['status']})")
    return {"added": added, "changed": changed, "retired": retired}


def notes(before: Rules, now: Rules, extra: dict[str, str] | None = None) -> str:
    """The rules section of the release notes, with a ``Notes`` list from ``RULE_NOTES`` (or ``extra``)."""
    found = changes(before, now)
    parts = [(title, found[key]) for title, key in (("Added", "added"), ("Changed", "changed"), ("Retired", "retired"))
             if found[key]]
    reworded = [f"{rid} {now[rid]['title']}" for rid in now
                if rid in before and now[rid]["title"] != before[rid]["title"] and REWORDED.get(rid) == before[rid]["title"]]
    if reworded:
        parts.append((REWORDED_HEADING, reworded))
    noted = [f"{rid} {now[rid]['title']}: {line}"
             for rid, line in sorted((RULE_NOTES if extra is None else extra).items()) if rid in now]
    body = ("\n\n".join(f"{title}:\n\n" + "\n".join(f"- {row}" for row in rows) for title, rows in parts) if parts
            else "No rule was added, changed or retired since the last release.")
    if noted:
        body += "\n\nNotes:\n\n" + "\n".join(f"- {row}" for row in noted)
    return f"## Rules\n\n{body}\n"


def _recorded_problems() -> list[str]:
    """The policy problems of the catalogue against the ID file; none for the first ``--write``, when the file is missing
    or empty. A file that holds something else is a problem: it is not overwritten unseen."""
    if not os.path.exists(IDS_FILE) or os.path.getsize(IDS_FILE) == 0:
        return []
    try:
        before = load(IDS_FILE)
    except ValueError as exc:
        return [str(exc)]
    return problems(before, current())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--check", action="store_true", help="list a removed, reused or revived ID; exit 1 if there is one")
    what.add_argument("--write", action="store_true", help="record the current IDs in the ID file (after a release)")
    ap.add_argument("--force", action="store_true", help="with --write: record a catalogue that breaks the ID policy")
    args = ap.parse_args(argv)
    if args.write:
        bad = _recorded_problems()
        if bad and not args.force:      # a removed ID or a reused title would become the record and a later --check would pass
            print("\n".join(bad) + "\nnot recorded: fix the catalogue, or pass --force to record it as it is")
            return 1
        with open(IDS_FILE, "w", encoding="utf-8") as fh:
            fh.write(render(current()))
        print(f"recorded {len(PR_RULES_122)} rule IDs in {IDS_FILE}")
        return 0
    bad = problems(load(), current())
    print("\n".join(bad) if bad else "rule IDs keep their meaning")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
