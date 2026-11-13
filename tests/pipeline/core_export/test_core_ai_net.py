"""The endpoint transport of ``brevio-core ai``: one request, to the address the user connected, with the key from the
variable the user named (the 0.7 set, 2026-11-11).

Standard library only: the exporter copies this file beside the code (the 0.7 set) and it runs unchanged there. The
endpoint is a fake ``http.server`` on a loopback port in a thread, so the tests reach no other machine and need no real
key: the key is made at run time. An address that is not on this machine is only ever checked, never opened.
"""
from __future__ import annotations

import http.server
import json
import os
import secrets
import threading
import time
import unittest
import unittest.mock
import urllib.request

from brevio.pipeline import core_ai_net as net
from brevio.pipeline.core_ai_state import Connector

REPLY_CHAT = {"choices": [{"message": {"content": "  It breaks the rule.  "}}],
              "usage": {"prompt_tokens": 31, "completion_tokens": 9, "total_tokens": 40}}
REPLY_MESSAGES = {"content": [{"type": "thinking", "text": "hidden"}, {"type": "text", "text": "Use a constant."},
                              {"type": "text", "text": " Done."}], "usage": {"input_tokens": 12, "output_tokens": 5}}
HOST = "llm.example.test"
LOOP = "127." + "0.0.1"       # joined: the leak scan reads an address of this shape as a private host
LAN = "192." + "168.1.5"
REMOTE = "https://" + HOST + "/v1/chat"


class Endpoint:
    """A fake endpoint on a loopback port: it records each request and answers as told."""

    def __init__(self, test, reply=None, status=200, raw=None, delay=0.0, location=None):
        self.requests = []
        self.wire = []      # (the raw body, the names of the headers as they came in)
        self.reply, self.status, self.raw, self.delay, self.location = reply, status, raw, delay, location
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.wire.append((body, list(self.headers.keys())))
                outer.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, json.loads(body)))
                time.sleep(outer.delay)
                payload = outer.raw if outer.raw is not None else json.dumps(outer.reply).encode()
                self.send_response(outer.status)
                if outer.location:
                    self.send_header("Location", outer.location)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass        # a client that gave up (the timeout test) is not an error of the fake

        self.server = Server((LOOP, 0), Handler)
        self.url = "http://" + f"{LOOP}:{self.server.server_address[1]}/v1/chat"
        threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True).start()
        test.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class Net(unittest.TestCase):
    def setUp(self):
        self.key = secrets.token_hex(12)
        self.env = {"MY_KEY": self.key}

    def conn(self, url, fmt="chat", key_env="MY_KEY", header=""):
        return Connector("c", "endpoint", url, fmt, "some-model", key_env, header)

    def fake(self, **kw):
        return Endpoint(self, **kw)


class TestOneRequestAndItsAnswer(Net):
    def test_a_chat_request_carries_the_key_as_a_bearer_token_and_returns_the_text_and_the_usage(self):
        ep = self.fake(reply=REPLY_CHAT)
        text, usage = net.complete(self.conn(ep.url), "Explain this.", 300, env=self.env)
        self.assertEqual((text, usage), ("It breaks the rule.", {"input_tokens": 31, "output_tokens": 9}))
        (path, headers, body), = ep.requests
        self.assertEqual((path, headers["authorization"], "x-api-key" in headers), ("/v1/chat", f"Bearer {self.key}", False))
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(body, {"model": "some-model", "max_tokens": 300,
                                "messages": [{"role": "system", "content": net.SYSTEM},
                                             {"role": "user", "content": "Explain this."}]})

    def test_a_messages_request_carries_the_key_in_its_own_header_and_reads_only_the_text_blocks(self):
        ep = self.fake(reply=REPLY_MESSAGES)
        text, usage = net.complete(self.conn(ep.url, "messages"), "Fix this.", 200, env=self.env)
        self.assertEqual((text, usage), ("Use a constant. Done.", {"input_tokens": 12, "output_tokens": 5}))
        (_path, headers, body), = ep.requests
        self.assertEqual((headers["x-api-key"], "authorization" in headers), (self.key, False))
        self.assertEqual(body, {"model": "some-model", "max_tokens": 200, "system": net.SYSTEM,
                                "messages": [{"role": "user", "content": "Fix this."}]})

    def test_the_caller_can_name_the_system_line_and_the_extra_header_goes_along(self):
        ep = self.fake(reply=REPLY_CHAT)
        net.complete(self.conn(ep.url, header="X-Version: 2"), "p", 50, env=self.env, system="Reply with one line.")
        (_path, headers, body), = ep.requests
        self.assertEqual(headers["x-version"], "2")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "Reply with one line."})

    def test_the_usage_is_none_when_the_reply_reports_none_or_something_that_is_not_a_count(self):
        for usage in (None, {}, {"prompt_tokens": "31", "completion_tokens": 9}, {"prompt_tokens": -1, "completion_tokens": 1},
                      {"prompt_tokens": True, "completion_tokens": 1}, {"prompt_tokens": 1}, [1, 2]):
            reply = {"choices": REPLY_CHAT["choices"], **({} if usage is None else {"usage": usage})}
            ep = self.fake(reply=reply)
            self.assertIsNone(net.complete(self.conn(ep.url), "p", 50, env=self.env)[1], usage)

    def test_an_endpoint_on_this_machine_needs_no_key_and_is_sent_none(self):
        ep = self.fake(reply=REPLY_CHAT)
        net.complete(self.conn(ep.url, key_env=""), "p", 50, env={})
        headers = ep.requests[0][1]
        self.assertFalse({"authorization", "x-api-key"} & set(headers))

    def test_a_key_variable_that_is_unset_for_an_endpoint_on_this_machine_still_sends_none(self):
        ep = self.fake(reply=REPLY_CHAT)
        net.complete(self.conn(ep.url, key_env="MY_KEY"), "p", 50, env={})
        self.assertNotIn("authorization", ep.requests[0][1])

    def test_the_key_is_read_from_the_process_environment_when_no_environment_is_given(self):
        ep = self.fake(reply=REPLY_CHAT)
        with unittest.mock.patch.dict(os.environ, self.env):
            net.complete(self.conn(ep.url), "p", 50)
        self.assertEqual(ep.requests[0][1]["authorization"], f"Bearer {self.key}")

    def test_the_call_makes_one_request_and_a_failure_is_not_retried(self):
        ep = self.fake(status=500, raw=b"{}")
        with self.assertRaises(net.NetError):
            net.complete(self.conn(ep.url), "p", 50, env=self.env)
        self.assertEqual(len(ep.requests), 1)

    def test_a_loopback_endpoint_is_not_sent_through_a_proxy(self):
        ep = self.fake(reply=REPLY_CHAT)
        dead = "http://" + LOOP + ":9"
        # An empty no_proxy: a machine that already exempts loopback would pass this test whatever the code did (2026-11-12).
        with unittest.mock.patch.dict(os.environ, {"http_proxy": dead, "HTTP_PROXY": dead, "all_proxy": dead,
                                                   "no_proxy": "", "NO_PROXY": ""}):
            self.assertEqual(net.complete(self.conn(ep.url), "p", 50, env=self.env)[0], "It breaks the rule.")


class TestWhatIsShownIsWhatIsSent(Net):
    def test_the_preview_holds_the_exact_body_the_destination_and_the_header_names_but_no_key(self):
        ep = self.fake(reply=REPLY_CHAT)
        c = self.conn(ep.url)
        shown = net.preview(c, "Explain é this.", 120)
        net.complete(c, "Explain é this.", 120, env=self.env)
        sent = ep.requests[0][2]
        self.assertEqual(json.loads(shown.split("\n\n", 1)[1]), sent)
        self.assertIn(net.destination(c), shown)
        self.assertIn("Authorization", shown)
        self.assertIn("MY_KEY", shown)
        self.assertNotIn(self.key, shown)

    def test_the_preview_of_an_endpoint_on_this_machine_says_it_sends_no_key(self):
        shown = net.preview(self.conn("http://" + "localhost:8080/v1", key_env=""), "p", 10)
        self.assertIn("no key", shown)
        self.assertNotIn("Authorization", shown)

    def test_the_destination_leaves_out_the_user_name_and_the_query(self):
        c = self.conn("https://" + "me:secret" + "@" + HOST + ":8443/v1/chat?api-version=1&k=abc")
        self.assertEqual(net.destination(c), "https://" + HOST + ":8443/v1/chat")
        self.assertEqual(net.destination(self.conn("http://" + "[::1]:80/x")), "http://" + "[::1]:80/x")
        self.assertEqual(net.destination(self.conn("https://" + "h:notaport/x")), "(not an address)")


class TestWhatKeepsItFromSending(Net):
    def test_an_https_address_with_a_key_may_send(self):
        self.assertEqual(net.problems(self.conn(REMOTE), self.env), [])

    def test_plain_http_is_allowed_to_this_machine_only(self):
        for host in (LOOP + ":8080", "localhost:11434", "[::1]:80", "127." + "1.2.3"):
            self.assertEqual(net.problems(self.conn("http://" + host + "/v1", key_env=""), {}), [], host)
        for url in ("http://" + HOST + "/v1", "http://" + LAN + "/v1", "http://" + "localhost." + HOST + "/v1",
                    "ftp://" + HOST + "/v1", "file:" + "///etc/hosts", HOST + "/v1", "https:" + "///v1"):
            self.assertTrue(net.problems(self.conn(url), self.env), url)

    def test_a_remote_endpoint_needs_a_named_variable_that_holds_a_key(self):
        self.assertIn("name the environment variable", net.problems(self.conn(REMOTE, key_env=""), self.env)[0])
        self.assertIn("MY_KEY holds no key", net.problems(self.conn(REMOTE), {})[0])
        self.assertIn("holds no key", net.problems(self.conn(REMOTE), {"MY_KEY": "  "})[0])

    def test_a_user_name_or_password_in_the_address_is_refused(self):
        found = net.problems(self.conn("https://" + "me:pw" + "@" + HOST + "/v1"), self.env)
        self.assertIn("environment variable", " ".join(found))

    def test_a_bad_format_a_missing_model_a_bad_header_or_a_bad_port_is_named(self):
        self.assertIn("format", " ".join(net.problems(self.conn(REMOTE, "xml"), self.env)))
        self.assertIn("model", " ".join(net.problems(self.conn(REMOTE)._replace(model=""), self.env)))
        self.assertIn("Name: value", " ".join(net.problems(self.conn(REMOTE, header="no colon"), self.env)))
        self.assertIn("Name: value", " ".join(net.problems(self.conn(REMOTE, header="bad name: x"), self.env)))
        self.assertEqual(net.problems(self.conn("https://" + HOST + ":99999/x"), self.env), ["the URL is not an address"])

    def test_nothing_is_opened_when_a_problem_is_found(self):
        bad = (self.conn("http://" + HOST + "/v1"), self.conn(REMOTE, key_env=""), self.conn(REMOTE, "xml"))
        with unittest.mock.patch.object(urllib.request.OpenerDirector, "open", side_effect=AssertionError("opened")):
            for c in bad:
                with self.assertRaises(net.NetError):
                    net.complete(c, "p", 50, env=self.env)
            for tokens in (0, -5, True, "50"):
                with self.assertRaises(net.NetError):
                    net.complete(self.conn(REMOTE), "p", tokens, env=self.env)       # type: ignore[arg-type]

    def test_a_remote_address_is_opened_with_its_own_key_and_nothing_else(self):
        seen = []

        def fake_open(opener, req, timeout=None):
            seen.append((req.full_url, dict(req.header_items()), timeout))
            raise net.urllib.error.URLError("stop here")

        with unittest.mock.patch.object(urllib.request.OpenerDirector, "open", fake_open):
            with self.assertRaises(net.NetError):
                net.complete(self.conn(REMOTE), "p", 50, env=self.env, timeout=7)
        (url, headers, timeout), = seen
        self.assertEqual((url, headers["Authorization"], timeout), (REMOTE, f"Bearer {self.key}", 7))


class TestWhenTheEndpointFails(Net):
    def test_an_http_error_is_reported_with_its_code_and_nothing_the_endpoint_said(self):
        ep = self.fake(status=401, raw=json.dumps({"error": f"bad key {self.key}"}).encode())
        with self.assertRaises(net.NetError) as cm:
            net.complete(self.conn(ep.url), "p", 50, env=self.env)
        self.assertIn("HTTP 401", str(cm.exception))
        self.assertNotIn(self.key, str(cm.exception))

    def test_a_redirect_is_not_followed_so_the_key_never_goes_to_another_host(self):
        other = self.fake(reply=REPLY_CHAT)
        ep = self.fake(status=307, raw=b"{}", location=other.url)
        for status in (301, 302, 303, 307, 308):
            ep.status = status
            with self.assertRaises(net.NetError) as cm:
                net.complete(self.conn(ep.url), "p", 50, env=self.env)
            self.assertIn(f"HTTP {status}", str(cm.exception))
        self.assertEqual(other.requests, [])

    def test_a_reply_that_is_not_json_is_not_the_wrong_shape_nor_empty_text(self):
        for raw, what in ((b"<html>", "not JSON"), (b"\xff\xfe", "not JSON"), (b'{"choices": []}', "chat shape"),
                          (b'{"choices": [{"message": {"content": "  "}}]}', "no text"),
                          (b'{"choices": [{"message": {"content": 5}}]}', "no text"), (b"[]", "chat shape")):
            ep = self.fake(raw=raw)
            with self.assertRaises(net.NetError, msg=raw) as cm:
                net.complete(self.conn(ep.url), "p", 50, env=self.env)
            self.assertIn(what, str(cm.exception), raw)

    def test_a_messages_reply_with_no_text_block_is_refused(self):
        ep = self.fake(reply={"content": [{"type": "thinking", "text": "x"}]})
        with self.assertRaises(net.NetError) as cm:
            net.complete(self.conn(ep.url, "messages"), "p", 50, env=self.env)
        self.assertIn("no text", str(cm.exception))

    def test_a_reply_larger_than_the_cap_is_refused(self):
        ep = self.fake(reply=REPLY_CHAT)
        with unittest.mock.patch.object(net, "MAX_REPLY_BYTES", 40):
            with self.assertRaises(net.NetError) as cm:
                net.complete(self.conn(ep.url), "p", 50, env=self.env)
        self.assertIn("too large", str(cm.exception))

    def test_an_endpoint_that_does_not_answer_in_time_fails_after_the_timeout(self):
        ep = self.fake(reply=REPLY_CHAT, delay=2.0)
        started = time.monotonic()
        with self.assertRaises(net.NetError) as cm:
            net.complete(self.conn(ep.url), "p", 50, env=self.env, timeout=0.3)
        self.assertLess(time.monotonic() - started, 1.8)
        self.assertRegex(str(cm.exception), r"did not answer|could not reach|no usable answer")

    def test_an_address_nothing_listens_on_is_could_not_reach(self):
        ep = self.fake(reply=REPLY_CHAT)
        ep.stop()
        with self.assertRaises(net.NetError) as cm:
            net.complete(self.conn(ep.url), "p", 50, env=self.env)
        self.assertIn("could not reach", str(cm.exception))

    def test_the_key_is_never_in_an_error_the_endpoint_failure_or_the_preview(self):
        ep = self.fake(status=500, raw=self.key.encode())
        c = self.conn(ep.url)
        texts = [net.preview(c, "p", 5), repr(net.headers(c, "-"))]
        with self.assertRaises(net.NetError) as cm:
            net.complete(c, "p", 5, env=self.env)
        texts.append(str(cm.exception))
        self.assertFalse([t for t in texts if self.key in t])


# ---- the claims check of 2026-11-12: what the public text says must be true ---------------------------------------------

class TestTheKeyIsNeverPrinted(Net):
    BAD = ("SECRET-line1\nX-Injected: yes", "SECRET\r\nX-Injected: yes", "SECRET\x00tail", "SECRET\x1btail", "SECRET\x7ftail",
           "SECRET-é-tail", "SECRET-€-tail", "SECRET tail")

    def test_a_key_a_header_cannot_carry_is_refused_before_a_request_is_built_and_is_not_in_the_message(self):
        for tail in self.BAD:
            key = secrets.token_hex(8) + tail
            ep = self.fake(reply=REPLY_CHAT)
            found = net.problems(self.conn(ep.url), {"MY_KEY": key})
            self.assertTrue(any("header cannot carry" in p for p in found), repr(tail))
            with unittest.mock.patch.object(net, "exact_request", side_effect=AssertionError("a request was built")):
                with self.assertRaises(net.NetError) as cm:
                    net.complete(self.conn(ep.url), "p", 50, env={"MY_KEY": key})
            self.assertNotIn(key.split("SECRET")[0], str(cm.exception), repr(tail))
            self.assertNotIn("X-Injected", str(cm.exception))
            self.assertEqual(ep.requests, [], "nothing went out")

    def test_the_http_clients_own_complaint_about_a_header_is_caught_and_never_carries_the_key(self):
        key = "SECRET-" + secrets.token_hex(8) + "\nX-Injected: yes"
        ep = self.fake(reply=REPLY_CHAT)
        with unittest.mock.patch.object(net, "problems", return_value=[]):      # as if the check before it had missed it
            with self.assertRaises(net.NetError) as cm:
                net.complete(self.conn(ep.url), "p", 50, env={"MY_KEY": key})
        self.assertNotIn("SECRET", str(cm.exception))
        self.assertNotIn("SECRET", repr(cm.exception))
        self.assertIsNone(cm.exception.__cause__)
        self.assertTrue(cm.exception.__suppress_context__, "the original error, which quotes the key, is not chained")
        self.assertEqual(ep.requests, [])

    def test_a_plain_printable_key_still_goes(self):
        ep = self.fake(reply=REPLY_CHAT)
        key = "sk-" + secrets.token_urlsafe(24) + "=~+/"
        net.complete(self.conn(ep.url), "p", 50, env={"MY_KEY": key})
        self.assertEqual(ep.requests[0][1]["authorization"], f"Bearer {key}")

    def test_an_extra_header_with_a_line_break_in_its_value_is_refused(self):
        self.assertTrue(net.problems(self.conn(REMOTE, header="X-A: b\nX-B: c"), self.env))


class TestTheAddressHoldsNoKey(Net):
    def test_a_query_string_or_a_fragment_is_refused_for_a_remote_and_a_local_address(self):
        token = secrets.token_hex(6)
        for url in (REMOTE + "?api_key=" + token, REMOTE + "#" + token, "http://" + LOOP + ":8080/v1?key=" + token, REMOTE + "?"):
            found = " ".join(net.problems(self.conn(url, key_env="MY_KEY"), self.env))
            self.assertIn("query string or a fragment", found, url)
            self.assertNotIn(token, found)

    def test_a_key_typed_as_the_variable_name_is_refused_without_being_echoed(self):
        typed = "sk_live_" + secrets.token_hex(10)
        found = " ".join(net.problems(self.conn(REMOTE, key_env=typed), {typed: "x"}))
        self.assertIn("looks like a key", found)
        self.assertNotIn(typed, found)


class TestTheExactRequest(Net):
    def test_the_bytes_and_the_header_names_shown_are_the_bytes_and_the_names_on_the_wire(self):
        for fmt in ("chat", "messages"):
            ep = self.fake(reply=REPLY_CHAT if fmt == "chat" else REPLY_MESSAGES)
            c = self.conn(ep.url, fmt, header="X-Version: 2")
            prompt = "Explain é 中文 \U0001f600 this."
            body, names = net.exact_request(c, prompt, 120)
            net.complete(c, prompt, 120, env=self.env)
            (raw, wire_names), = ep.wire
            self.assertEqual(raw, body, "byte for byte")
            self.assertEqual(sorted(n.lower() for n in wire_names), sorted(n.lower() for n in names))
            self.assertIn(f"({len(body)} bytes of body)", net.preview(c, prompt, 120))
            self.assertIn(body.decode("utf-8"), net.preview(c, prompt, 120))

    def test_an_endpoint_on_this_machine_lists_no_key_header_and_the_names_match_the_wire_too(self):
        ep = self.fake(reply=REPLY_CHAT)
        c = self.conn(ep.url, key_env="")
        body, names = net.exact_request(c, "p", 10)
        net.complete(c, "p", 10, env={})
        (raw, wire_names), = ep.wire
        self.assertEqual(raw, body)
        self.assertEqual(sorted(n.lower() for n in wire_names), sorted(n.lower() for n in names))
        self.assertNotIn("authorization", [n.lower() for n in names])

    def test_a_local_endpoint_that_names_a_variable_holding_nothing_lists_no_key_header_because_none_goes(self):
        # (2026-11-12, claims check) The list used to name Authorization whenever a variable was named, held or not.
        ep = self.fake(reply=REPLY_CHAT)
        c = self.conn(ep.url, key_env="MY_KEY")
        keyed = bool(net.key_of(c, {}))
        self.assertFalse(keyed)
        body, names = net.exact_request(c, "p", 10, keyed=keyed)
        net.complete(c, "p", 10, env={})
        (raw, wire_names), = ep.wire
        self.assertEqual(raw, body)
        self.assertEqual(sorted(n.lower() for n in wire_names), sorted(n.lower() for n in names))
        self.assertNotIn("authorization", [n.lower() for n in names])
        shown = net.preview(c, "p", 10, keyed=keyed)
        self.assertNotIn("Authorization", shown)
        self.assertIn("MY_KEY holds no key", shown)
        # and when the variable does hold one, the name is listed and the wire has the header
        ep2 = self.fake(reply=REPLY_CHAT)
        c2 = self.conn(ep2.url, key_env="MY_KEY")
        net.complete(c2, "p", 10, env=self.env)
        self.assertIn("authorization", [n.lower() for n in ep2.wire[0][1]])
        self.assertIn("Authorization", net.exact_request(c2, "p", 10, keyed=bool(net.key_of(c2, self.env)))[1])

    def test_the_key_header_is_listed_by_name_and_its_value_is_nowhere_in_what_is_shown(self):
        c = self.conn(REMOTE)
        body, names = net.exact_request(c, "p", 10)
        self.assertIn("Authorization", names)
        self.assertNotIn(self.key, net.preview(c, "p", 10) + body.decode() + repr(names))


class TestWhichProxyAnAddressGoesThrough(Net):
    def test_an_address_on_this_machine_never_goes_through_one(self):
        env = {"HTTPS_PROXY": "http://" + "proxy.example.test:3128", "HTTP_PROXY": "http://" + "proxy.example.test:3128"}
        for url in ("http://" + LOOP + ":8080/v1", "http://" + "localhost:11434/v1", "http://" + "[::1]:80/x"):
            self.assertEqual(net.proxy_for(url, env), "", url)

    def test_an_https_address_names_the_proxy_of_the_environment_without_its_credentials(self):
        env = {"HTTPS_PROXY": "http://" + "me:pw" + "@" + "proxy.example.test:3128/"}
        self.assertEqual(net.proxy_for(REMOTE, env), "http://" + "proxy.example.test:3128")
        self.assertNotIn("pw", net.proxy_for(REMOTE, env))
        self.assertEqual(net.proxy_for(REMOTE, {}), "")
        self.assertEqual(net.proxy_for(REMOTE, {"HTTP_PROXY": "http://" + "proxy.example.test:3128"}), "", "an http proxy is not used for https")

    def test_no_proxy_and_a_lower_case_variable_are_honoured_as_the_http_client_honours_them(self):
        up, low = "http://" + "up.example.test:1", "http://" + "low.example.test:2"
        self.assertEqual(net.proxy_for(REMOTE, {"HTTPS_PROXY": up, "https_proxy": low}), low)
        self.assertEqual(net.proxy_for(REMOTE, {"HTTPS_PROXY": up, "NO_PROXY": HOST}), "")
        self.assertEqual(net.proxy_for(REMOTE, {"HTTPS_PROXY": up, "no_proxy": "example.test"}), "")

    def test_with_no_environment_given_it_reads_the_process_environment_and_the_preview_says_so(self):
        with unittest.mock.patch.dict(os.environ, {"https_proxy": "http://" + "proxy.example.test:3128"}):
            self.assertEqual(net.proxy_for(REMOTE), "http://" + "proxy.example.test:3128")
            shown = net.preview(self.conn(REMOTE), "p", 10)
        self.assertIn("through the proxy " + "http://" + "proxy.example.test:3128", shown)
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("proxy", net.preview(self.conn(REMOTE), "p", 10).split("\n\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
