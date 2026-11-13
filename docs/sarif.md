# The SARIF format

`brevio-core scan --sarif FILE` writes the findings as a SARIF 2.1.0 log beside its normal output. GitHub code
scanning reads it (the Action's `sarif: true` uploads it), and so do most code review tools.

```sh
brevio-core scan --diff origin/main --sarif brevio-core.sarif
```

## The shape

One run, with the tool's rules and one result per finding:

```json
{
  "version": "2.1.0",
  "runs": [
    {
      "tool": {
        "driver": {
          "name": "brevio-core",
          "rules": [
            {"id": "R9.2", "shortDescription": {"text": "No silenced type errors: no @ts-ignore; every @ts-expect-error states its reason"}}
          ]
        }
      },
      "results": [
        {
          "ruleId": "R9.2",
          "level": "error",
          "message": {"text": "`@ts-ignore` used; use `@ts-expect-error` with explanatory reason"},
          "locations": [
            {"physicalLocation": {"artifactLocation": {"uri": "src/api.ts"}, "region": {"startLine": 12}}}
          ]
        }
      ]
    }
  ]
}
```

| Field | What it holds |
| --- | --- |
| `tool.driver.rules` | the rules of the catalogue, with the one line of each as `shortDescription`, so a viewer can name a rule ID; also `suppression` and `guard:<id>` when a result uses them |
| `tool.driver.version` | the version of the release that wrote the log; left out when the tool is not a release |
| `rules[].helpUri` | in a release whose source is on GitHub, the page of the rule at the tag of that version, `https://github.com/Brevio-Ventures/brevio-core/blob/v0.10.0/docs/rules/<ID>.md`; left out otherwise |
| `ruleIndex` | the position of the result's rule in `tool.driver.rules` |
| `ruleId` | the rule ID, such as `R9.2`; its page is `rules/<ID>` on this site |
| `level` | `error`, or `warning` for a rule `brevio-core.toml` sets to `warn` |
| `message.text` | the finding's message, as the text report prints it |
| `locations` | the file, relative to the folder the scan ran in, and the line |
| `suppressions` | only on a finding a person silenced with an ignore comment: `[{"kind": "inSource", "justification": "<the reason>"}]` |

## Silenced findings

An ignore comment with a reason (`brevio-core: ignore R9.2 -- reason`) hides a finding from the text report, but SARIF
keeps it, marked with a suppression that carries the reason. Code scanning shows it as dismissed in source, so a
reviewer can still see what was silenced and why. A baselined finding is not in the log.

## What it does not hold

No code snippet, no fingerprint and no information about the machine. The paths and messages are the same as in the
text report.
