"""Zero-Token Defect-to-Guard Compiler (Master Growth Plan G11 / S7).

Compiles bug fixes and git diffs into:
1. Zero-Token Guards saved in `.brevio/guards.json`
2. Progressive-Disclosure Agent Rules in `.agents/rules/<id>.md` with `paths:` YAML frontmatter
3. The guard's life: a trial with an end date, promotion by a person after a clean replay, a person's switch-off, and
   a switch-off after wrong hits a person marked (2026-11-12, improvement plan, builds 1b and 1d)
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
from typing import Any

from brevio.pipeline.agent_host import config, gitsafe, jsonstore

GUARDS_DIR = ".brevio"
GUARDS_FILE = "guards.json"
RULES_DIR = os.path.join(".agents", "rules")
STATUS_TRIAL, STATUS_ACTIVE, STATUS_OFF = "trial", "active", "off"
LIVE = (STATUS_TRIAL, STATUS_ACTIVE)    # the statuses that report; ``retired`` of an older file reads as off
TRIAL_DAYS = 14         # how long a new guard reports only, before a person may promote it
WRONG_LIMIT = 3         # hits a person marks wrong before the guard is switched off
MIN_PATTERN = 8         # characters a removed line needs to be kept as a pattern
TITLE_CHARS = 40        # the pattern text a default title keeps, cut at a word


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trial_end(days: int = TRIAL_DAYS) -> str:
    """The date a new guard's trial ends."""
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def short_title(text: str, limit: int = TITLE_CHARS) -> str:
    """``text`` cut at the last whole word within ``limit`` characters, with "..." when it was cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    head = cut.rsplit(" ", 1)[0] if " " in cut and text[limit] != " " else cut
    return head.rstrip(" ,;:(") + "..."


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(text).strip().lower()).strip("-")
    return cleaned[:48] or "unnamed-guard"


def get_guards_path(cwd: str | None = None) -> str:
    root = cwd or config.WORKSPACE_DIR
    return os.path.join(root, GUARDS_DIR, GUARDS_FILE)


def load_guards(cwd: str | None = None) -> dict[str, Any]:
    path = get_guards_path(cwd)
    return jsonstore.read_or(path, {"version": 1, "guards": {}})


def guard_table(data: Any) -> dict[str, Any]:
    """The guards of a loaded file, or an empty table when the file is not in the shape ``learn`` writes: a caller that
    changes a guard never writes into a file it cannot read as one."""
    table = data.get("guards") if isinstance(data, dict) else None
    return table if isinstance(table, dict) else {}


def save_guards(data: dict[str, Any], cwd: str | None = None) -> None:
    path = get_guards_path(cwd)
    jsonstore.write(path, data, indent=2, lock=False)


def get_git_diff_text(diff_spec: str = "HEAD~1", cwd: str | None = None) -> str:
    workspace = cwd or config.WORKSPACE_DIR
    cmd = [*gitsafe.GIT, "-c", "core.quotepath=off", "diff", "-U1", diff_spec]    # a non-ASCII path stays readable in the header
    try:
        proc = subprocess.run(cmd, cwd=workspace, capture_output=True, encoding="utf-8", errors="replace", timeout=15)
        return proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _extract_diff_hunks(diff_text: str) -> list[dict[str, Any]]:
    hunks: list[dict[str, Any]] = []
    current_file = ""
    current_removed: list[str] = []
    current_added: list[str] = []

    for line in diff_text.splitlines():
        if line.startswith("--- a/"):
            continue
        elif line.startswith("+++ b/"):
            if current_file and (current_removed or current_added):
                hunks.append({
                    "file": current_file,
                    "removed": current_removed,
                    "added": current_added,
                })
            current_file = line[6:].strip()
            current_removed = []
            current_added = []
        elif line.startswith("-") and not line.startswith("---") and current_file:
            stripped = line[1:].strip()
            if len(stripped) > 4 and not stripped.startswith("//") and not stripped.startswith("#"):
                current_removed.append(stripped)
        elif line.startswith("+") and not line.startswith("+++") and current_file:
            stripped = line[1:].strip()
            if len(stripped) > 4:
                current_added.append(stripped)

    if current_file and (current_removed or current_added):
        hunks.append({
            "file": current_file,
            "removed": current_removed,
            "added": current_added,
        })

    return hunks


def removed_lines(diff_text: str) -> dict[str, list[str]]:
    """The lines a diff removes, per file (the side a guard compiled from the fix must hit)."""
    out: dict[str, list[str]] = {}
    for hunk in _extract_diff_hunks(diff_text):
        out.setdefault(hunk["file"], []).extend(hunk["removed"])
    return out


def added_lines(diff_text: str) -> dict[str, list[str]]:
    """The lines a diff adds, per file."""
    out: dict[str, list[str]] = {}
    for hunk in _extract_diff_hunks(diff_text):
        out.setdefault(hunk["file"], []).extend(hunk["added"])
    return out


_TRIVIAL_STOP_WORDS = {
    "return null;",
    "return null",
    "return none",
    "return true;",
    "return true",
    "return false;",
    "return false",
    "pass",
    "pass;",
    "break;",
    "break",
    "continue;",
    "continue",
}


def _synthesize_forbidden_patterns(removed: list[str]) -> list[str]:
    patterns: list[str] = []
    for line in removed[:10]:
        pat = line.replace("\\", "\\\\")
        pat = re.sub(r"\s+", " ", pat).strip()
        pat_clean = pat.rstrip(";").lower()
        if pat.lower() in _TRIVIAL_STOP_WORDS or pat_clean in _TRIVIAL_STOP_WORDS:
            continue
        if len(pat) >= MIN_PATTERN and pat not in patterns:
            patterns.append(pat)
        if len(patterns) >= 3:
            break
    return patterns


def _derive_guard_patterns(hunk: dict[str, Any]) -> tuple[list[str], str]:
    patterns = _synthesize_forbidden_patterns(hunk.get("removed", []))
    title_candidate = short_title(patterns[0]) if patterns else ""
    return patterns, title_candidate or "defect regression"


# A removed ``return f(x)`` or ``name = f(x)``: what is left of the line is the call, which also catches ``cfg = f(y)``.
_CALL_AFTER_PREFIX = re.compile(
    r"^(?:return\s+|(?:(?:const|let|var|final)\s+)?[\w.\[\]]+\s*=\s*)(?:await\s+)?([A-Za-z_][\w.]*\()")


def call_text(pattern: str) -> str:
    """The call a removed ``return call(...)`` or ``x = call(...)`` line makes, up to and with its opening bracket
    (``yaml.load(``), or "" when the line has no such shape or the call is too short to match safely (2026-11-13, I6)."""
    found = _CALL_AFTER_PREFIX.match(pattern)
    call = found.group(1) if found else ""
    return call if len(call) >= MIN_PATTERN and call != pattern else ""


def broad_variant(guard: dict[str, Any]) -> dict[str, Any] | None:
    """``guard`` with each pattern that is a ``return``/assignment of a call cut down to the call, or ``None`` when no
    pattern changes. The caller keeps it only when the control run and the replay pass."""
    patterns: list[str] = []
    for pat in guard.get("patterns", []):
        wide = call_text(pat) or pat
        if wide not in patterns:
            patterns.append(wide)
    if patterns == guard.get("patterns"):
        return None
    wide_guard = {**guard, "patterns": patterns}
    if len(patterns) < 2:
        wide_guard.pop("match", None)
    return wide_guard


def _derive_file_globs(filepath: str) -> list[str]:
    ext = os.path.splitext(filepath)[1]
    dirname = os.path.dirname(filepath)
    if dirname:
        # ``[`` ``*`` ``?`` of a folder such as ``app/[id]`` are written as one-character classes, or the glob would read them
        # as patterns and watch nothing (2026-11-13, review of D45: the wider ``**/*ext`` that hid this is gone)
        plain = re.sub(r"([\[*?])", r"[\1]", dirname)
        return [f"{plain}/**/*{ext}"]     # the folder of the fix; the wider ``**/*ext`` flagged other code (2026-11-13, D45)
    return [f"**/*{ext}"] if ext else ["**/*"]


def _write_agent_rule(
    guard_id: str,
    title: str,
    category: str,
    file_globs: list[str],
    patterns: list[str],
    cwd: str | None = None,
) -> str:
    workspace = cwd or config.WORKSPACE_DIR
    target_dir = os.path.join(workspace, RULES_DIR)
    os.makedirs(target_dir, exist_ok=True)
    rule_path = os.path.join(target_dir, f"{guard_id}.md")

    safe_title = title.replace('"', "'").replace("\n", " ").replace("\r", " ")
    globs_yaml = json.dumps(file_globs)
    patterns_md = "\n".join(f"- `{p}`" for p in patterns)

    content = f"""---
description: "Learned Guard: {safe_title}"
paths: {globs_yaml}
---
# Invariant: {safe_title} ({category})

This rule was compiled by `brevio-core learn` from a fixed defect, so the mistake is easier to spot when it comes back.
When editing files matching `{', '.join(file_globs)}`, do not write these lines again:

{patterns_md}
"""
    with open(rule_path, "w", encoding="utf-8") as f:
        f.write(content)

    return rule_path


def retitle(guard: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    """``guard`` with its id, title and rule file made from its first pattern, or from ``name`` (the id follows ``name``, as
    it always did). A title set later (the subject of the commit that brought the line in) does not change the id, so the
    same fix keeps the same id."""
    first = guard["patterns"][0] if guard.get("patterns") else ""
    title = name or f"Fix {short_title(first) or 'defect regression'} in {os.path.basename(str(guard['source_file']))}"
    guard_id = f"guard-{_slugify(title)}"
    return {**guard, "id": guard_id, "title": title, "rule_file": f"{RULES_DIR.replace(os.sep, '/')}/{guard_id}.md"}   # "/" so a file written on Windows reads anywhere


def plan_guard(
    diff_text: str,
    name: str | None = None,
    category: str = "robustness",
    cwd: str | None = None,
) -> dict[str, Any] | None:
    """The guard a git diff compiles into, without writing anything (``learn --dry-run``, ROADMAP E34).

    Two or more patterns come from one hunk, so the guard says ``match: "all"``: a file is a hit only where the lines it
    adds hold each of them (2026-11-13, D45). A guard file without the field keeps meaning any one."""
    hunks = _extract_diff_hunks(diff_text)
    if not hunks:
        return None

    chosen_hunk = None
    patterns: list[str] = []
    for hunk in hunks:
        cand_patterns, _title = _derive_guard_patterns(hunk)
        if cand_patterns:
            chosen_hunk = hunk
            patterns = cand_patterns
            break
    if not chosen_hunk or not patterns:
        return None
    filepath = chosen_hunk["file"]
    now = _now_iso()
    guard = {
        "id": "",
        "title": "",
        "category": category,
        "source_file": filepath,
        "file_patterns": _derive_file_globs(filepath),
        "patterns": patterns,
        "status": STATUS_TRIAL,
        "trial_ends": _trial_end(),
        "runs_evaluated": 0,
        "false_fails": 0,
        "intercepted": 0,
        "rule_file": "",
        "created_at": now,
        "updated_at": now,
    }
    if len(patterns) > 1:
        guard["match"] = "all"
    return retitle(guard, name)


def explain_no_guard(diff_text: str) -> str:
    """Why ``plan_guard`` finds nothing to keep in ``diff_text``, and what to do next (2026-11-13, D48)."""
    next_step = "Run it on a fix that removes or changes a longer line, or look at `brevio-core rules` for a built-in rule."
    removed = [ln for hunk in _extract_diff_hunks(diff_text) for ln in hunk["removed"]]
    raw_removed = any(ln.startswith("-") and not ln.startswith("---") for ln in diff_text.splitlines())
    if not removed:
        if not raw_removed:
            return "This diff only adds lines; a guard needs a removed or changed line that held the fault. " + next_step
        return ("The removed lines are comments or have 4 characters or fewer, so there is no line to match. " + next_step)
    shortest = min(removed, key=len)
    if len(shortest) < MIN_PATTERN:
        shortest = "".join(c if c.isprintable() else " " for c in shortest)     # the diff may be of a change under review
        return (f"The removed line `{shortest}` is under {MIN_PATTERN} characters, too general to match safely. " + next_step)
    return "The removed lines are bare keywords such as `return null;`, too general to match safely. " + next_step


def compile_guard_from_diff(
    diff_text: str,
    name: str | None = None,
    category: str = "robustness",
    cwd: str | None = None,
    replay: dict[str, Any] | None = None,
    guard: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Compile removed patterns from a git diff into a zero-token guard and agent rule.

    ``replay`` (``guard_proof.replay``) is stored with the guard: the changes it flagged when the person kept it, which
    a later promotion compares with. ``guard`` is a plan the caller checked and changed (a pattern dropped, a title from
    the commit); it is written as it is, not planned again."""
    guard = guard or plan_guard(diff_text, name, category, cwd)
    if not guard:
        return None
    if replay is not None:
        guard["replay"] = replay
    data = load_guards(cwd)
    if guard["id"] in guard_table(data):
        return None     # not over a guard that is there: it would reset a promotion, wrong marks or a switch-off (2026-11-12, improvement plan)
    _write_agent_rule(guard["id"], guard["title"], category, guard["file_patterns"], guard["patterns"], cwd=cwd)
    data.setdefault("guards", {})[guard["id"]] = guard
    save_guards(data, cwd)
    return guard


def evaluate_guard_trial(
    guard_id: str,
    caught: bool = False,
    false_fail: bool = False,
    cwd: str | None = None,
    hit: str = "",
) -> dict[str, Any] | None:
    """Record a guard evaluation run.

    Silence does not promote a guard: a run with no hit proves nothing when no hit is the usual case (2026-11-12,
    improvement plan). A hit a person marked wrong (``false_fail``) counts, and the ``WRONG_LIMIT``-th one switches the
    guard off, with a note that says so. ``hit`` is the fingerprint of the hit a person named: it is kept in ``wrong_hits``
    and counted once, however often it is marked (2026-11-13, D41)."""
    data = load_guards(cwd)
    g = guard_table(data).get(guard_id)
    if not isinstance(g, dict):
        return None
    g["runs_evaluated"] = int(g.get("runs_evaluated", 0)) + 1
    if caught:
        g["intercepted"] = int(g.get("intercepted", 0)) + 1
    named = g["wrong_hits"] if isinstance(g.get("wrong_hits"), list) else []
    if false_fail and hit and hit in named:
        return g        # the same hit again: nothing to count and nothing to write
    if false_fail:
        g["false_fails"] = int(g.get("false_fails", 0)) + 1
        if hit:
            g["wrong_hits"] = [*named, hit]
        if g["false_fails"] >= WRONG_LIMIT and g.get("status") in LIVE:
            switch_off(g, "wrong", note=f"switched off: {g['false_fails']} hits were marked wrong", cwd=cwd)
    g["updated_at"] = _now_iso()
    save_guards(data, cwd)
    return g


def fingerprint(guard: dict[str, Any]) -> str:
    """What a guard watches and looks for, as a hash. A switch-off carries it (``dismissed.was``), so a change that
    removes the guard can be told from one a person made with ``learn dismiss``."""
    body = json.dumps({"patterns": sorted(map(str, guard.get("patterns", []))),
                       "file_patterns": sorted(map(str, guard.get("file_patterns", [])))}, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def drop_rule_file(guard: dict[str, Any], cwd: str | None = None) -> str:
    """Delete the agent rule of ``guard`` (it tells an agent to avoid the patterns of a guard that is off) and return its
    path, or "" when there was none. Only a file under ``.agents/rules`` of the workspace is deleted: the entry is text
    from a file a change may have edited."""
    workspace = os.path.realpath(cwd or config.WORKSPACE_DIR)
    path = os.path.realpath(os.path.join(workspace, str(guard.get("rule_file", ""))))
    folder = os.path.realpath(os.path.join(workspace, RULES_DIR))
    try:
        if not guard.get("rule_file") or os.path.commonpath([path, folder]) != folder or not os.path.isfile(path):
            return ""
        os.remove(path)
    except (OSError, ValueError):       # ValueError: a path on another drive has nothing in common with the folder
        return ""
    return str(guard["rule_file"])


def switch_off(guard: dict[str, Any], how: str, reason: str = "", note: str = "", cwd: str | None = None) -> None:
    """Set ``guard`` to off and record how, when and what it was, in the entry itself (it stays in the file), and drop
    its agent rule."""
    guard["dismissed"] = {"how": how, "at": _now_iso(), "reason": reason, "was": fingerprint(guard)}
    guard["status"] = STATUS_OFF
    guard["status_note"] = note
    guard["updated_at"] = _now_iso()
    drop_rule_file(guard, cwd)


def dismiss_guard(guard_id: str, reason: str = "", cwd: str | None = None) -> dict[str, Any] | None:
    """Switch a guard off at a person's request; ``None`` when there is no such guard."""
    data = load_guards(cwd)
    g = guard_table(data).get(guard_id)
    if not isinstance(g, dict):
        return None
    switch_off(g, "dismiss", reason, "switched off by a person with `learn dismiss`", cwd)
    save_guards(data, cwd)
    return g


def _trial_end_of(guard: dict[str, Any]) -> str:
    """The date a guard's trial ends: the one written, or for a guard written before ``trial_ends`` existed the day it was
    made (``created_at``, else ``updated_at``) plus ``TRIAL_DAYS``; "" when neither reads as a date, which counts as ended
    (2026-11-13, D49)."""
    if guard.get("trial_ends"):
        return str(guard["trial_ends"])
    for key in ("created_at", "updated_at"):
        try:
            made = datetime.datetime.strptime(str(guard.get(key, ""))[:10], "%Y-%m-%d")
        except ValueError:
            continue
        return (made + datetime.timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d")
    return ""


def promote_guard(guard_id: str, unexplained: list[str], today: str | None = None,
                  cwd: str | None = None, accept: bool = False) -> tuple[dict[str, Any] | None, str]:
    """``(guard, "")`` once a trial guard is active, or ``(None, why not)``.

    A guard is promoted when its trial has ended, no hit was marked wrong, and the replay of recent history flags
    nothing it did not flag when the guard was kept (``unexplained`` lists what it flags now). With ``accept`` a person
    has read those hits and takes them in: they join ``replay.accepted`` and the guard is promoted (2026-11-13, D42)."""
    data = load_guards(cwd)
    g = guard_table(data).get(guard_id)
    if not isinstance(g, dict):
        return None, f"no guard {guard_id}"
    if g.get("status") != STATUS_TRIAL:
        return None, f"{guard_id} is {g.get('status', 'unknown')}, not on trial"
    end = _trial_end_of(g)
    if end and end > (today or _now_iso()[:10]):
        return None, f"the trial of {guard_id} ends on {end}"
    if int(g.get("false_fails", 0)) > 0:
        return None, f"{guard_id} has {g['false_fails']} hit(s) marked wrong"
    note = "promoted by a person after a clean replay"
    if unexplained and not accept:
        return None, (f"the replay flags {len(unexplained)} change(s) it did not flag when the guard was kept: "
                      + ", ".join(unexplained[:5]))
    if unexplained:
        held = g["replay"] if isinstance(g.get("replay"), dict) else {}
        before = held["accepted"] if isinstance(held.get("accepted"), list) else []
        g["replay"] = {**held, "accepted": [*[x for x in before if isinstance(x, str)], *unexplained]}
        note = f"promoted by a person who accepted {len(unexplained)} replay hit(s)"
    g["status"] = STATUS_ACTIVE
    g["status_note"] = note
    g["updated_at"] = _now_iso()
    save_guards(data, cwd)
    return g, ""
