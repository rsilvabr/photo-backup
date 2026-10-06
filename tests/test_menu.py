"""The interactive menu: scripted answers, the backend either captured or real."""
import json
import os
from pathlib import Path

import pytest

import photo_backup
import photo_backup_menu as menu

from conftest import build_base_tree, dest_files


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
    script(monkeypatch, answers[:9] + ["0"])
    assert menu.main([]) == 0
    assert len(calls) == 1


def test_read_errors_need_an_explicit_yes(tmp_path, monkeypatch):
    source = tmp_path / "G"
    source.mkdir()
    calls = capture_backend(monkeypatch, codes=[1])
    answers = first_run_answers(source, tmp_path / "out")
    script(monkeypatch, answers[:9] + ["", "0"])     # Enter on "copy the rest anyway?" = no
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
    """The menu must read photo_backup.py's settings, never repeat them."""
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
    for flag in ("--run-preset", "--list-presets", "photo_backup_menu.py"):
        assert flag in readme
