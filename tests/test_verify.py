import os

import selective_backup

from conftest import argv_for, build_base_tree, latest_csv, make_tree, snapshot


def test_source_deleted_dest_kept(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    deleted = source / "_DSC1.NEF"
    deleted.unlink()
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "quick")) == 1
    assert (dest / "G/2025/shoot/_DSC1.NEF").exists()
    assert "SOURCE_MISSING" in latest_csv(tmp_path / "logs", "_verify.csv")


def test_verify_quick_vs_full_detects_corruption(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    target = dest / "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg"
    stat = target.stat()
    data = bytearray(target.read_bytes())
    data[0] ^= 0xFF
    target.write_bytes(bytes(data))
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "quick")) == 0
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 1
    assert "DEST_CORRUPT" in latest_csv(tmp_path / "logs", "_verify.csv")


def test_verify_source_corrupt_suspect(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    target = source / "_DSC1.NEF"
    stat = target.stat()
    data = bytearray(target.read_bytes())
    data[1] ^= 0xFF
    target.write_bytes(bytes(data))
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 1
    assert "SOURCE_CORRUPT_SUSPECT" in latest_csv(tmp_path / "logs", "_verify.csv")


def test_source_tree_untouched(tmp_path):
    build_base_tree(tmp_path)
    source_root = tmp_path / "G"
    source = source_root / "2025" / "shoot"
    dest = tmp_path / "out"
    before = snapshot(source_root)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert snapshot(source_root) == before
    make_tree(tmp_path, {"G/2025/shoot/_DSC6489.NEF": 999})
    before_update = snapshot(source_root)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes", "--update-changed")) == 0
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 0
    assert snapshot(source_root) == before_update
