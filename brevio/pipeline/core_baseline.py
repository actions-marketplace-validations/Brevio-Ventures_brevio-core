"""Baseline, inline ignores and the new-suppression check of ``brevio-core scan`` (ROADMAP E20, E21, the 0.4 set, 2026-11-05).

The baseline (``.brevio/baseline.json``) records the findings a project already has, so a scan reports only new ones.
An entry is keyed by path, rule ID and the sha256 of the stripped line text, never by line number, so a line that
moves stays baselined. The file holds one entry per line, sorted, so two branches that add entries merge cleanly.

An ignore comment (``brevio-core: ignore <rule ID> -- reason``) in a code file, on the line or the line above, silences
that rule there. Without a reason it does not count. In other files (docs that show the comment) it is plain text. Agents cannot silence the gate: the agent hook and the MCP tools do not honour ignores, and a diff
scan reports a new ignore comment or a new baseline entry as a finding a person has to review (plan review,
2026-11-05). Standard library only, so it exports with the CLI.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable
from typing import Any, NamedTuple

BASELINE_PATH = ".brevio/baseline.json"
INIT_RECORD = ".brevio/init.json"     # what ``brevio-core init`` records; core_init.RECORD names the same file
FORMAT_VERSION = 1
FAIL_ON = ("new", "all", "none")
SUPPRESSION_RULE = "suppression"
SUPPRESSION_MESSAGE = "new suppression: needs a human review"
CI_ONLY = " (an ignore comment counts in CI only, not here)"
# The test-custody rules `brevio-core check` runs without reading an ignore comment (R20.5 does read one). The hook's
# note above was added to their findings, which promised a silence that no command gives (2026-11-12, review of the
# precision stage).
NO_IGNORE = frozenset({"R20.1", "R20.2", "R20.3", "R20.4"})
# 0.9.1 printed an empty test as R6.1, and `ignore R6.1` silenced it. The comment is still read for R20.5 for one release
# (0.10.0, D34); the warning below tells the author to rename it.
ALIASES = {"R20.5": "R6.1"}
RENAMED = "{old} is now {new}; the old ID is read for one release"
SECRET_RULES = frozenset({"R13.1"})     # a baselined finding of these hides a secret that is still in the history (I7)
NOTE_MAX = 10                           # unused ignore comments listed before "and N more"
_ID = r"(?:guard:[\w.-]+|[A-Za-z]+\d+(?:\.\d+)*)"     # a rule ID, or ``guard:<id>`` for a learned guard (2026-11-12)
_DIRECTIVE = re.compile(rf"brevio-core:\s*ignore\s+(?P<rules>{_ID}(?:[\s,]+{_ID})*)(?P<rest>.*)$")
_CLOSER = re.compile(r"(?:\*/\s*\}?|--!?>|%\}|\}\})\s*$")     # the end of a /* */, <!-- -->, {/* */} or template comment

Key = tuple[str, str, str]              # path, rule ID, sha256 of the stripped line
Numbered = dict[str, list[tuple[int, str]]]
TextOf = Callable[[str, int], str]      # the text of line ``n`` of a file; "" when there is none
FieldsOf = Callable[[str], "dict[str, Any] | None"]


class Directive(NamedTuple):
    rules: frozenset[str]
    reason: str             # empty: the comment does not count


class Where(NamedTuple):
    file: str       # the baseline file, as a path from the current folder
    prefix: str     # the path of the current folder (a diff scan: of the git root) below the folder that holds the file


class Triage(NamedTuple):
    shown: list[str]                    # reported, and they fail the scan
    silenced: list[tuple[str, str]]     # (finding, reason)
    baselined: list[str]


def directive(text: str) -> Directive | None:
    """The ignore comment on one line, or ``None`` when it has none."""
    m = _DIRECTIVE.search(text)
    if not m:
        return None
    rest = _CLOSER.sub("", m["rest"]).strip()
    reason = rest[2:].strip() if rest.startswith("--") else ""
    return Directive(frozenset(r.upper() for r in re.split(r"[\s,]+", m["rules"].strip())), reason)


def ignore_reason(rule: str, *texts: str) -> str:
    """The reason the first ignore comment in ``texts`` that names ``rule`` gives; "" when none silences it."""
    if rule.upper() in NO_IGNORE:
        return ""       # `check` reads no comment for these, so a hook must not say the comment counts in CI
    old = ALIASES.get(rule.upper(), "")
    for text in texts:
        d = directive(text)
        if d and d.reason and (rule.upper() in d.rules or old in d.rules):
            return d.reason + (f" ({RENAMED.format(old=old, new=rule.upper())})" if rule.upper() not in d.rules else "")
    return ""


def line_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def key(fields: dict[str, Any], text_of: TextOf, prefix: str = "") -> Key:
    """The baseline key of a finding; ``prefix`` is the path of the current folder below the folder of the baseline file."""
    path = str(fields["path"])
    return (posixpath.normpath(posixpath.join(prefix, path)) if prefix else path), str(fields["rule"]), line_hash(text_of(path, int(fields["line"])))


def git_root(start: str = ".") -> str:
    """The folder with a ``.git`` entry at or above ``start`` (a folder or a worktree's file); "" when there is none."""
    here = os.path.abspath(start)
    while not os.path.exists(os.path.join(here, ".git")):
        parent = os.path.dirname(here)
        if parent == here:
            return ""
        here = parent
    return here


def locate(cwd: str = ".", diff: bool = False) -> Where:
    """Where the baseline of ``cwd`` is: the nearest ``.brevio/baseline.json`` from here up to the git root, else the file
    at the git root (``baseline`` writes there), else in ``cwd`` as in 0.9.1. A baseline in ``cwd`` always comes first.

    The keys of a file are relative to its folder, so ``prefix`` is the path from that folder to ``cwd``. A diff scan names
    files from the git root, so its prefix is the path from that folder to the root (empty when the file is there)."""
    here, root = os.path.abspath(cwd), git_root(cwd)
    folder = here
    if root:
        walk = here
        while not os.path.isfile(os.path.join(walk, BASELINE_PATH)) and walk != root:
            walk = os.path.dirname(walk)
        folder = walk if os.path.isfile(os.path.join(walk, BASELINE_PATH)) else root
    target = root if diff and root else here
    below = os.path.relpath(target, folder).replace(os.sep, "/")
    return Where(os.path.relpath(os.path.join(folder, BASELINE_PATH)).replace(os.sep, "/"),
                 "" if below == "." or below.startswith("..") else below)


def triage(findings: Iterable[str], fields_of: FieldsOf, text_of: TextOf, baseline: Counter[Key] | None,
           honour_ignores: bool = True, commented: Callable[[str], bool] = lambda _path: True,
           prefix: str = "", totals: Counter[Key] | None = None) -> Triage:
    """Split ``findings`` into shown, silenced and baselined ones (``baseline`` ``None``: every finding counts).

    ``commented(path)`` says whether an ignore comment in that file is one (a code file) rather than text. A diff scan
    sees only the added lines, so ``totals`` (how often each key occurs in the whole files) says where the baseline has
    been used up: a key found more often than the baseline holds it hides nothing, so a copy of a baselined line shows (D26)."""
    budget: Counter[Key] = Counter(baseline or {})
    if totals is not None:
        budget = Counter({k: n for k, n in budget.items() if totals[k] <= n})
    out = Triage([], [], [])
    for finding in findings:
        fields = fields_of(finding)
        if fields is None:
            out.shown.append(finding)
            continue
        path, lno = str(fields["path"]), int(fields["line"])
        reason = ignore_reason(str(fields["rule"]), text_of(path, lno), text_of(path, lno - 1) if lno > 1 else "") \
            if commented(path) else ""
        if reason and honour_ignores:
            out.silenced.append((finding, reason))
            continue
        k = key(fields, text_of, prefix)
        if budget[k] > 0:
            budget[k] -= 1
            out.baselined.append(finding)
            continue
        out.shown.append(finding + CI_ONLY if reason else finding)
    return out


def counts(findings: Iterable[str], fields_of: FieldsOf, text_of: TextOf, prefix: str = "") -> Counter[Key]:
    """How often each key occurs among ``findings``: what a baseline records for them."""
    return Counter(key(f, text_of, prefix) for f in map(fields_of, findings) if f is not None)


def merge(old: Counter[Key], new: Counter[Key], replaced: Callable[[str], bool]) -> Counter[Key]:
    """``old`` without the paths ``replaced`` names, plus ``new`` (the larger count wins where both hold a key)."""
    out = Counter({k: n for k, n in old.items() if not replaced(k[0])})
    for k, n in new.items():
        out[k] = max(out[k], n)
    return out


def render(entries: Counter[Key]) -> str:
    """The baseline file: sorted, one entry per line, the same text for the same entries."""
    rows = [json.dumps({"path": p, "rule": r, "line_sha256": h, "count": n}, ensure_ascii=False)
            for (p, r, h), n in sorted(entries.items()) if n > 0]
    body = "".join(f"    {row}{',' if i < len(rows) - 1 else ''}\n" for i, row in enumerate(rows))
    return f'{{\n  "version": {FORMAT_VERSION},\n  "findings": [\n{body}  ]\n}}\n'


def parse(text: str) -> Counter[Key]:
    """The entries of a baseline file; ``ValueError`` when it is not one."""
    doc = json.loads(text)
    rows = doc.get("findings") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise ValueError("not a brevio-core baseline (no findings list)")
    out: Counter[Key] = Counter()
    for row in rows:
        fields = [row.get(k) for k in ("path", "rule", "line_sha256")] if isinstance(row, dict) else []
        n = row.get("count", 1) if isinstance(row, dict) else 0
        texts = [f for f in fields if isinstance(f, str)]
        if len(texts) != 3 or not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ValueError(f"not a brevio-core baseline entry: {row!r}")
        out[(texts[0], texts[1], texts[2])] += n
    return out


def load(path: str = BASELINE_PATH) -> Counter[Key]:
    """The baseline at ``path``; empty when there is none, ``ValueError`` when it cannot be read."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return Counter()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    try:
        return parse(text)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def ignore_warnings(lines: Numbered, known: Collection[str]) -> list[str]:
    """A warning for each ignore comment among ``lines`` that names an unknown rule or gives no reason."""
    out: list[str] = []
    for path, numbered in sorted(lines.items()):
        for lno, text in numbered:
            d = directive(text)
            if d is None:
                continue
            out += [f"{path}:{lno}: the ignore comment names {r}, which is not a rule" for r in sorted(d.rules - set(known))]
            if not d.reason:
                out.append(f"{path}:{lno}: the ignore comment has no '-- reason', so it does not count")
    return out


def renamed_ignores(silenced: Iterable[tuple[str, str]], fields_of: FieldsOf) -> list[str]:
    """A warning for each finding an ignore comment silenced by its old rule ID; a comment for a live rule of that ID is left alone."""
    tails = tuple(f"({RENAMED.format(old=old, new=new)})" for new, old in ALIASES.items())
    out = []
    for finding, reason in silenced:
        fields = fields_of(finding)
        if fields and reason.endswith(tails):
            out.append(f"{fields['path']}:{fields['line']}: the ignore comment names the old ID of {fields['rule']}; rename it ({reason[reason.rindex('(') + 1:-1]})")
    return out


def stale_entries(entries: Counter[Key], known: Collection[str], where: str = BASELINE_PATH) -> list[str]:
    """A warning for each baseline entry whose rule ID is not a rule any more."""
    return [f"{where}: the entry for {p} names {r}, which is not a rule" for (p, r, _h) in sorted(entries) if r not in known]


def suppression(path: str, lno: int, detail: str = "") -> str:
    return f"{path}:{lno}: [{SUPPRESSION_RULE}] {SUPPRESSION_MESSAGE}{detail}"


def added_ignores(lines: Numbered) -> list[str]:
    """A finding for each line among ``lines`` (the lines a change adds) that holds an ignore comment which counts."""
    return [suppression(path, lno) for path, numbered in sorted(lines.items())
            for lno, text in numbered if (d := directive(text)) and d.reason]


def added_entries(before: Counter[Key], after: Counter[Key], after_text: str, where: str = BASELINE_PATH) -> list[str]:
    """A finding for each baseline entry ``after`` holds that ``before`` (the base's baseline) did not."""
    rows = after_text.splitlines()
    out = []
    for (path, rule, digest), n in sorted(after.items()):
        if n > before.get((path, rule, digest), 0):
            lno = next((i for i, row in enumerate(rows, 1) if digest in row), 1)
            out.append(suppression(where, lno, f" ({rule} in {path})"))
    return out


def secret_notes(entries: Counter[Key]) -> list[str]:
    """A line for each baselined finding of ``SECRET_RULES``: the baseline hides it, it does not remove the secret (I7)."""
    return [f"{r} in {p} is in the baseline: a secret is in your history; rotate it, baselining only hides it"
            for (p, r, _h) in sorted(entries) if r in SECRET_RULES]


def prune(entries: Counter[Key], found: Counter[Key]) -> tuple[Counter[Key], list[str]]:
    """``entries`` cut to what ``found`` still holds, and a line for each entry taken out (I47)."""
    kept = Counter({k: min(n, found[k]) for k, n in entries.items() if found[k] > 0})
    return kept, [f"{p}: {r}" + (f" (x{entries[(p, r, h)] - kept[(p, r, h)]})" if entries[(p, r, h)] - kept[(p, r, h)] > 1 else "")
                  for (p, r, h) in sorted(entries) if kept[(p, r, h)] < entries[(p, r, h)]]


def unused_ignores(lines: Numbered, silenced: Iterable[tuple[str, str]], fields_of: FieldsOf, scanned: Collection[str]) -> list[str]:
    """A note for each ignore comment among ``lines`` (a whole scan) that silenced nothing: no finding of a rule it names, on
    its line or the next. Only rules the scan runs count, so a comment for a ``check`` rule is never called unused."""
    used = {(f["path"], f["rule"], f["line"]) for f in map(fields_of, (s for s, _r in silenced)) if f}
    out = []
    for path, numbered in sorted(lines.items()):
        for lno, text in numbered:
            d = directive(text)
            mine = sorted(r for r in (d.rules if d and d.reason else ()) if r in scanned)
            if mine and not any((path, r, n) in used for r in mine for n in (lno, lno + 1)):
                out.append(f"the ignore comment at {path}:{lno} ({', '.join(mine)}) matches no finding any more")
    return out[:NOTE_MAX] + ([f"and {len(out) - NOTE_MAX} more ignore comments that match no finding"] if len(out) > NOTE_MAX else [])


def dead_entries(entries: Counter[Key], found: Counter[Key]) -> int:
    """How many baseline entries (counted with their repeats) match no finding of ``found``."""
    return sum(max(0, n - found[k]) for k, n in entries.items())


def init_wrote(base_baseline: str, base_record: str, record: str, baseline: str) -> bool:
    """True when ``baseline`` is the file ``brevio-core init`` wrote in the change being scanned.

    The base holds neither a baseline nor an init record, the change adds the record, and the record lists the baseline
    with the sha256 of the text it has now. Such a baseline is the adoption change's own, so its entries are not new
    suppressions; an entry added later, or a baseline edited after init, no longer matches the hash and stays reported
    (2026-11-13, the adoption pull request failed its own check)."""
    if base_baseline.strip() or base_record.strip():
        return False
    try:
        entries = json.loads(record).get("entries", [])
    except (ValueError, AttributeError):
        return False
    wanted = {hashlib.sha256(text.encode("utf-8")).hexdigest() for text in (baseline, baseline.replace("\r\n", "\n"))}
    return any(isinstance(e, dict) and e.get("path") == BASELINE_PATH and e.get("sha256") in wanted for e in entries)
