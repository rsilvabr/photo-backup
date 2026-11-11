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

    python restore_backup.py --backup H:\\BACKUP --to D:\\Restored
    python restore_backup.py --backup H:\\BACKUP --to-original
    python restore_backup.py --backup H:\\BACKUP --only "2025\\shoot" --to D:\\Restored
"""

import argparse
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selective_backup as sb

__version__ = "0.13.1"

# ---- Settings -------------------------------------------------------------------
SPACE_MARGIN_GB = 5.0     # free space kept on each restore target drive (default --space-margin-gb)

GOOD = ("RESTORED", "RESTORED_UNVERIFIED", "ALREADY_THERE")


def _drive_root(path):
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if drive:
        return drive + os.sep
    return os.path.abspath(os.sep)


def _under_any(rel, patterns):
    folded = rel.replace("/", "\\").casefold()
    for pattern in patterns:
        text = str(pattern).strip().strip('"').replace("/", "\\").casefold().rstrip("\\/")
        if not text:
            return True
        if folded == text or folded.startswith(text + "\\"):
            return True
    return False


def _target_for(row, args):
    if args.to_original:
        return row.source_path
    return os.path.join(os.path.abspath(args.to), row.rel)


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
    if args.space_margin_gb < 0:
        print("--space-margin-gb cannot be negative", file=sys.stderr)
        return 2
    backup = os.path.abspath(args.backup)
    try:
        catalog = sb._Catalog(backup, create=False, readonly=True)
    except (RuntimeError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        rows = catalog.all_rows()
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
    if args.to:
        to = os.path.abspath(args.to)
        if sb._is_under(to, backup) or sb._is_under(backup, to):
            print(f"--to cannot be inside the backup or contain it: {to} vs {backup}",
                  file=sys.stderr)
            return 2
    else:
        to = None
    patterns = args.only
    matched = set()
    items = []
    for row in rows:
        if patterns is not None:
            hits = [pattern for pattern in patterns if _under_any(row.rel, [pattern])]
            if not hits:
                continue
            matched.update(hits)
        src = os.path.join(backup, row.rel)
        target = _target_for(row, args)
        if args.to_original and sb._is_under(row.source_path, backup):
            status = "REFUSED"
        elif not sb._exists(src):
            status = "MISSING_IN_BACKUP"
        elif sb._exists(target):
            status = "EXISTS"
        else:
            status = "RESTORE"
        items.append((status, row, src, target))
    restores = [item for item in items if item[0] == "RESTORE"]
    by_drive = {}
    for _status, row, _src, target in restores:
        root = _drive_root(target)
        by_drive[root] = by_drive.get(root, 0) + row.size
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
                   if item[0] in ("MISSING_IN_BACKUP", "REFUSED")]
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
            for status, row, src, target in items:
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
                                        "source_path is inside the backup"))
                    bad = True
                    log.log(f"REFUSED {row.rel}: source_path is inside the backup")
                    continue
                if sb._exists(target):
                    try:
                        outcome, detail = _resolve_exists(row, src, target)
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
