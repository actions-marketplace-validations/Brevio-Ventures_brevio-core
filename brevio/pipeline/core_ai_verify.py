"""The fix verifier of ``brevio-core ai fix``: what a model proposes is data, and the rules check it (the 0.7 set,
owner decision D24, 2026-11-11).

Pure: no network, no model, no process. It takes the window of lines the model was shown and the lines it sent back,
applies them in memory and says whether the change may be shown as a fix. It accepts only when

- the rule no longer flags the line (the scan runs on the new text, and an ignore comment does not count);
- no finding of any rule is new anywhere in the file;
- the reply adds no ignore comment or other suppression on any line that did not have it (a list shared with ``patch``);
- one file changes, and neither it nor the place a link leads to is one that sets what the gate or an agent does;
- a test file keeps its assertions, and none is made always true (the check ``patch`` runs);
- a ``.py`` file still compiles (the whole text, not only its syntax tree).

It never retries, never asks again and never writes: the caller shows the diff and the reasons. Standard library and
exported brevio.* modules only.
"""
from __future__ import annotations

import ast
import os
import py_compile
import re
import tempfile
from collections import Counter
from typing import NamedTuple

from brevio.pipeline import core_cli, core_patch
from brevio.pipeline.code_rules import _hardcoded_secret

MAX_EXTRA_LINES = 20            # a window may grow by this many lines: a fix is small
_DIFF_HEADER = re.compile(r"^(?:diff --git |--- a/|\+\+\+ b/|@@ -\d|<<<<<<< |>>>>>>> )")
# A line the secret check flags besides the scanner's own (2026-11-12, claims check): used only to refuse to send, so a false
# alarm costs a refusal and nothing else. The gate's own rule is not changed by these.
_PRIVATE_KEY = re.compile(r"-----(?:BEGIN|END)[A-Z0-9 ]*PRIVATE KEY[A-Z ]*-----")
_URL_PASSWORD = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
_AUTH_LITERAL = re.compile(r"(?:authorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic|token)?\s*[A-Za-z0-9._~+/=-]{8,}"
                           r"|\bbearer\s+[A-Za-z0-9._~+/=-]{16,})", re.I)
_JSON_WEB_TOKEN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")      # header.payload.signature
_TOKEN_ASSIGNED = re.compile(r"[\w.-]*(?:key|token|secret|passw(?:or)?d|credential)[\w.-]*[\"']?\s*(?::=|[:=])\s*[rbfu]{0,2}"
                             r"[\"'][A-Za-z0-9+/=_-]{32,}[\"']", re.I)
SECRET_CONTEXT = 5              # lines above a line that the scanner's check reads with it


class Window(NamedTuple):
    path: str               # relative to the project folder
    rule: str
    line: int               # 1-based number of the flagged line in the file
    start: int              # 1-based number of the first line of the window
    lines: list[str]        # the window as the model was shown it

    @property
    def end(self) -> int:
        return self.start + len(self.lines) - 1


class Verdict(NamedTuple):
    ok: bool
    reasons: list[str]
    changes: core_cli.Changes       # path -> (old text, new text, []): what would be written
    diff: str


def holds_secret(text: str, above: str = "") -> bool:
    """``text`` (a line; ``above`` is the lines before it) holds a secret by the scanner's check or by a stricter one: a
    private-key block marker, a URL with a password in it, an Authorization or Bearer token or a JSON web token written out,
    or a quoted token of 32 or more characters assigned to a name that says key, token, secret, password or credential. For
    the ``ai`` commands, to refuse to send; it does not change what the gate flags."""
    return bool(_hardcoded_secret(text, above) or _PRIVATE_KEY.search(text) or _URL_PASSWORD.search(text)
                or _AUTH_LITERAL.search(text) or _JSON_WEB_TOKEN.search(text) or _TOKEN_ASSIGNED.search(text))


def secret_lines(rows: list[tuple[int, str]]) -> list[int]:
    """The line numbers among ``rows`` (``(number, text)``) that ``holds_secret`` flags, each read with the lines above it."""
    return [n for i, (n, text) in enumerate(rows)
            if holds_secret(text, "\n".join(t for _n, t in rows[max(0, i - SECRET_CONTEXT):i]))]


def window_of(found: core_patch.Located, context: int) -> Window:
    """The flagged line of ``found`` with ``context`` lines on each side, as far as the file goes."""
    start = max(found.line - context, 1)
    end = min(found.line + context, len(found.lines))
    return Window(found.path, found.rule, found.line, start, found.lines[start - 1:end])


def _read(root: str, rel: str) -> str | None:
    try:
        with open(os.path.join(root, *rel.split("/")), encoding="utf-8", newline="") as fh:
            return fh.read(core_cli.MAX_BYTES + 1)
    except (OSError, UnicodeDecodeError):
        return None


def _splice(text: str, window: Window, reply: list[str]) -> tuple[str, str]:
    """``(old text, new text)``, both with ``\\n`` line ends, or ``("", "")`` text that is not the one the model saw."""
    old = text.replace("\r\n", "\n")
    parts = old.split("\n")
    if parts[window.start - 1:window.end] != window.lines:
        return "", ""
    return old, "\n".join([*parts[:window.start - 1], *reply, *parts[window.end:]])


def _shape_reasons(window: Window, reply: list[str]) -> list[str]:
    if not any(x.strip() for x in reply):
        return ["the reply holds no lines"]
    if reply == window.lines:
        return ["the reply changes nothing"]
    if len(reply) > len(window.lines) + MAX_EXTRA_LINES:
        return [f"the reply is {len(reply) - len(window.lines)} lines longer than the window: a fix is small"]
    if any(_DIFF_HEADER.match(x) for x in reply):
        return ["the reply is a diff or names another file: it must hold the replacement lines only"]
    return []


def _suppression_reasons(window: Window, reply: list[str]) -> list[str]:
    """A reply may keep a suppression a line already had; it may not put one on any other line, nor move one."""
    return [core_patch.suppression_reason(window.path, window.start + i, "the reply") for i in core_patch.added_suppressions(window.lines, reply)]


def _parse_error(text: str) -> tuple[bool, int]:
    """``(failed, the line it names or 0)`` for the parser."""
    try:
        ast.parse(text)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return True, int(getattr(exc, "lineno", 0) or 0) if isinstance(exc, SyntaxError) else 0
    return False, 0


def _compile_error(text: str) -> tuple[bool, int]:
    """``(failed, the line it names or 0)`` for the compiler, which finds what the parser does not (a ``nonlocal`` with
    nothing to bind, a ``return`` outside a function). It runs on a copy in a temporary folder."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "unit.py")
        try:
            with open(src, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            py_compile.compile(src, cfile=os.path.join(tmp, "unit.pyc"), doraise=True)
        except py_compile.PyCompileError as exc:
            return True, int(getattr(exc.exc_value, "lineno", 0) or 0)
        except (ValueError, RecursionError, MemoryError, OSError):
            return True, 0
    return False, 0


def _syntax_reasons(path: str, old: str, new: str) -> list[str]:
    """A ``.py`` file must parse and compile whole, so an error only the compiler finds refuses the fix too (2026-11-12,
    claims check)."""
    if not path.endswith(".py"):
        return []
    if _parse_error(old)[0]:
        return [f"{path} does not parse even before the change, so the fix cannot be checked"]
    if _compile_error(old)[0]:
        return [f"{path} does not compile even before the change, so the fix cannot be checked"]
    for verb, test in (("parse", _parse_error), ("compile", _compile_error)):
        failed, lno = test(new)
        if failed:
            return [f"{path} would no longer {verb}{f' (line {lno})' if lno else ''}"]
    return []


def _custody_reasons(path: str, old: str, new: str) -> list[str]:
    """A test file keeps its assertions and none becomes always true: the pre-PR gate's own checks, which ``patch`` runs too."""
    return core_patch.custody_reasons({path: (old, new, [])})


def _keys(found: list[str], text: str) -> Counter[tuple[str, str]]:
    """Each finding as ``(rule, the flagged line's text)``, so a finding that only moved down a line is the same one."""
    lines = text.splitlines()       # the numbering the scan uses
    out: Counter[tuple[str, str]] = Counter()
    for finding in found:
        fields = core_cli.finding_fields(finding)
        if fields:
            lno = int(fields["line"])
            out[(str(fields["rule"]), lines[lno - 1].strip() if 1 <= lno <= len(lines) else "")] += 1
    return out


def _scan(path: str, text: str) -> list[str]:
    """The raw findings of ``text`` as the file ``path``: ignore comments and the baseline do not hide any."""
    proj = core_cli.project([path])
    with tempfile.TemporaryDirectory() as tmp:
        full = os.path.join(tmp, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        numbered = {path: list(enumerate(text.splitlines(), 1))}
        return core_cli.scan_surface(numbered, proj, root=tmp)


def _clip(text: str, limit: int = 70) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _scan_reasons(window: Window, old: str, new: str) -> list[str]:
    before, after = _keys(_scan(window.path, old), old), _keys(_scan(window.path, new), new)
    target = (window.rule, window.lines[window.line - window.start].strip())
    if not before[target]:
        return [f"{window.path}:{window.line}: the rule [{window.rule}] does not flag that line now, so there is nothing to verify"]
    out = [f"a new finding: [{rule}] on `{_clip(text)}`" for (rule, text), n in sorted((after - before).items()) for _ in range(n)]
    if after[target] >= before[target]:
        out.insert(0, f"[{window.rule}] still flags `{_clip(target[1])}`")
    return out


def check(root: str, window: Window, reply: list[str]) -> Verdict:
    """Apply ``reply`` in place of ``window`` in memory and say whether it is a fix: ``ok`` and no reasons, or the
    reasons it is refused. ``root`` is the project folder, the one ``brevio-core.toml`` is read from. Writes nothing."""
    text = _read(root, window.path)
    if text is None:
        return Verdict(False, [f"{window.path} cannot be read as text"], {}, "")
    if len(text) > core_cli.MAX_BYTES:
        return Verdict(False, [f"{window.path} is larger than {core_cli.MAX_BYTES} bytes"], {}, "")
    if "\r\n" in text and text.replace("\r\n", "").count("\n"):
        return Verdict(False, [f"{window.path} has mixed line endings; make them one kind first"], {}, "")
    blocked = core_patch.blocked(window.path, root) or core_patch.link_reason(window.path, root)    # the name and where a link leads (2026-11-12)
    reasons = [f"{window.path}: {blocked}"] if blocked else []
    reasons += _shape_reasons(window, reply)
    old, new = _splice(text, window, reply)
    if not old:
        reasons.append(f"{window.path} changed since the window was read; nothing was checked")
    if reasons:
        return Verdict(False, reasons, {}, "")
    reasons = _suppression_reasons(window, reply) + _syntax_reasons(window.path, old, new) + _custody_reasons(window.path, old, new)
    with core_patch.in_root(root):
        reasons += _scan_reasons(window, old, new)
    crlf = "\r\n" in text
    shown = (old.replace("\n", "\r\n"), new.replace("\n", "\r\n")) if crlf else (old, new)
    changes: core_cli.Changes = {window.path: (shown[0], shown[1], [])}
    return Verdict(not reasons, reasons, changes, core_cli.unified_diff(changes))
