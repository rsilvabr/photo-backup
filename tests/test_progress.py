"""The progress a person sees on a long run (v0.14.0-rc4, PENDENCIAS of 16/11): the speed of the
last minutes beside the average, the ETA from the recent one, and lines while a plan reads the
sources -- on a console only."""
import selective_backup as sb

from conftest import argv_for, build_base_tree


def test_the_recent_speed_and_the_eta_follow_a_slower_disk(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(sb.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(sb, "PROGRESS_RECENT_S", 120.0)
    speed = sb._Speed()
    done = 0
    for second in range(10, 230, 10):           # 200 MB/s for 100 s, then 50 MB/s
        done += (200 if second <= 100 else 50) * 10 * 1_000_000
        clock["now"] = 1000.0 + second
        text = speed.text(done, 30_000 * 1_000_000)
    # 26,000 MB in 220 s; the last 120 s at 50 MB/s; 4,000 MB left at 50 MB/s
    assert text == "118 MB/s, last 2 min 50 MB/s  ETA 0h01m"


def test_a_short_run_shows_the_average_only(monkeypatch):
    clock = {"now": 50.0}
    monkeypatch.setattr(sb.time, "monotonic", lambda: clock["now"])
    speed = sb._Speed()
    clock["now"] = 60.0
    assert speed.text(100 * 1_000_000, 200 * 1_000_000) == "10 MB/s  ETA 0h00m"


def test_reading_the_sources_shows_lines_on_a_console_only(tmp_path, monkeypatch, capsys):
    root = build_base_tree(tmp_path / "SRC")
    args = sb._build_parser().parse_args(argv_for(root / "G" / "2025", tmp_path / "dest", root))
    sb.plan(args)                               # a test is no console: nothing said
    assert capsys.readouterr().err == ""
    monkeypatch.setattr(sb, "_stderr_is_console", lambda: True)
    monkeypatch.setattr(sb, "PROGRESS_EVERY_S", 0.0)
    sb.plan(args)
    lines = capsys.readouterr().err.splitlines()
    assert lines and all(line.startswith("  read ") for line in lines)
    assert "folders" in lines[-1] and "files" in lines[-1]
    sb._READING.reset()
    sb._READING.line(2, 19, r"G:\2010")
    assert capsys.readouterr().err == "Reading [2/19] G:\\2010\n"
