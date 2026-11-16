"""``brevio-core ai summarize --diff BASE``: one call that asks what kinds of finding a change has (preview; the 0.7 set,
improvement plan, 2026-11-12).

The request is built from the findings of a scan of the diff and from nothing else: each rule's ID, its title in the catalogue
and how many findings it has. No source line, no file name, no path and no title of a learned guard goes in it; a finding of a
rule the catalogue does not list is counted and not named. The answer is what a model wrote: it is escaped as inert Markdown
with the escape of ``core_markdown`` and printed inside a closed block that says it is model-written and not part of the
gate. It changes no exit code and no finding. Same consent, limits and secret check as the other commands; one call, no retry.
Standard library and exported modules only; it reaches the network only through the transport that ``core_ai`` loads.
"""
from __future__ import annotations

import argparse
import subprocess
from collections import Counter
from collections.abc import Callable, Mapping

from brevio.pipeline import core_ai as flow
from brevio.pipeline import core_ai_verify as verify
from brevio.pipeline import core_cli, core_markdown
from brevio.pipeline.code_rules import PR_RULES_122
from brevio.pipeline.rule_host import GIT

SYSTEM = ("You summarise the output of a static checker for a reviewer. Answer briefly, in plain words, from the counts you "
          "are given only.")
LABEL = "Summary, model-written and not part of the gate"
QUOTE = "> "


def counts(findings: list[str]) -> tuple[Counter[str], int]:
    """``(findings per rule of the catalogue, findings of anything else)`` for the findings a scan printed."""
    named: Counter[str] = Counter()
    other = 0
    for line in findings:
        fields = core_cli.finding_fields(line)
        rule = str(fields["rule"]) if fields else ""
        if rule in PR_RULES_122:
            named[rule] += 1
        else:
            other += 1
    return named, other


def build_prompt(named: Counter[str], other: int) -> str:
    """The request: rule IDs, titles and counts. Nothing from the code."""
    rows = [f"{rule} {PR_RULES_122[rule]}: {n}" for rule, n in sorted(named.items())]
    total = sum(named.values()) + other
    rest = f" {other} more are from rules or guards this list does not name." if other else ""
    return ("A static checker scanned the lines a change adds. Its findings, as rule ID, rule title and number of findings:\n"
            + "\n".join(rows) + f"\n{total} findings in all.{rest}\n"
            "In at most five short sentences, say what kinds of mistake these are and what a reviewer should look at first. "
            "Name no file and no line: you were given none.\n")


def _resolves(base: str, root: str) -> bool:
    """``base`` names a commit of the repository (a read-only ``git rev-parse``)."""
    try:
        done = subprocess.run([*GIT, "-C", root, "rev-parse", "--verify", "-q", f"{base}^{{commit}}"], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _findings(base: str, root: str) -> list[str]:
    if not _resolves(base, root):
        raise flow.Stop(f"{base} is not a commit or branch of this repository: nothing was scanned", flow.EXIT_USAGE)
    try:
        lines = core_cli.select_lines([], base)
        shown = core_cli.triage(core_cli.scan(lines, core_cli.project(lines)), "new", True)[0]
    except core_cli.UsageError as exc:
        raise flow.Stop(str(exc), flow.EXIT_USAGE) from exc
    if not shown:
        raise flow.Stop("the scan of the diff finds nothing, so there is nothing to summarise", flow.EXIT_OK)
    return list(shown)


def inert(text: str) -> list[str]:
    """The model's answer, one inert Markdown line for each of its lines."""
    return [core_markdown.escape(flow.safe(line)) for line in text.split("\n")]


def framed(p: flow.Prepared, text: str, fmt: str) -> str:
    """The answer in a closed block. ``text``: every line behind ``| ``; ``markdown``: behind ``> ``. The opening line says it
    is model-written and not part of the gate; the closing line is one the answer cannot imitate."""
    lines = inert(text)
    if fmt == "markdown":
        who = (f"the program of {core_markdown.escape(p.connector.name)}" if p.connector.kind == "command"
               else f"{core_markdown.escape(p.connector.name)}, model {core_markdown.escape(p.connector.model)}")
        head = f"**{LABEL}.** Written by {who}; brevio-core did not write it."
        body = [QUOTE + line for line in lines]
        return "\n".join([head, "", *body, "", f"*End of the model-written summary: every line above starts with \"{QUOTE.strip()}\".*"])
    return flow.labelled(p, LABEL, "\n".join(lines))


def run(args: argparse.Namespace, env: Mapping[str, str], ask: Callable[[str], str] | None) -> int:
    cfg, connector, root, _policy = flow.session("summarize", env)
    named, other = counts(_findings(args.diff, root))
    flow.check_connector(connector, env)
    prompt = build_prompt(named, other)
    p = flow.Prepared(cfg, connector, root, None, prompt, SYSTEM)
    hit = verify.secret_lines(list(enumerate(prompt.split("\n"), 1)))
    if hit:
        raise flow.Stop("not sent: a line of the request looks like a secret; the request is not shown here")
    flow.refuse_limits(p)
    text = flow.exchange("summarize", p, env, ask, args.dry_run, args.yes)
    if text is None:
        return flow.EXIT_OK
    print(framed(p, text, args.format))
    return flow.EXIT_OK
