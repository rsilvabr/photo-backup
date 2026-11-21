"""v0.14.0-rc6: jxl-photo 2.31.0-rc3 (a '%' in the temp folder, JP-22), what a
failed conversion says, a file that never converts, no empty `pbconv`, output
into a file in UTF-8. jxl-photo is played by tests/fake_jxlphoto.py."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import manifest_backup as mb
import mirror_backup as mir
import selective_backup as sb

from conftest import argv_for, make_tree, row_count, snapshot
from test_bug_sweep import make_junction
from test_jpeg_jxl import add_jpegs, calls
from test_tiff_jxl import (FAKE, backed, backup, closed_console, db, env, restore,  # noqa: F401
                           restore_statuses, write_manifest)

ROOT = Path(__file__).resolve().parents[1]


def percent_folder(tmp_path, name="te%mp"):
    folder = tmp_path / name
    folder.mkdir()
    return folder


# ---- 1-5 the work folder with a '%' (R2) ----------------------------------------------------------
def test_1_a_work_folder_with_a_percent_is_refused(tmp_path):
    work = percent_folder(tmp_path)
    errors = sb._work_dir_errors([str(tmp_path / "src")], str(tmp_path / "dest"), str(work))
    assert len(errors) == 1 and "'%'" in errors[0] and "JP-22" in errors[0]
    assert sb._work_dir_errors([str(tmp_path / "src")], str(tmp_path / "dest"),
                               str(tmp_path / "work")) == []


def test_2_the_default_work_folder_names_work_dir(tmp_path, monkeypatch):
    temp = percent_folder(tmp_path, "t%x")
    monkeypatch.setattr(sb, "JXL_WORK_DIR", "")
    monkeypatch.setattr(sb.tempfile, "gettempdir", lambda: str(temp))
    errors = sb._work_dir_errors([str(tmp_path / "src")], str(tmp_path / "dest"))
    assert len(errors) == 1 and "--work-dir" in errors[0] and "system's temp folder" in errors[0]


def test_3_a_work_folder_name_with_a_percent_is_refused(monkeypatch):
    monkeypatch.setattr(sb, "JXL_WORK_FOLDER", "pb%conv")
    assert sb._work_folder_name_problem() is not None
    monkeypatch.setattr(sb, "JXL_WORK_FOLDER", "pbconv")
    assert sb._work_folder_name_problem() is None


def test_4_a_run_with_a_percent_work_dir_stops_before_anything(env, tmp_path, capsys):
    work = percent_folder(tmp_path)
    before = snapshot(env.src)
    assert backup(env, "--tiff", "jxl", "--jpeg", "jxl", "--work-dir", str(work)) == 2
    assert "JP-22" in capsys.readouterr().err
    assert not env.dest.exists() or not backed(env.dest)
    assert snapshot(env.src) == before
    assert not list(work.iterdir())


def test_5_the_work_root_refuses_a_percent(tmp_path):
    with pytest.raises(OSError, match="JP-22"):
        sb._work_root(str(tmp_path / "a%b"))


# ---- 6-7 the fake refuses a '%' temp folder; the restore gives it another one (R2, R8) -------------
def test_6_a_percent_temp_folder_is_refused_and_said(tmp_path):
    temp = percent_folder(tmp_path)
    environment = dict(os.environ, TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp))
    out = tmp_path / "out"
    out.mkdir()
    run = subprocess.run([sys.executable, str(FAKE), "encode", str(tmp_path / "x.tif"), str(out)],
                         capture_output=True, text=True, env=environment)
    assert run.returncode == 2 and run.stdout == ""
    assert not list(out.iterdir()) and not list(temp.iterdir())
    said = sb._jxlphoto_said(run)
    assert said.startswith("fake_jxlphoto.py: error: the temp folder has a '%'")
    assert said == [line for line in run.stderr.splitlines() if "error:" in line][-1]
    version = subprocess.run([sys.executable, str(FAKE), "encode", "--version"],
                             capture_output=True, text=True, env=environment)
    assert version.returncode == 0                 # --version is answered first


def test_7_the_restore_into_a_percent_folder_gets_a_temp_folder_of_its_own(
        env, tmp_path, monkeypatch, photobackup_log_dir):
    monkeypatch.setattr(sb, "JXLPHOTO_SERVE", False)    # a single call's own shape (rc7: serve has its own tests)
    assert backup(env, "--tiff", "jxl") == 0
    envs = tmp_path / "envs"
    monkeypatch.setenv("FAKE_JXLPHOTO_ENVS", str(envs))
    assert "%" in str(env.out)
    assert restore(env, "--to", env.out) == 0
    statuses = restore_statuses(photobackup_log_dir)
    assert {statuses[rel] for rel in ("2025/shoot/DSC_0001.tif", "2025/scans/000037.tiff")
            } == {"RESTORED_FROM_JXL"}
    seen = [json.loads(path.read_text(encoding="utf-8")) for path in envs.glob("*.json")]
    decodes = [item for item in seen if item["argv"][0] == "decode"]
    assert decodes
    for item in decodes:
        for name in ("TEMP", "TMP", "TMPDIR", "gettempdir"):
            if name in item:
                assert "%" not in item[name], (name, item[name])
        assert os.path.basename(item["TEMP"]).startswith("pbrestore_")
        assert not os.path.exists(item["TEMP"])


# ---- 8-9 what a failed conversion says (R3) ---------------------------------------------------------
CMYK = SimpleNamespace(stdout=(
    "00:20:01 | ERROR | [1/1] ERROR | cmyk.jpg | cjxl:  be created. Possibly there is too much tail data.\n"
    "Try using --allow_jpeg_reconstruction 0, to losslessly recompress the JPEG image data "
    "without bitstream reconstruction data.\n"
    "EncodeImageJXL() failed.\n"
    "00:20:01 | INFO | \n"
    "--------------------------------------------------\n"
    "00:20:01 | INFO | Done: 0 OK | 0 reconverted | 0 up to date | 1 errors\n"
    "00:20:01 | INFO | Log: C:\\x\\log\\jxl_jpeg_transcoder\\20261122_002001_57868.log\n"
    "00:20:01 | WARNING | Errors (1):\n"
    "00:20:01 | WARNING |   -> C:\\x\\cmyk.jpg: cjxl: be created. Possibly there is too much "
    "tail data. EncodeImageJXL() failed.\n"), stderr="")
CAMERA = SimpleNamespace(stdout=(
    "16:12:07 | ERROR | [1/1] ERROR | DSC00744.JPG | cjxl: 2] {Clang 22.1.3}\n"
    "Encoding [JPEG, lossless transcode, effort: 5]\n"
    "Error while decoding the JPEG image. It may be corrupt (e.g. truncated) or of an "
    "unsupported type (e.g. CMYK).\n"
    "EncodeImageJXL() failed.\n"
    "16:12:07 | INFO | \n"
    "16:12:07 | WARNING | Errors (1):\n"), stderr="")


def test_8_a_failed_conversion_says_cjxls_reason():
    said = sb._jxlphoto_said(CMYK)
    assert "Try using --allow_jpeg_reconstruction" in said
    assert "EncodeImageJXL() failed" in said
    assert "Errors (1)" not in said and "WARNING" not in said
    said = sb._jxlphoto_said(CAMERA)
    assert "Error while decoding the JPEG image" in said
    assert "Errors (1)" not in said


def test_9_what_is_said_is_short_and_keeps_the_page_check():
    lines = "\n".join("x" * 100 for _ in range(50))      # 50 lines: four are kept
    assert len(sb._jxlphoto_said(SimpleNamespace(
        stdout="12:00:00 | ERROR | [1/1] ERROR | a.tif | cjxl: end\n" + lines, stderr=""))) <= 600
    lines = "\n".join("x" * 400 for _ in range(50))
    run = SimpleNamespace(stdout="12:00:00 | ERROR | [1/1] ERROR | a.tif | cjxl: end\n" + lines,
                          stderr="")
    said = sb._jxlphoto_said(run)
    assert len(said) <= 600 and said.endswith("...") and said.startswith("[1/1] ERROR | a.tif")
    run = SimpleNamespace(stdout=(
        "12:00:01 | ERROR | [1/1] ERROR | a.tif | round-trip verification FAILED on page 0: "
        "lossless encode did not decode back pixel-identical\n"
        "12:00:01 | INFO | Done: 0 OK | 1 errors\n"), stderr="")
    assert "round-trip verification" in sb._jxlphoto_said(run)


# ---- 10 a file that never converts (R4) ---------------------------------------------------------------
BAD = "cmyk.jpg"


def transcodes_of(env, name=BAD):
    return [call for call in calls(env, command="transcode")
            if "--format" not in call and name in call[1]]


def fresh_calls(env):
    if env.calls.exists():
        shutil.rmtree(env.calls)
    sb._JXLPHOTO_VERSIONS.clear()


def not_converted(env):
    return db(env.dest, "SELECT rel, failures, tool FROM not_converted")


@pytest.fixture
def failing(env, monkeypatch):
    add_jpegs(env)
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", "exit")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", BAD)
    return env


def run_jpeg(env, *extra):
    fresh_calls(env)
    return backup(env, "--jpeg", "jxl", *extra)


def new_jpeg(env, number):
    """One more JPEG that converts: a run that converts one proves jxl-photo
    works there, so its failures count (v0.14.0-rc6 review, M2)."""
    (env.src / "2025/phone" / f"new_{number:02d}.jpg").write_bytes(
        b"\xff\xd8a new photo " + bytes([65 + number]) * 40)


def test_10_a_file_jxlphoto_cannot_convert_is_not_tried_again(failing, monkeypatch, capsys,
                                                              photobackup_log_dir):
    env = failing
    copy = env.dest / "2025/phone" / BAD
    # a. the first run: the failure, the file as it is, one failure recorded (the
    #    other JPEGs converted in the same run: it counts)
    assert run_jpeg(env) == 1
    log = (sorted(Path(photobackup_log_dir).glob("*.log"))[-1]).read_text(encoding="utf-8")
    assert "CONVERT FAILED" in log
    assert copy.read_bytes() == (env.src / "2025/phone" / BAD).read_bytes()
    assert [row[1] for row in not_converted(env)] == [1]
    # b. the second, with a new JPEG that converts: tried again, counted
    new_jpeg(env, 1)
    assert run_jpeg(env) == 1
    assert len(transcodes_of(env)) == 1
    assert [row[1] for row in not_converted(env)] == [2]
    # c. the third: not called, no failure, said in the log and the summary
    stat = copy.stat()
    capsys.readouterr()
    assert run_jpeg(env) == 0
    out = capsys.readouterr().out
    assert transcodes_of(env) == []
    assert row_count(out, "JPEG: not converted again") == "1"
    log = (sorted(Path(photobackup_log_dir).glob("*.log"), key=lambda p: p.stat().st_mtime_ns)[-1]
           ).read_text(encoding="utf-8")
    assert "NOT CONVERTED AGAIN" in log and "2 runs in a row" in log
    assert copy.stat().st_mtime_ns == stat.st_mtime_ns and (
        copy.read_bytes() == (env.src / "2025/phone" / BAD).read_bytes())
    assert [row[1] for row in not_converted(env)] == [2]
    # d. a dry-run says the same and writes nothing in the catalog
    catalog = env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"
    before = catalog.read_bytes()
    capsys.readouterr()
    assert run_jpeg(env, "--dry-run") == 0
    assert row_count(capsys.readouterr().out, "JPEG: not converted again") == "1"
    assert catalog.read_bytes() == before
    # e. --retry-conversions tries again
    new_jpeg(env, 2)
    assert run_jpeg(env, "--retry-conversions") == 1
    assert len(transcodes_of(env)) == 1
    assert [row[1] for row in not_converted(env)] == [3]
    # g. another jxl-photo: tried again, one failure
    monkeypatch.setenv("FAKE_JXLPHOTO_VERSION", "9.9.8")
    new_jpeg(env, 3)
    assert run_jpeg(env) == 1
    assert len(transcodes_of(env)) == 1
    assert [(row[1], row[2]) for row in not_converted(env)] == [(1, "jxl-photo 9.9.8")]
    monkeypatch.delenv("FAKE_JXLPHOTO_VERSION")
    # f. the source changed: tried again, failures back to 1
    new_jpeg(env, 4)
    assert run_jpeg(env) == 1
    new_jpeg(env, 5)
    assert run_jpeg(env) == 1                                   # 1, 2 with 9.9.9
    assert [row[1] for row in not_converted(env)] == [2]
    source = env.src / "2025/phone" / BAD
    source.write_bytes(source.read_bytes() + b"more")
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 60_000_000_000))
    new_jpeg(env, 6)
    assert run_jpeg(env, "--update-changed") == 1
    assert len(transcodes_of(env)) == 1
    assert [row[1] for row in not_converted(env)] == [1]
    # h. CONVERT_GIVE_UP_AFTER = 0: always tried again
    new_jpeg(env, 7)
    assert run_jpeg(env, "--update-changed") == 1               # failures 2 (the copy is current)
    assert run_jpeg(env, "--update-changed") == 0 and transcodes_of(env) == []
    monkeypatch.setattr(sb, "CONVERT_GIVE_UP_AFTER", 0)
    for _ in range(2):
        assert run_jpeg(env, "--update-changed") == 1
        assert len(transcodes_of(env)) == 1
    monkeypatch.setattr(sb, "CONVERT_GIVE_UP_AFTER", 2)
    # j. a catalog without the table: a dry-run works and writes nothing, a real run makes it
    db_path = env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"
    connection = sqlite3.connect(str(db_path))
    connection.execute("DROP TABLE not_converted")
    connection.commit()
    connection.close()
    before = db_path.read_bytes()
    assert run_jpeg(env, "--dry-run") in (0, 1)
    assert db_path.read_bytes() == before
    new_jpeg(env, 8)
    assert run_jpeg(env) == 1
    assert [row[1] for row in not_converted(env)] == [1]
    # i. jxl-photo converts it: the row goes, the copy as it is is retired to versions
    monkeypatch.delenv("FAKE_JXLPHOTO_FAIL")
    assert run_jpeg(env, "--retry-conversions") == 0
    assert not_converted(env) == []
    assert not copy.exists() and Path(str(copy) + ".jxl").exists()
    versions = list((env.dest / sb.CATALOG_FOLDER / "versions").rglob(BAD))
    assert versions


def test_10k_the_option_reaches_the_backends(tmp_path):
    modes = mb.read_modes(os.path.join(str(ROOT), mb.MODES_FILE))
    (tmp_path / "src").mkdir()
    manifest = write_manifest(tmp_path / "m.csv", (tmp_path / "src", "6", "", "", ""))
    rows = mb.read_manifest(manifest, modes)
    parsed = mb.build_parser().parse_args(["--manifest", str(manifest), "--dest", str(tmp_path),
                                           "--retry-conversions"])
    assert parsed.retry_conversions
    assert mb._backend_args(parsed, rows, str(tmp_path)).retry_conversions is True
    parsed = mb.build_parser().parse_args(["--manifest", str(manifest), "--dest", str(tmp_path)])
    assert mb._backend_args(parsed, rows, str(tmp_path)).retry_conversions is False
    mirror = mir.build_parser().parse_args(["--source", str(tmp_path), "--dest", str(tmp_path / "d"),
                                            "--retry-conversions"])
    assert mir._plain_backend_args(mirror, []).retry_conversions is True
    assert sb._build_parser().parse_args(["--source", "a", "--dest", "b", "--retry-conversions"]
                                         ).retry_conversions


# ---- 11 no empty pbconv (R5) ---------------------------------------------------------------------------
def test_11_nothing_of_the_work_folder_is_left(env, tmp_path):
    work = Path(sb.JXL_WORK_DIR)
    assert backup(env, "--tiff", "jxl") == 0
    assert (work / sb.JXL_WORK_FOLDER).exists() is False
    # a folder of a running process (ours: the parent's number, with the mark) stays
    other = work / sb.JXL_WORK_FOLDER / str(os.getppid())
    other.mkdir(parents=True)
    (other / sb._WORK_MARK).write_text("mark", encoding="utf-8")
    (env.src / "2025/scans/000039.tif").write_text("SCAN 000039 " * 40, encoding="utf-8")
    assert backup(env, "--tiff", "jxl") == 0
    assert (other / sb._WORK_MARK).exists()
    assert [path.name for path in (work / sb.JXL_WORK_FOLDER).iterdir()] == [str(os.getppid())]


def test_11_a_work_folder_that_is_a_junction_is_never_removed(tmp_path):
    target = tmp_path / "target"
    (target).mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    link = tmp_path / "links" / sb.JXL_WORK_FOLDER
    link.parent.mkdir()
    make_junction(link, target)
    sb._remove_empty_work_parent(str(link / "1234"))
    assert os.path.lexists(link) and (target / "keep.txt").read_text(encoding="utf-8") == "keep"
    plain = tmp_path / "plain" / sb.JXL_WORK_FOLDER
    plain.mkdir(parents=True)
    sb._remove_empty_work_parent(str(plain / "1234"))
    assert not plain.exists()
    held = tmp_path / "held" / sb.JXL_WORK_FOLDER
    (held / "5678").mkdir(parents=True)
    sb._remove_empty_work_parent(str(held / "1234"))
    assert (held / "5678").exists()
    other = tmp_path / "other" / "notours"
    other.mkdir(parents=True)
    sb._remove_empty_work_parent(str(other / "1234"))
    assert other.exists()


# ---- 12 output into a file keeps the names (R6) -------------------------------------------------------
def test_12_a_log_into_a_file_is_utf8(tmp_path):
    name = "吾妻山～[x]"
    src = make_tree(tmp_path / "src", {f"{name}/IMG_0001.CR2": 50, f"{name}/IMG_0001.JPG": 20,
                                       f"{name}/_EXPORT/IMG_0001_v1.jpg": 30})
    out = tmp_path / "stdout.txt"
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    environment["PHOTOBACKUP_LOG_DIR"] = str(tmp_path / "logs")
    with open(out, "wb") as handle:
        run = subprocess.run([sys.executable, str(ROOT / "selective_backup.py"),
                              *argv_for(src, tmp_path / "dest", src, "--dry-run")],
                             stdout=handle, stderr=subprocess.STDOUT, env=environment,
                             stdin=subprocess.DEVNULL)
    assert run.returncode == 0
    assert name in out.read_bytes().decode("utf-8")


class RecordingStream:
    def __init__(self):
        self.options = None

    def isatty(self):
        return False

    def reconfigure(self, **options):
        self.options = options


def test_12_pythonioencoding_is_respected(monkeypatch):
    streams = [RecordingStream(), RecordingStream()]
    monkeypatch.setattr(sys, "stdout", streams[0])
    monkeypatch.setattr(sys, "stderr", streams[1])
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    sb._start_output()
    assert all(stream.options["encoding"] == "utf-8" and stream.options["errors"] == "replace"
               and stream.options["line_buffering"] for stream in streams)
    streams = [RecordingStream(), RecordingStream()]
    monkeypatch.setattr(sys, "stdout", streams[0])
    monkeypatch.setattr(sys, "stderr", streams[1])
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    sb._start_output()
    assert all("encoding" not in stream.options for stream in streams)
