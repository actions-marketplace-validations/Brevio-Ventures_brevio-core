"""The rules block that ``brevio-core init`` writes for coding agents, with the one command that runs the tool when it is not installed.

An agent in a repository that has the block but not the tool would otherwise read a command that does not run (2026-11-12,
improvement plan). The block is kept short: the five rules of the skill, and on the first of them one sentence that names the
command pinned to this release. A pinned command runs the release a person chose and not whatever is newest, which is what a
person who reads an agent file wants to see before the agent runs a downloaded package. When the release is not known, no
command is written, because there is nothing to pin. Standard library only.
"""
from __future__ import annotations

from brevio.pipeline.gates import agent_skill

PINNED = " If it is not installed, run `uvx {package}=={version} scan <files>`."


def rules(package: str, version: str | None) -> str:
    """The skill's five rules with the pinned command added to the end of the first line; unchanged when ``version`` is None."""
    first, sep, rest = agent_skill.AGENT_RULES.partition("\n")
    return first + (PINNED.format(package=package, version=version) if version else "") + sep + rest
