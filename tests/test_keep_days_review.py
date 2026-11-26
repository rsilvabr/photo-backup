"""The fixes after the review of 261126 (topic `keep-days`,
AI_tools/261126_Opus_review_keep-days.md): MF2 (the space asked again after
a purge the fit counted on), SF1 (0 days never deletes what is already kept
without the typed number), SF2 (the mirror's box when it stops for space after
removing), SF3 (no date for a store kept `never`), SF4 (the mirror's table
says what happens to each JPEG copy), SF5 (a JPEG copy is deleted only after
reading it), SF6 (--empty-trash on a locked catalog).
Every test here failed on the code before the fixes."""
import os
import re
import sqlite3
from pathlib import Path

import mirror_backup as mir
import photo_backup as menu
import selective_backup as sb

from conftest import latest_log, snapshot
from test_keep_days import (ORIGINALS, answers, backup, chooser_asked, edit,  # noqa: F401
                            full_disk, make_run, plain, type_what_is_asked)
from test_menu import menu_config, script  # noqa: F401
from test_tiff_jxl import VERSIONS, backed, env, under  # noqa: F401
from test_tiff_jxl import backup as jxl_backup


def mirror_common(plain):
    return ["--source", str(plain.src / "2025"), "--base", str(plain.src), "--dest",
            str(plain.dest), "--unexported", "copy"]


# ---- MF2: the space asked again after a purge the fit counted on ------------------------------------
def test_mf2_fits_only_by_expiry_and_the_purge_fails_stops(plain, monkeypatch, capsys):
    """Free 1,000, 3,000 to copy, margin 0; an expired run of 5,000 bytes makes
    it fit. The purge fails (a locked file): the disk is asked again and the
    run stops before the first byte (exit 2)."""
    make_run(plain.dest, "versions", 40, {"a.NEF": 5000})
    (plain.src / "2025/day/NEW.NEF").write_bytes(b"n" * 3000)
    full_disk(monkeypatch, 1000)
    monkeypatch.setattr(sb, "_purge_store_run", lambda run, dest, folder, log, catalog=None:
                        (0, 0, 1))
    code = backup(plain, "--yes", "--space-margin-gb", "0", "--versions-keep-days", "30")
    assert not (plain.dest / "2025/day/NEW.NEF").exists()
    assert code == 2
    assert "Still not enough space" in capsys.readouterr().out


def test_mf2_the_mirror_asks_the_disk_again_after_its_expired_trash(plain, monkeypatch):
    """The mirror: what expires in its trash made it fit; the purge fails: it
    stops before copying."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    common = mirror_common(plain) + ["--space-margin-gb", "0"]
    assert mir.main(common + ["--yes"]) == 0
    make_run(plain.dest, mir.MIRROR_TRASH_FOLDER, 40, {"a.NEF": 5000})
    (plain.src / "2025/day/NEW.NEF").write_bytes(b"n" * 3000)
    full_disk(monkeypatch, 1000)
    monkeypatch.setattr(sb, "_purge_store_run", lambda run, dest, folder, log, catalog=None:
                        (0, 0, 1))
    code = mir.main(common + ["--yes"])
    assert not (plain.dest / "2025/day/NEW.NEF").exists()
    assert code == 2


# ---- SF1: 0 days never deletes what is already kept without a person ---------------------------------
def test_sf1_zero_days_with_yes_keeps_the_run_folders_already_there(plain, capsys):
    """VERSIONS_KEEP_DAYS = 0, --yes, nothing set aside in this run: the run
    folders already in versions\\ need the typed number: exit 2, nothing
    changed."""
    run = make_run(plain.dest, "versions", 1, {"2025/day/a.NEF": 40})
    before = snapshot(plain.dest)
    capsys.readouterr()
    assert backup(plain, "--yes", "--versions-keep-days", "0") == 2
    assert snapshot(plain.dest) == before and run.exists()
    assert "needs a person who types the number" in capsys.readouterr().err


def test_sf1_zero_days_typed_deletes_them(plain, monkeypatch, photobackup_log_dir):
    run = make_run(plain.dest, "versions", 1, {"2025/day/a.NEF": 40, "2025/day/b.NEF": 1})
    answers(monkeypatch, "5")                           # a wrong number: nothing changes
    before = snapshot(plain.dest)
    assert backup(plain, "--versions-keep-days", "0") == 2
    assert snapshot(plain.dest) == before
    answers(monkeypatch, "2", "y")
    assert backup(plain, "--versions-keep-days", "0") == 0
    assert not run.exists()


def test_sf1_the_mirror_zero_days_unattended_keeps_its_trash(plain, monkeypatch):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    common = mirror_common(plain)
    assert mir.main(common + ["--yes"]) == 0
    run = make_run(plain.dest, mir.MIRROR_TRASH_FOLDER, 1, {"a.NEF": 10})
    before = snapshot(plain.dest)
    assert mir.main(common + ["--unattended", "--keep-days", "0"]) == 2
    assert snapshot(plain.dest) == before and run.exists()
    type_what_is_asked(monkeypatch)
    assert mir.main(common + ["--yes", "--keep-days", "0"]) == 0
    assert not run.exists()


def test_sf1_the_menu_says_what_0_does(monkeypatch, menu_config, capsys):
    """The warning names the run folders already kept, the typed number, and
    that a preset stops."""
    config = menu.load_config()
    script(monkeypatch, ["y", "0", "-", "-", "-", "-"])
    menu.keep_days_settings(config)
    out = " ".join(capsys.readouterr().out.split())
    assert "already kept" in out and "type the number" in out
    assert "preset" in out and "stops" in out


# ---- SF2: the mirror stops for space after removing: its box says what left and where ---------------
def test_sf2_the_mirror_stops_after_its_removals_with_its_box(plain, monkeypatch, capsys,
                                                              photobackup_log_dir):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    common = mirror_common(plain) + ["--space-margin-gb", "0"]
    assert mir.main(common + ["--yes"]) == 0
    (plain.src / "2025/day/DSC_0002.NEF").unlink()              # gone: the mirror removes it
    run = make_run(plain.dest, mir.MIRROR_TRASH_FOLDER, 5, {"a.NEF": 4000})
    (plain.src / "2025/day/NEW.NEF").write_bytes(b"n" * 3000)
    full_disk(monkeypatch, 1000)                                # deleting frees nothing
    type_what_is_asked(monkeypatch, then=["1", "y"])
    capsys.readouterr()
    code = mir.main(common)
    out = capsys.readouterr().out
    assert not run.exists() and code == 1
    assert "MIRROR STOPPED" in out
    tail = out[out.index("MIRROR STOPPED"):]
    assert "removed from the backup (to the trash)" in tail
    assert os.path.join(sb.CATALOG_FOLDER, mir.MIRROR_TRASH_FOLDER) in " ".join(tail.split())
    assert "not enough space" in tail.lower()
    assert not (plain.dest / "2025/day/NEW.NEF").exists()


# ---- SF3: no date for a store kept `never` --------------------------------------------------------------
def test_sf3_show_trash_never_store_shows_no_date(plain, capsys):
    make_run(plain.dest, mir.MIRROR_TRASH_VERSIONS_FOLDER, 3, {"d.NEF": 10})
    capsys.readouterr()
    assert mir.main(["--show-trash", "--dest", str(plain.dest),
                     "--versions-keep-days", "never"]) == 0
    out = capsys.readouterr().out
    section = out[out.index("old copies replaced"):]
    assert not re.search(r"\d{4}-\d\d-\d\d \(in \d+ days?\)", section)
    assert "only when space is short" in section


# ---- SF4: the mirror's table says what happens to each JPEG copy ------------------------------------------
def test_sf4_the_mirrors_table_does_not_say_every_jpeg_copy_is_deleted(env, monkeypatch, capsys):
    from test_jpeg_jxl import add_jpegs
    from test_tiff_jxl import write_manifest
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    add_jpegs(env)
    copy = write_manifest(env.root / "copy.csv", (env.src / "2025", "6", "", "", ""))
    with open(env.root / "jxl.csv", "w", newline="", encoding="utf-8") as handle:
        handle.write("Folder,Mode,Originals,Mirror,Tiff,Jpeg\n"
                     f'"{env.src / "2025"}",6,,,,jxl\n')
    assert mir.main(["--manifest", str(copy), "--dest", str(env.dest), "--yes"]) == 0
    capsys.readouterr()
    assert mir.main(["--manifest", str(env.root / "jxl.csv"), "--dest", str(env.dest),
                     "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "(JPEG: deleted)" not in out
    assert "old copy replaced by its JXL (JPEG)" in out
    assert "deleted with proof, else to the trash" in out


# ---- SF5: a JPEG copy is deleted only after reading it ----------------------------------------------------
def test_sf5_a_jpeg_copy_with_other_bytes_than_its_row_goes_to_versions(env, photobackup_log_dir):
    """The JPEG copy no longer holds its row's bytes (same size, same date):
    the file is read, it is not the JXL's source, so it is kept (versions)."""
    export = "2025/shoot/_EXPORT/DSC_0001_v1.jpg"
    assert jxl_backup(env) == 0
    copy = env.dest / export
    data = copy.read_bytes()
    info = copy.stat()
    copy.write_bytes(b"\xff\xd8" + bytes(len(data) - 2))
    os.utime(copy, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert jxl_backup(env, "--jpeg", "jxl") == 0
    log = latest_log(photobackup_log_dir)
    assert export in under(env.dest, VERSIONS)
    assert "DELETED (its JXL gives these bytes back) " + os.path.join(*export.split("/")) not in log


# ---- SF6: --empty-trash on a catalog that cannot be written ---------------------------------------------
def test_sf6_empty_trash_on_a_locked_catalog(plain, monkeypatch, capsys):
    """'database is locked' in the middle: no traceback; what was deleted is
    said, the rest stays, exit 1."""
    first = make_run(plain.dest, mir.MIRROR_TRASH_FOLDER, 9, {"a.NEF": 10, "b.NEF": 10})
    second = make_run(plain.dest, mir.MIRROR_TRASH_FOLDER, 8, {"c.NEF": 10})
    calls = []

    def locked(self, stored_rel):
        calls.append(stored_rel)
        if len(calls) >= 2:
            raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(sb._Catalog, "forget_original", locked)
    type_what_is_asked(monkeypatch)
    capsys.readouterr()
    code = mir.main(["--empty-trash", "--dest", str(plain.dest)])
    out = capsys.readouterr().out
    assert code == 1
    assert "deleted for good" in out and "catalog" in out.lower()
    left = [path for path in (first / "a.NEF", first / "b.NEF", second / "c.NEF") if path.exists()]
    assert left                                                  # the rest stays
