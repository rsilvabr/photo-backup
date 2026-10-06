import os
import subprocess
import sys
from pathlib import Path

import pytest

import selective_backup

from conftest import (
    argv_for,
    build_base_tree,
    dest_files,
    make_tree,
    parse_args,
    planned_rels,
)


def test_candidate_prefixes():
    assert selective_backup._candidate_prefixes("DSC_9360_ProPhoto-g22_v1") == [
        "DSC",
        "DSC_9360",
        "DSC_9360_ProPhoto",
        "DSC_9360_ProPhoto-g22",
        "DSC_9360_ProPhoto-g22_v1",
    ]
    assert selective_backup._candidate_prefixes("_DSC6489_x") == ["_DSC6489", "_DSC6489_x"]
    assert selective_backup._candidate_prefixes("_DSC6489") == ["_DSC6489"]
    assert selective_backup._candidate_prefixes("000037") == ["000037"]


def test_key_nikon_underscore(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/DSC_9360.NEF": 10,
        "G/2025/shoot/DSC_9360.xmp": 11,
        "G/2025/shoot/DSC_936.NEF": 12,
        "G/2025/shoot/_EXPORT/x/DSC_9360_ProPhoto-g22_v1.jxl": 20,
    })
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(tmp_path / "G" / "2025" / "shoot", dest, tmp_path, "--yes")) == 0
    files = dest_files(dest)
    assert "G/2025/shoot/DSC_9360.NEF" in files
    assert "G/2025/shoot/DSC_9360.xmp" in files
    assert "G/2025/shoot/_EXPORT/x/DSC_9360_ProPhoto-g22_v1.jxl" in files
    assert "G/2025/shoot/DSC_936.NEF" not in files


def test_key_leading_underscore_and_nxstudio(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC6491.NEF": 10,
        "G/2025/shoot/_DSC6491.JPG": 11,
        "G/2025/shoot/_DSC6491.xmp": 12,
        "G/2025/shoot/_DSC6491_NXstudio.xmp": 13,
        "G/2025/shoot/_EXPORT/x/_DSC6491_NXstudio_ProPhoto-g22_v1.jxl": 20,
    })
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(tmp_path / "G" / "2025" / "shoot", dest, tmp_path, "--yes")) == 0
    files = dest_files(dest)
    for name in ("_DSC6491.NEF", "_DSC6491.JPG", "_DSC6491.xmp", "_DSC6491_NXstudio.xmp"):
        assert f"G/2025/shoot/{name}" in files


def test_key_shortest_anchor_wins(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/DSC0001.NEF": 10,
        "G/2025/shoot/DSC0001_Nxstudio_v1.tiff": 11,
        "G/2025/shoot/_EXPORT/x/DSC0001_Nxstudio_v1_sRGB.jpg": 20,
    })
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(tmp_path / "G" / "2025" / "shoot", dest, tmp_path, "--yes")) == 0
    files = dest_files(dest)
    assert "G/2025/shoot/DSC0001.NEF" in files
    assert "G/2025/shoot/DSC0001_Nxstudio_v1.tiff" in files


def test_formats_fallback_per_photo(tmp_path):
    build_base_tree(tmp_path)
    dest = tmp_path / "out"
    source = tmp_path / "G" / "2025" / "shoot"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    files = dest_files(dest)
    exports = {name for name in files if "_EXPORT/" in name}
    assert exports == {
        "G/2025/shoot/_EXPORT/16B_JXL/_DSC6489_ProPhoto-g22_v1.jxl",
        "G/2025/shoot/_EXPORT/JXL_mobile/_DSC6489_sRGB_v1.jxl",
        "G/2025/shoot/_EXPORT/16B_JXL/_DSC6491_NXstudio_ProPhoto-g22_v1.jxl",
        "G/2025/shoot/_EXPORT/16B_JXL/DSC_9360_ProPhoto-g22_v1.jxl",
        "G/2025/shoot/_EXPORT/sRGB/DSC0001_Nxstudio_v1_sRGB.jpg",
        "G/2025/shoot/_EXPORT/WEB/000037.jpg",
    }
    assert "G/2025/shoot/_EXPORT/WEB/notes.txt" not in files
    assert "G/2025/shoot/_EXPORT/CHECK/_DSC6491_v1_CHECK.jpg" not in files
    assert "G/2025/shoot/_EXPORT/sRGB/_DSC6489_sRGB_v1.jpg" not in files


def test_formats_multiple_and_all(tmp_path):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025" / "shoot"
    dest_multi = tmp_path / "multi"
    assert selective_backup.main(
        argv_for(source, dest_multi, tmp_path, "--yes", "--export-formats", "jxl+jpg")) == 0
    exports = {name for name in dest_files(dest_multi) if "_EXPORT/" in name}
    assert len(exports) == 8
    assert "G/2025/shoot/_EXPORT/WEB/notes.txt" not in exports
    dest_all = tmp_path / "all"
    assert selective_backup.main(
        argv_for(source, dest_all, tmp_path, "--yes", "--export-formats", "all")) == 0
    exports = {name for name in dest_files(dest_all) if "_EXPORT/" in name}
    assert len(exports) == 9
    assert "G/2025/shoot/_EXPORT/WEB/notes.txt" in exports


@pytest.mark.parametrize("spec", ["jxl>tif+jpg", "bmp", ""])
def test_formats_invalid(tmp_path, spec):
    build_base_tree(tmp_path)
    dest = tmp_path / "out"
    source = tmp_path / "G" / "2025" / "shoot"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes", "--export-formats", spec)) == 2
    assert not dest.exists()


def test_exclude_export_subfolders(tmp_path):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--yes",
        "--export-formats", "jxl", "--exclude-export-subfolders", "JXL_mobile")) == 0
    files = dest_files(dest)
    assert "G/2025/shoot/_EXPORT/16B_JXL/_DSC6489_ProPhoto-g22_v1.jxl" in files
    assert "G/2025/shoot/_EXPORT/JXL_mobile/_DSC6489_sRGB_v1.jxl" not in files
    assert "G/2025/shoot/_DSC6489.NEF" in files
    dest2 = tmp_path / "out2"
    assert selective_backup.main(argv_for(
        source, dest2, tmp_path, "--yes",
        "--export-formats", "jxl",
        "--exclude-export-subfolders", "16B_JXL;JXL_mobile")) == 0
    files2 = dest_files(dest2)
    assert not any(name.startswith("G/2025/shoot/_EXPORT/") for name in files2)
    assert "G/2025/shoot/_DSC6489.NEF" not in files2
    assert "G/2025/shoot/_DSC6491.NEF" not in files2


def test_export_names_case_insensitive_and_excluded(tmp_path):
    make_tree(tmp_path, {
        "G/2025/a/_DSC1.NEF": 10,
        "G/2025/a/Lightroom_Export/_DSC1_v1.jpg": 11,
        "G/2025/b/_DSC2.NEF": 12,
        "G/2025/b/exported/_DSC2_v1.jpg": 13,
        "G/2025/c/_DSC3.NEF": 14,
        "G/2025/c/C1_Export_WEB/_DSC3_v1.jpg": 15,
        "G/2025/d/_DSC4.NEF": 16,
        "G/2025/d/_EXPORT_OLD/_DSC4_v1.jpg": 17,
    })
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(
        source, dest, tmp_path, "--yes", "--exclude-export-names", "_EXPORT_OLD")) == 0
    files = dest_files(dest)
    assert "G/2025/a/Lightroom_Export/_DSC1_v1.jpg" in files
    assert "G/2025/b/exported/_DSC2_v1.jpg" in files
    assert "G/2025/c/C1_Export_WEB/_DSC3_v1.jpg" in files
    assert "G/2025/d/_EXPORT_OLD/_DSC4_v1.jpg" not in files
    args = parse_args(source, dest, tmp_path, "--exclude-export-names", "_EXPORT_OLD")
    planned = selective_backup.plan(args)
    assert planned.stats.shoots == 3
    unexported_paths = [Path(folder.path).name for folder in planned.unexported_folders]
    assert "d" in unexported_paths


def test_capture_one_cache(tmp_path):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    files = dest_files(dest)
    assert "G/2025/shoot/CaptureOne/Settings166/_DSC6489.NEF.cos" in files
    assert "G/2025/shoot/CaptureOne/Cache/Proxies/a.cop" not in files
    dest2 = tmp_path / "out2"
    assert selective_backup.main(argv_for(source, dest2, tmp_path, "--yes", "--c1-cache")) == 0
    files2 = dest_files(dest2)
    assert "G/2025/shoot/CaptureOne/Settings166/_DSC6489.NEF.cos" in files2
    assert "G/2025/shoot/CaptureOne/Cache/Proxies/a.cop" in files2
    assert not any("_OUTTAKES/CaptureOne" in name for name in files)
    assert not any("_OUTTAKES/CaptureOne" in name for name in files2)


def test_nksc_param_copied(tmp_path):
    build_base_tree(tmp_path)
    dest = tmp_path / "out"
    source = tmp_path / "G" / "2025" / "shoot"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert "G/2025/shoot/NKSC_PARAM/_DSC6491.NEF.nksc" in dest_files(dest)


def test_inside_shoot_never_copied(tmp_path):
    build_base_tree(tmp_path)
    dest = tmp_path / "out"
    source = tmp_path / "G" / "2025" / "shoot"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    files = dest_files(dest)
    assert "G/2025/shoot/_OUTTAKES/_DSC6490.NEF" not in files
    assert "G/2025/shoot/OLD_TIFFs/old.tif" not in files


def test_unselected_modes(tmp_path):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025" / "shoot"
    dest_skip = tmp_path / "skip"
    assert selective_backup.main(argv_for(source, dest_skip, tmp_path, "--yes")) == 0
    files = dest_files(dest_skip)
    assert "G/2025/shoot/_DSC6495.NEF" not in files
    assert "G/2025/shoot/_DSC6495.JPG" not in files
    assert "G/2025/shoot/_DSC6495.xmp" not in files
    dest_raw = tmp_path / "raw"
    assert selective_backup.main(argv_for(source, dest_raw, tmp_path, "--yes", "--unselected", "raw")) == 0
    files = dest_files(dest_raw)
    assert "G/2025/shoot/_DSC6495.NEF" in files
    assert "G/2025/shoot/_DSC6495.xmp" in files
    assert "G/2025/shoot/_DSC6495.JPG" not in files
    dest_all = tmp_path / "allsel"
    assert selective_backup.main(argv_for(source, dest_all, tmp_path, "--yes", "--unselected", "all")) == 0
    files = dest_files(dest_all)
    assert "G/2025/shoot/_DSC6495.NEF" in files
    assert "G/2025/shoot/_DSC6495.JPG" in files
    assert "G/2025/shoot/_DSC6495.xmp" in files


def test_unexported_skip_lists_and_copy(tmp_path, photobackup_log_dir):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    assert not any(name.startswith("G/2025/plain/") for name in dest_files(dest))
    from conftest import latest_csv

    unexported = latest_csv(photobackup_log_dir, "_unexported.csv")
    assert "plain" in unexported
    dest2 = tmp_path / "out2"
    assert selective_backup.main(argv_for(source, dest2, tmp_path, "--yes", "--unexported", "copy")) == 0
    files = dest_files(dest2)
    assert "G/2025/plain/IMG_0001.JPG" in files
    assert "G/2025/plain/CaptureOne/Settings166/s.cos" in files
    assert "G/2025/plain/CaptureOne/Cache/c.cop" not in files
    assert "G/2025/legacy/IMG_9999.CR2" in files


def test_nested_session(tmp_path):
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025"
    dest = tmp_path / "out"
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    files = dest_files(dest)
    assert "G/2025/legacy/1. Selected/Lightroom_Export/IMG_3132.jpg" in files
    assert "G/2025/legacy/1. Selected/IMG_3132.CR2" in files


def test_dest_layout_same_for_drive_or_year_root(tmp_path):
    build_base_tree(tmp_path)
    parser = selective_backup._build_parser()
    dest = tmp_path / "unused"
    args_drive = parser.parse_args(["--source", str(tmp_path / "G"), "--dest", str(dest)])
    args_year = parser.parse_args(["--source", str(tmp_path / "G" / "2025"), "--dest", str(dest)])
    rels_drive = set(planned_rels(selective_backup.plan(args_drive)))
    rels_year = set(planned_rels(selective_backup.plan(args_year)))
    assert rels_drive == rels_year
    assert any(rel.endswith("G/2025/shoot/_DSC6489.NEF") for rel in rels_drive)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows long paths")
def test_long_and_unicode_paths(tmp_path):
    session = "250129_吾妻山公園_花 [FINAL]"
    deep = "/".join("d" * 55 for _ in range(4))
    make_tree(tmp_path, {
        f"G/2025/{session}/{deep}/_DSC1.NEF": 100,
        f"G/2025/{session}/{deep}/_EXPORT/X/_DSC1_v1.jpg": 101,
    })
    dest = tmp_path / "out"
    source = tmp_path / "G" / "2025" / session
    assert selective_backup.main(argv_for(source, dest, tmp_path, "--yes")) == 0
    copied = dest / "G" / "2025" / session / deep / "_EXPORT" / "X" / "_DSC1_v1.jpg"
    assert copied.exists()
    assert len(str(copied)) > 300


def test_settings_are_read_not_hardcoded(tmp_path, monkeypatch):
    root1 = tmp_path / "t1"
    make_tree(root1, {
        "G/2025/a/_DSC1.NEF": 10,
        "G/2025/a/_saida/_DSC1_v1.jpg": 11,
        "G/2025/a/_EXPORT/_DSC1_v1.jpg": 12,
    })
    monkeypatch.setattr(selective_backup, "EXPORT_NAME_CONTAINS", "saida")
    dest1 = root1 / "out"
    assert selective_backup.main(argv_for(root1 / "G" / "2025" / "a", dest1, root1, "--yes")) == 0
    files1 = dest_files(dest1)
    assert "G/2025/a/_saida/_DSC1_v1.jpg" in files1
    assert "G/2025/a/_EXPORT/_DSC1_v1.jpg" not in files1

    root2 = tmp_path / "t2"
    make_tree(root2, {
        "G/2025/b/DSC1.NEF": 10,
        "G/2025/b/_EXPORT/DSC1_v1.jpg": 20,
        "G/2025/b/CaptureOne/Cache/f.bin": 30,
        "G/2025/b/CaptureOne/Proxies/p.bin": 31,
    })
    monkeypatch.setattr(selective_backup, "EXPORT_NAME_CONTAINS", "export")
    monkeypatch.setattr(selective_backup, "CAPTUREONE_CACHE_NAMES", "Proxies")
    dest2 = root2 / "out"
    assert selective_backup.main(argv_for(root2 / "G" / "2025" / "b", dest2, root2, "--yes")) == 0
    files2 = dest_files(dest2)
    assert "G/2025/b/CaptureOne/Cache/f.bin" in files2
    assert "G/2025/b/CaptureOne/Proxies/p.bin" not in files2

    root3 = tmp_path / "t3"
    make_tree(root3, {
        "G/2025/c/_DSC1.NEF": 10,
        "G/2025/c/_EXPORT/x/_DSC1_v1.jpg": 20,
        "G/2025/c/_EXPORT/x/_DSC1_v1.tif": 21,
    })
    monkeypatch.setattr(selective_backup, "EXPORT_FORMATS", "tif")
    dest3 = root3 / "out"
    assert selective_backup.main(argv_for(root3 / "G" / "2025" / "c", dest3, root3, "--yes")) == 0
    files3 = dest_files(dest3)
    assert "G/2025/c/_EXPORT/x/_DSC1_v1.tif" in files3
    assert "G/2025/c/_EXPORT/x/_DSC1_v1.jpg" not in files3


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_symlink_not_followed(tmp_path):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 10,
        "G/2025/shoot/_EXPORT/X/_DSC1_v1.jpg": 11,
        "G/2025/other/IMG_9.JPG": 12,
    })
    link = tmp_path / "G" / "2025" / "shoot" / "linkdir"
    target = tmp_path / "G" / "2025" / "other"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True)
        if result.returncode != 0:
            pytest.skip("cannot create a junction or symlink")
    args = parse_args(tmp_path / "G" / "2025" / "shoot", tmp_path / "out", tmp_path)
    planned = selective_backup.plan(args)
    assert planned.stats.skipped_links >= 1
    assert not any("linkdir" in item.rel for item in planned.files)
