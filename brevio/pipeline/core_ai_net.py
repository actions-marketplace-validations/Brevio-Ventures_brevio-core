"""The endpoint transport of ``brevio-core ai``: the only shipped module that may reach the network (the 0.7 set, owner
decision D24, 2026-11-11). Its request is checked and loopback-aware.

One function sends: ``complete(connector, prompt, max_output)``. It makes exactly one request to the address the user
connected, with no retry and no other destination. It needs https; plain http only to this machine, where no key is
needed. The key is read from the environment variable the user named, at the moment of the call, and is never stored,
printed or put in an error. A redirect is never followed, because a redirect would carry the key to another host. A
loopback endpoint is never sent through a proxy. The reply is read up to a size cap and is data: this module returns its
text and, when the endpoint reports them, its token counts, and does nothing else with it.

``exact_request`` builds the exact bytes of the body and the names of every header, and both ``preview`` and ``complete`` use
it, so what the user is shown is what leaves. ``proxy_for`` says which proxy, if any, an address would go through. A key that
a header cannot carry is refused before a request is built, and no message holds the key. Standard library only.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from brevio.pipeline.core_ai_state import FORMATS, Connector, key_env_problem, path_problem
from brevio.pipeline.core_ai_text import EXPLAIN_SYSTEM

SYSTEM = EXPLAIN_SYSTEM
TIMEOUT_S = 60
MAX_REPLY_BYTES = 1_000_000     # a reply larger than this is refused: it is read into memory
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]+$")
_SHOWN_AS = "(the key comes from {var} and is not shown)"
_PRINTABLE = re.compile(r"^[\x20-\x7e]+$")     # what a header may carry here: printable ASCII, no line break, no control character
USER_AGENT = "brevio-core"
AUTOMATIC_HEADERS = ("Host", "Content-Length", "Accept-Encoding", "Connection")      # the ones the HTTP client adds itself


class NetError(RuntimeError):
    """The request was refused before it left, or the endpoint failed; the message never holds the key."""


ERROR = NetError        # the name the command module catches, the same for this transport and for the command runner


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None     # urllib then raises the 3xx as an HTTPError: nothing is sent anywhere else


def loopback(host: str | None) -> bool:
    """``host`` is this machine."""
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_local(c: Connector) -> bool:
    return loopback(urllib.parse.urlsplit(c.url).hostname)


def key_problem(key: str) -> str:
    """Why ``key`` cannot be sent in a header (it holds a control character, a line break or a character outside printable
    ASCII), or ``""``. The message never holds the key (2026-11-12, claims check)."""
    if key and not _PRINTABLE.match(key):
        return "the key holds a character a header cannot carry (a control character, a line break or a non-ASCII character)"
    return ""


def _address_problems(c: Connector, parts: urllib.parse.SplitResult, local: bool) -> list[str]:
    out = []
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        out.append("the URL must use https (plain http only to this machine), so the key never travels in clear")
    elif not parts.hostname:
        out.append("the URL has no host")
    if parts.username or parts.password:
        out.append("the URL holds a user name or password: keep the key in an environment variable instead")
    if parts.query or parts.fragment or "?" in c.url or "#" in c.url:
        out.append("the URL holds a query string or a fragment: a key there would be stored and sent; keep it in an environment variable")
    if path_problem(parts.path):
        out.append(path_problem(parts.path))
    return out


def _shape_problems(c: Connector) -> list[str]:
    out = []
    if c.format not in FORMATS:
        out.append(f"format must be {' or '.join(FORMATS)}")
    if not c.model:
        out.append("no model name")
    if c.header:
        name, sep, value = c.header.partition(":")
        if not (sep and _HEADER_NAME.match(name.strip()) and _PRINTABLE.match(value.strip() or " ")):
            out.append("the header must read 'Name: value'")
    return out


def _key_problems(c: Connector, local: bool, env: Mapping[str, str]) -> list[str]:
    if c.key_env and key_env_problem(c.key_env):
        return [key_env_problem(c.key_env)]
    if not c.key_env:
        return [] if local else ["name the environment variable that holds your key (key_env)"]
    key = env.get(c.key_env, "").strip()
    if not key:
        return [] if local else [f"the variable {c.key_env} holds no key"]
    bad = key_problem(key)
    return [f"{bad} (the variable {c.key_env})"] if bad else []


def problems(c: Connector, env: Mapping[str, str] | None = None) -> list[str]:
    """What keeps ``c`` from sending, in plain words; empty when it may. An endpoint on this machine needs no key."""
    env = os.environ if env is None else env
    try:
        parts = urllib.parse.urlsplit(c.url)
        _ = parts.port      # a port that is not a number raises ValueError
    except ValueError:
        return ["the URL is not an address"]
    local = loopback(parts.hostname)
    return _address_problems(c, parts, local) + _shape_problems(c) + _key_problems(c, local, env)


def proxy_for(url: str, env: Mapping[str, str] | None = None) -> str:
    """The proxy a request to ``url`` would go through, as ``scheme://host:port`` (no credentials), or ``""`` for none. An
    endpoint on this machine never goes through one. ``env`` names the variables to read (the process environment, and where
    the system has its own proxy settings, those, as the HTTP client does)."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        return ""
    if not host or loopback(host):
        return ""
    if env is None:
        proxies, bypass = urllib.request.getproxies(), urllib.request.proxy_bypass(host)
    else:       # lower case wins over upper case, as in the standard library
        proxies = {k[:-6].lower(): v for k, v in sorted(env.items(), key=lambda kv: kv[0].islower())
                   if k.lower().endswith("_proxy") and v}
        bypass = urllib.request.proxy_bypass_environment(host, proxies)
    chosen = proxies.get(parts.scheme, "")
    if not chosen or bypass:
        return ""
    shown = urllib.parse.urlsplit(chosen if "://" in chosen else f"//{chosen}")
    return f"{shown.scheme or 'http'}://{shown.hostname or '?'}" + (f":{shown.port}" if shown.port else "")


def destination(c: Connector) -> str:
    """Where a request goes, for the user to read: scheme, host, port and path, with no user name and no query."""
    try:
        parts = urllib.parse.urlsplit(c.url)
        host, port = parts.hostname or "?", f":{parts.port}" if parts.port else ""
    except ValueError:
        return "(not an address)"
    host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{host}{port}{parts.path}"


def request_body(c: Connector, prompt: str, max_output: int, system: str = SYSTEM) -> dict[str, Any]:
    """The whole request body: the system line and the prompt; nothing else."""
    if c.format == "messages":
        return {"model": c.model, "max_tokens": max_output, "system": system,
                "messages": [{"role": "user", "content": prompt}]}
    return {"model": c.model, "max_tokens": max_output,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}


def headers(c: Connector, key: str) -> dict[str, str]:
    """The request headers; ``key`` is empty for an endpoint on this machine, which then gets no key header."""
    out = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "Accept": "application/json"}
    if key and c.format == "messages":
        out["x-api-key"] = key
    elif key:
        out["Authorization"] = f"Bearer {key}"
    if c.header:
        name, value = c.header.split(":", 1)
        out[name.strip()] = value.strip()
    return out


def key_of(c: Connector, env: Mapping[str, str]) -> str:
    """The key ``complete`` sends: the one in the variable ``c`` names, or none for an endpoint on this machine that names no
    variable. A variable that is empty gives an empty key, and then no key header goes."""
    return "" if _is_local(c) and not c.key_env else env.get(c.key_env, "").strip()


def exact_request(c: Connector, prompt: str, max_output: int, system: str = SYSTEM,
                  keyed: bool | None = None) -> tuple[bytes, list[str]]:
    """``(the exact bytes of the request body, the names of every header it goes with)``. ``preview`` shows these and
    ``complete`` sends these: the bytes are not built twice. The names are the ones of ``headers`` (the key header included
    when a key goes: ``keyed``, which is whether ``c`` names a variable unless the caller knows that it holds nothing) and the
    four the HTTP client adds itself (2026-11-12, claims check)."""
    body = json.dumps(request_body(c, prompt, max_output, system), indent=2, ensure_ascii=False).encode("utf-8")
    named = headers(c, "-" if (bool(c.key_env) if keyed is None else keyed) else "")
    return body, sorted({*named, *AUTOMATIC_HEADERS}, key=str.lower)


def preview(c: Connector, prompt: str, max_output: int, system: str = SYSTEM, keyed: bool | None = None) -> str:
    """What ``complete`` would send, as text: the destination (and the proxy it goes through, if any), the header names, the
    size in bytes and the exact body. ``keyed`` says whether a key goes (``key_of`` is not empty); by default it does when
    ``c`` names a variable."""
    keyed = bool(c.key_env) if keyed is None else keyed
    body, names = exact_request(c, prompt, max_output, system, keyed)
    shown = (_SHOWN_AS.format(var=c.key_env) if keyed else
             f"(the variable {c.key_env} holds no key, so no key header goes)" if c.key_env else
             "(no key: this endpoint is on this machine)")
    proxy = proxy_for(c.url)
    via = f"(through the proxy {proxy} from your environment: it carries the request and sees where it goes)\n" if proxy else ""
    return (f"POST {destination(c)}\n{via}(headers: {', '.join(names)}; {shown})\n({len(body)} bytes of body)\n\n"
            f"{body.decode('utf-8')}\n")


def answer_text(fmt: str, data: Any) -> str:
    """The reply's text, read in the shape of ``fmt``."""
    try:
        if fmt == "messages":
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        else:
            text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise NetError(f"the endpoint's answer is not in the {fmt} shape") from exc
    if not isinstance(text, str) or not text.strip():
        raise NetError("the endpoint answered with no text")
    return text.strip()


def usage_of(fmt: str, data: Any) -> dict[str, int] | None:
    """``{"input_tokens": n, "output_tokens": n}`` from the reply's ``usage``, or ``None`` when it reports none."""
    use = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(use, dict):
        return None
    names = ("input_tokens", "output_tokens") if fmt == "messages" else ("prompt_tokens", "completion_tokens")
    counts = [use.get(n) for n in names]
    if not all(type(n) is int and n >= 0 for n in counts):
        return None
    return {"input_tokens": int(counts[0] or 0), "output_tokens": int(counts[1] or 0)}


def _post(c: Connector, key: str, body: bytes, timeout: float) -> Any:
    handlers: list[Any] = [_NoRedirect()]
    if _is_local(c):
        handlers.append(urllib.request.ProxyHandler({}))      # a loopback endpoint never goes through a proxy
    try:
        req = urllib.request.Request(c.url, data=body, headers=headers(c, key), method="POST")
        opener = urllib.request.build_opener(*handlers)
        opener.addheaders = []      # the headers sent are the ones ``exact_request`` lists, not a default of the library
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_REPLY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        exc.close()     # the error holds the connection open until it is closed
        raise NetError(f"the endpoint answered HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise NetError(f"could not reach the endpoint: {exc.reason}") from exc
    except TimeoutError as exc:
        raise NetError(f"the endpoint did not answer within {timeout:g} seconds") from exc
    except ValueError:
        # The HTTP client's own text quotes the header value, which holds the key: say nothing of it and chain nothing.
        raise NetError("the request could not be built: a header holds a character it cannot carry") from None
    except (OSError, http.client.HTTPException) as exc:
        raise NetError(f"no usable answer from the endpoint: {type(exc).__name__}") from exc
    if len(raw) > MAX_REPLY_BYTES:
        raise NetError("the endpoint's answer is too large to read")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:       # UnicodeDecodeError is a ValueError
        raise NetError("the endpoint's answer is not JSON") from exc


def complete(c: Connector, prompt: str, max_output: int, *, env: Mapping[str, str] | None = None,
             timeout: float = TIMEOUT_S, system: str = SYSTEM) -> tuple[str, dict[str, int] | None]:
    """Send ``prompt`` to the endpoint ``c`` once and return ``(text, usage)``; ``usage`` is ``None`` when none is reported.

    Raises ``NetError`` before anything is sent when ``problems`` finds any, and after it when the endpoint fails. There is
    no retry here and no second destination. ``env`` is where the key variable is read (the process environment)."""
    env = os.environ if env is None else env
    if not (type(max_output) is int and max_output > 0):
        raise NetError("max_output must be a positive number of tokens")
    found = problems(c, env)
    if found:
        raise NetError("; ".join(found))
    key = key_of(c, env)
    data = _post(c, key, exact_request(c, prompt, max_output, system, bool(key))[0], timeout)
    return answer_text(c.format, data), usage_of(c.format, data)
