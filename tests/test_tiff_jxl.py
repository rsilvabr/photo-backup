"""TIFFs kept as JXL in the backup (v0.14.0-rc2): jxl-photo converts them, the
catalog proves where each JXL came from, the restore makes the TIFF again.
jxl-photo is played by tests/fake_jxlphoto.py (J1-J14 of the plan)."""
import csv
import io
import json
import os
import re
import shutil
import signal
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import manifest_backup as mb
import mirror_backup as mir
import restore_backup as rb
import selective_backup as sb

from conftest import argv_for, latest_csv, latest_log, make_tree, row_count, snapshot

HOSTILE = "吾妻山公園_菜の花 [FINAL] 5% off"
FAKE = Path(__file__).with_name("fake_jxlphoto.py")
TIFF_A = "TIFF16 DSC_0001 " * 40
SCAN_B = "SCAN 000037 " * 50
SCAN_C = "SCAN 000038 PAGES=3 " * 30
TREE = {
    "2025/shoot/DSC_0001.NEF": 1001,
    "2025/shoot/DSC_0001.tif": TIFF_A,
    "2025/shoot/DSC_0001.xmp": 40,
    "2025/shoot/_EXPORT/DSC_0001_v1.jpg": 300,
    "2025/shoot/_EXPORT/TIFF16/DSC_0001_v1.tif": 400,
    "2025/scans/000037.tiff": SCAN_B,
    "2025/scans/000038.tif": SCAN_C,
    "2025/scans/notes.txt": 20,
}
OTHERS = {"2025/shoot/DSC_0001.NEF", "2025/shoot/DSC_0001.xmp",
          "2025/shoot/_EXPORT/DSC_0001_v1.jpg", "2025/shoot/_EXPORT/TIFF16/DSC_0001_v1.tif",
          "2025/scans/notes.txt"}
JXL = {"2025/shoot/DSC_0001.tif.jxl", "2025/scans/000037.tiff.jxl", "2025/scans/000038.tif.jxl",
       "2025/scans/000038_page1.tif.jxl", "2025/scans/000038_page2.tif.jxl"}
TIFFS = {"2025/shoot/DSC_0001.tif", "2025/scans/000037.tiff", "2025/scans/000038.tif"}
SETTINGS = "jxl: d=0.05 scan=0 8bit=0 e=7"
VERSIONS = "versions"                                   # under CATALOG_FOLDER


@pytest.fixture(autouse=True)
def closed_console(monkeypatch):
    def closed(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", closed)


@pytest.fixture(params=["copy", "rename"])
def env(request, tmp_path, monkeypatch):
    """A source, a fake jxl-photo, and how a finished JXL reaches the backup:
    `copy` (the work folder on another drive, as the system's temp folder is on
    a real computer; conftest's default) or `rename` (the same drive)."""
    root = tmp_path / HOSTILE
    src = make_tree(root / "SRC", TREE)
    tool_dir = tmp_path / "jxl-photo"
    tool_dir.mkdir()
    tool = tool_dir / "jxl_photo.py"
    shutil.copy2(FAKE, tool)
    calls = tmp_path / "calls"
    monkeypatch.setattr(sb, "JXLPHOTO", str(tool))
    monkeypatch.setattr(sb, "JXL_WORKERS", 2)
    monkeypatch.setenv("FAKE_JXLPHOTO_CALLS", str(calls))
    for name in ("FAKE_JXLPHOTO_FAIL", "FAKE_JXLPHOTO_ONLY", "FAKE_JXLPHOTO_DECODE_FAIL",
                 "FAKE_JXLPHOTO_VERSION", "FAKE_JXLPHOTO_ENVS", "FAKE_JXLPHOTO_LEAK_TEMP",
                 "FAKE_JXLPHOTO_SLEEP", "FAKE_JXLPHOTO_SLEEP_ONLY", "FAKE_JXLPHOTO_SLEEP_PIDS"):
        monkeypatch.delenv(name, raising=False)
    if request.param == "rename":
        monkeypatch.setattr(sb, "_jxl_copy_needed", lambda work_root, dest: False)
    sb._JXLPHOTO_VERSIONS.clear()
    return SimpleNamespace(root=root, src=src, dest=root / "dest", tool=tool, calls=calls,
                           out=root / "restored", mode=request.param)


def no_work_left(env):
    """Nothing of the conversions is left: this run's work folder is gone, and
    the backup holds no work folder (before v0.14.0-rc4 it was there)."""
    work = Path(sb.JXL_WORK_DIR, sb.JXL_WORK_FOLDER, str(os.getpid()))
    return not work.exists() and not Path(env.dest, sb.CATALOG_FOLDER, "converting").exists()


def backup(env, *extra):
    return sb.main(argv_for(env.src / "2025", env.dest, env.src, "--yes", "--unexported", "copy",
                            "--export-formats", "all", *extra))


def encodes(env):
    if not env.calls.exists():
        return []
    calls = [json.loads(path.read_text(encoding="utf-8"))
             for path in sorted(env.calls.glob("*.json"))]
    return [call for call in calls if call[0] == "encode" and call[1] != "--version"]


def backed(dest):
    """The backup's files outside _photo_backup."""
    result = set()
    for dirpath, _dirs, names in os.walk(dest):
        rel_dir = os.path.relpath(dirpath, dest).replace("\\", "/")
        if rel_dir.split("/")[0] == sb.CATALOG_FOLDER:
            continue
        result |= {(name if rel_dir == "." else f"{rel_dir}/{name}") for name in names}
    return result


def under(dest, *parts):
    """Files under <dest>/_photo_backup/<parts...>, relative to their stamp folder."""
    root = Path(dest, sb.CATALOG_FOLDER, *parts)
    if not root.exists():
        return set()
    found = set()
    for path in root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            found.add(rel.split("/", 1)[1] if "/" in rel else rel)
    return found


def db(dest, query):
    connection = sqlite3.connect(str(Path(dest) / sb.CATALOG_FOLDER / "catalog.sqlite"))
    try:
        return connection.execute(query).fetchall()
    finally:
        connection.close()


def converted(dest):
    return {key.replace("\\", "/"): (source_rel.replace("\\", "/"), page, pages, tool, settings)
            for key, source_rel, page, pages, tool, settings in db(
                dest, "SELECT key, source_rel, page, pages, tool, settings FROM converted")}


def newest_csv(log_dir, kind):
    """The newest CSV of a kind (a second run in the same second is <stamp>_<kind>_2)."""
    files = sorted(Path(log_dir).glob(f"*_{kind}*.csv"), key=lambda path: path.stat().st_mtime_ns)
    assert files, f"no {kind} CSV in {log_dir}"
    return list(csv.DictReader(io.StringIO(files[-1].read_text(encoding="utf-8-sig"))))


def restore_statuses(log_dir):
    return {row["rel"].replace("\\", "/"): row["status"] for row in newest_csv(log_dir, "restore")}


def srcsum(path):
    return sb._jxlphoto_srcsum(sb._sha256_file(str(path)))


# ---- J1 the plan ---------------------------------------------------------------------------
def test_j1_the_plan(env):
    def planned(*extra):
        args = sb._build_parser().parse_args(argv_for(
            env.src / "2025", env.dest, env.src, "--unexported", "copy", "--export-formats", "all",
            *extra))
        result = sb.plan(args)
        return result, {item.rel.replace("\\", "/"): item.convert for item in result.files}

    _plan, rels = planned("--tiff", "jxl")
    assert rels["2025/shoot/DSC_0001.tif.jxl"] == "jxl"
    assert rels["2025/scans/000037.tiff.jxl"] == "jxl"
    assert rels["2025/scans/000038.tif.jxl"] == "jxl"
    assert rels["2025/shoot/_EXPORT/TIFF16/DSC_0001_v1.tif"] is None    # an export: never
    assert rels["2025/shoot/DSC_0001.NEF"] is None
    assert not TIFFS & set(rels)
    _plan, rels = planned()                                             # --tiff copy: as before
    assert TIFFS <= set(rels) and not any(rels.values())
    # a real file named like the JXL of a TIFF beside it: one destination, an error
    (env.src / "2025/scans/000037.tiff.jxl").write_bytes(b"not ours")
    plan_with, _rels = planned("--tiff", "jxl")
    assert any("000037.tiff.jxl" in error for error in plan_with.errors)


# ---- J2 new TIFFs become JXL ---------------------------------------------------------------
def test_j2_new_tiffs_become_jxl(env, capsys):
    before = snapshot(env.src)
    assert backup(env, "--tiff", "jxl") == 0
    assert snapshot(env.src) == before                  # the source is never written
    assert backed(env.dest) == OTHERS | JXL             # no TIFF but the export's
    rows = converted(env.dest)
    assert rows["2025/scans/000038_page2.tif.jxl"] == ("2025/scans/000038.tif", 2, 3,
                                                       "jxl-photo 9.9.9", SETTINGS)
    assert rows["2025/scans/000037.tiff.jxl"] == ("2025/scans/000037.tiff", 0, 1,
                                                  "jxl-photo 9.9.9", SETTINGS)
    assert len(rows) == 5
    # each file: a catalog row with the JXL's own hash, and the TIFF's mark
    files = {rel.replace("\\", "/"): (size, sha, source)
             for rel, size, sha, source in db(env.dest,
                                              "SELECT rel, size, sha256, source_path FROM files")}
    for rel in JXL:
        size, sha, source = files[rel]
        path = env.dest / rel
        assert size == path.stat().st_size and sha == sb._sha256_file(str(path))
        tiff = env.src / rows[rel.casefold()][0]
        assert os.path.normcase(source) == os.path.normcase(str(tiff))
        assert sb._jxl_tokens(str(path))["jxlphoto-srcsum"] == [srcsum(tiff)]
    calls = encodes(env)
    assert len(calls) == 3
    for call in calls:
        assert call[3:] == [
            "--mode", "0", "--distance", "0.05", "--scan-distance", "0",
            "--eight-bit-distance", "0", "--effort", "7", "--workers", "1",
            "--verify-roundtrip"]
    out = capsys.readouterr().out
    assert row_count(out, "TIFF kept as JXL: to convert") == "3"
    assert "jxl: d=0.05 scan=0 8bit=0 e=7" in out
    assert "Kept as JXL: 3 converted (3 TIFF)" in out
    assert no_work_left(env)


# ---- J3 a second run, a changed TIFF, another profile --------------------------------------
def test_j3_rerun_changed_tiff_and_profile(env, monkeypatch, capsys, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl") == 0
    out = capsys.readouterr().out
    assert row_count(out, "TIFF kept as JXL: to convert") == "0"
    assert row_count(out, "TIFF kept as JXL: already in the backup") == "3"
    assert len(encodes(env)) == 3
    orphans = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_orphans.csv"))))
    assert orphans == []                                # no page of a group is an orphan
    # a TIFF edited: CHANGED, made again with --update-changed; the old JXL to versions
    tiff = env.src / "2025/scans/000037.tiff"
    tiff.write_text(SCAN_B + "edited", encoding="utf-8")
    stat = tiff.stat()
    os.utime(tiff, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    assert backup(env, "--tiff", "jxl") == 0            # reported, not replaced
    assert len(encodes(env)) == 3
    assert backup(env, "--tiff", "jxl", "--update-changed") == 0
    assert len(encodes(env)) == 4
    assert sb._jxl_tokens(str(env.dest / "2025/scans/000037.tiff.jxl"))["jxlphoto-srcsum"] == [
        srcsum(tiff)]
    assert "2025/scans/000037.tiff.jxl" in under(env.dest, VERSIONS)
    # another profile: every TIFF is made again, the old JXL to versions
    monkeypatch.setattr(sb, "TIFF_JXL_PROFILES",
                        {"jxl": {"distance": 0.1, "scan_distance": 0, "eight_bit_distance": 0}})
    assert backup(env, "--tiff", "jxl", "--update-changed") == 0
    assert len(encodes(env)) == 7 and encodes(env)[-1][6] == "0.1"
    assert {settings for *_rest, settings in converted(env.dest).values()} == {
        "jxl: d=0.1 scan=0 8bit=0 e=7"}
    assert backed(env.dest) == OTHERS | JXL


# ---- J4 a TIFF copy already in the backup --------------------------------------------------
def test_j4_our_tiff_copies_become_jxl(env, capsys):
    assert backup(env) == 0                             # a backup made before: TIFF copies
    assert TIFFS <= backed(env.dest)
    assert backup(env, "--tiff", "jxl") == 0
    assert backed(env.dest) == OTHERS | JXL
    assert TIFFS <= under(env.dest, VERSIONS)
    assert not {rel for (rel,) in db(env.dest, "SELECT rel FROM files")} & {
        rel.replace("/", "\\") for rel in TIFFS}
    out = capsys.readouterr().out
    assert row_count(out, "copies their JXL replaces (to versions\\)") == "3"
    assert "3 copies in the backup replaced by their JXL (to versions)" in out


def test_j4_another_programs_copies_become_jxl_a_different_one_stays(env):
    for rel in TIFFS:                                   # FreeFileSync: same size and date
        target = env.dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(env.src / rel, target)
    other = env.dest / "2025/scans/000037.tiff"
    other.write_bytes(b"a different TIFF")
    assert backup(env, "--tiff", "jxl") == 0
    assert backed(env.dest) == OTHERS | JXL | {"2025/scans/000037.tiff"}
    assert other.read_bytes() == b"a different TIFF"    # not a copy of the source: left alone
    assert under(env.dest, VERSIONS) == {"2025/shoot/DSC_0001.tif",
                                                   "2025/scans/000038.tif"}


# ---- J5 a conversion that fails: the TIFF is copied as it is -------------------------------
@pytest.mark.parametrize("how", ["exit", "none", "srcsum", "short", "changed"])
def test_j5_a_failed_conversion_copies_the_tiff(env, monkeypatch, how, photobackup_log_dir):
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", how)
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "000038")
    assert backup(env, "--tiff", "jxl") == 1
    assert "CONVERT FAILED" in latest_log(photobackup_log_dir)
    have = backed(env.dest)
    assert "2025/scans/000038.tif" in have and not {rel for rel in have if "000038" in rel
                                                     and rel.endswith(".jxl")}
    assert (env.dest / "2025/scans/000038.tif").read_bytes() == (
        env.src / "2025/scans/000038.tif").read_bytes()
    assert {"2025/shoot/DSC_0001.tif.jxl", "2025/scans/000037.tiff.jxl"} <= have
    assert no_work_left(env)
    # the next run converts it, and the TIFF copy goes to versions
    monkeypatch.delenv("FAKE_JXLPHOTO_FAIL")
    assert backup(env, "--tiff", "jxl") == 0
    assert backed(env.dest) == OTHERS | JXL
    assert "2025/scans/000038.tif" in under(env.dest, VERSIONS)


def test_j5_no_jxlphoto_at_all_copies_every_tiff(env, monkeypatch):
    """jxl-photo gone between the check and the conversion: nothing is left
    without a copy."""
    monkeypatch.setattr(sb, "_jxlphoto_problem",
                        lambda path: "jxl-photo not found: (test)")
    monkeypatch.setattr(sb, "_validate_usage", lambda args: [])
    assert backup(env, "--tiff", "jxl") == 1
    assert backed(env.dest) == OTHERS | TIFFS


# ---- J6 a group: one more page missing in the backup ----------------------------------------
def test_j6_a_page_missing_in_the_backup_makes_the_group_again(env):
    assert backup(env, "--tiff", "jxl") == 0
    (env.dest / "2025/scans/000038_page1.tif.jxl").unlink()
    assert backup(env, "--tiff", "jxl", "--update-changed") == 0
    assert backed(env.dest) == OTHERS | JXL
    assert {"2025/scans/000038.tif.jxl", "2025/scans/000038_page2.tif.jxl"} <= under(
        env.dest, VERSIONS)
    assert len(encodes(env)) == 4


# ---- J7 the mirror and the manifest's Tiff column ------------------------------------------
def write_manifest(path, *lines):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Folder", "Mode", "Originals", "Mirror", "Tiff"])
        writer.writerows(lines)
    return path


def mirror_rel(env, rel):
    drive_root = os.path.splitdrive(str(env.src))[0] + os.sep
    return os.path.relpath(str(env.src / rel), drive_root).replace("\\", "/")


def test_j7_the_mirror_trashes_a_gone_group_and_our_tiff_copies(env, monkeypatch,
                                                                 photobackup_log_dir):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    man_copy = write_manifest(env.root / "copy.csv", (env.src / "2025", "6", "", "", ""))
    man_jxl = write_manifest(env.root / "jxl.csv", (env.src / "2025", "6", "", "", "jxl"))
    assert mir.main(["--manifest", str(man_copy), "--dest", str(env.dest), "--yes"]) == 0
    assert mir.main(["--manifest", str(man_jxl), "--dest", str(env.dest), "--yes"]) == 0
    have = backed(env.dest)
    assert {mirror_rel(env, rel) for rel in JXL} <= have
    assert not {mirror_rel(env, rel) for rel in TIFFS} & have
    trash = under(env.dest, mir.MIRROR_TRASH_FOLDER)
    assert {mirror_rel(env, rel) for rel in TIFFS} <= trash        # ours: to the trash
    rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_actions.csv"))))
    retired = [row for row in rows if row["action"] == "retire"]
    assert len(retired) == 3
    assert {row["result"] for row in retired} == {"to the trash (replaced by its JXL)"}
    # the TIFF of a group is gone from the source: the whole group to the trash
    (env.src / "2025/scans/000038.tif").unlink()
    assert mir.main(["--manifest", str(man_jxl), "--dest", str(env.dest), "--yes"]) == 0
    pages = {mirror_rel(env, rel) for rel in JXL if "000038" in rel}
    assert not pages & backed(env.dest)
    assert pages <= under(env.dest, mir.MIRROR_TRASH_FOLDER)
    assert not [key for key in converted(env.dest) if "000038" in key]


def test_j7_another_programs_tiff_copies_go_to_the_mirrors_trash(env, monkeypatch):
    """A backup FreeFileSync made: the TIFF copies have the source's size and
    date (what any run adopts as its own), so the mirror's trash takes them;
    a TIFF copy that differs from the source goes to versions\\."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    for rel in TREE:
        target = env.dest / mirror_rel(env, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(env.src / rel, target)
    other = env.dest / mirror_rel(env, "2025/scans/000037.tiff")
    other.write_bytes(b"not the source's TIFF")
    man = write_manifest(env.root / "jxl.csv", (env.src / "2025", "6", "", "", "jxl"))
    assert mir.main(["--manifest", str(man), "--dest", str(env.dest), "--yes"]) == 0
    assert {mirror_rel(env, rel) for rel in JXL} <= backed(env.dest)
    assert under(env.dest, mir.MIRROR_TRASH_FOLDER) == {
        mirror_rel(env, "2025/shoot/DSC_0001.tif"), mirror_rel(env, "2025/scans/000038.tif")}
    assert other.read_bytes() == b"not the source's TIFF"     # not a copy: never moved
    assert not under(env.dest, VERSIONS)


def test_j7_the_manifest_tiff_column_and_analyze(env, capsys, photobackup_log_dir):
    man = write_manifest(env.root / "m.csv", (env.src / "2025", "6", "", "", "jxl"))
    assert mb.main(["--manifest", str(man), "--analyze"]) == 0
    assert "of TIFF kept as JXL" in capsys.readouterr().out
    analysis = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir,
                                                          "_analysis.csv"))))
    chosen = [row for row in analysis if row["chosen"] == "yes"]
    assert int(chosen[0]["tiff_to_jxl_bytes"]) == sum(
        (env.src / rel).stat().st_size for rel in TIFFS)
    assert mb.main(["--manifest", str(man), "--dest", str(env.dest), "--yes"]) == 0
    assert {mirror_rel(env, rel) for rel in JXL} <= backed(env.dest)
    bad = write_manifest(env.root / "bad.csv", (env.src / "2025", "6", "", "", "jpeg"))
    assert mb.main(["--manifest", str(bad), "--dest", str(env.dest), "--yes"]) == 2


# ---- J8 verify ---------------------------------------------------------------------------------
def verify(env, kind):
    return sb.main(argv_for(env.src / "2025", env.dest, env.src, "--verify", kind,
                            "--unexported", "copy", "--export-formats", "all", "--tiff", "jxl"))


def verify_rows(log_dir):
    return {row["rel"].replace("\\", "/"): row["status"]
            for row in csv.DictReader(io.StringIO(latest_csv(log_dir, "_verify.csv")))}


def test_j8_verify(env, monkeypatch, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    assert verify(env, "quick") == 0
    assert verify(env, "full") == 0
    # the JXL does not say it came from the TIFF the catalog recorded
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    with connection:
        connection.execute("UPDATE converted SET source_sha256=? WHERE key LIKE '%000037%'",
                           ("ab" * 32,))
    connection.close()
    assert verify(env, "full") == 1
    assert verify_rows(photobackup_log_dir) == {"2025/scans/000037.tiff.jxl":
                                                "DEST_NOT_FROM_SOURCE"}
    # a damaged JXL (same size)
    page = env.dest / "2025/scans/000038_page1.tif.jxl"
    data = bytearray(page.read_bytes())
    data[-1] ^= 0xFF
    page.write_bytes(bytes(data))
    assert verify(env, "full") == 1
    assert verify_rows(photobackup_log_dir)["2025/scans/000038_page1.tif.jxl"] == "DEST_CORRUPT"
    # the TIFF edited since
    tiff = env.src / "2025/shoot/DSC_0001.tif"
    tiff.write_text(TIFF_A + "edited", encoding="utf-8")
    assert verify(env, "quick") == 1
    assert verify_rows(photobackup_log_dir)["2025/shoot/DSC_0001.tif.jxl"] == "SOURCE_CHANGED"


# ---- J9 restore -------------------------------------------------------------------------------
def restore(env, *extra):
    return rb.main(["--backup", str(env.dest), "--yes", *[str(item) for item in extra]])


def restored(env):
    return backed(env.out)


def test_j9_the_tiff_comes_back(env, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    before = snapshot(env.dest)
    assert restore(env, "--to", env.out) == 0
    assert snapshot(env.dest) == before                 # the backup is never written
    assert restored(env) == OTHERS | TIFFS
    for rel in TIFFS:                                   # the fake decodes to the same bytes
        assert (env.out / rel).read_bytes() == (env.src / rel).read_bytes()
        assert (env.out / rel).stat().st_mtime_ns == (env.src / rel).stat().st_mtime_ns
    statuses = restore_statuses(photobackup_log_dir)
    assert {statuses[rel] for rel in TIFFS} == {"RESTORED_FROM_JXL"}
    assert not [name for name in os.listdir(env.out / "2025/scans") if "pbtmp" in name]
    # a second restore: nothing is replaced; the fake decodes exactly, so each TIFF
    # there is the original's bytes
    assert restore(env, "--to", env.out) == 0
    statuses = restore_statuses(photobackup_log_dir)
    assert {statuses[rel] for rel in TIFFS} == {"ALREADY_THERE"}    # the fake is exact


def test_j9_keep_jxl_only_and_to_original(env, monkeypatch, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    assert restore(env, "--to", env.out, "--keep-jxl") == 0
    assert restored(env) == OTHERS | JXL
    shutil.rmtree(env.out)
    assert restore(env, "--to", env.out, "--only", "2025/scans/000038.tif") == 0
    assert restored(env) == {"2025/scans/000038.tif"}
    (env.src / "2025/scans/000037.tiff").unlink()
    assert restore(env, "--to-original", "--only", "2025/scans/000037.tiff.jxl") == 0
    assert (env.src / "2025/scans/000037.tiff").read_text(encoding="utf-8") == SCAN_B


def test_j9_without_jxlphoto_and_conflicts(env, monkeypatch, photobackup_log_dir, capsys):
    assert backup(env, "--tiff", "jxl") == 0
    monkeypatch.setattr(sb, "JXLPHOTO", "")
    assert restore(env, "--to", env.out) == 1
    assert restored(env) == OTHERS                      # every other file is restored
    out = capsys.readouterr().out
    assert "NEEDS_JXLPHOTO: 3 TIFF or JPEG kept as JXL" in out and "--convert-back none" in out
    monkeypatch.setattr(sb, "JXLPHOTO", str(env.tool))
    target = env.out / "2025/scans/000037.tiff"
    target.write_bytes(b"someone else's file")
    monkeypatch.setenv("FAKE_JXLPHOTO_DECODE_FAIL", "1")
    assert restore(env, "--to", env.out) == 1
    assert target.read_bytes() == b"someone else's file"
    statuses = restore_statuses(photobackup_log_dir)
    assert statuses["2025/scans/000037.tiff"] == "CONFLICT"
    assert statuses["2025/scans/000038.tif"] == "ERROR"
    assert not (env.out / "2025/scans/000038.tif").exists()
    assert not [name for name in os.listdir(env.out / "2025/scans") if "pbtmp" in name]


def test_j9_the_jxlphoto_kept_in_the_backup(env, monkeypatch, photobackup_log_dir):
    monkeypatch.setattr(sb, "KEEP_TOOLS_IN_BACKUP", True)
    assert backup(env, "--tiff", "jxl") == 0
    monkeypatch.setattr(sb, "JXLPHOTO", "")
    before = snapshot(env.dest)
    assert restore(env, "--to", env.out) == 0
    assert snapshot(env.dest) == before                 # running the kept copy writes nothing there
    assert restored(env) == OTHERS | TIFFS
    assert "kept in the backup" in latest_log(photobackup_log_dir)


# ---- J10 the catalog's schema ------------------------------------------------------------------
def test_j10_schema_1_becomes_2_and_an_unsafe_source_rel_is_damage(env):
    assert backup(env) == 0
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    with connection:
        connection.execute("DROP TABLE converted")
        connection.execute("UPDATE meta SET v='1' WHERE k='schema_version'")
    connection.close()
    catalog = sb._Catalog(str(env.dest), create=False, readonly=True)
    try:
        assert catalog.converted_rows() == {}           # a v0.14.0-rc1 catalog reads
    finally:
        catalog.close()
    assert restore(env, "--to", env.out) == 0
    assert backup(env, "--tiff", "jxl") == 0
    assert db(env.dest, "SELECT v FROM meta WHERE k='schema_version'") == [("3",)]
    assert len(converted(env.dest)) == 5
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    with connection:
        connection.execute("UPDATE converted SET source_rel='..\\..\\x.tif'")
    connection.close()
    with pytest.raises(RuntimeError, match="damaged"):
        sb._Catalog(str(env.dest), create=False, readonly=True)
    assert restore(env, "--to", env.out / "again") == 2


# ---- J11 the tools kept in the backup ------------------------------------------------------------
def test_j11_tools_kept_once_never_replaced(env, monkeypatch, tmp_path, capsys):
    extra = tmp_path / "photo-backup-exe 0.14.0.zip"
    extra.write_bytes(b"zip one")
    monkeypatch.setattr(sb, "KEEP_TOOLS_IN_BACKUP", True)
    monkeypatch.setattr(sb, "KEEP_TOOLS_EXTRA", (str(extra),))
    assert backup(env, "--tiff", "jxl") == 0
    tools = env.dest / sb.CATALOG_FOLDER / sb.TOOLS_FOLDER
    assert (tools / f"photo-backup {sb.__version__}" / "selective_backup.py").is_file()
    assert (tools / f"photo-backup {sb.__version__}" / "restore_backup.py").is_file()
    assert (tools / f"photo-backup {sb.__version__}" / "docs" / "safety.md").is_file()
    assert not (tools / f"photo-backup {sb.__version__}" / "backup_manifest.csv").exists()
    assert (tools / "jxl-photo 9.9.9" / "jxl_photo.py").is_file()
    assert (tools / "photo-backup-exe 0.14.0.zip").read_bytes() == b"zip one"
    assert "jxl-photo" in (tools / "HOW TO RESTORE.txt").read_text(encoding="utf-8")
    assert "Kept in the backup, to restore it anywhere" in capsys.readouterr().out
    # a second run: nothing replaced; a new extra of the same name gets (2); our temp goes
    marker = tools / "jxl-photo 9.9.9" / "the owner's note.txt"
    marker.write_text("mine", encoding="utf-8")
    (tools / "leftover.pbtmp").mkdir()
    extra.write_bytes(b"zip two, longer")
    assert backup(env, "--tiff", "jxl") == 0
    assert marker.read_text(encoding="utf-8") == "mine"
    assert (tools / "photo-backup-exe 0.14.0.zip").read_bytes() == b"zip one"
    assert (tools / "photo-backup-exe 0.14.0 (2).zip").read_bytes() == b"zip two, longer"
    assert not (tools / "leftover.pbtmp").exists()
    assert backed(env.dest) == OTHERS | JXL


# ---- J12 refused before anything is read ---------------------------------------------------------
def test_j12_refused_before_reading(env, monkeypatch, capsys):
    assert backup(env, "--tiff", "nope") == 2
    monkeypatch.setattr(sb, "JXLPHOTO", "")
    assert backup(env, "--tiff", "jxl") == 2
    assert "JXLPHOTO is not set" in capsys.readouterr().err
    monkeypatch.setattr(sb, "JXLPHOTO", str(env.root / "jxl_photo.txt"))
    assert backup(env, "--tiff", "jxl") == 2
    monkeypatch.setattr(sb, "JXLPHOTO", str(env.root / "missing" / "jxl_photo.py"))
    assert backup(env, "--tiff", "jxl") == 2
    monkeypatch.setattr(sb, "JXLPHOTO", str(env.tool))
    monkeypatch.setattr(sb, "TIFF_JXL_PROFILES",
                        {"jxl": {"distance": -1, "scan_distance": 0, "eight_bit_distance": 0}})
    assert backup(env, "--tiff", "jxl") == 2
    assert "must be a number, 0 or more" in capsys.readouterr().err
    assert not env.dest.exists()
    monkeypatch.setattr(sb, "TIFF_JXL_PROFILES",
                        {"jxl": {"distance": 0.05, "scan_distance": 0, "eight_bit_distance": 0}})
    monkeypatch.setattr(sb, "JXLPHOTO", "")
    man = write_manifest(env.root / "m.csv", (env.src / "2025", "6", "", "", "jxl"))
    assert mb.main(["--manifest", str(man), "--dest", str(env.dest), "--yes"]) == 2
    assert mir.main(["--manifest", str(man), "--dest", str(env.dest), "--yes"]) == 2
    assert not env.dest.exists()
    # a verify does not need jxl-photo
    capsys.readouterr()
    assert verify(env, "quick") == 2                    # no backup there yet: its own refusal
    assert "JXLPHOTO" not in capsys.readouterr().err


# ---- J13 a photo kept only as JXL is an anchor ---------------------------------------------------
def test_j13_a_jxl_photo_is_kept_by_raw_or_jpeg():
    assert sb._original_kept("IMG_0001.jxl", set(), "raw_or_jpeg", False)
    assert not sb._original_kept("DSC_8.jxl", {"dsc_8"}, "raw_or_jpeg", False)
    assert sb._original_kept("IMG_0001.jxl", set(), "raw_jpeg", False)


def test_j13_the_export_finds_a_jxl_original(tmp_path):
    root = make_tree(tmp_path / "SRC", {"2025/shoot/IMG_0001.jxl": 50,
                                        "2025/shoot/IMG_0002.jxl": 51,
                                        "2025/shoot/_EXPORT/IMG_0001_v1.jpg": 60})
    args = sb._build_parser().parse_args(argv_for(root / "2025", tmp_path / "dest", root,
                                                  "--unselected", "raw_or_jpeg"))
    rels = {item.rel.replace("\\", "/") for item in sb.plan(args).files}
    assert "2025/shoot/IMG_0001.jxl" in rels
    assert "2025/shoot/IMG_0002.jxl" in rels            # not exported, no RAW: its only copy


# ---- J14 Ctrl+C in the middle of the conversions ---------------------------------------------------
def test_j14_ctrl_c_while_converting(env, monkeypatch):
    real = sb._place_group
    placed = []

    def place_then_stop(*args, **kwargs):
        if placed:
            raise KeyboardInterrupt
        placed.append(real(*args, **kwargs))
    monkeypatch.setattr(sb, "_place_group", place_then_stop)
    assert backup(env, "--tiff", "jxl") == 130
    assert no_work_left(env)
    groups = {source for source, *_rest in converted(env.dest).values()}
    assert len(groups) == 1                             # the one placed is whole and recorded
    have = backed(env.dest)
    assert OTHERS <= have and len(have & JXL) == len(converted(env.dest))
    monkeypatch.setattr(sb, "_place_group", real)
    assert backup(env, "--tiff", "jxl") == 0
    assert backed(env.dest) == OTHERS | JXL


# ---- J15 the jxl-photo contract: the minimum version (docs/jxlphoto_contract.md, JP-1) ----------
def fake_version(monkeypatch, version):
    monkeypatch.setenv("FAKE_JXLPHOTO_VERSION", version)
    sb._JXLPHOTO_VERSIONS.clear()


def test_j15_versions_in_order():
    key = sb._version_key
    assert key("2.30.0-rc2") < key("2.30.0") < key("2.30.1") < key("jxl-photo 2.31.0_beta1")
    assert key("2.17.0_beta1") < key("2.17.0") and key("jxl-photo ?") is None
    assert key(sb.JXLPHOTO_MIN_VERSION) is not None


def test_j15_an_old_or_silent_jxlphoto_is_refused(env, monkeypatch, capsys):
    monkeypatch.setattr(sb, "JXLPHOTO_MIN_VERSION", "2.30.0")
    fake_version(monkeypatch, "2.29.1")
    assert backup(env, "--tiff", "jxl") == 2
    assert "jxl-photo 2.29.1 is older than 2.30.0" in capsys.readouterr().err
    fake_version(monkeypatch, "2.30.0_beta1")           # a test version of the minimum
    assert backup(env, "--tiff", "jxl") == 2
    fake_version(monkeypatch, "none")
    assert backup(env, "--tiff", "jxl") == 2
    assert "did not say its version" in capsys.readouterr().err
    man = write_manifest(env.root / "m.csv", (env.src / "2025", "6", "", "", "jxl"))
    assert mb.main(["--manifest", str(man), "--dest", str(env.dest), "--yes"]) == 2
    assert mir.main(["--manifest", str(man), "--dest", str(env.dest), "--yes"]) == 2
    assert not env.dest.exists()
    assert backup(env) == 0                             # without a profile: no jxl-photo needed
    fake_version(monkeypatch, "2.30.0")
    assert backup(env, "--tiff", "jxl") == 0
    assert backed(env.dest) == OTHERS | JXL


def test_j15_the_restore_never_runs_an_old_kept_jxlphoto(env, monkeypatch, capsys):
    monkeypatch.setattr(sb, "KEEP_TOOLS_IN_BACKUP", True)
    assert backup(env, "--tiff", "jxl") == 0
    monkeypatch.setattr(sb, "JXLPHOTO", "")
    fake_version(monkeypatch, "2.29.1")                 # what the kept copy now says
    capsys.readouterr()
    assert restore(env, "--to", env.out) == 1
    out = capsys.readouterr().out
    assert "NEEDS_JXLPHOTO: 3 TIFF or JPEG kept as JXL" in out and "older than" in out
    assert restored(env) == OTHERS


# ---- J27-J35 the conversions outside the backup, jxl-photo 2.31 (v0.14.0-rc4) --------------------
def test_j27_conversions_run_outside_the_backup_with_their_own_logs(env, monkeypatch,
                                                                    photobackup_log_dir):
    envs = env.root / "envs"
    monkeypatch.setenv("FAKE_JXLPHOTO_ENVS", str(envs))
    assert backup(env, "--tiff", "jxl") == 0
    assert backed(env.dest) == OTHERS | JXL
    assert no_work_left(env)
    work_root = Path(sb.JXL_WORK_DIR, sb.JXL_WORK_FOLDER, str(os.getpid()))
    calls = [json.loads(path.read_text(encoding="utf-8")) for path in envs.glob("*.json")]
    encode_calls = [call for call in calls
                    if call["argv"][0] == "encode" and "--version" not in call["argv"]]
    assert len(encode_calls) == 3
    for call in encode_calls:
        folder = Path(call["argv"][2])                  # this conversion's own folder
        assert folder.parent == work_root               # outside the backup
        assert Path(call["JXLPHOTO_LOG_DIR"]) == folder / "log"
        assert call["JXLPHOTO_SETTINGS"] == ""          # jxl-photo's own defaults (JP-21)
    # a conversion that worked leaves no log
    assert not list(Path(photobackup_log_dir, "jxl-photo").rglob("*.log"))


def test_j28_a_page_that_fails_its_check_is_named_and_its_log_kept(env, monkeypatch,
                                                                   photobackup_log_dir):
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", "verify")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "000038")
    assert backup(env, "--tiff", "jxl") == 1
    line = next(line for line in latest_log(photobackup_log_dir).splitlines()
                if "CONVERT FAILED" in line)
    assert "(JP-20)" in line and "round-trip verification FAILED on page 1" in line
    assert "12:00:01" not in line                       # jxl-photo's time is left out
    kept = Path(re.search(r"jxl-photo's log: (.+\.log)", line).group(1))
    assert kept.parent == Path(photobackup_log_dir, "jxl-photo")
    assert "round-trip verification FAILED" in kept.read_text(encoding="utf-8")
    assert [path.name for path in kept.parent.rglob("*.log")] == [kept.name]   # only its log
    have = backed(env.dest)
    assert "2025/scans/000038.tif" in have and not {rel for rel in have if "000038" in rel
                                                     and rel.endswith(".jxl")}
    assert {"2025/shoot/DSC_0001.tif.jxl", "2025/scans/000037.tiff.jxl"} <= have
    assert no_work_left(env)


def test_j29_without_djxl_a_tiff_profile_is_refused_before_reading(env, monkeypatch, capsys):
    monkeypatch.setattr(sb, "_djxl_on_path", lambda: None)
    assert backup(env, "--tiff", "jxl") == 2
    assert "djxl is not on PATH" in capsys.readouterr().err
    man = write_manifest(env.root / "m.csv", (env.src / "2025", "6", "", "", "jxl"))
    assert mb.main(["--manifest", str(man), "--dest", str(env.dest), "--yes"]) == 2
    assert mir.main(["--manifest", str(man), "--dest", str(env.dest), "--yes"]) == 2
    assert "JP-20" in capsys.readouterr().err
    assert not env.dest.exists()
    assert backup(env, "--jpeg", "jxl") == 0            # a JPEG does not need it from jxl-photo


def test_j30_the_work_folder_is_never_in_a_source_or_the_backup(env, monkeypatch, capsys):
    for inside in (env.src / "2025" / "work", env.dest / "work"):
        monkeypatch.setattr(sb, "JXL_WORK_DIR", str(inside))
        assert backup(env, "--tiff", "jxl") == 2
        assert "JXL_WORK_DIR overlaps" in capsys.readouterr().err
        man = write_manifest(env.root / "m.csv", (env.src / "2025", "6", "", "", "jxl"))
        assert mir.main(["--manifest", str(man), "--dest", str(env.dest), "--yes"]) == 2
        assert "JXL_WORK_DIR overlaps" in capsys.readouterr().err
    assert not (env.src / "2025" / "work").exists() and not env.dest.exists()


def test_j31_the_work_folders_of_backups_that_died_are_removed(env):
    """Only a folder named by a process number that no longer runs AND that
    holds a run's mark (rc4 review, finding 1)."""
    parent = Path(sb.JXL_WORK_DIR, sb.JXL_WORK_FOLDER)
    dead, unmarked = parent / "999999", parent / "999998"
    alive, other = parent / str(os.getppid()), parent / "not a number"
    for folder in (dead, unmarked, alive, other):
        (folder / "7").mkdir(parents=True)
        (folder / "7" / "x.jxl").write_bytes(b"left")
    for folder in (dead, alive, other):
        (folder / sb._WORK_MARK).write_text("mark", encoding="utf-8")
    assert not sb._pid_alive(999999) and not sb._pid_alive(999998)
    assert sb._pid_alive(os.getppid()) and sb._pid_alive(os.getpid())
    assert backup(env, "--tiff", "jxl") == 0
    assert not dead.exists()
    assert unmarked.exists() and alive.exists() and other.exists()
    assert no_work_left(env)


def test_j32_the_converters_never_run_far_ahead_of_the_writer(env, monkeypatch):
    make_tree(env.src, {f"2025/many/IMG_{number:03d}.tif": f"TIFF16 IMG_{number:03d} " * 20
                        for number in range(12)})
    monkeypatch.setattr(sb, "JXL_TIFF_WORKERS", 1)
    counts = {"started": 0, "placed": 0, "ahead": 0}
    lock = threading.Lock()
    real_convert, real_place = sb._convert_one, sb._place_group

    def convert(*args, **kwargs):
        with lock:
            counts["started"] += 1
            counts["ahead"] = max(counts["ahead"], counts["started"] - counts["placed"])
        return real_convert(*args, **kwargs)

    def place(*args, **kwargs):
        time.sleep(0.05)                                # a slow backup disk
        result = real_place(*args, **kwargs)
        with lock:
            counts["placed"] += 1
        return result
    monkeypatch.setattr(sb, "_convert_one", convert)
    monkeypatch.setattr(sb, "_place_group", place)
    assert backup(env, "--tiff", "jxl") == 0
    assert counts["placed"] == 15
    assert counts["ahead"] <= 2                         # twice the TIFF pool (1)


def test_j33_a_group_that_cannot_be_written_whole_leaves_nothing(env, monkeypatch,
                                                                 photobackup_log_dir):
    if env.mode != "copy":
        pytest.skip("the copy to the backup's drive")
    real = sb._write_jxl_temp

    def fail_on_page_1(path, target, *args):
        if target.endswith("000038_page1.tif.jxl"):
            raise OSError("the disk is full (test)")
        return real(path, target, *args)
    monkeypatch.setattr(sb, "_write_jxl_temp", fail_on_page_1)
    assert backup(env, "--tiff", "jxl") == 1
    assert "the JXL could not be put in place" in latest_log(photobackup_log_dir)
    have = backed(env.dest)
    assert "2025/scans/000038.tif" in have                  # copied as it is
    assert not {rel for rel in have if "000038" in rel
                and rel.endswith((".jxl", sb.TEMP_SUFFIX))}
    assert {"2025/shoot/DSC_0001.tif.jxl", "2025/scans/000037.tiff.jxl"} <= have
    assert no_work_left(env)


def test_j34_what_jxlphoto_said():
    run = SimpleNamespace(stdout=(
        "12:00:00 | INFO | Round-trip verification of every page written\n"
        "12:00:01 | ERROR | [1/1] ERROR | a.tif | round-trip verification FAILED on page 0: "
        "lossless encode did not decode back pixel-identical\n"
        "--------------------------------------------------\n"
        "12:00:01 | INFO | Done: 0 OK | 0 overwrites | 0 skipped | 1 errors\n"), stderr="")
    assert sb._jxlphoto_said(run) == ("[1/1] ERROR | a.tif | round-trip verification FAILED "
                                      "on page 0: lossless encode did not decode back "
                                      "pixel-identical")
    assert sb._jxlphoto_said(SimpleNamespace(stdout="", stderr="ERROR: EncodeImageJXL() failed\n")
                             ) == "EncodeImageJXL() failed"
    assert sb._jxlphoto_said(SimpleNamespace(stdout="a\nb\nc\nd\ne\n", stderr=None)
                             ) == "b | c | d | e"


def test_j35_the_minimum_is_a_jxlphoto_that_checks_every_page():
    key = sb._version_key
    assert key("jxl-photo 2.31.0-rc3") >= key(sb.JXLPHOTO_MIN_VERSION)
    assert key("jxl-photo 2.31.0-rc1") < key(sb.JXLPHOTO_MIN_VERSION)
    assert key("jxl-photo 2.31.0") >= key(sb.JXLPHOTO_MIN_VERSION)
    assert key("jxl-photo 2.30.2") < key(sb.JXLPHOTO_MIN_VERSION)
    assert key("jxl-photo 2.30.2") >= key(sb.JXLPHOTO_RESTORE_MIN_VERSION)


REAL_COPY_NEEDED = sb._jxl_copy_needed         # conftest puts a stand-in for every test
REAL_DJXL_ON_PATH = sb._djxl_on_path


# ---- J37-J43 the findings of the rc4 review ------------------------------------------------------
def test_j37_a_work_folder_name_that_is_not_one_name_is_refused(env, monkeypatch, capsys):
    for name in ("", " ", "..", ".", "a\\b", "a/b", "C:"):
        monkeypatch.setattr(sb, "JXL_WORK_FOLDER", name)
        assert backup(env, "--tiff", "jxl") == 2, name
        assert "JXL_WORK_FOLDER must be one plain folder name" in capsys.readouterr().err
    assert not env.dest.exists()
    monkeypatch.setattr(sb, "JXL_WORK_FOLDER", "")
    with pytest.raises(OSError):
        sb._work_root()                         # never a sweep of JXL_WORK_DIR itself


def test_j38_the_default_work_folder_is_checked_too(env, monkeypatch, capsys):
    monkeypatch.setattr(sb, "JXL_WORK_DIR", "")
    monkeypatch.setattr(sb.tempfile, "gettempdir", lambda: str(env.src / "2025" / "Temp"))
    (env.src / "2025" / "Temp").mkdir()
    assert backup(env, "--tiff", "jxl") == 2
    err = capsys.readouterr().err
    assert "JXL_WORK_DIR overlaps a source" in err and "system's temp folder" in err
    monkeypatch.setattr(sb, "JXL_WORK_DIR", str(env.root / "missing"))
    assert backup(env, "--tiff", "jxl") == 2
    assert "JXL_WORK_DIR does not exist" in capsys.readouterr().err
    assert not env.dest.exists()


def test_j39_a_work_folder_that_cannot_be_made_copies_every_file(env, monkeypatch,
                                                                photobackup_log_dir):
    def cannot(work_dir=None):
        raise OSError("the disk of the work folder is gone (test)")
    monkeypatch.setattr(sb, "_work_root", cannot)
    assert backup(env, "--tiff", "jxl") == 1                # no traceback, nothing left out
    assert backed(env.dest) == OTHERS | TIFFS
    assert "work folder cannot be made" in latest_log(photobackup_log_dir)


def test_j40_the_work_folder_of_rc2_and_rc3_leaves_the_backup(env):
    old = Path(env.dest, sb.CATALOG_FOLDER, "converting", "261119_101010_3")
    old.mkdir(parents=True)
    (old / "half.jxl").write_bytes(b"an interrupted run's")
    assert backup(env, "--tiff", "jxl") == 0
    assert not old.parent.exists()
    assert backed(env.dest) == OTHERS | JXL


def test_j41_a_name_that_fails_to_change_is_set_right_next_run(env, monkeypatch,
                                                               photobackup_log_dir):
    real, calls = sb._rename_no_clobber, []

    def fail_on_page_2(src, dst):
        calls.append(dst)
        if str(dst).endswith("000038_page2.tif.jxl"):
            raise PermissionError("held open by a viewer (test)")
        return real(src, dst)
    monkeypatch.setattr(sb, "_rename_no_clobber", fail_on_page_2)
    assert backup(env, "--tiff", "jxl") == 1
    assert "the JXL could not be put in place" in latest_log(photobackup_log_dir)
    assert "2025/scans/000038.tif" in backed(env.dest)        # copied as it is
    assert not {rel for rel in backed(env.dest) if rel.endswith(sb.TEMP_SUFFIX)}
    monkeypatch.setattr(sb, "_rename_no_clobber", real)
    assert backup(env, "--tiff", "jxl") == 0
    assert backed(env.dest) == OTHERS | JXL
    assert "2025/scans/000038.tif" in under(env.dest, VERSIONS)   # nothing lost


def test_j42_the_route_follows_the_volume_not_the_letter(tmp_path, monkeypatch):
    one, two = tmp_path / "work_here", tmp_path / "dest"
    one.mkdir()
    two.mkdir()
    assert REAL_COPY_NEEDED(str(one), str(two)) is False      # one volume: a rename
    real_stat = os.stat

    def mounted(path, *args, **kwargs):                       # a disk mounted in a folder
        result = real_stat(path, *args, **kwargs)
        if str(path).endswith("dest"):
            return os.stat_result((result.st_mode, result.st_ino, result.st_dev + 1)
                                  + tuple(result)[3:])
        return result
    monkeypatch.setattr(sb.os, "stat", mounted)
    assert REAL_COPY_NEEDED(str(one), str(two)) is True


def test_j43_djxl_is_looked_for_on_path(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert REAL_DJXL_ON_PATH() is None
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / ("djxl.exe" if os.name == "nt" else "djxl")).write_bytes(b"")
    if os.name != "nt":
        os.chmod(tools / "djxl", 0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(empty), str(tools)]))
    assert REAL_DJXL_ON_PATH() is not None


# ---- J44-J45 where the conversions run (v0.14.0-rc5) ---------------------------------------------
def env_calls(envs, command):
    calls = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(envs.glob("*.json"))]
    return [call for call in calls if call["argv"][0] == command and "--version" not in call["argv"]]


def test_j44_jxlphotos_own_temp_files_stay_in_the_conversions_folder(env, monkeypatch):
    """TMPDIR, TEMP and TMP of each jxl-photo call are its conversion's own
    folder: nothing of a conversion goes to the system's temp folder (the
    owner's SSD), and what jxl-photo leaves there leaves with the folder."""
    envs = env.root / "envs"
    monkeypatch.setenv("FAKE_JXLPHOTO_ENVS", str(envs))
    monkeypatch.setenv("FAKE_JXLPHOTO_LEAK_TEMP", "1")      # a temp file it never removes
    assert backup(env, "--tiff", "jxl") == 0
    assert backed(env.dest) == OTHERS | JXL
    work_root = Path(sb.JXL_WORK_DIR, sb.JXL_WORK_FOLDER, str(os.getpid()))
    calls = env_calls(envs, "encode")
    assert len(calls) == 3
    for call in calls:
        temp = Path(call["argv"][2]) / "temp"
        assert temp.parent.parent == work_root
        assert [Path(call[name]) for name in ("TMPDIR", "TEMP", "TMP")] == [temp] * 3
        assert Path(call["gettempdir"]) == temp             # what jxl-photo's tempfile uses
    assert no_work_left(env)
    monkeypatch.setenv("FAKE_JXLPHOTO_ENVS", str(envs / "restore"))   # the restore's own folder
    plain = env.root.parent / "restored_plain"          # no '%' in its path (JP-22: tests/test_rc6.py)
    assert restore(env, "--to", plain) == 0
    for call in env_calls(envs / "restore", "decode"):
        work = Path(call["argv"][1]).parent             # <restore folder>\jxl -> the folder
        assert Path(call["gettempdir"]) == work / "temp"
        assert not work.exists()
    assert not [path for path in plain.rglob("*") if "fake_jxlphoto_" in path.name]


def test_j45_work_dir_says_where_the_conversions_run(env, monkeypatch, capsys):
    """--work-dir (what the menu remembers on a computer) for one run, checked
    like JXL_WORK_DIR, and handed on by the manifest and the mirror."""
    elsewhere = env.root.parent / "E_TEMP"
    elsewhere.mkdir()
    envs = env.root / "envs"
    monkeypatch.setenv("FAKE_JXLPHOTO_ENVS", str(envs))
    assert backup(env, "--tiff", "jxl", "--work-dir", str(elsewhere)) == 0
    here = elsewhere / sb.JXL_WORK_FOLDER / str(os.getpid())
    assert {Path(call["argv"][2]).parent for call in env_calls(envs, "encode")} == {here}
    assert not here.exists() and backed(env.dest) == OTHERS | JXL
    assert not Path(sb.JXL_WORK_DIR, sb.JXL_WORK_FOLDER, str(os.getpid())).exists()
    capsys.readouterr()
    missing, inside = env.root / "missing", env.src / "2025"
    assert backup(env, "--tiff", "jxl", "--work-dir", str(missing)) == 2
    assert "--work-dir does not exist" in capsys.readouterr().err
    assert backup(env, "--tiff", "jxl", "--work-dir", str(inside)) == 2
    assert "--work-dir overlaps a source" in capsys.readouterr().err
    man = write_manifest(env.root / "m.csv", (env.src / "2025", "6", "", "", "jxl"))
    dest2 = env.root / "dest2"
    for main in (mb.main, mir.main):
        assert main(["--manifest", str(man), "--dest", str(dest2), "--yes",
                     "--work-dir", str(missing)]) == 2
        assert "--work-dir does not exist" in capsys.readouterr().err
    assert not dest2.exists()
    shutil.rmtree(envs)
    assert mb.main(["--manifest", str(man), "--dest", str(dest2), "--yes",
                    "--work-dir", str(elsewhere)]) == 0
    assert {Path(call["argv"][2]).parent for call in env_calls(envs, "encode")} == {here}
    assert not here.exists()


# ---- J46-J49 Ctrl+C stops in seconds, and says so (v0.14.0-rc5) -----------------------------------
def test_j46_ctrl_c_ends_the_conversions_at_once_and_says_so(env, monkeypatch, capsys):
    """The owner (20/11): a stop must never look like a hang. Ctrl+C while a
    TIFF takes 60 s: the run says it stops, ends that conversion instead of
    waiting for it, keeps what was placed (catalog written) and says last that
    the disk can be removed; the next run goes on."""
    pids = env.root / "pids"
    monkeypatch.setattr(sb, "JXL_TIFF_WORKERS", 2)     # 000038 sleeps while the others go on
    monkeypatch.setenv("FAKE_JXLPHOTO_SLEEP", "60")
    monkeypatch.setenv("FAKE_JXLPHOTO_SLEEP_ONLY", "000038")
    monkeypatch.setenv("FAKE_JXLPHOTO_SLEEP_PIDS", str(pids))
    real_wait, real_place = sb._wait_futures, sb._place_group
    placed, state = [], {"pressed": False}

    def place(*args, **kwargs):
        result = real_place(*args, **kwargs)
        placed.append(args[0].planned.rel)
        return result

    def wait(fs, *args, **kwargs):
        if not state["pressed"] and len(placed) == 2 and list(pids.glob("*.pid")):
            state["pressed"] = True
            signal.raise_signal(signal.SIGINT)          # Ctrl+C while 000038 converts
        return real_wait(fs, *args, **kwargs)
    monkeypatch.setattr(sb, "_place_group", place)
    monkeypatch.setattr(sb, "_wait_futures", wait)
    started = time.monotonic()
    assert backup(env, "--tiff", "jxl") == 130
    assert time.monotonic() - started < 30              # not the 60 s of the conversion
    out = capsys.readouterr().out
    assert sb.STOP_SAYING in out and out.rstrip().endswith(sb.STOPPED_SAYING)
    assert out.index(sb.STOP_SAYING) < out.index(sb.STOPPED_SAYING)
    assert list(pids.glob("*.pid"))
    for pid in pids.glob("*.pid"):
        assert not sb._pid_alive(int(pid.stem))         # ended, not waited for
    have = backed(env.dest)
    assert not {rel for rel in have if "000038" in rel}  # neither a JXL nor a copy as it is
    assert {"2025/shoot/DSC_0001.tif.jxl", "2025/scans/000037.tiff.jxl"} <= have
    assert {rel.casefold() for rel in have if rel.endswith(".jxl")} == set(converted(env.dest))
    assert no_work_left(env)
    for name in ("FAKE_JXLPHOTO_SLEEP", "FAKE_JXLPHOTO_SLEEP_ONLY"):
        monkeypatch.delenv(name)
    assert backup(env, "--tiff", "jxl") == 0            # the next run goes on from there
    assert backed(env.dest) == OTHERS | JXL
    assert not under(env.dest, VERSIONS)


def test_j47_ctrl_c_waits_for_a_groups_names_and_rows(env, monkeypatch, capsys):
    """Ctrl+C right after a JXL took its name: the group's other names and its
    catalog rows are written first, so no JXL is left without its row (before
    rc5 the next run set such a group aside to versions\\ and made it again)."""
    real = sb._rename_no_clobber
    state = {"pressed": False}

    def rename(source, target):
        real(source, target)
        if not state["pressed"] and str(target).endswith(".jxl"):
            state["pressed"] = True
            signal.raise_signal(signal.SIGINT)
    monkeypatch.setattr(sb, "_rename_no_clobber", rename)
    assert backup(env, "--tiff", "jxl") == 130
    assert capsys.readouterr().out.rstrip().endswith(sb.STOPPED_SAYING)
    jxl_files = {rel.casefold() for rel in backed(env.dest) if rel.endswith(".jxl")}
    assert jxl_files and jxl_files == set(converted(env.dest))
    assert no_work_left(env)
    monkeypatch.setattr(sb, "_rename_no_clobber", real)
    assert backup(env, "--tiff", "jxl") == 0
    assert backed(env.dest) == OTHERS | JXL
    assert not under(env.dest, VERSIONS)                # nothing had to be set aside


def test_j48_a_work_folder_that_cannot_go_yet_keeps_its_mark(tmp_path):
    """A file still held (a converter ended by Ctrl+C) keeps the run's mark,
    so a later run can still sweep the folder: never a folder left unmarked."""
    root = tmp_path / "pbconv" / "424242"
    (root / "7" / "temp").mkdir(parents=True)
    (root / sb._WORK_MARK).write_text("mark", encoding="utf-8")
    held = root / "7" / "page.jxl"
    held.write_bytes(b"x")
    with open(held, "rb"):
        sb._remove_work_root(str(root))
        if os.name == "nt":                             # Windows keeps an open file
            assert held.exists() and sb._ours_to_sweep(str(root))
    sb._remove_work_root(str(root))
    assert not root.exists()


def test_j49_ctrl_c_in_a_restore_ends_what_is_being_made(env, monkeypatch, capsys):
    assert backup(env, "--tiff", "jxl") == 0
    pids = env.root / "pids"
    monkeypatch.setenv("FAKE_JXLPHOTO_SLEEP", "60")
    monkeypatch.setenv("FAKE_JXLPHOTO_SLEEP_PIDS", str(pids))
    real = rb.as_completed

    def pressed(futures, *args, **kwargs):
        deadline = time.monotonic() + 20                # until a TIFF is being made
        while time.monotonic() < deadline and not list(pids.glob("*.pid")):
            time.sleep(0.1)
        signal.raise_signal(signal.SIGINT)
        return real(futures, *args, **kwargs)
    monkeypatch.setattr(rb, "as_completed", pressed)
    started = time.monotonic()
    assert restore(env, "--to", env.out) == 130
    assert time.monotonic() - started < 30
    out = capsys.readouterr().out
    assert sb.STOP_SAYING in out and "Stopped (Ctrl+C)" in out
    assert list(pids.glob("*.pid"))
    for pid in pids.glob("*.pid"):
        assert not sb._pid_alive(int(pid.stem))
    assert restored(env) == OTHERS                      # the plain files; no TIFF half made
    assert not [path for path in env.out.rglob("*") if ".restoring_" in path.name]


def test_j36_the_restore_runs_a_kept_jxlphoto_older_than_a_backup_needs(env, monkeypatch,
                                                                        photobackup_log_dir):
    monkeypatch.setattr(sb, "KEEP_TOOLS_IN_BACKUP", True)
    assert backup(env, "--tiff", "jxl") == 0
    monkeypatch.setattr(sb, "JXLPHOTO", "")
    monkeypatch.setattr(sb, "JXLPHOTO_MIN_VERSION", "99.0.0")   # a backup now needs a newer one
    sb._JXLPHOTO_VERSIONS.clear()
    assert restore(env, "--to", env.out) == 0
    assert restored(env) == OTHERS | TIFFS


# ---- J50 the conversion off for a run: --no-convert (v0.14.0-rc5) ----------------------------------
def test_j50_no_convert_copies_tiffs_as_they_are_and_keeps_the_jxl(env, monkeypatch, capsys):
    """A trip with a slow computer: no jxl-photo, no djxl needed; a new or
    changed TIFF is copied as it is (the outdated JXL to versions, after the
    copy), a JXL of an unchanged TIFF stays even when the profile changed; the
    next run with it changes nothing; a run without it converts them."""
    assert backup(env, "--tiff", "jxl") == 0
    assert len(encodes(env)) == 3
    new_tiff = env.src / "2025/scans/000039.tif"
    new_tiff.write_text("SCAN 000039 " * 40, encoding="utf-8")
    tiff = env.src / "2025/scans/000037.tiff"
    tiff.write_text(SCAN_B + "edited", encoding="utf-8")
    stat = tiff.stat()
    os.utime(tiff, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    monkeypatch.setattr(sb, "TIFF_JXL_PROFILES",
                        {"jxl": {"distance": 0.1, "scan_distance": 0, "eight_bit_distance": 0}})
    monkeypatch.setattr(sb, "JXLPHOTO", str(env.root / "no-jxl-photo.py"))
    monkeypatch.setattr(sb, "_djxl_on_path", lambda: None)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--update-changed", "--no-convert") == 0
    out = capsys.readouterr().out
    assert len(encodes(env)) == 3                       # nothing converted
    assert row_count(out, "TIFF: copied as they are (conversion off)") == "2"
    assert row_count(out, "TIFF: already in the backup (JXL or not)") == "2"
    assert "Conversion off: 2 TIFF/JPEG kept as they are" in out
    kept = JXL - {"2025/scans/000037.tiff.jxl"}
    assert backed(env.dest) == OTHERS | kept | {"2025/scans/000037.tiff", "2025/scans/000039.tif"}
    assert "2025/scans/000037.tiff.jxl" in under(env.dest, VERSIONS)
    assert (env.dest / "2025/scans/000037.tiff").read_bytes() == tiff.read_bytes()
    assert {settings for *_rest, settings in converted(env.dest).values()} == {SETTINGS}
    assert no_work_left(env)
    # again with it: everything is there, nothing moves
    assert backup(env, "--tiff", "jxl", "--update-changed", "--no-convert") == 0
    out = capsys.readouterr().out
    assert row_count(out, "TIFF: copied as they are (conversion off)") == "0"
    assert row_count(out, "TIFF: already in the backup (JXL or not)") == "4"
    assert under(env.dest, VERSIONS) == {"2025/scans/000037.tiff.jxl"}
    # back home, without it: what was copied as it is becomes JXL, and the new
    # profile makes the others again
    monkeypatch.setattr(sb, "JXLPHOTO", str(env.tool))
    monkeypatch.setattr(sb, "_djxl_on_path", lambda: "djxl")
    assert backup(env, "--tiff", "jxl", "--update-changed") == 0
    assert len(encodes(env)) == 3 + 4
    assert backed(env.dest) == OTHERS | JXL | {"2025/scans/000039.tif.jxl"}
    assert {"2025/scans/000037.tiff", "2025/scans/000039.tif"} <= under(env.dest, VERSIONS)
    # the manifest and the mirror take it too, without jxl-photo
    monkeypatch.setattr(sb, "JXLPHOTO", str(env.root / "no-jxl-photo.py"))
    monkeypatch.setattr(sb, "_djxl_on_path", lambda: None)
    man = write_manifest(env.root / "m.csv", (env.src / "2025", "6", "", "", "jxl"))
    for main, dest in ((mb.main, env.root / "dest2"), (mir.main, env.root / "dest3")):
        assert main(["--manifest", str(man), "--dest", str(dest), "--yes",
                     "--jxlphoto", str(env.root / "no-jxl-photo.py"), "--no-convert"]) == 0
        files = backed(dest)
        assert not {name for name in files if name.endswith(".jxl")}
        assert {name.rsplit("/", 1)[-1] for name in files} >= {
            "DSC_0001.tif", "000037.tiff", "000038.tif", "000039.tif"}
    assert len(encodes(env)) == 7
