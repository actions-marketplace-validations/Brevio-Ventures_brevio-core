# Roadmap

What is planned for the next release, in plain words. Only the next release is listed; later ideas live in
Discussions. The list changes as work lands, and nothing on it takes a shipped feature away ([GOVERNANCE.md](GOVERNANCE.md)).
A feature that has shipped is in the [changelog](docs/CHANGELOG.md), not here.

## Next release

- **A facts line at the end of a scan report.** It names the version, the rules that ran after your config file, the
  files read and the base. It also gives the time of the run. It is off unless you ask for it, and the Action gets an input for it.
- **A list of paths a learned guard leaves alone.** A guard meant for application code stops flagging your tests.
- **Scans that read files one at a time**, so a large folder needs less memory.
- **Precision by language.** The precision page and the rule pages name the corpus, the scanner and the date, and say
  "not measured" for a language with no corpus. Hit counts per repository are not published.
- **Honest output from the pull-request commands.** They print what they read from a file, or say "not run". They say
  so when git could not give them the diff.
- **Starter packs described as hand-written**, with the guards tested on a line they must flag and a line they must not.
- **A lock on Windows** for the call ledger of the optional model command.

Missing something? Ask in Discussions.
