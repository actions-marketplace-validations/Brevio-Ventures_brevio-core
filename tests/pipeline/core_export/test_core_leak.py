"""The generic leak checks hold for the tree this file sits in (ROADMAP E13).

Standard library only, so it runs in the public repository's CI too: no URL outside the project, its GitHub
organisation and documentation sites; no email but a placeholder or the published security address; no path or module
of the private harness that is not in the tree.
"""
import os
import tempfile
import unittest

from brevio.pipeline import leak_scan

# The planted leaks are built from pieces so that this file, which the scan also reads, holds none of them.
HTTPS, HTTP = "https" + "://", "http" + "://"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


HARNESS = os.path.exists(os.path.join(ROOT, "tools", "export_core.py"))


@unittest.skipIf(HARNESS, "the harness checkout is not the public tree; its test runs this file on an export")
class TestTreeIsClean(unittest.TestCase):
    def test_this_tree_has_no_stray_url_email_or_internal_path(self):
        self.assertEqual(leak_scan.scan_tree(ROOT), [])


class TestChecks(unittest.TestCase):
    def test_only_project_organisation_and_documentation_urls_pass(self):
        ok = f"https://brevio.pro/x {HTTPS}GitHub.com/Example-Org/repo https://docs.python.org/3/"
        self.assertEqual(leak_scan.bad_urls(ok, "example-org"), [])
        self.assertEqual(len(leak_scan.bad_urls(f"{HTTPS}github.com/example-org/repo")), 1)   # no organisation named
        bad = leak_scan.bad_urls(f"see {HTTPS}github.com/someone/else and {HTTP}intranet.example.org:8080/admin", "example-org")
        self.assertEqual(len(bad), 2)

    def test_a_lookalike_host_is_not_the_project_host(self):
        lookalikes = [f"{HTTPS}notbrevio.pro/", f"{HTTPS}brevio.pro.evil.example/"]
        self.assertEqual(leak_scan.bad_urls(" ".join(lookalikes)), lookalikes)

    def test_only_placeholder_and_the_security_and_legal_addresses_pass_as_email(self):
        self.assertEqual(leak_scan.bad_emails("a@example.com security@brevio.pro legal@brevio.pro"), [])
        stray = "jane" + "@corp.io"
        self.assertEqual(leak_scan.bad_emails(stray), [stray])
        self.assertEqual(leak_scan.bad_emails(f"Write to {stray}."), [stray])

    def test_a_pinned_version_is_not_an_email(self):
        self.assertEqual(leak_scan.bad_emails("uvx brevio-core@0.4.0 scan, uses: example-org/brevio-core@v0.4.0"), [])

    def test_the_organisation_pages_site_passes_only_for_that_organisation(self):
        pages = f"{HTTPS}example-org.github.io/brevio-core/rules"
        self.assertEqual(leak_scan.bad_urls(pages, "Example-Org"), [])
        self.assertEqual(leak_scan.bad_urls(pages, "other-org"), [pages])
        self.assertEqual(leak_scan.bad_urls(pages), [pages])

    def test_a_module_or_path_that_is_not_in_the_tree_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "brevio", "pipeline"))
            for rel in ("brevio/__init__.py", "brevio/pipeline/__init__.py"):
                with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
                    fh.write('"""Part of the portable brevio rule engines."""\n')
            with open(os.path.join(d, "brevio", "pipeline", "dev_rules.py"), "w", encoding="utf-8") as fh:
                fh.write("X = 1\n")
            private = ["brevio" + ".pipeline.gates", "tools" + "/x.py"]
            text = "brevio.pipeline.dev_rules.X, brevio/pipeline/dev_rules.py, brevio.pro, " + ", ".join(private)
            self.assertEqual(leak_scan.bad_internal_paths(text, d), private)

    def test_the_source_map_may_name_harness_paths_but_not_a_stray_url(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, leak_scan.SOURCE_MAP), "w", encoding="utf-8") as fh:
                fh.write('{"files": {"README.md": "' + "tools" + '/release/README.md"}}\n')
            self.assertEqual(leak_scan.scan_tree(d), [])
            with open(os.path.join(d, leak_scan.SOURCE_MAP), "a", encoding="utf-8") as fh:
                fh.write(f"{HTTPS}example.org/x\n")
            self.assertEqual(leak_scan.scan_tree(d), [f"{leak_scan.SOURCE_MAP}: url: {HTTPS}example.org/x"])
            with open(os.path.join(d, "NOTES.md"), "w", encoding="utf-8") as fh:
                fh.write("tools" + "/release/README.md\n")
            self.assertIn("NOTES.md: internal path: " + "tools" + "/release/README.md", leak_scan.scan_tree(d))


class TestThePrecisionFiles(unittest.TestCase):
    """The precision run names its corpus and quotes third-party code (improvement plan 2a, 2026-11-12)."""

    def write(self, d, rel, text):
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_the_address_of_a_corpus_repository_is_allowed_and_a_look_alike_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, leak_scan.CORPUS_FILE, '{"repos": [{"name": "Acme-Labs/widgets"}, {"name": "bad name"}, 7]}')
            self.assertEqual(leak_scan.corpus_urls(d), ("github.com/acme-labs/widgets",))
            self.write(d, "docs/precision.md", f"{HTTPS}github.com/Acme-Labs/widgets and {HTTPS}github.com/Acme-Labs/widgets/tree/main")
            self.assertEqual(leak_scan.scan_tree(d), [])
            self.write(d, "docs/other.md", f"{HTTPS}github.com/Acme-Labs/widgets-fork and {HTTPS}github.com/someone/else")
            self.assertEqual(leak_scan.scan_tree(d), [f"docs/other.md: url: {HTTPS}github.com/Acme-Labs/widgets-fork",
                                                      f"docs/other.md: url: {HTTPS}github.com/someone/else"])

    def test_a_tree_with_no_corpus_file_or_a_broken_one_allows_no_extra_address(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(leak_scan.corpus_urls(d), ())
            self.write(d, leak_scan.CORPUS_FILE, "not json")
            self.assertEqual(leak_scan.corpus_urls(d), ())
            self.write(d, "docs/precision.md", f"{HTTPS}github.com/Acme-Labs/widgets")
            self.assertEqual(len(leak_scan.scan_tree(d)), 1)

    def test_the_quoted_third_party_sample_is_not_read_for_urls_but_another_file_still_is(self):
        with tempfile.TemporaryDirectory() as d:
            quoted = f'{{"context": ["1: see {HTTPS}example.org/issue/7"]}}\n'
            for rel in leak_scan.QUOTED_DATA:
                self.write(d, rel, quoted)
            self.assertEqual(leak_scan.scan_tree(d), [])
            self.write(d, "docs/notes.jsonl", quoted)
            self.assertEqual(leak_scan.scan_tree(d), [f"docs/notes.jsonl: url: {HTTPS}example.org/issue/7"])


if __name__ == "__main__":
    unittest.main()
