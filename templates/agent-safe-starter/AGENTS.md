<!-- brevio-core:start -->
## brevio-core

- After you edit code, scan the files you changed: `brevio-core scan <files>`. If it is not installed, run `uvx brevio-core==0.11.0 scan <files>`.
- Fix a finding in the code itself, also one the agent hook reports after an edit or before you stop.
- Never add an ignore comment and never edit `.brevio/baseline.json` to make a finding go away.
- When you cannot fix a finding, stop and tell the human the rule and the line.
- After you fix a bug, run `brevio-core learn` so the fix becomes a guard.
<!-- brevio-core:end -->
