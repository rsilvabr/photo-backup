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
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timedelta

__version__ = "0.13.1"

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
                                      ".heic", ".heif", ".hif", ".psd")  # files whose stem can be a photo key
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
SUMMARY_FOLDER_LINES = 25           # lines of the per-folder table in the summary (the CSV has all)
SUMMARY_INSIDE_NAMES = 5            # subfolder names shown under "inside sessions" (the _inside.csv has all)

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

SCHEMA_VERSION = "1"


def _split_names(value):
    """Split a ';'-separated settings string into stripped, non-empty names."""
    return [part.strip() for part in str(value).split(";") if part.strip()]


def _fold_set(value):
    return {name.casefold() for name in _split_names(value)}


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
            os.stat(_io(parent))
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


def _jxl_role(path):
    """'derivative', 'master' or None for a .jxl, from the jxl-photo tokens in
    its XMP box. Only positive evidence counts: a bare codestream, a
    compressed (brob) or missing XMP box, both tokens, a read error -> None."""
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
                    xmp = handle.read(length - head)
                    derived = DERIVATIVE_TOKEN.encode() in xmp
                    master = MASTER_TOKEN.encode() in xmp
                    if derived and not master:
                        return "derivative"
                    if master and not derived:
                        return "master"
                    return None
                pos += length
    except OSError:
        return None
    return None


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
    return f"{n / 1e9:.1f} GB"


def _num(n):
    return str(n)


def _hms(seconds):
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m"


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


@dataclass
class FileStatus:
    planned: PlannedFile
    dst: str
    status: str


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
            self.links.append(link)
            self.stats.skipped_links += 1
        if self.skip_paths:
            dirs = [item for item in dirs
                    if os.path.normcase(item[1]) not in self.skip_paths]
        # skipped by its name everywhere, also inside an AddFolders folder, an
        # export or a Capture One folder: every walk lists through here (R2)
        dirs = [item for item in dirs if not self._skipped_by_name(*item)]
        return files, dirs

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
        return os.path.relpath(path, base)

    def _add(self, planned):
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
    )


def _is_under(path, root):
    folded_path = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
    folded_root = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
    if folded_path == folded_root:
        return True
    return folded_path.startswith(folded_root + os.sep)


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
    sources = [os.path.abspath(source) for source in args.source]
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
        if _is_under(dest, source) or _is_under(source, dest):
            errors.append(f"destination overlaps source: dest {dest}, source {source}")
    return errors


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


def _file_statuses(files, dest):
    """_file_status of every planned file, in the same order, with one listing
    per destination folder instead of one question per file (6 times faster on
    96 000 files, and no file record is read from a cold disk). The listing
    only answers for a name it holds exactly as planned: any other case -- a
    name stored in another case, a folder that could not be listed -- still
    asks the system, so a file that is there is never taken for a NEW one."""
    statuses = [None] * len(files)
    by_folder = {}
    for index, planned in enumerate(files):
        dst = os.path.join(dest, planned.rel)
        by_folder.setdefault(os.path.dirname(dst), []).append((index, planned, dst))
    for folder, items in by_folder.items():
        listed = _list_dest_folder(folder)
        for index, planned, dst in items:
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
    os.makedirs(_io(folder), exist_ok=True)
    target = os.path.join(folder, BACKUP_ID_FILE)
    tmp = target + TEMP_SUFFIX
    with open(_io(tmp), "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(_io(tmp), _io(target))
    log.log(f"Backup ID {backup_id} (new)")
    return backup_id


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
                if row[0] != SCHEMA_VERSION:
                    raise RuntimeError(
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
                if row is None:
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
        except sqlite3.Error as exc:
            self.close()
            raise RuntimeError(f"cannot open catalog {self.path}: {exc}")
        except RuntimeError:
            self.close()
            raise

    def all_rows(self):
        cursor = self.conn.execute(
            "SELECT key, rel, size, mtime_ns, sha256, source_path, "
            "copied_at, verified_at FROM files")
        return [_Row(*row) for row in cursor.fetchall()]

    def delete(self, key):
        self.conn.execute("DELETE FROM files WHERE key=?", (key,))

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


def _move_to_versions(dest, stamp, status, log):
    target = os.path.join(dest, CATALOG_FOLDER, "versions", stamp, status.planned.rel)
    os.makedirs(_io(os.path.dirname(target)), exist_ok=True)
    os.replace(_io(status.dst), _io(target))
    log.log(f"VERSION {status.planned.rel}")


def _handle_old_copy(dest, stamp, status, log):
    """The destination's old copy of a CHANGED file. It keeps its name until
    the returned callback runs, right before the new file takes that name: a
    copy that fails, or a crash before that, leaves the old copy where it was.
    A different size proves different content: the callback moves it to the
    versions folder. With the same size the callback compares it with the hash
    of the bytes really copied: only a new file identical byte for byte leaves
    the old one out of the versions folder (Capture One rewrites every .cos it
    opens, new date, same bytes). The new file is copied either way."""
    same_size = os.stat(_io(status.dst)).st_size == status.planned.size
    old_sha256 = _sha256_file(status.dst) if same_size else None

    def before_final(new_sha256):
        if old_sha256 is None or new_sha256 != old_sha256:
            _move_to_versions(dest, stamp, status, log)
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
        if status.status == ST_UP_TO_DATE:
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
        if status.status != ST_NEW:
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
            os.replace(_io(old), _io(new))
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
    list of the same run that moves nothing."""
    planned_keys = {status.planned.rel.casefold() for status in statuses}
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
            os.replace(_io(src), _io(target))
            catalog.delete(row.key)
            log.log(f"ORPHAN {row.rel}")
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
    width = min(40, max([12] + [len(name) for name, _count, _nbytes in biggest]))
    return [f"      {name:<{width}} {_num(count):>8} files {_gb(nbytes):>12}"
            for name, count, nbytes in biggest]


def _orphan_csv_rows(orphans):
    return [(reason, row.rel, row.size, row.source_path, row.copied_at)
            for reason, row in orphans]


def _copy_all(args, statuses, dest, stamp, catalog, log, stop_info=None):
    """Copy what the plan says. `stop_info` (a dict) receives why the copy
    stopped early and what was left, when the destination stopped taking
    writes: after DEST_CHECK_AFTER_ERRORS failed copies in a row the
    destination is tested, and a test that fails stops the reading too (B16).
    A catalog that can no longer be written (sqlite3.Error) is a destination
    that stopped taking writes too: nothing more is tried, and `unrecorded`
    says how many copies have their final name but no row (R1)."""
    to_copy = [status for status in statuses if _will_copy(status, args)]
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
                    before_final = _handle_old_copy(dest, stamp, status, log)
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
                elapsed = max(now - started, 1e-6)
                speed = done_bytes / elapsed
                eta = (total_bytes - done_bytes) / speed if speed > 0 else 0
                print(f"[{done}/{len(to_copy)}] {_gb(done_bytes)}/{_gb(total_bytes)}  "
                      f"{speed / 1e6:.0f} MB/s  ETA {_hms(eta)}")
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
        for item in pending:
            _remove_temp(item.tmp)
        commit()
        print(f"Interrupted: {done} copied, {error_count} errors")
        log.log(f"INTERRUPTED after {done} copied, {error_count} errors")
        return done, done_bytes, error_count, True
    finally:
        if read_ahead is not None:
            read_ahead.close()
        flush_pool.shutdown(wait=True)
    return done, done_bytes, error_count, False


def _summary_lines(args, planned, statuses, needed, free, margin, fits, log_paths,
                   dest_error=None, extra_lines=()):
    new = [status for status in statuses if status.status == ST_NEW]
    changed = [status for status in statuses if status.status == ST_CHANGED]
    auto = [status for status in changed if _auto_updated(status, args)]
    auto_note = f" ({_num(len(auto))} edit settings/sidecars auto-updated)" if auto else ""
    up_to_date = [status for status in statuses if status.status == ST_UP_TO_DATE]
    lines = [
        f"selective_backup {__version__}",
        "Sources:      " + ", ".join(os.path.abspath(source) for source in args.source),
    ]
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
    lines.append(f"Sessions: {_num(planned.stats.shoots)}   "
                 f"export folders: {_num(planned.stats.export_folders)}   "
                 f"export formats: {args.export_formats}")
    lines.append(f"Plan:  NEW {_num(len(new))} files {_gb(sum(s.planned.size for s in new))} "
                 f"| CHANGED {_num(len(changed))}{auto_note} | UP_TO_DATE {_num(len(up_to_date))}")
    categories = [CAT_EXPORT, CAT_SOURCE, CAT_ADDED, CAT_WHOLE, CAT_CAPTURE_ONE,
                  CAT_NX_STUDIO, CAT_UNSELECTED, CAT_UNEXPORTED]
    to_copy = [status for status in statuses if _will_copy(status, args)]
    parts = []
    for category in categories:
        selected = [status for status in to_copy if status.planned.category == category]
        if selected:
            parts.append(f"{category} {_num(len(selected))} / "
                         f"{_gb(sum(s.planned.size for s in selected))}")
    lines.append("  " + "   ".join(parts) if parts else "  (nothing to copy)")
    lines.extend(extra_lines)
    lines.append("Not copied:")
    lines.append(f"  export files filtered (formats / excluded subfolders): "
                 f"{_num(planned.stats.export_filtered + planned.stats.export_excluded)} / "
                 f"{_gb(planned.stats.export_filtered_bytes + planned.stats.export_excluded_bytes)}")
    lines.append(f"  unselected originals: {_num(planned.stats.unselected_skipped)} / "
                 f"{_gb(planned.stats.unselected_skipped_bytes)}      "
                 f"Capture One cache: {_gb(planned.stats.c1_cache_skipped_bytes)}")
    if planned.stats.add_filtered_skipped:
        lines.append(f"  left out inside added folders (Originals filter): "
                     f"{_num(planned.stats.add_filtered_skipped)} / "
                     f"{_gb(planned.stats.add_filtered_skipped_bytes)}")
    if planned.stats.mode_excluded:
        lines.append(f"  excluded by the mode (Exclude): {_num(planned.stats.mode_excluded)} / "
                     f"{_gb(planned.stats.mode_excluded_bytes)}")
    if planned.stats.derivatives_skipped:
        lines.append(f"  jxl-photo derivatives (their master is copied; --derivatives copies them): "
                     f"{_num(planned.stats.derivatives_skipped)} / "
                     f"{_gb(planned.stats.derivatives_skipped_bytes)}")
    if planned.stats.manifest_skip_folders:
        lines.append(f"  folders the manifest leaves out (Skip): "
                     f"{_num(planned.stats.manifest_skip_folders)} folders / "
                     f"{_num(planned.stats.manifest_skip_files)} files / "
                     f"{_gb(planned.stats.manifest_skip_bytes)}")
    if planned.stats.only_filtered:
        lines.append(f"  left out by a mode's Only filter: {_num(planned.stats.only_filtered)} / "
                     f"{_gb(planned.stats.only_filtered_bytes)}")
    inside_csv = f"   -> {log_paths['inside']}" if planned.inside else ""
    lines.append(f"  inside sessions (subfolders the mode leaves out): "
                 f"{_num(planned.stats.inside_shoot_skipped)} / "
                 f"{_gb(planned.stats.inside_shoot_skipped_bytes)}{inside_csv}")
    lines.extend(_inside_name_lines(planned.inside))
    not_copied = [folder for folder in planned.unexported_folders if not folder.copied]
    unexported_bytes = sum(folder.nbytes + folder.c1_nbytes + folder.nx_nbytes
                           for folder in not_copied)
    lines.append(f"  folders without export: {_num(len(not_copied))} folders / "
                 f"{_gb(unexported_bytes)}   -> {log_paths['unexported']}")
    sessions = {session for session, _ in planned.unmatched}
    lines.append(f"  exports without a matching original: {_num(len(planned.unmatched))} files "
                 f"in {_num(len(sessions))} sessions -> {log_paths['unmatched']}")
    if planned.links:
        lines.append(f"  links/junctions not followed: {_num(len(planned.links))} "
                     f"-> {log_paths['log']}")
    if planned.skipped_folders:
        lines.append(f"  folders skipped by name (system / --exclude-folders): "
                     f"{_num(len(planned.skipped_folders))} -> {log_paths['log']}")
    if planned.errors:
        lines.append(f"Errors: {_num(len(planned.errors))} folders/files could not be read "
                     f"(NOT backed up) -> {log_paths['log']}")
    if dest_error:
        lines.append(f"Space: need {_gb(needed)}, destination not available ({dest_error})")
    else:
        lines.append(f"Space: need {_gb(needed)}, free {_gb(free)}, margin {_gb(margin)} -> "
                     + ("fits" if fits else "DOES NOT FIT"))
    return lines


def _run_backup(args, log, stamp, plan_fn=None):
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
    for folder in planned.skipped_folders:
        log.log(f"SKIPPED FOLDER {folder}")
    reserved = _reserved_rel_error(planned)
    if reserved:
        print(reserved, file=sys.stderr)
        log.log(f"ERROR {reserved}")
        return 2
    statuses = _file_statuses(planned.files, dest)
    to_copy = [status for status in statuses if _will_copy(status, args)]
    needed = sum(status.planned.size for status in to_copy)
    dest_error = None
    try:
        free = shutil.disk_usage(_io(_existing_ancestor(dest))).free
    except OSError as exc:      # drive not connected / not mounted
        free = 0
        dest_error = exc.strerror or str(exc)
    margin = int(float(args.space_margin_gb) * 1e9)
    fits = dest_error is None and needed <= free - margin
    log_paths = {
        "log": log.path,
        "plan": log.prefix + "_plan.csv",
        "unexported": log.prefix + "_unexported.csv",
        "unmatched": log.prefix + "_unmatched.csv",
        "orphans_csv": log.prefix + "_orphans.csv",
        "by_folder_csv": log.prefix + "_by_folder.csv",
        "inside": log.prefix + "_inside.csv",
    }
    rows = []
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
    if read_only_catalog is not None:
        try:
            rows = read_only_catalog.all_rows()
        except sqlite3.Error as exc:
            message = f"cannot read catalog {read_only_catalog.path}: {exc}"
            print(message, file=sys.stderr)
            log.log(f"ERROR {message}")
            return 2
        finally:
            read_only_catalog.close()
        if args.detect_moves:
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
    folder_rows = _folder_rows(to_copy)
    if len(folder_rows) >= 2:
        extra_lines.append("  to copy by folder (cumulative):")
        folder_lines, more = _folder_summary_lines(folder_rows)
        extra_lines.extend(folder_lines)
        if more:
            extra_lines.append(f"    ... {more} more -> {log_paths['by_folder_csv']}")
    possible = [status for status, _matches in candidates.values()]
    if possible:
        extra_lines.append(
            f"  possible moves: {_num(len(possible))} files / "
            f"{_gb(sum(status.planned.size for status in possible))} "
            f"(same name and size as backup files whose source is gone; "
            f"confirmed by hash before moving)")
    gone = [row for reason, row in tentative_orphans if reason == "source_missing"]
    unselected = [row for reason, row in tentative_orphans if reason == "not_selected"]
    unreadable = [row for reason, row in tentative_orphans if reason == "source_unreadable"]
    elsewhere = [row for reason, row in tentative_orphans if reason == "other_source"]
    not_proven = ""
    if unreadable:
        not_proven += (f", {_num(len(unreadable))} files / "
                       f"{_gb(sum(row.size for row in unreadable))} source cannot be read")
    if elsewhere:
        not_proven += (f", {_num(len(elsewhere))} files / "
                       f"{_gb(sum(row.size for row in elsewhere))} from another source")
    extra_lines.append(
        f"  orphans (in the backup, not in the source): "
        f"{_num(len(gone))} files / {_gb(sum(row.size for row in gone))} source gone, "
        f"{_num(len(unselected))} files / {_gb(sum(row.size for row in unselected))} "
        f"no longer selected{not_proven} -> {log_paths['orphans_csv']}")
    orphans_blocked = (f"nothing moved: {_num(len(planned.errors))} errors in the plan of this "
                       f"run (a source that cannot be read is not one that is gone)")
    if args.move_orphans and args.dry_run:
        if planned.errors:
            extra_lines.append(f"  --move-orphans: {orphans_blocked}")
        else:
            extra_lines.append(
                f"  --move-orphans: {_num(len(gone))} files would be moved to "
                f"{os.path.join(dest, CATALOG_FOLDER, ORPHANS_FOLDER, stamp)}")
    for line in _summary_lines(args, planned, statuses, needed, free, margin, fits, log_paths,
                               dest_error, extra_lines):
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
        message = f"Not enough space: need {_gb(needed)}, free {_gb(free)}, margin {_gb(margin)}"
        # The count stays closed (B15): a possible move is only confirmed by
        # hash later, so it is counted as a copy; the message says how much.
        moves_note = ""
        if possible:
            moves_note = (f"; {_gb(sum(status.planned.size for status in possible))} of it "
                          f"may be files moved inside the backup (possible moves, confirmed "
                          f"by hash when copying), which would need no new space")
        message += moves_note
        if args.dry_run:
            print(f"WARNING: would not fit ({_gb(needed)} needed, {_gb(free)} free, "
                  f"{_gb(margin)} margin{moves_note})")
            log.log("WARNING: would not fit")
            return read_errors
        print(message, file=sys.stderr)
        log.log(message)
        return 2
    if args.dry_run:
        log.log("dry run: nothing copied")
        return read_errors
    if not args.yes:
        try:
            answer = input("Proceed? [y/N] ")
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
    try:
        _ensure_backup_id(dest, log)
        _adopt_rows(catalog, statuses)
        if candidates:
            moved, moved_bytes, move_errors, interrupted = _apply_moves(
                candidates, dest, catalog, log)
        if interrupted:
            return 130
        orphans = _find_orphans(catalog.all_rows(), statuses, prefixes, set(), planned.sources,
                                None if args.move_orphans else orphan_states)
        log.csv("_orphans.csv", ["reason", "rel", "size", "source_path", "copied_at"],
                _orphan_csv_rows(orphans))
        done, done_bytes, error_count, interrupted = _copy_all(
            args, statuses, dest, stamp, catalog, log, stop_info)
        log.log(f"moved {moved} files / {_gb(moved_bytes)}")
        log.out(f"copied {done} files / {_gb(done_bytes)}; {error_count} errors")
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
    return 1 if error_count or move_errors or planned.errors or stop_info else 0


@dataclass
class _VerifyJob:
    row: _Row
    in_catalog: bool
    dest_size: int = None       # from the listing of its folder (quick verify); None = ask


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
    state, source_stat = _source_stat(row.source_path)
    tolerance = int(MTIME_TOLERANCE_S * 1_000_000_000)
    source_same = (state == SOURCE_PRESENT and source_stat.st_size == row.size
                   and abs(_db_ns(source_stat.st_mtime_ns) - row.mtime_ns) <= tolerance)
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
        if hash_src != row.sha256:
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
    for folder in planned.skipped_folders:
        log.log(f"SKIPPED FOLDER {folder}")
    reserved = _reserved_rel_error(planned)
    if reserved:
        print(reserved, file=sys.stderr)
        log.log(f"ERROR {reserved}")
        return 2
    rows = {row.key: row for row in catalog.all_rows()}
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
        planned_jobs.append(_VerifyJob(row, key in db_keys))
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
        catalog_jobs.append(_VerifyJob(row, key in db_keys))
        checked.add(key)
    budget = args.max_gb * 1e9 if deep and args.max_gb > 0 else None
    if budget is not None:
        catalog_jobs.sort(key=lambda job: (job.row.verified_at is not None,
                                           job.row.verified_at or "",
                                           job.row.copied_at or ""))
    if not deep:
        _list_dest_sizes(planned_jobs + catalog_jobs, dest)
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
                elapsed = max(time.monotonic() - started, 1e-6)
                speed = done_bytes / elapsed
                eta = (total_bytes - done_bytes) / speed if speed > 0 else 0
                print(f"[{len(results)}/{job_count}] {_gb(done_bytes)}/{_gb(total_bytes)}  "
                      f"{speed / 1e6:.0f} MB/s  ETA {_hms(eta)}")
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
    try:                        # a cp1252 console cannot print the owner's ～ paths
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
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
