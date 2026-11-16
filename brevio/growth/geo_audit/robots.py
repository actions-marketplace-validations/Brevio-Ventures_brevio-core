"""The one robots.txt parser behind every GEO audit view of crawler access.

Three readers used to carry their own parse of the same file. They now share ``directives`` (the
line tokenizer) and keep their own answers: ``parse_robots`` / ``bot_blocked`` (grouped rules, the
scorecard), ``blocked_bots`` (which listed crawlers a whole-site ``Disallow: /`` shuts out, the
rubric) and ``ai_rules`` (blocked | allowed | not_specified per crawler, the competitor probe).
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator


def directives(text: str) -> Iterator[tuple[str, str]]:
    """Every ``key: value`` line of a robots.txt as ``(lower key, value)``, comments stripped."""
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" in line:
            key, _, val = line.partition(":")
            yield key.strip().lower(), val.strip()


def parse_robots(text: str) -> dict[str, list[tuple[str, str]]]:
    """Minimal robots.txt parser → {user_agent_lower: [(directive, path), ...]}.

    Consecutive `User-agent:` lines share the rules that follow, so a block like
    `User-agent: GPTBot\\nUser-agent: CCBot\\nDisallow: /` groups correctly.
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    pending: list[str] = []
    current: list[tuple[str, str]] = []
    for key, val in directives(text):
        if key == "user-agent":
            ua = val.lower()
            if ua not in pending:  # a new UA after rules starts a fresh block
                if not pending or current:
                    pending = []
                pending.append(ua)
            groups.setdefault(ua, [])
            current = groups[ua]
        elif key in ("disallow", "allow"):
            for ua in pending:
                groups.setdefault(ua, []).append((key, val))
            current = groups[pending[-1]] if pending else current
    return groups


def bot_blocked(groups: dict[str, list[tuple[str, str]]], bot: str) -> bool:
    """True if `bot` (or the `*` group it inherits from) is blanket-disallowed."""
    rules = groups.get(bot.lower())
    if rules is None:
        rules = groups.get("*")
    if not rules:
        return False
    disallow_all = any(d == "disallow" and p == "/" for d, p in rules)
    allow_all = any(d == "allow" and p == "/" for d, p in rules)
    return disallow_all and not allow_all


def blocked_bots(text: str, crawlers: Iterable[str]) -> list[str]:
    """The ``crawlers`` a whole-site ``Disallow: /`` shuts out, in the order given. A crawler with its
    own group is judged by that group alone, otherwise by ``*``; a group with ``Allow: /`` is open.
    Rebuilt on ``parse_robots`` so a group of several agents and an own group that overrides ``*``
    read true (second review 2026-11-14, growth-small-F1)."""
    groups = parse_robots(text)
    return [bot for bot in crawlers if bot_blocked(groups, bot)]


def ai_rules(robots_txt: str, bots: Iterable[str]) -> dict:
    """``{bot: blocked | allowed | not_specified}``, names matched without regard to case. Blocked only
    on a whole-site ``Disallow: /`` (a substring test once read ``Disallow: /admin`` as a full block,
    plan-llm 14); allowed when the crawler or ``*`` has a group that does not block it."""
    groups = parse_robots(robots_txt)
    rules = {}
    for bot in bots:
        if bot_blocked(groups, bot):
            rules[bot] = "blocked"
        elif bot.lower() in groups or "*" in groups:
            rules[bot] = "allowed"
        else:
            rules[bot] = "not_specified"
    return rules
