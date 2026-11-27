"""Pause and resume a long run (v0.14.0-rc10, plan 261127 resume): a real run saves
its plan in the backup before the copy; --resume goes on with it without listing
the sources again, each file checked first, and refuses (exit 2, nothing changed)
a plan that does not match."""
import csv
import gzip
import hashlib
import io
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import manifest_backup as mb
import mirror_backup as mir
import selective_backup as sb

from conftest import argv_for, latest_csv, latest_log, row_count, snapshot
from test_tiff_jxl import (FAKE, JXL, OTHERS, TIFFS, backed, backup, closed_console,  # noqa: F401
                           converted, db, encodes, env, mirror_rel, write_manifest)


@pytest.fixture(autouse=True)
def saving(monkeypatch):
    monkeypatch.setattr(sb, "SAVE_RESUME_PLAN", True)


def stop_after_first_group(monkeypatch, exc=KeyboardInterrupt):
    """The j14 pattern: the first JXL group is put in place, the second raises
    `exc`. Returns a function that puts the real _place_group back."""
    real = sb._place_group
    placed = []

    def place_then_stop(*args, **kwargs):
        if placed:
            raise exc
        placed.append(real(*args, **kwargs))
    monkeypatch.setattr(sb, "_place_group", place_then_stop)
    return lambda: monkeypatch.setattr(sb, "_place_group", real)


def meta(dest):
    return dict(db(dest, "SELECT k, v FROM meta"))


def plan_file(dest):
    return Path(dest, sb.CATALOG_FOLDER, sb.RESUME_FILE)


def plan_lines(dest):
    with gzip.open(plan_file(dest), "rt", encoding="ascii") as handle:
        return [json.loads(line) for line in handle]


def no_listing(monkeypatch):
    def listed(*args, **kwargs):
        raise AssertionError("the sources were listed")
    monkeypatch.setattr(sb, "plan", listed)
    monkeypatch.setattr(sb._Planner, "build", listed)


def stopped(env, monkeypatch, *extra):
    """A run stopped by Ctrl+C after the first JXL group; the real _place_group back."""
    put_back = stop_after_first_group(monkeypatch)
    assert backup(env, "--tiff", "jxl", *extra) == 130
    put_back()


def groups(dest):
    return {source for source, *_rest in converted(dest).values()}


# ---- R1 the point ------------------------------------------------------------------------------
def test_r1_a_stopped_run_goes_on_without_listing_the_sources(env, monkeypatch, capsys,
                                                             photobackup_log_dir):
    source_before = snapshot(env.src)
    put_back = stop_after_first_group(monkeypatch)
    assert backup(env, "--tiff", "jxl") == 130
    assert len(groups(env.dest)) == 1
    assert plan_file(env.dest).is_file()
    recorded = meta(env.dest)
    assert recorded.get("resume_plan") and "resume_done" not in recorded
    before = len(encodes(env))
    put_back()
    no_listing(monkeypatch)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--resume") == 0
    assert backed(env.dest) == OTHERS | JXL
    assert len(encodes(env)) - before == 2         # only the groups not in the catalog
    recorded = meta(env.dest)
    assert recorded["resume_done"] == recorded["resume_plan"]
    out = capsys.readouterr().out
    assert "RESUME the plan" in latest_log(photobackup_log_dir)
    assert "Resume:" in out and "the sources were not listed again" in out
    assert snapshot(env.src) == source_before


def test_r2_a_finished_plan_says_nothing_to_resume(env, capsys):
    assert backup(env, "--tiff", "jxl") == 0
    before = snapshot(env.dest)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--resume") == 2
    assert "Nothing to resume" in capsys.readouterr().err
    assert snapshot(env.dest) == before             # the catalog byte for byte


def test_r3_no_saved_plan(env, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sb, "SAVE_RESUME_PLAN", False)
    stopped(env, monkeypatch)
    before = snapshot(env.dest)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--resume") == 2
    assert "No saved plan" in capsys.readouterr().err
    assert snapshot(env.dest) == before
    fresh = tmp_path / "nowhere" / "dest"
    assert sb.main(argv_for(env.src / "2025", fresh, env.src, "--yes", "--unexported", "copy",
                            "--export-formats", "all", "--tiff", "jxl", "--resume")) == 2
    assert "No saved plan" in capsys.readouterr().err
    assert not fresh.exists() and not fresh.parent.exists()


# ---- R4 a plan that does not match -------------------------------------------------------------
def _change_option(env, monkeypatch):
    return ("--update-changed",), "--update-changed"


def _change_setting(env, monkeypatch):
    monkeypatch.setattr(sb, "TIFF_JXL_EFFORT", 6)
    return (), "TIFF_JXL_EFFORT"


def _change_version(env, monkeypatch):
    monkeypatch.setattr(sb, "__version__", "0.14.0-rc99")
    return (), "version"


def _change_backup_id(env, monkeypatch):
    path = Path(env.dest, sb.CATALOG_FOLDER, sb.BACKUP_ID_FILE)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["backup_id"] = "f" * 32
    path.write_text(json.dumps(data), encoding="utf-8")
    return (), "backup"


def _change_age(env, monkeypatch):
    monkeypatch.setattr(sb, "_resume_now", lambda: datetime.now()
                        + timedelta(days=sb.RESUME_MAX_DAYS, hours=1))
    return (), "RESUME_MAX_DAYS"


def _change_damage(env, monkeypatch):
    path = plan_file(env.dest)
    data = path.read_bytes()
    path.write_bytes(data[:len(data) // 2])
    return (), "plan"


def _change_sources(env, monkeypatch):
    return None, "--source"


def _change_off(env, monkeypatch):
    monkeypatch.setattr(sb, "RESUME_MAX_DAYS", 0)
    return (), "RESUME_MAX_DAYS"


CHANGES = {"option": _change_option, "setting": _change_setting, "version": _change_version,
           "backup id": _change_backup_id, "too old": _change_age, "damaged": _change_damage,
           "other sources": _change_sources, "off": _change_off}


@pytest.mark.parametrize("change", list(CHANGES), ids=list(CHANGES))
def test_r4_a_plan_that_does_not_match_is_refused(env, monkeypatch, capsys, change):
    stopped(env, monkeypatch)
    extra, text = CHANGES[change](env, monkeypatch)
    before = snapshot(env.dest)
    source_before = snapshot(env.src)
    capsys.readouterr()
    if extra is None:               # the resume names another source
        code = sb.main(argv_for(env.src / "2025" / "scans", env.dest, env.src, "--yes",
                                "--unexported", "copy", "--export-formats", "all",
                                "--tiff", "jxl", "--resume"))
    else:
        code = backup(env, "--tiff", "jxl", "--resume", *extra)
    assert code == 2
    assert text in capsys.readouterr().err
    assert snapshot(env.dest) == before
    assert snapshot(env.src) == source_before
    assert "resume_done" not in meta(env.dest)


def test_r5_a_free_setting_may_change(env, monkeypatch):
    stopped(env, monkeypatch)
    monkeypatch.setattr(sb, "JXL_WORKERS", 1)
    monkeypatch.setattr(sb, "PROGRESS_EVERY_S", 1.0)
    assert backup(env, "--tiff", "jxl", "--resume") == 0
    assert backed(env.dest) == OTHERS | JXL


def test_r6_a_source_changed_or_gone_since_the_plan_is_left(env, monkeypatch, capsys,
                                                           photobackup_log_dir):
    stopped(env, monkeypatch)
    done = groups(env.dest)
    missing = sorted(rel for rel in TIFFS if rel not in done)
    assert len(missing) == 2
    changed, gone = missing
    path = env.src / changed
    stat = path.stat()
    with open(path, "ab") as handle:
        handle.write(b"more bytes")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    (env.src / gone).unlink()
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--resume") == 0
    out = capsys.readouterr().out
    have = backed(env.dest)
    assert changed + ".jxl" not in have and gone + ".jxl" not in have
    text = latest_log(photobackup_log_dir)
    left = [line for line in text.splitlines() if line.startswith("RESUME LEFT ")]
    assert len(left) == 2
    assert any("changed since the plan" in line for line in left)
    assert any("gone from the source" in line for line in left)
    rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir,
                                                      "_resume_left.csv"))))
    assert {row["rel"].replace("\\", "/") for row in rows} == {changed + ".jxl", gone + ".jxl"}
    assert row_count(out, "left for the next full run: changed or gone since") == "2"
    assert meta(env.dest).get("resume_done")
    assert backup(env, "--tiff", "jxl") == 0
    assert changed + ".jxl" in backed(env.dest)


def test_r7_a_resume_deletes_nothing_set_aside(env, monkeypatch, photobackup_log_dir):
    stopped(env, monkeypatch)
    old = Path(env.dest, sb.CATALOG_FOLDER, "versions", "200101_000000", "old", "x.bin")
    old.parent.mkdir(parents=True)
    old.write_bytes(b"12345678")
    assert backup(env, "--tiff", "jxl", "--resume", "--versions-keep-days", "1") == 0
    assert old.is_file()
    text = latest_log(photobackup_log_dir)
    assert "DELETED" not in text and "deleted for good" not in text
    assert backup(env, "--tiff", "jxl", "--versions-keep-days", "1") == 0
    assert not old.parent.parent.exists()           # the set-up was a valid expired run


def test_r8_usage_errors(env, capsys, photobackup_log_dir):
    for extra, named in ((("--move-orphans",), "--move-orphans"),
                         (("--verify", "quick"), "--verify"),
                         (("--adopt-by-hash",), "--adopt-by-hash")):
        assert backup(env, "--resume", *extra) == 2
        assert named in capsys.readouterr().err
        assert not env.dest.exists()
    assert sb.main(["--dest", str(env.dest), "--space-report", "--resume"]) == 2
    assert "--space-report" in capsys.readouterr().err
    assert not env.dest.exists()
    man = write_manifest(env.root / "m.csv", (env.src / "2025", "6", "", "", ""))
    assert mb.main(["--manifest", str(man), "--analyze", "--resume"]) == 2
    assert "--resume" in capsys.readouterr().err
    assert mir.main(["--manifest", str(man), "--dest", str(env.dest), "--resume"]) == 2
    capsys.readouterr()
    assert not env.dest.exists()
    log = sb._Logger(os.path.join(sb.LOG_DIR, "r8"))
    try:
        args = sb._build_parser().parse_args(argv_for(env.src / "2025", env.dest, env.src,
                                                      "--yes", "--resume"))
        assert sb._run_backup(args, log, "s", mirror=SimpleNamespace()) == 2
    finally:
        log.close()
    assert "the mirror never resumes" in capsys.readouterr().err
    assert not env.dest.exists()


def test_r9_what_the_plan_holds(env, monkeypatch):
    stopped(env, monkeypatch)
    lines = plan_lines(env.dest)
    header, trailer = lines[0], lines[-1]
    recorded = meta(env.dest)
    assert header["tool"] == sb._RESUME_TOOL
    assert header["format"] == 1
    assert header["kind"] == "selective_backup"
    assert header["version"] == sb.__version__
    assert header["backup_id"] == sb.read_backup_id(env.dest)
    assert header["plan_id"] == recorded["resume_plan"]
    digest = hashlib.sha256(plan_file(env.dest).read_bytes()).hexdigest()
    assert digest == recorded["resume_plan_sha256"]
    assert len(lines) == header["files"] + 2
    entries = lines[1:-1]
    assert trailer == {"end": True, "files": len(entries),
                       "open": sum(1 for entry in entries if entry[0] == "o")}
    for entry in entries:
        if entry[0] == "o":
            assert len(entry) == 7
            assert Path(entry[1]).is_file() and sb._is_under(entry[1], str(env.src))
    assert header["open"] == header["files"]        # a fresh destination: every file is NEW
    first = header["plan_id"]
    assert backup(env, "--tiff", "jxl") == 0
    recorded = meta(env.dest)
    assert recorded["resume_plan"] != first
    assert recorded["resume_done"] == recorded["resume_plan"]
    data = plan_file(env.dest).read_bytes()
    assert backup(env, "--tiff", "jxl") == 0       # nothing to do: nothing saved, nothing marked
    assert plan_file(env.dest).read_bytes() == data
    assert meta(env.dest) == recorded


def test_r10_a_run_that_died_goes_on_too(env, monkeypatch):
    put_back = stop_after_first_group(monkeypatch, SystemExit(9))
    with pytest.raises(SystemExit):
        backup(env, "--tiff", "jxl")
    put_back()
    assert backup(env, "--tiff", "jxl", "--resume") == 0
    assert backed(env.dest) == OTHERS | JXL


def test_r11_a_resume_dry_run_changes_nothing(env, monkeypatch):
    stopped(env, monkeypatch)
    before = snapshot(env.dest)
    assert backup(env, "--tiff", "jxl", "--resume", "--dry-run") == 0
    assert snapshot(env.dest) == before
    assert backup(env, "--tiff", "jxl", "--resume") == 0
    assert backed(env.dest) == OTHERS | JXL


def test_r12_a_forged_plan_is_damage(env, monkeypatch, capsys):
    stopped(env, monkeypatch)
    lines = plan_lines(env.dest)
    header = dict(lines[0], files=1, open=1)
    evil = ["o", str(env.src / "2025" / "scans" / "000037.tiff"), "..\\evil.tif.jxl", 10, 0,
            sb.CAT_UNEXPORTED, "jxl"]
    path = plan_file(env.dest)
    with gzip.open(path, "wb") as handle:
        for item in (header, evil, {"end": True, "files": 1, "open": 1}):
            handle.write((json.dumps(item) + "\n").encode("ascii"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    connection = sqlite3.connect(str(Path(env.dest, sb.CATALOG_FOLDER, "catalog.sqlite")))
    try:
        connection.execute("UPDATE meta SET v=? WHERE k='resume_plan_sha256'", (digest,))
        connection.commit()
    finally:
        connection.close()
    before = snapshot(env.dest)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--resume") == 2
    assert "damaged" in capsys.readouterr().err
    assert not (env.root / "evil.tif.jxl").exists()
    assert snapshot(env.dest) == before


def test_r13_the_manifest_resumes_and_refuses_another_manifest(env, monkeypatch, capsys):
    man = write_manifest(env.root / "jxl.csv", (env.src / "2025", "6", "", "", "jxl"))
    argv = ["--manifest", str(man), "--dest", str(env.dest), "--yes"]
    put_back = stop_after_first_group(monkeypatch)
    assert mb.main(argv) == 130
    put_back()
    capsys.readouterr()
    assert sb.main(argv_for(env.src / "2025", env.dest, env.src, "--yes", "--resume")) == 2
    assert "made by manifest_backup" in capsys.readouterr().err
    original = man.read_bytes()
    man.write_bytes(original + b"#x\r\n")
    assert mb.main(argv + ["--resume"]) == 2
    assert "manifest changed" in capsys.readouterr().err
    man.write_bytes(original)

    def listed(*args, **kwargs):
        raise AssertionError("the folders were read")
    monkeypatch.setattr(mb, "_plan_rows", listed)
    assert mb.main(argv + ["--resume"]) == 0
    assert {mirror_rel(env, rel) for rel in JXL} <= backed(env.dest)


def test_r14_a_finished_mirror_leaves_the_plan_to_its_resume(env, monkeypatch, capsys):
    # the review's S1: only the run that saved or resumed a plan marks it done;
    # the mirror knows nothing of it, and the resume after it finds nothing to do
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    man = write_manifest(env.root / "jxl.csv", (env.src / "2025", "6", "", "", "jxl"))
    argv = ["--manifest", str(man), "--dest", str(env.dest), "--yes"]
    put_back = stop_after_first_group(monkeypatch)
    assert mb.main(argv) == 130
    put_back()
    assert mir.main(argv) == 0
    recorded = meta(env.dest)
    assert recorded.get("resume_done") != recorded["resume_plan"]
    made = len(encodes(env))
    assert mb.main(argv + ["--resume"]) == 0
    assert len(encodes(env)) == made                # the mirror made them: nothing to do
    assert {mirror_rel(env, rel) for rel in JXL} <= backed(env.dest)
    capsys.readouterr()
    assert mb.main(argv + ["--resume"]) == 2
    assert "Nothing to resume" in capsys.readouterr().err


def test_r15_the_hint_after_ctrl_c(env, monkeypatch, capsys):
    monkeypatch.setattr(sb, "SAVE_RESUME_PLAN", False)
    capsys.readouterr()
    stopped(env, monkeypatch)
    assert "the same command with --resume" not in capsys.readouterr().out
    monkeypatch.setattr(sb, "SAVE_RESUME_PLAN", True)
    put_back = stop_after_first_group(monkeypatch)
    other = env.root / "dest2"
    assert sb.main(argv_for(env.src / "2025", other, env.src, "--yes", "--unexported", "copy",
                            "--export-formats", "all", "--tiff", "jxl")) == 130
    put_back()
    assert "the same command with --resume" in capsys.readouterr().out


@pytest.mark.parametrize("name, value", [("RESUME_MAX_DAYS", -1), ("RESUME_MAX_DAYS", True),
                                         ("RESUME_MAX_DAYS", "7"), ("SAVE_RESUME_PLAN", "yes"),
                                         ("RESUME_FILE", "..\\x"),
                                         ("RESUME_FILE", "catalog.sqlite")])
def test_r16_bad_resume_settings_are_refused_before_reading(env, monkeypatch, capsys, name,
                                                            value):
    monkeypatch.setattr(sb, name, value)
    assert backup(env, "--tiff", "jxl") == 2
    assert name in capsys.readouterr().err
    assert not env.dest.exists()
