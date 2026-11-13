#!/usr/bin/env python3
"""Make the files a release publishes besides the PyPI package, so the publish jobs run no code from this tree.

Run from the repository root after the wheel is installed into a folder (``pip install --target``)::

    python scripts/release_assets.py --from pyz-root --out assets --commit <release commit> --repo <owner/name>

It writes into ``--out``:

- ``brevio-core.pyz``: the installed package as a zip application, run with any ``python3`` 3.10 or newer. It holds the
  ``VERSION`` file, so ``--version`` and the MCP server can say the version. The entries are sorted, stored uncompressed
  and carry a fixed timestamp and mode, so the same package gives the same bytes.
- ``brevio-core.cdx.json``: a CycloneDX SBOM. The package has no runtime dependency, so it lists no component.
- ``notes.md``: ``RELEASE.md`` plus the release commit, the SHA-256 of the ``.pyz`` (and of the PyPI files, with
  ``--dist``) and how to verify it.
- ``server.json``: the MCP registry entry with its versions set from ``VERSION`` (only when the tree ships the MCP server).
- ``skill/brevio-core/SKILL.md``: the agent skill without its ``license:`` line, for the skill registry, which publishes
  every skill under its own licence (only when the tree ships the skill).

Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import uuid
import zipfile

PYZ = "brevio-core.pyz"
SBOM = "brevio-core.cdx.json"
SHEBANG = b"#!/usr/bin/env python3\n"
MAIN = "from brevio.pipeline.core_cli import main\n\nraise SystemExit(main())\n"
EPOCH = (1980, 1, 1, 0, 0, 0)        # the earliest time a zip entry can carry
SKILL = os.path.join("skills", "brevio-core", "SKILL.md")
MCP_MODULE = os.path.join("brevio", "operator", "mcp_cmd.py")


def package_files(source: str) -> list[str]:
    """The ``.py`` files of the ``brevio`` package under ``source``, as sorted archive names."""
    names = []
    for dirpath, dirs, files in os.walk(os.path.join(source, "brevio")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        names += [os.path.relpath(os.path.join(dirpath, n), source).replace(os.sep, "/") for n in files if n.endswith(".py")]
    if not names:
        raise SystemExit(f"no brevio package under {source}")
    return sorted(names)


def _entry(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=EPOCH)
    info.create_system = 3                          # the same header on every platform
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_STORED
    return info


def build_pyz(source: str, target: str, version: str) -> str:
    """Write the zip application of the package under ``source`` and its ``VERSION`` to ``target``; return its SHA-256."""
    with open(target, "wb") as fh:
        fh.write(SHEBANG)
        with zipfile.ZipFile(fh, "w") as z:
            z.writestr(_entry("__main__.py"), MAIN)
            z.writestr(_entry("VERSION"), version.strip() + "\n")
            for name in package_files(source):
                with open(os.path.join(source, *name.split("/")), "rb") as src:
                    z.writestr(_entry(name), src.read())
    if os.name != "nt":
        os.chmod(target, 0o755)
    return sha256(target)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def sbom(version: str, commit: str, pyz_sha: str) -> dict:
    """A CycloneDX 1.6 SBOM of the ``.pyz``: the package itself and no runtime dependency."""
    ref = f"pkg:pypi/brevio-core@{version}"
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        # Derived from the version and commit rather than random, so the same release gives the same file.
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, ref + '#' + commit)}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": ref,
                "name": "brevio-core",
                "version": version,
                "purl": ref,
                "licenses": [{"license": {"id": "Apache-2.0"}}],
                "hashes": [{"alg": "SHA-256", "content": pyz_sha}],
                "properties": [{"name": "release:commit", "value": commit}],
            },
        },
        "components": [],
        "dependencies": [{"ref": ref, "dependsOn": []}],
    }


def notes(release_md: str, version: str, commit: str, pyz_sha: str, repo: str, dist: dict[str, str] | None = None) -> str:
    """The release notes: ``RELEASE.md``, the SHA-256 of each file (``dist``: the PyPI files) and how to check the ``.pyz``."""
    files = "".join(f"- `{name}` SHA-256: `{digest}`\n" for name, digest in sorted((dist or {}).items()))
    return (release_md.rstrip() + "\n\n"
            "## Verify\n\n"
            f"- Release commit: `{commit}`\n"
            f"- `{PYZ}` SHA-256: `{pyz_sha}`\n"
            f"{files}"
            f"- `{SBOM}`: the SBOM (no runtime dependency)\n\n"
            "```\n"
            f"gh attestation verify {PYZ} --repo {repo}\n"
            f"python3 {PYZ} scan\n"
            "```\n\n"
            f"The package is on PyPI as `brevio-core=={version}`, with the attestations of trusted publishing.\n")


def stamped_server(text: str, version: str) -> str:
    """``server.json`` with its own version and every package version set to ``version``."""
    doc = json.loads(text)
    doc["version"] = version
    for pkg in doc.get("packages", []):
        pkg["version"] = version
    return json.dumps(doc, indent=2) + "\n"


def skill_without_licence(text: str) -> str:
    """``SKILL.md`` with the ``license:`` line of its front matter removed; the rest is unchanged."""
    parts = text.split("---\n", 2)
    if len(parts) != 3 or parts[0]:
        raise SystemExit(f"{SKILL} has no front matter")
    front = "".join(ln for ln in parts[1].splitlines(keepends=True) if not re.match(r"license\s*:", ln))
    return "---\n" + front + "---\n" + parts[2]


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def make(root: str, source: str, out: str, commit: str, repo: str, dist: str | None = None) -> list[str]:
    """Write every asset into ``out`` and return their paths relative to it (``dist``: the folder of the PyPI files)."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SystemExit(f"--commit must be a full commit SHA, got {commit!r}")
    version = _read(os.path.join(root, "VERSION")).strip()
    os.makedirs(out, exist_ok=True)
    pyz_sha = build_pyz(source, os.path.join(out, PYZ), version)
    dist_shas = {n: sha256(os.path.join(dist, n)) for n in os.listdir(dist)} if dist else {}
    _write(os.path.join(out, SBOM), json.dumps(sbom(version, commit, pyz_sha), indent=2) + "\n")
    _write(os.path.join(out, "notes.md"),
           notes(_read(os.path.join(root, "RELEASE.md")), version, commit, pyz_sha, repo, dist_shas))
    written = [PYZ, SBOM, "notes.md"]
    server = os.path.join(root, "server.json")
    if os.path.isfile(server):
        _write(os.path.join(out, "server.json"), stamped_server(_read(server), version))
        written.append("server.json")
    elif os.path.isfile(os.path.join(root, MCP_MODULE)):
        raise SystemExit("this tree ships the MCP server but has no server.json; the release tool writes it")
    if os.path.isfile(os.path.join(root, SKILL)):
        _write(os.path.join(out, "skill", "brevio-core", "SKILL.md"), skill_without_licence(_read(os.path.join(root, SKILL))))
        written.append("skill/brevio-core/SKILL.md")
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="source", required=True, help="folder holding the installed brevio package")
    ap.add_argument("--out", required=True, help="folder to write the assets into")
    ap.add_argument("--commit", required=True, help="the full SHA of the release commit")
    ap.add_argument("--repo", required=True, help="owner/name of this repository, for the verify command")
    ap.add_argument("--root", default=".", help="the repository root (default: the current folder)")
    ap.add_argument("--dist", help="the folder of the built PyPI files, listed with their SHA-256 in the notes")
    args = ap.parse_args(argv)
    for rel in make(args.root, args.source, args.out, args.commit, args.repo, args.dist):
        print(os.path.join(args.out, rel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
