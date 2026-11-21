"""The progress a person sees on a long run (v0.14.0-rc4, PENDENCIAS of 16/11): the speed of the
last minutes beside the whole run's, each with its ETA (both since rc6), and lines while a plan
reads the sources -- on a console only."""
import selective_backup as sb

from conftest import argv_for, build_base_tree


def test_both_paces_show_their_own_eta(monkeypatch):
    """The JPEG conversion of 21/11: one ETA said 16 h, then 3 h, and the run took 22 h. The
    whole run's pace and the recent one, each with its ETA, show when the pace is changing."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(sb.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(sb, "PROGRESS_RECENT_S", 120.0)
    speed = sb._Speed()
    done = 0
    for second in range(10, 230, 10):           # 200 MB/s for 100 s, then 50 MB/s
        done += (200 if second <= 100 else 50) * 10 * 1_000_000
        clock["now"] = 1000.0 + second
        text = speed.text(done, 300_000 * 1_000_000)
    # 26,000 MB in 220 s (118 MB/s), the last 120 s at 50 MB/s; 274,000 MB left:
    # 2,318 s at the whole run's pace, 5,480 s at the recent one
    assert text == "whole run 118 MB/s (ETA 0h38m), last 2 min 50 MB/s (ETA 1h31m)"


def test_a_short_run_shows_the_whole_run_only(monkeypatch):
    clock = {"now": 50.0}
    monkeypatch.setattr(sb.time, "monotonic", lambda: clock["now"])
    speed = sb._Speed()
    clock["now"] = 60.0
    assert speed.text(100 * 1_000_000, 2_000 * 1_000_000) == "whole run 10 MB/s (ETA 0h03m)"


def test_nothing_done_says_eta_unknown(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(sb.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(sb, "PROGRESS_RECENT_S", 60.0)
    speed = sb._Speed()
    clock["now"] = 5.0
    assert speed.text(0, 1_000 * 1_000_000) == "whole run 0 MB/s (ETA ?)"
    clock["now"] = 30.0
    speed.text(600 * 1_000_000, 1_000 * 1_000_000)
    clock["now"] = 200.0                # a big TIFF: nothing finished for over a minute
    assert speed.text(600 * 1_000_000, 1_000 * 1_000_000) == (
        "whole run 3 MB/s (ETA 0h02m), last 1 min 0 MB/s (ETA ?)")


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
