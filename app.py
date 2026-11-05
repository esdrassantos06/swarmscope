"""Swarmscope desktop app: a native window around the Locust control panel.

The window process never imports Locust. Locust monkey-patches the stdlib with gevent,
which fights the GUI event loop, so it runs in a child process started from this same
executable with `--serve <port>`.
"""

import atexit
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

import psutil

HOST = "127.0.0.1"
SERVE_FLAG = "--serve"
TITLE = "Swarmscope"
BACKGROUND = "#0c0b09"


def status_page(headline: str, detail: str, color: str, pulse: bool = False) -> str:
    animation = "animation:p 1.2s ease-in-out infinite" if pulse else ""
    return f"""<!doctype html><html><body style="margin:0;height:100vh;display:grid;place-content:center;
background:{BACKGROUND};color:#8b8273;font:12px ui-monospace,Menlo,monospace;text-align:center">
<style>@keyframes p{{50%{{opacity:.35}}}}</style>
<p style="color:{color};letter-spacing:.3em;font-size:14px;{animation}">{headline}</p>
<p>{detail}</p></body></html>"""


STARTING = status_page("WARMING UP", "Starting the load engine.", "#ffb238", pulse=True)
STARTUP_FAILED = status_page(
    "ENGINE DID NOT START", "Close the window and open Swarmscope again.", "#ff5b3d"
)


def resource_dir() -> Path:
    """Folder holding locustfile.py and ui.html, inside the bundle when frozen."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def serve_argv(port: int, base: Path) -> list[str]:
    return [
        "locust",
        "-f",
        str(base / "locustfile.py"),
        "--web-host",
        HOST,
        "--web-port",
        str(port),
    ]


def child_command(
    port: int, parent_pid: int, frozen: bool, executable: str, script: str
) -> list[str]:
    """Command that relaunches this app as the Locust server, tied to `parent_pid`."""
    head = [executable] if frozen else [executable, script]
    return [*head, SERVE_FLAG, str(port), str(parent_pid)]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def wait_until_ready(
    url: str, alive: Callable[[], bool], timeout: float, interval: float = 0.15
) -> bool:
    """Poll `url` until it answers. False if `alive()` turns false or `timeout` seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive():
            return False
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(interval)
    return False


def exit_when_gone(
    pid: int,
    on_exit: Callable[[], None],
    sleep: Callable[[float], None] = time.sleep,
    interval: float = 1.0,
) -> None:
    """Poll until process `pid` exits, then call `on_exit`. Survives pid reuse."""
    try:
        parent = psutil.Process(pid)
        while parent.is_running() and parent.status() != psutil.STATUS_ZOMBIE:
            sleep(interval)
    except psutil.NoSuchProcess:
        pass
    on_exit()


def serve(port: int, parent_pid: int) -> None:
    sys.argv = serve_argv(port, resource_dir())
    import gevent
    from locust.main import main as locust_main

    # The window process can die without running atexit (macOS quits Cocoa apps hard),
    # so the server watches its parent. It must start after Locust's gevent patching:
    # a real thread started earlier deadlocks the import lock.
    gevent.spawn(exit_when_gone, parent_pid, lambda: os._exit(0), gevent.sleep)
    locust_main()


def stop(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()


def run_window() -> None:
    import webview

    port = free_port()
    command = child_command(
        port,
        os.getpid(),
        getattr(sys, "frozen", False),
        sys.executable,
        str(Path(__file__).resolve()),
    )
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    atexit.register(stop, child)

    url = f"http://{HOST}:{port}/ui"
    window_options = {
        "width": 1440,
        "height": 920,
        "min_size": (380, 600),
        "background_color": BACKGROUND,
    }
    window = webview.create_window(TITLE, html=STARTING, **window_options)

    def boot() -> None:
        if wait_until_ready(url, alive=lambda: child.poll() is None, timeout=45):
            window.load_url(url)
        else:
            window.load_html(STARTUP_FAILED)

    webview.start(boot, private_mode=False)
    stop(child)


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == SERVE_FLAG:
        serve(int(sys.argv[2]), int(sys.argv[3]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--probe":
        from probe import measure

        print(json.dumps(measure(sys.argv[2])), flush=True)
    else:
        run_window()


if __name__ == "__main__":
    main()
