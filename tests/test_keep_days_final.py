"""The fixes after the final review of 261126 (topic `keep-days`,
AI_tools/261126_Opus_review_keep-days-final.md): MF1 (the mirror deletes at
once only what a person typed for), SF3 (the copy `_copy_as_is` retires is
the kind it was counted as; the typed number is an upper bound), SF1 (no
write into a folder of `_photo_backup` that leads somewhere else), SF2 (the
mirror's box on a locked catalog during its purge), N1/N2 (texts).
Every test here failed on the code before the fixes."""
import os
import re
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
from test_tiff_jxl import SCAN_B, closed_console, env, mirror_rel, write_manifest  # noqa: F401


def build(tmp_path):
    src = make_tree(tmp_path / "SRC", {"2025/day/DSC_0001.NEF": 1001,
                                       "2025/day/DSC_0002.NEF": 1002,
                                       "2025/day/DSC_0003.NEF": 1003})
    dest = tmp_path / "dest"
    assert sb.main(argv_for(src / "2025", dest, src, "--unexported", "copy", "--yes")) == 0
    return src, dest


def mirror_argv(src, dest, *extra):
    return ["--source", str(src / "2025"), "--base", str(src), "--dest", str(dest),
            "--unexported", "copy", *extra]


def junction(link, target):
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True)
    if made.returncode != 0:
        pytest.skip("cannot make a junction here")


def stamp(days_ago, hour=12):
    return (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0).strftime("%y%m%d_%H%M%S")


def make_run(dest, folder, days_ago, files):
    run = Path(dest) / sb.CATALOG_FOLDER / folder / stamp(days_ago)
    for rel, size in files.items():
        path = run / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"s" * size)
    return run


def edit_tiff(env):
    tiff = env.src / "2025/scans/000037.tiff"
    tiff.write_text(SCAN_B + "edited", encoding="utf-8")
    stat = tiff.stat()
    os.utime(tiff, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))


def no_jxlphoto(env, monkeypatch):
    nojxl = str(env.root / "no-jxl-photo.py")
    monkeypatch.setattr(sb, "JXLPHOTO", nojxl)
    monkeypatch.setattr(sb, "_djxl_on_path", lambda: None)
    return nojxl


def holding(dest, data):
    return [p for p in Path(dest).rglob("*") if p.is_file() and p.read_bytes() == data]


# ---- MF1: the mirror deletes at once only what a person typed for --------------------------------
def test_mf1_p6_the_mirror_keeps_an_old_copy_nobody_typed_for(env, monkeypatch, capsys,
                                                              photobackup_log_dir):
    """P6 of the review: MIRROR_VERSIONS_KEEP_DAYS = 0, trash_originals 30 days, a
    TIFF copied as it is and then edited, --unattended: nothing was typed, so its
    old copy is never deleted for good."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    nojxl = no_jxlphoto(env, monkeypatch)
    man = write_manifest(env.root / "m.csv", (env.src / "2025", "6", "", "", "jxl"))
    common = ["--manifest", str(man), "--dest", str(env.dest), "--jxlphoto", nojxl,
              "--no-convert"]
    assert mir.main(common + ["--yes"]) == 0
    old_bytes = (env.dest / mirror_rel(env, "2025/scans/000037.tiff")).read_bytes()
    edit_tiff(env)
    code = mir.main(common + ["--unattended", "--versions-keep-days", "0"])
    log = latest_log(photobackup_log_dir)
    kept = holding(env.dest, old_bytes)
    assert code in (0, 2)
    assert kept, "the old version of the TIFF was deleted for good, nobody typed"
    assert all(sb.CATALOG_FOLDER in str(path) for path in kept)
    assert not [line for line in log.splitlines() if "MIRROR DELETED" in line and "000037" in line]


def _job(dest, *extra):
    args = mir.build_parser().parse_args(["--source", str(dest), "--dest", str(dest), *extra])
    job = mir.MirrorJob(args, None, "261126_120000")
    job.log = SimpleNamespace(log=lambda *_a, **_k: None, out=lambda *_a, **_k: None)
    return job


def _ours_file(job, dest, rel, data):
    path = Path(dest) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    info = path.stat()
    job.rows_by_key[rel.casefold()] = SimpleNamespace(size=info.st_size,
                                                      mtime_ns=sb._db_ns(info.st_mtime_ns))
    return path


def test_mf1_keep_old_file_deletes_only_up_to_the_typed_number(tmp_path, monkeypatch):
    """The days of `versions` are 0. Nothing typed: an old copy of ours goes to
    its trash folder. One file typed (at_once 1): the first is deleted, the
    second goes to the trash -- the typed number is an upper bound."""
    dest = tmp_path / "dest"
    log = SimpleNamespace(log=lambda *_a, **_k: None, out=lambda *_a, **_k: None)
    job = _job(dest, "--versions-keep-days", "0")
    first = _ours_file(job, dest, "a\\one.NEF", b"one" * 10)
    job.keep_old_file(str(dest), job.stamp, "a\\one.NEF", log)        # nobody typed
    assert not first.exists()
    assert holding(dest / sb.CATALOG_FOLDER / mir.MIRROR_TRASH_VERSIONS_FOLDER, b"one" * 10)

    job = _job(dest, "--versions-keep-days", "0")
    job.at_once = {"versions": 1}
    monkeypatch.setattr(mir, "take_lock", lambda dest_, log_: ("lock", None))
    monkeypatch.setattr(mir, "_typed_confirmation", lambda lines, count, log_: count == 1)
    assert job.confirm()
    second = _ours_file(job, dest, "a\\two.NEF", b"two" * 10)
    third = _ours_file(job, dest, "a\\three.NEF", b"three" * 10)
    job.keep_old_file(str(dest), job.stamp, "a\\two.NEF", log)
    job.keep_old_file(str(dest), job.stamp, "a\\three.NEF", log)
    assert not second.exists() and not third.exists()
    assert not holding(dest, b"two" * 10)                                # typed: deleted
    assert holding(dest / sb.CATALOG_FOLDER / mir.MIRROR_TRASH_VERSIONS_FOLDER, b"three" * 10)


def test_mf1_the_normal_backup_deletes_only_up_to_the_typed_number(tmp_path):
    """_SetAside: `versions` typed for 1 file; a second one is moved, not deleted."""
    dest = tmp_path / "dest"
    for name in ("one", "two"):
        (dest / "a").mkdir(parents=True, exist_ok=True)
        (dest / "a" / f"{name}.NEF").write_bytes(name.encode() * 10)
    log = SimpleNamespace(log=lambda *_a, **_k: None, out=lambda *_a, **_k: None)
    keeper = sb._SetAside({"versions": 1})
    keeper(str(dest), "261126_120000", "a\\one.NEF", log)
    keeper(str(dest), "261126_120000", "a\\two.NEF", log)
    assert not holding(dest, b"one" * 10)
    assert holding(dest / sb.CATALOG_FOLDER / "versions", b"two" * 10)


# ---- SF3: the copy _copy_as_is retires is the kind it was counted as --------------------------------
def test_sf3_a_changed_tiff_copied_as_it_is_keeps_its_old_copy_in_originals(env, monkeypatch):
    """P7 of the review: the summary said 'to originals\\' and the old copy went
    to versions\\. It goes where it was counted: originals\\."""
    nojxl = no_jxlphoto(env, monkeypatch)
    base = argv_for(env.src / "2025", env.dest, env.src, "--unexported", "copy",
                    "--export-formats", "all", "--tiff", "jxl", "--no-convert",
                    "--jxlphoto", nojxl)
    assert sb.main(base + ["--yes"]) == 0
    old_bytes = (env.dest / "2025/scans/000037.tiff").read_bytes()
    edit_tiff(env)
    assert sb.main(base + ["--yes", "--update-changed", "--versions-keep-days", "0"]) == 0
    kept = holding(env.dest, old_bytes)
    assert [str(path.relative_to(env.dest)).split(os.sep)[:2] for path in kept] \
        == [[sb.CATALOG_FOLDER, sb.ORIGINALS_FOLDER]]


def test_sf3_p9_never_more_deleted_than_typed(env, monkeypatch, photobackup_log_dir):
    """P9 of the review: VERSIONS_KEEP_DAYS = 0, the person types 1 (the changed
    NEF); the TIFF's old copy is not deleted with it."""
    nojxl = no_jxlphoto(env, monkeypatch)
    base = argv_for(env.src / "2025", env.dest, env.src, "--unexported", "copy",
                    "--export-formats", "all", "--tiff", "jxl", "--no-convert",
                    "--jxlphoto", nojxl)
    assert sb.main(base + ["--yes"]) == 0
    old_tiff = (env.dest / "2025/scans/000037.tiff").read_bytes()
    edit_tiff(env)
    (env.src / "2025/shoot/DSC_0001.NEF").write_bytes(b"edited NEF" * 50)
    asked = []

    def answer(prompt=""):
        asked.append(prompt)
        found = re.search(r"type ([\d,]+) and press Enter", prompt)
        return found.group(1) if found else "y"
    monkeypatch.setattr("builtins.input", answer)
    code = sb.main(base + ["--update-changed", "--versions-keep-days", "0"])
    typed = [re.search(r"type ([\d,]+)", p).group(1) for p in asked if "type " in p]
    log = latest_log(photobackup_log_dir)
    deleted = [line for line in log.splitlines() if "DELETED (VERSIONS_KEEP_DAYS = 0)" in line]
    assert code == 0 and typed == ["1"]
    assert len(deleted) <= 1
    assert holding(env.dest, old_tiff)


# ---- SF1: no write into a folder of _photo_backup that leads somewhere else -------------------------
def test_sf1_p1_the_mirror_stops_on_a_junctioned_versions(tmp_path, monkeypatch, capsys):
    """P1 of the review: `_photo_backup\\versions` is a junction into the source;
    the mirror would move an old copy it did not write into it. It stops."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    src, dest = build(tmp_path)
    (dest / "2025/day/DSC_0001.NEF").write_bytes(b"x" * 1500)
    junction(dest / sb.CATALOG_FOLDER / "versions", src / "2025")
    before = snapshot(src)
    code = mir.main(mirror_argv(src, dest, "--yes"))
    assert snapshot(src) == before
    assert code == 2
    out = capsys.readouterr().out
    assert "leads somewhere else" in out and "CANNOT BE USED" in out
    assert (dest / "2025/day/DSC_0001.NEF").read_bytes() == b"x" * 1500


def test_sf1_p5_move_orphans_stops_on_a_junctioned_orphans(tmp_path):
    """P5 of the review: `_photo_backup\\orphans` is a junction; --move-orphans
    stops before the first byte."""
    src, dest = build(tmp_path)
    (src / "2025/day/DSC_0002.NEF").unlink()
    other = tmp_path / "OTHER_SOURCE"
    other.mkdir()
    junction(dest / sb.CATALOG_FOLDER / sb.ORPHANS_FOLDER, other)
    before = snapshot(other)
    code = sb.main(argv_for(src / "2025", dest, src, "--unexported", "copy", "--yes",
                            "--move-orphans"))
    assert snapshot(other) == before
    assert code == 2
    assert (dest / "2025/day/DSC_0002.NEF").exists()


@pytest.mark.parametrize("script", ["backup", "mirror"])
def test_sf1_a_junctioned_tools_folder_stops_the_run(tmp_path, monkeypatch, script):
    """`_photo_backup\\tools` is a junction: _keep_tools would write the programs
    into what it leads to. Both runs stop before writing."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    src, dest = build(tmp_path)
    monkeypatch.setattr(sb, "KEEP_TOOLS_IN_BACKUP", True)
    other = tmp_path / "OTHER"
    other.mkdir()
    junction(dest / sb.CATALOG_FOLDER / sb.TOOLS_FOLDER, other)
    (src / "2025/day/NEW.NEF").write_bytes(b"n" * 100)
    if script == "backup":
        code = sb.main(argv_for(src / "2025", dest, src, "--unexported", "copy", "--yes"))
    else:
        code = mir.main(mirror_argv(src, dest, "--yes"))
    assert code == 2
    assert not list(other.iterdir())
    assert not (dest / "2025/day/NEW.NEF").exists()


# ---- SF2: the mirror, a locked catalog during its purge --------------------------------------------
class _Locker:
    def __init__(self, dest):
        self.path = os.path.join(str(dest), sb.CATALOG_FOLDER, "catalog.sqlite")
        self.conn = None

    def take(self):
        if self.conn is None:
            self.conn = sqlite3.connect(self.path, timeout=0.1, isolation_level=None)
            self.conn.execute("BEGIN IMMEDIATE")

    def release(self):
        if self.conn is not None:
            try:
                self.conn.execute("ROLLBACK")
            finally:
                self.conn.close()
                self.conn = None


def test_sf2_p2_the_mirrors_purge_on_a_locked_catalog_stops_with_its_box(tmp_path, monkeypatch,
                                                                          capsys):
    """P2 of the review, with a real lock: the purge stops, nothing is removed
    after it, and the MIRROR STOPPED box says what was deleted. Exit 1."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    src, dest = build(tmp_path)
    assert mir.main(mirror_argv(src, dest, "--yes")) == 0
    (src / "2025/day/DSC_0002.NEF").unlink()
    make_run(dest, mir.MIRROR_TRASH_FOLDER, 40, {"a.NEF": 10, "b.NEF": 10})
    locker = _Locker(dest)
    real = mir._purge_trash_run

    def purge(run_, dest_, log, folder=None, catalog=None):
        locker.take()
        return real(run_, dest_, log, folder, catalog)
    monkeypatch.setattr(mir, "_purge_trash_run", purge)
    capsys.readouterr()
    try:
        code = mir.main(mirror_argv(src, dest, "--yes"))
    finally:
        locker.release()
    out = capsys.readouterr().out
    assert code == 1
    assert "MIRROR STOPPED" in out
    assert "trash deleted for good" in out
    assert "catalog" in out
    assert (dest / "2025/day/DSC_0002.NEF").exists()           # the removals did not run


# ---- N1, N2: texts --------------------------------------------------------------------------------
def test_n1_the_menu_says_its_own_steps_run_with_yes(monkeypatch, menu_config, capsys):
    config = menu.load_config()
    script(monkeypatch, ["y", "0", "-", "-", "-", "-"])
    menu.keep_days_settings(config)
    out = " ".join(capsys.readouterr().out.split())
    assert "[1]" in out and "[7]" in out and "by hand" in out


def test_n2_a_run_folder_stamped_in_the_future_is_not_said_to_go_now(tmp_path, capsys):
    src, dest = build(tmp_path)
    make_run(dest, "versions", -400, {"x.NEF": 10})
    capsys.readouterr()
    assert sb.main(argv_for(src / "2025", dest, src, "--unexported", "copy", "--dry-run",
                            "--versions-keep-days", "0")) == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if "old versions" in line]
    assert rows and not any("at the next real run" in line or "at this run" in line
                            for line in rows)
