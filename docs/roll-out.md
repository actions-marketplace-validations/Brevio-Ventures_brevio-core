# Roll out to many repositories

Steps for a team that wants the same check in many repositories: a required check on the pull request, the agent hook on the
machines that run coding agents, and one policy file that the repositories read. It says what brevio-core gives and what a
policy can switch off. It does not describe how a coding client or GitHub treats settings an organisation deploys; read their
documentation for that, and test on one repository before you use it on many.

## Make the check required

1. In one repository, run `brevio-core init` (or `brevio-core init --dry-run` first to read the diff). It writes the
   pull-request workflow, pinned to this release.
2. Open a pull request so the workflow runs once. GitHub lists a check by the name of the job, which is `brevio-core` in the
   workflow that `init` writes. The scan does not report the baseline that `init` wrote in this pull request as new
   suppressions, so the pull request can pass. A baseline edited afterwards, or added by hand, needs a reviewer: set the
   `allow-new-suppressions` input to `true` for that run, and use CODEOWNERS on `.brevio/` so that a person approves it.
3. In the repository settings, add that check as a required status check of a branch ruleset (or of branch protection).
4. For many repositories, create the ruleset at the organisation level and target the repositories by name or by a property,
   or start new repositories from the [agent-safe starter template](https://github.com/Brevio-Ventures/brevio-core/tree/main/templates/agent-safe-starter).
5. Start with `fail-on: none` for a week, so the pull request shows what the check would report and nothing fails. Record
   what a repository has now with `brevio-core baseline`, then change the input back to the default. The README's "Roll it
   out" steps go in this order.

## Put the agent hook on the machines

`brevio-core hook install` writes the entries below into the project's `.claude/settings.json` (and the Cursor entries into
`.cursor/hooks.json`). They are generated from the code, and a test compares this page with them.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "uvx brevio-core==0.11.0 hook run --event post-edit",
            "timeout": 30
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "uvx brevio-core==0.11.0 hook run --event post-command",
            "timeout": 30
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uvx brevio-core==0.11.0 hook run --event stop",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

This repository ships no settings file for a client's managed or organisation-wide policy. A team that deploys settings to
its machines can place the same entries in the file its client reads for that, as the client's documentation describes. Test
that the client runs the hook there before you rely on it. The required check does not depend on the hook.

## Share one policy file

Put the settings the repositories should share in one `brevio-core.toml` and point the others at it with `extends`: a file
vendored into the repository, or a git checkout pinned to a commit. The scan reads only files already in the checkout and
fetches no policy. To change the policy, update the pin in a pull request in the repositories one by one, so a person reviews the move.

## What a policy file can switch off

A policy file can:

- stop rules from running (`ignore`, or leave them out of `select`) and change a rule's level to `warn`;
- leave paths out (`exclude`) and change the rules for some paths (`[[overrides]]`);
- raise a learned guard to `error`, so that a hit fails the check.

A policy file does not reach:

- the test-custody findings R20.1 to R20.4: a setting does not turn them off, and an ignore comment is not read for them;
- the agent hook and the MCP server: they do not read an ignore comment.

The changes a policy makes are visible. A diff scan reports a pull request that adds or edits a `brevio-core.toml`, an ignore
comment or a baseline entry as a new suppression until a reviewer allows it, with `--allow-new-suppressions` or the Action input of that name. `brevio-core config <path>` prints the settings
that apply to a file and the files they come from, so a reviewer can see what a repository switched off.

## Check one repository before the next

1. Open a pull request that adds a planted finding, such as a `@ts-ignore` comment, and see that the check reports it.
2. Run `brevio-core config src` and read the settings the repository ends up with.
3. Ask an agent on a rolled-out machine to make an edit that breaks a rule, and see that the hook returns the finding.
