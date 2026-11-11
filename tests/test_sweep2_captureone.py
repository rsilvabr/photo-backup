"""captureone_backup.py: the findings of the second sweep (N6-N8, N10-N16, N29).
Every folder is a synthetic tree in tmp_path, inside a hostile path; the real
Capture One folders are never read (conftest points PHOTOBACKUP_C1_* away)."""
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest

import captureone_backup as c1
import selective_backup as sb
from conftest import latest_csv, latest_log, make_tree

HOSTILE = "写真 [FINAL] 100% (2)"
STYLE = "Styles50/a.costyle"
PROFILE = "Color Profiles/DSLR/NikonD200-Generic.icm"
REAL_RUNNING = c1._captureone_running      # the fixture replaces it; N29 asks the real one


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
    yield {"appdata": appdata, "user": appdata / "CaptureOne", "install": install,
           "color": color, "dest": dest, "backup": dest / c1.BACKUP_SUBFOLDER, "root": root,
           "tmp": tmp_path}
    for path in tmp_path.rglob("*"):        # read-only test files must not block the cleanup
        try:
            if path.is_file() and not os.access(path, os.W_OK):
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass


@pytest.fixture
def new_pc(tmp_path):
    root = tmp_path / "新しいPC [C] 100% (1)"
    install = make_tree(root / "Program Files", {"CaptureOne.exe": 10})
    color = root / "color"
    color.mkdir()
    return {"appdata": root / "AppData", "install": install, "color": color}


def rels(folder):
    return {str(p.relative_to(folder)).replace("\\", "/")
            for p in Path(folder).rglob("*") if p.is_file()}


def backup(machine, *extra):
    return c1.main(["--dest", str(machine["dest"]), "--yes", *extra])


def restore(machine, *extra):
    return c1.main(["--restore-from", str(machine["dest"]), "--yes", *extra])


def revert(*extra):
    return c1.main(["--revert-last", "--yes", *extra])


def snapshot_folders(machine, prefix):
    parent = machine["appdata"] / c1.OLD_PARENT_FOLDER
    if not parent.exists():
        return []
    return sorted(p for p in parent.iterdir() if p.name.startswith(prefix))


def journal(folder):
    text = (folder / c1.JOURNAL_FILE).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def robocopy_sources(machine):
    text = (machine["backup"] / c1.RESTORE_SCRIPT).read_text(encoding="utf-8")
    return re.findall(r'robocopy "%HERE%([^"]+)"', text)


def run_restore_cmd(machine, new_pc, argument="--unattended", slash_s=True):
    script = machine["backup"] / c1.RESTORE_SCRIPT
    env = dict(os.environ, LOCALAPPDATA=str(new_pc["appdata"]),
               C1_INSTALL_TARGET=str(new_pc["install"]), C1_COLOR_TARGET=str(new_pc["color"]))
    # A string, not a list: cmd needs the whole line inside one extra pair of quotes.
    switch = "/s /c" if slash_s else "/c"
    return subprocess.run(f'cmd {switch} ""{script}" {argument}"', env=env,
                          capture_output=True, stdin=subprocess.DEVNULL, timeout=120)


# ---- N6: a junction inside Capture One's folder is named --------------------------------
def test_n6_a_junction_is_said_on_the_screen_and_in_the_log(machine, capsys, photobackup_log_dir):
    outside = make_tree(machine["tmp"] / "outside [x]", {"linked.costyle": 50})
    link = machine["user"] / "Styles60"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                   check=True, capture_output=True)
    assert backup(machine) == 0
    out = capsys.readouterr().out
    assert "linked.costyle" not in " ".join(rels(machine["backup"]))
    assert "Links/junctions not followed: 1" in out
    assert str(link) in out
    assert f"LINK not followed {link}" in latest_log(photobackup_log_dir)


def test_n6_the_dry_run_says_it_too(machine, capsys):
    outside = make_tree(machine["tmp"] / "outside [x]", {"linked.costyle": 50})
    link = machine["user"] / "Styles60"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                   check=True, capture_output=True)
    assert c1.main(["--dest", str(machine["dest"]), "--dry-run"]) == 0
    assert "Links/junctions not followed: 1" in capsys.readouterr().out


# ---- N7: read-only files do not break --force nor its undo ------------------------------
def test_n7_revert_undoes_a_force_of_read_only_files(machine):
    replaced = machine["user"] / STYLE
    added = machine["user"] / "Styles50" / "b.costyle"
    os.chmod(replaced, stat.S_IREAD)
    os.chmod(added, stat.S_IREAD)
    assert backup(machine) == 0
    wanted = replaced.read_bytes()
    os.chmod(replaced, stat.S_IWRITE | stat.S_IREAD)
    replaced.write_bytes(b"newer work")
    os.chmod(added, stat.S_IWRITE | stat.S_IREAD)
    added.unlink()
    assert restore(machine, "--force") == 0
    assert replaced.read_bytes() == wanted and added.exists()
    assert revert() == 0
    assert replaced.read_bytes() == b"newer work"
    assert not added.exists()
    kept = snapshot_folders(machine, c1.REVERT_PREFIX)[0]
    assert (kept / "UserData" / "Styles50" / "b.costyle").exists()     # taken out, not lost
    assert (kept / "UserData" / STYLE).read_bytes() == wanted


def test_n7_force_replaces_a_read_only_target_after_keeping_it(machine):
    assert backup(machine) == 0
    target = machine["user"] / STYLE
    wanted = target.read_bytes()
    target.write_bytes(b"other work!")
    os.chmod(target, stat.S_IREAD)
    assert restore(machine, "--force") == 0
    assert target.read_bytes() == wanted
    kept = snapshot_folders(machine, c1.OLD_PREFIX)[0]
    assert (kept / "UserData" / STYLE).read_bytes() == b"other work!"


def test_n7_a_force_that_fails_leaves_the_read_only_target_as_it_was(machine):
    assert backup(machine) == 0
    target = machine["user"] / STYLE
    target.write_bytes(b"other work!")
    os.chmod(target, stat.S_IREAD)
    copy = machine["backup"] / "UserData" / STYLE
    copy.write_bytes(copy.read_bytes()[::-1])       # the backup's copy is corrupt
    assert restore(machine, "--force") == 1
    assert target.read_bytes() == b"other work!"
    assert not os.access(target, os.W_OK)           # attribute untouched too


# ---- N8: the double-click restore is made from what the backup holds -------------------
def test_n8_script_and_map_keep_a_folder_this_run_did_not_find(machine, new_pc):
    assert backup(machine) == 0
    before = robocopy_sources(machine)
    assert "UserData\\Styles50" in before
    machine["user"].rename(machine["user"].with_name("CaptureOne_reset"))   # 'reset Capture One'
    assert backup(machine) == 0
    assert "UserData/Styles50/a.costyle" in rels(machine["backup"])
    assert sorted(robocopy_sources(machine)) == sorted(before)
    text = (machine["backup"] / c1.MAP_FILE).read_text(encoding="utf-8")
    listed = text.split("On a new computer")[0]
    assert c1.LABEL_USER in listed and str(machine["user"]) in listed
    result = run_restore_cmd(machine, new_pc)
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")
    assert (new_pc["appdata"] / "CaptureOne" / "Styles50" / "a.costyle").exists()
    assert (new_pc["appdata"] / "CaptureOne" / "Color Profiles" / "mine.icc").exists()


def test_n8_a_file_whose_copy_failed_gets_no_line(machine, monkeypatch):
    real = sb._copy_file

    def copy_file(planned, dst, verify, *args, **kwargs):
        if planned.rel.startswith(os.path.join(c1.LABEL_USER, "Recipes165")):
            raise OSError("disk says no")
        return real(planned, dst, verify, *args, **kwargs)

    monkeypatch.setattr(sb, "_copy_file", copy_file)
    assert backup(machine) == 1
    sources = robocopy_sources(machine)
    assert "UserData\\Styles50" in sources
    assert "UserData\\Recipes165" not in sources    # not in the backup: robocopy would fail on it


# ---- N10: the undo goes where the force went ----------------------------------------------
def forced_into_another_install(machine, monkeypatch):
    monkeypatch.setattr(c1, "_is_admin", lambda: True)
    real = make_tree(machine["tmp"] / "D Apps" / "Capture One", {
        PROFILE: 200,
        "Film Curves/NikonD200-Film Standard.fcrv": 210,
    })
    profile = real / PROFILE
    other = machine["install"] / PROFILE            # the folder Windows names
    assert backup(machine, "--install-folder", str(real)) == 0
    wanted = profile.read_bytes()
    profile.write_bytes(b"from a newer Capture One")
    other.unlink()
    assert restore(machine, "--force", "--force-installed", "--install-folder", str(real)) == 0
    assert profile.read_bytes() == wanted
    return profile, other, wanted


def test_n10_revert_without_the_option_undoes_the_folder_the_force_used(machine, monkeypatch):
    profile, other, _wanted = forced_into_another_install(machine, monkeypatch)
    assert revert() == 0
    assert profile.read_bytes() == b"from a newer Capture One"
    assert not other.exists()


def test_n10_the_journal_names_the_real_place(machine, monkeypatch):
    profile, _other, _wanted = forced_into_another_install(machine, monkeypatch)
    entries = journal(snapshot_folders(machine, c1.OLD_PREFIX)[0])
    assert [entry["target"] for entry in entries] == [str(profile)]


def test_n10_an_old_journal_without_targets_still_reverts(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert restore(machine, "--force") == 0
    folder = snapshot_folders(machine, c1.OLD_PREFIX)[0]
    lines = [json.dumps({"op": entry["op"], "rel": entry["rel"]}) for entry in journal(folder)]
    (folder / c1.JOURNAL_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert revert() == 0
    assert style.read_bytes() == b"newer work"


@pytest.mark.parametrize("target", ["relative\\Styles50\\a.costyle",
                                    "C:\\somewhere\\else.txt",
                                    "C:\\x\\..\\Styles50\\a.costyle"])
def test_n10_a_journal_target_that_is_not_that_file_is_refused(machine, target):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert restore(machine, "--force") == 0
    restored = style.read_bytes()
    folder = snapshot_folders(machine, c1.OLD_PREFIX)[0]
    line = json.dumps({"op": "replaced", "rel": "UserData\\Styles50\\a.costyle", "target": target})
    (folder / c1.JOURNAL_FILE).write_text(line + "\n" + line + "\n", encoding="utf-8")
    assert revert() == 2
    assert style.read_bytes() == restored


# ---- N11: an empty path is not "the option was not given" -----------------------------------
def test_n11_an_empty_to_never_restores_into_the_real_places(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.unlink()
    assert restore(machine, "--to", "") == 2
    assert not style.exists()


@pytest.mark.parametrize("flag", ["--dest", "--verify", "--restore-from"])
def test_n11_an_empty_folder_argument_is_refused(machine, flag):
    assert c1.main([flag, "", "--yes"]) == 2


# ---- N12: under --to nothing is 'installed' ----------------------------------------------------
def test_n12_a_different_file_under_to_is_a_conflict(machine, photobackup_log_dir):
    assert backup(machine) == 0
    out = machine["tmp"] / "out [x]"
    assert restore(machine, "--to", str(out)) == 0
    bad = out / "Installed" / PROFILE
    bad.write_bytes(b"x" * 7)
    assert restore(machine, "--to", str(out)) == 1
    assert bad.read_bytes() == b"x" * 7
    plan = latest_csv(photobackup_log_dir, "_plan.csv")
    assert "KEPT_INSTALLED" not in plan and "CONFLICT" in plan


def test_n12_in_the_real_place_a_different_installed_file_is_still_kept(machine):
    assert backup(machine) == 0
    (machine["install"] / PROFILE).write_bytes(b"of a newer Capture One")
    assert restore(machine) == 0
    assert (machine["install"] / PROFILE).read_bytes() == b"of a newer Capture One"


# ---- N13: the undo never replaces a file that appeared meanwhile -----------------------------
def test_n13_revert_leaves_a_target_that_appeared_during_the_copy(machine, monkeypatch, capsys):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert restore(machine, "--force") == 0
    style.unlink()                                  # gone again after the force
    real = sb._copy_file

    def copy_file(planned, dst, verify, *args, **kwargs):
        if planned.category == "revert" and dst.endswith("a.costyle"):
            Path(dst).write_bytes(b"another program wrote this")
        return real(planned, dst, verify, *args, **kwargs)

    monkeypatch.setattr(sb, "_copy_file", copy_file)
    assert revert() == 1
    assert style.read_bytes() == b"another program wrote this"
    assert "CONFLICT" in capsys.readouterr().out
    assert not list(style.parent.glob("*" + sb.TEMP_SUFFIX))
    for folder in snapshot_folders(machine, c1.REVERT_PREFIX):     # never journaled as added
        assert all(entry["rel"] != "UserData\\Styles50\\a.costyle" for entry in journal(folder))


# ---- N14: a folder that could not be read is not a folder that is gone ----------------------
def test_n14_files_of_an_unreadable_folder_are_not_called_gone(machine, monkeypatch, capsys,
                                                               photobackup_log_dir):
    assert backup(machine) == 0
    blocked = sb._io(str(machine["user"] / "Styles50"))
    real = os.scandir

    def scandir(path="."):
        if str(path) == blocked:
            raise PermissionError(5, "Access is denied", str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    capsys.readouterr()
    assert backup(machine) == 1
    monkeypatch.setattr(os, "scandir", real)
    out = capsys.readouterr().out
    assert "kept although gone from Capture One 0" in out
    assert "2 files of the backup are under a folder that could not be read" in out
    assert "Styles50" not in latest_csv(photobackup_log_dir, "_gone.csv")
    assert "UserData\\Styles50" in robocopy_sources(machine)


def test_n14_a_file_really_gone_is_still_reported(machine, capsys, photobackup_log_dir):
    assert backup(machine) == 0
    (machine["user"] / STYLE).unlink()
    capsys.readouterr()
    assert backup(machine) == 0
    assert "kept although gone from Capture One 1" in capsys.readouterr().out
    assert "a.costyle" in latest_csv(photobackup_log_dir, "_gone.csv")


def test_n14_a_root_that_cannot_be_reached_is_an_error_not_an_empty_computer(
        machine, monkeypatch, capsys, photobackup_log_dir):
    assert backup(machine) == 0
    real = sb._source_state
    user = os.path.normcase(str(machine["user"]))

    def source_state(path):
        if os.path.normcase(str(path)) == user:
            return sb.SOURCE_UNREADABLE
        return real(path)

    monkeypatch.setattr(sb, "_source_state", source_state)
    capsys.readouterr()
    assert backup(machine) == 1
    out = capsys.readouterr().out
    assert f"Could not read {machine['user']}" in out
    assert "kept although gone from Capture One 0" in out
    assert "UserData" not in latest_csv(photobackup_log_dir, "_gone.csv")


# ---- N15: a message and an exit code, not a traceback ---------------------------------------
def test_n15_verify_of_a_backup_that_cannot_be_written(machine, capsys, photobackup_log_dir):
    assert backup(machine) == 0
    catalog = machine["backup"] / sb.CATALOG_FOLDER / "catalog.sqlite"
    shutil.rmtree(machine["backup"] / sb.CATALOG_FOLDER)
    assert backup(machine) == 0                     # rows adopted again, without a hash
    os.chmod(catalog, stat.S_IREAD)
    capsys.readouterr()
    try:
        assert c1.main(["--verify", str(machine["dest"])]) == 0
    finally:
        os.chmod(catalog, stat.S_IWRITE | stat.S_IREAD)
    out = capsys.readouterr().out
    assert "HASH_NOT_RECORDED" in out and "HASH_FILLED" not in out
    assert "could not be written" in out
    assert list(Path(photobackup_log_dir).glob("*_verify.csv"))
    connection = sqlite3.connect(catalog)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM files WHERE sha256 IS NOT NULL").fetchone()[0] == 0
    finally:
        connection.close()


def test_n15_a_destination_that_cannot_be_created(machine):
    blocker = machine["tmp"] / "blocker"
    blocker.write_bytes(b"x")
    assert c1.main(["--dest", str(blocker / "sub"), "--yes"]) == 2
    assert blocker.read_bytes() == b"x"


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_n15_a_margin_that_is_not_a_number(machine, value):
    assert c1.main(["--dest", str(machine["dest"]), "--yes", "--space-margin-gb", value]) == 2
    assert not machine["dest"].exists()


@pytest.mark.parametrize("flag", ["--verify", "--restore-from", "--dest"])
def test_n15_a_damaged_catalog_is_a_message(machine, monkeypatch, flag):
    assert backup(machine) == 0

    def damaged(self):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(sb._Catalog, "all_rows", damaged)
    assert c1.main([flag, str(machine["dest"]), "--yes"]) == 2


# ---- N16: one of our own temp files is never restored ----------------------------------------
def test_n16_the_script_never_copies_a_leftover_temp_file(machine, new_pc):
    assert backup(machine) == 0
    leftover = machine["backup"] / "UserData" / "Styles50" / ("dead.costyle" + sb.TEMP_SUFFIX)
    leftover.write_bytes(b"half a file")
    for folder, name in (("Installed/Color Profiles", "dead.icm"), ("WindowsColor", "dead.icc")):
        (machine["backup"] / folder / (name + sb.TEMP_SUFFIX)).write_bytes(b"half a file")
    text = (machine["backup"] / c1.RESTORE_SCRIPT).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.startswith("robocopy ")]
    assert lines and all(f"/XF *{sb.TEMP_SUFFIX} " in line for line in lines)
    result = run_restore_cmd(machine, new_pc)
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")
    assert (new_pc["appdata"] / "CaptureOne" / "Styles50" / "a.costyle").exists()
    restored = (rels(new_pc["appdata"]) | rels(new_pc["install"]) | rels(new_pc["color"]))
    assert not [rel for rel in restored if rel.endswith(sb.TEMP_SUFFIX)]


# ---- N29: the instance started as administrator never asks again ----------------------------
def test_n29_the_elevated_instance_skips_the_administrator_question(machine, new_pc):
    assert backup(machine) == 0
    lines = (machine["backup"] / c1.RESTORE_SCRIPT).read_text(encoding="utf-8").splitlines()
    marker = f'if /i "%~1"=="{c1.ELEVATED_ARGUMENT}" goto :checks'
    # Checked BEFORE the script is run below: without this line it would ask
    # Windows for administrator rights on this computer.
    assert marker in lines
    assert lines.index(marker) < lines.index("net session >nul 2>&1")
    asking = [line for line in lines if "-Verb RunAs" in line]
    assert len(asking) == 1 and f"' {c1.ELEVATED_ARGUMENT}'" in asking[0]
    assert not any(line.lstrip().lower().startswith("call ") for line in lines)
    if REAL_RUNNING() is not False:
        pytest.skip("Capture One is open on this computer: the script would stop at its check")
    # the command line the elevated instance gets: cmd /c ""<script>" --elevated"
    result = run_restore_cmd(machine, new_pc, c1.ELEVATED_ARGUMENT, slash_s=False)
    text = result.stdout.decode("utf-8", "replace")
    assert result.returncode == 0, text
    assert "Asking Windows" not in text
    assert (new_pc["appdata"] / "CaptureOne" / "Styles50" / "a.costyle").exists()
