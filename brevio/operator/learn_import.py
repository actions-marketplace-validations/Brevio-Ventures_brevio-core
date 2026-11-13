"""``learn import FILE``: bring the guards of a guards file from another repository, on trial (preview, decision D28, 2026-11-13).

A person sends a ``.brevio/guards.json`` to another team; this reads it from a path on this machine and nothing else: no
address and no git remote, and it contacts no service. Text from a file is not trusted, so each guard in it is rebuilt from the fields the
tool knows, goes through the credential check and the regular-expression check, and is replayed over the recent history of
this repository. The command prints how many past changes each would have flagged here, asks for a typed yes at a terminal,
and writes the guards as trial guards: they report and do not fail a run. An ID already in the file is never overwritten.

No agent rule is written at import. An agent rule is an instruction an agent reads, so it is written only when a person
promotes the guard (``learn promote``), which for an imported guard also needs a terminal. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from brevio.pipeline import core_platform
from brevio.pipeline.agent_host import config
from brevio.pipeline.gates import defect_compiler, guard_proof, guard_scan

MAX_FILE_BYTES = 1_000_000      # a guards file is read up to this size
MAX_PATTERNS = 20               # patterns a guard may hold, and globs
MAX_TEXT = 200                  # characters of one pattern, glob or title
CATEGORIES = ("security", "robustness", "functional", "compliance", "finops")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_REMOTE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://|[\w.-]+@[\w.-]+:)")    # a URL, or an scp-style git address
HIDDEN = "(hidden: it looks like a credential)"


def _texts(value: Any, limit: int = MAX_PATTERNS) -> list[str] | None:
    """``value`` as a non-empty list of short strings, or ``None``."""
    if not isinstance(value, list) or not 0 < len(value) <= limit:
        return None
    return list(value) if all(isinstance(v, str) and 0 < len(v) <= MAX_TEXT and v.isprintable() for v in value) else None


def _glob_ok(glob: str) -> bool:
    parts = glob.replace("\\", "/").split("/")
    return not (glob.startswith(("/", "~")) or ".." in parts or re.match(r"^[A-Za-z]:", glob))


def _check(guard_id: str, raw: Any) -> str:
    """Why the foreign entry ``raw`` cannot be a guard, or "" when its shape is sound."""
    if not _ID.match(guard_id):
        return "the ID is not made of letters, digits, `.`, `_` and `-` (at most 80)"
    if not isinstance(raw, dict):
        return "the entry is not an object"
    patterns, globs = _texts(raw.get("patterns")), _texts(raw.get("file_patterns"))
    if patterns is None or globs is None:
        return "it needs `patterns` and `file_patterns`, each a short list of short single-line text"
    if not all(_glob_ok(g) for g in globs):
        return "a watched path is absolute or leaves the repository"
    need = raw.get("file_contains")
    if need is not None and not (isinstance(need, str) and 0 < len(need) <= MAX_TEXT and need.isprintable()):
        return "`file_contains` is not short text on one line"
    return ""


def build(guard_id: str, raw: Any, source: str) -> tuple[dict[str, Any] | None, str]:
    """``(guard, "")`` rebuilt from the foreign entry ``raw``, or ``(None, why it is refused)``. Only known fields are
    taken: the status, counters, replay and switch-off record of another repository are not, and neither is ``label``,
    which would replace the pattern in a finding with words that nothing checks."""
    if why := _check(guard_id, raw):
        return None, why
    patterns, globs, need = list(raw["patterns"]), list(raw["file_patterns"]), raw.get("file_contains")
    guard: dict[str, Any] = {
        "id": guard_id, "title": guard_proof.printable(raw.get("title") or guard_id, MAX_TEXT),
        "category": raw["category"] if raw.get("category") in CATEGORIES else "robustness",
        "file_patterns": globs, "patterns": patterns, **({"file_contains": need} if need else {}),
        **({"match": guard_scan.MATCH_ALL} if raw.get("match") == guard_scan.MATCH_ALL else {}),    # a hit needs every pattern (merge of D45 and D28)
    }
    if guard_proof.credential_in({**guard, "patterns": patterns + ([need] if need else []), "source_file": ""}):
        return None, "a pattern looks like a credential, and guards are committed"
    risky = next((why for p in patterns if (why := guard_scan.regex_problem(p))), "")
    if risky:
        return None, f"a pattern is not run as the regular expression it reads as: {risky}"
    guard.update({
        "status": defect_compiler.STATUS_TRIAL, "trial_ends": defect_compiler._trial_end(), "runs_evaluated": 0,
        "false_fails": 0, "intercepted": 0, "rule_file": "",
        "imported": {"from": guard_proof.printable(source, 80), "at": defect_compiler._now_iso()[:10]},
        "created_at": defect_compiler._now_iso(), "updated_at": defect_compiler._now_iso(),
    })
    return guard, ""


def read_file(path: str) -> tuple[dict[str, Any], str]:
    """``(guards by ID, "")`` of the local file ``path``, or ``({}, why it cannot be used)``."""
    if _REMOTE.match(path):
        return {}, "only a file on this machine is read: no address and no git remote"
    if not os.path.isfile(path):
        return {}, f"{guard_proof.printable(path)} is not a file"
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return {}, "the file is larger than a guards file is"
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return {}, f"the file could not be read as JSON ({guard_proof.printable(exc)})"
    table = defect_compiler.guard_table(data)
    return ({str(k): v for k, v in table.items()}, "") if table else ({}, "the file holds no guards table")


def _ask(question: str) -> bool:
    """A yes typed by a person: the question is asked at a terminal only; anything else, and end of input, is no."""
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _show(guard: dict[str, Any], replay: dict[str, Any]) -> None:
    print(f"  {guard['id']}: {guard['title']}")
    print(f"    watches: {', '.join(guard['file_patterns'])}")
    print(f"    looks for: {', '.join(guard['patterns'])}" + (f" in files holding `{guard['file_contains']}`" if guard.get("file_contains") else ""))
    print("    " + "\n    ".join(guard_proof.describe(replay)))


def run(path: str | None, commits: int) -> int:
    """Import the guards of ``path``: 0 when guards were written, 1 when none were, 2 for a usage error."""
    if not (core_platform.is_terminal(sys.stdin) and core_platform.is_terminal(sys.stdout)):
        print("brevio-core: `learn import` writes guards, so it runs only at a terminal; it is refused when input or output "
              "is a pipe or a file", file=sys.stderr)
        return 2
    if not path:
        print("brevio-core: name a guards file on this machine: `brevio-core learn import FILE`", file=sys.stderr)
        return 2
    foreign, why = read_file(path)
    if why:
        print(f"brevio-core: {why}", file=sys.stderr)
        return 2
    root = config.WORKSPACE_DIR
    data = defect_compiler.load_guards(root)
    have = defect_compiler.guard_table(data)
    source = os.path.basename(path)
    keep: list[dict[str, Any]] = []
    for gid, raw in foreign.items():
        if gid in have:
            print(f"  skip {guard_proof.printable(gid)}: it is in {defect_compiler.GUARDS_DIR}/{defect_compiler.GUARDS_FILE} already; "
                  "`learn import` does not overwrite a guard")
            continue
        guard, refused = build(gid, raw, source)
        if guard is None:
            print(f"  refuse {guard_proof.printable(gid)}: {refused}")
            continue
        replay = guard_proof.replay(guard, root, commits)
        _show(guard, replay)
        keep.append({**guard, "replay": guard_proof.stored(replay, commits)})
    if not keep:
        print("brevio-core: no guard to import; nothing written")
        return 1
    if not _ask(f"Write {len(keep)} guard(s) to {defect_compiler.GUARDS_DIR}/{defect_compiler.GUARDS_FILE} on trial? [y/N] "):
        print("brevio-core: nothing written")
        return 1
    data.setdefault("guards", {})
    table = defect_compiler.guard_table(data)
    table.update({g["id"]: g for g in keep})
    defect_compiler.save_guards(data, root)
    print(f"{len(keep)} guard(s) are on trial: they report and do not fail a run. No agent rule is written until you "
          "`learn promote` a guard.")
    print(f"Commit {defect_compiler.GUARDS_DIR}/{defect_compiler.GUARDS_FILE} so your team gets them.")
    return 0


def imported(guard: dict[str, Any]) -> bool:
    """The guard came from ``learn import`` and has no agent rule yet."""
    return isinstance(guard.get("imported"), dict) and not guard.get("rule_file")


def write_rule(guard_id: str) -> str:
    """Write the agent rule of the imported guard ``guard_id`` that a person just promoted, and return the file that is
    now to commit; "" when the guard is not an imported one without a rule."""
    root = config.WORKSPACE_DIR
    data = defect_compiler.load_guards(root)
    guard = defect_compiler.guard_table(data).get(guard_id)
    if not isinstance(guard, dict) or not imported(guard):
        return ""
    written = defect_compiler._write_agent_rule(guard_id, guard["title"], guard.get("category", "robustness"),
                                                guard["file_patterns"], guard["patterns"], cwd=root)
    guard["rule_file"] = os.path.relpath(written, root).replace(os.sep, "/")
    defect_compiler.save_guards(data, root)
    return str(guard["rule_file"])
