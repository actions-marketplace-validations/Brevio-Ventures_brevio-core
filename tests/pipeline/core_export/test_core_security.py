"""The claims of SECURITY-REVIEW.md, as tests: no network, only ``git`` with read-only subcommands, no dynamic code,
and an Action that pins what it uses and never runs on the privileged events (ROADMAP E24, E26, 2026-11-05).

Standard library only: the exporter copies this file beside the code and it runs unchanged in the public tree. In the
harness it first exports the newest set and reads that copy, so it checks exactly the modules that ship.

One module is exempt, by name: ``core_ai_net``, the endpoint transport of ``brevio-core ai`` (the 0.7 set, owner decision
D24, 2026-11-11). It may import ``urllib`` and ``http`` and nothing else of the network; no other shipped module may
import any, ``up`` included (it ships as a preview that uploads nothing, ROADMAP E37, 2026-11-05). This is the clause
this file always kept for a sending command: an exception named here, for its own module only, loaded by name from
``core_cli`` and never imported by the gate (the import-graph tests below). The other is ``core_ai_exec``, which starts the
program a user named (``PROCESS_EXCEPTIONS``; 2026-11-12); no other module starts anything but a read-only ``git`` call.
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
EXPORTER = os.path.join(ROOT, "tools", "export_core.py")      # present in the harness checkout only
HARNESS = os.path.isfile(EXPORTER)
TREE = ROOT
_EXPORT = tempfile.TemporaryDirectory()
# Where the Action and the workflows sit: the repository root in the public tree, the release templates in the harness.
ACTION = os.path.join(ROOT, "tools", "core_action", "action.yml") if HARNESS else os.path.join(ROOT, "action.yml")
WORKFLOWS = (os.path.join(ROOT, "tools", "core_release", ".github", "workflows") if HARNESS
             else os.path.join(ROOT, ".github", "workflows"))

# A name here bans the module and everything under it ("logging.handlers" bans that module and not ``logging``). The claims
# check of 2026-11-12 found the standard library's other ways to a socket missing: the logging handlers and listener, the
# C modules, a web server, a selector loop, the documentation server, and the third-party clients that no shipped module
# may use either.
NETWORK_MODULES = {"socket", "_socket", "ssl", "_ssl", "urllib", "http", "ftplib", "smtplib", "smtpd", "telnetlib", "poplib",
                   "imaplib", "nntplib", "xmlrpc", "socketserver", "webbrowser", "asyncio", "asyncio.streams", "asyncore",
                   "asynchat", "multiprocessing", "logging.handlers", "logging.config", "wsgiref", "selectors", "pydoc",
                   "requests", "httpx", "aiohttp", "urllib3",
                   # the last review: an XML parser given an address fetches it, the installers fetch packages, and more clients
                   "xml.sax", "xml.dom.pulldom", "xml.dom.minidom", "ensurepip", "pip", "paramiko", "websockets", "websocket", "boto3",
                   "botocore", "grpc", "zmq", "pycurl", "tornado", "twisted"}
# Modules that run code from a string or a file, or reach the interpreter's own builtins by name (the last review: with
# ``builtins.exec`` the ban on ``exec`` could be walked round).
CODE_MODULES = {"pickle", "marshal", "shelve", "ctypes", "pty", "builtins", "runpy", "code", "codeop", "pdb", "bdb", "timeit",
                "cProfile", "profile", "trace", "doctest", "imp", "zipimport"}
# The one named exception to "no module reaches the network" (2026-11-11): the transport of ``brevio-core ai``, for the
# two packages it needs and no more. Every other shipped module, the rest of ``ai`` included, stays under the ban.


def _rel(pkg, name):
    """A module path, joined at run time: this file ships in every set, and a path to a module of a later set would read
    as a leak (an internal path) in an earlier one."""
    return "/".join(("brevio", *pkg.split("/"), name + ".py"))


NETWORK_EXCEPTIONS = {_rel("pipeline", "core_ai_net"): frozenset({"urllib", "http"})}
# The one named exception to "starts only git" (2026-11-12, improvement plan): the command connector, which starts the program
# the user named. It is held to the same rules as every module otherwise: no ``os.system`` and no network import; its own test refuses a shell.
PROCESS_EXCEPTIONS = frozenset({_rel("pipeline", "core_ai_exec")})
# The optional bring-your-own-key plugin is a separate package that depends on the core; the core never imports it, by any
# route, so the core stays model-free whether or not the plugin is installed (ADOPTION.md 0.36, 2026-11-05).
ASSIST_PACKAGE = "brevio_core_assist"
# The connection helpers of asyncio and socket, banned as names too, in case a module reaches them another way.
NETWORK_NAMES = {"open_connection", "open_unix_connection", "start_server", "start_unix_server", "create_connection",
                 "create_server", "create_datagram_endpoint", "sock_connect", "urlopen"}
BANNED_BUILTINS = {"eval", "exec", "compile", "__import__"}
BANNED_OS = re.compile(r"^(system|popen|fork|forkpty|exec\w*|spawn\w*|posix_spawn\w*|startfile)$")
SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
GIT_SUBCOMMANDS = {"check-ignore", "diff", "ls-files", "ls-tree", "rev-parse", "show"}      # read-only, as SECURITY-REVIEW.md lists them
PRIVILEGED_EVENTS = ("pull_request" + "_target", "workflow" + "_run")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
PLANTED = "// @ts-ignore\nconst n: number = 'x';\n"
_NO_NET = (
    "import socket, sys\n"
    "def _refuse(*a, **k):\n"
    "    sys.stderr.write('NETWORK-USED\\n')\n"
    "    raise OSError('the network is off in this test')\n"
    "class _Socket:\n"
    "    def __init__(self, *a, **k):\n"
    "        _refuse()\n"
    "socket.socket = _Socket\n"
    "socket.create_connection = socket.getaddrinfo = socket.socketpair = socket.fromfd = _refuse\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from brevio.pipeline import core_cli\n"
    "raise SystemExit(core_cli.main(sys.argv[2:]))\n")


def setUpModule():
    global TREE
    if HARNESS:
        out = os.path.join(_EXPORT.name, "core")
        # The newest set, which holds every earlier one (tests/tools/test_release_core.py keeps this in step).
        subprocess.run([sys.executable, EXPORTER, "--out", out, "--set", "0.7"], check=True, capture_output=True, timeout=120)
        TREE = out


def tearDownModule():
    _EXPORT.cleanup()


def shipped_modules():
    """``(path relative to the tree, parsed module)`` for every Python file of the package (the tests are not shipped)."""
    out = []
    for dirpath, dirs, names in os.walk(os.path.join(TREE, "brevio")):
        dirs.sort()
        for n in sorted(names):
            if n.endswith(".py"):
                path = os.path.join(dirpath, n)
                with open(path, encoding="utf-8") as fh:
                    out.append((os.path.relpath(path, TREE).replace(os.sep, "/"), ast.parse(fh.read(), path)))
    return out


def network_hits(names):
    """The entries of ``NETWORK_MODULES`` that the module names in ``names`` are, or are inside of."""
    return {m for n in names for m in NETWORK_MODULES if n == m or n.startswith(m + ".")}


def _aliases(tree):
    """What each name an import binds stands for: ``import a.b as c`` gives ``c -> a.b``, ``from a import b as c`` gives
    ``c -> a.b`` and ``import a.b`` gives ``a -> a``."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _assignments(body):
    """Names bound among ``body`` by ``x = ...`` or ``x: T = ...``."""
    out = {}
    for node in body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for t in targets:
            if isinstance(t, ast.Name) and node.value is not None:
                out.setdefault(t.id, []).append(node.value)
    return out


class _Module:
    """One parsed module, with what the checks need: parents, module-level names, and the names of every module."""

    def __init__(self, rel, tree, everywhere):
        self.rel, self.tree, self.everywhere = rel, tree, everywhere
        self.parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        self.top = _assignments(tree.body)
        self.aliases = _aliases(tree)

    def resolved(self, node):
        """The dotted name of ``node`` with an import alias at its head put back (``sp.run`` is ``subprocess.run``)."""
        head, _, rest = _dotted(node).partition(".")
        full = self.aliases.get(head, head)
        return f"{full}.{rest}" if rest else full

    def function_of(self, node):
        while node in self.parent:
            node = self.parent[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        return None

    def values_of(self, name, at):
        fn = self.function_of(at)
        local = _assignments(ast.walk(fn)) if fn else {}
        return local.get(name) or self.top.get(name) or self.everywhere.get(name, [])

    def flatten(self, node, at, depth=0):
        """The items of an argv expression: a string for a constant, the node itself for a part only known at run time."""
        if depth > 8:
            return [node]
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [item for elt in node.elts for item in self.flatten(elt, at, depth + 1)]
        if isinstance(node, ast.Starred):
            inner = self.flatten(node.value, at, depth + 1)
            return inner if inner != [node.value] else [node]
        name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
        values = self.values_of(name, at) if name else []
        if len(values) == 1:
            return self.flatten(values[0], at, depth + 1)
        return [node]


def _modules():
    parsed = shipped_modules()
    everywhere = {}
    for _rel, tree in parsed:     # a name imported from another module (``GIT``, ``gitsafe.GIT``) resolves to its one definition
        for name, values in _assignments(tree.body).items():
            everywhere.setdefault(name, []).extend(values)
    return [_Module(rel, tree, everywhere) for rel, tree in parsed]


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


PACKAGE_ROOTS = {"top", "__name__.split('.')[0]"}     # how a module names its own top package at run time


def _in_package_import(call, mod=None):
    """``import_module(f"{top}.x.y")`` or ``find_spec(...)`` of a module of this package, named at run time (absent
    from a smaller set). The name may sit in a local variable first."""
    if len(call.args) != 1:
        return False
    arg = call.args[0]
    if isinstance(arg, ast.Name) and mod is not None:
        values = mod.values_of(arg.id, call)
        arg = values[0] if len(values) == 1 else arg
    if not isinstance(arg, ast.JoinedStr):
        return False
    parts = arg.values
    return (len(parts) >= 2 and isinstance(parts[0], ast.FormattedValue) and ast.unparse(parts[0].value) in PACKAGE_ROOTS
            and isinstance(parts[1], ast.Constant) and str(parts[1].value).startswith("."))


def _importlib_problem(mod, node):
    """Why this use of ``importlib`` is not allowed, or ``None``. Allowed: ``import_module`` and ``util.find_spec`` of a
    module of this package, and ``metadata`` (it reads the installed package's metadata and runs no code)."""
    if node.attr == "metadata":
        return None
    target = node
    if node.attr == "util":
        target = mod.parent.get(node)
        if not (isinstance(target, ast.Attribute) and target.attr == "find_spec"):
            return "importlib.util other than find_spec"
    elif node.attr != "import_module":
        return f"importlib.{node.attr}"
    call = mod.parent.get(target)
    if isinstance(call, ast.Call) and call.func is target and _in_package_import(call, mod):
        return None
    return f"importlib.{node.attr} of a module outside this package"


def _subcommand(mod, items, call):
    """The git subcommand of a flattened argv, or ``None``; a forwarded ``*args`` is looked up at each caller."""
    i = 1
    while i < len(items):
        item = items[i]
        if item in ("-c", "-C"):
            i += 2
            continue
        if isinstance(item, str):
            return [item]
        fn = mod.function_of(call)
        if (isinstance(item, ast.Starred) and isinstance(item.value, ast.Name) and fn is not None
                and fn.args.vararg is not None and fn.args.vararg.arg == item.value.id):
            return _forwarded(mod, fn)
        return None
    return None


def _forwarded(mod, fn, depth=0):
    """The argument after the named parameters of each call to ``fn``, a wrapper whose ``*args`` start the git line.

    A caller that passes its own ``*args`` on (a method around the wrapper) is followed to its callers in turn."""
    subs = []
    for node in ast.walk(mod.tree):
        if not isinstance(node, ast.Call):
            continue
        by_name = isinstance(node.func, ast.Name) and node.func.id == fn.name
        by_attr = isinstance(node.func, ast.Attribute) and node.func.attr == fn.name
        if not (by_name or by_attr):
            continue
        pos = len(fn.args.args) - (1 if by_attr and fn.args.args and fn.args.args[0].arg == "self" else 0)
        arg = node.args[pos] if len(node.args) > pos else None
        caller = mod.function_of(node)
        if (isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name) and caller is not None and depth < 4
                and caller.args.vararg is not None and caller.args.vararg.arg == arg.value.id):
            subs += _forwarded(mod, caller, depth + 1) or [None]
        else:
            subs.append(arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None)
    return subs or None


def _import_problems(mod, node):
    """Why this import is not allowed, as plain text. A name taken from a package counts as the module it names
    (``from logging import handlers`` is ``logging.handlers``); an alias of a submodule of ``importlib`` is refused."""
    allowed = NETWORK_EXCEPTIONS.get(mod.rel, frozenset())
    out = []
    if isinstance(node, ast.Import):
        for a in node.names:
            if (network_hits([a.name]) - allowed) or a.name.split(".")[0] in CODE_MODULES:
                out.append(f"imports {a.name}")
            if a.asname and a.name.startswith("importlib.") and a.name != "importlib.metadata":
                out.append(f"aliases {a.name}")
        return out
    name = node.module or ""
    full = [name, *(f"{name}.{a.name}" for a in node.names)] if not node.level else []
    bad = [n for n in full if (network_hits([n]) - allowed) or n.split(".")[0] in CODE_MODULES]
    if bad:
        out.append(f"imports {bad[0]}")
    if name.split(".")[0] in ("subprocess", "importlib", "os"):
        out.append(f"imports names from {name}")
    return out


def problems():
    """Every breach of the no-network and no-dynamic-code rules in the shipped modules, as ``path:line: what``."""
    out = []
    for mod in _modules():
        for node in ast.walk(mod.tree):
            where = f"{mod.rel}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                out += [f"{where}: {why}" for why in _import_problems(mod, node)]
            elif isinstance(node, (ast.Name, ast.Attribute)):
                name = node.id if isinstance(node, ast.Name) else node.attr
                if name in NETWORK_NAMES:
                    out.append(f"{where}: uses {name}")
                if isinstance(node, ast.Attribute) and mod.resolved(node.value) == "os" and BANNED_OS.match(node.attr):
                    out.append(f"{where}: starts a process with os.{node.attr}")
                lib = isinstance(node, ast.Attribute) and mod.resolved(node.value) == "importlib"
                why = _importlib_problem(mod, node) if lib else None
                if why:
                    out.append(f"{where}: {why}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BANNED_BUILTINS:
                out.append(f"{where}: calls {node.func.id}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and mod.resolved(node.func.value) == "subprocess"
                    and node.func.attr in SUBPROCESS_CALLS and mod.rel not in PROCESS_EXCEPTIONS):
                out += [f"{where}: {p}" for p in _git_call_problems(mod, node)]
            out += [f"{where}: {why}" for why in _subprocess_alias_problems(mod, node)]
    return out


def _subprocess_alias_problems(mod, node):
    """``subprocess`` or one of its calls used other than as a direct call (``f = subprocess.run``, ``p = subprocess``, a list of
    them): each would start a process the call check never sees. ``__builtins__`` is the same walk-round for ``exec``."""
    parent = mod.parent.get(node)
    if isinstance(node, ast.Name) and node.id == "__builtins__":
        return ["uses __builtins__"]
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and mod.resolved(node) == "subprocess":
        if not (isinstance(parent, ast.Attribute) and parent.value is node):
            return ["uses subprocess as a value, so a call through another name would not be checked"]
    if (isinstance(node, ast.Attribute) and node.attr in SUBPROCESS_CALLS and mod.resolved(node.value) == "subprocess"
            and not (isinstance(parent, ast.Call) and parent.func is node)):
        return [f"refers to subprocess.{node.attr} without calling it, so a call through another name would not be checked"]
    return []


def _git_call_problems(mod, call):
    if not call.args:
        return ["a subprocess call with no command"]
    items = mod.flatten(call.args[0], call)
    if not items or items[0] != "git":
        return ["a subprocess call that does not start git"]
    subs = _subcommand(mod, items, call)
    if not subs or None in subs:
        return ["a git call whose subcommand is not a constant"]
    return [f"git {s} is not one of the read-only subcommands" for s in subs if s not in GIT_SUBCOMMANDS]


def run_cli(cwd, *args, stdin=None):
    """``brevio-core`` with ``args`` under ``python -I``, with every socket call failing and reporting itself."""
    return subprocess.run([sys.executable, "-I", "-c", _NO_NET, TREE, *args], cwd=cwd, input=stdin, capture_output=True,
                          encoding="utf-8", timeout=60)


def git(d, *args):
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args], check=True,
                   capture_output=True, timeout=60)


def planted_repo(d):
    git(d, "init", "-q")
    git(d, "commit", "-q", "--allow-empty", "-m", "base")
    with open(os.path.join(d, "planted.ts"), "w", encoding="utf-8") as fh:
        fh.write(PLANTED)


class TestNoNetworkInTheCode(unittest.TestCase):
    def test_no_shipped_module_reaches_the_network_runs_dynamic_code_or_starts_anything_but_git(self):
        self.assertEqual(problems(), [])

    def test_the_check_sees_every_module_and_the_git_calls_it_allows(self):
        mods = {m.rel: m for m in _modules()}
        self.assertIn("brevio/pipeline/core_cli.py", mods)
        if os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_up.py")):
            self.assertIn("/".join(("brevio", "pipeline", "core_up.py")), mods)     # checked like every other module
        calls = [n for m in mods.values() for n in ast.walk(m.tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "run" and _dotted(n.func.value) == "subprocess"]
        self.assertTrue(calls, "the scan uses git, so the check must find its calls")

    def test_the_check_catches_a_socket_a_shell_a_foreign_import_and_a_git_write(self):
        bad = ("import socket\nimport subprocess\nimport importlib\nimport os\n"
               "def f(*args):\n    subprocess.run(['curl', 'x'], timeout=1)\n"
               "def g():\n    subprocess.run(['git', 'push'], timeout=1)\n"
               "os.system('ls')\nimportlib.import_module('ssl')\neval('1')\n")
        mod = _Module("x.py", ast.parse(bad), {})
        found = []
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.Call) and _dotted(node.func) == "subprocess.run":
                found += _git_call_problems(mod, node)
        self.assertEqual(sorted(found), ["a subprocess call that does not start git",
                                         "git push is not one of the read-only subcommands"])
        call = next(n for n in ast.walk(mod.tree) if isinstance(n, ast.Call) and _dotted(n.func) == "importlib.import_module")
        self.assertFalse(_in_package_import(call, mod))
        own = ast.parse("import importlib\nname = f\"{__name__.split('.')[0]}.pipeline.x\"\nimportlib.import_module(name)\n")
        own_call = next(n for n in ast.walk(own) if isinstance(n, ast.Call) and _dotted(n.func) == "importlib.import_module")
        self.assertTrue(_in_package_import(own_call, _Module("y.py", own, {})))


# The modules of ``brevio-core ai`` (2026-11-11), and the commands that must never reach them: the gate runs without a
# person, so none of it may import them, by any static route or by name.
AI_NAME = re.compile(r"^(?:[\w.]*\.)?core_ai\w*$")
GATE_ENTRIES = {
    "scan": ("brevio/pipeline/core_cli.py", "brevio/pipeline/code_rules/__init__.py", "brevio/pipeline/dev_rules.py"),
    "the hook runner": (_rel("pipeline", "core_hook"), _rel("operator", "hook_cmd"), _rel("pipeline/gates", "agent_hooks")),
    "the MCP server": (_rel("operator", "mcp_cmd"),),
    "check": (_rel("pipeline/gates", "core_gates"),),
    "init": (_rel("pipeline", "core_init"),),
    "baseline": (_rel("pipeline", "core_baseline"),),
    "heal": (_rel("pipeline", "core_heal"),),
    "patch": (_rel("pipeline", "core_patch"),),
    "up": (_rel("pipeline", "core_up"),),
}
PIPE = "brevio/pipeline"
# Written in two parts: the leak scan reads a path to a harness module that is not in the tree as a leak, and the command
# module and the verifier come with the later items of the 0.7 set.
AI_COMMAND = f"{PIPE}/core_" + "ai.py"
AI_VERIFY = f"{PIPE}/core_ai_" + "verify.py"
PURE_AI = (f"{PIPE}/core_ai_state.py", AI_VERIFY)   # no network, so never the transport
LOADER = "brevio/pipeline/core_sets.py"      # loads ``core_ai`` by name, to register the command; nothing else may name it
# The loader was split in two on 2026-11-13: ``core_sets`` finds the modules and builds the table, ``core_cli`` still asks
# for the table and holds the constants of the modules it loads itself. No other module joins them.
LOADERS = (LOADER, "brevio/pipeline/core_cli.py")


def _static_imports(tree):
    """Every module name a tree imports, and ``package.name`` for each name taken from a package."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names |= {node.module, *(f"{node.module}.{a.name}" for a in node.names)}
    return names


def import_closure(entries, modules=None):
    """The files (relative to the tree) the ``entries`` import, directly or through each other, by a static import."""
    trees = {m.rel: m.tree for m in (_modules() if modules is None else modules)}
    seen, todo = set(), [e for e in entries if e in trees]
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        for name in _static_imports(trees[rel]):
            base = name.replace(".", "/")
            todo += [c for c in (f"{base}.py", f"{base}/__init__.py") if c in trees]
    return seen


def ai_files(rels):
    return sorted(r for r in rels if os.path.basename(r).startswith("core_ai"))


def ai_names_outside_ai(modules=None):
    """``path:line`` of a string that names a ``core_ai*`` module, in a module that is not one of them, other than the
    one module-level constant of the loader (``AI_MODULE = "core_ai"``)."""
    out = []
    for mod in (_modules() if modules is None else modules):
        if os.path.basename(mod.rel).startswith("core_ai"):
            continue
        constants = {id(n.value) for n in mod.tree.body if mod.rel in LOADERS and isinstance(n, ast.Assign)
                     and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id.endswith("_MODULE")}
        for node in ast.walk(mod.tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and AI_NAME.match(node.value)
                    and id(node) not in constants):
                out.append(f"{mod.rel}:{node.lineno}")
    return out


LOADER_NAMES = {"ai_command", "line_commands", "_opt_cmd"}      # what registers or builds the table that holds ``ai``
LOADER_CALLS = LOADER_NAMES | {"set_module"}


def _named(node):
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""


def _module_name_problem(mod, call):
    """Why the module name a ``set_module`` or ``_opt_cmd`` call loads is not known to be a module other than an ai one."""
    last = call.args[-1] if call.args else None
    values = [last] if isinstance(last, ast.Constant) else mod.values_of(_named(last), call) if last is not None else []
    if len(values) != 1 or not (isinstance(values[0], ast.Constant) and isinstance(values[0].value, str)):
        return "loads a module whose name is not a string constant"
    return "loads an ai module" if AI_NAME.match(values[0].value) else ""


def ai_loader_uses_outside_the_loader(modules=None):
    """``path:line what`` of a use of the loader's way to ``ai`` in a module that is neither the loader nor an ai module: a
    call of ``ai_command``, ``line_commands`` or ``_opt_cmd``, a name ``AI_MODULE``, or a ``set_module`` call whose module name
    is not a string constant that names another module. Added with the loader that finds ``ai`` by name and loads it only when
    ``ai`` is run (the claims check, 2026-11-12): nothing else may reach it through the loader."""
    out = []
    for mod in (_modules() if modules is None else modules):
        if mod.rel in LOADERS or os.path.basename(mod.rel).startswith("core_ai"):
            continue
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.ImportFrom):
                out += [f"{mod.rel}:{node.lineno} imports {a.name}" for a in node.names if a.name in LOADER_NAMES | {"AI_MODULE"}]
            elif _named(node) == "AI_MODULE":
                out.append(f"{mod.rel}:{node.lineno} names AI_MODULE")
            elif isinstance(node, ast.Call) and _named(node.func) in LOADER_CALLS:
                why = "calls " + _named(node.func) if _named(node.func) in LOADER_NAMES else _module_name_problem(mod, node)
                if why:
                    out.append(f"{mod.rel}:{node.lineno} {why}")
    return out


class TestTheModelCommandsStayOutOfTheGate(unittest.TestCase):
    def test_the_transport_is_the_one_module_that_may_import_the_network_and_only_urllib_and_http(self):
        def found(rel, src):
            with unittest.mock.patch(f"{__name__}._modules", return_value=[_Module(rel, ast.parse(src), {})]):
                return problems()

        transport = _rel("pipeline", "core_ai_net")
        self.assertEqual(found(transport, "import urllib.request\nimport http.client\n"), [])
        self.assertEqual(len(found(transport, "import socket\nimport ssl\nimport asyncio\n")), 3)
        self.assertTrue(found(transport, "import urllib.request\nurllib.request.urlopen('x')\n")[0].endswith("uses urlopen"))
        for rel in (f"{PIPE}/core_cli.py", AI_COMMAND, f"{PIPE}/core_ai_state.py", f"{PIPE}/core_up.py", "x/core_ai_net.py"):
            self.assertEqual(len(found(rel, "import urllib.request\n")), 1, rel)

    def test_the_transport_is_in_the_tree_that_is_checked_and_nothing_else_there_imports_the_network(self):
        self.assertTrue(os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_ai_net.py")) or not HARNESS)
        users = {}
        for mod in _modules():
            tops = network_hits(_static_imports(mod.tree))
            if tops:
                users[mod.rel] = tops
        for rel, tops in users.items():
            self.assertIn(rel, NETWORK_EXCEPTIONS, rel)
            self.assertLessEqual(tops, NETWORK_EXCEPTIONS[rel], rel)

    def test_scan_the_hook_the_mcp_server_check_init_baseline_heal_patch_and_up_never_import_an_ai_module(self):
        modules = _modules()
        reached = {label: ai_files(import_closure(entries, modules)) for label, entries in GATE_ENTRIES.items()}
        self.assertEqual({k: v for k, v in reached.items() if v}, {})
        # A renamed module must not turn this into a check of nothing: scan is in every set.
        self.assertTrue(import_closure(GATE_ENTRIES["scan"], modules))

    def test_no_module_outside_the_ai_ones_names_one_but_the_loader_that_registers_the_command(self):
        self.assertEqual(ai_names_outside_ai(), [])

    def test_no_module_outside_the_loader_calls_its_command_table_or_names_the_ai_module_or_loads_one_by_a_name_it_cannot_see(self):
        self.assertEqual(ai_loader_uses_outside_the_loader(), [])

    def test_the_loader_check_sees_a_lazy_call_a_name_an_import_and_a_module_loaded_by_a_name_it_cannot_resolve(self):
        def found(rel, src):
            return ai_loader_uses_outside_the_loader([_Module(rel, ast.parse(src), {})])

        hook = f"{PIPE}/core_hook.py"
        lazy = "def _lazy():\n    from brevio.pipeline import core_cli\n    return core_cli.ai_command()\n"
        self.assertEqual(found(hook, lazy), [f"{hook}:3 calls ai_command"])
        self.assertEqual(found(hook, "from brevio.pipeline.core_cli import line_commands\nline_commands()\n"),
                         [f"{hook}:1 imports line_commands", f"{hook}:2 calls line_commands"])
        self.assertEqual(found(hook, "import brevio.pipeline.core_cli as c\nc.set_module('pipeline', c.AI_MODULE)\n"),
                         [f"{hook}:2 loads a module whose name is not a string constant", f"{hook}:2 names AI_MODULE"])
        self.assertEqual(found(hook, "def f(name):\n    return core_cli.set_module('pipeline', name)\n"),
                         [f"{hook}:2 loads a module whose name is not a string constant"])
        self.assertEqual(found(hook, "core_cli._opt_cmd('x', 'pipeline', 'core_ai_net')\n"), [f"{hook}:1 calls _opt_cmd"])
        self.assertEqual(found(hook, "NAME = 'core_ai_net'\ncore_cli.set_module('pipeline', NAME)\n"), [f"{hook}:2 loads an ai module"])
        # What the shipped modules do stays allowed: a constant name, here or taken from the loader, and the modules themselves.
        self.assertEqual(found(hook, "HOOK = 'core_heal'\ncore_cli.set_module('pipeline', HOOK)\ncore_cli.set_module('pipeline.gates', 'core_gates')\n"), [])
        self.assertEqual(found(LOADER, lazy), [])
        self.assertEqual(found(AI_COMMAND, lazy), [])

    def test_the_two_checks_see_an_import_a_name_and_a_package_import(self):
        def mods(**files):
            return [_Module(f"brevio/pipeline/{n}.py", ast.parse(src), {}) for n, src in files.items()]

        top = "brevio"      # a separate name: a dotted path to a module of a later set would read as a leak in an earlier set
        graph = mods(core_cli=f"import {top}.pipeline.core_hook\n", core_hook=f"from {top}.pipeline import core_ai\n",
                     core_ai=f"import {top}.pipeline.core_ai_net\n", core_ai_net="x = 1\n", core_up="from . import y\n")
        self.assertEqual(ai_files(import_closure([f"{PIPE}/core_cli.py"], graph)), [f"{PIPE}/core_ai.py", f"{PIPE}/core_ai_net.py"])
        self.assertEqual(ai_files(import_closure([f"{PIPE}/core_up.py"], graph)), [])
        dotted = f"{top}.pipeline.core_ai"
        named = mods(core_cli=f'AI_MODULE = "core_ai"\nOTHER = "core_ai_state"\ndef f():\n    return "{dotted}"\n',
                     core_hook='NAME = "core_ai"\n')
        self.assertEqual(ai_names_outside_ai(named), [f"{PIPE}/core_cli.py:2", f"{PIPE}/core_cli.py:4", f"{PIPE}/core_hook.py:1"])

    def test_the_two_modules_that_must_stay_pure_reach_neither_the_transport_nor_the_network(self):
        modules = _modules()
        closure = import_closure(PURE_AI, modules)
        self.assertNotIn(f"{PIPE}/core_ai_net.py", closure)
        trees = {m.rel: m.tree for m in modules}
        self.assertEqual([r for r in sorted(closure) if network_hits(_static_imports(trees[r]))], [])


class TestTheNetworkCheckResolvesAliasesAndNamesTakenFromAPackage(unittest.TestCase):
    """A copy of a shipped module with each new form must fail the check (the claims check, 2026-11-12)."""

    NEW = ("logging.handlers", "logging.config", "_socket", "_ssl", "wsgiref.simple_server", "selectors", "pydoc", "xmlrpc.client",
           "imaplib", "poplib", "telnetlib", "nntplib", "asyncio.streams", "smtpd", "asyncore", "requests", "urllib3")

    def copy_with(self, extra, rel=None):
        """The problems of a copy of a shipped module (the loader, which every set ships) with ``extra`` source appended.
        ``rel`` says which module the check believes it is looking at."""
        base = next(m for m in _modules() if m.rel == "brevio/pipeline/core_cli.py")
        with open(os.path.join(TREE, *base.rel.split("/")), encoding="utf-8") as fh:
            src = fh.read()
        copy = _Module(rel or base.rel, ast.parse(src + "\n" + extra), base.everywhere)
        with unittest.mock.patch(f"{__name__}._modules", return_value=[copy]):
            return problems()

    def test_the_copy_with_nothing_added_is_clean_so_what_follows_is_the_added_form(self):
        self.assertEqual(self.copy_with(""), [])

    def test_each_newly_listed_module_fails_as_import_as_from_import_and_as_an_alias(self):
        for name in self.NEW:
            top, _, rest = name.partition(".")
            forms = [f"import {name}", f"import {name} as aliased"]
            if rest:
                forms += [f"from {top} import {rest.split('.')[0]}", f"from {name} import thing", f"from {name} import thing as other"]
            else:
                forms += [f"from {name} import thing"]
            for form in forms:
                self.assertTrue(self.copy_with(form), form)

    def test_a_process_started_through_an_aliased_subprocess_or_os_fails(self):
        self.assertTrue(self.copy_with("import subprocess as sp\nsp.run(['curl', 'x'], timeout=1)"))
        self.assertTrue(self.copy_with("import subprocess as sp\nsp.Popen(['git', 'push'])"))
        self.assertTrue(self.copy_with("import os as o\no.system('ls')"))
        self.assertTrue(self.copy_with("import importlib as il\nil.import_module('ssl')"))
        self.assertTrue(self.copy_with("import importlib.util as iu\niu.spec_from_file_location('a', 'b')"))
        self.assertTrue(self.copy_with("from subprocess import run as r\nr(['curl'])"))

    def test_an_aliased_read_only_git_call_still_passes_and_the_check_only_widened(self):
        self.assertEqual(self.copy_with("import subprocess as sp\nsp.run(['git', 'rev-parse', 'HEAD'], timeout=1)"), [])
        self.assertEqual(self.copy_with("import logging\nlogging.getLogger('x').info('y')"), [])

    def test_the_exception_of_the_transport_still_covers_urllib_and_http_and_nothing_else_of_the_list(self):
        transport = _rel("pipeline", "core_ai_net")
        for allowed in ("import urllib.request", "import http.client", "from urllib import parse", "from http import client"):
            self.assertEqual(self.copy_with(allowed, transport), [], allowed)
        for refused in ("import logging.handlers", "from logging import handlers", "import selectors", "import _socket"):
            self.assertTrue(self.copy_with(refused, transport), refused)

    def test_the_alias_table_reads_the_forms_it_must(self):
        got = _aliases(ast.parse("import a.b as c\nimport d.e\nfrom f import g as h\nfrom i import j\nfrom . import k\n"))
        self.assertEqual(got, {"c": "a.b", "d": "d", "h": "f.g", "j": "i.j"})
        self.assertEqual(sorted(network_hits(["logging.handlers", "logging", "asyncio.tasks", "selectorsx"])), ["asyncio", "logging.handlers"])


def assist_references():
    """``path:line`` of every import of the optional plugin, or string naming it, in the shipped modules."""
    out = []
    for mod in _modules():
        for node in ast.walk(mod.tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom)
                     else [node.value] if isinstance(node, ast.Constant) and isinstance(node.value, str) else [])
            if any(ASSIST_PACKAGE in n.replace("-", "_") for n in names):
                out.append(f"{mod.rel}:{node.lineno}")
    return out


class TestPython310(unittest.TestCase):
    """The package says ``requires-python >= 3.10``: no shipped module may use syntax or names added later.

    The harness runs on a newer Python and its linter prefers ``datetime.UTC`` (3.11), which broke 16 exported tests on
    3.10 before 0.6.0 (2026-11-06). This keeps it from coming back where the gate does not run 3.10.
    """

    NEWER = {("datetime", "UTC"), ("typing", "Self"), ("enum", "StrEnum"), ("asyncio", "TaskGroup"),
             ("asyncio", "timeout"), ("typing", "reveal_type"), ("typing", "assert_never"), ("typing", "LiteralString")}

    def test_every_shipped_module_parses_and_uses_only_names_python_3_10_has(self):
        found = []
        for rel, _tree in shipped_modules():
            with open(os.path.join(TREE, rel), encoding="utf-8") as fh:
                src = fh.read()
            try:
                tree = ast.parse(src, rel, feature_version=(3, 10))
            except SyntaxError as exc:
                found.append(f"{rel}: syntax newer than 3.10 ({exc.msg})")
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                        and (node.value.id, node.attr) in self.NEWER:
                    found.append(f"{rel}:{node.lineno}: {node.value.id}.{node.attr}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found += [f"{rel}:{node.lineno}: from {node.module} import {a.name}" for a in node.names
                              if (node.module, a.name) in self.NEWER]
        self.assertEqual(found, [], "use datetime.timezone.utc and the 3.10 forms instead")


class TestTheOptionalPluginStaysOut(unittest.TestCase):
    def test_no_shipped_module_imports_or_names_the_optional_assist_plugin(self):
        self.assertEqual(assist_references(), [])

    def test_the_check_sees_an_import_and_a_name_given_at_run_time(self):
        bad = ("import brevio_core_assist\nfrom brevio_core_assist.assist import main\n"
               "import importlib\nimportlib.import_module('brevio-core-assist'.replace('-', '_'))\n")
        mod = _Module("x.py", ast.parse(bad), {})
        with unittest.mock.patch(f"{__name__}._modules", return_value=[mod]):
            self.assertEqual(assist_references(), ["x.py:1", "x.py:2", "x.py:4"])


class TestNoNetworkAtRunTime(unittest.TestCase):
    def test_a_diff_scan_finds_the_planted_line_with_every_socket_refused(self):
        with tempfile.TemporaryDirectory() as d:
            planted_repo(d)
            res = run_cli(d, "scan", "--diff", "HEAD")
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("[R9.2]", res.stdout)
        self.assertNotIn("NETWORK-USED", res.stderr)

    def test_a_one_file_scan_that_forbids_ignores_finds_the_planted_line_with_every_socket_refused(self):
        with tempfile.TemporaryDirectory() as d:
            planted_repo(d)
            res = run_cli(d, "scan", "--forbid-ignores", "--file", "planted.ts")
        self.assertEqual((res.returncode, "[R9.2]" in res.stdout), (1, True), res.stderr)
        self.assertNotIn("NETWORK-USED", res.stderr)

    def test_the_agent_hook_reads_the_edited_file_from_its_event_with_every_socket_refused(self):
        if not os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_hook.py")):
            self.skipTest("this set ships no hook runner")
        with tempfile.TemporaryDirectory() as d:
            planted_repo(d)
            event = {"cwd": d, "tool_name": "Write", "tool_input": {"file_path": os.path.join(d, "planted.ts")}}
            res = run_cli(d, "hook", "run", "--event", "post-edit", stdin=json.dumps(event))     # what `hook install` writes
        self.assertEqual((res.returncode, "[R9.2]" in res.stdout), (0, True), res.stderr)
        self.assertNotIn("NETWORK-USED", res.stderr)

    def test_up_lists_what_a_sync_would_take_with_every_socket_refused(self):
        if not os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_up.py")):
            self.skipTest("this set ships no up")
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".brevio"))
            with open(os.path.join(d, ".brevio", "guards.json"), "w", encoding="utf-8") as fh:
                json.dump({"guards": {"g1": {"id": "g1", "patterns": ["x"]}}}, fh)
            res = run_cli(d, "up")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(".brevio/guards.json (", res.stdout)
        self.assertNotIn("NETWORK-USED", res.stderr)

    def test_the_mcp_server_scans_with_every_socket_refused(self):
        if not os.path.isfile(os.path.join(TREE, "brevio", "operator", "mcp_cmd.py")):
            self.skipTest("this set ships no MCP server")
        call = "tools" + "/call"        # joined: the leak scan reads a literal "tools" + "/" as a private folder
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
                {"jsonrpc": "2.0", "id": 2, "method": call, "params": {"name": "scan", "arguments": {"paths": ["planted.ts"]}}}]
        with tempfile.TemporaryDirectory() as d:
            planted_repo(d)
            res = run_cli(d, "mcp", stdin="".join(json.dumps(m) + "\n" for m in msgs))
        replies = [json.loads(ln) for ln in res.stdout.splitlines()]
        self.assertEqual(replies[1]["result"]["structuredContent"]["count"], 1, res.stderr)
        self.assertNotIn("NETWORK-USED", res.stderr)


@unittest.skipUnless(os.path.isfile(ACTION), "a bare export carries no Action")
class TestTheActionAndWorkflows(unittest.TestCase):
    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def shipped(self):
        files = [ACTION]
        if os.path.isdir(WORKFLOWS):
            files += [os.path.join(WORKFLOWS, n) for n in sorted(os.listdir(WORKFLOWS)) if n.endswith((".yml", ".yaml"))]
        return files

    def test_no_privileged_event_appears_in_the_action_or_a_shipped_workflow(self):
        for path in self.shipped():
            for event in PRIVILEGED_EVENTS:
                self.assertNotIn(event, self.read(path), path)

    def test_every_action_the_action_uses_is_pinned_to_a_full_commit_sha(self):
        uses = re.findall(r"^\s*(?:-\s*)?uses:\s*['\"]?([^\s'\"#]+)", self.read(ACTION), re.M)
        self.assertTrue(uses)
        self.assertEqual([u for u in uses if not PINNED.match(u)], [])

    def test_the_action_refuses_events_it_does_not_list(self):
        text = self.read(ACTION)
        self.assertIn("pull_request|merge_group|push)", text)
        self.assertNotIn("secrets.", text)

    def test_the_pin_check_catches_a_tag(self):
        self.assertFalse(PINNED.match("actions/setup-python@v6"))
        self.assertTrue(PINNED.match("github/codeql-action/upload-sarif@" + "0" * 40))


if __name__ == "__main__":
    unittest.main()
