#!/usr/bin/env python3
"""Count how often each rule of ``brevio-core scan`` fires on pinned open-source repos, and how often it is right.

Reads ``tools/precision_corpus.json`` (popular TypeScript and React repositories, each pinned to a full commit SHA),
fetches each one shallowly at its SHA into a cache folder outside this repository, checks its licence file and runs the
scanner on it under ``python -I``, so the repository's own files cannot load code into it. It writes
``precision.json`` (hits per rule and per repository, files and lines scanned), a seeded sample of up to ``SAMPLE``
hits per rule (``precision_sample.jsonl``) and the page ``precision.md``, which is rendered from the JSON only, so it holds no
number typed by hand.

The sample quotes none of the third-party code it was drawn from (owner decision, 2026-11-13). An entry holds the repository,
its commit, the path, the line, the rule, the verdict and its reason, and ``line_sha256``: the hash of the line as the run read
it (``line_digest``). The lines around a hit, which a person needs to judge it, go to ``precision_to_judge.jsonl`` beside the
results; that file is a working copy, not part of a release, and a new run writes it for the hits still to judge.

A person judges each sampled hit: ``verdict`` is ``tp`` (the rule is right) or ``fp`` (it is not), with a one-line
``reason``. A re-run keeps each verdict whose hit is still sampled, matched by its ``id`` and by the hash of its line, read
again from the clone: a verdict given to other text is not kept. ``--rejudge`` only
recomputes the figures and the page from the judged sample. ``--record-before`` keeps the current figures and judged
sample (``precision_sample_before.jsonl``) as the "before" of a rule change, so the page shows both.

Anyone can run it again (improvement plan 2a, 2026-11-12). The file is standard library only and ships in the public
tree with the corpus and the judged results in ``docs/``. The scanner is the tree the file sits in, or the folder
``--scanner`` names. ``--seed DIR`` starts a run in a new ``--out`` folder from the judged results in ``DIR``, so the
committed ones stay as they are. The run needs the network to clone the corpus; the scan itself reads local files.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
from collections.abc import Callable

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:        # ``python -I`` leaves the script's folder off the path; the two files beside this one ship with it
    sys.path.insert(0, HERE)

import precision_stats as stats  # noqa: E402
from precision_corpora import (  # noqa: E402,F401  (the names this file has always offered, now defined beside it)
    _SHA,
    CORPUS,
    DEFAULT_LANGUAGE,
    GIT,
    GITHUB,
    LICENCES,
    RunError,
    _run,
    corpus_name,
    fetch,
    language_named,
    licence_of,
    load_corpus,
    pick,
    pin,
    results_folder,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "docs")
JSON_NAME, SAMPLE_NAME, PAGE_NAME = "precision.json", "precision_sample.jsonl", "precision.md"
BEFORE_SAMPLE_NAME = "precision_sample_before.jsonl"   # the judged sample of the run a rule change started from
TO_JUDGE_NAME = "precision_to_judge.jsonl"      # the lines around the hits still to judge; a working file, never released
DEFAULT_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "brevio-precision")
DETAIL_NAME = "precision_detail.json"    # the rows per repository, a working file beside the results, never released
SCHEMA = 2             # 2: a reviewer, an interval method, the corpus language and the level of detail; 1 is read as it was recorded
EXPORT_SET = "0.7"      # the set of files the scanner was measured with, kept in the results: the newest set, the one a release is built from
SAMPLE = 20             # hits judged per rule
SEED = "e35"            # the sample's seed: the same hits always give the same sample
CONTEXT = 3             # lines of context on each side of a sampled hit
POOR_BELOW = 0.7        # a rule whose sampled precision is below this is listed for a fix
MAX_TEXT = 240          # characters kept of each context line
_QUOTED = re.compile(r"""(['"`])[^'"`]{8,}?\1""")
_CREDENTIAL = re.compile(
    r"(?:AKIA|ASIA)[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|github_pat_\w{40,}|xox[abprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{20,}"
    r"|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?i:secret|token|passw(?:or)?d|api[_-]?key|credential|private[_-]?key)\w*['\"`]?\s*[:=]\s*['\"`][^'\"`\s]{16,}['\"`]")
_FINDING = re.compile(r"^(?P<path>.+?):(?P<line>\d+): \[(?P<rule>[^\]]+)\] (?P<message>.*)$")
# Both run the exported tree with the standard library only; the repository under scan is the working folder.
_SCAN = ("import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; "
         "raise SystemExit(core_cli.main(sys.argv[2:]))")
_COUNT = ("import json, sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; "
          "from brevio.pipeline.code_rules import PR_RULES_122 as T; f = core_cli.collect_files(['.']); "
          "print(json.dumps({'files': len(f), 'lines': sum(map(len, f.values())), "
          "'rules': {r: T[r] for r in core_cli.scanned_rules()}}))")
SCAN_ARGS = ("scan", "--fail-on", "all", ".")
VERDICTS = ("tp", "fp")
# Findings that moved to another rule ID: new ID -> the ID they had. A verdict survives the move (the hit is the same
# line and message), and the before-and-after table reads the old ID's before figures. The eval and
# dangerouslySetInnerHTML findings left R2.5, the SSRF guard, for R2.8 (2026-11-05).
MOVED_FROM = {"R2.8": "R2.5"}


def _inside(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def check_cache(cache: str) -> str:
    """The cache folder, refused when it lies inside this repository: the cloned repos must never be committed."""
    if _inside(cache, ROOT):
        raise RunError(f"the cache {cache} lies inside the repository; use a folder outside it")
    os.makedirs(cache, exist_ok=True)
    return cache


def tree_digest(tree: str, subdir: str = "") -> str:
    """SHA-256 over every file's path and bytes under ``tree/subdir``: names the scanner exactly, whatever commit it
    came from."""
    h = hashlib.sha256()
    top = os.path.join(tree, subdir)
    for dirpath, dirs, names in os.walk(top):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for n in sorted(names):
            path = os.path.join(dirpath, n)
            h.update(os.path.relpath(path, top).replace(os.sep, "/").encode() + b"\0")
            with open(path, "rb") as fh:
                h.update(fh.read() + b"\0")
    return h.hexdigest()


def scan_repo(tree: str, checkout: str) -> tuple[list[dict], dict]:
    """``(hits, counts)``: each finding of the exported scanner, and the files, lines and rules it scanned."""
    counted = _run([sys.executable, "-I", "-c", _COUNT, tree], cwd=checkout)
    if counted.returncode != 0:
        raise RunError(f"counting failed in {checkout}: {counted.stderr.strip()}")
    res = _run([sys.executable, "-I", "-c", _SCAN, tree, *SCAN_ARGS], cwd=checkout)
    if res.returncode not in (0, 1):
        raise RunError(f"the scan failed in {checkout}: {res.stderr.strip()}")
    hits = []
    for ln in res.stdout.splitlines():
        m = _FINDING.match(ln)
        if m:
            hits.append({"path": m["path"], "line": int(m["line"]), "rule": m["rule"], "message": m["message"]})
    return hits, json.loads(counted.stdout)


_LINES: dict[tuple[str, str], list[str]] = {}      # the lines of each file a hit was found in, read once however many hits it holds


def _lines(checkout: str, path: str) -> list[str]:
    """The lines of ``path`` in ``checkout``, or none when it is not a regular file inside the checkout. A path is the scanner's
    output for a file of a repository the run does not trust: a link or a forged line must not make it read another file."""
    key = (checkout, path)
    if key not in _LINES:
        full = os.path.join(checkout, path)
        lines: list[str] = []
        if _inside(full, checkout) and os.path.isfile(full) and not os.path.islink(full):
            try:
                with open(full, encoding="utf-8", errors="ignore") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                lines = []
        if len(_LINES) > 64:        # a corpus of many files keeps memory to the files of the last repositories
            _LINES.clear()
        _LINES[key] = lines
    return _LINES[key]


def _context(checkout: str, path: str, line: int) -> list[str]:
    lines = _lines(checkout, path)
    lo = max(1, line - CONTEXT)
    return [f"{n}: {redact(lines[n - 1][:MAX_TEXT])}" for n in range(lo, min(len(lines), line + CONTEXT) + 1)]


def line_digest(text: str) -> str:
    """SHA-256 of one line as a run stores it: cut at ``MAX_TEXT`` characters and masked when it reads as a credential. The
    sample keeps this and no text, and a re-run hashes the line it reads from the clone the same way, so a verdict is kept
    only for the text that was judged."""
    return hashlib.sha256(redact(text[:MAX_TEXT]).encode("utf-8")).hexdigest()


def _line_digest(checkout: str, path: str, line: int) -> str:
    lines = _lines(checkout, path)
    return line_digest(lines[line - 1]) if 0 < line <= len(lines) else ""


def public_entry(entry: dict, with_reviewer: bool = False) -> dict:
    """``entry`` as the sample file holds it: no ``context``, and a ``line_sha256`` (worked out from the context line of an
    older entry that has none, which is the text ``line_digest`` hashes)."""
    out = {k: v for k, v in entry.items() if k != "context"}
    if with_reviewer:
        out["reviewer"] = str(out.get("reviewer") or "")      # a field the owner fills, present on every row of a schema 2 sample
    elif not out.get("reviewer"):
        out.pop("reviewer", None)
    if not out.get("line_sha256"):
        marker = f"{entry['line']}: "
        stored = next((c[len(marker):] for c in entry.get("context", []) if c.startswith(marker)), None)
        out["line_sha256"] = hashlib.sha256(stored.encode("utf-8")).hexdigest() if stored is not None else ""
    return out


def redact(text: str) -> str:
    """``text`` with its quoted strings masked when it reads as a credential: a known token shape, or a long quoted
    value bound to a secret-like name. A sampled line may hold a key, public or not; the hit's ID does not depend on
    the text, so a verdict survives the masking. Standard library only, so this file ships in the public tree."""
    if not _CREDENTIAL.search(text):
        return text
    masked = _QUOTED.sub(r"\1<redacted>\1", text)
    return masked if not _CREDENTIAL.search(masked) else "<withheld: the line reads as a credential>"


def hit_id(hit: dict) -> str:
    """A stable short ID for one hit: the repository, its SHA, the place, the rule and the message."""
    key = f"{hit['repo']}@{hit['sha']}:{hit['path']}:{hit['line']}:{hit['rule']}:{hit['message']}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def draw_sample(hits: list[dict], rules: list[str], k: int = SAMPLE, seed: str = SEED) -> list[dict]:
    """Up to ``k`` hits per rule, drawn by a generator seeded with ``seed`` and the rule, from the hits in a fixed order."""
    out = []
    for rule in rules:
        mine = sorted((h for h in hits if h["rule"] == rule), key=lambda h: (h["repo"], h["path"], h["line"], h["message"]))
        picked = sorted(random.Random(f"{seed}:{rule}").sample(range(len(mine)), min(k, len(mine))))
        out += [mine[i] for i in picked]
    return out


def read_sample(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8-sig") as fh:      # a Windows editor may add a byte order mark
        for n, ln in enumerate(fh, 1):
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except ValueError as exc:       # the owner edits this file by hand to judge: say which line is broken
                raise RunError(f"{path}, line {n}: not valid JSON ({exc})") from exc
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("rule"), str):
                raise RunError(f"{path}, line {n}: a sample row is an object with an `id` and a `rule`")
            rows.append(row)
    return rows


def carry_verdicts(sample: list[dict], old: list[dict], moved: dict[str, str] | None = None) -> list[dict]:
    """``sample`` with the verdict and reason of each entry the old sample already judged (matched by ``id``, or by the
    ``id`` the hit had under the rule it moved from, ``MOVED_FROM``). When both entries hold the hash of the line and the
    hashes differ, the line is not the one that was judged and the verdict is not carried."""
    moved = MOVED_FROM if moved is None else moved
    judged = {e["id"]: e for e in old if e.get("verdict") in VERDICTS}
    for e in sample:
        prev = judged.get(e["id"])
        if prev is None and e["rule"] in moved:
            prev = judged.get(hit_id({**e, "rule": moved[e["rule"]]}))
        if prev and prev.get("line_sha256") and e.get("line_sha256") and prev["line_sha256"] != e["line_sha256"]:
            prev = None
        e["verdict"], e["reason"] = (prev["verdict"], prev["reason"]) if prev else (None, "")
        if prev and prev.get("reviewer"):
            e["reviewer"] = prev["reviewer"]
    return sample


def summarise(sample: list[dict], hits_by_rule: dict[str, int], need_reviewer: bool = False) -> dict[str, dict]:
    """Per rule: hits, sampled, true and false positives, unjudged, and precision (``None`` until all are judged). With
    ``need_reviewer`` (schema 2) each rule also counts the judged hits that name no reviewer, and its precision stays ``None``
    while there is one: a figure is published only from a sample that says who judged it."""
    out = {}
    for rule, hits in hits_by_rule.items():
        mine = [e for e in sample if e["rule"] == rule]
        tp = sum(e.get("verdict") == "tp" for e in mine)
        fp = sum(e.get("verdict") == "fp" for e in mine)
        unjudged = len(mine) - tp - fp
        unnamed = sum(e.get("verdict") in VERDICTS and not stats.named(e) for e in mine)
        ready = bool(mine) and not unjudged and not (need_reviewer and unnamed)
        out[rule] = {"hits": hits, "sampled": len(mine), "true_positives": tp, "false_positives": fp,
                     "unjudged": unjudged, "precision": round(tp / len(mine), 4) if ready else None}
        if need_reviewer:
            out[rule]["unreviewed"] = unnamed
    return out


def sample_totals(precision: dict[str, dict]) -> dict[str, int]:
    """Sampled, true and false positives over all rules, stored so the page prints no sum of its own."""
    return {k: sum(s[k] for s in precision.values()) for k in ("sampled", "true_positives", "false_positives")}


def _commit() -> tuple[str, bool]:
    """``(the harness commit the scanner comes from, whether it has uncommitted changes)``: the one ``MANIFEST.json`` of
    a release names, or an empty one for a tree that has none."""
    try:
        with open(os.path.join(ROOT, "MANIFEST.json"), encoding="utf-8") as fh:
            return str(json.load(fh).get("harness_commit", "")), False
    except (OSError, ValueError):
        return "", False


def commands(corpus: str | None = None, name: str | None = None) -> list[str]:
    """What the run does, as commands a reader can type, in the form the public tree uses (the page is the same in the
    harness). ``CACHE`` and ``OUT`` are folders outside the repository; ``TREE`` is the folder that holds the
    ``brevio`` package, the root of the public tree. For a corpus of ``corpus`` that is not its first, the first command names
    it and seeds from its own folder: without that, the page of a Python run would tell the reader to run the TypeScript one."""
    # Each git call names the cache folder with -C: typed in a checkout of this repository, a bare fetch and checkout
    # would switch that checkout to the corpus commit (review of the precision stage, 2026-11-12).
    repo = "CACHE/<owner>__<repo>"
    first = "python tools/precision_run.py --cache CACHE --out OUT --seed docs"
    if corpus and name and pick(corpus, None)["name"] != pick(corpus, name)["name"]:
        first = f"python tools/precision_run.py --corpus-name {name} --cache CACHE --out OUT --seed docs/{name}"
    return [first,
            f"git init -q {repo}",
            f"git -C {repo} fetch -q --depth 1 --no-tags <url> <sha>",
            f"git -C {repo} checkout -q --detach FETCH_HEAD",
            f"cd {repo} && python -I -c \"{_SCAN}\" TREE {' '.join(SCAN_ARGS)}"]


def scan_corpus(repos: list[dict], cache: str, tree: str) -> tuple[list[dict], list[dict], dict[str, str]]:
    """``(hits, per-repo rows, rule titles)`` for every repository of the corpus."""
    hits: list[dict] = []
    rows: list[dict] = []
    titles: dict[str, str] = {}
    for repo in repos:
        checkout = fetch(repo, cache)
        found = licence_of(checkout)
        if found != repo["licence"]:
            raise RunError(f"{repo['name']}: its licence file reads {found}, the corpus says {repo['licence']}")
        _LINES.clear()        # a file of the last run may have changed under the same path
        mine, counts = scan_repo(tree, checkout)
        titles = counts["rules"]
        for h in mine:
            h.update(repo=repo["name"], sha=repo["sha"])
            h["id"] = hit_id(h)
            h["context"] = _context(checkout, h["path"], h["line"])
            h["line_sha256"] = _line_digest(checkout, h["path"], h["line"])
        per_rule = {r: sum(h["rule"] == r for h in mine) for r in sorted(titles)}
        rows.append({"name": repo["name"], "url": repo["url"], "sha": repo["sha"], "licence": repo["licence"],
                     "files": counts["files"], "lines": counts["lines"], "hits": per_rule,
                     "hits_total": len(mine)})
        hits += mine
        _LINES.clear()
        print(f"[precision] {repo['name']}: {len(mine)} hit(s) in {counts['files']} file(s)", file=sys.stderr)
    return hits, rows, titles


def _old(out_dir: str) -> dict:
    path = os.path.join(out_dir, JSON_NAME)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def _write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:      # the same bytes on Windows as elsewhere
        fh.write(text)
    os.replace(tmp, path)


def write_outputs(data: dict, sample: list[dict], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    named = data.get("schema", 1) >= 2
    _write(os.path.join(out_dir, SAMPLE_NAME),
           "".join(json.dumps(public_entry(e, named), sort_keys=True) + "\n" for e in sample))
    _write(os.path.join(out_dir, JSON_NAME), json.dumps(data, indent=2, sort_keys=True) + "\n")
    _write(os.path.join(out_dir, PAGE_NAME), render(data))


def write_to_judge(sample: list[dict], out_dir: str) -> None:
    """The lines around each hit that has no verdict yet, for the person who judges it; removed when none is left."""
    rows = [{"id": e["id"], "repo": e["repo"], "path": e["path"], "line": e["line"], "rule": e["rule"], "context": e["context"]}
            for e in sample if e.get("verdict") is None and e.get("context")]
    path = os.path.join(out_dir, TO_JUDGE_NAME)
    if rows:
        _write(path, "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    elif os.path.exists(path):
        os.remove(path)


def seed_out(seed: str, out_dir: str) -> None:
    """Copy the judged results of ``seed`` into ``out_dir``, leaving a file that is already there, so a run in a new
    folder starts from the committed verdicts and changes none of them."""
    if not os.path.isdir(seed):
        raise RunError(f"--seed {seed}: no such folder")
    os.makedirs(out_dir, exist_ok=True)
    for name in (JSON_NAME, SAMPLE_NAME, BEFORE_SAMPLE_NAME):
        src, dst = os.path.join(seed, name), os.path.join(out_dir, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copyfile(src, dst)


def _before(old: dict) -> dict:
    return {k: old[k] for k in ("date", "tool", "totals", "precision")}


PUBLIC_REPO = ("name", "url", "sha", "licence")
TOTALS_ONLY, PER_REPOSITORY = "totals-only", "repository"


def split_detail(rows: list[dict], detail: str) -> tuple[list[dict], dict | None]:
    """``(the rows precision.json holds, the rows for the local detail file)``. In ``totals-only`` mode the published rows name
    each repository, its commit and its licence and carry no count per repository (the owner has not ruled whether hit counts of
    named repositories may be published); the counts go to ``precision_detail.json``, which a release does not ship."""
    if detail == PER_REPOSITORY:
        return rows, None
    return [{k: r[k] for k in PUBLIC_REPO} for r in rows], {"repos": rows}


def run(corpus: str, cache: str, out_dir: str, date: str, record_before: bool = False, scanner: str = ROOT,
        commit: tuple[str, bool] | None = None, name: str | None = None, detail: str | None = None) -> dict:
    """Fetch, scan, sample and write; returns the data written to ``precision.json``. ``scanner`` is the folder that
    holds the ``brevio`` package to scan with; ``commit`` names the commit it comes from (default: ``MANIFEST.json``). ``name``
    picks a corpus of the file (the first by default); ``detail`` is ``totals-only`` (the default) or ``repository``."""
    detail = detail or TOTALS_ONLY
    repos = load_corpus(corpus, name)
    check_cache(cache)
    old = _old(out_dir)
    digest = tree_digest(scanner, "brevio")
    hits, rows, titles = scan_corpus(repos, cache, scanner)
    rules = sorted(titles)
    hits_by_rule = {r: sum(row["hits"][r] for row in rows) for r in rules}
    if record_before and old:
        shutil.copyfile(os.path.join(out_dir, SAMPLE_NAME), os.path.join(out_dir, BEFORE_SAMPLE_NAME))
    sample = carry_verdicts(draw_sample(hits, rules), read_sample(os.path.join(out_dir, SAMPLE_NAME)))
    head, dirty = commit if commit is not None else _commit()
    public, local = split_detail(rows, detail)
    data = {
        "schema": SCHEMA, "date": date, "commands": commands(corpus, name), "rules": titles, "repos": public, "detail": detail,
        "corpus": {"name": corpus_name(corpus, name), "language": pick(corpus, name)["language"]},
        "reviewer": "",
        "method": {"export_set": EXPORT_SET, "sample_per_rule": SAMPLE, "seed": SEED, "context_lines": CONTEXT,
                   "poor_below": POOR_BELOW, "scan_args": list(SCAN_ARGS), "interval": stats.INTERVAL_NAME},
        "tool": {"harness_commit": head, "uncommitted_changes": dirty, "scanner_sha256": digest,
                 "python": platform.python_version()},
        "totals": {"repos": len(rows), "files": sum(r["files"] for r in rows), "lines": sum(r["lines"] for r in rows),
                   "hits": len(hits), "hits_by_rule": hits_by_rule},
        "precision": summarise(sample, hits_by_rule, True),
        "sample_totals": {},
        "review": old.get("review", {}),
        "moved_from": MOVED_FROM,
        "before": _before(old) if record_before and old else old.get("before"),
    }
    data["sample_totals"] = sample_totals(data["precision"])
    data["reviewer"] = ", ".join(stats.reviewers(sample))
    write_outputs(data, sample, out_dir)
    write_to_judge(sample, out_dir)
    if local is not None:
        _write(os.path.join(out_dir, DETAIL_NAME), json.dumps(local, indent=2, sort_keys=True) + "\n")
    return data


def rejudge(out_dir: str, corpus: str | None = None, reviewer: str = "", detail: str | None = None,
            publish: bool = False) -> dict:
    """Recompute the precision and the page from the judged sample, with no fetch and no scan. ``reviewer`` is set on each judged
    row that names none; ``publish`` refuses (``RunError``, nothing written) while a sampled hit has no verdict or a verdict has
    no reviewer. Naming a reviewer or publishing brings results recorded as schema 1 to schema 2. ``detail`` ``totals-only`` takes
    the counts per repository out of the results into the local detail file."""
    if reviewer and not stats.valid_name(reviewer):
        raise RunError(f"--reviewer {reviewer!r}: a name of at most {stats.MAX_NAME} characters with no line break or markup")
    data = _old(out_dir)
    if "totals" not in data:
        raise RunError(f"{os.path.join(out_dir, JSON_NAME)} holds no results to recompute; run once, or start from --seed DIR")
    data["commands"] = commands(corpus, data["corpus"]["name"])       # the page's Reproduce section shows the commands the script has now
    sample = read_sample(os.path.join(out_dir, SAMPLE_NAME))
    stats.stamp(sample, reviewer)
    problems = (stats.publish_problems(sample) + stats.size_problems(sample, data["totals"]["hits_by_rule"],
                                                                      data["method"]["sample_per_rule"])) if publish else []
    if problems:
        raise RunError("not published: " + "; ".join(problems))
    language = language_named(corpus, data["corpus"]["name"]) if corpus else None
    if language and "language" not in data["corpus"]:
        data["corpus"]["language"] = language
    if (reviewer or publish) and data.get("schema", 1) < 2:
        data.update(schema=SCHEMA, reviewer="", detail=data.get("detail", PER_REPOSITORY))
        data["method"]["interval"] = stats.INTERVAL_NAME
        data["corpus"].setdefault("language", DEFAULT_LANGUAGE)
    named = data.get("schema", 1) >= 2
    if detail == TOTALS_ONLY and data.get("detail", PER_REPOSITORY) == PER_REPOSITORY:
        data["repos"], local = split_detail(data["repos"], detail)
        data["detail"] = detail
        _write(os.path.join(out_dir, DETAIL_NAME), json.dumps(local, indent=2, sort_keys=True) + "\n")
    data["precision"] = summarise(sample, data["totals"]["hits_by_rule"], named)
    data["sample_totals"] = sample_totals(data["precision"])
    if named:
        data["reviewer"] = ", ".join(stats.reviewers(sample))
    if data.get("before"):
        before = read_sample(os.path.join(out_dir, BEFORE_SAMPLE_NAME))
        data["before"]["precision"] = summarise(before, data["before"]["totals"]["hits_by_rule"])
    write_outputs(data, sample, out_dir)
    return data


# --- the page: every figure in it is read from precision.json

def _pct(p: float | None) -> str:
    return "not judged" if p is None else f"{round(p * 100)}%"


def _share(s: dict) -> str:
    """The judged hits that were real, as counts ("15 of 20"): a bare "100%" reads as a promise (D18, 2026-11-13)."""
    if s.get("precision") is None:
        return "no reviewer named" if s.get("unreviewed") and not s.get("unjudged") else "not judged"
    return f"{s['true_positives']} of {s['true_positives'] + s['false_positives']}"


def _interval(s: dict) -> str:
    """The Wilson interval of a rule's published share, or an empty cell."""
    return "" if s.get("precision") is None else stats.interval_text(s["true_positives"], s["true_positives"] + s["false_positives"])


def _n(n: int) -> str:
    return f"{n:,}"


def by_rule(item: tuple[str, object]) -> tuple[int, ...]:
    """Rules in catalogue order (R2.5 before R13.1), not string order."""
    return tuple(int(p) for p in re.findall(r"\d+", item[0]))


def poor_rules(data: dict) -> list[str]:
    """Rules whose judged precision is below the threshold."""
    bar = data["method"]["poor_below"]
    return [r for r, s in sorted(data["precision"].items(), key=by_rule) if s["precision"] is not None and s["precision"] < bar]


def _results(data: dict) -> list[str]:
    out = ["| Rule | What it checks | Hits | Sampled | True | False | Precision | Interval |", "|---|---|---|---|---|---|---|---|"]
    for rule, s in sorted(data["precision"].items(), key=by_rule):
        out.append(f"| {rule} | {data['rules'][rule]} | {_n(s['hits'])} | {s['sampled']} | {s['true_positives']} "
                   f"| {s['false_positives']} | {_share(s)} | {_interval(s)} |")
    t = data["totals"]
    a = data["sample_totals"]
    out.append(f"| All | | {_n(t['hits'])} | {a['sampled']} | {a['true_positives']} | {a['false_positives']} | | |")
    return out


def _review(data: dict) -> list[str]:
    poor = poor_rules(data)
    out = [f"Rules whose sampled precision is below {_pct(data['method']['poor_below'])}: "
           + (", ".join(poor) if poor else "none") + "."]
    if data.get("review"):
        out += ["", "| Rule | Review |", "|---|---|"]
        out += [f"| {r} | {note} |" for r, note in sorted(data["review"].items(), key=by_rule)]
    return out


def _before_after(data: dict) -> list[str]:
    b = data.get("before")
    if not b:
        return []
    out = ["## Before and after the rule fixes", "",
           f"Before: the run of {b['date']} (scanner `{b['tool']['scanner_sha256'][:12]}`). After: this run "
           f"(scanner `{data['tool']['scanner_sha256'][:12]}`). Same corpus, same seed. The before sample and its "
           f"verdicts are in [{BEFORE_SAMPLE_NAME}]({BEFORE_SAMPLE_NAME}).", "",
           "| Rule | Hits before | Hits after | Precision before | Precision after |", "|---|---|---|---|---|"]
    moved = data.get("moved_from", {})
    for rule, s in sorted(data["precision"].items(), key=by_rule):
        was = moved.get(rule, rule) if moved.get(rule) in b["precision"] else rule
        p = b["precision"].get(was, {"hits": 0, "precision": None})
        label = rule if was == rule else f"{rule} (was {was})"
        out.append(f"| {label} | {_n(p['hits'])} | {_n(s['hits'])} | {_share(p)} | {_share(s)} |")
    out.append(f"| All | {_n(b['totals']['hits'])} | {_n(data['totals']['hits'])} | | |")
    return [*out, ""]


def _corpus(data: dict) -> list[str]:
    if data.get("detail") == TOTALS_ONLY:
        out = ["| Repository | Commit | Licence |", "|---|---|---|"]
        out += [f"| [{r['name']}]({r['url']}) | `{r['sha']}` | {r['licence']} |" for r in data["repos"]]
        return [*out, "", "Hits are counted over the whole corpus. The count for each repository is not published."]
    out = ["| Repository | Commit | Licence | Files | Lines | Hits |", "|---|---|---|---|---|---|"]
    for r in data["repos"]:
        out.append(f"| [{r['name']}]({r['url']}) | `{r['sha']}` | {r['licence']} | {_n(r['files'])} "
                   f"| {_n(r['lines'])} | {_n(r['hits_total'])} |")
    t = data["totals"]
    out.append(f"| All | | | {_n(t['files'])} | {_n(t['lines'])} | {_n(t['hits'])} |")
    return out


LANGUAGE_NAMES = {"typescript": "TypeScript and JavaScript", "python": "Python"}      # the names docs.py prints beside a figure


def _judges(data: dict) -> str:
    """The sentence about who judged: the reviewer the sample names, or a plain statement that the results do not say."""
    if data.get("schema", 1) >= 2 and data.get("reviewer"):
        return (f"**Reviewer: {data['reviewer']}.** The name is the one written in `precision_sample.jsonl` by whoever ran "
                "the measurement; the tool does not check it. Each verdict is made by reading the sampled hit in its code.")
    if data.get("schema", 1) >= 2:
        return ("**No reviewer is named yet.** A rule's figure is withheld until every sampled hit has a verdict and a "
                "reviewer.")
    return ("**These verdicts were recorded before the tool kept the name of the reviewer.** The results do not say who judged "
            "them, or whether one person did.")


def render(data: dict) -> str:
    """``precision.md`` from ``precision.json``: nothing on the page is typed by hand."""
    m, t, tool = data["method"], data["totals"], data["tool"]
    language = LANGUAGE_NAMES.get(data["corpus"].get("language", ""), "")
    lines = [
        "# Precision of `brevio-core scan` on open-source repositories" + (f": {language}" if language else ""), "",
        "<!-- Generated by tools/precision_run.py from precision.json. Do not edit by hand. -->", "",
        f"Run on {data['date']}. The scanner checked {t['repos']} pinned repositories ({data['corpus']['name']}"
        + (f", language: {language}" if language else "")
        + f"): {_n(t['files'])} files and {_n(t['lines'])} lines. It reported {_n(t['hits'])} hits. The figures are for this "
        "corpus and this language only.", "",
        "## Results", "",
        f"Precision is the share of sampled hits that are real problems, shown as the hits judged real of the hits judged. Up to {m['sample_per_rule']} hits per rule "
        "were drawn at random (seeded), then read in their code.", "",
        *_results(data), "",
        _judges(data) + " Each verdict and its one-line reason is in [precision_sample.jsonl](precision_sample.jsonl), so "
        "anyone can check or dispute them (see Reproduce): read the line at the path in the repository at that commit.", "",
        "The corpus is human-written code, so this is precision on human-written code, not on code an agent wrote. "
        f"The interval is the {m.get('interval', stats.INTERVAL_NAME)} for the share of judged hits that were real, worked out "
        "from the two counts in the same row. With few judged hits it is wide, and the figure is rough.", "",
        "## Review", "", *_review(data), "",
        *_before_after(data),
        "## Corpus", "", *_corpus(data), "",
        "## Method", "",
        "- The scanner is brevio-core's own code, run under `python -I`, so no file of the scanned repository can load code.",
        "- Each repository is fetched shallowly at its pinned commit into a cache outside this repository. Its licence "
        "file is checked against the corpus.",
        f"- The scan reads whole files: `brevio-core {' '.join(m['scan_args'])}`. It skips "
        "dependencies, build output, vendored folders, minified files and bundles. No rule reads a "
        "TypeScript declaration file (`.d.ts`).",
        f"- The sample is drawn per rule with the seed `{m['seed']}` from the hits in a fixed order, so the same hits "
        "give the same sample. The files quote none of the code: a sampled hit is stored with its repository, commit, path, "
        "line, rule, verdict and reason, and the SHA-256 of its line.",
        f"- Interval: {m.get('interval', stats.INTERVAL_NAME)}, on the hits judged for the rule. It is printed in the Interval column.",
        f"- Scanner: exported from harness commit `{tool['harness_commit']}`"
        + (" with uncommitted changes" if tool["uncommitted_changes"] else "")
        + f"; SHA-256 of the scanner's files `{tool['scanner_sha256']}`; Python {tool['python']}.", "",
        *_reproduce(data), *_own_corpus(),
    ]
    return "\n".join(lines)


def _reproduce(data: dict) -> list[str]:
    """The Reproduce section: the commands, and what to compare after a run (improvement plan 2a, 2026-11-12)."""
    seed = data["commands"][0].rsplit("--seed ", 1)[1]      # ``docs``, or the folder of a corpus after the first
    return [
        "## Reproduce", "",
        "You need git and a Python that brevio-core supports; no package is installed. In a checkout of this repository, with "
        "`CACHE` and `OUT` two folders outside it:", "",
        "```sh", *data["commands"], "```", "",
        "The first command does all the rest; the others show what it runs for each repository, with `TREE` the root "
        "of this repository, the folder that holds the `brevio` package. It needs the "
        "network, because it clones each pinned repository; the scan runs on the cloned files. "
        f"`--seed {seed}` starts `OUT` from the judged results in `{seed}/`, so the committed files stay as they are. A run keeps each verdict whose "
        "hit is still sampled, matched by the hit's ID and by the hash of its line, which the run reads again from the clone. A hit the run samples that has no verdict yet is listed with its surrounding lines in "
        "`OUT/precision_to_judge.jsonl`.", "",
        f"Compare `OUT/precision.json` with `{seed}/precision.json`. The date, the scanner's digest and the Python version "
        "differ by design; the hits and the figures per rule are what to compare. A figure that differs can mean that the "
        "scanner or a repository is not the one measured here; when you cannot tell, open an issue and attach both files.", "",
        "To dispute a verdict, open an issue with the form \"Dispute a precision verdict\" and name the hit's `id` from "
        "`precision_sample.jsonl`. `SUPPORT.md` says when to expect a first response.", "",
    ]


def own_commands() -> list[str]:
    """What the person who measures a corpus types, in order: pin, run, judge (an edit, not a command), publish. ``NAME`` is a
    corpus of ``tools/precision_corpus.json``; ``CACHE`` and ``OUT`` are folders outside the repository."""
    base = "python tools/precision_run.py --corpus-name NAME"
    return [f"{base} --cache CACHE --pin", f"{base} --cache CACHE --out OUT", f"{base} --out OUT --rejudge --reviewer YOURNAME",
            f"{base} --out OUT --publish --reviewer YOURNAME"]


def _own_corpus() -> list[str]:
    """The section for a person who measures another corpus, such as Python code (the commands are ``own_commands``)."""
    pin_, run_, judge, publish = own_commands()
    return [
        "## Measure a corpus yourself", "",
        "`tools/precision_corpus.json` can hold several corpora, each with a name and a language. Four steps, in order. The "
        "scan runs on local files, and a person judges the sampled hits.", "",
        "**Pin.** Give each repository a name, a URL and a `ref` (a branch or a tag) in the file, then run:", "",
        "```sh", pin_, "```", "",
        "With the network, this fetches each repository, writes the commit it found as `sha` and writes the licence it read "
        "from the licence file (MIT or Apache-2.0). It writes nothing when one repository fails.", "",
        "**Run.** This scans the pinned commits and draws the sample. Without `--detail` it writes totals only: no count per "
        "repository goes into `precision.json`. The counts are kept in `OUT/precision_detail.json`, which a release does not ship. "
        "`--detail repository` writes them into `precision.json`.", "",
        "```sh", run_, "```", "",
        "**Judge.** For a row of `OUT/precision_sample.jsonl`, read its line in `OUT/precision_to_judge.jsonl`, then set "
        "`verdict` to `tp` or `fp` and write a one-line `reason`. Write your name in `reviewer`, or let this command write it on "
        "the judged rows that have none:", "",
        "```sh", judge, "```", "",
        "**Publish.** This refuses, and writes nothing, while a sampled hit has no verdict or a verdict has no reviewer. "
        "Otherwise it writes the figures and this page, and the rule pages show them for the language of the corpus.", "",
        "```sh", publish, "```", "",
    ]


# --- the schema of precision.json, checked by tests/tools/test_precision_run.py

_TOP = {"schema": int, "date": str, "commands": list, "rules": dict, "repos": list, "method": dict, "tool": dict,
        "totals": dict, "precision": dict, "sample_totals": dict, "review": dict, "corpus": dict}
_REPO = {"name": str, "url": str, "sha": str, "licence": str, "files": int, "lines": int, "hits": dict,
         "hits_total": int}
_STAT = {"hits": int, "sampled": int, "true_positives": int, "false_positives": int, "unjudged": int}
_REPO_PUBLIC = {k: str for k in PUBLIC_REPO}
_TOP_2 = {"reviewer": str, "detail": str}        # what a schema 2 file adds; the language is checked with the corpus


def _typed(obj: dict, spec: dict, where: str) -> list[str]:
    return [f"{where}.{k}: want {t.__name__}" for k, t in spec.items() if not isinstance(obj.get(k), t)]


def check_schema(data: dict) -> list[str]:
    """Problems with the shape and the arithmetic of ``precision.json``; empty when it is sound."""
    problems = _typed(data, _TOP, "precision")
    schema2 = data.get("schema", 1) >= 2
    if schema2:
        problems += _typed(data, _TOP_2, "precision") + _typed(data["corpus"], {"language": str}, "corpus")
        problems += [] if data.get("detail") in (TOTALS_ONLY, PER_REPOSITORY) else ["precision.detail: not a known level"]
    if problems:
        return problems
    rules = set(data["rules"])
    totals_only = data.get("detail") == TOTALS_ONLY
    for i, r in enumerate(data["repos"]):
        problems += _typed(r, _REPO_PUBLIC if totals_only else _REPO, f"repos[{i}]")
        if not _SHA.match(str(r.get("sha", ""))):
            problems.append(f"repos[{i}].sha: not a full commit SHA")
        if totals_only:
            continue
        if isinstance(r.get("hits"), dict) and set(r["hits"]) != rules:
            problems.append(f"repos[{i}].hits: rules differ from the scanned rules")
        elif isinstance(r.get("hits"), dict) and r.get("hits_total") != sum(r["hits"].values()):
            problems.append(f"repos[{i}].hits_total: not the sum over the rules")
    if problems:
        return problems
    t = data["totals"]
    for key in ("files", "lines"):
        if totals_only and not isinstance(t.get(key), int):
            problems.append(f"totals.{key}: want int")
        elif not totals_only and t.get(key) != sum(r[key] for r in data["repos"]):
            problems.append(f"totals.{key}: not the sum over the repos")
    for rule in sorted(rules):
        s = data["precision"].get(rule)
        if not isinstance(s, dict) or _typed(s, _STAT, f"precision.{rule}"):
            problems.append(f"precision.{rule}: missing or mistyped")
            continue
        if s["hits"] != t["hits_by_rule"].get(rule) or (not totals_only and s["hits"] != sum(r["hits"][rule] for r in data["repos"])):
            problems.append(f"precision.{rule}.hits: not the sum over the repos")
        if s["true_positives"] + s["false_positives"] + s["unjudged"] != s["sampled"] or s["sampled"] > s["hits"]:
            problems.append(f"precision.{rule}: the sample does not add up")
        if schema2 and not isinstance(s.get("unreviewed"), int):
            problems.append(f"precision.{rule}.unreviewed: want int")
    if t.get("hits") != sum(t["hits_by_rule"].values()):
        problems.append("totals.hits: not the sum over the rules")
    if data["sample_totals"] != sample_totals(data["precision"]):
        problems.append("sample_totals: not the sum over the rules")
    return problems


def main(argv: list[str] | None = None, out_dir: str = OUT_DIR, runner: Callable[..., dict] | None = None,
         rejudger: Callable[..., dict] | None = None) -> int:
    """The command line. ``out_dir``, ``runner`` and ``rejudger`` let a caller that keeps more (the harness, which also
    exports the scanner and writes the rule pages' figures) reuse it."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", default=CORPUS, help="the pinned repositories (default tools/precision_corpus.json)")
    ap.add_argument("--cache", default=DEFAULT_CACHE, help=f"where the checkouts live, outside the repo (default {DEFAULT_CACHE})")
    ap.add_argument("--corpus-name", metavar="NAME", help="which corpus of the file to use (default: the first)")
    ap.add_argument("--out", help=f"where the results go (default {os.path.relpath(out_dir, ROOT)}, and a folder named for the corpus there when it is not the first)")
    ap.add_argument("--scanner", default=ROOT, metavar="DIR", help="the folder that holds the brevio package to scan with (default: this tree)")
    ap.add_argument("--seed", metavar="DIR", help="start --out from the judged results in DIR (files already in --out stay)")
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()      # noqa: UP017 (Python 3.10 has no datetime.UTC)
    ap.add_argument("--date", default=today, help="the run's date")
    ap.add_argument("--record-before", action="store_true",
                    help="keep the current figures as the 'before' of a rule change, then run again")
    ap.add_argument("--rejudge", action="store_true", help="recompute the figures and the page from the judged sample only")
    ap.add_argument("--pin", action="store_true",
                    help="pin each repository of the corpus to a commit and record its licence (needs the network), then stop")
    ap.add_argument("--detail", choices=(TOTALS_ONLY, PER_REPOSITORY),
                    help="a run writes totals-only unless told otherwise; with --rejudge, totals-only takes the counts per repository out")
    ap.add_argument("--reviewer", default="", metavar="NAME", help="with --rejudge or --publish: name on each judged row that has none")
    ap.add_argument("--publish", action="store_true",
                    help="recompute like --rejudge, but refuse while a sampled hit has no verdict or a verdict has no reviewer")
    args = ap.parse_args(argv)
    try:
        if args.pin:
            check_cache(args.cache)       # the clones are not to be committed, so a pin refuses a cache inside this repository as a run does
            for r in pin(args.corpus, args.cache, args.corpus_name):
                print(f"[precision] pinned {r['name']} at {r['sha']} ({r['licence']})", file=sys.stderr)
            return 0
        out = args.out or results_folder(args.corpus, args.corpus_name, out_dir)
        if args.seed:
            seed_out(args.seed, out)
        if args.rejudge or args.publish:
            data = (rejudger or rejudge)(out, args.corpus, args.reviewer, args.detail, args.publish)
        else:
            data = (runner or run)(args.corpus, args.cache, out, args.date, args.record_before, args.scanner,
                                   name=args.corpus_name, detail=args.detail)
    except (RunError, OSError, subprocess.SubprocessError) as exc:
        print(f"precision_run: {exc}", file=sys.stderr)
        return 1
    unjudged = sum(s["unjudged"] for s in data["precision"].values())
    print(f"[precision] {data['totals']['hits']} hit(s); {unjudged} sampled hit(s) still to judge", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
