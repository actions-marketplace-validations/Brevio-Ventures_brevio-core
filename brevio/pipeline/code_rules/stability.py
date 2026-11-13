"""Rule IDs are permanent (ROADMAP E33, the 0.6 set, 2026-11-05).

``rule_ids.json`` beside this file records each rule ID of the last release with its catalogue line (its title) and its
status. Against it, an ID may never disappear (a retired rule stays in the catalogue as ``deprecated``), never come back
with another title (that would be a new rule under an old ID), and never leave ``deprecated``; a rule the file does not
hold yet must ship as ``preview``. The release notes list what was added, changed and retired since that file. A title that promised more than its scan checks
may be reworded through ``REWORDED`` below, which names the recorded line it replaces. After a
release, record the new IDs with ``--write`` and commit the file. Standard library only, so it exports with the rules.

    python -m brevio.pipeline.code_rules.stability --check   list a removed, reused or revived ID
    python -m brevio.pipeline.code_rules.stability --write   record the current IDs (after a release)
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
# that old line, and the entry stops mattering once ``--write`` records the new title. The ID and the status do not change;
# a rule that finds more or less than 0.9.1 says so in ``RULE_NOTES``, and one whose findings change meaning gets a new ID
# (RULES-STABILITY.md).
REWORDED: dict[str, str] = {
    "R1.2": ("SSR visibility resilience: never leave [data-reveal] at opacity:0 without CSS animation "
              "fallback"),
    "R1.4": "Zero-flash theme bootstrap: paired semantic tokens across light/dark modes",
    "R1.5": "No dead controls: every interactive element wires a handler and loading/error feedback",
    "R2.1": ("Tenant context derivation: derive tenant context from verified session, never client "
              "payload"),
    "R2.2": "Tenant-scoped data access: every DB read/write filters by authenticated workspace scope",
    "R2.8": ("No unsafe code or HTML sinks: no eval or new Function on strings; "
              "dangerouslySetInnerHTML only with sanitised HTML"),
    "R4.5": "Bounded pagination: enforce hard LIMIT (<=100) on every list query",
    "R5.1": ("Deterministic graceful fallbacks: return explicit degraded status, never fabricated "
              "metrics"),
    "R5.4": "No silent exception swallowing: log or surface errors; never use empty catch blocks",
    "R5.5": "Process & lock safety: every subprocess.run carries timeout=; locks use try/finally",
    "R6.3": "Deterministic condition waits: never use waitForTimeout/sleep in tests",
    "R6.5": "Post-incident regression crystallization: every bug fix includes a failing-first test",
    "R8.2": "Honest telemetry attribution: label synthetic/demo data; never mix with live metrics",
    "R9.2": "No silenced type errors: no @ts-ignore; every @ts-expect-error states its reason",
    "R9.3": "Package-name imports: import workspace packages by name, never via ../../packages/ paths",
    "R10.1": "Explicit HTTP request timeouts: every fetch/axios call specifies timeout or AbortSignal",
    "R10.8": "Strict HTTP verb mapping: GET requests must be strictly read-only and side-effect free",
    "R13.1": "Zero secret logging: redact passwords, tokens, API keys, and authorization headers",
    "R14.5": "No credentials in URLs: tokens, API keys, and passwords never passed in query strings",
    "R16.1": ("Lockfile integrity enforcement: CI installs strictly via frozen lockfiles (pnpm "
              "--frozen)"),
    "R16.2": "No wildcard dependency versions: package.json avoids '*' or unbounded range dependencies",
    "R17.5": "Next/Font optimization: load Google/local fonts via next/font with zero layout shift",
    "R18.1": "Meaningful image alternative text: informative images supply descriptive alt attribute",
    "R18.2": "Form control labels: every input, select, and textarea element has associated label",
    "R18.5": ("No click handlers on non-interactive elements: divs/spans with onClick specify "
              "role='button'"),
}


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--check", action="store_true", help="list a removed, reused or revived ID; exit 1 if there is one")
    what.add_argument("--write", action="store_true", help="record the current IDs in the ID file (after a release)")
    args = ap.parse_args(argv)
    if args.write:
        with open(IDS_FILE, "w", encoding="utf-8") as fh:
            fh.write(render(current()))
        print(f"recorded {len(PR_RULES_122)} rule IDs in {IDS_FILE}")
        return 0
    bad = problems(load(), current())
    print("\n".join(bad) if bad else "rule IDs keep their meaning")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
