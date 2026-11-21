"""JPEG kept as JXL (v0.14.0-rc3): jxl-photo's lossless transcode, proven byte
for byte by rebuilding the JPEG; and the restore's choice of formats
(--convert-back all / lossless / none). jxl-photo is played by
tests/fake_jxlphoto.py; the TIFF side is in test_tiff_jxl.py."""
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

import manifest_backup as mb
import mirror_backup as mir
import selective_backup as sb

from conftest import argv_for, latest_log, row_count, snapshot
from test_tiff_jxl import (JXL, OTHERS, TIFFS, VERSIONS, backed, backup,  # noqa: F401
                           closed_console, converted, db, env, mirror_rel, newest_csv, restore,
                           restore_statuses, restored, under, write_manifest)

JPEGS = {"2025/shoot/DSC_0001.JPG": b"\xff\xd8camera JPEG of DSC_0001" * 30,
         "2025/phone/20250101_120000.jpeg": b"\xff\xd8a phone's photo" * 40,
         "2025/phone/cmyk.jpg": b"\xff\xd8CMYK, that cjxl will not transcode" * 20}
EXPORT_JPEG = "2025/shoot/_EXPORT/DSC_0001_v1.jpg"
ALL_JPEGS = set(JPEGS) | {EXPORT_JPEG}


def add_jpegs(env):
    for rel, data in JPEGS.items():
        path = env.src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def calls(env, *, command):
    if not env.calls.exists():
        return []
    import json
    every = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(env.calls.glob("*.json"))]
    return [call for call in every if call[0] == command and call[1] != "--version"]


# ---- J16 the plan --------------------------------------------------------------------------
def test_j16_every_jpeg_exports_too_is_planned_as_jxl(env):
    add_jpegs(env)
    args = sb._build_parser().parse_args(argv_for(
        env.src / "2025", env.dest, env.src, "--unexported", "copy", "--export-formats", "all",
        "--jpeg", "jxl"))
    rels = {item.rel.replace("\\", "/"): item.convert for item in sb.plan(args).files}
    for rel in ALL_JPEGS:
        assert rels[rel + ".jxl"] == sb.JPEG_CONVERT, rel
    assert rels["2025/shoot/DSC_0001.tif"] is None          # --tiff copy: as it is
    args = sb._build_parser().parse_args(argv_for(
        env.src / "2025", env.dest, env.src, "--unexported", "copy", "--export-formats", "all"))
    rels = {item.rel.replace("\\", "/") for item in sb.plan(args).files}
    assert ALL_JPEGS <= rels                                # --jpeg copy (the default)


def test_j16_a_tiff_profile_cannot_be_named_jpeg(env, monkeypatch, capsys):
    monkeypatch.setattr(sb, "TIFF_JXL_PROFILES", {"jpeg": {"distance": 0, "scan_distance": 0,
                                                           "eight_bit_distance": 0}})
    assert backup(env) == 2
    assert "cannot be a profile name" in capsys.readouterr().err


# ---- J17 a JPEG becomes JXL, proven byte for byte ------------------------------------------------
def test_j17_jpegs_become_jxl_proven_by_their_rebuild(env, capsys, monkeypatch):
    add_jpegs(env)
    before = snapshot(env.src)
    assert backup(env, "--jpeg", "jxl") == 0
    assert snapshot(env.src) == before
    have = backed(env.dest)
    assert {rel + ".jxl" for rel in ALL_JPEGS} <= have and not ALL_JPEGS & have
    assert TIFFS <= have                                    # TIFFs: --tiff copy
    rows = {key: (kind, lossless) for key, kind, lossless in db(
        env.dest, "SELECT key, kind, lossless FROM converted")}
    assert set(rows.values()) == {("jpeg", 1)} and len(rows) == len(ALL_JPEGS)
    transcodes = calls(env, command="transcode")
    made = [call for call in transcodes if "--format" not in call]
    rebuilt = [call for call in transcodes if "--format" in call]
    assert len(made) == len(rebuilt) == len(ALL_JPEGS)
    assert all(call[3:] == ["--mode", "0", "--workers", "1", "--effort", str(sb.JPEG_JXL_EFFORT),
                            "--force-transcode"] for call in made)
    out = capsys.readouterr().out
    last = [line for line in out.splitlines() if line.startswith("[4/4] to JXL: ")][-1]
    assert "kept as they are  whole run " in last and " MB/s (ETA " in last   # rc6
    assert row_count(out, "JPEG kept as JXL: to convert") == "4"
    assert "lossless: the same bytes back" in out
    assert "Kept as JXL: 4 converted (4 JPEG)" in out
    assert backup(env, "--jpeg", "jxl") == 0                # a second run: nothing to do
    assert row_count(capsys.readouterr().out, "JPEG kept as JXL: to convert") == "0"
    assert len(calls(env, command="transcode")) == len(transcodes)
    # every effort gives the same bytes back: another one is no reason to convert again (rc4)
    monkeypatch.setattr(sb, "JPEG_JXL_EFFORT", sb.JPEG_JXL_EFFORT + 2)
    assert backup(env, "--jpeg", "jxl") == 0
    assert row_count(capsys.readouterr().out, "JPEG kept as JXL: to convert") == "0"
    assert len(calls(env, command="transcode")) == len(transcodes)


@pytest.mark.parametrize("how, item", [("exit", "JP-15"), ("none", "JP-15"),
                                       ("mismatch", "JP-16")])
def test_j18_a_jpeg_that_does_not_come_back_is_copied_as_it_is(env, monkeypatch, how, item,
                                                               photobackup_log_dir):
    add_jpegs(env)
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", how)
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "cmyk")
    assert backup(env, "--jpeg", "jxl") == 1
    log = latest_log(photobackup_log_dir)
    assert "CONVERT FAILED" in log and f"({item})" in log
    have = backed(env.dest)
    assert "2025/phone/cmyk.jpg" in have and "2025/phone/cmyk.jpg.jxl" not in have
    assert (env.dest / "2025/phone/cmyk.jpg").read_bytes() == JPEGS["2025/phone/cmyk.jpg"]
    assert "2025/phone/20250101_120000.jpeg.jxl" in have
    monkeypatch.delenv("FAKE_JXLPHOTO_FAIL")
    assert backup(env, "--jpeg", "jxl") == 0                # it works now: the copy leaves
    assert "2025/phone/cmyk.jpg.jxl" in backed(env.dest)
    assert "2025/phone/cmyk.jpg" in under(env.dest, VERSIONS)


# ---- J19 the restore's choice of formats ------------------------------------------------------
@pytest.fixture
def both(env):
    """TIFFs (one lossy, two lossless scans) and JPEGs, all kept as JXL."""
    add_jpegs(env)
    assert backup(env, "--tiff", "jxl", "--jpeg", "jxl") == 0
    lossless = {source.replace("\\", "/"): flag for source, flag in db(
        env.dest, "SELECT source_rel, lossless FROM converted WHERE page = 0")}
    assert lossless["2025/shoot/DSC_0001.tif"] == 0        # developed 16-bit, d=0.05
    assert lossless["2025/scans/000037.tiff"] == 1          # a scan, d=0
    assert lossless["2025/scans/000038.tif"] == 1
    return env


def test_j19_all_back_jpegs_byte_for_byte(both, photobackup_log_dir):
    assert restore(both, "--to", both.out) == 0
    want = (OTHERS - {EXPORT_JPEG}) | TIFFS | ALL_JPEGS
    assert restored(both) == want
    for rel in ALL_JPEGS:
        assert (both.out / rel).read_bytes() == (both.src / rel).read_bytes(), rel
        assert (both.out / rel).stat().st_mtime_ns == (both.src / rel).stat().st_mtime_ns
    statuses = restore_statuses(photobackup_log_dir)
    assert {statuses[rel] for rel in ALL_JPEGS} == {"RESTORED"}
    assert {statuses[rel] for rel in TIFFS} == {"RESTORED_FROM_JXL"}


def test_j19_only_the_lossless_back_the_lossy_tiff_stays_jxl(both, photobackup_log_dir):
    assert restore(both, "--to", both.out, "--convert-back", "lossless") == 0
    have = restored(both)
    assert ALL_JPEGS <= have
    assert {"2025/scans/000037.tiff", "2025/scans/000038.tif"} <= have
    assert "2025/shoot/DSC_0001.tif.jxl" in have and "2025/shoot/DSC_0001.tif" not in have
    statuses = restore_statuses(photobackup_log_dir)
    assert statuses["2025/shoot/DSC_0001.tif.jxl"] == "RESTORED_AS_JXL"
    shutil.rmtree(both.out)                                 # --only by the TIFF's own name
    assert restore(both, "--to", both.out, "--convert-back", "lossless",
                   "--only", "2025/shoot/DSC_0001.tif") == 0
    assert restored(both) == {"2025/shoot/DSC_0001.tif.jxl"}


@pytest.mark.parametrize("option", [["--convert-back", "none"], ["--keep-jxl"]])
def test_j19_none_back_every_one_as_jxl(both, option, photobackup_log_dir):
    assert restore(both, "--to", both.out, *option) == 0
    have = restored(both)
    assert {rel + ".jxl" for rel in ALL_JPEGS} <= have and JXL <= have
    assert not (ALL_JPEGS | TIFFS) & have
    assert set(restore_statuses(photobackup_log_dir).values()) <= {"RESTORED", "RESTORED_AS_JXL"}


def test_j19_a_rebuild_that_is_not_the_original_writes_nothing(both, monkeypatch,
                                                               photobackup_log_dir):
    monkeypatch.setenv("FAKE_JXLPHOTO_REBUILD_WRONG", "1")
    assert restore(both, "--to", both.out, "--only", "2025/phone") == 1
    statuses = restore_statuses(photobackup_log_dir)
    assert statuses["2025/phone/cmyk.jpg"] == "ERROR"
    assert not (both.out / "2025/phone/cmyk.jpg").exists()
    assert not [path for path in (both.out / "2025/phone").iterdir() if "pbtmp" in path.name]


# ---- J20 a catalog of rc2 (schema 2) --------------------------------------------------------------
def test_j20_a_schema_2_catalog_reads_and_is_upgraded(env, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    with connection:                                        # what rc2 wrote
        connection.execute("ALTER TABLE converted DROP COLUMN kind")
        connection.execute("ALTER TABLE converted DROP COLUMN lossless")
        connection.execute("UPDATE meta SET v='2' WHERE k='schema_version'")
    connection.close()
    catalog = sb._Catalog(str(env.dest), create=False, readonly=True)
    try:
        rows = catalog.converted_rows()
    finally:
        catalog.close()
    assert {item.kind for item in rows.values()} == {"tiff"}
    assert {item.lossless for item in rows.values()} == {None}
    # not known: "only the lossless" keeps every TIFF as JXL
    assert restore(env, "--to", env.out, "--convert-back", "lossless") == 0
    assert JXL <= restored(env)
    assert backup(env, "--tiff", "jxl") == 0                # opened to write: schema 3
    assert db(env.dest, "SELECT v FROM meta WHERE k='schema_version'") == [("3",)]
    columns = {info[1] for info in db(env.dest, "PRAGMA table_info(converted)")}
    assert {"kind", "lossless"} <= columns


# ---- J21 verify, J22 mirror, J23 manifest ----------------------------------------------------------
def test_j21_a_jpegs_jxl_verifies_without_marks(env, photobackup_log_dir):
    add_jpegs(env)
    assert backup(env, "--jpeg", "jxl") == 0
    assert sb.main(argv_for(env.src / "2025", env.dest, env.src, "--verify", "full",
                            "--unexported", "copy", "--export-formats", "all",
                            "--jpeg", "jxl")) == 0


def test_j22_the_mirror_trashes_our_jpeg_copies(env, monkeypatch, photobackup_log_dir, capsys):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    add_jpegs(env)
    copy = write_manifest(env.root / "copy.csv", (env.src / "2025", "6", "", "", ""))
    with open(env.root / "jxl.csv", "w", newline="", encoding="utf-8") as handle:
        handle.write("Folder,Mode,Originals,Mirror,Tiff,Jpeg\n"
                     f'"{env.src / "2025"}",6,,,,jxl\n')
    assert mir.main(["--manifest", str(copy), "--dest", str(env.dest), "--yes"]) == 0
    capsys.readouterr()
    assert mir.main(["--manifest", str(env.root / "jxl.csv"), "--dest", str(env.dest),
                     "--yes"]) == 0
    out = capsys.readouterr().out
    assert "convert JPEG to JXL (lossless)" in out and "old copy replaced by its JXL" in out
    have = backed(env.dest)
    assert {mirror_rel(env, rel) + ".jxl" for rel in ALL_JPEGS} <= have
    assert {mirror_rel(env, rel) for rel in ALL_JPEGS} <= under(env.dest, mir.MIRROR_TRASH_FOLDER)


def test_j23_the_manifest_jpeg_column(env, capsys, photobackup_log_dir):
    add_jpegs(env)
    path = env.root / "m.csv"
    path.write_text("Folder,Mode,Originals,Mirror,Tiff,Jpeg\n"
                    f'"{env.src / "2025"}",6,,,,jxl\n', encoding="utf-8")
    assert mb.main(["--manifest", str(path), "--analyze"]) == 0
    analysis = newest_csv(photobackup_log_dir, "analysis")
    chosen = [row for row in analysis if row["chosen"] == "yes"]
    assert int(chosen[0]["jpeg_to_jxl_bytes"]) == sum(
        (env.src / rel).stat().st_size for rel in ALL_JPEGS)
    assert int(chosen[0]["tiff_to_jxl_bytes"]) == 0
    assert mb.main(["--manifest", str(path), "--dest", str(env.dest), "--yes"]) == 0
    assert {mirror_rel(env, rel) + ".jxl" for rel in ALL_JPEGS} <= backed(env.dest)
    path.write_text("Folder,Mode,Originals,Mirror,Tiff,Jpeg\n"
                    f'"{env.src / "2025"}",6,,,,lossy\n', encoding="utf-8")
    assert mb.main(["--manifest", str(path), "--dest", str(env.dest), "--yes"]) == 2
    assert "Jpeg must be copy or jxl" in capsys.readouterr().err


# ---- J24 the fast way: libjxl's djxl proves and rebuilds a JPEG (v0.14.0-rc4) -----------------
FAKE_DJXL = Path(__file__).with_name("fake_djxl.py")


def test_j24_djxl_proves_and_rebuilds_the_jpegs(env, monkeypatch, photobackup_log_dir):
    add_jpegs(env)
    monkeypatch.setattr(sb, "DJXL", str(FAKE_DJXL))
    assert backup(env, "--jpeg", "jxl") == 0
    transcodes = calls(env, command="transcode")
    assert transcodes and not [call for call in transcodes if "--format" in call]
    assert len(calls(env, command="djxl")) == len(ALL_JPEGS)
    assert {rel + ".jxl" for rel in ALL_JPEGS} <= backed(env.dest)
    before = len(calls(env, command="transcode"))
    assert restore(env, "--to", env.out) == 0           # back by djxl, straight from the backup
    for rel in ALL_JPEGS:
        assert (env.out / rel).read_bytes() == (env.src / rel).read_bytes(), rel
    assert len(calls(env, command="transcode")) == before
    statuses = restore_statuses(photobackup_log_dir)
    assert {statuses[rel] for rel in ALL_JPEGS} == {"RESTORED"}


def test_j24_a_wrong_rebuild_is_never_kept_nor_restored(env, monkeypatch, photobackup_log_dir):
    add_jpegs(env)
    monkeypatch.setattr(sb, "DJXL", str(FAKE_DJXL))
    monkeypatch.setenv("FAKE_DJXL_WRONG", "1")
    assert backup(env, "--jpeg", "jxl") == 1            # the proof fails: the JPEG as it is
    assert "(JP-18)" in latest_log(photobackup_log_dir)         # djxl rebuilt it
    assert ALL_JPEGS <= backed(env.dest)
    monkeypatch.delenv("FAKE_DJXL_WRONG")
    assert backup(env, "--jpeg", "jxl") == 0
    monkeypatch.setenv("FAKE_DJXL_WRONG", "1")
    assert restore(env, "--to", env.out, "--only", "2025/phone") == 1
    statuses = restore_statuses(photobackup_log_dir)
    assert statuses["2025/phone/cmyk.jpg"] == "ERROR"
    assert not (env.out / "2025/phone/cmyk.jpg").exists()
    monkeypatch.delenv("FAKE_DJXL_WRONG")
    jxl = env.dest / "2025/phone/cmyk.jpg.jxl"          # a damaged copy in the backup
    data = bytearray(jxl.read_bytes())
    data[-1] ^= 0xFF
    jxl.write_bytes(bytes(data))
    shutil.rmtree(env.out)
    assert restore(env, "--to", env.out, "--only", "2025/phone/cmyk.jpg") == 1
    assert restore_statuses(photobackup_log_dir)["2025/phone/cmyk.jpg"] == "CORRUPT_IN_BACKUP"



def test_j24_a_jpeg_needs_only_djxl_to_come_back(env, monkeypatch, photobackup_log_dir, capsys):
    add_jpegs(env)
    monkeypatch.setattr(sb, "DJXL", str(FAKE_DJXL))
    assert backup(env, "--jpeg", "jxl") == 0
    monkeypatch.setattr(sb, "JXLPHOTO", "")              # a new computer: libjxl, no jxl-photo
    capsys.readouterr()
    assert restore(env, "--to", env.out) == 0
    statuses = restore_statuses(photobackup_log_dir)
    assert {statuses[rel] for rel in ALL_JPEGS} == {"RESTORED"}
    for rel in ALL_JPEGS:
        assert (env.out / rel).read_bytes() == (env.src / rel).read_bytes(), rel
    assert "JPEG by djxl" in capsys.readouterr().out
    monkeypatch.setattr(sb, "DJXL", "none")              # neither: NEEDS_JXLPHOTO, as before
    shutil.rmtree(env.out)
    assert restore(env, "--to", env.out) == 1
    assert {restore_statuses(photobackup_log_dir)[rel] for rel in ALL_JPEGS} == {"NEEDS_JXLPHOTO"}

# ---- J25 how many at a time ---------------------------------------------------------------------
def test_j25_workers_follow_the_machine(monkeypatch, capsys):
    monkeypatch.setattr(sb, "JXL_WORKERS", 0)
    cores = sb._physical_cores()                        # rc4: one per physical core
    if os.name == "nt":
        assert cores is not None and 1 <= cores <= (os.cpu_count() or 1)
    assert sb._jxl_workers() == max(1, cores or os.cpu_count() or 1)
    monkeypatch.setattr(sb, "_physical_cores", lambda: None)    # cannot be counted
    assert sb._jxl_workers() == max(1, os.cpu_count() or 1)
    monkeypatch.setattr(sb, "JXL_WORKERS", 3)
    assert sb._jxl_workers() == 3
    monkeypatch.setattr(sb, "JXL_TIFF_WORKERS", 0)
    assert 1 <= sb._jxl_tiff_workers() <= max(1, (os.cpu_count() or 1) // 4)
    monkeypatch.setattr(sb, "JXL_TIFF_WORKERS", 2)
    assert sb._jxl_tiff_workers() == 2
    monkeypatch.setattr(sb, "JXL_TIFF_WORKERS", -1)
    assert any("JXL_TIFF_WORKERS" in error for error in sb._tiff_profile_errors())


# ---- J26 jxl-photo starts fast: each command's own script, its bytecode kept (v0.14.0-rc4) -----
def _backend_scripts(env, *, leave_out=()):
    """jxl-photo's layout beside the fake: one script per command (JP-19)."""
    for command, module in sb._JXLPHOTO_BACKENDS.items():
        if module in leave_out:
            continue
        (env.tool.parent / f"{module}.py").write_text(
            "import sys\n"
            "import jxl_photo\n"
            "if __name__ == '__main__':\n"
            f"    sys.exit(jxl_photo.main([{command!r}] + sys.argv[1:]))\n", encoding="utf-8")


def test_j26_a_command_runs_its_own_script_with_its_bytecode_kept(env, photobackup_log_dir):
    add_jpegs(env)
    _backend_scripts(env)
    tool = str(env.tool)
    for command, module in sb._JXLPHOTO_BACKENDS.items():
        argv = sb._jxlphoto_argv(tool, [command, "a", "b"])
        assert argv[1] == "-c" and argv[3:] == [str(env.tool.parent), module, "a", "b"]
    assert sb._jxlphoto_argv(tool, ["other", "a"])[1:] == ["-B", tool, "other", "a"]
    assert sb._jxlphoto_argv("C:/x/jxl-photo.exe", ["transcode", "a"]) == [
        "C:/x/jxl-photo.exe", "transcode", "a"]
    assert backup(env, "--jpeg", "jxl", "--tiff", "jxl") == 0
    assert {rel + ".jxl" for rel in JPEGS} <= backed(env.dest)
    assert restore(env, "--to", env.out) == 0
    for rel in ALL_JPEGS:
        assert (env.out / rel).read_bytes() == (env.src / rel).read_bytes(), rel
    cache = Path(photobackup_log_dir) / "pycache"
    kept = {path.name.split(".")[0] for path in cache.rglob("*.pyc")}
    assert {"jxl_tiff_encoder", "jxl_jpeg_transcoder", "jxl_photo"} <= kept
    assert not list(env.tool.parent.rglob("__pycache__"))       # nothing beside jxl-photo


def test_j26_a_command_without_its_script_goes_through_jxl_photo(env, photobackup_log_dir):
    add_jpegs(env)
    _backend_scripts(env, leave_out=("jxl_jpeg_transcoder",))
    assert sb._jxlphoto_argv(str(env.tool), ["transcode", "a"])[1:3] == ["-B", str(env.tool)]
    assert backup(env, "--jpeg", "jxl") == 0
    assert {rel + ".jxl" for rel in JPEGS} <= backed(env.dest)
    assert not list(env.tool.parent.rglob("__pycache__"))
