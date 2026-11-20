#!/usr/bin/env python3
"""selective_backup - selective backup of a photo archive.

For every folder that holds an export folder (any name containing the
configured marker, default: export), copy the exports plus the originals of
the exported photos (RAW, camera JPEG, NX Studio TIFF, sidecars), the
Capture One settings and the NX Studio NKSC_PARAM, mirroring the folder tree
on the destination. A later run can verify that the backup still matches.

The source is strictly READ-ONLY. Nothing on the destination is ever deleted;
a changed file is reported, and with --update-changed the old copy is MOVED to
the versions folder first, never overwritten in place. Edit settings and
sidecars (AUTO_UPDATE_EXTENSIONS) are updated that way on every run.
"""

import argparse
import collections
import contextlib
import csv
import hashlib
import json
import math
import os
import re
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import (FIRST_COMPLETED, ThreadPoolExecutor, TimeoutError as _FutureTimeout,
                                wait as _wait_futures)
from dataclasses import dataclass, field, replace as _replace_fields
from datetime import datetime, timedelta

__version__ = "0.14.0-rc5"

# ---- Export folders -------------------------------------------------------
EXPORT_NAME_CONTAINS = "export"     # a folder whose name contains this (any case) is an export folder
EXPORT_NAME_EXCLUDE = ""            # ';'-separated exact folder names (any case) that are NOT export folders
EXPORT_FORMATS = "jxl>tif>jpg"      # default --export-formats (see README: 'all', 'a>b>c' fallback, 'a+b' multiple)
EXCLUDE_EXPORT_SUBFOLDERS = ""      # ';'-separated folder names skipped INSIDE export folders (e.g. "JXL_mobile;WEB")
FORMAT_GROUPS = {                   # names usable in --export-formats -> extensions (lower case)
    "jxl": (".jxl",),
    "tif": (".tif", ".tiff"),
    "jpg": (".jpg", ".jpeg"),
    "png": (".png",),
    "heic": (".heic", ".heif", ".hif"),
    "psd": (".psd", ".psb"),
    "dng": (".dng",),
}

# ---- Originals beside the export folder ------------------------------------
RAW_EXTENSIONS = (".nef", ".nrw", ".cr2", ".cr3", ".crw", ".arw", ".srf", ".sr2",
                  ".raf", ".orf", ".rw2", ".dng", ".pef", ".srw", ".3fr", ".iiq",
                  ".x3f", ".erf", ".mrw", ".kdc", ".dcr")
JPEG_EXTENSIONS = (".jpg", ".jpeg")
ANCHOR_EXTENSIONS = RAW_EXTENSIONS + (".jpg", ".jpeg", ".tif", ".tiff", ".png",
                                      ".heic", ".heif", ".hif", ".psd",
                                      ".jxl")  # files whose stem can be a photo key
SIDECAR_EXTENSIONS = (".xmp", ".dop", ".pp3")   # copied with an unselected RAW under --unselected raw
NAME_SEPARATORS = "_-. ("           # a key must be followed by one of these in a longer name
UNSELECTED = "skip"                 # default --unselected: skip | raw | raw_or_jpeg | raw_jpeg | all

# ---- jxl-photo derivatives -------------------------------------------------------
COPY_JXLPHOTO_DERIVATIVES = False   # default for --derivatives: a JXL that jxl-photo derived from a
                                    # master (an sRGB/mobile copy) is skipped when that photo's master
                                    # is copied in the same run; it can be made again from the master
DERIVATIVE_TOKEN = "jxlphoto-derived:"   # XMP dc:Relation token of a jxl-photo derivative
MASTER_TOKEN = "jxlphoto-src:"           # ... and of a master (derivatives never carry it)
XMP_SCAN_LIMIT_MB = 4               # boxes read before the codestream at most, per JXL

# ---- Editor folders ---------------------------------------------------------
CAPTUREONE_FOLDER_NAMES = "CaptureOne"       # ';'-separated (any case)
CAPTUREONE_CACHE_NAMES = "Cache"             # ';'-separated subfolders of a Capture One folder skipped by default
COPY_CAPTUREONE_CACHE = False                # default for --c1-cache
NXSTUDIO_FOLDER_NAMES = "NKSC_PARAM"         # ';'-separated (any case); always copied whole

# ---- Folders without an export ----------------------------------------------
UNEXPORTED = "skip"                 # default --unexported: skip | copy

# ---- Inside sessions ----------------------------------------------------------
SESSION_LEAVE_OUT = "*"             # ';'-separated direct subfolders of a session (a folder with an export and
                                    # an original beside it) left out with all below them, "inside sessions";
                                    # '*' = every one that is not an export, Capture One or NKSC_PARAM folder.
                                    # manifest_backup.py's LeaveOut column replaces it per mode; a modes table
                                    # without that column uses it. Any other subfolder is judged alone.

# ---- Walk ---------------------------------------------------------------------
EXCLUDE_FOLDERS = ""                # ';'-separated folder names (any case) skipped anywhere
SYSTEM_SKIP_FOLDERS = "$RECYCLE.BIN;System Volume Information"

# ---- Copy and verify ------------------------------------------------------------
VERIFY_COPY = "hash"                # default --verify-copy: none | size | hash
SPACE_MARGIN_GB = 20.0              # free space kept on the destination (default --space-margin-gb)
MTIME_TOLERANCE_S = 2.0             # FAT/exFAT store mtimes in 2 s steps
COPY_CHUNK_MB = 8
READ_AHEAD_MB = 512                 # backup: source data read ahead in RAM while the destination writes; 0 = one disk at a time
SMALL_FILE_KB = 102400              # backup: files below this are flushed to disk in batches
FLUSH_BATCH_FILES = 50              # backup: files per flush batch and per catalog commit; 1 = flush each file
FLUSH_THREADS = 8                   # backup: simultaneous flushes of a batch (measured on a USB HDD: 8 beat 1 and 32)
TEMP_SUFFIX = ".pbtmp"              # in-progress copy, next to its final name
DEST_CHECK_AFTER_ERRORS = 20        # backup: after this many failed copies in a row, test the destination
                                    # (write and remove a temp file in CATALOG_FOLDER); it fails = stop; 0 = never
CATALOG_FOLDER = "_photo_backup"    # under the destination: catalog.sqlite + versions\
PROGRESS_EVERY_S = 5.0
PROGRESS_RECENT_S = 120.0           # the progress line's "last 2 min" speed, which the ETA follows (the average
                                    # since the start reacts slowly: RAW to small JPEGs, an SMR disk slowing down)
AUTO_UPDATE = True                  # default: changed edit settings/sidecars are updated without --update-changed
AUTO_UPDATE_EXTENSIONS = (".xmp", ".dop", ".pp3", ".cos", ".comask", ".nksc")  # small files rewritten by every edit

DETECT_MOVES = True                 # default: a NEW file that is a moved/renamed catalogued file is moved inside the backup (--no-detect-moves)
ORPHANS_FOLDER = "orphans"          # under CATALOG_FOLDER: where --move-orphans puts backup files whose source is gone
BACKUP_ID_FILE = "volume.json"      # under CATALOG_FOLDER: the identity of this backup (F2)
VERIFY_MAX_GB = 0.0                 # default --max-gb for --verify full: 0 = verify everything
VERIFY_PARALLEL = True              # --verify full: read the backup copy and the source at the same time when they
                                    # are on two drives (the time of the slower disk, not the sum); False = one
                                    # after the other (two partitions of ONE hard disk)
VERIFY_COMMIT_S = 30.0              # --verify full: the catalog is written at least this often, so a verify that
                                    # is interrupted keeps what it had checked
VERIFY_QUICK_USES_PLAN = True       # --verify quick: the size and date of a source file come from the listing the
                                    # plan has just made (as the backup itself decides UP_TO_DATE), not from one
                                    # more question per file (425 000 on a cold disk). False = ask for each one:
                                    # a file listed but whose own record is refused is then SOURCE_UNREADABLE
                                    # (a --verify full reads every file and says it either way)
SUMMARY_FOLDER_LINES = 25           # lines of the per-folder table in the summary (the CSV has all)
SUMMARY_INSIDE_NAMES = 5            # subfolder names shown under "inside sessions" (the _inside.csv has all)

# ---- TIFF and JPEG as JXL in the backup (jxl-photo) ---------------------------------------
JXLPHOTO = ""                       # jxl-photo's jxl_photo.py (or jxl-photo.exe): converts a TIFF or a JPEG to
                                    # JXL in the backup and back on restore; "" = no conversion (--jxlphoto)
JXLPHOTO_MIN_VERSION = "2.31.0-rc1"  # the oldest jxl-photo that keeps photo-backup's contract
                                    # (docs/jxlphoto_contract.md: 2.31.0-rc1 checks every page it
                                    # converts, JP-20); an older one is refused, exit 2
JXLPHOTO_RESTORE_MIN_VERSION = "2.30.0"  # the oldest the restore runs to make a file back (the
                                    # decode and the JPEG rebuild did not change since): the
                                    # jxl-photo kept in an older backup still restores it
TIFF_JXL_PROFILES = {               # the manifest's Tiff column (or --tiff) names one; "copy" = the TIFF as is.
    "jxl": {"distance": 0.05,       # jxl-photo picks per TIFF: a film scan gets scan_distance, an 8-bit
            "scan_distance": 0,     # TIFF eight_bit_distance, any other TIFF distance (0 = lossless)
            "eight_bit_distance": 0},
}
TIFF_EXTENSIONS = (".tif", ".tiff") # what a profile converts (outside the export folders)
TIFF_JXL_EFFORT = 7                 # cjxl effort of the TIFF conversions
JPEG_JXL_EXTENSIONS = (".jpg", ".jpeg")  # what the Jpeg column's `jxl` (or --jpeg jxl) keeps as JXL: every one,
                                    # exports too -- a JPEG comes back byte for byte
JPEG_JXL_EFFORT = 5                 # cjxl effort of the JPEG transcodes: lossless at any effort; measured on
                                    # 60 real JPEGs, 3, 5 and 7 give the same size; 16 at a time 5 was 30% faster
                                    # than 7 (alone, the same; 9: 8x slower). A JPEG already kept as JXL is not
                                    # made again for it.
JXL_WORKERS = 0                     # files converted (and restored) at the same time, one jxl-photo or
                                    # djxl each; 0 = one per physical core (16 on a 16-core CPU: as fast as
                                    # 32, measured, with a third of the CPU left free)
JXL_TIFF_WORKERS = 0                # of them, TIFFs at the same time (a 45 MP TIFF takes a few GB of RAM
                                    # and cjxl uses every core); 0 = a quarter of the CPUs, at most one per
                                    # 4 GB of free RAM
DJXL = ""                           # libjxl's djxl: rebuilds a JPEG from its JXL for the proof and the
                                    # restore, 3x faster than through jxl-photo; "" = found on PATH,
                                    # "none" = always through jxl-photo
JXL_TIMEOUT_MIN = 30                # a conversion that takes longer is given up: the file is copied as is
JXL_WORK_DIR = ""                   # where the conversions run, each in a folder of its own, jxl-photo's
                                    # own temp files too: "" = the system's temp folder (an SSD on most
                                    # PCs). The backup's disk then gets each finished JXL from one writer,
                                    # in turn, verified. --work-dir sets it for one run (the menu
                                    # remembers one per computer: [4] Settings)
JXL_WORK_FOLDER = "pbconv"          # this tool's folder inside JXL_WORK_DIR: one folder per running backup
                                    # (one plain name; a run removes only folders that carry its mark)
KEEP_TOOLS_IN_BACKUP = True         # every real run keeps photo-backup (and JXLPHOTO's jxl-photo) in
                                    # <dest>\_photo_backup\tools\, one folder per version, never replaced
KEEP_TOOLS_EXTRA = ()               # more files or folders kept there (the .zip of photo-backup-exe and of
                                    # jxl-photo-exe): copied once each, never replaced
TOOLS_FOLDER = "tools"              # under CATALOG_FOLDER

# ---- Logs ---------------------------------------------------------------------------
LOG_DIR = os.environ.get("PHOTOBACKUP_LOG_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "Logs")
PLAN_CSV_UP_TO_DATE = False         # _plan.csv of a run that copies: also one line per UP_TO_DATE file (83 MB a
                                    # run on a 425 000-file backup). A --dry-run always lists every planned file
VERIFY_CSV_OK = False               # _verify.csv and the verify's _plan.csv: also one line per file that is OK

# ---- Categories ---------------------------------------------------------------------
CAT_EXPORT = "export"
CAT_SOURCE = "source"
CAT_UNSELECTED = "unselected"
CAT_CAPTURE_ONE = "capture_one"
CAT_NX_STUDIO = "nx_studio"
CAT_UNEXPORTED = "unexported"
CAT_ADDED = "added"
CAT_WHOLE = "whole"

ST_NEW = "NEW"
ST_CHANGED = "CHANGED"
ST_UP_TO_DATE = "UP_TO_DATE"
ST_MOVED = "MOVED"

SCHEMA_VERSION = "3"                # 2 (v0.14.0-rc2): the table of TIFFs kept as JXL ("converted");
                                    # 3 (v0.14.0-rc3): its kind (tiff / jpeg) and lossless columns


def _split_names(value):
    """Split a ';'-separated settings string into stripped, non-empty names."""
    return [part.strip() for part in str(value).split(";") if part.strip()]


def _fold_set(value):
    return {name.casefold() for name in _split_names(value)}


_SCRIPTS = ("selective_backup", "manifest_backup", "mirror_backup", "restore_backup",
            "captureone_backup", "photo_backup")


def _program_name(default="selective_backup"):
    """'mirror_backup 0.14.0-rc1': the script this process runs (the name it
    was started with, when it is one of this project's) and the version. It
    heads the summary, the console's title and the catalog's record of who
    created and last wrote it."""
    name = os.path.splitext(os.path.basename(sys.argv[0] or ""))[0] if sys.argv else ""
    return f"{name if name in _SCRIPTS else default} {__version__}"


# The commands of the Windows program (photo-backup-exe: one photo-backup.exe that
# runs these same scripts) for each script; the menu is the exe on its own.
_EXE_COMMANDS = {"photo_backup.py": "", "selective_backup.py": "backup",
                 "manifest_backup.py": "manifest", "restore_backup.py": "restore",
                 "captureone_backup.py": "captureone", "mirror_backup.py": "mirror"}


def _run_command(script):
    """How a person starts `script` on this computer, for the hints we print:
    `python photo_backup.py`, or in the Windows program `photo-backup.exe` and
    its command (`photo-backup.exe captureone`)."""
    if getattr(sys, "frozen", False):
        command = _EXE_COMMANDS.get(script, "")
        return f"{os.path.basename(sys.executable)} {command}".rstrip()
    return f"python {script}"


def _set_console_title(text):
    """Show `text` (a program and its version) in the console window's title and
    the taskbar. Windows only, and only on a real console; never an error."""
    if os.name != "nt":
        return
    try:
        if not sys.stdout.isatty():
            return
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(str(text))
    except (AttributeError, OSError, ValueError):
        pass


def _io(path):
    """Extended-length Windows path for every filesystem call."""
    path = os.path.abspath(path)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _exists(path):
    return os.path.exists(_io(path))


SOURCE_PRESENT = "present"
SOURCE_MISSING = "missing"
SOURCE_UNREADABLE = "unreadable"


def _source_stat(path):
    """(state, stat) of a source file: SOURCE_PRESENT with its stat, or
    SOURCE_MISSING / SOURCE_UNREADABLE with None.
    MISSING needs positive proof: the system answered "does not exist" AND the
    nearest folder above that does exist could be listed. Any other error --
    access denied, a drive or share that does not answer, nothing above it
    that exists (a drive not connected) -- is UNREADABLE: a source that cannot
    be reached is never a source that is gone."""
    try:
        return SOURCE_PRESENT, os.stat(_io(path), follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError:
        return SOURCE_UNREADABLE, None
    parent = os.path.abspath(path)
    while True:
        up = os.path.dirname(parent)
        if up == parent:
            return SOURCE_UNREADABLE, None
        parent = up
        try:
            # the folder itself, never what a link of that name leads to: a
            # junction whose target is not connected is then listed below and
            # fails -- unreadable, never "missing" (finding 3 of the rc1 review)
            os.stat(_io(parent), follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            return SOURCE_UNREADABLE, None
        try:
            with os.scandir(_io(parent)) as entries:
                next(entries, None)
        except OSError:
            return SOURCE_UNREADABLE, None
        return SOURCE_MISSING, None


def _source_state(path):
    """SOURCE_PRESENT, SOURCE_MISSING or SOURCE_UNREADABLE: see _source_stat."""
    return _source_stat(path)[0]


def _trailing_dot_folder(path):
    """The first part of `path` whose name ends in '.' or a space, or None.
    Windows drops that ending when it opens the path: the part is read as the
    folder without it, and what it really holds is never seen."""
    drive, rest = os.path.splitdrive(path)
    current = drive
    for part in rest.replace("/", "\\").split("\\"):
        if not part:
            current += "\\"
            continue
        current = os.path.join(current, part) if current else part
        if part not in (".", "..") and part[-1] in ". ":
            return current
    return None


def _duplicate_message(rel, kept, other):
    """The error of two planned files with one destination path (v0.12.0):
    true in both cases (S8 of the 261111 review)."""
    dotted = _trailing_dot_folder(kept) or _trailing_dot_folder(other)
    if dotted is not None:
        plain = dotted.rstrip(". ") or dotted
        return (f"ERROR duplicate destination {rel}: {dotted} ends with a dot or a space, "
                f"and Windows cannot read it apart from {plain}: what is in {dotted} is NOT "
                f"backed up (rename it); {rel} holds the file of {plain} "
                f"(read as {kept} and as {other})")
    return (f"ERROR duplicate destination {rel}: {kept} and {other} are two files with the "
            f"same destination path; only {kept} has a copy, {other} is NOT backed up")


def _under_sources(path, sources):
    """Whether `path` is under one of the (source, base) pairs of this run."""
    return any(_is_under(path, source) for source, _base in sources)


def _candidate_prefixes(stem):
    """Prefixes of `stem` that end right before a separator, shortest first,
    then `stem` itself. 'DSC_9360_ProPhoto-g22_v1' -> ['DSC', 'DSC_9360',
    'DSC_9360_ProPhoto', 'DSC_9360_ProPhoto-g22', 'DSC_9360_ProPhoto-g22_v1'].
    A prefix is never empty: '_DSC6489_x' -> ['_DSC6489', '_DSC6489_x']."""
    out = [stem[:i] for i in range(1, len(stem)) if stem[i] in NAME_SEPARATORS]
    out.append(stem)
    return out


def _folder_matches(name, patterns):
    """True when `name` matches one of `patterns`: case-insensitive, only '*'
    is a wildcard, every other character literal ('[FINAL]' is a name, not a
    character class)."""
    folded = name.casefold()
    for pattern in patterns:
        regex = re.escape(str(pattern).casefold()).replace(r"\*", ".*")
        if re.fullmatch(regex, folded):
            return True
    return False


def _original_kept(name, raw_stems, originals, all_sidecars, photo_stems=None):
    """Whether an original beside an export is copied under `originals`.
    raw_stems: casefolded stems of the RAW files of that folder. With
    all_sidecars every sidecar is kept; otherwise only the sidecars of a photo
    of the folder (photo_stems, default raw_stems). A JPEG is kept by raw_jpeg
    always and by raw_or_jpeg only when no RAW of the photo is beside it (the
    photo would otherwise have no copy). Any other photo file (a TIFF, PNG,
    HEIC or PSD of ANCHOR_EXTENSIONS) is kept by both under that same rule:
    a camera that wrote TIFF originals, a developed TIFF whose RAW is not
    there. "X (2).JPG" is another file, not a variant of X: only a RAW named
    "X (2)" pairs with it."""
    stem, ext = os.path.splitext(name)
    folded_ext = ext.casefold()
    if folded_ext in RAW_EXTENSIONS:
        return True
    if originals == "all":
        return True
    if folded_ext in SIDECAR_EXTENSIONS:
        if all_sidecars:
            return True
        stems = raw_stems if photo_stems is None else photo_stems
        return any(prefix.casefold() in stems for prefix in _candidate_prefixes(stem))
    if folded_ext in JPEG_EXTENSIONS and originals == "raw_jpeg":
        return True
    if folded_ext in ANCHOR_EXTENSIONS and originals in ("raw_or_jpeg", "raw_jpeg"):
        prefixes = [stem] if _DUP_COUNTER.search(stem) else _candidate_prefixes(stem)
        return not any(prefix.casefold() in raw_stems for prefix in prefixes)
    return False


_DUP_COUNTER = re.compile(r" \(\d+\)")   # Windows' duplicate counter: "DSC_0001 (2)"
_DUP_KEY = re.compile(r"^(.*) \(\d+\)$")


def _source_keys(selected_keys):
    """The keys whose originals are copied: the selected ones plus, for a key
    with a duplicate counter, the name without it. '0005 (2)' is often a
    rescan of the same film frame as '0005' (both were copied before v0.5.0);
    for two camera photos it only copies one original more -- never fewer."""
    keys = set(selected_keys)
    for key in selected_keys:
        match = _DUP_KEY.match(key)
        if match:
            keys.add(match.group(1))
    return keys


def _match_key(stem, anchors):
    """The photo key of an export stem: its shortest prefix that is an original
    beside the export, extended by a duplicate counter when that longer name is
    an original too ('DSC_0001 (2)_v1' -> 'dsc_0001 (2)', not 'dsc_0001',
    when both DSC_0001 and DSC_0001 (2) are there). None when nothing matches."""
    for prefix in _candidate_prefixes(stem):
        if prefix.casefold() in anchors:
            counter = _DUP_COUNTER.match(stem, len(prefix))
            if counter and stem[:counter.end()].casefold() in anchors:
                return stem[:counter.end()].casefold()
            return prefix.casefold()
    return None


_JXL_CONTAINER = b"\x00\x00\x00\x0cJXL \r\n\x87\n"


def _jxl_xmp(path):
    """The XMP box of a JXL container, as bytes, or None: a bare codestream, a
    compressed (brob) or missing XMP box, one past XMP_SCAN_LIMIT_MB, a read
    error."""
    limit = XMP_SCAN_LIMIT_MB * 1024 * 1024
    try:
        with open(_io(path), "rb") as handle:
            if handle.read(12) != _JXL_CONTAINER:
                return None
            pos = 12
            size = os.fstat(handle.fileno()).st_size
            while pos < min(size, limit):
                handle.seek(pos)
                header = handle.read(16)
                if len(header) < 8:
                    return None
                length, kind = struct.unpack(">I4s", header[:8])
                head = 8
                if length == 1:
                    if len(header) < 16:
                        return None
                    length = struct.unpack(">Q", header[8:16])[0]
                    head = 16
                elif length == 0:
                    length = size - pos
                if length < head or kind in (b"jxlc", b"jxlp"):
                    return None
                if kind == b"xml ":
                    if length > limit:
                        return None
                    handle.seek(pos + head)
                    return handle.read(length - head)
                pos += length
    except OSError:
        return None
    return None


def _jxl_role(path):
    """'derivative', 'master' or None for a .jxl, from the jxl-photo tokens in
    its XMP box. Only positive evidence counts: a bare codestream, a
    compressed (brob) or missing XMP box, both tokens, a read error -> None."""
    xmp = _jxl_xmp(path)
    if xmp is None:
        return None
    derived = DERIVATIVE_TOKEN.encode() in xmp
    master = MASTER_TOKEN.encode() in xmp
    if derived and not master:
        return "derivative"
    if master and not derived:
        return "master"
    return None


_JXLPHOTO_TOKEN = re.compile(rb"jxlphoto-[a-z0-9]+:[^\s<>\"']*", re.IGNORECASE)


def _jxl_tokens(path):
    """{name: [values]} of the jxl-photo tokens ('jxlphoto-srcsum:ab12...') in
    a JXL's XMP box; {} when the box cannot be read. Names in lower case."""
    tokens = {}
    for match in _JXLPHOTO_TOKEN.finditer(_jxl_xmp(path) or b""):
        name, _, value = match.group().decode("utf-8", "replace").partition(":")
        tokens.setdefault(name.casefold(), []).append(value)
    return tokens


def _jxlphoto_srcsum(sha256_hex):
    """The `jxlphoto-srcsum` jxl-photo writes into a JXL made from a file whose
    SHA-256 is `sha256_hex`: its content id of one source, the first 16 hex
    digits of the SHA-256 of that file's raw SHA-256 (jxl_photo_common.
    _file_content_id). A JXL that carries it was made from those very bytes."""
    return hashlib.sha256(bytes.fromhex(sha256_hex)).hexdigest()[:16]


def _parse_formats(spec):
    """Return (mode, groups); mode is 'all', 'fallback' (a>b>c) or 'multi' (a+b)."""
    text = spec.strip().casefold().replace(" ", "")
    if not text:
        raise ValueError("--export-formats is empty; valid groups: " + ", ".join(FORMAT_GROUPS))
    if text == "all":
        return ("all", ())
    if ">" in text and "+" in text:
        raise ValueError("--export-formats cannot mix '>' and '+'; valid groups: " + ", ".join(FORMAT_GROUPS))
    if ">" in text:
        mode, sep = "fallback", ">"
    else:
        mode, sep = "multi", "+"
    groups = text.split(sep)
    for group in groups:
        if not group or group not in FORMAT_GROUPS:
            raise ValueError(
                "--export-formats: unknown group "
                + repr(group) + "; valid groups: " + ", ".join(FORMAT_GROUPS))
    return (mode, tuple(groups))


def _gb(n):
    """'654.9 GB'; from 1,000 GB on '1.38 TB' (v0.14.0-rc4: easier to read)."""
    return f"{n / 1e12:.2f} TB" if n >= 1e12 else f"{n / 1e9:.1f} GB"


def _num(n):
    """'419,711': a count a person reads (v0.14.0-rc4)."""
    return f"{n:,}" if isinstance(n, int) else str(n)


SUMMARY_LABEL_WIDTH = 53                # the label column of the summary's tables


def _summary_row(label, files=None, size=None, rest=""):
    """A line of a summary table: label | files | size, then `rest`. `files` is
    a count or a short text ('2 folders')."""
    count = "" if files is None else (_num(files) if isinstance(files, int) else str(files))
    amount = "" if size is None else _gb(size)
    line = f"  {label:<{SUMMARY_LABEL_WIDTH}} {count:>9} {amount:>10}".rstrip()
    return f"{line}   {rest}" if rest else line


def _summary_header(title):
    return f"{title:<{SUMMARY_LABEL_WIDTH + 2}} {'files':>9} {'size':>10}"


def _hms(seconds):
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m"


class _Speed:
    """The speed of a long copy or verify: since the start, and over the last
    PROGRESS_RECENT_S, which the ETA follows once it is known (v0.14.0-rc4)."""

    def __init__(self):
        self.started = time.monotonic()
        self.samples = collections.deque([(self.started, 0)])   # (time, bytes done)

    def text(self, done_bytes, total_bytes):
        """'85 MB/s, last 2 min 40 MB/s  ETA 2h10m'."""
        now = time.monotonic()
        self.samples.append((now, done_bytes))
        while len(self.samples) > 2 and self.samples[1][0] <= now - PROGRESS_RECENT_S:
            self.samples.popleft()          # the oldest kept is just outside the window
        average = done_bytes / max(now - self.started, 1e-6)
        then, then_bytes = self.samples[0]
        recent = None
        if now - then >= min(30.0, PROGRESS_RECENT_S) and now > then:
            recent = (done_bytes - then_bytes) / (now - then)
        speed = recent if recent else average
        eta = (total_bytes - done_bytes) / speed if speed > 0 else 0
        text = f"{average / 1e6:.0f} MB/s"
        if recent is not None and now - self.started > PROGRESS_RECENT_S:
            minutes = PROGRESS_RECENT_S / 60
            text += f", last {minutes:g} min {recent / 1e6:.0f} MB/s"
        return f"{text}  ETA {_hms(eta)}"


def _stderr_is_console():
    try:
        return sys.stderr is not None and sys.stderr.isatty()
    except (AttributeError, ValueError, OSError):
        return False


class _Reading:
    """While a plan reads the sources -- a manifest of a whole archive lists
    over a million names, for minutes --, a line every PROGRESS_EVERY_S: the
    folders and files read so far, the time, the folder now; a manifest also
    says which line it reads. On a real console only (stderr a terminal: the
    menu, the Task Scheduler's window), never into a log, a pipe or a test."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.folders = self.files = 0
        self.started = self.said = time.monotonic()
        self.on = _stderr_is_console()

    def _say(self, text):
        print(text, file=sys.stderr, flush=True)

    def line(self, index, count, folder):
        if self.on:
            self._say(f"Reading [{index}/{count}] {folder}")

    def folder(self, path, files):
        self.folders += 1
        self.files += files
        now = time.monotonic()
        if self.on and now - self.said >= PROGRESS_EVERY_S:
            self.said = now
            elapsed = int(now - self.started)
            where = path if len(path) <= 70 else "..." + path[-67:]
            self._say(f"  read {_num(self.folders)} folders, {_num(self.files)} files "
                      f"({elapsed // 60}:{elapsed % 60:02d})  {where}")


_READING = _Reading()


def _read_chunks(handle):
    """The data of an open file, in chunks of COPY_CHUNK_MB at most. A read
    never asks for more than the file is known to still hold, plus one byte
    (the read that finds the end): Python allocates what is asked for, and
    COPY_CHUNK_MB for every 300-byte sidecar cost 2.5 ms a read. A file that
    grew while it was read is still read to its real end."""
    limit = max(1, int(COPY_CHUNK_MB * 1024 * 1024))
    try:
        left = os.fstat(handle.fileno()).st_size
    except OSError:
        left = None
    while True:
        chunk = handle.read(limit if left is None else min(limit, left + 1))
        if not chunk:
            return
        yield chunk
        if left is not None:
            left -= len(chunk)
            if left < 0:        # it grew: its size says nothing any more
                left = None


class _Cancelled(Exception):
    """A read on another thread was told to stop (the run is ending)."""


def _sha256_file(path, cancel=None):
    """sha256 of a file. `cancel` (a threading.Event) stops a read that runs
    on a thread of its own, between two chunks."""
    hasher = hashlib.sha256()
    with open(_io(path), "rb") as handle:
        for chunk in _read_chunks(handle):
            if cancel is not None and cancel.is_set():
                raise _Cancelled()
            hasher.update(chunk)
    return hasher.hexdigest()


_NS_MIN = -2 ** 63
_NS_MAX = 2 ** 63 - 1


def _db_ns(mtime_ns):
    """A file date as the catalog can hold it: SQLite integers are 64 bits,
    and a date after the year 2262 (a camera or a zip with a broken clock) does
    not fit. Stored at the limit, and compared the same way (_verify_row)."""
    return max(_NS_MIN, min(_NS_MAX, int(mtime_ns)))


def _csv_date(mtime_ns):
    """A file date for a CSV. Windows cannot convert a date before 1970 or
    after the year 3000 to local time: it is written in UTC then, and a date
    no calendar holds as its number -- one odd file must not stop the run."""
    try:
        return datetime.fromtimestamp(mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        pass
    try:
        moment = datetime(1970, 1, 1) + timedelta(microseconds=mtime_ns // 1000)
        return moment.strftime("%Y-%m-%d %H:%M:%S") + " UTC"
    except (OverflowError, ValueError):
        return f"mtime_ns {mtime_ns}"


@dataclass
class Stats:
    shoots: int = 0
    export_folders: int = 0
    export_excluded: int = 0
    export_excluded_bytes: int = 0
    export_filtered: int = 0
    export_filtered_bytes: int = 0
    unselected_skipped: int = 0
    unselected_skipped_bytes: int = 0
    inside_shoot_skipped: int = 0
    inside_shoot_skipped_bytes: int = 0
    c1_cache_skipped: int = 0
    c1_cache_skipped_bytes: int = 0
    c1_cache_links: int = 0             # Capture One cache folders that are junctions/links (not followed)
    derivatives_skipped: int = 0
    derivatives_skipped_bytes: int = 0
    manifest_skip_folders: int = 0
    manifest_skip_files: int = 0
    manifest_skip_bytes: int = 0
    only_filtered: int = 0
    only_filtered_bytes: int = 0
    skipped_links: int = 0
    scan_errors: int = 0
    add_filtered_skipped: int = 0       # inside an AddFolders folder, left out by its Originals filter
    add_filtered_skipped_bytes: int = 0
    mode_excluded: int = 0              # under a folder that matches the mode's Exclude
    mode_excluded_bytes: int = 0


@dataclass
class PlannedFile:
    src: str            # display path (no \\?\ prefix)
    rel: str            # relative to base, backslashes on Windows
    size: int
    mtime_ns: int
    category: str       # export | source | unselected | capture_one | nx_studio | unexported
    convert: str = None     # a TIFF_JXL_PROFILES name (a TIFF) or JPEG_CONVERT (a JPEG): kept as JXL,
                            # the rel ends in .tif.jxl / .jpg.jxl


@dataclass
class UnexportedFolder:
    path: str
    files: int
    nbytes: int
    c1_nbytes: int
    nx_nbytes: int
    c1_files: int = 0       # Capture One files without the cache (the cache is in Stats)
    nx_files: int = 0
    copied: bool = False    # copied whole (--unexported copy, or a + mode of the manifest)


@dataclass
class Plan:
    files: list
    stats: Stats
    unexported_folders: list
    unmatched: list
    errors: list
    warnings: list
    sources: list
    links: list = field(default_factory=list)    # links/junctions not followed (paths)
    inside: list = field(default_factory=list)   # (session, subfolder, files, bytes) left out
    skipped_folders: list = field(default_factory=list)  # skipped by name, not listed (paths)
    cache_links: list = field(default_factory=list)  # Capture One cache folders that are links (paths)


@dataclass
class FileStatus:
    planned: PlannedFile
    dst: str
    status: str
    extra_keys: tuple = ()      # a TIFF kept as JXL: the other files of its group, and a TIFF copy
                                # that the JXL replaces -- planned too (never orphans)
    old_outputs: tuple = ()     # ... the rels of the JXL group that is there now (CHANGED)
    replaces: str = None        # ... the rel of a TIFF copy of it in the backup (ours, or the same
                                # size and date as the source TIFF), retired once the JXL is in place
    settings_only: bool = False # ... CHANGED only because the settings differ: the JXL group is
                                # whole and of this very file (--no-convert keeps it, rc5)


@dataclass
class VerifyResult:
    status: str
    rel: str
    source_path: str
    detail: str = ""
    new_hash: str = None
    dest_verified: bool = False


# A relative path with a name that Windows rewrites when it opens it: one that
# ends in a dot or a space, or a device name (NUL, COM1, AUX.txt, ...). Wider
# than Windows' own rule on purpose: a match only sends _Planner._rel to relpath.
_REL_REWRITTEN = re.compile(
    r"[. ](?:\\|/|$)"
    r"|(?:^|\\|/)(?:con|prn|aux|nul|conin\$|conout\$|(?:com|lpt)[0-9¹²³])"
    r"(?:[.: ][^\\/]*)?(?:\\|/|$)",
    re.IGNORECASE)


@dataclass(frozen=True)
class FolderRules:
    add_filtered: tuple = ()    # patterns: direct subfolders of a session, filtered by `originals`
    add_whole: tuple = ()       # patterns: direct subfolders of a session, copied whole
    exclude: tuple = ()         # patterns: never walked, at any depth (not the export,
                                # Capture One and NKSC_PARAM folders of a session)
    whole: bool = False         # the whole folder, session or not
    originals: str = "raw_or_jpeg"
    skip: bool = False          # this folder stays out: nothing planned, its size reported
    only: tuple = ()            # lower-case extensions: every other planned file is left out
    leave_out: tuple = field(default_factory=lambda: tuple(_split_names(SESSION_LEAVE_OUT)))
                                # patterns: direct subfolders of a session left out ("inside
                                # sessions"); any other one is judged alone (v0.13.0)
    tiff: str = None            # a TIFF_JXL_PROFILES name (or "copy"); None = the run's --tiff
    jpeg: str = None            # "jxl" or "copy"; None = the run's --jpeg


class _Planner:
    def __init__(self, args, list_cache=None, skip_paths=frozenset(), rules=None,
                 role_cache=None):
        self.args = args
        self.list_cache = list_cache
        self.skip_paths = frozenset(os.path.normcase(path) for path in skip_paths)
        self.rules = rules
        self.leave_out = (rules if rules is not None else FolderRules()).leave_out
        self.stats = Stats()
        self.errors = []
        self.warnings = []
        self.files = []
        self.seen = {}
        self.unexported = []
        self.unmatched = []
        self.sources = []
        self.links = []             # links/junctions not followed, each once
        self.cache_links = []       # ... of them, the Capture One cache folders (moved to another disk)
        self._links_seen = set()
        self.skipped_folders = []   # SYSTEM_SKIP_FOLDERS / --exclude-folders, each once (S4)
        self._skipped_seen = set()
        self.inside = {}            # session subfolder -> [session, subfolder, files, bytes]
        self._rel_prefixes = {}     # base -> base + separator (_rel)
        self.role_cache = role_cache    # (path, size, mtime_ns) -> _jxl_role, shared by --analyze
        self.export_contains = args.export_name_contains.casefold()
        self.export_excludes = _fold_set(args.exclude_export_names)
        self.skip_folder_names = _fold_set(SYSTEM_SKIP_FOLDERS) | _fold_set(args.exclude_folders)
        self.c1_names = _fold_set(CAPTUREONE_FOLDER_NAMES)
        self.nx_names = _fold_set(NXSTUDIO_FOLDER_NAMES)
        self.cache_names = _fold_set(CAPTUREONE_CACHE_NAMES)
        self.excluded_subfolders = _fold_set(args.exclude_export_subfolders)
        self.copy_cache = bool(args.c1_cache)
        self.cache_skip_direct = frozenset() if self.copy_cache else frozenset(self.cache_names)
        self.unselected = args.unselected
        self.unexported_mode = args.unexported
        self.base = os.path.abspath(args.base) if args.base else None
        self.formats = _parse_formats(args.export_formats)
        self.copy_derivatives = bool(args.derivatives)
        self.tiff_profile = ((rules.tiff if rules is not None else None)
                             or getattr(args, "tiff", None) or "copy")
        self.tiff_extensions = {ext.casefold() for ext in TIFF_EXTENSIONS}
        self.jpeg_mode = ((rules.jpeg if rules is not None else None)
                          or getattr(args, "jpeg", None) or "copy")
        self.jpeg_extensions = {ext.casefold() for ext in JPEG_JXL_EXTENSIONS}

    def _drop_derivatives(self, chosen):
        """Remove the jxl-photo derivatives of a photo whose master is chosen
        too. Only photos with two or more chosen .jxl are read at all."""
        if self.copy_derivatives:
            return chosen
        jxl_by_key = {}
        for rec in chosen:
            if rec[1] == ".jxl" and not rec[0].startswith("?"):
                jxl_by_key.setdefault(rec[0], []).append(rec)
        dropped = set()
        for key, recs in jxl_by_key.items():
            if len(recs) < 2:
                continue
            roles = {id(rec): self._role(rec) for rec in recs}
            if "master" not in roles.values():
                continue
            for rec in recs:
                if roles[id(rec)] == "derivative":
                    dropped.add(id(rec))
                    self.stats.derivatives_skipped += 1
                    self.stats.derivatives_skipped_bytes += rec[3]
        return [rec for rec in chosen if id(rec) not in dropped]

    def _role(self, rec):
        """_jxl_role of a chosen record. --analyze plans every line once per
        mode and Originals: with `role_cache` a JXL is opened once, as long as
        its listing (size and date) is the same."""
        if self.role_cache is None:
            return _jxl_role(rec[2])
        key = (os.path.normcase(rec[2]), rec[3], rec[4])
        if key not in self.role_cache:
            self.role_cache[key] = _jxl_role(rec[2])
        return self.role_cache[key]

    def _is_export_name(self, name):
        folded = name.casefold()
        return self.export_contains in folded and folded not in self.export_excludes

    def _scan_dir(self, path):
        """(files, dirs, links) of one folder. Links and junctions are never
        followed; _list_dir counts them, once per planner, cache or not."""
        files = []
        dirs = []
        links = []
        try:
            with os.scandir(_io(path)) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink() or entry.is_junction():
                            links.append(os.path.join(path, entry.name))
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            dirs.append((entry.name, os.path.join(path, entry.name)))
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            files.append((entry.name, os.path.join(path, entry.name),
                                          stat.st_size, stat.st_mtime_ns))
                    except OSError as exc:
                        self.errors.append(f"ERROR scan {os.path.join(path, entry.name)}: {exc}")
                        self.stats.scan_errors += 1
        except OSError as exc:
            self.errors.append(f"ERROR scan {path}: {exc}")
            self.stats.scan_errors += 1
        files.sort(key=lambda item: item[0].casefold())
        dirs.sort(key=lambda item: item[0].casefold())
        links.sort(key=lambda item: item.casefold())
        return files, dirs, links

    def _list_dir(self, path):
        key = os.path.normcase(path)
        cached = self.list_cache.get(key) if self.list_cache is not None else None
        if cached is None:
            cached = self._scan_dir(path)
            _READING.folder(path, len(cached[0]))
            if self.list_cache is not None:
                self.list_cache[key] = cached
        files, dirs, links = cached
        for link in links:
            # counted here, not in _scan_dir: a listing shared with another
            # planner (the manifest) or read twice must not lose or double it
            link_key = os.path.normcase(link)
            if link_key in self._links_seen or link_key in self.skip_paths:
                continue
            self._links_seen.add(link_key)
            if self._is_cache_link(link):
                # the cache this run leaves out anyway, moved to another disk:
                # its own line, so that "links not followed" means a folder
                # that is NOT backed up (O2 of the 261112 sweep: 429 on G:)
                self.cache_links.append(link)
                self.stats.c1_cache_links += 1
                continue
            self.links.append(link)
            self.stats.skipped_links += 1
        if self.skip_paths:
            dirs = [item for item in dirs
                    if os.path.normcase(item[1]) not in self.skip_paths]
        # skipped by its name everywhere, also inside an AddFolders folder, an
        # export or a Capture One folder: every walk lists through here (R2)
        dirs = [item for item in dirs if not self._skipped_by_name(*item)]
        return files, dirs

    def _is_cache_link(self, link):
        """A link or junction that IS a Capture One cache folder (named in
        CAPTUREONE_CACHE_NAMES, right inside a Capture One folder) in a run
        that does not copy the cache. With --c1-cache it stays a plain link:
        what is behind it is not copied, and the summary says so."""
        return (not self.copy_cache
                and os.path.basename(link).casefold() in self.cache_names
                and os.path.basename(os.path.dirname(link)).casefold() in self.c1_names)

    def _skipped_by_name(self, name, dpath):
        """True for a folder of SYSTEM_SKIP_FOLDERS or --exclude-folders, at any
        depth and in every mode (_list_dir leaves it out). It is not listed at
        all (a System Volume Information cannot be read, and a folder may be
        excluded because it gives errors): its files are not counted one by
        one, the folder itself is named, once per planner."""
        if name.casefold() not in self.skip_folder_names:
            return False
        key = os.path.normcase(dpath)
        if key not in self._skipped_seen:
            self._skipped_seen.add(key)
            self.skipped_folders.append(dpath)
        return True

    def _rel(self, path, base):
        """`path` relative to `base`. Every listed path starts with its source,
        which is under the base: cutting that prefix gives what os.path.relpath
        gives, at a fraction of its cost (28% of the planning time). relpath
        also reads a name as Windows will open it -- 'Trip.' is 'Trip', which
        is how two folders with one destination are found (S8) -- so any name
        Windows rewrites, and a --base typed in another case, still go
        through it."""
        prefix = self._rel_prefixes.get(base)
        if prefix is None:
            prefix = self._rel_prefixes[base] = base.rstrip("\\/") + os.sep
        if path.startswith(prefix):
            rest = path[len(prefix):]
            if rest and not _REL_REWRITTEN.search(rest):
                return rest
        try:
            return os.path.relpath(path, base)
        except ValueError as exc:
            # A file named NUL is read by Windows as the device '\\.\NUL': it
            # cannot be opened by its name. One such file stopped the whole
            # plan with a traceback (T12 of the 261112 sweep); it is an error
            # of the plan now, and _add leaves it out.
            self.errors.append(f"ERROR name {path}: Windows opens this name as a device, "
                               f"not as a file ({exc}); NOT backed up (rename it)")
            self.stats.scan_errors += 1
            return None

    def _add(self, planned):
        if planned.rel is None:         # its name cannot be used (_rel said why)
            return
        ext = os.path.splitext(planned.src)[1].casefold()
        if (self.tiff_profile != "copy" and planned.category != CAT_EXPORT
                and ext in self.tiff_extensions
                and not any(self._is_export_name(part)
                            for part in planned.rel.replace("/", "\\").split("\\")[:-1])):
            # kept as JXL in the backup: the TIFF's own name, then .jxl (v0.14.0-rc2);
            # an export stays a TIFF, also one copied with its whole folder (mode 6)
            planned.rel = planned.rel + ".jxl"
            planned.convert = self.tiff_profile
        elif self.jpeg_mode == "jxl" and ext in self.jpeg_extensions:
            # a JPEG -- exports too: it comes back byte for byte (v0.14.0-rc3)
            planned.rel = planned.rel + ".jxl"
            planned.convert = JPEG_CONVERT
        key = planned.rel.casefold()
        if key in self.seen:
            kept = self.seen[key].src
            if os.path.normcase(kept) == os.path.normcase(planned.src):
                self.warnings.append(f"WARNING duplicate {planned.rel} (kept {kept})")
            else:
                self.errors.append(_duplicate_message(planned.rel, kept, planned.src))
            return
        self.seen[key] = planned
        self.files.append(planned)

    def _tree_usage(self, root):
        count = 0
        total = 0
        stack = [root]
        while stack:
            path = stack.pop()
            files, dirs = self._list_dir(path)
            for _, _, size, _ in files:
                count += 1
                total += size
            for _, dpath in dirs:
                stack.append(dpath)
        return count, total

    def _split_usage(self, root, skip_direct=frozenset()):
        """(files, bytes) under `root` without its direct subfolders named in
        `skip_direct`, then (files, bytes) of those subfolders."""
        count = total = skipped = skipped_bytes = 0
        stack = [(root, True)]
        while stack:
            path, top = stack.pop()
            files, dirs = self._list_dir(path)
            for _, _, size, _ in files:
                count += 1
                total += size
            for name, dpath in dirs:
                if top and name.casefold() in skip_direct:
                    files_in, bytes_in = self._tree_usage(dpath)
                    skipped += files_in
                    skipped_bytes += bytes_in
                    continue
                stack.append((dpath, False))
        return count, total, skipped, skipped_bytes

    def _excluded(self, name, dpath):
        """True when the folder matches the mode's Exclude: it is never walked,
        and what it holds is counted as excluded by the mode."""
        if self.rules is None or not _folder_matches(name, self.rules.exclude):
            return False
        count, nbytes = self._tree_usage(dpath)
        self.stats.mode_excluded += count
        self.stats.mode_excluded_bytes += nbytes
        return True

    def _add_tree(self, root, base, category, skip_direct=frozenset()):
        stack = [(root, True)]
        while stack:
            path, top = stack.pop()
            files, dirs = self._list_dir(path)
            for name, dpath in dirs:
                if self._excluded(name, dpath):
                    continue
                if top and name.casefold() in skip_direct:
                    count, nbytes = self._tree_usage(dpath)
                    self.stats.c1_cache_skipped += count
                    self.stats.c1_cache_skipped_bytes += nbytes
                    continue
                stack.append((dpath, False))
            for _, fpath, size, mtime_ns in files:
                self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, category))

    def _files_under(self, root):
        """(path, size, mtime_ns) of every file under `root`."""
        stack = [root]
        while stack:
            files, dirs = self._list_dir(stack.pop())
            for _, fpath, size, mtime_ns in files:
                yield fpath, size, mtime_ns
            stack.extend(dpath for _, dpath in dirs)

    def _filter_keeps(self, roots, originals):
        """What v0.12.0 copied of these export folders when their session is
        inside an AddFolders folder: its walk did not know they were exports and
        kept what the Originals filter keeps, folder by folder, plus the Capture
        One (without its cache) and NKSC_PARAM folders whole, leaving out the
        mode's Exclude. Normcased paths: the session copies them too (M1 of the
        261111 review), so adding a folder never copies less than v0.12.0."""
        keep = set()
        stack = [(root, False, False) for root in roots]    # path, inside Capture One, whole
        while stack:
            path, in_c1, whole = stack.pop()
            files, dirs = self._list_dir(path)
            raw_stems = {os.path.splitext(name)[0].casefold()
                         for name, _, _, _ in files
                         if os.path.splitext(name)[1].casefold() in RAW_EXTENSIONS}
            for name, fpath, _size, _mtime_ns in files:
                if whole or _original_kept(name, raw_stems, originals, all_sidecars=True):
                    keep.add(os.path.normcase(fpath))
            for name, dpath in dirs:
                folded = name.casefold()
                if self.rules is not None and _folder_matches(name, self.rules.exclude):
                    continue
                if in_c1 and folded in self.cache_names and not self.copy_cache:
                    continue                # the cache _collect_export skips (and counts)
                is_c1 = folded in self.c1_names
                stack.append((dpath, in_c1 or is_c1, whole or is_c1 or folded in self.nx_names))
        return keep

    def _collect_export(self, root, collected, keep=frozenset(), rescued=None):
        """Every file of an export folder into `collected`. A subfolder in
        --exclude-export-subfolders is counted, except its files in `keep`,
        which go to `rescued` (see _filter_keeps)."""
        stack = [(root, False)]
        while stack:
            path, in_c1 = stack.pop()
            files, dirs = self._list_dir(path)
            for name, dpath in dirs:
                folded = name.casefold()
                if folded in self.excluded_subfolders:
                    for fpath, size, mtime_ns in self._files_under(dpath):
                        if rescued is not None and os.path.normcase(fpath) in keep:
                            rescued.append((fpath, size, mtime_ns))
                        else:
                            self.stats.export_excluded += 1
                            self.stats.export_excluded_bytes += size
                    continue
                if in_c1 and folded in self.cache_names and not self.copy_cache:
                    count, nbytes = self._tree_usage(dpath)
                    self.stats.c1_cache_skipped += count
                    self.stats.c1_cache_skipped_bytes += nbytes
                    continue
                stack.append((dpath, in_c1 or folded in self.c1_names))
            for _, fpath, size, mtime_ns in files:
                collected.append((fpath, size, mtime_ns))

    def _stem_matches(self, stem, keys):
        for prefix in _candidate_prefixes(stem):
            if prefix.casefold() in keys:
                return True
        return False

    def _apply_formats(self, records):
        mode, groups = self.formats
        filtered = []
        if mode == "all":
            return list(records)
        if mode == "multi":
            extensions = set()
            for group in groups:
                extensions.update(FORMAT_GROUPS[group])
            chosen = [rec for rec in records if rec[1] in extensions]
            filtered = [rec for rec in records if rec[1] not in extensions]
        else:
            by_key = {}
            order = []
            for rec in records:
                if rec[0] not in by_key:
                    by_key[rec[0]] = []
                    order.append(rec[0])
                by_key[rec[0]].append(rec)
            chosen = []
            for key in order:
                group_records = by_key[key]
                for group in groups:
                    extensions = FORMAT_GROUPS[group]
                    selected = [rec for rec in group_records if rec[1] in extensions]
                    if selected:
                        chosen.extend(selected)
                        filtered.extend(rec for rec in group_records if rec[1] not in extensions)
                        break
                else:
                    filtered.extend(group_records)
        self.stats.export_filtered += len(filtered)
        self.stats.export_filtered_bytes += sum(rec[3] for rec in filtered)
        return chosen

    def _unselected_kept(self, name, stem, folded_ext, raw_stems, photo_stems,
                         unselected_raw_stems):
        """Whether an original of a photo that was not exported is copied under --unselected."""
        if self.unselected == "raw":
            if folded_ext in RAW_EXTENSIONS:
                return True
            return (folded_ext in SIDECAR_EXTENSIONS
                    and any(prefix.casefold() in unselected_raw_stems
                            for prefix in _candidate_prefixes(stem)))
        if self.unselected in ("raw_or_jpeg", "raw_jpeg"):
            return _original_kept(name, raw_stems, self.unselected, all_sidecars=False,
                                  photo_stems=photo_stems)
        return self.unselected == "all"

    def _session(self, path, base, files, exports, c1_dirs, nx_dirs, added=None):
        """A folder with an export folder. `added`: the Originals filter of the
        AddFolders folder this session is in (None outside one). There, a file
        the session leaves out -- a loose file, or a file of its export folders
        (another format, a derivative, an excluded export subfolder) -- is still
        copied when that filter keeps it, as v0.12.0 did: a session inside an
        added folder never copies less than either rule."""
        self.stats.shoots += 1
        self.stats.export_folders += len(exports)
        anchors = set()
        for name, _, _, _ in files:
            stem, ext = os.path.splitext(name)
            if ext.casefold() in ANCHOR_EXTENSIONS:
                anchors.add(stem.casefold())
        keep = (self._filter_keeps([dpath for _, dpath in exports], added)
                if added is not None else frozenset())
        rescued = []
        collected = []
        for _, dpath in exports:
            self._collect_export(dpath, collected, keep, rescued)
        records = []
        for fpath, size, mtime_ns in collected:
            stem, ext = os.path.splitext(os.path.basename(fpath))
            key = _match_key(stem, anchors)
            if key is None:
                key = "?" + stem.casefold()
                self.unmatched.append((path, fpath))
            records.append((key, ext.casefold(), fpath, size, mtime_ns))
        formatted = self._apply_formats(records)
        chosen = self._drop_derivatives(formatted)
        if keep:
            chosen_ids = {id(rec) for rec in chosen}
            formatted_ids = {id(rec) for rec in formatted}
            for rec in records:
                if id(rec) in chosen_ids or os.path.normcase(rec[2]) not in keep:
                    continue
                # left out as an export, kept by the AddFolders filter: copied,
                # so it leaves the count that _apply_formats or _drop_derivatives made
                if id(rec) in formatted_ids:
                    self.stats.derivatives_skipped -= 1
                    self.stats.derivatives_skipped_bytes -= rec[3]
                else:
                    self.stats.export_filtered -= 1
                    self.stats.export_filtered_bytes -= rec[3]
                rescued.append((rec[2], rec[3], rec[4]))
        selected_keys = set()
        for key, _, fpath, size, mtime_ns in chosen:
            self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, CAT_EXPORT))
            if not key.startswith("?"):
                selected_keys.add(key)
        for fpath, size, mtime_ns in rescued:
            self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, CAT_ADDED))
        selected_keys = _source_keys(selected_keys)
        unselected_raw_stems = set()
        raw_stems = set()
        photo_stems = set()
        for name, _, _, _ in files:
            stem, ext = os.path.splitext(name)
            if ext.casefold() in ANCHOR_EXTENSIONS:     # a JPEG, TIFF, PNG, HEIC or PSD photo
                photo_stems.add(stem.casefold())
            if ext.casefold() in RAW_EXTENSIONS:
                raw_stems.add(stem.casefold())
                if not self._stem_matches(stem, selected_keys):
                    unselected_raw_stems.add(stem.casefold())
        photo_stems |= raw_stems
        for name, fpath, size, mtime_ns in files:
            stem, ext = os.path.splitext(name)
            folded_ext = ext.casefold()
            if self._stem_matches(stem, selected_keys):
                self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, CAT_SOURCE))
                continue
            if self._unselected_kept(name, stem, folded_ext, raw_stems, photo_stems,
                                     unselected_raw_stems):
                self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, CAT_UNSELECTED))
            elif added is not None and _original_kept(name, raw_stems, added, all_sidecars=True):
                self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, CAT_ADDED))
            elif added is not None:
                self.stats.add_filtered_skipped += 1
                self.stats.add_filtered_skipped_bytes += size
            else:
                self.stats.unselected_skipped += 1
                self.stats.unselected_skipped_bytes += size
        for _, dpath in c1_dirs:
            self._add_tree(dpath, base, CAT_CAPTURE_ONE, self.cache_skip_direct)
        for _, dpath in nx_dirs:
            self._add_tree(dpath, base, CAT_NX_STUDIO)
        # A folder with no original beside its export (G:\2017 holding
        # 171230_Lightroom_Export, or a drive root) is not a shoot: its
        # subfolders must keep their own classification.
        return bool(anchors)

    def _added_folder(self, path, base, files, c1_dirs, nx_dirs, inside, originals):
        """A folder without an export inside an AddFolders folder: its files
        go through the Originals filter, and what it leaves out is counted.
        Not inside a session (an added folder of a folder with no original
        beside its export), --unexported copy still copies it whole: adding it
        never copies less than walking it as a folder without an export."""
        if inside is None and self.unexported_mode == "copy":
            self._unexported_folder(path, base, files, c1_dirs, nx_dirs)
            return
        raw_stems = {os.path.splitext(name)[0].casefold()
                     for name, _, _, _ in files
                     if os.path.splitext(name)[1].casefold() in RAW_EXTENSIONS}
        for name, fpath, size, mtime_ns in files:
            if _original_kept(name, raw_stems, originals, all_sidecars=True):
                self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, CAT_ADDED))
            else:
                self.stats.add_filtered_skipped += 1
                self.stats.add_filtered_skipped_bytes += size
        for _, dpath in c1_dirs:
            self._add_tree(dpath, base, CAT_ADDED, self.cache_skip_direct)
        for _, dpath in nx_dirs:
            self._add_tree(dpath, base, CAT_ADDED)

    def _inside_folder(self, inside, files, editor_dirs):
        """A folder inside a subfolder a session leaves out (LeaveOut), not
        copied: its files, and those of its Capture One and NKSC_PARAM folders,
        count as inside sessions, under that subfolder (_inside.csv)."""
        count = len(files)
        nbytes = sum(item[2] for item in files)
        for _, dpath in editor_dirs:
            files_in, bytes_in = self._tree_usage(dpath)
            count += files_in
            nbytes += bytes_in
        if not count:
            return
        self.stats.inside_shoot_skipped += count
        self.stats.inside_shoot_skipped_bytes += nbytes
        session, subfolder = inside
        entry = self.inside.setdefault(os.path.normcase(subfolder), [session, subfolder, 0, 0])
        entry[2] += count
        entry[3] += nbytes

    def _unexported_folder(self, path, base, files, c1_dirs, nx_dirs):
        """A folder without an export, not inside a session: listed, and copied
        with --unexported copy -- its Capture One and NKSC_PARAM folders too,
        also when it has no file of its own."""
        if not (files or c1_dirs or nx_dirs):
            return
        c1_files = c1_nbytes = cache_files = cache_bytes = 0
        for _, dpath in c1_dirs:
            count, nbytes, skipped, skipped_bytes = self._split_usage(dpath, self.cache_skip_direct)
            c1_files += count
            c1_nbytes += nbytes
            cache_files += skipped
            cache_bytes += skipped_bytes
        nx_files = nx_nbytes = 0
        for _, dpath in nx_dirs:
            count, nbytes = self._tree_usage(dpath)
            nx_files += count
            nx_nbytes += nbytes
        self.unexported.append(UnexportedFolder(
            path, len(files), sum(item[2] for item in files), c1_nbytes, nx_nbytes,
            c1_files, nx_files, self.unexported_mode == "copy"))
        if self.unexported_mode == "copy":
            for _, fpath, size, mtime_ns in files:
                self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns,
                                      CAT_UNEXPORTED))
            for _, dpath in c1_dirs:        # counts its cache itself
                self._add_tree(dpath, base, CAT_CAPTURE_ONE, self.cache_skip_direct)
            for _, dpath in nx_dirs:
                self._add_tree(dpath, base, CAT_NX_STUDIO)
        else:
            self.stats.c1_cache_skipped += cache_files
            self.stats.c1_cache_skipped_bytes += cache_bytes

    def _add_whole_row(self, root, base):
        stack = [root]
        while stack:
            path = stack.pop()
            files, dirs = self._list_dir(path)
            for name, dpath in dirs:
                folded = name.casefold()
                if self._excluded(name, dpath):
                    continue
                if folded in self.c1_names:
                    self._add_tree(dpath, base, CAT_WHOLE, self.cache_skip_direct)
                else:
                    stack.append(dpath)
            for _, fpath, size, mtime_ns in files:
                self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns,
                                      CAT_WHOLE))

    def build(self):
        """Walk every source. A stack item is (path, base, inside, added):
        inside = (session, its direct subfolder) once the walk is inside a
        subfolder a session leaves out (None outside); added = the Originals
        filter once it is inside an AddFolders folder (None outside).

        A session leaves out only its direct subfolders that match `leave_out`
        (v0.13.0). Any other one keeps the `inside` of the session, as the
        subfolder of a folder with an export and no original beside it always
        did: judged alone, a session if it holds an export, a folder without an
        export if not -- copied whole with --unexported copy, listed if not."""
        stack = []
        whole = self.rules is not None and self.rules.whole
        for source in self.args.source:
            src = os.path.abspath(source)
            base = self.base
            if base is None:
                base = os.path.splitdrive(src)[0] + os.sep
            self.sources.append((src, base))
            if self.rules is not None and self.rules.skip:
                count, nbytes = self._tree_usage(src)
                self.stats.manifest_skip_folders += 1
                self.stats.manifest_skip_files += count
                self.stats.manifest_skip_bytes += nbytes
            elif whole:
                self._add_whole_row(src, base)
            else:
                stack.append((src, base, None, None))
        stack.reverse()
        while stack:
            path, base, inside, added = stack.pop()
            files, dirs = self._list_dir(path)
            exports = [(name, dpath) for name, dpath in dirs if self._is_export_name(name)]
            c1_dirs = [(name, dpath) for name, dpath in dirs if name.casefold() in self.c1_names]
            nx_dirs = [(name, dpath) for name, dpath in dirs if name.casefold() in self.nx_names]
            classified = {os.path.normcase(dpath) for _, dpath in exports + c1_dirs + nx_dirs}
            others = [(name, dpath) for name, dpath in dirs
                      if os.path.normcase(dpath) not in classified
                      and not self._excluded(name, dpath)]
            if exports:
                has_originals = self._session(path, base, files, exports, c1_dirs, nx_dirs, added)
                for name, dpath in others:
                    left_out = has_originals and _folder_matches(name, self.leave_out)
                    child_inside = (path, dpath) if left_out else inside
                    if self.rules is not None and _folder_matches(name, self.rules.add_whole):
                        self._add_tree(dpath, base, CAT_ADDED, self.cache_skip_direct)
                    elif self.rules is not None and _folder_matches(name, self.rules.add_filtered):
                        stack.append((dpath, base, child_inside, self.rules.originals))
                    else:
                        stack.append((dpath, base, child_inside, added))
            elif added is not None:
                self._added_folder(path, base, files, c1_dirs, nx_dirs, inside, added)
                for _, dpath in others:
                    stack.append((dpath, base, inside, added))
            elif inside is not None:
                self._inside_folder(inside, files, c1_dirs + nx_dirs)
                for _, dpath in others:
                    stack.append((dpath, base, inside, None))
            else:
                self._unexported_folder(path, base, files, c1_dirs, nx_dirs)
                for _, dpath in others:
                    stack.append((dpath, base, None, None))
        if self.rules is not None and self.rules.only:
            self._apply_only()

    def inside_rows(self):
        """(session, subfolder name, files, bytes) of every session subfolder left out."""
        return [(session, os.path.basename(subfolder), count, nbytes)
                for session, subfolder, count, nbytes in self.inside.values()]

    def _apply_only(self):
        kept = []
        for item in self.files:
            if os.path.splitext(item.src)[1].casefold() in self.rules.only:
                kept.append(item)
            else:
                self.stats.only_filtered += 1
                self.stats.only_filtered_bytes += item.size
        self.files = kept


def plan(args):
    _READING.reset()
    planner = _Planner(args)
    planner.build()
    return Plan(
        files=planner.files,
        stats=planner.stats,
        unexported_folders=planner.unexported,
        unmatched=planner.unmatched,
        errors=planner.errors,
        warnings=planner.warnings,
        sources=list(planner.sources),
        links=list(planner.links),
        inside=planner.inside_rows(),
        skipped_folders=list(planner.skipped_folders),
        cache_links=list(planner.cache_links),
    )


def _is_under(path, root):
    folded_path = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
    folded_root = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
    if folded_path == folded_root:
        return True
    return folded_path.startswith(folded_root + os.sep)


def _real(path):
    """Where `path` really is: a junction, a symbolic link or a subst letter
    on the way is followed (os.path.realpath; a part that does not exist yet
    is kept as typed). Without the \\\\?\\ prefix, so that it compares with a
    typed path."""
    try:
        real = os.path.realpath(os.path.abspath(path))
    except (OSError, ValueError):
        return os.path.abspath(path)
    if real.startswith("\\\\?\\UNC\\"):
        return "\\\\" + real[len("\\\\?\\UNC\\"):]
    if real.startswith("\\\\?\\"):
        return real[len("\\\\?\\"):]
    return real


def _overlaps(first, second):
    """Whether one of two folders is inside the other (or both are one), as
    typed or where they really are: a destination reached through a junction
    or a subst letter that lands inside a source is caught too. For the safety
    checks only (destination x source, restore target x backup); _is_under
    stays for the paths of the catalog."""
    if _is_under(first, second) or _is_under(second, first):
        return True
    real_first, real_second = _real(first), _real(second)
    return _is_under(real_first, real_second) or _is_under(real_second, real_first)


def _unsafe_rel(rel):
    """Why a catalogued relative path would lead outside the backup, or None:
    empty, starting at a root, with a drive or a stream (':'), or with a '.'
    or '..' part. A path this tool plans never has one; only a damaged or a
    forged catalog does."""
    if not isinstance(rel, str):
        return "a path that is not text"
    if not rel.strip():
        return "an empty path"
    if ":" in rel:
        return "a drive or a stream (':')"
    if rel[0] in "\\/":
        return "a path that starts at a root"
    if any(part.rstrip(" ") in (".", "..") for part in re.split(r"[\\/]", rel)):
        return "a '.' or '..' part"
    return None


def _tiff_profile_errors():
    """What is wrong in TIFF_JXL_PROFILES, JXL_WORKERS or JXL_TIMEOUT_MIN."""
    errors = []
    if not isinstance(TIFF_JXL_PROFILES, dict):
        return ["TIFF_JXL_PROFILES must be a dict of profiles"]
    for name, profile in TIFF_JXL_PROFILES.items():
        if (not isinstance(name, str) or not name.strip()
                or name.casefold() in ("copy", JPEG_CONVERT)):
            errors.append(f"TIFF_JXL_PROFILES: {name!r} cannot be a profile name")
            continue
        for key in ("distance", "scan_distance", "eight_bit_distance"):
            value = profile.get(key) if isinstance(profile, dict) else None
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                errors.append(f"TIFF_JXL_PROFILES[{name!r}][{key!r}] must be a number, "
                              f"0 or more (it is {value!r})")
    for name in ("JXL_WORKERS", "JXL_TIFF_WORKERS"):
        value = globals()[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append(f"{name} must be a whole number, 0 (automatic) or more (it is {value!r})")
    value = JXL_TIMEOUT_MIN
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        errors.append(f"JXL_TIMEOUT_MIN must be a whole number, 1 or more (it is {value!r})")
    if not isinstance(DJXL, str):
        errors.append(f"DJXL must be a path, \"\" or \"none\" (it is {DJXL!r})")
    for name in ("TIFF_JXL_EFFORT", "JPEG_JXL_EFFORT"):
        value = globals()[name]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
            errors.append(f"{name} must be a whole number from 1 to 10 (it is {value!r})")
    return errors


def _validate_usage(args):
    errors = []
    try:
        _parse_formats(args.export_formats)
    except ValueError as exc:
        errors.append(str(exc))
    if args.verify and args.dry_run:
        errors.append("--verify cannot be combined with --dry-run")
    if args.verify and args.update_changed:
        errors.append("--verify cannot be combined with --update-changed")
    if args.verify and args.move_orphans:
        errors.append("--move-orphans cannot be combined with --verify")
    if args.max_gb < 0:
        errors.append("--max-gb cannot be negative")
    elif not math.isfinite(args.max_gb):
        errors.append("--max-gb must be a number, 0 or more")
    # A margin that is not a number stopped the run with a traceback, and a
    # negative one turned "not enough space" into "fits" (T8 of the 261112 sweep)
    if not math.isfinite(args.space_margin_gb) or args.space_margin_gb < 0:
        errors.append("--space-margin-gb must be a number, 0 or more")
    errors += _tiff_profile_errors()
    asks = [f"--tiff {args.tiff}"] if getattr(args, "tiff", "copy") != "copy" else []
    asks += ["--jpeg jxl"] if getattr(args, "jpeg", "copy") == "jxl" else []
    if getattr(args, "no_convert", False):
        asks = []                       # no jxl-photo, no djxl, no work folder needed
    if asks and not args.verify:
        problem = _jxlphoto_problem(getattr(args, "jxlphoto", None) or JXLPHOTO)
        if problem:
            errors.append(f"{' and '.join(asks)}: {problem}")
        elif getattr(args, "tiff", "copy") != "copy" and _djxl_on_path() is None:
            errors.append(f"--tiff {args.tiff}: {DJXL_MISSING}")
    sources = [os.path.abspath(source) for source in args.source]
    if asks and not args.verify:
        errors += _work_dir_errors(sources, os.path.abspath(args.dest),
                                   getattr(args, "work_dir", None))
    for source in sources:
        if not os.path.isdir(_io(source)):
            errors.append(f"source does not exist or is not a directory: {source}")
    dest = os.path.abspath(args.dest)
    explicit_base = os.path.abspath(args.base) if args.base else None
    for source in sources:
        base = explicit_base
        if base is None:
            base = os.path.splitdrive(source)[0] + os.sep
        if base and not _is_under(source, base):
            errors.append(f"source is outside --base: {source} (base {base})")
    for index, source in enumerate(sources):
        for other in sources[index + 1:]:
            if _is_under(source, other) or _is_under(other, source):
                errors.append(f"sources overlap: {source} and {other}")
    for source in sources:
        # also where the two really are: a junction or a subst letter that
        # leads the destination into a source (T4 of the 261112 sweep)
        if _overlaps(dest, source):
            errors.append(f"destination overlaps source: dest {dest}, source {source}"
                          + _really(dest, source))
    return errors


def _really(*paths):
    """' (really: ...)' for the paths a junction, a link or a subst letter
    leads somewhere else, '' when every one is where it says."""
    moved = [f"{path} -> {_real(path)}" for path in paths
             if os.path.normcase(_real(path)).rstrip("\\/")
             != os.path.normcase(os.path.abspath(path)).rstrip("\\/")]
    return f" (really: {'; '.join(moved)})" if moved else ""


def _status_of(planned, dst, size, mtime_ns):
    """UP_TO_DATE or CHANGED for a destination file of this size and date."""
    tolerance = int(MTIME_TOLERANCE_S * 1_000_000_000)
    if size == planned.size and abs(mtime_ns - planned.mtime_ns) <= tolerance:
        return FileStatus(planned, dst, ST_UP_TO_DATE)
    return FileStatus(planned, dst, ST_CHANGED)


def _file_status(planned, dest):
    dst = os.path.join(dest, planned.rel)
    try:
        stat = os.stat(_io(dst), follow_symlinks=False)
    except OSError:
        return FileStatus(planned, dst, ST_NEW)
    return _status_of(planned, dst, stat.st_size, stat.st_mtime_ns)


def _list_dest_folder(folder):
    """{name: (size, mtime_ns)} of the files of one destination folder, as its
    listing gives them; None when the folder does not exist (then nothing in
    it does). A folder that cannot be listed, or only in part, gives what was
    read: the caller asks the system for every name that is not there."""
    listed = {}
    try:
        with os.scandir(_io(folder)) as entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False):
                        stat = entry.stat(follow_symlinks=False)
                        listed[entry.name] = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    continue
    except FileNotFoundError:
        return None
    except OSError:
        pass
    return listed


def _profile_settings(name):
    """'jxl: d=0.05 scan=0 8bit=0 e=7' (a TIFF), 'jpeg: lossless e=7' (a JPEG):
    what a file kept as JXL was made with; one made with other settings is made
    again."""
    if name == JPEG_CONVERT:
        return f"{JPEG_CONVERT}: lossless e={JPEG_JXL_EFFORT}"
    profile = TIFF_JXL_PROFILES[name]
    return (f"{name}: d={profile['distance']} scan={profile['scan_distance']} "
            f"8bit={profile['eight_bit_distance']} e={TIFF_JXL_EFFORT}")


def _listed_or_stat(path, listed):
    """(size, mtime_ns) of a destination file from its folder's listing (only
    a name it holds exactly), else from the system; None when it is not there
    or cannot be read."""
    if listed is not None:
        hit = listed.get(os.path.basename(path))
        if hit is not None:
            return hit
    try:
        stat = os.stat(_io(path), follow_symlinks=False)
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _convert_status(planned, dst, dest, listed, groups, catalog_rows):
    """The state of a TIFF kept as JXL (planned.convert): from its JXL group in
    the catalog -- a JXL never has the TIFF's size, so no file of the planned
    name can say UP_TO_DATE by itself. A TIFF copy of it in the backup (ours,
    or the same size and date as the source TIFF: made by another program) is
    retired once the JXL is in place."""
    tolerance = int(MTIME_TOLERANCE_S * 1_000_000_000)
    rows = catalog_rows or {}
    tiff_rel = planned.rel[:-len(".jxl")]
    source_key = tiff_rel.casefold()
    group = (groups or {}).get(source_key, [])
    extra = [item.key for item in group if item.key != planned.rel.casefold()]
    replaces = None
    copy_stat = _listed_or_stat(os.path.join(dest, tiff_rel), listed)
    if copy_stat is not None:
        size, mtime_ns = copy_stat
        row = rows.get(source_key)
        same_as_source = size == planned.size and abs(mtime_ns - planned.mtime_ns) <= tolerance
        ours = (row is not None and size == row.size
                and abs(_db_ns(mtime_ns) - row.mtime_ns) <= tolerance)
        if same_as_source or ours:
            replaces = tiff_rel
            extra.append(source_key)
    if not group:
        return FileStatus(planned, dst, ST_NEW, tuple(extra), (), replaces)
    first = group[0]
    outputs_ok = len(group) == first.pages
    for item in group:
        row = rows.get(item.key)
        found = _listed_or_stat(os.path.join(dest, row.rel), listed) if row is not None else None
        if found is None or found[0] != row.size:
            outputs_ok = False
    same_source = (first.source_size == planned.size
                   and abs(first.source_mtime_ns - _db_ns(planned.mtime_ns)) <= tolerance)
    if planned.convert == JPEG_CONVERT:
        # every effort gives the JPEG back byte for byte: another JPEG_JXL_EFFORT
        # is no reason to make again what is in the backup
        same_settings = first.settings.startswith(f"{JPEG_CONVERT}: lossless")
    else:
        same_settings = first.settings == _profile_settings(planned.convert)
    if outputs_ok and same_source and same_settings:
        return FileStatus(planned, dst, ST_UP_TO_DATE, tuple(extra), (), replaces)
    old = tuple(rows[item.key].rel for item in group if item.key in rows)
    return FileStatus(planned, dst, ST_CHANGED, tuple(extra), old, replaces,
                      outputs_ok and same_source)


def _without_conversion(statuses, dest):
    """--no-convert (v0.14.0-rc5: a trip with a slow computer): nothing is
    converted, and nothing already kept as JXL is undone. A JXL group that is
    whole and made from this very file stays, even when the profile's settings
    changed since (UP_TO_DATE); a TIFF or JPEG whose copy in the backup is
    current, and that has no JXL, stays as that copy (UP_TO_DATE, and nothing
    retires it); anything else -- new, changed, a JXL group not whole -- stays
    NEW or CHANGED and _convert_all copies it as it is (_copy_as_is: an
    outdated JXL group goes to versions only after the new copy is there). A
    later run without --no-convert converts what this one copied."""
    tolerance = int(MTIME_TOLERANCE_S * 1_000_000_000)
    result = []
    for status in statuses:
        planned = status.planned
        if planned.convert and status.status == ST_CHANGED and status.settings_only:
            status = _replace_fields(status, status=ST_UP_TO_DATE, old_outputs=())
        elif planned.convert and status.status == ST_NEW and status.replaces:
            there = _listed_or_stat(os.path.join(dest, status.replaces), None)
            if (there is not None and there[0] == planned.size
                    and abs(there[1] - planned.mtime_ns) <= tolerance):
                status = _replace_fields(status, status=ST_UP_TO_DATE, replaces=None)
        result.append(status)
    return result


def _file_statuses(files, dest, listings=None, groups=None, catalog_rows=None):
    """_file_status of every planned file, in the same order, with one listing
    per destination folder instead of one question per file (6 times faster on
    96 000 files, and no file record is read from a cold disk). The listing
    only answers for a name it holds exactly as planned: any other case -- a
    name stored in another case, a folder that could not be listed -- still
    asks the system, so a file that is there is never taken for a NEW one.
    `listings` (a dict), when given, receives {folder: what _list_dest_folder
    returned} for every destination folder listed: the mirror looks there for
    files this tool did not write without listing them again. A TIFF kept as
    JXL takes its state from its group (`groups`, from the catalog's
    converted rows, and `catalog_rows`: _convert_status)."""
    statuses = [None] * len(files)
    by_folder = {}
    for index, planned in enumerate(files):
        dst = os.path.join(dest, planned.rel)
        by_folder.setdefault(os.path.dirname(dst), []).append((index, planned, dst))
    for folder, items in by_folder.items():
        listed = _list_dest_folder(folder)
        if listings is not None:
            listings[folder] = listed
        for index, planned, dst in items:
            if planned.convert:
                statuses[index] = _convert_status(planned, dst, dest, listed, groups,
                                                  catalog_rows)
                continue
            if listed is None:
                statuses[index] = FileStatus(planned, dst, ST_NEW)
                continue
            hit = listed.get(os.path.basename(dst))
            if hit is None:
                statuses[index] = _file_status(planned, dest)
            else:
                statuses[index] = _status_of(planned, dst, *hit)
    return statuses


def _reserved_rel_error(planned):
    """Why this plan cannot run, or None. A planned file whose path starts
    with CATALOG_FOLDER would land in the backup's own folder: the volume.json
    of a source that is itself a backup replaces the identity of this one
    (two disks with one backup ID), and its versions mix with ours."""
    reserved = CATALOG_FOLDER.casefold()
    for item in planned.files:
        first = item.rel.replace("/", "\\").split("\\", 1)[0]
        if first.casefold() == reserved:
            return (f"{item.src} would be copied into {CATALOG_FOLDER}, the backup's own "
                    f"folder on the destination (the source is itself a backup and --base "
                    f"is its root?). Choose a --base above it, so that its files land in "
                    f"a folder of their own.")
    return None


def _existing_ancestor(path):
    path = os.path.abspath(path)
    while True:
        if _exists(path):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return path
        path = parent


def _source_prefixes(planned):
    prefixes = []
    for source, base in planned.sources:
        relative = os.path.relpath(source, base).casefold().rstrip("\\/")
        prefixes.append("" if relative == "." else relative)
    return prefixes


def _volume_label(root):
    """The volume label of a drive root ('' on non-Windows or unnamed drives)."""
    if os.name != "nt":
        return ""
    import ctypes
    buffer = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root), buffer, 261, None, None, None, None, 0)
    return buffer.value if ok else ""


def read_backup_id(dest):
    """The backup_id recorded in <dest>/CATALOG_FOLDER/BACKUP_ID_FILE, or None."""
    path = os.path.join(os.path.abspath(dest), CATALOG_FOLDER, BACKUP_ID_FILE)
    try:
        with open(_io(path), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    value = data.get("backup_id") if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


def _ensure_backup_id(dest, log):
    """Create the backup identity file on the first copy; return the id."""
    existing = read_backup_id(dest)
    if existing:
        return existing
    backup_id = uuid.uuid4().hex
    drive = os.path.splitdrive(os.path.abspath(dest))[0]
    data = {
        "backup_id": backup_id,
        "created": datetime.now().isoformat(timespec="seconds"),
        "label": _volume_label(drive + os.sep) if drive else "",
        "path": os.path.splitdrive(os.path.abspath(dest))[1].lstrip("\\/"),
    }
    folder = os.path.join(os.path.abspath(dest), CATALOG_FOLDER)
    target = os.path.join(folder, BACKUP_ID_FILE)
    tmp = target + TEMP_SUFFIX
    try:
        os.makedirs(_io(folder), exist_ok=True)
        with open(_io(tmp), "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        # Never over a file that is there: one that could not be read for a
        # moment (locked, half written) was replaced by a new identity, and
        # every preset of this backup refused it from then on (T17 of the
        # 261112 sweep).
        _rename_no_clobber(tmp, target)
    except FileExistsError:
        _remove_temp(tmp)
        log.log(f"WARNING {target} is there but could not be read: it is kept as it is "
                f"(the backup ID was not checked in this run)")
        return None
    except OSError as exc:
        _remove_temp(tmp)
        log.log(f"WARNING the backup ID could not be written ({exc}): {target}")
        return None
    log.log(f"Backup ID {backup_id} (new)")
    return backup_id


_RESTORE_NOTE = """\
photo-backup keeps here what is needed to restore this backup on another computer.
Nothing in this folder is ever replaced or deleted: each folder is the version that
wrote to this backup.

- photo-backup <version>\\  the scripts (Python 3.12 or newer, standard library only):
    python photo_backup.py           ->  [5] Restore from a backup
    python restore_backup.py --backup <this backup's folder> --to <a folder>
  The Windows program, when it is kept here (photo-backup-exe ... .zip), needs no Python.

- TIFF and JPEG files kept as JXL in this backup are named like photo.tif.jxl and
  photo.jpg.jxl. The restore turns them back into photo.tif / photo.jpg (a JPEG byte for
  byte) through jxl-photo: the jxl-photo <version>\\ folder here (Python 3.12, libjxl's
  cjxl/djxl and ExifTool), or jxl-photo-exe when it is kept here.
  restore_backup.py ... --convert-back lossless brings back only what is lossless;
  --convert-back none (no jxl-photo needed) gives the JXL files as they are.
"""


def _tool_files(folder, patterns, recursive=False):
    """[(name inside the tool folder, path)] of the files of `folder` that match
    `patterns` (and of docs\\*.md); every file below it with `recursive`.
    Links and junctions are never followed."""
    found = []
    if recursive:
        for current, dirs, names in os.walk(folder):
            dirs[:] = [name for name in dirs
                       if not os.path.islink(os.path.join(current, name))
                       and not os.path.isjunction(os.path.join(current, name))]
            for name in names:
                path = os.path.join(current, name)
                if not os.path.islink(path):
                    found.append((os.path.relpath(path, folder), path))
        return found
    import fnmatch
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and any(fnmatch.fnmatch(name.casefold(), pattern.casefold())
                                        for pattern in patterns):
            found.append((name, path))
    docs = os.path.join(folder, "docs")
    if os.path.isdir(docs):
        for name in sorted(os.listdir(docs)):
            if name.casefold().endswith(".md") and os.path.isfile(os.path.join(docs, name)):
                found.append((os.path.join("docs", name), os.path.join(docs, name)))
    return found


def _keep_tool_folder(root, name, files, log):
    """Copy `files` into root\\name -- written as name.pbtmp and renamed at the
    end, so a folder with the final name is always whole. A folder that is
    there already is left as it is. True when it was written now."""
    final = os.path.join(root, name)
    if os.path.lexists(_io(final)):
        return False
    temp = final + TEMP_SUFFIX
    _remove_own_folder(temp)
    for inside, path in files:
        target = os.path.join(temp, inside)
        os.makedirs(_io(os.path.dirname(target)), exist_ok=True)
        shutil.copy2(_io(path), _io(target))
    os.rename(_io(temp), _io(final))
    log.log(f"TOOLS kept {name} ({len(files)} files)")
    return True


def _keep_tool_extra(root, path, log):
    """Keep one more file or folder (KEEP_TOOLS_EXTRA) in root under its own
    name; a name taken by something of another size gets ' (2)'. True when it
    was written now."""
    if os.path.isdir(_io(path)):
        return _keep_tool_folder(root, os.path.basename(path.rstrip("\\/")),
                                 _tool_files(path, (), recursive=True), log)
    size = os.stat(_io(path)).st_size
    stem, ext = os.path.splitext(os.path.basename(path))
    for counter in range(1, 100):
        name = f"{stem}{ext}" if counter == 1 else f"{stem} ({counter}){ext}"
        target = os.path.join(root, name)
        if os.path.lexists(_io(target)):
            if os.path.isfile(_io(target)) and os.stat(_io(target)).st_size == size:
                return False            # kept already
            continue
        temp = target + TEMP_SUFFIX
        _remove_temp(temp)
        shutil.copy2(_io(path), _io(temp))
        _rename_no_clobber(temp, target)
        log.log(f"TOOLS kept {name}")
        return True
    return False


def _keep_tools(dest, log, jxlphoto=""):
    """Keep the programs that restore this backup in it (KEEP_TOOLS_IN_BACKUP):
    photo-backup of this version, jxl-photo (when set) and KEEP_TOOLS_EXTRA, in
    <dest>\\CATALOG_FOLDER\\TOOLS_FOLDER, one folder per version, never replaced
    or deleted; a HOW TO RESTORE.txt says what to do with them. Lines to show;
    a problem is a warning, never the end of the backup."""
    if not KEEP_TOOLS_IN_BACKUP:
        return []
    root = os.path.join(dest, CATALOG_FOLDER, TOOLS_FOLDER)
    kept = []
    try:
        os.makedirs(_io(root), exist_ok=True)
        with os.scandir(_io(root)) as listing:      # an interrupted run's own temp copies
            for entry in listing:
                if entry.name.endswith(TEMP_SUFFIX):
                    path = os.path.join(root, entry.name)
                    if entry.is_dir(follow_symlinks=False):
                        _remove_own_folder(path)
                    else:
                        _remove_temp(path)
        here = os.path.dirname(os.path.abspath(__file__))
        own = _tool_files(here, ("*.py", "backup_modes.csv", "backup_manifest.example.csv",
                                 "README*.md", "LICENSE"))
        if _keep_tool_folder(root, f"photo-backup {__version__}", own, log):
            kept.append(f"photo-backup {__version__}")
        if jxlphoto and _jxlphoto_problem(jxlphoto) is None:
            folder = os.path.dirname(os.path.abspath(jxlphoto))
            files = (_tool_files(folder, ("*.py", "README*.md", "LICENSE"))
                     if jxlphoto.casefold().endswith(".py")
                     else _tool_files(folder, (), recursive=True))
            name = _jxlphoto_version(jxlphoto)
            if _keep_tool_folder(root, name, files, log):
                kept.append(name)
        for extra in KEEP_TOOLS_EXTRA:
            if extra and _keep_tool_extra(root, extra, log):
                kept.append(os.path.basename(str(extra).rstrip("\\/")))
        note = os.path.join(root, "HOW TO RESTORE.txt")
        if not os.path.lexists(_io(note)):
            with open(_io(note + TEMP_SUFFIX), "w", encoding="utf-8", newline="\r\n") as handle:
                handle.write(_RESTORE_NOTE)
            _rename_no_clobber(note + TEMP_SUFFIX, note)
    except OSError as exc:
        log.log(f"WARNING the tools were not all kept in {root}: {exc}")
        return [f"WARNING the tools were not all kept in the backup ({exc}): see the log"]
    if kept:
        return [f"Kept in the backup, to restore it anywhere: {', '.join(kept)} "
                f"({os.path.join(CATALOG_FOLDER, TOOLS_FOLDER)})"]
    return []


class _Catalog:
    def __init__(self, dest, create, readonly=False):
        self.dir = os.path.join(os.path.abspath(dest), CATALOG_FOLDER)
        self.path = os.path.join(self.dir, "catalog.sqlite")
        if (readonly or not create) and not _exists(self.path):
            raise FileNotFoundError(f"catalog does not exist: {self.path}")
        if create and not readonly:
            os.makedirs(_io(self.dir), exist_ok=True)
        self.conn = None
        self.scratch = None
        self.writers = dict.fromkeys(("created_by", "created_at", "updated_by", "updated_at"), "")
        self._writer_recorded = True        # nothing to record until it is open to write
        self._changes_at_open = 0
        self._first_copy = ""
        self.has_converted = False
        self.converted_columns = set()
        database = self.path
        if readonly and _exists(self.path + "-journal"):
            # An interrupted commit left its journal: reading the catalog here
            # would roll it back INSIDE the backup. Read a private copy instead;
            # the next backup run, which may write, does the real rollback.
            self.scratch = tempfile.mkdtemp(prefix="photo_backup_catalog_")
            database = os.path.join(self.scratch, "catalog.sqlite")
            try:
                for suffix in ("", "-journal"):
                    shutil.copyfile(_io(self.path + suffix), _io(database + suffix))
            except OSError as exc:
                shutil.rmtree(self.scratch, ignore_errors=True)
                raise RuntimeError(f"cannot read catalog {self.path}: {exc}")
        try:
            self.conn = sqlite3.connect(_io(database))
            if readonly:
                try:
                    row = self.conn.execute(
                        "SELECT v FROM meta WHERE k='schema_version'").fetchone()
                except sqlite3.OperationalError as exc:
                    raise RuntimeError(f"not a photo-backup catalog: {self.path}") from exc
                if row is None:
                    raise RuntimeError(f"not a photo-backup catalog: {self.path}")
                if row[0] not in ("1", "2", SCHEMA_VERSION):    # 1: before the converted
                    raise RuntimeError(                          # table; 2: before its kind
                        f"catalog schema_version {row[0]} != {SCHEMA_VERSION}: {self.path}")
            else:
                self.conn.execute(
                    "CREATE TABLE IF NOT EXISTS files ("
                    "key TEXT PRIMARY KEY, rel TEXT NOT NULL, size INTEGER NOT NULL, "
                    "mtime_ns INTEGER NOT NULL, sha256 TEXT, source_path TEXT NOT NULL, "
                    "copied_at TEXT, verified_at TEXT)")
                self.conn.execute(
                    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
                row = self.conn.execute(
                    "SELECT v FROM meta WHERE k='schema_version'").fetchone()
                created_now = row is None
                if row is None or row[0] in ("1", "2"):
                    # new, or made before v0.14.0-rc2/rc3: the table of files kept
                    # as JXL comes in (or gains its kind and lossless columns), and
                    # the version that knows it (an older program refuses this
                    # catalog: it would take a JXL for a copy)
                    self.conn.execute(
                        "CREATE TABLE IF NOT EXISTS converted ("
                        "key TEXT PRIMARY KEY, source_key TEXT NOT NULL, "
                        "source_rel TEXT NOT NULL, source_size INTEGER NOT NULL, "
                        "source_mtime_ns INTEGER NOT NULL, source_sha256 TEXT NOT NULL, "
                        "page INTEGER NOT NULL, pages INTEGER NOT NULL, tool TEXT NOT NULL, "
                        "settings TEXT NOT NULL, kind TEXT, lossless INTEGER)")
                    columns = {info[1] for info in
                               self.conn.execute("PRAGMA table_info(converted)").fetchall()}
                    for column, kind in (("kind", "TEXT"), ("lossless", "INTEGER")):
                        if column not in columns:
                            self.conn.execute(f"ALTER TABLE converted ADD COLUMN {column} {kind}")
                    self.conn.execute(
                        "INSERT OR REPLACE INTO meta(k, v) VALUES('schema_version', ?)",
                        (SCHEMA_VERSION,))
                    self.conn.commit()
                elif row[0] != SCHEMA_VERSION:
                    raise RuntimeError(
                        f"catalog schema_version {row[0]} != {SCHEMA_VERSION}: {self.path}")
            # A damaged page can sit where a plain read of the table never
            # goes (an index): a verify said OK with it, and the next backup
            # died in the middle. Checked once, here, for every user.
            check = self.conn.execute("PRAGMA quick_check(1)").fetchone()
            if check is None or check[0] != "ok":
                found = " ".join(str(check[0]).split()) if check else "no answer"
                raise RuntimeError(
                    f"catalog is damaged ({found}): {self.path}. The copied files are not "
                    f"touched; see 'A damaged catalog' in the README.")
            # A valid database can still hold a path that leads out of the
            # backup ('..\\x'): the restore read and wrote outside, --move-orphans
            # would move a file of somewhere else (T13 of the 261112 sweep).
            unsafe = self._first_unsafe_rel()
            if unsafe is not None:
                raise RuntimeError(
                    f"catalog is damaged: the row {unsafe[0]!r} has {unsafe[1]}, which would "
                    f"lead outside the backup: {self.path}. Nothing was read or written "
                    f"through it; see 'A damaged catalog' in the README.")
            self.has_converted = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='converted'"
            ).fetchone() is not None
            self.converted_columns = ({info[1] for info in self.conn.execute(
                "PRAGMA table_info(converted)").fetchall()} if self.has_converted else set())
            unsafe = self._first_unsafe_source_rel()
            if unsafe is not None:
                raise RuntimeError(
                    f"catalog is damaged: the TIFF {unsafe[0]!r} kept as JXL has {unsafe[1]}, "
                    f"which would lead outside the backup: {self.path}. Nothing was read or "
                    f"written through it; see 'A damaged catalog' in the README.")
            # who made it and who wrote it last, as they were before this run;
            # this run is recorded at its first commit that changes something
            self.writers = self._writers()
            self._writer_recorded = readonly
            self._first_copy = ""
            if not readonly and created_now:
                self._record_writer(created_now)
                self.conn.commit()
            elif not readonly and not self.writers["created_by"]:
                # made before v0.14.0: its first copy, read before this run adds any
                self._first_copy = self.conn.execute(
                    "SELECT min(copied_at) FROM files").fetchone()[0] or ""
            self._changes_at_open = self.conn.total_changes
        except sqlite3.Error as exc:
            self.close()
            raise RuntimeError(f"cannot open catalog {self.path}: {exc}")
        except RuntimeError:
            self.close()
            raise

    def _writers(self):
        """{created_by, created_at, updated_by, updated_at} as the catalog
        records them ('' for what it does not record)."""
        keys = ("created_by", "created_at", "updated_by", "updated_at")
        recorded = dict(self.conn.execute(
            "SELECT k, v FROM meta WHERE k IN (?, ?, ?, ?)", keys).fetchall())
        return {key: recorded.get(key) or "" for key in keys}

    def _record_writer(self, created_now):
        """Record the program and version that made this catalog (once) and the
        last one that changed it (v0.14.0); the caller commits. A catalog made
        before this was recorded says so, with the date of its first copy."""
        self._writer_recorded = True
        now = datetime.now().isoformat(timespec="seconds")
        program = _program_name()
        values = {"updated_by": program, "updated_at": now}
        if not self.writers["created_by"]:
            if created_now:
                values.update(created_by=program, created_at=now)
            else:
                values.update(created_by="an earlier version (before 0.14.0)",
                              created_at=self._first_copy)
        for key, value in values.items():
            self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (key, value))

    def writers_line(self):
        """'created by X on D; last written by Y on D', from what was recorded
        when it was opened (before this run), or '' when nothing was."""
        writers = self.writers
        parts = []
        if writers["created_by"]:
            parts.append(f"created by {writers['created_by']}"
                         + (f" on {writers['created_at']}" if writers["created_at"] else ""))
        if writers["updated_by"]:
            parts.append(f"last written by {writers['updated_by']}"
                         + (f" on {writers['updated_at']}" if writers["updated_at"] else ""))
        return "; ".join(parts)

    def _first_unsafe_rel(self):
        """(rel, why) of the first row whose path would lead outside the
        backup (_unsafe_rel), or None. SQLite picks the few candidates: a
        path with ':', starting at a root, or with a part that starts with a
        dot."""
        cursor = self.conn.execute(
            "SELECT rel FROM files WHERE typeof(rel) != 'text' OR trim(rel) = '' "
            "OR instr(rel, ':') > 0 OR substr(rel, 1, 1) IN ('\\', '/', '.') "
            "OR instr(rel, '\\.') > 0 OR instr(rel, '/.') > 0")
        for (rel,) in cursor:
            why = _unsafe_rel(rel)
            if why is not None:
                return rel, why
        return None

    def _first_unsafe_source_rel(self):
        """(source_rel, why) of the first TIFF kept as JXL whose name would
        lead outside the backup (the restore writes with it), or None."""
        if not self.has_converted:
            return None
        for (rel,) in self.conn.execute("SELECT source_rel FROM converted"):
            why = _unsafe_rel(rel)
            if why is not None:
                return rel, why
        return None

    def all_rows(self):
        cursor = self.conn.execute(
            "SELECT key, rel, size, mtime_ns, sha256, source_path, "
            "copied_at, verified_at FROM files")
        return [_Row(*row) for row in cursor.fetchall()]

    def converted_rows(self):
        """{key: _Converted} of the backup files that are a TIFF or a JPEG kept
        as JXL. A schema 2 catalog (v0.14.0-rc2) read as it is has no kind
        (taken from the source's extension) and no lossless flag (unknown)."""
        if not self.has_converted:
            return {}
        extra = ", ".join(column if column in self.converted_columns else "NULL"
                          for column in ("kind", "lossless"))
        cursor = self.conn.execute(
            "SELECT key, source_key, source_rel, source_size, source_mtime_ns, source_sha256, "
            f"page, pages, tool, settings, {extra} FROM converted")
        rows = {}
        for row in cursor.fetchall():
            item = _Converted(*row)
            if not item.kind:
                item.kind = _convert_kind(item.source_rel)
            rows[item.key] = item
        return rows

    def record_converted(self, converted):
        """Record one file of a TIFF's JXL group (its files row is upserted
        apart, with the JXL's own size, date and hash)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO converted(key, source_key, source_rel, source_size, "
            "source_mtime_ns, source_sha256, page, pages, tool, settings, kind, lossless) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (converted.key, converted.source_key, converted.source_rel, converted.source_size,
             _db_ns(converted.source_mtime_ns), converted.source_sha256, converted.page,
             converted.pages, converted.tool, converted.settings, converted.kind,
             None if converted.lossless is None else int(bool(converted.lossless))))

    def delete(self, key):
        self.conn.execute("DELETE FROM files WHERE key=?", (key,))
        if self.has_converted:
            self.conn.execute("DELETE FROM converted WHERE key=?", (key,))

    def adopt(self, planned):
        """Record an up-to-date destination file. A row whose size/mtime no
        longer match the source (the file was updated outside this tool, or
        an older version recorded the planned instead of the copied stat) is
        refreshed and its hash cleared, so `--verify full` re-hashes both
        sides instead of reporting a false DEST_SIZE forever."""
        self.conn.execute(
            "INSERT INTO files"
            "(key, rel, size, mtime_ns, sha256, source_path, copied_at, verified_at) "
            "VALUES(?, ?, ?, ?, NULL, ?, NULL, NULL) "
            "ON CONFLICT(key) DO UPDATE SET rel=excluded.rel, size=excluded.size, "
            "mtime_ns=excluded.mtime_ns, source_path=excluded.source_path, "
            "sha256=NULL, verified_at=NULL "
            "WHERE files.size != excluded.size OR files.mtime_ns != excluded.mtime_ns",
            (planned.rel.casefold(), planned.rel, planned.size, _db_ns(planned.mtime_ns),
             planned.src))

    def upsert(self, planned, sha256, size=None, mtime_ns=None):
        """Record a copy. `size`/`mtime_ns` are the source's stat taken AT
        COPY TIME; the planned values are only a fallback (a file edited
        between planning and copying is copied as it is now). The row of a
        file copied again loses its verified_at: that date was of the old
        bytes, and these have never been read back by a verify."""
        size = planned.size if size is None else size
        mtime_ns = planned.mtime_ns if mtime_ns is None else mtime_ns
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO files"
            "(key, rel, size, mtime_ns, sha256, source_path, copied_at, verified_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, NULL) "
            "ON CONFLICT(key) DO UPDATE SET rel=excluded.rel, size=excluded.size, "
            "mtime_ns=excluded.mtime_ns, sha256=excluded.sha256, "
            "source_path=excluded.source_path, copied_at=excluded.copied_at, "
            "verified_at=NULL",
            (planned.rel.casefold(), planned.rel, size, _db_ns(mtime_ns),
             sha256, planned.src, now))

    def set_verified(self, key, when):
        self.conn.execute("UPDATE files SET verified_at=? WHERE key=?", (when, key))

    def set_hash_verified(self, key, sha256, when):
        self.conn.execute("UPDATE files SET sha256=?, verified_at=? WHERE key=?",
                          (sha256, when, key))

    def commit(self):
        """Commit. The first commit that changed something also records this
        program as the last one that wrote the catalog (v0.14.0): a run that
        changes nothing leaves the catalog as it was, byte for byte."""
        if not self._writer_recorded and self.conn.total_changes > self._changes_at_open:
            self._record_writer(created_now=False)
        self.conn.commit()

    def close(self):
        try:
            if self.conn is not None:
                self.conn.close()
        except sqlite3.Error:
            pass
        if self.scratch is not None:
            shutil.rmtree(self.scratch, ignore_errors=True)
            self.scratch = None


@dataclass
class _Row:
    key: str
    rel: str
    size: int
    mtime_ns: int
    sha256: str
    source_path: str
    copied_at: str = None
    verified_at: str = None


JPEG_CONVERT = "jpeg"                # PlannedFile.convert of a JPEG kept as JXL (no TIFF profile has it)


def _convert_kind(source_rel):
    """'jpeg' or 'tiff': what a file kept as JXL was, by its source's extension."""
    ext = os.path.splitext(str(source_rel))[1].casefold()
    return "jpeg" if ext in {item.casefold() for item in JPEG_JXL_EXTENSIONS} else "tiff"


@dataclass
class _Converted:
    """One file of a TIFF or a JPEG kept as JXL (catalog table `converted`,
    v0.14.0-rc2; kind and lossless since rc3)."""
    key: str                # the JXL's own key (= files.key)
    source_key: str         # the TIFF's rel, casefolded: groups the pages
    source_rel: str         # the TIFF's rel as written: the name the restore gives back
    source_size: int
    source_mtime_ns: int
    source_sha256: str
    page: int               # 0 = the main file
    pages: int              # how many JXL the TIFF became
    tool: str               # "jxl-photo 2.30.2"
    settings: str           # "jxl: d=0.05 scan=0 8bit=0 e=7", "jpeg: lossless e=7"
    kind: str = None        # "tiff" or "jpeg"
    lossless: object = None     # 1 = the original comes back exactly (a JPEG: its bytes;
                                # a TIFF: its pixels), 0 = lossy, None = not known


def _converted_groups(converted):
    """{source_key: [_Converted, ...] by page} of the catalog's TIFFs kept as JXL."""
    groups = {}
    for item in converted.values():
        groups.setdefault(item.source_key, []).append(item)
    for items in groups.values():
        items.sort(key=lambda item: (item.page, item.key))
    return groups


class _Logger:
    def __init__(self, prefix):
        os.makedirs(_io(LOG_DIR), exist_ok=True)
        candidate = prefix
        counter = 1
        while _exists(candidate + ".log"):
            counter += 1
            candidate = f"{prefix}_{counter}"
        self.prefix = candidate
        self.path = candidate + ".log"
        self.handle = open(_io(self.path), "a", encoding="utf-8")

    def log(self, message):
        self.handle.write(message + "\n")
        self.handle.flush()

    def out(self, message):
        print(message)
        self.log(message)

    def csv(self, suffix, header, rows):
        path = self.prefix + suffix
        with open(_io(path), "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def close(self):
        try:
            self.handle.close()
        except OSError:
            pass


def _rename_no_clobber(src, dst):
    """Rename that never replaces: FileExistsError when `dst` is there
    (Windows' rename refuses it; elsewhere a hard link does)."""
    if os.name == "nt":
        os.rename(_io(src), _io(dst))
    else:
        os.link(_io(src), _io(dst))
        os.remove(_io(src))


def _move_aside(src, target):
    """Move `src` to `target` -- or to 'name (2).ext', 'name (3).ext', ...
    when that name is taken: a file already there is never replaced (two runs
    started in the same second share the stamp of versions\\ and orphans\\,
    T16 of the 261112 sweep). Returns the path used."""
    stem, ext = os.path.splitext(target)
    candidate = target
    for counter in range(2, 10000):
        try:
            _rename_no_clobber(src, candidate)
            return candidate
        except FileExistsError:
            candidate = f"{stem} ({counter}){ext}"
    raise FileExistsError(f"no free name next to {target}")


def _move_file_to_versions(dest, stamp, rel, log):
    """Move the backup file at `rel` into the versions folder of this run."""
    target = os.path.join(dest, CATALOG_FOLDER, "versions", stamp, rel)
    os.makedirs(_io(os.path.dirname(target)), exist_ok=True)
    used = _move_aside(os.path.join(dest, rel), target)
    log.log(f"VERSION {rel}"
            + ("" if used == target else f" (kept as {os.path.basename(used)})"))


def _move_to_versions(dest, stamp, status, log):
    _move_file_to_versions(dest, stamp, status.planned.rel, log)


def _handle_old_copy(dest, stamp, status, log, keep_old=None):
    """The destination's old copy of a CHANGED file. It keeps its name until
    the returned callback runs, right before the new file takes that name: a
    copy that fails, or a crash before that, leaves the old copy where it was.
    A different size proves different content: the callback moves it to the
    versions folder. With the same size the callback compares it with the hash
    of the bytes really copied: only a new file identical byte for byte leaves
    the old one out of the versions folder (Capture One rewrites every .cos it
    opens, new date, same bytes). The new file is copied either way.
    `keep_old(dest, stamp, status, log)` keeps an old copy that differs
    (default: the versions folder; the mirror passes its trash)."""
    same_size = os.stat(_io(status.dst)).st_size == status.planned.size
    old_sha256 = _sha256_file(status.dst) if same_size else None

    def before_final(new_sha256):
        if old_sha256 is None or new_sha256 != old_sha256:
            (keep_old or _move_to_versions)(dest, stamp, status, log)
            return
        # Windows refuses to replace a read-only file
        os.chmod(_io(status.dst), os.stat(_io(status.dst)).st_mode | 0o200)
        log.log(f"IDENTICAL {status.planned.rel} (old copy not kept in versions)")

    return before_final


class _SourceRead:
    """One source file, read once and hashed as it is read. `digest` and
    `stat0` are set when the iteration ends; a file that changed meanwhile
    raises instead of ending."""

    def __init__(self, planned):
        self.planned = planned
        self.digest = None
        self.stat0 = None

    def __iter__(self):
        stat0 = os.stat(_io(self.planned.src), follow_symlinks=False)
        hasher = hashlib.sha256()
        with open(_io(self.planned.src), "rb") as handle:
            for chunk in _read_chunks(handle):
                hasher.update(chunk)
                yield chunk
        stat1 = os.stat(_io(self.planned.src), follow_symlinks=False)
        if (stat1.st_size, stat1.st_mtime_ns) != (stat0.st_size, stat0.st_mtime_ns):
            raise RuntimeError("source changed during copy")
        self.digest = hasher.hexdigest()
        self.stat0 = stat0

    def drain(self):
        """Nothing is queued for a file read directly."""


class _StreamedSource:
    """A file's chunks coming out of _ReadAhead, in the order they were read."""

    def __init__(self, read_ahead, planned):
        self._read_ahead = read_ahead
        self.planned = planned
        self.digest = None
        self.stat0 = None
        self._finished = False

    def _next_item(self):
        """The next queued item of this file; marks the file finished at its last."""
        item = self._read_ahead.take()
        if item[0] != "chunk":
            self._finished = True
        return item

    def __iter__(self):
        while not self._finished:
            item = self._next_item()
            if item[0] == "chunk":
                yield item[1]
            elif item[0] == "error":
                raise item[1]
            elif item[1] is not self.planned:
                raise RuntimeError("read-ahead out of step with the copy")
            else:
                self.digest, self.stat0 = item[2], item[3]

    def drain(self):
        """Discard what is left of this file, so the next one starts at its own data."""
        while not self._finished:
            self._next_item()


class _ReadAhead:
    """Reads the source files on a thread of its own, up to `capacity` bytes
    ahead, so the source disk works while the destination disk is busy. The
    files come out in the order of `planned_files`: take exactly one
    next_source() per file and drain() it."""

    def __init__(self, planned_files, capacity):
        self._planned_files = planned_files
        self._capacity = capacity
        self._items = collections.deque()
        self._bytes = 0
        self._stopping = False
        self._condition = threading.Condition()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _put(self, item, size=0):
        with self._condition:
            while (not self._stopping and self._bytes
                   and self._bytes + size > self._capacity):
                self._condition.wait(0.2)
            if self._stopping:
                return False
            self._items.append((item, size))
            self._bytes += size
            self._condition.notify_all()
            return True

    def _run(self):
        for planned in self._planned_files:
            source = _SourceRead(planned)
            try:
                for chunk in source:
                    if not self._put(("chunk", chunk), len(chunk)):
                        return
            except Exception as exc:
                if not self._put(("error", exc)):
                    return
                continue
            if not self._put(("end", planned, source.digest, source.stat0)):
                return

    def next_source(self, planned):
        return _StreamedSource(self, planned)

    def take(self):
        with self._condition:
            # A timed wait: Ctrl+C reaches a wait on Windows only when it ends.
            while not self._items:
                if not self._thread.is_alive():
                    return ("error", RuntimeError("the read-ahead thread stopped"))
                self._condition.wait(0.2)
            item, size = self._items.popleft()
            self._bytes -= size
            self._condition.notify_all()
            return item

    def close(self):
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        self._thread.join(5)


def _delete_temp(tmp):
    """Remove one of our own temp files. The copied attributes can make a temp
    read-only, and a crash can leave that temp behind: Windows refuses to
    remove it until it is writable again."""
    try:
        os.remove(_io(tmp))
    except PermissionError:
        os.chmod(_io(tmp), os.stat(_io(tmp)).st_mode | 0o200)
        os.remove(_io(tmp))


def _remove_temp(tmp):
    try:
        if _exists(tmp):
            _delete_temp(tmp)
    except OSError:
        pass


def _dest_accepts_writes(dest):
    """None when the destination still takes a write, else why not. Writes one
    file of our own (TEMP_SUFFIX, inside CATALOG_FOLDER), waits until it is on
    the disk and removes it: the only file this test ever touches."""
    probe = os.path.join(os.path.abspath(dest), CATALOG_FOLDER,
                         f"dest_check_{os.getpid()}{TEMP_SUFFIX}")
    try:
        os.makedirs(_io(os.path.dirname(probe)), exist_ok=True)
        with open(_io(probe), "wb") as handle:
            handle.write(b"photo-backup destination check")
            handle.flush()
            os.fsync(handle.fileno())
        os.remove(_io(probe))
    except OSError as exc:
        _remove_temp(probe)
        return exc.strerror or str(exc)
    return None


def _write_temp(planned, tmp, source, flush):
    """Write `source` to the temp file; `flush` waits until it is on the disk."""
    if _exists(tmp):
        _delete_temp(tmp)
    with open(_io(tmp), "xb") as handle:
        for chunk in source:
            handle.write(chunk)
        if flush:
            handle.flush()
            os.fsync(handle.fileno())


def _flush_temp(tmp):
    """Wait until a temp file written without `flush` is on the disk. Must run
    before _finish_temp: the copied attributes can make the file read-only."""
    with open(_io(tmp), "r+b") as handle:
        os.fsync(handle.fileno())


def _flush_error(tmp):
    """_flush_temp for a thread pool: the error comes back as a value."""
    try:
        _flush_temp(tmp)
    except Exception as exc:
        return exc
    return None


def _finish_temp(planned, tmp, dst, verify, digest, stat0, expected_sha256=None, no_clobber=False,
                 before_final=None, mtime_ns=None):
    """Verify a temp file already on the disk, then give it its final name.
    before_final(digest) runs last, right before that name is given.
    `mtime_ns`: the date these bytes are known to have (the restore: the
    catalog's, when the backup's disk could only keep it rounded)."""
    if expected_sha256 is not None and digest != expected_sha256:
        raise RuntimeError("content differs from the catalog sha256 (backup copy corrupt?)")
    shutil.copystat(_io(planned.src), _io(tmp))
    # The dates of the bytes really copied (stat0), not of the source as it is
    # now: a file edited after it was read keeps its old date here, so the next
    # run sees it as CHANGED instead of trusting old bytes under a new date.
    os.utime(_io(tmp), ns=(stat0.st_atime_ns,
                           stat0.st_mtime_ns if mtime_ns is None else mtime_ns))
    if verify == "size":
        if os.stat(_io(tmp)).st_size != stat0.st_size:
            raise RuntimeError("size mismatch after copy")
    elif verify == "hash":
        if _sha256_file(tmp) != digest:
            raise RuntimeError("hash mismatch after copy")
    if before_final is not None:
        before_final(digest)
    if no_clobber:
        # Atomic "only if absent": Windows' rename refuses an existing
        # target; elsewhere a hard link does (rename would overwrite).
        if os.name == "nt":
            os.rename(_io(tmp), _io(dst))
        else:
            os.link(_io(tmp), _io(dst))
            os.remove(_io(tmp))
    else:
        os.replace(_io(tmp), _io(dst))
    return digest, stat0.st_size, stat0.st_mtime_ns


def _copy_file(planned, dst, verify, expected_sha256=None, no_clobber=False, source=None,
               before_final=None, mtime_ns=None):
    """Copy to a temp file next to `dst`, verify, then move it into place.
    no_clobber: a `dst` that exists at that last step is never replaced
    (FileExistsError, nothing written) -- the restore's rule. `source`: where
    the data comes from (default: read the file here). `mtime_ns`: see
    _finish_temp."""
    tmp = dst + TEMP_SUFFIX
    os.makedirs(_io(os.path.dirname(dst)), exist_ok=True)
    source = _SourceRead(planned) if source is None else source
    try:
        _write_temp(planned, tmp, source, True)
        return _finish_temp(planned, tmp, dst, verify, source.digest, source.stat0,
                            expected_sha256, no_clobber, before_final, mtime_ns)
    except BaseException:
        _remove_temp(tmp)
        raise


@dataclass
class _PendingCopy:
    """A small file written to its temp name, waiting for its batch's flush."""
    status: object
    tmp: str
    source: object
    before_final: object = None


def _stage_small_file(status, source, before_final=None):
    """Write a small file to its temp name without waiting for the disk."""
    tmp = status.dst + TEMP_SUFFIX
    os.makedirs(_io(os.path.dirname(status.dst)), exist_ok=True)
    try:
        _write_temp(status.planned, tmp, source, False)
    except BaseException:
        _remove_temp(tmp)
        raise
    return _PendingCopy(status, tmp, source, before_final)


def _auto_updated(status, args):
    """A changed edit-settings/sidecar file that is updated without --update-changed."""
    return (status.status == ST_CHANGED and args.auto_update
            and os.path.splitext(status.planned.rel)[1].casefold() in AUTO_UPDATE_EXTENSIONS)


def _will_copy(status, args):
    if status.status == ST_NEW:
        return True
    return status.status == ST_CHANGED and (args.update_changed or _auto_updated(status, args))


def _adopt_rows(catalog, statuses):
    for status in statuses:
        # a TIFF kept as JXL is in the catalog already, as its group
        if status.status == ST_UP_TO_DATE and not status.planned.convert:
            catalog.adopt(status.planned)
    catalog.commit()


def _find_move_candidates(statuses, rows, dest, sources=None):
    """NEW statuses whose name+size match a catalogued backup file whose source
    is gone. Returns {id(status): (status, [row, ...])} — confirmed by hash
    later. (The plan wrote {id(status): [row, ...]}; the status object travels
    with its rows because _apply_moves receives only this mapping.) "Gone" is
    the orphans' proof: under a source of this run (`sources`) and
    SOURCE_MISSING; a source that cannot be read is never moved away from."""
    planned_keys = {status.planned.rel.casefold() for status in statuses}
    index = {}
    for row in rows:
        if row.key in planned_keys:
            continue
        index.setdefault((os.path.basename(row.rel).casefold(), row.size), []).append(row)
    candidates = {}
    for status in statuses:
        if status.status != ST_NEW or status.planned.convert:    # a JXL is made, not moved
            continue
        matches = index.get((os.path.basename(status.planned.rel).casefold(),
                             status.planned.size))
        if not matches:
            continue
        accepted = []
        for row in matches:
            if sources is not None and not _under_sources(row.source_path, sources):
                continue
            if _source_state(row.source_path) != SOURCE_MISSING:
                continue
            old = os.path.join(dest, row.rel)
            try:
                if os.stat(_io(old), follow_symlinks=False).st_size != row.size:
                    continue
            except OSError:
                continue
            accepted.append(row)
        if accepted:
            candidates[id(status)] = (status, accepted)
    return candidates


_REPARSE_POINT = 0x400              # FILE_ATTRIBUTE_REPARSE_POINT: a link, a junction, ...


def _is_reparse(path):
    """Whether `path` itself is a link, a junction or another reparse point
    (a path that cannot be asked counts as one: fail closed)."""
    try:
        info = os.lstat(_io(path))
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return (bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)
            or (info.st_mode & 0o170000) == 0o120000)


def _dest_link(dest, folder_rel, cache):
    """The folder on the way from `dest` to `folder_rel` (a folder of the
    backup) that is a link, a junction or another reparse point, or None.
    The backup never writes, moves or deletes through one: it could lead into
    a source (finding 1 of the rc1 review). `cache`: {folded path: state}."""
    path = dest
    for part in folder_rel.replace("/", "\\").split("\\"):
        if not part:
            continue
        path = os.path.join(path, part)
        key = os.path.normcase(path)
        state = cache.get(key)
        if state is None:
            if not os.path.lexists(_io(path)):
                state = "absent"
            else:
                state = "link" if _is_reparse(path) else "folder"
            cache[key] = state
        if state == "absent":
            return None             # nothing below it exists yet
        if state == "link":
            return path
    return None


def _first_dest_link(dest, rels):
    """The first link or junction inside the backup on the way to one of
    `rels` (backup files), or None. One question per folder."""
    cache = {}
    seen = set()
    for rel in rels:
        folder = os.path.dirname(rel.replace("/", "\\"))
        key = folder.casefold()
        if not folder or key in seen:
            continue
        seen.add(key)
        found = _dest_link(dest, folder, cache)
        if found is not None:
            return found
    return None


def _prune_empty_parents(path, stop, log):
    """Remove the folders left empty by one of our own moves, from `path` up to
    (not including) `stop`. This is the only place a folder is removed on the
    destination — never a file, and never anything inside CATALOG_FOLDER. It
    stops at the first folder that is not empty."""
    stop = os.path.abspath(stop)
    catalog_dir = os.path.join(stop, CATALOG_FOLDER)
    current = os.path.abspath(path)
    while True:
        if os.path.normcase(current) == os.path.normcase(stop):
            return
        if _is_under(current, catalog_dir):
            return
        parent = os.path.dirname(current)
        if parent == current:
            return
        if _is_reparse(current):
            # os.rmdir takes a junction away even when what it leads to is
            # not empty (finding 4 of the rc1 review): never
            return
        try:
            os.rmdir(_io(current))
        except OSError:
            return
        log.log(f"RMDIR {os.path.relpath(current, stop)}")
        current = parent


def _apply_moves(candidates, dest, catalog, log):
    """Move each confirmed candidate inside the backup; returns
    (moved, moved_bytes, move_errors, interrupted)."""
    moved = 0
    moved_bytes = 0
    move_errors = 0
    interrupted = False
    now = datetime.now().isoformat(timespec="seconds")
    for status, rows in candidates.values():
        try:
            sha_src = _sha256_file(status.planned.src)
            stat0 = os.stat(_io(status.planned.src), follow_symlinks=False)
            chosen = None
            for row in rows:
                old = os.path.join(dest, row.rel)
                try:
                    sha_old = _sha256_file(old)
                except OSError:
                    continue
                if sha_old != sha_src:
                    continue
                if row.sha256 is not None and row.sha256 != sha_old:
                    continue
                chosen = row
                break
            if chosen is None:
                log.log(f"MOVE REJECTED {status.planned.rel}: content differs")
                continue
            new = status.dst
            if _exists(new):
                move_errors += 1
                log.log(f"ERROR MOVE {status.planned.rel}: destination exists")
                continue
            old = os.path.join(dest, chosen.rel)
            old_dir = os.path.dirname(old)
            os.makedirs(_io(os.path.dirname(new)), exist_ok=True)
            # never over a file that appeared there since the check above
            _rename_no_clobber(old, new)
            os.utime(_io(new), ns=(stat0.st_atime_ns, stat0.st_mtime_ns))
            catalog.delete(chosen.key)
            catalog.upsert(status.planned, sha_src, stat0.st_size, stat0.st_mtime_ns)
            catalog.set_verified(status.planned.rel.casefold(), now)
            status.status = ST_MOVED
            moved += 1
            moved_bytes += stat0.st_size
            log.log(f"MOVE {chosen.rel} -> {status.planned.rel}")
            _prune_empty_parents(old_dir, dest, log)
            if moved % 50 == 0:
                catalog.commit()
        except KeyboardInterrupt:
            _say_stopping(log)
            with _ctrl_c_after():
                catalog.commit()
            return moved, moved_bytes, move_errors, True
        except Exception as exc:
            move_errors += 1
            log.log(f"ERROR MOVE {status.planned.rel}: {exc}")
    catalog.commit()
    return moved, moved_bytes, move_errors, interrupted


ORPHAN_REASONS = {SOURCE_PRESENT: "not_selected", SOURCE_MISSING: "source_missing",
                  SOURCE_UNREADABLE: "source_unreadable"}


def _find_orphans(rows, statuses, prefixes, used_keys, sources=None, states=None):
    """Catalogued files under the verified prefixes that are neither planned
    now nor consumed by a move. Returns [(reason, row), ...]. A row whose
    source is not under a source of this run (`sources`: another disk that
    shares the folder names) is other_source; only a source proven gone
    (_source_state) is source_missing -- the one --move-orphans moves.
    `states` (a dict) keeps the answer of _source_state per row, for a second
    list of the same run that moves nothing. The other files of a TIFF kept
    as JXL, and a TIFF copy its JXL replaces, count as planned (extra_keys)."""
    planned_keys = {status.planned.rel.casefold() for status in statuses}
    planned_keys.update(key for status in statuses for key in status.extra_keys)
    orphans = []
    for row in rows:
        if not _key_under_prefixes(row.key, prefixes):
            continue
        if row.key in planned_keys or row.key in used_keys:
            continue
        if sources is not None and not _under_sources(row.source_path, sources):
            reason = "other_source"
        else:
            state = states.get(row.source_path) if states is not None else None
            if state is None:
                state = _source_state(row.source_path)
                if states is not None:
                    states[row.source_path] = state
            reason = ORPHAN_REASONS[state]
        orphans.append((reason, row))
    return orphans


def _move_orphans(dest, stamp, orphans, catalog, log):
    """Move source_missing orphans into CATALOG_FOLDER/ORPHANS_FOLDER/<stamp>;
    never a not_selected one."""
    target_root = os.path.join(dest, CATALOG_FOLDER, ORPHANS_FOLDER, stamp)
    moved = 0
    for reason, row in orphans:
        if reason != "source_missing":
            continue
        src = os.path.join(dest, row.rel)
        if _exists(src):
            target = os.path.join(target_root, row.rel)
            os.makedirs(_io(os.path.dirname(target)), exist_ok=True)
            used = _move_aside(src, target)
            catalog.delete(row.key)
            log.log(f"ORPHAN {row.rel}"
                    + ("" if used == target else f" (kept as {os.path.basename(used)})"))
            _prune_empty_parents(os.path.dirname(src), dest, log)
            moved += 1
        else:
            catalog.delete(row.key)
            log.log(f"ORPHAN (missing) {row.rel}")
    catalog.commit()
    return moved


def _folder_rows(to_copy):
    """(folder, files, bytes) of what will be copied, grouped by top folder:
    the first rel segment, or the first two when everything shares one."""
    firsts = {status.planned.rel.split("\\")[0] for status in to_copy}
    depth = 2 if len(firsts) == 1 and to_copy else 1
    groups = {}
    for status in to_copy:
        parts = status.planned.rel.split("\\")[:-1]
        folder = "\\".join(parts[:depth]) if parts else ""
        count, nbytes = groups.get(folder, (0, 0))
        groups[folder] = (count + 1, nbytes + status.planned.size)
    return sorted((folder, count, nbytes) for folder, (count, nbytes) in groups.items())


def _folder_summary_lines(folder_rows):
    """The 'to copy by folder' table lines (capped) and how many went to the CSV."""
    lines = []
    cumulative = 0
    width = min(40, max([12] + [len(folder) for folder, _count, _nbytes in folder_rows]))
    for folder, _count, nbytes in folder_rows:
        cumulative += nbytes
        lines.append(f"    {folder:<{width}}{_gb(nbytes):>12}   ({_gb(cumulative)})")
    more = max(0, len(lines) - SUMMARY_FOLDER_LINES)
    return lines[:SUMMARY_FOLDER_LINES], more


def _inside_name_lines(inside):
    """The session subfolders left out, grouped by name, the biggest first:
    a folder that is not an outtake stands out next to _OUTTAKES."""
    groups = {}
    for _session, name, count, nbytes in inside:
        entry = groups.setdefault(name.casefold(), [name, 0, 0])
        entry[1] += count
        entry[2] += nbytes
    biggest = sorted(groups.values(), key=lambda item: (-item[2], item[0].casefold()))
    biggest = biggest[:SUMMARY_INSIDE_NAMES]
    return [_summary_row(f"    {name}", count, nbytes) for name, count, nbytes in biggest]


def _orphan_csv_rows(orphans):
    return [(reason, row.rel, row.size, row.source_path, row.copied_at)
            for reason, row in orphans]


# ---- TIFF kept as JXL (jxl-photo) ------------------------------------------------------------
class _ConvertFailed(Exception):
    """A TIFF that could not be kept as JXL: it is copied as it is instead."""


@dataclass
class _Conversion:
    source_size: int
    source_mtime_ns: int
    source_sha256: str
    work: str               # the conversion's own folder (under JXL_WORK_FOLDER)
    files: list             # [(name, path, size, mtime_ns, sha256, page)], page 0 first
    kind: str = "tiff"      # "tiff" or "jpeg"
    lossless: object = None     # 1 / 0 / None (see _Converted)


def _tiff_lossless(profile, tokens):
    """Whether a TIFF's JXL is lossless: the distance its class got (jxl-photo's
    marks, JP-17: jxlphoto-scan -> scan_distance, jxlphoto-origin:tiff8 ->
    eight_bit_distance, else distance) is 0. Without the marks: 1 when the
    profile's three distances are 0, 0 when none is, None (not known) else."""
    distances = (profile["distance"], profile["scan_distance"], profile["eight_bit_distance"])
    if tokens.get("jxlphoto-scan"):
        return int(profile["scan_distance"] == 0)
    origin = [value.casefold() for value in tokens.get("jxlphoto-origin", [])]
    if origin == ["tiff8"]:
        return int(profile["eight_bit_distance"] == 0)
    if origin == ["tiff16"]:
        return int(profile["distance"] == 0)
    if all(value == 0 for value in distances):
        return 1
    if all(value > 0 for value in distances):
        return 0
    return None


def _version_key(text):
    """(major, minor, patch, final) of the first X.Y.Z in `text`, or None. A
    test version of X.Y.Z (2.30.0-rc1, 2.17.0_beta1) comes before X.Y.Z itself:
    it may not hold what X.Y.Z holds."""
    match = re.search(r"(\d+)\.(\d+)\.(\d+)(\S*)", str(text or ""))
    if not match:
        return None
    major, minor, patch, rest = match.groups()
    return int(major), int(minor), int(patch), 0 if rest else 1


def _jxlphoto_problem(path, minimum=None):
    """Why `path` cannot run jxl-photo, or None: also a jxl-photo that does not
    say its version, or one older than `minimum` (JXLPHOTO_MIN_VERSION; the
    restore gives JXLPHOTO_RESTORE_MIN_VERSION) -- the contract,
    docs/jxlphoto_contract.md, JP-1. Runs `encode --version` once per path."""
    minimum = minimum or JXLPHOTO_MIN_VERSION
    if not path:
        return "JXLPHOTO is not set (the path of jxl-photo's jxl_photo.py or jxl-photo.exe)"
    if not path.casefold().endswith((".py", ".exe")):
        return f"JXLPHOTO must be jxl-photo's jxl_photo.py or jxl-photo.exe, not {path}"
    if not os.path.isfile(_io(path)):
        return f"jxl-photo not found: {path}"
    version = _jxlphoto_version(path)
    key = _version_key(version)
    if key is None:
        return (f"{path} did not say its version (`encode --version`, JP-1 of "
                f"docs/jxlphoto_contract.md): is it jxl-photo {minimum} or newer?")
    if key < _version_key(minimum):
        return (f"{version} is older than {minimum}, the first that keeps "
                f"photo-backup's contract (docs/jxlphoto_contract.md): update jxl-photo")
    return None


def _jxlphoto_command(path):
    """The command that runs jxl-photo: the script with this Python (-B: no
    __pycache__ beside it -- the restore may run the copy kept in a backup,
    which it never writes to), or the exe."""
    if path.casefold().endswith(".py"):
        return [sys.executable, "-B", path]
    return [path]


# jxl-photo's commands are scripts beside jxl_photo.py, which only hands them
# on (its _BACKEND_COMMANDS; docs/jxlphoto_contract.md, JP-19).
_JXLPHOTO_BACKENDS = {"encode": "jxl_tiff_encoder", "decode": "jxl_tiff_decoder",
                      "transcode": "jxl_jpeg_transcoder"}
# Runs one of them as __main__ through Python's import system: its bytecode is
# kept (in _jxlphoto_env's PYTHONPYCACHEPREFIX), never compiled again. Nothing
# of the current folder is importable.
_JXLPHOTO_BOOT = ("import os, runpy, sys\n"
                  "folder, module = sys.argv[1], sys.argv[2]\n"
                  "sys.path[:] = [folder] + [p for p in sys.path if p not in ('', '.', os.getcwd())]\n"
                  "sys.argv = [os.path.join(folder, module + '.py')] + sys.argv[3:]\n"
                  "runpy.run_module(module, run_name='__main__', alter_sys=True)\n")


def _jxlphoto_argv(path, arguments):
    """The whole command line of one jxl-photo command (v0.14.0-rc4). A script
    whose command has its own script beside it (JP-19) runs that one as a
    module: Python reads its bytecode from this tool's cache instead of
    compiling jxl_photo.py and the backend again (19,000 lines, 0.4 s of CPU)
    at every start, which photo-backup makes once per photo. Anything else --
    the exe, an older layout, the fake of the tests -- as before."""
    module = _JXLPHOTO_BACKENDS.get(arguments[0]) if arguments else None
    if module and path.casefold().endswith(".py"):
        folder = os.path.dirname(os.path.abspath(path))
        if os.path.isfile(_io(os.path.join(folder, module + ".py"))):
            return [sys.executable, "-c", _JXLPHOTO_BOOT, folder, module] + list(arguments[1:])
    return _jxlphoto_command(path) + list(arguments)


def _jxlphoto_env(log_dir=None, temp_dir=None):
    """jxl-photo's environment: its log goes to `log_dir` (a conversion's own
    folder, which keeps it only when the conversion failed: _keep_jxlphoto_log)
    or to this tool's log folder -- never beside the copy kept in a backup --,
    no update check (no network from a backup), UTF-8 output, and no settings
    overlay (JXLPHOTO_SETTINGS set empty, JP-21): a .jxl_script_settings.json
    beside jxl-photo or in the user's folder is made for its own presets (a
    TEMP_DIR on another disk, its tags), and a backup runs jxl-photo's own
    defaults, the ones the contract test checks. Its bytecode goes to this
    tool's cache, `pycache` in the log folder (PYTHONPYCACHEPREFIX): every
    Python started with it reads and writes there and never a __pycache__
    beside a script -- the restore may run the copy kept in a backup, which it
    never writes to. `temp_dir` (a conversion's own folder, v0.14.0-rc5): its
    temp files (exiftool's argument files, a decode's fallback PNG) go there
    through TMPDIR, TEMP and TMP -- jxl-photo's TEMP_DIR is unset without
    the overlay, so Python's tempfile reads them --, never to the system's
    temp folder when the work folder is somewhere else (--work-dir), and
    they go away with the conversion's folder."""
    env = dict(os.environ)
    if temp_dir:
        for name in ("TMPDIR", "TEMP", "TMP"):
            env[name] = temp_dir
    env["JXLPHOTO_LOG_DIR"] = log_dir or os.path.join(os.path.abspath(LOG_DIR), "jxl-photo")
    env["JXLPHOTO_SETTINGS"] = ""
    env["JXLPHOTO_NO_UPDATE_CHECK"] = "1"
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env["PYTHONPYCACHEPREFIX"] = os.path.join(os.path.abspath(LOG_DIR), "pycache")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _jxlphoto_encode_args(tiff, folder, profile):
    """The encode command photo-backup gives jxl-photo (docs/jxlphoto_contract.md,
    JP-2): one TIFF into a folder of its own, the profile's three distances, and
    --verify-roundtrip: since jxl-photo 2.31.0-rc1 every page is decoded and
    compared with its TIFF page before it takes its name, and a page that fails
    or cannot be checked is an error with no JXL (JP-20; it needs djxl on PATH).
    Up to 2.30.x the flag checked nothing here."""
    return ["encode", tiff, folder, "--mode", "0",
            "--distance", str(profile["distance"]),
            "--scan-distance", str(profile["scan_distance"]),
            "--eight-bit-distance", str(profile["eight_bit_distance"]),
            "--effort", str(TIFF_JXL_EFFORT), "--workers", "1", "--verify-roundtrip"]


def _free_ram_gb():
    """The RAM free now, in GB (Windows), or None when it cannot be asked."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                        ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                        ("virtual_total", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong),
                        ("extended", ctypes.c_ulonglong)]
        status = _Status()
        status.length = ctypes.sizeof(_Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return status.free / 1e9
    except (OSError, AttributeError, ValueError):
        return None


def _physical_cores():
    """The CPU's physical cores (Windows: GetLogicalProcessorInformationEx), or
    None when they cannot be counted."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        relation_core = 0                   # RelationProcessorCore
        size = ctypes.c_ulong(0)
        kernel32.GetLogicalProcessorInformationEx(relation_core, None, ctypes.byref(size))
        if not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if not kernel32.GetLogicalProcessorInformationEx(relation_core, buffer,
                                                         ctypes.byref(size)):
            return None
        count = offset = 0
        while offset + 8 <= size.value:     # SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX
            relationship, entry = struct.unpack_from("<iI", buffer, offset)
            if entry <= 0:
                break
            count += relationship == relation_core
            offset += entry
        return count or None
    except (OSError, AttributeError, ValueError, struct.error):
        return None


def _jxl_workers():
    """How many files are converted at the same time: JXL_WORKERS, or (0) one
    per physical core -- measured 2026-11-19 on 16 cores: 12 to 32 at a time
    give the same speed, and 16 leaves a third of the CPU free and half the
    writers --, one per logical CPU where the cores cannot be counted."""
    if JXL_WORKERS > 0:
        return JXL_WORKERS
    return max(1, _physical_cores() or os.cpu_count() or 1)


def _jxl_tiff_workers():
    """How many of them may be TIFFs: JXL_TIFF_WORKERS, or (0) a quarter of the
    CPUs, at most one per 4 GB of free RAM, at least one."""
    if JXL_TIFF_WORKERS > 0:
        return JXL_TIFF_WORKERS
    count = max(1, (os.cpu_count() or 1) // 4)
    free = _free_ram_gb()
    if free:
        count = min(count, max(1, int(free // 4)))
    return count


DJXL_MISSING = ("djxl is not on PATH: jxl-photo needs it to check every page of a TIFF it "
                "converts (docs/jxlphoto_contract.md, JP-20)")


def _djxl_on_path():
    """The djxl jxl-photo itself finds (on PATH, the one it checks the pages of
    an encode with: JP-20), or None. Not DJXL: that setting is photo-backup's
    own choice for the JPEG proof."""
    return shutil.which("djxl")


def _work_folder_name_problem():
    """Why JXL_WORK_FOLDER is not one plain folder name, or None: with "" or
    ".." the folders a run sweeps would be another folder's (rc4 review)."""
    name = JXL_WORK_FOLDER
    if (not isinstance(name, str) or not name.strip() or name.strip(" .") == ""
            or any(char in name for char in "\\/:")):
        return (f"JXL_WORK_FOLDER must be one plain folder name (not empty, no \\, / or :), "
                f"not {name!r}")
    return None


def _work_parent(work_dir=None):
    """Where the conversions' folders are: `work_dir` (--work-dir; None =
    JXL_WORK_DIR), "" = the system's temp folder, + JXL_WORK_FOLDER."""
    base = JXL_WORK_DIR if work_dir is None else work_dir
    return os.path.join(os.path.abspath(base or tempfile.gettempdir()), JXL_WORK_FOLDER)


def _work_dir_errors(sources, dest, work_dir=None):
    """Why the conversions cannot run in their work folder, or []: a plain
    JXL_WORK_FOLDER, a work folder (`work_dir`: --work-dir, None = JXL_WORK_DIR;
    "" = the system's temp folder) that exists, outside every source
    (read-only) and outside the backup -- the default one too: a source that
    holds the system's temp folder needs the work folder somewhere else."""
    problem = _work_folder_name_problem()
    if problem:
        return [problem]
    base_setting = JXL_WORK_DIR if work_dir is None else work_dir
    who = "JXL_WORK_DIR" if work_dir is None or work_dir == JXL_WORK_DIR else "--work-dir"
    work = _work_parent(work_dir)
    where = "" if base_setting else " (the system's temp folder)"
    errors = [f"{who} overlaps a source: the conversions would run in {work}{where}, "
              f"inside or above {source}{_really(work, source)}; set {who} elsewhere"
              for source in sources if _overlaps(work, source)]
    if _overlaps(work, dest):
        errors.append(f"{who} overlaps the destination: the conversions would run in "
                      f"{work}{where}, inside or above {dest}{_really(work, dest)}")
    base = os.path.dirname(work)
    if not os.path.isdir(_io(base)):
        errors.append(f"{who} does not exist or is not a folder: {base}")
    return errors


_DJXL_FOUND = {}


def _djxl_path():
    """libjxl's djxl for rebuilding a JPEG (DJXL; "" = on PATH), or None:
    then jxl-photo rebuilds it (slower, the same bytes)."""
    if DJXL.strip().casefold() == "none":
        return None
    if DJXL.strip():
        return DJXL if os.path.isfile(_io(DJXL)) else None
    if "path" not in _DJXL_FOUND:
        _DJXL_FOUND["path"] = shutil.which("djxl")
    return _DJXL_FOUND["path"]


def _rebuild_jpeg(jxl, jxlphoto, folder, timeout_s, data=None, log_dir=None, temp_dir=None):
    """The bytes of the JPEG rebuilt from `jxl` (a JPEG kept as JXL): libjxl's
    djxl reading the JXL from stdin and writing the JPEG to stdout (JP-18: no
    path is ever given to it, no file is written), else jxl-photo into
    `folder` (JP-16; its log in `log_dir` and its temp files in `temp_dir`,
    see _jxlphoto_env). Raises _ConvertFailed."""
    djxl = _djxl_path()
    if djxl is not None:
        if data is None:                # `data`: the JXL's bytes, read already
            with open(_io(jxl), "rb") as handle:
                data = handle.read()
        command = ([sys.executable, "-B", djxl] if djxl.casefold().endswith(".py") else [djxl])
        try:
            run = _run_converter(command + ["-", "-", "--output_format", "jpeg",
                                            "--num_threads=1"], timeout_s, data=data)
        except subprocess.TimeoutExpired:
            raise _ConvertFailed(f"djxl took more than {timeout_s // 60} min")
        except OSError as exc:
            raise _ConvertFailed(f"djxl could not be started: {exc}")
        if run.returncode != 0 or not run.stdout:
            said = _tail(run.stderr.decode("utf-8", "replace"))
            raise _ConvertFailed(f"djxl could not rebuild the JPEG (exit {run.returncode}, "
                                 f"JP-18): {said}")
        return run.stdout
    os.makedirs(_io(folder), exist_ok=True)
    try:
        run = _run_jxlphoto(jxlphoto, _jxlphoto_rebuild_args(jxl, folder), timeout_s, log_dir,
                            temp_dir)
    except subprocess.TimeoutExpired:
        raise _ConvertFailed(f"jxl-photo took more than {timeout_s // 60} min")
    with os.scandir(_io(folder)) as listing:
        made = [entry.name for entry in listing if entry.is_file(follow_symlinks=False)
                and entry.name.casefold().endswith((".jpg", ".jpeg"))]
    if run.returncode != 0 or len(made) != 1:
        raise _ConvertFailed(f"the JPEG could not be rebuilt from its JXL (exit "
                             f"{run.returncode}, {len(made)} JPEG, JP-16)")
    with open(_io(os.path.join(folder, made[0])), "rb") as handle:
        return handle.read()


def _jxlphoto_transcode_args(jpeg, folder):
    """The command that keeps a JPEG as JXL (JP-15): jxl-photo's lossless JPEG
    transcode, never a lossy route (--force-transcode)."""
    return ["transcode", jpeg, folder, "--mode", "0", "--workers", "1",
            "--effort", str(JPEG_JXL_EFFORT), "--force-transcode"]


def _jxlphoto_rebuild_args(jxl, folder):
    """The command that rebuilds a JPEG from its JXL (JP-16): the same bytes."""
    return ["transcode", jxl, folder, "--mode", "0", "--workers", "1", "--format", "jpeg"]


def _jxlphoto_decode_args(folder, out):
    """The decode command the restore gives jxl-photo (JP-10): the JXL files of
    one TIFF in `folder`, back as one TIFF in `out`, with the pages and bit
    depths the TIFF had and no preview page of jxl-photo's own."""
    return ["decode", folder, out, "--mode", "0", "--workers", "1",
            "--depth-policy", "preserve_original", "--no-preview"]


# ---- Stopping with Ctrl+C (v0.14.0-rc5) -----------------------------------------------------------
# The converters running now (jxl-photo, djxl): Ctrl+C ends them, with what
# they started, instead of waiting for work whose result would be thrown away.
_CONVERTERS = set()
_CONVERTERS_LOCK = threading.Lock()
_STOPPING = threading.Event()       # Ctrl+C: no converter starts, the running ones end

STOP_SAYING = ("Ctrl+C: stopping at a safe point -- a few seconds (Ctrl+C again: at once; "
               "the next run cleans up)...")
STOPPED_SAYING = ("Stopped (Ctrl+C). What was copied is in the backup and in its catalog; the next "
                  "run goes on from where this one stopped. Nothing is being written now: the disk "
                  "can be removed.")


def _start_output():
    """Every script's first step: a cp1252 console cannot print the owner's ～
    paths (errors replaced), and each line goes out when it is printed, also
    into a pipe or a file (a scheduled run's log, another program reading it):
    block by block, a progress line or a stop could wait minutes to be seen
    (v0.14.0-rc5)."""
    try:
        sys.stdout.reconfigure(errors="replace", line_buffering=True)
    except (AttributeError, OSError, ValueError):
        pass


def _tell(text):
    """A line for the person waiting, at once; a console that is gone (its
    window closed) never stops what is being finished."""
    try:
        print(text, flush=True)
    except (OSError, ValueError):
        pass


def _say_stopping(log=None):
    """The first thing Ctrl+C prints, at once, and only once per run: a stop
    that takes a moment must never look like a program that hangs."""
    if not _STOPPING.is_set():
        _STOPPING.set()
        _tell(STOP_SAYING)
        if log is not None:
            log.log("INTERRUPTED (Ctrl+C): stopping at a safe point")


def _kill_tree(process):
    """End a converter and what it started (jxl-photo runs cjxl, djxl and
    exiftool): taskkill /T on Windows, else the process itself."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def _stop_converters():
    """Ctrl+C: no converter starts any more and every running one ends now.
    How many were running."""
    with _CONVERTERS_LOCK:
        _STOPPING.set()
        running = list(_CONVERTERS)
    for process in running:
        _kill_tree(process)
    return len(running)


def _run_converter(argv, timeout_s, env=None, data=None):
    """subprocess.run(argv, capture_output=True, timeout=timeout_s) for a
    converter, known to _stop_converters while it runs. A timeout ends it with
    what it started (subprocess.run ended only the converter itself). `data`:
    bytes for its stdin, and bytes come out; without it stdin is closed and the
    output is text (UTF-8). After Ctrl+C a converter does not start (OSError)."""
    if _STOPPING.is_set():
        raise OSError("not started: the run is stopping (Ctrl+C)")
    text = {"text": True, "encoding": "utf-8", "errors": "replace"} if data is None else {}
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **text)
    with _CONVERTERS_LOCK:
        _CONVERTERS.add(process)
        stopping = _STOPPING.is_set()
    try:
        if stopping:                    # Ctrl+C came while it started
            _kill_tree(process)
        try:
            stdout, stderr = process.communicate(data, timeout=timeout_s)
        except BaseException:
            _kill_tree(process)
            process.communicate()
            raise
    finally:
        with _CONVERTERS_LOCK:
            _CONVERTERS.discard(process)
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _wait_ending(futures, what):
    """After Ctrl+C: wait until `futures` are done (their converters were
    ended, so it is short), saying so every PROGRESS_EVERY_S. A second Ctrl+C
    stops the waiting: False."""
    started = said = time.monotonic()
    try:
        while True:
            running = [future for future in futures if not future.done()]
            if not running:
                return True
            _wait_futures(running, timeout=0.5)     # Ctrl+C reaches a wait only when it ends
            now = time.monotonic()
            if now - said >= PROGRESS_EVERY_S:
                _tell(f"  still stopping: {_num(len(running))} {what} ending "
                      f"({_hms(now - started)}; Ctrl+C again: at once)")
                said = now
    except KeyboardInterrupt:
        return False


@contextlib.contextmanager
def _ctrl_c_after():
    """A few steps that go together (a group's names and its catalog rows):
    Ctrl+C pressed meanwhile is raised when they are done. On the main thread
    only (the one Ctrl+C reaches); elsewhere nothing changes."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    pressed = []
    try:
        before = signal.signal(signal.SIGINT, lambda signum, frame: pressed.append(signum))
    except (ValueError, OSError):
        yield
        return
    if before is None:                  # a handler not set from Python: Python's own then
        before = signal.default_int_handler
    try:
        yield
    except BaseException as exc:
        signal.signal(signal.SIGINT, before)
        if pressed and not isinstance(exc, KeyboardInterrupt):
            raise KeyboardInterrupt from exc
        raise
    signal.signal(signal.SIGINT, before)
    if pressed:
        raise KeyboardInterrupt


def _run_jxlphoto(path, arguments, timeout_s, log_dir=None, temp_dir=None):
    """Run jxl-photo with `arguments`; the CompletedProcess (OSError and
    subprocess.TimeoutExpired go to the caller). `log_dir`, `temp_dir`: see
    _jxlphoto_env. Ctrl+C ends it (_run_converter)."""
    return _run_converter(_jxlphoto_argv(path, arguments), timeout_s,
                          env=_jxlphoto_env(log_dir, temp_dir))


_JXLPHOTO_VERSIONS = {}


def _jxlphoto_version(path):
    """'jxl-photo 2.30.2' from the encoder's own --version; 'jxl-photo ?' when
    it does not answer."""
    if path not in _JXLPHOTO_VERSIONS:
        text = ""
        try:
            run = _run_jxlphoto(path, ["encode", "--version"], 120)
            text = (run.stdout or run.stderr or "").strip().splitlines()
            text = text[0].strip() if text else ""
        except (OSError, subprocess.SubprocessError):
            text = ""
        match = re.search(r"(\d+\.\d+\.\d+\S*)", text)
        _JXLPHOTO_VERSIONS[path] = f"jxl-photo {match.group(1)}" if match else "jxl-photo ?"
    return _JXLPHOTO_VERSIONS[path]


def _tail(text, lines=4):
    kept = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return " | ".join(kept[-lines:])


_JXLPHOTO_ERROR = re.compile(r"^(?:\d\d:\d\d:\d\d \| )?ERROR\b(?: \| |:\s*)?")


def _jxlphoto_said(run):
    """What jxl-photo's output says about a failure: its first two ERROR lines
    (a page that failed its check says why there, a few lines above the end of
    the run), without the time; else its last lines."""
    text = (run.stdout or "") + "\n" + (run.stderr or "")
    errors = [_JXLPHOTO_ERROR.sub("", line.strip(), count=1)
              for line in text.splitlines() if _JXLPHOTO_ERROR.match(line.strip())]
    errors = [line for line in errors if line]
    return " | ".join(errors[:2]) if errors else _tail(text)


def _convert_one(status, work_root, stamp, index, jxlphoto, flush=True):
    """Keep one TIFF or JPEG as JXL with jxl-photo, in a folder of its own
    (work_root\\<index>, with jxl-photo's log of this call inside), and prove
    what came out (on a worker thread: no catalog here). Raises _ConvertFailed,
    after keeping jxl-photo's log of the call in LOG_DIR. `flush`: wait until
    each JXL is on the disk (it takes its final name by a rename)."""
    work = os.path.join(work_root, str(index))
    try:
        if status.planned.convert == JPEG_CONVERT:
            return _transcode_one(status, work, jxlphoto, flush)
        return _encode_one(status, work, jxlphoto, flush)
    except _ConvertFailed as exc:
        if _STOPPING.is_set():          # ended by Ctrl+C: not a failure, no log to keep
            raise
        kept = _keep_jxlphoto_log(work, stamp, index, status.planned.src)
        if kept:
            raise _ConvertFailed(f"{exc}; jxl-photo's log: {kept}") from None
        raise


def _conversion_temp(work):
    """Make a conversion's folder (`work`) and, inside it, the folder for
    jxl-photo's own temp files (`temp`, see _jxlphoto_env): its path. A folder
    jxl-photo writes no JXL into -- only the .jxl files directly in `work` are
    read -- and removed with `work`, whatever happened."""
    temp = os.path.join(work, "temp")
    os.makedirs(_io(temp))
    return temp


def _keep_jxlphoto_log(work, stamp, index, src):
    """The jxl-photo log of a failed conversion, copied out of its work folder
    (which goes away) into LOG_DIR\\jxl-photo: the path of the first, or None.
    A conversion that worked leaves none: one log per photo was 140,000 files
    in a backup of the whole archive."""
    found = []
    for dirpath, _dirs, names in os.walk(_io(os.path.join(work, "log"))):
        found += sorted(os.path.join(dirpath, name) for name in names
                        if name.casefold().endswith(".log"))
    if not found:
        return None
    folder = os.path.join(os.path.abspath(LOG_DIR), "jxl-photo")
    kept = None
    for number, path in enumerate(found):
        target = os.path.join(folder, f"{stamp}_{index}_{os.path.basename(src)}"
                                      + (f"_{number}" if number else "") + ".log")
        try:
            os.makedirs(_io(folder), exist_ok=True)
            shutil.copyfile(path, _io(target))
        except OSError:
            continue
        kept = kept or target
    return kept


def _transcode_one(status, work, jxlphoto, flush=True):
    """A JPEG: jxl-photo's lossless transcode (JP-15), then the JPEG rebuilt
    from that JXL (JP-16) must have the SHA-256 this run took of the JPEG --
    the same bytes, proven before the JXL counts --, and the JPEG must be the
    same at the end (JP-8)."""
    planned = status.planned
    jpeg = planned.src
    try:
        stat0 = os.stat(_io(jpeg), follow_symlinks=False)
        source_sha = _sha256_file(jpeg)
    except OSError as exc:
        raise _ConvertFailed(f"the JPEG cannot be read: {exc}")
    rebuilt = os.path.join(work, "rebuilt")             # jxl-photo's rebuild, without djxl
    temp = _conversion_temp(work)
    timeout_s = max(JXL_TIMEOUT_MIN, 1) * 60
    try:
        run = _run_jxlphoto(jxlphoto, _jxlphoto_transcode_args(jpeg, work), timeout_s,
                            os.path.join(work, "log"), temp)
    except subprocess.TimeoutExpired:
        raise _ConvertFailed(f"jxl-photo took more than {JXL_TIMEOUT_MIN} min")
    except OSError as exc:
        raise _ConvertFailed(f"jxl-photo could not be started: {exc}")
    said = _jxlphoto_said(run)
    if run.returncode != 0:
        raise _ConvertFailed(f"jxl-photo stopped with exit {run.returncode} (JP-15): {said}")
    with os.scandir(_io(work)) as listing:
        names = sorted(entry.name for entry in listing
                       if entry.is_file(follow_symlinks=False)
                       and entry.name.casefold().endswith(".jxl"))
    if len(names) != 1:
        raise _ConvertFailed(f"jxl-photo wrote {len(names)} JXL for one JPEG (JP-15): {said}")
    jxl = os.path.join(work, names[0])
    rebuilt_jpeg = _rebuild_jpeg(jxl, jxlphoto, rebuilt, timeout_s,
                                 log_dir=os.path.join(work, "log"), temp_dir=temp)
    if hashlib.sha256(rebuilt_jpeg).hexdigest() != source_sha:
        item = "JP-18" if _djxl_path() is not None else "JP-16"      # who rebuilt it
        raise _ConvertFailed(f"the JPEG rebuilt from its JXL is not the same bytes ({item})")
    try:
        stat1 = os.stat(_io(jpeg), follow_symlinks=False)
    except OSError as exc:
        raise _ConvertFailed(f"the JPEG cannot be read any more: {exc}")
    if (stat1.st_size, stat1.st_mtime_ns) != (stat0.st_size, stat0.st_mtime_ns):
        raise _ConvertFailed("the JPEG changed while it was converted (JP-8)")
    if flush:
        _flush_temp(jxl)
    stat = os.stat(_io(jxl))
    return _Conversion(stat0.st_size, stat0.st_mtime_ns, source_sha, work,
                       [(names[0], jxl, stat.st_size, stat.st_mtime_ns, _sha256_file(jxl), 0)],
                       kind="jpeg", lossless=1)


def _encode_one(status, work, jxlphoto, flush=True):
    """A TIFF: jxl-photo checked every page against the TIFF (JP-20: else it
    exits with an error), every JXL must carry the jxlphoto-srcsum of the bytes
    this run hashed, a group must be whole, and the TIFF must be the same at
    the end."""
    planned = status.planned
    tiff = planned.src
    try:
        stat0 = os.stat(_io(tiff), follow_symlinks=False)
        source_sha = _sha256_file(tiff)
    except OSError as exc:
        raise _ConvertFailed(f"the TIFF cannot be read: {exc}")
    temp = _conversion_temp(work)
    profile = TIFF_JXL_PROFILES[planned.convert]
    command = _jxlphoto_encode_args(tiff, work, profile)
    try:
        run = _run_jxlphoto(jxlphoto, command, max(JXL_TIMEOUT_MIN, 1) * 60,
                            os.path.join(work, "log"), temp)
    except subprocess.TimeoutExpired:
        raise _ConvertFailed(f"jxl-photo took more than {JXL_TIMEOUT_MIN} min")
    except OSError as exc:
        raise _ConvertFailed(f"jxl-photo could not be started: {exc}")
    said = _jxlphoto_said(run)
    if run.returncode != 0:
        item = "JP-20" if "round-trip verification" in said else "JP-2"
        raise _ConvertFailed(f"jxl-photo stopped with exit {run.returncode} ({item}): {said}")
    with os.scandir(_io(work)) as listing:
        names = sorted(entry.name for entry in listing
                       if entry.is_file(follow_symlinks=False)
                       and entry.name.casefold().endswith(".jxl"))
    if not names:
        raise _ConvertFailed(f"jxl-photo wrote no JXL (JP-3): {said}")
    want = _jxlphoto_srcsum(source_sha)
    stem = os.path.splitext(os.path.basename(tiff))[0].casefold()
    files = []
    lossless = None
    for name in names:
        path = os.path.join(work, name)
        tokens = _jxl_tokens(path)
        if lossless is None or tokens.get("jxlphoto-page", ["0"])[0] == "0":
            lossless = _tiff_lossless(profile, tokens)
        if want not in [value.casefold() for value in tokens.get("jxlphoto-srcsum", [])]:
            raise _ConvertFailed(f"{name} does not say it was made from these bytes "
                                 f"(jxlphoto-srcsum, JP-4/JP-5)")
        pages = tokens.get("jxlphoto-pages", [])
        if len(names) > 1 and not pages:
            raise _ConvertFailed(f"{len(names)} JXL without a group mark (jxlphoto-pages, JP-6)")
        if pages and any(value != str(len(names)) for value in pages):
            raise _ConvertFailed(f"the group is not whole: {len(names)} JXL, "
                                 f"jxlphoto-pages:{pages[0]} (JP-6)")
        page_token = tokens.get("jxlphoto-page", [])
        if page_token and page_token[0].isdigit():
            page = int(page_token[0])
        else:
            page = 0 if name[:-4].casefold() == stem else None
        files.append([name, path, 0, 0, None, page])
    try:
        stat1 = os.stat(_io(tiff), follow_symlinks=False)
    except OSError as exc:
        raise _ConvertFailed(f"the TIFF cannot be read any more: {exc}")
    if (stat1.st_size, stat1.st_mtime_ns) != (stat0.st_size, stat0.st_mtime_ns):
        raise _ConvertFailed("the TIFF changed while it was converted (JP-8)")
    taken = {item[5] for item in files if item[5] is not None}
    spare = (number for number in range(len(files)) if number not in taken)
    for item in files:
        if item[5] is None:
            item[5] = next(spare)
        if flush:
            _flush_temp(item[1])
        stat = os.stat(_io(item[1]))
        item[2], item[3] = stat.st_size, stat.st_mtime_ns
        item[4] = _sha256_file(item[1])
    files.sort(key=lambda item: (item[5], item[0].casefold()))
    return _Conversion(stat0.st_size, stat0.st_mtime_ns, source_sha, work,
                       [tuple(item) for item in files], kind="tiff", lossless=lossless)


def _keep_old_file(dest, stamp, rel, log):
    """The default home of a backup file a TIFF's JXL replaces: the versions
    folder (the mirror passes its own: the trash for what is ours)."""
    _move_file_to_versions(dest, stamp, rel, log)


def _retire_file(dest, stamp, rel, catalog, log, keep_old_file):
    """Move the backup file at `rel` aside (keep_old_file) and drop its row."""
    if os.path.lexists(_io(os.path.join(dest, rel))):
        keep_old_file(dest, stamp, rel, log)
    catalog.delete(rel.casefold())


def _write_jxl_temp(path, target, size, mtime_ns, sha256, verify):
    """One JXL of a finished conversion, copied from its work folder (another
    disk) to a temp file next to `target`: written, on the disk, its date the
    one recorded, then checked (`verify`, as --verify-copy: by default read back
    and compared with the SHA-256 taken in the work folder). Returns the temp's
    path; its final name is the caller's to give."""
    tmp = target + TEMP_SUFFIX
    try:
        if _exists(tmp):
            _delete_temp(tmp)
        with open(_io(path), "rb") as reader, open(_io(tmp), "xb") as writer:
            for chunk in _read_chunks(reader):
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        os.utime(_io(tmp), ns=(mtime_ns, mtime_ns))
        if verify in ("size", "hash") and os.stat(_io(tmp)).st_size != size:
            raise OSError(f"size mismatch after copy: {target}")
        if verify == "hash" and _sha256_file(tmp) != sha256:
            raise OSError(f"hash mismatch after copy: {target}")
        return tmp
    except BaseException:
        _remove_temp(tmp)
        raise


def _place_group(status, conversion, dest, stamp, catalog, log, keep_old_file, tool,
                 copy=False, verify="hash"):
    """On the main thread -- the one writer of the backup's disk: the JXL (or
    the group of a multipage TIFF) takes its place beside where the TIFF or JPEG
    would be (each name with the source's extension before .jxl) and the
    catalog learns it, with its kind and whether it is lossless. `copy` (the
    work folder is on another disk, v0.14.0-rc4): every file of the group is
    first written next to its final name and checked (_write_jxl_temp), and
    only then does an old group leave and do the names change; else each is
    renamed from its work folder. A copy it replaces is retired by the caller,
    after this."""
    planned = status.planned
    ext = os.path.splitext(planned.src)[1]
    tiff_rel = planned.rel[:-len(".jxl")]
    folder_rel = os.path.dirname(planned.rel)
    final = []
    for name, path, size, mtime_ns, sha256, page in conversion.files:
        file_name = name[:-len(".jxl")] + ext + ".jxl"
        rel = os.path.join(folder_rel, file_name) if folder_rel else file_name
        final.append((rel, path, size, mtime_ns, sha256, page))
    os.makedirs(_io(os.path.dirname(os.path.join(dest, planned.rel))), exist_ok=True)
    staged = []                                 # (where the bytes are now, final rel)
    try:
        for rel, path, size, mtime_ns, sha256, _page in final:
            if copy:
                staged.append((_write_jxl_temp(path, os.path.join(dest, rel), size, mtime_ns,
                                               sha256, verify), rel))
            else:
                staged.append((path, rel))
        # From here the group's names and its rows go together: Ctrl+C waits
        # for them (milliseconds), so a stop never leaves a JXL without its row
        # (v0.14.0-rc5)
        with _ctrl_c_after():
            for old_rel in status.old_outputs:
                _retire_file(dest, stamp, old_rel, catalog, log, keep_old_file)
            for source, rel in staged:
                target = os.path.join(dest, rel)
                if os.path.lexists(_io(target)):     # a file of another program in the way
                    _retire_file(dest, stamp, rel, catalog, log, keep_old_file)
                _rename_no_clobber(source, target)
            settings = _profile_settings(planned.convert)
            for rel, _path, size, mtime_ns, sha256, page in final:
                catalog.upsert(PlannedFile(planned.src, rel, size, mtime_ns, planned.category),
                               sha256, size, mtime_ns)
                catalog.record_converted(_Converted(
                    rel.casefold(), tiff_rel.casefold(), tiff_rel, conversion.source_size,
                    conversion.source_mtime_ns, conversion.source_sha256, page, len(final), tool,
                    settings, conversion.kind, conversion.lossless))
            log.log(f"CONVERTED {tiff_rel} -> {len(final)} JXL, "
                    f"{_gb(conversion.source_size)} -> {_gb(sum(item[2] for item in final))}")
    except BaseException:
        if copy:
            for source, _rel in staged:         # a temp of ours that has no final name yet
                _remove_temp(source)
        raise


def _copy_as_is(status, dest, stamp, catalog, log, keep_old_file, args,
                why="the conversion failed"):
    """A conversion that failed (or --no-convert, `why`): the TIFF or JPEG is
    backed up as it is, under its own name (nothing is left without a copy);
    the next run tries again. Returns the bytes copied (0: the copy there was
    current)."""
    planned = status.planned
    tiff_rel = planned.rel[:-len(".jxl")]
    copy = PlannedFile(planned.src, tiff_rel, planned.size, planned.mtime_ns, planned.category)
    dst = os.path.join(dest, tiff_rel)
    there = _listed_or_stat(dst, None)
    tolerance = int(MTIME_TOLERANCE_S * 1_000_000_000)
    if (there is not None and there[0] == planned.size
            and abs(there[1] - planned.mtime_ns) <= tolerance):
        catalog.adopt(copy)             # a copy of this TIFF is there already
        log.log(f"KEPT {tiff_rel} as it is ({why}; the copy there is current)")
        size = 0
    else:
        if there is not None or os.path.lexists(_io(dst)):
            _retire_file(dest, stamp, tiff_rel, catalog, log, keep_old_file)
        digest, size, mtime_ns = _copy_file(copy, dst, args.verify_copy)
        catalog.upsert(copy, digest, size, mtime_ns)
        log.log(f"COPY {tiff_rel} (as it is: {why})")
    # the JXL of an older version of this TIFF: the old copy of a changed file,
    # retired only now that the new bytes are in the backup
    for old_rel in status.old_outputs:
        _retire_file(dest, stamp, old_rel, catalog, log, keep_old_file)
    return size


def _copy_without_conversion(todo, args, dest, stamp, catalog, log, keep_old_file, info):
    """--no-convert: every TIFF or JPEG that would be converted is copied as it
    is, on the main thread (no jxl-photo, no work folder). Not a failure: the
    owner's choice for this run. Returns True when interrupted (Ctrl+C)."""
    total, started = len(todo), time.monotonic()
    last_report = started
    log.out(f"Conversion off (--no-convert): {_num(total)} TIFF/JPEG copied as they are "
            f"({_gb(sum(status.planned.size for status in todo))}); the JXL already in "
            f"the backup stay")
    interrupted = False
    try:
        for finished, status in enumerate(todo, 1):
            rel = status.planned.rel[:-len(".jxl")]
            try:
                info["bytes_as_is"] += _copy_as_is(status, dest, stamp, catalog, log,
                                                   keep_old_file, args, "conversion off")
                info["as_is"] += 1
            except (OSError, sqlite3.Error) as exc:
                info["errors"] += 1
                log.log(f"ERROR {rel}: {exc}")
            if finished % max(FLUSH_BATCH_FILES, 1) == 0:
                with _ctrl_c_after():
                    catalog.commit()
            now = time.monotonic()
            if now - last_report >= PROGRESS_EVERY_S or finished == total:
                elapsed = max(now - started, 1e-6)
                print(f"[{finished}/{total}] as they are (conversion off): "
                      f"{_gb(info['bytes_as_is'])}  "
                      f"ETA {_hms(elapsed / finished * (total - finished))}", flush=True)
                last_report = now
    except KeyboardInterrupt:
        interrupted = True
        _say_stopping(log)
        log.log("INTERRUPTED while copying with the conversion off")
    finally:
        with _ctrl_c_after():
            catalog.commit()
    return interrupted


def _remove_own_folder(path):
    """Remove one of this tool's own working folders (a conversion's, a temp
    copy of the tools): never a link, never anything outside it."""
    if (os.path.lexists(_io(path)) and not os.path.islink(_io(path))
            and not os.path.isjunction(_io(path))):
        shutil.rmtree(_io(path), ignore_errors=True)


def _pid_alive(pid):
    """Whether a process with this number runs now. Anything that cannot be
    told counts as running (its folder then waits for a later run)."""
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
            kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
            kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            handle = kernel32.OpenProcess(0x1000, False, pid)     # QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() != 87              # INVALID_PARAMETER: no such process
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True
                return code.value == 259                          # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except (OSError, AttributeError, ValueError):
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


_WORK_MARK = "photo-backup work folder.txt"   # in each run's work folder: the proof it is ours
_OLD_WORK_FOLDER = "converting"     # under CATALOG_FOLDER: where rc2 and rc3 converted


def _ours_to_sweep(folder):
    """A run's work folder that may be removed: named by a process number,
    holding our mark, and not a link."""
    name = os.path.basename(folder)
    return (name.isdigit() and os.path.isfile(_io(os.path.join(folder, _WORK_MARK)))
            and not os.path.islink(_io(folder)) and not os.path.isjunction(_io(folder)))


def _remove_work_root(root):
    """A run's work folder, at its end or a later run's sweep: everything in it
    first, our mark and the folder last. What cannot go yet (a file a
    converter ended by Ctrl+C still held) keeps the mark, so a later run
    removes the rest (_ours_to_sweep) -- never a folder left unmarked for good
    (v0.14.0-rc5)."""
    if (not os.path.isdir(_io(root)) or os.path.islink(_io(root))
            or os.path.isjunction(_io(root))):
        return
    try:
        with os.scandir(_io(root)) as listing:
            entries = [entry.name for entry in listing if entry.name != _WORK_MARK]
    except OSError:
        return
    for name in entries:
        path = os.path.join(root, name)
        if os.path.isdir(_io(path)) and not os.path.islink(_io(path)):
            _remove_own_folder(path)
        else:
            _remove_temp(path)
    try:
        if os.listdir(_io(root)) in ([], [_WORK_MARK]):
            _remove_temp(os.path.join(root, _WORK_MARK))
            os.rmdir(_io(root))
    except OSError:
        pass


def _work_root(work_dir=None):
    """This run's own folder for the conversions, made empty: `work_dir`
    (--work-dir; None = JXL_WORK_DIR; "" = the system's temp
    folder)\\JXL_WORK_FOLDER\\<process number>, with our mark in it. The
    folders there of backups that no longer run (a crash, a power cut) are
    removed first -- only a folder named by a process number that carries our
    mark, and only when no process has that number now. Raises OSError when
    the folder cannot be made (the caller then copies every file as it is)."""
    problem = _work_folder_name_problem()
    if problem:
        raise OSError(problem)
    parent = _work_parent(work_dir)
    try:
        with os.scandir(_io(parent)) as listing:
            for entry in listing:
                folder = os.path.join(parent, entry.name)
                if (entry.name.isdigit() and int(entry.name) != os.getpid()
                        and _ours_to_sweep(folder) and not _pid_alive(int(entry.name))):
                    _remove_work_root(folder)
    except OSError:
        pass
    root = os.path.join(parent, str(os.getpid()))
    if os.path.lexists(_io(root)):          # left by a run that had this number before
        if not _ours_to_sweep(root):
            raise OSError(f"{root} is there and is not a work folder of photo-backup")
        _remove_work_root(root)
    os.makedirs(_io(root))
    with open(_io(os.path.join(root, _WORK_MARK)), "w", encoding="utf-8") as handle:
        handle.write(f"photo-backup {__version__}, process {os.getpid()}, "
                     f"{datetime.now().isoformat(timespec='seconds')}: the conversions of one "
                     f"backup; removed when it ends, or by a later run when it died.\n")
    return root


def _jxl_copy_needed(work_root, dest):
    """Whether a finished JXL goes from the work folder to the backup by a copy
    (another volume: the system's temp folder) or by a rename. The volume, not
    the letter: a disk mounted in a folder of C: is another volume."""
    try:
        return os.stat(_io(work_root)).st_dev != os.stat(_io(dest)).st_dev
    except OSError:
        return not _same_drive(work_root, dest)


def _convert_all(args, statuses, dest, stamp, catalog, log, keep_old_file=None, info=None):
    """The TIFFs and JPEGs kept as JXL: jxl-photo converts each in a folder of
    its own in this run's work folder (_work_root: --work-dir, else
    JXL_WORK_DIR, by default the system's temp folder), JPEGs _jxl_workers() at a time and
    TIFFs _jxl_tiff_workers(); the main thread -- the one writer of the backup's
    disk -- puts each proven group in place, in turn, and records it
    (v0.14.0-rc4: before, every worker wrote on the backup's disk at once, the
    worst load for an SMR drive). Each pool runs at most twice its size ahead of
    that writer, so the work folder holds a few GB at most, however slow the
    backup's disk is. A file that cannot be converted is copied as it is.
    Returns True when interrupted (Ctrl+C)."""
    info = {} if info is None else info
    for name in ("converted", "failed", "retired", "bytes_in", "bytes_out", "errors",
                 "as_is", "bytes_as_is"):
        info.setdefault(name, 0)
    keep_old_file = keep_old_file or _keep_old_file
    todo = [status for status in statuses
            if status.planned.convert and _will_copy(status, args)]
    retire_only = [status for status in statuses
                   if status.planned.convert and status.replaces
                   and status.status == ST_UP_TO_DATE]
    for status in retire_only:          # the JXL is there: only the old TIFF copy goes
        try:
            _retire_file(dest, stamp, status.replaces, catalog, log, keep_old_file)
            info["retired"] += 1
        except OSError as exc:
            info["errors"] += 1
            log.log(f"ERROR retiring {status.replaces}: {exc}")
    catalog.commit()
    if not todo:
        return False
    if getattr(args, "no_convert", False):
        return _copy_without_conversion(todo, args, dest, stamp, catalog, log, keep_old_file,
                                        info)
    jxlphoto = getattr(args, "jxlphoto", None) or JXLPHOTO
    problem = _jxlphoto_problem(jxlphoto)
    tool = _jxlphoto_version(jxlphoto) if problem is None else "jxl-photo ?"
    workers, tiff_workers = _jxl_workers(), _jxl_tiff_workers()
    total = len(todo)
    kinds = collections.Counter("JPEG" if status.planned.convert == JPEG_CONVERT else "TIFF"
                                for status in todo)
    pace = []
    if kinds.get("JPEG"):
        pace.append(f"{workers} JPEG" + (" (djxl proves)" if _djxl_path() else ""))
    if kinds.get("TIFF"):
        pace.append(f"{min(workers, tiff_workers)} TIFF")
    log.out(f"Converting to JXL: {', '.join(f'{_num(count)} {kind}' for kind, count in sorted(kinds.items()))} "
            f"({tool}; {' and '.join(pace)} at a time; "
            f"{_gb(sum(status.planned.size for status in todo))})")
    started = time.monotonic()
    progress = {"finished": 0, "last_report": started}
    interrupted = False
    # where rc2 and rc3 converted, inside the backup: what an interrupted run left
    _remove_own_folder(os.path.join(dest, CATALOG_FOLDER, _OLD_WORK_FOLDER))
    work_root, copy = None, False
    try:
        work_root = _work_root(getattr(args, "work_dir", None))
        copy = _jxl_copy_needed(work_root, dest)
        log.log(f"Work folder of the conversions: {work_root} (each finished JXL "
                f"{'copied into the backup and checked' if copy else 'renamed into the backup'})")
    except OSError as exc:
        problem = problem or f"the conversions' work folder cannot be made: {exc}"
        log.log(f"ERROR {problem}")
    # JPEGs are light: one per core. A TIFF takes GB of RAM and cjxl's threads:
    # a pool of its own, smaller (JXL_TIFF_WORKERS). Each pool gets work only
    # while fewer than twice its size wait for the writer: [pool, files not
    # started, how many ahead at most, how many ahead now].
    jpeg_pool = ThreadPoolExecutor(max_workers=workers)
    tiff_pool = ThreadPoolExecutor(max_workers=min(workers, tiff_workers))
    queues = [[jpeg_pool, collections.deque(), 2 * workers, 0],
              [tiff_pool, collections.deque(), 2 * min(workers, tiff_workers), 0]]
    for index, status in enumerate(todo):
        queues[0 if status.planned.convert == JPEG_CONVERT else 1][1].append((index, status))
    pending = {}

    def submit_more():
        for queue in queues:
            while queue[1] and queue[3] < queue[2]:
                index, status = queue[1].popleft()
                future = queue[0].submit(_convert_one, status, work_root, stamp, index,
                                         jxlphoto, not copy)
                pending[future] = (index, status, queue)
                queue[3] += 1

    def finish(index, status, future):
        conversion, why = None, problem
        if future is not None:
            try:
                conversion = future.result()
            except _ConvertFailed as exc:
                why = str(exc)
            except Exception as exc:
                why = f"{type(exc).__name__}: {exc}"
        if conversion is not None:
            try:
                _place_group(status, conversion, dest, stamp, catalog, log, keep_old_file,
                             tool, copy, args.verify_copy)
                info["converted"] += 1
                info[conversion.kind] = info.get(conversion.kind, 0) + 1
                info["bytes_in"] += conversion.source_size
                info["bytes_out"] += sum(item[2] for item in conversion.files)
            except OSError as exc:
                why = f"the JXL could not be put in place: {exc}"
                conversion = None
        if conversion is not None and status.replaces:
            # the JXL is in the backup and in the catalog: only now does the
            # TIFF copy it replaces leave (a failure here is an error of its
            # own, never a failed conversion)
            try:
                _retire_file(dest, stamp, status.replaces, catalog, log, keep_old_file)
                info["retired"] += 1
            except OSError as exc:
                info["errors"] += 1
                log.log(f"ERROR retiring {status.replaces}: {exc}")
        if conversion is None:
            info["failed"] += 1
            log.log(f"CONVERT FAILED {status.planned.rel[:-len('.jxl')]}: {why}")
            try:
                _copy_as_is(status, dest, stamp, catalog, log, keep_old_file, args)
            except Exception as exc:
                info["errors"] += 1
                log.log(f"ERROR {status.planned.rel[:-len('.jxl')]}: {exc}")
        if future is not None:                  # its folder, whatever happened
            _remove_own_folder(os.path.join(work_root, str(index)))
        progress["finished"] += 1
        finished = progress["finished"]
        if finished % max(FLUSH_BATCH_FILES, 1) == 0:
            catalog.commit()
        now = time.monotonic()
        if now - progress["last_report"] >= PROGRESS_EVERY_S or finished == total:
            elapsed = max(now - started, 1e-6)
            eta = elapsed / finished * (total - finished)
            print(f"[{finished}/{total}] to JXL: {_gb(info['bytes_in'])} -> "
                  f"{_gb(info['bytes_out'])}, {info['failed']} kept as they are  "
                  f"ETA {_hms(eta)}")
            progress["last_report"] = now

    ended = False
    try:
        if problem is None:
            submit_more()
            while pending:
                # a timed wait: Ctrl+C reaches a wait on Windows only when it ends
                done, _waiting = _wait_futures(list(pending), timeout=0.5,
                                               return_when=FIRST_COMPLETED)
                for future in sorted(done, key=lambda item: pending[item][0]):
                    index, status, queue = pending.pop(future)
                    queue[3] -= 1
                    finish(index, status, future)
                submit_more()
        else:
            for index, status in enumerate(todo):
                finish(index, status, None)
        ended = True
    except KeyboardInterrupt:
        interrupted = True
        _say_stopping(log)
        log.log("INTERRUPTED while converting to JXL")
    finally:
        if ended:
            for pool in (jpeg_pool, tiff_pool):
                pool.shutdown(wait=True, cancel_futures=True)
        else:
            # Ctrl+C (or an error): the conversions running now are ended, not
            # waited for -- their result would be thrown away (v0.14.0-rc5)
            _stop_converters()
            for pool in (jpeg_pool, tiff_pool):
                pool.shutdown(wait=False, cancel_futures=True)
            _wait_ending(list(pending), "conversions")
        with _ctrl_c_after():
            catalog.commit()
        if work_root is not None:
            _remove_work_root(work_root)
    return interrupted


def _copy_all(args, statuses, dest, stamp, catalog, log, stop_info=None, old_copy=None,
              keep_old_file=None, convert_info=None):
    """Copy what the plan says. `stop_info` (a dict) receives why the copy
    stopped early and what was left, when the destination stopped taking
    writes: after DEST_CHECK_AFTER_ERRORS failed copies in a row the
    destination is tested, and a test that fails stops the reading too (B16).
    A catalog that can no longer be written (sqlite3.Error) is a destination
    that stopped taking writes too: nothing more is tried, and `unrecorded`
    says how many copies have their final name but no row (R1).
    `old_copy(dest, stamp, status, log)` decides what happens to the old copy
    of a CHANGED file (default: _handle_old_copy; the mirror passes its own).
    The TIFFs kept as JXL go through _convert_all after the copies
    (`keep_old_file`, `convert_info`)."""
    to_copy = [status for status in statuses
               if _will_copy(status, args) and not status.planned.convert]
    total_bytes = sum(status.planned.size for status in to_copy)
    done = 0
    done_bytes = 0
    error_count = 0
    in_a_row = 0
    uncommitted = 0
    unrecorded = 0
    catalog_error = None
    started = time.monotonic()
    last_report = started
    last_said = (0, 0)
    speed = _Speed()
    log.log(f"Copying {_num(len(to_copy))} files / {_gb(total_bytes)}")
    pending = []
    read_ahead = None
    if READ_AHEAD_MB > 0 and to_copy:
        read_ahead = _ReadAhead([status.planned for status in to_copy],
                                READ_AHEAD_MB * 1024 * 1024)
    flush_pool = ThreadPoolExecutor(max_workers=max(FLUSH_THREADS, 1))

    def catalog_failed(exc):
        """The rows not committed yet are lost with the transaction: those
        copies keep their final names, and the next run adopts them by their
        size and date (_adopt_rows)."""
        nonlocal catalog_error, unrecorded, uncommitted
        if catalog_error is None:
            catalog_error = str(exc) or type(exc).__name__
            log.log(f"CATALOG NOT WRITTEN ({catalog_error}): the copy stops")
        unrecorded += uncommitted
        uncommitted = 0

    def commit():
        nonlocal uncommitted
        if catalog_error is not None:
            return
        try:
            catalog.commit()
        except sqlite3.Error as exc:
            catalog_failed(exc)
            return
        uncommitted = 0

    def record(status, sha256, size, mtime_ns):
        nonlocal done, done_bytes, uncommitted, in_a_row, unrecorded
        done += 1
        done_bytes += status.planned.size
        in_a_row = 0
        log.log(f"COPY {status.planned.rel}")
        if catalog_error is not None:
            unrecorded += 1
            return
        try:
            catalog.upsert(status.planned, sha256, size, mtime_ns)
        except sqlite3.Error as exc:
            unrecorded += 1
            catalog_failed(exc)
            return
        uncommitted += 1

    def failed(status, exc):
        nonlocal error_count, in_a_row
        error_count += 1
        in_a_row += 1
        log.log(f"ERROR {status.planned.rel}: {exc}")

    def finish_pending():
        """Flush the waiting temp files together. Only a file already on the
        disk gets its final name, and only then does the catalog list it: a
        crash must not leave a garbage file that looks like a finished copy."""
        flush_errors = list(flush_pool.map(_flush_error, [item.tmp for item in pending]))
        for flush_error in flush_errors:
            item = pending[0]
            try:
                if catalog_error is not None:
                    raise OSError(f"not finished: the catalog cannot be written ({catalog_error})")
                if flush_error is not None:
                    raise flush_error
                record(item.status, *_finish_temp(
                    item.status.planned, item.tmp, item.status.dst, args.verify_copy,
                    item.source.digest, item.source.stat0, before_final=item.before_final))
            except Exception as exc:
                _remove_temp(item.tmp)
                failed(item.status, exc)
            pending.pop(0)
        commit()

    stopped = None
    try:
        for index, status in enumerate(to_copy):
            source = read_ahead.next_source(status.planned) if read_ahead else _SourceRead(status.planned)
            try:
                before_final = None
                if status.status == ST_CHANGED:
                    before_final = (old_copy or _handle_old_copy)(dest, stamp, status, log)
                if FLUSH_BATCH_FILES > 1 and status.planned.size < SMALL_FILE_KB * 1024:
                    pending.append(_stage_small_file(status, source, before_final))
                    in_a_row = 0            # the destination took this write
                    if len(pending) >= FLUSH_BATCH_FILES:
                        finish_pending()
                else:
                    record(status, *_copy_file(status.planned, status.dst, args.verify_copy,
                                               source=source, before_final=before_final))
            except Exception as exc:
                failed(status, exc)
            source.drain()
            if uncommitted >= max(FLUSH_BATCH_FILES, 1):
                commit()
            if catalog_error is not None:
                stopped = (f"the catalog could not be written: {catalog_error}", None,
                           to_copy[index + 1:])
                break
            now = time.monotonic()
            if to_copy and now - last_report >= PROGRESS_EVERY_S:
                if (done, done_bytes) == last_said and pending:
                    # small files are counted when their batch is on the disk:
                    # say that instead of the same line again (PENDENCIAS, 16/11)
                    print(f"  writing a batch of {_num(len(pending))} small files "
                          f"({_gb(sum(item.status.planned.size for item in pending))}) "
                          f"to the disk...")
                else:
                    print(f"[{done}/{len(to_copy)}] {_gb(done_bytes)}/{_gb(total_bytes)}  "
                          f"{speed.text(done_bytes, total_bytes)}")
                last_said = (done, done_bytes)
                last_report = now
            if DEST_CHECK_AFTER_ERRORS > 0 and in_a_row >= DEST_CHECK_AFTER_ERRORS:
                reason = _dest_accepts_writes(dest)
                log.log(f"DEST CHECK after {in_a_row} failed copies in a row: "
                        + ("the destination takes writes, going on" if reason is None
                           else f"FAILED ({reason})"))
                if reason is not None:
                    stopped = (reason, in_a_row, to_copy[index + 1:])
                    break
                in_a_row = 0
        finish_pending()
        if stopped is None and catalog_error is None:
            if _convert_all(args, statuses, dest, stamp, catalog, log, keep_old_file,
                            convert_info):
                raise KeyboardInterrupt
        if stopped is None and catalog_error is not None:
            # the last commit failed: every file was tried, none is left
            stopped = (f"the catalog could not be written: {catalog_error}", None, [])
        if stopped is not None:
            reason, failures, left = stopped
            log.log(f"STOPPED: {_num(len(left))} files not attempted")
            if unrecorded:
                log.log(f"CATALOG: {_num(unrecorded)} copied files have no row "
                        f"({catalog_error})")
            if stop_info is not None:
                stop_info.update(reason=reason, failures=failures, left=len(left),
                                 left_bytes=sum(item.planned.size for item in left),
                                 unrecorded=unrecorded)
    except KeyboardInterrupt:
        _say_stopping(log)
        for item in pending:
            _remove_temp(item.tmp)
        with _ctrl_c_after():
            commit()
        _tell(f"Interrupted: {done} copied, {error_count} errors")
        log.log(f"INTERRUPTED after {done} copied, {error_count} errors")
        return done, done_bytes, error_count, True
    finally:
        if read_ahead is not None:
            read_ahead.close()
        flush_pool.shutdown(wait=True)
    return done, done_bytes, error_count, False


_CATEGORY_LABELS = {
    CAT_EXPORT: "exports",
    CAT_SOURCE: "originals of the exported photos",
    CAT_ADDED: "files of added folders (AddFolders)",
    CAT_WHOLE: "whole folders (AddWhole, Whole)",
    CAT_CAPTURE_ONE: "Capture One settings",
    CAT_NX_STUDIO: "NX Studio settings (NKSC_PARAM)",
    CAT_UNSELECTED: "unselected originals (--unselected)",
    CAT_UNEXPORTED: "folders not exported yet, copied whole",
}


def _summary_lines(args, planned, statuses, needed, free, margin, fits, log_paths,
                   dest_error=None, extra_lines=(), freed=0, catalog_note="", orphan_lines=(),
                   lists=()):
    """The summary a person reads before the question (v0.14.0-rc4, PENDENCIAS
    of 16/11: tables of label | files | size, one fact per line, counts with
    separators, TB from 1,000 GB on, the lists named once at the end, the space
    last). `extra_lines`: more rows of "This run"; `orphan_lines`: rows of the
    orphans' block; `lists`: (file name, what it holds) of the CSVs."""
    new = [status for status in statuses if status.status == ST_NEW]
    changed = [status for status in statuses if status.status == ST_CHANGED]
    auto = [status for status in changed if _auto_updated(status, args)]
    up_to_date = [status for status in statuses if status.status == ST_UP_TO_DATE]
    sources = [os.path.abspath(source) for source in args.source]
    if len(sources) <= 3:
        source_text = ", ".join(sources)
    else:
        source_text = (f"{_num(len(sources))} folders: {sources[0]}, {sources[1]}, ... "
                       f"{sources[-1]}")
    lines = [_program_name()]
    bases = {base for _, base in planned.sources}
    suffix = f"   (base {', '.join(sorted(bases))})" if bases else ""
    dest = os.path.abspath(args.dest)
    catalog_path = os.path.join(dest, CATALOG_FOLDER, "catalog.sqlite")
    if not _exists(catalog_path):
        id_note = f"   NEW BACKUP (no {CATALOG_FOLDER} here yet)"
    else:
        backup_id = read_backup_id(dest)
        id_note = (f"   backup ID {backup_id[:8]}" if backup_id
                   else "   backup ID (none yet; created on the next copy)")
    lines.append(f"Destination:  {dest}{suffix}{id_note}")
    if catalog_note:
        lines.append(f"              catalog {catalog_note}")
    lines.append(f"Sources:      {source_text}")
    lines.append(f"Sessions: {_num(planned.stats.shoots)}   "
                 f"export folders: {_num(planned.stats.export_folders)}   "
                 f"export formats: {args.export_formats}")
    lines.append("")
    lines.append(_summary_header("This run"))
    lines.append(_summary_row("copy (new)", len(new), sum(s.planned.size for s in new)))
    to_copy = [status for status in statuses if _will_copy(status, args)]
    for category, label in _CATEGORY_LABELS.items():
        selected = [status for status in to_copy if status.planned.category == category]
        if selected:
            lines.append(_summary_row(f"  {label}", len(selected),
                                      sum(s.planned.size for s in selected)))
    lines.append(_summary_row(
        "replace (changed; the old copy to versions\\)", len(changed),
        sum(s.planned.size for s in changed) if changed else None,
        f"{_num(len(auto))} of them edit settings/sidecars, updated by themselves" if auto
        else ""))
    lines.append(_summary_row("already the same", len(up_to_date)))
    lines.extend(extra_lines)
    if orphan_lines:
        lines.append("")
        lines.append(_summary_header("In the backup, not on the source (orphans)"))
        lines.extend(orphan_lines)
    lines.append("")
    lines.append(_summary_header("Not copied"))
    lines.append(_summary_row("export files filtered (formats / excluded subfolders)",
                              planned.stats.export_filtered + planned.stats.export_excluded,
                              planned.stats.export_filtered_bytes
                              + planned.stats.export_excluded_bytes))
    lines.append(_summary_row("unselected originals", planned.stats.unselected_skipped,
                              planned.stats.unselected_skipped_bytes))
    lines.append(_summary_row("Capture One cache", None, planned.stats.c1_cache_skipped_bytes))
    if planned.cache_links:
        lines.append(f"    + {_num(len(planned.cache_links))} cache folders that are links to "
                     f"another place (not followed)")
    if planned.stats.add_filtered_skipped:
        lines.append(_summary_row("left out inside added folders (Originals filter)",
                                  planned.stats.add_filtered_skipped,
                                  planned.stats.add_filtered_skipped_bytes))
    if planned.stats.mode_excluded:
        lines.append(_summary_row("excluded by the mode (Exclude)", planned.stats.mode_excluded,
                                  planned.stats.mode_excluded_bytes))
    if planned.stats.derivatives_skipped:
        lines.append(_summary_row("jxl-photo derivatives (their master is copied)",
                                  planned.stats.derivatives_skipped,
                                  planned.stats.derivatives_skipped_bytes,
                                  "--derivatives copies them"))
    if planned.stats.manifest_skip_folders:
        lines.append(_summary_row("folders the manifest leaves out (Skip)",
                                  planned.stats.manifest_skip_files,
                                  planned.stats.manifest_skip_bytes,
                                  f"in {_num(planned.stats.manifest_skip_folders)} folders"))
    if planned.stats.only_filtered:
        lines.append(_summary_row("left out by a mode's Only filter", planned.stats.only_filtered,
                                  planned.stats.only_filtered_bytes))
    lines.append(_summary_row("inside sessions (subfolders the mode leaves out)",
                              planned.stats.inside_shoot_skipped,
                              planned.stats.inside_shoot_skipped_bytes))
    lines.extend(_inside_name_lines(planned.inside))
    not_copied = [folder for folder in planned.unexported_folders if not folder.copied]
    unexported_bytes = sum(folder.nbytes + folder.c1_nbytes + folder.nx_nbytes
                           for folder in not_copied)
    lines.append(_summary_row("folders without export", f"{_num(len(not_copied))} folders",
                              unexported_bytes))
    sessions = {session for session, _ in planned.unmatched}
    lines.append(_summary_row("exports without a matching original", len(planned.unmatched),
                              None, f"in {_num(len(sessions))} sessions"))
    if planned.links:
        lines.append(_summary_row("links/junctions not followed", len(planned.links), None,
                                  "listed in the log"))
    if planned.skipped_folders:
        lines.append(_summary_row("folders skipped by name (system / --exclude-folders)",
                                  f"{_num(len(planned.skipped_folders))} folders", None,
                                  "listed in the log"))
    if lists:
        folder = os.path.normpath(os.path.dirname(log_paths["log"]))
        lines.append("")
        lines.append(f"The lists, in {folder}:")
        width = max(len(name) for name, _what in lists)
        lines.extend(f"  {name:<{width}}   {what}" for name, what in lists)
    lines.append("")
    if planned.errors:
        lines.append(f"Errors: {_num(len(planned.errors))} folders/files could not be read "
                     f"(NOT backed up) -> {log_paths['log']}")
    if dest_error:
        lines.append(f"Space: need {_gb(needed)}, destination not available ({dest_error})")
    else:
        freed_note = f" + {_gb(freed)} freed by this run" if freed else ""
        lines.append(f"Space: need {_gb(needed)}, free {_gb(free)}{freed_note}, "
                     f"margin {_gb(margin)} -> " + ("fits" if fits else "DOES NOT FIT"))
    return lines


def _run_backup(args, log, stamp, plan_fn=None, mirror=None):
    """Plan, summarise, check the space, ask, then copy. `mirror` (a
    mirror_backup.MirrorJob) makes it a mirror: the job reviews what would
    leave the backup, prints its own block after the summary, may free space
    after a typed confirmation, confirms, removes before the copy and decides
    the old copy of each changed file. None = a normal backup, unchanged."""
    _STOPPING.clear()                   # a run starts with no Ctrl+C pressed
    dest = os.path.abspath(args.dest)
    if args.expect_backup_id:
        found = read_backup_id(dest)
        if found != args.expect_backup_id:
            print(f"Destination {dest} is not the expected backup "
                  f"(expected {args.expect_backup_id}, found {found or 'no backup here'})",
                  file=sys.stderr)
            log.log("ERROR expected backup ID does not match")
            return 2
    planned = (plan_fn or plan)(args)
    for error in planned.errors:
        log.log(error)
    for warning in planned.warnings:
        log.log(warning)
    for link in planned.links:
        log.log(f"LINK not followed {link}")
    for link in planned.cache_links:
        log.log(f"CACHE LINK not followed {link}")
    for folder in planned.skipped_folders:
        log.log(f"SKIPPED FOLDER {folder}")
    reserved = _reserved_rel_error(planned)
    if reserved:
        print(reserved, file=sys.stderr)
        log.log(f"ERROR {reserved}")
        return 2
    rows = []
    converted = {}
    candidates = {}
    try:
        read_only_catalog = _Catalog(dest, create=False, readonly=True)
    except FileNotFoundError:           # no backup here yet
        read_only_catalog = None
    except RuntimeError as exc:
        # A catalog that is there and cannot be read -- damaged, or not ours --
        # stops the dry-run too: the copy would stop on it after the question.
        print(str(exc), file=sys.stderr)
        log.log(f"ERROR {exc}")
        return 2
    catalog_note = ""
    if read_only_catalog is not None:
        catalog_note = read_only_catalog.writers_line()
        if catalog_note:
            log.log(f"catalog: {catalog_note}")
        try:
            rows = read_only_catalog.all_rows()
            converted = read_only_catalog.converted_rows()
        except sqlite3.Error as exc:
            message = f"cannot read catalog {read_only_catalog.path}: {exc}"
            print(message, file=sys.stderr)
            log.log(f"ERROR {message}")
            return 2
        finally:
            read_only_catalog.close()
    # the state of every planned file (a TIFF kept as JXL: from its group, which
    # only the catalog knows -- so the catalog is read first)
    listings = {} if mirror is not None else None
    statuses = _file_statuses(planned.files, dest, listings, _converted_groups(converted),
                              {row.key: row for row in rows})
    if getattr(args, "no_convert", False):
        statuses = _without_conversion(statuses, dest)
    to_copy = [status for status in statuses if _will_copy(status, args)]
    needed =sum(status.planned.size for status in to_copy)
    dest_error = None
    try:
        free = shutil.disk_usage(_io(_existing_ancestor(dest))).free
    except OSError as exc:      # drive not connected / not mounted
        free = 0
        dest_error = exc.strerror or str(exc)
    margin = int(float(args.space_margin_gb) * 1e9)
    # nothing to copy always fits: a full disk must not stop the removals of a
    # mirror (finding 9 of the rc1 review), nor a run with nothing new
    fits = dest_error is None and (needed == 0 or needed <= free - margin)
    log_paths = {
        "log": log.path,
        "plan": log.prefix + "_plan.csv",
        "unexported": log.prefix + "_unexported.csv",
        "unmatched": log.prefix + "_unmatched.csv",
        "orphans_csv": log.prefix + "_orphans.csv",
        "by_folder_csv": log.prefix + "_by_folder.csv",
        "inside": log.prefix + "_inside.csv",
    }
    link = _first_dest_link(dest, [item.rel for item in planned.files] + [row.rel for row in rows])
    if link is not None:
        message = (f"The backup holds a link or junction: {link} (it leads to {_real(link)}). "
                   f"The backup never writes, moves or deletes through one -- it could lead into "
                   f"a source. Take it out of the backup, then run again.")
        print(message, file=sys.stderr)
        log.log(f"ERROR {message}")
        if mirror is not None:
            mirror._stop(f"the backup holds a link or junction: {link}",
                         "take that link or junction out of the backup (the backup never "
                         "writes one), then run the mirror again")
        return 2
    if rows and args.detect_moves:
        candidates = _find_move_candidates(statuses, rows, dest, planned.sources)
    prefixes = _source_prefixes(planned)
    tentative_used = set()
    for _status, matches in candidates.values():
        tentative_used.update(row.key for row in matches)
    # The orphans are looked for twice (for the summary, and again after the
    # moves). Without --move-orphans the second list only rewrites the CSV: it
    # reuses the answers of the first, one question per orphan instead of two.
    # With --move-orphans every source is asked again, right before the copy.
    orphan_states = {}
    tentative_orphans = (_find_orphans(rows, statuses, prefixes, tentative_used, planned.sources,
                                       orphan_states)
                         if rows else [])
    extra_lines = []
    lists = [(os.path.basename(log_paths["plan"]), "every planned file and what happens to it")]
    possible = [status for status, _matches in candidates.values()]
    if possible:
        extra_lines.append(_summary_row(
            "possible moves (each checked by its hash first)", len(possible),
            sum(status.planned.size for status in possible),
            "same name and size as a backup file whose source is gone"))
    converts = [status for status in statuses if status.planned.convert]
    for kind, label, matches in (
            ("TIFF", None, lambda status: status.planned.convert != JPEG_CONVERT),
            ("JPEG", "lossless: the same bytes back", lambda status:
             status.planned.convert == JPEG_CONVERT)):
        these = [status for status in converts if matches(status)]
        if not these:
            continue
        settings = label or "; ".join(_profile_settings(name)
                                      for name in sorted({status.planned.convert
                                                          for status in these}))
        to_make = [status for status in these if _will_copy(status, args)]
        same = [status for status in these if status.status == ST_UP_TO_DATE]
        if getattr(args, "no_convert", False):
            extra_lines.append(_summary_row(f"{kind}: copied as they are (conversion off)",
                                            len(to_make),
                                            sum(status.planned.size for status in to_make),
                                            "--no-convert: no JXL made"))
            extra_lines.append(_summary_row(f"{kind}: already in the backup (JXL or not)",
                                            len(same)))
            continue
        extra_lines.append(_summary_row(f"{kind} kept as JXL: to convert", len(to_make),
                                        sum(status.planned.size for status in to_make),
                                        settings))
        extra_lines.append(_summary_row(f"{kind} kept as JXL: already in the backup", len(same)))
    retire = [status for status in converts if status.replaces]
    if retire:
        where = "the mirror's trash" if mirror is not None else "versions\\"
        extra_lines.append(_summary_row(f"copies their JXL replaces (to {where})", len(retire)))
    folder_rows = _folder_rows(to_copy)
    if len(folder_rows) >= 2:
        extra_lines.append("  to copy by folder (cumulative):")
        folder_lines, more = _folder_summary_lines(folder_rows)
        extra_lines.extend(folder_lines)
        if more:
            extra_lines.append(f"    ... {_num(more)} more folders (in the list below)")
            lists.append((os.path.basename(log_paths["by_folder_csv"]),
                          "what is copied, folder by folder"))
    gone = [row for reason, row in tentative_orphans if reason == "source_missing"]
    unselected = [row for reason, row in tentative_orphans if reason == "not_selected"]
    unreadable = [row for reason, row in tentative_orphans if reason == "source_unreadable"]
    elsewhere = [row for reason, row in tentative_orphans if reason == "other_source"]
    orphan_lines = []
    if rows:
        orphan_lines.append(_summary_row("source gone", len(gone), sum(row.size for row in gone)))
        orphan_lines.append(_summary_row("no longer selected", len(unselected),
                                         sum(row.size for row in unselected)))
        if unreadable:
            orphan_lines.append(_summary_row("source cannot be read (never counted as gone)",
                                             len(unreadable),
                                             sum(row.size for row in unreadable)))
        if elsewhere:
            orphan_lines.append(_summary_row("from a source this run does not name",
                                             len(elsewhere), sum(row.size for row in elsewhere)))
        if tentative_orphans:
            lists.append((os.path.basename(log_paths["orphans_csv"]), "the orphans"))
    if planned.inside:
        lists.append((os.path.basename(log_paths["inside"]),
                      "the subfolders left out inside sessions"))
    if any(not folder.copied for folder in planned.unexported_folders):
        lists.append((os.path.basename(log_paths["unexported"]), "the folders without export"))
    if planned.unmatched:
        lists.append((os.path.basename(log_paths["unmatched"]),
                      "the exports without a matching original"))
    lists.append((os.path.basename(log_paths["log"]),
                  "the log: links, folders skipped by name, errors"))
    freed = 0
    mirror_lines = []
    if mirror is not None:
        mirror_lines, freed = mirror.review(planned, statuses, rows, prefixes, tentative_orphans,
                                            tentative_used, listings, dest, log, possible)
        fits = dest_error is None and (needed == 0 or needed <= free + freed - margin)
    orphans_blocked = (f"nothing moved: {_num(len(planned.errors))} errors in the plan of this "
                       f"run (a source that cannot be read is not one that is gone)")
    if args.move_orphans and args.dry_run:
        if planned.errors:
            orphan_lines.append(f"  --move-orphans: {orphans_blocked}")
        else:
            orphan_lines.append(
                f"  --move-orphans: {_num(len(gone))} files would be moved to "
                f"{os.path.join(dest, CATALOG_FOLDER, ORPHANS_FOLDER, stamp)}")
    for line in _summary_lines(args, planned, statuses, needed, free, margin, fits, log_paths,
                               dest_error, extra_lines, freed, catalog_note, orphan_lines,
                               lists):
        log.out(line)
    # the mirror's own block comes last: it is what is on the screen when the
    # question is asked
    for line in mirror_lines:
        log.out(line)
    read_errors = 1 if planned.errors else 0
    # A dry-run lists every planned file. A run that copies lists what it will
    # change: the UP_TO_DATE lines of a nightly run were 83 MB of "nothing".
    listed = [status for status in statuses
              if args.dry_run or PLAN_CSV_UP_TO_DATE or status.status != ST_UP_TO_DATE]
    log.csv("_plan.csv", ["action", "category", "size", "mtime", "src", "rel"],
            [(status.status, status.planned.category, status.planned.size,
              _csv_date(status.planned.mtime_ns), status.planned.src, status.planned.rel)
             for status in listed])
    if len(listed) != len(statuses):
        log.log(f"_plan.csv: {_num(len(statuses) - len(listed))} UP_TO_DATE files not listed "
                f"(PLAN_CSV_UP_TO_DATE; a --dry-run lists every file)")
    log.csv("_unexported.csv", ["folder", "files", "bytes", "c1_bytes", "nx_bytes", "copied"],
            [(folder.path, folder.files, folder.nbytes, folder.c1_nbytes, folder.nx_nbytes,
              "yes" if folder.copied else "no")
             for folder in planned.unexported_folders])
    log.csv("_unmatched.csv", ["session", "export_file"],
            [(session, path) for session, path in planned.unmatched])
    log.csv("_inside.csv", ["session", "subfolder", "files", "bytes"], planned.inside)
    cumulative = 0
    by_folder_csv = []
    for folder, count, nbytes in folder_rows:
        cumulative += nbytes
        by_folder_csv.append((folder, count, nbytes, cumulative))
    log.csv("_by_folder.csv", ["folder", "files", "bytes", "cumulative_bytes"],
            by_folder_csv)
    log.csv("_orphans.csv", ["reason", "rel", "size", "source_path", "copied_at"],
            _orphan_csv_rows(tentative_orphans))
    for status in statuses:
        if status.status == ST_CHANGED and not _will_copy(status, args):
            log.log(f"SKIP CHANGED {status.planned.rel}")
    if dest_error:
        message = f"Destination not available: {dest} ({dest_error})"
        if args.dry_run:
            print(f"WARNING: {message} - space not checked")
            log.log(f"WARNING: {message}")
            return read_errors
        print(message, file=sys.stderr)
        log.log(message)
        return 2
    if not fits:
        freed_note = f" + {_gb(freed)} freed by this run" if freed else ""
        message = (f"Not enough space: need {_gb(needed)}, free {_gb(free)}{freed_note}, "
                   f"margin {_gb(margin)}")
        # The count stays closed (B15): a possible move is only confirmed by
        # hash later, so it is counted as a copy; the message says how much.
        moves_note = ""
        if possible:
            moves_note = (f"; {_gb(sum(status.planned.size for status in possible))} of it "
                          f"may be files moved inside the backup (possible moves, confirmed "
                          f"by hash when copying), which would need no new space")
        message += moves_note
        if mirror is not None:
            for line in mirror.space_lines(needed, free + freed, margin):
                log.out(line)
        if args.dry_run:
            print(f"WARNING: would not fit ({_gb(needed)} needed, {_gb(free)} free{freed_note}, "
                  f"{_gb(margin)} margin{moves_note})")
            log.log("WARNING: would not fit")
            return read_errors
        if mirror is None or not mirror.free_space(needed, free + freed, margin):
            print(message, file=sys.stderr)
            log.log(message)
            return 2
    if args.dry_run:
        log.log("dry run: nothing copied")
        return read_errors
    if mirror is not None and not mirror.confirm():
        return 2
    if not args.yes:
        try:
            answer = input("Proceed? [y/N, Enter = no] " if mirror is None
                           else mirror.proceed_prompt())
        except EOFError:
            print("Aborted.")
            log.log("Aborted (no answer)")
            return 2
        except KeyboardInterrupt:
            print("Aborted.")
            log.log("Aborted (interrupted)")
            return 2
        if answer.strip().casefold() not in ("y", "yes"):
            print("Aborted.")
            log.log("Aborted by the user")
            if mirror is not None:
                mirror._stop("the answer was no", "nothing to do: run the mirror again when "
                                                   "you want it")
            return 2
    try:
        catalog = _Catalog(dest, create=True)
    except (RuntimeError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        log.log(f"ERROR {exc}")
        return 2
    except OSError as exc:      # an invalid name ("E:\x|y"), a drive that went away
        message = f"Destination not available: {dest} ({exc.strerror or exc})"
        print(message, file=sys.stderr)
        log.log(f"ERROR {message}")
        return 2
    done = done_bytes = error_count = 0
    moved = moved_bytes = move_errors = 0
    interrupted = False
    stop_info = {}
    mirror_problems = False
    convert_info = {}
    try:
        _ensure_backup_id(dest, log)
        for line in _keep_tools(dest, log, getattr(args, "jxlphoto", None) or JXLPHOTO):
            log.out(line)
        _adopt_rows(catalog, statuses)
        if candidates:
            moved, moved_bytes, move_errors, interrupted = _apply_moves(
                candidates, dest, catalog, log)
        if interrupted:
            return 130
        # --move-orphans and the mirror ask every source again, right now
        fresh = args.move_orphans or mirror is not None
        orphans = _find_orphans(catalog.all_rows(), statuses, prefixes, set(), planned.sources,
                                None if fresh else orphan_states)
        log.csv("_orphans.csv", ["reason", "rel", "size", "source_path", "copied_at"],
                _orphan_csv_rows(orphans))
        if mirror is not None:
            mirror.remove(dest, stamp, catalog, orphans, log)
            if mirror.interrupted:
                # what was removed before Ctrl+C, and where it is (finding 6)
                mirror.finish(log, 0, 0, 0, interrupted=True)
                return 130
        done, done_bytes, error_count, interrupted = _copy_all(
            args, statuses, dest, stamp, catalog, log, stop_info,
            mirror.old_copy if mirror is not None else None,
            mirror.keep_old_file if mirror is not None else None, convert_info)
        log.log(f"moved {moved} files / {_gb(moved_bytes)}")
        log.out(f"copied {done} files / {_gb(done_bytes)}; {error_count} errors")
        if any(convert_info.get(name) for name in ("converted", "failed", "retired")):
            made = ", ".join(f"{_num(convert_info[kind])} {kind.upper()}"
                             for kind in ("tiff", "jpeg") if convert_info.get(kind))
            log.out(f"Kept as JXL: {_num(convert_info['converted'])} converted"
                    + (f" ({made})" if made else "")
                    + f", {_gb(convert_info['bytes_in'])} -> {_gb(convert_info['bytes_out'])}")
            if convert_info["retired"]:
                log.out(f"  {_num(convert_info['retired'])} copies in the backup replaced by "
                        f"their JXL (to {'the trash' if mirror is not None else 'versions'})")
            if convert_info["failed"]:
                log.out(f"  {_num(convert_info['failed'])} copied as they are: the conversion "
                        f"failed (CONVERT FAILED in the log)")
        if convert_info.get("as_is"):
            log.out(f"Conversion off: {_num(convert_info['as_is'])} TIFF/JPEG kept as they are "
                    f"({_gb(convert_info['bytes_as_is'])} copied); a run without --no-convert "
                    f"converts them")
        if error_count or move_errors:
            log.out(f"ERRORS: {_num(error_count + move_errors)} files were NOT copied -> {log.path}")
        if stop_info:
            why = stop_info["reason"]
            if stop_info["failures"] is not None:
                why += f"; tested after {_num(stop_info['failures'])} failed copies in a row"
            log.out(f"STOPPED: the destination no longer takes writes "
                    f"({why}): {_num(stop_info['left'])} files / "
                    f"{_gb(stop_info['left_bytes'])} were left to copy and not read")
            if stop_info.get("unrecorded"):
                log.out(f"CATALOG: {_num(stop_info['unrecorded'])} copied files have no row "
                        f"in the catalog (it could not be written); they keep their final "
                        f"names and the next run adopts them by their size and date")
        if mirror is not None:
            mirror.convert_info = convert_info
            mirror_problems = mirror.finish(log, done, done_bytes, error_count, interrupted)
        if interrupted:
            return 130
        if args.move_orphans and stop_info:
            log.out("--move-orphans: nothing moved: the destination no longer takes writes")
        elif args.move_orphans and planned.errors:
            log.out(f"--move-orphans: {orphans_blocked}")
        elif args.move_orphans:
            moved_orphans = _move_orphans(dest, stamp, orphans, catalog, log)
            log.log(f"moved {moved_orphans} orphans to "
                    f"{os.path.join(dest, CATALOG_FOLDER, ORPHANS_FOLDER, stamp)}")
    except sqlite3.Error as exc:
        # outside the copy itself (_copy_all has its own): recording the files
        # already there, the moves, the orphans
        log.out(f"ERROR the catalog could not be read or written ({exc}): {catalog.path}")
        return 1 if done or moved else 2
    finally:
        catalog.close()
        if interrupted or (mirror is not None and getattr(mirror, "interrupted", False)):
            # only now, with the catalog closed: the disk may go (v0.14.0-rc5)
            log.log("STOPPED by Ctrl+C")
            _tell(STOPPED_SAYING)
    return 1 if (error_count or move_errors or planned.errors or stop_info or mirror_problems
                 or convert_info.get("failed") or convert_info.get("errors")) else 0


@dataclass
class _VerifyJob:
    row: _Row
    in_catalog: bool
    dest_size: int = None       # from the listing of its folder (quick verify); None = ask
    source_listed: tuple = None     # (size, mtime_ns) of the source from the plan's listing
                                    # (quick verify, VERIFY_QUICK_USES_PLAN); None = ask
    converted: object = None    # a _Converted: this file is a TIFF kept as JXL


def _same_drive(first, second):
    """Whether two paths are on one drive letter (or one share)."""
    return (os.path.splitdrive(os.path.abspath(first))[0].casefold()
            == os.path.splitdrive(os.path.abspath(second))[0].casefold())


def _hash_or_error(path, cancel=None):
    """(sha256, None) of a file, or (None, the error) when it cannot be read:
    a file that gives a read error is a result of the verify, not its end."""
    try:
        if cancel is None:
            return _sha256_file(path), None
        return _sha256_file(path, cancel), None
    except (OSError, _Cancelled) as exc:
        return None, exc


class _CopyReader:
    """Reads backup copies on a thread of its own, so that --verify full reads
    the copy and its source at the same time: two disks, the time of the
    slower one instead of the sum (hashlib releases the GIL). One file at a
    time; the results are used on the main thread, where the catalog lives."""

    def __init__(self):
        self.cancel = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=1)

    def parallel(self, copy, source):
        """Two files of one disk are read one after the other: together, its
        head would only travel between them."""
        return VERIFY_PARALLEL and not _same_drive(copy, source)

    def start(self, path):
        """Start reading `path`; the returned function waits for its
        (sha256, error). A timed wait: Ctrl+C reaches a wait on Windows only
        when it ends."""
        future = self._pool.submit(_hash_or_error, path, self.cancel)

        def result():
            while True:
                try:
                    return future.result(timeout=0.2)
                except _FutureTimeout:
                    continue
        return result

    def close(self):
        self.cancel.set()
        self._pool.shutdown(wait=True)


def _dest_available(dest):
    """Whether the backup still answers: its catalog file can be seen. A copy
    that is "missing" on a drive that went away is not a missing copy."""
    try:
        os.stat(_io(os.path.join(os.path.abspath(dest), CATALOG_FOLDER, "catalog.sqlite")))
    except OSError:
        return False
    return True


def _error_text(exc):
    return getattr(exc, "strerror", None) or str(exc) or type(exc).__name__


def _verify_row(job, dest, deep, reader=None):
    """Check one catalogued file. A file that cannot be read -- the copy or
    the source -- is a status (DEST_UNREADABLE, SOURCE_UNREADABLE), never an
    exception: a read error is what a failing disk shows first. `reader` (a
    _CopyReader) reads the copy while this thread reads the source."""
    row = job.row
    dst = os.path.join(dest, row.rel)
    dest_size = job.dest_size
    if dest_size is None:
        try:
            dest_size = os.stat(_io(dst), follow_symlinks=False).st_size
        except FileNotFoundError:
            return VerifyResult("DEST_MISSING", row.rel, row.source_path,
                                "destination file missing")
        except OSError as exc:
            return VerifyResult("DEST_UNREADABLE", row.rel, row.source_path,
                                f"backup copy cannot be read: {_error_text(exc)}")
    if dest_size != row.size:
        return VerifyResult("DEST_SIZE", row.rel, row.source_path,
                            f"destination size {dest_size} != {row.size}")
    if not deep and job.source_listed is not None:
        # the plan listed this very file seconds ago (VERIFY_QUICK_USES_PLAN)
        state = SOURCE_PRESENT
        source_size, source_mtime_ns = job.source_listed
    else:
        state, source_stat = _source_stat(row.source_path)
        source_size = source_stat.st_size if source_stat is not None else None
        source_mtime_ns = source_stat.st_mtime_ns if source_stat is not None else None
    tolerance = int(MTIME_TOLERANCE_S * 1_000_000_000)
    # a TIFF kept as JXL: the source is the TIFF the conversion recorded, the
    # copy is the JXL with its own size and hash
    conv = job.converted
    source_want = ((conv.source_size, conv.source_mtime_ns, conv.source_sha256)
                   if conv is not None else (row.size, row.mtime_ns, row.sha256))
    source_same = (state == SOURCE_PRESENT and source_size == source_want[0]
                   and abs(_db_ns(source_mtime_ns) - source_want[1]) <= tolerance)
    # The backup copy is judged BEFORE the source: when the original is gone
    # or changed, the copy may be the only one left, and its health matters
    # most. A copy with a catalog hash is always read; one without is read
    # only to be compared with a source that still has the row's size and date.
    hash_dst = hash_src = dest_error = source_error = None
    if deep and (row.sha256 or source_same):
        if source_same and reader is not None and reader.parallel(dst, row.source_path):
            waiting = reader.start(dst)
            hash_src, source_error = _hash_or_error(row.source_path)
            hash_dst, dest_error = waiting()
        else:
            hash_dst, dest_error = _hash_or_error(dst)
            copy_bad = dest_error is not None or bool(row.sha256 and hash_dst != row.sha256)
            if source_same and not copy_bad:
                hash_src, source_error = _hash_or_error(row.source_path)
    if dest_error is not None:
        return VerifyResult("DEST_UNREADABLE", row.rel, row.source_path,
                            f"backup copy cannot be read: {_error_text(dest_error)}")
    dest_verified = False
    if deep and row.sha256:
        if hash_dst != row.sha256:
            return VerifyResult("DEST_CORRUPT", row.rel, row.source_path,
                                "destination sha256 differs from the catalog")
        dest_verified = True
        if conv is not None and conv.kind != "jpeg":
            # a JPEG's JXL carries no marks (they would break its rebuilding):
            # it was proven byte for byte when it was made
            sums = [value.casefold()
                    for value in _jxl_tokens(dst).get("jxlphoto-srcsum", [])]
            if _jxlphoto_srcsum(conv.source_sha256) not in sums:
                return VerifyResult("DEST_NOT_FROM_SOURCE", row.rel, row.source_path,
                                    "the JXL does not say it was made from the TIFF the "
                                    "catalog recorded (jxlphoto-srcsum, JP-5)")
    copy_note = "; backup copy hash verified" if dest_verified else ""
    if state == SOURCE_MISSING:
        return VerifyResult("SOURCE_MISSING", row.rel, row.source_path,
                            "source file missing" + copy_note, dest_verified=dest_verified)
    if state == SOURCE_UNREADABLE:
        return VerifyResult("SOURCE_UNREADABLE", row.rel, row.source_path,
                            "source cannot be read (not proof that it is gone)" + copy_note,
                            dest_verified=dest_verified)
    if not source_same:
        return VerifyResult("SOURCE_CHANGED", row.rel, row.source_path,
                            "source size/mtime differ from the catalog" + copy_note,
                            dest_verified=dest_verified)
    if not deep:
        return VerifyResult("OK", row.rel, row.source_path, "")
    if source_error is not None:
        return VerifyResult("SOURCE_UNREADABLE", row.rel, row.source_path,
                            f"source cannot be read: {_error_text(source_error)}" + copy_note,
                            dest_verified=dest_verified)
    if row.sha256:
        if hash_src != source_want[2]:
            return VerifyResult("SOURCE_CORRUPT_SUSPECT", row.rel, row.source_path,
                                "source sha256 differs from the catalog with same size/mtime",
                                dest_verified=dest_verified)
        return VerifyResult("OK", row.rel, row.source_path, "", dest_verified=dest_verified)
    if hash_src == hash_dst:
        return VerifyResult("HASH_FILLED", row.rel, row.source_path,
                            "hash recorded", new_hash=hash_dst, dest_verified=True)
    return VerifyResult("DEST_DIFFERS", row.rel, row.source_path,
                        "destination differs from the source (no catalog hash)")


def _key_under_prefixes(key, prefixes):
    for prefix in prefixes:
        if prefix == "" or key == prefix or key.startswith(prefix + "\\"):
            return True
    return False


def _list_dest_sizes(jobs, dest):
    """Quick verify: the size of every job's copy that a listing of its folder
    answers (see _file_statuses); the others are asked one by one."""
    by_folder = {}
    for job in jobs:
        dst = os.path.join(dest, job.row.rel)
        by_folder.setdefault(os.path.dirname(dst), []).append((job, os.path.basename(dst)))
    for folder, items in by_folder.items():
        listed = _list_dest_folder(folder)
        if not listed:
            continue
        for job, name in items:
            hit = listed.get(name)
            if hit is not None:
                job.dest_size = hit[0]


def _listed_sources(jobs, planned_files):
    """Quick verify (VERIFY_QUICK_USES_PLAN): the size and date the plan's own
    listing gave for each job's source, when the plan holds that very file
    (same path) -- one question less per file. A row whose source the plan
    does not hold is still asked (_source_stat: missing needs proof)."""
    by_key = {planned_file.rel.casefold(): planned_file for planned_file in planned_files}
    for job in jobs:
        planned_file = by_key.get(job.row.key)
        if (planned_file is not None
                and os.path.normcase(planned_file.src) == os.path.normcase(job.row.source_path)):
            job.source_listed = (planned_file.size, planned_file.mtime_ns)


def _run_verify(args, log, plan_fn=None):
    dest = os.path.abspath(args.dest)
    if not os.path.isdir(_io(dest)):
        print(f"destination does not exist: {dest}", file=sys.stderr)
        return 2
    if args.expect_backup_id:
        found = read_backup_id(dest)
        if found != args.expect_backup_id:
            print(f"Destination {dest} is not the expected backup "
                  f"(expected {args.expect_backup_id}, found {found or 'no backup here'})",
                  file=sys.stderr)
            log.log("ERROR expected backup ID does not match")
            return 2
    try:
        catalog = _Catalog(dest, create=False)
    except (RuntimeError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        log.log(f"ERROR {exc}")
        return 2
    deep = args.verify == "full"
    if not deep and args.max_gb > 0:
        log.log("max-gb ignored (quick verify)")
    reader = _CopyReader() if deep and VERIFY_PARALLEL else None
    try:
        return _verify_catalog(args, log, plan_fn, dest, catalog, deep, reader)
    except sqlite3.Error as exc:
        message = f"cannot read catalog {catalog.path}: {exc}"
        print(message, file=sys.stderr)
        log.log(f"ERROR {message}")
        return 2
    finally:
        if reader is not None:
            reader.close()
        catalog.close()


def _verify_catalog(args, log, plan_fn, dest, catalog, deep, reader):
    """The verify itself (catalog open). What it has checked is written to
    the catalog every VERIFY_COMMIT_S, in one short transaction, and its
    summary and _verify.csv are written also when it is interrupted: hours of
    reading are never lost to a Ctrl+C or to a task that ran out of time."""
    good = {"OK", "HASH_FILLED"}
    planned = (plan_fn or plan)(args)
    for error in planned.errors:
        log.log(error)
    for warning in planned.warnings:
        log.log(warning)
    for link in planned.links:
        log.log(f"LINK not followed {link}")
    for link in planned.cache_links:
        log.log(f"CACHE LINK not followed {link}")
    for folder in planned.skipped_folders:
        log.log(f"SKIPPED FOLDER {folder}")
    reserved = _reserved_rel_error(planned)
    if reserved:
        print(reserved, file=sys.stderr)
        log.log(f"ERROR {reserved}")
        return 2
    rows = {row.key: row for row in catalog.all_rows()}
    converted = catalog.converted_rows()
    db_keys = set(rows)
    prefixes = _source_prefixes(planned)
    checked = set()
    results = []
    planned_jobs = []
    catalog_jobs = []
    uncatalogued = [planned_file for planned_file in planned.files
                    if planned_file.rel.casefold() not in rows]
    for status in _file_statuses(uncatalogued, dest):
        planned_file = status.planned
        key = planned_file.rel.casefold()
        if key in checked:
            continue
        if planned_file.convert and planned_file.rel[:-len(".jxl")].casefold() in rows:
            checked.add(key)        # its conversion failed: the TIFF copy is checked below
            continue
        if status.status == ST_NEW:
            results.append(VerifyResult("NOT_BACKED_UP", planned_file.rel, planned_file.src,
                                        "no destination file and no catalog row"))
            checked.add(key)
            continue
        row = _Row(key, planned_file.rel, planned_file.size, _db_ns(planned_file.mtime_ns),
                   None, planned_file.src)
        if deep:
            catalog.adopt(planned_file)
            db_keys.add(key)
        rows[key] = row
        planned_jobs.append(_VerifyJob(row, key in db_keys, converted=converted.get(key)))
        checked.add(key)
    if deep and planned_jobs:
        catalog.commit()
    outside = []
    for key, row in rows.items():
        if key in checked:
            continue
        if not _key_under_prefixes(key, prefixes):
            outside.append(row)
            continue
        catalog_jobs.append(_VerifyJob(row, key in db_keys, converted=converted.get(key)))
        checked.add(key)
    budget = args.max_gb * 1e9 if deep and args.max_gb > 0 else None
    if budget is not None:
        catalog_jobs.sort(key=lambda job: (job.row.verified_at is not None,
                                           job.row.verified_at or "",
                                           job.row.copied_at or ""))
    if not deep:
        _list_dest_sizes(planned_jobs + catalog_jobs, dest)
        if VERIFY_QUICK_USES_PLAN:
            _listed_sources(planned_jobs + catalog_jobs, planned.files)
    catalog_bytes = sum(job.row.size for job in catalog_jobs)
    total_bytes = 0
    if deep:
        total_bytes = sum(job.row.size for job in planned_jobs) + (
            catalog_bytes if budget is None else min(catalog_bytes, budget))
    job_count = len(results) + len(planned_jobs) + len(catalog_jobs)
    done_bytes = 0
    started = time.monotonic()
    last_report = started
    last_write = started
    speed = _Speed()
    now = datetime.now().isoformat(timespec="seconds")
    marks = []              # (key, new hash or None): verified, not yet in the catalog
    catalog_error = None
    stopped = False         # the destination went away: nothing after it was checked

    def write_marks():
        """Record what was verified since the last write. The transaction is
        open only for this moment: a backup started meanwhile is not locked
        out for the hours a full verify takes."""
        nonlocal catalog_error, last_write
        last_write = time.monotonic()
        if catalog_error is not None or not marks:
            return
        try:
            for key, new_hash in marks:
                if new_hash is not None:
                    catalog.set_hash_verified(key, new_hash, now)
                else:
                    catalog.set_verified(key, now)
            catalog.commit()
        except sqlite3.Error as exc:
            catalog_error = str(exc) or type(exc).__name__
            log.log(f"CATALOG NOT WRITTEN ({catalog_error})")
        marks.clear()

    def verify_one(job):
        """Verify one row; True when it got a verified_at (it leaves the
        front of the rolling queue)."""
        nonlocal done_bytes, last_report, stopped
        result = _verify_row(job, dest, deep, reader)
        if result.status in ("DEST_MISSING", "DEST_UNREADABLE") and not _dest_available(dest):
            stopped = True
            return False
        results.append(result)
        if result.status != "OK":
            log.log(f"{result.status} {result.rel}"
                    + (f": {result.detail}" if result.detail else ""))
        if deep:
            done_bytes += job.row.size
            if time.monotonic() - last_report >= PROGRESS_EVERY_S:
                print(f"[{len(results)}/{job_count}] {_gb(done_bytes)}/{_gb(total_bytes)}  "
                      f"{speed.text(done_bytes, total_bytes)}")
                last_report = time.monotonic()
        verified = (result.status in ("HASH_FILLED", "OK") or result.dest_verified)
        # Only a full verify reads the files: a quick one never records
        # verified_at, or it would reset the --max-gb queue (B23).
        if deep and job.in_catalog and verified:
            marks.append((job.row.key,
                          result.new_hash if result.status == "HASH_FILLED" else None))
            if time.monotonic() - last_write >= VERIFY_COMMIT_S:
                write_marks()
        return verified

    checked_jobs = []
    deferred = []
    interrupted = False
    try:
        for job in planned_jobs:
            verify_one(job)
            if stopped:
                break
        used = 0
        for index, job in enumerate(catalog_jobs):
            if stopped:
                break
            if budget is not None and used and used + job.row.size > budget:
                deferred = catalog_jobs[index:]
                break
            checked_jobs.append(job)
            # A row that stays without verified_at (DEST_CORRUPT, DEST_MISSING,
            # ...) is checked again every run and keeps the exit code 1; its
            # size goes back to the --max-gb budget, so it can never hold the
            # rest of the backup back from being verified.
            if verify_one(job):
                used += job.row.size
    except KeyboardInterrupt:
        interrupted = True
        _tell("Ctrl+C: stopping -- what was checked is recorded and listed (a few seconds)...")
    write_marks()
    counts = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    summary = f"Verify {args.verify}: {_num(len(results))} files  " + "  ".join(
        f"{status} {_num(counts[status])}" for status in sorted(counts))
    log.out(summary)
    if interrupted or stopped:
        why = ("INTERRUPTED" if interrupted else
               f"STOPPED: the destination is not available any more ({dest})")
        log.out(f"{why}: {_num(job_count - len(results))} of {_num(job_count)} files were "
                f"not checked; what was checked is recorded")
    if budget is not None:
        log.out(f"Rolling verify: checked {_num(len(checked_jobs))} files / "
                f"{_gb(sum(job.row.size for job in checked_jobs))}, "
                f"deferred {_num(len(deferred))} files / "
                f"{_gb(sum(job.row.size for job in deferred))} "
                f"(oldest verification first; the next run continues; "
                f"a row with a problem does not count against --max-gb)")
    if outside:
        log.out(f"{_num(len(outside))} files / {_gb(sum(row.size for row in outside))} "
                f"of the catalog are outside these sources: not checked")
    if planned.errors:
        log.out(f"Errors: {_num(len(planned.errors))} folders/files could not be read -> {log.path}")
    if catalog_error is not None:
        log.out(f"CATALOG: the dates of this verification could not be recorded "
                f"({catalog_error})")
    listed = [result for result in results if VERIFY_CSV_OK or result.status != "OK"]
    log.csv("_verify.csv", ["status", "rel", "source_path", "detail"],
            [(result.status, result.rel, result.source_path, result.detail)
             for result in listed])
    if len(listed) != len(results):
        log.log(f"_verify.csv: {_num(len(results) - len(listed))} OK files not listed "
                f"(VERIFY_CSV_OK)")
    status_by_rel = {result.rel: result.status for result in results}
    plan_rows = []
    for planned_file in planned.files:
        status = status_by_rel.get(planned_file.rel, "")
        if VERIFY_CSV_OK or status not in ("OK", ""):
            plan_rows.append((status, planned_file.category, planned_file.size,
                              _csv_date(planned_file.mtime_ns), planned_file.src,
                              planned_file.rel))
    log.csv("_plan.csv", ["action", "category", "size", "mtime", "src", "rel"], plan_rows)
    if interrupted:
        print("Interrupted.", file=sys.stderr)
        log.log("Interrupted.")
        return 130
    bad = [result for result in results if result.status not in good]
    return 1 if bad or planned.errors or stopped or catalog_error is not None else 0


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="selective_backup.py",
        description="Selective backup of a photo archive (source is read-only).")
    parser.add_argument("--source", action="append", required=True, metavar="PATH",
                        help="folder to back up (repeatable)")
    parser.add_argument("--dest", required=True, metavar="PATH",
                        help="destination root")
    parser.add_argument("--base", default=None, metavar="PATH",
                        help="base for the relative layout (default: drive root of each source)")
    parser.add_argument("--export-formats", default=EXPORT_FORMATS, metavar="SPEC",
                        help="all, a>b>c fallback, or a+b multiple (default: %(default)s)")
    parser.add_argument("--exclude-export-subfolders", default=EXCLUDE_EXPORT_SUBFOLDERS,
                        metavar="NAMES", help="';'-separated subfolders skipped inside export folders")
    parser.add_argument("--export-name-contains", default=EXPORT_NAME_CONTAINS, metavar="TEXT",
                        help="a folder whose name contains this (any case) is an export folder")
    parser.add_argument("--exclude-export-names", default=EXPORT_NAME_EXCLUDE, metavar="NAMES",
                        help="';'-separated exact folder names that are not export folders")
    parser.add_argument("--unselected",
                        choices=("skip", "raw", "raw_or_jpeg", "raw_jpeg", "all"),
                        default=UNSELECTED,
                        help="originals of photos that were not exported (default: %(default)s)")
    parser.add_argument("--unexported", choices=("skip", "copy"), default=UNEXPORTED,
                        help="folders without an export (default: %(default)s)")
    parser.add_argument("--c1-cache", action="store_true", default=COPY_CAPTUREONE_CACHE,
                        help="also copy the Capture One Cache folders")
    parser.add_argument("--no-c1-cache", dest="c1_cache", action="store_false",
                        default=COPY_CAPTUREONE_CACHE,
                        help="do not copy the Capture One Cache folders "
                             "(default: COPY_CAPTUREONE_CACHE)")
    parser.add_argument("--derivatives", action="store_true", default=COPY_JXLPHOTO_DERIVATIVES,
                        help="also copy the JXLs jxl-photo derived from a master that is copied "
                             "(sRGB/mobile copies; skipped by default, the master remakes them)")
    parser.add_argument("--no-derivatives", dest="derivatives", action="store_false",
                        default=COPY_JXLPHOTO_DERIVATIVES,
                        help="skip those JXLs (default: COPY_JXLPHOTO_DERIVATIVES)")
    parser.add_argument("--exclude-folders", default=EXCLUDE_FOLDERS, metavar="NAMES",
                        help="';'-separated folder names skipped anywhere")
    parser.add_argument("--verify-copy", choices=("none", "size", "hash"), default=VERIFY_COPY,
                        help="verification of each copy (default: %(default)s)")
    parser.add_argument("--update-changed", action="store_true",
                        help="move a changed destination file to the versions folder first")
    parser.add_argument("--no-auto-update", dest="auto_update", action="store_false",
                        default=AUTO_UPDATE,
                        help="do not update changed edit settings/sidecars "
                             "(AUTO_UPDATE_EXTENSIONS) without --update-changed")
    parser.add_argument("--no-detect-moves", dest="detect_moves", action="store_false",
                        default=DETECT_MOVES,
                        help="copy a moved/renamed file again instead of moving its backup copy")
    parser.add_argument("--move-orphans", action="store_true",
                        help="move backup files whose source is gone into the orphans folder")
    parser.add_argument("--tiff", choices=("copy",) + tuple(TIFF_JXL_PROFILES), default="copy",
                        help="TIFFs outside the export folders: copy them as they are, or keep "
                             "them as JXL with this profile of TIFF_JXL_PROFILES (jxl-photo)")
    parser.add_argument("--jpeg", choices=("copy", "jxl"), default="copy",
                        help="JPEGs (exports too): copy them as they are, or keep them as JXL "
                             "-- jxl-photo's lossless transcode, the same bytes back on restore")
    parser.add_argument("--jxlphoto", default=JXLPHOTO, metavar="PATH",
                        help="jxl-photo's jxl_photo.py or jxl-photo.exe, for --tiff and --jpeg "
                             "(default: JXLPHOTO)")
    parser.add_argument("--work-dir", default=JXL_WORK_DIR, metavar="PATH",
                        help="where the conversions of --tiff and --jpeg run, each in a folder "
                             "of its own (default: JXL_WORK_DIR; empty = the system's temp folder)")
    parser.add_argument("--no-convert", action="store_true",
                        help="the conversion off for this run (a slow computer, a trip): a "
                             "TIFF or JPEG of --tiff/--jpeg that is new or changed is copied as "
                             "it is; the JXL already in the backup stay, unless their file "
                             "changed. A later run without it converts what this one copied")
    parser.add_argument("--expect-backup-id", default=None, metavar="ID",
                        help="abort unless the destination holds this backup ID")
    parser.add_argument("--space-margin-gb", type=float, default=SPACE_MARGIN_GB, metavar="N",
                        help="free space kept on the destination (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="plan and log, copy nothing")
    parser.add_argument("--verify", choices=("quick", "full"), default=None,
                        help="verify an existing backup (no copy)")
    parser.add_argument("--max-gb", type=float, default=VERIFY_MAX_GB, metavar="N",
                        help="with --verify full: verify at most N GB, oldest verification "
                             "first (default: %(default)s = everything)")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask for confirmation")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv=None):
    _start_output()
    _STOPPING.clear()
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return int(code) if isinstance(code, int) else 2
    errors = _validate_usage(args)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    stamp = time.strftime("%y%m%d_%H%M%S")
    kind = "verify" if args.verify else "backup"
    _set_console_title(_program_name())
    try:
        log = _Logger(os.path.join(LOG_DIR, f"{stamp}_{kind}"))
    except OSError as exc:
        print(f"cannot write the log directory {LOG_DIR}: {exc}", file=sys.stderr)
        return 2
    try:
        command = sys.argv if argv is None else ["selective_backup.py"] + [str(a) for a in argv]
        log.log("selective_backup " + __version__)
        log.log("command: " + " ".join(command))
        log.log("settings: " + ", ".join(f"{key}={value}" for key, value in sorted(vars(args).items())))
        try:
            if args.verify:
                return _run_verify(args, log)
            return _run_backup(args, log, stamp)
        except KeyboardInterrupt:
            print("Interrupted.", file=sys.stderr)
            log.log("Interrupted.")
            return 130
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
