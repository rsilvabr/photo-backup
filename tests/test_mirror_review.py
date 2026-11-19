"""The fixes of the independent review of v0.14.0-rc1 (v0.14.0-rc2): a link
inside the backup, mass replacement, a dangling junction, a junction never
removed as an empty folder, Ctrl+C while removing, a superscript digit, a link
in an old trash run, nothing to copy on a full disk, the trash's day."""
import collections
import os
import shutil
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import mirror_backup as mir
import selective_backup as sb

from conftest import snapshot
from test_mirror import (actions, answers, backed, closed_console, env,  # noqa: F401
                         in_backup, make_trash_run, mirror, trashed)

Usage = collections.namedtuple("Usage", "total used free")


def junction(link, target):
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True)
    if made.returncode != 0:
        pytest.skip("cannot make a junction here")


# ---- finding 1: a link or junction inside the backup ------------------------------------------
def test_r1_a_junction_in_the_backup_stops_every_run(env, capsys):
    """The backup's A/day made a junction into the source itself: a mirror
    that lowered the line's mode would move source files into its trash. No
    run writes, moves or deletes through it: exit 2, nothing changed."""
    folder = in_backup(env, "A/day")
    shutil.rmtree(folder)
    junction(folder, env.src / "A" / "day")
    source_before = snapshot(env.src)
    capsys.readouterr()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 2
    out = capsys.readouterr()
    assert "holds a link or junction" in out.err
    assert "MIRROR STOPPED - nothing changed" in out.out
    assert sb.main(["--source", str(env.src / "A"), "--dest", str(env.dest), "--yes"]) == 2
    assert snapshot(env.src) == source_before


# ---- finding 2: replacing most of a line is a mass removal too --------------------------------
def test_r2_replacing_most_of_a_line_is_checked(env, monkeypatch, capsys):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_MIN", 1)
    for index in range(1, 7):                           # edit files: never counted
        (env.src / f"A/trip/DSC_000{index}.xmp").write_bytes(b"x" * 10)
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    for index in range(1, 7):
        (env.src / f"A/trip/DSC_000{index}.xmp").write_bytes(b"edited" * 10)
    assert mirror("--manifest", env.man, "--dest", env.dest, "--unattended") == 0
    for path in (env.src / "A/trip").glob("*.NEF"):     # every photo rewritten in place
        path.write_bytes(b"encrypted" * 300)
    before = snapshot(env.dest)
    capsys.readouterr()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--unattended") == 2
    assert "would remove or replace more than 20% of their files" in capsys.readouterr().out
    assert snapshot(env.dest) == before


# ---- finding 3: a junction whose target is gone is unreadable, never missing -------------------
def test_r3_a_dangling_junction_is_unreadable(tmp_path):
    target = tmp_path / "other disk" / "cache"
    target.mkdir(parents=True)
    (target / "x1.cop").write_bytes(b"x")
    link = tmp_path / "src" / "Old" / "cachefolder"
    link.parent.mkdir(parents=True)
    junction(link, target)
    assert sb._source_state(str(link / "x1.cop")) == sb.SOURCE_PRESENT
    shutil.rmtree(tmp_path / "other disk")              # the other disk unplugged
    assert sb._source_state(str(link / "x1.cop")) == sb.SOURCE_UNREADABLE
    assert sb._source_state(str(link / "sub" / "x2.cop")) == sb.SOURCE_UNREADABLE
    assert sb._source_state(str(link.parent / "gone.cop")) == sb.SOURCE_MISSING
    assert mir._dest_stat(str(link / "x1.cop"))[0] == "unreadable"


# ---- finding 4: a junction is never removed as an empty folder ---------------------------------
def test_r4_a_junction_is_never_removed_as_an_empty_folder(tmp_path):
    dest = tmp_path / "dest"
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.NEF").write_bytes(b"k")
    (dest / "A").mkdir(parents=True)
    link = dest / "A" / "trip"
    junction(link, target)
    lines = []
    sb._prune_empty_parents(str(link), str(dest), SimpleNamespace(log=lines.append))
    assert os.path.lexists(link) and (target / "keep.NEF").exists()
    assert not [line for line in lines if line.startswith("RMDIR")]


# ---- finding 6: Ctrl+C while removing ----------------------------------------------------------
def test_r6_ctrl_c_while_removing_keeps_the_box_and_the_list(env, monkeypatch, capsys,
                                                            photobackup_log_dir):
    for index in (1, 2):
        (env.src / f"A/trip/DSC_000{index}.NEF").unlink()
    real = sb._move_aside
    moved = []

    def move_then_stop(src, target):
        if moved:
            raise KeyboardInterrupt
        moved.append(real(src, target))
        return moved[-1]
    monkeypatch.setattr(sb, "_move_aside", move_then_stop)
    capsys.readouterr()
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 130
    out = capsys.readouterr().out
    assert "MIRROR INTERRUPTED" in out and mir.MIRROR_TRASH_FOLDER in out
    results = [row["result"] for row in actions(photobackup_log_dir) if row["action"] == "remove"]
    assert sorted(results) == ["planned", "to the trash"]
    assert len(trashed(env.dest)) == 1


# ---- finding 7: a superscript digit is not a choice --------------------------------------------
def test_r7_a_superscript_digit_is_not_a_choice(env, monkeypatch):
    make_trash_run(env.dest, datetime.now() - timedelta(days=5), {"A/x.NEF": 5000})
    (env.src / "A/trip/NEW.NEF").write_bytes(b"n" * 3000)
    monkeypatch.setattr(sb.shutil, "disk_usage", lambda path: Usage(10 ** 9, 0, 1000))
    answers(monkeypatch, "²")
    assert mirror("--manifest", env.man, "--dest", env.dest, "--space-margin-gb", "0",
                  "--yes") == 2


# ---- finding 8: a link inside an old trash run -------------------------------------------------
def test_r8_a_link_in_an_old_trash_run_is_no_error(env, tmp_path):
    run = make_trash_run(env.dest, datetime.now() - timedelta(days=40), {"A/x.NEF": 10})
    target = tmp_path / "linked"
    target.mkdir()
    (target / "keep.txt").write_bytes(b"k")
    junction(run / "SRC" / "link", target)
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert (target / "keep.txt").exists()


# ---- finding 9: nothing to copy needs no space -------------------------------------------------
def test_r9_nothing_to_copy_on_a_full_disk(env, monkeypatch):
    (env.src / "A/trip/DSC_0001.NEF").unlink()
    monkeypatch.setattr(sb.shutil, "disk_usage", lambda path: Usage(10 ** 9, 0, 10))
    assert mirror("--manifest", env.man, "--dest", env.dest, "--unattended") == 0
    assert "A/trip/DSC_0001.NEF" in trashed(env.dest)


# ---- finding 11: the trash goes on its day, at any hour ------------------------------------------
def test_r11_the_trash_goes_on_its_day(env):
    day = (datetime.now() - timedelta(days=mir.MIRROR_KEEP_DAYS)).date()
    run = make_trash_run(env.dest, datetime.combine(day, time(23, 59, 59)), {"A/x.NEF": 10})
    assert mirror("--manifest", env.man, "--dest", env.dest, "--yes") == 0
    assert not run.exists()
