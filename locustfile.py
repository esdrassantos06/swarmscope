import json
import random
import subprocess
import sys
from pathlib import Path

import gevent
from flask import jsonify, request, send_file, send_from_directory
from gevent.lock import BoundedSemaphore
from locust import events, task
from locust.contrib.fasthttp import FastHttpUser

from probe import probe_command
from retry import fetch_with_retry, parse_paths

CONTROL_PANEL = Path(__file__).with_name("ui.html")
FONTS = Path(__file__).with_name("assets") / "fonts"
APP_SCRIPT = Path(__file__).with_name("app.py")
PROBE_SLOT = BoundedSemaphore(1)


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


@events.init.add_listener
def mount_control_panel(environment, **_kwargs):
    """Serve the Swarmscope control panel at /ui next to Locust's own web UI."""
    if environment.web_ui is None:
        return
    environment.web_ui.app.add_url_rule(
        "/ui", "swarmscope_ui", lambda: send_file(CONTROL_PANEL, mimetype="text/html")
    )
    environment.web_ui.app.add_url_rule(
        "/ui/fonts/<path:name>",
        "swarmscope_fonts",
        lambda name: send_from_directory(FONTS, name, max_age=31536000),
    )
    environment.web_ui.app.add_url_rule(
        "/ui/probe", "swarmscope_probe", probe_endpoint, methods=["POST"]
    )


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
