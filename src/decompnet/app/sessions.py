"""One-time links and browser sessions for `decomp serve` (cli.py).

The app with its own window never accepts these: its page answers only clients whose user agent carries the
per-launch secret (window.py), and that stays so. But a browser can't carry a secret in its user agent the way the
app's own window does, so `decomp serve` pairs instead: it shows a code once in its terminal, the link trades the
code for a session cookie, and the code is spent. Sessions live in this process's memory and end with it: after an
update restart, or a stop and a start, the browser pairs again.

What keeps a session from reaching anything the window couldn't: the server still listens on 127.0.0.1 only (reaching
it takes a login to the machine), Host headers are checked as always, writes still need the X-Decomp header — which a
page from another site can't send without a CORS preflight the server never approves — and a session starts only with
a code that was shown once in the server's own terminal.

The tunnel's end on the user's machine needn't have the server's port (their own decompdecomp may hold it, or two
servers' tunnels are open at once): a paired browser, and the link that pairs it, may name 127.0.0.1 or localhost
with any port (tunnel_port). Only those two names: a page that reaches the server through DNS rebinding carries its
own name, and is refused as always. The cookie is named after that port, because a browser keeps one jar for every
port of 127.0.0.1 and two servers' sessions would otherwise replace each other.
"""

from __future__ import annotations

import re
import secrets
import time

COOKIE = "decomp_session"  # the browser's paired session, with the tunnel's port after it (cookie_for)
TUNNEL = re.compile(r"(?:127\.0\.0\.1|localhost):(\d{1,5})")  # the Host a browser sends through an SSH tunnel
WAITING_CODES = 8  # `b` in the TUI shows one after another; more than this waiting is a stuck terminal
CODE_SECONDS = 15 * 60  # how long a code shown and not used still works


def tunnel_port(host: str | None) -> str | None:
    """The port in a request's Host when it names this machine itself (127.0.0.1 or localhost), else None."""
    found = TUNNEL.fullmatch(host or "")
    return found[1] if found else None


def cookie_for(host: str | None) -> str | None:
    """The session cookie's name for a browser that reaches the app as `host` (None: not a name a tunnel gives)."""
    port = tunnel_port(host)
    return f"{COOKIE}_{port}" if port else None


def same(a: str, b: str) -> bool:
    """Whether two secrets are equal, in constant time (as bytes: compare_digest refuses text that isn't ASCII, and
    a cookie or a code can be anything)."""
    return secrets.compare_digest(a.encode("utf-8", "replace"), b.encode("utf-8", "replace"))


class Sessions:
    """The codes `decomp serve` has shown and not spent, and the sessions browsers have paired."""

    def __init__(self) -> None:
        self._codes: list[tuple[str, float]] = []  # (code, when it was shown)
        self._tokens: list[str] = []

    def new_code(self) -> str:
        """A fresh one-time code for the terminal to show."""
        code = secrets.token_urlsafe(9)
        self._codes = (self._fresh() + [(code, time.monotonic())])[-WAITING_CODES:]
        return code

    def _fresh(self) -> list[tuple[str, float]]:
        """The codes still waiting: shown less than CODE_SECONDS ago."""
        now = time.monotonic()
        return [(c, at) for c, at in self._codes if now - at < CODE_SECONDS]

    def spend(self, code: str) -> str:
        """Trade a one-time code for a session token. A code works once, within CODE_SECONDS of being shown; a wrong,
        used or old one is refused."""
        self._codes = self._fresh()
        for i, (waiting, _) in enumerate(self._codes):
            if same(waiting, code):
                del self._codes[i]
                token = secrets.token_urlsafe(24)
                self._tokens.append(token)
                return token
        raise ValueError("this link was used already, is too old, or is wrong: get a fresh one in the server's terminal")

    def valid(self, token: str) -> bool:
        """Whether a request's cookie names a live session (a browser that paired)."""
        return any(same(live, token) for live in self._tokens)

    def paired(self, request) -> bool:
        """Whether a request comes from a paired browser: through 127.0.0.1 or localhost (any port: the tunnel's
        own), carrying that port's session cookie."""
        name = cookie_for(request.headers.get("host"))
        return name is not None and self.valid(request.cookies.get(name, ""))
