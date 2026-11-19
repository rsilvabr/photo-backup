"""TIFFs kept as JXL in the backup (v0.14.0-rc2): jxl-photo converts them, the
catalog proves where each JXL came from, the restore makes the TIFF again.
jxl-photo is played by tests/fake_jxlphoto.py (J1-J14 of the plan)."""
import csv
import io
import json
import os
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import manifest_backup as mb
import mirror_backup as mir
import restore_backup as rb
import selective_backup as sb

from conftest import argv_for, latest_csv, latest_log, make_tree, snapshot

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


@pytest.fixture
def env(tmp_path, monkeypatch):
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
                 "FAKE_JXLPHOTO_VERSION"):
        monkeypatch.delenv(name, raising=False)
    sb._JXLPHOTO_VERSIONS.clear()
    return SimpleNamespace(root=root, src=src, dest=root / "dest", tool=tool, calls=calls,
                           out=root / "restored")


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
    assert "TIFF kept as JXL (jxl: d=0.05 scan=0 8bit=0 e=7): 3 to convert" in out
    assert "Kept as JXL: 3 converted (3 TIFF)" in out
    assert not Path(env.dest, sb.CATALOG_FOLDER, sb.JXL_WORK_FOLDER).exists()


# ---- J3 a second run, a changed TIFF, another profile --------------------------------------
def test_j3_rerun_changed_tiff_and_profile(env, monkeypatch, capsys, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl") == 0
    out = capsys.readouterr().out
    assert "0 to convert" in out and "3 up to date" in out
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
    assert "copies in the backup that their JXL replaces: 3 (to versions\\)" in out
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
    assert not Path(env.dest, sb.CATALOG_FOLDER, sb.JXL_WORK_FOLDER).exists()
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
    assert not Path(env.dest, sb.CATALOG_FOLDER, sb.JXL_WORK_FOLDER).exists()
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
