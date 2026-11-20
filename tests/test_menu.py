"""The interactive menu: scripted answers, the backend either captured or real."""
import json
import os
from pathlib import Path

import pytest

import selective_backup
import photo_backup as menu

from conftest import argv_for, build_base_tree, dest_files, make_tree


@pytest.fixture(autouse=True)
def menu_config(tmp_path, monkeypatch):
    path = tmp_path / "menu_config.json"
    monkeypatch.setattr(menu, "CONFIG_PATH", str(path))
    monkeypatch.setattr(menu, "list_drives", lambda: [])
    return path


def script(monkeypatch, answers):
    """Feed `answers` to input(); running out of answers is a closed stdin."""
    answers = list(answers)

    def fake_input(prompt=""):
        if not answers:
            raise EOFError
        return answers.pop(0)
    monkeypatch.setattr("builtins.input", fake_input)
    return answers


def capture_backend(monkeypatch, codes=None):
    calls = []
    codes = list(codes or [])

    def fake_run(argv):
        calls.append(list(argv))
        return codes.pop(0) if codes else 0
    monkeypatch.setattr(menu, "run_backend", fake_run)
    return calls


def first_run_answers(source, dest, copy="y"):
    return ["1",                 # menu: back up
            str(source),         # sources
            str(dest),           # destination
            "",                  # formats: keep the default
            "",                  # skip inside export: none
            "", "", "",          # unselected, unexported, c1 cache: defaults
            "", "",              # jxl-photo derivatives, Capture One step: defaults
            "",                  # replace changed: no
            copy,                # step 2/2: copy now?
            "n",                 # save as preset?
            "0"]                 # exit


def test_backup_is_a_dry_run_then_a_copy(tmp_path, monkeypatch):
    source = tmp_path / "G" / "2025"
    source.mkdir(parents=True)
    calls = capture_backend(monkeypatch)
    script(monkeypatch, first_run_answers(source, tmp_path / "out"))
    assert menu.main([]) == 0
    assert len(calls) == 2
    assert "--dry-run" in calls[0] and "--yes" not in calls[0]
    assert "--yes" in calls[1] and "--dry-run" not in calls[1]
    assert calls[0][:4] == ["--source", str(source), "--dest", str(tmp_path / "out")]


def test_declining_the_copy_runs_only_the_dry_run(tmp_path, monkeypatch):
    source = tmp_path / "G"
    source.mkdir()
    calls = capture_backend(monkeypatch)
    script(monkeypatch, first_run_answers(source, tmp_path / "out", copy="n")[:-2] + ["0"])
    assert menu.main([]) == 0
    assert len(calls) == 1 and "--dry-run" in calls[0]


def test_dry_run_abort_never_offers_the_copy(tmp_path, monkeypatch):
    source = tmp_path / "G"
    source.mkdir()
    calls = capture_backend(monkeypatch, codes=[2])
    answers = first_run_answers(source, tmp_path / "out")
    script(monkeypatch, answers[:11] + ["0"])
    assert menu.main([]) == 0
    assert len(calls) == 1


def test_read_errors_need_an_explicit_yes(tmp_path, monkeypatch):
    source = tmp_path / "G"
    source.mkdir()
    calls = capture_backend(monkeypatch, codes=[1])
    answers = first_run_answers(source, tmp_path / "out")
    script(monkeypatch, answers[:11] + ["", "0"])    # Enter on "copy the rest anyway?" = no
    assert menu.main([]) == 0
    assert len(calls) == 1


def test_choices_are_remembered(tmp_path, monkeypatch, menu_config):
    source = tmp_path / "G"
    source.mkdir()
    capture_backend(monkeypatch)
    answers = first_run_answers(source, tmp_path / "out")
    answers[3] = "4"                                 # formats: 4th option
    answers[4] = "JXL_mobile"
    script(monkeypatch, answers)
    assert menu.main([]) == 0
    saved = json.loads(menu_config.read_text(encoding="utf-8"))["last"]
    assert saved["sources"] == [str(source)] and saved["exclude_export_subfolders"] == "JXL_mobile"
    calls = capture_backend(monkeypatch)
    script(monkeypatch, ["1", "", "y", "n", "0"])    # use these settings, copy, no preset
    assert menu.main([]) == 0
    argv = calls[1]
    assert argv[argv.index("--exclude-export-subfolders") + 1] == "JXL_mobile"
    assert argv[argv.index("--export-formats") + 1] == saved["export_formats"]


def test_defaults_come_from_the_backend(tmp_path, monkeypatch):
    """The menu must read selective_backup.py's settings, never repeat them."""
    monkeypatch.setattr(menu._backend(), "EXPORT_FORMATS", "jxl+png")
    monkeypatch.setattr(menu._backend(), "UNEXPORTED", "copy")
    source = tmp_path / "G"
    source.mkdir()
    calls = capture_backend(monkeypatch)
    script(monkeypatch, first_run_answers(source, tmp_path / "out"))
    assert menu.main([]) == 0
    argv = calls[0]
    assert argv[argv.index("--export-formats") + 1] == "jxl+png"
    assert argv[argv.index("--unexported") + 1] == "copy"


def test_closed_stdin_leaves_cleanly(monkeypatch):
    calls = capture_backend(monkeypatch)
    script(monkeypatch, [])
    assert menu.main([]) == 2
    assert calls == []


def test_presets_unattended(tmp_path, monkeypatch, menu_config, capsys):
    source = tmp_path / "G"
    source.mkdir()
    calls = capture_backend(monkeypatch)
    answers = first_run_answers(source, tmp_path / "out")
    answers[-2:] = ["y", "NIGHTLY", "0"]             # save as preset NIGHTLY
    script(monkeypatch, answers)
    assert menu.main([]) == 0
    assert menu.main(["--list-presets"]) == 0
    assert "NIGHTLY" in capsys.readouterr().out
    calls.clear()
    assert menu.main(["--run-preset", "NIGHTLY"]) == 0
    assert len(calls) == 1 and "--yes" in calls[0] and "--dry-run" not in calls[0]
    assert menu.main(["--run-preset", "nope"]) == 2


def test_real_backup_through_the_menu(tmp_path, monkeypatch):
    """End to end: the real backend in a child process, scripted answers."""
    build_base_tree(tmp_path)
    source = tmp_path / "G" / "2025" / "shoot"
    dest = tmp_path / "out"
    before = {p: p.stat().st_mtime_ns for p in source.rglob("*") if p.is_file()}
    script(monkeypatch, first_run_answers(source, dest))
    assert menu.main([]) == 0
    copied = dest_files(dest)
    drive_rel = Path(os.path.splitdrive(str(source))[1].lstrip("\\/")).as_posix()
    assert f"{drive_rel}/_EXPORT/16B_JXL/_DSC6489_ProPhoto-g22_v1.jxl" in copied
    assert f"{drive_rel}/_DSC6489.NEF" in copied
    assert not any("_OUTTAKES" in rel for rel in copied)
    assert {p: p.stat().st_mtime_ns for p in source.rglob("*") if p.is_file()} == before


def test_menu_flags_are_documented():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for flag in ("--run-preset", "--list-presets", "photo_backup.py"):
        assert flag in readme


def test_menu_preset_finds_backup_on_another_letter(tmp_path, monkeypatch, menu_config):
    backup_id = "a" * 32
    other = tmp_path / "other"
    found = other / "fake" / "photos" / "backup"
    (found / "_photo_backup").mkdir(parents=True)
    (found / "_photo_backup" / "volume.json").write_text(
        json.dumps({"backup_id": backup_id}), encoding="utf-8")
    source = tmp_path / "G"
    source.mkdir()
    preset = menu.default_session()
    preset.update({"sources": [str(source)],
                   "dest": str(Path("C:/fake/photos/backup")), "kind": "backup"})
    menu_config.write_text(json.dumps(
        {"last": {}, "presets": {"P": dict(preset, backup_id=backup_id)}}),
        encoding="utf-8")
    calls = capture_backend(monkeypatch)
    monkeypatch.setattr(menu, "list_drives",
                        lambda: [(str(other) + os.sep, "", 0, 0)])
    assert menu.main(["--run-preset", "P"]) == 0
    argv = calls[0]
    assert argv[argv.index("--dest") + 1] == str(found)
    assert argv[argv.index("--expect-backup-id") + 1] == backup_id
    calls.clear()
    monkeypatch.setattr(menu, "list_drives", lambda: [])
    menu_config.write_text(json.dumps(
        {"last": {}, "presets": {"P": dict(preset, backup_id="b" * 32)}}),
        encoding="utf-8")
    assert menu.main(["--run-preset", "P"]) == 2
    assert calls == []


def test_menu_rolling_verify_argv():
    session = menu.default_session()
    session.update({"sources": ["x"], "dest": "y", "verify_mode": "full", "max_gb": 200.0})
    argv = menu.build_argv(session, "verify")
    assert argv[argv.index("--max-gb") + 1] == "200.0"
    session["verify_mode"] = "quick"
    assert "--max-gb" not in menu.build_argv(session, "verify")


def test_menu_restore_flow(tmp_path, monkeypatch, menu_config):
    make_tree(tmp_path, {
        "G/2025/shoot/_DSC1.NEF": 100,
        "G/2025/shoot/_EXPORT/x/_DSC1_v1.jpg": 50,
    })
    source = tmp_path / "G" / "2025"
    backup = tmp_path / "out"
    assert selective_backup.main(argv_for(source, backup, tmp_path, "--yes")) == 0
    calls = []

    def fake_restore(argv):
        calls.append(list(argv))
        return 0

    monkeypatch.setattr(menu, "run_restore", fake_restore)
    script(monkeypatch, ["5", str(backup), "", "1", "y", "0"])
    assert menu.main([]) == 0
    assert len(calls) == 2
    assert "--backup" in calls[0] and "--dry-run" in calls[0]
    assert "--yes" not in calls[0]
    assert "--to-original" in calls[0]
    assert "--yes" in calls[1] and "--dry-run" not in calls[1]
    assert "--only" not in calls[0]


def capture_mirror(monkeypatch, codes=None):
    calls = []
    codes = list(codes or [])

    def fake_run(argv):
        calls.append(list(argv))
        return codes.pop(0) if codes else 0
    monkeypatch.setattr(menu, "run_mirror", fake_run)
    monkeypatch.setattr(menu, "run_captureone", lambda argv: 0)
    return calls


def test_menu_mirror_asks_in_the_mirror_itself(tmp_path, monkeypatch, menu_config):
    """One real run, without --yes: the mirror asks right below its own box,
    with the numbers of the run that changes the backup (finding 5 of the rc1
    review: a dry-run confirmed in the menu, then --yes)."""
    calls = capture_mirror(monkeypatch)
    manifest, dest = str(tmp_path / "m.csv"), str(tmp_path / "out")
    script(monkeypatch, ["8", "1", manifest, dest, "n", "n", "0", "0"])
    assert menu.main([]) == 0
    assert calls == [["--manifest", manifest, "--dest", dest]]


def test_menu_mirror_answered_no_offers_nothing(tmp_path, monkeypatch, menu_config):
    calls = capture_mirror(monkeypatch, codes=[2])      # "no" inside the mirror: exit 2
    script(monkeypatch, ["8", "1", str(tmp_path / "m.csv"), str(tmp_path / "out"), "n",
                         "0", "0"])
    assert menu.main([]) == 0
    assert len(calls) == 1 and "--yes" not in calls[0] and "--dry-run" not in calls[0]


def test_menu_total_mirror_and_a_stopped_one(tmp_path, monkeypatch, menu_config):
    calls = capture_mirror(monkeypatch)
    script(monkeypatch, ["8", "2", str(tmp_path / "m.csv"), str(tmp_path / "out"), "n",
                         "0", "0"])           # a total mirror never offers a preset
    assert menu.main([]) == 0
    assert all("--total" in call for call in calls) and len(calls) == 1
    calls = capture_mirror(monkeypatch, codes=[2])
    script(monkeypatch, ["8", "1", "", "", "n", "0", "0"])     # the remembered manifest/dest
    assert menu.main([]) == 0
    assert len(calls) == 1


def test_menu_mirror_preset_runs_unattended(tmp_path, monkeypatch, menu_config, capsys):
    calls = capture_mirror(monkeypatch)
    manifest, dest = str(tmp_path / "m.csv"), str(tmp_path / "out")
    script(monkeypatch, ["8", "1", manifest, dest, "n", "y", "MIRROR NIGHT", "0", "0"])
    assert menu.main([]) == 0
    saved = json.loads(menu_config.read_text(encoding="utf-8"))["presets"]["MIRROR NIGHT"]
    assert saved["kind"] == menu.KIND_MIRROR and saved["manifest"] == os.path.abspath(manifest)
    capsys.readouterr()
    assert menu.main(["--list-presets"]) == 0
    assert "mirror: " in capsys.readouterr().out
    calls.clear()
    assert menu.main(["--run-preset", "MIRROR NIGHT"]) == 0
    assert calls == [["--manifest", os.path.abspath(manifest), "--dest", dest, "--unattended"]]


def test_menu_trash_items(tmp_path, monkeypatch, menu_config):
    calls = capture_mirror(monkeypatch)
    dest = str(tmp_path)
    script(monkeypatch, ["8", "3", dest, "4", dest, "2026-13-01", "2026-11-01", "0", "0"])
    assert menu.main([]) == 0
    assert calls == [["--dest", dest, "--show-trash"],
                     ["--dest", dest, "--empty-trash", "--dry-run"],
                     ["--dest", dest, "--empty-trash", "--before", "2026-11-01"]]


def test_menu_mirror_settings_come_from_the_script(monkeypatch):
    import mirror_backup
    assert menu._mirror_setting("MIRROR_KEEP_DAYS") == mirror_backup.MIRROR_KEEP_DAYS


# ---- v0.14.0-rc4: the menu remembers jxl-photo ---------------------------------------------------
def test_settings_remember_jxlphoto_after_checking_it(tmp_path, monkeypatch, menu_config):
    import shutil
    tool = tmp_path / "jxl-photo" / "jxl_photo.py"
    tool.parent.mkdir()
    shutil.copy2(Path(__file__).with_name("fake_jxlphoto.py"), tool)
    monkeypatch.delenv("FAKE_JXLPHOTO_VERSION", raising=False)
    # answers: jxl-photo? its path; the work folder? (rc5) no; the conversion off? no;
    # forget the choices? no
    script(monkeypatch, ["y", str(tmp_path / "missing.py"), "n", "n", "n"])
    menu.settings_menu(menu.load_config())
    assert "jxlphoto" not in menu.load_config()                 # not found: not saved
    script(monkeypatch, ["y", f'"{tool}"', "n", "n", "n"])
    menu.settings_menu(menu.load_config())
    assert menu.load_config()["jxlphoto"] == str(tool)
    script(monkeypatch, ["y", "-", "n", "n", "n"])
    menu.settings_menu(menu.load_config())
    assert menu.load_config()["jxlphoto"] == ""


def test_the_scripts_get_the_remembered_jxlphoto(monkeypatch, menu_config):
    commands = []

    class Done:
        returncode = 0
    monkeypatch.setattr(menu.subprocess, "run", lambda command, **kwargs: commands.append(
        command) or Done())
    monkeypatch.setattr(menu, "_menu_title", lambda: None)
    menu.run_manifest(["--manifest", "m.csv"])                  # nothing remembered
    menu_config.write_text(json.dumps({"jxlphoto": r"C:\jxl\jxl_photo.py"}), encoding="utf-8")
    menu.run_manifest(["--manifest", "m.csv"])
    menu.run_mirror(["--dest", "D"])
    menu.run_backend(["--source", "S"])
    menu.run_restore(["--backup", "B", "--jxlphoto", r"C:\other.py"])   # named: kept
    menu.run_captureone(["--dest", "D"])                        # needs no jxl-photo
    tails = [command[2:] for command in commands]
    assert tails[0] == ["--manifest", "m.csv"]
    assert tails[1] == ["--manifest", "m.csv", "--jxlphoto", r"C:\jxl\jxl_photo.py"]
    assert tails[2][-2:] == tails[3][-2:] == ["--jxlphoto", r"C:\jxl\jxl_photo.py"]
    assert tails[4] == ["--backup", "B", "--jxlphoto", r"C:\other.py"]
    assert tails[5] == ["--dest", "D"]


# ---- v0.14.0-rc5: the menu remembers the conversions' work folder -------------------------------
def test_settings_remember_the_work_folder_when_it_exists(tmp_path, monkeypatch, menu_config):
    work = tmp_path / "TEMP"
    work.mkdir()
    # answers: jxl-photo? no; the work folder? yes, its path; the conversion off? no;
    # forget the choices? no
    script(monkeypatch, ["n", "y", str(tmp_path / "missing"), "n", "n"])
    menu.settings_menu(menu.load_config())
    assert "work_dir" not in menu.load_config()                 # not a folder: not saved
    script(monkeypatch, ["n", "y", f'"{work}"', "n", "n"])
    menu.settings_menu(menu.load_config())
    assert menu.load_config()["work_dir"] == str(work)
    script(monkeypatch, ["n", "y", "-", "n", "n"])
    menu.settings_menu(menu.load_config())
    assert menu.load_config()["work_dir"] == ""


def test_the_backups_get_the_remembered_work_folder(monkeypatch, menu_config):
    """Only the scripts that convert get it (the restore works next to its
    target), and a --work-dir already given wins."""
    commands = []

    class Done:
        returncode = 0
    monkeypatch.setattr(menu.subprocess, "run", lambda command, **kwargs: commands.append(
        command) or Done())
    monkeypatch.setattr(menu, "_menu_title", lambda: None)
    menu.run_backend(["--source", "S"])                         # nothing remembered
    menu_config.write_text(json.dumps({"work_dir": r"E:\TEMP"}), encoding="utf-8")
    menu.run_manifest(["--manifest", "m.csv"])
    menu.run_mirror(["--dest", "D"])
    menu.run_backend(["--source", "S", "--work-dir", r"F:\other"])  # named: kept
    menu.run_restore(["--backup", "B"])
    menu.run_captureone(["--dest", "D"])
    tails = [command[2:] for command in commands]
    assert tails[0] == ["--source", "S"]
    assert tails[1] == ["--manifest", "m.csv", "--work-dir", r"E:\TEMP"]
    assert tails[2] == ["--dest", "D", "--work-dir", r"E:\TEMP"]
    assert tails[3] == ["--source", "S", "--work-dir", r"F:\other"]
    assert tails[4] == ["--backup", "B"]
    assert tails[5] == ["--dest", "D"]


def test_the_conversion_off_on_this_computer(monkeypatch, menu_config):
    """[4] Settings turns the conversion off (a trip with a slow computer): the
    backups get --no-convert, a verify, the restore and Capture One never; the
    menu says so on top; the same question turns it back on."""
    # answers: jxl-photo? no; the work folder? no; the conversion off? yes; forget? no
    script(monkeypatch, ["n", "n", "y", "n"])
    menu.settings_menu(menu.load_config())
    assert menu.load_config()["no_convert"] == "yes"
    commands = []

    class Done:
        returncode = 0
    monkeypatch.setattr(menu.subprocess, "run", lambda command, **kwargs: commands.append(
        command) or Done())
    monkeypatch.setattr(menu, "_menu_title", lambda: None)
    menu.run_backend(["--source", "S"])
    menu.run_manifest(["--manifest", "m.csv"])
    menu.run_mirror(["--dest", "D"])
    menu.run_manifest(["--manifest", "m.csv", "--verify", "quick"])
    menu.run_restore(["--backup", "B"])
    menu.run_captureone(["--dest", "D"])
    tails = [command[2:] for command in commands]
    assert [tail[-1] == "--no-convert" for tail in tails] == [True, True, True, False, False,
                                                              False]
    script(monkeypatch, ["n", "n", "y", "n"])
    menu.settings_menu(menu.load_config())
    assert menu.load_config()["no_convert"] == ""


def test_the_hints_name_the_program_that_runs(monkeypatch):
    """A hint says how to start a script here: `python ...`, or in the Windows
    program its own name and command (PENDENCIAS 109)."""
    backend = menu._backend()
    assert backend._run_command("photo_backup.py") == "python photo_backup.py"
    monkeypatch.setattr(menu.sys, "frozen", True, raising=False)
    monkeypatch.setattr(menu.sys, "executable", r"D:\Programs\photo-backup\photo-backup.exe")
    assert backend._run_command("photo_backup.py") == "photo-backup.exe"
    assert backend._run_command("captureone_backup.py") == "photo-backup.exe captureone"
    assert backend._run_command("restore_backup.py") == "photo-backup.exe restore"
