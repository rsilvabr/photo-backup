"""What the backup sets aside, how long it keeps it, and room on demand (261126,
topic `keep-days`): the three kinds (old versions, TIFF originals, the mirror's
removed files), their *_KEEP_DAYS (N, 0, never), the expiry, the space block,
--space-report, the shared chooser when space is short, the `originals` table
and the menu. Every tree lives in a HOSTILE folder (Japanese, [ ], a space, %).
Tests marked "(star)" fail on 8f1682b (the plan's section 8)."""
import collections
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import mirror_backup as mir
import photo_backup as menu
import selective_backup as sb

from conftest import argv_for, latest_log, make_tree, snapshot
from test_menu import menu_config, script  # noqa: F401
from test_tiff_jxl import (JXL, TIFFS, VERSIONS, backed, closed_console, db,  # noqa: F401
                           env, mirror_rel, under, write_manifest)
from test_tiff_jxl import backup as jxl_backup

HOSTILE = "吾妻山公園_菜の花 [FINAL] 5% off"
Usage = collections.namedtuple("Usage", "total used free")
ORIGINALS = getattr(sb, "ORIGINALS_FOLDER", "originals")


# ---- helpers ----------------------------------------------------------------------------------
@pytest.fixture
def plain(tmp_path):
    """A small source (no JXL) backed up once into `dest`."""
    root = tmp_path / HOSTILE
    src = make_tree(root / "SRC", {"2025/day/DSC_0001.NEF": 1001, "2025/day/DSC_0002.NEF": 1002,
                                   "2025/day/DSC_0003.NEF": 1003})
    dest = root / "dest"
    env_ = SimpleNamespace(root=root, src=src, dest=dest)
    assert backup(env_, "--yes") == 0
    return env_


def backup(env_, *extra):
    return sb.main(argv_for(env_.src / "2025", env_.dest, env_.src, "--unexported", "copy",
                            *extra))


def edit(env_, name="DSC_0001.NEF", data=b"edited on the source" * 60):
    path = env_.src / "2025/day" / name
    path.write_bytes(data)
    return path


def stamp(days_ago, hour=12):
    return (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0).strftime("%y%m%d_%H%M%S")


def make_run(dest, folder, days_ago, files):
    """A run folder of a store, made `days_ago` days ago, with `files` {rel: size}."""
    run = Path(dest) / sb.CATALOG_FOLDER / folder / stamp(days_ago)
    for rel, size in files.items():
        path = run / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"s" * size)
    return run


ASKED = []                              # the prompts the last `answers` was asked


def answers(monkeypatch, *replies):
    queue = list(replies)
    ASKED.clear()

    def fake(prompt=""):
        ASKED.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)
    monkeypatch.setattr("builtins.input", fake)
    return queue


def chooser_asked():
    return any(prompt.startswith("Choose one or more") for prompt in ASKED)


def type_what_is_asked(monkeypatch, then=()):
    """A person who types the number each typed confirmation asks for, and
    answers `then` (in order) to every other question."""
    rest = list(then)

    def answer(prompt=""):
        found = re.search(r"type ([\d,]+) and press Enter", prompt)
        if found is not None:
            return found.group(1)
        if rest:
            return rest.pop(0)
        raise EOFError
    monkeypatch.setattr("builtins.input", answer)


def files_under(path):
    path = Path(path)
    return {item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file()} \
        if path.exists() else set()


def originals_rows(dest):
    return db(dest, "SELECT stored_rel, source_rel, size, sha256 FROM originals")


# ---- 1. the values of a *_KEEP_DAYS setting and option ----------------------------------------
@pytest.mark.parametrize("value", ["-1", "abc", "1.5", "nevr", ""])
def test_a_bad_option_value_is_refused(plain, value, capsys):
    """(star) Anything but N, 0 or never: exit 2, before anything is read."""
    before = snapshot(plain.dest)
    for option in ("--versions-keep-days", "--originals-keep-days"):
        assert backup(plain, "--dry-run", option, "never") == 0     # the good ones pass
        assert backup(plain, "--dry-run", option, "12") == 0
        assert backup(plain, "--dry-run", option, value) == 2
    for option in ("--keep-days", "--versions-keep-days", "--originals-keep-days"):
        assert mir.main(["--source", str(plain.src / "2025"), "--dest", str(plain.dest),
                         "--dry-run", option, value]) == 2
    assert snapshot(plain.dest) == before


@pytest.mark.parametrize("name, value", [("VERSIONS_KEEP_DAYS", -1),
                                         ("ORIGINALS_KEEP_DAYS", "never"),
                                         ("VERSIONS_KEEP_DAYS", 2.5),
                                         ("ORIGINALS_KEEP_DAYS", True),
                                         ("ORIGINALS_FOLDER", "versions"),
                                         ("ORIGINALS_FOLDER", "a\\b")])
def test_a_bad_setting_of_the_backup_is_refused(plain, monkeypatch, name, value):
    """(star) A setting that is not N, 0 or None, or a store folder that is not
    one plain name of its own: every script stops before reading, exit 2."""
    monkeypatch.setattr(sb, name, value)
    assert backup(plain, "--dry-run") == 2
    assert sb.main(["--space-report", "--dest", str(plain.dest)]) == 2
    assert mir.main(["--source", str(plain.src / "2025"), "--dest", str(plain.dest),
                     "--dry-run"]) == 2


@pytest.mark.parametrize("name, value", [("MIRROR_VERSIONS_KEEP_DAYS", -1),
                                         ("MIRROR_ORIGINALS_KEEP_DAYS", "30"),
                                         ("MIRROR_VERSIONS_KEEP_DAYS", 1.0),
                                         ("MIRROR_TRASH_VERSIONS_FOLDER", "trash"),
                                         ("MIRROR_TRASH_ORIGINALS_FOLDER", "originals")])
def test_a_bad_setting_of_the_mirror_is_refused(plain, monkeypatch, name, value):
    """(star)"""
    monkeypatch.setattr(mir, name, value)
    assert mir.main(["--source", str(plain.src / "2025"), "--dest", str(plain.dest),
                     "--dry-run"]) == 2


def test_never_and_none_are_the_same(plain):
    """(star) `never` on the command line is None in the script."""
    args = sb._build_parser().parse_args(["--source", "S", "--dest", "D",
                                          "--versions-keep-days", "never",
                                          "--originals-keep-days", "7"])
    assert args.versions_keep_days is None and args.originals_keep_days == 7
    assert sb.VERSIONS_KEEP_DAYS is None and sb.ORIGINALS_KEEP_DAYS is None    # the defaults
    assert mir.MIRROR_VERSIONS_KEEP_DAYS == mir.MIRROR_ORIGINALS_KEEP_DAYS == 30
    assert backup(plain, "--dry-run", "--versions-keep-days", "never") == 0


# ---- 2. expiry by age ------------------------------------------------------------------------------
@pytest.mark.parametrize("days, gone", [("30", {"old"}), ("never", set()), ("0", {"old", "new"})])
def test_the_backup_deletes_its_run_folders_past_their_days(plain, monkeypatch, days, gone):
    """(star) N: a run folder older than N days is deleted by the first real
    run on or after its day; never: none; 0: every one there, but only after
    the typed number (--yes stops, nothing changed: SF1 of the review). A
    folder not named like a run, and a file loose in the store, are never
    touched."""
    old = make_run(plain.dest, "versions", 40, {"2025/day/a.NEF": 10})
    new = make_run(plain.dest, "versions", 1, {"2025/day/b.NEF": 20})
    foreign = Path(plain.dest, sb.CATALOG_FOLDER, "versions", "keep me")
    foreign.mkdir()
    (foreign / "mine.txt").write_bytes(b"x")
    loose = Path(plain.dest, sb.CATALOG_FOLDER, "versions", "loose.txt")
    loose.write_bytes(b"y")
    source = snapshot(plain.src)
    assert backup(plain, "--dry-run", "--versions-keep-days", days) == 0
    assert old.exists() and new.exists()                   # a dry-run deletes nothing
    if days == "0":
        before = snapshot(plain.dest)
        assert backup(plain, "--yes", "--versions-keep-days", days) == 2
        assert snapshot(plain.dest) == before
        answers(monkeypatch, "2", "y")                      # the typed number, then Proceed
        assert backup(plain, "--versions-keep-days", days) == 0
    else:
        assert backup(plain, "--yes", "--versions-keep-days", days) == 0
    assert snapshot(plain.src) == source
    assert {name for name, run in (("old", old), ("new", new)) if not run.exists()} == gone
    assert (foreign / "mine.txt").exists() and loose.exists()


def test_the_normal_backup_never_deletes_in_the_mirrors_trash(plain):
    """(star) The normal backup lists the mirror's trash (the same disk) and
    deletes only in its own folders."""
    trash = make_run(plain.dest, mir.MIRROR_TRASH_FOLDER, 400, {"2025/day/a.NEF": 10})
    versions = make_run(plain.dest, mir.MIRROR_TRASH_VERSIONS_FOLDER, 400, {"x.NEF": 10})
    assert backup(plain, "--yes", "--versions-keep-days", "0", "--originals-keep-days", "0") == 0
    assert trash.exists() and versions.exists()


def test_the_mirror_deletes_each_trash_folder_by_its_own_days(plain, monkeypatch):
    """(star) trash (MIRROR_KEEP_DAYS), trash_versions, trash_originals: each
    its own days; the normal backup's versions\\ is never the mirror's."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    removed = make_run(plain.dest, mir.MIRROR_TRASH_FOLDER, 20, {"a.NEF": 10})
    replaced = make_run(plain.dest, mir.MIRROR_TRASH_VERSIONS_FOLDER, 20, {"b.NEF": 10})
    originals = make_run(plain.dest, mir.MIRROR_TRASH_ORIGINALS_FOLDER, 20, {"c.tif": 10})
    versions = make_run(plain.dest, "versions", 400, {"d.NEF": 10})
    common = ["--source", str(plain.src / "2025"), "--base", str(plain.src), "--dest",
              str(plain.dest), "--unexported", "copy", "--yes"]
    assert mir.main(common + ["--keep-days", "30", "--versions-keep-days", "10",
                              "--originals-keep-days", "never"]) == 0
    assert removed.exists() and not replaced.exists() and originals.exists()
    assert versions.exists()
    assert mir.main(common + ["--keep-days", "15"]) == 0
    assert not removed.exists() and originals.exists()      # 30 days: not yet


def test_expiry_never_goes_through_a_junction(plain, tmp_path):
    """(star) A junction named like an old run is not a run: never followed,
    never deleted; neither is a junction inside a run that expires."""
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "precious.NEF").write_bytes(b"p" * 10)
    store = Path(plain.dest, sb.CATALOG_FOLDER, "versions")
    store.mkdir(parents=True, exist_ok=True)
    link = store / stamp(400)
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True)
    if made.returncode != 0:
        pytest.skip("cannot make a junction here")
    run = make_run(plain.dest, "versions", 300, {"a.NEF": 10})
    inner = subprocess.run(["cmd", "/c", "mklink", "/J", str(run / "inner"), str(target)],
                           capture_output=True)
    assert inner.returncode == 0
    assert backup(plain, "--yes", "--versions-keep-days", "30") == 0
    assert (target / "precious.NEF").exists() and os.path.lexists(link)
    assert not (run / "a.NEF").exists()                     # the run's own file went


def test_a_store_that_is_a_junction_stops_a_real_run(plain, tmp_path):
    """(star) originals\\ (or versions\\) leading somewhere else: a real run of
    the normal backup writes, moves or deletes nothing there (exit 2)."""
    target = tmp_path / "planted"
    old = target / stamp(400)
    old.mkdir(parents=True)
    (old / "precious.NEF").write_bytes(b"p")
    store = Path(plain.dest, sb.CATALOG_FOLDER, ORIGINALS)
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(store), str(target)],
                          capture_output=True)
    if made.returncode != 0:
        pytest.skip("cannot make a junction here")
    edit(plain)
    assert backup(plain, "--yes", "--update-changed") == 2
    assert (old / "precious.NEF").exists()
    assert sorted(path.name for path in target.iterdir()) == [old.name]


# ---- 3. 0 days: deleted at once, after the typed number ------------------------------------------
def test_zero_days_with_yes_stops_and_changes_nothing(plain, monkeypatch, capsys):
    """(star) VERSIONS_KEEP_DAYS = 0 and a changed file to replace: the old
    copy would be deleted at once, which needs a person; --yes stops (exit 2)
    and nothing changes."""
    monkeypatch.setattr(sb, "VERSIONS_KEEP_DAYS", 0)
    edit(plain)
    before = snapshot(plain.dest)
    capsys.readouterr()
    assert backup(plain, "--yes", "--update-changed") == 2
    assert "needs a person who types the number" in capsys.readouterr().err
    assert snapshot(plain.dest) == before


def test_zero_days_with_a_person_deletes_at_once(plain, monkeypatch, photobackup_log_dir):
    """(star) A person types the number: the old copy is deleted, not kept in
    versions\\; a wrong number changes nothing."""
    path = edit(plain)
    before = snapshot(plain.dest)
    answers(monkeypatch, "7")                            # 1 file: a wrong number
    assert backup(plain, "--update-changed", "--versions-keep-days", "0") == 2
    assert snapshot(plain.dest) == before
    answers(monkeypatch, "1", "y")                       # the typed number, then Proceed
    assert backup(plain, "--update-changed", "--versions-keep-days", "0") == 0
    assert (plain.dest / "2025/day/DSC_0001.NEF").read_bytes() == path.read_bytes()
    assert not files_under(Path(plain.dest, sb.CATALOG_FOLDER, "versions"))
    assert "DELETED (VERSIONS_KEEP_DAYS = 0) " in latest_log(photobackup_log_dir)


def test_zero_days_in_the_mirror_unattended_stops(plain, monkeypatch):
    """(star) The mirror: a trash folder with 0 days that would delete
    something needs the typed number; --unattended stops, nothing changed;
    typed, it deletes at once."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    common = ["--source", str(plain.src / "2025"), "--base", str(plain.src), "--dest",
              str(plain.dest), "--unexported", "copy"]
    assert mir.main(common + ["--yes"]) == 0
    edit(plain)
    before = snapshot(plain.dest)
    assert mir.main(common + ["--unattended", "--versions-keep-days", "0"]) == 2
    assert snapshot(plain.dest) == before
    type_what_is_asked(monkeypatch)
    assert mir.main(common + ["--yes", "--versions-keep-days", "0"]) == 0
    assert not files_under(Path(plain.dest, sb.CATALOG_FOLDER, mir.MIRROR_TRASH_VERSIONS_FOLDER))


# ---- 4. TIFF originals and JPEG copies a JXL replaces -----------------------------------------------
def test_tiff_copies_go_to_originals_with_their_rows(env):
    """(star) A TIFF copy its JXL replaced is a TIFF original: originals\\<run>,
    one `originals` row each (where it is, the name it had, size, SHA-256), and
    the row goes with the file when its run folder expires."""
    assert jxl_backup(env) == 0                                 # TIFF copies
    hashes = {key.replace("\\", "/"): sha for key, sha in db(env.dest,
                                                             "SELECT rel, sha256 FROM files")}
    source = snapshot(env.src)
    assert jxl_backup(env, "--tiff", "jxl") == 0
    assert snapshot(env.src) == source
    assert TIFFS <= under(env.dest, ORIGINALS) and not under(env.dest, VERSIONS)
    rows = originals_rows(env.dest)
    assert {source_rel.replace("\\", "/") for _stored, source_rel, _size, _sha in rows} == TIFFS
    for stored, source_rel, size, sha in rows:
        assert stored.startswith(os.path.join(sb.CATALOG_FOLDER, ORIGINALS))
        assert (env.dest / stored).stat().st_size == size
        assert sha == hashes[source_rel.replace("\\", "/")]
    # the run folder grows old: the next run past ORIGINALS_KEEP_DAYS deletes
    # it, and its rows
    store = Path(env.dest, sb.CATALOG_FOLDER, ORIGINALS)
    (run,) = list(store.iterdir())
    old_name = stamp(40)
    run.rename(store / old_name)
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    with connection:
        for stored, *_rest in rows:
            new = stored.replace(run.name, old_name)
            connection.execute("UPDATE originals SET stored_rel = ?, key = ? WHERE stored_rel = ?",
                               (new, new.casefold(), stored))
    connection.close()
    assert jxl_backup(env, "--tiff", "jxl", "--originals-keep-days", "30") == 0
    assert not store.joinpath(old_name).exists()
    assert originals_rows(env.dest) == []
    assert JXL <= backed(env.dest)


def test_a_jpeg_copy_is_deleted_only_with_the_proof_of_its_bytes(env, photobackup_log_dir):
    """(star) A JPEG copy its JXL replaces is deleted when its bytes are the
    ones the JXL gives back (its row's SHA-256 = the group's source_sha256);
    a copy whose row says other bytes goes to versions\\ (no proof)."""
    export, other = "2025/shoot/_EXPORT/DSC_0001_v1.jpg", "2025/phone/x.jpg"
    path = env.src / other
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8a phone's photo" * 40)
    assert jxl_backup(env) == 0                                 # JPEG copies, with rows
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    with connection:                                            # the row of x.jpg: other bytes
        connection.execute("UPDATE files SET sha256 = ? WHERE key = ?",
                           ("0" * 64, os.path.join(*other.split("/")).casefold()))
    connection.close()
    assert jxl_backup(env, "--jpeg", "jxl") == 0
    have = backed(env.dest)
    assert {export + ".jxl", other + ".jxl"} <= have and not {export, other} & have
    assert export not in under(env.dest, VERSIONS)              # proven: deleted
    assert other in under(env.dest, VERSIONS)                   # no proof: kept
    log = latest_log(photobackup_log_dir)
    assert "DELETED (its JXL gives these bytes back) " + os.path.join(*export.split("/")) in log


# ---- 5. room on demand ---------------------------------------------------------------------------
def full_disk(monkeypatch, free):
    monkeypatch.setattr(sb.shutil, "disk_usage", lambda path: Usage(10 ** 9, 0, free))


def grows(monkeypatch, base, *runs):
    """Free space = base + what was deleted of `runs`."""
    sizes = {run: sum(item.stat().st_size for item in run.rglob("*") if item.is_file())
             for run in runs}
    monkeypatch.setattr(sb.shutil, "disk_usage", lambda path: Usage(
        10 ** 9, 0, base + sum(size for run, size in sizes.items() if not run.exists())))


def room_env(plain):
    """Two stores with content, and a new file of 3,000 bytes to copy."""
    old = make_run(plain.dest, "versions", 50, {"a.NEF": 2000})
    newer = make_run(plain.dest, "versions", 5, {"b.NEF": 2500})
    tiff = make_run(plain.dest, ORIGINALS, 3, {"c.tif": 4000})
    (plain.src / "2025/day/NEW.NEF").write_bytes(b"n" * 3000)
    return old, newer, tiff


def test_room_on_demand_unattended_and_dry_run_change_nothing(plain, monkeypatch, capsys):
    """(star) --yes (nobody to ask): exit 2, nothing changed, the message names
    what is set aside; a dry-run shows the options and deletes nothing."""
    old, newer, tiff = room_env(plain)
    full_disk(monkeypatch, 1000)
    before = snapshot(plain.dest)
    capsys.readouterr()
    assert backup(plain, "--dry-run", "--space-margin-gb", "0") == 0
    out = capsys.readouterr().out
    assert "NOT ENOUGH SPACE" in out and "<- recommended" in out
    assert "old versions: the oldest (" in out and "TIFF originals: all of it" in out
    assert backup(plain, "--yes", "--space-margin-gb", "0") == 2
    out = capsys.readouterr().out
    assert "set aside: old versions" in out and "TIFF originals" in out
    assert snapshot(plain.dest) == before


@pytest.mark.parametrize("reply", ["", "x", "9", "1 9", "²"])
def test_room_on_demand_a_wrong_choice_cancels(plain, monkeypatch, reply):
    """(star) Enter, a letter, a number not shown, a superscript digit: exit 2."""
    room_env(plain)
    full_disk(monkeypatch, 1000)
    before = snapshot(plain.dest)
    answers(monkeypatch, reply)
    assert backup(plain, "--space-margin-gb", "0") == 2
    assert chooser_asked()
    assert snapshot(plain.dest) == before


def test_room_on_demand_a_wrong_typed_number_cancels(plain, monkeypatch):
    """(star)"""
    room_env(plain)
    full_disk(monkeypatch, 1000)
    before = snapshot(plain.dest)
    answers(monkeypatch, "1", "99")
    assert backup(plain, "--space-margin-gb", "0") == 2
    assert chooser_asked() and any(prompt.startswith("To go on, type") for prompt in ASKED)
    assert snapshot(plain.dest) == before


def test_room_on_demand_several_choices_the_larger_wins(plain, monkeypatch, capsys):
    """(star) "the oldest" and "all of it" of one store: the larger (all of
    it); with another store's option too. The typed number is the total of
    files; then the run copies."""
    old, newer, tiff = room_env(plain)
    grows(monkeypatch, 1000, old, newer, tiff)
    capsys.readouterr()
    assert backup(plain, "--dry-run", "--space-margin-gb", "0") == 0
    lines = capsys.readouterr().out.splitlines()
    numbers = {re.sub(r"\s+\d.*", "", line.split(") ", 1)[1]): line.split(")")[0].strip()
               for line in lines if re.match(r"\s+\d\) ", line)}
    oldest = numbers[next(key for key in numbers if key.startswith("old versions: the oldest"))]
    every = numbers["old versions: all of it"]
    tiffs = numbers["TIFF originals: all of it"]
    answers(monkeypatch, f"{oldest} {every} {tiffs}", "3", "y")     # 2 + 1 files
    source = snapshot(plain.src)
    assert backup(plain, "--space-margin-gb", "0") == 0
    assert snapshot(plain.src) == source
    assert not old.exists() and not newer.exists() and not tiff.exists()
    assert (plain.dest / "2025/day/NEW.NEF").exists()


def test_room_on_demand_still_not_enough_stops(plain, monkeypatch, photobackup_log_dir):
    """(star) The disk is asked again after deleting: still not enough, the
    run says so and stops (exit 2), with no second question."""
    old, newer, tiff = room_env(plain)
    full_disk(monkeypatch, 1000)                     # deleting frees nothing here
    answers(monkeypatch, "1", "1", "y")
    assert backup(plain, "--space-margin-gb", "0") == 2
    assert not old.exists()                          # what was chosen was deleted
    assert not (plain.dest / "2025/day/NEW.NEF").exists()
    assert "Still not enough space after deleting" in latest_log(photobackup_log_dir)


def test_room_on_demand_a_choice_that_cannot_cover_it_deletes_nothing(plain, monkeypatch):
    """(star) Options that together free less than is missing: nothing is
    deleted, exit 2."""
    old, newer, tiff = room_env(plain)
    full_disk(monkeypatch, 1000)
    (plain.src / "2025/day/BIG.NEF").write_bytes(b"b" * 6000)   # 9,000 bytes to copy now
    before = snapshot(plain.dest)
    answers(monkeypatch, "1", "1")
    assert backup(plain, "--space-margin-gb", "0") == 2
    assert chooser_asked()
    assert snapshot(plain.dest) == before


# ---- 6. the space block and --space-report ---------------------------------------------------------
def test_the_space_block_of_every_run(plain, capsys):
    """The block right after the Space line: one row per store that exists,
    its files, size, days and next deletion; what expires; free after."""
    make_run(plain.dest, "versions", 40, {"a.NEF": 1000, "b.NEF": 2000})
    make_run(plain.dest, ORIGINALS, 2, {"c.tif": 5000})
    make_run(plain.dest, mir.MIRROR_TRASH_FOLDER, 1, {"d.NEF": 700})
    capsys.readouterr()
    assert backup(plain, "--dry-run", "--versions-keep-days", "30",
                  "--originals-keep-days", "never") == 0
    out = capsys.readouterr().out
    block = out[out.index("\nSpace on "):]
    lines = block.splitlines()
    versions = next(line for line in lines if line.strip().startswith("old versions "))
    assert re.search(r"old versions\s+2\s+0\.0 GB\s+30 days\s+at the next real run", versions)
    originals = next(line for line in lines if line.strip().startswith("TIFF originals "))
    assert re.search(r"TIFF originals\s+1\s+0\.0 GB\s+never\s+only when space is short",
                     originals)
    assert any(line.strip().startswith("mirror trash ") for line in lines)
    assert "expire in this run (not in a dry-run): 2 files (0.0 GB) of old versions" in block
    assert "free after deleting: " in block and " · all " in block


def test_space_report_reads_only(plain, capsys):
    """(star) --space-report: every run folder, its dates and size, free after
    deleting each; reads only the destination (snapshot and catalog bytes
    the same), exit 0."""
    make_run(plain.dest, "versions", 40, {"a.NEF": 1000})
    make_run(plain.dest, ORIGINALS, 3, {"c.tif": 5000})
    make_run(plain.dest, mir.MIRROR_TRASH_VERSIONS_FOLDER, 3, {"d.NEF": 10})
    catalog = (plain.dest / sb.CATALOG_FOLDER / "catalog.sqlite").read_bytes()
    before = snapshot(plain.dest)
    capsys.readouterr()
    assert sb.main(["--space-report", "--dest", str(plain.dest),
                    "--versions-keep-days", "30"]) == 0
    out = capsys.readouterr().out
    assert snapshot(plain.dest) == before
    assert (plain.dest / sb.CATALOG_FOLDER / "catalog.sqlite").read_bytes() == catalog
    assert "Backup space of" in out and "(read only: nothing is changed)" in out
    assert "expires at the next backup run" in out
    assert "mirror trash: old versions" in out and "Free after deleting:" in out
    # a backup without a catalog, and one that is not there
    bare = plain.root / "bare"
    make_run(bare, "versions", 1, {"x.NEF": 5})
    assert sb.main(["--space-report", "--dest", str(bare)]) == 0
    assert not (bare / sb.CATALOG_FOLDER / "catalog.sqlite").exists()
    assert sb.main(["--space-report", "--dest", str(plain.root / "nowhere")]) == 2
    assert sb.main(["--space-report", "--dest", str(bare), "--source", str(plain.src)]) == 2


def test_space_report_a_store_that_cannot_be_read_is_exit_1(plain, monkeypatch, capsys):
    """(star)"""
    make_run(plain.dest, "versions", 1, {"x.NEF": 5})
    real = sb._store_runs

    def failing(dest, folder):
        if folder == "versions":
            return [], [], f"{folder} cannot be read (test)"
        return real(dest, folder)
    monkeypatch.setattr(sb, "_store_runs", failing)
    assert sb.main(["--space-report", "--dest", str(plain.dest)]) == 1
    assert "CANNOT BE READ" in capsys.readouterr().out


# ---- 7. the catalog's originals table ---------------------------------------------------------------
def test_an_unsafe_originals_row_is_a_damaged_catalog(plain):
    """(star) A stored_rel that leads outside the backup stops every run."""
    assert backup(plain, "--dry-run") == 0
    connection = sqlite3.connect(str(plain.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    with connection:
        connection.execute("CREATE TABLE IF NOT EXISTS originals (key TEXT PRIMARY KEY, "
                           "stored_rel TEXT NOT NULL, source_rel TEXT NOT NULL, size INTEGER "
                           "NOT NULL, mtime_ns INTEGER NOT NULL, sha256 TEXT, stored_at TEXT "
                           "NOT NULL)")
        connection.execute("INSERT INTO originals VALUES ('k', '..\\elsewhere\\x.tif', "
                           "'2025\\x.tif', 1, 1, NULL, 'now')")
    connection.close()
    assert backup(plain, "--dry-run") == 2


# ---- 8. the menu -----------------------------------------------------------------------------------
def test_the_menu_passes_the_five_options(monkeypatch, menu_config):
    """(star) Every backup, manifest and mirror the menu runs gets the days:
    this computer's choice, else the script's own setting; a verify, an
    analysis and the restore never."""
    commands = []

    class Done:
        returncode = 0
    monkeypatch.setattr(menu.subprocess, "run", lambda command, **kwargs: commands.append(
        command[2:]) or Done())
    monkeypatch.setattr(menu, "_menu_title", lambda: None)
    menu.run_backend(["--source", "S", "--dest", "D", "--yes"])
    menu.run_manifest(["--manifest", "m.csv", "--verify", "quick"])
    menu.run_restore(["--backup", "B"])
    menu_config.write_text(json.dumps({"originals_keep_days": "60",
                                       "mirror_keep_days": "never"}), encoding="utf-8")
    menu.run_manifest(["--manifest", "m.csv", "--dest", "D"])
    menu.run_mirror(["--manifest", "m.csv", "--dest", "D"])

    def value(tail, option):
        return tail[tail.index(option) + 1] if option in tail else None
    first, verify, restore, manifest, mirror = commands
    assert value(first, "--versions-keep-days") == sb._keep_days_text(sb.VERSIONS_KEEP_DAYS)
    assert value(first, "--originals-keep-days") == sb._keep_days_text(sb.ORIGINALS_KEEP_DAYS)
    assert value(verify, "--versions-keep-days") is None and "--keep-days" not in restore
    assert value(manifest, "--originals-keep-days") == "60"
    assert value(mirror, "--keep-days") == "never"
    assert value(mirror, "--versions-keep-days") == str(mir.MIRROR_VERSIONS_KEEP_DAYS)
    assert value(mirror, "--originals-keep-days") == str(mir.MIRROR_ORIGINALS_KEEP_DAYS)


def test_the_menu_settings_table_round_trip(monkeypatch, menu_config, capsys):
    """(star) [4] Settings: the table kind | backup | mirror, each value
    editable per computer (N, 0 with a warning, never, - = the script's)."""
    config = menu.load_config()
    rows = menu._keep_days_rows(config)
    assert rows[0][1] == "never" and rows[0][2] == str(mir.MIRROR_VERSIONS_KEEP_DAYS)
    assert rows[2][1] == "-"
    # change? yes; then backup/mirror of old versions, TIFF originals, mirror removed
    script(monkeypatch, ["y", "90", "x", "0", "never", "-", "7"])
    menu.keep_days_settings(config)
    out = capsys.readouterr().out
    assert "DELETED at once" in out and "Type a whole number" in out
    saved = json.loads(menu_config.read_text(encoding="utf-8"))
    assert saved["versions_keep_days"] == "90" and saved["mirror_versions_keep_days"] == "0"
    assert saved["originals_keep_days"] == "never" and saved["mirror_originals_keep_days"] == ""
    assert saved["mirror_keep_days"] == "7"
    rows = menu._keep_days_rows(menu.load_config())
    assert [row[1:] for row in rows] == [("90", "0"),
                                         ("never", str(mir.MIRROR_ORIGINALS_KEEP_DAYS)),
                                         ("-", "7")]


def test_the_menu_item_backup_space(monkeypatch, menu_config, tmp_path):
    """(star) [9] Backup space runs --space-report on the backup it asks for."""
    calls = []
    monkeypatch.setattr(menu, "run_backend", lambda argv: calls.append(list(argv)) or 0)
    script(monkeypatch, ["9", str(tmp_path), "0"])
    assert menu.main([]) == 0
    assert calls == [["--space-report", "--dest", str(tmp_path)]]
