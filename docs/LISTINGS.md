# Listings: what the release does by itself, and the owner's steps

Pushing a version tag `vX.Y.Z` starts `.github/workflows/release.yml`. The package itself contacts no
listing; only the workflow does, after the tests pass.

## What the release workflow does by itself

In this order, the jobs with only the permissions they need:

1. **Build.** Runs the tests, builds the package with the toolchain pinned by hash (`scripts/build-requirements.txt`),
   and prepares the files the later jobs publish (`scripts/release_assets.py`): the `brevio-core.pyz` zip
   application, its CycloneDX SBOM, the release notes, `server.json` with the version from `VERSION`, and a copy of the
   skill without its `license:` line.
2. **PyPI**, by trusted publishing, in the `brevio-core-pypi` environment. Waits for its reviewers.
3. **Attestation** of `brevio-core.pyz` (build provenance).
4. **GitHub release** for the tag: the notes are `RELEASE.md`, the release commit and the SHA-256 of the `.pyz`, then
   notes generated from the commits. `brevio-core.pyz`, its attestation bundle and the SBOM are attached.
5. **The major tag** (`v0` until 1.0) moves to the release commit, in the `brevio-core-major-tag` environment. Waits
   for its reviewers.
6. **The MCP registry**, in the `mcp-registry` environment: `mcp-publisher login github-oidc`, then `publish`. No token
   is stored; the workflow's identity vouches for the `io.github.Brevio-Ventures` namespace. The registry checks the package on PyPI,
   so this runs after it and retries for a few minutes.
7. **The skill registry**, in the `clawhub` environment, when a `CLAWHUB_TOKEN` secret is set there. Without it the job
   says so and does nothing. The registry publishes skills under the MIT-0 licence, so only the
   `skills/brevio-core` folder goes there, without the `license:` line. The code stays Apache-2.0 here and on PyPI.

To check a downloaded `.pyz`: `gh attestation verify brevio-core.pyz --repo Brevio-Ventures/brevio-core`, and compare its
SHA-256 with the release notes.

## The owner's steps for a release

1. Merge the release pull request.
2. Tag the merge commit `vX.Y.Z` (signed) and push the tag.
3. Approve the `brevio-core-pypi` environment, then the `brevio-core-major-tag` environment.
4. The GitHub Marketplace: open the release, edit it, tick "Publish this Action to the GitHub Marketplace" and save.
   GitHub offers this only on the release page.

## The repository's description and topics

One sentence everywhere: the repository description, the PyPI summary (`description` in `pyproject.toml`), the first
line of the README and the docs site. Paste it into the repository's About box:

```
Catches the mistakes coding agents repeat before they merge: a pull-request gate with no model call and no API key.
```

Website: the docs site, `https://brevio-ventures.github.io/brevio-core`. Topics (the About box takes them one by one; PyPI reads the same words from
`keywords` in `pyproject.toml`):

```
coding-agents agent-mistakes code-review pull-request github-action pre-commit mcp-server agent-hooks static-analysis code-quality
```

## Once

- **Environments.** Create `brevio-core-pypi` and `brevio-core-major-tag` with required reviewers, and `mcp-registry`
  and `clawhub` (reviewers optional). The PyPI trusted publisher names `release.yml` and `brevio-core-pypi`.
- **The skill registry token.** `clawhub login` on your machine, then `clawhub token` prints the token. Store it as the
  secret `CLAWHUB_TOKEN` of the `clawhub` environment.
- **The first Marketplace listing:** as in step 4, plus the categories.
- **The MCP server directory.** Add the repository to the directory, then claim it. `glama.json` at the root of the
  repository is what the claim rests on: it names `rmb` as the maintainer, so change that to the claiming
  GitHub username first if it is not right.
## Awesome lists, in launch week

The owner opens a pull request by hand, from their own account; nothing here is automated. Read the list's
contributing rules first: most want one line, in alphabetical order, in the right section, and a repository with a
licence and a README. An entry below leads with the same sentence as the description.

- **The awesome list of MCP servers**, under "Developer Tools", in alphabetical order:

  ```
  - [Brevio-Ventures/brevio-core](https://github.com/Brevio-Ventures/brevio-core) 🐍 🏠 - Catches the mistakes coding agents repeat before they merge: scan files or a diff, read a rule page, compile a fixed bug into a local guard. No model call; `scan`, the agent hook and the MCP server use no network and send nothing.
  ```

  Pull request title: `Add brevio-core (developer tools)`. Body:

  ```
  Adds brevio-core, in Developer Tools. It is a local MCP server over stdio (Python, standard library only) that
  serves the scan, the rule pages and learn to an agent. The gate commands make no model call and need no API key.
  Licence: Apache-2.0. I maintain it.
  ```

- **The awesome list for Claude Code**, under "Hooks" (or "Tooling" when the list has no hooks section):

  ```
  - [brevio-core](https://github.com/Brevio-Ventures/brevio-core) - Catches the mistakes coding agents repeat before they merge. A PostToolUse and Stop hook scans an edit and hands the findings back to the agent; the same rules run as a pull-request check. No model call, no API key.
  ```

  Pull request title: `Add brevio-core (hooks)`. Body:

  ```
  Adds brevio-core, a hook that scans a file the agent edits against fixed rules for the mistakes coding agents
  repeat, and returns the findings with decision: block so the agent fixes them in the same turn. The hook and the
  MCP server do not honour an ignore comment, so an agent that adds one still sees the finding; a person can silence
  one finding with a reason, in a scan they run themselves. Set up with `brevio-core init --with hook`. Apache-2.0;
  I maintain it.
  ```

- **The awesome list of Cursor rules and hooks**, under "Hooks" or "Tools":

  ```
  - [brevio-core](https://github.com/Brevio-Ventures/brevio-core) - Catches the mistakes coding agents repeat before they merge: a postToolUse and stop hook plus a rule file, and the same rules as a pull-request check.
  ```

- **The awesome list of agent skills**, under "Code quality" or "Developer tools":

  ```
  - [brevio-core](https://github.com/Brevio-Ventures/brevio-core/tree/main/skills/brevio-core) - Catches the mistakes coding agents repeat before they merge: tells the agent when to scan its changes and how to read a finding.
  ```

Same body as above, adjusted to the list. Do not ask anyone for a star, and do not open the same pull request twice.

## Later, once there is traction

- The pre-commit hook list.
- The awesome lists of GitHub Actions and of static analysis tools, with the Action and the rule pages.
