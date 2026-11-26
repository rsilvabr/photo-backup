"""Final review of rc9 (scoped to 1a/1b, AI_tools/261127_Opus_review_rc9-final.md):
the reviewer's probes of paths that hold (F4, F5, F6, F8) and the fixes of SF-1
(a kept original is read before it is put back: F1, F1b) and F7 (a file that
appears at the TIFF's place is never replaced, whatever else failed)."""
import hashlib
import os
import re
import shutil
import signal
import sqlite3
from pathlib import Path

import _winapi

import selective_backup as sb

from conftest import argv_for, latest_log, snapshot
from test_keep_days import answers, grows, make_run, type_what_is_asked
from test_rc9 import (ORIGINALS, break_kept, consistent_originals, log_lines,  # noqa: F401
                      originals_after_jxl, originals_rows, put_back_done, stored_path)
from test_tiff_jxl import (JXL, OTHERS, TIFFS, VERSIONS, backed, backup, closed_console,  # noqa: F401
                           db, env, restore, restore_statuses, under)

REL = "2025/shoot/DSC_0001.tif"


def key_of(rel):
    return os.path.join(*rel.split("/")).casefold()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def catalog_path(env):
    return str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite")


def verify_copy_plan(env, kind):
    """A verify of the plan `backup(env)` (Tiff = copy) makes."""
    return sb.main(argv_for(env.src / "2025", env.dest, env.src, "--verify", kind,
                            "--unexported", "copy", "--export-formats", "all"))


# ---- F1: a kept original with other bytes (size and date kept) is put back UNREAD ----------------
def test_f4_room_chooser_with_put_backs_and_enough_room(env, monkeypatch, photobackup_log_dir):
    originals_after_jxl(env)
    run = stored_path(env, REL).parents[2]                              # originals\<stamp>
    extra = make_run(env.dest, ORIGINALS, 3, {"extra/z.tif": 2000})     # older, not a put-back
    (env.src / "2025/scans/new.txt").write_bytes(b"n" * 1100)          # 1,000 B missing
    grows(monkeypatch, 100, run, extra)
    lines = []
    real_out = sb._Logger.out
    monkeypatch.setattr(sb._Logger, "out", lambda self, message: (lines.append(message),
                                                                   real_out(self, message)))
    argv = argv_for(env.src / "2025", env.dest, env.src, "--unexported", "copy",
                    "--export-formats", "all", "--space-margin-gb", "0")
    answers(monkeypatch, "")
    assert sb.main(argv) == 2
    number = next(re.match(r"\s+(\d)\)", line).group(1) for line in lines
                  if re.match(r"\s+\d\) TIFF originals: all of it", line))
    source = snapshot(env.src)
    type_what_is_asked(monkeypatch, then=[number, "y"])
    assert sb.main(argv) == 0
    assert snapshot(env.src) == source
    assert not log_lines(photobackup_log_dir, "PUT BACK FAILED ")
    cleared = log_lines(photobackup_log_dir, "PUT BACK NOT POSSIBLE ")
    assert len(cleared) == 3 and all("chosen to make room" in line for line in cleared), cleared
    assert backed(env.dest) == OTHERS | TIFFS | {"2025/scans/new.txt"}
    for rel in TIFFS:
        assert (env.dest / rel).read_bytes() == (env.src / rel).read_bytes()
    assert originals_rows(env) == [] and not run.exists() and not extra.exists()
    assert JXL <= under(env.dest, VERSIONS)


# ---- F5: Ctrl+C while a kept original is read in the write phase -----------------------------------------
def test_f5_ctrl_c_while_the_write_phase_reads_a_kept_original(env, monkeypatch,
                                                               photobackup_log_dir):
    originals_after_jxl(env)
    connection = sqlite3.connect(catalog_path(env))
    with connection:
        connection.execute("UPDATE originals SET sha256 = NULL")
    connection.close()
    real = sb._sha256_file
    calls = []

    def reading(path, *args, **kwargs):
        result = real(path, *args, **kwargs)
        if os.sep + ORIGINALS + os.sep in str(path):
            calls.append(path)
            if len(calls) == 4:                 # 3 while planning, the 4th in the write phase
                signal.raise_signal(signal.SIGINT)
        return result
    monkeypatch.setattr(sb, "_sha256_file", reading)
    assert backup(env) == 130
    assert len(calls) == 4
    assert not TIFFS & backed(env.dest) and JXL <= backed(env.dest)
    assert len(originals_rows(env)) == 3 and consistent_originals(env)[0]
    monkeypatch.setattr(sb, "_sha256_file", real)
    assert backup(env) == 0
    assert backed(env.dest) == OTHERS | TIFFS and originals_rows(env) == []
    assert len(put_back_done(photobackup_log_dir)) == 3


# ---- F6: M1 at the run folder's own level ----------------------------------------------------------------
def test_f6_a_junction_as_the_run_folder_never_moves_a_source_file(env, photobackup_log_dir):
    originals_after_jxl(env)
    run = stored_path(env, REL).parents[2]
    shutil.rmtree(run)
    _winapi.CreateJunction(str(env.src), str(run))     # originals\<stamp> -> SRC
    try:
        assert stored_path(env, REL).exists()           # every "kept file" is a source file
        source = snapshot(env.src)
        assert backup(env) == 0
        assert snapshot(env.src) == source, "a source file was moved"
        refused = log_lines(photobackup_log_dir, "PUT BACK NOT POSSIBLE ")
        assert len(refused) == 3 and all("link or junction" in line for line in refused), refused
        assert not put_back_done(photobackup_log_dir)
        for rel in TIFFS:
            assert (env.dest / rel).read_bytes() == (env.src / rel).read_bytes()
            assert not os.path.samefile(env.dest / rel, env.src / rel)
    finally:
        os.rmdir(run)                                   # the junction only


# ---- F7: a file that appears while the proof fails for another reason ------------------------------------
def test_f8_lossless_group_with_a_wrong_original_is_decoded(env, photobackup_log_dir):
    scan = "2025/scans/000037.tiff"                     # a scan: lossless (scan_distance 0)
    originals_after_jxl(env)
    break_kept(env, scan)
    before = snapshot(env.dest)
    assert restore(env, "--to", env.out, "--convert-back", "lossless") == 0
    assert snapshot(env.dest) == before
    statuses = restore_statuses(photobackup_log_dir)
    assert statuses[scan] == "RESTORED_FROM_JXL"
    assert (env.out / scan).read_bytes() == (env.src / scan).read_bytes()
    assert "ORIGINAL NOT USED " + os.path.join("2025", "scans", "000037.tiff") in latest_log(
        photobackup_log_dir)
    assert statuses["2025/shoot/DSC_0001.tif"] == "RESTORED_ORIGINAL"     # lossy, kept: exact


def test_f1_a_damaged_kept_original_is_copied_from_the_source(env, photobackup_log_dir):
    originals_after_jxl(env)
    break_kept(env, REL)
    assert backup(env) == 0
    assert (env.dest / REL).read_bytes() == (env.src / REL).read_bytes()
    assert db(env.dest, f"SELECT sha256 FROM files WHERE key = '{key_of(REL)}'") == [
        (sha(env.src / REL),)]
    failed = log_lines(photobackup_log_dir, "PUT BACK FAILED ")
    assert len(failed) == 1 and "its bytes are not the ones" in failed[0], failed
    assert len(put_back_done(photobackup_log_dir)) == 2
    assert len(originals_rows(env)) == 1                  # the damaged one stays where it was
    assert JXL <= under(env.dest, VERSIONS)
    assert verify_copy_plan(env, "full") == 0


def test_f1b_versions_keep_days_0(env, monkeypatch, photobackup_log_dir):
    originals_after_jxl(env)
    break_kept(env, REL)
    type_what_is_asked(monkeypatch, then=["y"])
    argv = argv_for(env.src / "2025", env.dest, env.src, "--unexported", "copy",
                    "--export-formats", "all", "--versions-keep-days", "0")
    assert sb.main(argv) == 0
    for rel in TIFFS:
        assert (env.dest / rel).read_bytes() == (env.src / rel).read_bytes()


def test_f7_a_file_that_appears_is_never_replaced(env, monkeypatch, photobackup_log_dir):
    originals_after_jxl(env)
    real = sb._put_back_originals

    def appear_and_edit(statuses, dest, *args, **kwargs):
        (Path(dest) / REL).write_bytes(b"someone else's file")
        source = env.src / REL
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
        return real(statuses, dest, *args, **kwargs)
    monkeypatch.setattr(sb, "_put_back_originals", appear_and_edit)
    assert backup(env) == 1
    assert (env.dest / REL).read_bytes() == b"someone else's file"
    assert REL + ".jxl" in backed(env.dest)
