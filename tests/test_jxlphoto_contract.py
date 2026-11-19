"""The contract between photo-backup and jxl-photo (docs/jxlphoto_contract.md),
checked against a REAL jxl-photo. Skipped unless PHOTOBACKUP_JXLPHOTO names
one (its jxl_photo.py or jxl-photo.exe):

    set PHOTOBACKUP_JXLPHOTO=C:\\path\\to\\jxl-photo\\jxl_photo.py
    python -m pytest tests/test_jxlphoto_contract.py -v

Each test is one item of the contract (JP-1 ... JP-14): a failure names the
item, and the document says where that item lives in both programs. Needs
numpy and tifffile (jxl-photo needs them too). About a minute: every call is
a real jxl-photo, on small synthetic TIFFs; nothing outside a temp folder."""
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import selective_backup as sb

JXLPHOTO = os.environ.get("PHOTOBACKUP_JXLPHOTO", "")
pytestmark = pytest.mark.skipif(not JXLPHOTO, reason="PHOTOBACKUP_JXLPHOTO is not set "
                                                     "(the path of a real jxl-photo)")
numpy = pytest.importorskip("numpy")
tifffile = pytest.importorskip("tifffile")

# the distances make each class of TIFF visible: a developed 16-bit TIFF is
# lossy (1.0), a scan and an 8-bit TIFF lossless (0)
PROFILE = {"distance": 1.0, "scan_distance": 0, "eight_bit_distance": 0}
DEVELOPED = "NX Studio 1.9 W"           # a Software tag of a developer, not of a scanner
TIMEOUT_S = 600


def _image(height, width, bits, seed):
    rng = numpy.random.default_rng(seed)
    top = (1 << bits) - 1
    ramp = numpy.linspace(0, top, height * width * 3).reshape(height, width, 3)
    noise = rng.integers(0, max(top // 64, 2), ramp.shape)
    return (ramp + noise).clip(0, top).astype(numpy.uint16 if bits == 16 else numpy.uint8)


def _tiffs(folder):
    """The TIFFs of the check, each a class jxl-photo tells apart."""
    made = {}
    path = folder / "photo16.tif"           # developed, 16-bit: `distance`
    tifffile.imwrite(path, _image(64, 96, 16, 1), photometric="rgb", software=DEVELOPED)
    made["photo16"] = path
    path = folder / "scan16.tif"            # no Software tag, no camera EXIF: a scan
    tifffile.imwrite(path, _image(64, 96, 16, 2), photometric="rgb", software=False)
    made["scan16"] = path
    path = folder / "photo8.tif"            # 8-bit: `eight_bit_distance`
    tifffile.imwrite(path, _image(64, 96, 8, 3), photometric="rgb", software=DEVELOPED)
    made["photo8"] = path
    path = folder / "multi.tif"             # the main image + a reduced 8-bit page,
    with tifffile.TiffWriter(path) as writer:  # as NX Studio writes its TIFF
        writer.write(_image(64, 96, 16, 4), photometric="rgb", software=DEVELOPED)
        writer.write(_image(16, 24, 8, 5), photometric="rgb", subfiletype=1)
    made["multi"] = path
    path = folder / "corrupt.tif"           # a TIFF header, then nothing that makes sense
    path.write_bytes(b"II*\x00" + bytes(range(256)) * 8)
    made["corrupt"] = path
    return made


def _stat(path):
    stat = os.stat(path)
    return stat.st_size, stat.st_mtime_ns, sb._sha256_file(str(path))


def _snapshot(folder):
    return {str(path.relative_to(folder)): _stat(path)
            for path in Path(folder).rglob("*") if path.is_file()}


def _tail(run):
    return sb._tail((run.stdout or "") + "\n" + (run.stderr or ""), 8)


@pytest.fixture(scope="module")
def check(tmp_path_factory):
    """Every TIFF encoded once with photo-backup's own command line, every
    group decoded once with the restore's, and a copy of jxl-photo kept the
    way a backup keeps it."""
    root = tmp_path_factory.mktemp("jxlphoto contract 吾妻山 [x]")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sb, "LOG_DIR", str(root / "logs"))
        tiffs = _tiffs(root)
        before = {name: _stat(path) for name, path in tiffs.items()}
        encoded = {}
        for name, tiff in tiffs.items():
            out = root / f"enc_{name}"
            out.mkdir()
            run = sb._run_jxlphoto(JXLPHOTO, sb._jxlphoto_encode_args(str(tiff), str(out),
                                                                     PROFILE), TIMEOUT_S)
            jxl = sorted(path.name for path in out.iterdir() if path.suffix.casefold() == ".jxl")
            encoded[name] = SimpleNamespace(run=run, out=out, jxl=jxl)
        decoded = {}
        for name in ("photo16", "scan16", "photo8", "multi"):
            source = encoded[name].out
            folder = root / f"dec_in_{name}"
            shutil.copytree(source, folder)
            out = root / f"dec_out_{name}"
            out.mkdir()
            inputs = _snapshot(folder)
            run = sb._run_jxlphoto(JXLPHOTO, sb._jxlphoto_decode_args(str(folder), str(out)),
                                   TIMEOUT_S)
            made = sorted(path for path in out.iterdir()
                          if path.suffix.casefold() in (".tif", ".tiff"))
            decoded[name] = SimpleNamespace(run=run, made=made, inputs=inputs,
                                            inputs_after=_snapshot(folder))
        # a copy of jxl-photo as the backup keeps it (_keep_tools), run on its own
        tools = root / "tools"
        tools.mkdir()
        folder = os.path.dirname(os.path.abspath(JXLPHOTO))
        files = (sb._tool_files(folder, ("*.py", "README*.md", "LICENSE"))
                 if JXLPHOTO.casefold().endswith(".py")
                 else sb._tool_files(folder, (), recursive=True))
        sb._keep_tool_folder(str(tools), "jxl-photo kept", files,
                             SimpleNamespace(log=lambda text: None))
        kept = tools / "jxl-photo kept" / os.path.basename(JXLPHOTO)
        kept_before = _snapshot(tools)
        sb._JXLPHOTO_VERSIONS.pop(str(kept), None)
        kept_version = sb._jxlphoto_version(str(kept))
        kept_out = root / "kept_enc"
        kept_out.mkdir()
        kept_encode = sb._run_jxlphoto(str(kept), sb._jxlphoto_encode_args(
            str(tiffs["photo8"]), str(kept_out), PROFILE), TIMEOUT_S)
        kept_dec = root / "kept_dec"
        kept_dec.mkdir()
        kept_decode = sb._run_jxlphoto(str(kept), sb._jxlphoto_decode_args(
            str(encoded["multi"].out), str(kept_dec)), TIMEOUT_S)
        kept_after = _snapshot(tools)
        # JPEGs: transcoded with photo-backup's command, rebuilt from the JXL alone
        jpegs, transcoded = _jpegs(root), {}
        for name, jpeg in jpegs.items():
            out = root / f"jpg_{name}"
            out.mkdir()
            before[name] = _stat(jpeg)
            run = sb._run_jxlphoto(JXLPHOTO, sb._jxlphoto_transcode_args(str(jpeg), str(out)),
                                   TIMEOUT_S)
            jxl = sorted(path.name for path in out.iterdir() if path.suffix.casefold() == ".jxl")
            result = SimpleNamespace(run=run, jxl=jxl, rebuild=None, rebuilt=[], inputs=None)
            if run.returncode == 0 and len(jxl) == 1:
                alone = root / f"jpg_alone_{name}"         # as the restore copies it
                alone.mkdir()
                shutil.copy2(out / jxl[0], alone / jxl[0])
                result.inputs = _snapshot(alone)
                back = root / f"jpg_back_{name}"
                back.mkdir()
                result.rebuild = sb._run_jxlphoto(JXLPHOTO, sb._jxlphoto_rebuild_args(
                    str(alone / jxl[0]), str(back)), TIMEOUT_S)
                result.rebuilt = [path for path in back.iterdir()
                                  if path.suffix.casefold() in (".jpg", ".jpeg")]
                result.inputs_after = _snapshot(alone)
            transcoded[name] = result
        yield SimpleNamespace(
            root=root, tiffs=tiffs, before=before, encoded=encoded, decoded=decoded,
            kept=kept, kept_before=kept_before, kept_after=kept_after,
            kept_version=kept_version, kept_encode=kept_encode, kept_decode=kept_decode,
            kept_dec=kept_dec, jpegs=jpegs, transcoded=transcoded)


XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-'
       b'rdf-syntax-ns#"><rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
       b'xmp:Rating="5"/></rdf:RDF></x:xmpmeta>')


def _jpegs(folder):
    """A camera JPEG with EXIF and XMP, a progressive one, and a CMYK one (which
    cjxl cannot keep losslessly: the failure path)."""
    image_module = pytest.importorskip("PIL.Image")
    image = image_module.fromarray(_image(48, 64, 8, 9))
    exif = image_module.Exif()
    exif[0x010F] = "NIKON CORPORATION"
    exif[0x0110] = "NIKON Z 7"
    made = {"camera": folder / "camera.jpg", "progressive": folder / "progressive.JPG",
            "cmyk": folder / "cmyk.jpeg"}
    image.save(made["camera"], quality=92, exif=exif.tobytes(), xmp=XMP)
    image.save(made["progressive"], quality=85, progressive=True)
    image.convert("CMYK").save(made["cmyk"], quality=90)
    return made


def _jxl_of(check, name, item):
    """The JXL files of one TIFF; an item with nothing to check fails (it
    never passes on an empty list: JP-2 or JP-3 failed first)."""
    names = check.encoded[name].jxl
    assert names, f"{item}: {name}: no JXL to check (see JP-2 and JP-3)"
    return [check.encoded[name].out / jxl for jxl in names]


def _pages(path):
    with tifffile.TiffFile(path) as tif:
        return [page.asarray() for page in tif.pages]


# ---- the encoder --------------------------------------------------------------------------
def test_jp1_it_says_its_version():
    version = sb._jxlphoto_version(JXLPHOTO)
    assert sb._version_key(version) is not None, (
        "JP-1: `encode --version` must print `jxl-photo X.Y.Z`")
    assert sb._version_key(version) >= sb._version_key(sb.JXLPHOTO_MIN_VERSION), (
        f"JP-1: {version} is older than JXLPHOTO_MIN_VERSION {sb.JXLPHOTO_MIN_VERSION}")


def test_jp2_the_encode_command_is_accepted(check):
    for name in ("photo16", "scan16", "photo8", "multi"):
        run = check.encoded[name].run
        assert run.returncode == 0, f"JP-2: {name}: exit {run.returncode}: {_tail(run)}"


def test_jp3_one_jxl_per_page_named_after_the_tiff(check):
    for name in ("photo16", "scan16", "photo8"):
        assert check.encoded[name].jxl == [f"{name}.jxl"], f"JP-3: {name}: {check.encoded[name].jxl}"
    assert len(check.encoded["multi"].jxl) == 2, f"JP-3: multi: {check.encoded['multi'].jxl}"
    assert "multi.jxl" in check.encoded["multi"].jxl, "JP-3: page 0 is <stem>.jxl"


def test_jp4_the_xmp_box_comes_before_the_image(check):
    for name in ("photo16", "scan16", "photo8", "multi"):
        for path in _jxl_of(check, name, "JP-4"):
            assert sb._jxl_xmp(str(path)) is not None, (
                f"JP-4: {path.name}: no uncompressed `xml ` box before the codestream")


def test_jp5_the_srcsum_is_the_tiffs_sha256(check):
    for name in ("photo16", "scan16", "photo8", "multi"):
        want = sb._jxlphoto_srcsum(check.before[name][2])
        for path in _jxl_of(check, name, "JP-5"):
            tokens = sb._jxl_tokens(str(path))
            assert tokens.get("jxlphoto-srcsum") == [want], (
                f"JP-5: {path.name}: jxlphoto-srcsum {tokens.get('jxlphoto-srcsum')} != {want}")


def test_jp6_a_multipage_tiff_is_a_whole_marked_group(check):
    groups, pages = set(), []
    for path in _jxl_of(check, "multi", "JP-6"):
        jxl = path.name
        tokens = sb._jxl_tokens(str(path))
        assert tokens.get("jxlphoto-pages") == ["2"], f"JP-6: {jxl}: {tokens.get('jxlphoto-pages')}"
        assert len(tokens.get("jxlphoto-page", [])) == 1, f"JP-6: {jxl}: no jxlphoto-page"
        pages.append(tokens["jxlphoto-page"][0])
        groups.update(tokens.get("jxlphoto-mpg", []))
    assert sorted(pages) == ["0", "1"], f"JP-6: pages {pages}"
    assert len(groups) == 1, f"JP-6: group marks {groups}"
    for name in ("photo16", "scan16", "photo8"):
        tokens = sb._jxl_tokens(str(check.encoded[name].out / f"{name}.jxl"))
        assert tokens.get("jxlphoto-pages") in (None, ["1"]), f"JP-6: {name}"


def test_jp7_each_class_gets_its_distance(check):
    for name, lossless in (("scan16", True), ("photo8", True), ("photo16", False)):
        original = _pages(check.tiffs[name])[0]
        back = _pages(check.decoded[name].made[0])[0]
        same = original.shape == back.shape and numpy.array_equal(original, back)
        assert same == lossless, (f"JP-7: {name}: expected "
                                  f"{'the same pixels' if lossless else 'a lossy JXL'}")


def test_jp8_the_tiff_is_never_changed(check):
    for name, tiff in check.tiffs.items():
        assert _stat(tiff) == check.before[name], f"JP-8: {name} changed"


def test_jp9_a_failure_never_claims_the_tiff(check):
    corrupt = check.encoded["corrupt"]
    want = sb._jxlphoto_srcsum(check.before["corrupt"][2])
    claims = [jxl for jxl in corrupt.jxl
              if want in sb._jxl_tokens(str(corrupt.out / jxl)).get("jxlphoto-srcsum", [])]
    assert corrupt.run.returncode != 0 or not claims, (
        f"JP-9: a TIFF that cannot be read gave exit 0 and {claims}")


# ---- the decoder ---------------------------------------------------------------------------
def test_jp10_the_decode_gives_one_tiff_with_its_pages(check):
    for name in ("photo16", "scan16", "photo8", "multi"):
        result = check.decoded[name]
        assert result.run.returncode == 0, (f"JP-10: {name}: exit {result.run.returncode}: "
                                            f"{_tail(result.run)}")
        assert len(result.made) == 1, f"JP-10: {name}: {[path.name for path in result.made]}"
        original, back = _pages(check.tiffs[name]), _pages(result.made[0])
        assert len(back) == len(original), f"JP-10: {name}: {len(original)} -> {len(back)} pages"
        for index, (page_a, page_b) in enumerate(zip(original, back)):
            assert (page_a.dtype, page_a.shape) == (page_b.dtype, page_b.shape), (
                f"JP-10: {name} page {index}: {page_a.dtype}{page_a.shape} -> "
                f"{page_b.dtype}{page_b.shape}")


def test_jp11_the_decode_leaves_its_input_alone(check):
    for name, result in check.decoded.items():
        assert result.inputs, f"JP-11: {name}: no JXL was decoded (see JP-2 and JP-10)"
        assert result.inputs_after == result.inputs, f"JP-11: {name}: the JXL changed"


# ---- a copy of jxl-photo kept in a backup ---------------------------------------------------------
def test_jp12_nothing_is_written_beside_jxlphoto(check):
    added = sorted(set(check.kept_after) - set(check.kept_before))
    changed = sorted(key for key in check.kept_before
                     if check.kept_after.get(key) != check.kept_before[key])
    assert not added and not changed, f"JP-12: written beside jxl-photo: {added + changed}"


def test_jp13_the_copy_a_backup_keeps_runs(check):
    assert check.kept_version == sb._jxlphoto_version(JXLPHOTO), "JP-13: --version"
    assert check.kept_encode.returncode == 0, f"JP-13: encode: {_tail(check.kept_encode)}"
    assert check.kept_decode.returncode == 0, f"JP-13: decode: {_tail(check.kept_decode)}"
    made = [path for path in check.kept_dec.iterdir() if path.suffix.casefold() in (".tif", ".tiff")]
    assert len(made) == 1, f"JP-13: decode made {[path.name for path in made]}"


def test_jp14_a_tiff_kept_as_jxl_is_a_master(check):
    for name in ("photo16", "scan16", "photo8", "multi"):
        for path in _jxl_of(check, name, "JP-14"):
            xmp = sb._jxl_xmp(str(path)) or b""
            assert sb.MASTER_TOKEN.encode() in xmp, f"JP-14: {path.name}: no {sb.MASTER_TOKEN}"
            assert sb.DERIVATIVE_TOKEN.encode() not in xmp, (
                f"JP-14: {path.name}: {sb.DERIVATIVE_TOKEN}")


# ---- JPEG (v0.14.0-rc3) ---------------------------------------------------------------------
def test_jp15_the_transcode_command_is_accepted(check):
    for name in ("camera", "progressive"):
        result = check.transcoded[name]
        assert result.run.returncode == 0, (f"JP-15: {name}: exit {result.run.returncode}: "
                                            f"{_tail(result.run)}")
        assert result.jxl == [f"{check.jpegs[name].stem}.jxl"], f"JP-15: {name}: {result.jxl}"
    for name in check.jpegs:
        assert _stat(check.jpegs[name]) == check.before[name], f"JP-8: {name} changed"


def test_jp16_the_jpeg_comes_back_byte_for_byte(check):
    for name in ("camera", "progressive"):
        result = check.transcoded[name]
        assert result.rebuild is not None, f"JP-16: {name}: nothing to rebuild (see JP-15)"
        assert result.rebuild.returncode == 0, f"JP-16: {name}: {_tail(result.rebuild)}"
        assert len(result.rebuilt) == 1, f"JP-16: {name}: {[p.name for p in result.rebuilt]}"
        assert _stat(result.rebuilt[0])[2] == check.before[name][2], (
            f"JP-16: {name}: the rebuilt JPEG is not the original's bytes")
        assert result.inputs_after == result.inputs, f"JP-16: {name}: the JXL changed"
    cmyk = check.transcoded["cmyk"]                     # refused, or perfect: never in between
    assert cmyk.run.returncode != 0 or (
        cmyk.rebuilt and _stat(cmyk.rebuilt[0])[2] == check.before["cmyk"][2]), (
        "JP-16: a JPEG kept as a JXL that does not give its bytes back")


def test_jp17_the_class_marks_say_which_distance(check):
    for name, lossless in (("scan16", 1), ("photo8", 1), ("photo16", 0)):
        tokens = sb._jxl_tokens(str(_jxl_of(check, name, "JP-17")[0]))
        assert sb._tiff_lossless(PROFILE, tokens) == lossless, (
            f"JP-17: {name}: the marks {sorted(tokens)} do not give its class")
    assert sb._jxl_tokens(str(_jxl_of(check, "scan16", "JP-17")[0])).get("jxlphoto-scan"), (
        "JP-17: a scan carries jxlphoto-scan")
