import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import selective_backup  # noqa: E402


@pytest.fixture(autouse=True)
def photobackup_log_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("PHOTOBACKUP_LOG_DIR", str(log_dir))
    monkeypatch.setattr(selective_backup, "LOG_DIR", str(log_dir))
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def make_tree(root, spec):
    root = Path(root)
    for rel, content in spec.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, int):
            name = path.name.encode("utf-8")
            data = (name * (content // len(name) + 1))[:content]
        elif isinstance(content, str):
            data = content.encode("utf-8")
        else:
            data = bytes(content)
        path.write_bytes(data)
    return root


def snapshot(root):
    root = Path(root)
    result = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns, digest)
    return result


def argv_for(source, dest, base, *extra):
    argv = ["--source", str(source), "--dest", str(dest), "--base", str(base)]
    argv.extend(str(item) for item in extra)
    return argv


def parse_args(source, dest, base, *extra):
    return selective_backup._build_parser().parse_args(argv_for(source, dest, base, *extra))


def planned_rels(planned):
    return {item.rel.replace("\\", "/"): item.category for item in planned.files}


def dest_files(dest):
    dest = Path(dest)
    result = set()
    for dirpath, _dirnames, filenames in os.walk(dest):
        for name in filenames:
            result.add(str((Path(dirpath) / name).relative_to(dest)).replace("\\", "/"))
    return result


def latest_log(log_dir):
    files = sorted(Path(log_dir).glob("*.log"), key=lambda path: path.stat().st_mtime_ns)
    assert files, f"no log file in {log_dir}"
    return files[-1].read_text(encoding="utf-8")


def latest_csv(log_dir, suffix):
    files = sorted(Path(log_dir).glob("*" + suffix), key=lambda path: path.stat().st_mtime_ns)
    assert files, f"no {suffix} in {log_dir}"
    return files[-1].read_text(encoding="utf-8-sig")


BASE_TREE = {
    "G/2025/shoot/_DSC6489.NEF": 120,
    "G/2025/shoot/_DSC6489.JPG": 110,
    "G/2025/shoot/_DSC6489.xmp": 40,
    "G/2025/shoot/_DSC6491.NEF": 121,
    "G/2025/shoot/_DSC6491.JPG": 111,
    "G/2025/shoot/_DSC6491.xmp": 41,
    "G/2025/shoot/_DSC6491_NXstudio.xmp": 42,
    "G/2025/shoot/_DSC6495.NEF": 122,
    "G/2025/shoot/_DSC6495.JPG": 112,
    "G/2025/shoot/_DSC6495.xmp": 43,
    "G/2025/shoot/DSC_9360.NEF": 130,
    "G/2025/shoot/DSC_9360.xmp": 44,
    "G/2025/shoot/DSC_936.NEF": 131,
    "G/2025/shoot/DSC0001.NEF": 140,
    "G/2025/shoot/DSC0001_Nxstudio_v1.tiff": 141,
    "G/2025/shoot/_EXPORT/16B_JXL/_DSC6489_ProPhoto-g22_v1.jxl": 200,
    "G/2025/shoot/_EXPORT/16B_JXL/_DSC6491_NXstudio_ProPhoto-g22_v1.jxl": 201,
    "G/2025/shoot/_EXPORT/16B_JXL/DSC_9360_ProPhoto-g22_v1.jxl": 202,
    "G/2025/shoot/_EXPORT/JXL_mobile/_DSC6489_sRGB_v1.jxl": 203,
    "G/2025/shoot/_EXPORT/sRGB/_DSC6489_sRGB_v1.jpg": 210,
    "G/2025/shoot/_EXPORT/sRGB/DSC0001_Nxstudio_v1_sRGB.jpg": 211,
    "G/2025/shoot/_EXPORT/CHECK/_DSC6491_v1_CHECK.jpg": 212,
    "G/2025/shoot/_EXPORT/WEB/000037.jpg": 213,
    "G/2025/shoot/_EXPORT/WEB/notes.txt": 30,
    "G/2025/shoot/CaptureOne/Cache/Proxies/a.cop": 300,
    "G/2025/shoot/CaptureOne/Settings166/_DSC6489.NEF.cos": 301,
    "G/2025/shoot/NKSC_PARAM/_DSC6491.NEF.nksc": 400,
    "G/2025/shoot/_OUTTAKES/_DSC6490.NEF": 500,
    "G/2025/shoot/_OUTTAKES/CaptureOne/Settings166/x.cos": 501,
    "G/2025/shoot/OLD_TIFFs/old.tif": 502,
    "G/2025/plain/IMG_0001.JPG": 600,
    "G/2025/plain/CaptureOne/Cache/c.cop": 601,
    "G/2025/plain/CaptureOne/Settings166/s.cos": 602,
    "G/2025/legacy/1. Selected/Lightroom_Export/IMG_3132.jpg": 700,
    "G/2025/legacy/1. Selected/IMG_3132.CR2": 701,
    "G/2025/legacy/IMG_3132.CR2": 702,
    "G/2025/legacy/IMG_9999.CR2": 703,
}


def build_base_tree(root):
    return make_tree(root, BASE_TREE)
