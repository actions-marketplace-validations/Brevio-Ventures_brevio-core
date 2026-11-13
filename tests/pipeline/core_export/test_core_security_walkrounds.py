"""The no-network, no-dynamic-code check of ``test_core_security`` against the walk-rounds the last review of the public text
found (2026-11-12): a process call kept under another name, the interpreter's builtins reached by name, and the modules
that fetch an address or a package. A copy of a shipped module with each form added must fail the check.

Standard library only: the exporter copies this file beside the code (the 0.7 set) and it runs unchanged there. It uses the
check and the exported tree of ``test_core_security`` and nothing else.
"""
from __future__ import annotations

import unittest

from tests.pipeline.core_export.test_core_security import (  # noqa: F401  (the module-level set-up exports the tree to read)
    TestTheNetworkCheckResolvesAliasesAndNamesTakenFromAPackage as Checked,
)
from tests.pipeline.core_export.test_core_security import (
    setUpModule,
    tearDownModule,
)


class TestTheCheckSeesTheWalkRounds(unittest.TestCase):
    copy_with = Checked.copy_with        # a copy of a shipped module with one more form of source added, and what the check says

    def test_a_process_call_kept_under_another_name_and_the_builtins_reached_by_name_fail(self):
        # The call check read ``subprocess.run(...)`` and not ``f = subprocess.run``.
        for form in ("import subprocess\nf = subprocess.run\nf(['curl'])",
                     "import subprocess\ndef g():\n    p = subprocess\n    p.run(['curl'])",
                     "import subprocess as sp\nCALLS = [sp.Popen, sp.call]",
                     "import subprocess\nrun = getattr(subprocess, 'run')",
                     "import builtins\nbuiltins.exec('x')",
                     "from builtins import exec as e",
                     "x = __builtins__",
                     "import runpy\nrunpy.run_path('x')",
                     "import timeit\ntimeit.timeit('x')"):
            self.assertTrue(self.copy_with(form), form)

    def test_the_modules_that_fetch_an_address_or_a_package_are_listed(self):
        for name in ("xml.sax", "xml.dom.minidom", "xml.dom.pulldom", "ensurepip", "pip", "paramiko", "websockets", "boto3", "grpc"):
            self.assertTrue(self.copy_with(f"import {name}"), name)
        self.assertEqual(self.copy_with("import xml.etree.ElementTree\nimport json"), [])      # a parser that opens no address is fine


if __name__ == "__main__":
    unittest.main()
