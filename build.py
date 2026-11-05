"""Build and open the Swarmscope desktop app (`task build`, `task open`).

macOS gives dist/Swarmscope.app, Windows gives dist/Swarmscope/Swarmscope.exe.
PyInstaller cannot cross-compile, so each build matches the OS and CPU it runs on:
an Apple Silicon Mac gives an arm64 app, an Intel Mac (or `arch -x86_64` with an
x86_64 Python under Rosetta) gives an x86_64 one.
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUNDLED_FILES = ["locustfile.py", "probe.py", "retry.py", "ui.html"]


def app_path(platform: str = sys.platform) -> Path:
    """Where PyInstaller puts the finished app on `platform`."""
    if platform == "darwin":
        return ROOT / "dist" / "Swarmscope.app"
    return ROOT / "dist" / "Swarmscope" / "Swarmscope.exe"


def pyinstaller_args(
    os_name: str = sys.platform, machine: str = platform.machine()
) -> list[str]:
    args = [
        str(ROOT / "app.py"),
        "--name=Swarmscope",
        "--windowed",
        "--noconfirm",
        "--clean",
        f"--distpath={ROOT / 'dist'}",
        f"--workpath={ROOT / 'build'}",
        f"--specpath={ROOT}",
        f"--icon={ROOT / 'assets' / 'icon.png'}",
        "--osx-bundle-identifier=app.swarmscope.desktop",
        "--collect-all=locust",
        "--collect-all=gevent",
        "--collect-all=geventhttpclient",
        "--collect-all=patchright",
        *[f"--add-data={ROOT / name}{os.pathsep}." for name in BUNDLED_FILES],
        f"--add-data={ROOT / 'assets' / 'fonts'}{os.pathsep}assets/fonts",
    ]
    if os_name == "darwin":
        args.append(f"--target-arch={machine}")
    return args


def main() -> None:
    import PyInstaller.__main__

    PyInstaller.__main__.run(pyinstaller_args())
    print(f"Built {app_path()}")


def open_app() -> None:
    """Launch the built app, the way double-clicking it would."""
    path = app_path()
    if not path.exists():
        raise SystemExit(f"{path} doesn't exist yet. Run `task build` first.")
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=True)
    else:
        os.startfile(path)


if __name__ == "__main__":
    main()
