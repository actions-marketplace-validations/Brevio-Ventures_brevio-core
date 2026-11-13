"""The user-level state of ``brevio-core ai``: the config file, a repository's narrowing, the consent and the ledger
(the 0.7 set, 2026-11-11).

Standard library only: the exporter copies this file beside the code (the 0.7 set) and it runs unchanged there. Every
file is a real one in a temporary folder; nothing is read from or written to the real home folder, and no key is
read, so the tests need no network and no secret.
"""
from __future__ import annotations

import datetime
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

import tests.pipeline.core_export.test_core_portable as portable
from brevio.pipeline import core_ai_state as st
from brevio.pipeline import core_config

HTTPS = "https://" + "llm.example.test/v1/chat"
ROOT_URL = "https://" + "llm.example.test"
LOCAL = "http://" + "localhost:8080/v1"
WORK = st.Connector("work", "endpoint", HTTPS, "chat", "some-model", "WORK_KEY")
POSIX = os.name == "posix"


class Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.cfg_path = os.path.join(self.dir, "cfg", "ai.toml")

    def put(self, rel, text):
        path = os.path.join(self.dir, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def get(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()


class TestWhereTheFileIs(Folder):
    def test_the_named_file_wins_then_an_absolute_xdg_folder_then_the_home_folder(self):
        named = os.path.join(self.dir, "x", "mine.toml")
        self.assertEqual(st.config_path({"BREVIO_AI_CONFIG": named, "XDG_CONFIG_HOME": self.dir}), named)
        self.assertEqual(st.config_path({"XDG_CONFIG_HOME": self.dir}), os.path.join(self.dir, "brevio-core", "ai.toml"))
        with unittest.mock.patch.dict(os.environ, {"HOME": self.dir, "USERPROFILE": self.dir}):
            self.assertEqual(st.config_path({}), os.path.join(self.dir, ".config", "brevio-core", "ai.toml"))
            # XDG says a relative value is invalid: it must not point the file into the current folder.
            self.assertEqual(st.config_path({"XDG_CONFIG_HOME": "relative/dir"}),
                             os.path.join(self.dir, ".config", "brevio-core", "ai.toml"))

    def test_a_file_in_the_repository_is_never_the_one_that_is_read(self):
        self.put("repo/ai.toml", st.render(st.with_connector(st.AiConfig(""), WORK, ("explain",))))
        other = os.path.join(self.dir, "elsewhere")
        before = os.getcwd()
        os.chdir(os.path.join(self.dir, "repo"))
        self.addCleanup(os.chdir, before)
        path = st.config_path({"XDG_CONFIG_HOME": other})
        self.assertTrue(path.startswith(other), path)
        self.assertEqual(st.load(path).connectors, {})

    def test_the_kill_switch_reads_off_and_its_spellings_and_nothing_else(self):
        for value in ("off", "OFF", " off ", "0", "false", "no", "disabled"):
            self.assertTrue(st.killed({"BREVIO_AI": value}), value)
        for value in ("", "on", "1", "yes", "offline"):
            self.assertFalse(st.killed({"BREVIO_AI": value}), value)
        self.assertFalse(st.killed({}))

    def test_the_ledger_sits_beside_the_config_file(self):
        self.assertEqual(st.ledger_path(self.cfg_path), os.path.join(self.dir, "cfg", "ai-ledger.json"))


class TestReadingTheFile(Folder):
    FULL = (f'[connector.work]\nkind = "endpoint"\nurl = "{HTTPS}"\nformat = "chat"\nmodel = "some-model"\n'
            'key_env = "WORK_KEY"\nheader = "X-Version: 2"\n\n[connector.local]\nurl = "' + LOCAL + '"\n'
            'model = "m"\n\n[ai.use]\nexplain = "work"\nfix = "local"\n\n[ai.limits]\nmax_calls_per_day = 5\n'
            'max_input_chars = "3000"\n\n[repos."' + "ab" * 32 + '"]\nallow = ["explain"]\n')

    def check_full(self, cfg):
        self.assertEqual(cfg.connectors["work"], st.Connector("work", "endpoint", HTTPS, "chat", "some-model", "WORK_KEY",
                                                              "X-Version: 2"))
        self.assertEqual(cfg.connectors["local"].key_env, "")
        self.assertEqual(cfg.use, {"explain": "work", "fix": "local"})
        self.assertEqual(cfg.limits, st.Limits(max_calls_per_day=5, max_input_chars=3000))
        self.assertEqual(cfg.repos, {"ab" * 32: ("explain",)})

    def test_it_reads_connectors_uses_limits_and_consent_with_whole_numbers_as_they_are_written(self):
        self.check_full(st.parse_config(self.FULL))

    def test_it_reads_the_same_with_the_reader_of_python_310(self):
        with unittest.mock.patch.object(core_config, "TOMLLIB", None):
            self.check_full(st.parse_config(self.FULL))

    def test_the_two_readers_agree_on_true_and_false(self):
        text = '[ai]\nenabled = false\nallow = ["explain"]\n'
        with unittest.mock.patch.object(core_config, "TOMLLIB", None):
            subset = core_config.parse(text)
        self.assertEqual(subset, {"ai": {"enabled": False, "allow": ["explain"]}})
        if core_config.TOMLLIB is not None:
            self.assertEqual(core_config.parse(text), subset)

    def test_a_missing_file_is_an_empty_config_with_every_command_off(self):
        cfg = st.load(self.cfg_path)
        self.assertEqual((cfg.connectors, cfg.use, cfg.limits), ({}, {}, st.Limits()))
        self.assertIsNone(st.use_for(cfg, "explain"))

    def test_the_defaults_are_modest(self):
        lim = st.Limits()
        self.assertEqual(lim.max_calls_per_day, 20)
        self.assertNotIn("max_calls_per_run", st.Limits._fields, "a run makes one call: there is one kind of limit, per day")
        self.assertTrue(lim.max_input_chars <= 10000 and lim.max_output_tokens <= 1000 and lim.timeout_s <= 120)

    def test_a_command_is_on_only_when_ai_use_names_a_connector_for_it(self):
        cfg = st.parse_config(self.FULL.replace('fix = "local"\n', ""))
        self.assertEqual(st.use_for(cfg, "explain").name, "work")
        self.assertIsNone(st.use_for(cfg, "fix"))

    def test_a_typo_a_wrong_type_or_a_missing_connector_is_an_error_that_names_it(self):
        base = '[connector.w]\nurl = "' + HTTPS + '"\nmodel = "m"\n'
        for text, what in (
                ("[nope]\na = \"b\"\n", "unknown key nope"),
                (base + 'colour = "x"\n', "unknown key colour"),
                (base.replace("[connector.w]", "[connector.w]\nkind = \"cli\""), "kind must be endpoint"),
                (base + 'format = "xml"\n', "format must be chat or messages"),
                (base + 'key_env = "not a variable name"\n', "NAME of an environment variable"),
                (base + 'header = "no colon"\n', "Name: value"),
                (base.replace('model = "m"\n', ""), "model"),
                (base + '[ai.use]\nexplain = "ghost"\n', "no [connector.ghost]"),
                (base + '[ai.use]\ndeploy = "w"\n', "unknown key deploy"),
                (base + '[ai.limits]\nmax_call_per_day = 3\n', "unknown key max_call_per_day"),
                (base + '[ai.limits]\nmax_calls_per_day = 0\n', "from 1 to 1000"),
                (base + '[ai.limits]\ntimeout_s = true\n', "whole number"),
                (base + '[ai.limits]\nmax_input_chars = "lots"\n', "whole number"),
                (base + '[repos."some/project/path"]\nallow = []\n', "sha256"),
                (base + '[repos."' + "a" * 64 + '"]\nallow = ["deploy"]\n', "unknown command deploy"),
                ('[connector.w]\nurl = "x\n', "line 2" if sys.version_info >= (3, 11) else "line 3")):        # 3.10's reader says where it ended
            with self.assertRaises(st.AiConfigError, msg=text) as cm:
                st.parse_config(text, "ai.toml")
            self.assertIn(what, str(cm.exception), text)

    def test_a_file_that_cannot_be_read_is_an_error_not_an_empty_config(self):
        os.makedirs(self.cfg_path)       # a folder where the file should be
        with self.assertRaises(st.AiConfigError):
            st.load(self.cfg_path)


class TestWritingTheFile(Folder):
    def cfg(self):
        cfg = st.with_connector(st.AiConfig(self.cfg_path), WORK, ("explain", "fix"))
        return st.with_consent(cfg, st.repo_key(self.dir), ("explain",))

    def test_what_is_written_reads_back_the_same(self):
        cfg = self.cfg()
        st.save(cfg)
        self.assertEqual(st.load(self.cfg_path), cfg)
        with unittest.mock.patch.object(core_config, "TOMLLIB", None):
            self.assertEqual(st.load(self.cfg_path), cfg)

    @unittest.skipUnless(POSIX, "file modes are a POSIX matter")
    def test_the_file_is_private_to_the_user_and_so_is_its_folder(self):
        st.save(self.cfg())
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.cfg_path)).st_mode), 0o700)
        self.assertFalse(st.loose_permissions(self.cfg_path))
        os.chmod(self.cfg_path, 0o644)
        self.assertTrue(st.loose_permissions(self.cfg_path))
        st.save(self.cfg())
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg_path).st_mode), 0o600, "a rewrite restores the mode")

    def test_the_file_holds_the_name_of_the_key_variable_and_never_the_key_or_a_path(self):
        key = secrets.token_hex(12)
        with unittest.mock.patch.dict(os.environ, {"WORK_KEY": key}):
            st.save(self.cfg())
        text = self.get(self.cfg_path)
        self.assertIn('key_env = "WORK_KEY"', text)
        self.assertNotIn(key, text)
        self.assertNotIn(self.dir, text, "the consent is filed under a hash, not under the repository's path")

    def test_a_failed_write_leaves_the_old_file_and_no_temporary_file(self):
        st.save(self.cfg())
        before = self.get(self.cfg_path)
        with unittest.mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                st.save(self.cfg()._replace(use={}))
        self.assertEqual(self.get(self.cfg_path), before)
        self.assertEqual(os.listdir(os.path.dirname(self.cfg_path)), ["ai.toml"])

    def test_a_connector_of_the_wrong_shape_or_a_command_that_does_not_exist_is_refused_before_writing(self):
        empty = st.AiConfig(self.cfg_path)
        for bad, commands in ((WORK._replace(name="has space"), ()), (WORK._replace(key_env="sk 123"), ()),
                              (WORK._replace(format="xml"), ()), (WORK, ("deploy",))):
            with self.assertRaises(st.AiConfigError):
                st.with_connector(empty, bad, commands)

    def test_connecting_again_replaces_the_connector_and_keeps_the_other_commands(self):
        cfg = self.cfg()
        again = st.with_connector(cfg, WORK._replace(model="other"), ("explain",))
        self.assertEqual(again.connectors["work"].model, "other")
        self.assertEqual(again.use, {"explain": "work", "fix": "work"})

    def test_a_quote_or_backslash_in_a_value_survives_the_round_trip(self):
        odd = WORK._replace(model='a"b\\c', header='X-Note: say "hi" \\ bye')
        cfg = st.with_connector(st.AiConfig(self.cfg_path), odd, ("explain",))
        st.save(cfg)
        self.assertEqual(st.load(self.cfg_path).connectors["work"], odd)


class TestConsent(Folder):
    def test_the_key_is_a_hash_of_the_real_path_so_a_link_to_the_folder_is_the_same_repository(self):
        key = st.repo_key(self.dir)
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertEqual(st.repo_key(self.dir + os.sep), key)
        other = os.path.join(self.dir, "other")
        os.makedirs(other)
        self.assertNotEqual(st.repo_key(other), key)
        if POSIX:
            link = os.path.join(self.dir, "link")
            os.symlink(other, link)
            self.assertEqual(st.repo_key(link), st.repo_key(other))

    def test_consent_is_per_repository_and_per_command_and_adds_up_without_repeats(self):
        cfg = st.AiConfig(self.cfg_path)
        a, b = st.repo_key(self.dir), "0" * 64
        cfg = st.with_consent(cfg, a, ("explain",))
        self.assertTrue(st.consented(cfg, a, "explain"))
        self.assertFalse(st.consented(cfg, a, "fix"))
        self.assertFalse(st.consented(cfg, b, "explain"))
        cfg = st.with_consent(cfg, a, ("fix", "explain"))
        self.assertEqual(cfg.repos[a], ("explain", "fix"))

    def test_consent_needs_a_repository_key_and_known_commands(self):
        for key, commands in (("/some/path", ("explain",)), ("0" * 64, ("deploy",))):
            with self.assertRaises(st.AiConfigError):
                st.with_consent(st.AiConfig(""), key, commands)


class TestWhatARepositoryMayNarrow(Folder):
    def policy(self, text, name="brevio-core.toml"):
        self.put(name, text)
        return st.repo_policy(self.dir)

    def test_a_repository_with_no_file_narrows_nothing(self):
        self.assertEqual(st.repo_policy(self.dir), st.RepoPolicy(True, None, ()))
        self.assertIsNone(st.RepoPolicy().refusal("fix"))

    def test_enabled_false_turns_every_command_off_and_says_where(self):
        p = self.policy("[ai]\nenabled = false\n")
        for command in st.COMMANDS:
            self.assertIn("enabled = false", p.refusal(command))

    def test_allow_keeps_only_the_commands_it_names(self):
        p = self.policy('[ai]\nallow = ["explain"]\n')
        self.assertIsNone(p.refusal("explain"))
        self.assertIn("allows only: explain", p.refusal("fix"))
        self.assertIn("nothing", self.policy("[ai]\nallow = []\n").refusal("explain"))

    def test_deny_paths_are_globs_relative_to_the_repository(self):
        p = self.policy('[ai]\ndeny_paths = ["secrets/**", "*.cfg"]\n')
        self.assertTrue(p.denies("secrets/a/b.py") and p.denies("x.cfg"))
        self.assertFalse(p.denies("src/a.py"))

    def test_a_url_a_key_a_model_or_any_routing_table_is_refused_as_user_level_only(self):
        for body in ('url = "' + HTTPS + '"', 'model = "m"', 'key_env = "K"', 'key = "abc"', 'format = "chat"',
                     'argv = ["tool"]', "[ai.use]\nexplain = \"w\"", "[ai.limits]\ntimeout_s = 1",
                     'enabled = false\nurl = "' + HTTPS + '"'):
            if sys.version_info < (3, 11) and "= 1" in body:      # 3.10's reader takes no number: it refuses the file, on another ground
                with self.assertRaises(st.AiConfigError, msg=body):
                    self.policy("[ai]\n" + body + "\n")
                continue
            with self.assertRaises(st.RoutingError, msg=body) as cm:
                self.policy("[ai]\n" + body + "\n")
            self.assertIn("routing is user-level only", str(cm.exception))
        self.assertTrue(issubclass(st.RoutingError, st.AiConfigError))

    def test_any_other_key_or_a_wrong_type_is_an_error_too(self):
        for body, what in (('colour = "x"', "unknown key colour"), ('enabled = "no"', "true or false"),
                           ('allow = ["deploy"]', "unknown command"), ('deny_paths = "a"', "list of strings"),
                           ('allow = "explain"', "list of strings")):
            with self.assertRaises(st.AiConfigError, msg=body) as cm:
                self.policy("[ai]\n" + body + "\n")
            self.assertIn(what, str(cm.exception))

    def test_the_policy_a_file_extends_can_only_be_tightened(self):
        self.put("policy/brevio-core.toml", '[ai]\nallow = ["explain", "fix"]\ndeny_paths = ["a/**"]\n')
        p = self.policy('extends = "policy/brevio-core.toml"\n[ai]\nallow = ["fix"]\ndeny_paths = ["b/**"]\n')
        self.assertEqual((p.enabled, p.allow, p.deny_paths), (True, ("fix",), ("a/**", "b/**")))
        self.put("policy/brevio-core.toml", "[ai]\nenabled = false\n")
        self.assertFalse(self.policy('extends = "policy/brevio-core.toml"\n[ai]\nenabled = true\n').enabled)

    def test_a_policy_that_extends_itself_in_a_loop_is_an_error(self):
        self.put("a/brevio-core.toml", 'extends = "../b/brevio-core.toml"\n')
        self.put("b/brevio-core.toml", 'extends = "../a/brevio-core.toml"\n')
        with self.assertRaises(st.AiConfigError):
            self.policy('extends = "a/brevio-core.toml"\n')

    def test_a_file_that_holds_an_ai_table_still_loads_for_the_scan_with_either_reader(self):
        self.put("brevio-core.toml", 'select = ["R9"]\n[ai]\nenabled = false\nallow = ["explain"]\ndeny_paths = ["x/**"]\n')
        for reader in (core_config.TOMLLIB, None):
            with unittest.mock.patch.object(core_config, "TOMLLIB", reader):
                found = core_config.Project(self.dir).for_path("a.ts")
            self.assertEqual(found.select, ("R9",), reader)

    def test_a_broken_policy_file_is_an_error_not_silence(self):
        with self.assertRaises(st.AiConfigError):
            self.policy('[ai\nenabled = false\n')


class TestTheLedger(Folder):
    def setUp(self):
        super().setUp()
        self.ledger = os.path.join(self.dir, "led", "ai-ledger.json")

    def test_a_missing_ledger_is_an_empty_day(self):
        self.assertEqual(st.usage_on(self.ledger), st.Usage())

    def test_calls_and_characters_add_up_by_day_and_tokens_only_when_reported(self):
        st.record_call(self.ledger, 120, "2026-11-11")
        st.record_call(self.ledger, 30, "2026-11-11")
        st.record_tokens(self.ledger, 40, 7, "2026-11-11")
        st.record_call(self.ledger, 5, "2026-11-12")
        self.assertEqual(st.usage_on(self.ledger, "2026-11-11"), st.Usage(2, 150, 40, 7))
        self.assertEqual(st.usage_on(self.ledger, "2026-11-12"), st.Usage(1, 5, 0, 0))
        self.assertEqual(st.usage_on(self.ledger, "2026-11-13"), st.Usage())

    def test_today_is_the_local_calendar_day(self):
        self.assertEqual(st.today(datetime.datetime(2026, 11, 11, 23, 59)), "2026-11-11")
        self.assertEqual(st.today(datetime.datetime(2026, 11, 12, 0, 0)), "2026-11-12")
        st.record_call(self.ledger, 1)
        self.assertEqual(st.usage_on(self.ledger).calls, 1)

    @unittest.skipUnless(POSIX, "file modes are a POSIX matter")
    def test_the_ledger_is_private_and_written_in_one_step(self):
        st.record_call(self.ledger, 1, "2026-11-11")
        self.assertEqual(stat.S_IMODE(os.stat(self.ledger).st_mode), 0o600)
        self.assertEqual(os.listdir(os.path.dirname(self.ledger)), ["ai-ledger.json"])

    def test_only_the_last_month_is_kept(self):
        for n in range(1, 41):
            st.record_call(self.ledger, 1, f"2026-10-{n:02d}" if n <= 31 else f"2026-11-{n - 31:02d}")
        days = json.loads(self.get(self.ledger))["days"]
        self.assertEqual(len(days), st.KEEP_DAYS)
        self.assertNotIn("2026-10-01", days)
        self.assertIn("2026-11-09", days)

    def test_a_ledger_that_cannot_be_read_stops_the_count_instead_of_resetting_it(self):
        for text in ("{not json", "[]", '{"days": []}', '{"days": {"2026-11-11": {"calls": -1}}}',
                     '{"days": {"2026-11-11": {"calls": true}}}', '{"days": {"2026-11-11": 3}}'):
            self.put("led/ai-ledger.json", text)
            with self.assertRaises(st.AiConfigError, msg=text) as cm:
                st.record_call(self.ledger, 1, "2026-11-11")
            self.assertIn("delete it", str(cm.exception))
            self.assertEqual(self.get(self.ledger), text, "a ledger it cannot read is left as it is")

    def test_a_limit_names_itself_when_a_call_would_pass_it(self):
        lim = st.Limits(max_calls_per_day=2, max_input_chars=1000)
        self.assertEqual(st.day_problems(lim, st.Usage(1, 10), 1000), [])
        self.assertIn("max_calls_per_day", st.day_problems(lim, st.Usage(2, 10), 10)[0])
        self.assertIn("max_input_chars", st.day_problems(lim, st.Usage(), 1001)[0])
        self.assertEqual(len(st.day_problems(lim, st.Usage(2), 1001)), 2)


# ---- the claims check of 2026-11-12: what the public text says must be true ---------------------------------------------

EVIL = "https://" + "evil.example.test/x"


def make_key(n=24):
    """A key-shaped value made at run time: no secret is written into the repository."""
    return "sk_live_" + secrets.token_hex(n // 2)


class TestTheSettingsHoldANameNotAKey(Folder):
    def test_a_key_typed_where_the_variable_name_belongs_is_refused_and_never_echoed(self):
        for typed in (make_key(), "ghp_" + secrets.token_hex(16), "AKIA" + secrets.token_hex(8).upper(), "Ab1" + secrets.token_hex(12)):
            c = WORK._replace(key_env=typed)
            found = " ".join(st.connector_problems(c))
            self.assertIn("key_env", found, typed)
            self.assertNotIn(typed, found)
            with self.assertRaises(st.AiConfigError) as cm:
                st.parse_config(st.render(st.AiConfig("", {"work": c})))
            self.assertNotIn(typed, str(cm.exception))

    def test_a_plausible_variable_name_is_accepted_and_a_wrong_shape_is_not(self):
        for name in ("WORK_KEY", "OPENAI_API_KEY", "my_key_2", "_K", "A" * 64):
            self.assertEqual(st.key_env_problem(name), "", name)
        for name in ("1KEY", "MY-KEY", "A" * 65, "KEY NAME", "k$"):
            self.assertIn("NAME", st.key_env_problem(name), name)

    def test_an_address_with_user_info_a_query_string_or_a_fragment_is_refused(self):
        for url in (HTTPS + "?api_key=" + secrets.token_hex(8), HTTPS + "#frag", "https://" + "user:pw" + "@" + "llm.example.test/v1",
                    "https://" + "tok" + "@" + "llm.example.test/v1", "http://" + "localhost:8080/v1?x=1"):
            found = " ".join(st.connector_problems(WORK._replace(url=url)))
            self.assertIn("url must be an address only", found, url)
            self.assertNotIn("api_key", found)
        self.assertEqual(st.connector_problems(WORK._replace(url=LOCAL)), [])

    def test_a_key_in_the_path_of_an_address_or_in_a_path_parameter_is_refused_and_a_model_name_is_not(self):
        # (2026-11-12, claims check) Some services take the key in the path; the file must not hold it there either.
        key = secrets.token_hex(16)
        for url in (f"{ROOT_URL}/v1/{key}/chat", f"{ROOT_URL}/v1/Ab1{key}", f"{ROOT_URL}/v1;k=1",
                    f"{ROOT_URL}/hf_" + secrets.token_hex(6)):
            found = " ".join(st.connector_problems(WORK._replace(url=url)))
            self.assertIn("url", found, url)
            self.assertNotIn(key, found)
        for url in (f"{ROOT_URL}/v1/models/some-model-1.5-pro-latest:generate",
                    f"{ROOT_URL}/model/some.vendor-model-3-5-20241022-v2:0/invoke",
                    f"{ROOT_URL}/deployments/my-chat-model-4o-mini/chat/completions", LOCAL):
            self.assertEqual(st.connector_problems(WORK._replace(url=url)), [], url)

    def test_the_common_key_prefixes_that_use_an_underscore_are_refused_as_a_variable_name(self):
        for prefix in ("hf_", "gsk_", "r8_", "npm_", "shpat_", "whsec_"):
            self.assertIn("looks like a key", st.key_env_problem(prefix + secrets.token_hex(12)), prefix)

    def test_the_header_field_is_a_plain_extra_header_and_a_key_shaped_value_is_refused(self):
        self.assertEqual(st.connector_problems(WORK._replace(header="X-Version: 2")), [])
        typed = make_key(32)
        found = " ".join(st.connector_problems(WORK._replace(header=f"X-Api-Key: {typed}")))
        self.assertIn("looks like a key", found)
        self.assertNotIn(typed, found)


@unittest.skipUnless(POSIX, "owners and modes are a POSIX matter")
class TestTheSettingsAreReadableByYouOnly(Folder):
    def saved(self):
        st.save(st.with_connector(st.AiConfig(self.cfg_path), WORK, ("explain",)))

    def test_a_file_you_wrote_in_your_own_folder_has_no_problem_and_a_missing_one_none_either(self):
        self.assertEqual(st.settings_problems(self.cfg_path), [])
        self.saved()
        self.assertEqual(st.settings_problems(self.cfg_path), [])

    def test_a_file_that_group_or_others_can_read_or_write_is_named(self):
        self.saved()
        for mode in (0o640, 0o604, 0o660, 0o644):
            os.chmod(self.cfg_path, mode)
            self.assertTrue(any("group or others can read or write" in p for p in st.settings_problems(self.cfg_path)), oct(mode))

    def test_a_folder_that_group_or_others_can_write_to_is_named_even_when_the_file_is_private(self):
        self.saved()
        os.chmod(os.path.dirname(self.cfg_path), 0o777)
        found = st.settings_problems(self.cfg_path)
        self.assertTrue(any("can be written to by group or others" in p for p in found), found)
        os.chmod(os.path.dirname(self.cfg_path), 0o770)
        self.assertTrue(st.settings_problems(self.cfg_path))

    def test_a_file_owned_by_someone_else_is_named(self):
        self.saved()
        with unittest.mock.patch("os.getuid", return_value=os.getuid() + 1):
            found = st.settings_problems(self.cfg_path)
        self.assertTrue(any("not owned by you" in p for p in found), found)

    def test_a_link_is_followed_to_the_file_it_leads_to(self):
        self.saved()
        loose = os.path.join(self.dir, "loose")
        os.makedirs(loose, mode=0o700)
        link = os.path.join(loose, "ai.toml")
        os.symlink(self.cfg_path, link)
        os.chmod(self.cfg_path, 0o666)
        self.assertTrue(st.settings_problems(link))

    def test_a_link_planted_at_the_old_temporary_name_is_not_followed_when_the_file_is_written(self):
        victim = self.put("victim.txt", "keep me")
        os.makedirs(os.path.dirname(self.cfg_path))
        os.symlink(victim, f"{self.cfg_path}.{os.getpid()}.tmp")       # the name the writer used to take
        self.saved()
        self.assertEqual(self.get(victim), "keep me")
        self.assertIn("[connector.work]", self.get(self.cfg_path))

    def test_the_temporary_file_is_created_exclusively_so_a_planted_name_is_skipped_never_opened(self):
        victim = self.put("victim.txt", "keep me")
        os.makedirs(os.path.dirname(self.cfg_path))
        os.symlink(victim, f"{self.cfg_path}.{os.getpid()}.aaaa.tmp")
        with unittest.mock.patch.object(st.secrets, "token_hex", side_effect=["aaaa", "bbbb"]):
            self.saved()
        self.assertEqual(self.get(victim), "keep me")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.cfg_path))), ["ai.toml", f"ai.toml.{os.getpid()}.aaaa.tmp"])


def run_git(folder, *args):
    subprocess.run(["git", "-C", folder, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                    *args], check=True, capture_output=True, timeout=60)


class TestConsentMeansThisRepository(Folder):
    def repo(self, name="repo", commits=2, message="first"):
        folder = os.path.join(self.dir, name)
        os.makedirs(folder, exist_ok=True)
        run_git(folder, "init", "-q")
        for n in range(commits):
            run_git(folder, "commit", "-q", "--allow-empty", "-m", message if n == 0 else f"more {n}")
        return folder

    def test_with_no_commit_yet_the_key_is_the_path_alone(self):
        folder = os.path.join(self.dir, "fresh")
        os.makedirs(folder)
        run_git(folder, "init", "-q")
        self.assertEqual(st.repo_identity(folder), "")
        plain = os.path.join(self.dir, "plain")
        os.makedirs(plain)
        self.assertEqual(st.repo_key(plain), st.repo_key(plain, ""))

    def test_the_identity_is_the_root_commit_and_does_not_change_when_the_history_grows(self):
        folder = self.repo(commits=3)
        root = subprocess.run(["git", "-C", folder, "rev-list", "--max-parents=0", "HEAD"], capture_output=True, encoding="utf-8",
                              check=True).stdout.strip()
        self.assertEqual(st.repo_identity(folder), root)
        key = st.repo_key(folder)
        run_git(folder, "commit", "-q", "--allow-empty", "-m", "later")
        self.assertEqual(st.repo_key(folder), key)
        self.assertNotEqual(key, st.repo_key(folder, ""), "the identity is part of the key")

    def test_a_long_history_is_found_in_few_calls(self):
        folder = self.repo(commits=40)
        with unittest.mock.patch.object(st, "_rev", wraps=st._rev) as spy:
            found = st.repo_identity(folder)
        self.assertTrue(found)
        self.assertLess(spy.call_count, 20, "40 commits must not mean 40 calls")

    def test_a_different_repository_placed_at_the_same_path_is_asked_again(self):
        folder = self.repo(message="the first repository")
        key = st.repo_key(folder)
        cfg = st.with_consent(st.AiConfig(self.cfg_path), key, ("explain", "fix"))
        self.assertTrue(st.consented(cfg, st.repo_key(folder), "explain"))
        portable.remove_tree(os.path.join(folder, ".git"))
        run_git(folder, "init", "-q")
        run_git(folder, "commit", "-q", "--allow-empty", "-m", "another repository, same folder")
        self.assertNotEqual(st.repo_key(folder), key)
        self.assertFalse(st.consented(cfg, st.repo_key(folder), "explain"))

    def test_a_clone_at_another_path_is_asked_again_as_before(self):
        folder = self.repo()
        other = os.path.join(self.dir, "clone")
        subprocess.run(["git", "clone", "-q", folder, other], check=True, capture_output=True, timeout=60)
        self.assertNotEqual(st.repo_key(folder), st.repo_key(other))


class TestARepositoryCanOnlyNarrow(Folder):
    def policy(self, text):
        self.put("brevio-core.toml", text)
        return st.repo_policy(self.dir)

    def test_a_top_level_connector_table_a_top_level_url_or_a_model_is_refused_like_the_gate_refuses_it(self):
        for text in ('[connector.evil]\nurl = "' + EVIL + '"\nmodel = "m"\n', 'url = "' + EVIL + '"\n',
                     'model = "m"\n', 'key_env = "K"\n', '[use]\nexplain = "evil"\n'):
            with self.assertRaises(st.AiConfigError, msg=text) as cm:
                self.policy(text)
            self.assertIn("unknown key", str(cm.exception))
            with self.assertRaises(core_config.ConfigError, msg=text):      # the gate says the same of the same file
                core_config.Project(self.dir).for_path("a.ts")

    def test_a_case_variant_of_the_ai_table_and_an_ai_table_inside_overrides_are_refused(self):
        for text in ('[AI]\nenabled = false\n', '[Ai]\nurl = "' + EVIL + '"\n',
                     '[overrides.ai]\nurl = "' + EVIL + '"\n',
                     '[[overrides]]\npaths = ["a/**"]\n[overrides.ai]\nurl = "x"\n'):
            with self.assertRaises(st.AiConfigError, msg=text):
                self.policy(text)

    def test_a_routing_key_in_the_ai_table_is_still_refused_as_routing(self):
        with self.assertRaises(st.RoutingError):
            self.policy('[ai]\nurl = "' + EVIL + '"\n')

    def test_a_file_the_gate_accepts_is_accepted_and_a_number_is_not_quietly_turned_into_a_string(self):
        self.assertEqual(self.policy('select = ["R9"]\nlevel = "warn"\n[ai]\nallow = ["explain"]\n').allow, ("explain",))
        with self.assertRaises(st.AiConfigError):
            self.policy('select = 5\n')


class TestTheLedgerIsLockedAndCountsByLocalDay(Folder):
    def setUp(self):
        super().setUp()
        self.ledger = os.path.join(self.dir, "led", "ai-ledger.json")

    @unittest.skipIf(st._fcntl is None, "this system has no flock")
    def test_a_command_waits_while_another_holds_the_ledger_and_then_counts_after_it(self):
        done = []
        with st.ledger_lock(self.ledger):
            t = threading.Thread(target=lambda: done.append(st.reserve_call(self.ledger, st.Limits(), 10)))
            t.start()
            t.join(0.3)
            self.assertTrue(t.is_alive(), "the second command ran while the first held the lock")
            self.assertEqual(st.usage_on(self.ledger).calls, 0)
        t.join(10)
        self.assertEqual((done, st.usage_on(self.ledger).calls), ([st.Usage(1, 10)], 1))

    @unittest.skipIf(st._fcntl is None, "this system has no flock")
    def test_two_commands_at_once_cannot_both_pass_a_limit_of_one(self):
        lim = st.Limits(max_calls_per_day=1)
        for round_ in range(5):
            ledger = os.path.join(self.dir, f"led{round_}", "ai-ledger.json")
            gate, results = threading.Barrier(8), []

            def run(ledger=ledger, gate=gate, results=results):
                gate.wait()
                try:
                    st.reserve_call(ledger, lim, 10)
                    results.append("sent")
                except st.LimitError:
                    results.append("stopped")

            threads = [threading.Thread(target=run) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            self.assertEqual((results.count("sent"), results.count("stopped")), (1, 7), results)
            self.assertEqual(st.usage_on(ledger).calls, 1)

    def test_a_call_over_the_limit_counts_nothing_and_says_which_limit(self):
        lim = st.Limits(max_calls_per_day=1, max_input_chars=200)
        st.reserve_call(self.ledger, lim, 10, "2026-11-12")
        with self.assertRaises(st.LimitError) as cm:
            st.reserve_call(self.ledger, lim, 10, "2026-11-12")
        self.assertIn("max_calls_per_day", str(cm.exception))
        with self.assertRaises(st.LimitError) as cm:
            st.reserve_call(self.ledger, lim, 201, "2026-11-13")
        self.assertIn("max_input_chars", str(cm.exception))
        self.assertEqual((st.usage_on(self.ledger, "2026-11-12"), st.usage_on(self.ledger, "2026-11-13")), (st.Usage(1, 10), st.Usage()))

    def test_there_is_no_per_run_limit_in_what_is_written_and_an_old_file_that_has_one_still_loads(self):
        self.assertNotIn("per_run", st.render(st.AiConfig("", {"work": WORK})))
        old = '[connector.work]\nurl = "' + HTTPS + '"\nmodel = "m"\nkey_env = "WORK_KEY"\n[ai.limits]\nmax_calls_per_run = 3\nmax_calls_per_day = 7\n'
        cfg = st.parse_config(old)
        self.assertEqual(cfg.limits.max_calls_per_day, 7)
        with self.assertRaises(st.AiConfigError):
            st.parse_config(old.replace("= 3", "= 99"))       # an old key is still checked, then dropped

    @unittest.skipUnless(hasattr(time, "tzset"), "needs the C library's time zone switch")
    def test_the_day_is_the_local_calendar_day_even_for_a_time_that_carries_a_zone(self):
        utc = datetime.timezone(datetime.timedelta(0))      # UTC, spelled so that Python 3.10 reads it too
        old = os.environ.get("TZ")
        self.addCleanup(lambda: (os.environ.__setitem__("TZ", old) if old is not None else os.environ.pop("TZ", None), time.tzset()))
        os.environ["TZ"] = "Etc/GMT-2"      # two hours ahead of UTC
        time.tzset()
        self.assertEqual(st.today(datetime.datetime(2026, 11, 11, 23, 30, tzinfo=utc)), "2026-11-12")
        self.assertEqual(st.today(datetime.datetime(2026, 11, 11, 23, 30)), "2026-11-11", "a time with no zone is already local")


if __name__ == "__main__":
    unittest.main()
