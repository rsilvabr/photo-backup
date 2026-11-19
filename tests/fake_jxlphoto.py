"""A stand-in for jxl-photo in the tests (selective_backup.JXLPHOTO): the
command line photo-backup calls, and files shaped where photo-backup looks.

    fake_jxlphoto.py encode --version
    fake_jxlphoto.py encode <tiff> <folder> --mode 0 --distance D ... --verify-roundtrip
    fake_jxlphoto.py decode <folder> <folder> --mode 0 ...
    fake_jxlphoto.py transcode <jpeg> <folder> --mode 0 ... --force-transcode
    fake_jxlphoto.py transcode <jxl> <folder> --mode 0 ... --format jpeg

encode writes <stem>.jxl: the JXL container signature, an XMP box with the
jxlphoto-src and jxlphoto-srcsum tokens (the real srcsum of the TIFF's bytes)
and the class marks (jxlphoto-origin, jxlphoto-scan for a TIFF whose bytes say
SCAN), then the TIFF's bytes in a jxlc box. A TIFF whose bytes hold PAGES=<n>
becomes n files (<stem>.jxl, <stem>_page1.jxl, ...) with jxlphoto-pages,
jxlphoto-page and jxlphoto-mpg, each holding a slice of the bytes. decode puts
the slices back together as <stem>.tif. transcode keeps a JPEG's bytes in a
JXL without marks, and gives them back with --format jpeg. The environment
(set by the tests):
    FAKE_JXLPHOTO_FAIL   exit | none | srcsum | short | changed (encode),
                         exit | none | mismatch (transcode): how it fails
    FAKE_JXLPHOTO_ONLY   fail only for a file whose name holds this text
    FAKE_JXLPHOTO_DECODE_FAIL   any value: decode (and the JPEG rebuild) stop with exit 1
    FAKE_JXLPHOTO_REBUILD_WRONG  any value: a rebuilt JPEG is not the original's bytes
    FAKE_JXLPHOTO_CALLS  a folder: each call writes its arguments there (one JSON file)
    FAKE_JXLPHOTO_VERSION  what `encode --version` says (default 9.9.9; "none": no number)
"""
import hashlib
import json
import os
import re
import struct
import sys
import time

VERSION = os.environ.get("FAKE_JXLPHOTO_VERSION", "9.9.9")    # "none": no version said
SIGNATURE = b"\x00\x00\x00\x0cJXL \r\n\x87\n"


def box(kind, payload):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def xmp(tokens):
    items = "".join(f"<rdf:li>{token}</rdf:li>" for token in tokens)
    return (f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description>'
            f"<dc:subject><rdf:Bag>{items}</rdf:Bag></dc:subject>"
            f"</rdf:Description></rdf:RDF></x:xmpmeta>").encode("utf-8")


def boxes(data):
    pos = 12
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        yield kind, data[pos + 8:pos + length]
        pos += length


def encode(tiff, folder):
    data = open(tiff, "rb").read()
    srcsum = hashlib.sha256(hashlib.sha256(data).digest()).hexdigest()[:16]
    fail = os.environ.get("FAKE_JXLPHOTO_FAIL", "")
    only = os.environ.get("FAKE_JXLPHOTO_ONLY", "")
    if only and only not in os.path.basename(tiff):
        fail = ""
    if fail == "exit":
        print("ERROR: the fake jxl-photo was told to fail", file=sys.stderr)
        return 1
    if fail == "none":
        return 0
    if fail == "srcsum":
        srcsum = "0" * 16
    match = re.search(rb"PAGES=(\d+)", data)
    pages = int(match.group(1)) if match else 1
    stem = os.path.splitext(os.path.basename(tiff))[0]
    step = -(-len(data) // pages)
    written = pages - 1 if fail == "short" and pages > 1 else pages
    for page in range(written):
        name = stem if page == 0 else f"{stem}_page{page}"
        tokens = [f"jxlphoto-src:{os.path.basename(tiff).replace(' ', '_')}",
                  f"jxlphoto-srcsum:{srcsum}", "jxlphoto-depth:16",
                  # the class marks (JP-17): a TIFF whose bytes say SCAN is a film
                  # scan, 8BIT an 8-bit TIFF
                  f"jxlphoto-origin:{'tiff8' if b'8BIT' in data else 'tiff16'}"]
        if b"SCAN" in data:
            tokens.append("jxlphoto-scan:raw")
        if pages > 1:
            tokens += [f"jxlphoto-pages:{pages}", f"jxlphoto-page:{page}",
                       f"jxlphoto-mpg:{srcsum}"]
        content = (SIGNATURE + box(b"ftyp", b"jxl \x00\x00\x00\x00jxl ")
                   + box(b"xml ", xmp(tokens)) + box(b"jxlc", data[page * step:(page + 1) * step]))
        with open(os.path.join(folder, name + ".jxl"), "wb") as handle:
            handle.write(content)
    if fail == "changed":                       # the TIFF changes while it is converted
        with open(tiff, "ab") as handle:
            handle.write(b"edited")
    return 0


def decode(folder, out):
    if os.environ.get("FAKE_JXLPHOTO_DECODE_FAIL"):
        print("ERROR: the fake decoder was told to fail", file=sys.stderr)
        return 1
    parts = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".jxl"):
            continue
        data = open(os.path.join(folder, name), "rb").read()
        page, payload = 0, b""
        for kind, content in boxes(data):
            if kind == b"xml ":
                found = re.search(rb"jxlphoto-page:(\d+)", content)
                page = int(found.group(1)) if found else 0
            elif kind == b"jxlc":
                payload = content
        parts.append((page, name, payload))
    if not parts:
        print("no JXL found", file=sys.stderr)
        return 1
    parts.sort()
    stem = re.sub(r"_page\d+$", "", os.path.splitext(parts[0][1])[0])
    with open(os.path.join(out, stem + ".tif"), "wb") as handle:
        handle.write(b"".join(payload for _page, _name, payload in parts))
    return 0


def transcode(source, folder, argv):
    """A JPEG -> <stem>.jxl (its bytes in a jxlc box, no XMP: as the real one
    keeps a JPEG rebuildable) + checksums.md5; with --format jpeg, a JXL ->
    <stem>.jpg rebuilt from those bytes. FAKE_JXLPHOTO_FAIL: exit, none, or
    mismatch (a JXL whose JPEG comes back different)."""
    stem = os.path.splitext(os.path.basename(source))[0]
    if "--format" in argv:
        if os.environ.get("FAKE_JXLPHOTO_DECODE_FAIL"):
            print("ERROR: the fake decoder was told to fail", file=sys.stderr)
            return 1
        payload = b""
        for kind, content in boxes(open(source, "rb").read()):
            if kind == b"jxlc":
                payload = content
        if os.environ.get("FAKE_JXLPHOTO_REBUILD_WRONG"):
            payload += b"!"                 # a rebuild that is not the original's bytes
        with open(os.path.join(folder, stem + ".jpg"), "wb") as handle:
            handle.write(payload)
        return 0
    data = open(source, "rb").read()
    fail = os.environ.get("FAKE_JXLPHOTO_FAIL", "")
    only = os.environ.get("FAKE_JXLPHOTO_ONLY", "")
    if only and only not in os.path.basename(source):
        fail = ""
    if fail == "exit":
        print("ERROR: EncodeImageJXL() failed (the fake was told to)", file=sys.stderr)
        return 1
    if fail == "none":
        return 0
    payload = data + (b"x" if fail == "mismatch" else b"")
    with open(os.path.join(folder, stem + ".jxl"), "wb") as handle:
        handle.write(SIGNATURE + box(b"ftyp", b"jxl \x00\x00\x00\x00jxl ")
                     + box(b"jbrd", b"reconstruction") + box(b"jxlc", payload))
    with open(os.path.join(folder, "checksums.md5"), "w", encoding="utf-8") as handle:
        handle.write(f"{hashlib.md5(data).hexdigest()}  {stem}.jxl\n")
    return 0


def main(argv):
    calls = os.environ.get("FAKE_JXLPHOTO_CALLS")
    if calls:                           # a folder: one file per call (calls run at once)
        os.makedirs(calls, exist_ok=True)
        name = f"{time.time_ns():020d}_{os.getpid()}.json"
        with open(os.path.join(calls, name), "w", encoding="utf-8") as handle:
            json.dump(argv, handle)
    if len(argv) >= 2 and argv[1] == "--version":
        print("jxl-photo" if VERSION == "none" else f"jxl-photo {VERSION}")
        return 0
    if argv[0] == "encode":
        return encode(argv[1], argv[2])
    if argv[0] == "decode":
        return decode(argv[1], argv[2])
    if argv[0] == "transcode":
        return transcode(argv[1], argv[2], argv)
    print(f"unknown command {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
