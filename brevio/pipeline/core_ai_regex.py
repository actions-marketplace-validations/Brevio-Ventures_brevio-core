"""The regular-expression subset that a model may propose for a guard (``brevio-core ai learn``, preview; the 0.7 set,
improvement plan, 2026-11-12).

A guard file is committed and a pattern in it runs over the changed lines of a repository, and Python's ``re`` has no time
limit: it holds the interpreter while it matches. A pattern a model wrote is data from outside, so it is read here before it
is compiled, and only a small, plain subset passes:

- plain characters, escaped characters, ``.``, the anchors ``^`` and ``$``, character classes, and the escapes ``\\d \\w \\s``
  (and their capitals), ``\\b`` and ``\\B``;
- groups, plain or ``(?:...)``, and ``|``; a group that holds a repeat or an alternative is not repeated again;
- the repeats ``* + ? {m} {m,n} {m,}``, one repeat after an item and no more, at most ``MAX_UNBOUNDED`` of them without an
  upper end, and a count in braces up to ``MAX_COUNT``;
- no backreference, no lookahead or lookbehind, no named group, no flag and no other ``(?`` form;
- at most ``MAX_LENGTH`` characters and at least ``MIN_LITERALS`` plain ones, so a pattern does not match most lines;
- at most ``MAX_PATHS`` ways to match one line, counted as the product of what each repeat can take (``a?`` 2, ``a{0,50}`` 51,
  ``a*`` as many as the longest line the scan reads) and of the alternatives of each group (review, 2026-11-12: the other
  limits let ``x.{0,50}x.{0,50}x.{0,50}x.{0,50}x.{0,50}y`` through, which takes minutes on a line of 200 characters).

``guard_scan.risky`` runs as well and has the last word. Pure: standard library and ``guard_scan`` only.
"""
from __future__ import annotations

import re

from brevio.pipeline.gates import guard_scan

MAX_LENGTH = 120
MIN_LITERALS = 6
MAX_UNBOUNDED = 2       # stricter than ``guard_scan``'s limit: a model's pattern is untrusted
MAX_COUNT = 50
MAX_PATHS = guard_scan.REGEX_MAX_LINE ** 2     # two repeats that run to the end of a line, the most the other limit lets through
_BRACES = re.compile(r"\{(\d*)(,?)(\d*)\}")       # Python reads {m}, {m,}, {,n}, {m,n} and {,} as repeats, and {} as text
_ESCAPES = frozenset("dDwWsSbB")        # the letters that may follow a backslash; a digit is a backreference or an octal


class _Reader:
    """One left-to-right pass over a pattern; ``why`` holds the first reason it is not accepted."""

    def __init__(self, expr: str) -> None:
        self.expr, self.i, self.why = expr, 0, ""
        self.busy = [False]         # per open group: it holds a repeat or an alternative
        self.last = "none"          # what the previous item was: none, atom, open, group, busyGroup or repeat
        self.literals = self.unbounded = 0
        self.alternatives = [0]     # per open group: how many ``|`` it holds
        self.paths = 1              # the ways a line can be matched, as far as repeats and alternatives multiply them

    def fail(self, why: str) -> None:
        self.why = self.why or why

    def read(self) -> str:
        while self.i < len(self.expr) and not self.why:
            c = self.expr[self.i]
            if c == "\\":
                self.escape()
            elif c == "[":
                self.char_class()
            elif c == "(":
                self.open()
            elif c == ")":
                self.close()
            elif c == "|":
                self.busy[-1], self.last, self.i = True, "none", self.i + 1
                self.alternatives[-1] += 1
            elif c in "*+?" or (c == "{" and _brace(self.expr, self.i)):
                self.repeat()
            else:
                self.literals += c not in ".^$"
                self.last, self.i = "atom", self.i + 1
        return self.why or ("a group is not closed" if len(self.busy) > 1 else "")

    def escape(self) -> None:
        nxt = self.expr[self.i + 1:self.i + 2]
        if not nxt:
            return self.fail("a backslash ends the pattern")
        if nxt.isdigit():
            return self.fail("a backreference or a numbered escape")
        if nxt.isalpha() and nxt not in _ESCAPES:
            return self.fail(f"the escape \\{nxt}")
        self.literals += not nxt.isalpha()
        self.last, self.i = "atom", self.i + 2

    def char_class(self) -> None:
        j = self.i + 1
        j += 1 if self.expr[j:j + 1] == "^" else 0
        j += 1 if self.expr[j:j + 1] == "]" else 0
        while j < len(self.expr) and self.expr[j] != "]":
            j += 2 if self.expr[j] == "\\" else 1
        if j >= len(self.expr):
            return self.fail("a character class is not closed")
        self.last, self.i = "atom", j + 1

    def open(self) -> None:
        if self.expr.startswith("(?", self.i) and not self.expr.startswith("(?:", self.i):
            return self.fail("a group form other than a plain or (?:...) group")
        self.busy.append(False)
        self.alternatives.append(0)
        self.last, self.i = "open", self.i + (3 if self.expr.startswith("(?:", self.i) else 1)

    def close(self) -> None:
        if len(self.busy) == 1:
            return self.fail("a closing bracket with no group")
        inner = self.busy.pop()
        self.paths *= self.alternatives.pop() + 1
        self.busy[-1] = self.busy[-1] or inner      # a group inside a group is part of what the outer one holds
        self.last, self.i = ("busyGroup" if inner else "group"), self.i + 1

    def repeat(self) -> None:
        brace = _brace(self.expr, self.i) if self.expr[self.i] == "{" else None
        if self.last in ("none", "open"):
            return self.fail("a repeat with nothing to repeat")
        if self.last == "repeat":
            return self.fail("a repeat after a repeat (lazy and possessive forms are not read)")
        if self.last == "busyGroup":
            return self.fail("a group that holds a repeat or an alternative is repeated")
        if brace and max(int(brace[1] or 0), int(brace[3] or 0)) > MAX_COUNT:
            return self.fail(f"a count in braces over {MAX_COUNT}")
        self.unbounded += self.expr[self.i] in "*+" or bool(brace and brace[2] and not brace[3])
        self.paths *= _choices(self.expr[self.i], brace)
        self.busy[-1], self.last = True, "repeat"
        self.i += brace.end() - self.i if brace else 1


def _brace(expr: str, i: int) -> re.Match[str] | None:
    m = _BRACES.match(expr, i)
    return m if m and m.group(0) != "{}" else None


def _choices(sign: str, brace: re.Match[str] | None) -> int:
    """How many lengths one repeat can take on a line of the longest length the scan reads."""
    longest = guard_scan.REGEX_MAX_LINE
    if brace is None:
        return 2 if sign == "?" else longest
    low, comma, high = int(brace[1] or 0), brace[2], brace[3]
    return (int(high) - low + 1 if high else longest) if comma else 1


def problem(pattern: str) -> str:
    """Why ``pattern`` is not accepted as a guard pattern, or ``""`` when it is. The message holds no part of the pattern."""
    if not pattern.strip() or pattern != pattern.strip():
        return "the pattern is empty or has spaces at its ends"
    if len(pattern) > MAX_LENGTH or not pattern.isprintable():
        return f"the pattern is over {MAX_LENGTH} characters or holds a character that does not print"
    reader = _Reader(pattern)
    why = reader.read()
    if why:
        return why
    if reader.unbounded > MAX_UNBOUNDED:
        return f"more than {MAX_UNBOUNDED} repeats without an upper end"
    if reader.paths > MAX_PATHS:
        return "its repeats and alternatives can be tried in too many ways on one line"
    if reader.literals < MIN_LITERALS:
        return f"fewer than {MIN_LITERALS} plain characters, so it would match many lines"
    try:
        re.compile(pattern)
    except re.error:
        return "it is not a regular expression Python reads"
    return guard_scan.risky(pattern)
