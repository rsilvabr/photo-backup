"""v0.14.0-rc7: jxl-photo's --serve mode (JP-23: one process per worker that
converts file after file), `_version_key` and the test versions (N2), a JPEG
restored by djxl without a work folder (P5). jxl-photo is played by
tests/fake_jxlphoto.py, whose serve mode is part of this round."""
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

import pytest

import restore_backup as rb
import selective_backup as sb

from test_jpeg_jxl import ALL_JPEGS, FAKE_DJXL, add_jpegs, calls
from test_tiff_jxl import (FAKE, JXL, OTHERS, TIFFS, backed, backup, closed_console,  # noqa: F401
                           converted, db, env, env_calls, no_work_left, restore, restore_statuses,
                           restored)
from conftest import latest_log, snapshot


@pytest.fixture(autouse=True)
def serve_knobs(monkeypatch):
    for name in ("FAKE_JXLPHOTO_SERVE", "FAKE_JXLPHOTO_SERVE_PIDS", "FAKE_JXLPHOTO_FAIL",
                 "FAKE_JXLPHOTO_ONLY", "FAKE_JXLPHOTO_VERSION"):
        monkeypatch.delenv(name, raising=False)
    sb._JXLPHOTO_VERSIONS.clear()
    monkeypatch.setattr(sb, "DJXL", str(FAKE_DJXL))     # the proof of a JPEG is djxl's, one jxl-photo command


def serve_pids(env):
    folder = env.root / "serve_pids"
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))] \
        if folder.exists() else []


def watch_serve(env, monkeypatch):
    monkeypatch.setenv("FAKE_JXLPHOTO_SERVE_PIDS", str(env.root / "serve_pids"))


def add_named(env, *names):
    for name in names:
        path = env.src / "2025/phone" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8" + name.encode() * 40)


def not_alive(processes):
    return all(not sb._pid_alive(item["pid"]) for item in processes)


# ---- 1-2 one process per worker ----------------------------------------------------------------
def test_serve_one_process_per_worker(env, monkeypatch):
    watch_serve(env, monkeypatch)
    monkeypatch.setattr(sb, "JXL_WORKERS", 2)
    monkeypatch.setattr(sb, "JXL_TIFF_WORKERS", 1)
    add_jpegs(env)
    add_named(env, *[f"p{number}.jpg" for number in range(8)])
    assert backup(env, "--jpeg", "jxl", "--tiff", "jxl") == 0
    rels = backed(env.dest)
    for number in range(8):
        assert f"2025/phone/p{number}.jpg.jxl" in rels
    assert JXL <= rels
    processes = serve_pids(env)
    assert len([item for item in processes if item["command"] == "transcode"]) <= 2
    assert len([item for item in processes if item["command"] == "encode"]) == 1
    assert len(calls(env, command="transcode")) == len(ALL_JPEGS) + 8     # a job per file
    assert len(calls(env, command="encode")) == 3
    assert processes and not_alive(processes)
    assert no_work_left(env)


def test_serve_off_runs_one_call_per_file(env, monkeypatch):
    watch_serve(env, monkeypatch)
    monkeypatch.setattr(sb, "JXLPHOTO_SERVE", False)
    assert backup(env, "--tiff", "jxl") == 0
    assert JXL <= backed(env.dest)
    assert serve_pids(env) == []
    assert not (env.root / "serve_pids").exists()


# ---- 3-8 what a failure does ---------------------------------------------------------------------
def test_serve_failed_job_keeps_its_log_part_and_restarts(env, monkeypatch, photobackup_log_dir):
    watch_serve(env, monkeypatch)
    monkeypatch.setattr(sb, "JXL_WORKERS", 1)
    add_named(env, "first.jpg", "bad.jpg", "third.jpg", "fourth.jpg")
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", "exit")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "bad")
    assert backup(env, "--jpeg", "jxl") == 1
    line = next(line for line in latest_log(photobackup_log_dir).splitlines()
                if "CONVERT FAILED" in line)
    assert "bad.jpg" in line and "EncodeImageJXL() failed (the fake was told to)" in line
    rels = backed(env.dest)
    assert "2025/phone/bad.jpg" in rels and "2025/phone/bad.jpg.jxl" not in rels
    for name in ("first", "third", "fourth"):
        assert f"2025/phone/{name}.jpg.jxl" in rels
    kept = list(Path(photobackup_log_dir, "jxl-photo").rglob("*.log"))
    assert len(kept) == 1
    text = kept[0].read_text(encoding="utf-8")
    assert "bad.jpg" in text and "first.jpg" not in text and "third.jpg" not in text
    assert len([item for item in serve_pids(env) if item["command"] == "transcode"]) == 2


def test_serve_unavailable_falls_back(env, monkeypatch, photobackup_log_dir):
    monkeypatch.setenv("FAKE_JXLPHOTO_SERVE", "refuse")
    add_jpegs(env)
    assert backup(env, "--jpeg", "jxl", "--tiff", "jxl") == 0
    assert JXL <= backed(env.dest) and "2025/shoot/DSC_0001.JPG.jxl" in backed(env.dest)
    assert latest_log(photobackup_log_dir).count("JXL-PHOTO SERVE NOT USED") == 1
    assert not sb._CONVERTERS


def test_serve_garbage_first_line_falls_back(env, monkeypatch, photobackup_log_dir):
    monkeypatch.setenv("FAKE_JXLPHOTO_SERVE", "garbage")
    add_jpegs(env)
    assert backup(env, "--jpeg", "jxl", "--tiff", "jxl") == 0
    assert JXL <= backed(env.dest)
    log = latest_log(photobackup_log_dir)
    assert log.count("JXL-PHOTO SERVE NOT USED") == 1 and "not the serve protocol" in log
    assert not sb._CONVERTERS                           # nothing of it left running


def test_serve_process_dying_fails_only_that_file(env, monkeypatch, photobackup_log_dir):
    monkeypatch.setattr(sb, "JXL_WORKERS", 1)
    add_named(env, "first.jpg", "bad.jpg", "third.jpg")
    monkeypatch.setenv("FAKE_JXLPHOTO_SERVE", "die")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "bad")
    assert backup(env, "--jpeg", "jxl") == 1
    line = next(line for line in latest_log(photobackup_log_dir).splitlines()
                if "CONVERT FAILED" in line)
    assert "bad.jpg" in line and "ended without answering" in line
    rels = backed(env.dest)
    assert "2025/phone/bad.jpg" in rels and "2025/phone/bad.jpg.jxl" not in rels
    assert {"2025/phone/first.jpg.jxl", "2025/phone/third.jpg.jxl"} <= rels
    assert not sb._CONVERTERS


def test_serve_wrong_answer_is_not_counted(env, monkeypatch, photobackup_log_dir):
    monkeypatch.setattr(sb, "JXL_WORKERS", 1)
    monkeypatch.setattr(sb, "CONVERT_GIVE_UP_AFTER", 2)
    monkeypatch.setenv("FAKE_JXLPHOTO_SERVE", "wrongid")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "bad")
    add_named(env, "first.jpg", "bad.jpg")
    assert backup(env, "--jpeg", "jxl") == 1
    add_named(env, "second.jpg")                        # the tool works in this run too: proof
    assert backup(env, "--jpeg", "jxl") == 1
    assert db(env.dest, "SELECT count(*) FROM not_converted") == [(0,)]
    lines = [line for line in latest_log(photobackup_log_dir).splitlines()
             if "CONVERT FAILED 2025" in line]
    assert lines and all("(JP-23)" in line for line in lines)


def test_serve_fatal_job_restarts(env, monkeypatch, photobackup_log_dir):
    watch_serve(env, monkeypatch)
    monkeypatch.setattr(sb, "JXL_WORKERS", 1)
    add_named(env, "first.jpg", "bad.jpg", "third.jpg", "fourth.jpg")
    monkeypatch.setenv("FAKE_JXLPHOTO_SERVE", "fatal")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "bad")
    assert backup(env, "--jpeg", "jxl") == 1
    line = next(line for line in latest_log(photobackup_log_dir).splitlines()
                if "CONVERT FAILED" in line)
    assert "bad.jpg" in line and "RuntimeError: the fake was told to" in line
    assert {"2025/phone/first.jpg.jxl", "2025/phone/third.jpg.jxl",
            "2025/phone/fourth.jpg.jxl"} <= backed(env.dest)
    assert len([item for item in serve_pids(env) if item["command"] == "transcode"]) == 2
    assert not_alive(serve_pids(env))


# ---- 9-11 timeout, Ctrl+C, the limit ---------------------------------------------------------------
def test_serve_timeout(env, monkeypatch, photobackup_log_dir):
    sleeping = env.root / "sleeping"
    monkeypatch.setattr(sb, "_jxl_timeout_s", lambda: 3)
    monkeypatch.setenv("FAKE_JXLPHOTO_SLEEP", "60")
    monkeypatch.setenv("FAKE_JXLPHOTO_SLEEP_ONLY", "slow")
    monkeypatch.setenv("FAKE_JXLPHOTO_SLEEP_PIDS", str(sleeping))
    add_named(env, "first.jpg", "slow.jpg", "third.jpg")
    monkeypatch.setattr(sb, "JXL_WORKERS", 1)
    started = time.monotonic()
    assert backup(env, "--jpeg", "jxl") == 1
    assert time.monotonic() - started < 40
    line = next(line for line in latest_log(photobackup_log_dir).splitlines()
                if "CONVERT FAILED" in line)
    assert "slow.jpg" in line and "took more than" in line
    assert list(sleeping.glob("*.pid"))
    for pid in sleeping.glob("*.pid"):
        assert not sb._pid_alive(int(pid.stem))
    assert {"2025/phone/first.jpg.jxl", "2025/phone/third.jpg.jxl"} <= backed(env.dest)
    assert "2025/phone/slow.jpg" in backed(env.dest)


def test_serve_ctrl_c_ends_every_server(env, monkeypatch, capsys):
    watch_serve(env, monkeypatch)
    pids = env.root / "pids"
    monkeypatch.setattr(sb, "JXL_TIFF_WORKERS", 2)
    monkeypatch.setenv("FAKE_JXLPHOTO_SLEEP", "60")
    monkeypatch.setenv("FAKE_JXLPHOTO_SLEEP_ONLY", "000038")
    monkeypatch.setenv("FAKE_JXLPHOTO_SLEEP_PIDS", str(pids))
    real_wait, real_place = sb._wait_futures, sb._place_group
    placed, state = [], {"pressed": False}

    def place(*args, **kwargs):
        result = real_place(*args, **kwargs)
        placed.append(args[0].planned.rel)
        return result

    def wait(fs, *args, **kwargs):
        if not state["pressed"] and len(placed) == 2 and list(pids.glob("*.pid")):
            state["pressed"] = True
            signal.raise_signal(signal.SIGINT)
        return real_wait(fs, *args, **kwargs)
    monkeypatch.setattr(sb, "_place_group", place)
    monkeypatch.setattr(sb, "_wait_futures", wait)
    started = time.monotonic()
    assert backup(env, "--tiff", "jxl") == 130
    assert time.monotonic() - started < 30
    assert sb.STOP_SAYING in capsys.readouterr().out
    processes = serve_pids(env)
    assert processes and not_alive(processes)
    for pid in pids.glob("*.pid"):
        assert not sb._pid_alive(int(pid.stem))
    assert not sb._CONVERTERS
    assert no_work_left(env)
    assert not {rel for rel in backed(env.dest) if "000038" in rel}


def test_serve_jobs_max_restarts(env, monkeypatch):
    watch_serve(env, monkeypatch)
    monkeypatch.setattr(sb, "_SERVE_JOBS_MAX", 3)
    monkeypatch.setattr(sb, "JXL_WORKERS", 1)
    add_named(env, *[f"p{number}.jpg" for number in range(6)])    # + the export's JPEG: 7
    assert backup(env, "--jpeg", "jxl") == 0
    assert len(db(env.dest, "SELECT key FROM converted")) == 7
    assert len([item for item in serve_pids(env) if item["command"] == "transcode"]) == 3


# ---- 12-13 the restore -------------------------------------------------------------------------------
def test_serve_restore_tiff(env, monkeypatch, tmp_path, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    system_temp = tmp_path / "systemp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    watch_serve(env, monkeypatch)
    envs = env.root / "envs"
    monkeypatch.setenv("FAKE_JXLPHOTO_ENVS", str(envs))
    monkeypatch.setattr(sb, "JXL_TIFF_WORKERS", 1)
    assert "%" in str(env.out)                          # the target's own folder is no temp folder
    before = snapshot(env.dest)
    assert restore(env, "--to", env.out) == 0
    assert snapshot(env.dest) == before
    statuses = restore_statuses(photobackup_log_dir)
    assert {statuses[rel] for rel in TIFFS} == {"RESTORED_FROM_JXL"}
    processes = [item for item in serve_pids(env) if item["command"] == "decode"]
    assert len(processes) == 1 and not_alive(processes)
    logs = list(Path(photobackup_log_dir, "jxl-photo", "jxl_tiff_decoder").glob("*.log"))
    assert len(logs) == 1 and str(processes[0]["pid"]) in logs[0].name
    assert not [path for path in system_temp.iterdir()]  # no pbrestore_serve_ folder left
    for call in env_calls(envs, "decode"):
        assert "%" not in call["TEMP"] and Path(call["TEMP"]).name == "temp"
        assert Path(call["TEMP"]).parent.parent.name.startswith("pbrestore_serve_")
        assert Path(call["JXLPHOTO_LOG_DIR"]) == Path(photobackup_log_dir, "jxl-photo")
    assert not [path for path in env.out.rglob("*") if ".restoring_" in path.name]


def test_serve_restore_old_jxlphoto_one_call_per_file(env, monkeypatch, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    monkeypatch.setenv("FAKE_JXLPHOTO_VERSION", "2.30.2")   # the restore's minimum is 2.30.0
    sb._JXLPHOTO_VERSIONS.clear()
    watch_serve(env, monkeypatch)
    assert restore(env, "--to", env.out) == 0
    assert {restore_statuses(photobackup_log_dir)[rel] for rel in TIFFS} == {"RESTORED_FROM_JXL"}
    assert serve_pids(env) == []


# ---- 14 N2: the test versions -----------------------------------------------------------------------
def test_version_key_orders_test_kinds():
    key = sb._version_key
    assert (key("2.31.0_beta4") < key("2.31.0-rc1") < key("2.31.0-rc3") < key("2.31.0")
            < key("2.31.1-rc1"))
    assert key("2.31.0_alpha9") < key("2.31.0_beta1") < key("2.31.0-RC1")
    assert key("2.30.0") < key("2.31.0_beta1")
    assert key("2.31.0-weird") < key("2.31.0_alpha1")   # no known kind: the oldest, conservative
    assert key("jxl-photo 2.31.0-rc3") == key("2.31.0-rc3")
    assert key("no number") is None


def test_beta_older_than_rc_minimum_refused(env, monkeypatch):
    monkeypatch.setenv("FAKE_JXLPHOTO_VERSION", "2.31.0_beta9")
    sb._JXLPHOTO_VERSIONS.clear()
    assert sb.JXLPHOTO_MIN_VERSION == "2.31.0"
    problem = sb._jxlphoto_problem(str(env.tool))
    assert problem and "older than" in problem


def test_serve_wanted_needs_the_setting_and_the_version(monkeypatch):
    assert sb._serve_wanted("jxl-photo 2.31.0-rc3") and sb._serve_wanted("jxl-photo 9.9.9")
    assert not sb._serve_wanted("jxl-photo 2.31.0-rc2") and not sb._serve_wanted("jxl-photo ?")
    assert not sb._serve_wanted("jxl-photo 2.30.2")
    monkeypatch.setattr(sb, "JXLPHOTO_SERVE", False)
    assert not sb._serve_wanted("jxl-photo 9.9.9")


# ---- 15 P5: a JPEG by djxl makes no work folder ---------------------------------------------------------
def test_restore_jpeg_with_djxl_makes_no_work_folder(env, monkeypatch, photobackup_log_dir):
    add_jpegs(env)
    assert backup(env, "--jpeg", "jxl") == 0
    made = []
    real_makedirs, real_mkdir = os.makedirs, os.mkdir

    def makedirs(path, *args, **kwargs):
        made.append(str(path))
        return real_makedirs(path, *args, **kwargs)

    def mkdir(path, *args, **kwargs):
        made.append(str(path))
        return real_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(os, "makedirs", makedirs)
    monkeypatch.setattr(os, "mkdir", mkdir)
    assert restore(env, "--to", env.out) == 0
    monkeypatch.undo()
    assert made and not [path for path in made if ".restoring_" in path]
    assert {restore_statuses(photobackup_log_dir)[rel] for rel in ALL_JPEGS} == {"RESTORED"}
    for rel in ALL_JPEGS:
        assert (env.out / rel).read_bytes() == (env.src / rel).read_bytes(), rel
    assert not [path for path in env.out.rglob("*") if ".restoring_" in path.name]


def test_restore_jpeg_with_djxl_failing_leaves_nothing(env, monkeypatch, photobackup_log_dir):
    add_jpegs(env)
    assert backup(env, "--jpeg", "jxl") == 0

    def failing(*args, **kwargs):
        raise sb._ConvertFailed("djxl was told to fail")
    monkeypatch.setattr(sb, "_rebuild_jpeg", failing)
    assert restore(env, "--to", env.out) == 1
    assert {restore_statuses(photobackup_log_dir)[rel] for rel in ALL_JPEGS} == {"ERROR"}
    assert not restored(env) & ALL_JPEGS                # no JPEG half made, none at all
    assert not [path for path in env.out.rglob("*") if ".restoring_" in path.name]


# ---- 16 an answer's error is capped ------------------------------------------------------------------------
LONG_SERVER = '''
import json, sys
if "--version" in sys.argv:
    print("jxl-photo 9.9.9")
    sys.exit(0)
print(json.dumps({"serve": 1, "version": "9.9.9", "script": "jxl_jpeg_transcoder.py"}), flush=True)
for line in sys.stdin:
    job = json.loads(line)
    print(json.dumps({"id": job["id"], "exit": 1, "outputs": [], "error": "x" * 1000}), flush=True)
'''


def test_serve_answer_error_capped(env, monkeypatch, tmp_path, photobackup_log_dir):
    tool = tmp_path / "long" / "jxl_photo.py"
    tool.parent.mkdir()
    tool.write_text(LONG_SERVER, encoding="utf-8")
    monkeypatch.setattr(sb, "JXLPHOTO", str(tool))
    add_named(env, "only.jpg")
    assert backup(env, "--jpeg", "jxl") == 1
    line = next(line for line in latest_log(photobackup_log_dir).splitlines()
                if "CONVERT FAILED" in line and "only.jpg" in line)
    said = line.split("(JP-15): ", 1)[1]
    assert len(said) == sb._JXLPHOTO_SAID_CHARS and said.endswith("...")
    assert said.count("x") == sb._JXLPHOTO_SAID_CHARS - 3


# ---- counterparts of the tests pinned to one call per file ------------------------------------------------
def test_serve_process_env_is_its_own_folder(env, monkeypatch):
    """TEMP, TMP, TMPDIR and the log of a serve process are its own folder of
    the run's work folder (j27 and j44 for one call per file)."""
    envs = env.root / "envs"
    monkeypatch.setenv("FAKE_JXLPHOTO_ENVS", str(envs))
    assert backup(env, "--tiff", "jxl") == 0
    assert JXL <= backed(env.dest) and no_work_left(env)
    work_root = Path(sb.JXL_WORK_DIR, sb.JXL_WORK_FOLDER, str(os.getpid()))
    seen = env_calls(envs, "encode")
    assert len(seen) == 3
    for call in seen:
        temp = Path(call["TEMP"])
        assert temp.name == "temp" and temp.parent.parent == work_root
        assert temp.parent.name.startswith("s")
        assert [Path(call[name]) for name in ("TMPDIR", "TEMP", "TMP")] == [temp] * 3
        assert Path(call["JXLPHOTO_LOG_DIR"]) == temp.parent / "log"
        assert call["JXLPHOTO_SETTINGS"] == ""


def test_serve_converter_that_cannot_start_never_counts(env, monkeypatch):
    """m2 of the rc6 review for a serve process: an OSError at its start says
    nothing of the file."""
    real = sb._JxlServers.run

    def cannot_start_for_one(self, arguments, *rest, **kwargs):
        if arguments[0] == "encode" and "000037" in arguments[1]:
            raise OSError("the program could not be started")
        return real(self, arguments, *rest, **kwargs)
    monkeypatch.setattr(sb._JxlServers, "run", cannot_start_for_one)
    for _ in range(3):                                  # the others convert: proof
        assert backup(env, "--tiff", "jxl") == 1
        assert db(env.dest, "SELECT count(*) FROM not_converted") == [(0,)]
    assert "2025/scans/000037.tiff" in backed(env.dest)


# ---- the review before rc7 (AI_tools/261122_Claude_review_rc7.md) ---------------------------------------
def _serve_job(tmp_path):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    source = tmp_path / "a.jpg"
    source.write_bytes(b"\xff\xd8 not used")
    return ["transcode", str(source), str(out), "--mode", "0"]


def test_r1_a_process_whose_output_ends_never_hangs_its_start(tmp_path, monkeypatch):
    """Its stdout ends while it still runs (it waits for jobs): the start
    waits 5 s for it, then ends it -- never a wait without an end."""
    code = "import os, time; os.close(1); time.sleep(120)"
    monkeypatch.setattr(sb, "_jxlphoto_argv", lambda path, arguments: [sys.executable, "-c", code])
    servers = sb._JxlServers("unused.py", str(tmp_path / "serve"))
    started = time.monotonic()
    try:
        with pytest.raises(sb._ServeUnavailable):
            servers.run(_serve_job(tmp_path), 60)
        assert time.monotonic() - started < 40
        assert servers.unavailable and "ended before it said it serves" in servers.unavailable
        assert not sb._CONVERTERS
    finally:
        servers.close()


def test_r2_a_start_ended_by_ctrl_c_is_not_a_jxlphoto_that_cannot_serve(tmp_path, monkeypatch):
    code = "import time; time.sleep(2)"
    monkeypatch.setattr(sb, "_jxlphoto_argv", lambda path, arguments: [sys.executable, "-c", code])
    servers = sb._JxlServers("unused.py", str(tmp_path / "serve"))
    sb._STOPPING.clear()
    import threading
    timer = threading.Timer(0.5, sb._stop_converters)       # Ctrl+C comes while it starts
    timer.start()
    try:
        with pytest.raises(OSError, match="stopping"):
            servers.run(_serve_job(tmp_path), 60)
        assert servers.unavailable is None
    finally:
        timer.cancel()
        sb._STOPPING.clear()
        servers.close()
    assert not sb._CONVERTERS


def _work_root():
    return Path(sb.JXL_WORK_DIR, sb.JXL_WORK_FOLDER, str(os.getpid()))


def test_r3_an_ended_process_leaves_nothing_until_the_run_ends(env, monkeypatch):
    """Every 2 files a process ends (_SERVE_JOBS_MAX), and after each failed
    file: its folder, pipe and thread go at once, not at the run's end."""
    watch_serve(env, monkeypatch)
    monkeypatch.setattr(sb, "_SERVE_JOBS_MAX", 2)
    monkeypatch.setattr(sb, "JXL_WORKERS", 1)
    add_named(env, *[f"p{number}.jpg" for number in range(6)], "bad1.jpg", "bad2.jpg")
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", "exit")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "bad")
    seen, real_place = [], sb._place_group

    def place(*args, **kwargs):
        seen.append(sorted(path.name for path in _work_root().iterdir()
                           if path.is_dir() and path.name.startswith("s")))
        return real_place(*args, **kwargs)
    monkeypatch.setattr(sb, "_place_group", place)
    assert backup(env, "--jpeg", "jxl") == 1
    assert seen and all(len(folders) <= 1 for folders in seen), seen
    assert len(serve_pids(env)) >= 5 and not_alive(serve_pids(env))
    assert no_work_left(env)


def test_r4_after_serve_is_given_up_no_process_waits_idle(env, monkeypatch, photobackup_log_dir):
    """One process cannot start: the run goes on with one call per file, and
    the processes other workers had are ended at their next file, not left
    idle until the run's end."""
    monkeypatch.setattr(sb, "JXL_WORKERS", 2)
    add_named(env, *[f"p{number}.jpg" for number in range(12)])
    real_start, starts = sb._JxlServer.start, []

    def start(self):
        starts.append(self.folder)
        if len(starts) == 2:
            raise sb._ServeUnavailable("the second process was told not to serve")
        return real_start(self)
    left = []
    real_close = sb._JxlServers.close

    def close(self):
        left.extend(server for server in self.started if server.alive())
        return real_close(self)
    monkeypatch.setattr(sb._JxlServer, "start", start)
    monkeypatch.setattr(sb._JxlServers, "close", close)
    assert backup(env, "--jpeg", "jxl") == 0
    assert len(db(env.dest, "SELECT key FROM converted")) == 13
    assert latest_log(photobackup_log_dir).count("JXL-PHOTO SERVE NOT USED") == 1
    assert left == []


def test_r5_the_restore_never_removes_a_file_it_did_not_make(env, monkeypatch, photobackup_log_dir):
    """A file appears at the restore's own file name after its check (another
    program): ERROR, and that file stays; the JPEG's target is not made."""
    add_jpegs(env)
    assert backup(env, "--jpeg", "jxl") == 0
    real, planted = sb._rebuild_jpeg, []

    def rebuild(jxl, jxlphoto, folder, *args, **kwargs):
        jpeg = real(jxl, jxlphoto, folder, *args, **kwargs)
        Path(folder).write_bytes(b"someone else's file")
        planted.append(Path(folder))
        return jpeg
    monkeypatch.setattr(sb, "_rebuild_jpeg", rebuild)
    assert restore(env, "--to", env.out) == 1
    assert planted
    for path in planted:
        assert path.read_bytes() == b"someone else's file"
    statuses = restore_statuses(photobackup_log_dir)
    assert {statuses[rel] for rel in ALL_JPEGS} == {"ERROR"}
    assert not restored(env) & ALL_JPEGS
