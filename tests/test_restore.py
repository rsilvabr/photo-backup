"""restore_backup.py: restore from a backup, never overwrite, read-only backup."""
import os

import restore_backup
import selective_backup

from conftest import argv_for, latest_csv, make_tree, snapshot


def build_backup(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_DSC1.xmp": 40,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
        "G/2025/shoot/_DSC2.NEF": 120,
        "G/2025/shoot/_EXPORT/x/_DSC2_v1.jpg": 60,
    })
    source = tmp_path / "G" / "2025"
    backup = tmp_path / "out"
    assert selective_backup.main(argv_for(source, backup, tmp_path, "--yes")) == 0
    return source, backup


def source_files(source):
    return {path.relative_to(source.parents[1]).as_posix(): path
            for path in source.rglob("*") if path.is_file()}


def test_restore_to_folder(tmp_path):
    source, backup = build_backup(tmp_path)
    out = tmp_path / "restored"
    assert restore_backup.main(
        ["--backup", str(backup), "--to", str(out), "--yes"]) == 0
    for rel, path in source_files(source).items():
        target = out / rel
        assert target.read_bytes() == path.read_bytes(), rel
        assert target.stat().st_mtime_ns == path.stat().st_mtime_ns, rel
    assert restore_backup.main(
        ["--backup", str(backup), "--to", str(out), "--yes"]) == 0
    csv_text = latest_csv(tmp_path / "logs", ".csv")
    assert csv_text.count("ALREADY_THERE") == len(source_files(source))


def test_restore_never_overwrites(tmp_path):
    source, backup = build_backup(tmp_path)
    out = tmp_path / "restored"
    assert restore_backup.main(
        ["--backup", str(backup), "--to", str(out), "--yes"]) == 0
    target = out / "G/2025/shoot/_DSC1.NEF"
    target.write_bytes(b"changed")
    assert restore_backup.main(
        ["--backup", str(backup), "--to", str(out), "--yes"]) == 1
    assert target.read_bytes() == b"changed"
    assert "CONFLICT" in latest_csv(tmp_path / "logs", ".csv")


def test_restore_detects_corrupt_backup(tmp_path):
    source, backup = build_backup(tmp_path)
    corrupt = backup / "G/2025/shoot/_DSC1.NEF"
    stat = corrupt.stat()
    data = bytearray(corrupt.read_bytes())
    data[0] ^= 0xFF
    corrupt.write_bytes(bytes(data))
    os.utime(corrupt, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    out = tmp_path / "restored"
    assert restore_backup.main(
        ["--backup", str(backup), "--to", str(out), "--yes"]) == 1
    assert not (out / "G/2025/shoot/_DSC1.NEF").exists()
    assert not list(out.rglob("*" + selective_backup.TEMP_SUFFIX))
    assert (out / "G/2025/shoot/_DSC2.NEF").exists()
    assert "CORRUPT_IN_BACKUP" in latest_csv(tmp_path / "logs", ".csv")


def test_restore_only_and_to_original(tmp_path):
    source, backup = build_backup(tmp_path)
    out = tmp_path / "only"
    assert restore_backup.main(
        ["--backup", str(backup), "--to", str(out),
         "--only", "G/2025/shoot/_EXPORT", "--yes"]) == 0
    assert (out / "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg").exists()
    assert not (out / "G/2025/shoot/_DSC1.NEF").exists()
    (source / "shoot/_DSC1.NEF").unlink()
    assert restore_backup.main(
        ["--backup", str(backup), "--to-original",
         "--only", "G/2025/shoot/_DSC1.NEF", "--yes"]) == 0
    assert (source / "shoot/_DSC1.NEF").exists()
    assert restore_backup.main(
        ["--backup", str(backup), "--to-original",
         "--only", "G/2025/shoot/_DSC1.NEF", "--yes"]) == 0
    assert "ALREADY_THERE" in latest_csv(tmp_path / "logs", ".csv")


def test_restore_is_read_only_on_backup(tmp_path):
    source, backup = build_backup(tmp_path)
    before = snapshot(backup)
    out = tmp_path / "restored"
    assert restore_backup.main(
        ["--backup", str(backup), "--to", str(out), "--yes"]) == 0
    assert snapshot(backup) == before


def test_restore_usage_errors(tmp_path):
    source, backup = build_backup(tmp_path)
    out = tmp_path / "restored"
    assert restore_backup.main(["--backup", str(backup), "--yes"]) == 2
    assert restore_backup.main(
        ["--backup", str(backup), "--to", str(out), "--to-original", "--yes"]) == 2
    assert restore_backup.main(
        ["--backup", str(backup), "--to", str(backup / "inner"), "--yes"]) == 2
    assert restore_backup.main(
        ["--backup", str(backup), "--to", str(tmp_path), "--yes"]) == 2
    assert restore_backup.main(
        ["--backup", str(backup / "inner"), "--to", str(out), "--yes"]) == 2
    assert not (backup / "inner").exists()
    assert not out.exists()
