"""A Python older than 3.12 stops at once with a message, exit 2 (plan
261127 py-versions): os.DirEntry.is_junction() / os.path.isjunction() exist
only since 3.12, and the program broke in the middle instead of saying so."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import photo_backup
import selective_backup

REPO = Path(__file__).resolve().parents[1]


def _import_as(tmp_path, module, version):
    code = (f"import sys; sys.version_info = {version!r}; "
            f"sys.path.insert(0, {str(REPO)!r}); import {module}")
    env = dict(os.environ, PHOTOBACKUP_LOG_DIR=str(tmp_path / "logs"))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=60, cwd=tmp_path, env=env)


SCRIPTS = ["selective_backup", "photo_backup", "manifest_backup", "mirror_backup",
           "restore_backup", "captureone_backup"]


@pytest.mark.parametrize("module", SCRIPTS)
def test_an_old_python_stops_with_a_message(tmp_path, module):
    done = _import_as(tmp_path, module, (3, 11, 9, "final", 0))
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "needs Python 3.12 or newer" in done.stderr
    assert "3.11.9" in done.stderr
    assert done.stdout == ""


@pytest.mark.parametrize("module", ["selective_backup", "photo_backup"])
def test_python_3_12_imports(tmp_path, module):
    done = _import_as(tmp_path, module, (3, 12, 0, "final", 0))
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert done.stderr == ""


def test_one_minimum():
    assert selective_backup._MIN_PYTHON == photo_backup._MIN_PYTHON == (3, 12)


def _old_python():
    """`py -3.9` when the launcher and that Python are installed, else None."""
    launcher = shutil.which("py")
    if not launcher:
        return None
    try:
        found = subprocess.run([launcher, "-3.9", "-c", "pass"], capture_output=True,
                               timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [launcher, "-3.9"] if found.returncode == 0 else None


@pytest.mark.parametrize("module", SCRIPTS)
def test_a_real_python_3_9_gets_the_message_not_a_syntax_error(tmp_path, module):
    """The check runs only once the whole file has compiled: 3.12-only syntax
    (an f-string with the same quotes inside) would give an old Python a
    SyntaxError instead of the message (review 261127 py-versions, SF1)."""
    old = _old_python()
    if old is None:
        pytest.skip("py -3.9 is not installed")
    env = dict(os.environ, PHOTOBACKUP_LOG_DIR=str(tmp_path / "logs"))
    done = subprocess.run(old + [str(REPO / f"{module}.py"), "--help"], capture_output=True,
                          text=True, timeout=120, cwd=tmp_path, env=env)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "needs Python 3.12 or newer" in done.stderr
    assert "SyntaxError" not in done.stderr
    assert done.stdout == ""
