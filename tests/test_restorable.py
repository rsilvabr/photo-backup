"""v0.12.0: a file never counts as saved when the right content cannot be
restored. Every tree lives in a HOSTILE folder (Japanese, [ ], a space, %)."""
import csv
import os
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import manifest_backup
import restore_backup
import selective_backup

from conftest import argv_for, latest_csv, latest_log, make_tree

EVENT = "G/2025/250118_吾妻山公園_菜の花 [FINAL] 5% off"
TREE = {
    f"{EVENT}/DSC_0001.NEF": 5000,
    f"{EVENT}/DSC_0001.xmp": 400,
    f"{EVENT}/DSC_0002.NEF": 5001,
    f"{EVENT}/_EXPORT/x/DSC_0001_v1.jpg": 300,
    f"{EVENT}/_EXPORT/x/DSC_0002_v1.jpg": 301,
}


def build(tmp_path):
    make_tree(tmp_path, TREE)
    return tmp_path / "G" / "2025", tmp_path / "バックアップ [x] 5%", tmp_path / EVENT


def failing_flush(monkeypatch, name):
    """The flush of the temp whose path holds `name` fails; returns the undo."""
    real_flush = selective_backup._flush_temp

    def flush(tmp):
        if name in tmp:
            raise OSError(5, "I/O error")
        return real_flush(tmp)

    monkeypatch.setattr(selective_backup, "_flush_temp", flush)
    return lambda: monkeypatch.setattr(selective_backup, "_flush_temp", real_flush)


@pytest.mark.parametrize("small_file_kb", [0, 102400])
def test_a_file_edited_after_it_was_read_is_changed_on_the_next_run(tmp_path, monkeypatch,
                                                                    small_file_kb):
    """F1. small_file_kb 0 = one file at a time, 102400 = in a batch."""
    monkeypatch.setattr(selective_backup, "SMALL_FILE_KB", small_file_kb)
    source, dest, event = build(tmp_path)
    victim = event / "DSC_0001.NEF"
    long_ago = time.time() - 30 * 86400
    os.utime(victim, (long_ago, long_ago))
    old_bytes = victim.read_bytes()
    old_mtime = victim.stat().st_mtime_ns
    real_finish = selective_backup._finish_temp
    edited = []

    def finish(planned, tmp, dst, *args, **kwargs):
        # another program rewrites the original, same size, after it was read
        if planned.rel.endswith("DSC_0001.NEF") and not edited:
            victim.write_bytes(b"E" * len(old_bytes))
            edited.append(True)
        return real_finish(planned, tmp, dst, *args, **kwargs)

    monkeypatch.setattr(selective_backup, "_finish_temp", finish)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert edited
    copy = dest / EVENT / "DSC_0001.NEF"
    assert copy.read_bytes() == old_bytes
    assert copy.stat().st_mtime_ns == old_mtime      # the date of the bytes it holds
    monkeypatch.setattr(selective_backup, "_finish_temp", real_finish)
    assert selective_backup.main(
        argv_for(source, dest, tmp_path, "--yes", "--update-changed")) == 0
    assert copy.read_bytes() == victim.read_bytes()
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 0


def test_two_source_files_with_one_destination_path_are_an_error(tmp_path, capsys):
    """F2. NTFS keeps both names; str.casefold() makes them one key."""
    make_tree(tmp_path, {"G/2019/Berlin/Straße.jpg": 500, "G/2019/Berlin/Strasse.jpg": 501})
    assert len(list((tmp_path / "G" / "2019" / "Berlin").iterdir())) == 2
    source, dest = tmp_path / "G" / "2019", tmp_path / "out"
    assert selective_backup.main(
        argv_for(source, dest, tmp_path, "--yes", "--unexported", "copy")) == 1
    log = latest_log(tmp_path / "logs")
    assert "ERROR duplicate" in log and "is NOT backed up" in log
    assert "Errors: 1 " in capsys.readouterr().out
    assert selective_backup.main(
        argv_for(source, dest, tmp_path, "--unexported", "copy", "--verify", "quick")) == 1


def test_the_old_copy_keeps_its_name_when_the_new_copy_fails(tmp_path, monkeypatch):
    """F3. A changed sidecar of another size: the old copy moves to the
    versions folder only when the new one is ready to take its name."""
    source, dest, event = build(tmp_path)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    copy = dest / EVENT / "DSC_0001.xmp"
    old_bytes = copy.read_bytes()
    (event / "DSC_0001.xmp").write_bytes(b"n" * 900)
    versions = dest / selective_backup.CATALOG_FOLDER / "versions"
    undo = failing_flush(monkeypatch, "DSC_0001.xmp")
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 1
    assert copy.read_bytes() == old_bytes
    assert not [path for path in versions.rglob("*") if path.is_file()]
    undo()
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert copy.read_bytes() == b"n" * 900
    kept = [path for path in versions.rglob("*") if path.is_file()]
    assert len(kept) == 1 and kept[0].read_bytes() == old_bytes


def test_a_read_only_temp_left_by_a_crash_does_not_block_the_copy(tmp_path):
    """F4."""
    source, dest, event = build(tmp_path)
    temp = dest / EVENT / ("DSC_0001.NEF" + selective_backup.TEMP_SUFFIX)
    temp.parent.mkdir(parents=True)
    temp.write_bytes(b"half a file")
    os.chmod(temp, stat.S_IREAD)
    try:
        assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
        assert not temp.exists()
        assert (dest / EVENT / "DSC_0001.NEF").read_bytes() == (event / "DSC_0001.NEF").read_bytes()
    finally:
        if temp.exists():
            os.chmod(temp, stat.S_IREAD | stat.S_IWRITE)


def test_copy_errors_are_said_on_the_console(tmp_path, monkeypatch, capsys):
    """F5."""
    source, dest, _event = build(tmp_path)
    failing_flush(monkeypatch, "DSC_0002.NEF")
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 1
    out = capsys.readouterr().out
    assert "ERRORS: 1 files were NOT copied" in out
    assert "copied 4 files" in out


def write_manifest(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("Folder", "Mode", "Originals"))
        writer.writerows(rows)
    return str(path)


def test_manifest_verify_follows_the_manifest(tmp_path, monkeypatch):
    """F6. Mode 2 selects the original that was not exported; --verify must
    miss it when it is not in the backup, and must not report the folder the
    manifest leaves out."""
    make_tree(tmp_path, {
        f"{EVENT}/DSC_0001.NEF": 5000,
        f"{EVENT}/DSC_0002.NEF": 5001,                       # not exported
        f"{EVENT}/_EXPORT/x/DSC_0001_v1.jpg": 300,
        "G/2025/left out/IMG_0001.CR2": 700,
        "G/2025/left out/_EXPORT/IMG_0001_v1.jpg": 70,
    })
    dest = tmp_path / "バックアップ [x] 5%"
    manifest = write_manifest(tmp_path / "manifest.csv", [
        (str(tmp_path / "G" / "2025"), "2", "raw"),
        (str(tmp_path / "G" / "2025" / "left out"), "skip", ""),
    ])
    base = ["--manifest", manifest, "--dest", str(dest), "--space-margin-gb", "0"]
    undo = failing_flush(monkeypatch, "DSC_0002.NEF")
    assert manifest_backup.main(base + ["--yes"]) == 1
    undo()
    assert manifest_backup.main(base + ["--verify", "quick"]) == 1
    rows = [line for line in latest_csv(tmp_path / "logs", "_verify.csv").splitlines()
            if "NOT_BACKED_UP" in line]
    assert len(rows) == 1 and "DSC_0002.NEF" in rows[0]
    assert manifest_backup.main(base + ["--yes"]) == 0
    assert manifest_backup.main(base + ["--verify", "full"]) == 0
    assert "left out" not in latest_csv(tmp_path / "logs", "_verify.csv")
    assert manifest_backup.main(base + ["--analyze", "--verify", "quick"]) == 2
    assert manifest_backup.main(base + ["--dry-run", "--verify", "quick"]) == 2


def test_restore_refuses_a_backup_file_with_the_wrong_size(tmp_path):
    """F7. A row without a hash (adopted) whose backup file lost bytes."""
    source, dest, _event = build(tmp_path)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    conn = sqlite3.connect(str(dest / selective_backup.CATALOG_FOLDER / "catalog.sqlite"))
    conn.execute("UPDATE files SET sha256=NULL")
    conn.commit()
    conn.close()
    damaged = dest / EVENT / "DSC_0001.NEF"
    damaged.write_bytes(damaged.read_bytes()[:10])
    out = tmp_path / "restored"
    assert restore_backup.main(
        ["--backup", str(dest), "--to", str(out), "--yes", "--space-margin-gb", "0"]) == 1
    lines = [line for line in latest_csv(tmp_path / "logs", "_restore.csv").splitlines()
             if "DSC_0001.NEF" in line]
    assert len(lines) == 1 and lines[0].startswith("CORRUPT_IN_BACKUP")
    assert not (out / EVENT / "DSC_0001.NEF").exists()
    assert (out / EVENT / "DSC_0002.NEF").exists()


def folder_bytes(folder):
    return {path.name: path.read_bytes() for path in Path(folder).iterdir() if path.is_file()}


def test_restore_does_not_touch_a_catalog_with_a_pending_journal(tmp_path):
    """F8. A process killed in the middle of a catalog transaction leaves a
    journal; reading the catalog must not roll it back inside the backup."""
    source, dest, event = build(tmp_path)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    folder = dest / selective_backup.CATALOG_FOLDER
    catalog = folder / "catalog.sqlite"
    child = ("import os, sqlite3, sys\n"
             "c = sqlite3.connect(sys.argv[1])\n"
             "c.execute('PRAGMA cache_size=5')\n"
             "c.execute('BEGIN')\n"
             "for i in range(20000):\n"
             "    c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?)',\n"
             "              ('k%d' % i, 'ghost%d' % i, 1, 1, None, 'x', None, None))\n"
             "os._exit(9)\n")
    subprocess.run([sys.executable, "-c", child, str(catalog)])
    assert (folder / "catalog.sqlite-journal").exists()
    before = folder_bytes(folder)
    out = tmp_path / "restored"
    restore = ["--backup", str(dest), "--to", str(out), "--space-margin-gb", "0"]
    assert restore_backup.main(restore + ["--dry-run"]) == 0
    assert folder_bytes(folder) == before
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--dry-run")) == 0
    assert folder_bytes(folder) == before
    assert restore_backup.main(restore + ["--yes"]) == 0
    assert folder_bytes(folder) == before
    assert (out / EVENT / "DSC_0001.NEF").read_bytes() == (event / "DSC_0001.NEF").read_bytes()
    assert "ghost" not in latest_csv(tmp_path / "logs", "_restore.csv")
    # the next backup run, which may write, recovers the catalog itself
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert not (folder / "catalog.sqlite-journal").exists()
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 0
