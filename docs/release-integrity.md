# Release integrity checklist

For the people who publish brevio-core. It lists steps to take and a way to check the result of a step. It does not say the steps are
enough: a person who reads a release can still decide to run it or not, and [SECURITY-REVIEW.md](https://github.com/Brevio-Ventures/brevio-core/blob/main/SECURITY-REVIEW.md) says what
a release is checked against.

## Standing setup

1. **Publish by trusted publishing, with no stored token.**
   - On the registry, add this repository's release workflow and the environment `brevio-core-pypi` as the trusted publisher
     of the project.
   - Look at the registry's list of tokens for the project and the repository's secrets. Delete a token that the release does
     not use, so that no long-lived upload credential stays on either side.
   - Check: the `publish` job of `.github/workflows/release.yml` has `id-token: write` and no secret of the registry.
2. **Ask the environment for approval.** In the repository settings, make the environment `brevio-core-pypi` require a
   reviewer who is not the person who pushed the tag.
3. **Keep the attestations.** The release workflow attests the `.pyz`, and the registry keeps an attestation for the files it
   publishes. After a release, run both checks of "Provenance" in [SECURITY-REVIEW.md](https://github.com/Brevio-Ventures/brevio-core/blob/main/SECURITY-REVIEW.md) on the files
   you published, and write down the result in the release notes.
4. **Protect the accounts that can publish.**
   - On the registry account and on the GitHub accounts of the maintainers, turn on two-factor sign-in with a hardware key.
   - Register a second key and keep it somewhere other than the first. Keep the recovery codes offline.
   - Remove sign-in methods that are weaker than the hardware key where the service allows it.
5. **Name a second person.** One other person has these rights, and has used them once on a test project:
   - to pull a release: yank a version on the registry and move the `v0` tag;
   - to read the security inbox: the address in [SECURITY.md](https://github.com/Brevio-Ventures/brevio-core/blob/main/SECURITY.md) reaches both of you, and a test message sent to
     it was seen by both.
6. **Pin what you tell others to run.** Give the pinned form of the commands you publish, as in the last section below.

## At a release

1. Build from a tag that matches `VERSION`, as the workflow does, and approve the environment only after you have read the
   release notes. Before the tag, check that the Windows jobs of the test matrix passed on the release pull request.
2. Run the two attestation checks of the list above on the files that were published.
3. Look at the registry page of the release: the files, their hashes and the attestation are the ones the workflow built.

## If a release is wrong

Work in this order. Write down the time of a step and what you saw, so the notes after the incident are not from memory.

1. **Stop new installs of the version.** Yank it on the registry. A yanked version stays installable by an exact pin, which is
   why the next steps still matter.
2. **Move the moving tag back.** Point `v0` at the last release that is good.
3. **Close the way in.** If an account, a token or the workflow was involved, remove that access first: sign out sessions,
   rotate the keys of the account, remove the trusted publisher until the workflow is reviewed.
4. **Say what is known.** Publish a security advisory on the repository with the versions affected, what the release did, and
   what a person who installed it should do. Do not wait to know everything: say what is known and when you will update.
5. **Publish a fixed version** under a new number. A number that was released is not reused.
6. **Tell the people who pinned.** An exact pin does not move by itself. The advisory and the release notes name the version
   to move to.
7. **Write down what changed** in this checklist, in the same pull request as the fix.

## The pinned form of the agent command

An agent file that says `brevio-core scan` runs whatever the machine has. The form that names the release is:

```sh
uvx brevio-core==0.11.0 scan <files>
```

`brevio-core init` writes this form into the marked agent rules in `AGENTS.md`, and `brevio-core init --with hook` writes the
agent hook with the same pinned form. To move to a newer release, change the number in a pull request, so a person reviews
the move.
