"""The findings of ``brevio-core scan --format markdown``, for a pull request comment or a job summary (ROADMAP E26, 2026-11-05).

The Action writes this text to the job summary and the pull request comment, so the text is made and tested here
and not in shell. Paths and messages come from the scanned repository and may be hostile: every character that could
make a mention, a link, an HTML tag, a table cell or a workflow command is escaped (plan review, 2026-11-05). The text
holds no number but counts, file lines and rule IDs. Standard library only (the 0.4 set).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

HEADING = "### brevio-core"
MAX_ROWS = 50
_SAFE_ID = re.compile(r"[A-Z]+\d+(?:\.\d+)*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]+")
_BACKSLASHED = re.compile(r"([\\`*_{}\[\]()#+!|~$])")
_ZW = "​"     # a zero-width space: it breaks a mention, an autolink or a workflow command and shows as nothing
_BREAKS = ((re.compile(r"@"), "@" + _ZW), (re.compile(r"(?i)(https?|ftp|file|mailto|javascript|data):"), r"\1" + _ZW + ":"),
           (re.compile(r"(?i)\bwww\."), "www" + _ZW + "."), (re.compile(r"::"), ":" + _ZW + ":"))


def escape(text: str) -> str:
    """``text`` as inert Markdown: one line, no HTML, no mention, no link, no emphasis, no cell break."""
    out = _CONTROL.sub(" ", str(text)).strip()
    out = out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = _BACKSLASHED.sub(r"\\\1", out)
    for rx, repl in _BREAKS:
        out = rx.sub(repl, out)
    return out


def _rule_cell(rule: str, rules_url: str) -> str:
    if rules_url and _SAFE_ID.fullmatch(rule):
        return f"[{rule}]({rules_url}{rule})"
    return escape(rule)


def _table(rows: list[dict[str, Any]], rules_url: str) -> list[str]:
    """The Rule, Where and Why table of ``rows``, cut at ``MAX_ROWS``, with the line that counts the rest."""
    lines = ["| Rule | Where | Why |", "| --- | --- | --- |"]
    lines += [f"| {_rule_cell(str(f['rule']), rules_url)} | {escape(str(f['path']) + ':' + str(f['line']))} | "
              f"{escape(f['message'])} |" for f in rows[:MAX_ROWS]]
    if len(rows) > MAX_ROWS:
        lines += ["", f"and {len(rows) - MAX_ROWS} more."]
    return lines


def render(findings: Iterable[dict[str, Any]], silenced: int = 0, baselined: int = 0, rules_url: str = "",
           footer: str = "", files: int | None = None, added_only: bool = False) -> str:
    """A heading, then a table of rule ID, ``file:line`` and why, cut at ``MAX_ROWS`` rows; or the no-findings form.

    ``footer`` is a closing line of ours (the 0.6 set's Teams line, ROADMAP E37), added as it is, never escaped. With
    ``files`` ``0`` the scan read no line, and the report says so instead of "No findings." (I1). ``added_only`` says the
    scan read the lines a change adds, so the table does not list the rest of a file (2026-11-13, revisit I16)."""
    rows = list(findings)
    lines = [HEADING, ""]
    if not rows:
        lines.append("No lines were read." if files == 0 else "No findings.")
    else:
        lines += [f"{len(rows)} finding(s).", "", *_table(rows, rules_url)]
        if added_only:
            lines += ["", "The table lists added lines only."]
    hidden = [f"{n} {word}" for n, word in ((silenced, "silenced"), (baselined, "baselined")) if n]
    if hidden:
        lines += ["", f"Not shown: {', '.join(hidden)}."]
    if footer:
        lines += ["", footer]
    return "\n".join(lines) + "\n"


CUSTODY_HEADING = "### brevio-core test custody"
_FINDING = re.compile(r"^(?P<path>.+?):(?P<line>\d+): (?:\[(?P<rule>[^\]]+)\] )?(?P<message>.*)$", re.S)
CUSTODY_MODES = ("off", "report", "fail")


def custody_section(found: Iterable[str] | None, mode: str = "report", rules_url: str = "") -> str:
    """The test-custody part of a pull request comment (the ``custody`` input of the Action, 2026-11-13, revisit D54).

    ``found`` is the ``test_custody`` list of ``brevio-core check --format json`` (``file:line: [R20.x] message``), or
    ``None`` when the check did not run. Only the R20 rules are listed: ``check`` runs others, which a person runs with
    ``brevio-core check --base <ref>``. ``mode`` ``fail`` says the findings fail the check; ``report`` says they do not."""
    lines = [CUSTODY_HEADING, ""]
    if found is None:
        return "\n".join([*lines, "The test-custody check did not run; the job log says why.", ""])
    rows = [m.groupdict() for m in map(_FINDING.match, found) if m]
    if not rows:
        return "\n".join([*lines, "No test-custody findings.", ""])
    lines += [f"{len(rows)} finding(s) in the test files this change touches.", "", *_table(rows, rules_url), ""]
    lines.append("These findings fail the check." if mode == "fail" else "These findings are reported and do not fail the check.")
    return "\n".join([*lines, ""])
