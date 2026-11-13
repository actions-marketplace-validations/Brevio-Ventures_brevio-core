# An example report

`brevio-core scan --diff main --format markdown` prints the report below for the change on the
[See it work](try-it.md) page. The Action puts the same report in the job summary and in the comment on a
pull request. This page is written by the release tool from a run of this release.

## As printed

```markdown
### brevio-core

2 finding(s).

| Rule | Where | Why |
| --- | --- | --- |
| R5.4 | app.py:4 | Bare \`except:\` detected; specify explicit exception tuple |
| R9.2 | app.ts:2 | \`@​ts-ignore\` used; use \`@​ts-expect-error\` with explanatory reason |

The table lists added lines only.
brevio-core: 2 finding(s) in 2 file(s), 0 baselined, 0 silenced
```

## As it reads

> ### brevio-core
>
> 2 finding(s).
>
> | Rule | Where | Why |
> | --- | --- | --- |
> | R5.4 | app.py:4 | Bare \`except:\` detected; specify explicit exception tuple |
> | R9.2 | app.ts:2 | \`@​ts-ignore\` used; use \`@​ts-expect-error\` with explanatory reason |
>
> The table lists added lines only.
> brevio-core: 2 finding(s) in 2 file(s), 0 baselined, 0 silenced
