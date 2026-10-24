"""Sign-in + token purchase for the desktop app.

Desktop sign-in uses a browser handshake: the app opens the account page
with a one-time desktop link, the user signs in there (Firebase), and the
page hands the ID token back to the app.  Two transports are attempted,
in order:

1. Loopback HTTP: the page POSTs the token to ``http://127.0.0.1:8765/handshake``.
2. Clipboard: the user copies the code shown on the page and pastes it
   into the dialog.

Token purchase happens on the account page in the browser (Stripe
checkout lives there, backed by the billing service) — the desktop app
never handles card data.  The "Buy tokens" button opens the web page;
after purchase the token balance shown in the app refreshes on the next
API call.
"""

from __future__ import annotations

import http.server
import threading
import urllib.parse
import webbrowser
from typing import Callable

from . import config

HANDSHAKE_PORT = 8765


def _account_url(next_path: str = "desktop/handshake") -> str:
    base = config.web_base().rstrip("/")
    return f"{base}/{next_path}"


def open_account_page() -> None:
    webbrowser.open(_account_url(""))


def open_token_purchase() -> None:
    webbrowser.open(_account_url("account/billing"))


def sign_in_interactive(
    on_message: Callable[[str], None] | None = None,
    timeout_s: float = 300.0,
) -> dict:
    """Open the browser, wait for the token via loopback or clipboard.

    Returns the credential payload {id_token, uid, email, refresh_hint}.
    Raises ``RuntimeError`` when the handshake does not complete in time.
    """
    received: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/handshake" and "token" in qs:
                received.update({
                    "id_token": qs["token"][0],
                    "uid": qs.get("uid", [""])[0],
                    "email": qs.get("email", [""])[0],
                })
                done.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h3>Signed in!</h3>"
                    b"<p>You can close this tab and return to "
                    b"Phytospectra Desktop.</p></body></html>"
                )
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode()
            parsed = urllib.parse.parse_qs(body)
            if parsed.get("token"):
                received.update({
                    "id_token": parsed["token"][0],
                    "uid": parsed.get("uid", [""])[0],
                    "email": parsed.get("email", [""])[0],
                })
                done.set()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *args) -> None:  # silence
            return

    server = http.server.HTTPServer(("127.0.0.1", HANDSHAKE_PORT), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        if on_message:
            on_message("Opening the sign-in page in your browser…")
        webbrowser.open(f"{_account_url('desktop/handshake')}?port={HANDSHAKE_PORT}")
        if not done.wait(timeout=timeout_s):
            raise RuntimeError(
                "Sign-in did not complete in time. Use the code shown on "
                "the web page, or try again later."
            )
        return received
    finally:
        server.shutdown()
        server.server_close()
