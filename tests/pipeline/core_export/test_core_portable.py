"""What the exported tests share so that they run on Windows as well as on POSIX (2026-11-12, first Windows run).

Standard library only. A child process of a test gets an environment of its own (a PATH that finds git and nothing
else), and on Windows that environment still has to carry the few variables the system needs to start a program at
all: Python 3.10 on Windows stops at start-up without ``SYSTEMROOT``, and git looks for its configuration in the
profile folder. Git for Windows is a folder of programs, so ``git.exe`` cannot be linked or copied away from it.
"""
import ctypes
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

try:
    import resource
except ImportError:          # Windows has no resource module
    resource = None

WINDOWS = os.name == "nt"
_SYSTEM_VARIABLES = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")


# Code that makes standard input, output and error a legacy code page before the program runs, as a pipe on Windows is.
LEGACY_STREAMS = "".join(f"sys.{name}.reconfigure(encoding='cp1252'); " for name in ("stdin", "stdout", "stderr"))


def git_only_path(folder):
    """A PATH value that finds git and nothing else. On POSIX a link in ``folder``; on Windows git's own folder."""
    git = shutil.which("git")
    if WINDOWS:
        return os.path.dirname(git)
    os.symlink(git, os.path.join(folder, "git"))
    return folder


def can_symlink():
    """Whether this process may create a symbolic link (on Windows that takes a privilege or the developer mode)."""
    folder = tempfile.mkdtemp()
    try:
        os.symlink(folder, os.path.join(folder, "link"))
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        remove_tree(folder)


def child_env(path, home=None, **more):
    """The environment of a child process: ``path`` as its PATH, ``home`` as its home folder, and on Windows the
    variables of the system that a program needs to start."""
    env = {"PATH": path}
    if home is not None:
        env["HOME"] = home
        if WINDOWS:
            env["USERPROFILE"] = home
    if WINDOWS:
        env.update({name: value for name, value in os.environ.items() if name.upper() in _SYSTEM_VARIABLES})
    env.update(more)
    return env


def git_env():
    """The environment of a git child that builds a scratch repository: the caller's PATH and none of the user's or the
    system's git configuration, so a signing key or a hook of the machine cannot fail a fixture commit (2026-11-13)."""
    return child_env(os.environ.get("PATH", ""), None, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                     GIT_CONFIG_NOSYSTEM="1")


def remove_tree(path):
    """``shutil.rmtree`` that also removes read-only files, which git writes for its objects and Windows will not delete."""
    for folder, dirs, files in os.walk(path):
        for name in dirs + files:
            os.chmod(os.path.join(folder, name), stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    shutil.rmtree(path)


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    def seconds(self):
        return ((self.high << 32) | self.low) / 1e7


def run_with_cpu(args, **kwargs):
    """``(the finished process, the CPU seconds it and, on POSIX, every child it waited for used)``.

    POSIX asks for the usage of the children this process waited for. Windows has no such total, so it reads the
    times of the one process it started (the interpreter, not the git it calls)."""
    if resource is not None:
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        done = subprocess.run(args, **kwargs)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        return done, (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding="utf-8", cwd=kwargs.get("cwd"), env=kwargs.get("env"))
    out, err = proc.communicate(kwargs.get("input"), timeout=kwargs.get("timeout"))
    created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
    ctypes.windll.kernel32.GetProcessTimes(int(proc._handle), ctypes.byref(created), ctypes.byref(exited),
                                           ctypes.byref(kernel), ctypes.byref(user))
    return subprocess.CompletedProcess(args, proc.returncode, out, err), kernel.seconds() + user.seconds()


class TestTheHelpersThemselves(unittest.TestCase):
    def test_a_child_environment_has_the_path_the_home_and_what_was_added(self):
        env = child_env("a-path", "a-home", EXTRA="1")
        self.assertEqual((env["PATH"], env["HOME"], env["EXTRA"]), ("a-path", "a-home", "1"))
        self.assertEqual("SYSTEMROOT" in env, WINDOWS and "SYSTEMROOT" in os.environ)

    def test_a_path_made_for_git_finds_git_and_nothing_else_beside_it(self):
        with tempfile.TemporaryDirectory() as folder:
            found = shutil.which("git", path=git_only_path(folder))
            self.assertTrue(found and os.path.basename(found).lower().startswith("git"), found)

    def test_a_tree_with_a_read_only_file_is_removed(self):
        folder = tempfile.mkdtemp()
        locked = os.path.join(folder, "objects", "50", "abc")
        os.makedirs(os.path.dirname(locked))
        with open(locked, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.chmod(locked, stat.S_IREAD)
        remove_tree(folder)
        self.assertFalse(os.path.exists(folder))

    def test_the_cpu_of_a_finished_process_is_read_with_its_output(self):
        done, cpu = run_with_cpu([sys.executable, "-I", "-c", "print('hi')"], capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "hi"))
        self.assertGreaterEqual(cpu, 0.0)

    def test_whether_a_link_can_be_made_is_a_plain_yes_or_no(self):
        self.assertIsInstance(can_symlink(), bool)


if __name__ == "__main__":
    unittest.main()
