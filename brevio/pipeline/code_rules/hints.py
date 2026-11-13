"""The fix hint a finding carries: one source for the agent hook, the MCP ``scan`` reply and ``explain``.

A rule has one example in ``details.DETAILS`` in one language, and some rules report more than one kind of finding. So a
hint is chosen by the finding's rule, its message and the language of the file it is in; where nothing fits it is left
out rather than showing code in another language (ROADMAP D20, I25, 2026-11-13). Standard library only.
"""
from __future__ import annotations

import os
from typing import NamedTuple

from brevio.pipeline.code_rules.details import DETAILS

HINT_CHARS = 100        # characters of a hint a finding line carries

# File extension -> language family. A rule example in ``ts`` or ``tsx`` fits every script file.
_FAMILY = {".py": "py", ".ts": "ts", ".tsx": "ts", ".js": "ts", ".jsx": "ts", ".mjs": "ts", ".cjs": "ts", ".vue": "ts", ".svelte": "ts",
           ".css": "css", ".scss": "css", ".html": "html", ".sql": "sql", ".json": "json"}
_LANG_FAMILY = {"tsx": "ts"}
_NAMES = {"py": "Python", "ts": "TypeScript and JavaScript"}


class Variant(NamedTuple):
    rule: str
    marker: str     # text the finding message holds
    kind: str       # what the finding is, for the rule page
    lang: str       # language family of the files it fits
    text: str


# Rules that report several kinds of finding, or whose one example fits only some of them: the hint is picked here,
# and a finding that matches no entry gets none. Each text is a line the rule does not flag (a test checks it).
VARIANTS: tuple[Variant, ...] = (
    Variant("R2.8", "SQL string interpolation", "SQL built from a string", "py",
            'cur.execute("SELECT * FROM users WHERE name = %s", (name,))'),
    Variant("R2.8", "SQL string interpolation", "SQL built from a string", "ts",
            "await db.query('SELECT * FROM users WHERE name = $1', [name]);"),
    Variant("R2.8", "dangerouslySetInnerHTML", "HTML set from a string", "ts",
            "<div>{text}</div>"),
    Variant("R2.8", "`eval`", "code run from a string", "ts", "const value = JSON.parse(userInput);"),
    Variant("R5.4", "Bare `except:`", "a bare except", "py",
            "except ValueError as exc:  # name the error, then log it or raise again"),
    Variant("R5.4", "Empty catch", "an empty catch", "ts",
            "try { await save(); } catch (e) { log.error('save failed', e); throw e; }"),
)
_MULTI = frozenset(v.rule for v in VARIANTS)


def family(path: str) -> str:
    """The language family of ``path``: ``py``, ``ts``, ``css``, ``html``, ``sql``, ``json``, or "" when unknown."""
    return _FAMILY.get(os.path.splitext(path)[1].lower(), "")


def example(rule: str, path: str, message: str = "") -> str:
    """A good example for a finding of ``rule`` in ``path`` with ``message``, or "" when none fits the file's language."""
    fam = family(path)
    if rule in _MULTI:
        return next((v.text for v in VARIANTS if v.rule == rule and v.lang == fam and v.marker in message), "")
    detail = DETAILS.get(rule)
    if detail is None:
        return ""
    lang = _LANG_FAMILY.get(detail.lang, detail.lang)
    return detail.good.strip() if lang == "text" or lang == fam else ""


def fix_text(rule: str, path: str, message: str = "") -> str:
    """The first line of ``example``: the fix hint a finding line carries."""
    return (example(rule, path, message).splitlines() or [""])[0].strip()


def hint(rule: str, path: str, message: str = "") -> str:
    """`` -> `` and the fix text, cut to ``HINT_CHARS``, or "" when there is none."""
    text = fix_text(rule, path, message)
    return f" -> {text[:HINT_CHARS]}" if text else ""


def page_lines(rule: str) -> list[str]:
    """One line per kind and language of a rule that has several hints, for the rule page; none for the other rules."""
    return [f"- {v.kind} in {_NAMES[v.lang]}: `{v.text}`" for v in VARIANTS if v.rule == rule]
