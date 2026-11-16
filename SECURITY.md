# Security policy

## Reporting a vulnerability

Please report a vulnerability privately to security@brevio.pro. Do not open a public issue for it.

You can also use GitHub's private vulnerability reporting on this repository.

Say which version you used, what you did and what you saw. We will acknowledge your report and keep you told of the
fix. Please give us time to ship a fix before you publish details.

## Scope

brevio-core reads source files and diffs on your machine. `scan`, the agent hook and the MCP server use no network and
send nothing. A report that any command sends data,
runs code from the files it scans, or writes outside the files you name is in scope.

The optional `brevio-core ai` commands send a request when you connect a model or a program and answer yes. The
request is the text they print first: lines of your code, or rule IDs and counts for `summarize`. It goes to the address you
connected or, with a command connector, to the standard input of the program you named. A report that they send more than the
request they print, send without your yes, or start a program other than the one you named is in scope. What the program you
named does with the request is outside brevio-core.

## Supported versions

Only the latest release gets fixes.
