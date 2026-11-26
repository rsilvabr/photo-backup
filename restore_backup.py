#!/usr/bin/env python3
"""restore_backup - restore files from a photo-backup backup.

Reads the catalog of a backup made by selective_backup.py and copies files
from it: either to a folder (--to PATH, under PATH\\<rel>) or back to the
source path recorded in the catalog (--to-original). --only REL restricts to
one folder or file.

The backup is READ-ONLY for this script: nothing under --backup is written,
moved or deleted (the catalog is opened read-only). A target file that
already exists is NEVER overwritten: it is reported as ALREADY_THERE or
CONFLICT and left alone.

A TIFF or a JPEG the backup keeps as JXL (photo.tif.jxl, photo.jpg.jxl;
v0.14.0-rc2/rc3) comes back in its own format through jxl-photo (--jxlphoto /
JXLPHOTO, else the newest copy kept in the backup's tools folder): a JPEG byte
for byte, a TIFF with its pixels (lossless) or close to them (lossy).
--convert-back lossless brings back only what is lossless and keeps the rest as
JXL; --convert-back none (or --keep-jxl) keeps every one as JXL.

    python restore_backup.py --backup H:\\BACKUP --to D:\\Restored
    python restore_backup.py --backup H:\\BACKUP --to-original
    python restore_backup.py --backup H:\\BACKUP --only "2025\\shoot" --to D:\\Restored
"""

import argparse
import collections
import hashlib
import math
import os
import queue
import re
import shutil
import sqlite3
import stat as stat_module
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selective_backup as sb

__version__ = "0.14.0-rc9"

# ---- Settings -------------------------------------------------------------------
SPACE_MARGIN_GB = 5.0     # free space kept on each restore target drive (default --space-margin-gb)

GOOD = ("RESTORED", "RESTORED_UNVERIFIED", "RESTORED_FROM_JXL", "RESTORED_AS_JXL",
        "RESTORED_ORIGINAL", "ALREADY_THERE")
CONVERT_BACK = ("all", "lossless", "none")


class _JxlGroup:
    """A TIFF or a JPEG the backup keeps as JXL: its JXL files (one per page
    of a multipage TIFF), restored as one file under its own name."""

    def __init__(self, converted, source_path):
        self.conv = converted               # the first page's sb._Converted
        self.rel = converted.source_rel     # the TIFF's / JPEG's place in the backup's tree
        self.size = converted.source_size   # its size when it was converted
        self.source_path = source_path
        self.members = []                   # [(sb._Row, sb._Converted)], by page
        self.sha256 = converted.source_sha256
        self.kind = converted.kind or sb._convert_kind(converted.source_rel)
        # rc9 (round 2 of keep-days): the TIFF copy its JXL replaced, kept in the
        # backup's originals\ with proof that it holds these very bytes:
        # (stored_rel, size, mtime_ns) of the one restored instead of decoding
        self.original = None
        self.fallback = None        # the status it has when that copy cannot be used

    def names(self):
        return [self.rel] + [row.rel for row, _conv in self.members]

    def whole(self):
        pages = sorted(conv.page for _row, conv in self.members)
        return (len(self.members) == self.conv.pages
                and pages == list(range(len(self.members))))

    def jxl_size(self):
        return sum(row.size for row, _conv in self.members)

    def lossless(self):
        """1 when the original comes back exactly (a JPEG's bytes, a lossless
        TIFF's pixels), 0 when lossy, None when the catalog does not know.
        A multipage TIFF: lossless only when every page's row says so (rc9, N4)."""
        if self.kind == "jpeg":
            return 1
        values = [conv.lossless for _row, conv in self.members] or [self.conv.lossless]
        return sb._group_lossless(values)


def _drive_root(path):
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if drive:
        return drive + os.sep
    return os.path.abspath(os.sep)


def _only_text(pattern):
    """An --only value as it is compared: no quotes, no spaces at its ends, no
    separator at its end, backslashes, lower case. Empty means it names
    nothing (before v0.13.2 it meant everything: T2 of the 261112 sweep)."""
    return str(pattern).strip().strip('"').replace("/", "\\").casefold().rstrip("\\/")


def _under_any(rel, patterns):
    folded = rel.replace("/", "\\").casefold()
    for pattern in patterns:
        text = _only_text(pattern)
        if text and (folded == text or folded.startswith(text + "\\")):
            return True
    return False


def _target_for(row, args, kept_jxl=False):
    """Where a catalog row (or a _JxlGroup) is restored. A JXL restored as it
    is (--keep-jxl) goes back beside its TIFF's place, under its own name."""
    if args.to_original:
        if kept_jxl:
            return os.path.join(os.path.dirname(row.source_path), os.path.basename(row.rel))
        return row.source_path
    return os.path.join(os.path.abspath(args.to), row.rel)


def _find_jxlphoto(given, backup):
    """(path, where) of the jxl-photo that turns a JXL back into its TIFF:
    --jxlphoto / JXLPHOTO when it is there, else the newest one the backup
    keeps in its tools folder (KEEP_TOOLS_IN_BACKUP); (None, why) when none.
    The restore's own minimum (JXLPHOTO_RESTORE_MIN_VERSION, v0.14.0-rc4): a
    backup made with an older jxl-photo comes back with the copy it keeps."""
    minimum = sb.JXLPHOTO_RESTORE_MIN_VERSION
    if given and sb._jxlphoto_problem(given, minimum) is None:
        return given, "JXLPHOTO" if given == sb.JXLPHOTO else "--jxlphoto"
    tools = os.path.join(backup, sb.CATALOG_FOLDER, sb.TOOLS_FOLDER)
    found = []
    try:
        with os.scandir(sb._io(tools)) as listing:
            for entry in listing:
                if (not entry.is_dir(follow_symlinks=False)
                        or not entry.name.casefold().startswith("jxl-photo")):
                    continue
                for name in ("jxl_photo.py", "jxl-photo.exe"):
                    candidate = os.path.join(tools, entry.name, name)
                    if os.path.isfile(sb._io(candidate)):
                        numbers = tuple(int(part) for part in re.findall(r"\d+", entry.name))
                        found.append((numbers, name == "jxl_photo.py", candidate))
    except OSError:
        pass
    why = sb._jxlphoto_problem(given, minimum) or "jxl-photo not found"
    for _numbers, _is_py, candidate in sorted(found, reverse=True):
        problem = sb._jxlphoto_problem(candidate, minimum)  # the contract holds for it too (JP-1)
        if problem is None:
            return candidate, "kept in the backup"
        why = f"{why}; the one kept in the backup: {problem}"
    return None, why


def _kept_original(group, candidates, backup):
    """(stored_rel, size, mtime_ns) of the TIFF copy kept in the backup (the
    catalog's `originals` table, read only) that PROVES to be the TIFF this
    JXL group was made from, or None (rc9, round 2 of keep-days). A row is
    proof when its file is there, a plain file with the row's size, and its
    SHA-256 is the group's `source_sha256`: the row's hash when it has one (the
    copy checks it again), else the file is read here. Several with proof: the
    newest. Anything that cannot be read is no proof (the JXL is decoded)."""
    if group.kind != "tiff" or not group.sha256:
        return None
    for stored_rel, _source_rel, size, mtime_ns, sha256, _stored_at in sorted(
            candidates, key=lambda row: str(row[5] or ""), reverse=True):
        if size != group.size or (sha256 is not None and sha256 != group.sha256):
            continue
        path = os.path.join(backup, stored_rel)
        try:
            info = os.stat(sb._io(path), follow_symlinks=False)
            if not stat_module.S_ISREG(info.st_mode) or info.st_size != size:
                continue
            if sha256 is None and sb._sha256_file(path) != group.sha256:
                continue
        except OSError:
            continue
        return stored_rel, size, mtime_ns
    return None


def _restore_original(group, backup, target):
    """Copy the TIFF original kept in the backup to the target (the restore's
    own copy: a temp file beside the target, its SHA-256 checked against the
    group's `source_sha256`, never replacing a file). (status, detail); a
    status None = that copy is no proof after all (the JXL is decoded)."""
    stored_rel, size, mtime_ns = group.original
    src = os.path.join(backup, stored_rel)
    try:
        info = os.stat(sb._io(src), follow_symlinks=False)
    except OSError as exc:
        return None, f"{stored_rel}: {exc}"
    if info.st_size != size:
        return None, f"{stored_rel}: size {info.st_size} != {size}"
    tolerance = int(sb.MTIME_TOLERANCE_S * 1_000_000_000)
    source_mtime = group.conv.source_mtime_ns
    exact = source_mtime if abs(info.st_mtime_ns - source_mtime) <= tolerance else None
    planned = sb.PlannedFile(src, group.rel, size, mtime_ns, "restore")
    try:
        sb._copy_file(planned, target, "hash", expected_sha256=group.sha256, no_clobber=True,
                      mtime_ns=exact)
    except FileExistsError:
        return "CONFLICT", "the target appeared during the restore; left as it was"
    except RuntimeError as exc:
        if "content differs from the catalog" in str(exc):
            return None, f"{stored_rel}: not the bytes the JXL was made from"
        raise
    return ("RESTORED_ORIGINAL", f"the original TIFF kept in the backup: {stored_rel} "
                                 f"(SHA-256 checked; no JXL decoded)")


def _resolve_group_exists(group, target):
    """A file kept as JXL whose target already has a file: never overwrite."""
    stat = os.stat(sb._io(target), follow_symlinks=False)
    if stat.st_size == group.size and sb._sha256_file(target) == group.sha256:
        return ("ALREADY_THERE", f"the original {group.kind.upper()}")
    if group.kind == "jpeg":
        return ("CONFLICT", "target exists with different content")
    return ("CONFLICT", "target exists and is not the original TIFF (a TIFF made from its JXL "
                        "earlier is never the same bytes either); left as it was")


def _restore_group(group, backup, target, jxlphoto, work, servers=None):
    """Turn a TIFF kept as JXL back into a TIFF at the target: the JXL files
    are copied (hash checked against the catalog) under jxl-photo's own names
    into the work folder -- a folder of this restore next to the target,
    never in the backup --, jxl-photo decodes them there (its own temp files
    in there too, v0.14.0-rc5), and the one TIFF it makes takes the target's
    name, never replacing a file. A '%' in the work folder's path (the target's
    folder has one, JP-22) or a work folder too deep for a temp folder (240
    characters or more: Windows hangs a program whose temp folder has 260)
    gives jxl-photo a temp folder of its own in the system's temp folder
    instead, removed after (v0.14.0-rc6). `servers`: jxl-photo's serve processes
    of this restore (sb._JxlServers, JP-23), else one call per group. A JPEG
    rebuilt by djxl makes no folder: `work` is the one file it is written to
    (v0.14.0-rc7). (status, detail)."""
    folder = os.path.dirname(os.path.abspath(target))
    os.makedirs(sb._io(folder), exist_ok=True)
    if os.path.lexists(sb._io(work)):
        return "ERROR", f"something is in the way of the restore's own folder: {work}"
    if group.kind == "jpeg" and sb._djxl_path() is not None:
        return _rebuild_jpeg_with_djxl(group, backup, target, work)
    work_in, work_out = os.path.join(work, "jxl"), os.path.join(work, "tiff")
    work_temp = os.path.join(work, "temp")      # jxl-photo's own temp files (v0.14.0-rc5)
    os.makedirs(sb._io(work_in))
    os.makedirs(sb._io(work_out))
    os.makedirs(sb._io(work_temp))
    ext = os.path.splitext(group.rel)[1]
    own_temp = None
    try:
        if "%" in work_temp or len(work_temp) >= sb._JXL_TEMP_MAX_CHARS:
            # a '%' (JP-22), or a deep target: Windows hangs a program whose temp
            # folder has 260 characters or more (v0.14.0-rc6 review, M1)
            system_temp = tempfile.gettempdir()
            if ("%" in system_temp or len(system_temp) + 30 >= sb._JXL_TEMP_MAX_CHARS
                    or sb._is_under(system_temp, backup)
                    or sb._is_under(sb._real(system_temp), sb._real(backup))):
                return "ERROR", ("jxl-photo cannot keep its temp files next to the target (a '%' "
                                 "in its path, or 240 characters or more) nor in the system's "
                                 "temp folder (a '%' in its path, too deep, or inside the "
                                 "backup) (JP-22); set TEMP and TMP to a short folder without "
                                 "'%'")
            own_temp = tempfile.mkdtemp(prefix="pbrestore_")
        for row, _conv in group.members:
            src = os.path.join(backup, row.rel)
            size = os.stat(sb._io(src)).st_size
            if size != row.size:
                return ("CORRUPT_IN_BACKUP",
                        f"{row.rel}: backup file size {size} != {row.size} (backup copy corrupt?)")
            name = os.path.basename(row.rel)
            if ext and name.casefold().endswith((ext + ".jxl").casefold()):
                name = name[:-len(ext + ".jxl")] + ".jxl"     # jxl-photo's own name
            planned = sb.PlannedFile(src, name, row.size, row.mtime_ns, "restore")
            try:
                sb._copy_file(planned, os.path.join(work_in, name), "hash",
                              expected_sha256=row.sha256, no_clobber=True)
            except RuntimeError as exc:
                if "content differs from the catalog" in str(exc):
                    return "CORRUPT_IN_BACKUP", f"{row.rel}: {exc}"
                raise
        jpeg = group.kind == "jpeg"
        if jpeg:            # the JPEG rebuilt from its JXL (JP-16)
            arguments = sb._jxlphoto_rebuild_args(
                os.path.join(work_in, os.listdir(sb._io(work_in))[0]), work_out)
            wanted, item = (".jpg", ".jpeg"), "JP-16"
        else:               # the pages the TIFF had, each with its own bit depth, and no
            # preview page of jxl-photo's own (the original had none of it)
            arguments = sb._jxlphoto_decode_args(work_in, work_out)
            wanted, item = (".tif", ".tiff"), "JP-10"
        try:
            run = sb._jxlphoto_call(servers, jxlphoto, arguments, sb._jxl_timeout_s(), None,
                                    own_temp or work_temp)
        except subprocess.TimeoutExpired:
            return "ERROR", f"jxl-photo took more than {sb.JXL_TIMEOUT_MIN} min"
        except sb._ConvertFailed as exc:
            return "ERROR", str(exc)
        said = run.said
        if run.returncode != 0:
            return "ERROR", f"jxl-photo stopped with exit {run.returncode} ({item}): {said}"
        with os.scandir(sb._io(work_out)) as listing:
            made = [entry.name for entry in listing if entry.is_file(follow_symlinks=False)
                    and entry.name.casefold().endswith(wanted)]
        if len(made) != 1:
            return ("ERROR", f"jxl-photo made {len(made)} {group.kind.upper()} instead of 1 "
                             f"({item}): {said}")
        result = os.path.join(work_out, made[0])
        if jpeg and sb._sha256_file(result) != group.sha256:
            return "ERROR", ("the JPEG rebuilt from its JXL is not the original's bytes (JP-16); "
                             "nothing was written at the target")
        mtime_ns = group.conv.source_mtime_ns
        os.utime(sb._io(result), ns=(mtime_ns, mtime_ns))   # the original's date
        sb._flush_temp(result)
        try:
            sb._rename_no_clobber(result, target)
        except FileExistsError:
            return "CONFLICT", "the target appeared during the restore; left as it was"
        if jpeg:
            return ("RESTORED", f"rebuilt from its JXL by {group.conv.tool or 'jxl-photo'}: the "
                                f"original JPEG's bytes (SHA-256 checked)")
        return ("RESTORED_FROM_JXL",
                f"made from {len(group.members)} JXL by {group.conv.tool or 'jxl-photo'} "
                f"({group.conv.settings}); not the original TIFF's bytes")
    finally:
        sb._remove_own_folder(work)
        if own_temp:
            sb._remove_own_folder(own_temp)


def _rebuild_jpeg_with_djxl(group, backup, target, work):
    """A JPEG kept as JXL, the fast way (v0.14.0-rc4): its JXL is read once from
    the backup (never written there) and checked against its catalog SHA-256;
    libjxl's djxl rebuilds the JPEG in memory (JP-18), which must have the
    original's SHA-256; it is written next to the target and takes its name,
    never replacing a file. `work` is the file it is written to, next to the
    target (no folder: four folder operations less per JPEG, v0.14.0-rc7),
    created here ("xb": a file already there is someone else's and stays) and
    removed when it does not take the target's name. (status, detail)."""
    row, _conv = group.members[0]
    with open(sb._io(os.path.join(backup, row.rel)), "rb") as handle:
        data = handle.read()
    if len(data) != row.size or (row.sha256 and hashlib.sha256(data).hexdigest() != row.sha256):
        return ("CORRUPT_IN_BACKUP", f"{row.rel}: the backup copy is not the one the catalog "
                                     f"recorded (size or SHA-256)")
    try:
        jpeg = sb._rebuild_jpeg(None, None, work, sb._jxl_timeout_s(), data=data)
    except sb._ConvertFailed as exc:
        return "ERROR", f"{exc}; nothing was written at the target"
    if hashlib.sha256(jpeg).hexdigest() != group.sha256:
        return "ERROR", ("the JPEG rebuilt from its JXL is not the original's bytes (JP-18); "
                         "nothing was written at the target")
    try:
        handle = open(sb._io(work), "xb")
    except FileExistsError:
        return "ERROR", f"something is in the way of the restore's own file: {work}"
    renamed = False
    try:
        with handle:
            handle.write(jpeg)
            handle.flush()
            os.fsync(handle.fileno())
        mtime_ns = group.conv.source_mtime_ns
        os.utime(sb._io(work), ns=(mtime_ns, mtime_ns))     # the original's date
        try:
            sb._rename_no_clobber(work, target)
        except FileExistsError:
            return "CONFLICT", "the target appeared during the restore; left as it was"
        renamed = True
    finally:
        if not renamed:                 # this restore's own file, never the target's
            sb._remove_temp(work)
    return ("RESTORED", "rebuilt from its JXL by djxl: the original JPEG's bytes "
                        "(SHA-256 checked)")


RESTORE_STOPPED_SAYING = ("Stopped (Ctrl+C). {count} files came back and are in place (the list: "
                          "{csv}); a file being made when you pressed it was not left at its "
                          "target. The backup was only read. Nothing is being written now.")


def _run_group(group, backup, target, jxlphoto, work, servers=None):
    """_restore_group on a worker thread: every error becomes a status."""
    try:
        return _restore_group(group, backup, target, jxlphoto, work, servers)
    except Exception as exc:
        return "ERROR", str(exc)


def _resolve_exists(row, src, target):
    """The outcome for a target that already has a file: never overwrite."""
    stat = os.stat(sb._io(target), follow_symlinks=False)
    if stat.st_size != row.size:
        return ("CONFLICT", f"target exists with size {stat.st_size} != {row.size}")
    expected = row.sha256 if row.sha256 is not None else sb._sha256_file(src)
    if sb._sha256_file(target) == expected:
        return ("ALREADY_THERE", "")
    return ("CONFLICT", "target exists with different content")


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="restore_backup.py",
        description="Restore files from a photo-backup backup (the backup is read-only).")
    parser.add_argument("--backup", required=True, metavar="PATH",
                        help="backup root (the folder that holds the catalog)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--to", metavar="PATH",
                       help="restore each file under PATH\\<rel>")
    group.add_argument("--to-original", action="store_true",
                       help="restore each file to the source_path recorded in the catalog")
    parser.add_argument("--only", action="append", default=None, metavar="REL",
                        help="restore only this folder or file (repeatable, case-insensitive)")
    parser.add_argument("--expect-backup-id", default=None, metavar="ID",
                        help="abort unless the backup holds this backup ID")
    parser.add_argument("--space-margin-gb", type=float, default=SPACE_MARGIN_GB, metavar="N",
                        help="free space kept on the target drive (default: %(default)s)")
    parser.add_argument("--convert-back", choices=CONVERT_BACK, default="all",
                        help="the TIFFs and JPEGs the backup keeps as JXL: all back to their "
                             "format (default), only the lossless ones (every JPEG, a lossless "
                             "TIFF; the rest stays JXL), or none (every one as JXL)")
    parser.add_argument("--keep-jxl", action="store_true",
                        help="the same as --convert-back none")
    parser.add_argument("--jxlphoto", default=sb.JXLPHOTO, metavar="PATH",
                        help="jxl-photo's jxl_photo.py or jxl-photo.exe, to turn a file kept as "
                             "JXL back into its TIFF or JPEG (default: JXLPHOTO; else the newest "
                             "one kept in the backup)")
    parser.add_argument("--dry-run", action="store_true",
                        help="plan and report, restore nothing")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask for confirmation")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv=None):
    try:
        return _main(argv)
    except KeyboardInterrupt:   # while planning; the restore itself handles its own (B20)
        print("Interrupted.", file=sys.stderr)
        return 130


def _main(argv):
    sb._start_output()          # ～ paths on a cp1252 console; each line out at once
    sb._STOPPING.clear()
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return int(code) if isinstance(code, int) else 2
    if not math.isfinite(args.space_margin_gb) or args.space_margin_gb < 0:
        print("--space-margin-gb must be a number, 0 or more", file=sys.stderr)
        return 2
    # An empty --to (a variable left empty in a .cmd) restored into the current
    # folder, and an empty --only selected everything (T2 of the 261112 sweep)
    if args.to is not None and not str(args.to).strip().strip('"').strip():
        print("--to is empty: give the folder to restore into", file=sys.stderr)
        return 2
    if args.only is not None and any(not _only_text(pattern) for pattern in args.only):
        print("--only is empty: give a folder or a file of the backup "
              "(without --only, everything is restored)", file=sys.stderr)
        return 2
    backup = os.path.abspath(args.backup)
    try:
        catalog = sb._Catalog(backup, create=False, readonly=True)
    except (RuntimeError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        rows = catalog.all_rows()
        converted = catalog.converted_rows()
        originals = catalog.originals_rows()     # rc9: the TIFF copies kept in originals\
    except sqlite3.Error as exc:
        print(f"cannot read catalog {catalog.path}: {exc}", file=sys.stderr)
        return 2
    finally:
        catalog.close()
    if args.expect_backup_id:
        found = sb.read_backup_id(backup)
        if found != args.expect_backup_id:
            print(f"Destination {backup} is not the expected backup "
                  f"(expected {args.expect_backup_id}, found {found or 'no backup here'})",
                  file=sys.stderr)
            return 2
    if args.to is not None:
        to = os.path.abspath(args.to)
        # also where the two really are: a junction or a subst letter (T4)
        if sb._overlaps(to, backup):
            print(f"--to cannot be inside the backup or contain it: {to} vs {backup}"
                  + sb._really(to, backup), file=sys.stderr)
            return 2
    else:
        to = None
    backup_real = sb._real(backup)
    real_folders = {}

    def inside_backup(target):
        """Whether a target lands in the backup, as typed or where its folder
        really is (a junction on the way): the restore never writes there."""
        if sb._is_under(target, backup):
            return True
        folder = os.path.dirname(os.path.abspath(target))
        key = os.path.normcase(folder)
        if key not in real_folders:
            real_folders[key] = sb._real(folder)
        return sb._is_under(real_folders[key], backup_real)

    # a TIFF or JPEG kept as JXL is one entry (its JXL files are one file);
    # --convert-back says which ones go back to their format: all, only the
    # lossless ones (the others stay JXL), or none
    convert_back = "none" if args.keep_jxl else args.convert_back
    entries = []
    groups = {}
    for row in rows:
        conv = converted.get(row.key)
        if conv is None or convert_back == "none":
            entries.append(row)
            continue
        group = groups.get(conv.source_key)
        if group is None:
            group = groups[conv.source_key] = _JxlGroup(conv, row.source_path)
            entries.append(group)
        group.members.append((row, conv))
    for group in groups.values():
        group.members.sort(key=lambda member: member[1].page)
        group.conv = group.members[0][1]
    patterns = args.only
    # rc9 (round 2 of keep-days): a TIFF whose copy its JXL replaced is kept in
    # originals\ comes back as that copy -- its own bytes, no jxl-photo -- when
    # the copy proves to be the TIFF the JXL was made from (_kept_original)
    originals_by_source = collections.defaultdict(list)
    for item in originals:
        originals_by_source[str(item[1]).casefold()].append(item)
    proven = {}

    def original_of(group):
        key = group.conv.source_key
        if key not in proven:
            proven[key] = _kept_original(
                group, originals_by_source.get(group.rel.casefold(), []), backup)
        return proven[key]

    def selected(names):
        return patterns is None or any(_under_any(name, [pattern])
                                       for pattern in patterns for name in names)

    if convert_back == "lossless":      # a lossy (or unknown) TIFF stays JXL, unless its
        # original TIFF is kept in the backup with proof: that one is exact
        kept_as_jxl = {key for key, group in groups.items() if group.lossless() != 1
                       and not (selected(group.names()) and original_of(group))}
        entries = [item for entry in entries for item in
                   ([member for member, _conv in entry.members]
                    if isinstance(entry, _JxlGroup) and entry.conv.source_key in kept_as_jxl
                    else [entry])]
        groups = {key: group for key, group in groups.items() if key not in kept_as_jxl}
    jxlphoto = jxlphoto_where = djxl = None
    if groups:
        jxlphoto, jxlphoto_where = _find_jxlphoto(args.jxlphoto, backup)
        djxl = sb._djxl_path()          # a JPEG needs only djxl (JP-18), a TIFF jxl-photo
    matched = set()
    items = []
    for row in entries:
        is_group = isinstance(row, _JxlGroup)
        names = [row.rel] + ([member.rel for member, _conv in row.members] if is_group else [])
        if not is_group and row.key in converted:   # a JXL kept as it is: its TIFF's/JPEG's name too
            names.append(converted[row.key].source_rel)
        if patterns is not None:
            hits = [pattern for pattern in patterns
                    if any(_under_any(name, [pattern]) for name in names)]
            if not hits:
                continue
            matched.update(hits)
        src = None if is_group else os.path.join(backup, row.rel)
        target = _target_for(row, args, kept_jxl=not is_group and row.key in converted)
        if is_group and not inside_backup(target):
            row.original = original_of(row)
        if inside_backup(target):
            status = "REFUSED"
        elif is_group and row.original is not None:
            # the TIFF original, kept with proof: no JXL is decoded, no jxl-photo needed
            status = "EXISTS" if sb._exists(target) else "RESTORE"
        elif is_group and (not row.whole() or not all(
                sb._exists(os.path.join(backup, member.rel)) for member, _conv in row.members)):
            status = "MISSING_IN_BACKUP"
        elif not is_group and not sb._exists(src):
            status = "MISSING_IN_BACKUP"
        elif sb._exists(target):
            status = "EXISTS"
        elif is_group and jxlphoto is None and not (row.kind == "jpeg" and djxl):
            status = "NEEDS_JXLPHOTO"
        else:
            status = "RESTORE"
        if is_group and row.original is not None:
            # what it is when that copy cannot be used after all: decoded as before
            if not row.whole() or not all(sb._exists(os.path.join(backup, member.rel))
                                          for member, _conv in row.members):
                row.fallback = "MISSING_IN_BACKUP"
            elif jxlphoto is None:
                row.fallback = "NEEDS_JXLPHOTO"
            else:
                row.fallback = "RESTORE"
        items.append((status, row, src, target))
    restores = [item for item in items if item[0] == "RESTORE"]
    by_drive = {}
    for _status, row, _src, target in restores:
        root = _drive_root(target)
        # a TIFF made from its JXL needs its own size plus the JXL copied beside it
        need = row.size + (row.jxl_size() if isinstance(row, _JxlGroup)
                           and row.original is None else 0)
        by_drive[root] = by_drive.get(root, 0) + need
    margin = int(float(args.space_margin_gb) * 1e9)
    space_ok = True
    space_lines = []
    for root in sorted(by_drive):
        try:
            free = shutil.disk_usage(root).free
        except OSError as exc:
            space_ok = False
            space_lines.append(f"Space: {root} not available ({exc})")
            continue
        need = by_drive[root]
        fits = need <= free - margin
        space_ok = space_ok and fits
        space_lines.append(f"Space: {root} need {sb._gb(need)}, free {sb._gb(free)}, "
                           f"margin {sb._gb(margin)} -> " + ("fits" if fits else "DOES NOT FIT"))
    counts = {}
    bytes_by_status = {}
    for status, row, _src, _target in items:
        counts[status] = counts.get(status, 0) + 1
        bytes_by_status[status] = bytes_by_status.get(status, 0) + row.size
    plan_text = "  ".join(f"{status} {sb._num(counts[status])} / {sb._gb(bytes_by_status[status])}"
                          for status in sorted(counts))
    lines = [
        f"restore_backup {__version__}",
        f"Backup:    {backup}",
        ("Target:    original places (source_path from the catalog)" if args.to_original
         else f"Target:    {to}"),
        f"Catalog:   {sb._num(len(rows))} files; selected {sb._num(len(items))}",
        f"Plan:      {plan_text if plan_text else '(nothing to restore)'}",
    ] + space_lines
    from_original = [item[1] for item in items
                     if isinstance(item[1], _JxlGroup) and item[1].original is not None]
    if from_original:
        lines.append(f"Kept as JXL, their original TIFF kept in the backup with proof: "
                     f"{sb._num(len(from_original))} TIFF (restored as that copy, "
                     f"RESTORED_ORIGINAL; no JXL decoded)")
    from_jxl = [item[1] for item in items
                if isinstance(item[1], _JxlGroup) and item[1].original is None]
    if from_jxl:
        kinds = collections.Counter(group.kind.upper() for group in from_jxl)
        how = [f"JPEG by djxl ({djxl})"] if djxl and kinds.get("JPEG") else []
        if kinds.get("TIFF") or not djxl:
            how.append(f"by {sb._jxlphoto_version(jxlphoto)}, {jxlphoto_where}: {jxlphoto}"
                       if jxlphoto else "jxl-photo is needed")
        lines.append(f"Kept as JXL, back to their format ({convert_back}): "
                     f"{', '.join(f'{sb._num(count)} {kind}' for kind, count in sorted(kinds.items()))}"
                     + "".join(f" -- {text}" for text in how))
    as_jxl = [row for _status, row, _src, _target in items
              if not isinstance(row, _JxlGroup) and row.key in converted]
    if as_jxl:
        sources = {converted[row.key].source_key for row in as_jxl}
        lines.append(f"Kept as JXL, restored as JXL ({convert_back}): {sb._num(len(sources))} TIFF "
                     f"or JPEG ({sb._num(len(as_jxl))} JXL files; --convert-back all gives their "
                     f"TIFF or JPEG)")
    if counts.get("NEEDS_JXLPHOTO"):
        lines.append(f"NEEDS_JXLPHOTO: {sb._num(counts['NEEDS_JXLPHOTO'])} TIFF or JPEG kept as "
                     f"JXL cannot be made without jxl-photo ({jxlphoto_where}; a JPEG needs only "
                     f"libjxl's djxl). Set JXLPHOTO or --jxlphoto, or restore them as JXL with "
                     f"--convert-back none.")
    # B21: an --only that matches nothing is named, and the run ends with exit 1
    unmatched = [pattern for pattern in (patterns or []) if pattern not in matched]
    for pattern in unmatched:
        lines.append(f"--only {pattern}: matches nothing in the catalog (not restored)")
    stamp = time.strftime("%y%m%d_%H%M%S")
    try:
        log = sb._Logger(os.path.join(sb.LOG_DIR, f"{stamp}_restore"))
    except OSError as exc:
        print(f"cannot write the log directory {sb.LOG_DIR}: {exc}", file=sys.stderr)
        return 2
    try:
        log.log("restore_backup " + __version__)
        for line in lines:
            log.out(line)
        if not space_ok and not args.dry_run:
            message = "Not enough space on a target drive; nothing restored."
            print(message, file=sys.stderr)
            log.log(message)
            return 2
        if args.dry_run:
            log.log("dry run: nothing restored")
            log.csv(".csv", ["status", "rel", "target", "detail"],
                    [(status, row.rel, target, "") for status, row, _src, target in items])
            bad = [item for item in items
                   if item[0] in ("MISSING_IN_BACKUP", "REFUSED", "NEEDS_JXLPHOTO")]
            return 1 if bad or unmatched else 0
        if not args.yes:
            try:
                answer = input(f"Restore {sb._num(len(restores))} files? [y/N, Enter = no] ")
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
        # The files kept as JXL come back on worker threads (JXL_WORKERS JPEGs,
        # fewer TIFFs at a time: JXL_TIFF_WORKERS) while the plain copies go on in
        # order; each result keeps its place in the list.
        results = [None] * len(items)
        extra_results = []      # the 2nd, 3rd ... JXL file of a group restored as JXL (S3)
        result_rows = []
        bad = False
        total_bytes = sum(row.size for _status, row, _src, _target in restores)
        done_bytes = 0
        started = time.monotonic()
        last_report = started
        tolerance = int(sb.MTIME_TOLERANCE_S * 1_000_000_000)
        workers = sb._jxl_workers()
        jpeg_pool = ThreadPoolExecutor(max_workers=workers)
        tiff_pool = ThreadPoolExecutor(max_workers=min(workers, sb._jxl_tiff_workers()))
        servers, serve_root = None, None
        needs_jxlphoto = any(
            isinstance(row, _JxlGroup) and status not in ("MISSING_IN_BACKUP", "REFUSED",
                                                          "NEEDS_JXLPHOTO")
            and row.original is None
            and (row.kind == "tiff" or sb._djxl_path() is None)
            for status, row, _src, _target in items)
        if needs_jxlphoto and jxlphoto and sb._serve_wanted(sb._jxlphoto_version(jxlphoto)):
            system_temp = tempfile.gettempdir()
            if ("%" in system_temp
                    or len(system_temp) >= sb._JXL_TEMP_MAX_CHARS - sb._WORK_TEMP_EXTRA - 30
                    or sb._is_under(system_temp, backup)
                    or sb._is_under(sb._real(system_temp), sb._real(backup))):
                log.log("jxl-photo serve not used: the system's temp folder has a '%' in its "
                        "path, is too deep or is inside the backup; one jxl-photo per file")
            else:
                try:
                    serve_root = tempfile.mkdtemp(prefix="pbrestore_serve_")
                    servers = sb._JxlServers(jxlphoto, serve_root,
                                             log_dir=os.path.join(os.path.abspath(sb.LOG_DIR),
                                                                  "jxl-photo"))
                except OSError as exc:
                    log.log(f"jxl-photo serve not used: {exc}; one jxl-photo per file")
        pending = {}
        # each group's future lands here when it is done: the main thread takes
        # the finished ones from the queue instead of asking every pending group
        # at every item (v0.14.0-rc6 review, S2: quadratic, 37 min of CPU for
        # 114,683 JPEGs)
        finished_queue = queue.SimpleQueue()

        speed = sb._Speed()

        def report(force=False):
            nonlocal last_report
            now = time.monotonic()
            if force or now - last_report >= sb.PROGRESS_EVERY_S:
                finished = sum(1 for item in results if item is not None)
                print(f"[{finished}/{len(items)}] {sb._gb(done_bytes)}/{sb._gb(total_bytes)}  "
                      f"{speed.text(done_bytes, total_bytes)}")
                last_report = now

        def collect(future):
            """A file kept as JXL is back (or not): its result, on the main thread."""
            nonlocal bad, done_bytes
            index, row, target = pending.pop(future)
            status, detail = future.result()
            if status in ("RESTORED_FROM_JXL", "RESTORED"):
                done_bytes += row.size
            else:
                bad = True
            results[index] = (status, row.rel, target, detail)
            log.log(f"{status} {row.rel}" + (f": {detail}" if detail else ""))

        def collect_finished():
            while True:
                try:
                    future = finished_queue.get_nowait()
                except queue.Empty:
                    return
                if future in pending:
                    collect(future)

        def copy_plain(row, src, target):
            """A catalog row copied as it is in the backup: (status, detail, ok)."""
            try:
                backup_stat = os.stat(sb._io(src))
                backup_size = backup_stat.st_size
                if backup_size != row.size:
                    raise RuntimeError(
                        f"content differs from the catalog: backup file size "
                        f"{backup_size} != {row.size} (backup copy corrupt?)")
                planned = sb.PlannedFile(src, row.rel, row.size, row.mtime_ns, "restore")
                # A backup on FAT/exFAT keeps a date in 2 s steps: a copy
                # whose date is the catalog's within that step is restored
                # with the exact date the original had. Any other copy
                # keeps the date it has.
                exact = (row.mtime_ns
                         if abs(backup_stat.st_mtime_ns - row.mtime_ns) <= tolerance
                         else None)
                sb._copy_file(planned, target, "hash", expected_sha256=row.sha256,
                              no_clobber=True, mtime_ns=exact)
                status = "RESTORED" if row.sha256 else "RESTORED_UNVERIFIED"
                detail = ""
                if row.key in converted:        # a JXL kept as it is (--convert-back)
                    status = "RESTORED_AS_JXL"
                    detail = (f"the JXL of {converted[row.key].source_rel}, as it is in the "
                              f"backup ({convert_back})")
                return status, detail, True
            except FileExistsError:
                return ("CONFLICT", "the target appeared during the restore; left as it was",
                        False)
            except RuntimeError as exc:
                return (("CORRUPT_IN_BACKUP" if "content differs from the catalog" in str(exc)
                         else "ERROR"), str(exc), False)
            except Exception as exc:
                return "ERROR", str(exc), False

        def restore_members_as_jxl(group):
            """The review of rc9, S3: a LOSSY group under --convert-back lossless
            whose kept original proved wrong at its copy is never decoded: each
            of its JXL files is restored as it is (RESTORED_AS_JXL), as any
            lossy group in that mode. [(status, rel, target, detail)]."""
            nonlocal bad, done_bytes
            out = []
            for member, _conv in group.members:
                member_src = os.path.join(backup, member.rel)
                member_target = _target_for(member, args, kept_jxl=True)
                if inside_backup(member_target):
                    status, detail, ok = "REFUSED", "the target is inside the backup", False
                elif not sb._exists(member_src):
                    status, detail, ok = "MISSING_IN_BACKUP", "backup file missing", False
                elif sb._exists(member_target):
                    try:
                        status, detail = _resolve_exists(member, member_src, member_target)
                        ok = status != "CONFLICT"
                    except OSError as exc:
                        status, detail, ok = "ERROR", str(exc), False
                else:
                    status, detail, ok = copy_plain(member, member_src, member_target)
                    if ok:
                        done_bytes += member.size
                if not ok:
                    bad = True
                out.append((status, member.rel, member_target, detail))
                log.log(f"{status} {member.rel}" + (f": {detail}" if detail else ""))
            return out

        try:
            for index, (status, row, src, target) in enumerate(items):
                collect_finished()
                report()
                if status == "MISSING_IN_BACKUP":
                    results[index] = (status, row.rel, target, "backup file missing")
                    bad = True
                    log.log(f"MISSING_IN_BACKUP {row.rel}")
                    continue
                if status == "REFUSED":
                    results[index] = (status, row.rel, target, "the target is inside the backup")
                    bad = True
                    log.log(f"REFUSED {row.rel}: the target is inside the backup")
                    continue
                is_group = isinstance(row, _JxlGroup)
                if status == "NEEDS_JXLPHOTO":
                    results[index] = (status, row.rel, target,
                                      f"kept as JXL; jxl-photo not found ({jxlphoto_where})")
                    bad = True
                    log.log(f"NEEDS_JXLPHOTO {row.rel}")
                    continue
                if sb._exists(target):
                    try:
                        outcome, detail = (_resolve_group_exists(row, target) if is_group
                                           else _resolve_exists(row, src, target))
                    except OSError as exc:
                        results[index] = ("ERROR", row.rel, target, str(exc))
                        bad = True
                        log.log(f"ERROR {row.rel}: {exc}")
                        continue
                    results[index] = (outcome, row.rel, target, detail)
                    if outcome == "CONFLICT":
                        bad = True
                    log.log(f"{outcome} {row.rel}")
                    continue
                if is_group and row.original is not None:
                    # rc9: the TIFF original kept in the backup, copied as it is
                    try:
                        outcome, detail = _restore_original(row, backup, target)
                    except Exception as exc:
                        outcome, detail = None, str(exc)
                    if outcome is not None:
                        if outcome == "RESTORED_ORIGINAL":
                            done_bytes += row.size
                        else:
                            bad = True
                        results[index] = (outcome, row.rel, target, detail)
                        log.log(f"{outcome} {row.rel}" + (f": {detail}" if detail else ""))
                        continue
                    row.original = None
                    if convert_back == "lossless" and row.lossless() != 1:
                        # lossy: --convert-back lossless never decodes it (S3)
                        log.log(f"ORIGINAL NOT USED {row.rel}: {detail}; it is lossy: restored "
                                f"as its JXL (--convert-back lossless)")
                        as_jxl_rows = restore_members_as_jxl(row)
                        if as_jxl_rows:
                            results[index] = as_jxl_rows[0]
                            extra_results.extend(as_jxl_rows[1:])
                        continue
                    # no proof after all: the JXL is decoded, as without that copy
                    log.log(f"ORIGINAL NOT USED {row.rel}: {detail}; its JXL is decoded")
                    if row.fallback != "RESTORE":
                        why = ("backup file missing" if row.fallback == "MISSING_IN_BACKUP"
                               else f"kept as JXL; jxl-photo not found ({jxlphoto_where})")
                        results[index] = (row.fallback, row.rel, target, why)
                        bad = True
                        log.log(f"{row.fallback} {row.rel}")
                        continue
                if is_group:
                    work = os.path.join(os.path.dirname(os.path.abspath(target)),
                                        f"{os.path.basename(target)}.restoring_{stamp}_{index}"
                                        f"{sb.TEMP_SUFFIX}")
                    pool = jpeg_pool if row.kind == "jpeg" else tiff_pool
                    future = pool.submit(_run_group, row, backup, target, jxlphoto, work, servers)
                    pending[future] = (index, row, target)
                    future.add_done_callback(finished_queue.put)
                    continue
                status, detail, ok = copy_plain(row, src, target)
                if ok:
                    done_bytes += row.size
                else:
                    bad = True
                results[index] = (status, row.rel, target, detail)
                log.log(f"{status} {row.rel}" + (f": {detail}" if detail else ""))
            for future in as_completed(list(pending)):
                collect(future)
                report()
        except KeyboardInterrupt:
            # what came back before Ctrl+C is kept; the files being made now are
            # ended, not waited for, and leave nothing at their targets (v0.14.0-rc5)
            sb._say_stopping(log)
            finished = [future for future in pending if future.done() and not future.cancelled()]
            sb._stop_converters()
            for pool in (jpeg_pool, tiff_pool):
                pool.shutdown(wait=False, cancel_futures=True)
            sb._wait_ending(list(pending), "restores")
            for future in finished:
                collect(future)
            # a group that ended between that look and the stop is at its
            # target too: it goes in the list (v0.14.0-rc6 review, N6)
            for future in list(pending):
                if (future.done() and not future.cancelled() and future.exception() is None
                        and future.result()[0].startswith("RESTORED")):
                    collect(future)
            print("Interrupted.", file=sys.stderr)
            log.log("Interrupted.")
            log.csv(".csv", ["status", "rel", "target", "detail"],
                    [item for item in results if item is not None] + extra_results)
            sb._tell(RESTORE_STOPPED_SAYING.format(
                count=sb._num(sum(1 for item in results + extra_results if item is not None
                                  and item[0].startswith("RESTORED"))),
                csv=log.prefix + ".csv"))
            return 130
        finally:
            for pool in (jpeg_pool, tiff_pool):
                pool.shutdown(wait=True, cancel_futures=True)
            if servers is not None:
                servers.close()
                sb._remove_own_folder(serve_root)
                if servers.unavailable:
                    log.log(f"JXL-PHOTO SERVE NOT USED: {servers.unavailable}; one jxl-photo per "
                            f"file")
            elif serve_root:
                sb._remove_own_folder(serve_root)
        result_rows = [item for item in results if item is not None] + extra_results
        log.csv(".csv", ["status", "rel", "target", "detail"], result_rows)
        result_counts = {}
        for status, _rel, _target, _detail in result_rows:
            result_counts[status] = result_counts.get(status, 0) + 1
        log.out("Result: " + "  ".join(
            f"{status} {sb._num(count)}" for status, count in sorted(result_counts.items())))
        for pattern in unmatched:
            log.out(f"--only {pattern}: matches nothing in the catalog (not restored)")
        return 1 if bad or unmatched else 0
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
