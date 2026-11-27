"""v0.14.0-rc6: the fixes of the independent review before release
(AI_tools/261122_Fable_review_rc6.md). Each test failed on rc6 as the coder left
it, except where it says it guards a new rule. jxl-photo is played by
tests/fake_jxlphoto.py."""
import csv
import io
import json
import os
from pathlib import Path

import pytest

import mirror_backup as mir
import photo_backup as menu
import selective_backup as sb

from conftest import latest_csv, latest_log, row_count
from test_jpeg_jxl import add_jpegs, calls
from test_menu import menu_config, script  # noqa: F401
from test_tiff_jxl import (backed, backup, closed_console, db, env, restore,  # noqa: F401
                           restore_statuses, under, write_manifest)


def not_converted(env):
    return db(env.dest, "SELECT rel, failures FROM not_converted")


def deep_folder(base, length):
    """A folder under `base` whose path is `length` characters long."""
    path = str(base)
    while len(path) < length:
        part = "d" * min(100, length - len(path) - 1)
        path = os.path.join(path, part)
    return Path(path)


# ---- M1 a deep target or work folder: never a temp folder Windows hangs on ----------------------
def test_m1_a_deep_restore_target_gets_a_short_temp_folder(env, monkeypatch, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    envs = env.root.parent / "envs"
    monkeypatch.setenv("FAKE_JXLPHOTO_ENVS", str(envs))
    target = deep_folder(env.root.parent / "restored", 200)       # no '%' in it
    assert "%" not in str(target)
    assert restore(env, "--to", target) == 0
    statuses = restore_statuses(photobackup_log_dir)
    assert {statuses[rel] for rel in ("2025/shoot/DSC_0001.tif", "2025/scans/000037.tiff",
                                      "2025/scans/000038.tif")} == {"RESTORED_FROM_JXL"}
    decodes = [json.loads(path.read_text(encoding="utf-8")) for path in envs.glob("*.json")]
    decodes = [item for item in decodes if item["argv"][0] == "decode"]
    assert decodes
    limit = getattr(sb, "_JXL_TEMP_MAX_CHARS", 240)               # 240 before the fix too
    for item in decodes:
        assert len(item["TEMP"]) < limit, item["TEMP"]
        assert not os.path.exists(item["TEMP"])                   # removed after


def test_m1_a_work_folder_too_deep_is_refused(tmp_path):
    deep = deep_folder(tmp_path, 225)
    deep.mkdir(parents=True)
    errors = sb._work_dir_errors([str(tmp_path / "src")], str(tmp_path / "dest"), str(deep))
    assert len(errors) == 1 and "too deep" in errors[0]
    with pytest.raises(OSError):
        sb._work_root(str(deep))


# ---- M2 a failure counts only in a run that proved the tools work ------------------------------
def test_m2_two_runs_where_nothing_converts_count_nothing(env, monkeypatch, photobackup_log_dir):
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", "exit")              # every conversion fails
    for _ in range(2):
        assert backup(env, "--tiff", "jxl") == 1
        assert not_converted(env) == []
        assert "NOT COUNTED: 3 TIFF conversions failed" in latest_log(photobackup_log_dir)
    monkeypatch.delenv("FAKE_JXLPHOTO_FAIL")                      # the tools work again
    assert backup(env, "--tiff", "jxl") == 0
    assert {"2025/scans/000037.tiff.jxl", "2025/shoot/DSC_0001.tif.jxl"} <= backed(env.dest)


def test_m2_a_failure_that_says_nothing_of_the_file_never_counts(env, monkeypatch):
    monkeypatch.setattr(sb, "JXLPHOTO_SERVE", False)    # a single call's own shape (rc7: serve has its own tests)
    real = sb._run_jxlphoto

    def cannot_start_for_one(path, arguments, *rest, **kwargs):
        if arguments[0] == "encode" and "000037" in arguments[1]:
            raise OSError("the program could not be started")
        return real(path, arguments, *rest, **kwargs)
    monkeypatch.setattr(sb, "_run_jxlphoto", cannot_start_for_one)
    for _ in range(3):                                          # the others convert: proof
        assert backup(env, "--tiff", "jxl") == 1
        assert not_converted(env) == []
    assert "2025/scans/000037.tiff" in backed(env.dest)           # as it is, tried every run


# ---- M3 the old copy keeps its name until the new one is on the disk ---------------------------
def test_m3_the_old_copy_stays_when_the_new_copy_as_it_is_fails(env, monkeypatch):
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", "exit")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "000037")
    assert backup(env, "--tiff", "jxl") == 1                     # 000037.tiff kept as it is
    copy = env.dest / "2025/scans/000037.tiff"
    old = copy.read_bytes()
    source = env.src / "2025/scans/000037.tiff"
    source.write_bytes(source.read_bytes() + b" edited")
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 60_000_000_000))
    real = sb._copy_file

    def disk_gone(planned, dst, *rest, **kwargs):
        if str(dst).endswith("000037.tiff"):
            raise OSError("the destination stopped taking writes")
        return real(planned, dst, *rest, **kwargs)
    monkeypatch.setattr(sb, "_copy_file", disk_gone)
    assert backup(env, "--tiff", "jxl", "--update-changed") == 1
    assert copy.read_bytes() == old                              # still under its name
    assert not [name for name in under(env.dest, "versions") if "000037" in name]
    assert db(env.dest, "SELECT count(*) FROM files WHERE key LIKE '%000037.tiff'") == [(1,)]


# ---- S1 a page never takes another file's name --------------------------------------------------
def test_s1_a_page_never_takes_another_tiffs_name(env, photobackup_log_dir):
    other = env.src / "2025/scans/000038_page1.tif"
    other.write_bytes(b"SCAN 000038_page1 " * 30)
    for _ in range(2):
        assert backup(env, "--tiff", "jxl") == 1
        have = backed(env.dest)
        assert "2025/scans/000038.tif" in have                    # as it is: both have a copy
        assert "2025/scans/000038_page1.tif.jxl" in have
        rows = dict(db(env.dest, "SELECT key, source_rel FROM converted"))
        page = [source for key, source in rows.items() if key.endswith("000038_page1.tif.jxl")]
        assert [source.replace("\\", "/") for source in page] == ["2025/scans/000038_page1.tif"]
        assert not under(env.dest, "versions")                    # nobody displaced
        assert "would take the name of another file" in latest_log(photobackup_log_dir)


# ---- S3 a changed source whose copy is as it is waits for --update-changed ---------------------
def changed_copy_as_is(env, monkeypatch):
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", "exit")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "000037")
    assert backup(env, "--tiff", "jxl") == 1
    monkeypatch.delenv("FAKE_JXLPHOTO_FAIL")
    monkeypatch.delenv("FAKE_JXLPHOTO_ONLY")
    source = env.src / "2025/scans/000037.tiff"
    source.write_bytes(source.read_bytes() + b" edited")
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 60_000_000_000))


def test_s3_a_changed_source_with_a_copy_as_is_waits_for_update_changed(env, monkeypatch, capsys):
    changed_copy_as_is(env, monkeypatch)
    copy = env.dest / "2025/scans/000037.tiff"
    old = copy.read_bytes()
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl") == 0
    out = capsys.readouterr().out
    assert copy.read_bytes() == old                              # reported, not replaced
    assert not under(env.dest, "versions")
    assert row_count(out, "replace (changed; the old copy to versions\\)") == "1"
    assert row_count(out, "copies their JXL replaces (to versions\\)") is None
    assert backup(env, "--tiff", "jxl", "--update-changed") == 0
    assert "2025/scans/000037.tiff.jxl" in backed(env.dest)
    assert "2025/scans/000037.tiff" in under(env.dest, sb.ORIGINALS_FOLDER)    # 261126


def test_s3_the_mirror_shows_the_old_copy_as_retired_not_as_a_stranger(env, monkeypatch,
                                                                         photobackup_log_dir):
    """Guards the new status: a CHANGED file kept as JXL with no JXL yet has no
    .jxl in the backup; the mirror lists its copy as it is to retire, never a
    'versions' item for a .jxl that is not there."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    manifest = write_manifest(env.root / "jxl.csv", (env.src / "2025", "6", "", "", "jxl"))
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", "exit")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "000037")
    mir.main(["--manifest", str(manifest), "--dest", str(env.dest), "--yes"])
    monkeypatch.delenv("FAKE_JXLPHOTO_FAIL")
    monkeypatch.delenv("FAKE_JXLPHOTO_ONLY")
    source = env.src / "2025/scans/000037.tiff"
    source.write_bytes(source.read_bytes() + b" edited")
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 60_000_000_000))
    mir.main(["--manifest", str(manifest), "--dest", str(env.dest), "--dry-run"])
    rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_actions.csv"))))
    mine = [row for row in rows if "000037" in row["rel"]]
    assert [row["action"] for row in mine] == ["retire"], mine
    assert mine[0]["rel"].endswith("000037.tiff")


# ---- S5 the menu can ask the next backup to try the failed conversions again -------------------
def test_s5_the_menu_asks_the_next_backup_to_try_again(monkeypatch, menu_config):
    # answers: jxl-photo? no; the work folder? no; the conversion off? no; try the failed
    # conversions again? yes; forget the choices? no
    script(monkeypatch, ["n", "n", "n", "y", "n", "n", "n"])
    menu.settings_menu(menu.load_config())
    assert os.path.isfile(menu._retry_marker())
    commands = []

    class Done:
        returncode = 0
    monkeypatch.setattr(menu.subprocess, "run", lambda command, **kwargs: commands.append(
        command) or Done())
    monkeypatch.setattr(menu, "_menu_title", lambda: None)
    menu.run_backend(["--source", "S", "--dry-run"])               # the dry-run sees it too
    menu.run_restore(["--backup", "B"])                            # never the restore
    assert os.path.isfile(menu._retry_marker())
    menu.run_manifest(["--manifest", "m.csv", "--yes"])            # the run: then done
    menu.run_backend(["--source", "S", "--yes"])
    tails = [command[2:] for command in commands]
    assert ["--retry-conversions" in tail for tail in tails] == [True, False, True, False]
    assert not os.path.exists(menu._retry_marker())


# ---- N5 and P0 ----------------------------------------------------------------------------------
def test_n5_the_plan_names_a_file_not_converted_again_as_it_is(env, monkeypatch,
                                                               photobackup_log_dir):
    add_jpegs(env)
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", "exit")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "cmyk.jpg")
    assert backup(env, "--jpeg", "jxl") == 1
    (env.src / "2025/phone/new.jpg").write_bytes(b"\xff\xd8a new photo " * 30)
    assert backup(env, "--jpeg", "jxl") == 1                     # counted: new.jpg converted
    assert backup(env, "--jpeg", "jxl", "--dry-run") == 0
    rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_plan.csv"))))
    cmyk = [row["rel"].replace("\\", "/") for row in rows if "cmyk" in row["rel"]]
    assert cmyk == ["2025/phone/cmyk.jpg"]


def test_p0_the_log_says_where_the_conversion_time_went(env, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    # rc10 added a "TIMING before the copy:" line: only the conversions' one here
    line = [line for line in latest_log(photobackup_log_dir).splitlines()
            if "TIMING conversions" in line]
    assert len(line) == 1
    for part in ("TIMING conversions: 3 files", "the writer: waiting for a conversion",
                 "putting the JXL in place", "the workers (sum): reading the sources",
                 "jxl-photo", "proof"):
        assert part in line[0], (part, line[0])
