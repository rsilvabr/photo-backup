"""Per-folder modes: the manifest, the modes table and manifest_backup.py."""
import csv
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

import manifest_backup as mb
import selective_backup as sb

from conftest import dest_files, latest_csv, latest_log, make_tree, snapshot

HOSTILE = "吾妻山公園_菜の花 [FINAL] 5% off"
SHIPPED_MODES = Path(mb.__file__).resolve().parent / mb.MODES_FILE


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def write_modes(path, rows):
    return write_csv(path, ("Mode", "Unselected", "AddFolders", "AddWhole",
                            "Exclude", "Whole", "Description"), rows)


def write_manifest(path, rows):
    return write_csv(path, ("Folder", "Mode", "Originals"), rows)


def rules_from_mode(mode, originals="raw"):
    return sb.FolderRules(add_filtered=tuple(mode.add_folders),
                          add_whole=tuple(mode.add_whole), exclude=tuple(mode.exclude),
                          whole=mode.whole,
                          originals=mb.ORIGINALS_VALUES[originals])


def planner_for(source, tmp_path, *extra, rules=None, skip_paths=frozenset()):
    args = sb._build_parser().parse_args(
        ["--source", str(source), "--dest", str(tmp_path / "out"),
         "--base", str(tmp_path), *extra])
    planner = sb._Planner(args, skip_paths=skip_paths, rules=rules)
    planner.build()
    return planner


def rels(planner):
    return {item.rel.replace("\\", "/") for item in planner.files}


def categories(planner):
    return {item.rel.replace("\\", "/"): item.category for item in planner.files}


def test_t1_folder_matches():
    assert sb._folder_matches("_OUTTAKES", ("_OUTTAKES*",))
    assert sb._folder_matches("_outtakes_keep", ("_OUTTAKES*",))
    assert not sb._folder_matches("x_OUTTAKES", ("_OUTTAKES*",))
    assert sb._folder_matches("RAW SCANS GAMMA 1", ("RAW SCANS*", "RAW TIFF*"))
    assert sb._folder_matches(HOSTILE, (HOSTILE,))
    assert not sb._folder_matches(HOSTILE.replace("[", "X").replace("]", "Y"), (HOSTILE,))
    assert sb._folder_matches("a[b]", ("a[b]",))
    assert not sb._folder_matches("ab", ("a[b]",))
    assert sb._folder_matches("a*b", ("a*b",))
    assert sb._folder_matches("axxb", ("a*b",))


def test_t2_raw_or_jpeg():
    raw_stems = {"dsc_8"}
    assert sb._original_kept("DSC_8.NEF", raw_stems, "raw_or_jpeg", False)
    assert not sb._original_kept("DSC_8_v1.jpg", raw_stems, "raw_or_jpeg", False)
    assert sb._original_kept("DSC_7.jpg", raw_stems, "raw_or_jpeg", False)
    assert sb._original_kept("DSC_8.xmp", raw_stems, "raw_or_jpeg", False)
    assert sb._original_kept("DSC_9.xmp", raw_stems, "raw_or_jpeg", True)


def test_t3_raw_jpeg():
    assert sb._original_kept("DSC_7.JPG", set(), "raw_jpeg", False)
    assert sb._original_kept("x.jpeg", set(), "raw_jpeg", False)
    # a TIFF is kept only when no RAW of the photo is beside it (round 2 of the sweep)
    assert sb._original_kept("scan.tif", set(), "raw_jpeg", False)
    assert not sb._original_kept("scan.tif", {"scan"}, "raw_jpeg", False)
    assert sb._original_kept("scan.tif", set(), "all", False)


def test_t4_unselected_raw_unchanged(tmp_path):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/{HOSTILE}/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/shoot/_DSC1.xmp": 11,
        f"2025/{HOSTILE}/shoot/_DSC2.NEF": 12,
        f"2025/{HOSTILE}/shoot/_DSC2.JPG": 13,
        f"2025/{HOSTILE}/shoot/_DSC2.xmp": 14,
        f"2025/{HOSTILE}/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
    })
    source = root / "2025" / HOSTILE / "shoot"
    args = sb._build_parser().parse_args(
        ["--source", str(source), "--dest", str(tmp_path / "out"),
         "--base", str(tmp_path), "--unselected", "raw"])
    planned = sb.plan(args)
    found = {item.rel.replace("\\", "/"): item.category for item in planned.files}
    prefix = f"G/2025/{HOSTILE}/shoot"
    assert found[f"{prefix}/_DSC1.NEF"] == sb.CAT_SOURCE
    assert found[f"{prefix}/_DSC1.xmp"] == sb.CAT_SOURCE
    assert found[f"{prefix}/_DSC2.NEF"] == sb.CAT_UNSELECTED
    assert found[f"{prefix}/_DSC2.xmp"] == sb.CAT_UNSELECTED
    assert f"{prefix}/_DSC2.JPG" not in found


def test_t5_add_folders_raw_originals(tmp_path):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/{HOSTILE}/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
        f"2025/{HOSTILE}/shoot/_OUTTAKES_KEEP/_DSC10.NEF": 30,
        f"2025/{HOSTILE}/shoot/_OUTTAKES_KEEP/_DSC10.JPG": 31,
        f"2025/{HOSTILE}/shoot/_OUTTAKES_KEEP/_DSC11.JPG": 32,
        f"2025/{HOSTILE}/shoot/_OUTTAKES_KEEP/_xmp/_DSC10.NEF.xmp": 33,
        f"2025/{HOSTILE}/shoot/_OUTTAKES_KEEP/CaptureOne/Settings166/x.cos": 34,
        f"2025/{HOSTILE}/shoot/_OUTTAKES_KEEP/CaptureOne/Cache/p.cop": 35,
    })
    source = root / "2025" / HOSTILE / "shoot"
    planner = planner_for(source, tmp_path,
                          rules=sb.FolderRules(add_filtered=("_OUTTAKES_KEEP",),
                                               originals="raw_or_jpeg"))
    found = categories(planner)
    prefix = f"G/2025/{HOSTILE}/shoot/_OUTTAKES_KEEP"
    assert found[f"{prefix}/_DSC10.NEF"] == sb.CAT_ADDED
    assert f"{prefix}/_DSC10.JPG" not in found
    assert found[f"{prefix}/_DSC11.JPG"] == sb.CAT_ADDED
    assert found[f"{prefix}/_xmp/_DSC10.NEF.xmp"] == sb.CAT_ADDED
    assert found[f"{prefix}/CaptureOne/Settings166/x.cos"] == sb.CAT_ADDED
    assert f"{prefix}/CaptureOne/Cache/p.cop" not in found


def test_t6_add_whole_leads_raw_scans(tmp_path):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/{HOSTILE}/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
        f"2025/{HOSTILE}/shoot/RAW SCANS GAMMA 1/scan.tif": 40,
    })
    modes = mb.read_modes(str(SHIPPED_MODES))
    planner = planner_for(root / "2025" / HOSTILE / "shoot", tmp_path,
                          rules=rules_from_mode(modes["1"]))
    assert (f"G/2025/{HOSTILE}/shoot/RAW SCANS GAMMA 1/scan.tif"
            in rels(planner))


def test_t7_outtakes_patterns(tmp_path):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/{HOSTILE}/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
        f"2025/{HOSTILE}/shoot/_OUTTAKES_KEEP/a.NEF": 30,
        f"2025/{HOSTILE}/shoot/_OUTTAKES/b.NEF": 31,
        f"2025/{HOSTILE}/shoot/_OUTTAKES_DROP/c.NEF": 32,
    })
    modes = mb.read_modes(str(SHIPPED_MODES))
    source = root / "2025" / HOSTILE / "shoot"
    prefix = f"G/2025/{HOSTILE}/shoot"
    mode4 = rels(planner_for(source, tmp_path, rules=rules_from_mode(modes["4"])))
    assert f"{prefix}/_OUTTAKES_KEEP/a.NEF" in mode4
    assert f"{prefix}/_OUTTAKES/b.NEF" in mode4
    assert f"{prefix}/_OUTTAKES_DROP/c.NEF" not in mode4
    mode5 = rels(planner_for(source, tmp_path, rules=rules_from_mode(modes["5"])))
    assert f"{prefix}/_OUTTAKES_KEEP/a.NEF" in mode5
    assert f"{prefix}/_OUTTAKES/b.NEF" in mode5
    assert f"{prefix}/_OUTTAKES_DROP/c.NEF" in mode5
    assert not sb._folder_matches("_OUTTAKES_KEEP", ("_OUTTAKES",))
    assert sb._folder_matches("_OUTTAKES_KEEP", ("_OUTTAKES*",))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_t8_whole(tmp_path):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/{HOSTILE}/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
        f"2025/{HOSTILE}/shoot/_OUTTAKES/o.NEF": 30,
        f"2025/{HOSTILE}/shoot/CaptureOne/Cache/p.cop": 31,
        f"2025/{HOSTILE}/shoot/CaptureOne/Settings166/a.cos": 32,
        f"2025/{HOSTILE}/plain/p.NEF": 40,
        f"2025/outside/z.NEF": 41,
    })
    link = root / "2025" / HOSTILE / "shoot" / "linkdir"
    target = root / "2025" / "outside"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True)
        if result.returncode != 0:
            pytest.skip("cannot create a junction or symlink")
    planner = planner_for(root / "2025" / HOSTILE, tmp_path,
                          rules=sb.FolderRules(whole=True))
    found = categories(planner)
    prefix = f"G/2025/{HOSTILE}"
    assert found[f"{prefix}/shoot/_EXPORT/x/_DSC1_v1.jpg"] == sb.CAT_WHOLE
    assert found[f"{prefix}/shoot/_DSC1.NEF"] == sb.CAT_WHOLE
    assert found[f"{prefix}/shoot/_OUTTAKES/o.NEF"] == sb.CAT_WHOLE
    assert found[f"{prefix}/shoot/CaptureOne/Settings166/a.cos"] == sb.CAT_WHOLE
    assert found[f"{prefix}/plain/p.NEF"] == sb.CAT_WHOLE
    assert f"{prefix}/shoot/CaptureOne/Cache/p.cop" not in found
    assert not any("outside" in rel or "linkdir" in rel for rel in found)
    assert planner.stats.skipped_links >= 1


def test_t9_add_folders_only_inside_sessions(tmp_path):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/plain/_OUTTAKES/x.NEF": 30,
        f"2025/plain/file.txt": 31,
    })
    modes = mb.read_modes(str(SHIPPED_MODES))
    planner = planner_for(root / "2025", tmp_path, rules=rules_from_mode(modes["5"]))
    assert not any("_OUTTAKES/x.NEF" in rel for rel in rels(planner))
    assert any(Path(folder.path).name == "plain" for folder in planner.unexported)


def test_t10_nested_manifest_line(tmp_path, photobackup_log_dir):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/{HOSTILE}/evento/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/evento/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
        f"2025/{HOSTILE}/evento/shoot/_OUTTAKES/o.NEF": 30,
        f"2025/{HOSTILE}/other/shoot/_DSC2.NEF": 40,
        f"2025/{HOSTILE}/other/shoot/_EXPORT/x/_DSC2_v1.jpg": 50,
        f"2025/{HOSTILE}/other/shoot/_OUTTAKES/o2.NEF": 60,
    })
    parent = root / "2025" / HOSTILE
    child = parent / "evento"
    manifest = write_manifest(tmp_path / "manifest.csv",
                              [(parent, "1", "raw"), (child, "6", "raw")])
    dest = tmp_path / "out"
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(dest), "--yes"]) == 0
    files = dest_files(dest)
    base = f"/2025/{HOSTILE}"
    assert any(rel.endswith(f"{base}/evento/shoot/_OUTTAKES/o.NEF") for rel in files)
    assert any(rel.endswith(f"{base}/evento/shoot/_EXPORT/x/_DSC1_v1.jpg") for rel in files)
    assert any(rel.endswith(f"{base}/evento/shoot/_DSC1.NEF") for rel in files)
    assert any(rel.endswith(f"{base}/other/shoot/_EXPORT/x/_DSC2_v1.jpg") for rel in files)
    assert any(rel.endswith(f"{base}/other/shoot/_DSC2.NEF") for rel in files)
    assert not any(rel.endswith(f"{base}/other/shoot/_OUTTAKES/o2.NEF") for rel in files)
    plan_rows = list(csv.DictReader(io.StringIO(
        latest_csv(photobackup_log_dir, "_plan.csv"))))
    planned = [row["rel"] for row in plan_rows]
    assert len(planned) == len(set(planned))


def test_t11_aborts(tmp_path, monkeypatch, photobackup_log_dir):
    data = tmp_path / "data"
    make_tree(data, {
        f"2025/{HOSTILE}/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
    })
    shoot = data / "2025" / HOSTILE / "shoot"
    other = data / "2025" / HOSTILE / "other"
    other.mkdir(parents=True)
    good_modes = write_modes(tmp_path / "modes_ok.csv",
                             [("1", "no", "", "SCANS*", "", "no", "ok")])

    def expect(argv, dest, name):
        rc = mb.main(argv)
        assert rc == 2, name
        assert not Path(dest).exists(), name

    out = lambda name: tmp_path / ("out_" + name)

    expect(["--manifest", str(tmp_path / "missing.csv"), "--modes", str(good_modes),
            "--dest", str(out("missing_manifest"))],
           out("missing_manifest"), "missing manifest")

    bad_columns = tmp_path / "bad_columns.csv"
    bad_columns.write_text("Folder,Mode\n" + str(shoot) + ",1\n", encoding="utf-8")
    expect(["--manifest", str(bad_columns), "--modes", str(good_modes),
            "--dest", str(out("missing_column"))],
           out("missing_column"), "missing column")

    unknown = write_manifest(tmp_path / "unknown.csv", [(shoot, "99", "raw")])
    expect(["--manifest", str(unknown), "--modes", str(good_modes),
            "--dest", str(out("unknown_mode"))],
           out("unknown_mode"), "unknown mode")

    repeated = write_modes(tmp_path / "modes_repeated.csv",
                           [("1", "no", "", "", "", "no", "a"),
                            ("1", "yes", "", "", "", "no", "b")])
    ok_manifest = write_manifest(tmp_path / "ok.csv", [(shoot, "1", "raw")])
    expect(["--manifest", str(ok_manifest), "--modes", str(repeated),
            "--dest", str(out("repeated_mode"))],
           out("repeated_mode"), "repeated mode")

    bad_yes_no = write_modes(tmp_path / "modes_yesno.csv",
                             [("1", "maybe", "", "", "", "no", "")])
    expect(["--manifest", str(ok_manifest), "--modes", str(bad_yes_no),
            "--dest", str(out("bad_yes_no"))],
           out("bad_yes_no"), "invalid yes/no")

    whole_add_folders = write_modes(tmp_path / "modes_whole_folders.csv",
                                    [("1", "no", "KEEP", "", "", "yes", "")])
    expect(["--manifest", str(ok_manifest), "--modes", str(whole_add_folders),
            "--dest", str(out("whole_add_folders"))],
           out("whole_add_folders"), "Whole=yes with AddFolders")

    whole_add_whole = write_modes(tmp_path / "modes_whole_whole.csv",
                                  [("1", "no", "", "SCANS*", "", "yes", "")])
    expect(["--manifest", str(ok_manifest), "--modes", str(whole_add_whole),
            "--dest", str(out("whole_add_whole"))],
           out("whole_add_whole"), "Whole=yes with AddWhole")

    bad_originals = write_manifest(tmp_path / "bad_originals.csv",
                                   [(shoot, "1", "everything")])
    expect(["--manifest", str(bad_originals), "--modes", str(good_modes),
            "--dest", str(out("bad_originals"))],
           out("bad_originals"), "invalid Originals")

    missing_folder = write_manifest(tmp_path / "missing_folder.csv",
                                    [(tmp_path / "nope", "1", "raw")])
    expect(["--manifest", str(missing_folder), "--modes", str(good_modes),
            "--dest", str(out("missing_folder"))],
           out("missing_folder"), "folder does not exist")

    repeated_folder = write_manifest(tmp_path / "repeated_folder.csv",
                                     [(shoot, "1", "raw"), (str(shoot).upper(), "1", "raw")])
    expect(["--manifest", str(repeated_folder), "--modes", str(good_modes),
            "--dest", str(out("repeated_folder"))],
           out("repeated_folder"), "folder repeated")

    with monkeypatch.context() as patch:
        patch.setattr(mb, "_drive_key",
                      lambda path: "c:" if os.path.normcase(str(path)).endswith("shoot")
                      else "d:")
        two_drives = write_manifest(tmp_path / "two_drives.csv",
                                    [(shoot, "1", "raw"), (other, "1", "raw")])
        expect(["--manifest", str(two_drives), "--modes", str(good_modes),
                "--dest", str(out("two_drives"))],
               out("two_drives"), "folders on two drives")

    empty_modes = tmp_path / "modes_empty.csv"
    empty_modes.write_text("", encoding="utf-8")
    expect(["--manifest", str(ok_manifest), "--modes", str(empty_modes),
            "--dest", str(out("empty_modes"))],
           out("empty_modes"), "empty modes file")

    missing_modes = tmp_path / "modes_missing.csv"
    expect(["--manifest", str(ok_manifest), "--modes", str(missing_modes),
            "--dest", str(out("missing_modes"))],
           out("missing_modes"), "missing modes file")

    modes_missing_column = tmp_path / "modes_column.csv"
    modes_missing_column.write_text("Mode,Unselected,AddFolders\n1,no,\n", encoding="utf-8")
    expect(["--manifest", str(ok_manifest), "--modes", str(modes_missing_column),
            "--dest", str(out("modes_column"))],
           out("modes_column"), "modes file missing column")

    empty_manifest = tmp_path / "empty_manifest.csv"
    empty_manifest.write_text("Folder,Mode,Originals\n", encoding="utf-8")
    expect(["--manifest", str(empty_manifest), "--modes", str(good_modes),
            "--dest", str(out("empty_manifest"))],
           out("empty_manifest"), "manifest without lines")

    expect(["--manifest", str(ok_manifest), "--modes", str(good_modes)],
           out("no_dest"), "no destination without --analyze")

    rc = mb.main(["--manifest", str(ok_manifest), "--modes", str(good_modes),
                  "--analyze", "--export-formats", "garbage"])
    assert rc == 2, "invalid --export-formats with --analyze"

    assert list(Path(photobackup_log_dir).iterdir()) == []


def test_t12_real_copy_source_unchanged_and_second_run_copies_nothing(
        tmp_path, photobackup_log_dir):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/{HOSTILE}/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/shoot/_DSC1.xmp": 11,
        f"2025/{HOSTILE}/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
        f"2025/{HOSTILE}/shoot/_DSC2.NEF": 30,
        f"2025/{HOSTILE}/shoot/_DSC2.JPG": 31,
    })
    folder = root / "2025" / HOSTILE
    manifest = write_manifest(tmp_path / "manifest.csv", [(folder, "2", "raw")])
    dest = tmp_path / "backup"
    before = snapshot(root)
    argv = ["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
            "--dest", str(dest), "--yes"]
    assert mb.main(argv) == 0
    assert snapshot(root) == before
    assert mb.main(argv) == 0
    assert snapshot(root) == before
    assert "copied 0 files" in latest_log(photobackup_log_dir)


def test_t13_lowering_a_mode_deletes_nothing(tmp_path, photobackup_log_dir):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/{HOSTILE}/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
        f"2025/{HOSTILE}/shoot/_DSC2.NEF": 30,
    })
    folder = root / "2025" / HOSTILE
    manifest = write_manifest(tmp_path / "manifest.csv", [(folder, "2", "raw")])
    dest = tmp_path / "backup"
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(dest), "--yes"]) == 0
    assert any(rel.endswith("_DSC2.NEF") for rel in dest_files(dest))
    write_manifest(manifest, [(folder, "1", "raw")])
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(dest), "--yes"]) == 0
    assert any(rel.endswith("_DSC2.NEF") for rel in dest_files(dest))
    orphans = latest_csv(photobackup_log_dir, "_orphans.csv")
    rows = list(csv.DictReader(io.StringIO(orphans)))
    assert any(row["reason"] == "not_selected" and row["rel"].endswith("_DSC2.NEF")
               for row in rows)


def test_t14_analyze_matches_dry_run(tmp_path, photobackup_log_dir):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/{HOSTILE}/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/shoot/_DSC1.JPG": 11,
        f"2025/{HOSTILE}/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
        f"2025/{HOSTILE}/shoot/_DSC2.NEF": 30,
        f"2025/{HOSTILE}/shoot/_DSC2.JPG": 31,
        f"2025/{HOSTILE}/shoot/_DSC3.JPG": 32,
    })
    folder = root / "2025" / HOSTILE
    manifest = write_manifest(tmp_path / "manifest.csv", [(folder, "2", "raw+jpeg")])
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--analyze"]) == 0
    analysis = list(csv.DictReader(io.StringIO(
        latest_csv(photobackup_log_dir, "_analysis.csv"))))
    chosen = [row for row in analysis if row["chosen"] == "yes"]
    assert len(chosen) == 1
    assert chosen[0]["mode"] == "2" and chosen[0]["originals"] == "raw+jpeg"
    analyzed_files = int(chosen[0]["files"])
    analyzed_bytes = int(chosen[0]["bytes"])
    by_key = {(row["mode"], row["originals"]): int(row["bytes"]) for row in analysis}
    assert by_key[("1", "raw")] < by_key[("2", "raw")] < by_key[("2", "raw+jpeg")]
    assert by_key[("6", "raw")] == by_key[("6", "raw+jpeg")] == by_key[("6", "all")]
    dest = tmp_path / "out"
    assert not dest.exists()
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(dest), "--dry-run"]) == 0
    planned = list(csv.DictReader(io.StringIO(
        latest_csv(photobackup_log_dir, "_plan.csv"))))
    assert len(planned) == analyzed_files
    assert sum(int(row["size"]) for row in planned) == analyzed_bytes


def test_t15_other_mode_names_work(tmp_path):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/{HOSTILE}/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
        f"2025/{HOSTILE}/shoot/MY_KEEP/_DSC2.NEF": 30,
        f"2025/{HOSTILE}/shoot/MY_KEEP/_DSC2.JPG": 31,
        f"2025/{HOSTILE}/shoot/MY_KEEP/_DSC3.JPG": 32,
        f"2025/{HOSTILE}/shoot/SCANS/scan.tif": 40,
    })
    modes = write_modes(tmp_path / "modes_custom.csv",
                        [("A", "yes", "MY_KEEP", "SCANS", "", "no", "custom names")])
    folder = root / "2025" / HOSTILE
    manifest = write_manifest(tmp_path / "manifest.csv", [(folder, "A", "raw+jpeg")])
    dest = tmp_path / "backup"
    assert mb.main(["--manifest", str(manifest), "--modes", str(modes),
                    "--dest", str(dest), "--yes"]) == 0
    files = dest_files(dest)
    for suffix in ("shoot/_DSC1.NEF", "shoot/_EXPORT/x/_DSC1_v1.jpg",
                   "shoot/MY_KEEP/_DSC2.NEF", "shoot/MY_KEEP/_DSC2.JPG",
                   "shoot/MY_KEEP/_DSC3.JPG", "shoot/SCANS/scan.tif"):
        assert any(rel.endswith(suffix) for rel in files), suffix


def test_menu_manifest_analyze_and_copy(tmp_path, monkeypatch):
    import photo_backup as menu

    monkeypatch.setattr(menu, "CONFIG_PATH", str(tmp_path / "menu_config.json"))
    monkeypatch.setattr(menu, "list_drives", lambda: [])
    data = tmp_path / "data"
    data.mkdir()
    manifest = write_manifest(tmp_path / "manifest.csv", [(data, "1", "raw")])
    calls = []

    def fake_run(argv):
        calls.append(list(argv))
        return 0

    monkeypatch.setattr(menu, "run_manifest", fake_run)
    #               menu manifest dest choice  Capture One  replace  copy  exit
    answers = iter(["7", str(manifest), str(tmp_path / "out"), "2", "n", "", "y", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert menu.main([]) == 0
    assert len(calls) == 2
    assert "--dry-run" in calls[0] and "--yes" not in calls[0]
    assert "--yes" in calls[1] and "--dry-run" not in calls[1]
    calls.clear()
    answers = iter(["7", str(manifest), str(tmp_path / "out"), "1", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert menu.main([]) == 0
    assert len(calls) == 1 and "--analyze" in calls[0]


def test_t16_verify_full_after_copy(tmp_path):
    root = tmp_path / "G"
    make_tree(root, {
        f"2025/{HOSTILE}/shoot/_DSC1.NEF": 10,
        f"2025/{HOSTILE}/shoot/_DSC1.xmp": 11,
        f"2025/{HOSTILE}/shoot/_EXPORT/x/_DSC1_v1.jpg": 20,
        f"2025/{HOSTILE}/shoot/_DSC2.NEF": 30,
    })
    folder = root / "2025" / HOSTILE
    manifest = write_manifest(tmp_path / "manifest.csv", [(folder, "2", "raw")])
    dest = tmp_path / "backup"
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(dest), "--yes"]) == 0
    assert sb.main(["--source", str(folder), "--dest", str(dest),
                    "--verify", "full"]) == 0


# ---- Final review (261109): each one failed before its fix ---------------------------
def session_tree(root, extra):
    prefix = f"2025/{HOSTILE}/shoot"
    spec = {f"{prefix}/_DSC1.NEF": 10, f"{prefix}/_EXPORT/x/_DSC1_v1.jpg": 20}
    spec.update({f"{prefix}/{rel}": size for rel, size in extra.items()})
    make_tree(root, spec)
    return root / "2025" / HOSTILE / "shoot", f"G/2025/{HOSTILE}/shoot"


def test_review_a_duplicate_counter_jpeg_is_its_own_photo(tmp_path):
    """`DSC_0001 (2).JPG` beside `DSC_0001.NEF` is another file, not a variant:
    `raw` promises that no photo is left without a copy."""
    source, prefix = session_tree(tmp_path / "G", {
        "DSC_0001.NEF": 30, "DSC_0001 (2).JPG": 31, "DSC_0001.JPG": 32, "DSC_0001_v1.jpg": 33})
    found = rels(planner_for(source, tmp_path, "--unselected", "raw_or_jpeg"))
    assert f"{prefix}/DSC_0001 (2).JPG" in found
    assert f"{prefix}/DSC_0001.JPG" not in found
    assert f"{prefix}/DSC_0001_v1.jpg" not in found


def test_review_b_the_sidecar_of_a_kept_jpeg_comes_along(tmp_path):
    source, prefix = session_tree(tmp_path / "G", {
        "_DSC7.JPG": 30, "_DSC7.xmp": 31, "_DSC8.xmp": 32})
    found = rels(planner_for(source, tmp_path, "--unselected", "raw_or_jpeg"))
    assert f"{prefix}/_DSC7.JPG" in found
    assert f"{prefix}/_DSC7.xmp" in found
    assert f"{prefix}/_DSC8.xmp" not in found           # no photo of its own here


def test_review_c_analyze_does_not_hide_an_unreadable_folder(
        tmp_path, monkeypatch, capsys, photobackup_log_dir):
    source, _ = session_tree(tmp_path / "G", {"_OUTTAKES/o.NEF": 30})
    manifest = write_manifest(tmp_path / "manifest.csv", [(source, "4", "raw")])
    real = sb._Planner._scan_dir

    def unreadable(self, path):
        if os.path.basename(path) == "_OUTTAKES":
            self.errors.append(f"ERROR scan {path}: denied")
            self.stats.scan_errors += 1
            return [], [], []
        return real(self, path)

    monkeypatch.setattr(sb._Planner, "_scan_dir", unreadable)
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--analyze"]) == 1
    assert "could not be read" in capsys.readouterr().out
    assert "ERROR scan" in latest_log(photobackup_log_dir)


@pytest.mark.parametrize("target", ["manifest", "modes"])
def test_review_d_a_csv_that_is_not_utf8_is_refused_cleanly(tmp_path, capsys, target):
    """What Excel writes for 'CSV' on a Japanese Windows: cp932."""
    folder = tmp_path / "吾妻"
    folder.mkdir()
    manifest = tmp_path / "manifest.csv"
    modes = tmp_path / "modes.csv"
    write_manifest(manifest, [(folder, "1", "raw")])
    write_modes(modes, [("1", "no", "", "", "", "no", "吾妻")])
    bad = manifest if target == "manifest" else modes
    bad.write_bytes(bad.read_text(encoding="utf-8").encode("cp932"))
    assert mb.main(["--manifest", str(manifest), "--modes", str(modes),
                    "--analyze"]) == 2
    assert "UTF-8" in capsys.readouterr().err


def test_review_e_exclude_works_at_any_depth_of_add_whole(tmp_path):
    source, prefix = session_tree(tmp_path / "G", {
        "SCANS/a.tif": 30, "SCANS/tmp/b.tif": 31})
    found = rels(planner_for(source, tmp_path, rules=sb.FolderRules(
        add_whole=("SCANS",), exclude=("tmp",))))
    assert f"{prefix}/SCANS/a.tif" in found
    assert f"{prefix}/SCANS/tmp/b.tif" not in found


def test_review_f_an_export_folder_cannot_have_a_line_below_another(tmp_path, capsys):
    """The line above would stop seeing its session's export and lose every original."""
    source, _ = session_tree(tmp_path / "G", {})
    manifest = write_manifest(tmp_path / "manifest.csv",
                              [(source, "2", "raw"), (source / "_EXPORT", "6", "raw")])
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--analyze"]) == 2
    assert "export folder" in capsys.readouterr().err


def test_review_f_an_export_folder_alone_is_allowed(tmp_path):
    source, _ = session_tree(tmp_path / "G", {})
    manifest = write_manifest(tmp_path / "manifest.csv", [(source / "_EXPORT", "6", "raw")])
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--analyze"]) == 0


# ---- Modes with '+': the mode where there is an export, the whole folder where there is none
def mixed_tree(root):
    prefix = f"2025/{HOSTILE}"
    make_tree(root, {
        f"{prefix}/edited/_DSC1.NEF": 10,
        f"{prefix}/edited/_DSC1.xmp": 11,
        f"{prefix}/edited/_DSC2.NEF": 12,
        f"{prefix}/edited/_EXPORT/x/_DSC1_v1.jpg": 20,
        f"{prefix}/edited/_OUTTAKES/o.NEF": 30,
        f"{prefix}/edited/_OUTTAKES_KEEP/k.NEF": 31,
        f"{prefix}/not_edited/_DSC5.NEF": 40,
        f"{prefix}/not_edited/_DSC5.JPG": 41,
        f"{prefix}/not_edited/notes.txt": 42,
        f"{prefix}/not_edited/_OUTTAKES/x.NEF": 43,
        f"{prefix}/not_edited/tmp/y.NEF": 44,
        f"{prefix}/not_edited/CaptureOne/Settings166/a.cos": 45,
        f"{prefix}/not_edited/CaptureOne/Cache/p.cop": 46,
    })
    return root / "2025" / HOSTILE, f"2025/{HOSTILE}"


def run_mixed(tmp_path, mode, modes=SHIPPED_MODES):
    folder, prefix = mixed_tree(tmp_path / "G")
    manifest = write_manifest(tmp_path / "manifest.csv", [(folder, mode, "raw")])
    dest = tmp_path / "backup"
    assert mb.main(["--manifest", str(manifest), "--modes", str(modes),
                    "--dest", str(dest), "--yes"]) == 0
    return dest_files(dest), prefix


def has(files, suffix):
    return any(rel.endswith(suffix) for rel in files)


def test_plus_mode_takes_the_whole_folder_where_there_is_no_export(tmp_path):
    files, prefix = run_mixed(tmp_path, "3+")
    for rel in ("not_edited/_DSC5.NEF", "not_edited/_DSC5.JPG", "not_edited/notes.txt",
                "not_edited/_OUTTAKES/x.NEF", "not_edited/tmp/y.NEF",
                "not_edited/CaptureOne/Settings166/a.cos"):
        assert has(files, f"{prefix}/{rel}"), rel
    assert not has(files, "CaptureOne/Cache/p.cop")


def test_plus_mode_follows_its_mode_where_there_is_an_export(tmp_path):
    files, prefix = run_mixed(tmp_path, "3+")
    assert has(files, f"{prefix}/edited/_EXPORT/x/_DSC1_v1.jpg")
    assert has(files, f"{prefix}/edited/_DSC1.NEF")
    assert has(files, f"{prefix}/edited/_DSC2.NEF")                  # mode 2 onwards
    assert has(files, f"{prefix}/edited/_OUTTAKES_KEEP/k.NEF")       # mode 3
    assert not has(files, f"{prefix}/edited/_OUTTAKES/o.NEF")        # mode 4 onwards


def test_a_mode_without_plus_still_leaves_those_folders_out(tmp_path):
    files, prefix = run_mixed(tmp_path, "3")
    assert has(files, f"{prefix}/edited/_DSC1.NEF")
    assert not has(files, f"{prefix}/not_edited/_DSC5.NEF")


def test_every_plus_mode_of_the_table_is_its_mode_with_the_column_on(tmp_path):
    modes = mb.read_modes(str(SHIPPED_MODES))
    plus = [name for name in modes if name.endswith("+")]
    assert plus
    for name in plus:
        base, mixed = modes[name[:-1]], modes[name]
        assert mixed.unexported and not base.unexported
        assert (mixed.unselected, mixed.add_folders, mixed.add_whole, mixed.whole) == (
            base.unselected, base.add_folders, base.add_whole, base.whole)


def test_a_table_without_the_column_keeps_working(tmp_path):
    table = write_modes(tmp_path / "modes.csv", [("1", "no", "", "", "", "no", "old table")])
    assert mb.read_modes(str(table))["1"].unexported is False


def test_exclude_also_works_where_there_is_no_export(tmp_path):
    table = tmp_path / "modes.csv"
    table.write_text("Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,Description\n"
                     "X,no,,,tmp,no,yes,test\n", encoding="utf-8")
    files, prefix = run_mixed(tmp_path, "X", table)
    assert has(files, f"{prefix}/not_edited/_DSC5.NEF")
    assert not has(files, f"{prefix}/not_edited/tmp/y.NEF")


@pytest.mark.parametrize("line,name", [
    ("X,no,,,,no,maybe,t", "invalid yes/no"),
    ("X,no,,,,yes,yes,t", "Whole=yes with Unexported=yes"),
])
def test_a_bad_unexported_value_aborts(tmp_path, line, name):
    table = tmp_path / "modes.csv"
    table.write_text("Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,Description\n"
                     + line + "\n", encoding="utf-8")
    folder = tmp_path / HOSTILE
    folder.mkdir()
    manifest = write_manifest(tmp_path / "manifest.csv", [(folder, "X", "raw")])
    assert mb.main(["--manifest", str(manifest), "--modes", str(table), "--analyze"]) == 2, name


def test_analyze_of_a_plus_mode_matches_the_dry_run(tmp_path, photobackup_log_dir):
    folder, _ = mixed_tree(tmp_path / "G")
    manifest = write_manifest(tmp_path / "manifest.csv", [(folder, "2+", "raw")])
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--analyze"]) == 0
    rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_analysis.csv"))))
    chosen = [row for row in rows if row["chosen"] == "yes"][0]
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(tmp_path / "out"), "--dry-run"]) == 0
    planned = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_plan.csv"))))
    assert sum(int(row["size"]) for row in planned) == int(chosen["bytes"])


# ---- Skip and Only: a line below another leaves that folder out or keeps one type
def nested_run(tmp_path, mode, extra=()):
    folder, prefix = mixed_tree(tmp_path / "G")
    manifest = write_manifest(tmp_path / "manifest.csv",
                              [(folder, "3+", "raw"), (folder / "not_edited", mode, "")])
    dest = tmp_path / "backup"
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(dest), "--yes", *extra]) == 0
    return dest_files(dest), prefix, manifest, dest


def test_skip_leaves_only_that_folder_out(tmp_path, capsys):
    files, prefix, _, _ = nested_run(tmp_path, "skip")
    assert not any("not_edited" in rel for rel in files)
    assert has(files, f"{prefix}/edited/_DSC1.NEF")
    out = capsys.readouterr().out
    assert "folders the manifest leaves out (Skip): 1 folders / 7 files" in out


def test_a_line_below_a_skip_line_is_still_copied(tmp_path):
    folder, prefix = mixed_tree(tmp_path / "G")
    manifest = write_manifest(tmp_path / "manifest.csv", [
        (folder, "skip", ""), (folder / "not_edited", "6", "")])
    dest = tmp_path / "backup"
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--dest", str(dest), "--yes"]) == 0
    files = dest_files(dest)
    assert has(files, f"{prefix}/not_edited/_DSC5.NEF")
    assert not any("/edited/" in rel for rel in files)


def test_skip_after_a_backup_deletes_nothing_and_reports_orphans(tmp_path, photobackup_log_dir):
    folder, prefix = mixed_tree(tmp_path / "G")
    before = snapshot(tmp_path / "G")
    dest = tmp_path / "backup"
    first = write_manifest(tmp_path / "first.csv", [(folder, "3+", "raw")])
    assert mb.main(["--manifest", str(first), "--modes", str(SHIPPED_MODES),
                    "--dest", str(dest), "--yes"]) == 0
    copied = dest_files(dest)
    second = write_manifest(tmp_path / "second.csv",
                            [(folder, "3+", "raw"), (folder / "not_edited", "skip", "")])
    assert mb.main(["--manifest", str(second), "--modes", str(SHIPPED_MODES),
                    "--dest", str(dest), "--yes"]) == 0
    assert dest_files(dest) == copied
    orphans = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_orphans.csv"))))
    assert orphans and {row["reason"] for row in orphans} == {"not_selected"}
    assert snapshot(tmp_path / "G") == before


@pytest.mark.parametrize("mode,kept,gone", [
    ("jpeg-only", ["_DSC5.JPG"], ["_DSC5.NEF", "notes.txt", "a.cos"]),
    ("raw-only", ["_DSC5.NEF", "_OUTTAKES/x.NEF", "CaptureOne/Settings166/a.cos"],
     ["_DSC5.JPG", "notes.txt"]),
    ("jxl-only", [], ["_DSC5.NEF", "_DSC5.JPG"]),
])
def test_only_modes_keep_one_type_in_that_folder(tmp_path, capsys, mode, kept, gone):
    files, prefix, _, _ = nested_run(tmp_path, mode)
    for rel in kept:
        assert has(files, f"{prefix}/not_edited/{rel}"), rel
    for rel in gone:
        assert not has(files, f"{prefix}/not_edited/{rel}"), rel
    assert has(files, f"{prefix}/edited/_DSC1.NEF")
    assert "left out by a mode's Only filter:" in capsys.readouterr().out


def test_analyze_of_skip_and_only_matches_the_dry_run(tmp_path, photobackup_log_dir):
    for mode in ("skip", "jpeg-only"):
        run_dir = tmp_path / mode
        folder, _ = mixed_tree(run_dir / "G")
        manifest = write_manifest(run_dir / "manifest.csv", [(folder, mode, "")])
        assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                        "--analyze"]) == 0
        rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_analysis.csv"))))
        chosen = [row for row in rows if row["chosen"] == "yes"][0]
        assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                        "--dest", str(run_dir / "out"), "--dry-run"]) == 0
        planned = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_plan.csv"))))
        assert sum(int(row["size"]) for row in planned) == int(chosen["bytes"])


def test_skip_cannot_take_an_export_folder_below_another_line(tmp_path):
    folder, _ = mixed_tree(tmp_path / "G")
    manifest = write_manifest(tmp_path / "manifest.csv", [
        (folder, "3+", "raw"), (folder / "edited" / "_EXPORT", "skip", "")])
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--analyze"]) == 2


@pytest.mark.parametrize("line", [
    "X,no,,,,yes,no,nosuchtype,no,t",
    "X,no,,,,no,no,,maybe,t",
    "X,no,,,,yes,no,,yes,t",
    "X,yes,,,,no,no,,yes,t",
    "X,no,,,,no,no,jpeg,yes,t",
])
def test_bad_only_or_skip_values_abort(tmp_path, line):
    table = tmp_path / "modes.csv"
    table.write_text("Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,Only,Skip,"
                     "Description\n" + line + "\n", encoding="utf-8")
    folder = tmp_path / HOSTILE
    folder.mkdir()
    manifest = write_manifest(tmp_path / "manifest.csv", [(folder, "X", "raw")])
    assert mb.main(["--manifest", str(manifest), "--modes", str(table), "--analyze"]) == 2
