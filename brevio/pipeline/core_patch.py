"""``brevio-core patch``: an edit is written only when the rules find the changed lines clean (2026-11-11).

Reads SEARCH/REPLACE blocks (the format ``heal`` prints) or a unified diff from a file or from stdin, applies it in memory,
and writes the files only when the scan of the changed lines is clean (an ignore comment does not count), the edit adds no
suppression, touches no protected file (the config, the baseline, the guards, the agent rules and hooks, the workflows)
and deletes no test or removes none of its assertions. Exit 0: written. 1: refused, with the reasons. 2: usage. The same
verdict serves the MCP tool ``verify_edit``, and ``finding_packet`` returns what an assistant needs to explain one finding.

Standard library and exported brevio.* modules only. No network, no model call, no process: git is not used.
"""
from __future__ import annotations

import argparse
import contextlib
import difflib
import json
import os
import posixpath
import re
import sys
import tempfile
from collections.abc import Callable, Generator
from typing import Any, NamedTuple

from brevio.pipeline import core_cli
from brevio.pipeline.code_rules import PR_RULES_122
from brevio.pipeline.code_rules.details import DETAILS
from brevio.pipeline.code_rules.hints import example as fitting_example
from brevio.pipeline.gates import core_gates

HELP = "apply SEARCH/REPLACE blocks or a unified diff, only when the rules find the changed lines clean"
EXIT_WRITTEN, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2
MAX_PATCH_CHARS = 400_000
MAX_CONTEXT = 50
_OPEN, _MID, _CLOSE = "<<<<<<< SEARCH", "=======", ">>>>>>> REPLACE"
_HEADER = re.compile(r"^[#*\s`]*(?:(?:file|gate issue|path)\s*:\s*)?(?P<path>[^\s:`*]+?)(?::\d+)?(?:\s+\[[^\]]*\])?[#*\s`]*$", re.I)
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FINDING = re.compile(r"^(?P<path>.+?):(?P<line>\d+):? \[(?P<rule>[^\]]+)\] ?(?P<msg>.*?)(?: -> .*)?$", re.S)
# What an edit may not touch: a file that decides what the gate or an agent does is a human's to change. That is the config,
# the agent instruction files, and everything under a folder whose name starts with a dot: the baseline and guards in
# ``.brevio``, the agent rules, hooks and settings of each coding client, the workflows, the hooks of the repository itself.
PROTECTED_NAMES = frozenset({"brevio-core.toml", "claude.md", "agents.md", "skill.md", ".cursorrules", ".mcp.json",
                             "copilot-instructions.md"})
# Never read into a packet or a prompt. ``.envrc`` and the usual credential files joined the list on 2026-11-12 (claims check),
# and so did other common stores: git, htpasswd and pgpass files, cluster and cloud files, Terraform variables, a secrets file.
SENSITIVE_NAME = re.compile(
    r"^(?:\.env(?:\..*)?|\.envrc|\.netrc|\.npmrc|\.pypirc|credentials.*|.*\.(?:pem|key|p12|pfx|jks|keystore|kdbx|tfvars)|id_[^/]*"
    r"|\.git-credentials|\.htpasswd|\.pgpass|\.my\.cnf|\.s3cfg|\.dockercfg|kubeconfig|service[-_]account.*\.json"
    r"|secrets?\.(?:ya?ml|json|toml|ini|cfg|conf|properties|txt|env))$", re.I)
TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")      # ``.env.example`` holds no secret and may be edited
# The ways a line switches a check off, the tool's own and other tools': the one list the patch check and the fix verifier
# both read (2026-11-12, claims check). A model that cannot fix a line may try to hide it.
SUPPRESSION = re.compile(
    r"noqa\b|type:\s*ignore|pragma:\s*no\s*cover|nosec\b|nosemgrep|pylint:\s*(?:disable|skip-file)|mypy:\s*ignore"
    r"|pyright:\s*ignore|pytype:\s*disable|pyre-ignore|isort:\s*skip|yapf:\s*disable"
    r"|fmt:\s*(?:off|skip)|eslint-disable|@ts-(?:ignore|expect-error|nocheck)|tslint:\s*disable|prettier-ignore"
    r"|biome-ignore|oxlint-disable|deno-lint-ignore|(?:v8|istanbul|c8)\s+ignore|skipcq|nolint|nosonar|noinspection|nopmd"
    r"|codeql\[|lgtm\[|rubocop:\s*(?:disable|todo)|phpcs:\s*(?:ignore|disable)|@phpstan-ignore|@psalm-suppress"
    r"|@SuppressWarnings|SuppressMessage|swiftlint:\s*disable|stylelint-disable|markdownlint-disable|shellcheck\s+disable"
    r"|#!?\[\s*allow\s*\(|lint:ignore|jshint\s+ignore"
    # secret scanners, config comments that turn a type or lint check off, and infrastructure checks (2026-11-12, claims check)
    r"|pragma:\s*allowlist|gitleaks:\s*allow|trufflehog:\s*ignore|checkov:\s*skip|tfsec:\s*ignore|hadolint\s+ignore|yamllint\s+disable"
    r"|cppcheck-suppress|clang-format\s+off|mypy:\s*(?:disable|allow|no-|disallow)|pyright:\s*(?:basic|report)"
    r"|eslint\s+[@\w/-]+\s*:\s*[\"']?(?:off|0)\b", re.I)


class PatchError(ValueError):
    """The patch cannot be read, or does not fit the files."""


class Denied(PatchError):
    """The file, as named or as it resolves, matches a path the repository keeps out of ``ai`` (``deny_paths``)."""


class Edit(NamedTuple):
    path: str
    search: list[str]
    replace: list[str]
    hint: int           # 0-based line a unified-diff hunk starts at; -1 for a SEARCH/REPLACE block


class Parsed(NamedTuple):
    edits: list[Edit]
    deletes: list[str]


class Verdict(NamedTuple):
    ok: bool
    reasons: list[str]
    changes: core_cli.Changes       # path -> (old text, new text, []): what would be written
    diff: str

    def data(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": self.reasons, "files": sorted(self.changes), "diff": self.diff}


@contextlib.contextmanager
def in_root(root: str) -> Generator[None, None, None]:
    """The scan reads ``brevio-core.toml`` and the files relative to the current folder, so work from ``root``."""
    here = os.getcwd()
    try:
        os.chdir(root)
        yield
    finally:
        os.chdir(here)


# ---- reading the patch -------------------------------------------------------------------------------------------

def _path_of(line: str) -> str:
    m = _HEADER.match(line)
    return m.group("path") if m and "<<<" not in line and ">>>" not in line else ""


def _block_path(lines: list[str], at: int, default: str) -> str:
    """The file a block belongs to: the nearest line above that reads as a path (``#### File: a.py:3 [R5.4]``)."""
    for line in reversed(lines[:at]):
        if line.strip() and not line.lstrip().startswith("```"):
            return _path_of(line) or default
    return default


def parse_blocks(text: str, default_path: str = "") -> list[Edit]:
    """The SEARCH/REPLACE blocks of ``text``; a block with no path above it takes the one before it, or ``default_path``."""
    lines, edits, i, path = text.split("\n"), [], 0, default_path
    while i < len(lines):
        if lines[i].rstrip() != _OPEN:
            i += 1
            continue
        path = _block_path(lines, i, path)
        mid = next((j for j in range(i + 1, len(lines)) if lines[j].rstrip() == _MID), -1)
        end = next((j for j in range(mid + 1, len(lines)) if lines[j].rstrip() == _CLOSE), -1) if mid > 0 else -1
        if end < 0:
            raise PatchError(f"line {i + 1}: a SEARCH block with no ======= and REPLACE end")
        if not path:
            raise PatchError(f"line {i + 1}: a block names no file (put the path on the line above it)")
        edits.append(Edit(path, lines[i + 1:mid], lines[mid + 1:end], -1))
        i = end + 1
    return edits


def _diff_path(raw: str) -> str:
    name = raw.split("\t")[0].strip()
    return "" if name == "/dev/null" else (name[2:] if name[:2] in ("a/", "b/") else name)


def _hunk_body(lines: list[str], at: int, old_n: int, new_n: int) -> tuple[list[str], list[str], int]:
    """``(old lines, new lines, next index)`` of the hunk body at ``at``, read by the counts its header gives."""
    old: list[str] = []
    new: list[str] = []
    while at < len(lines) and (len(old) < old_n or len(new) < new_n):
        tag, body = lines[at][:1], lines[at][1:]
        if tag == "\\":
            pass                      # "\ No newline at end of file"
        elif tag == "-":
            old.append(body)
        elif tag == "+":
            new.append(body)
        else:                         # a context line; an editor may have stripped the blank one down to nothing
            old.append(body)
            new.append(body)
        at += 1
    if len(old) != old_n or len(new) != new_n:
        raise PatchError("a hunk is shorter than its @@ header says")
    return old, new, at


def parse_diff(text: str) -> Parsed:
    """The hunks of a unified diff as edits, and the files it deletes."""
    lines, edits, deletes, i = text.split("\n"), [], [], 0
    refused = ("rename from ", "rename to ", "GIT binary patch", "Binary files ")
    while i < len(lines):
        line = lines[i]
        if line.startswith(refused):
            raise PatchError(f"not supported: {line.strip()[:40]}")
        if not (line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ ")):
            i += 1
            continue
        old_path, new_path = _diff_path(line[4:]), _diff_path(lines[i + 1][4:])
        if old_path and new_path and old_path != new_path:
            raise PatchError(f"not supported: a rename ({old_path} to {new_path})")
        path, i = new_path or old_path, i + 2
        if not new_path:
            deletes.append(path)
        while i < len(lines) and (m := _HUNK.match(lines[i])):
            old_n, new_n = int(m.group(2) or 1), int(m.group(4) or 1)
            old, new, i = _hunk_body(lines, i + 1, old_n, new_n)
            edits.append(Edit(path, old, new, max(int(m.group(1)) - 1, 0) if old_n else int(m.group(1))))
    return Parsed(edits, deletes)


def parse_patch(text: str, default_path: str = "") -> Parsed:
    """SEARCH/REPLACE blocks when ``text`` holds any, else a unified diff."""
    if not text.strip():
        raise PatchError("the patch is empty")
    text = text.replace("\r\n", "\n")
    rows = text.split("\n")
    if any(ln.rstrip() == _OPEN for ln in rows):
        if any(rows[i].startswith("--- ") and rows[i + 1].startswith("+++ ") for i in range(len(rows) - 1)):
            raise PatchError("the patch mixes SEARCH/REPLACE blocks with a unified diff; send one kind at a time")
        return Parsed(parse_blocks(text, default_path), [])
    parsed = parse_diff(text)
    if not parsed.edits and not parsed.deletes:
        raise PatchError("no SEARCH/REPLACE block and no unified diff hunk found")
    return parsed


# ---- applying it in memory ---------------------------------------------------------------------------------------

def _split(text: str) -> tuple[list[str], bool]:
    """``(lines, ends with a newline)``; split on ``\\n`` only, so a form feed inside a line stays there."""
    if not text:
        return [], False
    ends = text.endswith("\n")
    return (text[:-1] if ends else text).split("\n"), ends


def _join(lines: list[str], ends: bool) -> str:
    return "\n".join(lines) + ("\n" if ends and lines else "")


def _same(a: list[str], b: list[str], loose: bool) -> bool:
    return [x.rstrip() for x in a] == [x.rstrip() for x in b] if loose else a == b


def _find(lines: list[str], needle: list[str], hint: int) -> int:
    """Where ``needle`` sits in ``lines``: at ``hint`` when it fits there, else the one place it fits (trailing blanks
    ignored the second time round); an error when it fits nowhere or in several places."""
    n = len(needle)
    for loose in (False, True):
        if 0 <= hint <= len(lines) - n and _same(lines[hint:hint + n], needle, loose):
            return hint
        hits = [p for p in range(len(lines) - n + 1) if _same(lines[p:p + n], needle, loose)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise PatchError(f"the text to replace matches {len(hits)} places; add lines around it so one place fits")
    raise PatchError("the text to replace is not in the file (it may have changed since the edit was written)")


def _apply_one(lines: list[str], edit: Edit, exists: bool) -> list[str]:
    if edit.hint >= 0 and not edit.search:       # a hunk that only adds lines, after the line its header names
        return [*lines[:edit.hint], *edit.replace, *lines[edit.hint:]]
    if not edit.search:
        if exists and lines:
            raise PatchError(f"{edit.path}: an empty SEARCH only creates a new file, and this one has text")
        return list(edit.replace)
    at = _find(lines, edit.search, edit.hint)
    return [*lines[:at], *edit.replace, *lines[at + len(edit.search):]]


def apply_edits(path: str, old: str | None, edits: list[Edit]) -> str:
    """The text of ``path`` after ``edits``; ``old`` is ``None`` for a file that does not exist yet."""
    exists, crlf = old is not None, "\r\n" in (old or "")
    if crlf and (old or "").replace("\r\n", "").count("\n"):
        raise PatchError(f"{path}: mixed line endings; make them one kind first")
    lines, ends = _split((old or "").replace("\r\n", "\n"))
    # Hunks of one file go bottom-up, so the start line each names stays true for the ones above it.
    for edit in sorted(edits, key=lambda e: -e.hint) if all(e.hint >= 0 for e in edits) else edits:
        try:
            lines = _apply_one(lines, edit, exists)
        except PatchError as exc:
            raise PatchError(f"{path}: {exc}") from exc
    text = _join(lines, ends or not exists)
    return text.replace("\n", "\r\n") if crlf else text


# ---- where the edit may land -------------------------------------------------------------------------------------

def protected(rel: str) -> str:
    """Why ``rel`` may not be edited by a patch, or "" when it may."""
    low = rel.lower()
    name = low.rsplit("/", 1)[-1]
    if name in PROTECTED_NAMES or name.endswith(".mdc") or any(part.startswith(".") for part in low.split("/")[:-1]):
        return "a file that sets what the gate or an agent does: a person changes it"
    return ""


def _inside(rel: str, root: str) -> tuple[str, str]:
    """``(clean path, path of what it resolves to)``, both relative to ``root``: never absolute, never ``..``, never
    ``.git``, never through a link that leaves the folder."""
    norm = posixpath.normpath(rel.replace("\\", "/"))
    if norm in ("", ".") or norm.startswith(("/", "../")) or norm == ".." or re.match(r"^[A-Za-z]:", norm):
        raise PatchError(f"{rel}: the path must be inside the project folder")
    if ".git" in norm.split("/"):
        raise PatchError(f"{rel}: a patch does not touch .git")
    here = os.path.realpath(root)
    real = os.path.realpath(os.path.join(root, *norm.split("/")))
    try:
        inside = os.path.commonpath([here, real]) == here
    except ValueError:
        inside = False
    if not inside:
        raise PatchError(f"{rel}: the path leaves the project folder")
    target = os.path.relpath(real, here).replace(os.sep, "/")
    if ".git" in target.split("/"):     # a link into .git is .git (2026-11-12, claims check)
        raise PatchError(f"{rel}: a patch does not touch .git")
    return norm, target


def secret_name(rel: str, templates_ok: bool = False) -> bool:
    """The file name of ``rel`` is one that holds secrets; with ``templates_ok`` a ``.env.example`` is not."""
    name = posixpath.basename(rel)
    return bool(SENSITIVE_NAME.match(name)) and not (templates_ok and name.lower().endswith(TEMPLATE_SUFFIXES))


def blocked(rel: str, root: str = ".", templates_ok: bool = False) -> str:
    """Why ``rel`` may not be edited or read into a request, looking at the path as named AND at the place a link makes it
    resolve to (a link to a secret file, into a protected folder or out of the project is the file it leads to); "" when
    it may. Added 2026-11-12 (claims check): the checks used to read the name only."""
    try:
        norm, target = _inside(rel, root)
    except PatchError as exc:
        return str(exc)
    reason = protected(norm) or protected(target)
    if reason:
        return f"protected, {reason}"
    if secret_name(norm, templates_ok) or secret_name(target, templates_ok):
        return "a file that holds secrets is never read or edited by a patch or a fix"
    return ""


def link_reason(rel: str, root: str = ".") -> str:
    """Why a change must not be written to ``rel`` by name: it is a link (or lies behind one), and a write to its name would put a
    copy of the file in the link's place and leave the file it leads to as it was. "" when it is the file itself. Ask about
    the file the link leads to instead (2026-11-12, claims check)."""
    try:
        norm, target = _inside(rel, root)
    except PatchError:
        return ""
    if norm != target:
        return f"it is a link to {target}: a fix would put a copy in its place and leave {target} as it was, so name {target} itself"
    return ""


def _safe_path(rel: str, root: str) -> str:
    """The clean relative path of a file a patch may change: inside ``root`` and not protected (a link counts as its target)."""
    norm = _inside(rel, root)[0]
    reason = blocked(rel, root, templates_ok=True)
    if reason:
        # A verdict prints diff lines: one for a file that holds secrets would show them to whoever asked (2026-11-12).
        raise PatchError(f"{norm}: {reason}")
    return norm


def _read_text(path: str) -> str | None:
    try:
        if os.path.getsize(path) > core_cli.MAX_BYTES:
            raise PatchError(f"{path}: larger than {core_cli.MAX_BYTES} bytes")
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise PatchError(f"{path}: cannot be read as text ({type(exc).__name__})") from exc


def _changed_lines(changes: core_cli.Changes) -> core_cli.Numbered:
    """The lines each file gains or rewrites, with their line numbers in the new text (what a diff scan reads)."""
    out: core_cli.Numbered = {}
    for rel, (old, new, _rules) in sorted(changes.items()):
        old_lines, new_lines = old.split("\n"), new.split("\n")
        for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
            if tag in ("replace", "insert"):
                out.setdefault(rel, []).extend((n + 1, new_lines[n]) for n in range(j1, j2))
    return out


# ---- the verdict -------------------------------------------------------------------------------------------------

def _scan_reasons(changes: core_cli.Changes, changed: core_cli.Numbered) -> list[str]:
    """Findings on the changed lines, read from the new text. An ignore comment and the baseline do not count here:
    nothing in the patch may silence a rule."""
    proj = core_cli.project(list(changed))
    with tempfile.TemporaryDirectory() as tmp:
        for rel in changed:
            path = os.path.join(tmp, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(changes[rel][1])
        found = core_cli.scan_surface(changed, proj, root=tmp)
    on_changed = {(p, n) for p, rows in changed.items() for n, _t in rows}
    kept = [f for f in found if (fields := core_cli.finding_fields(f)) and (fields["path"], fields["line"]) in on_changed]
    return core_cli.blocking(kept, "new", proj)


def _custody_reasons(changes: core_cli.Changes, code: core_cli.Numbered) -> list[str]:
    """The pre-PR gate's own checks of tests and markers, run on the new text instead of the working tree."""
    out = [m for rel, (old, new, _r) in sorted(changes.items())
           if old and core_gates.TEST_FILE_RE.search(rel) and (m := core_gates.weakened_test(rel, old, new, "the working tree"))]
    return out + core_gates.check_test_tampering(code) + core_gates.check_conflict_markers(code)


def custody_reasons(changes: core_cli.Changes) -> list[str]:
    """What ``changes`` does to tests (assertions removed, a skip or focus marker, an assertion that is always true) and a
    conflict marker: the check ``patch`` runs, for the fix verifier too (2026-11-12, claims check)."""
    changed = _changed_lines(changes)
    return _custody_reasons(changes, {p: rows for p, rows in changed.items() if core_cli.commented(p)})


def line_markers(text: str) -> frozenset[str]:
    """Each way ``text`` switches a check off, as the text of the marker to the end of the line (the tool's own ignore
    comment included): a marker that changes in any way is a different marker."""
    tools = core_cli.baseline_tools()
    found = tools.directive(text) if tools is not None else None
    out = {f"ignore {sorted(found.rules)} {found.reason}"} if found else set()
    return frozenset(out | {text[m.start():].strip() for m in SUPPRESSION.finditer(text)})


def _bare(text: str) -> str:
    """``text`` before its first marker, so two lines that differ only in a comment compare equal."""
    m = SUPPRESSION.search(text)
    return (text[:m.start()] if m else text).rstrip(" \t#/*-<!;")


def _allowed_markers(old: list[str], new_line: str, k: int, same_size: bool) -> frozenset[str]:
    """The markers the ``k``-th line of a replaced block may keep: those of the line it replaces; in a block that changed
    size, those of an old line that is the same code."""
    if same_size:
        return line_markers(old[k])
    out: frozenset[str] = frozenset()
    for line in old:
        if _bare(line) == _bare(new_line):
            out |= line_markers(line)
    return out


def added_suppressions(old: list[str], new: list[str]) -> list[int]:
    """The 0-based indexes of the lines of ``new`` that carry a suppression marker the line they replace did not carry. A
    marker moved to another line, or onto an edited line that did not have it, is added: lines are compared one by one,
    not by how many markers there are (2026-11-12, claims check)."""
    out: list[int] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag not in ("replace", "insert"):
            continue
        for k, j in enumerate(range(j1, j2)):
            marks = line_markers(new[j])
            if marks and not marks <= _allowed_markers(old[i1:i2], new[j], k, tag == "replace" and i2 - i1 == j2 - j1):
                out.append(j)
    return out


def suppression_reason(rel: str, lno: int, what: str = "the edit") -> str:
    tools = core_cli.baseline_tools()
    rule = tools.SUPPRESSION_RULE if tools is not None else "brevio-core"
    return f"{rel}:{lno}: [{rule}] {what} adds a comment that switches a check off; a fix has to remove the cause"


def _added_suppressions(changes: core_cli.Changes) -> list[str]:
    return [suppression_reason(rel, j + 1) for rel, (old, new, _r) in sorted(changes.items()) if core_cli.commented(rel)
            for j in added_suppressions(old.split("\n"), new.split("\n"))]


def _rule_reasons(changes: core_cli.Changes) -> list[str]:
    changed = _changed_lines(changes)
    code = {p: rows for p, rows in changed.items() if core_cli.commented(p)}
    tools = core_cli.baseline_tools()
    # Any ignore directive counts as an attempt to silence a rule, with a reason or without one.
    silenced = [tools.suppression(p, n) for p, rows in sorted(code.items()) for n, text in rows if tools.directive(text)] if tools else []
    return _scan_reasons(changes, changed) + silenced + _added_suppressions(changes) + _custody_reasons(changes, code)


def _delete_reason(rel: str) -> str:
    if core_gates.TEST_FILE_RE.search(rel):
        return f"{rel}:1: [R20.1] Existing test file would be deleted; restore it or get human review"
    return f"{rel}: a patch does not delete files; delete it yourself"


def _edited(by_path: dict[str, list[Edit]], reasons: list[str]) -> core_cli.Changes:
    changes: core_cli.Changes = {}
    for rel, edits in sorted(by_path.items()):
        try:
            old = _read_text(rel)
            new = apply_edits(rel, old, edits)
        except PatchError as exc:
            reasons.append(str(exc))
            continue
        if len(new) > core_cli.MAX_BYTES:
            reasons.append(f"{rel}: the result is larger than {core_cli.MAX_BYTES} bytes")
        elif old is None or new != old:
            changes[rel] = (old or "", new, [])
    return changes


def _plan(text: str, default_path: str) -> Verdict:
    """The verdict for ``text`` against the files of the current folder. Nothing is written."""
    parsed = parse_patch(text, default_path)
    reasons: list[str] = []
    by_path: dict[str, list[Edit]] = {}
    for edit in parsed.edits:
        try:
            by_path.setdefault(_safe_path(edit.path, "."), []).append(edit)
        except PatchError as exc:
            reasons.append(str(exc))
    for gone in parsed.deletes:
        reasons.append(_delete_reason(gone))
    changes = _edited(by_path, reasons)
    if not changes and not reasons:
        reasons.append("the patch changes nothing")
    if not reasons:
        reasons += _rule_reasons(changes)
    return Verdict(not reasons, sorted(set(reasons)), changes, core_cli.unified_diff(changes))


def check_patch(text: str, root: str = ".", default_path: str = "") -> Verdict:
    """Apply ``text`` in memory against the files under ``root`` and say whether it may be written (``ok``), and why not.

    ``default_path`` is the file a SEARCH/REPLACE block applies to when it names none. Writes nothing."""
    try:
        with in_root(root):
            return _plan(text, default_path)
    except (PatchError, core_cli.UsageError) as exc:
        return Verdict(False, [str(exc)], {}, "")


def verdict_text(verdict: Verdict) -> str:
    """The verdict as plain lines: what would be written, or each reason it is refused, then the diff."""
    head = (f"accepted: {', '.join(sorted(verdict.changes))}" if verdict.ok
            else "refused:\n" + "\n".join(f"- {r}" for r in verdict.reasons))
    return head + ("\n" + verdict.diff.rstrip("\n") if verdict.diff else "")


def _put_back(done: list[tuple[str, str, bool]], root: str) -> list[str]:
    """Undo writes made so far (``(path, old text, existed)``); the paths that could not be put back."""
    failed: list[str] = []
    for rel, old, existed in reversed(done):
        full = os.path.join(root, *rel.split("/"))
        try:
            if existed:
                core_cli.write_atomic(full, old)
            else:
                os.remove(full)
        except OSError:
            failed.append(rel)
    return failed


def write_changes(changes: core_cli.Changes, root: str = ".") -> list[str]:
    """Write each file of an accepted verdict (atomically, keeping its mode) and return the paths.

    A file that changed on disk since the verdict was made stops the write before it starts; a failed write puts back
    what was written already."""
    targets = {rel: os.path.join(root, *rel.split("/")) for rel in changes}
    for rel, full in targets.items():
        current = _read_text(full)
        if (current if current is not None else "") != changes[rel][0]:
            raise PatchError(f"{rel}: changed on disk since the patch was checked; nothing was written")
    done: list[tuple[str, str, bool]] = []
    try:
        for rel, full in sorted(targets.items()):
            existed = os.path.isfile(full)
            mode = os.stat(full).st_mode if existed else None
            os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
            core_cli.write_atomic(full, changes[rel][1])
            done.append((rel, changes[rel][0], existed))
            if mode is not None:
                os.chmod(full, mode)
    except OSError as exc:
        failed = _put_back(done, root)
        more = f"; could not put back {', '.join(failed)}" if failed else "; the files written so far were put back"
        raise PatchError(f"write failed ({type(exc).__name__}: {exc}){more}") from exc
    return sorted(targets)


# ---- the finding packet ------------------------------------------------------------------------------------------

def _clip(line: str, limit: int = 400) -> str:
    return line if len(line) <= limit else line[:limit] + " ..."


class Located(NamedTuple):
    rule: str
    message: str
    path: str               # relative to the project folder, ``/``-separated
    line: int               # 1-based line the finding names
    lines: list[str]        # every line of that file, with the line endings taken off
    target: str = ""        # where ``path`` resolves to once links are followed, relative to the project folder


def _more_names(path: str) -> bool:
    """The file at ``path`` has more than one name (a hard link): the other name could be one this module refuses by name."""
    try:
        return os.stat(path).st_nlink > 1
    except OSError:
        return False


def locate(finding: str, context: int = 0, root: str = ".", base: str | None = None,
           deny: Callable[[str], bool] | None = None, single_name: bool = False) -> Located:
    """The file and line a finding names, read from disk. ``base`` is the folder its path is relative to (default
    ``root``): ``scan`` prints paths relative to the folder it ran in. Raises ``PatchError`` for a line that is not a
    finding, a path outside ``root``, a file that holds secrets (``.env*``, keys) or a line that is not there. The name
    and the place a link leads to are both checked (2026-11-12, claims check); ``deny`` (a ``deny_paths`` match) is
    asked of both and raises ``Denied``. With ``single_name`` a file that has more than one hard link is refused too: its other
    name may be a secret file's, and no name check can see that (2026-11-12, claims check)."""
    m = _FINDING.match(finding.strip())
    if not m:
        raise PatchError("not a finding: write it as path:line [rule] message")
    if not 0 <= context <= MAX_CONTEXT:
        raise PatchError(f"context must be between 0 and {MAX_CONTEXT} lines")
    named = m["path"] if base is None else os.path.relpath(os.path.join(base, m["path"]), root)
    rel, target = _inside(named, root)
    if secret_name(rel) or secret_name(target):
        raise PatchError(f"{rel}: a file that holds secrets is never put in a packet")
    if deny is not None and (deny(rel) or deny(target)):
        raise Denied(f"{rel} is a path this repository's brevio-core.toml never lets ai read (deny_paths)")
    if single_name and _more_names(os.path.join(root, *rel.split("/"))):
        raise PatchError(f"{rel}: the file has more than one name (a hard link), so it cannot be told apart from a file that holds "
                         "secrets; copy it to a file of its own first")
    text = _read_text(os.path.join(root, *rel.split("/")))
    lines = _split((text or "").replace("\r\n", "\n"))[0]
    lno = int(m["line"])
    if text is None or not 1 <= lno <= len(lines):
        raise PatchError(f"{rel}:{lno}: no such line")
    return Located(m["rule"], m["msg"], rel, lno, lines, target)


def finding_packet(finding: str, context: int = 0, root: str = ".") -> dict[str, Any]:
    """What an assistant needs to explain or fix one finding: the rule's text, the line, ``context`` lines around it and
    the file extension. ``finding`` reads ``path:line: [rule] message`` (``scan``) or ``path:line [rule] message`` (the
    agent format). Zero tokens: it reads two local sources and sends nothing."""
    found = locate(finding, context, root)
    rel, lno, lines = found.path, found.line, found.lines
    rule, detail = found.rule, DETAILS.get(found.rule)
    return {
        "rule": rule, "message": found.message, "summary": PR_RULES_122.get(rule, ""),
        "why": detail.why if detail else "", "bad": detail.bad if detail else "", "good": fitting_example(rule, rel, found.message),
        "path": rel, "line": lno, "extension": posixpath.splitext(rel)[1], "text": _clip(lines[lno - 1]),
        "before": [_clip(x) for x in lines[max(lno - 1 - context, 0):lno - 1]],
        "after": [_clip(x) for x in lines[lno:lno + context]],
    }


def packet_text(packet: dict[str, Any]) -> str:
    """The packet as the short text an assistant reads."""
    out = [f"[{packet['rule']}] {packet['message']}"]
    out += [f"{label}: {packet[key]}" for label, key in (("Rule", "summary"), ("Why", "why"), ("Bad", "bad"), ("Good", "good"))
            if packet[key]]
    first = packet["line"] - len(packet["before"])
    rows = [*packet["before"], packet["text"], *packet["after"]]
    out.append(f"{packet['path']}:{packet['line']} ({packet['extension'] or 'no extension'})")
    out += [f"{'>' if first + i == packet['line'] else ' '} {first + i} | {row}" for i, row in enumerate(rows)]
    return "\n".join(out)


# ---- the command -------------------------------------------------------------------------------------------------

def _read_patch(source: str) -> str:
    if source == "-":
        return sys.stdin.read(MAX_PATCH_CHARS + 1)
    with open(source, encoding="utf-8", newline="") as fh:
        return fh.read(MAX_PATCH_CHARS + 1)


def main(argv: list[str] | None = None, prog: str = "brevio-core patch") -> int:
    ap = argparse.ArgumentParser(prog=prog, description=HELP, epilog="exit 0: written, 1: refused (the reasons are printed), 2: usage")
    ap.add_argument("patch", metavar="FILE", help="the patch file, or - for standard input")
    ap.add_argument("--root", default=".", help="the project folder (default: .)")
    ap.add_argument("--dry-run", action="store_true", help="print the verdict and the diff; write nothing")
    ap.add_argument("--path", default="", help="the file a SEARCH/REPLACE block applies to when it names none")
    ap.add_argument("--json", action="store_true", help="print the verdict as JSON")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        text = _read_patch(args.patch)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"brevio-core: cannot read the patch: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not text.strip() or len(text) > MAX_PATCH_CHARS or not os.path.isdir(args.root):
        print("brevio-core: need a non-empty patch of at most "
              f"{MAX_PATCH_CHARS} characters and an existing --root folder", file=sys.stderr)
        return EXIT_USAGE
    verdict, written = check_patch(text, args.root, args.path), []
    if verdict.ok and not args.dry_run:
        try:
            written = write_changes(verdict.changes, args.root)
        except PatchError as exc:
            verdict = verdict._replace(ok=False, reasons=[str(exc)])
    if args.json:
        print(json.dumps({**verdict.data(), "written": written, "dry_run": args.dry_run}, indent=2))
    elif written:
        print(f"brevio-core patch: wrote {', '.join(written)}")
    else:
        print(verdict_text(verdict))
    return EXIT_WRITTEN if verdict.ok else EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
