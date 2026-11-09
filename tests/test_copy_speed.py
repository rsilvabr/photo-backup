"""The backup's read-ahead and batched flushes: faster, but a file still gets
its final name only once it is on the disk, and the catalog lists it only
after that. Every tree lives in a HOSTILE folder (Japanese, [ ], a space, %)."""
import hashlib
import os
import stat
import time

import pytest

import selective_backup

from conftest import argv_for, make_tree, snapshot

EVENT = "G/2025/250118_吾妻山公園_菜の花 [FINAL] 5% off"
PHOTOS = 8
MB = 1024 * 1024


def build(tmp_path, raw_size=3000):
    """PHOTOS photos, each with a NEF, an .xmp and an exported JPEG."""
    spec = {}
    for number in range(PHOTOS):
        spec[f"{EVENT}/DSC_{number:04d}.NEF"] = bytes([number]) * (raw_size + number)
        spec[f"{EVENT}/DSC_{number:04d}.xmp"] = bytes([number + 100]) * (200 + number)
        spec[f"{EVENT}/_EXPORT/x/DSC_{number:04d}_v1.jpg"] = bytes([number + 50]) * (300 + number)
    make_tree(tmp_path, spec)
    return tmp_path / "G" / "2025", tmp_path / "out", tmp_path / EVENT


def assert_copies_match(event, dest):
    copied = dest / EVENT
    for source_file in event.rglob("*"):
        if source_file.is_file():
            target = copied / source_file.relative_to(event)
            assert target.read_bytes() == source_file.read_bytes(), target
    assert not list(dest.rglob("*" + selective_backup.TEMP_SUFFIX))


def catalog_rels(dest):
    catalog = selective_backup._Catalog(str(dest), create=False, readonly=True)
    try:
        return {row.rel.replace("\\", "/") for row in catalog.all_rows()}
    finally:
        catalog.close()


@pytest.mark.parametrize("read_ahead_mb", [0, 1, 512])
def test_every_copy_is_exact_and_verifies(tmp_path, monkeypatch, read_ahead_mb):
    monkeypatch.setattr(selective_backup, "READ_AHEAD_MB", read_ahead_mb)
    monkeypatch.setattr(selective_backup, "SMALL_FILE_KB", 1)
    source, dest, event = build(tmp_path)
    before = snapshot(source)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert_copies_match(event, dest)
    assert snapshot(source) == before
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 0


def test_small_files_flush_as_a_batch_before_their_final_name(tmp_path, monkeypatch):
    monkeypatch.setattr(selective_backup, "FLUSH_BATCH_FILES", 5)
    source, dest, _event = build(tmp_path)
    events = []
    real_fsync, real_replace = os.fsync, os.replace
    real_upsert = selective_backup._Catalog.upsert

    def fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    def replace(src, dst, **kwargs):
        if selective_backup.BACKUP_ID_FILE not in str(src):
            events.append("replace")
        return real_replace(src, dst, **kwargs)

    def upsert(self, *args, **kwargs):
        events.append("upsert")
        return real_upsert(self, *args, **kwargs)

    monkeypatch.setattr(selective_backup.os, "fsync", fsync)
    monkeypatch.setattr(selective_backup.os, "replace", replace)
    monkeypatch.setattr(selective_backup._Catalog, "upsert", upsert)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert events[:5] == ["fsync"] * 5, events[:12]
    counts = {"fsync": 0, "replace": 0, "upsert": 0}
    for event_name in events:
        counts[event_name] += 1
        assert counts["fsync"] >= counts["replace"] >= counts["upsert"], events
    assert counts["fsync"] == counts["replace"] == counts["upsert"] == PHOTOS * 3


def test_a_failed_flush_gives_no_final_name_and_no_catalog_row(tmp_path, monkeypatch):
    source, dest, event = build(tmp_path)
    real_flush = selective_backup._flush_temp

    def flush(tmp):
        if "DSC_0003.xmp" in tmp:
            raise OSError(5, "I/O error")
        return real_flush(tmp)

    monkeypatch.setattr(selective_backup, "_flush_temp", flush)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 1
    target = dest / EVENT / "DSC_0003.xmp"
    assert not target.exists()
    assert not list(dest.rglob("*" + selective_backup.TEMP_SUFFIX))
    assert f"{EVENT}/DSC_0003.xmp" not in catalog_rels(dest)
    assert (dest / EVENT / "DSC_0004.xmp").read_bytes() == (event / "DSC_0004.xmp").read_bytes()


def test_a_read_error_does_not_shift_the_next_files(tmp_path, monkeypatch):
    source, dest, event = build(tmp_path)
    real_iter = selective_backup._SourceRead.__iter__

    def iterate(self):
        if self.planned.rel.endswith("DSC_0002.NEF"):
            raise OSError(5, "cannot read")
        return real_iter(self)

    monkeypatch.setattr(selective_backup._SourceRead, "__iter__", iterate)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 1
    assert not (dest / EVENT / "DSC_0002.NEF").exists()
    assert not list(dest.rglob("*" + selective_backup.TEMP_SUFFIX))
    for name in ("DSC_0001.NEF", "DSC_0003.NEF", "DSC_0003.xmp", "_EXPORT/x/DSC_0007_v1.jpg"):
        assert (dest / EVENT / name).read_bytes() == (event / name).read_bytes(), name


def test_a_write_error_mid_file_does_not_shift_the_next_files(tmp_path, monkeypatch):
    monkeypatch.setattr(selective_backup, "COPY_CHUNK_MB", 1)
    monkeypatch.setattr(selective_backup, "READ_AHEAD_MB", 2)
    monkeypatch.setattr(selective_backup, "SMALL_FILE_KB", 1024)
    source, dest, event = build(tmp_path, raw_size=3 * MB)
    real_write = selective_backup._write_temp

    def write(planned, tmp, source_data, flush):
        if planned.rel.endswith("DSC_0002.NEF"):
            next(iter(source_data))
            raise OSError(28, "No space left on device")
        return real_write(planned, tmp, source_data, flush)

    monkeypatch.setattr(selective_backup, "_write_temp", write)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 1
    assert not (dest / EVENT / "DSC_0002.NEF").exists()
    for name in ("DSC_0001.NEF", "DSC_0003.NEF", "DSC_0007.NEF", "DSC_0003.xmp"):
        assert (dest / EVENT / name).read_bytes() == (event / name).read_bytes(), name


def test_ctrl_c_removes_waiting_temps_and_a_rerun_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(selective_backup, "FLUSH_BATCH_FILES", 100)
    source, dest, event = build(tmp_path)
    real_write = selective_backup._write_temp
    writes = []

    def write(planned, tmp, source_data, flush):
        writes.append(planned.rel)
        if len(writes) == 6:
            raise KeyboardInterrupt
        return real_write(planned, tmp, source_data, flush)

    monkeypatch.setattr(selective_backup, "_write_temp", write)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 130
    assert not list(dest.rglob("*" + selective_backup.TEMP_SUFFIX))
    assert catalog_rels(dest) == set()
    monkeypatch.setattr(selective_backup, "_write_temp", real_write)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert_copies_match(event, dest)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--verify", "full")) == 0


def test_a_read_only_small_source_is_flushed_and_copied(tmp_path):
    source, dest, event = build(tmp_path)
    locked = event / "DSC_0001.xmp"
    os.chmod(locked, stat.S_IREAD)
    try:
        assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
        assert (dest / EVENT / "DSC_0001.xmp").read_bytes() == locked.read_bytes()
    finally:
        os.chmod(locked, stat.S_IREAD | stat.S_IWRITE)
        target = dest / EVENT / "DSC_0001.xmp"
        if target.exists():
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)


def _wait_until(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_read_ahead_runs_ahead_but_stays_within_its_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(selective_backup, "COPY_CHUNK_MB", 1)
    folder = tmp_path / "吾妻山 [A] 5%"
    folder.mkdir()
    planned_files = []
    expected = []
    for number in range(4):
        path = folder / f"f{number}.bin"
        data = bytes([number]) * (3 * MB)
        path.write_bytes(data)
        expected.append(hashlib.sha256(data).hexdigest())
        planned_files.append(selective_backup.PlannedFile(str(path), path.name, len(data), 0, "x"))
    read_ahead = selective_backup._ReadAhead(planned_files, 2 * MB)
    try:
        # Nobody consumes yet: the reader fills its budget by itself, then waits.
        assert _wait_until(lambda: read_ahead._bytes == 2 * MB)
        time.sleep(0.2)
        assert read_ahead._bytes == 2 * MB
        for planned, digest in zip(planned_files, expected):
            streamed = read_ahead.next_source(planned)
            assert hashlib.sha256(b"".join(streamed)).hexdigest() == digest
            assert streamed.digest == digest
            assert read_ahead._bytes <= 2 * MB
    finally:
        read_ahead.close()


def test_the_next_file_is_read_while_the_current_one_waits(tmp_path, monkeypatch):
    source, dest, event = build(tmp_path)
    monkeypatch.setattr(selective_backup, "FLUSH_BATCH_FILES", 1)
    monkeypatch.setattr(selective_backup, "SMALL_FILE_KB", 1)
    started = []
    real_iter = selective_backup._SourceRead.__iter__

    def iterate(self):
        started.append(self.planned.rel)
        return real_iter(self)

    monkeypatch.setattr(selective_backup._SourceRead, "__iter__", iterate)
    real_write = selective_backup._write_temp
    overlaps = []

    def write(planned, tmp, source_data, flush):
        position = next(i for i, rel in enumerate(started) if rel == planned.rel)
        if not overlaps:
            overlaps.append(_wait_until(lambda: len(started) > position + 1))
        return real_write(planned, tmp, source_data, flush)

    monkeypatch.setattr(selective_backup, "_write_temp", write)
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert overlaps == [True]
    assert_copies_match(event, dest)
