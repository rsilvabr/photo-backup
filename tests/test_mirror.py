"""The mirror (mirror_backup.py): what leaves the backup and what never does,
the trash, the locks, the typed confirmations and the screen."""
import collections
import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import manifest_backup as mb
import mirror_backup as mir
import selective_backup as sb

from conftest import latest_csv, latest_log, make_tree, snapshot

HOSTILE = "吾妻山公園_菜の花 [FINAL] 5% off"
TRASH = f"{sb.CATALOG_FOLDER}/{mir.MIRROR_TRASH_FOLDER}"


@pytest.fixture(autouse=True)
def closed_console(monkeypatch):
    """No test may wait for an answer: a question without a scripted answer
    is a closed console."""
    def closed(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", closed)


def answers(monkeypatch, *replies):
    queue = list(replies)

    def fake(prompt=""):
        if not queue:
            raise EOFError
        return queue.pop(0)
    monkeypatch.setattr("builtins.input", fake)
    return queue


def mirror(*argv):
    return mir.main([str(item) for item in argv])


def write_manifest(path, *lines, mirror_column=True):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Folder", "Mode", "Originals"] + (["Mirror"] if mirror_column else []))
        writer.writerows(lines)
    return path


def source_tree(root):
    return make_tree(root / "SRC", {
        "A/trip/DSC_0001.NEF": 1001, "A/trip/DSC_0002.NEF": 1002, "A/trip/DSC_0003.NEF": 1003,
        "A/trip/DSC_0004.NEF": 1004, "A/trip/DSC_0005.NEF": 1005, "A/trip/DSC_0006.NEF": 1006,
        "A/day/IMG_0001.JPG": 2001, "A/day/IMG_0002.JPG": 2002,
        "B/party/DSC_0100.NEF": 3001, "B/party/DSC_0101.NEF": 3002,
    })


@pytest.fixture
def env(tmp_path):
    """A source with two manifest lines (A, B, mode 6 = the whole folder),
    mirrored once into `dest`."""
    root = tmp_path / HOSTILE
    src = source_tree(root)
    dest = root / "dest"
    man = write_manifest(root / "manifest.csv", (src / "A", "6", "", ""), (src / "B", "6", "", ""))
    assert mirror("--manifest", man, "--dest", dest, "--yes") == 0
    return SimpleNamespace(root=root, src=src, dest=dest, man=man)


def _after_src(rel):
    return rel.replace("\\", "/").split("/SRC/", 1)[1]


def backed(dest):
    """The backup's files outside _photo_backup, relative to SRC."""
    result = set()
    for dirpath, _dirs, names in os.walk(dest):
        rel_dir = os.path.relpath(dirpath, dest).replace("\\", "/")
        if rel_dir.split("/")[0] == sb.CATALOG_FOLDER:
            continue
        result |= {_after_src(f"{rel_dir}/{name}") for name in names if "/SRC/" in f"/{rel_dir}/"}
    return result


def trashed(dest):
    """The files in the trash (every run), relative to SRC."""
    root = Path(dest) / sb.CATALOG_FOLDER / mir.MIRROR_TRASH_FOLDER
    if not root.exists():
        return set()
    return {_after_src(str(path)) for path in root.rglob("*") if path.is_file()}


def catalogued(dest):
    connection = sqlite3.connect(str(Path(dest) / sb.CATALOG_FOLDER / "catalog.sqlite"))
    try:
        return {_after_src(rel) for (rel,) in connection.execute("SELECT rel FROM files")}
    finally:
        connection.close()


def in_backup(env, rel):
    """The backup's copy of SRC/<rel>."""
    drive_root = os.path.splitdrive(str(env.src))[0] + os.sep
    return env.dest / os.path.relpath(str(env.src), drive_root) / rel


def actions(log_dir):
    return list(csv.DictReader(io.StringIO(latest_csv(log_dir, "_actions.csv"))))


def mirror_text(out):
    """The lines the mirror itself prints: its boxes (a rule, a title, a rule,
    the body, a rule) and its questions (from '!!' to the line asking for the
    number). The backup's own summary is not the mirror's."""
    lines = out.splitlines()
    rule = "=" * 80
    result = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if (line == mir._top_rule() and index + 1 < len(lines)
                and lines[index + 1].startswith((" MIRROR", " TRASH", " EMPTY"))):
            end, rules = index + 1, 1
            while end < len(lines) and rules < 3:
                rules += lines[end] == rule
                end += 1
            result += lines[index:end]
            index = end
        elif line.lstrip().startswith("!!"):
            end = index
            while end < len(lines) and not lines[end].startswith(("To go on", "Choose")):
                end += 1
            result += lines[index:end + 1]
            index = end + 1
        else:
            index += 1
    return result


def make_trash_run(dest, when, files):
    run = Path(dest) / sb.CATALOG_FOLDER / mir.MIRROR_TRASH_FOLDER / when.strftime("%y%m%d_%H%M%S")
    for rel, size in files.items():
        path = run / "SRC" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"t" * size)
    return run


# ---- T1, T2: the manifest -------------------------------------------------------------------
def test_t1_the_mirror_column(tmp_path):
    root = tmp_path / HOSTILE
    src = source_tree(root)
    modes = mb.read_modes(mir.build_parser().get_default("modes"))
    old = write_manifest(root / "old.csv", (src / "A", "6", ""), mirror_column=False)
    assert mb.read_manifest(old, modes)[0].mirror == mb.DEFAULT_MIRROR
    selection = write_manifest(root / "sel.csv", (src / "A", "6", "", "Selection"))
    assert mb.read_manifest(selection, modes)[0].mirror == "selection"
    bad = write_manifest(root / "bad.csv", (src / "A", "6", "", "maybe"))
    with pytest.raises(mb.ManifestError, match="Mirror must be one of"):
        mb.read_manifest(bad, modes)
    assert mirror("--manifest", bad, "--dest", root / "d", "--dry-run") == 2
    assert mb.main(["--manifest", str(bad), "--dest", str(root / "d"), "--dry-run"]) == 2
    # manifest_backup.py reads the column and plans exactly the same
    parsed = mb.build_parser().parse_args(["--manifest", str(old), "--dest", str(root / "d")])
    rows_old = mb.read_manifest(old, modes)
    args = mb._backend_args(parsed, rows_old, str(root / "d"))
    plan_old = mb._plan_rows(rows_old, modes, args, {})
    plan_new = mb._plan_rows(mb.read_manifest(selection, modes), modes, args, {})
    assert [item.rel for item in plan_old.files] == [item.rel for item in plan_new.files]
    assert mb.main(["--manifest", str(selection), "--dest", str(root / "d"), "--dry-run"]) == 0


def test_t2_on_row_sees_every_line(tmp_path):
    root = tmp_path / HOSTILE
    src = source_tree(root)
    modes = mb.read_modes(mir.build_parser().get_default("modes"))
    man = write_manifest(root / "m.csv", (src / "A", "6", "", ""), (src / "B", "6", "", ""))
    rows = mb.read_manifest(man, modes)
    parsed = mb.build_parser().parse_args(["--manifest", str(man), "--dest", str(root / "d")])
    calls = []
    mb._plan_rows(rows, modes, mb._backend_args(parsed, rows, str(root / "d")), {},
                  on_row=lambda row, planner: calls.append((row.folder, len(planner.files))))
    assert calls == [(rows[0].folder, 8), (rows[1].folder, 2)]


# ---- T3-T13: what leaves and what never does ---------------------------------------------
def test_t3_gone_file_goes_to_the_trash(env, capsys):
    (env.src / "A/trip/DSC_0001.NEF").unlink()
    for name in ("IMG_0001.JPG", "IMG_0002.JPG"):
        (env.src / "A/day" / name).unlink()
    before_dest = snapshot(env.dest)
    capsys.readouterr()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "3 files (5.0 KB) will be removed from the backup: to the trash until" in out
    assert "Dry-run: nothing was changed in the backup." in out
    assert snapshot(env.dest) == before_dest
    before_src = snapshot(env.src)
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert snapshot(env.src) == before_src
    gone = {"A/trip/DSC_0001.NEF", "A/day/IMG_0001.JPG", "A/day/IMG_0002.JPG"}
    assert trashed(env.dest) == gone
    assert not gone & backed(env.dest)
    assert not gone & catalogued(env.dest)
    assert not in_backup(env, "A/day").exists()          # left empty by the removal
    assert not (env.dest / sb.CATALOG_FOLDER / mir.MIRROR_LOCK_FILE).exists()
    out = capsys.readouterr().out
    assert "MIRROR DONE" in out and "3 removed from the backup" in out


def test_t4_scoped_keeps_folders_outside_the_run_total_removes_them(env, monkeypatch, capsys):
    only_a = write_manifest(env.root / "a.csv", (env.src / "A", "6", "", ""))
    assert mirror("--manifest", only_a, "--dest", env.dest, "--yes") == 0
    assert {"B/party/DSC_0100.NEF", "B/party/DSC_0101.NEF"} <= backed(env.dest)
    assert trashed(env.dest) == set()
    before = snapshot(env.dest)
    assert mirror("--manifest", only_a, "--dest", env.dest, "--total", "--unattended") == 2
    answers(monkeypatch, "3")                            # a wrong number
    assert mirror("--manifest", only_a, "--dest", env.dest, "--total", "--yes") == 2
    assert snapshot(env.dest) == before
    assert "MIRROR STOPPED - nothing changed" in capsys.readouterr().out
    answers(monkeypatch, "2")
    assert mirror("--manifest", only_a, "--dest", env.dest, "--total", "--yes") == 0
    assert trashed(env.dest) == {"B/party/DSC_0100.NEF", "B/party/DSC_0101.NEF"}


def test_t5_a_lower_mode_disk_keeps_selection_removes(env, monkeypatch):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    disk = write_manifest(env.root / "disk.csv", (env.src / "A", "jpeg-only", "", ""),
                          (env.src / "B", "6", "", ""))
    assert mirror("--manifest", disk, "--dest", env.dest, "--yes") == 0
    assert "A/trip/DSC_0001.NEF" in backed(env.dest) and trashed(env.dest) == set()
    selection = write_manifest(env.root / "sel.csv", (env.src / "A", "jpeg-only", "", "selection"),
                               (env.src / "B", "6", "", ""))
    assert mirror("--manifest", selection, "--dest", env.dest, "--yes") == 0
    assert trashed(env.dest) == {f"A/trip/DSC_000{index}.NEF" for index in range(1, 7)}
    assert {"A/day/IMG_0001.JPG", "A/day/IMG_0002.JPG"} <= backed(env.dest)


def test_t6_a_source_that_cannot_be_read_never_leaves(env, monkeypatch):
    gone = env.src / "A/trip/DSC_0002.NEF"
    gone.unlink()
    real = sb._source_stat

    def stat(path):
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(str(gone)):
            return sb.SOURCE_UNREADABLE, None
        return real(path)
    monkeypatch.setattr(sb, "_source_stat", stat)
    for extra in ([], ["--total"]):
        assert mirror("--manifest", env.man, "--dest", env.dest, "--yes", *extra) == 0
        assert "A/trip/DSC_0002.NEF" in backed(env.dest)
    assert trashed(env.dest) == set()


def test_t7_another_source_leaves_only_with_total(tmp_path, monkeypatch):
    root = tmp_path / HOSTILE
    one = make_tree(root / "disk1", {"2025/x/DSC_1.NEF": 100, "2025/x/DSC_2.NEF": 101})
    two = make_tree(root / "disk2", {"2025/x/DSC_1.NEF": 100})
    dest = root / "dest"
    assert mirror("--source", one / "2025", "--base", one, "--dest", dest,
                  "--unexported", "copy", "--yes") == 0
    assert mirror("--source", two / "2025", "--base", two, "--dest", dest,
                  "--unexported", "copy", "--yes") == 0
    assert (dest / "2025/x/DSC_2.NEF").exists()
    answers(monkeypatch, "1")
    assert mirror("--source", two / "2025", "--base", two, "--dest", dest,
                  "--unexported", "copy", "--total", "--yes") == 0
    assert not (dest / "2025/x/DSC_2.NEF").exists()
    assert (dest / "2025/x/DSC_1.NEF").exists()


def test_t8_never_behind_a_junction(env, tmp_path, photobackup_log_dir):
    day = env.src / "A/day"
    for path in day.iterdir():
        path.unlink()
    day.rmdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(day), str(target)],
                          capture_output=True)
    if made.returncode != 0:
        pytest.skip("cannot make a junction here")
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert {"A/day/IMG_0001.JPG", "A/day/IMG_0002.JPG"} <= backed(env.dest)
    notes = {row["note"] for row in actions(photobackup_log_dir) if row["action"] == "keep"}
    assert "behind a link the backup does not follow" in notes


def _unreadable_day(monkeypatch):
    real = sb._Planner._scan_dir

    def scan(self, path):
        if os.path.basename(path) == "day":
            self.errors.append(f"ERROR scan {path}: [WinError 5] Access is denied")
            self.stats.scan_errors += 1
            return [], [], []
        return real(self, path)
    monkeypatch.setattr(sb._Planner, "_scan_dir", scan)


def test_t9_read_errors_block_their_line_only(env, monkeypatch, capsys):
    (env.src / "A/trip/DSC_0003.NEF").unlink()
    (env.src / "B/party/DSC_0100.NEF").unlink()
    _unreadable_day(monkeypatch)
    # without a manifest, an error anywhere blocks every removal
    assert mirror("--source", env.src / "A", "--source", env.src / "B", "--dest", env.dest,
                  "--unexported", "copy", "--yes") == 1
    assert trashed(env.dest) == set()
    capsys.readouterr()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 1
    assert "A/trip/DSC_0003.NEF" in backed(env.dest)
    assert trashed(env.dest) == {"B/party/DSC_0100.NEF"}
    assert "READ ERRORS: these lines remove nothing" in capsys.readouterr().out


def test_t10_a_copy_changed_by_hand_stays(env, photobackup_log_dir):
    (env.src / "A/trip/DSC_0004.NEF").unlink()
    copy = in_backup(env, "A/trip/DSC_0004.NEF")
    with open(copy, "ab") as handle:
        handle.write(b"mine")
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert copy.read_bytes().endswith(b"mine")
    kept = [row for row in actions(photobackup_log_dir) if row["rel"].endswith("DSC_0004.NEF")]
    assert kept[0]["action"] == "keep" and kept[0]["note"] == "changed in the backup by hand"


def test_t11_files_the_backup_did_not_write_are_never_touched(env, photobackup_log_dir):
    touched = in_backup(env, "A/trip/notes.txt")
    touched.write_text("mine", encoding="utf-8")
    elsewhere = in_backup(env, "A/old/notes.txt")
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("mine too", encoding="utf-8")
    (env.src / "A/trip/DSC_0005.NEF").unlink()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert touched.read_text(encoding="utf-8") == "mine"
    assert elsewhere.read_text(encoding="utf-8") == "mine too"
    strays = [row["rel"].replace("\\", "/") for row in actions(photobackup_log_dir)
              if row["action"] == "stray"]
    assert len(strays) == 1 and strays[0].endswith("A/trip/notes.txt")


def test_t12_a_changed_file_old_copy_to_the_trash_identical_kept_nowhere(env):
    changed = env.src / "A/trip/DSC_0006.NEF"
    changed.write_bytes(b"new content" * 50)
    same_bytes = env.src / "B/party/DSC_0101.NEF"
    later = same_bytes.stat().st_mtime + 3600
    os.utime(same_bytes, (later, later))
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert trashed(env.dest) == {"A/trip/DSC_0006.NEF"}
    assert in_backup(env, "A/trip/DSC_0006.NEF").read_bytes() == b"new content" * 50
    assert not (env.dest / sb.CATALOG_FOLDER / "versions").exists()


def test_t13_a_changed_file_the_backup_did_not_write_goes_to_versions(env):
    (env.src / "A/trip/DSC_0007.NEF").write_bytes(b"s" * 1500)
    mine = in_backup(env, "A/trip/DSC_0007.NEF")
    mine.write_bytes(b"m" * 700)
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    versions = list((env.dest / sb.CATALOG_FOLDER / "versions").rglob("DSC_0007.NEF"))
    assert len(versions) == 1 and versions[0].read_bytes() == b"m" * 700
    assert trashed(env.dest) == set()
    assert mine.read_bytes() == b"s" * 1500


# ---- T14-T15: the trash -------------------------------------------------------------------------
def test_t14_no_trash_with_keep_days_0(env, monkeypatch):
    monkeypatch.setattr(mir, "MIRROR_KEEP_DAYS", 0)
    make_trash_run(env.dest, datetime(2026, 1, 1), {"old/x.NEF": 10})
    (env.src / "A/trip/DSC_0001.NEF").unlink()
    (env.src / "A/trip/DSC_0002.NEF").write_bytes(b"n" * 1500)
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert "A/trip/DSC_0001.NEF" not in backed(env.dest)
    assert in_backup(env, "A/trip/DSC_0002.NEF").read_bytes() == b"n" * 1500
    trash = env.dest / sb.CATALOG_FOLDER / mir.MIRROR_TRASH_FOLDER
    assert not any(path.is_file() for path in trash.rglob("*"))
    assert not (env.dest / sb.CATALOG_FOLDER / "versions").exists()


def test_t15_the_trash_expires_and_a_foreign_folder_stays(env, capsys):
    now = datetime.now()
    old = make_trash_run(env.dest, now - timedelta(days=40), {"A/x.NEF": 10})
    recent = make_trash_run(env.dest, now - timedelta(days=1), {"A/y.NEF": 20})
    foreign = env.dest / sb.CATALOG_FOLDER / mir.MIRROR_TRASH_FOLDER / "not_a_run"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("keep", encoding="utf-8")
    capsys.readouterr()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--dry-run") == 0
    assert "trash older than 30 days: deleted for good" in capsys.readouterr().out
    assert old.exists()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert not old.exists() and recent.exists() and (foreign / "keep.txt").exists()


# ---- T16: the limit per line ------------------------------------------------------------------
def test_t16_the_limit_per_line(env, monkeypatch, capsys):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_MIN", 1)
    for index in (1, 2, 3):
        (env.src / f"A/trip/DSC_000{index}.NEF").unlink()
    before = snapshot(env.dest)
    capsys.readouterr()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--unattended") == 2
    out = capsys.readouterr().out
    assert "CHECK: these lines would remove or replace more than 20% of their files" in out
    assert "MIRROR STOPPED - nothing changed" in out
    answers(monkeypatch, "2")
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 2
    assert snapshot(env.dest) == before
    answers(monkeypatch, "3")
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert len(trashed(env.dest)) == 3
    for index in (4, 5):
        (env.src / f"A/trip/DSC_000{index}.NEF").unlink()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--unattended",
                  "--allow-mass-delete") == 0
    assert len(trashed(env.dest)) == 5


def test_t16b_the_question_without_yes(env, monkeypatch, capsys):
    (env.src / "A/trip/DSC_0001.NEF").unlink()
    before = snapshot(env.dest)
    answers(monkeypatch, "")                     # Enter = no
    assert mirror("--manifest", env.man, "--dest", env.dest) == 2
    assert snapshot(env.dest) == before
    seen = []

    def reply(prompt=""):
        seen.append(prompt)
        return "y"
    monkeypatch.setattr("builtins.input", reply)
    assert mirror("--manifest", env.man, "--dest", env.dest) == 0
    assert seen == ["Mirror now? copy 0, replace 0, remove 1 [y/N, Enter = no] "]


# ---- T17-T19: space and the trash on demand ----------------------------------------------------
Usage = collections.namedtuple("Usage", "total used free")


def test_t17_not_enough_space(env, monkeypatch, capsys):
    now = datetime.now()
    first = make_trash_run(env.dest, now - timedelta(days=5), {"A/x.NEF": 5000})
    second = make_trash_run(env.dest, now - timedelta(days=1), {"A/y.NEF": 7000})
    (env.src / "A/trip/NEW.NEF").write_bytes(b"n" * 3000)
    monkeypatch.setattr(sb.shutil, "disk_usage", lambda path: Usage(10 ** 9, 0, 1000))
    common = ("--manifest", env.man, "--dest", env.dest, "--space-margin-gb", "0")
    capsys.readouterr()
    assert mirror(*common, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "NOT ENOUGH SPACE" in out and "<- recommended" in out
    assert "1) the oldest trash" in out and "2) the whole trash" in out
    before = snapshot(env.dest)
    assert mirror(*common, "--unattended") == 2
    answers(monkeypatch, "0")
    assert mirror(*common, "--yes") == 2
    answers(monkeypatch, "1", "2")               # option 1, a wrong number
    assert mirror(*common, "--yes") == 2
    assert snapshot(env.dest) == before
    answers(monkeypatch, "1", "1")
    assert mirror(*common, "--yes") == 0
    assert not first.exists() and second.exists()
    assert in_backup(env, "A/trip/NEW.NEF").exists()
    monkeypatch.setattr(sb.shutil, "disk_usage", lambda path: Usage(10 ** 9, 0, 10))
    (env.src / "A/trip/BIG.NEF").write_bytes(b"b" * 50000)
    assert mirror(*common, "--yes") == 2          # nothing can make it fit


def test_t18_this_run_without_the_trash(env, monkeypatch):
    (env.src / "A/trip/DSC_0001.NEF").unlink()
    (env.src / "A/trip/NEW.NEF").write_bytes(b"n" * 1500)
    monkeypatch.setattr(sb.shutil, "disk_usage", lambda path: Usage(10 ** 9, 0, 600))
    answers(monkeypatch, "1", "1")
    assert mirror("--manifest", env.man, "--dest", env.dest, "--space-margin-gb", "0",
                  "--yes") == 0
    assert "A/trip/DSC_0001.NEF" not in backed(env.dest)
    assert trashed(env.dest) == set()
    assert in_backup(env, "A/trip/NEW.NEF").exists()


def test_t19_show_and_empty_the_trash(env, monkeypatch, capsys):
    first = make_trash_run(env.dest, datetime(2026, 10, 1, 8), {"A/x.NEF": 10})
    second = make_trash_run(env.dest, datetime(2026, 11, 1, 8), {"A/y.NEF": 20, "A/z.NEF": 30})
    capsys.readouterr()
    assert mirror("--dest", env.dest, "--show-trash") == 0
    out = capsys.readouterr().out
    assert "To get a file back, copy it from the folder of its run" in out
    assert str(second) in out and "2026-11-01 08:00" in out
    assert mirror("--dest", env.dest, "--empty-trash", "--dry-run") == 0
    answers(monkeypatch, "5")                   # 3 files: a wrong number
    assert mirror("--dest", env.dest, "--empty-trash") == 2
    assert mirror("--dest", env.dest, "--empty-trash", "--unattended") == 2
    assert first.exists() and second.exists()
    answers(monkeypatch, "1")
    assert mirror("--dest", env.dest, "--empty-trash", "--before", "2026-10-15") == 0
    assert not first.exists() and second.exists()
    answers(monkeypatch, "2")
    assert mirror("--dest", env.dest, "--empty-trash") == 0
    assert not second.exists()
    capsys.readouterr()
    assert mirror("--dest", env.dest, "--show-trash") == 0
    assert "The trash is empty." in capsys.readouterr().out


# ---- T20-T24: the lock, moves, without a manifest, what was shown, nothing to do ------------
def test_t20_one_run_at_a_time(env, photobackup_log_dir):
    lock = env.dest / sb.CATALOG_FOLDER / mir.MIRROR_LOCK_FILE
    lock.write_text(mir._lock_text(), encoding="utf-8")       # this live process
    (env.src / "A/trip/DSC_0001.NEF").unlink()
    before = snapshot(env.dest)
    assert mirror("--manifest", env.man, "--dest", env.dest, "--dry-run") == 2
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 2
    assert snapshot(env.dest) == before
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    lock.write_text(json.dumps({"pid": finished.pid, "created": 1, "started": "x"}),
                    encoding="utf-8")
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert "LOCK taken over" in latest_log(photobackup_log_dir)
    assert not lock.exists()
    assert trashed(env.dest) == {"A/trip/DSC_0001.NEF"}


def test_t21_a_renamed_folder_is_moved_not_removed(env, photobackup_log_dir):
    os.rename(env.src / "A/trip", env.src / "A/trip2")
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert trashed(env.dest) == set()
    assert "A/trip2/DSC_0001.NEF" in backed(env.dest)
    assert "A/trip/DSC_0001.NEF" not in backed(env.dest)
    assert "MOVE " in latest_log(photobackup_log_dir)


def test_t22_without_a_manifest_and_usage_errors(env, monkeypatch):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    plain = ("--source", env.src / "A", "--dest", env.dest)
    assert mirror(*plain, "--unexported", "copy", "--yes") == 0
    assert mirror(*plain, "--unexported", "skip", "--mirror", "disk", "--yes") == 0
    assert trashed(env.dest) == set()
    assert mirror(*plain, "--unexported", "skip", "--mirror", "selection", "--yes") == 0
    assert len(trashed(env.dest)) == 8
    assert mirror(*plain, "--move-orphans") == 2
    assert mirror(*plain, "--verify", "quick") == 2
    assert mirror("--manifest", env.man, "--dest", env.dest, "--mirror", "selection") == 2
    assert mirror("--manifest", env.man, *plain) == 2
    assert mirror("--dest", env.dest, "--before", "2026-01-01") == 2
    assert mirror("--dest", env.dest, "--empty-trash", "--before", "01/01/2026") == 2
    assert mirror("--dest", env.dest, "--show-trash", "--empty-trash") == 2


def test_t22b_a_bad_setting_stops_before_anything(env, monkeypatch):
    for name, value in (("MIRROR_KEEP_DAYS", -1), ("MIRROR_TRASH_FOLDER", "versions"),
                        ("MIRROR_TRASH_FOLDER", "..\\x"), ("MIRROR_MAX_DELETE_PERCENT", float("nan"))):
        with monkeypatch.context() as patch:
            patch.setattr(mir, name, value)
            assert mirror("--manifest", env.man, "--dest", env.dest, "--dry-run") == 2


def test_t23_only_what_the_summary_showed(env, monkeypatch):
    (env.src / "A/trip/DSC_0001.NEF").unlink()
    real_confirm = mir.MirrorJob.confirm

    def confirm(self):
        (env.src / "A/trip/DSC_0002.NEF").unlink()           # gone after the summary
        return real_confirm(self)
    monkeypatch.setattr(mir.MirrorJob, "confirm", confirm)
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert trashed(env.dest) == {"A/trip/DSC_0001.NEF"}
    assert "A/trip/DSC_0002.NEF" in backed(env.dest)
    monkeypatch.setattr(mir.MirrorJob, "confirm", real_confirm)
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert "A/trip/DSC_0002.NEF" in trashed(env.dest)


def test_t24_nothing_to_do(env, capsys):
    capsys.readouterr()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    out = capsys.readouterr().out
    assert "Nothing will be removed from the backup." in out
    assert "nothing removed from the backup. No errors." in out


# ---- T26, T27: a junction as the trash, the screen ---------------------------------------------
def test_t26_a_trash_that_is_a_junction_is_never_used(env, tmp_path):
    target = tmp_path / "planted"
    old_run = target / (datetime.now() - timedelta(days=60)).strftime("%y%m%d_%H%M%S")
    old_run.mkdir(parents=True)
    (old_run / "precious.NEF").write_bytes(b"p" * 10)
    trash = env.dest / sb.CATALOG_FOLDER / mir.MIRROR_TRASH_FOLDER
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(trash), str(target)],
                          capture_output=True)
    if made.returncode != 0:
        pytest.skip("cannot make a junction here")
    (env.src / "A/trip/DSC_0001.NEF").unlink()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 2
    assert (old_run / "precious.NEF").exists()
    assert "A/trip/DSC_0001.NEF" in backed(env.dest)
    assert sorted(path.name for path in target.iterdir()) == [old_run.name]
    assert mirror("--dest", env.dest, "--empty-trash") == 2
    assert (old_run / "precious.NEF").exists()


def test_t27_the_screen_is_easy_to_read(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_MIN", 1)
    root = tmp_path / HOSTILE
    long_name = "Viagem " + "muito longa " * 9 + "[FINAL]"
    src = make_tree(root / "SRC", {f"A/{long_name}/DSC_{index:04d}.NEF": 1000 + index
                                   for index in range(1, 5)})
    dest = root / "dest"
    man = write_manifest(root / "m.csv", (src / "A", "6", "", ""))
    assert mirror("--manifest", man, "--dest", dest, "--yes") == 0
    for index in (1, 2):
        (src / f"A/{long_name}/DSC_{index:04d}.NEF").unlink()
    drive_root = os.path.splitdrive(str(src))[0] + os.sep
    stray = dest / os.path.relpath(str(src), drive_root) / "A" / long_name / "notes.txt"
    stray.write_text("mine", encoding="utf-8")
    capsys.readouterr()
    assert mirror("--manifest", man, "--dest", dest, "--dry-run") == 0
    answers(monkeypatch, "2")
    assert mirror("--manifest", man, "--dest", dest, "--yes") == 0
    assert mirror("--dest", dest, "--show-trash") == 0
    out = capsys.readouterr().out
    mine = mirror_text(out)
    assert any("CHECK BEFORE GOING ON" in line for line in mine)
    assert any("MIRROR DONE" in line for line in mine) and any("TRASH OF" in line for line in mine)
    too_long = [line for line in mine if len(re.sub(r"[A-Za-z]:\\.*$", "", line)) > 100]
    assert not too_long, too_long
    assert out.count("_actions.csv") >= 3          # the dry-run, the run's summary and its end
    assert all(line.count("_actions.csv") <= 1 for line in out.splitlines())
    assert mir._n(1234567) == "1,234,567" and mir._files(1) == "1 file"
    assert mir._short("C:\\a\\b\\very long folder\\Dia 2", 20).endswith("Dia 2")


# ---- v0.14.0-rc1: the program's name and version, and the catalog's record ---------------------
def test_t28_the_version_is_on_the_screen_and_in_the_catalog(env, monkeypatch, capsys):
    top = mir._top_rule()
    assert top.startswith(f"== mirror_backup {mir.__version__} ") and len(top) == 80
    monkeypatch.setattr(sys, "argv", [str(Path(mir.__file__))])       # run as mirror_backup.py
    capsys.readouterr()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--dry-run") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"mirror_backup {sb.__version__}"
    assert top in out
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    try:
        meta = dict(connection.execute("SELECT k, v FROM meta"))
    finally:
        connection.close()
    # the fixture's first mirror ran in-process (argv: pytest): the default name
    assert meta["created_by"] == meta["updated_by"] == f"selective_backup {sb.__version__}"
    assert meta["created_at"] and meta["updated_at"]
    assert f"catalog created by selective_backup {sb.__version__}" in out
    (env.src / "A/trip/DSC_0001.NEF").unlink()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    try:
        meta = dict(connection.execute("SELECT k, v FROM meta"))
    finally:
        connection.close()
    assert meta["created_by"] == f"selective_backup {sb.__version__}"
    assert meta["updated_by"] == f"mirror_backup {sb.__version__}"


def test_t29_a_catalog_from_before_says_so(tmp_path):
    dest = tmp_path / "dest"
    catalog = sb._Catalog(str(dest), create=True)
    catalog.conn.execute("DELETE FROM meta WHERE k != 'schema_version'")
    catalog.conn.execute("INSERT INTO files(key, rel, size, mtime_ns, sha256, source_path, "
                         "copied_at, verified_at) VALUES('a', 'a', 1, 1, NULL, 'x', "
                         "'2026-10-01T08:00:00', NULL)")
    catalog.commit()
    catalog.close()
    before = (dest / sb.CATALOG_FOLDER / "catalog.sqlite").read_bytes()
    reader = sb._Catalog(str(dest), create=False, readonly=True)
    assert reader.writers_line() == ""
    reader.close()
    assert (dest / sb.CATALOG_FOLDER / "catalog.sqlite").read_bytes() == before   # read-only
    writer = sb._Catalog(str(dest), create=False)
    writer.commit()                              # nothing changed: nothing recorded
    writer.close()
    assert (dest / sb.CATALOG_FOLDER / "catalog.sqlite").read_bytes() == before
    writer = sb._Catalog(str(dest), create=False)
    writer.set_verified("a", "2026-11-19T08:00:00")
    writer.commit()
    writer.close()
    reader = sb._Catalog(str(dest), create=False, readonly=True)
    assert reader.writers["created_by"] == "an earlier version (before 0.14.0)"
    assert reader.writers["created_at"] == "2026-10-01T08:00:00"
    assert reader.writers["updated_by"].endswith(sb.__version__)
    reader.close()
