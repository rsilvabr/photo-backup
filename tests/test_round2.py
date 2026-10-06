"""Round 2 (261007 review): each test reproduces a failure found on the real
archive or by the adversarial scenarios, and fails against v0.1.0."""
import os
import sqlite3

import selective_backup

from conftest import argv_for, build_base_tree, dest_files, latest_csv, make_tree, parse_args


def _corrupt_keep_mtime(path):
    stat = path.stat()
    data = bytearray(path.read_bytes())
    data[0] ^= 0xFF
    path.write_bytes(bytes(data))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_session_without_originals_does_not_swallow_its_subtree(tmp_path):
    """G:\\2017 holds 171230_Lightroom_Export but no original: its trip folders
    must still be reported (and copied with --unexported copy)."""
    make_tree(tmp_path, {
        "G/2017/171230_Lightroom_Export/IMG_0001.jpg": 50,
        "G/2017/170101_trip/IMG_5000.JPG": 60,
        "G/2017/170202_shoot/IMG_7000.CR2": 70,
        "G/2017/170202_shoot/Lightroom_Export/IMG_7000.jpg": 71,
        "G/2017/170202_shoot/_OUTTAKES/IMG_7001.CR2": 72,
    })
    source = tmp_path / "G" / "2017"
    planned = selective_backup.plan(parse_args(source, tmp_path / "out", tmp_path))
    unexported = {os.path.basename(folder.path) for folder in planned.unexported_folders}
    assert "170101_trip" in unexported
    assert "_OUTTAKES" not in unexported          # a real session still owns its subtree

    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes", "--unexported", "copy")) == 0
    copied = dest_files(dest)
    assert "G/2017/170101_trip/IMG_5000.JPG" in copied
    assert "G/2017/171230_Lightroom_Export/IMG_0001.jpg" in copied
    assert "G/2017/170202_shoot/IMG_7000.CR2" in copied
    assert "G/2017/170202_shoot/_OUTTAKES/IMG_7001.CR2" not in copied


def test_unexported_copy_never_copies_inside_a_session(tmp_path):
    """--unexported copy must not reach _OUTTAKES / OLD_TIFFs (mutation M05)."""
    build_base_tree(tmp_path)
    make_tree(tmp_path, {"G/2025/shoot/_OUTTAKES/day2/_DSC6499.NEF": 80})
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes", "--unexported", "copy")) == 0
    copied = dest_files(dest)
    assert "G/2025/plain/IMG_0001.JPG" in copied
    assert not any("_OUTTAKES" in rel or "OLD_TIFFs" in rel for rel in copied)


def test_verify_full_checks_the_copy_when_the_original_is_gone(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_DSC2.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
        "G/2025/shoot/_EXPORT/x/_DSC2_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    (source / "_DSC1.NEF").unlink()
    (source / "_DSC2.NEF").unlink()
    _corrupt_keep_mtime(dest / "G/2025/shoot/_DSC1.NEF")
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 1
    rows = {line.split(",")[1].replace("\\", "/"): line
            for line in latest_csv(tmp_path / "logs", "_verify.csv").splitlines()[1:]}
    assert rows["G/2025/shoot/_DSC1.NEF"].startswith("DEST_CORRUPT")
    assert rows["G/2025/shoot/_DSC2.NEF"].startswith("SOURCE_MISSING")
    assert "backup copy hash verified" in rows["G/2025/shoot/_DSC2.NEF"]


def test_file_edited_between_planning_and_copy(tmp_path, monkeypatch):
    """The catalog must describe what was copied, not what was planned."""
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_DSC1.xmp": 40,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    real_plan = selective_backup.plan

    def plan_then_edit(args):
        result = real_plan(args)
        make_tree(tmp_path, {"G/2025/shoot/_DSC1.xmp": 400})   # NX Studio saved meanwhile
        return result
    monkeypatch.setattr(selective_backup, "plan", plan_then_edit)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    monkeypatch.setattr(selective_backup, "plan", real_plan)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 0


def test_stale_catalog_row_is_refreshed(tmp_path):
    """A v0.1.0 catalog may hold the planned size; an up-to-date file must fix it."""
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    db = dest / selective_backup.CATALOG_FOLDER / "catalog.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE files SET size = 7 WHERE rel LIKE '%_DSC1.NEF'")
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    with sqlite3.connect(db) as conn:
        size, sha = conn.execute("SELECT size, sha256 FROM files WHERE rel LIKE '%_DSC1.NEF'").fetchone()
    assert size == 100 and sha is None
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 0
    assert "HASH_FILLED" in latest_csv(tmp_path / "logs", "_verify.csv")


def test_read_errors_are_shown_and_fail_a_dry_run(tmp_path, monkeypatch, capsys):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025"
    blocked = os.path.normcase(str(source / "shoot" / "_EXPORT" / "16B_JXL"))
    real_scandir = os.scandir

    def scandir(path):
        if os.path.normcase(str(path)).endswith(blocked):
            raise PermissionError(13, "Access is denied", str(path))
        return real_scandir(path)
    monkeypatch.setattr(selective_backup.os, "scandir", scandir)
    rc = selective_backup.main(argv_for(source, tmp_path / "out", tmp_path, "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "Errors: 1" in out and "NOT backed up" in out


def test_missing_destination_drive_is_a_clean_abort(tmp_path, monkeypatch, capsys):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025"

    def no_drive(path):
        raise FileNotFoundError(3, "The system cannot find the path specified", path)
    monkeypatch.setattr(selective_backup.shutil, "disk_usage", no_drive)
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 2
    assert "Destination not available" in capsys.readouterr().err
    assert not dest.exists()
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--dry-run")) == 0


def test_edit_settings_and_sidecars_are_auto_updated(tmp_path):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC6489.xmp": "rating 5",
        "G/2025/shoot/CaptureOne/Settings166/_DSC6489.NEF.cos": "new crop",
        "G/2025/shoot/NKSC_PARAM/_DSC6491.NEF.nksc": "new curve",
        "G/2025/shoot/_DSC6489.NEF": 999,                         # a RAW is never auto-updated
    })
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    out = dest / "G" / "2025" / "shoot"
    assert (out / "_DSC6489.xmp").read_text() == "rating 5"
    assert (out / "CaptureOne/Settings166/_DSC6489.NEF.cos").read_text() == "new crop"
    assert (out / "NKSC_PARAM/_DSC6491.NEF.nksc").read_text() == "new curve"
    assert (out / "_DSC6489.NEF").stat().st_size == 120
    versions = dest / selective_backup.CATALOG_FOLDER / "versions"
    kept = {p.name for p in versions.rglob("*") if p.is_file()}
    assert {"_DSC6489.xmp", "_DSC6489.NEF.cos", "_DSC6491.NEF.nksc"} <= kept
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 1   # the RAW


def test_no_auto_update_flag(tmp_path, monkeypatch):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    make_tree(tmp_path, {"G/2025/shoot/_DSC6489.xmp": "rating 5"})
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes", "--no-auto-update")) == 0
    assert (dest / "G/2025/shoot/_DSC6489.xmp").read_text() != "rating 5"
    monkeypatch.setattr(selective_backup, "AUTO_UPDATE", False)        # the setting is read
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert (dest / "G/2025/shoot/_DSC6489.xmp").read_text() != "rating 5"
