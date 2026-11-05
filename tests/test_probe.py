import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from probe import BROWSER_MISSING, probe_command

ROOT = Path(__file__).resolve().parent.parent


def probe_cli(url: str) -> dict:
    """Runs the probe the way the app does: in a fresh process, not this gevent-patched one."""
    done = subprocess.run(
        probe_command(
            url, frozen=False, executable=sys.executable, script=str(ROOT / "app.py")
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    result = json.loads(done.stdout.strip().splitlines()[-1])
    if result.get("error") == BROWSER_MISSING:
        pytest.skip("no Chrome or Edge installed")
    return result


PAGE = b"""<!doctype html><html><head><title>t</title>
<link rel="stylesheet" href="/style.css"></head>
<body><h1>Hello under load</h1><p>Some text for a paint.</p></body></html>"""


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        body, kind = (
            (b"h1{color:red}", "text/css")
            if self.path == "/style.css"
            else (PAGE, "text/html")
        )
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


def test_probe_command_reuses_frozen_executable():
    assert probe_command(
        "https://a.test/", frozen=True, executable="/App/Swarmscope", script="app.py"
    ) == [
        "/App/Swarmscope",
        "--probe",
        "https://a.test/",
    ]


def test_probe_command_runs_script_with_interpreter_in_dev():
    assert probe_command(
        "https://a.test/", frozen=False, executable="/py", script="/src/app.py"
    ) == [
        "/py",
        "/src/app.py",
        "--probe",
        "https://a.test/",
    ]


def test_measure_reports_real_page_timings(site):
    result = probe_cli(site)

    assert result["ok"] is True
    assert 0 < result["ttfb"] <= result["load"]
    assert result["fcp"] is not None and result["fcp"] > result["ttfb"]
    assert result["lcp"] is not None and result["lcp"] >= result["fcp"]
    assert result["requests"] >= 2
    assert result["bytes"] > 0
    assert result["browser"].startswith("patchright")


def test_measure_reports_unreachable_page_as_error():
    result = probe_cli("http://127.0.0.1:9/")

    assert result["ok"] is False
    assert result["error"]
