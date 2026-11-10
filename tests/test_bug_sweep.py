"""Round 1 of the 261110 bug sweep (AI_tools/261110_Fable_review_bug-sweep.md).

Each test here failed against v0.12.0 (the report has the run); the ones marked
"guard" pass there too and only keep a fix from copying less than before."""
import csv
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import manifest_backup as mb
import selective_backup as sb

from conftest import argv_for, latest_csv, latest_log, make_tree

SHIPPED_MODES = Path(mb.__file__).resolve().parent / mb.MODES_FILE
HOSTILE = "吾妻山公園 [FINAL] 5% (x)"

# (files counter, bytes counter) of everything a plan leaves out on purpose
COUNTERS = (
    ("export_filtered", "export_filtered_bytes"),
    ("export_excluded", "export_excluded_bytes"),
    ("unselected_skipped", "unselected_skipped_bytes"),
    ("inside_shoot_skipped", "inside_shoot_skipped_bytes"),
    ("c1_cache_skipped", "c1_cache_skipped_bytes"),
    ("derivatives_skipped", "derivatives_skipped_bytes"),
    ("manifest_skip_files", "manifest_skip_bytes"),
    ("only_filtered", "only_filtered_bytes"),
    ("add_filtered_skipped", "add_filtered_skipped_bytes"),
    ("mode_excluded", "mode_excluded_bytes"),
)


def sweep_tree(root):
    p = f"2025/{HOSTILE}"
    make_tree(root, {
        # a session: export, unselected originals, outtakes
        f"{p}/250101_Shoot/_DSC1.NEF": 10,
        f"{p}/250101_Shoot/_DSC1.JPG": 11,
        f"{p}/250101_Shoot/_DSC1.xmp": 12,
        f"{p}/250101_Shoot/_DSC2.NEF": 13,
        f"{p}/250101_Shoot/_DSC2.JPG": 14,
        f"{p}/250101_Shoot/IMG_5.JPG": 15,
        f"{p}/250101_Shoot/scan.tif": 16,
        f"{p}/250101_Shoot/notes.txt": 17,
        f"{p}/250101_Shoot/_EXPORT/16B_JXL/_DSC1_v1.jxl": 20,
        f"{p}/250101_Shoot/_EXPORT/16B_JXL/_DSC1_v1.tif": 21,
        f"{p}/250101_Shoot/_EXPORT/WEB/_DSC1_v1.jpg": 22,
        f"{p}/250101_Shoot/CaptureOne/Settings166/_DSC1.NEF.cos": 23,
        f"{p}/250101_Shoot/CaptureOne/Cache/Proxies/_DSC1.cop": 24,
        f"{p}/250101_Shoot/NKSC_PARAM/_DSC1.NEF.nksc": 25,
        # _OUTTAKES with an export of its own: a session inside the outtakes
        f"{p}/250101_Shoot/_OUTTAKES/DSC_0002.NEF": 30,
        f"{p}/250101_Shoot/_OUTTAKES/DSC_0003.NEF": 31,
        f"{p}/250101_Shoot/_OUTTAKES/DSC_0003.JPG": 32,
        f"{p}/250101_Shoot/_OUTTAKES/clip.mp4": 33,
        f"{p}/250101_Shoot/_OUTTAKES/_EXPORT/DSC_0002_v1.jxl": 34,
        f"{p}/250101_Shoot/_OUTTAKES/CaptureOne/Settings166/DSC_0002.NEF.cos": 35,
        f"{p}/250101_Shoot/_OUTTAKES/old/x.psd": 36,
        # _OUTTAKES_KEEP with TIFF, HEIC, PSD and video
        f"{p}/250101_Shoot/_OUTTAKES_KEEP/DSC_0011.NEF": 40,
        f"{p}/250101_Shoot/_OUTTAKES_KEEP/DSC_0011.JPG": 41,
        f"{p}/250101_Shoot/_OUTTAKES_KEEP/DSC_0011_pano.tif": 42,
        f"{p}/250101_Shoot/_OUTTAKES_KEEP/IMG_0001.HEIC": 43,
        f"{p}/250101_Shoot/_OUTTAKES_KEEP/edit.psd": 44,
        f"{p}/250101_Shoot/_OUTTAKES_KEEP/clip.mov": 45,
        f"{p}/250101_Shoot/_OUTTAKES_KEEP/IMG_0002.JPG": 46,
        f"{p}/250101_Shoot/_OUTTAKES_KEEP/sub/DSC_0012.NEF": 47,
        f"{p}/250101_Shoot/_OUTTAKES_KEEP/sub/DSC_0012.JPG": 48,
        f"{p}/250101_Shoot/_OUTTAKES_DROP/DSC_0020.NEF": 50,
        f"{p}/250101_Shoot/day2/CaptureOne/Settings166/a.cos": 51,
        f"{p}/250101_Shoot/day2/DSC_0030.NEF": 52,
        f"{p}/250101_Shoot/RAW SCANS/000001.tif": 53,
        # a film session: the originals are only in RAW SCANS
        f"{p}/190101_Film/RAW SCANS/000037.tif": 60,
        f"{p}/190101_Film/_EXPORT/000037_v1.jxl": 61,
        f"{p}/190101_Film/_OUTTAKES_KEEP/000099.tif": 62,
        # not exported yet
        f"{p}/250202_NotEdited/DSC_0100.NEF": 70,
        f"{p}/250202_NotEdited/DSC_0100.JPG": 71,
        f"{p}/250202_NotEdited/clip.mp4": 72,
        f"{p}/250202_NotEdited/CaptureOne/Settings166/DSC_0100.NEF.cos": 73,
        f"{p}/250202_NotEdited/CaptureOne/Cache/p.cop": 74,
        f"{p}/250202_NotEdited/_OUTTAKES/DSC_0101.NEF": 75,
        # only subfolders, a CaptureOne and a NKSC_PARAM
        f"{p}/250303_Trip/CaptureOne/Settings166/x.cos": 80,
        f"{p}/250303_Trip/NKSC_PARAM/y.nksc": 81,
        f"{p}/250303_Trip/cam1/DSC_0200.NEF": 82,
        # an old folder holding a session
        f"{p}/OLD_2019/190101_Trip/DSC_0500.NEF": 90,
        f"{p}/OLD_2019/190101_Trip/_EXPORT/DSC_0500_v1.jxl": 91,
        f"{p}/OLD_2019/readme.txt": 92,
    })
    return root / "2025" / HOSTILE


def backend_args(rows, dest, *extra):
    parsed = mb.build_parser().parse_args(["--manifest", "unused.csv", "--dest", str(dest),
                                           *extra])
    return mb._backend_args(parsed, rows, str(dest))


def plan_rows(rows_spec, tmp_path, modes_path=SHIPPED_MODES):
    modes = mb.read_modes(str(modes_path))
    rows = [mb.ManifestRow(os.path.abspath(str(folder)), mode, originals or mb.DEFAULT_ORIGINALS)
            for folder, mode, originals in rows_spec]
    return mb._plan_rows(rows, modes, backend_args(rows, tmp_path / "out"), {})


def planned_set(plan):
    return {os.path.normcase(item.src) for item in plan.files}


def listed(root):
    """Every file under `root`, links and junctions not followed (as the planner)."""
    out = {}
    stack = [str(root)]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for entry in entries:
                if entry.is_symlink() or entry.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    out[os.path.normcase(entry.path)] = entry.stat().st_size
    return out


def left_out(plan, unexported_copied):
    files = sum(getattr(plan.stats, name, 0) for name, _ in COUNTERS)
    nbytes = sum(getattr(plan.stats, name, 0) for _, name in COUNTERS)
    if not unexported_copied:
        for folder in plan.unexported_folders:
            files += folder.files + getattr(folder, "c1_files", 0) + getattr(folder, "nx_files", 0)
            nbytes += folder.nbytes + folder.c1_nbytes + folder.nx_nbytes
    return files, nbytes


CHAINS = [("1", "2"), ("2", "3"), ("3", "4"), ("4", "5"), ("5", "6"),
          ("1+", "2+"), ("2+", "3+"), ("3+", "4+"), ("4+", "5+"), ("5+", "6"),
          ("1", "1+"), ("2", "2+"), ("3", "3+"), ("4", "4+"), ("5", "5+")]


@pytest.mark.parametrize("originals", ["raw", "raw+jpeg", "all"])
def test_b1_a_bigger_mode_never_copies_less(tmp_path, originals):
    folder = sweep_tree(tmp_path / "G")
    plans = {name: planned_set(plan_rows([(folder, name, originals)], tmp_path))
             for name in ("1", "2", "3", "4", "5", "6", "1+", "2+", "3+", "4+", "5+")}
    for small, big in CHAINS:
        missing = sorted(plans[small] - plans[big])
        assert not missing, f"mode {small} copies what mode {big} does not: {missing}"


@pytest.mark.parametrize("originals", ["raw", "raw+jpeg", "all"])
@pytest.mark.parametrize("mode", ["1", "2", "3", "4", "5", "6", "1+", "3+", "5+", "skip",
                                  "jpeg-only", "raw-only"])
def test_b1_every_listed_file_is_planned_or_counted_once(tmp_path, mode, originals):
    folder = sweep_tree(tmp_path / "G")
    plan = plan_rows([(folder, mode, originals)], tmp_path)
    modes = mb.read_modes(str(SHIPPED_MODES))
    files = listed(folder)
    assert len(planned_set(plan)) == len(plan.files)
    assert planned_set(plan) <= set(files)
    counted_files, counted_bytes = left_out(plan, modes[mode].unexported)
    assert len(files) == len(plan.files) + counted_files
    assert sum(files.values()) == sum(item.size for item in plan.files) + counted_bytes


def test_b1_nested_lines_keep_the_count(tmp_path):
    folder = sweep_tree(tmp_path / "G")
    plan = plan_rows([(folder, "3+", "raw"),
                      (folder / "250202_NotEdited", "jpeg-only", ""),
                      (folder / "OLD_2019", "skip", ""),
                      (folder / "250101_Shoot" / "_OUTTAKES_KEEP", "4+", "raw")], tmp_path)
    files = listed(folder)
    # 3+ copies its folders without an export: nothing of them is counted
    counted_files, counted_bytes = left_out(plan, True)
    assert len(files) == len(plan.files) + counted_files
    assert sum(files.values()) == sum(item.size for item in plan.files) + counted_bytes


def test_b1_outtakes_export_is_a_session_inside_add_folders(tmp_path):
    folder = sweep_tree(tmp_path / "G")
    jxl = os.path.normcase(str(folder / "250101_Shoot" / "_OUTTAKES" / "_EXPORT" / "DSC_0002_v1.jxl"))
    assert jxl in planned_set(plan_rows([(folder, "3", "raw")], tmp_path))
    assert jxl in planned_set(plan_rows([(folder, "4", "raw")], tmp_path))


def test_b1_summary_counts_what_add_folders_left_out(tmp_path, capsys):
    folder = sweep_tree(tmp_path / "G")
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{folder},3,raw\n", encoding="utf-8")
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(tmp_path / "out"), "--dry-run"]) == 0
    out = capsys.readouterr().out
    line = [text for text in out.splitlines() if "left out inside added folders" in text]
    assert line, out
    # DSC_0011.JPG, DSC_0011_pano.tif, clip.mov and sub/DSC_0012.JPG (round 2: IMG_0001.HEIC,
    # edit.psd and the film session's 000099.tif have no RAW beside them and are copied)
    assert "(Originals filter): 4 /" in line[0]


def export_in_added_tree(root):
    p = "2025/shoot"
    make_tree(root, {
        f"{p}/_DSC1.NEF": 10,
        f"{p}/_EXPORT/_DSC1_v1.jxl": 11,
        f"{p}/_OUTTAKES_KEEP/DSC_0011.NEF": 20,
        f"{p}/_OUTTAKES_KEEP/_EXPORT/DSC_0011_v1.jxl": 21,
        f"{p}/_OUTTAKES_KEEP/_EXPORT/DSC_0011_WEB.jpg": 22,
        f"{p}/_OUTTAKES_KEEP/_EXPORT/DSC_0011_v1.xmp": 23,
        f"{p}/_OUTTAKES_KEEP/_EXPORT/DSC_0011-NR.dng": 24,
        f"{p}/_OUTTAKES_KEEP/_EXPORT/notes.txt": 25,
        f"{p}/_OUTTAKES_KEEP/_EXPORT/CaptureOne/Settings166/e.cos": 26,
        f"{p}/_OUTTAKES_KEEP/_EXPORT/CaptureOne/Cache/c.cop": 27,
        f"{p}/_OUTTAKES_KEEP/_EXPORT/NKSC_PARAM/e.nksc": 28,
        f"{p}/_OUTTAKES_KEEP/_EXPORT/WEB/DSC_0011_small.jpg": 29,
        f"{p}/_OUTTAKES_KEEP/sub/DSC_0021.NEF": 30,
        f"{p}/_OUTTAKES_KEEP/sub/_EXPORT/DSC_0021_v1.jpg": 31,
        f"{p}/_OUTTAKES_KEEP/sub/_EXPORT/DSC_0021_v1.jxl": 32,
        f"{p}/_OUTTAKES_KEEP/$RECYCLE.BIN/r.NEF": 33,
        f"{p}/_OUTTAKES/DSC_0061.NEF": 40,
        f"{p}/_OUTTAKES/_EXPORT/DSC_0061_v1.jxl": 41,
        f"{p}/_OUTTAKES/_EXPORT/DSC_0061_v1.jpg": 42,
    })
    return root / "2025"


# What v0.12.0's AddFolders walk copied (the Originals filter, also inside an
# export folder it did not know was one): v0.12.1 must copy at least this. Not
# $RECYCLE.BIN/r.NEF: since round 3 a folder skipped by its name is skipped
# everywhere, inside an added folder too (declared in docs/version_history.md).
V0120_KEEP = ("DSC_0011.NEF", "_EXPORT/DSC_0011_WEB.jpg", "_EXPORT/DSC_0011_v1.xmp",
              "_EXPORT/DSC_0011-NR.dng", "_EXPORT/CaptureOne/Settings166/e.cos",
              "_EXPORT/NKSC_PARAM/e.nksc", "_EXPORT/WEB/DSC_0011_small.jpg",
              "sub/DSC_0021.NEF", "sub/_EXPORT/DSC_0021_v1.jpg")
V0120_KEEP_ALL = V0120_KEEP + ("_EXPORT/DSC_0011_v1.jxl", "_EXPORT/notes.txt",
                               "sub/_EXPORT/DSC_0021_v1.jxl")
V0120_OUTTAKES = ("DSC_0061.NEF", "_EXPORT/DSC_0061_v1.jpg")


@pytest.mark.parametrize("excluded", ["", "WEB"])
@pytest.mark.parametrize("originals", ["raw", "raw+jpeg", "all"])
@pytest.mark.parametrize("mode", ["3", "3+", "4", "4+", "5", "5+"])
def test_m1_an_export_inside_add_folders_copies_what_v0120_did(tmp_path, monkeypatch, mode,
                                                                originals, excluded):
    """An export folder inside _OUTTAKES_KEEP is a session's export since v0.12.1;
    what the Originals filter kept there in v0.12.0 is still copied, also in an
    --exclude-export-subfolders subfolder (v0.12.0 did not know it was an export)."""
    monkeypatch.setattr(sb, "EXCLUDE_EXPORT_SUBFOLDERS", excluded)
    folder = export_in_added_tree(tmp_path / "G")
    plan = plan_rows([(folder, mode, originals)], tmp_path)
    found = planned_set(plan)
    keep = tmp_path / "G" / "2025" / "shoot" / "_OUTTAKES_KEEP"
    for rel in (V0120_KEEP_ALL if originals == "all" else V0120_KEEP):
        assert os.path.normcase(str(keep / rel)) in found, rel
    outtakes = tmp_path / "G" / "2025" / "shoot" / "_OUTTAKES"
    if mode.startswith(("4", "5")):
        for rel in V0120_OUTTAKES:
            assert os.path.normcase(str(outtakes / rel)) in found, rel
    assert os.path.normcase(str(keep / "_EXPORT/CaptureOne/Cache/c.cop")) not in found
    assert os.path.normcase(str(keep / "$RECYCLE.BIN/r.NEF")) not in found
    assert os.path.normcase(str(keep / "$RECYCLE.BIN")) in {
        os.path.normcase(path) for path in plan.skipped_folders}
    files = listed(folder)
    skipped = listed(keep / "$RECYCLE.BIN")
    counted_files, counted_bytes = left_out(plan, mode.endswith("+"))
    # a folder skipped by its name is not listed: its files are not counted
    assert len(files) == len(plan.files) + counted_files + len(skipped)
    assert sum(files.values()) == (sum(item.size for item in plan.files) + counted_bytes
                                   + sum(skipped.values()))


# ---- B10 ----------------------------------------------------------------------------------
def test_b10_unexported_copy_takes_capture_one_of_a_folder_without_files(tmp_path):
    folder = sweep_tree(tmp_path / "G")
    found = planned_set(plan_rows([(folder, "3+", "raw")], tmp_path))
    for rel in ("250303_Trip/CaptureOne/Settings166/x.cos", "250303_Trip/NKSC_PARAM/y.nksc"):
        assert os.path.normcase(str(folder / rel)) in found, rel


def test_b10_capture_one_inside_a_session_is_counted(tmp_path):
    folder = sweep_tree(tmp_path / "G")
    without = plan_rows([(folder, "3", "raw")], tmp_path)
    # day2 (NEF + .cos), _OUTTAKES/old, _OUTTAKES_DROP, and the film session has none
    sizes = 52 + 51 + 36 + 50
    assert without.stats.inside_shoot_skipped == 4
    assert without.stats.inside_shoot_skipped_bytes == sizes


# ---- B9 -----------------------------------------------------------------------------------
def exclude_modes(tmp_path):
    table = tmp_path / "modes_exclude.csv"
    table.write_text(
        "Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,Only,Skip,Description\n"
        "X,yes,_OUTTAKES_KEEP,RAW SCANS*,OLD*;old,no,no,,,t\n"
        "X+,yes,_OUTTAKES_KEEP,RAW SCANS*,OLD*;old,no,yes,,,t\n", encoding="utf-8")
    return table


def test_b9_exclude_is_never_walked_and_is_counted(tmp_path, capsys):
    folder = sweep_tree(tmp_path / "G")
    table = exclude_modes(tmp_path)
    base = planned_set(plan_rows([(folder, "X", "raw")], tmp_path, table))
    plus = planned_set(plan_rows([(folder, "X+", "raw")], tmp_path, table))
    assert base <= plus
    trip = os.path.normcase(str(folder / "OLD_2019" / "190101_Trip" / "DSC_0500.NEF"))
    assert trip not in base and trip not in plus
    plan = plan_rows([(folder, "X+", "raw")], tmp_path, table)
    # OLD_2019 (3 files) and _OUTTAKES/old (1 file)
    assert plan.stats.mode_excluded == 4
    files = listed(folder)
    counted_files, _ = left_out(plan, True)
    assert len(files) == len(plan.files) + counted_files
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{folder},X+,raw\n", encoding="utf-8")
    assert mb.main(["--manifest", str(manifest), "--modes", str(table),
                    "--dest", str(tmp_path / "out"), "--dry-run"]) == 0
    assert "excluded by the mode (Exclude): 4 /" in capsys.readouterr().out


# ---- B18 ----------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["1", "3", "5"])
def test_b18_add_whole_without_an_original_beside_the_export(tmp_path, mode):
    folder = sweep_tree(tmp_path / "G")
    found = planned_set(plan_rows([(folder, mode, "raw")], tmp_path))
    assert os.path.normcase(str(folder / "190101_Film" / "RAW SCANS" / "000037.tif")) in found


def test_b18_guard_plus_mode_still_copies_that_folder_whole(tmp_path):
    """guard: 190101_Film has no original beside its export, so in a + mode its
    _OUTTAKES_KEEP was copied whole as a folder without an export."""
    folder = sweep_tree(tmp_path / "G")
    found = planned_set(plan_rows([(folder, "3+", "raw")], tmp_path))
    assert os.path.normcase(str(folder / "190101_Film" / "_OUTTAKES_KEEP" / "000099.tif")) in found


# ---- B19 ----------------------------------------------------------------------------------
def test_b19_unknown_column_in_the_modes_table_aborts(tmp_path, capsys):
    folder = sweep_tree(tmp_path / "G")
    table = tmp_path / "modes.csv"
    table.write_text(SHIPPED_MODES.read_text(encoding="utf-8").replace(
        ",Unexported,", ",Unexported ,", 1), encoding="utf-8")
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{folder},3+,raw\n", encoding="utf-8")
    dest = tmp_path / "out"
    assert mb.main(["--manifest", str(manifest), "--modes", str(table),
                    "--dest", str(dest), "--dry-run"]) == 2
    assert "'Unexported '" in capsys.readouterr().err
    assert not dest.exists()


def test_b19_unknown_column_in_the_manifest_aborts(tmp_path, capsys):
    folder = sweep_tree(tmp_path / "G")
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals,Orginals\n{folder},3+,raw,all\n",
                        encoding="utf-8")
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--analyze"]) == 2
    assert "'Orginals'" in capsys.readouterr().err


# ---- B17 ----------------------------------------------------------------------------------
def make_junction(link, target):
    target.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        pass
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True)
    if result.returncode != 0:
        pytest.skip("cannot create a junction or symlink")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_b17_links_are_shown_and_logged(tmp_path, capsys, photobackup_log_dir):
    make_tree(tmp_path, {"G/2025/shoot/_DSC1.NEF": 10,
                         "G/2025/shoot/_EXPORT/_DSC1_v1.jxl": 20,
                         "G/2025/shoot/CaptureOne/Settings166/_DSC1.NEF.cos": 5,
                         "E/cache/Proxies/a.cop": 30})
    link = tmp_path / "G" / "2025" / "shoot" / "CaptureOne" / "Cache"
    make_junction(link, tmp_path / "E" / "cache")
    assert sb.main(argv_for(tmp_path / "G" / "2025", tmp_path / "out", tmp_path,
                            "--dry-run")) == 0
    assert "links/junctions not followed: 1" in capsys.readouterr().out
    assert str(link) in latest_log(photobackup_log_dir)
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{tmp_path / 'G' / '2025'},3+,raw\n",
                        encoding="utf-8")
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(tmp_path / "out"), "--dry-run"]) == 0
    assert "links/junctions not followed: 1" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_b17_a_shared_listing_cache_keeps_the_count(tmp_path):
    make_tree(tmp_path, {"G/2025/shoot/_DSC1.NEF": 10,
                         "G/2025/shoot/_EXPORT/_DSC1_v1.jxl": 20})
    make_junction(tmp_path / "G" / "2025" / "shoot" / "linked", tmp_path / "E" / "x")
    args = sb._build_parser().parse_args(argv_for(tmp_path / "G" / "2025", tmp_path / "out",
                                                  tmp_path))
    cache = {}
    first = sb._Planner(args, list_cache=cache)
    first.build()
    second = sb._Planner(args, list_cache=cache)
    second.build()
    assert first.stats.skipped_links == second.stats.skipped_links == 1


# ---- B2 -----------------------------------------------------------------------------------
def test_b2_inside_csv_and_the_biggest_names(tmp_path, capsys, photobackup_log_dir):
    make_tree(tmp_path, {
        "G/2026/Untitled folder/_EXPORT/x.jpg": 10,
        "G/2026/Untitled folder/chat_photo_0001.jpeg": 11,
        "G/2026/Untitled folder/Birthday party/IMG_1.JPG": 5000,
        "G/2026/Untitled folder/Birthday party/IMG_2.JPG": 5000,
        "G/2026/Untitled folder/_OUTTAKES/a.NEF": 300,
        "G/2026/shoot/_DSC1.NEF": 10,
        "G/2026/shoot/_EXPORT/_DSC1_v1.jxl": 20,
        "G/2026/shoot/_OUTTAKES/b.NEF": 200,
    })
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{tmp_path / 'G' / '2026'},3+,raw\n",
                        encoding="utf-8")
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(tmp_path / "out"), "--dry-run"]) == 0
    out = capsys.readouterr().out.splitlines()
    rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_inside.csv"))))
    by_sub = {(Path(row["session"]).name, row["subfolder"]): row for row in rows}
    assert by_sub[("Untitled folder", "Birthday party")]["files"] == "2"
    assert by_sub[("Untitled folder", "Birthday party")]["bytes"] == "10000"
    assert by_sub[("Untitled folder", "_OUTTAKES")]["bytes"] == "300"
    assert by_sub[("shoot", "_OUTTAKES")]["bytes"] == "200"
    start = next(i for i, text in enumerate(out) if "inside sessions" in text)
    names = out[start + 1:start + 3]
    assert "Birthday party" in names[0]
    assert "_OUTTAKES" in names[1] and "2 files" in names[1]     # grouped by name


def test_b2_guard_the_selection_does_not_change(tmp_path):
    """guard: B2 only reports; the rule of what is a session waits for the owner."""
    make_tree(tmp_path, {
        "G/2026/Untitled folder/_EXPORT/x.jpg": 10,
        "G/2026/Untitled folder/chat_photo.jpeg": 11,
        "G/2026/Untitled folder/Birthday party/IMG_1.JPG": 50,
    })
    plan = plan_rows([(tmp_path / "G" / "2026", "3+", "raw")], tmp_path)
    assert not any("Birthday party" in item.src for item in plan.files)


# ---- B4 -----------------------------------------------------------------------------------
def deny(patch, marker):
    """The OS refuses every path containing `marker` (a folder that cannot be read).
    `patch` is a monkeypatch.context(): undoing the whole monkeypatch would also
    undo the conftest's LOG_DIR."""
    real_stat = os.stat
    real_exists = os.path.exists

    def stat(path, *args, **kwargs):
        if marker in os.fspath(path):
            raise PermissionError(13, "Access is denied", os.fspath(path))
        return real_stat(path, *args, **kwargs)

    def exists(path):
        if marker in os.fspath(path):
            return False
        return real_exists(path)

    patch.setattr(os, "stat", stat)
    patch.setattr(os.path, "exists", exists)


def unreadable_dir(name):
    """A _Planner._scan_dir that cannot read the folder `name` (same shape as the real one)."""
    real = sb._Planner._scan_dir

    def scan(self, path):
        result = real(self, path)
        if os.path.basename(path) == name:
            self.errors.append(f"ERROR scan {path}: denied")
            self.stats.scan_errors += 1
            return tuple([] for _ in result)
        return result
    return scan


def two_trips(tmp_path):
    make_tree(tmp_path, {
        "A/2024/tripA/DSC_0001.NEF": 100,
        "A/2024/tripA/_EXPORT/DSC_0001_v1.jpg": 50,
        "B/2024/tripB/DSC_0002.NEF": 101,
        "B/2024/tripB/_EXPORT/DSC_0002_v1.jpg": 51,
    })


def test_b4_another_source_that_is_away_is_not_gone(tmp_path, photobackup_log_dir):
    two_trips(tmp_path)
    dest = tmp_path / "D"
    assert sb.main(argv_for(tmp_path / "A" / "2024", dest, tmp_path / "A", "--yes")) == 0
    assert sb.main(argv_for(tmp_path / "B" / "2024", dest, tmp_path / "B", "--yes")) == 0
    os.rename(tmp_path / "A", tmp_path / "A_away")          # that disk is disconnected
    assert sb.main(argv_for(tmp_path / "B" / "2024", dest, tmp_path / "B", "--yes",
                            "--move-orphans")) == 0
    assert (dest / "2024" / "tripA" / "DSC_0001.NEF").exists()
    orphans = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_orphans.csv"))))
    assert {row["reason"] for row in orphans} == {"other_source"}
    catalog = sb._Catalog(dest, create=False, readonly=True)
    try:
        assert any(row.rel.endswith("DSC_0001.NEF") for row in catalog.all_rows())
    finally:
        catalog.close()


def test_b4_no_orphan_is_moved_in_a_run_with_read_errors(tmp_path, monkeypatch, capsys,
                                                         photobackup_log_dir):
    two_trips(tmp_path)
    dest = tmp_path / "D"
    source = tmp_path / "B" / "2024"
    assert sb.main(argv_for(source, dest, tmp_path / "B", "--yes")) == 0

    with monkeypatch.context() as patch:
        patch.setattr(sb._Planner, "_scan_dir", unreadable_dir("tripB"))
        deny(patch, "tripB")
        assert sb.main(argv_for(source, dest, tmp_path / "B", "--yes", "--move-orphans")) == 1
    assert (dest / "2024" / "tripB" / "DSC_0002.NEF").exists()
    assert "nothing moved" in capsys.readouterr().out


def test_b4_source_state_needs_positive_proof(tmp_path, monkeypatch):
    make_tree(tmp_path, {"S/trip/a.NEF": 10})
    assert sb._source_state(str(tmp_path / "S" / "trip" / "a.NEF")) == "present"
    assert sb._source_state(str(tmp_path / "S" / "trip" / "gone.NEF")) == "missing"
    assert sb._source_state(str(tmp_path / "S" / "gone" / "deep" / "x.NEF")) == "missing"
    with monkeypatch.context() as patch:
        deny(patch, "trip")
        assert sb._source_state(str(tmp_path / "S" / "trip" / "a.NEF")) == "unreadable"

    def nothing_there(path, *args, **kwargs):     # a drive that is not connected
        raise FileNotFoundError(2, "not found", os.fspath(path))

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", nothing_there)
        assert sb._source_state(str(tmp_path / "S" / "trip" / "a.NEF")) == "unreadable"


def test_b4_verify_says_source_unreadable(tmp_path, monkeypatch, photobackup_log_dir):
    make_tree(tmp_path, {"G/2024/trip/DSC_0001.NEF": 100,
                         "G/2024/trip/_EXPORT/DSC_0001_v1.jpg": 50})
    source = tmp_path / "G" / "2024"
    dest = tmp_path / "D"
    assert sb.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    with monkeypatch.context() as patch:
        deny(patch, str(source / "trip" / "DSC_0001.NEF"))      # not the backup copy
        assert sb.main(argv_for(source, dest, tmp_path, "--verify", "quick")) == 1
    rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_verify.csv"))))
    status = {Path(row["rel"]).name: row["status"] for row in rows}
    assert status["DSC_0001.NEF"] == "SOURCE_UNREADABLE"


def test_b4_move_detection_needs_the_same_proof(tmp_path, monkeypatch, photobackup_log_dir):
    make_tree(tmp_path, {"G/2024/tripold/DSC_0001.NEF": 100,
                         "G/2024/tripold/_EXPORT/DSC_0001_v1.jpg": 50})
    source = tmp_path / "G" / "2024"
    dest = tmp_path / "D"
    assert sb.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    shutil.copytree(source / "tripold", source / "tripnew")
    with monkeypatch.context() as patch:        # tripold cannot be read; tripnew is a copy
        patch.setattr(sb._Planner, "_scan_dir", unreadable_dir("tripold"))
        deny(patch, str(source / "tripold"))     # the source, not its backup copy
        assert sb.main(argv_for(source, dest, tmp_path, "--yes")) == 1
    assert (dest / "G" / "2024" / "tripold" / "DSC_0001.NEF").exists()
    assert (dest / "G" / "2024" / "tripnew" / "DSC_0001.NEF").exists()
    assert "MOVE " not in latest_log(photobackup_log_dir)


# ---- B6 and B7 ----------------------------------------------------------------------------
def test_b6_bad_rows_do_not_stop_the_rolling_verify(tmp_path):
    spec = {f"G/2024/trip/DSC_{index:04d}.NEF": 100 for index in range(10)}
    spec["G/2024/trip/_EXPORT/DSC_0000_v1.jpg"] = 3000
    spec["G/2024/trip/_EXPORT/DSC_0001_v1.jpg"] = 3000
    make_tree(tmp_path, spec)
    source = tmp_path / "G" / "2024"
    dest = tmp_path / "D"
    assert sb.main(argv_for(source, dest, tmp_path, "--yes", "--unselected", "raw")) == 0
    for index in range(2):
        copy = dest / "G" / "2024" / "trip" / "_EXPORT" / f"DSC_{index:04d}_v1.jpg"
        data = bytearray(copy.read_bytes())
        data[0] ^= 0xFF
        stat = copy.stat()
        os.chmod(copy, 0o666)
        copy.write_bytes(bytes(data))
        os.utime(copy, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    db = sqlite3.connect(dest / sb.CATALOG_FOLDER / "catalog.sqlite")
    db.execute("UPDATE files SET copied_at='2000-01-01T00:00:00' WHERE rel LIKE '%_v1.jpg'")
    db.commit()
    db.close()
    budget = str(2500 / 1e9)                    # smaller than each corrupt file
    for _night in range(3):
        assert sb.main(argv_for(source, dest, tmp_path, "--unselected", "raw",
                                "--verify", "full", "--max-gb", budget)) == 1
    catalog = sb._Catalog(dest, create=False, readonly=True)
    try:
        rows = catalog.all_rows()
    finally:
        catalog.close()
    good = [row for row in rows if not row.rel.endswith("_v1.jpg")]
    assert len(good) == 10
    assert all(row.verified_at for row in good)
    assert not any(row.verified_at for row in rows if row.rel.endswith("_v1.jpg"))


def test_b7_verify_says_what_it_did_not_check(tmp_path, capsys):
    make_tree(tmp_path, {"G/2023/a/DSC_0001.NEF": 100, "G/2023/a/_EXPORT/DSC_0001_v1.jpg": 50,
                         "G/2024/b/DSC_0002.NEF": 101, "G/2024/b/_EXPORT/DSC_0002_v1.jpg": 51})
    dest = tmp_path / "D"
    assert sb.main(["--source", str(tmp_path / "G" / "2023"), "--source",
                    str(tmp_path / "G" / "2024"), "--dest", str(dest), "--base",
                    str(tmp_path), "--yes"]) == 0
    capsys.readouterr()
    assert sb.main(argv_for(tmp_path / "G" / "2024", dest, tmp_path,
                            "--verify", "full")) == 0
    out = capsys.readouterr().out
    assert "2 files / 0.0 GB of the catalog are outside these sources: not checked" in out


# ---- B5 and B14 (menu) -------------------------------------------------------------------
@pytest.fixture
def menu(tmp_path, monkeypatch):
    import photo_backup
    monkeypatch.setattr(photo_backup, "CONFIG_PATH", str(tmp_path / "menu_config.json"))
    monkeypatch.setattr(photo_backup, "list_drives", lambda: [])
    monkeypatch.setattr(photo_backup, "run_captureone", lambda argv: 0)
    return photo_backup


def scripted(monkeypatch, answers):
    answers = list(answers)

    def fake_input(prompt=""):
        if not answers:
            raise EOFError
        return answers.pop(0)
    monkeypatch.setattr("builtins.input", fake_input)


def captured(monkeypatch, menu):
    calls = []
    monkeypatch.setattr(menu, "run_backend", lambda argv: calls.append(list(argv)) or 0)
    return calls


def value(argv, option):
    return argv[argv.index(option) + 1]


@pytest.mark.parametrize("where", ["config", "default"])
def test_b5_enter_keeps_raw_or_jpeg(tmp_path, monkeypatch, menu, where):
    source = tmp_path / "G"
    source.mkdir()
    if where == "config":
        session = menu.default_session()
        session.update(sources=[str(source)], dest=str(tmp_path / "out"), unselected="raw_or_jpeg")
        Path(menu.CONFIG_PATH).write_text(json.dumps({"last": session, "presets": {}}),
                                          encoding="utf-8")
        answers = ["1", "n"] + [""] * 10     # Enter on every question of the session
    else:
        monkeypatch.setattr(menu._backend(), "UNSELECTED", "raw_or_jpeg")
        answers = ["1", str(source), str(tmp_path / "out")] + [""] * 8
    calls = captured(monkeypatch, menu)
    scripted(monkeypatch, answers + ["n", "0"])
    menu.main([])
    assert calls, "the dry-run did not run"
    assert value(calls[0], "--unselected") == "raw_or_jpeg"


def test_b5_every_backend_choice_is_offered(menu, monkeypatch, capsys):
    scripted(monkeypatch, [""])
    choices = next(action.choices for action in menu._backend()._build_parser()._actions
                   if action.dest == "unselected")
    menu.ask_unselected("raw_jpeg")
    out = capsys.readouterr().out
    for choice in choices:
        assert choice in out
    assert "raw_jpeg" in [line.split()[1] for line in out.splitlines() if "<- current" in line]


def test_b14_the_argv_says_what_the_menu_showed(tmp_path, monkeypatch, menu):
    monkeypatch.setattr(menu._backend(), "COPY_CAPTUREONE_CACHE", True)
    monkeypatch.setattr(menu._backend(), "COPY_JXLPHOTO_DERIVATIVES", True)
    source = tmp_path / "G"
    source.mkdir()
    calls = captured(monkeypatch, menu)
    #          menu src          dest                  fmt sub unsel unexp c1cache deriv c1step repl copy preset exit
    scripted(monkeypatch, ["1", str(source), str(tmp_path / "out"), "", "", "", "", "n", "n", "", "",
                           "y", "n", "0"])
    assert menu.main([]) == 0
    for argv in calls:
        assert "--no-c1-cache" in argv and "--c1-cache" not in argv
        assert "--no-derivatives" in argv and "--derivatives" not in argv
    args = sb._build_parser().parse_args(calls[0])
    assert args.c1_cache is False and args.derivatives is False


def test_b14_negative_forms_beat_a_true_default(monkeypatch):
    monkeypatch.setattr(sb, "COPY_CAPTUREONE_CACHE", True)
    monkeypatch.setattr(sb, "COPY_JXLPHOTO_DERIVATIVES", True)
    args = sb._build_parser().parse_args(["--source", "x", "--dest", "y",
                                          "--no-c1-cache", "--no-derivatives"])
    assert args.c1_cache is False and args.derivatives is False
    args = sb._build_parser().parse_args(["--source", "x", "--dest", "y"])
    assert args.c1_cache is True and args.derivatives is True


def test_b14_capture_one_step_is_asked_and_not_blamed_on_the_setting(tmp_path, monkeypatch,
                                                                     menu):
    monkeypatch.setattr(menu, "COPY_CAPTUREONE_SETTINGS", True)
    source = tmp_path / "G"
    source.mkdir()
    session = menu.default_session()
    session.update(sources=[str(source)], dest=str(tmp_path / "out"), captureone=False)
    assert "COPY_CAPTUREONE_SETTINGS" not in menu.describe(session)
    steps = []
    monkeypatch.setattr(menu, "run_captureone", lambda argv: steps.append(argv) or 0)
    Path(menu.CONFIG_PATH).write_text(json.dumps({"last": session, "presets": {}}),
                                      encoding="utf-8")
    captured(monkeypatch, menu)
    #              menu use? src dest fmt sub unsel unexp c1cache deriv c1step repl copy preset exit
    scripted(monkeypatch, ["1", "n", "", "", "", "", "", "", "", "", "y", "", "y", "n", "0"])
    assert menu.main([]) == 0
    assert steps, "the Capture One step did not run after a yes"


# ---- B3 and B8 (mobile_backup.py) ---------------------------------------------------------
import mobile_backup as mob  # noqa: E402


@pytest.fixture
def mobile_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(mob, "LOG_DIR", str(tmp_path / "mlogs"))


def mobile_tree(tmp_path):
    make_tree(tmp_path / "G", {
        "2024/240101_A/_EXPORT/JXL_mobile/a1.jxl": 10,
        "2024/240101_A/_EXPORT/JXL_mobile/a2.jxl": 11,
        "2025/250202_B/_EXPORT/JXL_mobile/b1.jxl": 12,
        "2025/250202_B/_EXPORT/JXL_mobile/b2.jxl": 13,
        "2025/250303_C/_EXPORT/JXL_mobile/c1.jxl": 14,
    })
    return tmp_path / "G" / "2024", tmp_path / "G" / "2025"


def mirror(dest):
    return sorted(str(p.relative_to(dest)).replace("\\", "/") for p in Path(dest).rglob("*")
                  if p.is_file() and mob.STATE_FOLDER not in p.parts)


def no_dates(paths, log=None):
    return {path: None for path in paths}


def test_b3_a_run_with_fewer_sources_deletes_nothing_of_the_others(tmp_path, monkeypatch,
                                                                   mobile_logs):
    monkeypatch.setattr(mob, "read_shot_times", no_dates)
    y2024, y2025 = mobile_tree(tmp_path)
    dest = tmp_path / "H"
    assert mob.main(["--source", str(y2024), "--source", str(y2025), "--dest", str(dest)]) == 0
    before = mirror(dest)
    assert mob.main(["--source", str(y2025), "--dest", str(dest)]) == 0
    assert mirror(dest) == before
    assert mob.main(["--source", str(y2024), "--source", str(y2025), "--dest", str(dest)]) == 0
    assert mirror(dest) == before


def test_b3_old_state_without_roots(tmp_path, monkeypatch, mobile_logs):
    monkeypatch.setattr(mob, "read_shot_times", no_dates)
    y2024, y2025 = mobile_tree(tmp_path)
    dest = tmp_path / "H"
    assert mob.main(["--source", str(y2024), "--source", str(y2025), "--dest", str(dest)]) == 0
    state_path = dest / mob.STATE_FOLDER / mob.STATE_FILE
    data = json.loads(state_path.read_text(encoding="utf-8"))
    for rec in data["files"].values():
        rec.pop("root", None)
    state_path.write_text(json.dumps(data), encoding="utf-8")
    before = mirror(dest)
    assert mob.main(["--source", str(y2025), "--dest", str(dest)]) == 0
    assert mirror(dest) == before


def test_b3_a_folder_that_cannot_be_read_deletes_nothing(tmp_path, monkeypatch, mobile_logs,
                                                         capsys):
    monkeypatch.setattr(mob, "read_shot_times", no_dates)
    _y2024, y2025 = mobile_tree(tmp_path)
    dest = tmp_path / "H"
    assert mob.main(["--source", str(y2025), "--dest", str(dest)]) == 0
    before = mirror(dest)
    real = os.scandir

    def scandir(path="."):
        text = os.fspath(path)
        if "250202_B" in text and text.rstrip("\\/").endswith("_EXPORT"):
            raise PermissionError(13, "Access is denied", text)
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    assert mob.main(["--source", str(y2025), "--dest", str(dest)]) == 1
    monkeypatch.setattr(os, "scandir", real)
    assert mirror(dest) == before
    assert "250202_B" in capsys.readouterr().out


def test_s1_a_narrower_run_does_not_pin_a_deleted_photo(tmp_path, monkeypatch, mobile_logs):
    """Normal run, a quick test with one event, a photo of that event deleted, a
    normal run: the photo leaves the mirror (it is under a source read in full)."""
    monkeypatch.setattr(mob, "read_shot_times", no_dates)
    _y2024, y2025 = mobile_tree(tmp_path)
    dest = tmp_path / "H"
    assert mob.main(["--source", str(y2025), "--dest", str(dest)]) == 0
    assert mob.main(["--source", str(y2025 / "250202_B"), "--dest", str(dest)]) == 0
    (y2025 / "250202_B" / "_EXPORT" / "JXL_mobile" / "b1.jxl").unlink()
    assert mob.main(["--source", str(y2025), "--dest", str(dest)]) == 0
    assert sorted(Path(p).name for p in mirror(dest) if "250202_B" in p) == ["0000_b2.jxl"]


def test_s1_the_summary_says_what_it_left_alone(tmp_path, monkeypatch, mobile_logs, capsys):
    monkeypatch.setattr(mob, "read_shot_times", no_dates)
    y2024, y2025 = mobile_tree(tmp_path)
    dest = tmp_path / "H"
    assert mob.main(["--source", str(y2024), "--source", str(y2025), "--dest", str(dest)]) == 0
    capsys.readouterr()
    assert mob.main(["--source", str(y2025), "--dest", str(dest)]) == 0
    assert "2 photo(s) of sources not given in this run were left alone" in capsys.readouterr().out


def test_b8_an_exiftool_failure_is_retried_and_exits_1(tmp_path, monkeypatch, mobile_logs):
    make_tree(tmp_path / "G", {
        "2025/250101_A/_EXPORT/JXL_mobile/A.jxl": 10,
        "2025/250101_A/_EXPORT/JXL_mobile/B.jxl": 11,
        "2025/250101_A/_EXPORT/JXL_mobile/C.jxl": 12,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "H"
    monkeypatch.setattr(mob, "EXIFTOOL", str(tmp_path / "no_such_exiftool.exe"))
    assert mob.main(["--source", str(source), "--dest", str(dest)]) == 1
    calls = []

    def dates(paths, log=None):
        calls.append(sorted(os.path.basename(p) for p in paths))
        table = {"A.jxl": "2025:01:01 12:00:03.000000", "B.jxl": "2025:01:01 12:00:02.000000",
                 "C.jxl": "2025:01:01 12:00:01.000000"}
        return {p: table[os.path.basename(p)] for p in paths}

    monkeypatch.setattr(mob, "read_shot_times", dates)
    assert mob.main(["--source", str(source), "--dest", str(dest)]) == 0
    assert calls == [["A.jxl", "B.jxl", "C.jxl"]]
    assert sorted(Path(p).name for p in mirror(dest)) == ["0000_C.jxl", "0001_B.jxl", "0002_A.jxl"]
    assert mob.main(["--source", str(source), "--dest", str(dest)]) == 0
    assert len(calls) == 1                      # read once it worked
