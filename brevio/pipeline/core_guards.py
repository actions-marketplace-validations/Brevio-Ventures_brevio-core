"""The learned guards in the scan: ``scan``, the agent hook, the MCP ``scan`` tool and the Action report them.

``brevio-core learn`` writes ``.brevio/guards.json``. Until 2026-11-12 only ``learn --check`` read it. Now a scan
that reads the working tree also reads the guards (``core_cli.scan`` loads this module by name, 0.2 set and later;
improvement plan, build 1a):

- A hit is a finding with the rule ``guard:<id>``. Baselines, ignore comments and ``brevio-core.toml`` treat it like the
  finding of a rule.
- A guard on trial is reported only: it does not fail the run. An active guard is a warning, which does not fail it either,
  unless ``brevio-core.toml`` raises it to an error (``[rules."guard:<id>"] level = "error"``).
- The scan only reads the file. A hit changes no counter and writes nothing.

The other half is the change check: ``weakened`` compares the guards of a base with the working tree and names the guards
that were removed, switched off or narrowed without the record ``learn dismiss`` leaves. That record is part of the file,
so it is something a reviewer reads, not a lock: whoever can write the file can write it. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from typing import Any

from brevio.pipeline.gates import defect_compiler, guard_scan

PREFIX = "guard:"
GUARDS_PATH = f"{defect_compiler.GUARDS_DIR}/{defect_compiler.GUARDS_FILE}"
OWN_RULES = ".agents/rules/"
MAX_TEXT = 60       # characters of a title or pattern a finding quotes
SUPPRESSION_RULE = "suppression"
SUPPRESSION_MESSAGE = "new suppression: needs a human review"

Numbered = dict[str, list[tuple[int, str]]]
_RANK = {defect_compiler.STATUS_TRIAL: 1, defect_compiler.STATUS_ACTIVE: 2}


def _safe(guard_id: str) -> str:
    """A guard ID as it may appear in a finding: the file is part of the change under review."""
    return "".join(c if c.isalnum() or c in "_.-" else "-" for c in str(guard_id))


def _oneline(text: Any, limit: int = MAX_TEXT) -> str:
    out = " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())
    return out if len(out) <= limit else out[:limit - 3].rstrip() + "..."


def _guards_of(text: str) -> tuple[dict[str, dict[str, Any]], str]:
    """``(guards by ID, "")`` of a guards file's text, or ``({}, why it could not be read)``."""
    if not text.strip():
        return {}, ""
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return {}, f"{GUARDS_PATH} is not JSON ({exc})"
    raw = doc.get("guards") if isinstance(doc, dict) else None
    if not isinstance(raw, dict):
        return {}, f"{GUARDS_PATH} holds no guards table"
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}, ""


_CACHE: dict[str, tuple[tuple[int, int], tuple[dict[str, dict[str, Any]], str]]] = {}


def _load(root: str) -> tuple[dict[str, dict[str, Any]], str]:
    """``(guards by rule ID, problem)`` of the file under ``root``. A finding asks for its guard's status and level, so
    the parse is kept until the file's size or modification time changes: a scan of a file with thousands of hits took
    most of its time re-reading the file (2026-11-12, improvement plan)."""
    path = os.path.abspath(os.path.join(root, defect_compiler.GUARDS_DIR, defect_compiler.GUARDS_FILE))
    try:
        stat = os.stat(path)
    except OSError:
        return {}, ""
    sig = (stat.st_mtime_ns, stat.st_size)
    if (hit := _CACHE.get(path)) is not None and hit[0] == sig:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as fh:
            guards, problem = _guards_of(fh.read())
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"{GUARDS_PATH} could not be read ({exc})"
    result = ({rule_of(k): {**v, "id": _safe(k)} for k, v in guards.items()}, problem)
    _CACHE[path] = (sig, result)
    return result


def rule_of(guard_id: str) -> str:
    """The rule ID a guard's findings carry."""
    return PREFIX + _safe(guard_id)


def is_guard_rule(rule: str) -> bool:
    return rule.startswith(PREFIX)


def status_of(rule: str, root: str = ".") -> str:
    """``trial``, ``active`` or ``off`` for the guard behind a finding's rule; "" when there is none."""
    guard = _load(root)[0].get(rule)
    if guard is None:
        return ""
    return str(guard.get("status")) if guard.get("status") in defect_compiler.LIVE else defect_compiler.STATUS_OFF


def known_rules(root: str = ".") -> list[str]:
    """The rule IDs of the guards in the file, as written and in capitals (an ignore comment's IDs are capitals)."""
    rules = list(_load(root)[0])
    return rules + [r.upper() for r in rules]


def problems(root: str = ".") -> list[str]:
    """Why the guards file could not be used, as a list of one, and each live guard pattern that is not run as the regular
    expression it reads as; the scan warns and goes on."""
    guards, why = _load(root)
    notes = [f"guard {_oneline(rule)}: the pattern `{_oneline(p, 40)}` is not run as a regular expression: {reason}"
             for rule, g in guards.items() if g.get("status") in defect_compiler.LIVE
             for p in g.get("patterns", []) if isinstance(p, str) and (reason := guard_scan.regex_problem(p))]
    return ([why] if why else []) + notes


def level(rule: str, where: Any, root: str = ".") -> str:
    """``warn`` or ``error`` for a guard's finding. ``where`` is what ``brevio-core.toml`` says for the file (a
    ``PathConfig``) or ``None``. A guard on trial is ``warn``; an active one is ``warn`` unless the config
    names it."""
    if status_of(rule, root) != defect_compiler.STATUS_ACTIVE or where is None:
        return "warn"
    return str(where.level(rule, "warn"))


def _message(guard: dict[str, Any], label: str) -> str:
    title = _oneline(guard.get("title") or guard.get("id", ""))
    # A starter pack's guard says what it looks for in words (``label``) and is not called learned (2026-11-13, packs).
    held = _oneline(guard["label"]) if isinstance(guard.get("label"), str) and guard["label"] else f"`{_oneline(label)}`"
    if guard.get("status") == defect_compiler.STATUS_TRIAL:
        return f'trial guard "{title}" (reported only): the line holds {held}'
    return f'{"pack" if guard.get("pack") else "learned"} guard "{title}": the line holds {held}'


def _whole_file(root: str, path: str, numbered: list[tuple[int, str]]) -> list[str]:
    """The lines of the file for a guard's ``file_contains``: the scan may hold only the lines a diff adds, so the file on
    disk is read when it can be, and the numbered lines are the fallback (a file not on disk)."""
    try:
        with open(os.path.join(root, path), encoding="utf-8", errors="ignore") as fh:
            return fh.read().splitlines()
    except OSError:
        return [text for _n, text in numbered]


def findings(files: Numbered, root: str = ".") -> list[str]:
    """One finding (``path:line: [guard:<id>] message``) for the lines of ``files`` that holds a pattern of a live guard
    the path is watched by."""
    live = {rule: g for rule, g in _load(root)[0].items() if g.get("status") in defect_compiler.LIVE}
    if not live:
        return []
    pats = {rule: guard_scan.compile_patterns(g) for rule, g in live.items()}
    out: list[str] = []
    for path, numbered in sorted(files.items()):
        if path == GUARDS_PATH or path.startswith(OWN_RULES):
            continue        # a guard's own file and rule file hold its patterns: they are not a hit
        texts = _whole_file(root, path, numbered) if any(g.get("file_contains") for g in live.values()) else []
        watching = [rule for rule, g in live.items() if guard_scan.watches(g, path) and guard_scan.file_ok(g, texts)]
        if not watching:
            continue
        rows = [(n, guard_scan.fold(text)) for n, text in numbered]
        for rule in watching:
            out += [f"{path}:{lno}: [{rule}] {_message(live[rule], label)}"
                    for lno, label in guard_scan.matching_rows(pats[rule], rows, guard_scan.needs_all(live[rule]))]
    return out


def fingerprint(path: str, rule: str, line_text: str) -> str:
    """A hit's identity for code scanning: the path, the rule and the text of the line, not its number, so a hit that
    moves is the same hit (the key the baseline uses)."""
    line = hashlib.sha256(line_text.strip().encode("utf-8")).hexdigest()
    return hashlib.sha256(f"{path}\0{rule}\0{line}".encode()).hexdigest()


def fingerprints(fields: dict[str, Any], text_of: Callable[[str, int], str]) -> dict[str, dict[str, str]]:
    """The SARIF ``partialFingerprints`` of a guard's finding, or nothing for another kind of finding."""
    rule = str(fields["rule"])
    if not is_guard_rule(rule):
        return {}
    path = str(fields["path"])
    return {"partialFingerprints": {"guardHit/v1": fingerprint(path, rule, text_of(path, int(fields["line"])))}}


def _weaker(before: dict[str, Any], after: dict[str, Any]) -> str:
    """How ``after`` is weaker than the live guard ``before``, or "" when it is not."""
    if after.get("status") not in defect_compiler.LIVE:
        record = after.get("dismissed")
        recorded = isinstance(record, dict) and record.get("was") == defect_compiler.fingerprint(before)
        return "" if recorded else "switched off without `brevio-core learn dismiss`"
    if set(map(str, before.get("patterns", []))) - set(map(str, after.get("patterns", []))):
        return "a pattern was taken out"
    if guard_scan.needs_all(after) and not guard_scan.needs_all(before):
        return "all its patterns are now needed for a hit"
    if set(map(str, before.get("file_patterns", []))) - set(map(str, after.get("file_patterns", []))):
        return "a watched path was taken out"
    if _RANK.get(str(after.get("status")), 0) < _RANK.get(str(before.get("status")), 0):
        return "put back on trial"
    return ""


def weakened(base_text: str, now_text: str) -> list[tuple[int, str]]:
    """``(line, " (guard <id>: what changed)")`` for each live guard of ``base_text`` that ``now_text`` removes, switches
    off without a ``learn dismiss`` record, narrows or puts back on trial. The line is where the guard is now."""
    before, now, rows = _guards_of(base_text)[0], _guards_of(now_text)[0], now_text.splitlines()
    out = []
    for gid, guard in before.items():
        if guard.get("status") not in defect_compiler.LIVE:
            continue
        why = "removed" if gid not in now else _weaker(guard, now[gid])
        if why:
            lno = next((i for i, row in enumerate(rows, 1) if json.dumps(gid) in row), 1)
            out.append((lno, f" (guard {_oneline(gid)}: {why})"))
    return out


def changes(base_text: str, now_text: str) -> list[str]:
    """A ``suppression`` finding for the guards ``weakened`` names: it needs a person's review, like a new ignore comment.

    The finding has the form ``core_baseline.suppression`` gives, which a test compares (the baseline is of a later set,
    so it is not imported here)."""
    return [f"{GUARDS_PATH}:{lno}: [{SUPPRESSION_RULE}] {SUPPRESSION_MESSAGE}{detail}" for lno, detail in weakened(base_text, now_text)]
