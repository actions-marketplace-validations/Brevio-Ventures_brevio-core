"""Mechanical fixes for the code rules that have exactly one correct rewrite (ROADMAP E16, the 0.3 set, 2026-11-05).

Each fixer is declared for one rule id, the file suffixes it applies to, a pattern and its replacement. A fixer runs
on a line only when its rule fires on that line, and its output must no longer trigger the rule (tested). Rules
without a fixer are reported unchanged: a secret (R13.1), an empty catch, ``as any``, ``eval`` or a missing ``alt``
need a person to decide, so no fix is invented for them. Standard library only; no model call, no network.
"""
from __future__ import annotations

import os
import re
from typing import NamedTuple

from brevio.pipeline.code_rules import handler_body, only_passes, scan_lines


class Fixer(NamedTuple):
    suffixes: tuple[str, ...]
    pattern: re.Pattern[str]
    replacement: str
    summary: str

    def __call__(self, line: str) -> str:
        return self.pattern.sub(self.replacement, line)


_TARGET_BLANK_FIX_RX = re.compile(r"""(<a\b(?![^>]*\brel\s*=)[^>]*?\btarget\s*=\s*["']_blank["'])""", re.I)


def fix_target_blank(line: str) -> str:
    """Insert ``rel="noopener noreferrer"`` right after ``target="_blank"`` on ``<a>`` tags that lack ``rel=``."""
    return _TARGET_BLANK_FIX_RX.sub(r'\1 rel="noopener noreferrer"', line)


_REQUESTS_TIMEOUT_FIX_RX = re.compile(
    r"^([^()\n]*\brequests\.(?:get|post|put|delete|patch|head)\s*\([^()\n]+)(\).*)$"
)

fix_requests_timeout = Fixer(
    (".py",),
    _REQUESTS_TIMEOUT_FIX_RX,
    r"\1, timeout=30\2",
    "single-line requests call gets `, timeout=30`",
)


FIXERS: dict[str, Fixer] = {
    # Only the comment directive is rewritten; ``@ts-ignore`` inside a string stays a finding for a person to read.
    "R9.2": Fixer((".ts", ".tsx", ".js", ".jsx"), re.compile(r"(//|/\*)(\s*)@ts-ignore\b"), r"\1\2@ts-expect-error",
                  "`@ts-ignore` comment becomes `@ts-expect-error`"),
    # ``except Exception:`` names the errors a program can recover from and no longer swallows Ctrl-C or a system exit.
    # ``except BaseException:`` was the rewrite until 2026-11-13: it changed nothing a reader could see.
    "R5.4": Fixer((".py",), re.compile(r"^(\s*)except\s*:"), r"\1except Exception:",
                  "bare `except:` becomes `except Exception:` (it no longer catches Ctrl-C or a system exit)"),
    "R10.1": fix_requests_timeout,
    "R18.2": Fixer((".tsx", ".jsx", ".html", ".vue", ".svelte"), _TARGET_BLANK_FIX_RX,
                   r'\1 rel="noopener noreferrer"',
                   '`target="_blank"` link gets `rel="noopener noreferrer"`'),
}
_RULE = re.compile(r"\[([^\]]+)\]")


def rules_on(rel_path: str, line: str) -> set[str]:
    """The rule ids that fire on one line of ``rel_path``."""
    prefix = f"{rel_path}:1: "
    return {m.group(1) for f in scan_lines(rel_path, [(1, line)]) if (m := _RULE.match(f, len(prefix)))}


def fix_line(rel_path: str, line: str, numbered: list[tuple[int, str]] | None = None, lno: int = 1) -> tuple[str, list[str]]:
    """``line`` with every applicable fixer applied, and the rule ids it fixed (sorted). Given the ``numbered`` lines of
    the file and this line's number ``lno``, a bare ``except:`` whose handler re-raises is left alone, as the scan
    leaves it: the line alone cannot tell (found in review, 2026-11-13). A bare ``except:`` whose handler only passes
    is left alone too: ``except Exception:`` + ``pass`` is still a finding, so only a person can decide (2026-11-13)."""
    ext = os.path.splitext(rel_path)[1].lower()
    fired, fixed = rules_on(rel_path, line), []
    if "R5.4" in fired:
        if numbered is not None and not any(
                f.startswith(f"{rel_path}:{lno}: [R5.4]") for f in scan_lines(rel_path, numbered)):
            fired.discard("R5.4")
        elif only_passes(handler_body(numbered if numbered is not None else [(lno, line)], lno) or []):
            fired.discard("R5.4")
    for rule, fixer in sorted(FIXERS.items()):
        if rule in fired and ext in fixer.suffixes:
            new = fixer.pattern.sub(fixer.replacement, line)
            if new != line:
                line = new
                fixed.append(rule)
    return line, fixed
