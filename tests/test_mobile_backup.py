import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mobile_backup as mb  # noqa: E402
from conftest import make_tree, snapshot  # noqa: E402


@pytest.fixture(autouse=True)
def mobile_log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "LOG_DIR", str(tmp_path / "mlogs"))


def fake_shots(table):
    """read_shot_times stand-in: shot time by file name."""
    def reader(paths, log=None):
        return {p: table.get(os.path.basename(p)) for p in paths}
    return reader


def listing(root):
    root = Path(root)
    return sorted(str(p.relative_to(root)).replace("\\", "/")
                  for p in root.rglob("*") if p.is_file() and mb.STATE_FOLDER not in p.parts)


def run(src, dest, *extra):
    return mb.main(["--source", str(src), "--dest", str(dest), *extra])


SHOTS = {
    "_DSC_0001_sRGB_v1.jxl": "2026:06:21 12:26:19.950000",   # camera 2, first
    "_DSC_0002_sRGB_v1.jxl": "2026:06:21 12:30:00.000000",
    "DSC_0100_sRGB_v1.jxl": "2026:06:21 12:28:00.000000",    # camera 1, between the two
}


@pytest.fixture
def archive(tmp_path):
    src = tmp_path / "G" / "2026"
    make_tree(src, {
        "260620-23_Kyoto/1_Z8/_EXPORT/JXL_mobile/DSC_0100_sRGB_v1.jxl": 300,
        "260620-23_Kyoto/2_Z8/_EXPORT/JXL_mobile/_DSC_0001_sRGB_v1.jxl": 100,
        "260620-23_Kyoto/2_Z8/_EXPORT/JXL_mobile/_DSC_0002_sRGB_v1.jxl": 200,
        "260620-23_Kyoto/2_Z8/_EXPORT/16B_JXL/_DSC_0001_ProPhoto-g22_v1.jxl": 900,
        "260620-23_Kyoto/2_Z8/_DSC_0001.NEF": 50,
    })
    return src


def test_album_is_the_dated_event_folder(tmp_path):
    root = tmp_path / "2025"
    nested = root / "251219-31_BRASIL [FINAL]" / "251231_Teresina" / "01_D810" / "_EXPORT" / "JXL_mobile"
    assert mb.album_folder(str(nested), str(root)) == str(root / "251219-31_BRASIL [FINAL]" / "251231_Teresina")
    flat = root / "250101_Event" / "_EXPORT" / "JXL_mobile"
    assert mb.album_folder(str(flat), str(root)) == str(root / "250101_Event")
    undated = root / "Misc" / "_EXPORT" / "JXL_mobile"      # no date: the folder above the export folder
    assert mb.album_folder(str(undated), str(root)) == str(root / "Misc")


def test_first_run_numbers_in_shot_order_across_cameras(archive, tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "read_shot_times", fake_shots(SHOTS))
    dest = tmp_path / "H" / "MOBILE"
    before = snapshot(archive)
    assert run(archive, dest) == 0
    rel = str(archive.relative_to(Path(archive.anchor))).replace("\\", "/")
    album = f"{rel}/260620-23_Kyoto"
    assert listing(dest) == [
        f"{album}/0000__DSC_0001_sRGB_v1.jxl",
        f"{album}/0001_DSC_0100_sRGB_v1.jxl",
        f"{album}/0002__DSC_0002_sRGB_v1.jxl",
    ]
    assert snapshot(archive) == before          # the source is read-only
    assert (dest / f"{album}/0001_DSC_0100_sRGB_v1.jxl").stat().st_size == 300


def _album(dest):
    files = listing(dest)
    return {Path(f).name for f in files}


def test_second_run_changes_nothing(archive, tmp_path, monkeypatch):
    calls = []

    def reader(paths, log=None):
        calls.append(list(paths))
        return fake_shots(SHOTS)(paths)
    monkeypatch.setattr(mb, "read_shot_times", reader)
    dest = tmp_path / "H"
    assert run(archive, dest) == 0
    mtimes = {p: p.stat().st_mtime_ns for p in dest.rglob("*.jxl")}
    assert run(archive, dest) == 0
    assert {p: p.stat().st_mtime_ns for p in dest.rglob("*.jxl")} == mtimes
    assert len(calls) == 1                      # unchanged files are not read again


def test_new_photo_in_the_middle_renumbers_by_rename(archive, tmp_path, monkeypatch):
    shots = dict(SHOTS, **{"_DSC_0003_sRGB_v1.jxl": "2026:06:21 12:27:00.000000"})
    monkeypatch.setattr(mb, "read_shot_times", fake_shots(shots))
    dest = tmp_path / "H"
    assert run(archive, dest) == 0
    make_tree(archive, {"260620-23_Kyoto/2_Z8/_EXPORT/JXL_mobile/_DSC_0003_sRGB_v1.jxl": 400})
    assert run(archive, dest) == 0
    assert _album(dest) == {"0000__DSC_0001_sRGB_v1.jxl", "0001__DSC_0003_sRGB_v1.jxl",
                            "0002_DSC_0100_sRGB_v1.jxl", "0003__DSC_0002_sRGB_v1.jxl"}
    by_name = {p.name: p.stat().st_size for p in dest.rglob("*.jxl")}
    assert by_name["0002_DSC_0100_sRGB_v1.jxl"] == 300     # the renamed file is the right photo
    assert not [p for p in dest.rglob("*") if p.name.endswith((mb.TEMP_SUFFIX, mb.RENAME_SUFFIX))]


def test_photo_gone_from_the_source_is_removed(archive, tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "read_shot_times", fake_shots(SHOTS))
    dest = tmp_path / "H"
    assert run(archive, dest) == 0
    (archive / "260620-23_Kyoto/2_Z8/_EXPORT/JXL_mobile/_DSC_0001_sRGB_v1.jxl").unlink()
    assert run(archive, dest) == 0
    assert _album(dest) == {"0000_DSC_0100_sRGB_v1.jxl", "0001__DSC_0002_sRGB_v1.jxl"}


def test_event_renamed_moves_the_album(archive, tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "read_shot_times", fake_shots(SHOTS))
    dest = tmp_path / "H"
    assert run(archive, dest) == 0
    os.rename(archive / "260620-23_Kyoto", archive / "260620-23_Kyoto [FINAL]")
    assert run(archive, dest) == 0
    folders = {Path(f).parent.name for f in listing(dest)}
    assert folders == {"260620-23_Kyoto [FINAL]"}
    assert not any(p.name == "260620-23_Kyoto" for p in dest.rglob("*"))   # empty album folder removed


def test_changed_source_is_copied_again(archive, tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "read_shot_times", fake_shots(SHOTS))
    dest = tmp_path / "H"
    assert run(archive, dest) == 0
    src = archive / "260620-23_Kyoto/1_Z8/_EXPORT/JXL_mobile/DSC_0100_sRGB_v1.jxl"
    src.write_bytes(b"x" * 333)
    assert run(archive, dest) == 0
    assert {p.name: p.stat().st_size for p in dest.rglob("*.jxl")}["0001_DSC_0100_sRGB_v1.jxl"] == 333


def test_foreign_file_in_the_way_skips_the_album(archive, tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "read_shot_times", fake_shots(SHOTS))
    dest = tmp_path / "H"
    rel = archive.relative_to(Path(archive.anchor))
    blocker = dest / rel / "260620-23_Kyoto" / "0001_DSC_0100_sRGB_v1.jxl"
    other = dest / rel / "260620-23_Kyoto" / "my_note.txt"
    make_tree(blocker.parent, {blocker.name: "mine", other.name: "keep me"})
    assert run(archive, dest) == 1
    assert blocker.read_text() == "mine"
    assert other.read_text() == "keep me"
    assert sorted(p.name for p in blocker.parent.iterdir()) == ["0001_DSC_0100_sRGB_v1.jxl", "my_note.txt"]


def test_files_we_did_not_write_are_never_deleted(archive, tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "read_shot_times", fake_shots(SHOTS))
    dest = tmp_path / "H"
    assert run(archive, dest) == 0
    album = next(dest.rglob("0000_*")).parent
    (album / "phone_edit.jpg").write_text("x")
    shutil.rmtree(archive / "260620-23_Kyoto")
    assert run(archive, dest) == 0
    assert [p.name for p in album.iterdir()] == ["phone_edit.jpg"]


def test_photos_without_a_date_come_last(archive, tmp_path, monkeypatch):
    shots = dict(SHOTS)
    shots["_DSC_0001_sRGB_v1.jxl"] = None
    monkeypatch.setattr(mb, "read_shot_times", fake_shots(shots))
    dest = tmp_path / "H"
    assert run(archive, dest) == 0
    assert _album(dest) == {"0000_DSC_0100_sRGB_v1.jxl", "0001__DSC_0002_sRGB_v1.jxl",
                            "0002__DSC_0001_sRGB_v1.jxl"}


def test_dry_run_changes_nothing(archive, tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "read_shot_times", fake_shots(SHOTS))
    dest = tmp_path / "H"
    assert run(archive, dest, "--dry-run") == 0
    assert not dest.exists()


def test_destination_inside_a_source_is_refused(archive, monkeypatch):
    monkeypatch.setattr(mb, "read_shot_times", fake_shots(SHOTS))
    assert run(archive, archive / "mirror") == 2
    assert not (archive / "mirror").exists()


def test_wide_album_gets_more_digits():
    entries = [(f"C:\\a\\{i:05d}.jxl", None) for i in range(10001)]
    names = mb.numbered_names(entries)
    assert names["C:\\a\\00000.jxl"] == "00000_00000.jxl"
    assert names["C:\\a\\10000.jxl"] == "10000_10000.jxl"


@pytest.mark.skipif(shutil.which(mb.EXIFTOOL) is None, reason="exiftool not installed")
def test_real_exiftool_shot_order_with_japanese_folder(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    mobile = tmp_path / "src" / "2026" / "260808_伊勢原向日葵" / "3_17T_Pro" / "_EXPORT" / "JXL_mobile"
    mobile.mkdir(parents=True)
    times = {"a.jpg": "2026:08:09 13:47:27", "b.jpg": "2026:08:09 13:29:57", "c.jpg": "2026:08:09 13:29:57"}
    subsec = {"a.jpg": "", "b.jpg": "50", "c.jpg": "07"}
    args = tmp_path / "write.args"
    lines = ["-charset", "filename=utf8", "-overwrite_original", "-q"]
    for name, when in times.items():
        pil.new("RGB", (8, 8)).save(mobile / name)
        lines += [f"-DateTimeOriginal={when}"]
        if subsec[name]:
            lines += [f"-SubSecTimeOriginal={subsec[name]}"]
        lines += [str(mobile / name), "-execute"]
    args.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run([mb.EXIFTOOL, "-@", str(args)], check=True, capture_output=True)
    dest = tmp_path / "H"
    assert mb.main(["--source", str(tmp_path / "src"), "--dest", str(dest)]) == 0
    album = [p for p in dest.rglob("*.jpg")]
    assert {p.parent.name for p in album} == {"260808_伊勢原向日葵"}
    assert sorted(p.name for p in album) == ["0000_c.jpg", "0001_b.jpg", "0002_a.jpg"]
