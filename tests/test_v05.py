"""v0.5.0: jxl-photo derivatives skipped when their master is copied; the
duplicate counter "(2)" is part of the photo key. Every tree lives in a
HOSTILE folder (Japanese, [ ], a space, %)."""
import struct

import pytest

import selective_backup

from conftest import argv_for, latest_csv, latest_log

EVENT = "G/2025/250118_吾妻山公園_菜の花 [FINAL] 5% off"
SIGNATURE = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
FTYP = struct.pack(">I4s4sI4s", 20, b"ftyp", b"jxl ", 0, b"jxl ")


def box(kind, payload):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def xmp(*tokens):
    relation = "".join(f"<rdf:li>{token}</rdf:li>" for token in tokens)
    return (f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><dc:relation><rdf:Bag>{relation}'
            f"</rdf:Bag></dc:relation></x:xmpmeta>").encode("utf-8")


def jxl(*tokens, size=2000, xml_kind=b"xml "):
    """A JXL container: signature, ftyp, Exif, XMP (with these tokens), codestream."""
    head = SIGNATURE + FTYP + box(b"Exif", b"\x00" * 30)
    if tokens:
        head += box(xml_kind, xmp(*tokens))
    codestream = b"\xff\x0a" + b"\x00" * max(0, size - len(head) - 10)
    return head + box(b"jxlc", codestream)


MASTER = ("jxlphoto-depth:16", "jxlphoto-origin:tiff16",
          "jxlphoto-src:b9cf20e1422140c1", "jxlphoto-srcsum:e6317b596c1e840d")
DERIVED = ("jxlphoto-depth:16", "jxlphoto-origin:tiff16", "jxlphoto-derived:sRGB/d3e9")


def write(root, files):
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else b"x" * data)


def backed_up(dest):
    return {p.relative_to(dest).as_posix().split("01_Z7/", 1)[-1]
            for p in dest.rglob("*") if p.is_file() and "_photo_backup" not in p.parts}


def run(tmp_path, files, *extra):
    write(tmp_path, {f"{EVENT}/01_Z7/{rel}": data for rel, data in files.items()})
    dest = tmp_path / "バックアップ [x] 5%"
    rc = selective_backup.main(argv_for(tmp_path / "G" / "2025", dest, tmp_path, "--yes", *extra))
    return rc, dest


def test_derivative_skipped_when_its_master_is_copied(tmp_path):
    rc, dest = run(tmp_path, {
        "DSC_0001.NEF": 100,
        "_EXPORT/16B_JXL/DSC_0001_ProPhoto-g22.jxl": jxl(*MASTER),
        "_EXPORT/sRGB/DSC_0001_sRGB.jxl": jxl(*DERIVED),
    })
    assert rc == 0
    assert backed_up(dest) == {"DSC_0001.NEF", "_EXPORT/16B_JXL/DSC_0001_ProPhoto-g22.jxl"}
    log = latest_log(tmp_path / "logs")
    assert "jxl-photo derivatives (their master is copied" in log
    # the backup verifies clean: the skipped derivative is not "not backed up"
    assert selective_backup.main(argv_for(tmp_path / "G" / "2025", dest, tmp_path,
                                          "--verify", "quick")) == 0


def test_derivatives_flag_copies_them(tmp_path):
    rc, dest = run(tmp_path, {
        "DSC_0001.NEF": 100,
        "_EXPORT/16B_JXL/DSC_0001_ProPhoto-g22.jxl": jxl(*MASTER),
        "_EXPORT/sRGB/DSC_0001_sRGB.jxl": jxl(*DERIVED),
    }, "--derivatives")
    assert rc == 0
    assert "_EXPORT/sRGB/DSC_0001_sRGB.jxl" in backed_up(dest)


def test_no_master_in_the_run_means_copy(tmp_path):
    # a lossless JPEG -> JXL archive carries no token; an excluded master is not copied
    rc, dest = run(tmp_path, {
        "DSC_0002.NEF": 100,
        "_EXPORT/JXL_jpeg/DSC_0002.jxl": jxl(),
        "_EXPORT/sRGB/DSC_0002_sRGB.jxl": jxl(*DERIVED),
        "DSC_0003.NEF": 100,
        "_EXPORT/16B_JXL/DSC_0003_ProPhoto-g22.jxl": jxl(*MASTER),
        "_EXPORT/sRGB/DSC_0003_sRGB.jxl": jxl(*DERIVED),
    }, "--exclude-export-subfolders", "16B_JXL")
    assert rc == 0
    copied = backed_up(dest)
    assert "_EXPORT/sRGB/DSC_0002_sRGB.jxl" in copied
    assert "_EXPORT/JXL_jpeg/DSC_0002.jxl" in copied
    assert "_EXPORT/sRGB/DSC_0003_sRGB.jxl" in copied


@pytest.mark.parametrize("derived", [
    jxl(*DERIVED, xml_kind=b"brob"),                     # compressed XMP: cannot read
    b"\xff\x0a" + b"\x00" * 500,                         # bare codestream: no metadata
    jxl(*(DERIVED + ("jxlphoto-src:0123456789abcdef",))),  # both tokens: not a proof
    jxl(*DERIVED)[:30],                                  # truncated file
])
def test_uncertain_derivative_is_copied(tmp_path, derived):
    rc, dest = run(tmp_path, {
        "DSC_0004.NEF": 100,
        "_EXPORT/16B_JXL/DSC_0004_ProPhoto-g22.jxl": jxl(*MASTER),
        "_EXPORT/sRGB/DSC_0004_sRGB.jxl": derived,
    })
    assert rc == 0
    assert "_EXPORT/sRGB/DSC_0004_sRGB.jxl" in backed_up(dest)


def test_jxl_role_reads_only_the_xmp_box(tmp_path):
    folder = tmp_path / "吾妻山 [x] 5%"
    folder.mkdir()
    cases = {"m.jxl": (jxl(*MASTER), "master"), "d.jxl": (jxl(*DERIVED), "derivative"),
             "none.jxl": (jxl(), None), "brob.jxl": (jxl(*DERIVED, xml_kind=b"brob"), None)}
    # a 64-bit box length before the XMP box
    big = SIGNATURE + FTYP + struct.pack(">I4sQ", 1, b"Exif", 16 + 30) + b"\x00" * 30
    cases["large.jxl"] = (big + box(b"xml ", xmp(*DERIVED)) + box(b"jxlc", b"\xff\x0a"), "derivative")
    # XMP after the codestream is never read
    cases["late.jxl"] = (SIGNATURE + FTYP + box(b"jxlc", b"\xff\x0a" * 10)
                         + box(b"xml ", xmp(*DERIVED)), None)
    for name, (data, role) in cases.items():
        (folder / name).write_bytes(data)
        assert selective_backup._jxl_role(str(folder / name)) == role, name
    assert selective_backup._jxl_role(str(folder / "missing.jxl")) is None


def test_settings_read_not_hardcoded_v05(tmp_path, monkeypatch):
    monkeypatch.setattr(selective_backup, "DERIVATIVE_TOKEN", "other-derived:")
    folder = tmp_path / "吾妻山 [x] 5%"
    folder.mkdir()
    (folder / "d.jxl").write_bytes(jxl(*DERIVED))
    assert selective_backup._jxl_role(str(folder / "d.jxl")) is None
    monkeypatch.setattr(selective_backup, "COPY_JXLPHOTO_DERIVATIVES", True)
    args = selective_backup._build_parser().parse_args(["--source", "x", "--dest", "y"])
    assert args.derivatives is True


def test_duplicate_counter_is_its_own_photo(tmp_path):
    # before v0.5.0 "DSC_0001 (2)_sRGB.jpg" took the key DSC_0001, and the
    # JXL of DSC_0001 made jxl>tif>jpg drop this JPEG of another photo
    rc, dest = run(tmp_path, {
        "DSC_0001.NEF": 100,
        "DSC_0001 (2).NEF": 110,
        "_EXPORT/16B_JXL/DSC_0001_ProPhoto-g22.jxl": jxl(),
        "_EXPORT/sRGB/DSC_0001 (2)_sRGB.jpg": 60,
    })
    assert rc == 0
    copied = backed_up(dest)
    assert "_EXPORT/sRGB/DSC_0001 (2)_sRGB.jpg" in copied
    assert {"DSC_0001.NEF", "DSC_0001 (2).NEF"} <= copied


def test_rescan_keeps_both_originals(tmp_path):
    # film rescans: "0005 (2).tif" is the same frame as "0005.tif"; the export
    # came from the rescan. v0.4.0 copied both originals; v0.5.0 must too.
    rc, dest = run(tmp_path, {
        "0005.tif": 300,
        "0005 (2).tif": 310,
        "0006.tif": 320,
        "Lightroom_Export/0005 (2).jpg": 60,
    })
    assert rc == 0
    copied = backed_up(dest)
    assert {"0005.tif", "0005 (2).tif", "Lightroom_Export/0005 (2).jpg"} <= copied
    assert "0006.tif" not in copied


def test_match_key_keeps_the_shortest_rule_otherwise():
    anchors = {"dsc_0001", "dsc_0001 (2)", "dsc0001", "dsc0001_nxstudio_v1", "p1010076 (2)"}
    assert selective_backup._match_key("DSC_0001 (2)_sRGB", anchors) == "dsc_0001 (2)"
    assert selective_backup._match_key("DSC_0001_v1 (2)", anchors) == "dsc_0001"
    assert selective_backup._match_key("DSC_0001 (3)_x", anchors) == "dsc_0001"
    assert selective_backup._match_key("DSC0001_Nxstudio_v1_sRGB", anchors) == "dsc0001"
    assert selective_backup._match_key("P1010076 (2)_web", anchors) == "p1010076 (2)"
    assert selective_backup._match_key("XYZ_1", anchors) is None
