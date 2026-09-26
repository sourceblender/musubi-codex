"""Install-path smoke tests for ``musubi-codex-plugin``.

These tests run against an editable install of this repo on top of a real
``musubi-harness`` install, so a regression to a path-hacked layout fails
here first. They mirror ``tests/test_musubi_harness_installable.py`` in the
fleet-tools repo but for the plugin side.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import venv
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run_in_clean_interpreter(script: str) -> subprocess.CompletedProcess[str]:
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        venv_path = Path(scratch) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(str(venv_path))
        python = venv_path / "bin" / "python"
        subprocess.check_call(
            [str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Install the published musubi-harness from PyPI, then the plugin
        # from this repo. This mirrors what an external user would do.
        subprocess.check_call(
            [str(python), "-m", "pip", "install", "--quiet", "musubi-harness>=1.0,<2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.check_call(
            [str(python), "-m", "pip", "install", "--quiet", str(REPO)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # The venv's bin/ must be on PATH: that is where pip puts the console
        # scripts, and it's what an activated venv or a PATH install provides.
        env = {
            "PATH": os.pathsep.join([str(venv_path / "bin"), os.environ.get("PATH", "")]),
            "HOME": os.environ.get("HOME", ""),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        }
        return subprocess.run(
            [str(python), "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )


def test_plugin_installs_into_clean_venv() -> None:
    """pip install works and the entry points are reachable on PATH."""
    result = _run_in_clean_interpreter(
        "import importlib.util; "
        "import sys; "
        "mods = ['musubi_codex', 'musubi_codex.stop', 'musubi_codex.mcp', "
        "'musubi_codex.session_start', 'musubi_codex.runtime']; "
        "ok = all(importlib.util.find_spec(m) is not None for m in mods); "
        "print('PLUGIN_OK' if ok else 'PLUGIN_MISSING'); "
        # Verify the entry points are reachable
        "import shutil; "
        "eps = ['musubi-codex-mcp', 'musubi-codex-session-start', 'musubi-codex-stop']; "
        "reachable = [name for name in eps if shutil.which(name)]; "
        "print('EPS:', ','.join(sorted(reachable)))"
    )
    assert result.returncode == 0, (
        f"install failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "PLUGIN_OK", result.stdout
    eps_line = lines[1]
    assert "musubi-codex-mcp" in eps_line, eps_line
    assert "musubi-codex-session-start" in eps_line, eps_line
    assert "musubi-codex-stop" in eps_line, eps_line


def test_plugin_has_no_fleet_tools_dependency() -> None:
    """A fresh venv with only the plugin + harness must not require fleet-tools."""
    result = _run_in_clean_interpreter(
        "import sys, musubi_codex, musubi_harness; "
        "fleet_paths = [p for p in sys.path if 'fleet-tools' in p]; "
        "print('CLEAN' if not fleet_paths else 'CONTAMINATED'); "
        "print('codex_path:', musubi_codex.__file__); "
        "print('harness_path:', musubi_harness.__file__)"
    )
    assert result.returncode == 0, (
        f"contamination check failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "CLEAN", (
        f"fresh venv has fleet-tools on sys.path:\n{result.stdout}\nstderr={result.stderr}"
    )
    # musubi_codex may be either editable-installed (source repo path) or
    # wheel-installed (site-packages). The hard requirement is just that
    # the harness lives in site-packages and neither path mentions fleet-tools.
    assert "/site-packages/" in lines[2], lines  # musubi_harness in site-packages
    assert "fleet-tools" not in lines[1], lines  # musubi_codex never references fleet-tools
    assert "fleet-tools" not in lines[2], lines  # musubi_harness never references fleet-tools


def test_plugin_adapter_imports_cleanly() -> None:
    """The adapter code imports without touching anything outside its public surface."""
    importlib.import_module("musubi_codex")
    importlib.import_module("musubi_codex.runtime")
    importlib.import_module("musubi_codex.mcp")
    importlib.import_module("musubi_codex.session_start")
    importlib.import_module("musubi_codex.stop")
