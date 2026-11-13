"""Tests that ship with the portable rule engines (ROADMAP 8.2 item 2, Stage 1A).

They import only the standard library and the exported modules, so the exporter can copy them beside the code
and they run unchanged in the exported tree: ``python -m unittest discover -s tests/pipeline/core_export``.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

from brevio.growth.geo_audit import rubric
from brevio.pipeline import dev_rules
from brevio.pipeline.code_rules import (
    PR_RULES_122,
    RULE_NOTES,
    details,
    docs,
    is_test_path,
    rule_status,
    scan_added_lines_for_rules,
    scan_lines,
)

# Fakes in real credential formats, joined at run time so this file is not itself a finding of a secret scan: AWS's
# documented example key ID, and 32 hex characters (the shape of an Algolia search key) made from a fixed seed.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
_SCAN = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[2:]))"
AWS_EXAMPLE = "AKIA" + "IOSFODNN7EXAMPLE"
HEX_KEY = "".join("0123456789abcdef"[(i * 7 + 3) % 16] for i in range(32))


class TestCodeRules(unittest.TestCase):
    def test_the_catalogue_holds_129_rules_across_r1_to_r20(self):
        # R2.8 was added before the first release (E35, 2026-11-05) and R20.1 to R20.5, the test-custody rules, after the
        # count was pinned at 123 (2026-11-12, improvement plan 3a), and R10.9 for fetch and axios timeouts on 2026-11-13: this
        # count changes with the catalogue on purpose.
        self.assertEqual(len(PR_RULES_122), 129)
        for n in range(1, 21):
            self.assertTrue(any(k.startswith(f"R{n}.") for k in PR_RULES_122), n)

    def test_a_planted_secret_and_eval_are_flagged_and_a_clean_file_is_not(self):
        found = scan_added_lines_for_rules({
            "src/a.ts": ["const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';", "eval('2 + 2');"],
            "src/clean.ts": ["export const add = (a: number, b: number): number => a + b;"],
        })
        self.assertTrue(any("R13.1" in f for f in found))
        self.assertFalse(any("src/clean.ts" in f for f in found))

    # The false positives the E35 precision run found on open-source repositories (docs/benchmarks/precision.md).

    def test_playwrights_dollar_eval_is_not_javascript_eval(self):
        found = scan_added_lines_for_rules({"tests/page/a.spec.ts": ["expect(await page.$eval('div', e => e.id)).toBe('x');",
                                                                     "await page.$$eval('div', els => els.length);"],
                                            "src/run.ts": ["const v = eval(expr);", "globalThis.eval(code);"]})
        self.assertEqual([f.split(":")[0] for f in found if "[R2.8]" in f], ["src/run.ts", "src/run.ts"])

    def test_sanitized_dangerously_set_inner_html_is_not_flagged(self):
        """R2.8 false positive from remix-run/react-router: escapeHtml at the dangerouslySetInnerHTML sink."""
        found = scan_added_lines_for_rules({
            "packages/react-router/lib/dom/ssr/components.tsx": [
                'dangerouslySetInnerHTML={{ __html: escapeHtml(json) }}',
                'dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(dirty) }}',
                'dangerouslySetInnerHTML={{ __html: sanitizeHtml(content) }}',
                'dangerouslySetInnerHTML={{ __html: contextScript }}',
            ],
            "src/component.tsx": [
                'dangerouslySetInnerHTML={{ __html: svgData }}',
            ],
        })
        hits = [f for f in found if "[R2.8]" in f]
        self.assertEqual(len(hits), 2)
        self.assertIn("packages/react-router/lib/dom/ssr/components.tsx:4:", hits[0])
        self.assertIn("src/component.tsx:1:", hits[1])

    def test_static_eval_probe_in_tests_is_not_flagged(self):
        """R2.8 false positives from playwright and react-router: static literal eval probes in test/integration files."""
        found = scan_added_lines_for_rules({
            "tests/library/capabilities.spec.ts": [
                'window.testStatus = eval("\'FAILED\'");',
            ],
            "integration/split-route-modules-test.ts": [
                'return "clientLoader in main chunk: " + eval("typeof inSplittableMainChunk === \'function\'");',
                'return "clientAction in main chunk: " + eval("typeof inUnsplittableMainChunk === \'function\'");',
            ],
            "tests/dynamic.test.ts": [
                'const v = eval(dynamicCode);',
            ],
            "src/run.ts": [
                'const res = eval("2 + 2");',
            ],
        })
        hits = [f for f in found if "[R2.8]" in f]
        self.assertEqual(len(hits), 2)
        self.assertIn("tests/dynamic.test.ts:1:", hits[0])
        self.assertIn("src/run.ts:1:", hits[1])

    def test_vendored_test_assets_are_not_flagged_for_r28(self):
        """R2.8 false positives from playwright: vendored builds in tests/assets."""
        found = scan_added_lines_for_rules({
            "tests/assets/reading-list/vue_2.6.14.js": [
                "new Function('return 1');",
            ],
            "tests/assets/reading-list/vue_3.1.5.js": [
                "const render = (new Function(code)());",
            ],
            "src/compiler.ts": [
                "const fn = new Function('a', 'return a');",
            ],
            "tests/unit/generateNodeJSON.test.ts": [
                "const parsed = new Function(`\"use strict\";\\n${code}`);",
            ],
        })
        hits = [f for f in found if "[R2.8]" in f]
        self.assertEqual(len(hits), 2)
        self.assertIn("src/compiler.ts:1:", hits[0])
        self.assertIn("tests/unit/generateNodeJSON.test.ts:1:", hits[1])

    def test_a_fixture_key_in_a_test_file_is_not_a_hardcoded_secret(self):
        line = "      accessKeyId: '" + AWS_EXAMPLE + "',"
        found = scan_added_lines_for_rules({"src/provider.test.ts": [line], "src/provider.ts": [line]})
        self.assertEqual([f.split(":")[0] for f in found if "[R13.1]" in f], ["src/provider.ts"])

    def test_a_public_search_key_or_a_storage_entry_name_is_not_a_hardcoded_secret(self):
        """The R13.1 false positives of the E35 precision run: Algolia search keys and a local-storage entry name."""
        found = scan_added_lines_for_rules({
            "examples/react/algolia/src/api.ts": ["// From Algolia example", "const ALGOLIA_APP_ID = 'latency'",
                                                  "const ALGOLIA_SEARCH_API_KEY = '" + HEX_KEY + "'"],
            "dev-docs/docusaurus.config.js": ["      algolia: {", '        appId: "8FEAOD28DI",',
                                              '        apiKey: "' + HEX_KEY + '",', '        indexName: "docs",'],
            "packages/common/src/constants.ts": ["export const EDITOR_LS_KEYS = {", '  OAI_API_KEY: "excalidraw-oai-api-key",'],
            "lib/react-dom.js": ["var ReactPropTypesSecret = 'SECRET_DO_NOT_PASS_THIS_OR_YOU_WILL_BE_FIRED';"],
            "src/pay.ts": ["const stripeKey = 'pk_live_' + 'x';", "const publishableKey = '" + HEX_KEY + "';",
                           "const apiKey = 'your-api-key-1234567890';", "const apiKey = process.env.API_KEY;"],
        })
        self.assertEqual([f for f in found if "[R13.1]" in f], [])

    def test_a_credential_format_or_a_random_value_under_a_secret_name_is_a_hardcoded_secret(self):
        lines = {
            "src/aws.ts": "const client = new S3({ accessKeyId: '" + AWS_EXAMPLE + "' });",
            "src/gh.ts": "const gh = new Octokit({ auth: '" + "gh" + "p_" + "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5" + "' });",
            "src/key.ts": "const pem = '-----BEGIN " + "RSA PRIVATE KEY-----';",
            "src/jwt.ts": "const sessionToken = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.example-signature';",
            "src/hex.ts": "const apiKey: string = '" + HEX_KEY + "';",
            "src/conf.py": "CLIENT_SECRET = '" + HEX_KEY + "'",
        }
        found = scan_added_lines_for_rules({path: [line] for path, line in lines.items()})
        self.assertEqual(sorted(f.split(":")[0] for f in found if "[R13.1]" in f), sorted(lines))

    def test_a_comment_that_names_img_or_as_any_or_eval_is_not_code(self):
        found = scan_added_lines_for_rules({"src/App.tsx": [
            "  /** updates image cache for images that fail during <img> element creation */",
            " * The zero-size `<img>` the reconciler parks outside a block",
            "  // we cast the value as any because the type is wrong upstream",
            "// Note: this file should be loadable with eval() into a worker.",
            "  } catch {} // not a comment line: still flagged",
            "  // @ts-ignore",
        ]})
        self.assertEqual(sorted(f.split(" ")[1] for f in found), ["[R5.4]", "[R9.2]"])

    def test_selective_comment_and_string_masking(self):
        found = scan_added_lines_for_rules({
            "src/safe.ts": [
                'const x = 1; // avoid as any',
                'const msg = "never call eval(x)";',
                'const warning = `do not use ${name} as any`;',
            ],
            "src/real_violations.ts": [
                'const x = (y as any);',
                'eval(userInput);',
            ],
        })
        self.assertEqual(sorted(f.split(":")[0] for f in found), ["src/real_violations.ts", "src/real_violations.ts"])
        self.assertEqual(sorted(f.split(" ")[1] for f in found), ["[R2.8]", "[R9.1]"])

    def test_contiguous_multiline_coalescing(self):
        found = scan_added_lines_for_rules({
            "src/Component.tsx": [
                '  <img',
                '    src="/logo.png"',
                '  />',
                '  <div',
                '    onClick={handleClick}',
                '  >',
            ],
            "src/client.py": [
                'resp = requests.get(',
                '    url,',
                '    headers=headers,',
                ')',
            ],
            "src/db.py": [
                'db.execute(',
                '    f"SELECT * FROM users WHERE id = {uid}"',
                ')',
            ],
        })
        self.assertTrue(any("Component.tsx:1: [R18.1]" in f for f in found))
        self.assertTrue(any("Component.tsx:4: [R18.5]" in f for f in found))
        self.assertTrue(any("client.py:1: [R10.1]" in f for f in found))
        self.assertTrue(any("db.py:1: [R2.8]" in f for f in found))

    def test_r172_use_client_with_server_only(self):
        found = scan_added_lines_for_rules({
            "src/bad.tsx": [
                "'use client';",
                "import 'server-only';",
            ],
            "src/good.tsx": [
                "'use client';",
                "import { Button } from './button';",
            ],
        })
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("src/bad.tsx:"))
        self.assertIn("[R17.2]", found[0])

    def test_test_only_components_are_not_checked_for_accessibility(self):
        line = "      return <div onClick={() => setIndex(1)}>{data}</div>"
        img = "  <img src={url} />"
        found = scan_added_lines_for_rules({"test/use-swr-cache.test.tsx": [line, img], "src/Page.tsx": [line, img]})
        self.assertEqual(sorted(f.split(" ")[1] for f in found), ["[R18.1]", "[R18.5]"])
        self.assertTrue(all(f.startswith("src/Page.tsx") for f in found))

    def test_a_div_that_only_stops_click_propagation_is_not_a_control(self):
        found = scan_added_lines_for_rules({"src/Filter.tsx": [
            '    <div className="column-filter" onClick={(e) => e.stopPropagation()}>',
            "    <div onClick={e => e.stopPropagation()}>",
            '    <div className="close" onClick={onClose}>',
        ]})
        self.assertEqual([f.split(":")[1] for f in found if "[R18.5]" in f], ["3"])

    def test_a_typescript_declaration_file_gives_no_finding(self):
        """The R2.8 false positive of the E35 precision run: prose in a doc comment of Playwright's protocol.d.ts,
        on a line that does not start with an asterisk. A declaration holds no code that runs (2026-11-06)."""
        comment = ["      replMode?: boolean;", "      /**",
                   "       * The Content Security Policy (CSP) for the target might block 'unsafe-eval'",
                   "which includes eval(), Function(), setTimeout() and setInterval()",
                   "when called with non-callable arguments. This flag bypasses CSP for this", "       */"]
        found = scan_added_lines_for_rules({
            "packages/playwright-core/types/protocol.d.ts": comment,
            "utils/overrides.d.ts": ["// @ts-ignore this will be any if zod is not installed"],
            "types/index.d.mts": ["const apiKey = '" + AWS_EXAMPLE + "';"],
            "types/index.d.cts": ["const apiKey = '" + AWS_EXAMPLE + "';"],
            "src/protocol.ts": comment,
        })
        self.assertEqual([f.split(":")[0] for f in found], ["src/protocol.ts"])

    def test_promoted_rules_r55_r101_r162_and_r182_flag_bad_patterns_and_pass_clean_code(self):
        found = scan_added_lines_for_rules({
            "src/runner.py": [
                "subprocess.run(cmd, shell=True)",
                'os.system(f"rm -rf {target}")',
                'subprocess.run(["git", "status"], timeout=30, check=True)',
            ],
            "tests/test_runner.py": [
                "subprocess.run(cmd, shell=True)",
            ],
            "src/client.py": [
                "res = requests.get(url)",
                "res = requests.post(url, json=payload, timeout=10)",
            ],
            "package.json": [
                '  "version": "*",',
                '  "private": "*",',
                '  "left-pad": "*",',
                '  "lodash": "latest",',
                '  "zod": "3.22.4"',
            ],
            "src/Link.tsx": [
                '<a href="/docs" target="_blank">Docs</a>',
                '<a href="/docs" target="_blank" rel="noopener noreferrer">Docs</a>',
            ],
        })
        self.assertEqual([f.split(":")[1] for f in found if "[R5.5]" in f], ["1", "2"])
        self.assertEqual([f.split(":")[1] for f in found if "[R10.1]" in f], ["1"])
        self.assertEqual([f.split(":")[1] for f in found if "[R16.2]" in f], ["3", "4"])
        self.assertEqual([f.split(":")[1] for f in found if "[R18.2]" in f], ["1"])

    def test_r28_flags_raw_sql_interpolation_and_bidi_control_chars_and_r131_flags_client_env_secrets(self):
        bidi = "const isAdmin = false; // \u202e } \u2066if (isAdmin)\u2069"
        found = scan_added_lines_for_rules({
            "src/db.py": [
                'cursor.execute(f"SELECT * FROM users WHERE id = {uid}")',
                'cursor.execute("SELECT * FROM users WHERE id = %s", (uid,))',
            ],
            "src/repo.ts": [
                "await prisma.$queryRawUnsafe(`SELECT * FROM orders WHERE id = ${orderId}`);",
                bidi,
            ],
            "src/env.ts": [
                "const key = process.env.NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY;",
                "const db = import.meta.env.VITE_DATABASE_URL;",
                "const pub = process.env.NEXT_PUBLIC_APP_URL;",
            ],
        })
        r28 = [f"{f.split(':')[0]}:{f.split(':')[1]}" for f in found if "[R2.8]" in f]
        r131 = [f"{f.split(':')[0]}:{f.split(':')[1]}" for f in found if "[R13.1]" in f]
        self.assertEqual(r28, ["src/db.py:1", "src/repo.ts:1", "src/repo.ts:2"])
        self.assertEqual(r131, ["src/env.ts:1", "src/env.ts:2"])


class TestDevRules(unittest.TestCase):
    def test_the_catalogue_holds_46_rules(self):
        self.assertEqual(len(dev_rules.CATALOG_RULES), 46)

    def test_a_scan_of_a_real_checkout_flags_an_added_as_any_cast(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*a):
                subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *a],
                               check=True, capture_output=True)
            git("init", "-q", "-b", "main")
            os.makedirs(os.path.join(d, "src"))
            path = os.path.join(d, "src", "a.ts")
            with open(path, "w") as f:
                f.write("export const a = 1;\n")
            git("add", ".")
            git("commit", "-q", "-m", "x")
            with open(path, "w") as f:
                f.write("export const a = 1;\nexport const b = (x as any).y;\n")
            self.assertTrue(dev_rules.scan_added_lines(d, ["src/a.ts"]))


def rules_of(found, rule):
    """The ``path:line`` of each finding of ``rule`` in ``found``."""
    return [":".join(f.split(":")[:2]) for f in found if f"[{rule}]" in f]


class TestPathsThatOnlyContainTheLettersTest(unittest.TestCase):
    """D1 (2026-11-13): ``"test" in path`` read latest.py, contest_utils.py and attestation/ as test files and skipped the
    secret, shell, requests and eval checks there. A test path is one whose folder or file name has the word at its
    start or end."""

    KEY = "const k = '" + "sk_live_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3" + "';"

    def test_the_same_secret_is_found_in_every_file_whose_name_only_contains_test(self):
        paths = ["src/contest/x.ts", "src/latest/x.ts", "src/ui/x.ts", "src/attestation/x.ts", "src/testimonials/x.ts",
                 "src/latestUtils.ts", "src/fastest.ts"]
        found = scan_added_lines_for_rules({p: [self.KEY] for p in paths})
        self.assertEqual(sorted(f.split(":")[0] for f in found if "[R13.1]" in f), sorted(paths))

    def test_python_files_with_test_inside_a_word_get_the_shell_and_requests_checks(self):
        lines = ["subprocess.run(cmd, shell=True)", "requests.get(url)"]
        for path in ("app/latest.py", "app/contest_utils.py", "app/server.py"):
            found = scan_added_lines_for_rules({path: lines})
            self.assertEqual(len(rules_of(found, "R5.5")), 1, path)
            self.assertEqual(len(rules_of(found, "R10.1")), 1, path)

    def test_a_file_or_folder_that_says_test_by_a_word_is_still_a_test_path(self):
        for path in ("tests/a.py", "src/__tests__/a.ts", "src/a.test.ts", "src/a.spec.tsx", "pkg/test_a.py", "pkg/a_test.py",
                     "pkg/conftest.py", "e2e/login.ts", "src/UserTest.ts", "src/testUtils.ts", "src/unit-tests/a.ts",
                     "integration/split-route-modules-test.ts", "a\\tests\\b.py", "src/a.test.d.ts", "src/unittest/a.py",
                     "pkg/pytest_helpers.py", "src/testdata/a.py", "vitest.config.ts"):
            self.assertTrue(is_test_path(path), path)
        for path in ("app/latest.py", "app/contest_utils.py", "src/attestation/x.ts", "src/testimonials.tsx", "src/fastest.ts",
                     "src/ui/x.ts", "src/inspect.ts"):
            self.assertFalse(is_test_path(path), path)

    def test_a_secret_in_a_real_test_file_is_still_left_alone(self):
        found = scan_added_lines_for_rules({"src/a.test.ts": [self.KEY], "tests/b.ts": [self.KEY], "pkg/test_c.py": [self.KEY]})
        self.assertEqual(rules_of(found, "R13.1"), [])


class TestMjsAndCjsGetTheScriptRules(unittest.TestCase):
    """D16: ``.mjs`` and ``.cjs`` were scanned and matched no JavaScript rule."""

    def test_eval_is_flagged_in_every_script_extension(self):
        line = "export const r = (s) => eval(s);"
        paths = ["m.ts", "m.js", "m.mjs", "m.cjs", "m.mts", "m.cts"]
        found = scan_added_lines_for_rules({p: [line] for p in paths})
        self.assertEqual(sorted(f.split(":")[0] for f in found if "[R2.8]" in f), sorted(paths))

    def test_ts_ignore_and_empty_catch_are_flagged_in_a_module_file(self):
        found = scan_added_lines_for_rules({"m.mjs": ["// @ts-ignore", "try { f(); } catch (e) {}"]})
        self.assertEqual(len(rules_of(found, "R9.2")), 1)
        self.assertEqual(len(rules_of(found, "R5.4")), 1)


class TestBareExceptThatReRaises(unittest.TestCase):
    """D30: a bare ``except:`` was flagged whatever its handler did."""

    @staticmethod
    def scan(*lines, path="a.py"):
        return rules_of(scan_added_lines_for_rules({path: list(lines)}), "R5.4")

    def test_a_bare_except_that_cleans_up_and_re_raises_is_not_flagged(self):
        self.assertEqual(self.scan("try:", "    g()", "except:", "    cleanup()", "    raise"), [])
        self.assertEqual(self.scan("try:", "    g()", "except:  # noqa", "    # why", "    cleanup()  # x", "    raise  # again"), [])
        self.assertEqual(self.scan("try: g()", "except: cleanup(); raise"), [])

    def test_a_bare_except_that_can_swallow_is_still_flagged(self):
        self.assertEqual(self.scan("try:", "    g()", "except:", "    log()"), ["a.py:3"])
        self.assertEqual(self.scan("try:", "    g()", "except:", "    if x:", "        raise", "    return None"), ["a.py:3"])
        self.assertEqual(self.scan("try:", "    g()", "except:", "    raise RuntimeError('x')"), ["a.py:3"])
        self.assertEqual(self.scan("try:", "    g()", "except:", "    pass"), ["a.py:3"])
        self.assertEqual(self.scan("def f():", "    try:", "        g()", "    except:", "        log()", "    raise"), ["a.py:4"])

    def test_the_raise_after_the_handler_and_one_in_a_nested_block_do_not_count(self):
        self.assertEqual(self.scan("try:", "    g()", "except:", "    try:", "        h()", "    finally:", "        raise"), ["a.py:3"])

    def test_a_handler_cut_off_by_a_gap_in_the_added_lines_is_still_flagged(self):
        found = scan_lines("a.py", [(10, "except:"), (12, "    raise")])
        self.assertEqual(rules_of(found, "R5.4"), ["a.py:10"])


class TestPythonSwallowedAndEvalChecks(unittest.TestCase):
    """I2: the README named eval and dropped exceptions with no language, and Python got neither."""

    @staticmethod
    def scan(rule, *lines, path="src/app.py"):
        return rules_of(scan_added_lines_for_rules({path: list(lines)}), rule)

    def test_eval_and_exec_on_a_non_literal_are_flagged(self):
        for line in ("x = eval(input())", "exec(code, ns)", "y = eval(f'{a}+1')", "z = eval('a' + b)", "eval(compile(src, 'f', 'eval'))",
                     "v = eval('%s' % name)"):
            self.assertEqual(self.scan("R2.8", line), ["src/app.py:1"], line)

    def test_a_method_literal_eval_a_literal_and_a_comment_are_not_flagged(self):
        for line in ("model.eval()", "df.eval('a + b')", "ast.literal_eval(text)", "y = eval('1 + 1')", "exec('import os', ns)",
                     "def eval(self, x):", "async def exec(self):", "# eval(x)", "print('do not eval(x)')", "e = evaluate(x)",
                     "cursor.execute(sql)", "ev = eval()"):
            self.assertEqual(self.scan("R2.8", line), [], line)

    def test_a_python_test_file_may_eval_a_literal_but_not_a_variable(self):
        self.assertEqual(self.scan("R2.8", "eval('1')", "eval(expr)", path="tests/test_a.py"), ["tests/test_a.py:2"])

    def test_an_except_exception_handler_that_only_passes_is_flagged(self):
        self.assertEqual(self.scan("R5.4", "try:", "    g()", "except Exception:", "    pass"), ["src/app.py:3"])
        self.assertEqual(self.scan("R5.4", "try:", "    g()", "except (KeyError, Exception) as e:", "    # later", "    ..."), ["src/app.py:3"])
        self.assertEqual(self.scan("R5.4", "try: g()", "except Exception: pass  # ignore"), ["src/app.py:2"])

    def test_the_fixer_output_for_a_bare_except_is_not_flagged_again(self):
        """``scan --fix`` turns ``except:`` into ``except BaseException:``; the fixed file must scan clean."""
        self.assertEqual(self.scan("R5.4", "try:", "    g()", "except BaseException:", "    pass"), [])

    def test_a_handler_for_a_named_narrow_error_that_passes_is_not_flagged(self):
        """Measured on the standard library: hundreds of ``except OSError: pass`` cleanups, all deliberate."""
        self.assertEqual(self.scan("R5.4", "try:", "    g()", "except ValueError:", "    pass"), [])
        self.assertEqual(self.scan("R5.4", "try: g()", "except (OSError, EOFError): pass"), [])
        self.assertEqual(self.scan("R5.4", "try:", "    g()", "except my.MyException:", "    pass"), [])

    def test_a_handler_that_does_something_is_not_flagged(self):
        self.assertEqual(self.scan("R5.4", "try:", "    g()", "except ValueError:", "    log.warning('x')", "    pass"), [])
        self.assertEqual(self.scan("R5.4", "try:", "    g()", "except ValueError as e:", "    raise Wrapped() from e"), [])
        self.assertEqual(self.scan("R5.4", "try:", "    g()", "except OSError:", "    return None"), [])
        self.assertEqual(self.scan("R5.4", "x = {'except': 1}", "y = 'except ValueError:'"), [])


class TestFetchWithoutOptions(unittest.TestCase):
    """I3: R10.1 named fetch and axios and checked Python requests only. The JavaScript half is R10.9, in preview."""

    @staticmethod
    def scan(*lines, path="src/api.ts"):
        return rules_of(scan_added_lines_for_rules({path: list(lines)}), "R10.9")

    def test_a_function_or_method_named_fetch_is_a_definition_not_a_call(self):
        """Found in review: ``function fetch(url) {`` and a class method ``fetch(url) {`` were reported as calls."""
        for line in ("function fetch(url) {", "export async function fetch(url: string) {", "  async fetch(url: string): Promise<R> {",
                     "  fetch(url) {"):
            self.assertEqual(self.scan(line), [], line)

    def test_a_call_with_no_options_argument_is_flagged(self):
        for line in ("const r = await fetch(url);", "const r = await fetch(`/api/${id}`);", "fetch('/a,b').then(r => r.json());",
                     "const r = await axios.get(url);", "axios.delete(`/x/${id}`)", "await axios.post(url, payload);",
                     "const r = await fetch(buildUrl(a, b));"):
            self.assertEqual(self.scan(line), ["src/api.ts:1"], line)

    def test_a_call_that_can_carry_options_is_not_flagged(self):
        for line in ("await fetch(url, { signal });", "await fetch(url, init);", "await axios.get(url, { timeout: 5000 });",
                     "await axios.post(url, body, config);", "fetch(url, { signal: AbortSignal.timeout(5000) })",
                     "await fetch(new Request(url, { signal }));", "api.get(url)", "window.fetch(url)", "this.fetch(url)",
                     "await fetch(url", "fetch(...args)", "const fetchData = () => 1;", "// await fetch(url);",
                     "const s = \"fetch(url)\";"):
            self.assertEqual(self.scan(line), [], line)

    def test_a_test_file_and_python_are_not_read(self):
        self.assertEqual(self.scan("await fetch(url);", path="src/api.test.ts"), [])
        self.assertEqual(self.scan("fetch(url)", path="src/api.py"), [])

    def test_the_rule_is_new_and_preview_so_it_never_fails_a_scan_and_r10_1_stays_stable(self):
        self.assertEqual(rule_status("R10.9"), "preview")
        self.assertEqual(rule_status("R10.1"), "stable")

    def test_r10_1_still_names_python_requests_only_and_makes_no_absolute_claim(self):
        title = PR_RULES_122["R10.1"]
        self.assertIn("Python requests", title)
        self.assertNotIn("fetch", title)
        self.assertNotRegex(title, r"\bevery\b")


class TestClientBundleSecretMessage(unittest.TestCase):
    """D14: a secret-named variable that is only read from the environment was reported as a hardcoded secret."""

    def test_a_secret_named_public_variable_gets_a_message_that_names_the_problem(self):
        found = scan_added_lines_for_rules({"e.ts": ["const k = process.env.NEXT_PUBLIC_API_SECRET;"]})
        self.assertEqual(len(found), 1)
        self.assertIn("[R13.1]", found[0])
        self.assertIn("`NEXT_PUBLIC_API_SECRET` is sent to the browser", found[0])
        self.assertNotIn("Hardcoded", found[0])

    def test_a_real_hardcoded_secret_keeps_its_message_and_a_server_variable_is_not_flagged(self):
        found = scan_added_lines_for_rules({"a.ts": ["const apiKey = '" + AWS_EXAMPLE + "';"],
                                            "b.ts": ["const k = process.env.STRIPE_SECRET_KEY;", "const p = process.env.NEXT_PUBLIC_API_KEY;"]})
        self.assertEqual(len(found), 1)
        self.assertIn("Hardcoded secret pattern", found[0])

    def test_both_on_one_line_report_the_hardcoded_secret_once(self):
        line = "const a = '" + AWS_EXAMPLE + "', b = process.env.VITE_SECRET_X;"
        found = scan_added_lines_for_rules({"a.ts": [line]})
        self.assertEqual(len(found), 1)
        self.assertIn("Hardcoded", found[0])


class TestWildcardDependencyInAManifest(unittest.TestCase):
    """D13: R16.2 missed a minified package.json, and a folder scan read no package.json at all."""

    def test_wildcards_on_one_line_are_flagged_and_a_version_field_is_not(self):
        found = scan_added_lines_for_rules({"package.json": ['{"name":"x","version":"*","dependencies":{"a":"*","b":"latest"}}']})
        self.assertEqual(rules_of(found, "R16.2"), ["package.json:1"])
        found = scan_added_lines_for_rules({"package.json": ['{"name":"x","version":"*","dependencies":{"a":"^1.2.0"}}', '  "c": "*",']})
        self.assertEqual(rules_of(found, "R16.2"), ["package.json:2"])

    def test_a_folder_scan_reads_a_package_json_and_no_other_json(self):
        with tempfile.TemporaryDirectory() as d:
            for rel, text in (("pretty/package.json", '{\n  "dependencies": {\n    "a": "*",\n    "b": "latest"\n  }\n}\n'),
                              ("mini/package.json", '{"dependencies":{"a":"*","b":"latest"}}\n'),
                              ("mini/other.json", '{"dependencies":{"a":"*"}}\n')):
                os.makedirs(os.path.join(d, os.path.dirname(rel)), exist_ok=True)
                with open(os.path.join(d, rel), "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            res = subprocess.run([sys.executable, "-I", "-c", _SCAN, ROOT, "scan", "."], cwd=d, capture_output=True,
                                 encoding="utf-8", timeout=60)
            self.assertIn("pretty/package.json:3", res.stdout)
            self.assertIn("mini/package.json:1", res.stdout)
            self.assertNotIn("other.json", res.stdout)
            self.assertIn("in 2 file(s)", res.stdout + res.stderr)


class TestRuleWordsMatchWhatIsChecked(unittest.TestCase):
    """D15 (2026-11-13): a title or page that promised more than its scan checks, or used an absolute word."""

    def test_no_catalogue_line_uses_an_absolute_word(self):
        words = re.compile(r"\b(?:never|every|always|zero|guarantees?|proves?)\b", re.I)
        self.assertEqual([rid for rid, line in PR_RULES_122.items() if words.search(line)], [])

    def test_the_titles_of_the_scanned_rules_name_what_the_scan_checks(self):
        self.assertIn("shell=True", PR_RULES_122["R5.5"])
        self.assertNotIn("lock", PR_RULES_122["R5.5"])
        self.assertIn("waitForTimeout", PR_RULES_122["R6.3"])
        self.assertNotIn("sleep", PR_RULES_122["R6.3"])
        self.assertIn("'latest'", PR_RULES_122["R16.2"])
        self.assertNotIn("range", PR_RULES_122["R16.2"])
        self.assertIn("target=", PR_RULES_122["R18.2"])
        self.assertIn("not scanned", PR_RULES_122["R18.2"])
        self.assertTrue(PR_RULES_122["R13.1"].startswith("No hardcoded secrets"))

    def test_a_scoped_rule_page_says_where_it_reads_and_the_scope_names_only_scanned_rules(self):
        self.assertLessEqual(set(details.SCOPE), set(details.scanned_by("code")))
        page = docs.render("R10.1")
        self.assertIn("What it reads: Python only", page)
        self.assertIn("R10.9 covers fetch and axios", page)

    def test_a_rule_with_a_python_check_added_after_the_precision_run_says_it_is_not_measured_there(self):
        for rid in ("R2.8", "R5.4"):
            self.assertIn("Python checks of this rule were added after this run", docs.precision_text(rid), rid)
        self.assertNotIn("Python checks", docs.precision_text("R9.1"))

    def test_the_pages_of_r6_1_and_r6_5_point_to_the_test_custody_ids(self):
        """D34, the pointer line only: the ignore alias is another change."""
        self.assertIn("printed as R6.5 up to 0.9.1, are R20.1 to R20.4", docs.render("R6.5"))
        self.assertIn("printed as R6.1 up to 0.9.1, is R20.5", docs.render("R6.1"))

    def test_the_r13_1_page_explains_the_browser_bundle_case(self):
        self.assertIn("NEXT_PUBLIC_", docs.render("R13.1"))

    def test_every_extension_the_r2_8_scope_and_note_name_is_read_by_a_folder_scan(self):
        """B2 (2026-11-13): the 0.10.0 note said R2.8 reads eval in .mts and .cts files, but the folder scan and the diff scan read
        neither extension (the engine does, the file list does not), so a user of 0.9.1 would have been told of a check that
        never ran. The note and the page may name only extensions that a scan of a folder reports."""
        named = set(re.findall(r"\.[cm]?[jt]sx?\b", details.SCOPE["R2.8"].split(":")[0]))
        named |= set(re.findall(r"\.[cm]?[jt]s\b", RULE_NOTES["R2.8"]))
        self.assertIn(".mjs", named)
        with tempfile.TemporaryDirectory() as d:
            for ext in sorted(named):
                with open(os.path.join(d, "a" + ext), "w", encoding="utf-8", newline="\n") as fh:
                    fh.write("export const r = (s) => eval(s);\n")
            res = subprocess.run([sys.executable, "-I", "-c", _SCAN, ROOT, "scan", "."], cwd=d, capture_output=True,
                                 encoding="utf-8", timeout=60)
        for ext in sorted(named):
            self.assertIn("a" + ext + ":1: [R2.8]", res.stdout, ext)


class TestAeoRubric(unittest.TestCase):
    def test_an_empty_page_scores_below_a_discoverable_one(self):
        empty = rubric.score_page(html="<html></html>")
        rich = rubric.score_page(
            html='<html><head><link rel="canonical" href="/"></head><body><h1>Hi</h1>'
                 '<h2>More</h2><script type="application/ld+json">{"@type":"Organization"}</script></body></html>',
            robots_txt="User-agent: *\nAllow: /\n", llms_txt="# X\n> 120 tokens\n")
        self.assertGreater(rich["score"], empty["score"])
        self.assertLessEqual(rich["score"], 100)

    def test_a_blocked_ai_crawler_is_named(self):
        res = rubric.check_robots_txt("User-agent: GPTBot\nDisallow: /\n")
        self.assertIn("GPTBot", res["blocked_bots"])
