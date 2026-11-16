"""The thin host adapter of the agent tools: learn, hook install, gain and add (ROADMAP E16, the 0.2 set, 2026-11-04).

The guard compiler, the hook installer, the savings ledger and the guard packs need four things from their host: the
folder to work in, a JSON store, the hardened ``git`` command line and a coding-client detector. They live here on the
standard library alone, so those modules export with no private import (the core exporter, set 0.2), the same way
``rule_host`` serves the rule engines. In the exported package these are the only implementations. In the harness,
``brevio/pipeline/gates/__init__.py`` calls ``bind`` before any of those modules loads, so the harness keeps using its
own config, JSON store, git hardening and client detection and behaves as it did.
``tests/pipeline/test_agent_host.py`` pins each stand-in to the harness helper it mirrors.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any

from brevio.pipeline.rule_host import GIT

# The tools a coding client may run: a name on PATH, or a marker folder in the workspace.
_CLIENT_CLIS = ("claude", "cursor", "windsurf", "hermes")


class _Config:
    """The workspace the tools default to: the folder the command runs in (read when asked, never at import)."""

    def __getattr__(self, name: str) -> str:
        if name == "WORKSPACE_DIR":
            return os.getcwd()
        raise AttributeError(name)


class _JsonStore:
    """``read_or`` and ``write`` as the harness's ``jsonstore`` has them; the write is atomic and takes no lock.

    Only the exported package runs this; the harness binds its own store, whose lock and write hooks its files need."""

    @staticmethod
    def read_or(path: str, default: Any = None) -> Any:
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    @staticmethod
    def write(path: str, data: Any, indent: int | None = 2, lock: bool | None = None) -> str:
        return _write_text(path, json.dumps(data, indent=indent, default=str))


def _write_text(path: str, text: str) -> str:
    """Replace ``path`` with ``text`` through a temporary file, so a crash never leaves half a file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):    # gone already once the replace has happened
            os.unlink(tmp)
    return path


@dataclass(frozen=True)
class DetectedClients:
    """The coding clients found for a workspace; ``active_agents`` are the ones that should get hooks."""

    clis: dict[str, bool]
    workspace_markers: dict[str, bool]
    active_agents: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        found = [k for k, v in self.clis.items() if v]
        return "\n".join(["Detected coding clients:", f"  CLIs in PATH: {', '.join(found) or 'none'}",
                          f"  Active workspace agents: {', '.join(self.active_agents) or 'none'}"])


class _ClientDetect:
    """Finds coding clients from PATH and the workspace's marker folders (no process table, no daemon probe)."""

    @staticmethod
    def detect_llm_clients(workspace: str | None = None, probe_daemons: bool = False,
                           path_env: str | None = None) -> DetectedClients:
        ws = workspace or "."
        clis = {name: bool(shutil.which(name, path=path_env)) for name in _CLIENT_CLIS}
        markers = {
            "claude_code": os.path.isdir(os.path.join(ws, ".claude")),
            "cursor": os.path.isdir(os.path.join(ws, ".cursor")),
            "windsurf": os.path.isfile(os.path.join(ws, ".windsurfrules")) or os.path.isdir(os.path.join(ws, ".windsurf")),
            "hermes": os.path.isdir(os.path.join(ws, ".hermes")),
            "openclaw": os.path.isdir(os.path.join(ws, "skills", "brevio-core")),
        }
        active = {"claude_code": markers["claude_code"] or clis["claude"], "cursor": markers["cursor"] or clis["cursor"],
                  "windsurf": markers["windsurf"] or clis["windsurf"], "hermes": markers["hermes"] or clis["hermes"],
                  "openclaw": markers["openclaw"]}
        return DetectedClients(clis=clis, workspace_markers=markers, active_agents=sorted(k for k, v in active.items() if v))


config: Any = _Config()
jsonstore: Any = _JsonStore()
gitsafe: Any = SimpleNamespace(GIT=GIT)
client_detect: Any = _ClientDetect()


def bind(*, config: Any, jsonstore: Any, gitsafe: Any, client_detect: Any) -> None:
    """Replace the stand-ins with the host's own helpers; the harness does this once, before the tools load."""
    globals().update(config=config, jsonstore=jsonstore, gitsafe=gitsafe, client_detect=client_detect)
