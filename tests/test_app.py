import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import child_command, child_env, free_port, serve_argv, wait_until_ready


class Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture
def server_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Ok)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/ui"
    server.shutdown()


def test_serve_argv_points_locust_at_bundled_locustfile_on_localhost():
    assert serve_argv(4321, Path("/bundle")) == [
        "locust",
        "-f",
        str(Path("/bundle/locustfile.py")),
        "--web-host",
        "127.0.0.1",
        "--web-port",
        "4321",
    ]


def test_child_command_reuses_frozen_executable():
    assert child_command(
        4321, 99, frozen=True, executable="/App/Swarmscope", script="app.py"
    ) == [
        "/App/Swarmscope",
        "--serve",
        "4321",
        "99",
    ]


def test_child_command_runs_script_with_interpreter_in_dev():
    assert child_command(
        4321, 99, frozen=False, executable="/py", script="/src/app.py"
    ) == [
        "/py",
        "/src/app.py",
        "--serve",
        "4321",
        "99",
    ]


def test_free_port_is_bindable():
    port = free_port()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_wait_until_ready_returns_once_server_answers(server_url):
    assert wait_until_ready(server_url, alive=lambda: True, timeout=5) is True


def test_wait_until_ready_stops_when_child_dies():
    url = f"http://127.0.0.1:{free_port()}/ui"
    assert wait_until_ready(url, alive=lambda: False, timeout=5) is False


def test_wait_until_ready_times_out():
    url = f"http://127.0.0.1:{free_port()}/ui"
    assert wait_until_ready(url, alive=lambda: True, timeout=0.3) is False


def test_exit_when_gone_fires_after_parent_process_exits():
    root = Path(__file__).resolve().parent.parent
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    watcher = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, app; app.exit_when_gone(int(sys.argv[1]), lambda: print('gone'), interval=0.1)",
            str(parent.pid),
        ],
        cwd=root,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            watcher.wait(timeout=0.8)
        parent.kill()
        parent.wait()
        out, _ = watcher.communicate(timeout=10)
        assert out.strip() == "gone"
    finally:
        parent.kill()
        watcher.kill()


class Forbidden(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(403)
        self.end_headers()

    def log_message(self, *_args):
        pass


def test_wait_until_ready_counts_a_refusal_as_up():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Forbidden)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/ui"
        assert wait_until_ready(url, alive=lambda: True, timeout=5) is True
    finally:
        server.shutdown()


def test_child_env_carries_the_token_without_putting_it_in_argv():
    env = child_env("abc123", {"PATH": "/bin"})

    assert env == {"PATH": "/bin", "SWARMSCOPE_TOKEN": "abc123"}
    assert "abc123" not in child_command(
        4321, 99, frozen=True, executable="/x", script="y"
    )
