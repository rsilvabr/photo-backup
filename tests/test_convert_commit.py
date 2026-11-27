"""The catalog cadence of the conversions (v0.14.0-rc10, plan 261127 resume, D11):
a placed JXL group gets its row on the disk within CONVERT_COMMIT_S seconds, so a
hard stop (taskkill /F, a power cut) makes almost nothing again. The hard kill of
26-27/11 lost the rows of 45 groups with the every-50-files cadence: the next run
set their JXL aside and converted those TIFFs again.

Uses only what HEAD before rc10 has (the pre-fix proof runs this file there)."""
import sqlite3
from pathlib import Path

import pytest

import selective_backup as sb

from conftest import latest_log
from test_tiff_jxl import backup, closed_console, converted, env  # noqa: F401


def committed_groups(dest):
    """The JXL groups whose rows another connection sees: what is on the disk."""
    path = Path(dest, sb.CATALOG_FOLDER, "catalog.sqlite")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        return connection.execute("SELECT COUNT(DISTINCT source_key) FROM converted").fetchone()[0]
    finally:
        connection.close()


def counting(monkeypatch, env):
    """Before each group is put in place: how many groups are committed."""
    real = sb._place_group
    seen = []

    def place(*args, **kwargs):
        seen.append(committed_groups(env.dest))
        return real(*args, **kwargs)
    monkeypatch.setattr(sb, "_place_group", place)
    return seen


def test_c1_each_placed_group_is_on_the_disk_before_the_next_one(env, monkeypatch):
    monkeypatch.setattr(sb, "CONVERT_COMMIT_S", 0, raising=False)
    monkeypatch.setattr(sb, "FLUSH_BATCH_FILES", 50)
    seen = counting(monkeypatch, env)
    assert backup(env, "--tiff", "jxl") == 0
    assert seen == [0, 1, 2]                    # HEAD before rc10: [0, 0, 0]
    assert len({source for source, *_rest in converted(env.dest).values()}) == 3


def test_c2_the_count_rule_still_writes_every_flush_batch(env, monkeypatch):
    monkeypatch.setattr(sb, "CONVERT_COMMIT_S", 3600, raising=False)
    monkeypatch.setattr(sb, "FLUSH_BATCH_FILES", 2)
    seen = counting(monkeypatch, env)
    assert backup(env, "--tiff", "jxl") == 0
    assert seen == [0, 0, 2]


def test_c3_the_timing_line_says_how_long_the_catalog_took(env, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    lines = [line for line in latest_log(photobackup_log_dir).splitlines()
             if "TIMING conversions:" in line]
    assert len(lines) == 1
    assert "the catalog written" in lines[0]


@pytest.mark.parametrize("value", [-1, float("nan"), "2", True])
def test_c4_convert_commit_s_must_be_a_number(env, monkeypatch, capsys, value):
    monkeypatch.setattr(sb, "CONVERT_COMMIT_S", value, raising=False)
    assert backup(env, "--tiff", "jxl") == 2
    assert "CONVERT_COMMIT_S" in capsys.readouterr().err
    assert not env.dest.exists()
