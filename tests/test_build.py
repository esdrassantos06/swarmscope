from pathlib import Path

import pytest

import build

ROOT = Path(__file__).resolve().parent.parent


def test_importing_build_does_not_start_pyinstaller():
    assert callable(build.main)


@pytest.mark.parametrize(
    "platform,expected",
    [
        ("darwin", ROOT / "dist" / "Swarmscope.app"),
        ("win32", ROOT / "dist" / "Swarmscope" / "Swarmscope.exe"),
    ],
)
def test_app_path_points_at_the_built_app(platform, expected):
    assert build.app_path(platform) == expected


def test_pyinstaller_args_use_absolute_paths_so_any_cwd_works():
    args = build.pyinstaller_args("darwin", "arm64")

    assert args[0] == str(ROOT / "app.py")
    assert f"--icon={ROOT / 'assets' / 'icon.png'}" in args


@pytest.mark.parametrize(
    "machine,expected", [("arm64", "--target-arch=arm64"), ("x86_64", "--target-arch=x86_64")]
)
def test_mac_builds_target_the_cpu_they_run_on(machine, expected):
    assert expected in build.pyinstaller_args("darwin", machine)


def test_windows_builds_set_no_target_arch():
    assert not any(a.startswith("--target-arch") for a in build.pyinstaller_args("win32", "AMD64"))


def test_open_app_explains_how_to_build_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        build, "app_path", lambda _platform=None: tmp_path / "missing.app"
    )

    with pytest.raises(SystemExit, match="task build"):
        build.open_app()
