"""Which parts of ``brevio-core`` a build ships, and how they join the command line (split from ``core_cli``, 2026-11-13).

Each later export set adds modules; this module finds them by name, loads them only when asked and returns ``None``
or empty tables when the build does not ship one. It also lists the commands those modules bring and adds the
scan flags the baseline and the Markdown report need. Standard library only, so it exports with ``core_cli``.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
from collections.abc import Callable
from typing import Any

AGENT_MAX = 20          # finding lines an agent is shown at once; a longer list is cut, so one reply stays short
FORMATS = ("text", "markdown", "agent", "json", "github")

FIXERS_MODULE = "fixers"   # the module of the 0.3 set that declares the fixers; a build without it offers no ``--fix``
BASELINE_MODULE, MARKDOWN_MODULE = "core_baseline", "core_markdown"   # the 0.4 set; a build without them has no baseline
CONFIG_MODULE = "core_config"   # the 0.6 set's brevio-core.toml (ROADMAP E32); a build without it reads no config


def set_module(package: str, name: str) -> Any:
    """The module ``name`` of ``package`` from a later export set, or ``None`` in a build that does not ship it."""
    top = "brevio" if __name__.startswith("__main__") else __name__.split(".")[0]
    try:
        return importlib.import_module(f"{top}.{package}.{name}")   # a name built at run time: absent from earlier sets
    except ModuleNotFoundError as err:
        if not (err.name or "").startswith(f"{top}.{package}"):
            raise
        return None


def fixers() -> Any:
    """The fixer module of the 0.3 set, or ``None`` in a build that does not ship it."""
    return set_module("pipeline.code_rules", FIXERS_MODULE)


def baseline_tools() -> Any:
    """The baseline and ignore module of the 0.4 set, or ``None`` in a build that does not ship it."""
    return set_module("pipeline", BASELINE_MODULE)


def config_tools() -> Any:
    """The ``brevio-core.toml`` module of the 0.6 set, or ``None`` in a build that does not ship it."""
    return set_module("pipeline", CONFIG_MODULE)


AGENT_TOOLS = "agent_cmds"      # the module of the 0.2 set that holds the commands; named here so a build without it is detected


def agent_tools() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    """``(commands, help)`` of the 0.2 agent tools, or two empty tables in a build that does not ship them."""
    mod = set_module("operator", AGENT_TOOLS)
    return (mod.COMMANDS, mod.HELP) if mod is not None else ({}, {})


INIT_MODULE = "core_init"       # the 0.4 set's ``init`` and ``init --undo`` (ROADMAP E22); a build without it has no ``init``
UP_MODULE = "core_up"           # the 0.6 set's ``up`` preview and upgrade line (ROADMAP E37); a build without it has neither
AEO_MODULE = "core_aeo"         # the 0.6 set's ``aeo``, the AEO rubric on local pages (ADOPTION.md 0.29); absent before it
CHECK_MODULE = "core_gates"     # the 0.6 set's ``check`` pre-PR flight check
BRIEF_MODULE = "core_memory"    # the 0.6 set's ``brief`` surface rules & trap recall
SCORE_MODULE = "core_score"     # the 0.6 set's ``score`` (preview): findings counted by rule
MAP_MODULE = "core_map"
HEAL_MODULE = "core_heal"
SHIELD_MODULE = "core_shield"
TASK_MODULE = "core_task"
ROAST_MODULE = "core_roast"     # ``roast``, the same counts as ``score`` under its old name: a module of its own, so a trimmed release can leave it out
PATCH_MODULE = "core_patch"     # the 0.6 set's ``patch``, an edit written only when the rules find it clean (2026-11-11)
AI_MODULE = "core_ai"           # the 0.7 set's ``ai``, the one command that may call a model: found by name, loaded only when it runs (2026-11-12)
AI_HELP = "ask a model, or a program, you connect (connect, status, explain, fix, summarize, learn); asks before it sends"   # not in the module: listing loads nothing


def _opt_cmd(cmd: str, pkg: str, mod_name: str) -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    mod = set_module(pkg, mod_name)
    if mod is None or not hasattr(mod, "main"):
        return ({}, {})
    hlp = getattr(mod, "HELP", f"run brevio-core {cmd}")
    return ({cmd: mod.main}, {cmd: hlp})


def init_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    """``({"init": main}, {"init": help})`` in a build that ships ``init``, else two empty tables."""
    return _opt_cmd("init", "pipeline", INIT_MODULE)


def up_tools() -> Any:
    """The ``up`` module of the 0.6 set (the preview and the upgrade line), or ``None`` in a build without it."""
    return set_module("pipeline", UP_MODULE)


def up_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    """``({"up": main}, {"up": help})`` in a build that ships ``up``, else two empty tables."""
    return _opt_cmd("up", "pipeline", UP_MODULE)


def upgrade_line(ref: str, wanted: bool = True) -> str:
    """The labelled Teams line for ``ref``, or "" when it is turned off or the build does not ship ``up``."""
    up = up_tools() if wanted else None
    return up.upgrade_line(ref) if up is not None else ""


def aeo_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("aeo", "pipeline", AEO_MODULE)


def check_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("check", "pipeline.gates", CHECK_MODULE)


def brief_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("brief", "pipeline", BRIEF_MODULE)


def score_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("score", "pipeline", SCORE_MODULE)


def map_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("map", "pipeline", MAP_MODULE)


def heal_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("heal", "pipeline", HEAL_MODULE)


def shield_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("shield", "pipeline", SHIELD_MODULE)


def patch_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("patch", "pipeline", PATCH_MODULE)


def ai_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    top = "brevio" if __name__.startswith("__main__") else __name__.split(".")[0]
    found = importlib.util.find_spec(f"{top}.pipeline.{AI_MODULE}") is not None      # found, not loaded (2026-11-12, claims check)
    return ({"ai": lambda argv: int(set_module("pipeline", AI_MODULE).main(argv))}, {"ai": AI_HELP}) if found else ({}, {})


def has_ai() -> bool:
    """Whether this tree holds the ``ai`` command (found, not loaded): ``hook install`` writes the skill text of its set."""
    return bool(ai_command()[0])


def task_commands() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    mod = set_module("pipeline", TASK_MODULE)
    if mod is None:
        return ({}, {})
    cmds: dict[str, Callable[[list[str]], int]] = {}
    helps: dict[str, str] = {}
    if hasattr(mod, "main_connect"):
        cmds["connect"] = mod.main_connect
        helps["connect"] = getattr(mod, "CONNECT_HELP", "detect stack and coding clients and export CLAUDE.md, CONSTITUTION.md & roadmap.json")
    if hasattr(mod, "main_plan"):
        cmds["plan"] = mod.main_plan
        helps["plan"] = getattr(mod, "PLAN_HELP", "create a single-task implementation plan (.brevio/task_plan.json & .brevio/TASK_PLAN.md)")
    if hasattr(mod, "main_build"):
        cmds["build"] = mod.main_build
        helps["build"] = getattr(mod, "BUILD_HELP", "execute single-task build preparation and deterministic auto-fixes")
    if hasattr(mod, "main_qa"):
        cmds["qa"] = mod.main_qa
        helps["qa"] = getattr(mod, "QA_HELP", "run the single-task pre-PR quality gates and safety scorecard")
    if hasattr(mod, "main_pr"):
        cmds["pr"] = mod.main_pr
        helps["pr"] = getattr(mod, "PR_HELP", "run the QA gates and write a pull-request body from what they found (.brevio/PR_BODY.md)")
    if hasattr(mod, "main"):
        cmds["task"] = mod.main
        helps["task"] = getattr(mod, "HELP", "run the single-task local workflow (connect -> plan -> build -> qa -> pr)")
    return (cmds, helps)


def roast_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("roast", "pipeline", ROAST_MODULE)


def add_baseline_arguments(sub: Any, sc: argparse.ArgumentParser) -> None:
    """The 0.4 set: ``baseline`` and the scan flags that read it."""
    tools = baseline_tools()
    by_level = config_tools() is not None       # the 0.6 set: levels from brevio-core.toml
    sc.add_argument("--fail-on", choices=(*tools.FAIL_ON, *(("error",) if by_level else ())), default="new",
                    help="new (default): baselined findings are hidden and pass; all: ignore the baseline, any finding "
                         "fails; none: report what is not baselined and always exit 0"
                         + ("; error: like new, but only error-level findings fail (warn-level ones are reported)"
                            if by_level else "") + ". A preview rule does not fail a run; a learned guard fails one only at error level")
    sc.add_argument("--allow-new-suppressions", action="store_true",
                    help="with --diff: do not report the ignore comments and baseline entries the change adds "
                         "(for a change a person has reviewed)")
    if set_module("pipeline", MARKDOWN_MODULE) is not None:
        sc.add_argument("--format", choices=FORMATS, default="text",
                        help="how to print the findings (agent: one short line each with a fix hint, grouped by rule, "
                             f"at most {AGENT_MAX})")
        sc.add_argument("--rules-url", default="", metavar="BASE", help="with --format markdown: link each rule ID to BASE<ID>")
        sc.add_argument("--facts", action="store_true",
                        help="with --format markdown: end the report with one line of the facts of this run (version, rules that ran, files read, base, head, time)")
        sc.add_argument("--no-reproduce", action="store_true",
                        help="with --facts: leave out the command that repeats the scan (the Action sets it when it installs from a source)")
        if up_tools() is not None:
            sc.add_argument("--no-upgrade-line", action="store_true",
                            help="with --format markdown: leave out the closing Teams line (the Action's footer: false)")
    bl = sub.add_parser("baseline", help=f"record the current findings in {tools.BASELINE_PATH}, so scan reports only new ones")
    bl.add_argument("paths", nargs="*", help="files or folders (default: the current folder)")
    bl.add_argument("--diff", nargs="?", const="", default=None, metavar="BASE",
                    help="record only the findings on the lines added against BASE (default origin/main, else main, else HEAD); a BASE the checkout does not have exits 2")
    bl.add_argument("--prune", action="store_true", help="only drop the entries no finding matches any more, and list them")


def line_commands() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    """``(commands, help)`` of every command that reads the rest of its line itself, in the order ``--help`` lists them:
    the agent tools, ``init``, ``aeo``, ``check``, ``brief``, ``score`` and ``up``, each where the build ships it."""
    tools, tool_help = agent_tools()
    for more, more_help in (
        init_command(),
        aeo_command(),
        check_command(),
        brief_command(),
        score_command(),
        roast_command(),
        map_command(),
        heal_command(),
        shield_command(),
        patch_command(),
        ai_command(),
        task_commands(),
        up_command(),
    ):
        tools, tool_help = {**tools, **more}, {**tool_help, **more_help}
    if "learn" in tools:
        orig_learn = tools["learn"]

        def _learn_wrapped(argv: list[str]) -> int:
            rc = orig_learn(argv)
            if rc == 0:
                try:
                    skill_mod = set_module("pipeline.gates", "agent_skill")
                    if skill_mod is not None and hasattr(skill_mod, "sync_learned_guards_to_skill"):
                        skill_mod.sync_learned_guards_to_skill(".")
                except Exception:
                    pass
            return rc

        tools["learn"] = _learn_wrapped
    return tools, tool_help
