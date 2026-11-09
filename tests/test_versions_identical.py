"""versions\\ keeps real old versions only. Capture One rewrites every .cos of a
folder it opens: new date, same bytes. The new file is still copied every time;
the comparison only decides whether the old one is worth keeping."""
import os
import stat
from pathlib import Path

import pytest

import selective_backup as sb

from conftest import argv_for, build_base_tree

HOSTILE = "吾妻山公園_菜の花 [FINAL] 5% off"
COS = "G/2025/shoot/CaptureOne/Settings166/_DSC6489.NEF.cos"
NEF = "G/2025/shoot/_DSC6489.NEF"


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / HOSTILE
    build_base_tree(root)
    source = root / "G" / "2025" / "shoot"
    dest = tmp_path / "out" / HOSTILE
    assert sb.main(argv_for(source, dest, root, "--yes")) == 0
    return root, source, dest


def run(tree, *extra):
    root, source, dest = tree
    return sb.main(argv_for(source, dest, root, "--yes", *extra))


def versions(dest):
    folder = Path(dest) / sb.CATALOG_FOLDER / "versions"
    return [p for p in folder.rglob("*") if p.is_file()] if folder.exists() else []


def newer(path):
    """A new date, as when Capture One saves the same bytes again."""
    st = path.stat()
    later = st.st_mtime_ns + 3600 * 10**9
    os.utime(path, ns=(later, later))
    return later


@pytest.fixture(params=["small", "large"])
def size_path(request, monkeypatch):
    """Small files are flushed in batches, large ones go through _copy_file."""
    if request.param == "large":
        monkeypatch.setattr(sb, "SMALL_FILE_KB", 0)
    return request.param


def test_same_bytes_with_a_new_date_is_copied_but_not_kept(tree, size_path):
    root, _, dest = tree
    src = root / COS
    later = newer(src)
    assert run(tree) == 0
    assert versions(dest) == []
    out = dest / COS
    assert out.read_bytes() == src.read_bytes()
    assert out.stat().st_mtime_ns == later          # the new file was really copied


def test_a_second_run_finds_it_up_to_date(tree):
    root, _, dest = tree
    newer(root / COS)
    run(tree)
    before = (dest / COS).stat().st_mtime_ns
    assert run(tree) == 0
    assert versions(dest) == []
    assert (dest / COS).stat().st_mtime_ns == before


def test_update_changed_on_a_raw_follows_the_same_rule(tree, size_path):
    root, _, dest = tree
    newer(root / NEF)
    assert run(tree, "--update-changed") == 0
    assert versions(dest) == []


def test_a_different_file_of_the_same_size_is_kept(tree, size_path):
    root, _, dest = tree
    src = root / COS
    old = src.read_bytes()
    src.write_bytes(bytes(reversed(old)))
    newer(src)
    assert old != src.read_bytes() and len(old) == src.stat().st_size
    assert run(tree) == 0
    kept = versions(dest)
    assert len(kept) == 1 and kept[0].read_bytes() == old
    assert (dest / COS).read_bytes() == src.read_bytes()


def test_a_file_of_another_size_is_kept(tree, size_path):
    root, _, dest = tree
    src = root / COS
    old = src.read_bytes()
    src.write_bytes(old + b"more")
    newer(src)
    assert run(tree) == 0
    kept = versions(dest)
    assert len(kept) == 1 and kept[0].read_bytes() == old


def test_a_source_that_changes_after_the_comparison_keeps_the_old_copy(
        tree, monkeypatch):
    """What counts is what was really copied, not what the file held earlier."""
    root, _, dest = tree
    monkeypatch.setattr(sb, "READ_AHEAD_MB", 0)
    src = root / COS
    old = src.read_bytes()
    newer(src)
    real = sb._sha256_file

    def changes_right_after(path):
        digest = real(path)
        if str(path) == str(dest / COS.replace("/", os.sep)):
            src.write_bytes(bytes(reversed(old)))
        return digest

    monkeypatch.setattr(sb, "_sha256_file", changes_right_after)
    assert run(tree) == 0
    kept = versions(dest)
    assert len(kept) == 1 and kept[0].read_bytes() == old
    assert (dest / COS).read_bytes() == bytes(reversed(old))


def test_a_failed_copy_leaves_the_identical_old_copy_in_place(tree, monkeypatch, size_path):
    root, _, dest = tree
    newer(root / COS)
    old = (dest / COS).read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk trouble")

    monkeypatch.setattr(sb.shutil, "copystat", boom)
    assert run(tree) != 0
    assert (dest / COS).read_bytes() == old
    assert versions(dest) == []
    assert not list(Path(dest).rglob("*" + sb.TEMP_SUFFIX))


def test_a_read_only_copy_can_be_replaced(tree, size_path):
    """Windows refuses to replace a read-only file; the old copy has to give way."""
    root, _, dest = tree
    src, out = root / COS, dest / COS
    newer(src)
    os.chmod(out, stat.S_IREAD)
    os.chmod(src, stat.S_IREAD)
    try:
        assert run(tree) == 0
        assert versions(dest) == []
        assert out.stat().st_mtime_ns == src.stat().st_mtime_ns
    finally:
        os.chmod(out, stat.S_IWRITE)
        os.chmod(src, stat.S_IWRITE)
