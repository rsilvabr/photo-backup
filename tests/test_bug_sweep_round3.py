"""Round 3 of the 261110 bug sweep (AI_tools/261111_Fable_review_bug-sweep-fixes-round2.md,
R1-R3). Each test here failed against a301f55 (v0.12.1 before round 3; the
report has the run)."""
import os
import shutil
import sqlite3
import string
import subprocess
import sys

import pytest

import selective_backup as sb

from conftest import argv_for, latest_log, make_tree
from test_bug_sweep import CHAINS, planned_set, plan_rows
from test_bug_sweep_round2 import catalog_rows, counting_reads, failing_writes, full_tree


# ---- R1: a catalog that cannot be written stops the backup, never a traceback ----------------
def eighty_files(tmp_path, size=1000):
    make_tree(tmp_path, {f"G/2024/trip/F{index:03d}.NEF": size for index in range(80)})
    return tmp_path / "G" / "2024", tmp_path / "D"


def final_files(dest):
    return sorted(path.name for path in (dest / "G" / "2024" / "trip").glob("*.NEF"))


def failing_commits(patch, after_rows):
    """The catalog commit raises as on a detached disk once `after_rows` rows
    were recorded (catalog.upsert) in this run."""
    rows = {"n": 0}
    real_upsert = sb._Catalog.upsert
    real_commit = sb._Catalog.commit

    def upsert(self, *args, **kwargs):
        rows["n"] += 1
        return real_upsert(self, *args, **kwargs)

    def commit(self):
        if rows["n"] >= after_rows:
            raise sqlite3.OperationalError("disk I/O error")
        return real_commit(self)
    patch.setattr(sb._Catalog, "upsert", upsert)
    patch.setattr(sb._Catalog, "commit", commit)
    return rows


def counting_copies(patch):
    calls = []
    real = sb._copy_file

    def copy_file(planned, *args, **kwargs):
        calls.append(planned.src)
        return real(planned, *args, **kwargs)
    patch.setattr(sb, "_copy_file", copy_file)
    return calls


@pytest.mark.parametrize("small_files", [False, True])
def test_r1_a_commit_that_fails_in_the_loop_stops_the_backup(tmp_path, monkeypatch, capsys,
                                                            small_files):
    """The periodic commit of the loop (one file at a time) and the one of a
    flushed batch (small files): the copy stops there, the reading too, and the
    summary says how many copies have no row yet."""
    make_tree(tmp_path, {"G/2024/old/gone.NEF": 500})
    source, dest = eighty_files(tmp_path)
    shutil.move(str(source / "trip"), str(tmp_path / "later"))
    assert sb.main(argv_for(source, dest, tmp_path, "--unexported", "copy", "--yes")) == 0
    os.remove(source / "old" / "gone.NEF")
    shutil.move(str(tmp_path / "later"), str(source / "trip"))
    capsys.readouterr()
    with monkeypatch.context() as patch:
        patch.setattr(sb, "READ_AHEAD_MB", 0.002)
        patch.setattr(sb, "FLUSH_BATCH_FILES", 10)
        if not small_files:
            patch.setattr(sb, "SMALL_FILE_KB", 0)
        failing_commits(patch, 20)          # the commit after 10 rows works, after 20 not
        opened = counting_reads(patch)
        copies = counting_copies(patch)
        code = sb.main(argv_for(source, dest, tmp_path, "--unexported", "copy", "--yes",
                                "--move-orphans"))
    out = capsys.readouterr().out
    assert code == 1
    assert "STOPPED:" in out and "60 files" in out, out
    assert "10 copied files have no row in the catalog" in out
    assert "--move-orphans: nothing moved" in out
    assert (dest / "G" / "2024" / "old" / "gone.NEF").exists()
    assert len(final_files(dest)) == 20                 # the copies keep their final names
    assert len(catalog_rows(dest)) == 1 + 10
    assert len(opened) <= 20 + 4                        # the reading stopped too
    if not small_files:
        assert len(copies) == 20                        # nothing more was tried
    assert not list(dest.rglob("*" + sb.TEMP_SUFFIX))
    assert "STOPPED" in latest_log(tmp_path / "logs")
    # the next run adopts the 10 copies without a row and copies the rest
    assert sb.main(argv_for(source, dest, tmp_path, "--unexported", "copy", "--yes")) == 0
    assert "copied 60 files" in capsys.readouterr().out
    assert len(catalog_rows(dest)) == 1 + 80


def test_r1_a_commit_that_fails_after_the_destination_check(tmp_path, monkeypatch, capsys):
    """The review's case: the destination check fails with rows still to commit."""
    source, dest = eighty_files(tmp_path)
    real_open = open

    def guarded_open(path, *args, **kwargs):
        text = os.fspath(path)
        if "dest_check_" in text and text.endswith(sb.TEMP_SUFFIX):
            raise OSError(5, "Access is denied", text)      # the destination check
        return real_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sb, "SMALL_FILE_KB", 0)
        patch.setattr(sb, "DEST_CHECK_AFTER_ERRORS", 20)
        failing_writes(patch, 5)
        failing_commits(patch, 5)
        patch.setattr(sb, "open", guarded_open, raising=False)
        code = sb.main(argv_for(source, dest, tmp_path, "--unexported", "copy", "--yes"))
    out = capsys.readouterr().out
    assert code == 1
    assert "STOPPED:" in out and "tested after 20 failed copies" in out and "55 files" in out
    assert "5 copied files have no row in the catalog" in out
    assert len(final_files(dest)) == 5
    assert catalog_rows(dest) == []


def free_drive_letter():
    for letter in reversed(string.ascii_uppercase[3:]):
        if not os.path.exists(f"{letter}:\\"):
            return letter
    return None


def subst(*args):
    return subprocess.run(["subst", *args], capture_output=True, text=True, errors="replace")


@pytest.mark.skipif(sys.platform != "win32", reason="subst")
def test_r1_a_destination_drive_that_goes_away_for_real(tmp_path, monkeypatch, capsys):
    """The destination on a subst letter, removed after 30 copies: every file is
    recorded one by one (SMALL_FILE_KB = 0), so 30 rows wait for their commit."""
    source, _ = eighty_files(tmp_path, size=64 * 1024)
    target = tmp_path / "drive"
    target.mkdir()
    letter = None
    for _attempt in range(3):
        letter = free_drive_letter()
        if letter is None:
            pytest.skip("no free drive letter")
        if subst(f"{letter}:", str(target)).returncode == 0:
            break
        letter = None
    if letter is None:
        pytest.skip("subst failed")
    removed = {"done": False}
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sb, "SMALL_FILE_KB", 0)
            patch.setattr(sb, "DEST_CHECK_AFTER_ERRORS", 20)
            opened = counting_reads(patch)
            real = sb._copy_file
            calls = {"n": 0}

            def copy_file(*args, **kwargs):
                result = real(*args, **kwargs)
                calls["n"] += 1
                if calls["n"] == 30:
                    assert subst(f"{letter}:", "/d").returncode == 0
                    removed["done"] = True
                return result
            patch.setattr(sb, "_copy_file", copy_file)
            code = sb.main(argv_for(source, f"{letter}:\\dest", tmp_path,
                                    "--unexported", "copy", "--yes"))
    finally:
        if not removed["done"]:
            subst(f"{letter}:", "/d")
    out = capsys.readouterr().out
    assert removed["done"]
    assert code == 1
    assert "STOPPED:" in out
    assert "have no row in the catalog" in out
    assert len(opened) < 80                             # the source was not read to the end
    assert not os.path.exists(f"{letter}:\\")
    assert f"{letter}:\\" not in subst().stdout
    copied = sorted(path.name for path in (target / "dest").rglob("*.NEF"))
    assert 1 <= len(copied) <= 30


# ---- R2: a folder skipped by its name is skipped everywhere -----------------------------------
@pytest.mark.parametrize("originals", ["raw", "raw+jpeg", "all"])
def test_r2_a_bigger_mode_never_copies_less_with_skipped_folders(tmp_path, monkeypatch,
                                                                 originals):
    monkeypatch.setattr(sb, "EXCLUDE_FOLDERS", "Skipme")
    folder = full_tree(tmp_path / "G")
    plans = {name: planned_set(plan_rows([(folder, name, originals)], tmp_path))
             for name in ("1", "2", "3", "4", "5", "6", "1+", "2+", "3+", "4+", "5+")}
    for small, big in CHAINS:
        missing = sorted(plans[small] - plans[big])
        assert not missing, f"mode {small} copies what mode {big} does not: {missing}"


@pytest.mark.parametrize("mode", ["3", "3+", "4", "4+", "5", "5+", "6"])
def test_r2_a_folder_skipped_by_name_inside_an_added_folder(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(sb, "EXCLUDE_FOLDERS", "Skipme")
    folder = full_tree(tmp_path / "G")
    plan = plan_rows([(folder, mode, "all")], tmp_path)
    found = planned_set(plan)
    skipped = {os.path.normcase(path) for path in plan.skipped_folders}
    for path in sorted(folder.rglob("*")):
        if path.is_dir() and path.name.casefold() in ("skipme", "$recycle.bin"):
            assert os.path.normcase(str(path)) in skipped, path
            for item in path.rglob("*"):
                assert os.path.normcase(str(item)) not in found, item


# ---- R3: a destination with an invalid name ---------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="'|' is a valid name elsewhere")
def test_r3_an_invalid_destination_name_is_an_exit_2(tmp_path, capsys):
    make_tree(tmp_path, {"G/2024/trip/DSC_0001.NEF": 100,
                         "G/2024/trip/_EXPORT/DSC_0001_v1.jpg": 50})
    source = tmp_path / "G" / "2024"
    dest = str(tmp_path / "x|y")
    assert sb.main(argv_for(source, dest, tmp_path, "--yes")) == 2
    captured = capsys.readouterr()
    assert "Destination not available:" in captured.err
    assert not os.path.exists(str(tmp_path / "x"))
