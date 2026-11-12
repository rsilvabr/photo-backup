"""Third bug sweep (AI_tools/261112_Opus_review_bug-sweep-3.md), v0.13.2:
selective_backup.py, restore_backup.py and manifest_backup.py. Every test runs
on a synthetic tree in tmp_path."""
import csv
import io
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import manifest_backup as mb
import restore_backup as rb
import selective_backup as sb
from conftest import argv_for, latest_csv, latest_log, make_tree, snapshot

SESSION = {
    "G/2025/shoot/DSC_0001.NEF": 1000,
    "G/2025/shoot/DSC_0002.NEF": 1001,
    "G/2025/shoot/_EXPORT/DSC_0001_v1.jpg": 500,
    "G/2025/shoot/_EXPORT/DSC_0002_v1.jpg": 501,
}
SHIPPED_MODES = Path(__file__).resolve().parents[1] / "backup_modes.csv"


def backed_up(tmp_path):
    """The session backed up to tmp_path/BACKUP with base tmp_path/G."""
    make_tree(tmp_path, SESSION)
    source, dest = tmp_path / "G" / "2025", tmp_path / "BACKUP"
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--yes")) == 0
    return source, dest


def make_junction(link, target):
    target.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True)
    if result.returncode != 0:
        pytest.skip("cannot create a junction")


def remove_junction(link):
    """The junction itself, never what it points to."""
    if os.path.lexists(link):
        os.rmdir(link)


def statuses_by_name(log_dir, suffix):
    rows = list(csv.DictReader(io.StringIO(latest_csv(log_dir, suffix))))
    return {Path(row["rel"]).name: row["status"] for row in rows}


# ---- T2: an empty --to or --only --------------------------------------------------------------
def test_t2_an_empty_to_is_refused_and_nothing_lands_in_the_current_folder(tmp_path, monkeypatch,
                                                                          capsys):
    _source, dest = backed_up(tmp_path)
    here = tmp_path / "cwd"
    here.mkdir()
    monkeypatch.chdir(here)
    for value in ("", "   ", '""'):
        assert rb.main(["--backup", str(dest), "--to", value, "--yes"]) == 2
    assert "--to is empty" in capsys.readouterr().err
    assert list(here.iterdir()) == []
    # the current folder inside the backup: before v0.13.2 the restore wrote there
    before = snapshot(dest)
    monkeypatch.chdir(dest / "2025")
    assert rb.main(["--backup", str(dest), "--to", "", "--yes"]) == 2
    assert snapshot(dest) == before


def test_t2_an_empty_only_is_refused(tmp_path, capsys):
    _source, dest = backed_up(tmp_path)
    out = tmp_path / "out"
    for value in ("", " ", "\\", "/", '""'):
        assert rb.main(["--backup", str(dest), "--to", str(out), "--only", value,
                        "--dry-run"]) == 2
    assert "--only is empty" in capsys.readouterr().err
    assert rb.main(["--backup", str(dest), "--to", str(out), "--only", "2025\\shoot\\_EXPORT",
                    "--dry-run"]) == 0
    assert "RESTORE 2" in capsys.readouterr().out


# ---- T4: a destination that is really inside the source ----------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="junctions")
def test_t4_a_destination_through_a_junction_into_the_source_is_refused(tmp_path, capsys):
    make_tree(tmp_path, SESSION)
    source = tmp_path / "G" / "2025"
    link = tmp_path / "dest_link"
    make_junction(link, source / "backup_here")
    try:
        before = snapshot(tmp_path / "G")
        assert sb.main(argv_for(source, link, tmp_path / "G", "--yes")) == 2
        err = capsys.readouterr().err
        assert "destination overlaps source" in err and "really" in err
        manifest = tmp_path / "manifest.csv"
        manifest.write_text(f"Folder,Mode,Originals\n{source},3+,raw\n", encoding="utf-8")
        assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                        "--dest", str(link), "--yes"]) == 2
        assert snapshot(tmp_path / "G") == before
    finally:
        remove_junction(link)


@pytest.mark.skipif(sys.platform != "win32", reason="junctions")
def test_t4_a_source_through_a_junction_into_the_destination_is_refused(tmp_path):
    make_tree(tmp_path, {"D/inner/2025/shoot/DSC_0001.NEF": 10,
                         "D/inner/2025/shoot/_EXPORT/DSC_0001_v1.jpg": 5})
    link = tmp_path / "src_link"
    make_junction(link, tmp_path / "D" / "inner")
    try:
        before = snapshot(tmp_path / "D")
        assert sb.main(["--source", str(link), "--dest", str(tmp_path / "D"),
                        "--base", str(tmp_path), "--yes"]) == 2
        assert snapshot(tmp_path / "D") == before
    finally:
        remove_junction(link)


@pytest.mark.skipif(sys.platform != "win32", reason="subst")
def test_t4_a_destination_on_a_subst_letter_of_a_source_folder_is_refused(tmp_path):
    make_tree(tmp_path, SESSION)
    source = tmp_path / "G" / "2025"
    inner = source / "backup_here"
    inner.mkdir()
    taken = subprocess.run(["subst"], capture_output=True, text=True, errors="replace").stdout
    free = [letter for letter in "ZYXWVUTSRQ"
            if not os.path.exists(letter + ":\\") and f"{letter}:\\" not in taken]
    if not free:
        pytest.skip("no free drive letter")
    letter = free[0]
    if subprocess.run(["subst", f"{letter}:", str(inner)], capture_output=True).returncode != 0:
        pytest.skip("subst failed")
    try:
        before = snapshot(tmp_path / "G")
        assert sb.main(argv_for(source, f"{letter}:\\", tmp_path / "G", "--yes")) == 2
        assert snapshot(tmp_path / "G") == before
    finally:
        subprocess.run(["subst", f"{letter}:", "/d"], capture_output=True)


@pytest.mark.skipif(sys.platform != "win32", reason="junctions")
def test_t4_a_restore_never_writes_into_the_backup_through_a_junction(tmp_path, capsys,
                                                                    photobackup_log_dir):
    source, dest = backed_up(tmp_path)
    links = [tmp_path / "to_link", tmp_path / "backup_link", source / "shoot"]
    try:
        make_junction(links[0], dest / "restored_here")
        before = snapshot(dest)
        assert rb.main(["--backup", str(dest), "--to", str(links[0]), "--yes"]) == 2
        assert "--to cannot be inside the backup" in capsys.readouterr().err
        make_junction(links[1], dest)
        assert rb.main(["--backup", str(links[1]), "--to", str(dest / "x"), "--yes"]) == 2
        # --to-original whose original folder is now a junction into the backup
        (source / "shoot").rename(source / "shoot_moved")
        make_junction(links[2], dest / "inside")
        assert rb.main(["--backup", str(dest), "--to-original", "--yes"]) == 1
        status = statuses_by_name(photobackup_log_dir, "_restore.csv")
        assert set(status.values()) == {"REFUSED"} and len(status) == 4
        assert snapshot(dest) == before
    finally:
        for link in links:
            remove_junction(link)


# ---- T8: a space margin that is not a number, or negative ---------------------------------------
def test_t8_a_margin_that_is_not_a_number_or_is_negative_is_refused(tmp_path, capsys):
    source, dest = backed_up(tmp_path)
    out = tmp_path / "out"
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{source},3+,raw\n", encoding="utf-8")
    for value in ("nan", "inf", "-1"):
        assert sb.main(argv_for(source, dest, tmp_path / "G", "--dry-run",
                                "--space-margin-gb", value)) == 2
        assert rb.main(["--backup", str(dest), "--to", str(out), "--dry-run",
                        "--space-margin-gb", value]) == 2
        assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                        "--dest", str(dest), "--dry-run", "--space-margin-gb", value]) == 2
    assert "--space-margin-gb must be a number, 0 or more" in capsys.readouterr().err
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--verify", "full",
                            "--max-gb", "nan")) == 2
    assert "--max-gb must be a number" in capsys.readouterr().err


# ---- T12: a file named NUL ----------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="device names")
def test_t12_a_file_named_nul_is_an_error_of_the_plan_not_a_traceback(tmp_path, capsys,
                                                                    photobackup_log_dir):
    make_tree(tmp_path, SESSION)
    source = tmp_path / "G" / "2025"
    odd = "\\\\?\\" + str(source / "shoot" / "NUL")
    with open(odd, "wb") as handle:
        handle.write(b"x" * 10)
    try:
        dest = tmp_path / "BACKUP"
        assert sb.main(argv_for(source, dest, tmp_path / "G", "--yes",
                                "--unselected", "all")) == 1
        assert "Errors: 1 folders/files could not be read" in capsys.readouterr().out
        assert "ERROR name" in latest_log(photobackup_log_dir)
        assert (dest / "2025" / "shoot" / "DSC_0001.NEF").exists()
        assert (dest / "2025" / "shoot" / "_EXPORT" / "DSC_0002_v1.jpg").exists()
    finally:
        os.remove(odd)


# ---- T13: a catalog row that leads outside the backup ------------------------------------------
def test_t13_unsafe_rel_cases():
    for rel in ("2025\\shoot\\x.NEF", "a\\...\\b", "a\\.DS_Store", ".hidden\\x", "a\\b..c",
                "a/b/c.jpg"):
        assert sb._unsafe_rel(rel) is None, rel
    for rel in ("..\\x", "a\\..\\b", "a\\.. \\b", "a\\.\\b", "a/../b", "C:\\x", "C:x", "\\x",
                "/x", "", "  ", "a:b", 5, ".", ".."):
        assert sb._unsafe_rel(rel) is not None, rel


def test_t13_a_catalog_row_that_leads_outside_the_backup_stops_every_run(tmp_path, capsys):
    source, dest = backed_up(tmp_path)
    outside = tmp_path / "not_in_the_backup.txt"
    outside.write_bytes(b"outside")
    rel = "2025\\..\\..\\not_in_the_backup.txt"     # under the source prefix '2025'
    con = sqlite3.connect(dest / "_photo_backup" / "catalog.sqlite")
    con.execute("INSERT INTO files VALUES(?, ?, ?, ?, NULL, ?, NULL, NULL)",
                (rel.casefold(), rel, 7, outside.stat().st_mtime_ns,
                 str(source / "shoot" / "gone.txt")))
    con.commit()
    con.close()
    before = snapshot(dest)
    out = tmp_path / "out"
    assert rb.main(["--backup", str(dest), "--to", str(out), "--yes"]) == 2
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--dry-run")) == 2
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--yes", "--move-orphans")) == 2
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--verify", "full")) == 2
    assert "would lead outside the backup" in capsys.readouterr().err
    assert not out.exists()
    assert outside.read_bytes() == b"outside"
    assert snapshot(dest) == before


def test_t13_names_that_only_look_odd_do_not_stop_a_catalog(tmp_path):
    make_tree(tmp_path, {"G/2025/shoot/DSC_0001.NEF": 10,
                         "G/2025/shoot/_EXPORT/DSC_0001_v1.jpg": 5,
                         "G/2025/other/.hidden/notes.txt": 3,
                         "G/2025/other/a...b/c..d.txt": 3})
    source, dest = tmp_path / "G" / "2025", tmp_path / "BACKUP"
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--yes", "--unexported", "copy")) == 0
    catalog = sb._Catalog(str(dest), create=False, readonly=True)
    try:
        rels = {row.rel for row in catalog.all_rows()}
    finally:
        catalog.close()
    assert "2025\\other\\.hidden\\notes.txt" in rels and "2025\\other\\a...b\\c..d.txt" in rels
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--verify", "quick")) == 0
    assert rb.main(["--backup", str(dest), "--to", str(tmp_path / "out"), "--dry-run"]) == 0


# ---- T16: versions\ and orphans\ never replace a file -------------------------------------------
def test_t16_two_versions_with_one_stamp_are_both_kept(tmp_path):
    dest = tmp_path / "D"
    (dest / "2025").mkdir(parents=True)
    log = sb._Logger(str(tmp_path / "logs" / "t16"))
    try:
        for content in (b"first old copy", b"second old copy"):
            dst = dest / "2025" / "a.cos"
            dst.write_bytes(content)
            planned = sb.PlannedFile(str(tmp_path / "src" / "a.cos"), "2025\\a.cos", 9, 0,
                                     sb.CAT_CAPTURE_ONE)
            sb._move_to_versions(str(dest), "261112_120000",
                                 sb.FileStatus(planned, str(dst), sb.ST_CHANGED), log)
    finally:
        log.close()
    versions = dest / "_photo_backup" / "versions" / "261112_120000" / "2025"
    assert {path.name: path.read_bytes() for path in versions.iterdir()} == {
        "a.cos": b"first old copy", "a (2).cos": b"second old copy"}


def test_t16_an_orphan_never_replaces_one_moved_before(tmp_path):
    dest = tmp_path / "D"
    make_tree(dest, {"2025/x.NEF": "new orphan",
                     "_photo_backup/orphans/261112_120000/2025/x.NEF": "older orphan"})
    catalog = sb._Catalog(str(dest), create=True)
    log = sb._Logger(str(tmp_path / "logs" / "t16b"))
    try:
        row = sb._Row("2025\\x.nef", "2025\\x.NEF", 10, 0, None, str(tmp_path / "gone.NEF"))
        assert sb._move_orphans(str(dest), "261112_120000", [("source_missing", row)],
                                catalog, log) == 1
    finally:
        catalog.close()
        log.close()
    moved = dest / "_photo_backup" / "orphans" / "261112_120000" / "2025"
    assert {path.name: path.read_text() for path in moved.iterdir()} == {
        "x.NEF": "older orphan", "x (2).NEF": "new orphan"}


def test_t16_a_move_never_replaces_a_file_that_appeared(tmp_path, monkeypatch):
    source, dest = backed_up(tmp_path)
    (source / "shoot").rename(source / "shoot2")
    args = sb._build_parser().parse_args(argv_for(source, dest, tmp_path / "G", "--yes"))
    planned = sb.plan(args)
    statuses = sb._file_statuses(planned.files, str(dest))
    catalog = sb._Catalog(str(dest), create=True)
    log = sb._Logger(str(tmp_path / "logs" / "t16c"))
    try:
        candidates = sb._find_move_candidates(statuses, catalog.all_rows(), str(dest),
                                              planned.sources)
        assert candidates
        appeared = dest / "2025" / "shoot2" / "DSC_0001.NEF"
        appeared.parent.mkdir(parents=True)
        appeared.write_bytes(b"a file that appeared")
        real_exists = sb._exists
        monkeypatch.setattr(sb, "_exists", lambda path: (
            False if os.path.normcase(str(path)) == os.path.normcase(str(appeared))
            else real_exists(path)))
        _moved, _bytes, errors, _interrupted = sb._apply_moves(candidates, str(dest), catalog,
                                                               log)
    finally:
        catalog.close()
        log.close()
    assert errors >= 1
    assert appeared.read_bytes() == b"a file that appeared"
    assert (dest / "2025" / "shoot" / "DSC_0001.NEF").exists()


# ---- T17: the backup ID file is never replaced ----------------------------------------------
def test_t17_a_backup_id_file_that_cannot_be_read_is_kept(tmp_path, photobackup_log_dir):
    source, dest = backed_up(tmp_path)
    volume = dest / "_photo_backup" / "volume.json"
    volume.write_text("{ half written", encoding="utf-8")
    before = volume.read_bytes()
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--yes")) == 0
    assert volume.read_bytes() == before
    assert "is there but could not be read" in latest_log(photobackup_log_dir)


# ---- O1: the quick verify takes a planned source from the plan's listing ------------------------
def test_o1_a_quick_verify_asks_nothing_more_of_a_planned_source(tmp_path, monkeypatch):
    source, dest = backed_up(tmp_path)
    asked = []
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        text = os.fspath(path)
        if str(source) in text and text.endswith((".NEF", ".jpg")):
            asked.append(text)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--verify", "quick")) == 0
    assert asked == []
    monkeypatch.setattr(sb, "VERIFY_QUICK_USES_PLAN", False)
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--verify", "quick")) == 0
    assert len(asked) == 4


def test_o1_a_source_listed_but_refused_shows_in_the_full_verify(tmp_path, monkeypatch,
                                                                photobackup_log_dir):
    source, dest = backed_up(tmp_path)
    refused = str(source / "shoot" / "DSC_0001.NEF")
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        if os.fspath(path).endswith(refused):
            raise PermissionError(13, "Access is denied", os.fspath(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--verify", "quick")) == 0
    assert sb.main(argv_for(source, dest, tmp_path / "G", "--verify", "full")) == 1
    assert statuses_by_name(photobackup_log_dir, "_verify.csv")["DSC_0001.NEF"] == \
        "SOURCE_UNREADABLE"


def test_o1_a_row_whose_source_is_another_path_is_still_asked(tmp_path, monkeypatch,
                                                              photobackup_log_dir):
    source, dest = backed_up(tmp_path)
    other = tmp_path / "G2"
    make_tree(other, {key.replace("G/", "", 1): value for key, value in SESSION.items()})
    for name in ("DSC_0001.NEF", "DSC_0002.NEF"):          # the same dates as the first tree
        stat = (source / "shoot" / name).stat()
        os.utime(other / "2025" / "shoot" / name, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    for name in ("DSC_0001_v1.jpg", "DSC_0002_v1.jpg"):
        stat = (source / "shoot" / "_EXPORT" / name).stat()
        os.utime(other / "2025" / "shoot" / "_EXPORT" / name,
                 ns=(stat.st_atime_ns, stat.st_mtime_ns))
    (source / "shoot" / "DSC_0001.NEF").unlink()        # the source the catalog names is gone
    assert sb.main(argv_for(other / "2025", dest, other, "--verify", "quick")) == 1
    assert statuses_by_name(photobackup_log_dir, "_verify.csv")["DSC_0001.NEF"] == \
        "SOURCE_MISSING"
