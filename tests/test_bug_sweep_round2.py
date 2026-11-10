"""Round 2 of the 261110 bug sweep (AI_tools/261110_Fable_review_bug-sweep.md and
AI_tools/261111_Fable_review_bug-sweep-fixes.md).

Each test here failed against the round-1 code (v0.12.1 without these fixes;
the report has the run). The S9 tests (real read failures) failed against
v0.12.0 instead: their bugs were fixed in round 1."""
import csv
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import captureone_backup as c1
import manifest_backup as mb
import mobile_backup as mob
import photo_backup as menu
import restore_backup as rb
import selective_backup as sb

from conftest import argv_for, latest_csv, latest_log, make_tree
from test_bug_sweep import (COUNTERS, SHIPPED_MODES, export_in_added_tree, listed, mirror,
                            mobile_tree, no_dates, plan_rows, planned_set, sweep_tree)
from test_captureone import STYLE, backup, force, machine, revert  # noqa: F401 (fixture)


def catalog_rows(dest):
    catalog = sb._Catalog(dest, create=False, readonly=True)
    try:
        return catalog.all_rows()
    finally:
        catalog.close()


def corrupt_keeping_size_and_date(path):
    stat = path.stat()
    data = bytearray(path.read_bytes())
    data[0] ^= 0xFF
    os.chmod(path, 0o666)
    path.write_bytes(bytes(data))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def interrupted(call):
    """The exit code of `call`, or 'raised' when a KeyboardInterrupt escaped
    (it must not reach pytest, which would stop the whole run)."""
    try:
        return call()
    except KeyboardInterrupt:
        return "raised"


# ---- B23: only --verify full records verified_at -------------------------------------------
def twelve_files(tmp_path):
    make_tree(tmp_path, {f"G/2024/trip/F{index:02d}.NEF": 1000 for index in range(12)})
    return tmp_path / "G" / "2024", tmp_path / "D"


def test_b23_quick_verify_records_nothing(tmp_path):
    source, dest = twelve_files(tmp_path)
    assert sb.main(argv_for(source, dest, tmp_path, "--unexported", "copy", "--yes")) == 0
    assert sb.main(argv_for(source, dest, tmp_path, "--unexported", "copy",
                            "--verify", "quick")) == 0
    assert not any(row.verified_at for row in catalog_rows(dest))


def test_b23_a_quick_verify_does_not_reset_the_rolling_queue(tmp_path, photobackup_log_dir):
    source, dest = twelve_files(tmp_path)
    common = ("--unexported", "copy")
    assert sb.main(argv_for(source, dest, tmp_path, *common, "--yes")) == 0
    checked = set()
    for _night in range(4):
        assert sb.main(argv_for(source, dest, tmp_path, *common, "--verify", "full",
                                "--max-gb", str(3000 / 1e9))) == 0
        rows = csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_verify.csv")))
        checked |= {Path(row["rel"]).name for row in rows}
        assert sb.main(argv_for(source, dest, tmp_path, *common, "--verify", "quick")) == 0
    assert checked == {f"F{index:02d}.NEF" for index in range(12)}


def test_b23_manifest_quick_verify_records_nothing(tmp_path):
    source, dest = twelve_files(tmp_path)
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{source},3+,raw\n", encoding="utf-8")
    common = ["--manifest", str(manifest), "--modes", str(SHIPPED_MODES), "--dest", str(dest)]
    assert mb.main(common + ["--yes"]) == 0
    assert mb.main(common + ["--verify", "quick"]) == 0
    assert not any(row.verified_at for row in catalog_rows(dest))
    assert mb.main(common + ["--verify", "full"]) == 0
    assert all(row.verified_at for row in catalog_rows(dest))


# ---- B11: captureone_backup.py --verify and the rows without a hash --------------------------
def hashless_backup(machine):
    """A backup whose catalog rows have no sha256 (the catalog was made again)."""
    assert backup(machine) == 0
    shutil.rmtree(machine["backup"] / sb.CATALOG_FOLDER)
    assert backup(machine) == 0
    rows = catalog_rows(machine["backup"])
    assert rows and not any(row.sha256 for row in rows)
    return rows


def verify_c1(machine):
    return c1.main(["--verify", str(machine["dest"])])


def test_b11_verify_fills_the_hash_when_the_source_still_matches(machine, capsys):
    rows = hashless_backup(machine)
    capsys.readouterr()
    assert verify_c1(machine) == 0
    out = capsys.readouterr().out
    assert f"HASH_FILLED {len(rows)}" in out
    assert all(row.sha256 for row in catalog_rows(machine["backup"]))
    assert verify_c1(machine) == 0
    assert f"OK {len(rows)}" in capsys.readouterr().out


def test_b11_copy_differs_from_an_unchanged_source(machine, capsys):
    hashless_backup(machine)
    corrupt_keeping_size_and_date(machine["backup"] / "UserData" / STYLE)
    capsys.readouterr()
    assert verify_c1(machine) == 1
    out = capsys.readouterr().out
    assert "DEST_DIFFERS" in out and "D200_standard.costyle" in out


def test_b11_what_cannot_be_filled_is_counted(machine, capsys):
    hashless_backup(machine)
    style = machine["user"] / STYLE
    style.write_bytes(b"edited since")          # the source no longer proves the copy
    capsys.readouterr()
    assert verify_c1(machine) == 0              # the exit code of these rows does not change
    out = capsys.readouterr().out
    assert "OK_SIZE_ONLY 1" in out
    assert "1 files were not checked by hash" in out


def test_b11_restore_of_a_row_without_hash(machine, tmp_path):
    hashless_backup(machine)
    out = tmp_path / "restored"
    assert c1.main(["--restore-from", str(machine["dest"]), "--to", str(out), "--yes"]) == 0
    rows = list(csv.DictReader(io.StringIO(
        latest_csv(sb.LOG_DIR, "_restore.csv"))))
    assert rows and {row["status"] for row in rows} == {"RESTORED_UNVERIFIED"}


def test_b11_restore_refuses_a_row_without_hash_of_another_size(machine, tmp_path):
    hashless_backup(machine)
    copy = machine["backup"] / "UserData" / STYLE
    os.chmod(copy, 0o666)
    copy.write_bytes(copy.read_bytes() + b"x")
    out = tmp_path / "restored"
    assert c1.main(["--restore-from", str(machine["dest"]), "--to", str(out), "--yes"]) == 1
    assert not (out / "UserData" / STYLE).exists()


# ---- B15: the space message says how much may be moves --------------------------------------
def test_b15_not_enough_space_names_the_possible_moves(tmp_path, capsys):
    make_tree(tmp_path, {"G/2024/trip/DSC_0001.NEF": 5000,
                         "G/2024/trip/_EXPORT/DSC_0001_v1.jpg": 3000})
    source = tmp_path / "G" / "2024"
    dest = tmp_path / "D"
    assert sb.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    os.rename(source / "trip", source / "trip_renamed")
    margin = str(shutil.disk_usage(tmp_path).free / 1e9 + 1)
    capsys.readouterr()
    assert sb.main(argv_for(source, dest, tmp_path, "--yes", "--space-margin-gb", margin)) == 2
    captured = capsys.readouterr()
    assert "possible moves" in captured.err
    assert "0.0 GB of it may be files moved inside the backup" in captured.err
    assert (dest / "G" / "2024" / "trip" / "DSC_0001.NEF").exists()


# ---- B12 and B22: captureone_backup.py ----------------------------------------------------
def test_b12_the_old_copy_keeps_its_name_until_the_new_one_is_safe(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    copy = machine["backup"] / "UserData" / STYLE
    old = copy.read_bytes()
    style.write_bytes(b"edited style")
    os.utime(style, (2_000_000_000, 2_000_000_000))
    (copy.parent / (copy.name + sb.TEMP_SUFFIX)).mkdir()     # the new copy cannot be written
    assert backup(machine) == 1
    assert copy.read_bytes() == old
    versions = machine["backup"] / sb.CATALOG_FOLDER / "versions"
    assert not (versions.exists() and list(versions.rglob("*.costyle")))
    assert verify_c1(machine) == 0


def test_b22_a_target_that_appears_during_the_force_is_not_taken_out(machine, monkeypatch):
    assert backup(machine) == 0
    added = machine["user"] / "Recipes165" / "sRGB.xml"
    added.unlink()
    real = sb._copy_file

    def copy_file(planned, dst, verify, expected_sha256=None, no_clobber=False, **kwargs):
        if no_clobber and dst.endswith("sRGB.xml"):
            Path(dst).write_bytes(b"another program wrote this")
        return real(planned, dst, verify, expected_sha256, no_clobber, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sb, "_copy_file", copy_file)
        assert force(machine) == 1                      # CONFLICT
    assert added.read_bytes() == b"another program wrote this"
    revert()
    assert added.exists() and added.read_bytes() == b"another program wrote this"


# ---- B13 and S2: mobile_backup.py ----------------------------------------------------------
@pytest.fixture
def mlogs(tmp_path, monkeypatch):
    monkeypatch.setattr(mob, "LOG_DIR", str(tmp_path / "mlogs"))


def killed_run(monkeypatch, argv, copies_before_kill):
    """A run killed (no `finally`) at copy number copies_before_kill + 1: what
    is on the disk then is what a real kill leaves, state.json included."""
    state = {"killed": False, "copies": 0}
    real_save = mob.save_state
    real_copy = mob._copy

    def save_state(dest, files):
        if not state["killed"]:
            real_save(dest, files)

    def copy(source, target):
        if state["copies"] >= copies_before_kill:
            state["killed"] = True
            raise RuntimeError("killed")
        state["copies"] += 1
        real_copy(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(mob, "save_state", save_state)
        patch.setattr(mob, "_copy", copy)
        with pytest.raises(RuntimeError):
            mob.main(argv)


def test_b13_a_kill_after_the_renames_does_not_block_the_album(tmp_path, monkeypatch, mlogs):
    monkeypatch.setattr(mob, "read_shot_times", no_dates)
    album = tmp_path / "G" / "2025" / "250101_A" / "_EXPORT" / "JXL_mobile"
    make_tree(album, {"b.jxl": 10, "c.jxl": 11})
    argv = ["--source", str(tmp_path / "G" / "2025"), "--dest", str(tmp_path / "H")]
    assert mob.main(argv) == 0
    make_tree(album, {"a.jxl": 12})                 # renumbers b and c
    killed_run(monkeypatch, argv, 0)                # after the renames, before the copy
    assert mob.main(argv) == 0
    assert mob.main(argv) == 0
    assert sorted(Path(p).name for p in mirror(tmp_path / "H")) == [
        "0000_a.jxl", "0001_b.jxl", "0002_c.jxl"]


def test_b13_the_state_is_saved_every_few_copies(tmp_path, monkeypatch, mlogs):
    monkeypatch.setattr(mob, "read_shot_times", no_dates)
    monkeypatch.setattr(mob, "STATE_SAVE_EVERY", 2, raising=False)
    album = tmp_path / "G" / "2025" / "250101_A" / "_EXPORT" / "JXL_mobile"
    make_tree(album, {f"{name}.jxl": 10 + index for index, name in enumerate("abcde")})
    argv = ["--source", str(tmp_path / "G" / "2025"), "--dest", str(tmp_path / "H")]
    killed_run(monkeypatch, argv, 4)                # 4 copies done (two saves), killed at the 5th
    assert mob.main(argv) == 0
    assert len(mirror(tmp_path / "H")) == 5
    data = json.loads((tmp_path / "H" / mob.STATE_FOLDER / mob.STATE_FILE).read_text("utf-8"))
    assert all(rec.get("root") for rec in data["files"].values())


def test_b13_a_save_keeps_the_unread_photos_without_a_shot(tmp_path, monkeypatch, mlogs):
    album = tmp_path / "G" / "2025" / "250101_A" / "_EXPORT" / "JXL_mobile"
    make_tree(album, {"a.jxl": 10, "b.jxl": 11, "c.jxl": 12})
    monkeypatch.setattr(mob, "read_shot_times", lambda paths, log=None: {})
    monkeypatch.setattr(mob, "STATE_SAVE_EVERY", 1, raising=False)
    argv = ["--source", str(tmp_path / "G" / "2025"), "--dest", str(tmp_path / "H")]
    killed_run(monkeypatch, argv, 2)
    data = json.loads((tmp_path / "H" / mob.STATE_FOLDER / mob.STATE_FILE).read_text("utf-8"))
    assert data["files"] and not any("shot" in rec for rec in data["files"].values())


def old_state(dest, tool):
    path = dest / mob.STATE_FOLDER / mob.STATE_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    if tool is None:
        data.pop("tool", None)
    else:
        data["tool"] = tool
    for rec in data["files"].values():
        rec["shot"] = None                          # what a 0.1.1 exiftool failure wrote
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("tool", ["mobile_backup 0.1.1", None])
def test_s2_a_null_shot_of_an_older_state_is_read_once(tmp_path, monkeypatch, mlogs, tool):
    album = tmp_path / "G" / "2025" / "250101_A" / "_EXPORT" / "JXL_mobile"
    make_tree(album, {"A.jxl": 10, "B.jxl": 11, "C.jxl": 12, "D.jxl": 13})
    argv = ["--source", str(tmp_path / "G" / "2025"), "--dest", str(tmp_path / "H")]
    monkeypatch.setattr(mob, "read_shot_times", no_dates)
    assert mob.main(argv) == 0
    old_state(tmp_path / "H", tool)
    calls = []

    def dates(paths, log=None):
        calls.append(sorted(os.path.basename(p) for p in paths))
        table = {"A.jxl": "2025:01:01 12:00:03.000000", "B.jxl": "2025:01:01 12:00:02.000000",
                 "C.jxl": "2025:01:01 12:00:01.000000", "D.jxl": None}
        return {p: table[os.path.basename(p)] for p in paths}

    monkeypatch.setattr(mob, "read_shot_times", dates)
    assert mob.main(argv) == 0
    assert calls == [["A.jxl", "B.jxl", "C.jxl", "D.jxl"]]
    assert sorted(Path(p).name for p in mirror(tmp_path / "H")) == [
        "0000_C.jxl", "0001_B.jxl", "0002_A.jxl", "0003_D.jxl"]
    assert mob.main(argv) == 0
    assert len(calls) == 1                          # D read and without a date: not read again


def test_s2_exiftool_not_answering_stays_unread(tmp_path, monkeypatch, mlogs):
    album = tmp_path / "G" / "2025" / "250101_A" / "_EXPORT" / "JXL_mobile"
    make_tree(album, {"A.jxl": 10, "B.jxl": 11})
    argv = ["--source", str(tmp_path / "G" / "2025"), "--dest", str(tmp_path / "H")]
    monkeypatch.setattr(mob, "read_shot_times", no_dates)
    assert mob.main(argv) == 0
    old_state(tmp_path / "H", "mobile_backup 0.1.1")
    monkeypatch.setattr(mob, "read_shot_times", lambda paths, log=None: {})
    assert mob.main(argv) == 1
    data = json.loads((tmp_path / "H" / mob.STATE_FOLDER / mob.STATE_FILE).read_text("utf-8"))
    assert not any("shot" in rec for rec in data["files"].values())


# ---- B16: the backup stops when the destination no longer accepts writes ------------------
def eighty_files(tmp_path):
    make_tree(tmp_path, {f"G/2024/trip/F{index:03d}.NEF": 1000 for index in range(80)})
    return tmp_path / "G" / "2024", tmp_path / "D"


def failing_writes(patch, after):
    """Every copy after the first `after` fails as on a full or detached disk."""
    real = sb._write_temp
    count = {"n": 0}

    def write_temp(planned, tmp, source, flush):
        count["n"] += 1
        if count["n"] > after:
            for _chunk in source:           # the read happened; the write fails
                pass
            raise OSError(28, "No space left on device")
        return real(planned, tmp, source, flush)
    patch.setattr(sb, "_write_temp", write_temp)
    return count


def counting_reads(patch):
    opened = []
    real = sb._SourceRead.__iter__

    def iterate(self):
        opened.append(self.planned.src)
        return real(self)
    patch.setattr(sb._SourceRead, "__iter__", iterate)
    return opened


def test_b16_a_destination_that_stops_writing_stops_the_backup(tmp_path, monkeypatch, capsys):
    source, dest = eighty_files(tmp_path)
    real_open = open
    count = {"n": 0}

    def guarded_open(path, *args, **kwargs):
        text = os.fspath(path)
        if (count["n"] > 5 and sb.CATALOG_FOLDER in text
                and text.endswith(sb.TEMP_SUFFIX)):
            raise OSError(5, "Access is denied", text)      # the destination check
        return real_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sb, "READ_AHEAD_MB", 0.002)
        patch.setattr(sb, "DEST_CHECK_AFTER_ERRORS", 20, raising=False)
        count = failing_writes(patch, 5)
        opened = counting_reads(patch)
        patch.setattr(sb, "open", guarded_open, raising=False)
        assert sb.main(argv_for(source, dest, tmp_path, "--unexported", "copy", "--yes")) == 1
    out = capsys.readouterr().out
    assert len(opened) <= 5 + 20 + 4                # the read-ahead stopped too
    assert "stopped" in out.casefold() and "55 files" in out
    assert len(catalog_rows(dest)) == 5
    assert not list(dest.rglob("*" + sb.TEMP_SUFFIX))


def test_b16_the_check_passes_and_the_backup_goes_on(tmp_path, monkeypatch, capsys):
    source, dest = eighty_files(tmp_path)
    checks = []
    real_check = getattr(sb, "_dest_accepts_writes", None)

    with monkeypatch.context() as patch:
        patch.setattr(sb, "DEST_CHECK_AFTER_ERRORS", 20, raising=False)
        real = sb._write_temp
        count = {"n": 0}

        def write_temp(planned, tmp, source, flush):
            count["n"] += 1
            if 5 < count["n"] <= 50:            # 45 failures in a row, then it works again
                for _chunk in source:
                    pass
                raise OSError(28, "No space left on device")
            return real(planned, tmp, source, flush)
        patch.setattr(sb, "_write_temp", write_temp)
        if real_check is not None:
            patch.setattr(sb, "_dest_accepts_writes",
                          lambda d: checks.append(d) or real_check(d))
        assert sb.main(argv_for(source, dest, tmp_path, "--unexported", "copy", "--yes")) == 1
    assert len(checks) == 2                         # after 20 failures, then 20 more
    assert len(catalog_rows(dest)) == 35
    assert not list(dest.rglob("*" + sb.TEMP_SUFFIX))
    assert "stopped" not in capsys.readouterr().out.casefold()


def test_b16_zero_never_checks(tmp_path, monkeypatch):
    source, dest = eighty_files(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(sb, "DEST_CHECK_AFTER_ERRORS", 0, raising=False)
        failing_writes(patch, 5)
        patch.setattr(sb, "_dest_accepts_writes", lambda d: pytest.fail("checked"),
                      raising=False)
        assert sb.main(argv_for(source, dest, tmp_path, "--unexported", "copy", "--yes")) == 1
    assert len(catalog_rows(dest)) == 5


# ---- B20: Ctrl+C while planning ---------------------------------------------------------------
def ctrl_c(*_args, **_kwargs):
    raise KeyboardInterrupt


def small_manifest(tmp_path):
    make_tree(tmp_path, {"G/2025/shoot/_DSC1.NEF": 10, "G/2025/shoot/_EXPORT/_DSC1_v1.jxl": 20})
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{tmp_path / 'G' / '2025'},3+,raw\n",
                        encoding="utf-8")
    return ["--manifest", str(manifest), "--modes", str(SHIPPED_MODES)]


@pytest.mark.parametrize("extra", [["--dry-run"], ["--yes"], ["--verify", "quick"]])
def test_b20_manifest_planning(tmp_path, monkeypatch, capsys, extra):
    argv = small_manifest(tmp_path) + ["--dest", str(tmp_path / "out")]
    if extra[0] == "--verify":
        assert mb.main(argv + ["--yes"]) == 0
    monkeypatch.setattr(mb, "_plan_rows", ctrl_c)
    assert interrupted(lambda: mb.main(argv + extra)) == 130
    assert "Interrupted." in capsys.readouterr().err


def test_b20_manifest_analyze(tmp_path, monkeypatch, capsys):
    argv = small_manifest(tmp_path) + ["--analyze"]
    monkeypatch.setattr(mb, "_analysis_counts", ctrl_c)
    assert interrupted(lambda: mb.main(argv)) == 130
    assert "Interrupted." in capsys.readouterr().err


def test_b20_restore_planning(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path, {"G/2025/shoot/_DSC1.NEF": 10, "G/2025/shoot/_EXPORT/_DSC1_v1.jxl": 20})
    dest = tmp_path / "D"
    assert sb.main(argv_for(tmp_path / "G" / "2025", dest, tmp_path, "--yes")) == 0
    monkeypatch.setattr(rb, "_target_for", ctrl_c)
    assert interrupted(lambda: rb.main(["--backup", str(dest), "--to", str(tmp_path / "R"),
                                        "--dry-run"])) == 130
    assert "Interrupted." in capsys.readouterr().err


def test_b20_captureone_planning(machine, monkeypatch, capsys):
    monkeypatch.setattr(c1, "_build_plan", ctrl_c)
    assert interrupted(lambda: backup(machine)) == 130
    assert "Interrupted." in capsys.readouterr().err


# ---- B21: --only that matches nothing ------------------------------------------------------
def test_b21_an_only_that_matches_nothing_is_named(tmp_path, capsys, photobackup_log_dir):
    make_tree(tmp_path, {"G/2024/trip/DSC_0001.NEF": 100,
                         "G/2024/trip/_EXPORT/DSC_0001_v1.jpg": 50})
    dest = tmp_path / "D"
    assert sb.main(argv_for(tmp_path / "G" / "2024", dest, tmp_path, "--yes")) == 0
    target = tmp_path / "R"
    for extra in (["--dry-run"], ["--yes"]):
        capsys.readouterr()
        assert rb.main(["--backup", str(dest), "--to", str(target),
                        "--only", "G\\2024\\tripp", "--only", "G\\2024\\trip", *extra]) == 1
        out = capsys.readouterr().out
        assert "G\\2024\\tripp" in out and "matches nothing" in out
        assert "G\\2024\\tripp" in latest_log(photobackup_log_dir)
    assert (target / "G" / "2024" / "trip" / "DSC_0001.NEF").exists()


def test_b21_the_menu_does_not_say_done(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(menu, "CONFIG_PATH", str(tmp_path / "menu_config.json"))
    monkeypatch.setattr(menu, "list_drives", lambda: [])
    make_tree(tmp_path, {"G/2024/trip/DSC_0001.NEF": 100,
                         "G/2024/trip/_EXPORT/DSC_0001_v1.jpg": 50})
    dest = tmp_path / "D"
    assert sb.main(argv_for(tmp_path / "G" / "2024", dest, tmp_path, "--yes")) == 0
    monkeypatch.setattr(menu, "run_restore", lambda argv: rb.main(argv))
    answers = [str(dest), "G\\2024\\tripp", "2", str(tmp_path / "R"), "y"]
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    menu.restore_flow({"last": {}, "presets": {}})
    assert "Restore: done." not in capsys.readouterr().out


# ---- Originals=raw: a photo whose only original is a TIFF, PNG, HEIC or PSD -----------------
ORIGINALS_TREE = {
    "DSC_0001.NEF": 10,
    "_EXPORT/DSC_0001_v1.jxl": 11,
    "DSCN0101.TIF": 12,                 # a camera that wrote TIFF originals, no RAW
    "DSCN0101.xmp": 13,                 # its sidecar
    "PB010007.TIF": 14,
    "PB010007 (2).tif": 15,
    "_DSC0777_NXstudio.TIF": 16,        # a developed TIFF whose NEF is not here
    "capture.png": 17,
    "IMG_0001.HEIC": 18,
    "IMG_0002.HIF": 19,
    "collage.psd": 20,
    "DSC_4321.NEF": 21,
    "DSC_4321_NXstudio.TIF": 22,        # its RAW is here: stays out
    "P1010099.ORF": 23,
    "P1010099.tif": 24,                 # its RAW is here: stays out
    "X.NEF": 25,
    "X (2).tif": 26,                    # another file, not a variant of X
    "clip.mp4": 27,
    "_OUTTAKES_KEEP/DSC_0011.NEF": 30,
    "_OUTTAKES_KEEP/DSC_0011_pano.tif": 31,     # its RAW is here
    "_OUTTAKES_KEEP/IMG_0003.HEIC": 32,
}
ONLY_ORIGINAL = ("DSCN0101.TIF", "DSCN0101.xmp", "PB010007.TIF", "PB010007 (2).tif",
                 "_DSC0777_NXstudio.TIF", "capture.png", "IMG_0001.HEIC", "IMG_0002.HIF",
                 "collage.psd", "X (2).tif")
HAS_ITS_RAW = ("DSC_4321_NXstudio.TIF", "P1010099.tif", "_OUTTAKES_KEEP/DSC_0011_pano.tif")


def originals_tree(root):
    make_tree(root / "2025" / "251103_Show", ORIGINALS_TREE)
    return root / "2025"


@pytest.mark.parametrize("originals", ["raw", "raw+jpeg"])
@pytest.mark.parametrize("mode", ["2", "3", "4+", "5"])
def test_originals_raw_keeps_a_photo_whose_only_original_is_not_raw(tmp_path, mode, originals):
    folder = originals_tree(tmp_path / "G")
    found = planned_set(plan_rows([(folder, mode, originals)], tmp_path))
    show = folder / "251103_Show"
    for rel in ONLY_ORIGINAL + ("_OUTTAKES_KEEP/IMG_0003.HEIC",):
        if rel.startswith("_OUTTAKES_KEEP") and mode == "2":
            continue
        assert os.path.normcase(str(show / rel)) in found, rel
    for rel in HAS_ITS_RAW + ("clip.mp4",):
        assert os.path.normcase(str(show / rel)) not in found, rel


@pytest.mark.parametrize("unselected", ["raw_or_jpeg", "raw_jpeg"])
def test_unselected_option_keeps_them_too(tmp_path, unselected):
    folder = originals_tree(tmp_path / "G")
    args = sb._build_parser().parse_args(argv_for(folder, tmp_path / "out", tmp_path,
                                                  "--unselected", unselected))
    found = {os.path.normcase(item.src) for item in sb.plan(args).files}
    show = folder / "251103_Show"
    for rel in ONLY_ORIGINAL:
        assert os.path.normcase(str(show / rel)) in found, rel
    for rel in HAS_ITS_RAW + ("clip.mp4",):
        assert os.path.normcase(str(show / rel)) not in found, rel


def test_unselected_raw_is_unchanged(tmp_path):
    """guard: --unselected raw (RAW + their sidecars) keeps its meaning."""
    folder = originals_tree(tmp_path / "G")
    args = sb._build_parser().parse_args(argv_for(folder, tmp_path / "out", tmp_path,
                                                  "--unselected", "raw"))
    found = {os.path.normcase(item.src) for item in sb.plan(args).files}
    assert os.path.normcase(str(folder / "251103_Show" / "DSCN0101.TIF")) not in found


def full_tree(root):
    """The invariant tree: the round-1 tree, an export inside the added folders, the
    originals above, a $RECYCLE.BIN outside an added folder and a folder named in
    --exclude-folders (S4/S9), also inside an added folder (R2 of round 3)."""
    folder = sweep_tree(root)
    export_in_added_tree(root)
    originals_tree(root)
    make_tree(root / "2025", {
        "$RECYCLE.BIN/S-1-5/old.NEF": 7,
        f"{folder.name}/250101_Shoot/$RECYCLE.BIN/r2.NEF": 8,
        f"{folder.name}/250202_NotEdited/Skipme/x.NEF": 9,
        "Skipme/y.NEF": 9,
        # skipped by name inside an AddFolders folder and a session in it (R2 of round 3)
        f"{folder.name}/250101_Shoot/_OUTTAKES_KEEP/Skipme/k.NEF": 10,
        "shoot/_OUTTAKES_KEEP/sub/Skipme/k2.NEF": 11,
        "shoot/_OUTTAKES_KEEP/_EXPORT/Skipme/k3.jpg": 12,
        "shoot/CaptureOne/Skipme/k4.cos": 13,
    })
    return root / "2025"


MODES = list(mb.read_modes(str(SHIPPED_MODES)))


@pytest.mark.parametrize("mode", MODES)
def test_originals_raw_within_raw_jpeg_within_all(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(sb, "EXCLUDE_FOLDERS", "Skipme")
    folder = full_tree(tmp_path / "G")
    plans = [planned_set(plan_rows([(folder, mode, originals)], tmp_path))
             for originals in ("raw", "raw+jpeg", "all")]
    assert plans[0] <= plans[1] <= plans[2]


# ---- S9: every listed file planned or counted exactly once, file by file ----------------------
def octal_plan(monkeypatch, tmp_path, folder, rows_spec):
    """Plan with the size of file number k replaced by 8**k: the bytes of every
    counter then say which files it counted and how many times (one octal digit
    per file; the review's technique, section 3)."""
    paths = sorted(listed(folder))
    index = {path: number for number, path in enumerate(paths)}
    real = sb._Planner._scan_dir

    def scan(self, path):
        files, dirs, links = real(self, path)
        return ([(name, fpath, 8 ** index[os.path.normcase(fpath)], mtime_ns)
                 for name, fpath, _size, mtime_ns in files], dirs, links)

    with monkeypatch.context() as patch:
        patch.setattr(sb._Planner, "_scan_dir", scan)
        plan = plan_rows(rows_spec, tmp_path)
    return plan, paths


def digits(total, count):
    out = []
    for _ in range(count):
        total, digit = divmod(total, 8)
        out.append(digit)
    assert total == 0, "a file outside the listing was counted"
    return out


def check_once(plan, paths, unexported_copied):
    total = sum(item.size for item in plan.files)
    total += sum(getattr(plan.stats, name) for _, name in COUNTERS)
    for folder in plan.unexported_folders:
        if not getattr(folder, "copied", unexported_copied):
            total += folder.nbytes + folder.c1_nbytes + folder.nx_nbytes
    skipped = [os.path.normcase(path) for path in getattr(plan, "skipped_folders", [])]
    problems = []
    for path, digit in zip(paths, digits(total, len(paths))):
        expected = 0 if any(sb._is_under(path, folder) for folder in skipped) else 1
        if digit != expected:
            problems.append((digit, path))
    assert not problems, problems


@pytest.mark.parametrize("originals", ["raw", "raw+jpeg", "all"])
@pytest.mark.parametrize("mode", MODES)
def test_s9_every_listed_file_once_by_file(tmp_path, monkeypatch, mode, originals):
    monkeypatch.setattr(sb, "EXCLUDE_FOLDERS", "Skipme")
    folder = full_tree(tmp_path / "G")
    plan, paths = octal_plan(monkeypatch, tmp_path, folder, [(folder, mode, originals)])
    check_once(plan, paths, mb.read_modes(str(SHIPPED_MODES))[mode].unexported)


def test_s9_nested_lines_by_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "EXCLUDE_FOLDERS", "Skipme")
    folder = full_tree(tmp_path / "G")
    hostile = folder / sweep_tree(tmp_path / "G").name
    plan, paths = octal_plan(monkeypatch, tmp_path, folder, [
        (folder, "3+", "raw"),
        (hostile / "250202_NotEdited", "jpeg-only", ""),
        (hostile / "OLD_2019", "skip", ""),
        (hostile / "250101_Shoot" / "_OUTTAKES_KEEP", "4+", "raw"),
        (folder / "251103_Show", "5", "raw+jpeg")])
    check_once(plan, paths, True)


# ---- S4: folders skipped by name are named ---------------------------------------------------
def test_s4_folders_skipped_by_name_are_in_the_summary_and_the_log(tmp_path, capsys,
                                                                    photobackup_log_dir):
    make_tree(tmp_path, {"G/2025/shoot/_DSC1.NEF": 10,
                         "G/2025/shoot/_EXPORT/_DSC1_v1.jxl": 20,
                         "G/2025/$RECYCLE.BIN/S-1/x.NEF": 30,
                         "G/2025/shoot/Skipme/y.NEF": 40})
    source = tmp_path / "G" / "2025"
    assert sb.main(argv_for(source, tmp_path / "out", tmp_path, "--dry-run",
                            "--exclude-folders", "Skipme")) == 0
    out = capsys.readouterr().out
    assert "folders skipped by name (system / --exclude-folders): 2 -> " in out
    log = latest_log(photobackup_log_dir)
    assert f"SKIPPED FOLDER {source / '$RECYCLE.BIN'}" in log
    assert f"SKIPPED FOLDER {source / 'shoot' / 'Skipme'}" in log


def test_s4_once_per_planner_with_a_shared_cache(tmp_path):
    make_tree(tmp_path, {"G/2025/shoot/_DSC1.NEF": 10,
                         "G/2025/shoot/_EXPORT/_DSC1_v1.jxl": 20,
                         "G/2025/$RECYCLE.BIN/x.NEF": 30})
    args = sb._build_parser().parse_args(argv_for(tmp_path / "G" / "2025", tmp_path / "out",
                                                  tmp_path))
    cache = {}
    first = sb._Planner(args, list_cache=cache)
    first.build()
    second = sb._Planner(args, list_cache=cache)
    second.build()
    expected = [str(tmp_path / "G" / "2025" / "$RECYCLE.BIN")]
    assert getattr(first, "skipped_folders", None) == expected
    assert second.skipped_folders == expected


def test_s4_whole_mode_names_them_too(tmp_path, capsys):
    make_tree(tmp_path, {"G/2025/a.NEF": 10, "G/2025/$RECYCLE.BIN/x.NEF": 30})
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{tmp_path / 'G' / '2025'},6,raw\n",
                        encoding="utf-8")
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(tmp_path / "out"), "--dry-run"]) == 0
    assert "folders skipped by name (system / --exclude-folders): 1 -> " in capsys.readouterr().out


# ---- "folders without export" counts only those not copied ----------------------------------
@pytest.mark.parametrize("mode, line, copied", [("3+", "folders without export: 0 folders", "yes"),
                                                ("3", "folders without export: 2 folders", "no")])
def test_folders_without_export_counts_only_what_is_not_copied(tmp_path, capsys,
                                                                photobackup_log_dir,
                                                                mode, line, copied):
    make_tree(tmp_path, {"G/2025/shoot/_DSC1.NEF": 10,
                         "G/2025/shoot/_EXPORT/_DSC1_v1.jxl": 20,
                         "G/2025/250202_A/DSC_0100.NEF": 1000,
                         "G/2025/250303_B/DSC_0200.NEF": 2000})
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{tmp_path / 'G' / '2025'},{mode},raw\n",
                        encoding="utf-8")
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(tmp_path / "out"), "--dry-run"]) == 0
    assert line in capsys.readouterr().out
    rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_unexported.csv"))))
    assert len(rows) == 2
    assert {row.get("copied") for row in rows} == {copied}


# ---- S8: messages ---------------------------------------------------------------------------
@pytest.mark.parametrize("which", ["modes", "manifest"])
def test_s8_an_empty_header_says_there_is_an_extra_comma(tmp_path, capsys, which):
    folder = tmp_path / "G"
    folder.mkdir()
    modes = tmp_path / "modes.csv"
    text = SHIPPED_MODES.read_text(encoding="utf-8")
    if which == "modes":
        text = "\n".join(line + "," for line in text.splitlines()) + "\n"
    modes.write_text(text, encoding="utf-8")
    manifest = tmp_path / "manifest.csv"
    body = f"Folder,Mode,Originals\n{folder},3+,raw\n"
    if which == "manifest":
        body = "\n".join(line + "," for line in body.splitlines()) + "\n"
    manifest.write_text(body, encoding="utf-8")
    assert mb.main(["--manifest", str(manifest), "--modes", str(modes), "--analyze"]) == 2
    err = capsys.readouterr().err
    assert "empty column name" in err and "extra comma" in err


def test_s8_two_files_with_one_destination_name_both(tmp_path):
    args = sb._build_parser().parse_args(argv_for(tmp_path, tmp_path / "out", tmp_path))
    planner = sb._Planner(args)
    first = os.path.join("Q:\\", "2024", "trip", "a.NEF")
    second = os.path.join("R:\\", "2024", "trip", "a.NEF")
    planner._add(sb.PlannedFile(first, "2024\\trip\\a.NEF", 10, 0, sb.CAT_SOURCE))
    planner._add(sb.PlannedFile(second, "2024\\trip\\a.NEF", 10, 0, sb.CAT_SOURCE))
    assert len(planner.errors) == 1
    message = planner.errors[0]
    assert first in message and second in message
    assert f"only {first} has a copy" in message
    assert f"{second} is NOT backed up" in message


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path normalization")
def test_s8_a_folder_name_ending_in_a_dot(tmp_path, capsys, photobackup_log_dir):
    make_tree(tmp_path, {"G/2024/Trip/DSC_0001.NEF": 100,
                         "G/2024/Trip/_EXPORT/DSC_0001_v1.jpg": 50})
    dotted = "\\\\?\\" + str(tmp_path / "G" / "2024" / "Trip.")
    os.mkdir(dotted)
    with open(dotted + "\\other.NEF", "wb") as handle:
        handle.write(b"only in the dotted folder")
    source = tmp_path / "G" / "2024"
    try:
        assert sb.main(argv_for(source, tmp_path / "out", tmp_path, "--dry-run",
                                "--move-orphans")) == 1
        out = capsys.readouterr().out
        assert "Errors:" in out
        assert "errors in the plan of this run" in out
        log = latest_log(photobackup_log_dir)
        errors = [line for line in log.splitlines() if line.startswith("ERROR duplicate")]
        assert errors
        for line in errors:
            assert "Windows" in line
            assert f"{source / 'Trip.'} is NOT backed up" in line
            assert "DSC_0001" in line
    finally:
        shutil.rmtree(dotted, ignore_errors=True)


# ---- S6 and [7] (menu) ----------------------------------------------------------------------
@pytest.fixture
def menu_env(tmp_path, monkeypatch):
    monkeypatch.setattr(menu, "CONFIG_PATH", str(tmp_path / "menu_config.json"))
    monkeypatch.setattr(menu, "list_drives", lambda: [])
    calls = {"backend": [], "manifest": [], "captureone": []}
    codes = {"captureone": 0}
    monkeypatch.setattr(menu, "run_backend", lambda argv: calls["backend"].append(list(argv)) or 0)
    monkeypatch.setattr(menu, "run_manifest",
                        lambda argv: calls["manifest"].append(list(argv)) or 0)
    monkeypatch.setattr(menu, "run_captureone",
                        lambda argv: calls["captureone"].append(list(argv)) or codes["captureone"])
    return calls, codes


def answers(monkeypatch, values):
    values = list(values)

    def fake_input(prompt=""):
        if not values:
            raise EOFError
        return values.pop(0)
    monkeypatch.setattr("builtins.input", fake_input)


def test_s6_an_old_config_without_the_verify_key(monkeypatch):
    monkeypatch.setattr(menu, "COPY_CAPTUREONE_SETTINGS", True)
    assert menu.session_from({"captureone": False}).get("captureone_verify") is False
    assert menu.session_from({}).get("captureone_verify") is True
    assert menu.session_from({"captureone": True,
                              "captureone_verify": False})["captureone_verify"] is False


def test_s6_a_no_in_the_verify_keeps_the_backup_step(tmp_path, monkeypatch, menu_env):
    calls, _codes = menu_env
    source = tmp_path / "G"
    source.mkdir()
    dest = tmp_path / "out"
    (dest / c1.BACKUP_SUBFOLDER).mkdir(parents=True)
    session = menu.default_session()
    session.update(sources=[str(source)], dest=str(dest), captureone=True)
    Path(menu.CONFIG_PATH).write_text(json.dumps({"last": session, "presets": {}}),
                                      encoding="utf-8")
    #               menu src dest fmt sub unsel unexp c1cache deriv c1verify mode exit
    answers(monkeypatch, ["2", "", "", "", "", "", "", "", "", "n", "", "0"])
    assert menu.main([]) == 0
    assert calls["captureone"] == []
    saved = json.loads(Path(menu.CONFIG_PATH).read_text(encoding="utf-8"))["last"]
    assert saved["captureone"] is True and saved.get("captureone_verify") is False


def manifest_answers(manifest, dest, *rest):
    return ["7", str(manifest), str(dest), *rest, "0"]


def test_7_backup_runs_the_capture_one_step_and_update_changed(tmp_path, monkeypatch, menu_env):
    calls, _codes = menu_env
    manifest = tmp_path / "m.csv"
    dest = tmp_path / "out"
    #                                               choice c1   replace copy?
    answers(monkeypatch, manifest_answers(manifest, dest, "2", "", "y", "y"))
    assert menu.main([]) == 0
    dry, copy = calls["manifest"]
    assert "--dry-run" in dry and "--update-changed" not in dry
    assert "--yes" in copy and "--update-changed" in copy
    assert calls["captureone"] == [["--dest", str(dest), "--dry-run"],
                                   ["--dest", str(dest), "--yes"]]
    config = json.loads(Path(menu.CONFIG_PATH).read_text(encoding="utf-8"))
    assert config.get("last_manifest_captureone") is True
    assert config.get("last_manifest_update_changed") is True


def test_7_backup_without_capture_one_is_remembered(tmp_path, monkeypatch, menu_env):
    calls, _codes = menu_env
    manifest = tmp_path / "m.csv"
    dest = tmp_path / "out"
    answers(monkeypatch, manifest_answers(manifest, dest, "2", "n", "", "y"))
    assert menu.main([]) == 0
    assert calls["captureone"] == []
    assert "--update-changed" not in calls["manifest"][1]
    calls["manifest"].clear()
    answers(monkeypatch, manifest_answers(manifest, dest, "2", "", "", "y"))
    assert menu.main([]) == 0
    assert calls["captureone"] == []                # the remembered "no"
    assert len(calls["manifest"]) == 2


def test_7_a_failed_capture_one_step_is_not_done(tmp_path, monkeypatch, menu_env, capsys):
    calls, codes = menu_env
    codes["captureone"] = 1
    answers(monkeypatch, manifest_answers(tmp_path / "m.csv", tmp_path / "out", "2", "", "", "y"))
    assert menu.main([]) == 0
    assert len(calls["captureone"]) == 2
    assert "Manifest backup: done." not in capsys.readouterr().out


def test_7_verify_full_asks_max_gb_and_checks_capture_one(tmp_path, monkeypatch, menu_env):
    calls, _codes = menu_env
    manifest = tmp_path / "m.csv"
    dest = tmp_path / "out"
    (dest / c1.BACKUP_SUBFOLDER).mkdir(parents=True)
    #                                               choice full max  c1verify
    answers(monkeypatch, manifest_answers(manifest, dest, "3", "2", "50", ""))
    assert menu.main([]) == 0
    assert calls["manifest"] == [["--manifest", str(manifest), "--dest", str(dest),
                                  "--verify", "full", "--max-gb", "50.0"]]
    assert calls["captureone"] == [["--verify", str(dest)]]


def test_7_verify_quick_and_no_capture_one_backup(tmp_path, monkeypatch, menu_env):
    calls, _codes = menu_env
    manifest = tmp_path / "m.csv"
    dest = tmp_path / "out"
    dest.mkdir()
    answers(monkeypatch, manifest_answers(manifest, dest, "3", ""))
    assert menu.main([]) == 0
    assert calls["manifest"] == [["--manifest", str(manifest), "--dest", str(dest),
                                  "--verify", "quick"]]
    assert calls["captureone"] == []


def test_7_verify_full_default_max_gb_comes_from_the_backend(tmp_path, monkeypatch, menu_env):
    calls, _codes = menu_env
    monkeypatch.setattr(menu._backend(), "VERIFY_MAX_GB", 123.0)
    manifest = tmp_path / "m.csv"
    dest = tmp_path / "out"
    dest.mkdir()
    answers(monkeypatch, manifest_answers(manifest, dest, "3", "2", ""))
    assert menu.main([]) == 0
    assert calls["manifest"][0][-2:] == ["--max-gb", "123.0"]


# ---- S9: real read failures (icacls), Windows only ------------------------------------------
def icacls(*args):
    return subprocess.run(["icacls", *map(str, args)], capture_output=True, text=True,
                          errors="replace")


@pytest.fixture
def deny_read():
    denied = []

    def deny(folder):
        user = os.environ.get("USERNAME")
        if not user:
            pytest.skip("no USERNAME")
        # list the folder and read the attributes: denied. Not (RX): it also takes
        # READ_CONTROL and SYNCHRONIZE, and then not even /remove:d can undo it.
        result = icacls(folder, "/deny", f"{user}:(OI)(CI)(RD,RA)")
        if result.returncode != 0:
            pytest.skip(f"icacls failed: {result.stdout} {result.stderr}")
        denied.append(folder)

    yield deny
    user = os.environ.get("USERNAME")
    for folder in denied:
        result = icacls(folder, "/remove:d", user)
        assert result.returncode == 0, f"the deny on {folder} stayed: {result.stdout}"
        os.listdir(folder)


@pytest.mark.skipif(sys.platform != "win32", reason="icacls")
def test_s9_b3_a_folder_denied_for_real_deletes_nothing(tmp_path, monkeypatch, mlogs, deny_read,
                                                        capsys):
    monkeypatch.setattr(mob, "read_shot_times", no_dates)
    _y2024, y2025 = mobile_tree(tmp_path)
    dest = tmp_path / "H"
    assert mob.main(["--source", str(y2025), "--dest", str(dest)]) == 0
    before = mirror(dest)
    deny_read(y2025 / "250202_B" / "_EXPORT")
    assert mob.main(["--source", str(y2025), "--dest", str(dest)]) == 1
    assert mirror(dest) == before
    assert "250202_B" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform != "win32", reason="icacls")
def test_s9_b4_a_folder_denied_for_real_is_not_gone(tmp_path, deny_read, photobackup_log_dir):
    make_tree(tmp_path, {"G/2024/tripA/DSC_0001.NEF": 100,
                         "G/2024/tripA/_EXPORT/DSC_0001_v1.jpg": 50,
                         "G/2024/tripB/DSC_0002.NEF": 101,
                         "G/2024/tripB/_EXPORT/DSC_0002_v1.jpg": 51})
    source = tmp_path / "G" / "2024"
    dest = tmp_path / "D"
    assert sb.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    deny_read(source / "tripB")
    assert sb.main(argv_for(source, dest, tmp_path, "--verify", "quick")) == 1
    rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_verify.csv"))))
    status = {Path(row["rel"]).name: row["status"] for row in rows}
    assert status["DSC_0002.NEF"] == "SOURCE_UNREADABLE"
    assert sb.main(argv_for(source, dest, tmp_path, "--yes", "--move-orphans")) == 1
    assert (dest / "G" / "2024" / "tripB" / "DSC_0002.NEF").exists()
    assert any(row.rel.endswith("DSC_0002.NEF") for row in catalog_rows(dest))
