"""captureone_backup.py: the findings of the third sweep (T3, T4, T6, T7, T9,
T11, T13, T16, O5 of AI_tools/261112_Opus_review_bug-sweep-3.md). Every folder
is a synthetic tree in tmp_path, inside a hostile path; the real Capture One
folders are never read (conftest points PHOTOBACKUP_C1_* away)."""
import contextlib
import json
import msvcrt
import os
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import captureone_backup as c1
import selective_backup as sb
from conftest import make_tree

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="junctions, msvcrt")

HOSTILE = "写真 [FINAL] 100% (2)"
STYLE = "Styles50/a.costyle"
PROFILE = "Color Profiles/DSLR/NikonD200-Generic.icm"
STAMP = "261112_120000"


@pytest.fixture
def machine(tmp_path, monkeypatch):
    root = tmp_path / HOSTILE
    appdata = root / "AppData Local"
    make_tree(appdata, {
        "CaptureOne/Color Profiles/mine.icc": 500,
        "CaptureOne/Styles50/a.costyle": 40,
        "CaptureOne/Styles50/b.costyle": 41,
        "CaptureOne/Recipes165/sRGB.xml": 20,
        "CaptureOne/_Instalador/serial.png": 55,
        "Capture_One/Strong_abc/16.7.8/user.config": 110,
    })
    install = make_tree(root / "Program Files C1", {
        PROFILE: 200,
        "Film Curves/NikonD200-Film Standard.fcrv": 210,
    })
    color = make_tree(root / "win color", {"ProPhoto.icm": 121})
    monkeypatch.setenv("PHOTOBACKUP_C1_LOCALAPPDATA", str(appdata))
    monkeypatch.setenv("PHOTOBACKUP_C1_INSTALL", str(install))
    monkeypatch.setenv("PHOTOBACKUP_C1_WINDOWS_COLOR", str(color))
    monkeypatch.setattr(c1, "_captureone_running", lambda: False)
    monkeypatch.setattr(c1, "_is_admin", lambda: False)
    dest = tmp_path / "宛先 [BK] 50%"
    links = []
    yield {"appdata": appdata, "user": appdata / "CaptureOne", "install": install,
           "color": color, "dest": dest, "backup": dest / c1.BACKUP_SUBFOLDER, "root": root,
           "tmp": tmp_path, "links": links}
    for link in links:                      # the junction only, never what it points to
        try:
            os.rmdir(link)
        except OSError:
            pass
    for path in tmp_path.rglob("*"):        # read-only test files must not block the cleanup
        try:
            if path.is_file() and not os.access(path, os.W_OK):
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass


def rels(folder):
    return {str(p.relative_to(folder)).replace("\\", "/")
            for p in Path(folder).rglob("*") if p.is_file()}


def backup(machine, *extra):
    return c1.main(["--dest", str(machine["dest"]), "--yes", *extra])


def restore(machine, *extra):
    return c1.main(["--restore-from", str(machine["dest"]), "--yes", *extra])


def revert(*extra):
    return c1.main(["--revert-last", "--yes", *extra])


def junction(machine, link, target):
    """A junction made for the test; the fixture removes it (never its target)."""
    Path(target).mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, capture_output=True)
    machine["links"].append(link)
    return link


def plant(machine, entries, files=None, name="old_29991231_235959_999"):
    """An old_ folder written by someone else than --force, newer than any real one."""
    old = machine["appdata"] / c1.OLD_PARENT_FOLDER / name
    old.mkdir(parents=True)
    for rel, data in (files or {}).items():
        path = old / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (old / c1.JOURNAL_FILE).write_text("".join(json.dumps(entry) + "\n" for entry in entries),
                                       encoding="utf-8")
    return old


def robocopy_sources(machine):
    import re
    text = (machine["backup"] / c1.RESTORE_SCRIPT).read_text(encoding="utf-8")
    return re.findall(r'robocopy "%HERE%([^"]+)"', text)


def add_row(machine, rel, source=None):
    """A row put into the backup's catalog by hand (a damaged or forged catalog)."""
    path = machine["backup"] / sb.CATALOG_FOLDER / "catalog.sqlite"
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("INSERT INTO files VALUES(?, ?, ?, ?, NULL, ?, NULL, NULL)",
                           (rel.casefold(), rel, 17, 0, str(source or "x")))
        connection.commit()
    finally:
        connection.close()


@contextlib.contextmanager
def locked(path):
    """Another handle holds the bytes of `path`: it can be seen, not read."""
    size = os.path.getsize(path)
    handle = open(path, "r+b")
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, size)
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, size)
        handle.close()


# ---- T3: --revert-last undoes only where Capture One keeps things ---------------------------
def test_t3_a_planted_journal_never_writes_or_removes_outside_capture_one(machine, capsys):
    assert backup(machine) == 0
    elsewhere = machine["tmp"] / "elsewhere (not Capture One)"
    victim = make_tree(elsewhere, {"Documents/notes.txt": "the user's own document"})
    victim = elsewhere / "Documents" / "notes.txt"
    dll = elsewhere / "Other App" / "plugins" / "payload.dll"
    make_tree(elsewhere, {"Other App/plugins/payload.dll": "the real dll"})
    plant(machine, [
        {"op": "added", "rel": "UserData\\Documents\\notes.txt", "target": str(victim)},
        {"op": "replaced", "rel": "UserData\\plugins\\payload.dll", "target": str(dll)},
    ], {"UserData/plugins/payload.dll": b"PLANTED BYTES"})
    assert c1.main(["--revert-last", "--dry-run"]) == 2
    assert revert() == 2
    assert victim.read_bytes() == b"the user's own document"
    assert dll.read_bytes() == b"the real dll"
    assert "is not in this computer's UserData folder" in capsys.readouterr().err
    assert not list((machine["appdata"] / c1.OLD_PARENT_FOLDER).glob(c1.REVERT_PREFIX + "*"))


@pytest.mark.parametrize("case", ["not an install folder", "not a data folder"])
def test_t3_an_installed_target_is_trusted_only_in_an_install_data_folder(machine, monkeypatch,
                                                                         case):
    monkeypatch.setattr(c1, "_is_admin", lambda: True)          # the dangerous case
    assert backup(machine) == 0
    if case == "not an install folder":
        place = machine["tmp"] / "some app" / "Color Profiles" / "x.icm"     # no Film Curves
        rel = "Installed\\Color Profiles\\x.icm"
    else:
        place = machine["install"] / "plugins" / "x.dll"
        rel = "Installed\\plugins\\x.dll"
    make_tree(place.parent, {place.name: "the real file"})
    plant(machine, [{"op": "replaced", "rel": rel, "target": str(place)}],
          {rel.replace("\\", "/"): b"PLANTED BYTES"})
    assert revert() == 2
    assert place.read_bytes() == b"the real file"


def test_t3_a_target_through_a_junction_below_the_folder_is_refused(machine):
    assert backup(machine) == 0
    outside = make_tree(machine["tmp"] / "System-like", {"a.costyle": "outside, not ours"})
    link = junction(machine, machine["user"] / "StylesX", outside)
    plant(machine, [{"op": "replaced", "rel": "UserData\\StylesX\\a.costyle",
                     "target": str(link / "a.costyle")}],
          {"UserData/StylesX/a.costyle": b"PLANTED BYTES"})
    assert revert() == 2
    assert (outside / "a.costyle").read_bytes() == b"outside, not ours"


def test_t3_a_journal_rel_with_a_drive_is_refused(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert restore(machine, "--force") == 0
    restored = style.read_bytes()
    plant(machine, [{"op": "replaced", "rel": "C:\\UserData\\Styles50\\a.costyle"}],
          {"x": b"y"})
    assert revert() == 2
    assert style.read_bytes() == restored


def test_t3_a_force_into_another_install_is_undone_there_and_named_first(machine, monkeypatch,
                                                                         capsys):
    """N10 still works: the target outside this run's folders is a real install
    folder, and it is named before the question (and in the dry-run)."""
    monkeypatch.setattr(c1, "_is_admin", lambda: True)
    real = make_tree(machine["tmp"] / "D Apps" / "Capture One", {
        PROFILE: 200,
        "Film Curves/NikonD200-Film Standard.fcrv": 210,
    })
    profile = real / PROFILE
    assert backup(machine, "--install-folder", str(real)) == 0
    wanted = profile.read_bytes()
    profile.write_bytes(b"from a newer Capture One")
    assert restore(machine, "--force", "--force-installed", "--install-folder", str(real)) == 0
    assert profile.read_bytes() == wanted
    capsys.readouterr()
    assert c1.main(["--revert-last", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "outside this run's Capture One folders" in out and str(profile) in out
    assert revert() == 0
    assert profile.read_bytes() == b"from a newer Capture One"


# ---- T4: a path through a junction is judged where it really is -----------------------------
def test_t4_a_dest_through_a_junction_into_capture_one_is_refused(machine, capsys):
    link = junction(machine, machine["tmp"] / "dest link", machine["user"])
    before = rels(machine["user"])
    assert c1.main(["--dest", str(link), "--yes"]) == 2
    assert rels(machine["user"]) == before
    assert "really" in capsys.readouterr().err


def test_t4_a_restore_to_through_a_junction_into_the_backup_is_refused(machine):
    assert backup(machine) == 0
    link = junction(machine, machine["tmp"] / "out link", machine["backup"])
    before = rels(machine["backup"])
    assert restore(machine, "--to", str(link / "restored here")) == 2
    assert rels(machine["backup"]) == before


# ---- T6: a force that changed nothing is the one --revert-last finds -------------------------
def test_t6_a_force_that_changed_nothing_does_not_undo_the_force_before(machine, capsys):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"work of last month")
    assert restore(machine, "--force") == 0                 # force A: replaced and journaled
    style.write_bytes(b"work of today")
    capsys.readouterr()
    with locked(style):                                     # force B: nothing can be kept
        assert restore(machine, "--force") == 1
    out = capsys.readouterr().out
    assert "Old files kept in" not in out and "Nothing was replaced or added" in out
    assert revert() == 2                                    # B's folder: nothing to undo
    assert "Nothing to undo" in capsys.readouterr().err
    assert style.read_bytes() == b"work of today"


def test_t6_an_old_folder_without_a_journal_is_refused_and_the_suffix_skips_it(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"work of last month")
    assert restore(machine, "--force") == 0
    style.write_bytes(b"work of today")
    empty = machine["appdata"] / c1.OLD_PARENT_FOLDER / "old_29991231_235959_999"
    empty.mkdir()                                           # as a v0.13.1 force that failed left it
    assert revert() == 2
    assert style.read_bytes() == b"work of today"
    empty.rename(empty.with_name(empty.name + c1.DISABLED_SUFFIX))
    assert revert() == 0
    assert style.read_bytes() == b"work of last month"


# ---- T7: the map and the .cmd never end a run with a traceback -------------------------------
def test_t7_a_read_only_map_and_script_are_written_again(machine):
    assert backup(machine) == 0
    for name in (c1.MAP_FILE, c1.RESTORE_SCRIPT):
        os.chmod(machine["backup"] / name, stat.S_IREAD)
    make_tree(machine["user"], {"Workspaces1/mine.xml": 30})
    assert backup(machine) == 0
    assert "UserData\\Workspaces1" in robocopy_sources(machine)
    assert not list(machine["backup"].glob("*" + sb.TEMP_SUFFIX))


def test_t7_a_map_that_cannot_be_written_is_said_and_the_run_ends_with_1(machine, monkeypatch,
                                                                        capsys):
    real = os.replace

    def replace(src, dst, *args, **kwargs):
        if os.fspath(dst).endswith(c1.MAP_FILE):
            raise PermissionError(13, "disk says no", os.fspath(dst))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    assert backup(machine) == 1
    out = capsys.readouterr().out
    assert "could not be written" in out and c1.MAP_FILE in out
    assert (machine["backup"] / c1.RESTORE_SCRIPT).exists()       # written before the map
    assert not list(machine["backup"].glob("*" + sb.TEMP_SUFFIX))
    assert "UserData/Styles50/a.costyle" in rels(machine["backup"])


# ---- T9: the dry-run checks the catalog like the copy does -----------------------------------
def test_t9_the_dry_run_says_a_damaged_catalog(machine, capsys):
    make_tree(machine["user"], {f"Styles50/s{i:04d}.costyle": 30 for i in range(400)})
    assert backup(machine) == 0
    catalog = machine["backup"] / sb.CATALOG_FOLDER / "catalog.sqlite"
    data = bytearray(catalog.read_bytes())
    assert len(data) > 3 * 4096
    data[4096:8192] = b"\xAB" * 4096
    catalog.write_bytes(bytes(data))
    assert c1.main(["--dest", str(machine["dest"]), "--dry-run"]) == 2
    assert "damaged" in capsys.readouterr().err


def test_t9_the_dry_run_says_a_row_that_leads_outside(machine, capsys):
    assert backup(machine) == 0
    add_row(machine, "UserData\\..\\..\\outside.txt")
    assert c1.main(["--dest", str(machine["dest"]), "--dry-run"]) == 2
    assert "outside the backup" in capsys.readouterr().err


# ---- T11: a link where the walk never goes is not "missing from the backup" -----------------
def test_t11_links_where_the_walk_never_goes_are_not_reported(machine, capsys):
    outside = make_tree(machine["tmp"] / "elsewhere", {"x.txt": 5})
    (machine["user"] / "ImageCore").mkdir()
    junction(machine, machine["user"] / "Logs", outside)                    # EXCLUDE_TOP_FOLDERS
    junction(machine, machine["user"] / "ImageCore" / "EBWebView", outside)  # EXCLUDE_ANY_FOLDER
    junction(machine, machine["color"] / "vendor_subfolder", outside)      # never walked below
    assert backup(machine) == 0
    assert "Links/junctions not followed" not in capsys.readouterr().out
    junction(machine, machine["user"] / "Styles60", outside)                # a real gap
    assert backup(machine) == 0
    assert "Links/junctions not followed: 1" in capsys.readouterr().out


# ---- T13: a catalog row that leads outside the backup stops every use of it -----------------
def test_t13_a_row_that_leads_outside_stops_restore_verify_and_backup(machine):
    assert backup(machine) == 0
    outside = machine["tmp"] / "outside.txt"
    outside.write_bytes(b"not in the backup")
    add_row(machine, "UserData\\Styles50\\..\\..\\..\\..\\outside.txt", outside)
    out = machine["tmp"] / "out"
    assert restore(machine) == 2
    assert restore(machine, "--to", str(out)) == 2
    assert restore(machine, "--to", str(out), "--also", "*") == 2
    assert c1.main(["--verify", str(machine["dest"])]) == 2
    assert backup(machine) == 2
    assert not out.exists()
    assert outside.read_bytes() == b"not in the backup"


# ---- T16: two runs in one second keep both old copies ----------------------------------------
def test_t16_two_runs_in_one_second_keep_both_old_copies(machine, monkeypatch):
    real = time.strftime

    def strftime(fmt, *args):
        return STAMP if fmt == "%y%m%d_%H%M%S" else real(fmt, *args)

    monkeypatch.setattr(time, "strftime", strftime)
    style = machine["user"] / STYLE
    assert backup(machine) == 0
    first = style.read_bytes()
    style.write_bytes(b"version two!")
    assert backup(machine) == 0
    style.write_bytes(b"version three, longer")
    assert backup(machine) == 0
    kept = machine["backup"] / sb.CATALOG_FOLDER / "versions" / STAMP / "UserData" / "Styles50"
    assert sorted(path.read_bytes() for path in kept.iterdir()) == sorted([first, b"version two!"])
    assert (machine["backup"] / "UserData" / STYLE).read_bytes() == b"version three, longer"


# ---- O5: one listing per folder of the backup ------------------------------------------------
def test_o5_the_backup_lists_each_folder_instead_of_asking_per_file(machine, monkeypatch):
    assert backup(machine) == 0
    asked = []
    real = sb._file_status

    def file_status(planned, dest):
        asked.append(planned.rel)
        return real(planned, dest)

    monkeypatch.setattr(sb, "_file_status", file_status)
    assert backup(machine) == 0
    assert asked == []
