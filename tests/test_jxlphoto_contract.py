"""The contract between photo-backup and jxl-photo (docs/jxlphoto_contract.md),
checked against a REAL jxl-photo. Skipped unless PHOTOBACKUP_JXLPHOTO names
one (its jxl_photo.py or jxl-photo.exe):

    set PHOTOBACKUP_JXLPHOTO=C:\\path\\to\\jxl-photo\\jxl_photo.py
    python -m pytest tests/test_jxlphoto_contract.py -v

Each test is one item of the contract (JP-1 ... JP-22): a failure names the
item, and the document says where that item lives in both programs. Needs
numpy and tifffile (jxl-photo needs them too), and djxl on PATH (jxl-photo
checks every page of an encode with it: JP-20). About a minute: every call is
a real jxl-photo, on small synthetic TIFFs; nothing outside a temp folder."""
import ast
import json
import os
import re
import shutil
import subprocess
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


def _sha256(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


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
            log_dir = root / f"enc_log_{name}"          # this call's log, as a backup gives it
            run = sb._run_jxlphoto(JXLPHOTO, sb._jxlphoto_encode_args(str(tiff), str(out),
                                                                     PROFILE), TIMEOUT_S,
                                   str(log_dir))
            jxl = sorted(path.name for path in out.iterdir() if path.suffix.casefold() == ".jxl")
            encoded[name] = SimpleNamespace(run=run, out=out, jxl=jxl, log_dir=log_dir)
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
            result = SimpleNamespace(run=run, jxl=jxl, rebuild=None, rebuilt=[], inputs=None,
                                     djxl=None)
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
                patch.setattr(sb, "DJXL", "")             # libjxl's djxl from PATH (JP-18)
                sb._DJXL_FOUND.clear()
                if sb._djxl_path() is not None:
                    try:
                        result.djxl = sb._rebuild_jpeg(str(alone / jxl[0]), None,
                                                       str(root / f"djxl_{name}"), TIMEOUT_S)
                    except sb._ConvertFailed as exc:
                        result.djxl = str(exc)
                sb._DJXL_FOUND.clear()
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


# ---- the fast routes (v0.14.0-rc4) ------------------------------------------------------------
def test_jp18_djxl_rebuilds_the_jpeg_from_stdin(check):
    results = {name: check.transcoded[name].djxl for name in ("camera", "progressive")}
    if all(value is None for value in results.values()):
        pytest.skip("JP-18: no djxl on PATH (photo-backup then uses JP-16)")
    for name, rebuilt in results.items():
        assert isinstance(rebuilt, bytes), f"JP-18: {name}: {rebuilt}"
        assert len(rebuilt) == check.before[name][0] and \
            _sha256(rebuilt) == check.before[name][2], (
                f"JP-18: {name}: djxl did not give the original JPEG's bytes back")


def test_jp19_each_command_is_a_script_beside_jxl_photo(check):
    if not JXLPHOTO.casefold().endswith(".py"):
        pytest.skip("JP-19: jxl-photo.exe runs its commands itself")
    folder = Path(JXLPHOTO).resolve().parent
    backends = None
    for node in ast.parse((folder / "jxl_photo.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_BACKEND_COMMANDS"
                for target in node.targets):
            backends = ast.literal_eval(node.value)
    assert backends is not None, "JP-19: jxl_photo.py has no _BACKEND_COMMANDS"
    for command, module in sb._JXLPHOTO_BACKENDS.items():
        assert backends.get(command) == f"{module}.py", f"JP-19: {command}: {backends.get(command)}"
        argv = sb._jxlphoto_argv(JXLPHOTO, [command, "--version"])
        assert argv[1] == "-c" and argv[4] == module, f"JP-19: {command} not run as a module"
        as_module = sb._run_jxlphoto(JXLPHOTO, [command, "--version"], TIMEOUT_S)
        as_command = subprocess.run(sb._jxlphoto_command(JXLPHOTO) + [command, "--version"],
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", env=sb._jxlphoto_env(), timeout=TIMEOUT_S)
        assert as_module.returncode == as_command.returncode == 0, f"JP-19: {command} --version"
        assert as_module.stdout.split() == as_command.stdout.split(), (
            f"JP-19: {command}: {as_module.stdout!r} != {as_command.stdout!r}")
    # every item above ran this way: their bytecode is in photo-backup's own cache
    cached = {path.name.split(".")[0] for path in (check.root / "logs" / "pycache").rglob("*.pyc")}
    assert set(sb._JXLPHOTO_BACKENDS.values()) <= cached, f"JP-19: not cached: {sorted(cached)}"


# ---- the page check and the defaults (v0.14.0-rc4, jxl-photo 2.31.0-rc1) ------------------------
def _log_text(folder):
    return "".join(path.read_text(encoding="utf-8", errors="replace")
                   for path in sorted(Path(folder).rglob("*.log")))


def test_jp20_every_page_is_checked_before_it_takes_its_name(check):
    for name in ("photo16", "scan16", "photo8", "multi"):
        jxl = _jxl_of(check, name, "JP-20")
        checked = re.findall(r">Round-trip verified page (\d+) \(", _log_text(
            check.encoded[name].log_dir))
        assert sorted(int(page) for page in checked) == list(range(len(jxl))), (
            f"JP-20: {name}: {len(jxl)} JXL, pages checked against the TIFF: "
            f"{checked or 'none'} (the encode's --verify-roundtrip must decode every page "
            f"it writes and compare it with its TIFF page)")


def test_jp20_without_djxl_nothing_is_kept_unchecked(check, tmp_path):
    """A page that cannot be checked is an error with no JXL: without djxl on
    PATH (cjxl and everything else still there) the encode gives none."""
    djxl = shutil.which("djxl")
    if djxl is None:
        pytest.skip("JP-20: no djxl on PATH to take away")
    bin_dir = Path(djxl).parent
    copy = tmp_path / "libjxl without djxl"
    copy.mkdir()
    for path in bin_dir.iterdir():
        if path.is_file() and not path.name.casefold().startswith("djxl"):
            shutil.copy2(path, copy / path.name)
    env = sb._jxlphoto_env(str(tmp_path / "log"))
    env["PATH"] = os.pathsep.join([str(copy)] + [
        part for part in env.get("PATH", "").split(os.pathsep)
        if part and os.path.normcase(os.path.normpath(part)) != os.path.normcase(str(bin_dir))])
    out = tmp_path / "out"
    out.mkdir()
    run = subprocess.run(sb._jxlphoto_argv(JXLPHOTO, sb._jxlphoto_encode_args(
        str(check.tiffs["photo16"]), str(out), PROFILE)), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        timeout=TIMEOUT_S)
    made = [path.name for path in out.iterdir() if path.suffix.casefold() == ".jxl"]
    assert run.returncode != 0 and not made, (
        f"JP-20: without djxl the encode gave exit {run.returncode} and {made}: "
        f"{_tail(run)}")


def test_jp21_an_empty_jxlphoto_settings_turns_the_overlay_off(check, tmp_path):
    if not JXLPHOTO.casefold().endswith(".py"):
        pytest.skip("JP-21: checked with the scripts (the exe's overlay sits beside the exe)")
    copy = tmp_path / "jxl-photo with an overlay"
    copy.mkdir()
    for path in Path(JXLPHOTO).resolve().parent.glob("*.py"):
        shutil.copy2(path, copy / path.name)
    (copy / ".jxl_script_settings.json").write_text(json.dumps(
        {"jxl_tiff_encoder": {"WORKER_MEMORY_FRACTION": 1.2}}), encoding="utf-8")
    script = str(copy / os.path.basename(JXLPHOTO))

    def encode(label, without_setting):
        out, logs = tmp_path / f"out_{label}", tmp_path / f"log_{label}"
        out.mkdir()
        env = sb._jxlphoto_env(str(logs))
        if without_setting:
            env.pop("JXLPHOTO_SETTINGS", None)
        run = subprocess.run(sb._jxlphoto_argv(script, sb._jxlphoto_encode_args(
            str(check.tiffs["photo8"]), str(out), PROFILE)), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
            timeout=TIMEOUT_S)
        return run, (run.stdout or "") + (run.stderr or "") + _log_text(logs)

    run, said = encode("ours", False)
    assert run.returncode == 0, f"JP-21: {_tail(run)}"
    assert "settings overlay" not in said.casefold() and "WORKER_MEMORY_FRACTION" not in said, (
        "JP-21: JXLPHOTO_SETTINGS set empty did not turn jxl-photo's settings overlay off")
    run, said = encode("control", True)
    assert "WORKER_MEMORY_FRACTION" in said, (
        "JP-21: the test's overlay was not read even without JXLPHOTO_SETTINGS "
        "(is it still .jxl_script_settings.json beside the scripts?)")


# ---- the temp folder (v0.14.0-rc6, jxl-photo 2.31.0-rc3) ----
def test_jp22_a_temp_folder_with_a_percent_is_refused(check, tmp_path):
    temp = tmp_path / "te%mp"
    temp.mkdir()
    jpeg = check.jpegs["camera"]
    cases = (("JP-2", check.tiffs["photo16"],
              sb._jxlphoto_encode_args(str(check.tiffs["photo16"]), "{out}", PROFILE)),
             ("JP-15", jpeg, sb._jxlphoto_transcode_args(str(jpeg), "{out}")))
    for item, source, arguments in cases:
        before = _stat(source)
        out = tmp_path / f"out_{item}"
        out.mkdir()
        arguments = [str(out) if part == "{out}" else part for part in arguments]
        run = subprocess.run(
            sb._jxlphoto_argv(JXLPHOTO, arguments), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=sb._jxlphoto_env(str(tmp_path / "log"), str(temp)), timeout=TIMEOUT_S)
        assert run.returncode == 2, f"JP-22: {item}: exit {run.returncode}: {_tail(run)}"
        assert not list(out.rglob("*")), (
            f"JP-22: {item}: files in the output folder: {[p.name for p in out.rglob('*')]}")
        assert "%" in (run.stderr or ""), f"JP-22: {item}: stderr says nothing of the '%'"
        assert _stat(source) == before, f"JP-22: {item}: the source changed"
