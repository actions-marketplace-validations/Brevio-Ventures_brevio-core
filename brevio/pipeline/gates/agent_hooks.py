"""Agent Hooks Installer (Master Growth Plan G12 / S7).

Wires zero-token hooks and progressive rules into:
1. Claude Code (.claude/settings.json: PostToolUse on Edit|Write|MultiEdit and on Bash, and Stop)
2. Cursor (.cursor/hooks.json: postToolUse on Write and on Shell, and stop; plus the rule .cursor/rules/brevio-core.mdc)
3. Hermes Agent & OpenClaw (.hermes/skills/brevio-core/SKILL.md, skills/brevio-core/SKILL.md)

Each hook runs ``brevio-core hook run`` (``core_hook``), which reads the client's event JSON on stdin and answers in
the client's own format (ROADMAP E34, 2026-11-05). The shell-tool entry only notices a commit with a fix-like
message and suggests ``learn``: it never scans, never runs ``learn`` and never blocks (ROADMAP E40, 2026-11-05). Entries
of other tools in those files are kept; an older
brevio-core entry is replaced, so a second install adds no copy.

Every skill file is the text of ``agent_skill`` (Claude Code gets it in ``.claude/skills/brevio-core``); a
run replaces older skill text (ROADMAP E16, 2026-11-05). ``version`` pins the package in the hook command;
``brevio-core init`` passes it so the hook never runs a later release than the one set up (ROADMAP E22, 2026-11-05).
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence

from brevio.pipeline import core_cli
from brevio.pipeline.agent_host import client_detect, config, jsonstore
from brevio.pipeline.code_rules import PR_RULES_122
from brevio.pipeline.gates import agent_skill

CLAUDE_SETTINGS_DIR = ".claude"
CLAUDE_SETTINGS_FILE = "settings.json"
CURSOR_RULES_DIR = os.path.join(".cursor", "rules")
CURSOR_HOOKS_FILE = os.path.join(".cursor", "hooks.json")
HERMES_SKILLS_DIR = os.path.join(".hermes", "skills", "brevio-core")
OPENCLAW_SKILLS_DIR = os.path.join("skills", "brevio-core")
CLAUDE_SKILLS_DIR = os.path.join(CLAUDE_SETTINGS_DIR, "skills", "brevio-core")

# The hook reads the client's event JSON on stdin (the edited file is ``tool_input.file_path``); no client sets a
# ``$FILE`` variable, so the 0.2 and 0.3 hook (``scan --file $FILE``) never scanned anything (ROADMAP E34, 2026-11-05).
# ``hook run`` never honours an ignore comment: an agent cannot silence a rule (plan review, 2026-11-05).
POST_EDIT, POST_COMMAND, STOP = "post-edit", "post-command", "stop"
CLAUDE_CODE, CURSOR = "claude-code", "cursor"
CLAUDE_EDIT_TOOLS = "Edit|Write|MultiEdit"   # Claude Code matches tool names with this regular expression
CURSOR_EDIT_TOOL = "Write"                   # Cursor's file-writing tool (it maps Claude Code's Edit and Write to it)
# The shell tools, matched only so a fix commit can suggest ``learn`` (ROADMAP E40); the edit entries stay as they are.
CLAUDE_SHELL_TOOL, CURSOR_SHELL_TOOL = "Bash", "Shell"
HOOK_TIMEOUT = 30       # seconds the client waits; the first ``uvx`` run downloads the package, the scan has its own budget
_OUR_HOOK = re.compile(r"brevio-core(?:==[\w.]+)? (?:scan|hook run)\b")   # pinned or not, old or new: one copy only


def hook_command(event: str, client: str = CLAUDE_CODE, version: str | None = None) -> str:
    """The command a client runs for ``event``, pinned to ``brevio-core==version`` when a version is given."""
    package = f"brevio-core=={version}" if version else "brevio-core"
    return f"uvx {package} hook run --event {event}" + (f" --client {client}" if client != CLAUDE_CODE else "")


def ours(entry: object) -> bool:
    """``entry`` (a handler or a matcher group of a hook config) runs brevio-core and nothing else."""
    if not isinstance(entry, dict):
        return False
    if "hooks" in entry:
        inner = entry["hooks"]
        return isinstance(inner, list) and bool(inner) and all(ours(h) for h in inner)
    return bool(_OUR_HOOK.search(str(entry.get("command", ""))))


def _replace_ours(entries: object, new: list[dict]) -> list:
    """``entries`` without brevio-core's handlers (old or current form), then ``new`` once at the end."""
    kept: list = []
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict) and isinstance(e.get("hooks"), list) and not ours(e):
            e = {**e, "hooks": [h for h in e["hooks"] if not ours(h)]}
        if not ours(e):
            kept.append(e)
    return [*kept, *new]


def claude_hooks(version: str | None = None) -> dict[str, list[dict]]:
    """Claude Code's matcher groups: PostToolUse on the edit tools and on Bash, and Stop (which takes no matcher)."""
    def handlers(event: str) -> list[dict]:
        return [{"type": "command", "command": hook_command(event, CLAUDE_CODE, version), "timeout": HOOK_TIMEOUT}]
    return {"PostToolUse": [{"matcher": CLAUDE_EDIT_TOOLS, "hooks": handlers(POST_EDIT)},
                            {"matcher": CLAUDE_SHELL_TOOL, "hooks": handlers(POST_COMMAND)}],
            "Stop": [{"hooks": handlers(STOP)}]}


def cursor_hooks(version: str | None = None) -> dict[str, list[dict]]:
    """Cursor's hooks: postToolUse on its write tool and on its shell tool, and stop with one follow-up at most."""
    return {"postToolUse": [{"command": hook_command(POST_EDIT, CURSOR, version), "matcher": CURSOR_EDIT_TOOL,
                             "timeout": HOOK_TIMEOUT},
                            {"command": hook_command(POST_COMMAND, CURSOR, version), "matcher": CURSOR_SHELL_TOOL,
                             "timeout": HOOK_TIMEOUT}],
            "stop": [{"command": hook_command(STOP, CURSOR, version), "loop_limit": 1, "timeout": HOOK_TIMEOUT}]}


def _merge_hooks(path: str, wanted: dict[str, list[dict]], top: dict | None = None) -> None:
    """Put ``wanted`` (event -> our entries) into the ``hooks`` object of the JSON file at ``path``; write only on a change."""
    data = jsonstore.read_or(path, {})
    data = data if isinstance(data, dict) else {}
    before = json.dumps(data, sort_keys=True)
    for key, value in (top or {}).items():
        data.setdefault(key, value)
    found = data.get("hooks")
    hooks: dict = found if isinstance(found, dict) else {}
    for event, entry in wanted.items():
        hooks[event] = _replace_ours(hooks.get(event), entry)
    data["hooks"] = hooks
    if json.dumps(data, sort_keys=True) != before:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        jsonstore.write(path, data, indent=2, lock=False)


def _install_claude_hook(workspace: str, version: str | None = None) -> bool:
    _merge_hooks(os.path.join(workspace, CLAUDE_SETTINGS_DIR, CLAUDE_SETTINGS_FILE), claude_hooks(version))
    return _write_skill(os.path.join(workspace, CLAUDE_SKILLS_DIR))


def _install_cursor_hook(workspace: str, version: str | None = None) -> bool:
    _merge_hooks(os.path.join(workspace, CURSOR_HOOKS_FILE), cursor_hooks(version), top={"version": 1})
    return _install_cursor_rule(workspace)


def installed(path: str) -> bool:
    """The hook config at ``path`` runs ``brevio-core hook run``."""
    data = jsonstore.read_or(path, {})
    hooks = data.get("hooks") if isinstance(data, dict) else None
    entries = [e for v in hooks.values() if isinstance(v, list) for e in v] if isinstance(hooks, dict) else []
    return any(ours(e) and "hook run" in json.dumps(e) for e in entries)


def _install_cursor_rule(workspace: str) -> bool:
    cursor_dir = os.path.join(workspace, CURSOR_RULES_DIR)
    os.makedirs(cursor_dir, exist_ok=True)
    mdc_path = os.path.join(cursor_dir, "brevio-core.mdc")

    scanned = len(core_cli.scanned_rules())     # counted, never typed: the scan checks fewer rules than it documents
    content = f"""---
description: "Zero-Token PR Invariants & Auto-Healers"
globs: ["**/*"]
alwaysApply: true
---
# Brevio Core Invariants

Code edits in this workspace are checked by `brevio-core scan`: {scanned} scanned rules, line patterns on the
lines you add. The other {len(PR_RULES_122) - scanned} documented rules on its rule pages are for review.
Refer to `.agents/rules/*.md` for progressive-disclosure file-specific invariants.
"""
    with open(mdc_path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def _write_skill(directory: str) -> bool:
    """Write the one skill text into ``directory``, replacing whatever skill text was there before."""
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(agent_skill.SKILL_MD)
    return True


def _install_hermes_skill(workspace: str) -> bool:
    return _write_skill(os.path.join(workspace, HERMES_SKILLS_DIR))


def _install_openclaw_skill(workspace: str) -> bool:
    return _write_skill(os.path.join(workspace, OPENCLAW_SKILLS_DIR))


def _workspace_dir(cwd: str | None = None) -> str:
    if cwd:
        return cwd
    if hasattr(config, "get_workspace_dir"):
        return str(config.get_workspace_dir())
    return str(getattr(config, "WORKSPACE_DIR", "."))


def detect_target_agents(workspace: str | None = None) -> list[str]:
    """Detect which coding agents are present in the workspace or system."""
    ws = _workspace_dir(workspace)
    detected = client_detect.detect_llm_clients(ws, probe_daemons=False)
    return detected.active_agents


def install_targeted_hooks(
    cwd: str | None = None,
    targets: Sequence[str] | None = None,
    version: str | None = None,
) -> dict[str, bool]:
    """Install hooks targeting only detected or explicitly specified agents (``version`` pins the hook command)."""
    workspace = _workspace_dir(cwd)
    selected_targets = list(targets) if targets is not None else detect_target_agents(workspace)
    results: dict[str, bool] = {}

    if "claude_code" in selected_targets:
        results["claude_code"] = _install_claude_hook(workspace, version)
    if "cursor" in selected_targets:
        results["cursor"] = _install_cursor_hook(workspace, version)
    if "hermes" in selected_targets:
        results["hermes"] = _install_hermes_skill(workspace)
    if "openclaw" in selected_targets:
        results["openclaw"] = _install_openclaw_skill(workspace)

    return results


def install_all_hooks(
    cwd: str | None = None,
    target_only_detected: bool = False,
    targets: Sequence[str] | None = None,
    force_all: bool = False,
    version: str | None = None,
) -> dict[str, bool]:
    """Install hooks across agents.

    If target_only_detected is True, only installs for detected agents.
    If force_all is True or defaults, installs for all 4 supported agents (Claude, Cursor, Hermes, OpenClaw).
    """
    workspace = _workspace_dir(cwd)
    if (target_only_detected or targets is not None) and not force_all:
        return install_targeted_hooks(workspace, targets=targets, version=version)

    hermes_ok = _install_hermes_skill(workspace)
    openclaw_ok = _install_openclaw_skill(workspace)
    return {
        "claude_code": _install_claude_hook(workspace, version),
        "cursor": _install_cursor_hook(workspace, version),
        "hermes": hermes_ok,
        "openclaw": openclaw_ok,
    }


def hook_files(results: dict[str, bool]) -> list[str]:
    """What an install wrote, one line per hook file: ``.claude/settings.json: 3 hooks`` (the other agents get a skill)."""
    files = {"claude_code": (os.path.join(CLAUDE_SETTINGS_DIR, CLAUDE_SETTINGS_FILE), claude_hooks()),
             "cursor": (CURSOR_HOOKS_FILE, cursor_hooks())}
    return [f"{path.replace(os.sep, '/')}: {sum(len(v) for v in hooks.values())} hooks" for name, (path, hooks) in files.items()
            if results.get(name)]


def get_hooks_status(cwd: str | None = None) -> dict[str, bool]:
    workspace = _workspace_dir(cwd)
    claude_ok = installed(os.path.join(workspace, CLAUDE_SETTINGS_DIR, CLAUDE_SETTINGS_FILE))
    cursor_ok = installed(os.path.join(workspace, CURSOR_HOOKS_FILE))
    hermes_ok = os.path.isfile(os.path.join(workspace, HERMES_SKILLS_DIR, "SKILL.md"))
    openclaw_ok = os.path.isfile(os.path.join(workspace, OPENCLAW_SKILLS_DIR, "SKILL.md"))
    return {
        "claude_code": claude_ok,
        "cursor": cursor_ok,
        "hermes": hermes_ok,
        "openclaw": openclaw_ok,
    }
