"""``brevio-core.toml``: which rules run where, at which level, and the policy a project extends (ROADMAP E32, the 0.6
set, 2026-11-05).

A ``brevio-core.toml`` may sit at the root of the scan (the current folder) and in any folder under it. For a file, the
files from the root down to its folder apply in that order, so the nearest one wins:

- ``select`` and ``ignore``: rule patterns (``R9.1``, ``R9`` for the group, ``R1*``); a nearer file replaces them;
- ``exclude``: path globs, relative to the folder of the file that names them; they add up;
- ``level = "warn"|"error"`` for every rule, ``[rules."R9.1"] level = ...`` for one, and ``[[overrides]]`` with
  ``paths`` globs and ``ignore``, ``rules`` and ``level`` for those paths; the last setting that names a rule wins;
- ``extends``: a policy file read before this one, with its paths relative to this file's folder. It is a local path
  (a vendored file), or a path or git URL with ``@<commit SHA>``, which must name a checkout already present, at that
  commit: a submodule whose URL in ``.gitmodules`` matches. Nothing is ever fetched.

A level matters only to ``scan --fail-on error``, which fails on error-level findings alone. Python 3.11 and newer read
the file with ``tomllib``; Python 3.10 has a small reader of exactly the TOML these keys need. Standard library only.
"""
from __future__ import annotations

import configparser
import fnmatch
import os
import re
import subprocess
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple

from brevio.pipeline.rule_host import GIT

TOMLLIB: Callable[[str], dict[str, Any]] | None
try:
    import tomllib
    TOMLLIB = tomllib.loads
except ModuleNotFoundError:     # Python 3.10: the reader below
    TOMLLIB = None

CONFIG_NAME = "brevio-core.toml"
LEVELS = ("warn", "error")
DEFAULT_LEVEL = "error"
MAX_EXTENDS = 8
CHANGE_MESSAGE = "config change: it can turn rules off or down, so it needs a human review"
# ``ai`` is the narrowing table of ``brevio-core ai`` (2026-11-11): the gate ignores it, and only that command reads and
# checks it, so a file that holds one still loads for ``scan`` and the hook.
_TOP_KEYS = frozenset({"extends", "select", "ignore", "exclude", "level", "rules", "overrides", "ai"})
_OVERRIDE_KEYS = frozenset({"paths", "ignore", "rules", "level"})
_PIN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_URL = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://|[\w.-]+@[\w.-]+:)")      # scheme://... or user@host:path


class ConfigError(ValueError):
    """A config file that cannot be read or does not say what it means."""


# --- the TOML reader for Python 3.10 -------------------------------------------------------------------------------

_BARE = re.compile(r"[A-Za-z0-9_-]+")
_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}


class _Reader:
    """A cursor over the text, with the few TOML forms the keys need: comments, ``[table]``, ``[[array]]``, bare and
    quoted keys, dotted keys, strings, arrays of strings and ``true`` or ``false`` (``enabled`` of the ``ai`` table)."""

    def __init__(self, text: str) -> None:
        self.text, self.pos = text, 0

    def fail(self, what: str) -> ConfigError:
        line = self.text.count("\n", 0, self.pos) + 1
        return ConfigError(f"line {line}: {what} (Python 3.10 reads only the TOML these settings use; "
                           "Python 3.11 or newer reads all of it)")

    def at(self, s: str) -> bool:
        return self.text.startswith(s, self.pos)

    def skip(self, newlines: bool) -> None:
        while self.pos < len(self.text):
            c = self.text[self.pos]
            if c in " \t\r" or (newlines and c == "\n"):
                self.pos += 1
            elif c == "#":
                end = self.text.find("\n", self.pos)
                self.pos = len(self.text) if end < 0 else end
            else:
                return

    def expect(self, s: str) -> None:
        self.skip(False)
        if not self.at(s):
            raise self.fail(f"expected {s!r}")
        self.pos += len(s)

    def string(self) -> str:
        if self.at('"""') or self.at("'''"):
            raise self.fail("a multi-line string is not supported")
        quote, start = self.text[self.pos], self.pos + 1
        if quote == "'":
            end = self.text.find("'", start)
            if end < 0 or "\n" in self.text[start:end]:
                raise self.fail("a string is not closed")
            self.pos = end + 1
            return self.text[start:end]
        out: list[str] = []
        self.pos = start
        while True:
            c = self.text[self.pos:self.pos + 1]
            self.pos += 1
            if c in ("", "\n"):
                raise self.fail("a string is not closed")
            if c == '"':
                return "".join(out)
            if c == "\\":
                esc = self.text[self.pos:self.pos + 1]
                if esc not in _ESCAPES:
                    raise self.fail(f"the escape \\{esc} is not supported")
                out.append(_ESCAPES[esc])
                self.pos += 1
            else:
                out.append(c)

    def keys(self) -> list[str]:
        """A dotted key: ``a``, ``a.b`` or ``rules."R9.1"``."""
        out = []
        while True:
            self.skip(False)
            if self.at('"') or self.at("'"):
                out.append(self.string())
            else:
                m = _BARE.match(self.text, self.pos)
                if not m:
                    raise self.fail("expected a key")
                out.append(m.group(0))
                self.pos = m.end()
            self.skip(False)
            if not self.at("."):
                return out
            self.pos += 1

    def value(self) -> str | bool | list[str]:
        self.skip(False)
        if self.at('"') or self.at("'"):
            return self.string()
        for word in ("true", "false"):
            if self.at(word):
                self.pos += len(word)
                return word == "true"
        if not self.at("["):
            raise self.fail("a value here is a string or a list of strings (or true or false)")
        self.pos += 1
        items: list[str] = []
        while True:
            self.skip(True)
            if self.at("]"):
                self.pos += 1
                return items
            if not (self.at('"') or self.at("'")):
                raise self.fail("a list here holds strings only")
            items.append(self.string())
            self.skip(True)
            if self.at(","):
                self.pos += 1
            elif not self.at("]"):
                raise self.fail("expected ',' or ']'")

    def end_of_line(self) -> None:
        self.skip(False)
        if self.pos < len(self.text) and not self.at("\n"):
            raise self.fail("expected the end of the line")


def _table(root: dict[str, Any], keys: list[str], reader: _Reader) -> dict[str, Any]:
    table = root
    for key in keys:
        nxt = table.setdefault(key, {})
        if isinstance(nxt, list) and nxt and isinstance(nxt[-1], dict):
            nxt = nxt[-1]           # a key under the last [[array]] entry
        if not isinstance(nxt, dict):
            raise reader.fail(f"{key} is a value, not a table")
        table = nxt
    return table


def parse_subset(text: str) -> dict[str, Any]:
    """The TOML of a config file, read without ``tomllib``: what ``tomllib.loads`` gives for the forms it supports."""
    reader, doc = _Reader(text), dict[str, Any]()
    table: dict[str, Any] = doc
    defined: set[tuple[str, ...]] = set()
    while True:
        reader.skip(True)
        if reader.pos >= len(reader.text):
            return doc
        if reader.at("[["):
            reader.pos += 2
            keys = reader.keys()
            reader.expect("]]")
            parent = _table(doc, keys[:-1], reader)
            entries = parent.setdefault(keys[-1], [])
            if not isinstance(entries, list):
                raise reader.fail(f"{keys[-1]} is not an array of tables")
            table = {}
            entries.append(table)
        elif reader.at("["):
            reader.pos += 1
            keys = reader.keys()
            reader.expect("]")
            if tuple(keys) in defined:
                raise reader.fail(f"the table {'.'.join(keys)} is defined twice")
            defined.add(tuple(keys))
            table = _table(doc, keys, reader)
        else:
            keys = reader.keys()
            reader.expect("=")
            parent = _table(table, keys[:-1], reader)
            if keys[-1] in parent:
                raise reader.fail(f"the key {keys[-1]} is defined twice")
            parent[keys[-1]] = reader.value()
        reader.end_of_line()


def parse(text: str) -> dict[str, Any]:
    """The TOML document ``text``: ``tomllib`` when there is one (``TOMLLIB``, read at each call), else ``parse_subset``."""
    if TOMLLIB is None:
        return parse_subset(text)
    try:
        return TOMLLIB(text)
    except ValueError as exc:       # tomllib.TOMLDecodeError is a ValueError
        raise ConfigError(str(exc)) from exc


# --- what one file says ---------------------------------------------------------------------------------------------

class Override(NamedTuple):
    paths: tuple[str, ...]
    ignore: tuple[str, ...]
    rules: tuple[str, ...]      # the rules ``level`` applies to; empty: every rule
    level: str                  # "" leaves the level as it is


class Layer(NamedTuple):
    source: str                 # the file, for ``brevio-core config``
    base: str                   # the folder its paths are relative to ("" is the root)
    select: tuple[str, ...] | None
    ignore: tuple[str, ...] | None
    exclude: tuple[str, ...]
    levels: tuple[tuple[tuple[str, ...], str], ...]     # (rule patterns, level), in the order they apply
    overrides: tuple[Override, ...]


def _strings(value: Any, where: str) -> tuple[str, ...]:
    if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        raise ConfigError(f"{where} must be a list of strings")
    return tuple(value)


def _level(value: Any, where: str) -> str:
    if value not in LEVELS:
        raise ConfigError(f"{where} must be \"warn\" or \"error\", not {value!r}")
    return str(value)


def _unknown(keys: Iterable[str], allowed: frozenset[str], where: str) -> None:
    extra = sorted(set(keys) - allowed)
    if extra:
        raise ConfigError(f"{where}: unknown key {', '.join(extra)} (known: {', '.join(sorted(allowed))})")


def _rule_levels(rules: Any) -> list[tuple[tuple[str, ...], str]]:
    if not isinstance(rules, dict):
        raise ConfigError("rules must be tables such as [rules.\"R9.1\"]")
    out: list[tuple[tuple[str, ...], str]] = []
    for rid, table in rules.items():
        if not isinstance(table, dict) or (table and "level" not in table and all(isinstance(v, dict) for v in table.values())):
            raise ConfigError(f"rules.{rid}: write the rule ID in quotes, as in [rules.\"R9.1\"], with level = ...")
        _unknown(table, frozenset({"level"}), f"rules.\"{rid}\"")
        if "level" in table:
            out.append(((rid,), _level(table["level"], f"rules.\"{rid}\".level")))
    return out


def _override(entry: Any, n: int) -> Override:
    where = f"overrides[{n}]"
    if not isinstance(entry, dict) or "paths" not in entry:
        raise ConfigError(f"{where} needs paths = [...]")
    _unknown(entry, _OVERRIDE_KEYS, where)
    return Override(_strings(entry["paths"], f"{where}.paths"), _strings(entry.get("ignore", []), f"{where}.ignore"),
                    _strings(entry.get("rules", []), f"{where}.rules"),
                    _level(entry["level"], f"{where}.level") if "level" in entry else "")


def layer(doc: dict[str, Any], source: str, base: str) -> Layer:
    """One file's settings, checked: an unknown key or a wrong type is an error that names it."""
    _unknown(doc, _TOP_KEYS, source)
    every: tuple[str, ...] = ("*",)
    levels = [(every, _level(doc["level"], "level"))] if "level" in doc else []
    levels += _rule_levels(doc.get("rules", {}))
    overrides = doc.get("overrides", [])
    if not isinstance(overrides, list):
        raise ConfigError("overrides must be written as [[overrides]] tables")
    if "extends" in doc and not isinstance(doc["extends"], str):
        raise ConfigError("extends must be one string")
    return Layer(source, base,
                 _strings(doc["select"], "select") if "select" in doc else None,
                 _strings(doc["ignore"], "ignore") if "ignore" in doc else None,
                 _strings(doc.get("exclude", []), "exclude"), tuple(levels),
                 tuple(_override(entry, n) for n, entry in enumerate(overrides)))


# --- matching -------------------------------------------------------------------------------------------------------

def rule_matches(rule: str, pattern: str) -> bool:
    """``R9.1`` names one rule, ``R9`` its group, and a glob (``R1*``, ``*``) what it matches."""
    pat = pattern.strip().upper()
    return fnmatch.fnmatchcase(rule.upper(), pat) or rule.upper().startswith(pat + ".")


def _glob(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        else:
            out.append({"*": "[^/]*", "?": "[^/]"}.get(pattern[i], re.escape(pattern[i])))
            i += 1
    return re.compile("".join(out))


def path_matches(pattern: str, rel: str) -> bool:
    """A path glob against a path relative to its config's folder. ``vendor/**`` and ``vendor`` cover the folder; a
    pattern with no ``/`` (``*.min.js``, ``generated``) matches a name at any depth; a leading ``/`` anchors it."""
    anchored = pattern.startswith("/")
    pat = pattern.strip().strip("/")
    if not pat:
        return False
    rx, parts = _glob(pat), rel.split("/")
    if "/" not in pat and not anchored:
        return any(rx.fullmatch(p) for p in parts)
    return any(rx.fullmatch("/".join(parts[:n])) for n in range(1, len(parts) + 1))


def _under(base: str, path: str) -> str | None:
    """``path`` relative to the folder ``base`` (both relative to the root), or ``None`` when it is not under it."""
    if not base:
        return path
    return path[len(base) + 1:] if path.startswith(base + "/") else None


class PathConfig(NamedTuple):
    """What applies to one file."""
    files: tuple[str, ...]
    excluded: bool
    select: tuple[str, ...]
    ignore: tuple[str, ...]
    levels: tuple[tuple[tuple[str, ...], str], ...]

    def runs(self, rule: str) -> bool:
        return any(rule_matches(rule, p) for p in self.select) and not any(rule_matches(rule, p) for p in self.ignore)

    def level(self, rule: str, default: str = DEFAULT_LEVEL) -> str:
        """The level the nearest setting gives ``rule``; ``default`` when none names it (a learned guard's is ``warn``)."""
        for patterns, level in reversed(self.levels):
            if any(rule_matches(rule, p) for p in patterns):
                return level
        return default


def resolve(layers: list[Layer], path: str) -> PathConfig:
    """Fold the layers (root first, each one's ``extends`` before it) into what applies to ``path``."""
    select: tuple[str, ...] = ("*",)
    ignore: tuple[str, ...] = ()
    added: list[str] = []
    levels: list[tuple[tuple[str, ...], str]] = []
    excluded = False
    for lay in layers:
        rel = _under(lay.base, path)
        if rel is None:
            continue
        select = lay.select if lay.select is not None else select
        ignore = lay.ignore if lay.ignore is not None else ignore
        excluded = excluded or any(path_matches(g, rel) for g in lay.exclude)
        levels += lay.levels
        for ov in lay.overrides:
            if any(path_matches(g, rel) for g in ov.paths):
                added += ov.ignore
                levels += [(ov.rules or ("*",), ov.level)] if ov.level else []
    files = tuple(dict.fromkeys(lay.source for lay in layers))
    return PathConfig(files, excluded, select, (*ignore, *added), tuple(levels))


# --- extends --------------------------------------------------------------------------------------------------------

def _norm_url(url: str) -> str:
    url = url.strip().rstrip("/")
    return (url[:-4] if url.endswith(".git") else url).lower()


def submodule_path(root: str, url: str) -> str | None:
    """The path of the submodule of ``root`` whose ``.gitmodules`` URL is ``url``, or ``None``."""
    gm = os.path.join(root, ".gitmodules")
    if not os.path.isfile(gm):
        return None
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(gm, encoding="utf-8")
    except configparser.Error as exc:
        raise ConfigError(f".gitmodules cannot be read: {exc}") from exc
    for section in cp.sections():
        if cp.get(section, "url", fallback="") and _norm_url(cp.get(section, "url")) == _norm_url(url):
            return cp.get(section, "path", fallback="") or None
    return None


def checkout_head(folder: str) -> str:
    """The commit the git checkout holding ``folder`` is at, or "" when there is none."""
    try:
        r = subprocess.run([*GIT, "-C", folder, "rev-parse", "HEAD"],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def resolve_extends(spec: str, here: str, root: str) -> str:
    """The policy file ``extends = spec`` names, from a config in the folder ``here``. Never fetches anything."""
    target, sep, pin = spec.rpartition("@")
    if not (sep and _PIN.fullmatch(pin)):
        target, pin = spec, ""
    if _URL.match(target):
        if not pin:
            raise ConfigError(f"extends {spec!r}: a git URL needs @<commit SHA>")
        found = submodule_path(root, target)
        if found is None:
            raise ConfigError(f"extends {target}: no checkout of it here, and brevio-core never fetches one; add it as "
                              "a git submodule at that commit, or vendor the policy file and name its path")
        path = os.path.join(root, found)
    else:
        path = os.path.normpath(os.path.join(here, target))
    if pin:
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        head = checkout_head(folder) if os.path.isdir(folder) else ""
        if head != pin:
            raise ConfigError(f"extends {spec!r}: the checkout at {os.path.relpath(folder, root)} is at "
                              f"{head[:12] or 'no commit'}, not {pin[:12]}; check out that commit "
                              "(git submodule update) and scan again")
    if os.path.isdir(path):
        path = os.path.join(path, CONFIG_NAME)
    if not os.path.isfile(path):
        raise ConfigError(f"extends {spec!r}: there is no file {os.path.relpath(path, root)}")
    return path


# --- a project ------------------------------------------------------------------------------------------------------

def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path} cannot be read: {exc}") from exc


class Project:
    """The config files of a scan root, read once each, on demand."""

    def __init__(self, root: str = ".") -> None:
        self.root = os.path.abspath(root)
        self._folders: dict[str, list[Layer]] = {}

    def _rel(self, path: str) -> str:
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    def _file_layers(self, path: str, base: str, chain: tuple[str, ...]) -> list[Layer]:
        """The layers one file adds: what it extends (recursively), then itself."""
        real = os.path.realpath(path)
        if real in chain:
            raise ConfigError(f"{self._rel(path)}: extends goes round in a loop")
        if len(chain) >= MAX_EXTENDS:
            raise ConfigError(f"{self._rel(path)}: more than {MAX_EXTENDS} files deep in extends")
        try:
            doc = parse(_read(path))
            own = layer(doc, self._rel(path), base)
        except ConfigError as exc:
            raise ConfigError(f"{self._rel(path)}: {exc}") from exc
        spec = doc.get("extends")
        parent = (self._file_layers(resolve_extends(spec, os.path.dirname(path), self.root), base, (*chain, real))
                  if spec else [])
        return [*parent, own]

    def folder_layers(self, folder: str) -> list[Layer]:
        """Every layer that applies in ``folder`` (relative to the root, "" for the root), the root's first."""
        if folder not in self._folders:
            above = self.folder_layers(folder.rpartition("/")[0]) if folder else []
            path = os.path.join(self.root, folder, CONFIG_NAME)
            self._folders[folder] = above + (self._file_layers(path, folder, ()) if os.path.isfile(path) else [])
        return self._folders[folder]

    def for_path(self, path: str, folder: bool = False) -> PathConfig:
        """What applies to the file ``path`` (relative to the root, ``/``-separated), or with ``folder`` to the folder
        itself; a path outside the root gets the root's settings."""
        rel = path.replace(os.sep, "/")
        inside = not (rel.startswith("../") or rel == ".." or os.path.isabs(rel))
        where = ("" if rel == "." else rel) if folder else os.path.dirname(rel)
        return resolve(self.folder_layers(where if inside else ""), rel)

    def preload(self, paths: Iterable[str]) -> None:
        """Read the config of each path now, so a broken file fails the command before anything is scanned."""
        for p in [".", *paths]:
            self.for_path(p)

    def warnings(self, known: Iterable[str]) -> list[str]:
        """A rule pattern in a loaded file that matches no rule of the catalogue."""
        ids = list(known)
        out = []
        for lay in dict.fromkeys(lay for layers in self._folders.values() for lay in layers):
            patterns = [*(lay.select or ()), *(lay.ignore or ()), *(p for ps, _l in lay.levels for p in ps),
                        *(p for ov in lay.overrides for p in (*ov.ignore, *ov.rules))]
            out += [f"{lay.source}: {p} matches no rule" for p in dict.fromkeys(patterns)
                    if not any(rule_matches(r, p) for r in ids)]
        return out


def is_config(path: str) -> bool:
    return os.path.basename(path) == CONFIG_NAME


def describe(project: Project, path: str, rules: Iterable[str], status_of: Callable[[str], str],
             folder: bool = False) -> dict[str, Any]:
    """The effective config for ``path``: its files, whether it is excluded, and each scanned rule that runs there."""
    cfg = project.for_path(path, folder)
    runs = {r: {"level": cfg.level(r), "status": status_of(r)} for r in rules if cfg.runs(r) and status_of(r) != "deprecated"}
    return {"path": path, "files": list(cfg.files), "excluded": cfg.excluded, "select": list(cfg.select),
            "ignore": list(cfg.ignore), "rules": {} if cfg.excluded else runs}


def render(desc: dict[str, Any]) -> str:
    """``describe`` as plain lines."""
    lines = [f"brevio-core config for {desc['path']}",
             f"files: {', '.join(desc['files']) or 'none (every scanned rule at level error)'}",
             f"excluded: {'yes' if desc['excluded'] else 'no'}",
             f"select: {', '.join(desc['select'])}", f"ignore: {', '.join(desc['ignore']) or '-'}",
             "rules that run here (level, status):"]
    lines += [f"  {rid:<7} {v['level']:<5} {v['status']}" for rid, v in desc["rules"].items()] or ["  none"]
    return "\n".join(lines) + "\n"
