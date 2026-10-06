import shutil
import sqlite3
import types

import selective_backup

from conftest import (
    argv_for,
    build_base_tree,
    dest_files,
    latest_csv,
    latest_log,
    make_tree,
    snapshot,
)


def test_rerun_is_up_to_date(tmp_path):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    before = snapshot(dest)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert snapshot(dest) == before
    text = latest_log(tmp_path / "logs")
    assert "NEW 0 files" in text
    assert "COPY " not in text


def test_changed_skipped_then_versioned(tmp_path):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    dest_nef = dest / "G" / "2025" / "shoot" / "_DSC6489.NEF"
    assert dest_nef.exists()
    make_tree(tmp_path, {"G/2025/shoot/_DSC6489.NEF": 500})
    old_content = dest_nef.read_bytes()
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert dest_nef.read_bytes() == old_content
    assert "SKIP CHANGED" in latest_log(tmp_path / "logs")
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes", "--update-changed")) == 0
    assert dest_nef.read_bytes() != old_content
    versions = list((dest / "_photo_backup" / "versions").glob("*/G/2025/shoot/_DSC6489.NEF"))
    assert len(versions) == 1
    assert versions[0].read_bytes() == old_content


def test_adopted_file_hash_filled(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    for rel in ("G/2025/shoot/_DSC1.NEF", "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg"):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp_path / rel, target)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    database = dest / "_photo_backup" / "catalog.sqlite"
    connection = sqlite3.connect(database)
    rows = connection.execute("SELECT key, sha256 FROM files").fetchall()
    assert len(rows) == 2
    assert all(sha is None for _, sha in rows)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 0
    rows = connection.execute("SELECT sha256 FROM files").fetchall()
    assert len(rows) == 2
    assert all(sha for (sha,) in rows)
    connection.close()


def test_not_enough_space_aborts(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025" / "shoot"
    fake = types.SimpleNamespace(total=10, used=10, free=0)
    monkeypatch.setattr(selective_backup.shutil, "disk_usage", lambda path: fake)
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 2
    assert not dest.exists()
    dest2 = tmp_path / "out2"
    assert selective_backup.main(argv_for(source, dest2, tmp_path, "--dry-run")) == 0
    assert "would not fit" in capsys.readouterr().out
    assert not dest2.exists()


def test_closed_stdin_without_yes_aborts(tmp_path, monkeypatch):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"

    def closed_input(prompt=""):
        raise EOFError()

    monkeypatch.setattr("builtins.input", closed_input)
    assert selective_backup.main(argv_for(source, dest, tmp_path)) == 2
    assert not dest.exists()


def test_dest_inside_source_and_overlap_abort(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025" / "shoot"
    assert selective_backup.main(argv_for(source, source / "sub", tmp_path, "--yes")) == 2
    assert selective_backup.main(argv_for(source, tmp_path, tmp_path, "--yes")) == 2
    assert selective_backup.main(argv_for(
        source, tmp_path / "other", tmp_path, "--yes",
        "--source", str(source / "_EXPORT"))) == 2


def test_failed_copy_leaves_no_temp(tmp_path, monkeypatch):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    real_copystat = selective_backup.shutil.copystat

    def failing_copystat(src, dst, **kwargs):
        if str(dst).endswith("_DSC6489_ProPhoto-g22_v1.jxl" + selective_backup.TEMP_SUFFIX):
            raise OSError(28, "No space left on device")
        return real_copystat(src, dst, **kwargs)

    monkeypatch.setattr(selective_backup.shutil, "copystat", failing_copystat)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 1
    assert not (dest / "G/2025/shoot/_EXPORT/16B_JXL/_DSC6489_ProPhoto-g22_v1.jxl").exists()
    assert not list(dest.rglob("*" + selective_backup.TEMP_SUFFIX))
    assert (dest / "G/2025/shoot/_DSC6489.NEF").exists()


def test_stale_temp_is_replaced(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    final = dest / "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg"
    final.parent.mkdir(parents=True, exist_ok=True)
    (final.parent / (final.name + selective_backup.TEMP_SUFFIX)).write_bytes(b"stale")
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert final.read_bytes() == (tmp_path / "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg").read_bytes()
    assert not list(dest.rglob("*" + selective_backup.TEMP_SUFFIX))
    assert not list(dest.rglob("*.pbtmp"))


def test_dry_run_writes_nothing_in_dest(tmp_path, photobackup_log_dir):
    build_base_tree(tmp_path)
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(tmp_path / "G", dest, tmp_path, "--dry-run")) == 0
    assert not dest.exists()
    assert latest_csv(photobackup_log_dir, "_plan.csv")
    assert latest_csv(photobackup_log_dir, "_unexported.csv")
    assert latest_csv(photobackup_log_dir, "_unmatched.csv")
