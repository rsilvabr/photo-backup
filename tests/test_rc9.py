"""rc9 (AI_tools/261127_Claude_plan_rc9.md): round 2 of keep-days -- the TIFF
original kept in originals\\ is restored as it is (1a) and put back by a rename
when the manifest no longer keeps the TIFF as JXL (1b); a time on every log line
(N8); what the mirror's log says of a copy retired without a JXL (3a); Ctrl+C
while things leave or come back (3b); a multipage TIFF is lossless only when
every page is (4a); `not_converted` rows of a gone source are forgotten (4b); a
rename refused as "another device" takes the copy route (4c). jxl-photo is
played by tests/fake_jxlphoto.py. Tests marked (star) fail on 307d400."""
import _winapi
import errno
import os
import re
import shutil
import signal
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

import mirror_backup as mir
import restore_backup as rb
import selective_backup as sb

from conftest import argv_for, latest_log, latest_log_raw, make_tree, row_count, snapshot
from test_keep_days import answers, grows, room_env, plain  # noqa: F401
from test_keep_days import backup as plain_backup
from test_rc6 import BAD, failing, not_converted  # noqa: F401
from test_tiff_jxl import (JXL, OTHERS, TIFFS, VERSIONS, backed, backup, closed_console,  # noqa: F401
                           db, env, mirror_rel, newest_csv, restore, restore_statuses, under,
                           write_manifest)

ORIGINALS = sb.ORIGINALS_FOLDER


def originals_after_jxl(env):
    """Run 1 copies the TIFFs as they are; run 2 keeps them as JXL: the TIFF
    copies go to originals\\, each with its row (with its SHA-256)."""
    assert backup(env) == 0
    assert TIFFS <= backed(env.dest)
    assert backup(env, "--tiff", "jxl") == 0
    assert TIFFS <= under(env.dest, ORIGINALS) and not TIFFS & backed(env.dest)
    assert len(originals_rows(env)) == 3


def originals_rows(env):
    return db(env.dest, "SELECT stored_rel, source_rel, size, sha256, stored_at FROM originals")


def stored_path(env, rel):
    """Where the TIFF copy of `rel` is kept now (its originals row)."""
    wanted = os.path.join(*rel.split("/")).casefold()
    found = [stored for stored, source, _size, _sha, _at in originals_rows(env)
             if source.casefold() == wanted]
    assert len(found) == 1, found
    return env.dest / found[0]


def decodes(env):
    if not env.calls.exists():
        return []
    import json
    calls = [json.loads(path.read_text(encoding="utf-8")) for path in env.calls.glob("*.json")]
    return [call for call in calls if call[0] == "decode" and "--version" not in call]


def log_lines(log_dir, prefix):
    return [line for line in latest_log(log_dir).splitlines() if line.startswith(prefix)]


def put_back_done(log_dir):
    """The PUT BACK lines of the files renamed back (not PLANNED, FAILED, ...)."""
    return [line for line in log_lines(log_dir, "PUT BACK ") if "(renamed from " in line
            and not line.startswith("PUT BACK PLANNED")]


# ---- 1a the restore gives back the kept original --------------------------------------------------
def test_1a_the_restore_gives_back_the_kept_original(env, photobackup_log_dir):
    """(star) Each TIFF comes back as the copy kept in originals\\: its own bytes
    and date, no JXL decoded, the backup only read."""
    originals_after_jxl(env)
    before = snapshot(env.dest)
    decoded_before = len(decodes(env))
    assert restore(env, "--to", env.out) == 0
    assert snapshot(env.dest) == before
    statuses = restore_statuses(photobackup_log_dir)
    assert {statuses[rel] for rel in TIFFS} == {"RESTORED_ORIGINAL"}
    for rel in TIFFS:
        assert (env.out / rel).read_bytes() == (env.src / rel).read_bytes()
        assert (env.out / rel).stat().st_mtime_ns == (env.src / rel).stat().st_mtime_ns
    assert len(decodes(env)) == decoded_before
    rows = {row["rel"].replace("\\", "/"): row["detail"]
            for row in newest_csv(photobackup_log_dir, "restore")}
    assert ORIGINALS in rows["2025/scans/000038.tif"]
    # a second restore: nothing replaced
    assert restore(env, "--to", env.out) == 0
    assert {restore_statuses(photobackup_log_dir)[rel] for rel in TIFFS} == {"ALREADY_THERE"}


def test_1a_no_jxlphoto_is_needed_for_a_kept_original(env, monkeypatch, photobackup_log_dir):
    """(star) Without any jxl-photo the TIFFs with a kept original come back
    (NEEDS_JXLPHOTO does not fire for them)."""
    originals_after_jxl(env)
    missing = str(env.root / "no-jxl-photo.py")
    monkeypatch.setattr(sb, "JXLPHOTO", missing)
    assert restore(env, "--to", env.out, "--jxlphoto", missing) == 0
    assert {restore_statuses(photobackup_log_dir)[rel] for rel in TIFFS} == {"RESTORED_ORIGINAL"}


def test_1a_convert_back_lossless_takes_the_original_of_a_lossy_tiff(env, monkeypatch,
                                                                     photobackup_log_dir):
    """(star) A lossy JXL stays JXL with --convert-back lossless, unless its
    original is kept: that one is exact."""
    monkeypatch.setitem(sb.TIFF_JXL_PROFILES, "jxl", {"distance": 1.0, "scan_distance": 1.0,
                                                      "eight_bit_distance": 1.0})
    originals_after_jxl(env)
    assert restore(env, "--to", env.out, "--convert-back", "lossless") == 0
    assert {restore_statuses(photobackup_log_dir)[rel] for rel in TIFFS} == {"RESTORED_ORIGINAL"}
    assert backed(env.out) == OTHERS | TIFFS


def test_1a_without_proof_the_jxl_is_decoded(env, photobackup_log_dir):
    """No proof -- the kept copy changed (its row's hash no longer holds), a row
    without a hash whose file changed, a kept copy gone -- the JXL is decoded as
    before, no error."""
    originals_after_jxl(env)
    changed = stored_path(env, "2025/shoot/DSC_0001.tif")       # the row has its hash
    data = bytearray(changed.read_bytes())
    data[0] ^= 1
    stat = changed.stat()
    changed.write_bytes(bytes(data))
    os.utime(changed, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    unhashed = stored_path(env, "2025/scans/000037.tiff")       # no hash in the row, changed
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    with connection:
        connection.execute("UPDATE originals SET sha256 = NULL WHERE key = ?",
                           (os.path.relpath(unhashed, env.dest).casefold(),))
    connection.close()
    data = bytearray(unhashed.read_bytes())
    data[0] ^= 1
    unhashed.write_bytes(bytes(data))
    stored_path(env, "2025/scans/000038.tif").unlink()           # gone
    before = snapshot(env.dest)
    assert restore(env, "--to", env.out) == 0
    assert snapshot(env.dest) == before
    assert {restore_statuses(photobackup_log_dir)[rel] for rel in TIFFS} == {"RESTORED_FROM_JXL"}
    for rel in TIFFS:
        assert (env.out / rel).read_bytes() == (env.src / rel).read_bytes()
    log = latest_log(photobackup_log_dir)
    assert "ORIGINAL NOT USED " + os.path.join("2025", "shoot", "DSC_0001.tif") in log


def test_1a_a_row_without_a_hash_is_read_and_the_newest_wins(env, photobackup_log_dir):
    """(star) A row without SHA-256 is proof once its file is read; of several
    rows with proof, the newest is restored."""
    originals_after_jxl(env)
    rel = "2025/scans/000037.tiff"
    kept = stored_path(env, rel)
    stored_rel = os.path.relpath(kept, env.dest)
    other_rel = stored_rel.replace(kept.relative_to(env.dest).parts[2], "200101_120000", 1)
    other = env.dest / other_rel
    other.parent.mkdir(parents=True)
    other.write_bytes(kept.read_bytes())
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    with connection:
        connection.execute("UPDATE originals SET sha256 = NULL")
        size, mtime_ns = connection.execute("SELECT size, mtime_ns FROM originals WHERE key = ?",
                                            (stored_rel.casefold(),)).fetchone()
        connection.execute("INSERT INTO originals VALUES (?, ?, ?, ?, ?, NULL, ?)",
                           (other_rel.casefold(), other_rel, os.path.join(*rel.split("/")),
                            size, mtime_ns, "2099-01-01T00:00:00"))
    connection.close()
    assert restore(env, "--to", env.out, "--only", rel) == 0
    rows = {row["rel"].replace("\\", "/"): row for row in newest_csv(photobackup_log_dir,
                                                                     "restore")}
    assert rows[rel]["status"] == "RESTORED_ORIGINAL"
    assert other_rel in rows[rel]["detail"]                     # the newest
    assert (env.out / rel).read_bytes() == (env.src / rel).read_bytes()


# ---- 1b the plain TIFF copy is put back by a rename ------------------------------------------------
def test_1b_the_kept_original_is_renamed_back(env, capsys, photobackup_log_dir):
    """(star) The manifest says copy again: each TIFF comes back from originals\\
    by a rename (the same file), its row with its hash; its JXL leaves."""
    originals_after_jxl(env)
    ids = {rel: stored_path(env, rel).stat().st_ino for rel in TIFFS}
    source = snapshot(env.src)
    capsys.readouterr()
    assert backup(env) == 0                                 # --tiff copy
    out = capsys.readouterr().out
    assert snapshot(env.src) == source
    assert backed(env.dest) == OTHERS | TIFFS
    for rel in TIFFS:
        target = env.dest / rel
        assert target.read_bytes() == (env.src / rel).read_bytes()
        assert target.stat().st_ino == ids[rel]             # renamed, not copied
        assert target.stat().st_mtime_ns == (env.src / rel).stat().st_mtime_ns
        key = os.path.join(*rel.split("/")).casefold()
        assert db(env.dest, f"SELECT sha256 FROM files WHERE key = '{key}'") == [
            (sb._sha256_file(str(env.src / rel)),)]
    assert not under(env.dest, ORIGINALS) and originals_rows(env) == []
    assert JXL <= under(env.dest, VERSIONS)
    assert db(env.dest, "SELECT count(*) FROM converted") == [(0,)]
    assert row_count(out, f"TIFF put back from {ORIGINALS}\\ (renamed, not copied)") == "3"
    assert "3 TIFF put back from originals\\" in out
    assert len(put_back_done(photobackup_log_dir)) == 3
    assert backup(env) == 0                                 # a third run: nothing to do
    assert not log_lines(photobackup_log_dir, "PUT BACK")


def test_1b_a_dry_run_says_it_and_changes_nothing(env, capsys):
    originals_after_jxl(env)
    before = snapshot(env.dest)
    capsys.readouterr()
    assert backup(env, "--dry-run") == 0
    assert row_count(capsys.readouterr().out,
                     f"TIFF put back from {ORIGINALS}\\ (renamed, not copied)") == "3"
    assert snapshot(env.dest) == before


def test_1b_a_rename_that_fails_copies_from_the_source(env, monkeypatch, photobackup_log_dir):
    """(star) The kept file stays where it is with its row; the TIFF is copied
    from the source in the same run; its JXL still leaves (the copy's proof)."""
    originals_after_jxl(env)
    kept = {rel: stored_path(env, rel) for rel in TIFFS}
    real = sb._rename_no_clobber

    def refuse(src, dst):
        if os.sep + ORIGINALS + os.sep in str(src):
            raise PermissionError(13, "the kept file is in use")
        return real(src, dst)
    monkeypatch.setattr(sb, "_rename_no_clobber", refuse)
    assert backup(env) == 0
    assert backed(env.dest) == OTHERS | TIFFS
    for rel, path in kept.items():
        assert path.exists() and (env.dest / rel).stat().st_ino != path.stat().st_ino
        assert (env.dest / rel).read_bytes() == (env.src / rel).read_bytes()
    assert len(originals_rows(env)) == 3
    assert JXL <= under(env.dest, VERSIONS)
    assert len(log_lines(photobackup_log_dir, "PUT BACK FAILED ")) == 3


def test_1b_no_proof_copies_from_the_source(env, photobackup_log_dir):
    """A kept copy whose date is not the source's is no proof: the TIFF is
    copied, the kept copy stays."""
    originals_after_jxl(env)
    path = stored_path(env, "2025/scans/000037.tiff")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    assert backup(env) == 0
    assert path.exists()
    assert len(log_lines(photobackup_log_dir, "PUT BACK NOT POSSIBLE ")) == 1
    assert len(put_back_done(photobackup_log_dir)) == 2
    assert backed(env.dest) == OTHERS | TIFFS


def test_1b_the_mirror_copies(env, monkeypatch):
    """Not in this round: the mirror copies (its kept originals are in
    trash_originals\\, never put back)."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    man_copy = write_manifest(env.root / "copy.csv", (env.src / "2025", "6", "", "", ""))
    man_jxl = write_manifest(env.root / "jxl.csv", (env.src / "2025", "6", "", "", "jxl"))
    assert mir.main(["--manifest", str(man_copy), "--dest", str(env.dest), "--yes"]) == 0
    assert mir.main(["--manifest", str(man_jxl), "--dest", str(env.dest), "--yes"]) == 0
    trash = under(env.dest, mir.MIRROR_TRASH_ORIGINALS_FOLDER)
    assert mir.main(["--manifest", str(man_copy), "--dest", str(env.dest), "--yes"]) == 0
    assert under(env.dest, mir.MIRROR_TRASH_ORIGINALS_FOLDER) == trash
    assert {mirror_rel(env, rel) for rel in TIFFS} <= backed(env.dest)


# ---- N8 a time on every log line ---------------------------------------------------------------------
LINE_TIME = re.compile(r"^\d{2}:\d{2}:\d{2} ")
FIRST_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ")


def test_n8_every_log_line_starts_with_its_time(env, capsys, photobackup_log_dir):
    """(star) The backup's log and the restore's: the first line with its date,
    every other with its time; the console shows the messages as they are."""
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl") == 0
    out = capsys.readouterr().out
    for raw in (latest_log_raw(photobackup_log_dir),):
        lines = raw.splitlines()
        assert FIRST_TIME.match(lines[0]), lines[0]
        assert all(LINE_TIME.match(line) for line in lines[1:]), [
            line for line in lines[1:] if not LINE_TIME.match(line)][:3]
    assert not any(LINE_TIME.match(line) or FIRST_TIME.match(line) for line in out.splitlines())
    assert restore(env, "--to", env.out) == 0
    lines = latest_log_raw(photobackup_log_dir).splitlines()
    assert FIRST_TIME.match(lines[0]) and all(LINE_TIME.match(line) for line in lines[1:])
    assert "restore_backup" in latest_log(photobackup_log_dir).splitlines()[0]


def test_n8_a_new_day_is_said_once(tmp_path, monkeypatch):
    """(star) A message of several lines: each line its time; the day that
    changes during a run: one line `--- YYYY-MM-DD ---`."""
    times = iter([datetime(2026, 11, 27, 23, 59, 58), datetime(2026, 11, 27, 23, 59, 59),
                  datetime(2026, 11, 28, 0, 0, 1), datetime(2026, 11, 28, 0, 0, 2)])
    monkeypatch.setattr(sb, "_log_clock", lambda: next(times))
    log = sb._Logger(str(tmp_path / "logs" / "n8"))
    try:
        log.log("first")
        log.log("two\nlines")
        log.log("after midnight")
        log.log("next")
    finally:
        log.close()
    assert Path(log.path).read_text(encoding="utf-8").splitlines() == [
        "2026-11-27 23:59:58 first", "23:59:59 two", "23:59:59 lines",
        "--- 2026-11-28 ---", "00:00:01 after midnight", "00:00:02 next"]
    assert sb._strip_log_times(Path(log.path).read_text(encoding="utf-8")).splitlines() == [
        "first", "two", "lines", "after midnight", "next"]


# ---- 3a the mirror says what took the place of a copy retired without a JXL ---------------------------
def test_3a_the_mirrors_log_says_copied_as_it_is(env, monkeypatch, photobackup_log_dir):
    """(star) Conversion off: the old copy of an edited TIFF leaves because the
    new copy as it is takes its place, not a JXL."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    man = write_manifest(env.root / "m.csv", (env.src / "2025", "6", "", "", "jxl"))
    common = ["--manifest", str(man), "--dest", str(env.dest), "--no-convert"]
    assert mir.main(common + ["--yes"]) == 0
    tiff = env.src / "2025/scans/000037.tiff"
    tiff.write_bytes(tiff.read_bytes() + b"edited")
    stat = tiff.stat()
    os.utime(tiff, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    assert mir.main(common + ["--yes"]) == 0
    lines = [line for line in latest_log(photobackup_log_dir).splitlines()
             if line.startswith("MIRROR TRASH") and "000037.tiff" in line]
    assert lines and all("replaced by its JXL" not in line for line in lines), lines
    assert all("copied as it is (conversion off" in line for line in lines), lines


# ---- 3b Ctrl+C ----------------------------------------------------------------------------------------
def ctrl_c_after(monkeypatch, name, when=lambda *args: True, times=1):
    """Ctrl+C right after the module function `name` did its work (once);
    `pressed` says it was, `pressed.release()` puts the function back."""
    real = getattr(sb, name)

    class Pressed(list):
        def release(self):
            monkeypatch.setattr(sb, name, real)

    pressed = Pressed()

    def pressing(*args, **kwargs):
        result = real(*args, **kwargs)
        if len(pressed) < times and when(*args):
            pressed.append(True)
            signal.raise_signal(signal.SIGINT)
        return result
    monkeypatch.setattr(sb, name, pressing)
    return pressed


def test_3b_ctrl_c_while_a_jxl_is_undone(env, monkeypatch, photobackup_log_dir):
    """The JXL moved aside and its rows go together; the plain copy is in
    place with its row; the next run finishes."""
    assert backup(env, "--jpeg", "jxl") == 0
    export = "2025/shoot/_EXPORT/DSC_0001_v1.jpg"
    pressed = ctrl_c_after(monkeypatch, "_move_file_to_versions")
    assert backup(env, "--jpeg", "copy") == 130
    assert pressed
    assert export + ".jxl" not in backed(env.dest) and export + ".jxl" in under(env.dest, VERSIONS)
    assert db(env.dest, "SELECT count(*) FROM converted") == [(0,)]
    assert db(env.dest, "SELECT count(*) FROM files WHERE key LIKE '%.jxl'") == [(0,)]
    key = os.path.join(*export.split("/")).casefold()
    assert db(env.dest, f"SELECT count(*) FROM files WHERE key = '{key}'") == [(1,)]
    pressed.release()
    assert backup(env, "--jpeg", "copy") == 0


def expired_originals(env):
    """The run folder of originals\\ dated 40 days ago, its rows with it."""
    rows = originals_rows(env)
    stamp = Path(rows[0][0]).parts[2]
    old = (datetime.now().replace(microsecond=0)
           - __import__("datetime").timedelta(days=40)).strftime("%y%m%d_%H%M%S")
    folder = env.dest / sb.CATALOG_FOLDER / ORIGINALS
    (folder / stamp).rename(folder / old)
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    with connection:
        for stored_rel, *_rest in rows:
            new = stored_rel.replace(stamp, old, 1)
            connection.execute("UPDATE originals SET key = ?, stored_rel = ? WHERE key = ?",
                               (new.casefold(), new, stored_rel.casefold()))
    connection.close()


def consistent_originals(env):
    """Every originals row has its file, every kept file has its row."""
    rows = {stored.casefold() for stored, *_rest in originals_rows(env)}
    root = env.dest / sb.CATALOG_FOLDER / ORIGINALS
    files = {str(path.relative_to(env.dest)).casefold() for path in root.rglob("*")
             if path.is_file()} if root.exists() else set()
    return rows == files, rows, files


def test_3b_ctrl_c_during_the_expiry_purge(env, monkeypatch, photobackup_log_dir):
    """(star) Ctrl+C right after a kept file is deleted by age: its row is gone
    with it (it was left behind), the others keep theirs; exit 130."""
    originals_after_jxl(env)
    expired_originals(env)
    pressed = ctrl_c_after(monkeypatch, "_delete_backup_file")
    assert backup(env, "--tiff", "jxl", "--originals-keep-days", "30") == 130
    assert pressed
    same, rows, files = consistent_originals(env)
    assert same, (rows, files)
    assert len(files) == 2


def test_3b_ctrl_c_during_the_room_choosers_deletion(plain, monkeypatch):
    """(star) What a person chose to delete: Ctrl+C after the first file, its
    row gone with it, exit 130."""
    old, newer, tiff = room_env(plain)
    second = tiff / "d.tif"
    second.write_bytes(b"t" * 10)
    catalog = sb._Catalog(str(plain.dest), create=True)
    for path in (tiff / "c.tif", second):
        rel = os.path.relpath(path, plain.dest)
        catalog.record_original(rel, os.path.basename(rel), path.stat().st_size, 0, None)
    catalog.commit()
    catalog.close()
    grows(monkeypatch, 1000, old, newer, tiff)
    lines = []
    real_out = sb._Logger.out
    monkeypatch.setattr(sb._Logger, "out", lambda self, message: (lines.append(message),
                                                                   real_out(self, message)))
    answers(monkeypatch, "")                        # see the options first
    assert plain_backup(plain, "--space-margin-gb", "0") == 2
    number = next(re.match(r"\s+(\d)\)", line).group(1) for line in lines
                  if re.match(r"\s+\d\) TIFF originals: all of it", line))
    pressed = ctrl_c_after(monkeypatch, "_delete_backup_file")
    answers(monkeypatch, number, "2", "y")
    assert plain_backup(plain, "--space-margin-gb", "0") == 130
    assert pressed
    rows = {stored.casefold() for (stored,) in db(plain.dest, "SELECT stored_rel FROM originals")}
    files = {str(path.relative_to(plain.dest)).casefold() for path in tiff.rglob("*")
             if path.is_file()} if tiff.exists() else set()
    assert rows == files and len(files) == 1


def test_3b_ctrl_c_while_a_tiff_is_put_back(env, monkeypatch, photobackup_log_dir):
    """The rename and the rows go together: the TIFF is at its place with its
    row, its originals row gone; its JXL stays for the next run, which retires it."""
    originals_after_jxl(env)
    pressed = ctrl_c_after(monkeypatch, "_rename_no_clobber",
                           when=lambda src, dst: os.sep + ORIGINALS + os.sep in str(src))
    assert backup(env) == 130
    assert pressed
    back = [rel for rel in TIFFS if (env.dest / rel).exists()]
    assert len(back) == 1
    key = os.path.join(*back[0].split("/")).casefold()
    assert db(env.dest, f"SELECT count(*) FROM files WHERE key = '{key}' AND sha256 IS NOT NULL"
              ) == [(1,)]
    assert len(originals_rows(env)) == 2 and consistent_originals(env)[0]
    pressed.release()
    assert backup(env) == 0
    assert backed(env.dest) == OTHERS | TIFFS
    assert JXL <= under(env.dest, VERSIONS) and originals_rows(env) == []


# ---- 4a lossless only when every page is --------------------------------------------------------------
def test_4a_a_multipage_tiff_with_a_lossy_page_is_not_lossless(env, monkeypatch,
                                                                photobackup_log_dir):
    """(star) Page 0 lossless, page 1 lossy (an 8-bit page with a distance): the
    group is lossy, and --convert-back lossless keeps it as JXL."""
    monkeypatch.setitem(sb.TIFF_JXL_PROFILES, "jxl", {"distance": 0, "scan_distance": 0,
                                                      "eight_bit_distance": 0.5})
    (env.src / "2025/scans/mixed.tif").write_text("TIFF16 mixed PAGES=2 PAGE1_EIGHT " * 20,
                                                  encoding="utf-8")
    assert backup(env, "--tiff", "jxl") == 0
    rows = db(env.dest, "SELECT page, lossless FROM converted WHERE source_key LIKE '%mixed.tif'")
    assert sorted(rows) == [(0, 0), (1, 0)]
    assert db(env.dest, "SELECT lossless FROM converted WHERE source_key LIKE '%dsc_0001.tif'"
              ) == [(1,)]
    assert restore(env, "--to", env.out, "--convert-back", "lossless") == 0
    statuses = restore_statuses(photobackup_log_dir)
    assert statuses["2025/scans/mixed.tif.jxl"] == "RESTORED_AS_JXL"
    assert statuses["2025/shoot/DSC_0001.tif"] == "RESTORED_FROM_JXL"


def test_4a_group_lossless():
    """(star)"""
    assert sb._group_lossless([1, 1, 1]) == 1
    assert sb._group_lossless([1, 0]) == 0
    assert sb._group_lossless([1, None]) is None
    assert sb._group_lossless([None, 0]) == 0
    assert sb._group_lossless([]) is None


# ---- 4b not_converted rows of a gone source --------------------------------------------------------------
def test_4b_a_gone_sources_not_converted_row_is_forgotten(failing, monkeypatch,
                                                         photobackup_log_dir):
    """(star) Not planned and its source proven gone: the row goes in a real run
    (not in a dry-run, not when its source cannot be read)."""
    env = failing
    assert backup(env, "--jpeg", "jxl") == 1
    assert [row[0].replace("\\", "/") for row in not_converted(env)] == ["2025/phone/" + BAD]
    (env.src / "2025/phone" / BAD).unlink()
    monkeypatch.delenv("FAKE_JXLPHOTO_FAIL")
    assert backup(env, "--jpeg", "jxl", "--dry-run") == 0
    assert len(not_converted(env)) == 1
    real = sb._source_state
    monkeypatch.setattr(sb, "_source_state", lambda path: (
        sb.SOURCE_UNREADABLE if BAD in str(path) else real(path)))
    assert backup(env, "--jpeg", "jxl") == 0
    assert len(not_converted(env)) == 1                 # unreadable: no proof
    monkeypatch.setattr(sb, "_source_state", real)
    assert backup(env, "--jpeg", "jxl") == 0
    assert not_converted(env) == []
    assert log_lines(photobackup_log_dir, "NOT CONVERTED FORGOTTEN ")


# ---- 4c a rename refused as "another device" ---------------------------------------------------------------
def test_4c_a_cross_device_rename_takes_the_copy_route(env, monkeypatch, capsys,
                                                       photobackup_log_dir):
    """(star) Two volumes with one serial number: st_dev says one volume, the
    rename says otherwise; the group and the rest go the copy route, said once."""
    monkeypatch.setattr(sb, "_jxl_copy_needed", lambda work_root, dest: False)
    work = os.path.normcase(str(sb.JXL_WORK_DIR))
    real = sb._rename_no_clobber
    refused = []

    def rename(src, dst):
        if os.path.normcase(str(src)).startswith(work):
            refused.append(src)
            raise OSError(errno.EXDEV, "The system cannot move the file to a different disk "
                                       "drive")
        return real(src, dst)
    monkeypatch.setattr(sb, "_rename_no_clobber", rename)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl") == 0
    out = capsys.readouterr().out
    assert refused
    assert backed(env.dest) == OTHERS | JXL
    assert db(env.dest, "SELECT count(*) FROM converted") == [(5,)]
    assert out.count("different volumes after all") == 1
    assert "CONVERT FAILED" not in latest_log(photobackup_log_dir)


def test_4c_cross_device():
    """(star)"""
    assert sb._cross_device(OSError(errno.EXDEV, "x"))
    error = OSError(1, "x")
    error.winerror = 17
    assert sb._cross_device(error)
    assert not sb._cross_device(PermissionError(13, "x"))


# ---- the review of rc9 (AI_tools/261127_Opus_review_rc9.md): M1, S1, S2, S3 --------------------------
def test_m1_a_junction_inside_originals_never_moves_a_source_file(env, photobackup_log_dir):
    """(star) A junction planted below originals\\<stamp> that leads into the
    source makes 'the kept file' the SOURCE file (its size, date and SHA-256
    are the proof): it is never put back -- no proof, copied from the source;
    the source stays as it was."""
    originals_after_jxl(env)
    rel = "2025/shoot/DSC_0001.tif"
    folder = stored_path(env, rel).parent                 # originals\<stamp>\2025\shoot
    shutil.rmtree(folder)
    _winapi.CreateJunction(str(env.src / "2025" / "shoot"), str(folder))
    try:
        assert stored_path(env, rel).exists()             # through the junction: the source file
        source = snapshot(env.src)
        assert backup(env) == 0
        assert snapshot(env.src) == source, "a source file was moved"
        assert (env.dest / rel).read_bytes() == (env.src / rel).read_bytes()
        assert not os.path.samefile(env.dest / rel, env.src / rel)
        refused = log_lines(photobackup_log_dir,
                            "PUT BACK NOT POSSIBLE " + os.path.join("2025", "shoot", "DSC_0001.tif"))
        assert refused and "a link or junction on the way" in refused[0], refused
        assert len(put_back_done(photobackup_log_dir)) == 2
        assert backed(env.dest) == OTHERS | TIFFS
    finally:
        os.rmdir(folder)                                  # the junction only, never its target


def test_m1_the_proof_asks_the_folders_and_the_real_path(tmp_path):
    """(star) _kept_original_proof with `dest`/`stored_rel`: a junction on the
    way is no proof; a plain file at its place is."""
    dest = tmp_path / "dest"
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "a.tif").write_bytes(b"x" * 10)
    run = dest / sb.CATALOG_FOLDER / ORIGINALS / "261127_120000"
    run.mkdir(parents=True)
    plain_dir = run / "plain"
    plain_dir.mkdir()
    (plain_dir / "a.tif").write_bytes(b"x" * 10)
    _winapi.CreateJunction(str(other), str(run / "linked"))
    try:
        sha = sb._sha256_file(str(other / "a.tif"))
        for name, expected in (("plain", None), ("linked", "link")):
            path = run / name / "a.tif"
            info = path.stat()
            planned = sb.PlannedFile(str(path), "a.tif", 10, info.st_mtime_ns, "x")
            stored_rel = os.path.relpath(path, dest)
            have, why = sb._kept_original_proof(str(path), 10, info.st_mtime_ns, sha, sha,
                                                planned, dest=str(dest), stored_rel=stored_rel)
            if expected is None:
                assert why is None and have == sha
            else:
                assert have is None and "link or junction" in why, why
    finally:
        os.rmdir(run / "linked")


def appear_at(monkeypatch, rel, name="_put_back_originals"):
    """Another program writes `rel` in the backup right before `name` runs
    (the write phase of the put-back, or its rename)."""
    real = getattr(sb, name)

    def appear(*args, **kwargs):
        if name == "_put_back_originals":
            target = Path(args[1]) / rel
        else:
            target = Path(args[1])
            if not str(target).casefold().endswith(os.path.join(*rel.split("/")).casefold()):
                return real(*args, **kwargs)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"someone else's file")
        return real(*args, **kwargs)
    monkeypatch.setattr(sb, name, appear)
    return real


@pytest.mark.parametrize("when", ["_put_back_originals", "_rename_no_clobber"])
def test_s1_a_file_that_appears_at_the_place_is_left_alone(env, monkeypatch, capsys, when,
                                                          photobackup_log_dir):
    """(star) A file that appears at the TIFF's place between the plan and the
    put-back (or right before its rename) is never replaced: left as it is in
    this run, an error (exit 1), the kept original and its JXL stay."""
    originals_after_jxl(env)
    rel = "2025/shoot/DSC_0001.tif"
    kept = stored_path(env, rel)
    real = appear_at(monkeypatch, rel, when)
    capsys.readouterr()
    assert backup(env) == 1
    out = capsys.readouterr().out
    assert (env.dest / rel).read_bytes() == b"someone else's file"
    assert kept.exists() and len(originals_rows(env)) == 1
    assert "2025/shoot/DSC_0001.tif.jxl" in backed(env.dest)
    failed = log_lines(photobackup_log_dir, "PUT BACK FAILED ")
    assert len(failed) == 1 and "a file appeared at its place" in failed[0], failed
    assert "left as it is" in failed[0] and "copied from the source" not in failed[0]
    assert len(put_back_done(photobackup_log_dir)) == 2
    assert "1 left as they are" in out
    monkeypatch.setattr(sb, when, real)
    backup(env)                                           # the next run: CHANGED, never replaced
    assert (env.dest / rel).read_bytes() == b"someone else's file"


def test_s2_an_expiring_run_folder_holds_a_planned_put_back(env, capsys, photobackup_log_dir):
    """(star) The originals run folder expires in the same run that would put
    its files back: they are not put back (the purge deletes them), they are
    copied from the source, counted as copies -- never a PUT BACK FAILED."""
    originals_after_jxl(env)
    expired_originals(env)
    capsys.readouterr()
    assert backup(env, "--originals-keep-days", "30") == 0
    out = capsys.readouterr().out
    assert not log_lines(photobackup_log_dir, "PUT BACK FAILED ")
    cleared = log_lines(photobackup_log_dir, "PUT BACK NOT POSSIBLE ")
    assert len(cleared) == 3 and all("deleted in this run" in line for line in cleared), cleared
    assert row_count(out, f"TIFF put back from {ORIGINALS}\\ (renamed, not copied)") is None
    assert backed(env.dest) == OTHERS | TIFFS and originals_rows(env) == []
    for rel in TIFFS:
        assert (env.dest / rel).read_bytes() == (env.src / rel).read_bytes()


def put_back_space(env):
    """The kept originals' run folder, and a new file of half their size: the
    disk fits the new file only when the TIFFs are put back, not copied."""
    run = stored_path(env, "2025/shoot/DSC_0001.tif").parents[2]     # originals\<stamp>
    tiffs = sum((env.src / rel).stat().st_size for rel in TIFFS)
    (env.src / "2025/scans/new.txt").write_bytes(b"n" * (tiffs // 2))
    return run


def test_s2_an_expiring_put_back_counts_as_a_copy_in_the_space(env, monkeypatch,
                                                              photobackup_log_dir):
    """(star) What the expiry frees is the put-backs' own bytes: copied from the
    source they need it again. A disk that does not fit stops before the first
    byte (exit 2), nothing deleted."""
    originals_after_jxl(env)
    expired_originals(env)
    run = put_back_space(env)
    grows(monkeypatch, 0, run)
    before = snapshot(env.dest)
    assert backup(env, "--originals-keep-days", "30", "--space-margin-gb", "0") == 2
    assert snapshot(env.dest) == before


def test_s2_the_room_chooser_picks_the_folder_of_a_planned_put_back(env, monkeypatch,
                                                                   photobackup_log_dir):
    """(star) A person chooses to delete the run folder that holds the planned
    put-backs: they are then copies, and what was chosen no longer makes room --
    exit 2 before anything is deleted."""
    originals_after_jxl(env)
    run = put_back_space(env)
    grows(monkeypatch, 0, run)
    lines = []
    real_out = sb._Logger.out
    monkeypatch.setattr(sb._Logger, "out", lambda self, message: (lines.append(message),
                                                                   real_out(self, message)))
    argv = argv_for(env.src / "2025", env.dest, env.src, "--unexported", "copy",
                    "--export-formats", "all", "--space-margin-gb", "0")
    answers(monkeypatch, "")                              # see the options first
    assert sb.main(argv) == 2
    number = next(re.match(r"\s+(\d)\)", line).group(1) for line in lines
                  if re.match(r"\s+\d\) TIFF originals: all of it", line))
    before = snapshot(env.dest)
    answers(monkeypatch, number, "3", "y")
    assert sb.main(argv) == 2
    assert snapshot(env.dest) == before
    assert any("put back" in line and "chosen" in line for line in lines), lines[-8:]


def break_kept(env, rel):
    """The kept original of `rel` gets other bytes, its size and date kept (its
    row's SHA-256 no longer holds; found only when it is copied)."""
    path = stored_path(env, rel)
    data = bytearray(path.read_bytes())
    data[0] ^= 1
    stat = path.stat()
    path.write_bytes(bytes(data))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_s3_lossless_never_decodes_a_lossy_jxl_after_all(env, monkeypatch, photobackup_log_dir):
    """(star) --convert-back lossless, a LOSSY group whose kept original turns
    out other bytes at the copy: restored as its JXL (RESTORED_AS_JXL), never
    decoded into a TIFF; a lossless one is decoded as before."""
    monkeypatch.setitem(sb.TIFF_JXL_PROFILES, "jxl", {"distance": 1.0, "scan_distance": 1.0,
                                                      "eight_bit_distance": 1.0})
    originals_after_jxl(env)
    for rel in ("2025/shoot/DSC_0001.tif", "2025/scans/000038.tif"):
        break_kept(env, rel)
    decoded_before = len(decodes(env))
    before = snapshot(env.dest)
    assert restore(env, "--to", env.out, "--convert-back", "lossless") == 0
    assert snapshot(env.dest) == before
    statuses = restore_statuses(photobackup_log_dir)
    as_jxl = {"2025/shoot/DSC_0001.tif.jxl", "2025/scans/000038.tif.jxl",
              "2025/scans/000038_page1.tif.jxl", "2025/scans/000038_page2.tif.jxl"}
    assert {statuses.get(rel) for rel in as_jxl} == {"RESTORED_AS_JXL"}, statuses
    assert "2025/shoot/DSC_0001.tif" not in statuses and "2025/scans/000038.tif" not in statuses
    assert statuses["2025/scans/000037.tiff"] == "RESTORED_ORIGINAL"
    for rel in as_jxl:
        assert (env.out / rel).read_bytes() == (env.dest / rel).read_bytes()
    assert not (env.out / "2025/shoot/DSC_0001.tif").exists()
    assert len(decodes(env)) == decoded_before
    log = latest_log(photobackup_log_dir)
    assert "ORIGINAL NOT USED " + os.path.join("2025", "shoot", "DSC_0001.tif") in log
    assert "restored as its JXL" in log
