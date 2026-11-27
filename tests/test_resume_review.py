"""The fixes after the review of rc10's pause and resume
(AI_tools/261127_Opus_review_resume.md): M1 (a resume keeps the plan's read
errors), S1 (only the plan a run saved or resumed is marked done), S2 (any
malformed plan file is a refusal, never a traceback), S3 (Ctrl+C still ends
with "the disk can be removed"), and the nits N1-N5."""
import gzip
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import selective_backup as sb

from conftest import argv_for, latest_log, row_count, snapshot
from test_resume import groups, meta, plan_file, plan_lines, stop_after_first_group, stopped
from test_tiff_jxl import (JXL, OTHERS, TIFFS, backed, backup, closed_console,  # noqa: F401
                           env)


@pytest.fixture(autouse=True)
def saving(monkeypatch):
    monkeypatch.setattr(sb, "SAVE_RESUME_PLAN", True)


def rewrite(env, lines, bind=True):
    """Write the plan file again (a str is written as it is) and, with `bind`,
    record its SHA-256 in the catalog, as a forged file would need."""
    path = plan_file(env.dest)
    with gzip.open(path, "wb") as handle:
        for item in lines:
            text = item if isinstance(item, str) else json.dumps(item)
            handle.write((text + "\n").encode("ascii"))
    if bind:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        connection = sqlite3.connect(str(Path(env.dest, sb.CATALOG_FOLDER, "catalog.sqlite")))
        try:
            connection.execute("UPDATE meta SET v=? WHERE k='resume_plan_sha256'", (digest,))
            connection.commit()
        finally:
            connection.close()


def junction(link, target):
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError, OSError) as exc:
        pytest.skip(f"cannot make a junction here ({exc})")


# ---- M1: the plan's read errors are not lost in a resume ----------------------------------------
def with_read_errors(monkeypatch, count):
    real_plan = sb.plan

    def plan_with_errors(args):
        planned = real_plan(args)
        planned.errors.extend(f"ERROR scan C:\\nowhere\\unreadable {number} (test)"
                              for number in range(count))
        return planned
    monkeypatch.setattr(sb, "plan", plan_with_errors)
    return real_plan


def test_m1_a_resume_says_the_plans_read_errors_and_exits_1(env, monkeypatch, capsys,
                                                            photobackup_log_dir):
    real = with_read_errors(monkeypatch, 2)
    stopped(env, monkeypatch)
    monkeypatch.setattr(sb, "plan", real)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--resume") == 1        # as the full run
    out = capsys.readouterr().out
    assert "Errors: 2 folders/files could not be read (NOT backed up)" in out
    assert row_count(out, "read errors when it was planned (NOT backed up)") == "2"
    text = latest_log(photobackup_log_dir)
    assert "unreadable 0 (test)" in text and "unreadable 1 (test)" in text
    assert backed(env.dest) == OTHERS | JXL                     # the work itself is done
    recorded = meta(env.dest)
    assert recorded["resume_done"] == recorded["resume_plan"]


def test_m1_more_read_errors_than_the_plan_keeps_are_all_counted(env, monkeypatch, capsys,
                                                                 photobackup_log_dir):
    kept = getattr(sb, "_RESUME_ERROR_LINES", 100)
    real = with_read_errors(monkeypatch, kept + 5)
    stopped(env, monkeypatch)
    monkeypatch.setattr(sb, "plan", real)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--resume") == 1
    out = capsys.readouterr().out
    assert f"Errors: {kept + 5} folders/files could not be read (NOT backed up)" in out
    text = latest_log(photobackup_log_dir)
    assert f"unreadable {kept - 1} (test)" in text and f"unreadable {kept} (test)" not in text
    assert "and 5 more" in text


# ---- S1: a run marks done only the plan it saved or resumed -------------------------------------
def test_s1_a_run_that_saved_no_plan_leaves_another_runs_plan_pending(env, monkeypatch, capsys):
    other = env.src / "2024" / "misc"
    other.mkdir(parents=True)
    (other / "a.txt").write_bytes(b"x" * 10)
    argv_2024 = argv_for(env.src / "2024", env.dest, env.src, "--yes", "--unexported", "copy",
                         "--export-formats", "all", "--tiff", "jxl")
    assert sb.main(argv_2024) == 0                  # 2024 backed up in full
    stopped(env, monkeypatch)                       # 2025: 1 of 3 TIFF, its plan pending
    pending = meta(env.dest)["resume_plan"]
    assert len(groups(env.dest)) == 1
    assert sb.main(argv_2024) == 0                  # 2024 again: nothing to do, no plan saved
    assert meta(env.dest).get("resume_done") != pending
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--resume") == 0
    assert len(groups(env.dest)) == 3
    assert backed(env.dest) >= OTHERS | JXL
    assert meta(env.dest)["resume_done"] == pending


def test_s1_a_run_whose_plan_was_not_saved_does_not_finish_the_older_one(env, monkeypatch):
    stopped(env, monkeypatch)
    pending = meta(env.dest)["resume_plan"]
    monkeypatch.setattr(sb, "_save_plan", lambda *args, **kwargs: None)    # a WARNING
    assert backup(env, "--tiff", "jxl") == 0
    assert meta(env.dest).get("resume_done") != pending
    assert meta(env.dest)["resume_plan"] == pending


# ---- S2: any malformed plan is exit 2 with a message ---------------------------------------------
def _first_open(lines):
    return next(index for index, line in enumerate(lines) if isinstance(line, list)
                and line[0] == "o")


def _entry(field, value):
    def change(lines):
        lines[_first_open(lines)][field] = value(lines[_first_open(lines)][field])
        return lines
    return change


def _header(**values):
    def change(lines):
        lines[0].update(values)
        return lines
    return change


def _nested(lines):
    lines[_first_open(lines)] = "[" * 100000 + "]" * 100000
    return lines


def _nul(text):
    return text[:2] + "\x00" + text[2:]


S2_CASES = {
    "category list": _entry(5, lambda old: ["x"]),
    "category dict": _entry(5, lambda old: {"a": 1}),
    "convert list": _entry(6, lambda old: ["jxl"]),
    "rel with NUL": _entry(2, _nul),
    "src with NUL": _entry(1, _nul),
    "nested": _nested,
    "started with a zone": _header(started="2026-11-27T10:00:00+05:00"),
    "plan_errors negative": _header(plan_errors=-1),
    "errors not a list": _header(plan_errors=1, errors="x"),
}


@pytest.mark.parametrize("case", list(S2_CASES), ids=list(S2_CASES))
def test_s2_a_malformed_plan_is_refused_never_a_traceback(env, monkeypatch, capsys, case):
    stopped(env, monkeypatch)
    rewrite(env, S2_CASES[case](plan_lines(env.dest)))
    before = snapshot(env.dest)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--resume") == 2
    err = capsys.readouterr().err
    assert "Nothing was changed" in err
    assert snapshot(env.dest) == before


# ---- S3: with plan saving on, Ctrl+C's last line is still "the disk can be removed" -------------
def test_s3_ctrl_c_still_ends_with_the_disk_can_be_removed(env, monkeypatch, capsys):
    hint = sb._resume_hint("selective_backup")
    capsys.readouterr()
    stopped(env, monkeypatch)
    out = capsys.readouterr().out
    assert out.rstrip().endswith(sb.STOPPED_SAYING)
    assert hint in out and out.index(hint) < out.index(sb.STOPPED_SAYING)
    put_back = stop_after_first_group(monkeypatch)
    assert backup(env, "--tiff", "jxl", "--resume") == 130      # a resume stopped again
    put_back()
    out = capsys.readouterr().out
    assert out.rstrip().endswith(sb.STOPPED_SAYING)
    assert hint in out and out.index(hint) < out.index(sb.STOPPED_SAYING)


# ---- N1: a link at the plan's temp name is never deleted ----------------------------------------
def test_n1_a_link_at_the_plans_temp_name_is_left_as_it_is(env, monkeypatch, capsys):
    assert backup(env, "--tiff", "jxl") == 0
    recorded = meta(env.dest)
    data = plan_file(env.dest).read_bytes()
    victim = env.root / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_bytes(b"not the backup's")
    temp = Path(str(plan_file(env.dest)) + sb.TEMP_SUFFIX)
    junction(temp, victim)
    (env.src / "2025" / "scans" / "new.txt").write_bytes(b"new")     # work for the next run
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl") == 0
    out = capsys.readouterr().out
    assert os.path.lexists(temp) and (temp / "keep.txt").read_bytes() == b"not the backup's"
    assert "WARNING" in out and "not saved" in out
    assert plan_file(env.dest).read_bytes() == data
    assert meta(env.dest)["resume_plan"] == recorded["resume_plan"]
    assert "2025/scans/new.txt" in backed(env.dest)
    os.rmdir(temp)


# ---- N2: a link at the plan file: the message says "file" ---------------------------------------
def test_n2_a_link_at_the_plan_file_is_named_a_file(env, capsys):
    assert backup(env, "--tiff", "jxl") == 0
    path = plan_file(env.dest)
    path.unlink()
    target = env.root / "elsewhere"
    target.mkdir()
    junction(path, target)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl") == 2
    err = capsys.readouterr().err
    assert "plain file" in err and "plain folder" not in err
    os.rmdir(path)


# ---- N3: refused after the catalog was opened to write: "nothing was copied" ---------------------
def test_n3_a_catalog_changed_after_the_check_copies_nothing(env, monkeypatch, capsys):
    stopped(env, monkeypatch)
    real_load = sb._resume_load

    def load_then_another_run(*args, **kwargs):
        resume = real_load(*args, **kwargs)
        connection = sqlite3.connect(str(Path(env.dest, sb.CATALOG_FOLDER, "catalog.sqlite")))
        try:
            connection.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('resume_done', ?)",
                               (resume.header["plan_id"],))
            connection.commit()
        finally:
            connection.close()
        return resume
    monkeypatch.setattr(sb, "_resume_load", load_then_another_run)
    have = backed(env.dest)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--resume") == 2
    err = capsys.readouterr().err
    assert "Nothing was copied" in err and "Nothing was changed" not in err
    assert backed(env.dest) == have


# ---- N4: the age of a plan is counted in UTC (a clock put back for the winter) -------------------
def test_n4_the_age_of_a_plan_is_counted_in_utc(env, monkeypatch, capsys):
    stopped(env, monkeypatch)
    lines = plan_lines(env.dest)
    utc_now = datetime.now(timezone.utc)
    lines[0]["started_utc"] = (utc_now + timedelta(hours=1)).isoformat(timespec="seconds")
    rewrite(env, lines)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--resume") == 2        # later than now: refused
    assert "later than now" in capsys.readouterr().err
    # the local clock went back an hour after the plan was saved: its local time
    # is "in the future", its UTC time is not
    lines[0]["started"] = (datetime.now() + timedelta(minutes=50)).isoformat(timespec="seconds")
    lines[0]["started_utc"] = (utc_now - timedelta(minutes=10)).isoformat(timespec="seconds")
    rewrite(env, lines)
    assert backup(env, "--tiff", "jxl", "--resume") == 0
    assert backed(env.dest) == OTHERS | JXL


# ---- N5: a source folder that became a junction after the plan is not read through it ------------
def test_n5_a_source_folder_that_became_a_junction_is_left(env, monkeypatch, photobackup_log_dir):
    stopped(env, monkeypatch)
    done = groups(env.dest)
    scans = env.src / "2025" / "scans"
    moved = env.root / "elsewhere"
    os.rename(scans, moved)                 # the same files, sizes and dates
    junction(scans, moved)
    try:
        assert backup(env, "--tiff", "jxl", "--resume") == 0
        text = latest_log(photobackup_log_dir)
        left = [line for line in text.splitlines()
                if line.startswith("RESUME LEFT ") and "scans" in line]
        open_scans = {rel for rel in TIFFS if rel.startswith("2025/scans/") and rel not in done}
        assert open_scans and len(left) >= len(open_scans)
        assert all("link or junction" in line for line in left)
        assert not {rel + ".jxl" for rel in open_scans} & backed(env.dest)
    finally:
        os.rmdir(scans)                     # the junction only
        os.rename(moved, scans)
