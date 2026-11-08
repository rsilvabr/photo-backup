"""captureone_backup.py. Every folder here is a synthetic tree in tmp_path, inside
a hostile path (Japanese, brackets, spaces, %); the real Capture One is never read."""
import json
import os
import re
import struct
import subprocess
import time
from pathlib import Path

import pytest

import captureone_backup as c1
import photo_backup as menu
from conftest import make_tree, snapshot

HOSTILE = "写真 [FINAL] 100% (2)"
ICC = "NikonD80-EO-NXD-Mode3.icc"


@pytest.fixture
def machine(tmp_path, monkeypatch):
    root = tmp_path / HOSTILE
    appdata = root / "AppData Local"
    make_tree(appdata, {
        f"CaptureOne/Color Profiles/{ICC}": 500,
        "CaptureOne/Styles50/D200_standard.costyle": 40,
        "CaptureOne/Styles50/D200_standard.costyle.NikonD80-EO-NXD-Mode3.icm": 300,
        "CaptureOne/Defaults/Base Characteristics/NikonD200.codefault": 30,
        "CaptureOne/Recipes165/sRGB.xml": 20,
        "CaptureOne/Presets60/Curve/mine.copreset": 15,
        "CaptureOne/CustomCommands/shortcuts.xml": 25,
        "CaptureOne/Batch144/active/job.task": 35,
        "CaptureOne/ImageCore/16.7/cache.bin": 45,
        "CaptureOne/_Instalador/CaptureOne.Win.exe": 600,
        "CaptureOne/_Instalador/serial.png": 55,
        "CaptureOne/userLog.db": 60,
        "CaptureOne/Logs/app.log": 70,
        "CaptureOne/MSALCache16.7/token.bin": 80,
        "CaptureOne/WebViewUserData/EBWebView/Cookies": 90,
        "CaptureOne/CaptureOneCloudSettingsUserData/x.bin": 95,
        "CaptureOne/Plugins/EBWebView/Cookies2": 96,
        "CaptureOne/Plugins/one.plugin": 97,
        "CaptureOne/Styles50/license9.enc": 98,
        "Capture_One/Strong_abc/16.7.8/user.config": 110,
    })
    install = make_tree(root / "Program Files C1", {
        "Color Profiles/DSLR/NikonD200-Generic.icm": 200,
        "Film Curves/NikonD200-Film Standard.fcrv": 210,
        "Program.dll": 220,
    })
    color = make_tree(root / "win color", {
        "LargeRGB-elle-V2-g22.icc": 120,
        "ProPhoto.icm": 121,
        "readme.pdf": 122,
    })
    (color / "sub").mkdir()
    (color / "sub" / "nested.icc").write_bytes(b"n")
    monkeypatch.setenv("PHOTOBACKUP_C1_LOCALAPPDATA", str(appdata))
    monkeypatch.setenv("PHOTOBACKUP_C1_INSTALL", str(install))
    monkeypatch.setenv("PHOTOBACKUP_C1_WINDOWS_COLOR", str(color))
    monkeypatch.setattr(c1, "_captureone_running", lambda: False)
    monkeypatch.setattr(c1, "_is_admin", lambda: False)
    dest = tmp_path / "宛先 [BK] 50%"
    return {"appdata": appdata, "user": appdata / "CaptureOne", "install": install,
            "color": color, "dest": dest, "backup": dest / c1.BACKUP_SUBFOLDER, "root": root}


def rels(folder):
    return {str(p.relative_to(folder)).replace("\\", "/")
            for p in Path(folder).rglob("*") if p.is_file()}


def backup(machine):
    return c1.main(["--dest", str(machine["dest"]), "--yes"])


def restore(machine, *extra):
    return c1.main(["--restore-from", str(machine["dest"]), "--yes", *extra])


def test_copies_what_edits_need_and_leaves_out_login_data(machine):
    assert backup(machine) == 0
    got = rels(machine["backup"])
    for wanted in (f"UserData/Color Profiles/{ICC}",
                   "UserData/Styles50/D200_standard.costyle.NikonD80-EO-NXD-Mode3.icm",
                   "UserData/_Instalador/CaptureOne.Win.exe",
                   "UserData/_Instalador/serial.png",
                   "UserData/Batch144/active/job.task",
                   "UserData/Plugins/one.plugin",
                   "Preferences/Strong_abc/16.7.8/user.config",
                   "Installed/Color Profiles/DSLR/NikonD200-Generic.icm",
                   "Installed/Film Curves/NikonD200-Film Standard.fcrv",
                   "WindowsColor/LargeRGB-elle-V2-g22.icc",
                   "WindowsColor/ProPhoto.icm",
                   c1.MAP_FILE):
        assert wanted in got
    text = " ".join(sorted(got))
    for secret in ("Logs/", "MSALCache", "WebViewUserData", "CloudSettings", "EBWebView",
                   "license9.enc", "Program.dll", "readme.pdf", "nested.icc"):
        assert secret not in text, secret


def test_sources_are_read_only(machine):
    before = snapshot(machine["root"])
    assert backup(machine) == 0
    assert snapshot(machine["root"]) == before


def test_map_says_where_each_part_came_from(machine):
    assert backup(machine) == 0
    text = (machine["backup"] / c1.MAP_FILE).read_text(encoding="utf-8")
    assert str(machine["user"]) in text
    assert str(machine["install"]) in text
    assert str(machine["color"]) in text


def test_dry_run_writes_nothing(machine):
    assert c1.main(["--dest", str(machine["dest"]), "--dry-run"]) == 0
    assert not machine["dest"].exists()


def test_no_space_stops_before_copying(machine):
    code = c1.main(["--dest", str(machine["dest"]), "--yes", "--space-margin-gb", "999999999"])
    assert code == 2
    assert not machine["dest"].exists()


def test_closed_stdin_without_yes_copies_nothing(machine, monkeypatch):
    def closed(_prompt):
        raise EOFError
    monkeypatch.setattr("builtins.input", closed)
    assert c1.main(["--dest", str(machine["dest"])]) == 2
    assert not machine["backup"].exists()


def test_rerun_copies_nothing_new(machine):
    assert backup(machine) == 0
    before = snapshot(machine["backup"] / "UserData")
    assert backup(machine) == 0
    assert snapshot(machine["backup"] / "UserData") == before
    assert not (machine["backup"] / "_photo_backup" / "versions").exists()


def test_changed_file_keeps_old_copy_in_versions(machine):
    assert backup(machine) == 0
    style = machine["user"] / "Styles50" / "D200_standard.costyle"
    old = style.read_bytes()
    style.write_bytes(b"edited style")
    os.utime(style, (2_000_000_000, 2_000_000_000))
    assert backup(machine) == 0
    assert (machine["backup"] / "UserData/Styles50/D200_standard.costyle").read_bytes() == b"edited style"
    kept = list((machine["backup"] / "_photo_backup" / "versions").rglob("D200_standard.costyle"))
    assert len(kept) == 1 and kept[0].read_bytes() == old


def test_rewrite_with_same_bytes_adds_no_version(machine):
    assert backup(machine) == 0
    style = machine["user"] / "Styles50" / "D200_standard.costyle"
    os.utime(style, (2_000_000_000, 2_000_000_000))
    assert backup(machine) == 0
    assert not (machine["backup"] / "_photo_backup" / "versions").exists()


def test_file_gone_from_capture_one_stays_in_backup(machine):
    assert backup(machine) == 0
    (machine["user"] / "Styles50" / "D200_standard.costyle").unlink()
    assert backup(machine) == 0
    assert (machine["backup"] / "UserData/Styles50/D200_standard.costyle").exists()


def test_junction_is_not_followed(machine, tmp_path):
    outside = make_tree(tmp_path / "outside [x]", {"huge.bin": 50})
    link = machine["user"] / "Styles50" / "linked"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                   check=True, capture_output=True)
    assert backup(machine) == 0
    assert "huge.bin" not in " ".join(rels(machine["backup"]))


def test_destination_inside_capture_one_is_refused(machine):
    assert c1.main(["--dest", str(machine["user"] / "x"), "--yes"]) == 2
    assert not (machine["user"] / "x").exists()


def test_no_capture_one_on_this_computer_is_quiet(tmp_path):
    dest = tmp_path / "dest"
    assert c1.main(["--dest", str(dest), "--yes"]) == 0
    assert not dest.exists()


def wipe_user_folder(machine):
    for path in sorted(machine["user"].rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()


def test_restore_brings_back_the_edit_files_only(machine):
    assert backup(machine) == 0
    wipe_user_folder(machine)
    assert restore(machine) == 0
    got = rels(machine["user"])
    for wanted in (f"Color Profiles/{ICC}", "Styles50/D200_standard.costyle",
                   "Styles50/D200_standard.costyle.NikonD80-EO-NXD-Mode3.icm",
                   "Defaults/Base Characteristics/NikonD200.codefault",
                   "Recipes165/sRGB.xml", "Presets60/Curve/mine.copreset",
                   "CustomCommands/shortcuts.xml"):
        assert wanted in got
    for left in ("_Instalador/serial.png", "Batch144/active/job.task",
                 "ImageCore/16.7/cache.bin", "Plugins/one.plugin", "userLog.db"):
        assert left not in got


def test_restored_bytes_match(machine):
    assert backup(machine) == 0
    original = (machine["user"] / "Color Profiles" / ICC).read_bytes()
    wipe_user_folder(machine)
    assert restore(machine) == 0
    assert (machine["user"] / "Color Profiles" / ICC).read_bytes() == original


def test_also_brings_back_named_parts(machine):
    assert backup(machine) == 0
    wipe_user_folder(machine)
    assert restore(machine, "--also", "_Instalador", "--also", "Batch*") == 0
    got = rels(machine["user"])
    assert "_Instalador/serial.png" in got
    assert "Batch144/active/job.task" in got
    assert "ImageCore/16.7/cache.bin" not in got


def test_also_preferences(machine):
    assert backup(machine) == 0
    config = machine["appdata"] / "Capture_One" / "Strong_abc" / "16.7.8" / "user.config"
    config.unlink()
    assert restore(machine) == 0
    assert not config.exists()
    assert restore(machine, "--also", "Preferences") == 0
    assert config.exists()


def test_restore_never_overwrites(machine):
    assert backup(machine) == 0
    style = machine["user"] / "Styles50" / "D200_standard.costyle"
    style.write_bytes(b"newer work")
    assert restore(machine) == 1
    assert style.read_bytes() == b"newer work"


def test_restore_never_writes_to_the_backup(machine):
    assert backup(machine) == 0
    before = snapshot(machine["backup"] / "UserData")
    wipe_user_folder(machine)
    assert restore(machine) == 0
    assert snapshot(machine["backup"] / "UserData") == before


def test_new_style_folder_number_is_covered(machine):
    (machine["user"] / "Styles60").mkdir()
    (machine["user"] / "Styles60" / "future.costyle").write_bytes(b"f")
    assert backup(machine) == 0
    (machine["user"] / "Styles60" / "future.costyle").unlink()
    assert restore(machine) == 0
    assert (machine["user"] / "Styles60" / "future.costyle").exists()


def test_protected_folder_needs_admin(machine):
    assert backup(machine) == 0
    profile = machine["install"] / "Color Profiles" / "DSLR" / "NikonD200-Generic.icm"
    profile.unlink()
    assert restore(machine) == 1
    assert not profile.exists()


def test_protected_folder_restored_as_admin(machine, monkeypatch):
    assert backup(machine) == 0
    monkeypatch.setattr(c1, "_is_admin", lambda: True)
    profile = machine["install"] / "Color Profiles" / "DSLR" / "NikonD200-Generic.icm"
    profile.unlink()
    assert restore(machine) == 0
    assert profile.exists()


def test_newer_installed_profile_is_kept_without_error(machine):
    assert backup(machine) == 0
    profile = machine["install"] / "Color Profiles" / "DSLR" / "NikonD200-Generic.icm"
    profile.write_bytes(b"from a newer Capture One")
    assert restore(machine) == 0
    assert profile.read_bytes() == b"from a newer Capture One"


def test_install_folder_missing_says_install_first(machine, tmp_path, monkeypatch):
    assert backup(machine) == 0
    monkeypatch.setenv("PHOTOBACKUP_C1_INSTALL", str(tmp_path / "never installed"))
    assert restore(machine) == 1
    assert not (tmp_path / "never installed").exists()


def test_restore_refused_while_capture_one_is_open(machine, monkeypatch):
    assert backup(machine) == 0
    wipe_user_folder(machine)
    monkeypatch.setattr(c1, "_captureone_running", lambda: True)
    assert restore(machine) == 2
    assert not rels(machine["user"])


def test_restore_to_another_folder(machine, tmp_path):
    assert backup(machine) == 0
    out = tmp_path / "出力 [x]"
    assert restore(machine, "--to", str(out), "--also", "_Instalador") == 0
    assert f"UserData/Color Profiles/{ICC}" in rels(out)
    assert "UserData/_Instalador/serial.png" in rels(out)


def test_restore_detects_a_corrupt_backup_file(machine):
    assert backup(machine) == 0
    copy = machine["backup"] / "UserData/Styles50/D200_standard.costyle"
    data = bytearray(copy.read_bytes())
    data[0] ^= 0xFF
    copy.write_bytes(bytes(data))
    target = machine["user"] / "Styles50" / "D200_standard.costyle"
    target.unlink()
    assert restore(machine) == 1
    assert not target.exists()


def test_verify_clean_then_corrupt_then_missing(machine):
    assert backup(machine) == 0
    assert c1.main(["--verify", str(machine["dest"])]) == 0
    copy = machine["backup"] / "UserData/Recipes165/sRGB.xml"
    data = bytearray(copy.read_bytes())
    data[0] ^= 0xFF
    copy.write_bytes(bytes(data))
    assert c1.main(["--verify", str(machine["dest"])]) == 1
    copy.unlink()
    assert c1.main(["--verify", str(machine["dest"])]) == 1


def test_verify_without_a_backup_stops(tmp_path):
    assert c1.main(["--verify", str(tmp_path / "nothing")]) == 2


def test_restore_flags_refused_on_a_backup(machine):
    assert c1.main(["--dest", str(machine["dest"]), "--also", "x", "--yes"]) == 2
    assert not machine["dest"].exists()


# ---- The double-click restore (a new PC has no Python) --------------------------------
def run_restore_cmd(machine, new_appdata, new_install, new_color):
    script = machine["backup"] / c1.RESTORE_SCRIPT
    env = dict(os.environ, LOCALAPPDATA=str(new_appdata), C1_INSTALL_TARGET=str(new_install),
               C1_COLOR_TARGET=str(new_color))
    # A string, not a list: cmd needs the whole line inside one extra pair of quotes.
    return subprocess.run(f'cmd /s /c ""{script}" --unattended"', env=env,
                          capture_output=True, timeout=120)


@pytest.fixture
def new_pc(tmp_path):
    root = tmp_path / "新しいPC [C] 100% (1)"
    install = make_tree(root / "Program Files", {
        "CaptureOne.exe": 10,
        "Color Profiles/DSLR/NikonD200-Generic.icm": b"stock of the new install",
    })
    color = root / "color"
    color.mkdir()
    return {"appdata": root / "AppData", "install": install, "color": color}


def test_restore_script_rebuilds_a_new_pc(machine, new_pc):
    assert backup(machine) == 0
    result = run_restore_cmd(machine, new_pc["appdata"], new_pc["install"], new_pc["color"])
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")
    user = new_pc["appdata"] / "CaptureOne"
    assert (user / "Color Profiles" / ICC).stat().st_size == 500
    assert (user / "Styles50" / "D200_standard.costyle.NikonD80-EO-NXD-Mode3.icm").exists()
    assert (user / "Defaults" / "Base Characteristics" / "NikonD200.codefault").exists()
    assert (new_pc["install"] / "Film Curves" / "NikonD200-Film Standard.fcrv").exists()
    assert (new_pc["color"] / "LargeRGB-elle-V2-g22.icc").exists()


def test_restore_script_never_overwrites(machine, new_pc):
    assert backup(machine) == 0
    style = new_pc["appdata"] / "CaptureOne" / "Styles50" / "D200_standard.costyle"
    style.parent.mkdir(parents=True)
    style.write_bytes(b"made on the new pc")
    result = run_restore_cmd(machine, new_pc["appdata"], new_pc["install"], new_pc["color"])
    assert result.returncode == 0
    assert style.read_bytes() == b"made on the new pc"
    profile = new_pc["install"] / "Color Profiles" / "DSLR" / "NikonD200-Generic.icm"
    assert profile.read_bytes() == b"stock of the new install"


def test_restore_script_leaves_the_rest_in_the_backup(machine, new_pc):
    assert backup(machine) == 0
    run_restore_cmd(machine, new_pc["appdata"], new_pc["install"], new_pc["color"])
    got = rels(new_pc["appdata"] / "CaptureOne")
    assert not any(rel.startswith(("_Instalador", "Batch144", "ImageCore")) for rel in got)


def test_restore_script_says_install_first(machine, new_pc, tmp_path):
    assert backup(machine) == 0
    result = run_restore_cmd(machine, new_pc["appdata"], tmp_path / "not installed", new_pc["color"])
    assert result.returncode == 1
    assert c1.INSTALLER_FOLDER.encode() in result.stdout
    assert not (new_pc["appdata"] / "CaptureOne").exists()


def test_restore_script_has_no_personal_path(machine):
    assert backup(machine) == 0
    text = (machine["backup"] / c1.RESTORE_SCRIPT).read_text(encoding="utf-8")
    assert str(machine["appdata"]) not in text
    assert "%LOCALAPPDATA%" in text


def test_restore_script_is_not_written_on_a_dry_run(machine):
    assert c1.main(["--dest", str(machine["dest"]), "--dry-run"]) == 0
    assert not (machine["backup"] / c1.RESTORE_SCRIPT).exists()


# ---- Force restore and revert ----------------------------------------------------------
STYLE = "Styles50/D200_standard.costyle"
SNAPSHOT_NAME = re.compile(r"^(revert_)?old_\d{8}_\d{6}_\d{3}(_not)?$")


def snapshots(machine):
    parent = machine["appdata"] / c1.OLD_PARENT_FOLDER
    return sorted(p.name for p in parent.iterdir()) if parent.exists() else []


def force(machine, *extra):
    return restore(machine, "--force", *extra)


def revert(*extra):
    return c1.main(["--revert-last", "--yes", *extra])


def fake_icc(device_class, tags=()):
    table = struct.pack(">I", len(tags)) + b"".join(t + b"\0" * 8 for t in tags)
    return b"\0" * 12 + device_class + b"\0" * 20 + b"acsp" + b"\0" * 88 + table


def test_force_replaces_and_keeps_the_old_file(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    wanted = style.read_bytes()
    style.write_bytes(b"newer work")
    assert force(machine) == 0
    assert style.read_bytes() == wanted
    names = snapshots(machine)
    assert len(names) == 1 and SNAPSHOT_NAME.match(names[0])
    kept = machine["appdata"] / c1.OLD_PARENT_FOLDER / names[0] / "UserData" / STYLE
    assert kept.read_bytes() == b"newer work"


def test_force_keeps_only_what_really_differs(machine):
    assert backup(machine) == 0
    (machine["user"] / STYLE).write_bytes(b"newer work")
    assert force(machine) == 0
    folder = machine["appdata"] / c1.OLD_PARENT_FOLDER / snapshots(machine)[0]
    assert [r for r in rels(folder) if r != c1.JOURNAL_FILE] == [f"UserData/{STYLE}"]


def test_force_with_nothing_to_do_leaves_no_snapshot(machine):
    assert backup(machine) == 0
    assert force(machine) == 0
    assert snapshots(machine) == []


def test_snapshot_is_outside_capture_one_and_not_backed_up(machine):
    assert backup(machine) == 0
    (machine["user"] / STYLE).write_bytes(b"newer work")
    assert force(machine) == 0
    assert backup(machine) == 0
    assert not any(c1.OLD_PREFIX in rel for rel in rels(machine["backup"] / "UserData"))


def test_force_dry_run_changes_nothing(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert c1.main(["--restore-from", str(machine["dest"]), "--force", "--dry-run"]) == 0
    assert style.read_bytes() == b"newer work"
    assert snapshots(machine) == []


def test_without_force_nothing_is_replaced(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert restore(machine) == 1
    assert style.read_bytes() == b"newer work"
    assert snapshots(machine) == []


def test_force_leaves_factory_profiles_alone(machine):
    assert backup(machine) == 0
    profile = machine["install"] / "Color Profiles" / "DSLR" / "NikonD200-Generic.icm"
    profile.write_bytes(b"from a newer Capture One")
    assert force(machine) == 0
    assert profile.read_bytes() == b"from a newer Capture One"


def test_force_installed_needs_admin(machine):
    assert backup(machine) == 0
    profile = machine["install"] / "Color Profiles" / "DSLR" / "NikonD200-Generic.icm"
    profile.write_bytes(b"from a newer Capture One")
    assert force(machine, "--force-installed") == 1
    assert profile.read_bytes() == b"from a newer Capture One"


def test_force_installed_as_admin_replaces_and_keeps(machine, monkeypatch):
    assert backup(machine) == 0
    monkeypatch.setattr(c1, "_is_admin", lambda: True)
    profile = machine["install"] / "Color Profiles" / "DSLR" / "NikonD200-Generic.icm"
    wanted = profile.read_bytes()
    profile.write_bytes(b"from a newer Capture One")
    assert force(machine, "--force-installed") == 0
    assert profile.read_bytes() == wanted
    assert revert() == 0
    assert profile.read_bytes() == b"from a newer Capture One"


def test_monitor_calibration_is_never_forced(machine, monkeypatch):
    calibration = machine["color"] / "CS2730(21778116)04CAL1.icc"
    calibration.write_bytes(fake_icc(b"mntr", [b"vcgt", b"desc"]) + b"old")
    working = machine["color"] / "LargeRGB-elle-V2-g22.icc"
    working.write_bytes(fake_icc(b"mntr", [b"desc"]) + b"old")
    assert backup(machine) == 0
    monkeypatch.setattr(c1, "_is_admin", lambda: True)
    calibration.write_bytes(fake_icc(b"mntr", [b"vcgt", b"desc"]) + b"this week")
    working.write_bytes(fake_icc(b"mntr", [b"desc"]) + b"other")
    assert force(machine, "--force-installed") == 0
    assert calibration.read_bytes().endswith(b"this week")
    assert working.read_bytes().endswith(b"old")


@pytest.mark.parametrize("data, expected", [
    (fake_icc(b"mntr", [b"desc", b"vcgt"]), True),
    (fake_icc(b"mntr", [b"desc"]), False),
    (fake_icc(b"scnr", [b"vcgt"]), False),
    (fake_icc(b"prtr"), False),
    (b"not a profile", True),
    (b"", True),
])
def test_calibration_detection(tmp_path, data, expected):
    path = tmp_path / "プロファイル [x].icc"
    path.write_bytes(data)
    assert c1._is_monitor_calibration(str(path)) is expected


def test_calibration_detection_unreadable_is_kept(tmp_path):
    assert c1._is_monitor_calibration(str(tmp_path / "missing.icc")) is True


def test_force_corrupt_backup_keeps_the_current_file(machine):
    assert backup(machine) == 0
    copy = machine["backup"] / "UserData" / STYLE
    data = bytearray(copy.read_bytes())
    data[0] ^= 0xFF
    copy.write_bytes(bytes(data))
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert force(machine) == 1
    assert style.read_bytes() == b"newer work"


@pytest.mark.parametrize("state", [True, None])
def test_force_refused_unless_capture_one_is_known_closed(machine, monkeypatch, state):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    monkeypatch.setattr(c1, "_captureone_running", lambda: state)
    assert force(machine) == 2
    assert style.read_bytes() == b"newer work"


@pytest.mark.parametrize("extra", [
    ["--force", "--to", "x"],
    ["--force-installed"],
])
def test_force_flag_combinations_refused(machine, extra):
    assert backup(machine) == 0
    assert c1.main(["--restore-from", str(machine["dest"]), "--yes", *extra]) == 2


def test_force_only_on_a_restore(machine):
    assert c1.main(["--dest", str(machine["dest"]), "--force", "--yes"]) == 2
    assert not machine["dest"].exists()


def test_snapshot_names_are_unique_within_one_millisecond(machine, monkeypatch):
    moments = iter([1000.5, 1000.5, 1000.501, 1000.501])
    real = time.time
    monkeypatch.setattr(c1.time, "time", lambda: next(moments, real()))
    first, second = c1._Snapshot(c1.OLD_PREFIX), c1._Snapshot(c1.OLD_PREFIX)
    first._open()
    second._open()
    assert first.folder != second.folder


def test_revert_puts_the_old_file_back(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert force(machine) == 0
    assert style.read_bytes() != b"newer work"
    assert revert() == 0
    assert style.read_bytes() == b"newer work"


def test_revert_takes_out_what_the_force_added_without_losing_it(machine):
    assert backup(machine) == 0
    added = machine["user"] / "Recipes165" / "sRGB.xml"
    added.unlink()
    assert force(machine) == 0
    assert added.exists()
    assert revert() == 0
    assert not added.exists()
    parent = machine["appdata"] / c1.OLD_PARENT_FOLDER
    folder = [n for n in snapshots(machine) if n.startswith(c1.REVERT_PREFIX)][0]
    assert (parent / folder / "UserData" / "Recipes165" / "sRGB.xml").exists()


def test_revert_keeps_what_it_replaces_in_revert_old(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert force(machine) == 0
    restored = style.read_bytes()
    assert revert() == 0
    names = [n for n in snapshots(machine) if n.startswith(c1.REVERT_PREFIX)]
    assert len(names) == 1 and SNAPSHOT_NAME.match(names[0])
    kept = machine["appdata"] / c1.OLD_PARENT_FOLDER / names[0] / "UserData" / STYLE
    assert kept.read_bytes() == restored


def test_second_revert_changes_nothing(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert force(machine) == 0
    assert revert() == 0
    count = len(snapshots(machine))
    assert revert() == 0
    assert style.read_bytes() == b"newer work"
    assert len(snapshots(machine)) == count


def test_revert_uses_the_newest_and_skips_not(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"first")
    assert force(machine) == 0
    style.write_bytes(b"second")
    assert force(machine) == 0
    parent = machine["appdata"] / c1.OLD_PARENT_FOLDER
    newest = max(snapshots(machine))
    (parent / newest).rename(parent / (newest + c1.DISABLED_SUFFIX))
    assert revert() == 0
    assert style.read_bytes() == b"first"


def test_revert_picks_the_newest(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"first")
    assert force(machine) == 0
    style.write_bytes(b"second")
    assert force(machine) == 0
    assert revert() == 0
    assert style.read_bytes() == b"second"


def test_revert_old_renamed_to_old_redoes(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert force(machine) == 0
    restored = style.read_bytes()
    assert revert() == 0
    parent = machine["appdata"] / c1.OLD_PARENT_FOLDER
    name = [n for n in snapshots(machine) if n.startswith(c1.REVERT_PREFIX)][0]
    (parent / name).rename(parent / name.replace(c1.REVERT_PREFIX, c1.OLD_PREFIX + "9"))
    assert revert() == 0
    assert style.read_bytes() == restored


def test_revert_dry_run_changes_nothing(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert force(machine) == 0
    restored = style.read_bytes()
    count = len(snapshots(machine))
    assert c1.main(["--revert-last", "--dry-run"]) == 0
    assert style.read_bytes() == restored
    assert len(snapshots(machine)) == count


def test_revert_with_nothing_to_undo(machine):
    assert revert() == 2


@pytest.mark.parametrize("state", [True, None])
def test_revert_refused_unless_capture_one_is_known_closed(machine, monkeypatch, state):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert force(machine) == 0
    restored = style.read_bytes()
    monkeypatch.setattr(c1, "_captureone_running", lambda: state)
    assert revert() == 2
    assert style.read_bytes() == restored


def test_revert_refuses_a_path_out_of_the_snapshot(machine):
    assert backup(machine) == 0
    (machine["user"] / STYLE).write_bytes(b"newer work")
    assert force(machine) == 0
    folder = machine["appdata"] / c1.OLD_PARENT_FOLDER / snapshots(machine)[0]
    line = json.dumps({"op": "added", "rel": "UserData\\..\\..\\evil.txt"})
    (folder / c1.JOURNAL_FILE).write_text(line + "\n" + line + "\n", encoding="utf-8")
    assert revert() == 2


def test_journal_cut_by_a_crash_is_still_usable(machine):
    assert backup(machine) == 0
    style = machine["user"] / STYLE
    style.write_bytes(b"newer work")
    assert force(machine) == 0
    folder = machine["appdata"] / c1.OLD_PARENT_FOLDER / snapshots(machine)[0]
    with open(folder / c1.JOURNAL_FILE, "a", encoding="utf-8") as handle:
        handle.write('{"op": "repl')
    assert revert() == 0
    assert style.read_bytes() == b"newer work"


def test_chosen_folder_works_given_either_way(machine, tmp_path):
    chosen = tmp_path / "選んだ [C1] 100%"
    assert c1.main(["--dest", str(chosen), "--yes"]) == 0
    wipe_user_folder(machine)
    direct = chosen / c1.BACKUP_SUBFOLDER
    assert not (direct / c1.BACKUP_SUBFOLDER).exists()
    assert c1.main(["--restore-from", str(direct), "--yes"]) == 0
    assert (machine["user"] / "Color Profiles" / ICC).exists()


def test_backup_into_the_subfolder_is_not_nested(machine, tmp_path):
    direct = tmp_path / "x" / c1.BACKUP_SUBFOLDER
    assert c1.main(["--dest", str(direct), "--yes"]) == 0
    assert (direct / "UserData").is_dir()
    assert not (direct / c1.BACKUP_SUBFOLDER).exists()


# ---- Menu ---------------------------------------------------------------------------
def answer_with(monkeypatch, answers):
    answers = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))


@pytest.fixture
def c1_menu(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "CONFIG_PATH", str(tmp_path / "config.json"))
    calls = []
    monkeypatch.setattr(menu, "run_captureone", lambda argv: calls.append(argv) or 0)
    return calls


def test_menu_backs_up_only_capture_one_to_a_chosen_folder(monkeypatch, tmp_path, c1_menu):
    answer_with(monkeypatch, ["1", str(tmp_path), "y", "0"])
    menu.captureone_menu(menu.load_config())
    assert c1_menu == [["--dest", str(tmp_path), "--dry-run"], ["--dest", str(tmp_path), "--yes"]]


def test_menu_remembers_the_chosen_folder(monkeypatch, tmp_path, c1_menu):
    answer_with(monkeypatch, ["1", str(tmp_path), "y", "0"])
    menu.captureone_menu(menu.load_config())
    assert menu.load_config()["captureone_folder"] == str(tmp_path)


def test_menu_restore_passes_the_extras(monkeypatch, tmp_path, c1_menu):
    answer_with(monkeypatch, ["2", str(tmp_path), "_Instalador; Preferences", "y", "0"])
    menu.captureone_menu(menu.load_config())
    assert c1_menu[-1] == ["--restore-from", str(tmp_path), "--also", "_Instalador",
                           "--also", "Preferences", "--yes"]


def test_menu_force_leaves_factory_profiles_by_default(monkeypatch, tmp_path, c1_menu):
    answer_with(monkeypatch, ["3", str(tmp_path), "", "", "y", "0"])
    menu.captureone_menu(menu.load_config())
    assert c1_menu[-1] == ["--restore-from", str(tmp_path), "--force", "--yes"]


def test_menu_force_installed_only_when_asked(monkeypatch, tmp_path, c1_menu):
    answer_with(monkeypatch, ["3", str(tmp_path), "", "y", "y", "0"])
    menu.captureone_menu(menu.load_config())
    assert c1_menu[-1] == ["--restore-from", str(tmp_path), "--force", "--force-installed", "--yes"]


def test_menu_undo(monkeypatch, c1_menu):
    answer_with(monkeypatch, ["4", "y", "0"])
    menu.captureone_menu(menu.load_config())
    assert c1_menu == [["--revert-last", "--dry-run"], ["--revert-last", "--yes"]]


def test_menu_no_means_only_the_dry_run(monkeypatch, c1_menu):
    answer_with(monkeypatch, ["4", "n", "0"])
    menu.captureone_menu(menu.load_config())
    assert c1_menu == [["--revert-last", "--dry-run"]]


def test_every_menu_flag_exists_in_the_script():
    parser = c1.build_parser()
    known = {option for action in parser._actions for option in action.option_strings}
    for flag in ("--dest", "--restore-from", "--also", "--force", "--force-installed",
                 "--revert-last", "--dry-run", "--yes"):
        assert flag in known


def session_for(dest, **changes):
    session = menu.default_session()
    session.update(sources=["G:\\x"], dest=str(dest))
    session.update(changes)
    return session


def test_menu_step_copies_to_the_same_destination(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(menu, "run_captureone", lambda argv: calls.append(argv) or 0)
    assert menu.captureone_step(session_for(tmp_path), "copy") == 0
    assert calls == [["--dest", str(tmp_path), "--yes"]]


def test_menu_step_dry_run_is_a_dry_run(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(menu, "run_captureone", lambda argv: calls.append(argv) or 0)
    menu.captureone_step(session_for(tmp_path), "dry-run")
    assert calls == [["--dest", str(tmp_path), "--dry-run"]]


def test_menu_step_can_be_off(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(menu, "run_captureone", lambda argv: calls.append(argv) or 0)
    assert menu.captureone_step(session_for(tmp_path, captureone=False), "copy") == 0
    assert calls == []


def test_old_preset_gets_the_capture_one_step():
    assert menu.session_from({"sources": ["G:\\x"], "dest": "H:\\y"})["captureone"] is True


def test_menu_default_reads_its_setting(monkeypatch):
    monkeypatch.setattr(menu, "COPY_CAPTUREONE_SETTINGS", False)
    assert menu.default_session()["captureone"] is False


def test_menu_verify_skips_a_backup_without_capture_one(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(menu, "run_captureone", lambda argv: calls.append(argv) or 0)
    assert menu.captureone_verify_step(session_for(tmp_path)) == 0
    assert calls == []


def test_menu_verify_runs_when_the_backup_has_it(monkeypatch, tmp_path):
    (tmp_path / c1.BACKUP_SUBFOLDER).mkdir()
    calls = []
    monkeypatch.setattr(menu, "run_captureone", lambda argv: calls.append(argv) or 0)
    menu.captureone_verify_step(session_for(tmp_path))
    assert calls == [["--verify", str(tmp_path)]]


def test_preset_failure_in_capture_one_step_is_reported(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(menu, "CONFIG_PATH", str(config_path))
    config = menu.load_config()
    config["presets"]["p"] = {"sources": [str(tmp_path)], "dest": str(tmp_path), "kind": "backup"}
    menu.save_config(config)
    monkeypatch.setattr(menu, "run_backend", lambda argv: 0)
    monkeypatch.setattr(menu, "run_captureone", lambda argv: 1)
    assert menu.run_preset("p") == 1
