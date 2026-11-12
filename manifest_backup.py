#!/usr/bin/env python3
"""manifest_backup - back up each folder with its own mode, in a manifest.

A manifest lists the folders and the mode of each one. The modes table
(backup_modes.csv beside this script, or --modes) says, per mode, what to copy:
the exports and the originals of the exported photos, plus the unselected
originals, plus whole subfolders (AddFolders / AddWhole), the whole folder, or
nothing extra. Each manifest line also chooses how the originals of the photos
that were not exported are kept (Originals: raw, raw+jpeg, all).

The selection itself is made by selective_backup.py's _Planner, where the
safety rules live: this script reads the two tables, builds one planner per
manifest line and hands the result to selective_backup._run_backup, so the
copy, the catalog, the space check, the orphans and the confirmation are the
ones of a normal selective backup. Nothing on the destination is ever deleted;
a file that a lower mode no longer selects only appears as a `not_selected`
orphan. The source is READ-ONLY.

--analyze copies nothing: it prints how many bytes each mode would take in each
folder of the manifest, and writes <stamp>_analysis.csv next to the log.

    python manifest_backup.py --manifest backup_manifest.csv --dest H:\\BACKUP --dry-run
    python manifest_backup.py --manifest backup_manifest.csv --dest H:\\BACKUP
    python manifest_backup.py --manifest backup_manifest.csv --analyze
"""

import argparse
import copy
import csv
import dataclasses
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selective_backup as sb

__version__ = "0.13.2"

# ---- Settings -------------------------------------------------------------------
MODES_FILE = "backup_modes.csv"     # next to this script; --modes reads another
DEFAULT_ORIGINALS = "raw"           # a manifest line with an empty Originals uses this
ORIGINALS_VALUES = {                # manifest Originals -> --unselected / FolderRules.originals
    "raw": "raw_or_jpeg",
    "raw+jpeg": "raw_jpeg",
    "all": "all",
}
ANALYSIS_ORIGINALS = ("raw", "raw+jpeg", "all")   # the --analyze columns
ONLY_GROUPS = {**sb.FORMAT_GROUPS,    # names usable in the Only column -> extensions
               "raw": sb.RAW_EXTENSIONS, "jpeg": sb.JPEG_EXTENSIONS}
MODE_COLUMNS = ("Mode", "Unselected", "AddFolders", "AddWhole", "Exclude", "Whole",
                "Description")
MODE_OPTIONAL_COLUMNS = ("Unexported", "Only", "Skip",   # a table written before them keeps working
                         "LeaveOut")                     # (without LeaveOut: selective_backup.SESSION_LEAVE_OUT)
MANIFEST_COLUMNS = ("Folder", "Mode", "Originals")


class ManifestError(Exception):
    """A bad manifest or modes table: abort before any photo is read."""


@dataclasses.dataclass(frozen=True)
class Mode:
    name: str
    unselected: bool
    add_folders: tuple
    add_whole: tuple
    exclude: tuple
    whole: bool
    description: str = ""
    unexported: bool = False    # folders without an export are copied whole
    skip: bool = False          # this folder stays out of the backup
    only: tuple = ()            # lower-case extensions; every other file is left out
    leave_out: tuple = dataclasses.field(              # session subfolders left out (LeaveOut)
        default_factory=lambda: tuple(sb._split_names(sb.SESSION_LEAVE_OUT)))


@dataclasses.dataclass(frozen=True)
class ManifestRow:
    folder: str
    mode: str
    originals: str


def _drive_key(path):
    return os.path.splitdrive(os.path.abspath(path))[0].casefold()


def _read_csv(path, columns, optional=()):
    """[(line, DictReader row), ...] with the comment lines (first field starting
    with '#') removed. A missing file or column is a ManifestError, and so is a
    column that is neither required nor `optional`: a header typed wrong
    ('Unexported ' with a space) would otherwise be ignored and its column read
    as empty."""
    try:
        with open(sb._io(path), newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ManifestError(f"{path}: empty file")
            missing = [column for column in columns if column not in reader.fieldnames]
            if missing:
                raise ManifestError(
                    f"{path}: missing column(s): {', '.join(missing)}")
            known = set(columns) | set(optional)
            unknown = [column for column in reader.fieldnames if column not in known]
            if any(not str(column).strip() for column in unknown):
                raise ManifestError(
                    f"{path}: the header has an empty column name: there is probably an "
                    f"extra comma at the end of the lines (an empty cell left in Excel); "
                    f"remove it (known columns: {', '.join(columns + tuple(optional))})")
            if unknown:
                raise ManifestError(
                    f"{path}: unknown column(s): {', '.join(repr(c) for c in unknown)} "
                    f"(known: {', '.join(columns + tuple(optional))})")
            rows = []
            first_name = reader.fieldnames[0]
            for line, row in enumerate(reader, 2):
                first = row.get(first_name) or ""
                if str(first).startswith("#"):
                    continue
                extra = [value for value in (row.get(None) or []) if str(value).strip()]
                if extra:
                    raise ManifestError(f"{path} line {line}: more values than columns: "
                                        + ", ".join(repr(value) for value in extra))
                rows.append((line, row))
            return rows
    except ManifestError:
        raise
    except FileNotFoundError:
        raise ManifestError(f"file not found: {path}")
    except UnicodeDecodeError:
        raise ManifestError(f"{path}: not UTF-8 (in Excel, save as 'CSV UTF-8')")
    except csv.Error as exc:
        raise ManifestError(f"{path}: not a valid CSV: {exc}")
    except OSError as exc:
        raise ManifestError(f"cannot read {path}: {exc}")


def _yes_no(row, column, path, line):
    value = str(row.get(column) or "").strip().casefold()
    if value == "yes":
        return True
    if value == "no":
        return False
    raise ManifestError(f"{path} line {line}: {column} must be yes or no, not "
                        f"{row.get(column)!r}")


def _only_extensions(value, path, line):
    """The Only column: group names of ONLY_GROUPS and/or .extensions."""
    extensions = []
    for token in sb._split_names(value):
        folded = token.casefold()
        if folded.startswith(".") and len(folded) > 1:
            extensions.append(folded)
        elif folded in ONLY_GROUPS:
            extensions.extend(ONLY_GROUPS[folded])
        else:
            raise ManifestError(f"{path} line {line}: Only: unknown {token!r} (use "
                                + ", ".join(ONLY_GROUPS) + " or .ext)")
    return tuple(extensions)


def read_modes(path):
    """The modes table: {mode name: Mode}, in file order."""
    modes = {}
    for line, row in _read_csv(path, MODE_COLUMNS, MODE_OPTIONAL_COLUMNS):
        name = str(row.get("Mode") or "").strip()
        if not name:
            raise ManifestError(f"{path} line {line}: empty Mode")
        if name in modes:
            raise ManifestError(f"{path} line {line}: mode {name!r} repeated")
        unselected = _yes_no(row, "Unselected", path, line)
        whole = _yes_no(row, "Whole", path, line)
        add_folders = tuple(sb._split_names(row.get("AddFolders") or ""))
        add_whole = tuple(sb._split_names(row.get("AddWhole") or ""))
        exclude = tuple(sb._split_names(row.get("Exclude") or ""))
        # an optional column: a table written before it existed keeps working
        unexported = (_yes_no(row, "Unexported", path, line)
                      if str(row.get("Unexported") or "").strip() else False)
        if whole and (add_folders or add_whole or unexported):
            raise ManifestError(f"{path} line {line}: Whole=yes cannot be combined "
                                f"with AddFolders, AddWhole or Unexported")
        only = _only_extensions(row.get("Only") or "", path, line)
        skip = (_yes_no(row, "Skip", path, line)
                if str(row.get("Skip") or "").strip() else False)
        if skip and (unselected or whole or add_folders or add_whole or unexported or only):
            raise ManifestError(f"{path} line {line}: Skip=yes cannot be combined with "
                                f"anything that copies files")
        if "LeaveOut" in row:       # the column is there; an empty cell leaves nothing out
            leave_out = tuple(sb._split_names(row.get("LeaveOut") or ""))
        else:                       # a table written before it: as v0.12.1 (every subfolder)
            leave_out = tuple(sb._split_names(sb.SESSION_LEAVE_OUT))
        if "LeaveOut" in row and leave_out and (whole or skip):
            raise ManifestError(f"{path} line {line}: LeaveOut cannot be combined with "
                                f"{'Whole=yes' if whole else 'Skip=yes'}: that mode walks "
                                f"no session (leave LeaveOut empty)")
        modes[name] = Mode(name, unselected, add_folders, add_whole, exclude, whole,
                           str(row.get("Description") or "").strip(), unexported, skip, only,
                           leave_out)
    return modes


def read_manifest(path, modes):
    """The manifest lines, each checked before any photo is read."""
    rows = []
    seen = set()
    drives = set()
    for line, row in _read_csv(path, MANIFEST_COLUMNS):
        folder = str(row.get("Folder") or "").strip()
        mode = str(row.get("Mode") or "").strip()
        originals = str(row.get("Originals") or "").strip().casefold() or DEFAULT_ORIGINALS
        if not folder:
            raise ManifestError(f"{path} line {line}: empty Folder")
        if mode not in modes:
            raise ManifestError(f"{path} line {line}: unknown mode {mode!r}")
        if originals not in ORIGINALS_VALUES:
            raise ManifestError(f"{path} line {line}: Originals must be one of "
                                + ", ".join(ORIGINALS_VALUES)
                                + f", not {originals!r}")
        folder = os.path.abspath(folder)
        if not os.path.isdir(sb._io(folder)):
            raise ManifestError(f"{path} line {line}: folder does not exist: {folder}")
        key = os.path.normcase(folder)
        if key in seen:
            raise ManifestError(f"{path} line {line}: folder repeated: {folder}")
        seen.add(key)
        drives.add(_drive_key(folder))
        rows.append(ManifestRow(folder, mode, originals))
    if not rows:
        raise ManifestError(f"{path}: no manifest lines")
    if len(drives) > 1:
        raise ManifestError(f"{path}: folders on more than one drive: "
                            + ", ".join(sorted(drives)))
    contains = sb.EXPORT_NAME_CONTAINS.casefold()
    not_exports = sb._fold_set(sb.EXPORT_NAME_EXCLUDE)
    for row in rows:
        name = os.path.basename(row.folder).casefold()
        if (contains in name and name not in not_exports
                and any(other is not row and sb._is_under(row.folder, other.folder)
                        for other in rows)):
            raise ManifestError(f"{path}: {row.folder} is an export folder below another "
                                f"line: that line would no longer see its session's "
                                f"export and would lose the originals. List the session "
                                f"folder instead.")
    return rows


def _row_unselected(mode, originals):
    return ORIGINALS_VALUES[originals] if mode.unselected else "skip"


def _row_rules(mode, originals):
    return sb.FolderRules(add_filtered=mode.add_folders, add_whole=mode.add_whole,
                          exclude=mode.exclude, whole=mode.whole,
                          originals=ORIGINALS_VALUES[originals],
                          skip=mode.skip, only=mode.only, leave_out=mode.leave_out)


def _skip_paths_for(folder, rows):
    """The other manifest folders below this one: this line plans its own."""
    key = os.path.normcase(folder)
    return [other.folder for other in rows
            if os.path.normcase(other.folder) != key
            and sb._is_under(other.folder, folder)]


def _row_args(args, folder, unselected, unexported=False):
    row_args = copy.copy(args)
    row_args.source = [folder]
    row_args.base = None
    row_args.unselected = unselected
    if unexported:
        row_args.unexported = "copy"
    return row_args


def _plan_row(row, rows, modes, args, list_cache, originals=None, mode=None, role_cache=None):
    mode = modes[row.mode] if mode is None else mode
    originals = row.originals if originals is None else originals
    planner = sb._Planner(_row_args(args, row.folder, _row_unselected(mode, originals),
                                    mode.unexported),
                          list_cache, _skip_paths_for(row.folder, rows),
                          _row_rules(mode, originals), role_cache)
    planner.build()
    return planner


def _plan_rows(rows, modes, args, list_cache):
    """One _Planner per manifest line, everything joined in one sb.Plan."""
    files = []
    unexported_folders = []
    unmatched = []
    errors = []
    warnings = []
    sources = []
    links = []
    inside = []
    skipped_folders = []
    cache_links = []
    stats = sb.Stats()
    for row in rows:
        planner = _plan_row(row, rows, modes, args, list_cache)
        files.extend(planner.files)
        unexported_folders.extend(planner.unexported)
        unmatched.extend(planner.unmatched)
        errors.extend(planner.errors)
        warnings.extend(planner.warnings)
        sources.extend(planner.sources)
        links.extend(planner.links)
        inside.extend(planner.inside_rows())
        skipped_folders.extend(planner.skipped_folders)
        cache_links.extend(planner.cache_links)
        for field in dataclasses.fields(sb.Stats):
            setattr(stats, field.name,
                    getattr(stats, field.name) + getattr(planner.stats, field.name))
    return sb.Plan(files=files, stats=stats, unexported_folders=unexported_folders,
                   unmatched=unmatched, errors=errors, warnings=warnings,
                   sources=sources, links=links, inside=inside,
                   skipped_folders=skipped_folders, cache_links=cache_links)


def _backend_args(parsed, rows, dest):
    """The args selective_backup.py's own code expects. Every choice is passed
    explicitly; --unselected skip is only the neutral value, since each row's
    planner receives the mode's own value."""
    argv = []
    for row in rows:
        argv += ["--source", row.folder]
    argv += ["--dest", dest,
             "--unselected", "skip",
             "--export-formats", parsed.export_formats,
             "--verify-copy", parsed.verify_copy,
             "--space-margin-gb", str(parsed.space_margin_gb)]
    if parsed.update_changed:
        argv.append("--update-changed")
    if not parsed.auto_update:
        argv.append("--no-auto-update")
    if parsed.expect_backup_id:
        argv += ["--expect-backup-id", parsed.expect_backup_id]
    if parsed.dry_run:
        argv.append("--dry-run")
    if parsed.yes:
        argv.append("--yes")
    if parsed.verify:
        argv += ["--verify", parsed.verify, "--max-gb", str(parsed.max_gb)]
    return sb._build_parser().parse_args(argv)


def _validate_rows(args, rows):
    """sb._validate_usage, minus the row-vs-row overlap: the manifest may nest a
    folder inside another (the most specific line wins) and every line is
    already checked to exist and to be unique. Only the top-level rows go to
    the check; they cover every nested one for the destination-overlap rule."""
    top = [row.folder for row in rows
           if not any(other is not row and sb._is_under(row.folder, other.folder)
                      for other in rows)]
    check = copy.copy(args)
    check.source = top
    return sb._validate_usage(check)


def _command_line(argv):
    if argv is None:
        return sys.argv
    return ["manifest_backup.py"] + [str(item) for item in argv]


def _new_log(stamp, kind):
    try:
        return sb._Logger(os.path.join(sb.LOG_DIR, f"{stamp}_{kind}"))
    except OSError as exc:
        print(f"cannot write the log directory {sb.LOG_DIR}: {exc}", file=sys.stderr)
        return None


def _run(parsed, rows, modes, stamp, argv):
    backend_args = _backend_args(parsed, rows, os.path.abspath(parsed.dest))
    log = _new_log(stamp, "manifest_verify" if parsed.verify else "manifest")
    if log is None:
        return 2
    try:
        command = _command_line(argv)
        log.log("manifest_backup " + __version__)
        log.log("command: " + " ".join(command))
        log.log("settings: " + ", ".join(
            f"{key}={value}" for key, value in sorted(vars(parsed).items())))
        log.log("manifest: " + os.path.abspath(parsed.manifest))
        log.log("modes: " + os.path.abspath(parsed.modes))
        for row in rows:
            log.log(f"row {row.folder} mode {row.mode} originals {row.originals}")
        list_cache = {}

        def plan_fn(plan_args):
            try:
                return _plan_rows(rows, modes, plan_args, list_cache)
            finally:
                # the listing of the whole source (0.5 GB for 1.2 M files) is
                # only needed while the lines are planned, not during the copy
                list_cache.clear()

        try:
            if parsed.verify:
                return sb._run_verify(backend_args, log, plan_fn=plan_fn)
            return sb._run_backup(backend_args, log, stamp, plan_fn=plan_fn)
        except KeyboardInterrupt:       # while planning (the copy handles its own)
            print("Interrupted.", file=sys.stderr)
            log.log("Interrupted.")
            return 130
    finally:
        log.close()


def _analysis_counts(row, rows, modes, args, list_cache, mode, errors, role_cache=None):
    """{originals: (files, bytes)} for one mode of one manifest line, through
    the same _Planner the copy uses. A mode that has no unselected originals
    and no AddFolders does not depend on Originals: it is planned once. What
    could not be read goes to `errors` (a folder is scanned once: the cache
    reports it to the first planner only). `role_cache`: a JXL read to tell a
    master from a derivative is read once, not once per mode and Originals."""
    if mode.unselected or mode.add_folders:
        counts = {}
        for originals in ANALYSIS_ORIGINALS:
            planner = _plan_row(row, rows, modes, args, list_cache,
                                originals=originals, mode=mode, role_cache=role_cache)
            errors.extend(planner.errors)
            counts[originals] = (len(planner.files),
                                 sum(item.size for item in planner.files))
        return counts
    planner = _plan_row(row, rows, modes, args, list_cache,
                        originals=ANALYSIS_ORIGINALS[0], mode=mode, role_cache=role_cache)
    errors.extend(planner.errors)
    count = (len(planner.files), sum(item.size for item in planner.files))
    return {originals: count for originals in ANALYSIS_ORIGINALS}


def _analyze(parsed, rows, modes, stamp, argv):
    log = _new_log(stamp, "analysis")
    if log is None:
        return 2
    try:
        command = _command_line(argv)
        log.log("manifest_backup " + __version__)
        log.log("command: " + " ".join(command))
        log.log("settings: " + ", ".join(
            f"{key}={value}" for key, value in sorted(vars(parsed).items())))
        log.log("manifest: " + os.path.abspath(parsed.manifest))
        log.log("modes: " + os.path.abspath(parsed.modes))
        try:
            return _analyze_rows(parsed, rows, modes, stamp, log)
        except KeyboardInterrupt:
            print("Interrupted.", file=sys.stderr)
            log.log("Interrupted.")
            return 130
    finally:
        log.close()


def _analyze_rows(parsed, rows, modes, stamp, log):
    """The --analyze loop (log already open)."""
    backend_args = _backend_args(parsed, rows, os.getcwd())
    list_cache = {}
    role_cache = {}
    errors = []
    results = []
    total_files = 0
    total_bytes = 0
    started = time.monotonic()
    for index, row in enumerate(rows, 1):
        log.out(f"[{index}/{len(rows)}] {row.folder}  "
                f"({sb._hms(time.monotonic() - started)})")
        line_rows = []
        for mode in modes.values():
            counts = _analysis_counts(row, rows, modes, backend_args, list_cache, mode, errors,
                                      role_cache)
            for originals in ANALYSIS_ORIGINALS:
                files, nbytes = counts[originals]
                chosen = mode.name == row.mode and originals == row.originals
                line_rows.append((row.folder, mode.name, originals, files, nbytes,
                                  "yes" if chosen else ""))
                if chosen:
                    total_files += files
                    total_bytes += nbytes
        log.out(f"    {'mode':<6} {'originals':<10} {'files':>10} {'bytes':>12}")
        for _folder, mode_name, originals, files, nbytes, chosen in line_rows:
            mark = "  <- chosen" if chosen else ""
            log.out(f"    {mode_name:<6} {originals:<10} {sb._num(files):>10} "
                    f"{sb._gb(nbytes):>12}{mark}")
        results.extend(line_rows)
    path = os.path.join(sb.LOG_DIR, f"{stamp}_analysis.csv")
    os.makedirs(sb._io(sb.LOG_DIR), exist_ok=True)
    with open(sb._io(path), "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["folder", "mode", "originals", "files", "bytes", "chosen"])
        writer.writerows(results)
    log.out(f"Total (the mode chosen in each line): {sb._num(total_files)} files / "
            f"{sb._gb(total_bytes)}")
    log.out(f"Analysis: {path}")
    for error in errors:
        log.log(error)
    if errors:
        log.out(f"Errors: {sb._num(len(errors))} folders/files could not be read: "
                f"the numbers above are partial -> {log.path}")
        return 1
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="manifest_backup.py",
        description="Back up each folder of a manifest with its own mode "
                    "(the source is read-only).")
    parser.add_argument("--manifest", required=True, metavar="PATH",
                        help="CSV with the folders: Folder,Mode,Originals")
    parser.add_argument("--dest", default=None, metavar="PATH",
                        help="destination root (required unless --analyze)")
    parser.add_argument("--modes",
                        default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             MODES_FILE),
                        metavar="PATH",
                        help="modes table (default: %(default)s)")
    parser.add_argument("--analyze", action="store_true",
                        help="print how many bytes each mode would take in each folder; "
                             "copy nothing and write no destination")
    parser.add_argument("--dry-run", action="store_true",
                        help="plan and log, copy nothing")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask for confirmation")
    parser.add_argument("--update-changed", action="store_true",
                        help="move a changed destination file to the versions folder first")
    parser.add_argument("--no-auto-update", dest="auto_update", action="store_false",
                        default=sb.AUTO_UPDATE,
                        help="do not update changed edit settings/sidecars "
                             "(AUTO_UPDATE_EXTENSIONS) without --update-changed")
    parser.add_argument("--verify-copy", choices=("none", "size", "hash"),
                        default=sb.VERIFY_COPY,
                        help="verification of each copy (default: %(default)s)")
    parser.add_argument("--space-margin-gb", type=float, default=sb.SPACE_MARGIN_GB,
                        metavar="N",
                        help="free space kept on the destination (default: %(default)s)")
    parser.add_argument("--export-formats", default=sb.EXPORT_FORMATS, metavar="SPEC",
                        help="all, a>b>c fallback, or a+b multiple (default: %(default)s)")
    parser.add_argument("--expect-backup-id", default=None, metavar="ID",
                        help="abort unless the destination holds this backup ID")
    parser.add_argument("--verify", choices=("quick", "full"), default=None,
                        help="verify the backup against what the manifest selects (no copy)")
    parser.add_argument("--max-gb", type=float, default=sb.VERIFY_MAX_GB, metavar="N",
                        help="with --verify full: verify at most N GB, oldest verification "
                             "first (default: %(default)s = everything)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv=None):
    try:                        # a cp1252 console cannot print the owner's ～ paths
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return int(code) if isinstance(code, int) else 2
    if not args.analyze and not args.dest:
        print("--dest is required unless --analyze is given", file=sys.stderr)
        return 2
    if args.analyze and args.verify:
        print("--verify cannot be combined with --analyze", file=sys.stderr)
        return 2
    try:
        modes = read_modes(args.modes)
        rows = read_manifest(args.manifest, modes)
    except ManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    stamp = time.strftime("%y%m%d_%H%M%S")
    if args.analyze:
        try:
            sb._parse_formats(args.export_formats)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return _analyze(args, rows, modes, stamp, argv)
    backend_args = _backend_args(args, rows, os.path.abspath(args.dest))
    errors = _validate_rows(backend_args, rows)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    return _run(args, rows, modes, stamp, argv)


if __name__ == "__main__":
    sys.exit(main())
