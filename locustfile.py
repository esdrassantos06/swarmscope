import hmac
import json
import logging
import os
import random
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

import gevent
from flask import Response, jsonify, redirect, request, send_from_directory
from gevent.lock import BoundedSemaphore
from locust import events, task
from locust.contrib.fasthttp import FastHttpUser

from probe import probe_command
from retry import fetch_with_retry, parse_paths

CONTROL_PANEL = Path(__file__).with_name("ui.html")
PANEL_DIR = CONTROL_PANEL.parent
FONTS = Path(__file__).with_name("assets") / "fonts"
APP_SCRIPT = Path(__file__).with_name("app.py")
PROBE_SLOT = BoundedSemaphore(1)
TOKEN_ENV = "SWARMSCOPE_TOKEN"
TOKEN_COOKIE = "swarmscope_token"
TOKEN_PLACEHOLDER = "__SWARMSCOPE_TOKEN__"

logger = logging.getLogger(__name__)


def run_probe(url: str) -> dict:
    """Measure one real page load of `url` in a separate browser process."""
    command = probe_command(
        url, getattr(sys, "frozen", False), sys.executable, str(APP_SCRIPT)
    )
    try:
        done = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "The page took over 2 minutes to load."}
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        detail = (done.stderr or "no output").strip().splitlines()[-1:]
        return {"ok": False, "error": f"Page probe failed: {' '.join(detail)}"}


def probe_endpoint():
    url = str((request.get_json(silent=True) or {}).get("url", ""))
    if not url.startswith(("http://", "https://")):
        return jsonify(
            {"ok": False, "error": "URL must start with http:// or https://"}
        ), 400
    if not PROBE_SLOT.acquire(blocking=False):
        return jsonify({"ok": False, "error": "A page probe is already running."}), 409
    try:
        return jsonify(run_probe(url))
    finally:
        PROBE_SLOT.release()


def same_token(offered: str, token: str) -> bool:
    return hmac.compare_digest(offered.encode(), token.encode())


def access_guard(token: str, port: int):
    """Flask `before_request` hook that only lets Swarmscope's own window through.

    The window opens `/ui?token=...`; that GET sets a SameSite=Strict, HttpOnly cookie
    and redirects to the same URL without the token. Every other request, Locust's own
    routes included, needs that cookie and a loopback Host. A web page in the user's
    browser has neither, so it can't start, stop or probe anything (CSRF), and a
    rebound DNS name fails the Host check.
    """
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    refused = Response(
        "Open Swarmscope from its own window.", 403, mimetype="text/plain"
    )

    def guard():
        if request.host not in hosts:
            return refused
        offered = request.args.get("token")
        if offered is not None and request.method == "GET":
            if not same_token(offered, token):
                return refused
            rest = urlencode(
                [(k, v) for k, v in request.args.items(multi=True) if k != "token"]
            )
            response = redirect(f"{request.path}?{rest}" if rest else request.path)
            response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="Strict")
            return response
        if not same_token(request.cookies.get(TOKEN_COOKIE, ""), token):
            return refused
        return None

    return guard


@events.init.add_listener
def mount_control_panel(environment, **_kwargs):
    """Serve the Swarmscope control panel at /ui and lock the engine to its window."""
    if environment.web_ui is None:
        return
    app = environment.web_ui.app
    port = environment.parsed_options.web_port
    token = os.environ.get(TOKEN_ENV)
    if not token:
        token = secrets.token_urlsafe(32)
        logger.warning("Swarmscope panel: http://127.0.0.1:%s/ui?token=%s", port, token)
    app.before_request(access_guard(token, port))

    panel = CONTROL_PANEL.read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, token)
    app.add_url_rule(
        "/ui", "swarmscope_ui", lambda: Response(panel, mimetype="text/html")
    )
    app.add_url_rule(
        "/ui/<any(ui.css, ui.js):name>",
        "swarmscope_assets",
        lambda name: send_from_directory(PANEL_DIR, name),
    )
    app.add_url_rule(
        "/ui/fonts/<path:name>",
        "swarmscope_fonts",
        lambda name: send_from_directory(FONTS, name, max_age=31536000),
    )
    app.add_url_rule("/ui/probe", "swarmscope_probe", probe_endpoint, methods=["POST"])


@events.init_command_line_parser.add_listener
def add_settings(parser):
    parser.add_argument(
        "--paths",
        default="/",
        help="Paths to visit, comma separated (e.g. /, /docs, /pricing)",
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=3.0,
        help="Min seconds each user waits between requests",
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=5.0,
        help="Max seconds each user waits between requests",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Retries on 429, 5xx or network error (0 disables)",
    )
    parser.add_argument(
        "--backoff",
        type=float,
        default=1.0,
        help="Base seconds for exponential retry backoff",
    )


class SiteVisitor(FastHttpUser):
    def wait_time(self) -> float:
        options = self.environment.parsed_options
        return max(
            random.uniform(float(options.delay_min), float(options.delay_max)), 0.0
        )

    @task
    def visit_page(self) -> None:
        options = self.environment.parsed_options
        fetch_with_retry(
            self.client,
            random.choice(parse_paths(options.paths)),
            max_retries=int(options.max_retries),
            base=float(options.backoff),
            sleep=gevent.sleep,
        )
