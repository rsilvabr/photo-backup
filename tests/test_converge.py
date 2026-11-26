"""The backup converges to the state the manifest asks for (261126, topic
`converge`): A, a JXL group the manifest no longer wants leaves once its plain
copy is in place; B, --reconvert-older makes again the TIFF JXL of another
jxl-photo; C, --adopt-by-hash adopts a backup file made by another program
only with proof of its bytes. jxl-photo is played by tests/fake_jxlphoto.py."""
import csv
import io
import os
import re
import shutil
import stat
import sqlite3
from pathlib import Path

import pytest

import manifest_backup as mb
import mirror_backup as mir
import photo_backup as menu
import selective_backup as sb

from conftest import latest_csv, latest_log, row_count, snapshot
from test_jpeg_jxl import calls
from test_menu import menu_config, script  # noqa: F401
from test_tiff_jxl import (JXL, TIFFS, VERSIONS, backed, backup, closed_console,  # noqa: F401
                           converted, db, env, mirror_rel, newest_csv, under)

EXPORT_JPG = "2025/shoot/_EXPORT/DSC_0001_v1.jpg"
NOTES = "2025/scans/notes.txt"
DAY_NS = 86_400 * 1_000_000_000


def manifest(env, name, jpeg="", tiff=""):
    path = env.root / name
    path.write_text("Folder,Mode,Originals,Mirror,Tiff,Jpeg\n"
                    f'"{env.src / "2025"}",6,,,{tiff},{jpeg}\n', encoding="utf-8")
    return path


def shift(path, nanoseconds):
    info = Path(path).stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + nanoseconds))


def hand_copy(env, rel=NOTES):
    """The file as another program (FreeFileSync, by hand) puts it in the backup."""
    source, target = env.src / rel, env.dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return source, target


def key(rel):
    return os.path.join(*rel.split("/")).casefold()


def file_row(env, rel):
    return db(env.dest, "SELECT sha256, size, mtime_ns FROM files WHERE key = '%s'" % key(rel))


def log_lines(log_dir, prefix):
    return [line for line in latest_log(log_dir).splitlines() if line.startswith(prefix)]


# ---- A the manifest is the state: a JXL group it no longer wants leaves -----------------------
def test_a1_a_jpeg_kept_as_jxl_comes_back_as_the_file(env, capsys, photobackup_log_dir):
    before = snapshot(env.src)
    assert backup(env, "--jpeg", "jxl") == 0
    assert EXPORT_JPG + ".jxl" in backed(env.dest) and EXPORT_JPG not in backed(env.dest)
    capsys.readouterr()
    assert backup(env, "--jpeg", "copy") == 0
    out = capsys.readouterr().out
    assert snapshot(env.src) == before
    assert (env.dest / EXPORT_JPG).read_bytes() == (env.src / EXPORT_JPG).read_bytes()
    assert EXPORT_JPG + ".jxl" not in backed(env.dest)
    assert EXPORT_JPG + ".jxl" in under(env.dest, VERSIONS)
    assert db(env.dest, "SELECT count(*) FROM converted") == [(0,)]
    assert db(env.dest, "SELECT count(*) FROM files WHERE key LIKE '%.jxl'") == [(0,)]
    assert file_row(env, EXPORT_JPG)
    assert row_count(out, "JXL replaced by their plain copy (to versions\\)") == "1"
    assert "1 JXL in the backup replaced by their plain copy (to versions)" in out
    assert log_lines(photobackup_log_dir, "UNDONE ")
    assert backup(env, "--jpeg", "copy") == 0           # a third run: nothing left to do
    assert not log_lines(photobackup_log_dir, "UNDONE ")


def test_a2_a_two_page_tiff_group_leaves_with_both_pages(env, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    assert JXL <= backed(env.dest)
    assert backup(env) == 0                             # --tiff copy
    assert TIFFS <= backed(env.dest) and not {rel for rel in backed(env.dest) if rel.endswith(".jxl")}
    assert JXL <= under(env.dest, VERSIONS)             # both pages of 000038 too
    assert db(env.dest, "SELECT count(*) FROM converted") == [(0,)]
    assert db(env.dest, "SELECT count(*) FROM files WHERE key LIKE '%.jxl'") == [(0,)]
    for rel in TIFFS:
        assert (env.dest / rel).read_bytes() == (env.src / rel).read_bytes()


def test_a3_a_failed_plain_copy_keeps_the_group(env, monkeypatch, photobackup_log_dir):
    assert backup(env, "--jpeg", "jxl") == 0
    jxl_row = db(env.dest, "SELECT key, size FROM files WHERE key LIKE '%.jxl'")
    real = sb._finish_temp

    def failing(planned, *args, **kwargs):
        if planned.rel.endswith("DSC_0001_v1.jpg"):
            raise OSError("the destination stopped taking writes")
        return real(planned, *args, **kwargs)
    monkeypatch.setattr(sb, "_finish_temp", failing)
    assert backup(env, "--jpeg", "copy") == 1
    assert EXPORT_JPG + ".jxl" in backed(env.dest) and EXPORT_JPG not in backed(env.dest)
    assert db(env.dest, "SELECT key, size FROM files WHERE key LIKE '%.jxl'") == jxl_row
    assert db(env.dest, "SELECT count(*) FROM converted") == [(1,)]
    assert not log_lines(photobackup_log_dir, "UNDONE ")


def test_a4_a_changed_plain_copy_that_is_skipped_keeps_the_group(env, photobackup_log_dir):
    assert backup(env, "--jpeg", "jxl") == 0
    old = env.dest / EXPORT_JPG                         # a copy of another program, other bytes
    old.write_bytes(b"old bytes")
    assert backup(env, "--jpeg", "copy") == 0           # CHANGED, no --update-changed: skipped
    assert old.read_bytes() == b"old bytes"
    assert EXPORT_JPG + ".jxl" in backed(env.dest)
    assert db(env.dest, "SELECT count(*) FROM converted") == [(1,)]
    assert not log_lines(photobackup_log_dir, "UNDONE ")
    assert backup(env, "--jpeg", "copy", "--update-changed") == 0   # now the copy is made
    assert (env.dest / EXPORT_JPG).read_bytes() == (env.src / EXPORT_JPG).read_bytes()
    assert EXPORT_JPG + ".jxl" not in backed(env.dest)
    assert {EXPORT_JPG, EXPORT_JPG + ".jxl"} <= under(env.dest, VERSIONS)


def test_a5_the_mirror_trashes_the_group_it_replaces(env, monkeypatch, capsys, photobackup_log_dir):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    jxl, copy = manifest(env, "jxl.csv", jpeg="jxl"), manifest(env, "copy.csv", jpeg="copy")
    assert mir.main(["--manifest", str(jxl), "--dest", str(env.dest), "--yes"]) == 0
    capsys.readouterr()
    assert mir.main(["--manifest", str(copy), "--dest", str(env.dest), "--yes"]) == 0
    out = capsys.readouterr().out
    assert "JXL replaced by their plain copy (to the mirror's trash)" in out
    assert "JXL replaced by the file (to the trash)" in out
    rel = mirror_rel(env, EXPORT_JPG)
    assert rel in backed(env.dest) and rel + ".jxl" not in backed(env.dest)
    # ours: the trash of old versions (261126, keep-days)
    assert rel + ".jxl" in under(env.dest, mir.MIRROR_TRASH_VERSIONS_FOLDER)
    assert db(env.dest, "SELECT count(*) FROM converted") == [(0,)]
    rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_actions.csv"))))
    retired = [row for row in rows if row["action"] == "retire"]
    assert [row["rel"].replace("\\", "/") for row in retired] == [rel + ".jxl"]
    assert retired[0]["result"] == "to the trash (replaced by its plain copy)"


def test_a5_the_mirror_counts_it_in_the_limit_of_its_line(env, monkeypatch, capsys,
                                                          photobackup_log_dir):
    jxl, copy = manifest(env, "jxl.csv", jpeg="jxl"), manifest(env, "copy.csv", jpeg="copy")
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    assert mir.main(["--manifest", str(jxl), "--dest", str(env.dest), "--yes"]) == 0
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 10)
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_MIN", 1)
    capsys.readouterr()
    before = snapshot(env.dest)
    assert mir.main(["--manifest", str(copy), "--dest", str(env.dest), "--unattended"]) == 2
    assert "CHECK: these lines would remove or replace more than" in capsys.readouterr().out
    assert snapshot(env.dest) == before                 # stopped, nothing changed


def test_a6_the_groups_keys_are_never_orphans(env, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    assert backup(env, "--dry-run") == 0                # --tiff copy: the plain files are new
    rows = newest_csv(photobackup_log_dir, "orphans")
    assert not [row for row in rows if row["rel"].endswith(".jxl")], rows
    plan = newest_csv(photobackup_log_dir, "plan")
    assert {row["action"] for row in plan if row["rel"].replace("\\", "/") in TIFFS} == {"NEW"}


def test_a7_no_convert_does_not_undo(env):
    assert backup(env, "--jpeg", "jxl") == 0
    assert backup(env, "--jpeg", "jxl", "--no-convert") == 0      # planned as JXL: A is not asked
    assert EXPORT_JPG + ".jxl" in backed(env.dest)


# ---- B --reconvert-older ---------------------------------------------------------------------------
def new_jxl_photo(monkeypatch, version="9.9.10"):
    monkeypatch.setenv("FAKE_JXLPHOTO_VERSION", version)
    sb._JXLPHOTO_VERSIONS.clear()


def tools(env):
    return {tool for (tool,) in db(env.dest, "SELECT tool FROM converted")}


def test_b1_the_tiff_jxl_of_another_jxl_photo_are_made_again(env, monkeypatch, capsys,
                                                             photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    assert tools(env) == {"jxl-photo 9.9.9"}
    made = len(calls(env, command="encode"))
    new_jxl_photo(monkeypatch)
    assert backup(env, "--tiff", "jxl") == 0            # without the flag: nothing changes
    assert len(calls(env, command="encode")) == made and tools(env) == {"jxl-photo 9.9.9"}
    capsys.readouterr()
    before = snapshot(env.src)
    assert backup(env, "--tiff", "jxl", "--reconvert-older") == 0
    out = capsys.readouterr().out
    assert snapshot(env.src) == before
    assert tools(env) == {"jxl-photo 9.9.10"} and len(converted(env.dest)) == 5
    assert JXL <= backed(env.dest) and JXL <= under(env.dest, VERSIONS)      # the old ones kept
    assert len(calls(env, command="encode")) == made + 3
    assert row_count(out, "TIFF kept as JXL: made again (--reconvert-older)") == "3"
    assert ("RECONVERT 3 TIFF made by an older jxl-photo than jxl-photo 9.9.10 (versions "
            "found: jxl-photo 9.9.9)") in latest_log(photobackup_log_dir)
    assert backup(env, "--tiff", "jxl", "--reconvert-older") == 0           # same jxl-photo now
    assert len(calls(env, command="encode")) == made + 3


def test_b1_a_failed_new_conversion_keeps_the_old_jxl(env, monkeypatch, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    new_jxl_photo(monkeypatch)
    monkeypatch.setenv("FAKE_JXLPHOTO_FAIL", "exit")
    monkeypatch.setenv("FAKE_JXLPHOTO_ONLY", "000037")
    assert backup(env, "--tiff", "jxl", "--reconvert-older") == 1
    assert "2025/scans/000037.tiff.jxl" in backed(env.dest)
    assert "2025/scans/000037.tiff" not in backed(env.dest)     # never copied as it is instead
    assert converted(env.dest)["2025/scans/000037.tiff.jxl"][3] == "jxl-photo 9.9.9"
    assert "RECONVERT FAILED" in latest_log(photobackup_log_dir)


def test_b2_a_jpeg_jxl_is_never_made_again(env, monkeypatch):
    assert backup(env, "--jpeg", "jxl") == 0
    made = len(calls(env, command="transcode"))
    new_jxl_photo(monkeypatch)
    assert backup(env, "--jpeg", "jxl", "--reconvert-older") == 0
    assert len(calls(env, command="transcode")) == made
    assert tools(env) == {"jxl-photo 9.9.9"}


def test_b3_reconvert_older_with_no_convert_is_a_usage_error(env, capsys):
    assert backup(env, "--tiff", "jxl", "--reconvert-older", "--no-convert") == 2
    assert "--reconvert-older cannot be combined with --no-convert" in capsys.readouterr().err
    assert not env.dest.exists()
    manifest_path = manifest(env, "m.csv", tiff="jxl")
    assert mb.main(["--manifest", str(manifest_path), "--dest", str(env.dest), "--yes",
                    "--reconvert-older", "--no-convert"]) == 2
    assert mir.main(["--manifest", str(manifest_path), "--dest", str(env.dest), "--yes",
                     "--reconvert-older", "--no-convert"]) == 2
    assert not env.dest.exists()


def test_b4_a_jxl_photo_that_is_refused_changes_nothing(env, monkeypatch, capsys):
    assert backup(env, "--tiff", "jxl") == 0
    new_jxl_photo(monkeypatch, "2.0.0")                 # older than JXLPHOTO_MIN_VERSION
    before = snapshot(env.dest)
    capsys.readouterr()
    assert backup(env, "--tiff", "jxl", "--reconvert-older") == 2
    assert "jxl-photo 2.0.0 is older than" in capsys.readouterr().err   # the usage check's own
    assert snapshot(env.dest) == before
    # and the flag's own guard, should that check let it through
    monkeypatch.setattr(sb, "_validate_usage", lambda args: [])
    assert backup(env, "--tiff", "jxl", "--reconvert-older") == 2
    assert "--reconvert-older: jxl-photo cannot be used" in capsys.readouterr().err
    assert snapshot(env.dest) == before
    assert tools(env) == {"jxl-photo 9.9.9"}


def test_b4_the_options_reach_the_backend_from_the_manifest_and_the_mirror(tmp_path):
    (tmp_path / "src").mkdir()
    path = tmp_path / "m.csv"
    path.write_text("Folder,Mode,Originals,Mirror,Tiff,Jpeg\n"
                    f'"{tmp_path / "src"}",6,,,,\n', encoding="utf-8")
    rows = mb.read_manifest(str(path), mb.read_modes(os.path.join(str(Path(mb.__file__).parent),
                                                                  mb.MODES_FILE)))
    parsed = mb.build_parser().parse_args(["--manifest", "m.csv", "--dest", str(tmp_path),
                                           "--reconvert-older", "--adopt-by-hash"])
    backend = mb._backend_args(parsed, rows, str(tmp_path))
    assert backend.reconvert_older is True and backend.adopt_by_hash is True
    parsed = mb.build_parser().parse_args(["--manifest", "m.csv", "--dest", str(tmp_path)])
    backend = mb._backend_args(parsed, rows, str(tmp_path))
    assert backend.reconvert_older is False and backend.adopt_by_hash is sb.ADOPT_BY_HASH
    mirror = mir.build_parser().parse_args(["--source", "a", "--dest", str(tmp_path),
                                            "--reconvert-older", "--no-adopt-by-hash"])
    backend = mir._plain_backend_args(mirror, [])
    assert backend.reconvert_older is True and backend.adopt_by_hash is False
    mirror = mir.build_parser().parse_args(["--source", "a", "--dest", str(tmp_path)])
    assert mir._plain_backend_args(mirror, []).reconvert_older is False


def test_b5_the_menu_marker_asks_one_backup_to_make_them_again(monkeypatch, menu_config):
    # answers: jxl-photo? no; the work folder? no; the conversion off? no; try the failed
    # conversions again? no; make the TIFF JXL again? yes; forget the choices? no
    script(monkeypatch, ["n", "n", "n", "n", "y", "n", "n"])
    menu.settings_menu(menu.load_config())
    assert os.path.isfile(menu._reconvert_marker()) and not os.path.isfile(menu._retry_marker())
    commands = []

    class Done:
        returncode = 0
    monkeypatch.setattr(menu.subprocess, "run", lambda command, **kwargs: commands.append(
        command) or Done())
    monkeypatch.setattr(menu, "_menu_title", lambda: None)
    menu.run_backend(["--source", "S", "--dry-run"])            # the dry-run sees it too
    menu.run_restore(["--backup", "B"])                         # never the restore
    menu.run_manifest(["--manifest", "m.csv", "--verify", "quick"])     # never a verify
    assert os.path.isfile(menu._reconvert_marker())
    menu.run_manifest(["--manifest", "m.csv", "--yes"])         # the run: then done
    menu.run_backend(["--source", "S", "--yes"])
    tails = [command[2:] for command in commands]
    assert [tail.count("--reconvert-older") for tail in tails] == [1, 0, 0, 1, 0]
    assert not os.path.exists(menu._reconvert_marker())


# ---- C --adopt-by-hash -----------------------------------------------------------------------------
def test_c1_a_file_copied_by_hand_is_adopted_with_its_hash(env, photobackup_log_dir):
    source, target = hand_copy(env)
    before = snapshot(env.src)
    assert backup(env, "--adopt-by-hash") == 0
    assert snapshot(env.src) == before
    sha, size, _mtime = file_row(env, NOTES)[0]
    assert sha == sb._sha256_file(str(source)) and size == source.stat().st_size
    assert f"ADOPT BY HASH {os.path.join(*NOTES.split('/'))}" in latest_log(photobackup_log_dir)
    assert f"COPY {os.path.join(*NOTES.split('/'))}" not in latest_log(photobackup_log_dir)
    assert target.read_bytes() == source.read_bytes()


def test_c1_without_the_option_the_row_has_no_hash(env):
    hand_copy(env)
    assert backup(env) == 0
    assert file_row(env, NOTES)[0][0] is None           # adopted by size and date, as before


def test_c2_the_same_bytes_with_another_date_are_adopted_and_dated(env, photobackup_log_dir):
    source, target = hand_copy(env)
    shift(target, DAY_NS)
    assert target.stat().st_mtime_ns != source.stat().st_mtime_ns
    assert backup(env) == 0                             # without: CHANGED, skipped, as today
    assert target.stat().st_mtime_ns == source.stat().st_mtime_ns + DAY_NS
    assert "SKIP CHANGED " + os.path.join(*NOTES.split("/")) in latest_log(photobackup_log_dir)
    assert not file_row(env, NOTES)
    before = snapshot(env.src)
    assert backup(env, "--adopt-by-hash") == 0
    assert snapshot(env.src) == before
    assert target.stat().st_mtime_ns == source.stat().st_mtime_ns       # its date is the source's
    assert target.read_bytes() == source.read_bytes()
    assert file_row(env, NOTES)[0][0] == sb._sha256_file(str(source))
    assert not under(env.dest, VERSIONS)                # nothing moved, nothing copied
    log = latest_log(photobackup_log_dir)
    assert "(date set to the source's)" in log and "COPY " + os.path.join(*NOTES.split("/")) not in log
    assert backup(env, "--adopt-by-hash") == 0          # a second run: the row is there
    assert "ADOPT BY HASH" not in latest_log(photobackup_log_dir)


def test_c3_the_same_size_and_date_with_other_bytes_is_changed(env, photobackup_log_dir, capsys):
    source, target = hand_copy(env)
    info = target.stat()
    target.write_bytes(b"x" * info.st_size)             # other bytes, same size
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert backup(env, "--adopt-by-hash") == 0
    out = capsys.readouterr().out
    assert "ADOPT REFUSED " + os.path.join(*NOTES.split("/")) in latest_log(photobackup_log_dir)
    assert row_count(out, "same size and date as the source, other bytes") == "1"
    assert target.read_bytes() == b"x" * info.st_size and not file_row(env, NOTES)
    assert backup(env, "--adopt-by-hash", "--update-changed") == 0
    assert target.read_bytes() == source.read_bytes()
    assert NOTES in under(env.dest, VERSIONS)           # the old file kept
    assert file_row(env, NOTES)


def test_c4_a_dry_run_reads_and_writes_nothing(env, capsys):
    source, target = hand_copy(env)
    shift(target, DAY_NS)
    before_dest, before_src = snapshot(env.dest), snapshot(env.src)
    assert backup(env, "--adopt-by-hash", "--dry-run") == 0
    out = capsys.readouterr().out
    assert snapshot(env.dest) == before_dest and snapshot(env.src) == before_src
    assert not (env.dest / sb.CATALOG_FOLDER).exists()  # no catalog written
    assert row_count(out, "adopted by their bytes (no row; same as the source)") == "1"
    assert "1 of them get the source's date" in out


@pytest.mark.skipif(os.name != "nt", reason="the read-only attribute is a Windows one here")
def test_c5_a_read_only_file_is_never_made_writable(env, photobackup_log_dir):
    source, target = hand_copy(env)
    shift(target, DAY_NS)
    target.chmod(stat.S_IREAD)
    mtime = target.stat().st_mtime_ns
    try:
        # decided while planning (review 261126, S5): not adopted, left as it
        # is, and no error
        assert backup(env, "--adopt-by-hash") == 0
        assert "ADOPT SKIPPED " + os.path.join(*NOTES.split("/")) + ": read-only" in \
            latest_log(photobackup_log_dir)
        assert target.stat().st_mtime_ns == mtime
        assert not target.stat().st_mode & stat.S_IWRITE        # still read-only
        assert not file_row(env, NOTES)
    finally:
        target.chmod(stat.S_IWRITE | stat.S_IREAD)


def test_c6_a_backup_file_that_changes_while_it_is_read_is_left(env, monkeypatch,
                                                                 photobackup_log_dir):
    source, target = hand_copy(env)
    shift(target, DAY_NS)
    real = sb._sha256_file

    def touching(path, *rest):
        digest = real(path, *rest)
        if os.path.normcase(str(path)) == os.path.normcase(str(target)):
            shift(target, 5 * DAY_NS)                   # the other program writes meanwhile
        return digest
    monkeypatch.setattr(sb, "_sha256_file", touching)
    assert backup(env, "--adopt-by-hash") == 0
    assert "the backup file changed while it was read" in latest_log(photobackup_log_dir)
    assert not file_row(env, NOTES)
    assert target.stat().st_mtime_ns == source.stat().st_mtime_ns + 6 * DAY_NS


def test_c6_a_file_that_changes_between_the_plan_and_the_write_gets_no_row(
        env, monkeypatch, photobackup_log_dir):
    source, target = hand_copy(env)
    shift(target, DAY_NS)
    real = sb._apply_hash_adoptions

    def late(statuses, dest, catalog, log):
        shift(target, 3 * DAY_NS)                       # after the hashes, before the write
        return real(statuses, dest, catalog, log)
    monkeypatch.setattr(sb, "_apply_hash_adoptions", late)
    assert backup(env, "--adopt-by-hash") == 1
    assert "ADOPT SKIPPED " + os.path.join(*NOTES.split("/")) + ": changed since it was read" \
        in latest_log(photobackup_log_dir)
    assert not file_row(env, NOTES)
    assert target.stat().st_mtime_ns == source.stat().st_mtime_ns + 4 * DAY_NS    # untouched


def test_c7_a_file_with_a_row_is_never_hashed(env, monkeypatch):
    assert backup(env) == 0                             # every file has its row
    hand_copy_target = env.dest / NOTES
    assert file_row(env, NOTES)
    hashed = []
    real = sb._sha256_file
    monkeypatch.setattr(sb, "_sha256_file", lambda path, *rest: hashed.append(str(path))
                        or real(path, *rest))
    assert backup(env, "--adopt-by-hash") == 0
    assert hashed == []
    hand_copy_target.unlink()                           # now one file without a row
    db_path = env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"
    connection = sqlite3.connect(str(db_path))
    connection.execute("DELETE FROM files WHERE key = ?", (key(NOTES),))
    connection.commit()
    connection.close()
    hand_copy(env)
    assert backup(env, "--adopt-by-hash") == 0
    assert sorted(os.path.normcase(path) for path in hashed) == sorted(
        os.path.normcase(str(path)) for path in (env.src / NOTES, env.dest / NOTES))


def test_c8_the_setting_is_the_default(monkeypatch):
    base = ["--source", "a", "--dest", "b"]
    assert sb._build_parser().parse_args(base).adopt_by_hash is False
    monkeypatch.setattr(sb, "ADOPT_BY_HASH", True)
    assert sb._build_parser().parse_args(base).adopt_by_hash is True
    assert sb._build_parser().parse_args(base + ["--no-adopt-by-hash"]).adopt_by_hash is False
    assert mb.build_parser().parse_args(["--manifest", "m"]).adopt_by_hash is True
    assert mir.build_parser().parse_args(["--source", "a", "--dest", "b"]).adopt_by_hash is True


# ---- The fixes after the review (AI_tools/261126_Opus_review_converge.md) ------------------------
def fail_new_page_rename(monkeypatch, name):
    """The new page's rename to its final name fails (an antivirus or the
    indexer holds the fresh file: WinError 32); the old group's move to
    versions\\ (or the trash) still works."""
    real = sb._rename_no_clobber

    def failing(src, dst):
        parts = os.path.normcase(dst).split(os.sep)
        # the stores of 261126 (keep-days): versions, the mirror's trash folders
        if dst.endswith(name) and not {"versions", *mir._trash_folders()} & set(parts):
            raise PermissionError(32, "The process cannot access the file because it is "
                                      "being used by another process", dst)
        return real(src, dst)
    monkeypatch.setattr(sb, "_rename_no_clobber", failing)


def test_m1_reconvert_failure_after_the_old_group_left(env, monkeypatch, capsys,
                                                       photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    new_jxl_photo(monkeypatch)
    real_rename = sb._rename_no_clobber
    fail_new_page_rename(monkeypatch, "000037.tiff.jxl")
    capsys.readouterr()
    before = snapshot(env.src)
    assert backup(env, "--tiff", "jxl", "--reconvert-older") == 1
    out = capsys.readouterr().out
    assert snapshot(env.src) == before
    there = backed(env.dest)
    # the old JXL left before the failure: the TIFF is copied as it is instead
    assert "2025/scans/000037.tiff" in there
    assert (env.dest / "2025/scans/000037.tiff").read_bytes() == \
        (env.src / "2025/scans/000037.tiff").read_bytes()
    assert file_row(env, "2025/scans/000037.tiff")
    log = latest_log(photobackup_log_dir)
    assert "RECONVERT FAILED 2025" in log and "the old JXL had already left" in log
    assert "jxl-photo stays" not in log
    assert "1 TIFF could not be made again after their old JXL had left: copied as it is" in out
    assert "their JXL stays as it was" not in out
    # the next ordinary run (the file no longer held) converts it again and
    # retires that copy
    monkeypatch.setattr(sb, "_rename_no_clobber", real_rename)
    assert backup(env, "--tiff", "jxl") == 0
    assert "2025/scans/000037.tiff.jxl" in backed(env.dest)
    assert "2025/scans/000037.tiff" not in backed(env.dest)


def type_what_is_asked(monkeypatch):
    """A person who types the number each typed confirmation asks for."""
    def answer(prompt=""):
        found = re.search(r"type ([\d,]+) and press Enter", prompt)
        if found is None:
            raise EOFError
        return found.group(1)
    monkeypatch.setattr("builtins.input", answer)


def test_m1_the_mirror_without_trash_keeps_a_copy(env, monkeypatch, photobackup_log_dir):
    from test_tiff_jxl import write_manifest
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    # the mirror's "no trash" for the old JXL (an old version: 261126, keep-days);
    # deleting at once needs the typed number
    monkeypatch.setattr(mir, "MIRROR_VERSIONS_KEEP_DAYS", 0)
    man = write_manifest(env.root / "jxl.csv", (env.src / "2025", "6", "", "", "jxl"))
    assert mir.main(["--manifest", str(man), "--dest", str(env.dest), "--yes"]) == 0
    new_jxl_photo(monkeypatch)
    fail_new_page_rename(monkeypatch, "000037.tiff.jxl")
    type_what_is_asked(monkeypatch)
    assert mir.main(["--manifest", str(man), "--dest", str(env.dest), "--yes",
                     "--reconvert-older"]) == 1
    assert not under(env.dest, mir.MIRROR_TRASH_VERSIONS_FOLDER)     # deleted, not kept
    rel = mirror_rel(env, "2025/scans/000037.tiff")
    assert rel in backed(env.dest)                       # deleted the old JXL, so copied it
    assert (env.dest / rel).read_bytes() == (env.src / "2025/scans/000037.tiff").read_bytes()


def test_m1_a_half_retired_multipage_group_gets_a_copy(env, monkeypatch, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    new_jxl_photo(monkeypatch)
    real = sb._rename_no_clobber

    def failing(src, dst):
        # the old group's SECOND page cannot be moved to versions (held open)
        if dst.endswith("000038_page1.tif.jxl") and "versions" in os.path.normcase(dst):
            raise PermissionError(32, "being used by another process", dst)
        return real(src, dst)
    monkeypatch.setattr(sb, "_rename_no_clobber", failing)
    assert backup(env, "--tiff", "jxl", "--reconvert-older") == 1
    assert "2025/scans/000038.tif" in backed(env.dest)  # the TIFF as it is
    assert (env.dest / "2025/scans/000038.tif").read_bytes() == \
        (env.src / "2025/scans/000038.tif").read_bytes()
    monkeypatch.setattr(sb, "_rename_no_clobber", real)
    assert backup(env, "--tiff", "jxl") in (0, 1)       # the next ordinary run
    have = backed(env.dest)
    assert "2025/scans/000038.tif.jxl" in have or "2025/scans/000038.tif" in have


def test_m1_a_failure_before_the_old_group_left_keeps_it(env, monkeypatch, photobackup_log_dir):
    """The page's name is refused before anything moves (_place_group's check):
    the old group is whole, it stays, nothing is copied as it is."""
    assert backup(env, "--tiff", "jxl") == 0
    new_jxl_photo(monkeypatch)
    real = sb._place_group

    def refusing(status, *args, **kwargs):
        if status.planned.rel.endswith("000037.tiff.jxl"):
            raise OSError("page 0 would take the name of another file of the backup")
        return real(status, *args, **kwargs)
    monkeypatch.setattr(sb, "_place_group", refusing)
    assert backup(env, "--tiff", "jxl", "--reconvert-older") == 1
    assert "2025/scans/000037.tiff.jxl" in backed(env.dest)
    assert "2025/scans/000037.tiff" not in backed(env.dest)
    assert converted(env.dest)["2025/scans/000037.tiff.jxl"][3] == "jxl-photo 9.9.9"
    assert "the JXL made by the older jxl-photo stays" in latest_log(photobackup_log_dir)


def test_s1_reconvert_older_never_remakes_a_newer_jxl(env, monkeypatch, photobackup_log_dir):
    new_jxl_photo(monkeypatch, "9.9.10")                # the groups made by the NEWER one
    assert backup(env, "--tiff", "jxl") == 0
    made = len(calls(env, command="encode"))
    new_jxl_photo(monkeypatch, "9.9.9")                 # this run's jxl-photo is older
    assert backup(env, "--tiff", "jxl", "--reconvert-older") == 0
    assert tools(env) == {"jxl-photo 9.9.10"}
    assert len(calls(env, command="encode")) == made
    assert not under(env.dest, VERSIONS)


def test_s1_a_tool_without_a_version_counts_as_older(env, monkeypatch, photobackup_log_dir):
    assert backup(env, "--tiff", "jxl") == 0
    connection = sqlite3.connect(str(env.dest / sb.CATALOG_FOLDER / "catalog.sqlite"))
    connection.execute("UPDATE converted SET tool = 'jxl-photo ?' WHERE source_key LIKE "
                       "'%000037.tiff'")
    connection.commit()
    connection.close()
    made = len(calls(env, command="encode"))
    assert backup(env, "--tiff", "jxl", "--reconvert-older") == 0
    assert len(calls(env, command="encode")) == made + 1    # only that one
    log = latest_log(photobackup_log_dir)
    assert "RECONVERT 1 TIFF made by an older jxl-photo" in log
    assert "jxl-photo ?" in log and "no version" in log


def test_s2_the_group_of_an_older_version_of_the_file_stays(env, capsys, photobackup_log_dir):
    assert backup(env, "--jpeg", "jxl") == 0
    jxl = (env.dest / (EXPORT_JPG + ".jxl")).read_bytes()
    source = env.src / EXPORT_JPG
    source.write_bytes(source.read_bytes() + b" edited on the source")     # a changed source
    capsys.readouterr()
    before = snapshot(env.src)
    assert backup(env, "--jpeg", "copy") == 0           # no --update-changed
    out = capsys.readouterr().out
    assert snapshot(env.src) == before
    assert (env.dest / EXPORT_JPG).read_bytes() == source.read_bytes()  # the new one: NEW
    assert (env.dest / (EXPORT_JPG + ".jxl")).read_bytes() == jxl       # the old one stays
    assert db(env.dest, "SELECT count(*) FROM converted") == [(1,)]
    assert not under(env.dest, VERSIONS)
    assert row_count(out, "JXL replaced by their plain copy") is None
    # final review 261126, SF1: the JXL of an older version waits for --update-changed
    assert row_count(out, "JXL of an older version: waits for --update-changed") == "1"
    assert log_lines(photobackup_log_dir, "UNDO WAITS " + os.path.join(*EXPORT_JPG.split("/")))
    assert not log_lines(photobackup_log_dir, "UNDONE ")
    assert backup(env, "--jpeg", "copy", "--dry-run") == 0   # never an orphan
    assert not [row for row in newest_csv(photobackup_log_dir, "orphans")
                if row["rel"].endswith(".jxl")]


def test_s2_an_edit_of_the_same_size_is_proven_after_the_copy(env, photobackup_log_dir):
    """The size says nothing: the bytes this run copied are compared with the
    SHA-256 the JXL was made from, after the copy."""
    assert backup(env, "--jpeg", "jxl") == 0
    source = env.src / EXPORT_JPG
    source.write_bytes(b"z" * source.stat().st_size)    # edited, the same size
    before = snapshot(env.src)
    assert backup(env, "--jpeg", "copy") == 0
    assert snapshot(env.src) == before
    assert (env.dest / EXPORT_JPG).read_bytes() == source.read_bytes()
    assert EXPORT_JPG + ".jxl" in backed(env.dest)
    assert db(env.dest, "SELECT count(*) FROM converted") == [(1,)]
    # the bytes this run copied are the source's: the JXL is of another version
    # of the file, it waits for --update-changed (final review 261126, SF1)
    assert log_lines(photobackup_log_dir, "UNDO WAITS ")
    assert not log_lines(photobackup_log_dir, "UNDONE ")


def test_s2_a_plain_copy_of_another_program_with_other_bytes(env, photobackup_log_dir):
    """Same size and date as the source, no row, other bytes: UP_TO_DATE by size
    and date, but no proof it is the file the JXL holds: the JXL stays."""
    assert backup(env, "--jpeg", "jxl") == 0
    source, target = env.src / EXPORT_JPG, env.dest / EXPORT_JPG
    target.write_bytes(b"y" * source.stat().st_size)
    info = source.stat()
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
    before = snapshot(env.src)
    assert backup(env, "--jpeg", "copy") == 0
    assert snapshot(env.src) == before
    assert EXPORT_JPG + ".jxl" in backed(env.dest)
    assert db(env.dest, "SELECT count(*) FROM converted") == [(1,)]
    assert log_lines(photobackup_log_dir, "UNDO SKIPPED ")
    assert not log_lines(photobackup_log_dir, "UNDONE ")


def test_s2_a_plain_copy_of_another_program_with_the_same_bytes(env, photobackup_log_dir):
    """No row and no hash known: the backup file is read; the same bytes as the
    group's source: proof, the JXL leaves."""
    assert backup(env, "--jpeg", "jxl") == 0
    hand_copy(env, EXPORT_JPG)
    before = snapshot(env.src)
    assert backup(env, "--jpeg", "copy") == 0
    assert snapshot(env.src) == before
    assert EXPORT_JPG + ".jxl" not in backed(env.dest)
    assert EXPORT_JPG + ".jxl" in under(env.dest, VERSIONS)
    assert log_lines(photobackup_log_dir, "UNDONE ")


def test_s3_the_markers_wait_for_a_run_that_backs_up(monkeypatch, menu_config, tmp_path):
    commands = []
    codes = []

    class Done:
        @property
        def returncode(self):
            return codes.pop(0) if codes else 0
    monkeypatch.setattr(menu.subprocess, "run", lambda command, **kwargs: commands.append(
        command[2:]) or Done())
    monkeypatch.setattr(menu, "_menu_title", lambda: None)
    for marker in (menu._reconvert_marker(), menu._retry_marker()):
        Path(marker).write_text("asked\n", encoding="utf-8")
    menu.run_manifest(["--manifest", "m.csv", "--analyze"])
    menu.run_mirror(["--dest", str(tmp_path / "d"), "--show-trash"])
    menu.run_mirror(["--dest", str(tmp_path / "d"), "--empty-trash"])
    menu.run_backend(["--source", "S", "--verify", "quick"])
    for tail in commands:
        assert "--reconvert-older" not in tail and "--retry-conversions" not in tail, tail
    codes.append(2)                                     # a run that stops before writing
    menu.run_backend(["--source", "S", "--yes"])
    assert "--reconvert-older" in commands[-1] and "--retry-conversions" in commands[-1]
    assert os.path.isfile(menu._reconvert_marker()) and os.path.isfile(menu._retry_marker())
    menu.run_manifest(["--manifest", "m.csv", "--yes"])          # the run: then done
    assert "--reconvert-older" in commands[-1] and "--retry-conversions" in commands[-1]
    assert not os.path.exists(menu._reconvert_marker())
    assert not os.path.exists(menu._retry_marker())


def mirror_box(out):
    """The mirror's box printed before the question (not the DONE box)."""
    start = out.index(" MIRROR (")
    end = out.find("MIRROR DONE", start)
    return out[start:end if end >= 0 else len(out)]


def test_s4_the_box_says_the_jxl_leave_and_where_they_go(env, monkeypatch, capsys,
                                                         photobackup_log_dir):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    jxl, copy = manifest(env, "jxl.csv", jpeg="jxl"), manifest(env, "copy.csv", jpeg="copy")
    assert mir.main(["--manifest", str(jxl), "--dest", str(env.dest), "--yes"]) == 0
    capsys.readouterr()
    assert mir.main(["--manifest", str(copy), "--dest", str(env.dest), "--yes"]) == 0
    box = mirror_box(capsys.readouterr().out)
    assert "Nothing will be removed" not in box
    assert "1 file (" in box and "to the trash until" in box
    assert os.path.join(env.dest, sb.CATALOG_FOLDER, mir.MIRROR_TRASH_FOLDER) in box


@pytest.mark.skipif(os.name != "nt", reason="the read-only attribute is a Windows one here")
def test_s5_a_read_only_file_is_left_as_it_is(env, photobackup_log_dir):
    source, target = hand_copy(env)
    shift(target, DAY_NS)
    target.chmod(stat.S_IREAD)
    mtime = target.stat().st_mtime_ns
    try:
        before = snapshot(env.src)
        assert backup(env, "--adopt-by-hash", "--update-changed") == 0      # not an error
        assert snapshot(env.src) == before
        log = latest_log(photobackup_log_dir)
        rel = os.path.join(*NOTES.split("/"))
        assert f"ADOPT SKIPPED {rel}: read-only" in log
        assert "ADOPT FAILED" not in log and f"COPY {rel}" not in log
        assert target.stat().st_mtime_ns == mtime
        assert not target.stat().st_mode & stat.S_IWRITE        # still read-only
        assert not file_row(env, NOTES)
        assert not under(env.dest, VERSIONS)
    finally:
        target.chmod(stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.skipif(os.name != "nt", reason="the read-only attribute is a Windows one here")
def test_s5_the_mirror_leaves_a_read_only_file_too(env, monkeypatch, capsys, photobackup_log_dir):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    target = env.dest / mirror_rel(env, NOTES)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(env.src / NOTES, target)
    shift(target, DAY_NS)
    target.chmod(stat.S_IREAD)
    try:
        man = manifest(env, "m.csv")
        assert mir.main(["--manifest", str(man), "--dest", str(env.dest), "--yes",
                         "--adopt-by-hash"]) == 0
        out = capsys.readouterr().out
        assert not target.stat().st_mode & stat.S_IWRITE        # never made writable
        assert "No errors." in out[out.index("MIRROR DONE"):]
        assert "replacing a changed file" not in out[out.index("MIRROR DONE"):]
    finally:
        target.chmod(stat.S_IWRITE | stat.S_IREAD)


def test_s5_the_mirror_box_counts_an_adoption_that_failed(env, monkeypatch, capsys,
                                                          photobackup_log_dir):
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    target = env.dest / mirror_rel(env, NOTES)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(env.src / NOTES, target)
    shift(target, DAY_NS)
    real = sb._apply_hash_adoptions

    def late(statuses, dest, catalog, log):
        shift(target, 3 * DAY_NS)                       # after the hashes, before the write
        return real(statuses, dest, catalog, log)
    monkeypatch.setattr(sb, "_apply_hash_adoptions", late)
    man = manifest(env, "m.csv")
    assert mir.main(["--manifest", str(man), "--dest", str(env.dest), "--yes",
                     "--adopt-by-hash"]) == 1
    done = capsys.readouterr().out
    done = done[done.index("MIRROR DONE"):]
    assert "No errors." not in done and "ERRORS: see the log." in done


def test_n1_a_third_run_undoes_nothing(env, photobackup_log_dir):
    assert backup(env, "--jpeg", "jxl") == 0
    assert backup(env, "--jpeg", "copy") == 0
    assert log_lines(photobackup_log_dir, "UNDONE ")
    assert backup(env, "--jpeg", "copy") == 0
    assert not log_lines(photobackup_log_dir, "UNDONE ")


# ---- final review 261126, MF1: the mirror's verdict said "(to versions\ when the
# backup did not write them)" for another program's TIFF copies, which go to the
# trash (or are deleted for good with no trash) ---------------------------------
@pytest.mark.parametrize("keep_days", [0, 30])
def test_mf1_the_verdict_never_sends_another_programs_copy_to_versions(
        env, monkeypatch, capsys, photobackup_log_dir, keep_days):
    from test_tiff_jxl import write_manifest
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    # a TIFF copy its JXL replaces: a TIFF original (261126, keep-days)
    monkeypatch.setattr(mir, "MIRROR_ORIGINALS_KEEP_DAYS", keep_days)
    type_what_is_asked(monkeypatch)
    rel = mirror_rel(env, "2025/scans/000037.tiff")
    target = env.dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(env.src / "2025/scans/000037.tiff", target)   # another program's copy, no row
    before = snapshot(env.src)
    man = write_manifest(env.root / "jxl.csv", (env.src / "2025", "6", "", "", "jxl"))
    assert mir.main(["--manifest", str(man), "--dest", str(env.dest), "--yes"]) == 0
    assert snapshot(env.src) == before
    out = capsys.readouterr().out
    box = out[out.index(" MIRROR ("):out.index("MIRROR DONE")]
    verdict = " ".join(box[:box.index("=====")].split())
    assert "to versions\\ when the backup did not write them" not in verdict
    assert rel not in under(env.dest, VERSIONS)
    assert not target.exists()
    assert (rel in under(env.dest, mir.MIRROR_TRASH_ORIGINALS_FOLDER)) == (keep_days > 0)


# ---- final review 261126, SF1 (plan keep-days, section 1): the JXL of an OLDER
# version of a file is the old copy of a changed file: it leaves with
# --update-changed (the mirror: always), as an old version ------------------------
def restore_rows(log_dir):
    files = sorted(Path(log_dir).glob("*_restore*.csv"), key=lambda path: path.stat().st_mtime_ns)
    return list(csv.DictReader(io.StringIO(files[-1].read_text(encoding="utf-8-sig"))))


def test_sf1_an_older_versions_jxl_leaves_and_the_restore_gives_the_new_one(
        env, capsys, photobackup_log_dir):
    """(star) After an edit and --jpeg copy with --update-changed, the JXL of
    the old version is in versions (UNDONE (older version)), and the restore
    gives back the NEW version, exit 0 (review P1)."""
    import restore_backup as rb
    assert backup(env, "--jpeg", "jxl") == 0
    source = env.src / EXPORT_JPG
    old = source.read_bytes()
    source.write_bytes(old + b" edited on the source")      # the JPEG changed since its JXL
    before = snapshot(env.src)
    capsys.readouterr()
    assert backup(env, "--jpeg", "copy", "--update-changed") == 0
    out = capsys.readouterr().out
    assert snapshot(env.src) == before
    assert row_count(out, "JXL of an older version (to versions\\)") == "1"
    have = backed(env.dest)
    assert EXPORT_JPG in have and EXPORT_JPG + ".jxl" not in have
    assert EXPORT_JPG + ".jxl" in under(env.dest, VERSIONS)
    assert db(env.dest, "SELECT count(*) FROM converted") == [(0,)]
    assert log_lines(photobackup_log_dir, "UNDONE (older version) ")
    code = rb.main(["--backup", str(env.dest), "--yes", "--to", str(env.out)])
    assert (env.out / EXPORT_JPG).read_bytes() == source.read_bytes()
    rows = [row["status"] for row in restore_rows(photobackup_log_dir)
            if row["rel"].replace("\\", "/") == EXPORT_JPG]
    assert code == 0 and rows == ["RESTORED"], rows


def test_sf1_without_update_changed_it_waits(env, photobackup_log_dir):
    """(star) Without --update-changed the older version's JXL stays (UNDO
    WAITS); the next run with it sets it aside."""
    assert backup(env, "--jpeg", "jxl") == 0
    source = env.src / EXPORT_JPG
    source.write_bytes(source.read_bytes() + b" edited")
    assert backup(env, "--jpeg", "copy") == 0
    assert EXPORT_JPG + ".jxl" in backed(env.dest)
    assert log_lines(photobackup_log_dir, "UNDO WAITS ")
    assert backup(env, "--jpeg", "copy", "--update-changed") == 0
    assert EXPORT_JPG + ".jxl" not in backed(env.dest)
    assert EXPORT_JPG + ".jxl" in under(env.dest, VERSIONS)


def test_sf1_no_proof_still_keeps_it(env, photobackup_log_dir):
    """Only "no proof" keeps it unconditionally: a plain copy of another
    program with other bytes (no row) proves nothing of the source."""
    assert backup(env, "--jpeg", "jxl") == 0
    source, target = env.src / EXPORT_JPG, env.dest / EXPORT_JPG
    target.write_bytes(b"y" * source.stat().st_size)
    info = source.stat()
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert backup(env, "--jpeg", "copy", "--update-changed") == 0
    assert EXPORT_JPG + ".jxl" in backed(env.dest)
    assert log_lines(photobackup_log_dir, "UNDO SKIPPED ")


@pytest.mark.parametrize("policy", ["selection", "disk"])
def test_sf1_the_mirror_sets_the_older_versions_jxl_aside(env, monkeypatch, policy,
                                                         photobackup_log_dir):
    """(star) A mirror (Mirror=selection or disk) moves the JXL of the old
    version out again, to its trash of old versions (review P6)."""
    monkeypatch.setattr(mir, "MIRROR_MAX_DELETE_PERCENT", 0)
    jxl = env.root / "jxl.csv"
    jxl.write_text("Folder,Mode,Originals,Mirror,Tiff,Jpeg\n"
                   f'"{env.src / "2025"}",6,,{policy},,jxl\n', encoding="utf-8")
    copy = env.root / "copy.csv"
    copy.write_text("Folder,Mode,Originals,Mirror,Tiff,Jpeg\n"
                    f'"{env.src / "2025"}",6,,{policy},,copy\n', encoding="utf-8")
    assert mir.main(["--manifest", str(jxl), "--dest", str(env.dest), "--yes"]) == 0
    source = env.src / EXPORT_JPG
    source.write_bytes(source.read_bytes() + b" edited on the source")
    before = snapshot(env.src)
    assert mir.main(["--manifest", str(copy), "--dest", str(env.dest), "--yes"]) == 0
    assert mir.main(["--manifest", str(copy), "--dest", str(env.dest), "--yes"]) == 0
    assert snapshot(env.src) == before
    rel = mirror_rel(env, EXPORT_JPG)
    assert rel + ".jxl" not in backed(env.dest)
    assert rel + ".jxl" in under(env.dest, mir.MIRROR_TRASH_VERSIONS_FOLDER)
    assert (env.dest / rel).read_bytes() == source.read_bytes()
