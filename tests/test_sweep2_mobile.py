"""mobile_backup.py 0.1.3: the findings of the second sweep (N5, N17-N22, O6).

Everything runs on synthetic trees under tmp_path; exiftool is a stand-in.
"""
import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mobile_backup as mb  # noqa: E402
from conftest import make_tree, snapshot  # noqa: E402

ALBUM = "260620_Kyoto/1_Z8/_EXPORT/JXL_mobile/"


@pytest.fixture(autouse=True)
def mobile_log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "LOG_DIR", str(tmp_path / "mlogs"))


@pytest.fixture(autouse=True)
def no_exiftool(monkeypatch):
    monkeypatch.setattr(mb, "read_shot_times", no_dates)


def no_dates(paths, log=None):
    return {path: None for path in paths}


def run(src, dest, *extra):
    return mb.main(["--source", str(src), "--dest", str(dest), *extra])


def mirror(dest):
    """File names of the mirror, without the state folder."""
    return sorted(p.name for p in Path(dest).rglob("*")
                  if p.is_file() and mb.STATE_FOLDER not in p.parts)


def mirror_paths(dest):
    return sorted(str(p.relative_to(dest)).replace("\\", "/") for p in Path(dest).rglob("*")
                  if p.is_file() and mb.STATE_FOLDER not in p.parts)


def state_files(dest):
    path = Path(dest) / mb.STATE_FOLDER / mb.STATE_FILE
    return json.loads(path.read_text(encoding="utf-8"))["files"]


def count_copies(monkeypatch):
    """Every real copy of the following runs, as (source name)."""
    done = []
    real = mb._copy

    def copy(source, target):
        done.append(os.path.basename(source))
        return real(source, target)

    monkeypatch.setattr(mb, "_copy", copy)
    return done


def writable_again(*roots):
    for root in roots:
        for path in Path(root).rglob("*"):
            if path.is_file():
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


# ---- N5: the state records what was really copied -----------------------------------------
def test_n5_a_photo_exported_again_between_the_scan_and_the_copy(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100, ALBUM + "b.jxl": 200, ALBUM + "c.jxl": 300})
    target = src / ALBUM / "b.jxl"
    read = []

    def exporter_runs_meanwhile(paths, log=None):
        # exiftool runs between the scan and the copies; the exporter rewrites b.jxl then
        target.write_bytes(b"y" * 555)
        return {p: None for p in paths}

    def reader(paths, log=None):
        read.append(sorted(os.path.basename(p) for p in paths))
        return {p: None for p in paths}

    monkeypatch.setattr(mb, "read_shot_times", exporter_runs_meanwhile)
    assert run(src, dest) == 0
    files = state_files(dest)
    record = files[str(target)]
    assert record["size"] == 555                    # what is on the mirror, not what the scan saw
    assert record["mtime_ns"] == target.stat().st_mtime_ns
    assert "shot" not in record                     # the time read was the old file's
    assert "shot" in files[str(src / ALBUM / "a.jxl")]

    monkeypatch.setattr(mb, "read_shot_times", reader)
    assert run(src, dest) == 0                      # 0.1.2: SKIPPED album, exit 1, for good
    assert read == [["b.jxl"]]
    assert "shot" in state_files(dest)[str(target)]
    make_tree(src, {ALBUM + "d.jxl": 400})
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl", "0002_c.jxl", "0003_d.jxl"]
    assert next(dest.rglob("0001_b.jxl")).stat().st_size == 555


def test_n5_a_source_that_changes_during_its_copy_is_an_error(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100, ALBUM + "b.jxl": 200})
    target = src / ALBUM / "b.jxl"
    real = shutil.copyfileobj
    grown = []

    def copyfileobj(reader, writer, *args):
        real(reader, writer, *args)
        if reader.name.endswith("b.jxl") and not grown:
            grown.append(True)
            with open(target, "ab") as handle:      # the exporter is still writing it
                handle.write(b"more")

    with monkeypatch.context() as patch:
        patch.setattr(mb.shutil, "copyfileobj", copyfileobj)
        assert run(src, dest) == 1
    assert mirror(dest) == ["0000_a.jxl"]           # no half b, no temp file
    assert str(target) not in state_files(dest)
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl"]
    assert next(dest.rglob("0001_b.jxl")).read_bytes() == target.read_bytes()


def test_n5_the_copy_keeps_the_date_of_the_source(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    os.utime(src / ALBUM / "a.jxl", ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    before = snapshot(src)
    assert run(src, dest) == 0
    assert next(dest.rglob("0000_a.jxl")).stat().st_mtime_ns == 1_600_000_000_000_000_000
    assert snapshot(src) == before                  # the source is read-only


# ---- N17: a state entry with nothing behind it ---------------------------------------------
def test_n17_a_stale_entry_does_not_delete_the_file_of_another_photo(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    album = "260620_Kyoto/2_Z8/_EXPORT/JXL_mobile/"
    make_tree(src, {album + "a.jxl": 100, album + "b.jxl": 200})
    assert run(src, dest) == 0
    old_keys = set(state_files(dest))
    # the album is removed from the mirror by hand, the camera folder renamed in the archive
    shutil.rmtree(next(dest.rglob("0000_a.jxl")).parent)
    os.rename(src / "260620_Kyoto" / "2_Z8", src / "260620_Kyoto" / "2_Z8ii")
    for _ in range(3):                              # 0.1.2: the second of these emptied the album
        assert run(src, dest) == 0
        assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl"]
    assert not old_keys & set(state_files(dest))    # the entries with nothing behind them are gone


def test_n17_a_state_that_already_holds_one_name_twice(tmp_path):
    """What 0.1.2 left after that scenario: two entries, one mirror file."""
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    album = "260620_Kyoto/2_Z8ii/_EXPORT/JXL_mobile/"
    make_tree(src, {album + "a.jxl": 100, album + "b.jxl": 200})
    assert run(src, dest) == 0
    path = dest / mb.STATE_FOLDER / mb.STATE_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    live = dict(data["files"])
    for source, record in live.items():
        data["files"][source.replace("2_Z8ii", "2_Z8")] = dict(record)   # the folder's old name
    path.write_text(json.dumps(data), encoding="utf-8")
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl"]
    assert set(state_files(dest)) == set(live)
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl"]


def test_n17_nothing_is_forgotten_when_a_source_could_not_be_read(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100, "260701_Nara/_EXPORT/JXL_mobile/n.jxl": 50})
    assert run(src, dest) == 0
    before = state_files(dest)
    shutil.rmtree(next(dest.rglob("0000_a.jxl")).parent)     # its mirror file is gone...
    real = os.scandir

    def scandir(path="."):
        text = os.fspath(path)
        if "260620_Kyoto" in text and text.rstrip("\\/").endswith("_EXPORT"):
            raise PermissionError(13, "Access is denied", text)
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)     # ... and its source cannot be listed
    assert run(src, dest) == 1
    monkeypatch.setattr(os, "scandir", real)
    assert set(state_files(dest)) == set(before)    # not proof that the photo is gone


# ---- N18: the same source in another case --------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="a case-insensitive file system")
def test_n18_the_same_source_typed_in_another_case(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + f"p{i}.jxl": 100 + i for i in range(5)})
    read = []

    def reader(paths, log=None):
        read.append(len(paths))
        return {p: None for p in paths}

    monkeypatch.setattr(mb, "read_shot_times", reader)
    assert run(src, dest) == 0
    before = mirror_paths(dest)
    copies = count_copies(monkeypatch)
    other = tmp_path / "g" / "2026"
    assert os.path.samefile(src, other) and str(other) != str(src)
    assert run(other, dest) == 0
    assert read == [5]                              # 0.1.2: [5, 5]
    assert copies == []                             # 0.1.2: 5 deleted and 5 copied again
    assert mirror_paths(dest) == before
    assert not [p for p in dest.rglob("*") if p.name.endswith((mb.TEMP_SUFFIX, mb.RENAME_SUFFIX))]
    assert len(state_files(dest)) == 5
    # and a photo deleted from the source still leaves the mirror, under either spelling
    (src / ALBUM / "p4.jxl").unlink()
    assert run(src, dest) == 0
    assert mirror(dest) == [f"000{i}_p{i}.jxl" for i in range(4)]


# ---- N19: read-only -----------------------------------------------------------------------
def test_n19_a_read_only_source_does_not_make_a_read_only_copy(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100, ALBUM + "b.jxl": 200})
    a, b = src / ALBUM / "a.jxl", src / ALBUM / "b.jxl"
    os.chmod(a, stat.S_IREAD)
    os.chmod(b, stat.S_IREAD)
    try:
        assert run(src, dest) == 0
        for path in dest.rglob("*.jxl"):
            assert path.stat().st_mode & stat.S_IWRITE, path.name
        os.chmod(a, stat.S_IREAD | stat.S_IWRITE)
        a.unlink()
        assert run(src, dest) == 0
        assert mirror(dest) == ["0000_b.jxl"]
    finally:
        writable_again(src, dest)


def test_n19_a_read_only_copy_of_an_older_version_is_replaced_and_deleted(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100, ALBUM + "b.jxl": 200, ALBUM + "c.jxl": 300})
    assert run(src, dest) == 0
    try:
        for path in dest.rglob("*.jxl"):            # what 0.1.2 wrote from read-only sources
            os.chmod(path, stat.S_IREAD)
        (src / ALBUM / "b.jxl").write_bytes(b"z" * 222)      # exported again, same number
        (src / ALBUM / "c.jxl").unlink()                     # gone from the archive
        assert run(src, dest) == 0                  # 0.1.2: ERROR delete, a read-only temp left
        assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl"]
        assert next(dest.rglob("0001_b.jxl")).stat().st_size == 222
        assert run(src, dest) == 0                  # 0.1.2: a traceback in every later run
        # a.jxl was not touched: nothing asked for it, it keeps its attribute
        assert not next(dest.rglob("0000_a.jxl")).stat().st_mode & stat.S_IWRITE
    finally:
        writable_again(dest)


def test_n19_a_read_only_leftover_temp_does_not_end_the_run(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    assert run(src, dest) == 0
    album = next(dest.rglob("0000_a.jxl")).parent
    leftover = album / ("0001_b.jxl" + mb.TEMP_SUFFIX)
    leftover.write_bytes(b"half")
    os.chmod(leftover, stat.S_IREAD)
    try:
        assert run(src, dest) == 0                  # 0.1.2: PermissionError, a traceback
        assert mirror(dest) == ["0000_a.jxl"]
    finally:
        writable_again(dest)


def test_n19_a_read_only_file_that_is_not_ours_stays_as_it_is(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    rel = src.relative_to(Path(src.anchor))
    foreign = dest / rel / "260620_Kyoto" / "0000_a.jxl"
    note = dest / rel / "260620_Kyoto" / "note.txt"
    make_tree(foreign.parent, {foreign.name: "mine", note.name: "keep"})
    os.chmod(foreign, stat.S_IREAD)
    os.chmod(note, stat.S_IREAD)
    try:
        assert run(src, dest) == 1                  # the album is skipped
        for path, text in ((foreign, "mine"), (note, "keep")):
            assert path.read_text() == text
            assert not path.stat().st_mode & stat.S_IWRITE
    finally:
        writable_again(dest)


# ---- N20: no traceback ----------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="drive letters")
def test_n20_a_destination_drive_that_is_not_connected(tmp_path, monkeypatch, capsys):
    letters = [letter for letter in "QRTUVWXYZ" if not os.path.exists(letter + ":\\")]
    if not letters:
        pytest.skip("every drive letter is in use")
    src = tmp_path / "G" / "2026"
    make_tree(src, {ALBUM + "a.jxl": 100})
    read = []

    def reader(paths, log=None):
        read.append(len(paths))
        return {p: None for p in paths}

    monkeypatch.setattr(mb, "read_shot_times", reader)
    for extra in ((), ("--dry-run",)):
        assert run(src, letters[0] + ":\\MOBILE", *extra) == 2      # 0.1.2: a traceback
    assert read == []                               # asked before the shot times are read
    assert "destination" in capsys.readouterr().out


def test_n20_a_destination_that_is_a_file(tmp_path):
    src = tmp_path / "G" / "2026"
    make_tree(src, {ALBUM + "a.jxl": 100})
    make_tree(tmp_path, {"H": "a file"})
    assert run(src, tmp_path / "H" / "MOBILE") == 2
    assert (tmp_path / "H").read_text() == "a file"


@pytest.mark.skipif(sys.platform != "win32", reason="an open file cannot be renamed on Windows")
def test_n20_a_mirror_file_open_elsewhere_when_it_is_renumbered(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "b.jxl": 100, ALBUM + "c.jxl": 200, ALBUM + "d.jxl": 300})
    assert run(src, dest) == 0
    copies = count_copies(monkeypatch)
    make_tree(src, {ALBUM + "a.jxl": 50})           # renumbers b, c and d
    with open(next(dest.rglob("0001_c.jxl")), "rb"):         # a viewer, a sync tool
        assert run(src, dest) == 1                  # 0.1.2: a traceback, files left as *.mbren
        assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl", "0001_c.jxl", "0003_d.jxl"]
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl", "0002_c.jxl", "0003_d.jxl"]
    assert copies == ["a.jxl"]                      # renamed, never copied again
    assert next(dest.rglob("0002_c.jxl")).stat().st_size == 200


def test_n20_a_rename_that_fails_half_way_goes_back_to_its_old_name(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "b.jxl": 100, ALBUM + "c.jxl": 200})
    assert run(src, dest) == 0
    make_tree(src, {ALBUM + "a.jxl": 50})
    real = mb._rename_free

    def rename_free(old, new):
        if new.endswith("0002_c.jxl"):
            raise PermissionError(13, "Access is denied", new)
        real(old, new)

    with monkeypatch.context() as patch:
        patch.setattr(mb, "_rename_free", rename_free)
        assert run(src, dest) == 1
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl", "0001_c.jxl"]
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl", "0002_c.jxl"]
    assert next(dest.rglob("0002_c.jxl")).stat().st_size == 200


def test_n20_a_rename_never_lands_on_a_file(tmp_path):
    make_tree(tmp_path, {"old.jxl": "ours", "new.jxl": "someone else's"})
    with pytest.raises(OSError):
        mb._rename_free(str(tmp_path / "old.jxl"), str(tmp_path / "new.jxl"))
    assert (tmp_path / "new.jxl").read_text() == "someone else's"
    assert (tmp_path / "old.jxl").read_text() == "ours"


def test_n20_a_state_file_that_cannot_be_written_is_an_error_line(tmp_path, monkeypatch, capsys):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})

    def save_state(dest, files):
        raise OSError(5, "the device is not ready")

    monkeypatch.setattr(mb, "save_state", save_state)
    assert run(src, dest) == 1                      # 0.1.2: a traceback
    assert "state file" in capsys.readouterr().out


# ---- N21: the album does not depend on --source ---------------------------------------------
def test_n21_a_source_inside_an_event_keeps_the_album(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {
        "260620_Kyoto/1_Z8/_EXPORT/JXL_mobile/a.jxl": 100,
        "260620_Kyoto/2_Z8/_EXPORT/JXL_mobile/b.jxl": 200,
        "260620_Kyoto/2_Z8/_EXPORT/JXL_mobile/c.jxl": 300,
        "Misc/_EXPORT/JXL_mobile/m.jxl": 50,
    })
    assert run(src, dest) == 0
    before = mirror_paths(dest)
    assert {Path(p).parent.name for p in before} == {"260620_Kyoto", "Misc"}
    copies = count_copies(monkeypatch)
    for narrow in (src / "260620_Kyoto" / "2_Z8", src / "260620_Kyoto" / "2_Z8" / "_EXPORT",
                   src / "Misc" / "_EXPORT"):
        assert run(narrow, dest) == 0
        assert mirror_paths(dest) == before         # 0.1.2: b and c moved to 260620_Kyoto\2_Z8
    assert copies == []
    assert run(src, dest) == 0
    assert mirror_paths(dest) == before


def test_n21_a_part_of_an_album_is_numbered_inside_the_whole_album(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {
        "260620_Kyoto/1_Z8/_EXPORT/JXL_mobile/a.jxl": 100,
        "260620_Kyoto/2_Z8/_EXPORT/JXL_mobile/b.jxl": 200,
        "260620_Kyoto/2_Z8/_EXPORT/JXL_mobile/c.jxl": 300,
    })
    assert run(src, dest) == 0
    copies = count_copies(monkeypatch)
    make_tree(src, {"260620_Kyoto/2_Z8/_EXPORT/JXL_mobile/e.jxl": 400})
    assert run(src / "260620_Kyoto" / "2_Z8", dest) == 0
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl", "0002_c.jxl", "0003_e.jxl"]
    make_tree(src, {"260620_Kyoto/1_Z8/_EXPORT/JXL_mobile/a2.jxl": 150})
    assert run(src / "260620_Kyoto" / "1_Z8", dest) == 0
    # the second camera was not given: its photos are not renumbered by this run
    assert mirror(dest) == ["0000_a.jxl", "0001_a2.jxl", "0001_b.jxl", "0002_c.jxl", "0003_e.jxl"]
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_a.jxl", "0001_a2.jxl", "0002_b.jxl", "0003_c.jxl", "0004_e.jxl"]
    assert copies == ["e.jxl", "a2.jxl"]            # the rest was renamed, never copied again
    assert {Path(p).parent.name for p in mirror_paths(dest)} == {"260620_Kyoto"}


def test_n21_the_album_is_the_same_for_every_source_root(tmp_path):
    root = tmp_path / "2025"
    nested = root / "251219-31_TRIP" / "251231_City" / "01_D810" / "_EXPORT" / "JXL_mobile"
    album = str(root / "251219-31_TRIP" / "251231_City")
    for given in (root, root / "251219-31_TRIP", nested.parent.parent, nested.parent, nested):
        assert mb.album_folder(str(nested), str(given)) == album
    undated = root / "Misc" / "_EXPORT" / "JXL_mobile"
    for given in (root, root / "Misc", undated.parent, undated):
        assert mb.album_folder(str(undated), str(given)) == str(root / "Misc")
    assert mb.album_folder(str(nested)) == album


# ---- N22: the dry run's exit code -----------------------------------------------------------
def test_n22_a_dry_run_with_a_skipped_album_exits_1_like_the_real_run(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    rel = src.relative_to(Path(src.anchor))
    blocker = dest / rel / "260620_Kyoto" / "0000_a.jxl"
    make_tree(blocker.parent, {blocker.name: "mine"})
    assert run(src, dest, "--dry-run") == 1         # 0.1.2: 0
    assert run(src, dest) == 1
    assert blocker.read_text() == "mine"
    assert mirror(dest) == ["0000_a.jxl"]


# ---- O6: the state is not rewritten every 20 copies for ever --------------------------------
def count_saves(monkeypatch):
    saves = []
    real = mb.save_state

    def save_state(dest, files):
        saves.append(len(files))
        real(dest, files)

    monkeypatch.setattr(mb, "save_state", save_state)
    return saves


def test_o6_a_long_run_saves_the_state_less_and_less_often(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + f"p{i:03d}.jxl": 20 for i in range(300)})
    monkeypatch.setattr(mb, "STATE_SAVE_EVERY", 2)
    monkeypatch.setattr(mb, "STATE_SAVE_SPREAD", 10, raising=False)
    saves = count_saves(monkeypatch)
    assert run(src, dest) == 0
    assert len(saves) < 60                          # 0.1.2: 151
    assert saves[:3] == [2, 4, 6]                   # the start of a run is as before
    gaps = [b - a for a, b in zip(saves, saves[1:-1])]
    assert gaps == sorted(gaps) and max(gaps) <= 300 // 10
    assert len(state_files(dest)) == 300


def test_o6_spread_zero_is_the_fixed_interval(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + f"p{i:02d}.jxl": 20 for i in range(40)})
    monkeypatch.setattr(mb, "STATE_SAVE_EVERY", 4)
    monkeypatch.setattr(mb, "STATE_SAVE_SPREAD", 0, raising=False)
    saves = count_saves(monkeypatch)
    assert run(src, dest) == 0
    assert saves == [4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 40]
    assert mb._save_interval(10 ** 6) == 4


def test_o6_a_short_nightly_run_still_saves_every_few_copies(monkeypatch):
    assert mb._save_interval(0) == mb.STATE_SAVE_EVERY
    assert mb._save_interval(mb.STATE_SAVE_EVERY * mb.STATE_SAVE_SPREAD) == mb.STATE_SAVE_EVERY
    far = mb.STATE_SAVE_EVERY * mb.STATE_SAVE_SPREAD * 10
    assert mb._save_interval(far) == far // mb.STATE_SAVE_SPREAD
