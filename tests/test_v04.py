"""v0.4.0 features: moved/renamed files, orphans, backup identity, rolling verify."""
import csv
import json
import os
import re
import sqlite3

import pytest

import selective_backup

from conftest import (
    argv_for,
    latest_csv,
    latest_log,
    make_tree,
    snapshot,
)


def catalog_rows(dest):
    database = dest / selective_backup.CATALOG_FOLDER / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT key, rel, verified_at FROM files ORDER BY rel").fetchall()


def test_renamed_session_is_moved_not_copied(tmp_path, monkeypatch):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_DSC1.xmp": 40,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
        "G/2025/shoot/_DSC2.NEF": 120,
        "G/2025/shoot/_EXPORT/x/_DSC2_v1.jpg": 60,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    os.rename(tmp_path / "G/2025/shoot", tmp_path / "G/2025/shoot [FINAL]")
    calls = []
    real_copy = selective_backup._copy_file

    def counting_copy(planned, dst, verify, **kwargs):
        calls.append(planned.rel)
        return real_copy(planned, dst, verify, **kwargs)

    monkeypatch.setattr(selective_backup, "_copy_file", counting_copy)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert calls == []
    renamed = dest / "G/2025/shoot [FINAL]"
    assert (renamed / "_DSC1.NEF").exists()
    assert (renamed / "_EXPORT/x/_DSC2_v1.jpg").exists()
    assert not (dest / "G/2025/shoot").exists()
    text = latest_log(tmp_path / "logs")
    assert "MOVE G\\2025\\shoot\\_DSC1.NEF -> G\\2025\\shoot [FINAL]\\_DSC1.NEF" in text
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 0


def test_move_rejected_when_content_differs(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    old_copy = dest / "G/2025/shoot/_DSC1.NEF"
    old_bytes = old_copy.read_bytes()
    patched = tmp_path / "G/2025/shoot/_DSC1.NEF"
    data = bytearray(patched.read_bytes())
    data[0] ^= 0xFF
    patched.write_bytes(bytes(data))
    os.rename(tmp_path / "G/2025/shoot", tmp_path / "G/2025/shoot [FINAL]")
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    text = latest_log(tmp_path / "logs")
    assert "MOVE REJECTED G\\2025\\shoot [FINAL]\\_DSC1.NEF" in text
    assert old_copy.read_bytes() == old_bytes
    assert (dest / "G/2025/shoot [FINAL]/_DSC1.NEF").read_bytes() == bytes(data)


def test_corrupt_backup_copy_is_not_moved(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    old_copy = dest / "G/2025/shoot/_DSC1.NEF"
    stat = old_copy.stat()
    data = bytearray(old_copy.read_bytes())
    data[0] ^= 0xFF
    old_copy.write_bytes(bytes(data))
    os.utime(old_copy, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.rename(tmp_path / "G/2025/shoot", tmp_path / "G/2025/shoot [FINAL]")
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    text = latest_log(tmp_path / "logs")
    assert "MOVE REJECTED" in text
    assert old_copy.read_bytes() == bytes(data)
    assert (dest / "G/2025/shoot [FINAL]/_DSC1.NEF").read_bytes() == \
        (tmp_path / "G/2025/shoot [FINAL]/_DSC1.NEF").read_bytes()


def test_dry_run_reports_possible_moves_and_writes_nothing(tmp_path, capsys):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    os.rename(tmp_path / "G/2025/shoot", tmp_path / "G/2025/shoot [FINAL]")
    before = snapshot(dest)
    catalog = dest / selective_backup.CATALOG_FOLDER / "catalog.sqlite"
    catalog_stat = catalog.stat()
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "possible moves: 2 files" in out
    assert snapshot(dest) == before
    assert catalog.stat().st_mtime_ns == catalog_stat.st_mtime_ns


def test_no_detect_moves_flag(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    os.rename(tmp_path / "G/2025/shoot", tmp_path / "G/2025/shoot [FINAL]")
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes",
                                        "--no-detect-moves")) == 0
    text = latest_log(tmp_path / "logs")
    assert "MOVE " not in text
    assert "COPY G\\2025\\shoot [FINAL]\\_DSC1.NEF" in text
    assert (dest / "G/2025/shoot [FINAL]/_DSC1.NEF").exists()
    assert (dest / "G/2025/shoot/_DSC1.NEF").exists()   # the old copy is kept


def test_orphans_reported_and_moved(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
        "G/2025/shoot/_DSC2.NEF": 120,
        "G/2025/shoot/_EXPORT/x/_DSC2_v1.jpg": 60,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--yes", "--unselected", "all")) == 0
    (source / "shoot/_DSC2.NEF").unlink()
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--yes", "--unselected", "skip",
        "--export-formats", "tif", "--move-orphans")) == 0
    text = latest_csv(tmp_path / "logs", "_orphans.csv")
    rows = list(csv.DictReader(text.splitlines()))
    by_rel = {row["rel"].replace("\\", "/"): row["reason"] for row in rows}
    assert by_rel["G/2025/shoot/_DSC2.NEF"] == "source_missing"
    assert by_rel["G/2025/shoot/_DSC1.NEF"] == "not_selected"
    orphans = list((dest / "_photo_backup" / "orphans").rglob("_DSC2.NEF"))
    assert len(orphans) == 1
    assert not (dest / "G/2025/shoot/_DSC2.NEF").exists()
    keys = {row[0] for row in catalog_rows(dest)}
    assert "g\\2025\\shoot\\_dsc2.nef" not in keys
    assert "g\\2025\\shoot\\_dsc1.nef" in keys                 # not_selected kept
    assert (dest / "G/2025/shoot/_DSC1.NEF").exists()


def test_move_orphans_with_verify_is_usage_error(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--dry-run", "--move-orphans")) == 0
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--verify", "full", "--move-orphans")) == 2
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--dry-run", "--max-gb", "-1")) == 2
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--dry-run", "--max-gb", "1")) == 0


def test_backup_id_created_once_and_shown(tmp_path, capsys):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    volume = dest / "_photo_backup" / "volume.json"
    data = json.loads(volume.read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{32}", data["backup_id"])
    before = volume.read_bytes()
    capsys.readouterr()
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert volume.read_bytes() == before
    assert "backup ID " + data["backup_id"][:8] in capsys.readouterr().out
    empty = tmp_path / "empty"
    assert selective_backup.main(argv_for(source, empty, tmp_path, "--dry-run")) == 0
    assert "NEW BACKUP" in capsys.readouterr().out
    assert not empty.exists()


def test_expect_backup_id(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    backup_id = selective_backup.read_backup_id(dest)
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--yes", "--expect-backup-id", backup_id)) == 0
    before = snapshot(dest)
    wrong = "0" * 32
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--yes", "--expect-backup-id", wrong)) == 2
    assert snapshot(dest) == before
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--verify", "quick", "--expect-backup-id", wrong)) == 2
    none = tmp_path / "none"
    assert selective_backup.main(argv_for(
        source, none, tmp_path, "--yes", "--expect-backup-id", backup_id)) == 2
    assert not none.exists()


def test_rolling_verify_oldest_first(tmp_path, capsys):
    make_tree(tmp_path, {
        "G/2025/shoot/_EXPORT/x/a_v1.jpg": 1_000_000,
        "G/2025/shoot/_EXPORT/x/b_v1.jpg": 1_000_000,
        "G/2025/shoot/_EXPORT/x/c_v1.jpg": 1_000_000,
        "G/2025/shoot/_EXPORT/x/d_v1.jpg": 1_000_000,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0

    def verified():
        return [row[2] for row in catalog_rows(dest)]

    assert verified() == [None, None, None, None]
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--verify", "full", "--max-gb", "0.002")) == 0
    out = capsys.readouterr().out
    assert "Rolling verify:" in out
    first = verified()
    assert len([value for value in first if value]) == 2
    assert first.count(None) == 2
    first_csv = latest_csv(tmp_path / "logs", "_verify.csv")
    assert len(first_csv.strip().splitlines()) == 3
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--verify", "full", "--max-gb", "0.002")) == 0
    assert all(verified())
    second_csv = latest_csv(tmp_path / "logs", "_verify.csv")
    assert len(second_csv.strip().splitlines()) == 3
    assert {line.split(",")[1] for line in first_csv.splitlines()[1:]} != \
        {line.split(",")[1] for line in second_csv.splitlines()[1:]}


def test_rolling_verify_advances_on_source_missing(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    (source / "shoot/_DSC1.NEF").unlink()
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--verify", "full", "--max-gb", "1")) == 1
    assert "SOURCE_MISSING" in latest_csv(tmp_path / "logs", "_verify.csv")
    rows = {row[1]: row[2] for row in catalog_rows(dest)}
    assert rows["G\\2025\\shoot\\_DSC1.NEF"] is not None


def test_by_folder_table_and_csv(tmp_path, capsys):
    make_tree(tmp_path, {
        "src/2024/shoot/_DSC1.NEF": 100,
        "src/2024/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
        "src/2025/shoot/_DSC2.NEF": 100,
        "src/2025/shoot/_EXPORT/x/_DSC2_v1.jpg": 50,
    })
    source = tmp_path / "src"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, source, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "to copy by folder (cumulative):" in out
    assert "2024" in out and "2025" in out
    assert (dest / "_photo_backup").exists() is False
    csv_text = latest_csv(tmp_path / "logs", "_by_folder.csv")
    assert "2024" in csv_text and "2025" in csv_text
    assert "cumulative_bytes" in csv_text


def test_copy_file_expected_sha256(tmp_path):
    make_tree(tmp_path, {"src/f.bin": 100})
    src = tmp_path / "src" / "f.bin"
    dst = tmp_path / "out" / "f.bin"
    planned = selective_backup.PlannedFile(
        str(src), "f.bin", src.stat().st_size, src.stat().st_mtime_ns, "export")
    with pytest.raises(RuntimeError):
        selective_backup._copy_file(planned, str(dst), "hash", expected_sha256="0" * 64)
    assert not dst.exists()
    assert not list((tmp_path / "out").rglob("*" + selective_backup.TEMP_SUFFIX))


def test_hostile_paths_move_orphan_and_restore(tmp_path):
    # review 261108: the v0.4 tests used ASCII names only (owner's rule:
    # Japanese + [ ] + space + % in every path test)
    import restore_backup
    event = "G/2025/250118_吾妻山公園_菜の花 [FINAL] 5% off"
    make_tree(tmp_path, {
        f"{event}/01_Z7/DSC_0001 (2).NEF": 100,
        f"{event}/01_Z7/DSC_0001 (2).xmp": 40,
        f"{event}/01_Z7/_EXPORT/16B_JXL/DSC_0001 (2)_ProPhoto-g22.jxl": 50,
        f"{event}/01_Z7/_DSC6489.NEF": 120,
        f"{event}/01_Z7/_EXPORT/JPG_only/WEB [x]/_DSC6489_WEB 5%.jpg": 60,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "バックアップ [x] 5%"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
              for p in source.rglob("*") if p.is_file()}
    renamed = source / "250118_吾妻山公園_菜の花 [FINAL] 5% off (2)"
    os.rename(tmp_path / event, renamed)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    log = latest_log(tmp_path / "logs")
    assert log.count("MOVE ") == 5 and "\nCOPY " not in log
    assert not (dest / event).exists()
    (renamed / "01_Z7" / "_DSC6489.NEF").unlink()
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--move-orphans", "--yes")) == 0
    orphans = list((dest / selective_backup.CATALOG_FOLDER / selective_backup.ORPHANS_FOLDER)
                   .rglob("_DSC6489.NEF"))
    assert len(orphans) == 1
    out = tmp_path / "復元 [x] 5%"
    assert restore_backup.main(["--backup", str(dest), "--to", str(out), "--yes"]) == 0
    for rel, data in before.items():
        if rel.endswith("_DSC6489.NEF"):
            continue
        moved = rel.replace("5% off/", "5% off (2)/")
        assert (out / moved).read_bytes() == data, moved


def test_copy_file_no_clobber_keeps_an_existing_target(tmp_path):
    # review 261108: the restore checks the target first, but a file created
    # between that check and the final rename must not be overwritten
    folder = tmp_path / "吾妻山公園 [FINAL] 5% off"
    make_tree(folder, {"src/f.bin": 100, "out/f.bin": 7})
    src = folder / "src" / "f.bin"
    dst = folder / "out" / "f.bin"
    before = dst.read_bytes()
    planned = selective_backup.PlannedFile(
        str(src), "f.bin", src.stat().st_size, src.stat().st_mtime_ns, "restore")
    with pytest.raises(FileExistsError):
        selective_backup._copy_file(planned, str(dst), "hash", no_clobber=True)
    assert dst.read_bytes() == before
    assert not list((folder / "out").rglob("*" + selective_backup.TEMP_SUFFIX))
    dst.unlink()
    selective_backup._copy_file(planned, str(dst), "hash", no_clobber=True)
    assert dst.read_bytes() == src.read_bytes()


def test_settings_read_not_hardcoded_v04(tmp_path, monkeypatch):
    monkeypatch.setattr(selective_backup, "ORPHANS_FOLDER", "orf")
    monkeypatch.setattr(selective_backup, "BACKUP_ID_FILE", "id.json")
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
        "G/2025/shoot/_DSC2.NEF": 120,
        "G/2025/shoot/_EXPORT/x/_DSC2_v1.jpg": 60,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--yes", "--unselected", "all")) == 0
    assert (dest / "_photo_backup" / "id.json").exists()
    assert not (dest / "_photo_backup" / "volume.json").exists()
    (source / "shoot/_DSC2.NEF").unlink()
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--yes", "--unselected", "skip",
        "--export-formats", "tif", "--move-orphans")) == 0
    assert list((dest / "_photo_backup" / "orf").rglob("_DSC2.NEF"))
    monkeypatch.setattr(selective_backup, "DETECT_MOVES", False)
    args = selective_backup._build_parser().parse_args(
        ["--source", "x", "--dest", "y"])
    assert args.detect_moves is False
