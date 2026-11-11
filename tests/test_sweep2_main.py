"""Second bug sweep (v0.13.0 review of 2026-11-11): selective_backup.py,
manifest_backup.py, restore_backup.py and the menu. N = a finding, O = an
optimization; the numbers are the review's."""
import contextlib
import csv
import hashlib
import io
import msvcrt
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pytest

import manifest_backup as mb
import photo_backup as menu
import restore_backup as rb
import selective_backup as sb

from conftest import argv_for, dest_files, latest_csv, latest_log, make_tree, parse_args

SHIPPED_MODES = Path(__file__).resolve().parents[1] / "backup_modes.csv"
COPY_ALL = ("--unexported", "copy")


def catalog_rows(dest):
    catalog = sb._Catalog(str(dest), create=False, readonly=True)
    try:
        return {row.rel.replace("\\", "/"): row for row in catalog.all_rows()}
    finally:
        catalog.close()


def files_tree(tmp_path, count=12, size=1000):
    make_tree(tmp_path, {f"G/2024/trip/F{index:02d}.NEF": size for index in range(count)})
    return tmp_path / "G" / "2024", tmp_path / "D"


def verify_rows(log_dir):
    return {Path(row["rel"]).name: row
            for row in csv.DictReader(io.StringIO(latest_csv(log_dir, "_verify.csv")))}


@contextlib.contextmanager
def locked(path):
    """Another handle holds the bytes of `path`: it can be seen, not read."""
    size = os.path.getsize(path)
    handle = open(path, "r+b")
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, size)
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, size)
        handle.close()


# ---- N1: a file that cannot be read is a status of the verify, not its end -------------------
def test_n1_an_unreadable_copy_does_not_stop_the_verify(tmp_path, capsys, photobackup_log_dir):
    source, dest = files_tree(tmp_path)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    with locked(dest / "G" / "2024" / "trip" / "F03.NEF"):
        assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 1
    out = capsys.readouterr().out
    assert "DEST_UNREADABLE 1" in out and "OK 11" in out
    assert verify_rows(photobackup_log_dir)["F03.NEF"]["status"] == "DEST_UNREADABLE"
    rows = catalog_rows(dest)
    assert rows["G/2024/trip/F03.NEF"].verified_at is None
    assert sum(1 for row in rows.values() if row.verified_at) == 11
    assert "DEST_UNREADABLE G\\2024\\trip\\F03.NEF" in latest_log(photobackup_log_dir)
    # the next verify reads it again, and finds it good
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 0
    assert all(row.verified_at for row in catalog_rows(dest).values())


def test_n1_an_unreadable_source_does_not_stop_the_verify(tmp_path, capsys, photobackup_log_dir):
    source, dest = files_tree(tmp_path)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    with locked(source / "trip" / "F07.NEF"):
        assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 1
    assert "SOURCE_UNREADABLE 1" in capsys.readouterr().out
    row = verify_rows(photobackup_log_dir)["F07.NEF"]
    assert row["status"] == "SOURCE_UNREADABLE"
    assert "backup copy hash verified" in row["detail"]
    # its copy was read and matches the catalog: it counts as verified
    assert all(row.verified_at for row in catalog_rows(dest).values())


def test_n1_a_copy_without_a_hash_that_cannot_be_read(tmp_path, photobackup_log_dir):
    source, dest = files_tree(tmp_path, count=3)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    connection = sqlite3.connect(str(dest / "_photo_backup" / "catalog.sqlite"))
    connection.execute("UPDATE files SET sha256=NULL")
    connection.commit()
    connection.close()
    with locked(dest / "G" / "2024" / "trip" / "F01.NEF"):
        assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 1
    rows = verify_rows(photobackup_log_dir)
    assert rows["F01.NEF"]["status"] == "DEST_UNREADABLE"
    assert rows["F00.NEF"]["status"] == rows["F02.NEF"]["status"] == "HASH_FILLED"
    assert catalog_rows(dest)["G/2024/trip/F01.NEF"].sha256 is None


# ---- N2: what a verify has checked is kept ---------------------------------------------------
def interrupt_at(monkeypatch, call_number, before=None):
    real = sb._verify_row
    calls = {"n": 0}

    def verify_row(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == call_number:
            if before is not None:
                before()
            raise KeyboardInterrupt
        return real(*args, **kwargs)
    monkeypatch.setattr(sb, "_verify_row", verify_row)
    return calls


def test_n2_ctrl_c_keeps_what_was_verified(tmp_path, monkeypatch, capsys, photobackup_log_dir):
    source, dest = files_tree(tmp_path)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    with monkeypatch.context() as patch:
        interrupt_at(patch, 6)
        assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 130
    out = capsys.readouterr().out
    assert "Verify full: 5 files  OK 5" in out
    assert "INTERRUPTED: 7 of 12 files were not checked" in out
    assert sum(1 for row in catalog_rows(dest).values() if row.verified_at) == 5
    assert latest_csv(photobackup_log_dir, "_verify.csv").startswith("status,rel")
    # the next run goes on with the other seven first (oldest verification first)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full",
                            "--max-gb", str(7000 / 1e9))) == 0
    assert all(row.verified_at for row in catalog_rows(dest).values())


def test_n2_the_catalog_is_written_while_the_verify_runs(tmp_path, monkeypatch):
    source, dest = files_tree(tmp_path)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    monkeypatch.setattr(sb, "VERIFY_COMMIT_S", 0)
    seen = []

    def another_program():      # reads the catalog in the middle, as a kill would leave it
        connection = sqlite3.connect(str(dest / "_photo_backup" / "catalog.sqlite"))
        seen.append(connection.execute(
            "SELECT COUNT(*) FROM files WHERE verified_at IS NOT NULL").fetchone()[0])
        connection.close()
    with monkeypatch.context() as patch:
        interrupt_at(patch, 9, before=another_program)
        assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 130
    assert seen == [8]


def test_n2_a_verify_does_not_hold_the_catalog_locked(tmp_path, monkeypatch):
    """Between two writes the transaction is closed: a backup started while a
    long verify runs can write its own rows."""
    source, dest = files_tree(tmp_path)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    monkeypatch.setattr(sb, "VERIFY_COMMIT_S", 3600)
    wrote = []

    def another_program():
        connection = sqlite3.connect(str(dest / "_photo_backup" / "catalog.sqlite"), timeout=0.2)
        connection.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('probe', '1')")
        connection.commit()
        connection.close()
        wrote.append(True)
    with monkeypatch.context() as patch:
        interrupt_at(patch, 9, before=another_program)
        assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 130
    assert wrote == [True]
    assert sum(1 for row in catalog_rows(dest).values() if row.verified_at) == 8


# ---- N3: a date Windows cannot convert -------------------------------------------------------
@pytest.mark.parametrize("year", [1965, 2500, 3500])
def test_n3_an_odd_date_does_not_stop_the_backup(tmp_path, capsys, photobackup_log_dir, year):
    make_tree(tmp_path, {"G/2024/trip/DSC_0001.NEF": 100, "G/2024/trip/DSC_0002.NEF": 101,
                         "G/2024/trip/_EXPORT/DSC_0001_v1.jpg": 50})
    odd = tmp_path / "G" / "2024" / "trip" / "DSC_0001.NEF"
    stamp = int((datetime(year, 6, 1) - datetime(1970, 1, 1)).total_seconds()) * 10 ** 9
    try:
        os.utime(odd, ns=(stamp, stamp))
    except OSError:
        pytest.skip("this file system cannot hold that date")
    source, dest = tmp_path / "G" / "2024", tmp_path / "D"
    assert sb.main(argv_for(source, dest, tmp_path, "--dry-run")) == 0
    assert "DSC_0001.NEF" in latest_csv(photobackup_log_dir, "_plan.csv")
    assert sb.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    copy = dest / "G" / "2024" / "trip" / "DSC_0001.NEF"
    assert copy.read_bytes() == odd.read_bytes()
    assert copy.stat().st_mtime_ns == stamp
    capsys.readouterr()
    assert sb.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert "NEW 0 files" in capsys.readouterr().out
    assert sb.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 0
    assert sb.main(argv_for(source, dest, tmp_path, "--verify", "quick")) == 0


def test_n3_csv_date():
    assert sb._csv_date(0) == datetime.fromtimestamp(0).strftime("%Y-%m-%d %H:%M:%S")
    assert sb._csv_date(-157766400 * 10 ** 9) == "1965-01-01 00:00:00 UTC"
    assert sb._csv_date(48282134400 * 10 ** 9) == "3500-01-01 00:00:00 UTC"
    assert sb._csv_date(10 ** 30).startswith("mtime_ns ")
    assert sb._db_ns(48282134400 * 10 ** 9) == 2 ** 63 - 1
    assert sb._db_ns(-5) == -5


# ---- N4: the dry-run that is confirmed is what runs ------------------------------------------
def test_n4_the_dry_run_carries_update_changed():
    session = menu.default_session()
    session.update(sources=["G:\\2025"], dest="H:\\B", update_changed=True)
    assert "--update-changed" in menu.build_argv(session, "dry-run")
    assert "--update-changed" in menu.build_argv(session, "copy")
    assert "--update-changed" not in menu.build_argv(session, "verify")
    session["update_changed"] = False
    assert "--update-changed" not in menu.build_argv(session, "dry-run")


def test_n4_the_dry_run_counts_what_the_copy_replaces(tmp_path, capsys):
    make_tree(tmp_path, {"G/2024/trip/DSC_0001.NEF": 100,
                         "G/2024/trip/_EXPORT/DSC_0001_v1.jpg": 50})
    source, dest = tmp_path / "G" / "2024", tmp_path / "D"
    session = menu.default_session()
    session.update(sources=[str(source)], dest=str(dest), update_changed=True)
    base = ["--base", str(tmp_path)]
    assert sb.main(menu.build_argv(session, "copy") + base) == 0
    (source / "trip" / "_EXPORT" / "DSC_0001_v1.jpg").write_bytes(b"exported again, longer" * 9)
    capsys.readouterr()
    assert sb.main(menu.build_argv(session, "dry-run") + base) == 0
    dry = capsys.readouterr().out
    assert "CHANGED 1" in dry and "(nothing to copy)" not in dry
    assert "export 1 /" in dry


# ---- N9: a plan that would write into the backup's own folder --------------------------------
def test_n9_a_backup_copied_with_its_root_as_base_is_refused(tmp_path, capsys):
    make_tree(tmp_path, {"G/2024/trip/DSC_0001.NEF": 100,
                         "G/2024/trip/_EXPORT/DSC_0001_v1.jpg": 50})
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert sb.main(argv_for(tmp_path / "G", first, tmp_path, "--yes")) == 0
    first_id = sb.read_backup_id(str(first))
    capsys.readouterr()
    for extra in (("--dry-run",), ("--yes",), ("--verify", "quick")):
        if extra[0] == "--verify":
            second.mkdir(exist_ok=True)
            assert sb.main(argv_for(tmp_path / "G", second, tmp_path, "--yes")) == 0
        assert sb.main(argv_for(first, second, first, *COPY_ALL, *extra)) == 2
        assert "_photo_backup" in capsys.readouterr().err
    assert sb.read_backup_id(str(second)) not in (None, first_id)
    assert not (second / "_photo_backup" / "versions").exists()
    # with a base above it, the backup lands in a folder of its own: allowed
    third = tmp_path / "third"
    assert sb.main(argv_for(first, third, tmp_path, *COPY_ALL, "--yes")) == 0
    assert "first/_photo_backup/volume.json" in dest_files(third)
    assert sb.read_backup_id(str(third)) != first_id


# ---- N23: menu [5], the files that are loose in the year -------------------------------------
def test_n23_the_loose_files_line_restores_only_those_files(tmp_path, monkeypatch):
    make_tree(tmp_path, {"G/2025/note.JPG": 30, "G/2025/other.JPG": 31,
                         "G/2025/shoot/DSC_0001.NEF": 100,
                         "G/2025/shoot/_EXPORT/DSC_0001_v1.jpg": 50})
    backup = tmp_path / "B"
    assert sb.main(argv_for(tmp_path / "G" / "2025", backup, tmp_path / "G",
                            *COPY_ALL, "--yes")) == 0
    listed = menu._catalog_folder_rows(str(backup))
    assert [(label, count, only) for label, count, _bytes, only in listed] == [
        ("(files directly in 2025)", 2, ["2025\\note.JPG", "2025\\other.JPG"]),
        ("2025\\shoot", 2, ["2025\\shoot"])]
    monkeypatch.setattr(menu, "run_restore", lambda argv: rb.main(argv))
    answers = [str(backup), "1", "2", str(tmp_path / "R"), "y"]
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    assert menu.restore_flow({"last": {}, "presets": {}}) == 0
    assert dest_files(tmp_path / "R") == {"2025/note.JPG", "2025/other.JPG"}


def test_n23_too_many_loose_files_are_not_sent_as_one_command(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path, {f"G/2025/note{index}.JPG": 30 for index in range(5)})
    make_tree(tmp_path, {"G/2025/shoot/DSC_0001.NEF": 100})
    backup = tmp_path / "B"
    assert sb.main(argv_for(tmp_path / "G" / "2025", backup, tmp_path / "G",
                            *COPY_ALL, "--yes")) == 0
    monkeypatch.setattr(menu, "MAX_ONLY_CHARS", 40)
    calls = []
    monkeypatch.setattr(menu, "run_restore", lambda argv: calls.append(argv) or 0)
    answers = [str(backup), "1", "2", str(tmp_path / "R"), "y"]
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    assert menu.restore_flow({"last": {}, "presets": {}}) == 2
    assert calls == []
    assert "Too many loose files" in capsys.readouterr().out


# ---- N24: a catalog with a damaged page ------------------------------------------------------
def damaged_backup(tmp_path):
    make_tree(tmp_path, {f"G/2024/trip{index // 50:02d}/F{index:04d}.NEF": 20
                         for index in range(400)})
    source, dest = tmp_path / "G" / "2024", tmp_path / "D"
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    catalog = dest / "_photo_backup" / "catalog.sqlite"
    data = bytearray(catalog.read_bytes())
    assert len(data) > 5 * 4096
    middle = (len(data) // 2 // 4096) * 4096
    data[middle:middle + 4096] = b"\xa5" * 4096
    catalog.write_bytes(bytes(data))
    return source, dest


def test_n24_a_damaged_catalog_stops_every_run_with_a_message(tmp_path, capsys):
    source, dest = damaged_backup(tmp_path)
    before = (dest / "_photo_backup" / "catalog.sqlite").read_bytes()
    capsys.readouterr()
    for extra in (("--dry-run",), ("--yes",), ("--verify", "quick"), ("--verify", "full")):
        assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, *extra)) == 2, extra
        captured = capsys.readouterr()
        assert "catalog" in captured.err and "Traceback" not in captured.err
        assert "Verify" not in captured.out
    assert rb.main(["--backup", str(dest), "--to", str(tmp_path / "R"), "--dry-run"]) == 2
    assert "catalog" in capsys.readouterr().err
    with pytest.raises(RuntimeError):
        menu._catalog_folder_rows(str(dest))
    assert (dest / "_photo_backup" / "catalog.sqlite").read_bytes() == before


def test_n24_going_on_after_a_damaged_catalog(tmp_path, capsys, photobackup_log_dir):
    """The README's way out: the catalog moved away, the files adopted by size
    and date, their hashes recorded again by the next full verify."""
    source, dest = damaged_backup(tmp_path)
    backup_id = sb.read_backup_id(str(dest))
    catalog = dest / "_photo_backup" / "catalog.sqlite"
    catalog.rename(catalog.with_name("catalog.damaged"))
    capsys.readouterr()
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    out = capsys.readouterr().out
    assert "NEW 0 files" in out and "UP_TO_DATE 400" in out and "copied 0 files" in out
    assert sb.read_backup_id(str(dest)) == backup_id
    rows = catalog_rows(dest)
    assert len(rows) == 400 and not any(row.sha256 for row in rows.values())
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 0
    assert "HASH_FILLED 400" in capsys.readouterr().out
    assert all(row.sha256 and row.verified_at for row in catalog_rows(dest).values())


def test_n24_a_good_catalog_passes_the_check(tmp_path):
    source, dest = files_tree(tmp_path)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    assert len(catalog_rows(dest)) == 12


# ---- N25: verified_at belongs to the bytes that were read ------------------------------------
def test_n25_a_file_copied_again_is_not_verified_yet(tmp_path):
    source, dest = files_tree(tmp_path, count=3)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 0
    assert all(row.verified_at for row in catalog_rows(dest).values())
    (source / "trip" / "F01.NEF").write_bytes(b"edited after the verify")
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes", "--update-changed")) == 0
    rows = catalog_rows(dest)
    assert rows["G/2024/trip/F01.NEF"].verified_at is None
    assert rows["G/2024/trip/F00.NEF"].verified_at and rows["G/2024/trip/F02.NEF"].verified_at


# ---- N26: a destination that goes away during the verify -------------------------------------
def test_n26_a_destination_that_went_away_is_not_a_list_of_missing_files(
        tmp_path, monkeypatch, capsys, photobackup_log_dir):
    source, dest = files_tree(tmp_path)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    assert sb._dest_available(str(dest)) is True
    assert sb._dest_available(str(tmp_path / "not there")) is False
    real = sb._verify_row
    calls = {"n": 0}

    def verify_row(job, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 4:          # the drive is gone: nothing answers any more
            return sb.VerifyResult("DEST_MISSING", job.row.rel, job.row.source_path, "x")
        return real(job, *args, **kwargs)
    monkeypatch.setattr(sb, "_verify_row", verify_row)
    monkeypatch.setattr(sb, "_dest_available", lambda dest: calls["n"] <= 4)
    capsys.readouterr()
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 1
    out = capsys.readouterr().out
    assert "Verify full: 4 files  OK 4" in out
    assert "STOPPED: the destination is not available any more" in out
    assert "8 of 12 files were not checked" in out
    assert calls["n"] == 5                                  # it stopped at the first one
    assert "DEST_MISSING" not in latest_csv(photobackup_log_dir, "_verify.csv")


def test_n26_a_copy_really_missing_is_still_reported(tmp_path, capsys):
    source, dest = files_tree(tmp_path)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    (dest / "G" / "2024" / "trip" / "F05.NEF").unlink()
    for mode in ("quick", "full"):
        capsys.readouterr()
        assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", mode)) == 1
        out = capsys.readouterr().out
        assert "DEST_MISSING 1" in out and "OK 11" in out and "STOPPED" not in out


# ---- N27: two names NTFS tells apart and the catalog does not (not changed: never silent) ----
def test_n27_names_that_differ_only_by_sharp_s_are_an_error_not_a_silent_loss(
        tmp_path, capsys, photobackup_log_dir):
    make_tree(tmp_path, {"G/2024/trip/Stra\u00dfe.NEF": 100, "G/2024/trip/Strasse.NEF": 101})
    if len(os.listdir(tmp_path / "G" / "2024" / "trip")) != 2:
        pytest.skip("this file system does not tell the two names apart")
    assert sb.main(argv_for(tmp_path / "G" / "2024", tmp_path / "D", tmp_path,
                            *COPY_ALL, "--yes")) == 1
    assert "Errors: 1" in capsys.readouterr().out
    assert "ERROR duplicate destination" in latest_log(photobackup_log_dir)
    assert len(dest_files(tmp_path / "D") - {"_photo_backup/catalog.sqlite",
                                             "_photo_backup/volume.json"}) == 1


# ---- N28: the restore gives the date the catalog recorded ------------------------------------
def test_n28_a_copy_whose_date_was_rounded_is_restored_with_the_exact_date(tmp_path):
    source, dest = files_tree(tmp_path, count=2)
    original = source / "trip" / "F00.NEF"
    exact = 1_600_000_000_123_456_700
    os.utime(original, ns=(exact, exact))
    other = source / "trip" / "F01.NEF"
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    rounded = dest / "G" / "2024" / "trip" / "F00.NEF"          # what FAT/exFAT would keep
    os.utime(rounded, ns=(exact, 1_600_000_001_000_000_000))
    touched = dest / "G" / "2024" / "trip" / "F01.NEF"          # a copy dated by someone else
    far = other.stat().st_mtime_ns + 3600 * 10 ** 9
    os.utime(touched, ns=(far, far))
    assert rb.main(["--backup", str(dest), "--to", str(tmp_path / "R"), "--yes"]) == 0
    assert (tmp_path / "R" / "G/2024/trip/F00.NEF").stat().st_mtime_ns == exact
    assert (tmp_path / "R" / "G/2024/trip/F01.NEF").stat().st_mtime_ns == far


# ---- O1: a read asks for what the file holds -------------------------------------------------
class SpyHandle:
    def __init__(self, handle):
        self.handle = handle
        self.asked = []

    def fileno(self):
        return self.handle.fileno()

    def read(self, size):
        self.asked.append(size)
        return self.handle.read(size)


def test_o1_a_small_file_is_not_read_with_the_whole_chunk(tmp_path):
    small = tmp_path / "small.xmp"
    small.write_bytes(b"x" * 300)
    with open(small, "rb") as handle:
        spy = SpyHandle(handle)
        assert b"".join(sb._read_chunks(spy)) == b"x" * 300
    assert spy.asked == [301, 1]


@pytest.mark.parametrize("size", [0, 1, 1023, 1024, 1025, 2048, 5000])
def test_o1_the_bytes_and_the_hash_are_the_same(tmp_path, monkeypatch, size):
    monkeypatch.setattr(sb, "COPY_CHUNK_MB", 1024 / (1024 * 1024))      # 1 KiB chunks
    data = bytes(range(256)) * (size // 256 + 1)
    data = data[:size]
    path = tmp_path / "file.bin"
    path.write_bytes(data)
    with open(path, "rb") as handle:
        chunks = list(sb._read_chunks(handle))
    assert b"".join(chunks) == data
    assert all(0 < len(chunk) <= 1024 for chunk in chunks)
    assert sb._sha256_file(str(path)) == hashlib.sha256(data).hexdigest()
    source = sb._SourceRead(sb.PlannedFile(str(path), "file.bin", size, 0, "x"))
    assert b"".join(source) == data
    assert source.digest == hashlib.sha256(data).hexdigest()


def test_o1_a_file_that_grows_is_read_to_its_end(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "COPY_CHUNK_MB", 1024 / (1024 * 1024))
    path = tmp_path / "growing.bin"
    path.write_bytes(b"a" * 100)
    read = []
    with open(path, "rb") as handle:
        for chunk in sb._read_chunks(handle):
            if not read:
                with open(path, "ab") as writer:
                    writer.write(b"b" * 3000)
            read.append(chunk)
    assert b"".join(read) == b"a" * 100 + b"b" * 3000
    assert len(read) <= 5                   # back to whole chunks, not byte by byte


# ---- O2: the copy and the source are read at the same time -----------------------------------
def reading_threads(monkeypatch):
    real = sb._sha256_file
    seen = []

    def sha256_file(path, *args, **kwargs):
        seen.append((os.path.basename(path), threading.current_thread() is threading.main_thread()))
        return real(path, *args, **kwargs)
    monkeypatch.setattr(sb, "_sha256_file", sha256_file)
    return seen


def test_o2_two_drives_are_read_together(tmp_path, monkeypatch, capsys):
    source, dest = files_tree(tmp_path, count=6)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    monkeypatch.setattr(sb, "_same_drive", lambda first, second: False)
    seen = reading_threads(monkeypatch)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 0
    assert "Verify full: 6 files  OK 6" in capsys.readouterr().out
    assert len(seen) == 12
    assert sum(1 for _name, main_thread in seen if main_thread) == 6       # the sources
    assert sum(1 for _name, main_thread in seen if not main_thread) == 6   # the copies
    assert all(row.verified_at for row in catalog_rows(dest).values())


def test_o2_one_drive_is_read_one_file_at_a_time(tmp_path, monkeypatch):
    source, dest = files_tree(tmp_path, count=4)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    seen = reading_threads(monkeypatch)                 # tmp_path: one drive
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 0
    assert len(seen) == 8 and all(main_thread for _name, main_thread in seen)
    seen.clear()
    monkeypatch.setattr(sb, "_same_drive", lambda first, second: False)
    monkeypatch.setattr(sb, "VERIFY_PARALLEL", False)   # the owner's switch
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 0
    assert len(seen) == 8 and all(main_thread for _name, main_thread in seen)


def flip_first_byte(path):
    stat = path.stat()
    data = bytearray(path.read_bytes())
    data[0] ^= 0xFF
    path.write_bytes(bytes(data))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_o2_read_together_the_statuses_are_the_same(tmp_path, monkeypatch, photobackup_log_dir):
    source, dest = files_tree(tmp_path, count=8)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    copies = dest / "G" / "2024" / "trip"
    flip_first_byte(copies / "F00.NEF")                             # DEST_CORRUPT
    flip_first_byte(source / "trip" / "F01.NEF")                    # SOURCE_CORRUPT_SUSPECT
    (source / "trip" / "F02.NEF").unlink()                          # SOURCE_MISSING
    (source / "trip" / "F03.NEF").write_bytes(b"changed and longer" * 99)   # SOURCE_CHANGED
    (copies / "F04.NEF").unlink()                                   # DEST_MISSING
    connection = sqlite3.connect(str(dest / "_photo_backup" / "catalog.sqlite"))
    connection.execute("UPDATE files SET sha256=NULL WHERE rel LIKE '%F05.NEF'")    # HASH_FILLED
    connection.commit()
    connection.close()
    expected = {"F00.NEF": "DEST_CORRUPT", "F01.NEF": "SOURCE_CORRUPT_SUSPECT",
                "F02.NEF": "SOURCE_MISSING", "F03.NEF": "SOURCE_CHANGED",
                "F04.NEF": "DEST_MISSING", "F05.NEF": "HASH_FILLED",
                "F06.NEF": "DEST_UNREADABLE", "F07.NEF": "SOURCE_UNREADABLE"}
    monkeypatch.setattr(sb, "VERIFY_CSV_OK", True)
    outcomes = []
    for together in (False, True):
        connection = sqlite3.connect(str(dest / "_photo_backup" / "catalog.sqlite"))
        connection.execute("UPDATE files SET sha256=NULL, verified_at=NULL "
                           "WHERE rel LIKE '%F05.NEF'")
        connection.commit()
        connection.close()
        with monkeypatch.context() as patch:
            patch.setattr(sb, "_same_drive", lambda first, second: not together)
            with locked(copies / "F06.NEF"), locked(source / "trip" / "F07.NEF"):
                assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL,
                                        "--verify", "full")) == 1
        rows = verify_rows(photobackup_log_dir)
        outcomes.append({name: (row["status"], row["detail"]) for name, row in rows.items()})
        assert {name: status for name, (status, _detail) in outcomes[-1].items()} == expected
        verified = {Path(rel).name for rel, row in catalog_rows(dest).items() if row.verified_at}
        # a copy that was read and matches its hash is verified, whatever its source is
        assert verified == {"F01.NEF", "F02.NEF", "F03.NEF", "F05.NEF", "F07.NEF"}
    assert outcomes[0] == outcomes[1]


def test_o2_ctrl_c_while_both_are_read_ends_the_run(tmp_path, monkeypatch, capsys):
    source, dest = files_tree(tmp_path, count=6)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    monkeypatch.setattr(sb, "_same_drive", lambda first, second: False)
    real = sb._sha256_file
    calls = {"main": 0}

    def sha256_file(path, *args, **kwargs):
        if threading.current_thread() is threading.main_thread():
            calls["main"] += 1
            if calls["main"] == 4:
                raise KeyboardInterrupt
        return real(path, *args, **kwargs)
    monkeypatch.setattr(sb, "_sha256_file", sha256_file)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 130
    assert "INTERRUPTED: 3 of 6 files were not checked" in capsys.readouterr().out
    assert sum(1 for row in catalog_rows(dest).values() if row.verified_at) == 3
    assert not [thread for thread in threading.enumerate()
                if thread is not threading.main_thread() and thread.name.startswith("ThreadPool")
                and thread.is_alive() and not thread.daemon]


# ---- O3: the destination is listed, not asked file by file -----------------------------------
def test_o3_the_listing_gives_the_same_statuses(tmp_path, monkeypatch):
    make_tree(tmp_path, {f"G/2024/trip/F{index:02d}.NEF": 100 + index for index in range(8)})
    make_tree(tmp_path, {"G/2024/other/X.NEF": 50})
    source, dest = tmp_path / "G" / "2024", tmp_path / "D"
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    copies = dest / "G" / "2024" / "trip"
    (source / "trip" / "F01.NEF").write_bytes(b"changed")                   # CHANGED (size)
    stat = (source / "trip" / "F02.NEF").stat()
    os.utime(source / "trip" / "F02.NEF", ns=(stat.st_atime_ns, stat.st_mtime_ns + 60 * 10 ** 9))
    (copies / "F03.NEF").unlink()                                           # NEW
    os.rename(copies / "F04.NEF", copies / "f04.nef")                       # stored in another case
    make_tree(tmp_path, {"G/2024/new folder/N.NEF": 10})                    # NEW, no folder yet
    planned = sb.plan(parse_args(source, dest, tmp_path, *COPY_ALL))
    one_by_one = [sb._file_status(item, str(dest)) for item in planned.files]
    asked = []
    real = sb._file_status
    monkeypatch.setattr(sb, "_file_status",
                        lambda item, dest: asked.append(item.rel) or real(item, dest))
    listed = sb._file_statuses(planned.files, str(dest))
    assert [(s.planned.rel, s.dst, s.status) for s in listed] == \
           [(s.planned.rel, s.dst, s.status) for s in one_by_one]
    by_name = {Path(s.planned.rel).name: s.status for s in listed}
    assert by_name["F00.NEF"] == sb.ST_UP_TO_DATE
    assert by_name["F01.NEF"] == by_name["F02.NEF"] == sb.ST_CHANGED
    assert by_name["F03.NEF"] == by_name["N.NEF"] == sb.ST_NEW
    assert by_name["F04.NEF"] == sb.ST_UP_TO_DATE       # never taken for a NEW file
    # only the names the listing does not hold are asked one by one
    assert sorted(Path(rel).name for rel in asked) == ["F03.NEF", "F04.NEF"]


def test_o3_a_folder_that_cannot_be_listed_still_answers(tmp_path, monkeypatch):
    source, dest = files_tree(tmp_path, count=4)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    monkeypatch.setattr(sb, "_list_dest_folder", lambda folder: {})
    planned = sb.plan(parse_args(source, dest, tmp_path, *COPY_ALL))
    assert {s.status for s in sb._file_statuses(planned.files, str(dest))} == {sb.ST_UP_TO_DATE}


# ---- O4: the relative path without os.path.relpath -------------------------------------------
def test_o4_the_cut_prefix_is_what_relpath_gives(tmp_path):
    planner = sb._Planner(parse_args(tmp_path, tmp_path / "out", tmp_path))
    base = str(tmp_path)
    names = ["2024\\trip\\DSC_0001.NEF", "2024\\Trip.\\DSC_0001.NEF", "2024\\trip \\a.NEF",
             "2024\\trip\\name.", "2024\\trip\\name ", "2024\\CONSOLE\\a.jpg",
             "2024\\..hidden\\.xmp", "2024\\a.b.c\\d..e", "2024\\\u5199\u771f \uff5e [FINAL]\\x.jxl",
             "2024\\Lightroom_Export\\_DSC6489 (2).jpg", "top.jpg"]
    for name in names:
        path = os.path.join(base, name)
        assert planner._rel(path, base) == os.path.relpath(path, base), name
        assert planner._rel(path, base.upper()) == os.path.relpath(path, base.upper()), name
    root = os.path.splitdrive(base)[0] + os.sep
    assert planner._rel(os.path.join(root, "2024", "a.NEF"), root) == "2024\\a.NEF"
    # a plain name takes the short way, a name Windows rewrites does not
    assert not sb._REL_REWRITTEN.search("2024\\trip\\DSC_0001.NEF")
    assert not sb._REL_REWRITTEN.search("2024\\CONSOLE\\a.b.c\\d..e")
    for rewritten in ("2024\\Trip.\\a.NEF", "a ", "NUL", "x\\com1.txt\\y", "lpt9"):
        assert sb._REL_REWRITTEN.search(rewritten), rewritten


# ---- O5: the CSVs list what changed, not everything ------------------------------------------
def plan_actions(log_dir):
    rows = csv.DictReader(io.StringIO(latest_csv(log_dir, "_plan.csv")))
    return sorted((Path(row["rel"]).name, row["action"]) for row in rows)


def test_o5_a_run_that_copies_lists_what_it_changes(tmp_path, monkeypatch, photobackup_log_dir):
    source, dest = files_tree(tmp_path, count=5)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    assert len(plan_actions(photobackup_log_dir)) == 5              # all NEW
    make_tree(tmp_path, {"G/2024/trip/F99.NEF": 10})
    (source / "trip" / "F01.NEF").write_bytes(b"changed")
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--dry-run")) == 0
    assert len(plan_actions(photobackup_log_dir)) == 6              # a dry-run lists every file
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    assert plan_actions(photobackup_log_dir) == [("F01.NEF", "CHANGED"), ("F99.NEF", "NEW")]
    assert "_plan.csv: 4 UP_TO_DATE files not listed" in latest_log(photobackup_log_dir)
    monkeypatch.setattr(sb, "PLAN_CSV_UP_TO_DATE", True)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    assert len(plan_actions(photobackup_log_dir)) == 6


def test_o5_the_verify_lists_what_is_not_ok(tmp_path, monkeypatch, photobackup_log_dir):
    source, dest = files_tree(tmp_path, count=5)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    (source / "trip" / "F02.NEF").unlink()
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 1
    assert {name: row["status"] for name, row in verify_rows(photobackup_log_dir).items()} == {
        "F02.NEF": "SOURCE_MISSING"}
    assert plan_actions(photobackup_log_dir) == []      # the four planned files are OK
    assert "_verify.csv: 4 OK files not listed" in latest_log(photobackup_log_dir)
    monkeypatch.setattr(sb, "VERIFY_CSV_OK", True)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--verify", "full")) == 1
    assert len(verify_rows(photobackup_log_dir)) == 5
    assert len(plan_actions(photobackup_log_dir)) == 4


# ---- O8: the listing of the source is not kept during the copy -------------------------------
def test_o8_the_manifest_drops_its_listing_after_the_plan(tmp_path, monkeypatch):
    make_tree(tmp_path, {"G/2024/trip/DSC_0001.NEF": 100,
                         "G/2024/trip/_EXPORT/DSC_0001_v1.jpg": 50})
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{tmp_path / 'G' / '2024'},2,raw\n",
                        encoding="utf-8")
    real = mb._plan_rows
    caches = []

    def plan_rows(rows, modes, args, list_cache):
        result = real(rows, modes, args, list_cache)
        caches.append((list_cache, len(list_cache)))
        return result
    monkeypatch.setattr(mb, "_plan_rows", plan_rows)
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(tmp_path / "D"), "--yes", "--space-margin-gb", "0"]) == 0
    (cache, size_while_planning), = caches
    assert size_while_planning > 0 and cache == {}
    assert any(rel.endswith("G/2024/trip/DSC_0001.NEF") for rel in dest_files(tmp_path / "D"))


# ---- O9: --analyze opens a JXL once ----------------------------------------------------------
def jxl_with(token):
    xmp = f"<x:xmpmeta><dc:relation>{token}abc</dc:relation></x:xmpmeta>".encode()
    box = (8 + len(xmp)).to_bytes(4, "big") + b"xml " + xmp
    return sb._JXL_CONTAINER + box + (8).to_bytes(4, "big") + b"jxlc"


def test_o9_analyze_reads_each_jxl_once(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path, {
        "G/2024/trip/DSC_0001.NEF": 100,
        "G/2024/trip/DSC_0002.NEF": 100,
        "G/2024/trip/_EXPORT/16B/DSC_0001_v1.jxl": jxl_with(sb.MASTER_TOKEN),
        "G/2024/trip/_EXPORT/mobile/DSC_0001_m.jxl": jxl_with(sb.DERIVATIVE_TOKEN)})
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{tmp_path / 'G' / '2024'},2,raw\n",
                        encoding="utf-8")
    real = sb._jxl_role
    opened = []
    monkeypatch.setattr(sb, "_jxl_role", lambda path: opened.append(path) or real(path))
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES), "--analyze"]) == 0
    assert len(opened) == 2 and len(set(opened)) == 2
    out = capsys.readouterr().out
    # the derivative is left out in every mode that walks the session: 1 JXL + 1 RAW at least
    assert "<- chosen" in out
    # a backup reads them itself (no cache between runs)
    opened.clear()
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(tmp_path / "D"), "--yes", "--space-margin-gb", "0"]) == 0
    assert len(opened) == 2
    copied = dest_files(tmp_path / "D")
    assert any(rel.endswith("_EXPORT/16B/DSC_0001_v1.jxl") for rel in copied)
    assert not any(rel.endswith("_EXPORT/mobile/DSC_0001_m.jxl") for rel in copied)


# ---- O10: an orphan's source is asked once ---------------------------------------------------
def test_o10_the_second_list_of_orphans_reuses_the_first(tmp_path, monkeypatch):
    source, dest = files_tree(tmp_path, count=6)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    gone = source / "trip" / "F02.NEF"
    gone.unlink()
    real = sb._source_stat
    asked = []

    def source_stat(path):
        asked.append(os.path.basename(path))
        return real(path)
    monkeypatch.setattr(sb, "_source_stat", source_stat)
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes")) == 0
    assert asked.count("F02.NEF") == 1
    asked.clear()
    # --move-orphans moves on proof: the source is asked again right before the copy
    assert sb.main(argv_for(source, dest, tmp_path, *COPY_ALL, "--yes", "--move-orphans")) == 0
    assert asked.count("F02.NEF") == 2
    assert "G/2024/trip/F02.NEF" not in dest_files(dest)
