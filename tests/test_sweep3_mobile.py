"""mobile_backup.py 0.1.4: the findings of the third sweep (261112): T1, T4, T5,
T10, T14, T15/O4, O3, O6, O7 and the mobile part of D1.

Everything runs on synthetic trees under tmp_path; exiftool is a stand-in.
Junctions are made with `mklink /J` inside tmp_path and removed with os.rmdir.
"""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mobile_backup as mb  # noqa: E402
from conftest import make_tree, snapshot  # noqa: E402

ALBUM = "260620_Kyoto/1_Z8/_EXPORT/JXL_mobile/"
REPO = Path(__file__).resolve().parents[1]


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


def state_path(dest):
    return Path(dest) / mb.STATE_FOLDER / mb.STATE_FILE


def state_files(dest):
    return json.loads(state_path(dest).read_text(encoding="utf-8"))["files"]


def last_log(tmp_path):
    logs = sorted((tmp_path / "mlogs").glob("*.log"), key=lambda p: p.stat().st_mtime_ns)
    return "\n".join(p.read_text(encoding="utf-8") for p in logs)


def album_dir(dest, src):
    rel = Path(src).relative_to(Path(src).anchor)
    return Path(dest) / rel / "260620_Kyoto"


def junction(link, target):
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True)
    if result.returncode != 0:
        pytest.skip("cannot create a junction")


def unjunction(*links):
    for link in links:
        try:
            os.rmdir(link)              # the junction only, never its target
        except OSError:
            pass


windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions and file locks")


# ---- T1: an old file that cannot be deleted keeps its photo's new copy for later -------------
@windows
def test_t1_a_failed_delete_postpones_the_new_copy(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "b.jxl": 100, ALBUM + "c.jxl": 200, ALBUM + "d.jxl": 300})
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_b.jxl", "0001_c.jxl", "0002_d.jxl"]
    make_tree(src, {ALBUM + "a.jxl": 50,          # renumbers b, c, d
                    ALBUM + "c.jxl": 222})        # and c is exported again
    old_c = album_dir(dest, src) / "0001_c.jxl"
    with open(old_c, "rb"):                       # open in another program: cannot be deleted
        assert run(src, dest) == 1
    assert "0002_c.jxl" not in mirror(dest)       # 0.1.3: copied, and 0001_c.jxl forgotten
    record = state_files(dest)[str(src / ALBUM / "c.jxl")]
    assert record["dest"].endswith("0001_c.jxl") and record["size"] == 200
    assert "POSTPONED copy" in last_log(tmp_path)
    assert run(src, dest) == 0                    # the old file is free now
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl", "0002_c.jxl", "0003_d.jxl"]
    assert (album_dir(dest, src) / "0002_c.jxl").stat().st_size == 222
    assert run(src, dest) == 0                    # 0.1.3: SKIPPED album, exit 1, for good
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl", "0002_c.jxl", "0003_d.jxl"]


def test_t1_a_failed_delete_of_a_photo_gone_keeps_its_entry(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100, ALBUM + "b.jxl": 200})
    assert run(src, dest) == 0
    (src / ALBUM / "b.jxl").unlink()
    real = mb._unlink_ours

    def busy(path):
        if path.endswith("0001_b.jxl"):
            raise PermissionError(32, "in use", path)
        return real(path)

    monkeypatch.setattr(mb, "_unlink_ours", busy)
    assert run(src, dest) == 1
    assert str(src / ALBUM / "b.jxl") in state_files(dest)
    monkeypatch.setattr(mb, "_unlink_ours", real)
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_a.jxl"]


# ---- T5: one run at a time -------------------------------------------------------------------
def lock_of(dest):
    return Path(dest) / mb.STATE_FOLDER / mb.LOCK_FILE


def plant_lock(dest, pid, created=None):
    lock_of(dest).parent.mkdir(parents=True, exist_ok=True)
    lock_of(dest).write_text(json.dumps({"pid": pid, "created": created,
                                         "started": "2026-11-12 03:00:00"}), encoding="utf-8")


def test_t5_a_live_run_holding_the_lock_stops_the_second_one(tmp_path, capsys):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    assert run(src, dest) == 0
    make_tree(src, {ALBUM + "b.jxl": 200})
    _status, created = mb._process_status(os.getpid())
    plant_lock(dest, os.getpid(), created)        # this very process: alive
    before = state_path(dest).read_bytes()
    capsys.readouterr()
    assert run(src, dest) == 2                    # 0.1.3: no lock, exit 0
    assert "another mobile_backup run" in capsys.readouterr().out
    assert state_path(dest).read_bytes() == before
    assert mirror(dest) == ["0000_a.jxl"]
    assert lock_of(dest).exists()                 # someone else's lock is never removed
    assert run(src, dest, "--dry-run") == 2
    lock_of(dest).unlink()
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl"]
    assert not lock_of(dest).exists()


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_t5_the_lock_of_a_run_that_is_gone_is_taken_over(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    pid = dead_pid()
    assert mb._process_status(pid)[0] == "dead"
    plant_lock(dest, pid)
    assert run(src, dest) == 0
    assert "LOCK taken over" in last_log(tmp_path)
    assert not lock_of(dest).exists()
    assert mirror(dest) == ["0000_a.jxl"]


def test_t5_a_reused_pid_is_not_the_run_that_wrote_the_lock(tmp_path):
    if sys.platform != "win32":
        pytest.skip("start times are read on Windows")
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    _status, created = mb._process_status(os.getpid())
    plant_lock(dest, os.getpid(), created + 1)    # same PID, another start time
    assert run(src, dest) == 0
    assert "LOCK taken over" in last_log(tmp_path)


def test_t5_the_lock_is_removed_after_errors_and_exceptions(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100, ALBUM + "b.jxl": 200})
    real = mb._copy

    def killed(source, target):
        raise RuntimeError("killed")

    monkeypatch.setattr(mb, "_copy", killed)
    with pytest.raises(RuntimeError):
        run(src, dest)
    assert not lock_of(dest).exists()
    monkeypatch.setattr(mb, "_copy", real)
    assert run(src, dest) == 0
    assert not lock_of(dest).exists()
    assert run(src, dest, "--dry-run") == 0
    assert not lock_of(dest).exists()             # a dry run never makes one


def test_t5_a_second_run_inside_the_first_one_changes_nothing(tmp_path, monkeypatch):
    """The m2 scenario of the 261112 sweep, in one process: run B starts while
    run A is copying. 0.1.3: both exit 0 and A's final save forgot B's copy."""
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100, ALBUM + "b.jxl": 200})
    assert run(src, dest) == 0
    make_tree(src, {ALBUM + "a.jxl": 111})        # run A copies a again
    real = mb._copy
    second = []

    def copy(source, target):
        if not second:
            make_tree(src, {ALBUM + "c.jxl": 300})
            second.append(run(src, dest))         # run B, while A holds the mirror
        return real(source, target)

    monkeypatch.setattr(mb, "_copy", copy)
    assert run(src, dest) == 0
    assert second == [2]
    monkeypatch.setattr(mb, "_copy", real)
    assert run(src, dest) == 0                    # c is copied now
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl", "0002_c.jxl"]


def test_t5_process_status_never_ends_a_process():
    status, _created = mb._process_status(os.getpid())
    assert status == "alive"                      # and this process is still here to say it
    assert mb._process_status(dead_pid())[0] == "dead"


# ---- T4: a destination reached through a junction --------------------------------------------
@windows
def test_t4_a_destination_through_a_junction_into_the_source_is_refused(tmp_path):
    src = tmp_path / "G" / "2026"
    make_tree(src, {ALBUM + "a.jxl": 100})
    inner = src / "mirror_here"
    link = tmp_path / "dest_link"
    junction(link, inner)
    try:
        before = snapshot(src)
        assert run(src, link) == 2                # 0.1.3: exit 0, the mirror inside the source
        assert snapshot(src) == before
        assert "really" in last_log(tmp_path)
    finally:
        unjunction(link)


@windows
def test_t4_a_source_through_a_junction_that_holds_the_destination_is_refused(tmp_path):
    src = tmp_path / "G" / "2026"
    make_tree(src, {ALBUM + "a.jxl": 100})
    link = tmp_path / "src_link"
    junction(link, tmp_path / "G")
    try:
        dest = src / "mirror"
        assert mb.main(["--source", str(link), "--dest", str(dest)]) == 2
        assert not dest.exists()
    finally:
        unjunction(link)


# ---- T10: on the disk before the name; state.json.bak --------------------------------------
def test_t10_copies_and_state_are_synced_before_their_names(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100, ALBUM + "b.jxl": 200})
    synced = []
    real = os.fsync

    def fsync(fd):
        synced.append(fd)
        return real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    assert run(src, dest) == 0
    assert len(synced) >= 2 + 1                   # two copies, the state (0.1.3: none)


def test_t10_the_backup_is_the_state_of_the_last_finished_run(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    assert run(src, dest) == 0
    backup = Path(str(state_path(dest)) + mb.STATE_BACKUP_SUFFIX)
    assert backup.read_bytes() == state_path(dest).read_bytes()
    make_tree(src, {ALBUM + "b.jxl": 200})
    assert run(src, dest, "--dry-run") == 0
    assert len(json.loads(backup.read_text(encoding="utf-8"))["files"]) == 1
    assert run(src, dest) == 0
    assert len(json.loads(backup.read_text(encoding="utf-8"))["files"]) == 2


def test_t10_an_unreadable_state_uses_the_backup_and_deletes_nothing(tmp_path, capsys):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100, ALBUM + "b.jxl": 200})
    assert run(src, dest) == 0
    (src / ALBUM / "b.jxl").unlink()              # gone: a normal run deletes 0001_b.jxl
    make_tree(src, {ALBUM + "c.jxl": 300})
    state_path(dest).write_text("{ broken", encoding="utf-8")
    capsys.readouterr()
    assert run(src, dest) == 1                    # 0.1.3: exit 2 for good
    out = capsys.readouterr().out
    assert "cannot read the state file" in out and "state.json.bak" in out
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl", "0001_c.jxl"]    # nothing deleted
    assert Path(str(state_path(dest)) + ".unreadable").read_text(encoding="utf-8") == "{ broken"
    assert run(src, dest) == 0                    # a normal run again
    assert mirror(dest) == ["0000_a.jxl", "0001_c.jxl"]


def test_t10_an_unreadable_state_without_a_backup_still_stops(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    assert run(src, dest) == 0
    Path(str(state_path(dest)) + mb.STATE_BACKUP_SUFFIX).unlink()
    state_path(dest).write_text("[]", encoding="utf-8")       # JSON, but not a state
    assert run(src, dest) == 2
    assert mirror(dest) == ["0000_a.jxl"]


# ---- T14: a mirror folder that does not answer is not an empty one ----------------------------
def deny_mirror_album(monkeypatch, folder):
    marker = os.path.normcase(str(folder))
    real = os.stat

    def stat(path, *args, **kwargs):
        if marker in os.path.normcase(os.fspath(path)):
            raise PermissionError(13, "Access is denied", os.fspath(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)


def test_t14_an_unreadable_mirror_album_forgets_and_deletes_nothing(tmp_path, monkeypatch, capsys):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100, ALBUM + "b.jxl": 200})
    assert run(src, dest) == 0
    (src / ALBUM / "b.jxl").unlink()
    with monkeypatch.context() as patch:
        deny_mirror_album(patch, album_dir(dest, src))
        capsys.readouterr()
        assert run(src, dest) == 1                # 0.1.3: exit 0, b forgotten for good
        assert "cannot read the mirror folder" in capsys.readouterr().out
    assert str(src / ALBUM / "b.jxl") in state_files(dest)
    assert mirror(dest) == ["0000_a.jxl", "0001_b.jxl"]
    assert run(src, dest) == 0
    assert mirror(dest) == ["0000_a.jxl"]


def test_t14_mirror_stat_says_absent_only_when_it_is():
    assert mb._mirror_stat(os.path.join(os.getcwd(), "no such folder", "x.jxl")) == (None, None)
    size, error = mb._mirror_stat(__file__)
    assert size == os.path.getsize(__file__) and error is None


# ---- T15 + O4: links and junctions in the sources are not followed ---------------------------
@windows
def test_t15_a_junction_to_a_folder_above_is_not_a_loop(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    loop = src / "260620_Kyoto" / "loop"
    junction(loop, src)                           # 0.1.3: walked until the path broke
    try:
        assert run(src, dest) == 0
        assert mirror(dest) == ["0000_a.jxl"]
        assert "LINK not followed" in last_log(tmp_path)
    finally:
        unjunction(loop)


@windows
def test_t15_what_is_behind_a_junction_is_not_mirrored_and_is_said(tmp_path, capsys):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    elsewhere = tmp_path / "E" / "260701_Nara"
    make_tree(elsewhere, {"_EXPORT/JXL_mobile/n.jxl": 50})
    link = src / "260701_Nara"
    junction(link, elsewhere)
    try:
        capsys.readouterr()
        assert run(src, dest) == 0
        out = capsys.readouterr().out
        assert mirror(dest) == ["0000_a.jxl"]     # 0.1.3: n.jxl too, through the junction
        assert "1 link(s)/junction(s) in the sources not followed" in out
    finally:
        unjunction(link)


@windows
def test_t15_a_capture_one_cache_junction_is_only_counted_in_the_log(tmp_path, capsys):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    cache = src / "260620_Kyoto" / "CaptureOne" / "Cache"
    junction(cache, tmp_path / "C_cache" / "Proxies")
    try:
        capsys.readouterr()
        assert run(src, dest) == 0
        assert "not followed" not in capsys.readouterr().out
        assert "1 Capture One cache folder(s) are links/junctions" in last_log(tmp_path)
    finally:
        unjunction(cache)


@windows
def test_t15_a_photo_mirrored_through_a_junction_is_not_deleted_as_gone(tmp_path, monkeypatch,
                                                                       capsys):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    elsewhere = tmp_path / "E" / "260701_Nara"
    make_tree(elsewhere, {"_EXPORT/JXL_mobile/n.jxl": 50})
    link = src / "260701_Nara"
    junction(link, elsewhere)
    try:
        with monkeypatch.context() as patch:      # what 0.1.3 did: walk through it
            patch.setattr(mb, "_is_link", lambda path: False)
            assert run(src, dest) == 0
        assert mirror(dest) == ["0000_a.jxl", "0000_n.jxl"]
        capsys.readouterr()
        assert run(src, dest) == 0                # 0.1.4 does not walk there: not proof it is gone
        assert mirror(dest) == ["0000_a.jxl", "0000_n.jxl"]
        assert "behind a link not followed" in capsys.readouterr().out
    finally:
        unjunction(link)


# ---- O3: a run that would empty the mirror changes nothing -----------------------------------
def renamed_mobile_folders(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    for event in ("260620_Kyoto", "260701_Nara"):
        make_tree(src, {f"{event}/1_Z8/_EXPORT/JXL_mobile/p{i}.jxl": 100 + i for i in range(3)})
    assert run(src, dest) == 0
    for event in ("260620_Kyoto", "260701_Nara"):
        folder = src / event / "1_Z8" / "_EXPORT"
        (folder / "JXL_mobile").rename(folder / "JXL_mobile_v2")
    return src, dest


def test_o3_a_mass_delete_is_stopped(tmp_path, capsys):
    src, dest = renamed_mobile_folders(tmp_path)
    before = mirror(dest)
    state = state_path(dest).read_bytes()
    capsys.readouterr()
    assert run(src, dest, "--dry-run") == 1
    assert "STOPPED" in capsys.readouterr().out
    assert run(src, dest) == 1                    # 0.1.3: delete 6, exit 0
    assert "--allow-mass-delete" in capsys.readouterr().out
    assert mirror(dest) == before
    assert state_path(dest).read_bytes() == state
    assert run(src, dest, "--allow-mass-delete") == 0
    assert mirror(dest) == []


def test_o3_photos_moved_or_a_few_deleted_are_not_a_mass_delete(tmp_path):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + f"p{i}.jxl": 100 + i for i in range(10)})
    assert run(src, dest) == 0
    os.rename(src / "260620_Kyoto", src / "260620_Kyoto_trip")      # 10 gone, 10 new
    assert run(src, dest) == 0
    assert len(mirror(dest)) == 10
    for i in range(2):                            # 2 of 10: below MAX_DELETE_PERCENT
        (src / "260620_Kyoto_trip" / "1_Z8" / "_EXPORT" / "JXL_mobile" / f"p{i}.jxl").unlink()
    assert run(src, dest) == 0
    assert len(mirror(dest)) == 8


def test_o3_the_limits_are_settings(tmp_path, monkeypatch):
    src, dest = renamed_mobile_folders(tmp_path)
    monkeypatch.setattr(mb, "MAX_DELETE_PERCENT", 0)          # 0 turns the check off
    assert run(src, dest) == 0
    assert mirror(dest) == []


# ---- O7: a state.json held for a moment is tried again ------------------------------------
def test_o7_the_state_is_put_in_place_again_after_a_moment(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    real = os.replace
    tries = []

    def replace(old, new, *args, **kwargs):
        if os.fspath(new).endswith(mb.STATE_FILE):
            tries.append(new)
            if len(tries) <= 2:
                raise PermissionError(32, "in use by an antivirus", os.fspath(new))
        return real(old, new, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(mb.time, "sleep", lambda seconds: None)
    assert run(src, dest) == 0                    # 0.1.3: exit 1, "the run stopped"
    assert len(tries) >= 3
    assert state_files(dest)


def test_o7_a_state_that_stays_held_still_stops_the_run(tmp_path, monkeypatch):
    src, dest = tmp_path / "G" / "2026", tmp_path / "H"
    make_tree(src, {ALBUM + "a.jxl": 100})
    real = os.replace
    tries = []

    def replace(old, new, *args, **kwargs):
        if os.fspath(new).endswith(mb.STATE_FILE):
            tries.append(new)
            raise PermissionError(32, "in use", os.fspath(new))
        return real(old, new, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(mb.time, "sleep", lambda seconds: None)
    assert run(src, dest) == 1
    assert len(tries) >= mb.STATE_REPLACE_TRIES


# ---- O6 and D1: the README names every setting and flag ----------------------------------
def top_settings():
    """The UPPER_CASE names assigned at the top of mobile_backup.py (before the first def)."""
    tree = ast.parse((REPO / "mobile_backup.py").read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            break
        if isinstance(node, ast.Assign):
            names += [target.id for target in node.targets
                      if isinstance(target, ast.Name) and re.fullmatch(r"[A-Z][A-Z0-9_]+", target.id)]
    return names


def test_d1_the_mobile_readme_names_every_setting_and_flag():
    readme = (REPO / "README_mobile_backup.md").read_text(encoding="utf-8")
    names = top_settings()
    assert "MAX_DELETE_PERCENT" in names and "LOCK_FILE" in names
    for name in names:
        assert f"`{name}`" in readme, f"{name} is missing from README_mobile_backup.md"
    for action in mb._build_parser()._actions:
        for option in action.option_strings:
            if option.startswith("--") and option != "--help":
                assert f"`{option}`" in readme, f"{option} is missing from README_mobile_backup.md"
    assert mb.__version__ in readme


def test_o6_the_freefilesync_filter_leaves_out_the_temp_files():
    readme = (REPO / "README_mobile_backup.md").read_text(encoding="utf-8")
    line = next(line for line in readme.splitlines() if "FreeFileSync" in line and "exclude" in line)
    for pattern in (f"*{mb.TEMP_SUFFIX}", f"*{mb.RENAME_SUFFIX}", f"\\{mb.STATE_FOLDER}\\"):
        assert pattern in line, pattern
