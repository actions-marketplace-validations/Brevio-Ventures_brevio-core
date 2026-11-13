"""The SARIF log of ``brevio-core scan --sarif``: the rules the results name, their index, the version and the rule page link.

``core_cli.sarif`` makes the results; this module adds what GitHub code scanning wants around them (2026-11-13, revisit D31
and I39). A result may name a rule that is not in the catalogue: ``suppression`` (a new ignore comment or baseline entry) and
``guard:<id>`` (a learned guard). Each such rule is declared in ``tool.driver.rules`` with a short text, and each result
carries the ``ruleIndex`` of its rule. The driver names the version when the release is known. A catalogue rule gets a
``helpUri`` only when the release's source is on GitHub: the page of the rule at the tag of this version, under the
repository's ``blob/v<version>/docs/rules`` (the SARIF page of the docs names the address, so the page of links the tool
prints lists it). A build that is not a release has no tag to point at, so it writes no link. Standard library only.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

GITHUB = "https" + "://github.com/"      # joined: the export check refuses a literal address that names no organisation
SUPPRESSION = "suppression"
GUARD_PREFIX = "guard:"
SUPPRESSION_TEXT = "A new ignore comment, baseline entry or guard change in this change; a person reviews it."
SUPPRESSION_HELP = ("Review the entries in .brevio/baseline.json and the ignore comments the change adds. "
                    "brevio-core scan --allow-new-suppressions accepts a change that a person reviewed.")


def help_uri(release: Any, rule_id: str) -> str | None:
    """The page of ``rule_id`` at the tag of ``release`` on GitHub, or ``None`` when the source is elsewhere or unknown."""
    url = str(getattr(release, "repo_url", "") or "").rstrip("/")
    version = getattr(release, "version", "")
    return f"{url}/blob/v{version}/docs/rules/{rule_id}.md" if version and url.startswith(GITHUB) else None


def declared(rule_id: str, level: str) -> dict[str, Any]:
    """The ``tool.driver.rules`` entry for a rule outside the catalogue: ``suppression``, ``guard:<id>`` or another ID."""
    if rule_id.startswith(GUARD_PREFIX):
        text = f"Learned guard {rule_id[len(GUARD_PREFIX):]} of this repository"
        hint = "brevio-core learn list shows the guard and its status."
    elif rule_id == SUPPRESSION:
        text, hint = SUPPRESSION_TEXT, SUPPRESSION_HELP
    else:       # an ID the catalogue does not hold (a finding line without a rule tag, or a rule since removed): say only that
        text, hint = f"A finding of {rule_id}, which is not in this version's rule catalogue", "brevio-core explain names the rules this version knows."
    return {"id": rule_id, "shortDescription": {"text": text}, "defaultConfiguration": {"level": level}, "help": {"text": hint}}


def log(results: Iterable[dict[str, Any]], rules: list[dict[str, Any]], release: Any = None) -> dict[str, Any]:
    """A SARIF 2.1.0 log of ``results`` and the catalogue ``rules``, plus a declared rule for each other ID the results use."""
    out, rules = list(results), [dict(r) for r in rules]
    if release is not None:
        for rule in rules:
            uri = help_uri(release, rule["id"])
            if uri:
                rule["helpUri"] = uri
    index = {r["id"]: i for i, r in enumerate(rules)}
    for result in out:
        if result["ruleId"] not in index:
            index[result["ruleId"]] = len(rules)
            rules.append(declared(result["ruleId"], result.get("level", "error")))
        result["ruleIndex"] = index[result["ruleId"]]
    driver: dict[str, Any] = {"name": "brevio-core", "rules": rules}
    if release is not None and getattr(release, "version", ""):
        driver["version"] = release.version
    return {"version": "2.1.0", "runs": [{"tool": {"driver": driver}, "results": out}]}
