"""Words of ``brevio-core ai`` that the commands of the 0.7 set after ``explain`` and ``fix`` print: the first-use agreement
of ``summarize`` and ``learn``, and of a command connector (improvement plan, 2026-11-12). Plain strings, no import: the loader
text and the tests read them without loading the rest.

Each sentence says what the code does; ``tests/pipeline/core_export/test_core_ai_learn.py`` ties the numbers in it to the
constants of the commands. The first-use text of ``explain`` and ``fix`` with an endpoint stays in ``core_ai``.
"""
from __future__ import annotations

EXPLAIN_SYSTEM = ("You review one line of code against one rule of a static checker. Answer briefly, about that line only, "
                  "in plain words.")       # the one line both connectors send with ``explain``, so the two ask the same
SENDS = {
    "explain": "the file's extension, the rule's text and the flagged line; lines around it only if you pass --context N. "
               "Anything written in those lines goes with them. No file name and no path",
    "fix": "the same, with 5 lines either side of the flagged one unless you pass --context 0, and it asks for replacement "
           "lines. Anything written in those lines goes with them. No file name and no path",
    "summarize": "the rule IDs and rule titles of the findings of a scan of the diff, and how many findings a rule has; "
                 "no source line, no file name and no title of a learned guard",
    "learn": "the lines the fix removed from the file it changed, with runs of spaces folded to one (at most {lines} lines, none "
             "longer than {chars} characters), and the file's extension; it asks for up to three patterns. Anything written in those lines goes "
             "with them. No file name and no path",
}
_FILE_CHECK = ("Nothing at all is sent when a line to be sent looks like a secret (the scanner's R13.1 rule, and a stricter "
               "check: key blocks, passwords in addresses, bearer tokens, long quoted tokens; it does not catch every "
               "secret), when the file is one that holds secrets (.env*, .netrc, *.pem, *.key, id_* and similar, or a link "
               "to one), or when the repository's brevio-core.toml denies its path.")
CHECKS = {
    "explain": _FILE_CHECK, "fix": _FILE_CHECK, "learn": _FILE_CHECK,
    "summarize": "Nothing at all is sent when a line of the request looks like a secret (the checks of the other commands).",
}
ENDPOINT_TAIL = ("Each request is printed in full first (the exact bytes, their size, the header names and any proxy your "
                 "environment sends it through) and asks \"Send it?\". It goes to the address you connected, through that "
                 "proxy if there is one; nothing goes to Brevio.\n")
LEARN_TAIL = ("`learn` takes no --yes: you answer \"Send it?\" at a terminal each time, and a second question before anything "
              "is written. What the model proposes is data: a pattern is kept only when it passes the checks of "
              "`brevio-core learn`.\n")
COMMAND_TAIL = (
    "Each request is printed in full first and asks \"Send it?\". It goes to the standard input of the program you named, "
    "not to an address. brevio-core starts that program once, with no shell, from a new empty temporary folder, with the "
    "environment variables a program needs to start and the ones you listed, and stops it after the timeout or when it "
    "writes more than a size cap. The program receives the request and may itself send it elsewhere: brevio-core cannot see "
    "what the program does with it. It is started only for a person at a terminal and not when a CI variable is set.\n")


def first_use(command: str, kind: str, lines: int = 0, chars: int = 0) -> str:
    """The agreement text for ``command`` (``summarize`` or ``learn``) with a connector of ``kind``."""
    tail = COMMAND_TAIL if kind == "command" else ENDPOINT_TAIL
    extra = LEARN_TAIL if command == "learn" else ""
    sends = SENDS[command].format(lines=lines, chars=chars)
    return (f"First use of brevio-core ai {command} in this repository. What it sends, once you say yes to a request:\n"
            f"  {command}  {sends}\n{CHECKS[command]} {tail}{extra}")
