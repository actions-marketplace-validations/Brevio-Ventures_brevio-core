"""The generic leak checks of the exported tree: URLs, emails and internal paths (ROADMAP E13, 2026-11-04).

Standard library only, so the harness's exporter and the public repo's own CI run the same code. It knows no product
or tenant name; those come from the harness at run time (the exporter). ``scan_tree`` returns one
``path: kind: text`` line per problem and an empty list for a clean tree.
"""
from __future__ import annotations

import json
import os
import re

# A URL is public only when it points at the project, its GitHub organisation or a documentation site. The
# organisation is not listed here: the release names it (``--org``) and ``allowed_urls`` adds it (ROADMAP E16, 2026-11-05).
ALLOWED_URLS = (
    "brevio.pro", "docs.github.com", "docs.python.org", "packaging.python.org",
    "www.apache.org/licenses", "developercertificate.org", "www.contributor-covenant.org", "spdx.org/licenses",
    "static.modelcontextprotocol.io/schemas", "glama.ai/mcp/schemas",    # the $schema of the two listing files (2026-11-05)
    "img.shields.io/badge",     # the static "checked by" badge ``init`` prints; a fixed image, no data (ROADMAP E22, 2026-11-05)
    "schema.org",               # the JSON-LD vocabulary of the rule pages, a name and not a link (ADOPTION.md E38, 2026-11-05)
)
PLACEHOLDER_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
ALLOWED_EMAILS = ("security@brevio.pro", "legal@brevio.pro")    # legal: trademark and privacy (ROADMAP E39, 2026-11-05)
# Folders of the harness that the public tree must not point into unless the file really is there.
PRIVATE_ROOTS = ("brevio", "tools", "scripts", "bin", "state", "projects")
# The release's map of each file to its harness source names harness paths on purpose, so it skips the path check
# only; its URLs and emails are still checked (ROADMAP E29a, 2026-11-05).
SOURCE_MAP = "MANIFEST.json"
# The precision run quotes third-party code (a sample of what the scanner flagged) and names the repositories of its
# corpus. That text is not ours: the quoted samples are skipped here, and the GitHub address of each corpus repository
# is allowed (improvement plan 2a, 2026-11-12). The exporter's tenant-name scan still reads every file.
QUOTED_DATA = ("docs/precision_sample.jsonl", "docs/precision_sample_before.jsonl")
CORPUS_FILE = os.path.join("tools", "precision_corpus.json")      # a join, so a bare export holds no text naming a harness path
_PACKAGE_STUB = "Part of the portable brevio rule engines."

_SOURCE = re.compile(r"^Source\s*=\s*\"https:(?://)github\.com/([^/\"]+)/", re.M)
_URL = re.compile(r"https?://[^\s)>\]\"'`<,;]+", re.I)
# The last label of a mail domain holds a letter, so a pinned version (``brevio-core@0.4.0``, ``x@v1.2.3``) is no email.
_EMAIL = re.compile(r"(?<![\w.%+-])[\w.%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]*)", re.I)
_DOTTED = re.compile(r"(?<![\w.])brevio((?:\.[A-Za-z_]\w*)+)")
_SLASHED = re.compile(r"(?<![\w/.-])((?:" + "|".join(PRIVATE_ROOTS) + r")/[\w./-]*\w)")


def allowed_urls(org: str | None = None, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The allow-list, plus ``github.com/<org>`` and the organisation's Pages site (lower case, as URLs are compared)
    when an organisation is named, plus the ``extra`` paths. The rule pages live on that site, and the Action links
    each finding to its page."""
    return ALLOWED_URLS + ((f"github.com/{org.lower()}", f"{org.lower()}.github.io") if org else ()) + extra


def corpus_urls(root: str) -> tuple[str, ...]:
    """``github.com/<owner>/<repo>`` (lower case) for each repository the tree's precision corpus names, or ``()``."""
    try:
        with open(os.path.join(root, CORPUS_FILE), encoding="utf-8") as fh:
            repos = json.load(fh)["repos"]
        names = [r.get("name") for r in repos if isinstance(r, dict)]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ()
    return tuple(f"github.com/{n.lower()}" for n in names if isinstance(n, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", n))


def tree_org(root: str) -> str | None:
    """The GitHub organisation a released tree names as its source in ``pyproject.toml``, or None for a bare export."""
    try:
        with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as fh:
            m = _SOURCE.search(fh.read())
    except OSError:
        return None
    return m.group(1) if m else None


def _url_allowed(target: str, org: str | None = None, extra: tuple[str, ...] = ()) -> bool:
    """``target`` (host and path, no scheme) is an allowed host (or one of its subdomains) or sits under an allowed path."""
    host = target.split("/")[0].split(":")[0]
    for allowed in allowed_urls(org, extra):
        if "/" in allowed:
            if target == allowed or target.startswith(allowed + "/"):
                return True
        elif host == allowed or host.endswith("." + allowed):
            return True
    return False


def bad_urls(text: str, org: str | None = None, extra: tuple[str, ...] = ()) -> list[str]:
    """URLs in ``text`` that are not on the allow-list (and not under ``github.com/<org>``, compared without case)."""
    return [m.group(0) for m in _URL.finditer(text)
            if not _url_allowed(m.group(0).split("://", 1)[1].lower().rstrip("."), org, extra)]


def bad_emails(text: str) -> list[str]:
    """Email addresses in ``text`` that are neither a placeholder nor a published address."""
    out = []
    for m in _EMAIL.finditer(text):
        if text[m.end():m.end() + 1] == ":":    # git SSH address (git@host:repo), not an email
            continue
        if m.group(0).lower() not in ALLOWED_EMAILS and m.group(1).lower() not in PLACEHOLDER_EMAIL_DOMAINS:
            out.append(m.group(0))
    return out


def _module_present(root: str, parts: list[str]) -> bool:
    """True when ``brevio.<parts>`` is a module (or an attribute of one) or a package present in ``root``."""
    for i in range(len(parts), 0, -1):
        base = os.path.join(root, "brevio", *parts[:i])
        if os.path.isfile(base + ".py"):
            return True
        init = os.path.join(base, "__init__.py")
        if os.path.isfile(init):
            if i == len(parts):
                return True
            with open(init, encoding="utf-8") as fh:
                return _PACKAGE_STUB not in fh.read()   # a stub package holds no attributes
    return not parts


def bad_internal_paths(text: str, root: str) -> list[str]:
    """Harness module or folder paths named in ``text`` that do not exist in ``root``."""
    out = []
    for m in _DOTTED.finditer(text):
        parts = m.group(1).lstrip(".").split(".")
        if parts[0] != "pro" and not _module_present(root, parts):   # ``brevio.pro`` is the domain
            out.append("brevio." + ".".join(parts))
    for m in _SLASHED.finditer(text):
        if not os.path.exists(os.path.join(root, m.group(1).rstrip("/"))):
            out.append(m.group(1))
    return out


def read_text(path: str) -> str | None:
    """The file's text, or None for a binary file."""
    with open(path, "rb") as fh:
        data = fh.read()
    return None if b"\0" in data else data.decode("utf-8", errors="replace")


def tree_files(root: str) -> list[str]:
    """Every file under ``root`` (relative, sorted), leaving out ``.git``."""
    out = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")]
        out += [os.path.relpath(os.path.join(dirpath, n), root).replace(os.sep, "/") for n in names]
    return sorted(out)


def scan_tree(root: str, org: str | None = None) -> list[str]:
    """``path: kind: text`` for every URL, email or internal path in ``root`` that should not be public.

    ``org`` defaults to the organisation the tree's own ``pyproject.toml`` names, so the public repository's CI needs no setting.
    """
    org = org or tree_org(root)
    extra = corpus_urls(root)
    problems: list[str] = []
    for rel in tree_files(root):
        text = None if rel in QUOTED_DATA else read_text(os.path.join(root, rel))
        if text is None:
            continue
        file_org = org or ("example-org" if rel.startswith("tests/") else None)
        problems += [f"{rel}: url: {u}" for u in bad_urls(text, file_org, extra)]
        problems += [f"{rel}: email: {e}" for e in bad_emails(text)]
        if rel != SOURCE_MAP:
            problems += [f"{rel}: internal path: {p}" for p in bad_internal_paths(text, root)]
    return sorted(set(problems))
