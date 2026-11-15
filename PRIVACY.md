# Privacy

Who is responsible: Brevio, reachable at legal@brevio.pro. This notice covers the tool, the people who contribute to
this repository, and the repositories that use it.

## The tool

brevio-core collects nothing about you or your code. `scan`, the agent hook and the MCP server use no network and send
nothing. No command counts its use or reports it anywhere, and there is no setting to turn that on.
[SECURITY-REVIEW.md](SECURITY-REVIEW.md) lists what each command reads, writes and sends.

Local trap-to-fix episode memory written by `brevio-core brief --record` stays on disk in `.brevio/file_memory.json`.
`brevio-core up` only inspects local `.brevio/` files to preview what a team sync would share, making zero network calls.

## Contributors

When you contribute, this is kept:

- your GitHub handle, on your pull requests, issues, reviews and Discussions;
- the name and email address in your commits and in your `Signed-off-by` line, in this repository's public git
  history;
- in the private harness this repository is exported from, the commit that brings your change back names the pull
  request and your GitHub handle.

Why: the sign-off records that you may submit the change under Apache-2.0, and the history and the credit show where
each line came from. The lawful basis is our legitimate interest (Article 6(1)(f) GDPR) in keeping a record of the
origin and licence of the code and in crediting you. It is kept for as long as the code is distributed. Git history
is shared by everyone who has cloned it, so a commit cannot be removed from it; we can stop using your handle in
anything we write later.

## Repositories that use brevio-core

To learn whether brevio-core is used, we search public GitHub code for workflows that run it. Each repository found is
stored only as a salted hash of its name, never the name itself, so we can count how many still run it four weeks
later. The hash lists are deleted after 90 days; after that only the counts are kept. No repository or owner name is
printed, stored or published. The lawful basis is our legitimate interest in knowing whether the project is used.

## How to ask

Write to legal@brevio.pro with "Privacy" in the subject to ask what we hold about you, to correct it, or to object.
We answer within one month. You may also complain to your data protection authority.

## Before `share` or `up` sends anything

`share` is not part of this release. `up` in this release only inspects local `.brevio/` files to preview what a team
sync would share, with their sizes and fields, making zero network calls; it uploads
nothing and makes no link. When the sending forms ship, each one shows a prompt like the ones below before
anything leaves your machine, the answer defaults to no, and they never take the answer from an agent: the agent hook
and the MCP server cannot run them. In a run with no terminal to ask in, they stop and say how to give consent.

`share` would turn the counts that `gain` prints into a public link. `gain` itself prints no link:

```
brevio-core share makes these counts public at a link anyone can open:

  <the counts, exactly as they will be published>

It holds counts only: no code, no file names and no repository name.
Make it public? Type yes to go on, anything else to stop:
```

`up` packs learned guards and rules into a link to brevio.pro:

```
brevio-core up packs these files into a link to brevio.pro:

  <each file, with its size>

The repository name goes only as a salted hash. Opening the link sends these files to brevio.pro, which keeps them
for your account until you delete them there.
Make the link? Type yes to go on, anything else to stop:
```
