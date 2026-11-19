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
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selective_backup as sb

__version__ = "0.14.0-rc3"

# ---- Settings -------------------------------------------------------------------
SPACE_MARGIN_GB = 5.0     # free space kept on each restore target drive (default --space-margin-gb)

GOOD = ("RESTORED", "RESTORED_UNVERIFIED", "RESTORED_FROM_JXL", "RESTORED_AS_JXL",
        "ALREADY_THERE")
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

    def whole(self):
        pages = sorted(conv.page for _row, conv in self.members)
        return (len(self.members) == self.conv.pages
                and pages == list(range(len(self.members))))

    def jxl_size(self):
        return sum(row.size for row, _conv in self.members)

    def lossless(self):
        """1 when the original comes back exactly (a JPEG's bytes, a lossless
        TIFF's pixels), 0 when lossy, None when the catalog does not know."""
        return 1 if self.kind == "jpeg" else self.conv.lossless


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
    keeps in its tools folder (KEEP_TOOLS_IN_BACKUP); (None, why) when none."""
    if given and sb._jxlphoto_problem(given) is None:
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
    why = sb._jxlphoto_problem(given) or "jxl-photo not found"
    for _numbers, _is_py, candidate in sorted(found, reverse=True):
        problem = sb._jxlphoto_problem(candidate)   # the contract holds for it too (JP-1)
        if problem is None:
            return candidate, "kept in the backup"
        why = f"{why}; the one kept in the backup: {problem}"
    return None, why


def _resolve_group_exists(group, target):
    """A file kept as JXL whose target already has a file: never overwrite."""
    stat = os.stat(sb._io(target), follow_symlinks=False)
    if stat.st_size == group.size and sb._sha256_file(target) == group.sha256:
        return ("ALREADY_THERE", f"the original {group.kind.upper()}")
    if group.kind == "jpeg":
        return ("CONFLICT", "target exists with different content")
    return ("CONFLICT", "target exists and is not the original TIFF (a TIFF made from its JXL "
                        "earlier is never the same bytes either); left as it was")


def _restore_group(group, backup, target, jxlphoto, work):
    """Turn a TIFF kept as JXL back into a TIFF at the target: the JXL files
    are copied (hash checked against the catalog) under jxl-photo's own names
    into the work folder -- a folder of this restore next to the target,
    never in the backup --, jxl-photo decodes them there, and the one TIFF it
    makes takes the target's name, never replacing a file. (status, detail)."""
    folder = os.path.dirname(os.path.abspath(target))
    os.makedirs(sb._io(folder), exist_ok=True)
    if os.path.lexists(sb._io(work)):
        return "ERROR", f"something is in the way of the restore's own folder: {work}"
    work_in, work_out = os.path.join(work, "jxl"), os.path.join(work, "tiff")
    os.makedirs(sb._io(work_in))
    os.makedirs(sb._io(work_out))
    ext = os.path.splitext(group.rel)[1]
    try:
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
            run = sb._run_jxlphoto(jxlphoto, arguments, max(sb.JXL_TIMEOUT_MIN, 1) * 60)
        except subprocess.TimeoutExpired:
            return "ERROR", f"jxl-photo took more than {sb.JXL_TIMEOUT_MIN} min"
        said = sb._tail((run.stdout or "") + "\n" + (run.stderr or ""))
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
    if convert_back == "lossless":      # a lossy (or unknown) TIFF stays JXL
        kept_as_jxl = {key for key, group in groups.items() if group.lossless() != 1}
        entries = [item for entry in entries for item in
                   ([member for member, _conv in entry.members]
                    if isinstance(entry, _JxlGroup) and entry.conv.source_key in kept_as_jxl
                    else [entry])]
        groups = {key: group for key, group in groups.items() if key not in kept_as_jxl}
    jxlphoto = jxlphoto_where = None
    if groups:
        jxlphoto, jxlphoto_where = _find_jxlphoto(args.jxlphoto, backup)
    patterns = args.only
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
        if inside_backup(target):
            status = "REFUSED"
        elif is_group and (not row.whole() or not all(
                sb._exists(os.path.join(backup, member.rel)) for member, _conv in row.members)):
            status = "MISSING_IN_BACKUP"
        elif not is_group and not sb._exists(src):
            status = "MISSING_IN_BACKUP"
        elif sb._exists(target):
            status = "EXISTS"
        elif is_group and jxlphoto is None:
            status = "NEEDS_JXLPHOTO"
        else:
            status = "RESTORE"
        items.append((status, row, src, target))
    restores = [item for item in items if item[0] == "RESTORE"]
    by_drive = {}
    for _status, row, _src, target in restores:
        root = _drive_root(target)
        # a TIFF made from its JXL needs its own size plus the JXL copied beside it
        need = row.size + (row.jxl_size() if isinstance(row, _JxlGroup) else 0)
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
    from_jxl = [item[1] for item in items if isinstance(item[1], _JxlGroup)]
    if from_jxl:
        kinds = collections.Counter(group.kind.upper() for group in from_jxl)
        lines.append(f"Kept as JXL, back to their format ({convert_back}): "
                     f"{', '.join(f'{sb._num(count)} {kind}' for kind, count in sorted(kinds.items()))}"
                     + (f" -- by {sb._jxlphoto_version(jxlphoto)}, {jxlphoto_where}: {jxlphoto}"
                        if jxlphoto else " -- jxl-photo is needed"))
    as_jxl = [row for _status, row, _src, _target in items
              if not isinstance(row, _JxlGroup) and row.key in converted]
    if as_jxl:
        sources = {converted[row.key].source_key for row in as_jxl}
        lines.append(f"Kept as JXL, restored as JXL ({convert_back}): {sb._num(len(sources))} TIFF "
                     f"or JPEG ({sb._num(len(as_jxl))} JXL files; --convert-back all gives their "
                     f"TIFF or JPEG)")
    if counts.get("NEEDS_JXLPHOTO"):
        lines.append(f"NEEDS_JXLPHOTO: {sb._num(counts['NEEDS_JXLPHOTO'])} TIFF or JPEG kept as "
                     f"JXL cannot be made without jxl-photo ({jxlphoto_where}). Set JXLPHOTO or "
                     f"--jxlphoto, or restore them as JXL with --convert-back none.")
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
                answer = input(f"Restore {sb._num(len(restores))} files? [y/N] ")
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
        result_rows = []
        bad = False
        total_bytes = sum(row.size for _status, row, _src, _target in restores)
        done_bytes = 0
        started = time.monotonic()
        last_report = started
        tolerance = int(sb.MTIME_TOLERANCE_S * 1_000_000_000)
        try:
            for index, (status, row, src, target) in enumerate(items):
                now = time.monotonic()
                if now - last_report >= sb.PROGRESS_EVERY_S:
                    speed = done_bytes / max(now - started, 1e-6)
                    eta = (total_bytes - done_bytes) / speed if speed > 0 else 0
                    print(f"[{len(result_rows)}/{len(items)}] {sb._gb(done_bytes)}/"
                          f"{sb._gb(total_bytes)}  {speed / 1e6:.0f} MB/s  ETA {sb._hms(eta)}")
                    last_report = now
                if status == "MISSING_IN_BACKUP":
                    result_rows.append((status, row.rel, target, "backup file missing"))
                    bad = True
                    log.log(f"MISSING_IN_BACKUP {row.rel}")
                    continue
                if status == "REFUSED":
                    result_rows.append((status, row.rel, target,
                                        "the target is inside the backup"))
                    bad = True
                    log.log(f"REFUSED {row.rel}: the target is inside the backup")
                    continue
                is_group = isinstance(row, _JxlGroup)
                if status == "NEEDS_JXLPHOTO":
                    result_rows.append((status, row.rel, target,
                                        f"kept as JXL; jxl-photo not found ({jxlphoto_where})"))
                    bad = True
                    log.log(f"NEEDS_JXLPHOTO {row.rel}")
                    continue
                if sb._exists(target):
                    try:
                        outcome, detail = (_resolve_group_exists(row, target) if is_group
                                           else _resolve_exists(row, src, target))
                    except OSError as exc:
                        result_rows.append(("ERROR", row.rel, target, str(exc)))
                        bad = True
                        log.log(f"ERROR {row.rel}: {exc}")
                        continue
                    result_rows.append((outcome, row.rel, target, detail))
                    if outcome == "CONFLICT":
                        bad = True
                    log.log(f"{outcome} {row.rel}")
                    continue
                if is_group:
                    work = os.path.join(os.path.dirname(os.path.abspath(target)),
                                        f"{os.path.basename(target)}.restoring_{stamp}_{index}"
                                        f"{sb.TEMP_SUFFIX}")
                    try:
                        status, detail = _restore_group(row, backup, target, jxlphoto, work)
                    except Exception as exc:
                        status, detail = "ERROR", str(exc)
                    if status in ("RESTORED_FROM_JXL", "RESTORED"):
                        done_bytes += row.size
                    else:
                        bad = True
                    result_rows.append((status, row.rel, target, detail))
                    log.log(f"{status} {row.rel}" + (f": {detail}" if detail else ""))
                    continue
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
                    done_bytes += row.size
                    status = "RESTORED" if row.sha256 else "RESTORED_UNVERIFIED"
                    detail = ""
                    if row.key in converted:        # a JXL kept as it is (--convert-back)
                        status = "RESTORED_AS_JXL"
                        detail = (f"the JXL of {converted[row.key].source_rel}, as it is in the "
                                  f"backup ({convert_back})")
                except FileExistsError:
                    status = "CONFLICT"
                    detail = "the target appeared during the restore; left as it was"
                    bad = True
                except RuntimeError as exc:
                    status = ("CORRUPT_IN_BACKUP"
                              if "content differs from the catalog" in str(exc) else "ERROR")
                    detail = str(exc)
                    bad = True
                except Exception as exc:
                    status = "ERROR"
                    detail = str(exc)
                    bad = True
                result_rows.append((status, row.rel, target, detail))
                log.log(f"{status} {row.rel}" + (f": {detail}" if detail else ""))
        except KeyboardInterrupt:
            print("Interrupted.", file=sys.stderr)
            log.log("Interrupted.")
            log.csv(".csv", ["status", "rel", "target", "detail"], result_rows)
            return 130
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
