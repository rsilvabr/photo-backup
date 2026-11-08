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
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

__version__ = "0.4.0"

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
ANCHOR_EXTENSIONS = RAW_EXTENSIONS + (".jpg", ".jpeg", ".tif", ".tiff", ".png",
                                      ".heic", ".heif", ".hif", ".psd")  # files whose stem can be a photo key
SIDECAR_EXTENSIONS = (".xmp", ".dop", ".pp3")   # copied with an unselected RAW under --unselected raw
NAME_SEPARATORS = "_-. ("           # a key must be followed by one of these in a longer name
UNSELECTED = "skip"                 # default --unselected: skip | raw | all

# ---- Editor folders ---------------------------------------------------------
CAPTUREONE_FOLDER_NAMES = "CaptureOne"       # ';'-separated (any case)
CAPTUREONE_CACHE_NAMES = "Cache"             # ';'-separated subfolders of a Capture One folder skipped by default
COPY_CAPTUREONE_CACHE = False                # default for --c1-cache
NXSTUDIO_FOLDER_NAMES = "NKSC_PARAM"         # ';'-separated (any case); always copied whole

# ---- Folders without an export ----------------------------------------------
UNEXPORTED = "skip"                 # default --unexported: skip | copy

# ---- Walk ---------------------------------------------------------------------
EXCLUDE_FOLDERS = ""                # ';'-separated folder names (any case) skipped anywhere
SYSTEM_SKIP_FOLDERS = "$RECYCLE.BIN;System Volume Information"

# ---- Copy and verify ------------------------------------------------------------
VERIFY_COPY = "hash"                # default --verify-copy: none | size | hash
SPACE_MARGIN_GB = 20.0              # free space kept on the destination (default --space-margin-gb)
MTIME_TOLERANCE_S = 2.0             # FAT/exFAT store mtimes in 2 s steps
COPY_CHUNK_MB = 8
TEMP_SUFFIX = ".pbtmp"              # in-progress copy, next to its final name
CATALOG_FOLDER = "_photo_backup"    # under the destination: catalog.sqlite + versions\
PROGRESS_EVERY_S = 5.0
AUTO_UPDATE = True                  # default: changed edit settings/sidecars are updated without --update-changed
AUTO_UPDATE_EXTENSIONS = (".xmp", ".dop", ".pp3", ".cos", ".comask", ".nksc")  # small files rewritten by every edit

DETECT_MOVES = True                 # default: a NEW file that is a moved/renamed catalogued file is moved inside the backup (--no-detect-moves)
ORPHANS_FOLDER = "orphans"          # under CATALOG_FOLDER: where --move-orphans puts backup files whose source is gone
BACKUP_ID_FILE = "volume.json"      # under CATALOG_FOLDER: the identity of this backup (F2)
VERIFY_MAX_GB = 0.0                 # default --max-gb for --verify full: 0 = verify everything
SUMMARY_FOLDER_LINES = 25           # lines of the per-folder table in the summary (the CSV has all)

# ---- Logs ---------------------------------------------------------------------------
LOG_DIR = os.environ.get("PHOTOBACKUP_LOG_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "Logs")

# ---- Categories ---------------------------------------------------------------------
CAT_EXPORT = "export"
CAT_SOURCE = "source"
CAT_UNSELECTED = "unselected"
CAT_CAPTURE_ONE = "capture_one"
CAT_NX_STUDIO = "nx_studio"
CAT_UNEXPORTED = "unexported"

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


def _candidate_prefixes(stem):
    """Prefixes of `stem` that end right before a separator, shortest first,
    then `stem` itself. 'DSC_9360_ProPhoto-g22_v1' -> ['DSC', 'DSC_9360',
    'DSC_9360_ProPhoto', 'DSC_9360_ProPhoto-g22', 'DSC_9360_ProPhoto-g22_v1'].
    A prefix is never empty: '_DSC6489_x' -> ['_DSC6489', '_DSC6489_x']."""
    out = [stem[:i] for i in range(1, len(stem)) if stem[i] in NAME_SEPARATORS]
    out.append(stem)
    return out


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
    return f"{n:,}".replace(",", " ")


def _hms(seconds):
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m"


def _sha256_file(path):
    hasher = hashlib.sha256()
    with open(_io(path), "rb") as handle:
        while True:
            chunk = handle.read(COPY_CHUNK_MB * 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


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
    skipped_links: int = 0
    scan_errors: int = 0


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


@dataclass
class Plan:
    files: list
    stats: Stats
    unexported_folders: list
    unmatched: list
    errors: list
    warnings: list
    sources: list


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


class _Planner:
    def __init__(self, args):
        self.args = args
        self.stats = Stats()
        self.errors = []
        self.warnings = []
        self.files = []
        self.seen = {}
        self.unexported = []
        self.unmatched = []
        self.sources = []
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

    def _is_export_name(self, name):
        folded = name.casefold()
        return self.export_contains in folded and folded not in self.export_excludes

    def _list_dir(self, path):
        files = []
        dirs = []
        try:
            with os.scandir(_io(path)) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink() or entry.is_junction():
                            self.stats.skipped_links += 1
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
        return files, dirs

    def _rel(self, path, base):
        return os.path.relpath(path, base)

    def _add(self, planned):
        key = planned.rel.casefold()
        if key in self.seen:
            self.warnings.append(f"WARNING duplicate {planned.rel} (kept {self.seen[key].src})")
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

    def _walk_usage(self, root, skip_direct=frozenset()):
        count = 0
        total = 0
        stack = [(root, True)]
        while stack:
            path, top = stack.pop()
            files, dirs = self._list_dir(path)
            for _, _, size, _ in files:
                count += 1
                total += size
            for name, dpath in dirs:
                if top and name.casefold() in skip_direct:
                    continue
                stack.append((dpath, False))
        return count, total

    def _add_tree(self, root, base, category, skip_direct=frozenset()):
        stack = [(root, True)]
        while stack:
            path, top = stack.pop()
            files, dirs = self._list_dir(path)
            for name, dpath in dirs:
                if top and name.casefold() in skip_direct:
                    count, nbytes = self._tree_usage(dpath)
                    self.stats.c1_cache_skipped += count
                    self.stats.c1_cache_skipped_bytes += nbytes
                    continue
                stack.append((dpath, False))
            for _, fpath, size, mtime_ns in files:
                self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, category))

    def _collect_export(self, root, collected):
        stack = [(root, False)]
        while stack:
            path, in_c1 = stack.pop()
            files, dirs = self._list_dir(path)
            for name, dpath in dirs:
                folded = name.casefold()
                if folded in self.excluded_subfolders:
                    count, nbytes = self._tree_usage(dpath)
                    self.stats.export_excluded += count
                    self.stats.export_excluded_bytes += nbytes
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

    def _session(self, path, base, files, exports, c1_dirs, nx_dirs):
        self.stats.shoots += 1
        self.stats.export_folders += len(exports)
        anchors = set()
        for name, _, _, _ in files:
            stem, ext = os.path.splitext(name)
            if ext.casefold() in ANCHOR_EXTENSIONS:
                anchors.add(stem.casefold())
        collected = []
        for _, dpath in exports:
            self._collect_export(dpath, collected)
        records = []
        for fpath, size, mtime_ns in collected:
            stem, ext = os.path.splitext(os.path.basename(fpath))
            key = None
            for prefix in _candidate_prefixes(stem):
                if prefix.casefold() in anchors:
                    key = prefix.casefold()
                    break
            if key is None:
                key = "?" + stem.casefold()
                self.unmatched.append((path, fpath))
            records.append((key, ext.casefold(), fpath, size, mtime_ns))
        chosen = self._apply_formats(records)
        selected_keys = set()
        for key, _, fpath, size, mtime_ns in chosen:
            self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, CAT_EXPORT))
            if not key.startswith("?"):
                selected_keys.add(key)
        unselected_raw_stems = set()
        for name, _, _, _ in files:
            stem, ext = os.path.splitext(name)
            if ext.casefold() in RAW_EXTENSIONS and not self._stem_matches(stem, selected_keys):
                unselected_raw_stems.add(stem.casefold())
        for name, fpath, size, mtime_ns in files:
            stem, ext = os.path.splitext(name)
            folded_ext = ext.casefold()
            if self._stem_matches(stem, selected_keys):
                self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, CAT_SOURCE))
                continue
            if self.unselected == "raw":
                copied = folded_ext in RAW_EXTENSIONS
                if not copied and folded_ext in SIDECAR_EXTENSIONS:
                    copied = any(prefix.casefold() in unselected_raw_stems
                                 for prefix in _candidate_prefixes(stem))
                if copied:
                    self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, CAT_UNSELECTED))
                else:
                    self.stats.unselected_skipped += 1
                    self.stats.unselected_skipped_bytes += size
            elif self.unselected == "all":
                self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns, CAT_UNSELECTED))
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

    def build(self):
        stack = []
        for source in self.args.source:
            src = os.path.abspath(source)
            base = self.base
            if base is None:
                base = os.path.splitdrive(src)[0] + os.sep
            self.sources.append((src, base))
            stack.append((src, base, False))
        stack.reverse()
        while stack:
            path, base, inside_shoot = stack.pop()
            files, dirs = self._list_dir(path)
            dirs = [(name, dpath) for name, dpath in dirs
                    if name.casefold() not in self.skip_folder_names]
            exports = [(name, dpath) for name, dpath in dirs if self._is_export_name(name)]
            c1_dirs = [(name, dpath) for name, dpath in dirs if name.casefold() in self.c1_names]
            nx_dirs = [(name, dpath) for name, dpath in dirs if name.casefold() in self.nx_names]
            classified = {os.path.normcase(dpath) for _, dpath in exports + c1_dirs + nx_dirs}
            if exports:
                has_originals = self._session(path, base, files, exports, c1_dirs, nx_dirs)
                child_inside = inside_shoot or has_originals
                for _, dpath in dirs:
                    if os.path.normcase(dpath) not in classified:
                        stack.append((dpath, base, child_inside))
            elif inside_shoot:
                for _, _, size, _ in files:
                    self.stats.inside_shoot_skipped += 1
                    self.stats.inside_shoot_skipped_bytes += size
                for _, dpath in dirs:
                    if os.path.normcase(dpath) not in classified:
                        stack.append((dpath, base, True))
            else:
                if files:
                    c1_nbytes = sum(self._walk_usage(dpath, self.cache_skip_direct)[1]
                                    for _, dpath in c1_dirs)
                    nx_nbytes = sum(self._walk_usage(dpath)[1] for _, dpath in nx_dirs)
                    self.unexported.append(UnexportedFolder(
                        path, len(files), sum(item[2] for item in files), c1_nbytes, nx_nbytes))
                    if self.unexported_mode == "copy":
                        for _, fpath, size, mtime_ns in files:
                            self._add(PlannedFile(fpath, self._rel(fpath, base), size, mtime_ns,
                                                  CAT_UNEXPORTED))
                        for _, dpath in c1_dirs:
                            self._add_tree(dpath, base, CAT_CAPTURE_ONE, self.cache_skip_direct)
                        for _, dpath in nx_dirs:
                            self._add_tree(dpath, base, CAT_NX_STUDIO)
                for _, dpath in dirs:
                    if os.path.normcase(dpath) not in classified:
                        stack.append((dpath, base, False))


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


def _file_status(planned, dest):
    dst = os.path.join(dest, planned.rel)
    try:
        stat = os.stat(_io(dst), follow_symlinks=False)
    except OSError:
        return FileStatus(planned, dst, ST_NEW)
    tolerance = int(MTIME_TOLERANCE_S * 1_000_000_000)
    if stat.st_size == planned.size and abs(stat.st_mtime_ns - planned.mtime_ns) <= tolerance:
        return FileStatus(planned, dst, ST_UP_TO_DATE)
    return FileStatus(planned, dst, ST_CHANGED)


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
        try:
            self.conn = sqlite3.connect(_io(self.path))
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
        except sqlite3.Error as exc:
            raise RuntimeError(f"cannot open catalog {self.path}: {exc}")

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
            (planned.rel.casefold(), planned.rel, planned.size, planned.mtime_ns, planned.src))

    def upsert(self, planned, sha256, size=None, mtime_ns=None):
        """Record a copy. `size`/`mtime_ns` are the source's stat taken AT
        COPY TIME; the planned values are only a fallback (a file edited
        between planning and copying is copied as it is now)."""
        size = planned.size if size is None else size
        mtime_ns = planned.mtime_ns if mtime_ns is None else mtime_ns
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO files"
            "(key, rel, size, mtime_ns, sha256, source_path, copied_at, verified_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, NULL) "
            "ON CONFLICT(key) DO UPDATE SET rel=excluded.rel, size=excluded.size, "
            "mtime_ns=excluded.mtime_ns, sha256=excluded.sha256, "
            "source_path=excluded.source_path, copied_at=excluded.copied_at",
            (planned.rel.casefold(), planned.rel, size, mtime_ns,
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
            self.conn.close()
        except sqlite3.Error:
            pass


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


def _copy_file(planned, dst, verify, expected_sha256=None, no_clobber=False):
    """Copy to a temp file next to `dst`, verify, then move it into place.
    no_clobber: a `dst` that exists at that last step is never replaced
    (FileExistsError, nothing written) -- the restore's rule."""
    tmp = dst + TEMP_SUFFIX
    os.makedirs(_io(os.path.dirname(dst)), exist_ok=True)
    try:
        if _exists(tmp):
            os.remove(_io(tmp))
        stat0 = os.stat(_io(planned.src), follow_symlinks=False)
        hasher = hashlib.sha256()
        with open(_io(planned.src), "rb") as source_handle:
            with open(_io(tmp), "xb") as tmp_handle:
                while True:
                    chunk = source_handle.read(COPY_CHUNK_MB * 1024 * 1024)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    tmp_handle.write(chunk)
                tmp_handle.flush()
                os.fsync(tmp_handle.fileno())
        stat1 = os.stat(_io(planned.src), follow_symlinks=False)
        if (stat1.st_size, stat1.st_mtime_ns) != (stat0.st_size, stat0.st_mtime_ns):
            raise RuntimeError("source changed during copy")
        if expected_sha256 is not None and hasher.hexdigest() != expected_sha256:
            raise RuntimeError("content differs from the catalog sha256 (backup copy corrupt?)")
        shutil.copystat(_io(planned.src), _io(tmp))
        if verify == "size":
            if os.stat(_io(tmp)).st_size != stat0.st_size:
                raise RuntimeError("size mismatch after copy")
        elif verify == "hash":
            if _sha256_file(tmp) != hasher.hexdigest():
                raise RuntimeError("hash mismatch after copy")
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
        return hasher.hexdigest(), stat0.st_size, stat0.st_mtime_ns
    except BaseException:
        try:
            if _exists(tmp):
                os.remove(_io(tmp))
        except OSError:
            pass
        raise


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


def _find_move_candidates(statuses, rows, dest):
    """NEW statuses whose name+size match a catalogued backup file whose source
    is gone. Returns {id(status): (status, [row, ...])} — confirmed by hash
    later. (The plan wrote {id(status): [row, ...]}; the status object travels
    with its rows because _apply_moves receives only this mapping.)"""
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
            if _exists(row.source_path):
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


def _find_orphans(rows, statuses, prefixes, used_keys):
    """Catalogued files under the verified prefixes that are neither planned
    now nor consumed by a move. Returns [(reason, row), ...]."""
    planned_keys = {status.planned.rel.casefold() for status in statuses}
    orphans = []
    for row in rows:
        if not _key_under_prefixes(row.key, prefixes):
            continue
        if row.key in planned_keys or row.key in used_keys:
            continue
        reason = "source_missing" if not _exists(row.source_path) else "not_selected"
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


def _orphan_csv_rows(orphans):
    return [(reason, row.rel, row.size, row.source_path, row.copied_at)
            for reason, row in orphans]


def _copy_all(args, statuses, dest, stamp, catalog, log):
    to_copy = [status for status in statuses if _will_copy(status, args)]
    total_bytes = sum(status.planned.size for status in to_copy)
    done = 0
    done_bytes = 0
    error_count = 0
    started = time.monotonic()
    last_report = started
    log.log(f"Copying {_num(len(to_copy))} files / {_gb(total_bytes)}")
    for index, status in enumerate(to_copy, 1):
        try:
            if status.status == ST_CHANGED:
                _move_to_versions(dest, stamp, status, log)
            sha256, size, mtime_ns = _copy_file(status.planned, status.dst, args.verify_copy)
            catalog.upsert(status.planned, sha256, size, mtime_ns)
            done += 1
            done_bytes += status.planned.size
            log.log(f"COPY {status.planned.rel}")
        except KeyboardInterrupt:
            catalog.commit()
            print(f"Interrupted: {done} copied, {error_count} errors")
            log.log(f"INTERRUPTED after {done} copied, {error_count} errors")
            return done, done_bytes, error_count, True
        except Exception as exc:
            error_count += 1
            log.log(f"ERROR {status.planned.rel}: {exc}")
        now = time.monotonic()
        if to_copy and now - last_report >= PROGRESS_EVERY_S:
            elapsed = max(now - started, 1e-6)
            speed = done_bytes / elapsed
            eta = (total_bytes - done_bytes) / speed if speed > 0 else 0
            print(f"[{done}/{len(to_copy)}] {_gb(done_bytes)}/{_gb(total_bytes)}  "
                  f"{speed / 1e6:.0f} MB/s  ETA {_hms(eta)}")
            last_report = now
        if done and done % 50 == 0:
            catalog.commit()
    catalog.commit()
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
    categories = [CAT_EXPORT, CAT_SOURCE, CAT_CAPTURE_ONE, CAT_NX_STUDIO,
                  CAT_UNSELECTED, CAT_UNEXPORTED]
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
    lines.append(f"  inside sessions (_OUTTAKES, OLD_TIFFs, ...): "
                 f"{_num(planned.stats.inside_shoot_skipped)} / "
                 f"{_gb(planned.stats.inside_shoot_skipped_bytes)}")
    unexported_bytes = sum(folder.nbytes + folder.c1_nbytes + folder.nx_nbytes
                           for folder in planned.unexported_folders)
    lines.append(f"  folders without export: {_num(len(planned.unexported_folders))} folders / "
                 f"{_gb(unexported_bytes)}   -> {log_paths['unexported']}")
    sessions = {session for session, _ in planned.unmatched}
    lines.append(f"  exports without a matching original: {_num(len(planned.unmatched))} files "
                 f"in {_num(len(sessions))} sessions -> {log_paths['unmatched']}")
    if planned.errors:
        lines.append(f"Errors: {_num(len(planned.errors))} folders/files could not be read "
                     f"(NOT backed up) -> {log_paths['log']}")
    if dest_error:
        lines.append(f"Space: need {_gb(needed)}, destination not available ({dest_error})")
    else:
        lines.append(f"Space: need {_gb(needed)}, free {_gb(free)}, margin {_gb(margin)} -> "
                     + ("fits" if fits else "DOES NOT FIT"))
    return lines


def _run_backup(args, log, stamp):
    dest = os.path.abspath(args.dest)
    if args.expect_backup_id:
        found = read_backup_id(dest)
        if found != args.expect_backup_id:
            print(f"Destination {dest} is not the expected backup "
                  f"(expected {args.expect_backup_id}, found {found or 'no backup here'})",
                  file=sys.stderr)
            log.log("ERROR expected backup ID does not match")
            return 2
    planned = plan(args)
    for error in planned.errors:
        log.log(error)
    for warning in planned.warnings:
        log.log(warning)
    statuses = [_file_status(planned_file, dest) for planned_file in planned.files]
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
    }
    rows = []
    candidates = {}
    try:
        read_only_catalog = _Catalog(dest, create=False, readonly=True)
    except (RuntimeError, FileNotFoundError):
        read_only_catalog = None
    if read_only_catalog is not None:
        try:
            rows = read_only_catalog.all_rows()
        finally:
            read_only_catalog.close()
        if args.detect_moves:
            candidates = _find_move_candidates(statuses, rows, dest)
    prefixes = _source_prefixes(planned)
    tentative_used = set()
    for _status, matches in candidates.values():
        tentative_used.update(row.key for row in matches)
    tentative_orphans = _find_orphans(rows, statuses, prefixes, tentative_used) if rows else []
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
    extra_lines.append(
        f"  orphans (in the backup, not in the source): "
        f"{_num(len(gone))} files / {_gb(sum(row.size for row in gone))} source gone, "
        f"{_num(len(unselected))} files / {_gb(sum(row.size for row in unselected))} "
        f"no longer selected -> {log_paths['orphans_csv']}")
    if args.move_orphans and args.dry_run:
        extra_lines.append(
            f"  --move-orphans: {_num(len(gone))} files would be moved to "
            f"{os.path.join(dest, CATALOG_FOLDER, ORPHANS_FOLDER, stamp)}")
    for line in _summary_lines(args, planned, statuses, needed, free, margin, fits, log_paths,
                               dest_error, extra_lines):
        log.out(line)
    read_errors = 1 if planned.errors else 0
    log.csv("_plan.csv", ["action", "category", "size", "mtime", "src", "rel"],
            [(status.status, status.planned.category, status.planned.size,
              datetime.fromtimestamp(status.planned.mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M:%S"),
              status.planned.src, status.planned.rel)
             for status in statuses])
    log.csv("_unexported.csv", ["folder", "files", "bytes", "c1_bytes", "nx_bytes"],
            [(folder.path, folder.files, folder.nbytes, folder.c1_nbytes, folder.nx_nbytes)
             for folder in planned.unexported_folders])
    log.csv("_unmatched.csv", ["session", "export_file"],
            [(session, path) for session, path in planned.unmatched])
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
        if args.dry_run:
            print(f"WARNING: would not fit ({_gb(needed)} needed, {_gb(free)} free, "
                  f"{_gb(margin)} margin)")
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
    done = done_bytes = error_count = 0
    moved = moved_bytes = move_errors = 0
    interrupted = False
    try:
        _ensure_backup_id(dest, log)
        _adopt_rows(catalog, statuses)
        if candidates:
            moved, moved_bytes, move_errors, interrupted = _apply_moves(
                candidates, dest, catalog, log)
        if interrupted:
            return 130
        orphans = _find_orphans(catalog.all_rows(), statuses, prefixes, set())
        log.csv("_orphans.csv", ["reason", "rel", "size", "source_path", "copied_at"],
                _orphan_csv_rows(orphans))
        done, done_bytes, error_count, interrupted = _copy_all(
            args, statuses, dest, stamp, catalog, log)
        log.log(f"moved {moved} files / {_gb(moved_bytes)}")
        log.log(f"copied {done} files / {_gb(done_bytes)}; {error_count} errors")
        if interrupted:
            return 130
        if args.move_orphans:
            moved_orphans = _move_orphans(dest, stamp, orphans, catalog, log)
            log.log(f"moved {moved_orphans} orphans to "
                    f"{os.path.join(dest, CATALOG_FOLDER, ORPHANS_FOLDER, stamp)}")
    finally:
        catalog.close()
    return 1 if error_count or move_errors or planned.errors else 0


@dataclass
class _VerifyJob:
    row: _Row
    in_catalog: bool


def _verify_row(job, dest, deep):
    row = job.row
    dst = os.path.join(dest, row.rel)
    if not _exists(dst):
        return VerifyResult("DEST_MISSING", row.rel, row.source_path, "destination file missing")
    stat = os.stat(_io(dst), follow_symlinks=False)
    if stat.st_size != row.size:
        return VerifyResult("DEST_SIZE", row.rel, row.source_path,
                            f"destination size {stat.st_size} != {row.size}")
    # The backup copy is checked BEFORE the source: when the original is gone
    # or changed, the copy may be the only one left, and its health matters most.
    hash_dst = None
    dest_verified = False
    if deep and row.sha256:
        hash_dst = _sha256_file(dst)
        if hash_dst != row.sha256:
            return VerifyResult("DEST_CORRUPT", row.rel, row.source_path,
                                "destination sha256 differs from the catalog")
        dest_verified = True
    if not _exists(row.source_path):
        detail = "source file missing; backup copy hash verified" if hash_dst else "source file missing"
        return VerifyResult("SOURCE_MISSING", row.rel, row.source_path, detail,
                            dest_verified=dest_verified)
    source_stat = os.stat(_io(row.source_path), follow_symlinks=False)
    tolerance = int(MTIME_TOLERANCE_S * 1_000_000_000)
    if (source_stat.st_size != row.size
            or abs(source_stat.st_mtime_ns - row.mtime_ns) > tolerance):
        detail = "source size/mtime differ from the catalog"
        if hash_dst:
            detail += "; backup copy hash verified"
        return VerifyResult("SOURCE_CHANGED", row.rel, row.source_path, detail,
                            dest_verified=dest_verified)
    if not deep:
        return VerifyResult("OK", row.rel, row.source_path, "")
    if row.sha256:
        hash_src = _sha256_file(row.source_path)
        if hash_src != row.sha256:
            return VerifyResult("SOURCE_CORRUPT_SUSPECT", row.rel, row.source_path,
                                "source sha256 differs from the catalog with same size/mtime",
                                dest_verified=dest_verified)
    else:
        hash_dst = _sha256_file(dst)
        hash_src = _sha256_file(row.source_path)
        if hash_src == hash_dst:
            return VerifyResult("HASH_FILLED", row.rel, row.source_path,
                                "hash recorded", new_hash=hash_dst, dest_verified=True)
        return VerifyResult("DEST_DIFFERS", row.rel, row.source_path,
                            "destination differs from the source (no catalog hash)")
    return VerifyResult("OK", row.rel, row.source_path, "", dest_verified=dest_verified)


def _key_under_prefixes(key, prefixes):
    for prefix in prefixes:
        if prefix == "" or key == prefix or key.startswith(prefix + "\\"):
            return True
    return False


def _run_verify(args, log):
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
        return 2
    deep = args.verify == "full"
    good = {"OK", "HASH_FILLED"}
    if not deep and args.max_gb > 0:
        log.log("max-gb ignored (quick verify)")
    try:
        planned = plan(args)
        for error in planned.errors:
            log.log(error)
        for warning in planned.warnings:
            log.log(warning)
        rows = {row.key: row for row in catalog.all_rows()}
        db_keys = set(rows)
        prefixes = _source_prefixes(planned)
        checked = set()
        results = []
        planned_jobs = []
        catalog_jobs = []
        for planned_file in planned.files:
            key = planned_file.rel.casefold()
            if key in rows:
                continue
            dst = os.path.join(dest, planned_file.rel)
            if not _exists(dst):
                results.append(VerifyResult("NOT_BACKED_UP", planned_file.rel, planned_file.src,
                                            "no destination file and no catalog row"))
                checked.add(key)
                continue
            row = _Row(key, planned_file.rel, planned_file.size, planned_file.mtime_ns,
                       None, planned_file.src)
            if deep:
                catalog.adopt(planned_file)
                db_keys.add(key)
            rows[key] = row
            planned_jobs.append(_VerifyJob(row, key in db_keys))
            checked.add(key)
        for key, row in rows.items():
            if key in checked:
                continue
            if not _key_under_prefixes(key, prefixes):
                continue
            catalog_jobs.append(_VerifyJob(row, key in db_keys))
            checked.add(key)
        deferred = []
        if deep and args.max_gb > 0:
            catalog_jobs.sort(key=lambda job: (job.row.verified_at is not None,
                                               job.row.verified_at or "",
                                               job.row.copied_at or ""))
            budget = args.max_gb * 1e9
            rolling_bytes = 0
            kept = []
            for index, job in enumerate(catalog_jobs):
                if index == 0 or rolling_bytes + job.row.size <= budget:
                    kept.append(job)
                    rolling_bytes += job.row.size
                else:
                    deferred.extend(catalog_jobs[index:])
                    break
            catalog_jobs = kept
        jobs = planned_jobs + catalog_jobs
        total_bytes = sum(job.row.size for job in jobs if deep)
        done_bytes = 0
        started = time.monotonic()
        last_report = started
        now = datetime.now().isoformat(timespec="seconds")
        for job in jobs:
            result = _verify_row(job, dest, deep)
            results.append(result)
            if deep:
                done_bytes += job.row.size
                if time.monotonic() - last_report >= PROGRESS_EVERY_S:
                    print(f"[{len(results)}/{len(jobs)}] {_gb(done_bytes)}/{_gb(total_bytes)}")
                    last_report = time.monotonic()
            if job.in_catalog:
                if result.status == "HASH_FILLED":
                    catalog.set_hash_verified(job.row.key, result.new_hash, now)
                elif result.status == "OK":
                    catalog.set_verified(job.row.key, now)
                elif result.dest_verified:
                    catalog.set_verified(job.row.key, now)
        catalog.commit()
        counts = {}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
        summary = f"Verify {args.verify}: {_num(len(results))} files  " + "  ".join(
            f"{status} {_num(counts[status])}" for status in sorted(counts))
        log.out(summary)
        if deep and args.max_gb > 0:
            log.out(f"Rolling verify: checked {_num(len(catalog_jobs))} files / "
                    f"{_gb(sum(job.row.size for job in catalog_jobs))}, "
                    f"deferred {_num(len(deferred))} files / "
                    f"{_gb(sum(job.row.size for job in deferred))} "
                    f"(oldest verification first; the next run continues)")
        if planned.errors:
            log.out(f"Errors: {_num(len(planned.errors))} folders/files could not be read -> {log.path}")
        log.csv("_verify.csv", ["status", "rel", "source_path", "detail"],
                [(result.status, result.rel, result.source_path, result.detail)
                 for result in results])
        status_by_rel = {result.rel: result.status for result in results}
        log.csv("_plan.csv", ["action", "category", "size", "mtime", "src", "rel"],
                [(status_by_rel.get(planned_file.rel, ""), planned_file.category,
                  planned_file.size,
                  datetime.fromtimestamp(planned_file.mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M:%S"),
                  planned_file.src, planned_file.rel)
                 for planned_file in planned.files])
        bad = [result for result in results if result.status not in good]
        if planned.errors:
            return 1
        return 1 if bad else 0
    finally:
        catalog.close()


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
    parser.add_argument("--unselected", choices=("skip", "raw", "all"), default=UNSELECTED,
                        help="originals of photos that were not exported (default: %(default)s)")
    parser.add_argument("--unexported", choices=("skip", "copy"), default=UNEXPORTED,
                        help="folders without an export (default: %(default)s)")
    parser.add_argument("--c1-cache", action="store_true", default=COPY_CAPTUREONE_CACHE,
                        help="also copy the Capture One Cache folders")
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
