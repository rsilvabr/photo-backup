#!/usr/bin/env python3
"""mirror_backup - the backup follows the source: what is gone leaves it too.

What changed on the source is replaced in the backup, and what is gone from the
source is removed from it. This is the one backup script of this project that
removes files from its destination. Two modes:

  scoped (default)  only what the folders of this run cover: a backup that also
                    holds folders this run does not name keeps them untouched;
  total (--total)   the backup becomes this run: what came from folders that
                    are not in it leaves the backup too (a person types it).

Each manifest line chooses what "gone" means with its Mirror column: disk (the
default) removes what is gone from the folder; selection also removes what the
line no longer selects (a lower mode, Skip, Only, Exclude). Without a manifest,
--mirror does the same for the whole run.

It removes only what it wrote itself: a file with a row in the catalog that
still has that row's size and date. A file this tool did not write is never
removed, and a changed file it did not write goes to the versions folder, as in
a normal backup. Removed and replaced files go to a trash inside the backup
(<dest>\\_photo_backup\\trash\\<run>) and stay there MIRROR_KEEP_DAYS days.

Locks: a line with read errors removes nothing; a line that would remove or
replace more than MIRROR_MAX_DELETE_PERCENT of its files stops the run unless a person types
the number of files (or --allow-mass-delete); nothing behind a link or junction;
one real run at a time. Deleting anything for good before its date (to make
room, or --empty-trash) needs a person who types the number of files.

The copy, the catalog and the space check are selective_backup.py's own
(_run_backup with mirror=MirrorJob); this script only decides what leaves.
The source is READ-ONLY.

    python mirror_backup.py --manifest backup_manifest.csv --dest H:\\BACKUP --dry-run
    python mirror_backup.py --manifest backup_manifest.csv --dest H:\\BACKUP
    python mirror_backup.py --dest H:\\BACKUP --show-trash
"""

import argparse
import collections
import dataclasses
import json
import math
import os
import re
import sys
import textwrap
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selective_backup as sb
import manifest_backup as mb

__version__ = "0.14.0-rc5"

# ---- Settings -------------------------------------------------------------------
MIRROR_KEEP_DAYS = 30               # days a file the mirror removed or replaced stays in the trash
                                    # (<dest>\_photo_backup\trash\<run>); then the next mirror deletes it for
                                    # good. 0 = no trash: removed at once (and the next run empties the trash)
MIRROR_MAX_DELETE_PERCENT = 20      # a manifest line (without a manifest: the whole run) that would remove
MIRROR_MAX_DELETE_MIN = 5           # or replace more than this % of its files in the backup, and at least
                                    # MIRROR_MAX_DELETE_MIN files, changes nothing unless the number of files
                                    # is typed (or --allow-mass-delete); MIRROR_MAX_DELETE_PERCENT = 0 = no limit
MIRROR_TRASH_FOLDER = "trash"       # under selective_backup.CATALOG_FOLDER
MIRROR_LOCK_FILE = "mirror.lock"    # under selective_backup.CATALOG_FOLDER while a real mirror runs
MIRROR_SUMMARY_LINES = 10           # lines per table on the screen (the _actions.csv has all)

_STAMP = re.compile(r"\d{6}_\d{6}")     # a trash folder made by a run: its stamp, YYMMDD_HHMMSS
_RULE = "=" * 80                        # the frame of every block this script prints
_LABEL = 50                             # width of a table's label column, its indent included


# ---- Words and numbers on the screen ------------------------------------------------
def _n(count):
    """A count with thousands separators: 12,345."""
    return f"{count:,}"


def _files(count):
    """'1 file', '1,204 files'."""
    return f"{_n(count)} file{'' if count == 1 else 's'}"


def _size(nbytes):
    """KB, MB, GB or TB with one or two decimals: short and easy to compare."""
    if nbytes < 1e6:
        return f"{nbytes / 1e3:.1f} KB"
    if nbytes < 1e9:
        return f"{nbytes / 1e6:.1f} MB"
    if nbytes < 1e12:
        return f"{nbytes / 1e9:.1f} GB"
    return f"{nbytes / 1e12:.2f} TB"


def _short(text, width):
    """`text` cut in the middle with '...' when it is longer than `width`."""
    text = str(text)
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    head = (width - 3) // 2
    return text[:head] + "..." + text[len(text) - (width - 3 - head):]


def _table_head():
    return f"{'':<{_LABEL}}{'files':>12}{'size':>12}"


def _table_line(label, count=None, nbytes=None, indent=2):
    """label | files | size, aligned; a sub-line has a deeper indent."""
    width = _LABEL - indent
    files = "" if count is None else _n(count)
    size = "" if nbytes is None or (count == 0 and not nbytes) else _size(nbytes)
    return f"{' ' * indent}{_short(label, width):<{width}}{files:>12}{size:>12}".rstrip()


def _wrap(text, indent=1):
    """A sentence wrapped to the frame (never inside a word or a path)."""
    pad = " " * indent
    return textwrap.wrap(text, width=80, initial_indent=pad, subsequent_indent=pad,
                         break_long_words=False, break_on_hyphens=False) or [pad]


def _group(rel):
    """The folder of a backup file: where it is (a table cuts it in the
    middle, so its last, most telling names stay)."""
    return os.path.dirname(rel.replace("/", "\\")) or "(the backup's own root)"


def _day(moment):
    return moment.strftime("%Y-%m-%d")


def _until():
    """The day a file put in the trash now is deleted for good."""
    return _day(datetime.now() + timedelta(days=MIRROR_KEEP_DAYS))


def _list_place(path):
    """Two short lines that say where a CSV of this run is."""
    return [f"    {os.path.basename(path)}", f"    in {os.path.dirname(path)}"]


def _top_rule():
    """The top border of a box, with the program and its version in it."""
    return f"== mirror_backup {__version__} ".ljust(len(_RULE), "=")


def _stop_lines(dest, why, todo):
    """The box of a run that stopped before changing anything."""
    lines = ["", _top_rule(), f" MIRROR STOPPED - nothing changed in {dest}", _RULE]
    lines += _wrap("Why: " + why)
    lines += _wrap("What to do: " + todo)
    lines.append(_RULE)
    return lines


# ---- Settings and options ------------------------------------------------------------
def _settings_errors():
    errors = []
    for name in ("MIRROR_KEEP_DAYS", "MIRROR_MAX_DELETE_MIN"):
        value = globals()[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append(f"{name} must be a whole number, 0 or more (it is {value!r})")
    value = MIRROR_MAX_DELETE_PERCENT
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        errors.append(f"MIRROR_MAX_DELETE_PERCENT must be a number, 0 or more (it is {value!r})")
    value = MIRROR_SUMMARY_LINES
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        errors.append(f"MIRROR_SUMMARY_LINES must be a whole number, 1 or more (it is {value!r})")
    reserved = {"versions", sb.ORPHANS_FOLDER.casefold(), sb.BACKUP_ID_FILE.casefold(),
                "catalog.sqlite", "catalog.sqlite-journal"}
    names = []
    for name in ("MIRROR_TRASH_FOLDER", "MIRROR_LOCK_FILE"):
        value = globals()[name]
        text = value.strip() if isinstance(value, str) else ""
        if (not text or text != value or text in (".", "..")
                or any(char in text for char in "\\/:") or text.casefold() in reserved):
            errors.append(f"{name} must be a plain name (not versions, orphans or a file of the "
                          f"catalog), not {value!r}")
        names.append(text.casefold())
    if names[0] and names[0] == names[1]:
        errors.append("MIRROR_TRASH_FOLDER and MIRROR_LOCK_FILE must be two different names")
    return errors


def _usage_errors(args, extra):
    errors = []
    trash_actions = [option for option, on in (("--show-trash", args.show_trash),
                                               ("--empty-trash", args.empty_trash)) if on]
    if len(trash_actions) > 1:
        errors.append("--show-trash and --empty-trash cannot be combined")
    if trash_actions:
        if args.manifest or args.source or args.total:
            errors.append(f"{trash_actions[0]} takes only --dest (and --before, --dry-run, "
                          f"--unattended, --expect-backup-id)")
        if extra:
            errors.append("unknown option(s): " + " ".join(extra))
    else:
        if bool(args.manifest) == bool(args.source):
            errors.append("give either --manifest or --source (one or more)")
        if args.manifest:
            if args.mirror:
                errors.append("with a manifest, each line chooses with its Mirror column "
                              "(--mirror is for --source)")
            if args.base:
                errors.append("--base is for --source; a manifest uses the drive root")
            if extra:
                errors.append("unknown option(s): " + " ".join(extra))
        else:
            for option in ("--move-orphans", "--verify", "--max-gb"):
                if any(item == option or item.startswith(option + "=") for item in extra):
                    errors.append(f"{option} is not for a mirror (the mirror removes what is gone "
                                  f"itself; verify with selective_backup.py or manifest_backup.py)")
    if args.before is not None:
        if not args.empty_trash:
            errors.append("--before goes with --empty-trash")
        else:
            try:
                datetime.strptime(args.before, "%Y-%m-%d")
            except ValueError:
                errors.append(f"--before must be a day written YYYY-MM-DD, not {args.before!r}")
    if args.total and args.unattended:
        errors.append("--total needs a person at the console: it cannot run with --unattended")
    return errors


def _manifest_backend_args(args, rows):
    """selective_backup's args for a manifest, through manifest_backup's own
    builder: every option explicit, and --update-changed always (a mirror
    replaces what changed)."""
    parsed = argparse.Namespace(**vars(args))
    parsed.update_changed = True
    parsed.auto_update = sb.AUTO_UPDATE
    parsed.verify = None
    parsed.max_gb = sb.VERIFY_MAX_GB
    parsed.yes = args.yes or args.unattended
    return mb._backend_args(parsed, rows, os.path.abspath(args.dest))


def _plain_backend_args(args, extra):
    """selective_backup's args for --source: the options this script knows,
    plus every other one (the selection options) handed to its own parser."""
    argv = []
    for source in args.source:
        argv += ["--source", source]
    argv += ["--dest", os.path.abspath(args.dest),
             "--jxlphoto", args.jxlphoto,
             "--work-dir", args.work_dir,
             "--export-formats", args.export_formats,
             "--verify-copy", args.verify_copy,
             "--space-margin-gb", str(args.space_margin_gb),
             "--update-changed"]
    if args.base:
        argv += ["--base", args.base]
    if args.no_convert:
        argv.append("--no-convert")
    if args.expect_backup_id:
        argv += ["--expect-backup-id", args.expect_backup_id]
    if args.dry_run:
        argv.append("--dry-run")
    if args.yes or args.unattended:
        argv.append("--yes")
    return sb._build_parser().parse_args(argv + list(extra))


# ---- Files of the destination ------------------------------------------------------------
def _ours(row, size, mtime_ns):
    """A destination file is ours when it still has the size and the date its
    catalog row recorded: the bytes this tool wrote."""
    tolerance = int(sb.MTIME_TOLERANCE_S * 1_000_000_000)
    return size == row.size and abs(sb._db_ns(mtime_ns) - row.mtime_ns) <= tolerance


def _dest_stat(path, listed=None):
    """("ok", size, mtime_ns), ("missing", None, None) or ("unreadable", None,
    None) of a destination file. `listed` (the listing of its folder) answers
    only for a name it holds exactly; "missing" needs proof, as for a source:
    the nearest folder above that exists could be listed."""
    if listed is not None:
        hit = listed.get(os.path.basename(path))
        if hit is not None:
            return "ok", hit[0], hit[1]
    try:
        stat = os.stat(sb._io(path), follow_symlinks=False)
    except FileNotFoundError:
        if sb._source_state(path) == sb.SOURCE_MISSING:
            return "missing", None, None
        return "unreadable", None, None
    except OSError:
        return "unreadable", None, None
    return "ok", stat.st_size, stat.st_mtime_ns


def _fold(path):
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def _behind_link(path, link_keys):
    """Whether `path` is at or below one of the links (folded paths) the plan
    did not follow."""
    current = _fold(path)
    while True:
        if current in link_keys:
            return True
        parent = os.path.dirname(current).rstrip("\\/")
        if not parent or parent == current:
            return False
        current = parent


def _make_writable(path):
    os.chmod(sb._io(path), os.stat(sb._io(path)).st_mode | 0o200)


def _delete_file(path):
    """Delete one file for good (Windows refuses a read-only one: made writable first)."""
    try:
        os.remove(sb._io(path))
    except PermissionError:
        _make_writable(path)
        os.remove(sb._io(path))


# ---- The trash -------------------------------------------------------------------------------
@dataclasses.dataclass
class _TrashRun:
    name: str           # the stamp of the run that filled it: YYMMDD_HHMMSS
    path: str
    when: datetime
    files: int = 0
    nbytes: int = 0
    links: int = 0      # links/junctions inside: never followed, never removed
    folders: object = None      # collections.Counter: where (_group) -> files
    folder_bytes: object = None


def _trash_root(dest):
    return os.path.join(dest, sb.CATALOG_FOLDER, MIRROR_TRASH_FOLDER)


def _trash_problem(dest):
    """Why the backup's own folder or its trash cannot be used, or None. Each
    must be where it says, by its real path: a junction planted there would
    lead the mirror to move files into another place -- a source even -- and
    to delete there."""
    real_dest = sb._real(dest)
    for rel in (sb.CATALOG_FOLDER, os.path.join(sb.CATALOG_FOLDER, MIRROR_TRASH_FOLDER)):
        path = os.path.join(dest, rel)
        try:
            present = os.path.lexists(sb._io(path))
        except (OSError, ValueError):
            present = False
        if not present:
            continue
        real = sb._real(path)
        expected = os.path.join(real_dest, rel)
        if os.path.normcase(real).rstrip("\\/") != os.path.normcase(expected).rstrip("\\/"):
            return f"{path} leads somewhere else ({real}): the mirror does not use it"
    return None


def _scan_tree(root):
    """([(path relative to root, size)], [links], problems) of a folder:
    links and junctions are never followed."""
    files, links, problems = [], [], 0
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(sb._io(folder)) as listing:
                entries = list(listing)
        except OSError:
            problems += 1
            continue
        for entry in entries:
            path = os.path.join(folder, entry.name)
            try:
                if entry.is_symlink() or entry.is_junction():
                    links.append(path)
                elif entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                else:
                    files.append((os.path.relpath(path, root),
                                  entry.stat(follow_symlinks=False).st_size))
            except OSError:
                problems += 1
    return files, links, problems


def _trash_runs(dest):
    """(runs, foreign, problem): the runs in the trash, oldest first; the
    paths in it that no run made (never touched); why it cannot be read."""
    root = _trash_root(dest)
    runs, foreign = [], []
    try:
        with os.scandir(sb._io(root)) as listing:
            entries = list(listing)
    except FileNotFoundError:
        return runs, foreign, None
    except OSError as exc:
        return runs, foreign, f"the trash {root} cannot be read ({exc.strerror or exc})"
    for entry in entries:
        path = os.path.join(root, entry.name)
        when = None
        try:
            plain = (not (entry.is_symlink() or entry.is_junction())
                     and entry.is_dir(follow_symlinks=False))
        except OSError:
            plain = False
        if plain and _STAMP.fullmatch(entry.name):
            try:
                when = datetime.strptime(entry.name, "%y%m%d_%H%M%S")
            except ValueError:
                when = None
        if when is None:
            foreign.append(path)
            continue
        files, links, _problems = _scan_tree(path)
        folders = collections.Counter()
        folder_bytes = collections.Counter()
        for rel, size in files:
            folders[_group(rel)] += 1
            folder_bytes[_group(rel)] += size
        runs.append(_TrashRun(entry.name, path, when, len(files), sum(size for _rel, size in files),
                              len(links), folders, folder_bytes))
    runs.sort(key=lambda run: (run.when, run.name))
    return runs, foreign, None


def _purge_trash_run(run, dest, log):
    """Delete one run of the trash for good: (files, bytes, problems). Only
    what is inside that run's folder, by its real path; a link or junction
    inside is left where it is and never followed."""
    expected = os.path.join(sb._real(dest), sb.CATALOG_FOLDER, MIRROR_TRASH_FOLDER, run.name)
    real = sb._real(run.path)
    if os.path.normcase(real).rstrip("\\/") != os.path.normcase(expected).rstrip("\\/"):
        log.log(f"ERROR TRASH {run.path}: it leads somewhere else ({real}); not touched")
        return 0, 0, 1
    counts = [0, 0, 0]

    def purge(folder):
        try:
            with os.scandir(sb._io(folder)) as listing:
                entries = list(listing)
        except OSError as exc:
            log.log(f"ERROR TRASH {folder}: {exc}")
            counts[2] += 1
            return
        for entry in entries:
            path = os.path.join(folder, entry.name)
            try:
                if entry.is_symlink() or entry.is_junction():
                    # left where it is, never followed; not a problem of each
                    # later run either (finding 8 of the rc1 review)
                    log.log(f"MIRROR TRASH LINK left {path} (never followed)")
                elif entry.is_dir(follow_symlinks=False):
                    purge(path)
                    try:
                        os.rmdir(sb._io(path))
                    except OSError:
                        pass
                else:
                    size = entry.stat(follow_symlinks=False).st_size
                    _delete_file(path)
                    counts[0] += 1
                    counts[1] += size
            except OSError as exc:
                log.log(f"ERROR TRASH {path}: {exc}")
                counts[2] += 1

    purge(run.path)
    try:
        os.rmdir(sb._io(run.path))
    except OSError:
        pass
    log.log(f"MIRROR TRASH PURGED {run.name}: {counts[0]} files / {_size(counts[1])}"
            + (f", {counts[2]} problems" if counts[2] else ""))
    return tuple(counts)


def _expiry_text(run):
    """When a run of the trash is deleted for good, in words."""
    until = run.when + timedelta(days=MIRROR_KEEP_DAYS)
    days = (until.date() - datetime.now().date()).days
    if MIRROR_KEEP_DAYS == 0 or days <= 0:
        return "at the next mirror"
    return f"{_day(until)} (in {days} day{'s' if days != 1 else ''})"


def _trash_table(runs):
    """The runs of the trash, newest first, with where their files came from."""
    lines = [f"  {'taken out on':<21}{'deleted for good on':<27}{'files':>12}{'size':>12}"]
    newest = list(reversed(runs))
    for run in newest[:MIRROR_SUMMARY_LINES]:
        lines.append(f"  {run.when:%Y-%m-%d %H:%M:%S}  {_expiry_text(run):<27}"
                     f"{_n(run.files):>12}{_size(run.nbytes):>12}")
        for folder, count in run.folders.most_common(3):
            lines.append(f"     {_short(folder, 45):<45}{_n(count):>12}"
                         f"{_size(run.folder_bytes[folder]):>12}")
    older = newest[MIRROR_SUMMARY_LINES:]
    if older:
        lines.append(f"  ... and {_n(len(older))} older runs: {_n(sum(r.files for r in older))} "
                     f"files, {_size(sum(r.nbytes for r in older))}")
    lines.append(f"  {'total':<48}{_n(sum(r.files for r in runs)):>12}"
                 f"{_size(sum(r.nbytes for r in runs)):>12}")
    return lines


# ---- Typed confirmation ------------------------------------------------------------------------
def _typed_confirmation(lines, count, log):
    """True only when the person types `count` (dots, commas and spaces are
    ignored): a number that changes every time cannot be typed from habit, and
    reading it is reading how much is at stake. Anything else -- Enter, 'y',
    a closed console -- is a no."""
    for line in lines:
        print(line)
    try:
        answer = input(f"To go on, type {count} and press Enter (anything else cancels): ")
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""
    ok = count > 0 and re.sub(r"[\s.,]", "", answer) == str(count)
    log.log(f"CONFIRMATION {'given' if ok else 'refused'} ({count} files): "
            + " ".join(line.strip() for line in lines if line.strip()))
    return ok


# ---- One run at a time (the rules of mobile_backup.py 0.1.4, copied on purpose:
# ---- that script is independent and keeps its own version) ----------------------
def _lock_path(dest):
    return os.path.join(dest, sb.CATALOG_FOLDER, MIRROR_LOCK_FILE)


def _process_status(pid):
    """("alive", start time), ("dead", None) or ("unknown", None) for process
    `pid`. The start time (Windows: an opaque number) tells a reused PID from
    the run that wrote the lock. Never os.kill on Windows: it ends the process."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                # 87 = no such process; anything else (access denied) = it is there
                return ("dead", None) if ctypes.get_last_error() == 87 else ("unknown", None)
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return "unknown", None
                if code.value != 259:                   # STILL_ACTIVE
                    return "dead", None
                times = [wintypes.FILETIME() for _ in range(4)]
                if not kernel32.GetProcessTimes(handle, *[ctypes.byref(item) for item in times]):
                    return "alive", None
                return "alive", (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            finally:
                kernel32.CloseHandle(handle)
        except (OSError, AttributeError, ValueError, TypeError, OverflowError):
            return "unknown", None
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return "dead", None
    except (OSError, ValueError, TypeError, OverflowError):
        return "unknown", None
    return "alive", None


def _lock_text():
    _status, created = _process_status(os.getpid())
    return json.dumps({"pid": os.getpid(), "created": created,
                       "started": time.strftime("%Y-%m-%d %H:%M:%S")})


def _lock_holder(path):
    """(text, why) of the lock at `path`: `why` says which live run holds it,
    or is None when the run that wrote it is not running any more (killed, the
    PC turned off). Whatever cannot be told counts as a live run."""
    try:
        with open(sb._io(path), encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return "", f"the lock {path} cannot be read ({exc})"
    try:
        data = json.loads(text)
        pid = int(data["pid"])
    except (ValueError, KeyError, TypeError):
        try:                        # a lock being written right now, or cut by a crash
            age = time.time() - os.stat(sb._io(path)).st_mtime
        except OSError:
            age = 0
        if age > 60:
            return text, None
        return text, f"the lock {path} is being written by another run"
    status, created = _process_status(pid)
    if status == "dead" or (status == "alive" and created is not None
                            and data.get("created") is not None and created != data.get("created")):
        return text, None
    return text, (f"another mirror_backup run is working on this backup (pid {pid}, started "
                  f"{data.get('started', '?')}); if none is, delete {path}")


def take_lock(dest, log):
    """Hold the backup for this run: the lock file is created only if it is
    not there (O_CREAT | O_EXCL). Returns (text, None) when this run holds it,
    or (None, why) when a live run does. The lock of a run that is not running
    any more is taken over, and the log says so."""
    path = _lock_path(dest)
    os.makedirs(sb._io(os.path.dirname(path)), exist_ok=True)
    for _attempt in range(5):
        mine = _lock_text()
        try:
            fd = os.open(sb._io(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            text, why = _lock_holder(path)
            if why is not None:
                return None, why
            if text is not None:
                try:                # only the lock that was judged stale, not a new one
                    with open(sb._io(path), encoding="utf-8") as handle:
                        still = handle.read()
                    if still == text:
                        os.remove(sb._io(path))
                        log.log(f"LOCK taken over from a run that is not running any more: {text}")
                except FileNotFoundError:
                    pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(mine)
            handle.flush()
            os.fsync(handle.fileno())
        return mine, None
    return None, f"the lock {path} could not be taken"


def drop_lock(dest, mine, log):
    """Remove this run's lock (only if it is still ours)."""
    path = _lock_path(dest)
    try:
        with open(sb._io(path), encoding="utf-8") as handle:
            if handle.read() != mine:
                return
        os.remove(sb._io(path))
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.out(f"WARNING the lock {path} could not be removed ({exc}): the next run takes it over")


def _live_lock(dest):
    """Why a live run holds this backup's lock, or None (nothing is created)."""
    path = _lock_path(dest)
    try:
        if not os.path.exists(sb._io(path)):
            return None
    except (OSError, ValueError):
        return None
    return _lock_holder(path)[1]


# ---- The job -------------------------------------------------------------------------------------
@dataclasses.dataclass(eq=False)        # hashable by identity
class _Line:
    name: str           # the manifest folder, or "the sources of this run"
    policy: str         # disk | selection
    errors: int = 0     # read errors of its planner in this run


@dataclasses.dataclass(eq=False)
class _Item:
    action: str         # remove | drop_row | keep | replace | versions | stray | trash_purge | trash_foreign
    reason: str         # source_missing | not_selected | source_unreadable | other_source | outside_run |
                        # changed | not_catalogued | expired | not_a_run
    line: object        # _Line or None
    rel: str
    size: int
    source_path: str = ""
    note: str = ""
    result: str = "planned"
    row: object = None  # sb._Row of a catalogued file


_REMOVE_REASONS = (("gone from the source", "source_missing"),
                   ("no longer selected by its line", "not_selected"),
                   ("from a source that is not in this run", "other_source"),
                   ("in a folder that is not in this run", "outside_run"))


class MirrorJob:
    """What a mirror run removes, replaces and keeps. selective_backup's
    _run_backup calls it at fixed points: review (plan, nothing written),
    space_lines / free_space (not enough space), confirm (the lock and the
    typed confirmations), proceed_prompt, remove (before the copy), old_copy
    (for each changed file) and finish."""

    def __init__(self, args, rows=None, stamp=None):
        self.total = bool(args.total)
        self.allow_mass = bool(args.allow_mass_delete)
        self.unattended = bool(args.unattended)
        self.dest = os.path.abspath(args.dest)
        self.stamp = stamp or time.strftime("%y%m%d_%H%M%S")
        self.for_good = MIRROR_KEEP_DAYS == 0
        self.interrupted = False
        self.changed_something = False
        self.lock_text = None
        self.log = None
        self.stop_reason = None
        self.stop_todo = None
        self.items = []
        self.over_limit = []
        self.ours_changed = {}
        self.trash_problem = None
        self.trash_runs = []
        self.trash_foreign = []
        self.expired = []
        self.purge_runs = []
        self.space_options = []
        self.rows_by_key = {}
        self.planned_keys = set()
        self.prefixes = []
        self.run_errors = 0
        self.numbers = {"new": 0, "changed": 0, "remove": 0}
        self.actions_csv = None
        self.errors = 0
        self.purged = [0, 0, 0]             # files, bytes, problems
        self.replaced = collections.Counter()   # trash | for_good | versions | done | jxl_*
        self.convert_info = {}              # _copy_all's TIFF-to-JXL counts
        self.retire_items = {}              # TIFF copies a JXL replaces: {key: _Item}
        self.adoptable = {}                 # ... without a row: {key: (size, mtime_ns) of the source}
        if rows is not None:
            self.lines = [_Line(row.folder, row.mirror) for row in rows]
            keys = [(_fold(row.folder), line) for row, line in zip(rows, self.lines)]
        else:
            line = _Line("the sources of this run", args.mirror or mb.DEFAULT_MIRROR)
            self.lines = [line]
            keys = [(_fold(source), line) for source in args.source]
        self._by_folder = dict(keys)
        self._line_keys = sorted(keys, key=lambda item: len(item[0]), reverse=True)

    # -- the plan's lines ----------------------------------------------------------
    def record_row(self, row, planner):
        """manifest_backup._plan_rows' on_row: the read errors of each line."""
        line = self._by_folder.get(_fold(row.folder))
        if line is not None:
            line.errors += len(planner.errors)

    def record_plan(self, planned):
        """Without a manifest the run is one line: its read errors."""
        self.lines[0].errors += len(planned.errors)
        return planned

    def line_of(self, source_path):
        """The line a source path belongs to: the most specific one, as in the
        manifest; None when it is under none of this run's folders."""
        key = _fold(source_path)
        for folder, line in self._line_keys:
            if key == folder or key.startswith(folder + os.sep):
                return line
        return None

    def _items(self, *actions):
        return [item for item in self.items if item.action in actions]

    def _keep(self, reason, line, row, note, size=None):
        self.items.append(_Item("keep", reason, line, row.rel, row.size if size is None else size,
                                row.source_path, note, row=row))

    def _fate(self):
        if self.for_good:
            return "deleted for good (no trash)"
        return f"to the trash until {_until()}"

    def _stop(self, why, todo):
        self.stop_reason = why
        self.stop_todo = todo

    # -- review: before the summary is printed; nothing is written but the CSV ----------
    def review(self, planned, statuses, rows, prefixes, orphans, used_keys, listings, dest, log,
               possible=()):
        """Decide what each file of the backup becomes; (lines to print, bytes
        this run frees by itself)."""
        self.log = log
        self.dest = dest
        self.prefixes = list(prefixes)
        self.rows_by_key = {row.key: row for row in rows}
        self.planned_keys = {status.planned.rel.casefold() for status in statuses}
        self.planned_keys.update(key for status in statuses for key in status.extra_keys)
        self.run_errors = len(planned.errors)
        link_keys = {_fold(path) for path in list(planned.links) + list(planned.cache_links)}
        self.items = []
        candidates = []
        for reason, row in orphans:
            line = None if reason == "other_source" else self.line_of(row.source_path)
            if reason == "source_unreadable":
                self._keep(reason, line, row, "the source cannot be read")
            elif reason == "not_selected" and (line is None or line.policy != "selection"):
                self._keep(reason, line, row, "still on the source (Mirror=disk)")
            elif reason == "other_source" and not self.total:
                self._keep(reason, line, row,
                           "from a source that is not in this run (only --total removes it)")
            elif reason not in ("source_missing", "not_selected", "other_source"):
                self._keep(reason, line, row, f"unknown reason {reason}")
            elif link_keys and _behind_link(row.source_path, link_keys):
                self._keep(reason, line, row, "behind a link the backup does not follow")
            elif line is not None and line.errors:
                self._keep(reason, line, row, "its line had read errors in this run")
            elif line is None and self.run_errors:
                self._keep(reason, line, row, "this run had read errors")
            else:
                candidates.append((reason, line, row))
        if self.total:
            used = set(used_keys)
            for row in rows:
                if (sb._key_under_prefixes(row.key, self.prefixes) or row.key in self.planned_keys
                        or row.key in used):
                    continue
                if self.run_errors:
                    self._keep("outside_run", None, row, "this run had read errors")
                else:
                    candidates.append(("outside_run", None, row))
        # ours? one listing per folder, as the backup decides NEW and CHANGED
        by_folder = {}
        for reason, line, row in candidates:
            dst = os.path.join(dest, row.rel)
            by_folder.setdefault(os.path.dirname(dst), []).append((reason, line, row, dst))
        for folder, group in by_folder.items():
            if folder not in listings:
                listings[folder] = sb._list_dest_folder(folder)
            listed = listings[folder]
            for reason, line, row, dst in group:
                state, size, mtime_ns = _dest_stat(dst, listed)
                if state == "missing":
                    self.items.append(_Item("drop_row", reason, line, row.rel, row.size,
                                            row.source_path, "already missing from the backup",
                                            row=row))
                elif state == "unreadable":
                    self._keep(reason, line, row, "cannot be read in the backup")
                elif _ours(row, size, mtime_ns):
                    self.items.append(_Item("remove", reason, line, row.rel, row.size,
                                            row.source_path, row=row))
                else:
                    self._keep(reason, line, row, "changed in the backup by hand", size)
        # changed files: ours go to the trash, any other old copy to versions\
        self.ours_changed = {}
        for status in statuses:
            if status.status != sb.ST_CHANGED:
                continue
            key = status.planned.rel.casefold()
            row = self.rows_by_key.get(key)
            state, size, mtime_ns = _dest_stat(status.dst, listings.get(os.path.dirname(status.dst)))
            if row is not None and state == "ok" and _ours(row, size, mtime_ns):
                self.ours_changed[key] = row
                self.items.append(_Item("replace", "changed", self.line_of(status.planned.src),
                                        status.planned.rel, size, status.planned.src, row=row))
            else:
                self.items.append(_Item("versions", "not_catalogued" if row is None else "changed",
                                        None, status.planned.rel, size or 0, status.planned.src,
                                        "not written by the backup: the old copy goes to versions\\"))
        # the limit per line counts what is removed and what of ours is replaced
        self._check_limit(rows)
        # files the backup did not write, in the folders the mirror touches
        for folder, listed in listings.items():
            if not listed:
                continue
            rel_folder = os.path.relpath(folder, dest)
            for name, (size, _mtime) in listed.items():
                if name.endswith(sb.TEMP_SUFFIX):
                    continue
                rel = name if rel_folder == "." else os.path.join(rel_folder, name)
                key = rel.casefold()
                if key in self.rows_by_key or key in self.planned_keys:
                    continue
                self.items.append(_Item("stray", "not_catalogued", None, rel, size, "",
                                        "not written by the backup: never touched"))
        # the trash
        self.trash_problem = _trash_problem(dest)
        problem = None
        if self.trash_problem is None:
            self.trash_runs, self.trash_foreign, problem = _trash_runs(dest)
            self.trash_problem = problem
        if self.trash_problem is not None:
            self.trash_runs, self.trash_foreign = [], []
        now = datetime.now()
        # by its day, as the screen shows it: the first mirror on or after that
        # day deletes it, at any hour (finding 11 of the rc1 review)
        self.expired = [run for run in self.trash_runs
                        if (now.date() - run.when.date()).days >= MIRROR_KEEP_DAYS]
        for run in self.expired:
            self.items.append(_Item("trash_purge", "expired", None, os.path.relpath(run.path, dest),
                                    run.nbytes, "", f"{run.files} files, past their date"))
        for path in self.trash_foreign:
            self.items.append(_Item("trash_foreign", "not_a_run", None, os.path.relpath(path, dest),
                                    0, "", "not made by the mirror: never touched"))
        for status in statuses:
            if status.planned.convert and status.replaces:
                row = self.rows_by_key.get(status.replaces.casefold())
                found = sb._listed_or_stat(os.path.join(dest, status.replaces),
                                           listings.get(os.path.dirname(status.dst)))
                item = _Item("retire", "replaced_by_jxl", None, status.replaces,
                             found[0] if found else 0, status.planned.src,
                             "a TIFF or JPEG copy that its JXL replaces", row=row)
                self.items.append(item)
                self.retire_items[status.replaces.casefold()] = item
                if row is None:
                    # a copy with the source TIFF's size and date (another
                    # program's): what any run adopts as its own; ours, then
                    self.adoptable[status.replaces.casefold()] = (status.planned.size,
                                                                  status.planned.mtime_ns)
        freed = sum(run.nbytes for run in self.expired)
        if self.for_good and self.trash_problem is None:
            freed += sum(item.size for item in self._items("remove", "replace", "retire"))
        new = [status for status in statuses
               if status.status == sb.ST_NEW and not status.planned.convert]
        changed = [status for status in statuses
                   if status.status == sb.ST_CHANGED and not status.planned.convert]
        converts = [status for status in statuses if status.planned.convert
                    and status.status in (sb.ST_NEW, sb.ST_CHANGED)]
        self.numbers = {"new": len(new), "changed": len(changed),
                        "remove": len(self._items("remove")), "convert": len(converts)}
        self._write_actions()
        return self._summary(statuses, possible), freed

    def _check_limit(self, rows):
        """The lines that would remove or replace more than
        MIRROR_MAX_DELETE_PERCENT of their files in the backup (and at least
        MIRROR_MAX_DELETE_MIN). A replaced file of ours counts: its old copy
        leaves through the trash too (finding 2 of the rc1 review); the small
        edit files rewritten by every edit (AUTO_UPDATE_EXTENSIONS) do not."""
        self.over_limit = []
        removes = collections.Counter()
        nbytes = collections.Counter()
        for item in self._items("remove", "replace"):
            if item.line is None:
                continue
            if (item.action == "replace"
                    and os.path.splitext(item.rel)[1].casefold() in sb.AUTO_UPDATE_EXTENSIONS):
                continue
            removes[item.line] += 1
            nbytes[item.line] += item.size
        if MIRROR_MAX_DELETE_PERCENT <= 0 or not removes:
            return
        totals = collections.Counter()
        for row in rows:
            line = self.line_of(row.source_path)
            if line in removes:
                totals[line] += 1
        for line in self.lines:
            count = removes.get(line, 0)
            if (count and count >= max(1, MIRROR_MAX_DELETE_MIN)
                    and count * 100 > MIRROR_MAX_DELETE_PERCENT * totals[line]):
                self.over_limit.append((line, count, totals[line], nbytes[line]))

    def _write_actions(self):
        if self.log is None:
            return
        self.actions_csv = self.log.csv(
            "_actions.csv",
            ["action", "reason", "line", "rel", "size", "source_path", "note", "result"],
            [(item.action, item.reason, item.line.name if item.line is not None else "", item.rel,
              item.size, item.source_path, item.note, item.result) for item in self.items])

    def _limit_lines(self, rows_indent=5):
        lines = []
        for line, count, total, nbytes in self.over_limit[:MIRROR_SUMMARY_LINES]:
            percent = count * 100 // max(total, 1)
            share = f"{_n(count)} of {_n(total)} files ({percent}%)"
            lines.append(f"{' ' * rows_indent}{_short(line.name, 40):<40}{share:>27}{_size(nbytes):>12}")
        if len(self.over_limit) > MIRROR_SUMMARY_LINES:
            lines.append(f"{' ' * rows_indent}... and {len(self.over_limit) - MIRROR_SUMMARY_LINES} "
                         f"more lines (all in the list)")
        return lines

    def _summary(self, statuses, possible):
        """The mirror's block: the verdict first, then one table, then what
        needs attention. It is printed after the backup's summary, so it is
        what is on the screen when the question comes."""
        same = [s for s in statuses if s.status == sb.ST_UP_TO_DATE]
        new = [s for s in statuses if s.status == sb.ST_NEW and not s.planned.convert]
        changed = [s for s in statuses if s.status == sb.ST_CHANGED and not s.planned.convert]
        converts = [s for s in statuses if s.planned.convert
                    and s.status in (sb.ST_NEW, sb.ST_CHANGED)]
        jpegs = [s for s in converts if s.planned.convert == sb.JPEG_CONVERT]
        tiffs = [s for s in converts if s.planned.convert != sb.JPEG_CONVERT]
        removes = self._items("remove")
        removed_bytes = sum(item.size for item in removes)
        mode = "total" if self.total else "scoped"
        if removes:
            verdict = (f"{_files(len(removes))} ({_size(removed_bytes)}) will be removed from "
                       f"the backup: {self._fate()}.")
        else:
            verdict = "Nothing will be removed from the backup."
        lines = ["", _top_rule(), f" MIRROR ({mode}) - what changes in {self.dest}"]
        lines += _wrap(verdict)
        lines += [_RULE, _table_head()]
        lines.append(_table_line("already the same", len(same), sum(s.planned.size for s in same)))
        lines.append(_table_line("copy (new on the source)", len(new),
                                 sum(s.planned.size for s in new)))
        if possible:
            lines.append(_table_line("of them maybe only moved (renamed)", len(possible),
                                     sum(s.planned.size for s in possible), indent=4))
        if changed:
            lines.append(_table_line("replace (changed on the source)", len(changed),
                                     sum(s.planned.size for s in changed)))
            ours = self._items("replace")
            if ours:
                where = "deleted (no trash)" if self.for_good else "to the trash"
                lines.append(_table_line(f"old copy {where}", len(ours),
                                         sum(item.size for item in ours), indent=4))
            others = self._items("versions")
            if others:
                lines.append(_table_line("old copy to versions\\ (not written by the backup)",
                                         len(others), sum(item.size for item in others), indent=4))
        if tiffs:
            lines.append(_table_line("convert TIFF to JXL (the TIFF's size)", len(tiffs),
                                     sum(s.planned.size for s in tiffs)))
        if jpegs:
            lines.append(_table_line("convert JPEG to JXL (lossless)", len(jpegs),
                                     sum(s.planned.size for s in jpegs)))
        retires = self._items("retire")
        if retires:
            where = "deleted (no trash)" if self.for_good else "to the trash"
            lines.append(_table_line(f"old copy replaced by its JXL ({where})", len(retires),
                                     sum(item.size for item in retires), indent=4))
        where = "delete for good" if self.for_good else "to the trash"
        lines.append(_table_line(f"remove from the backup ({where})", len(removes), removed_bytes))
        for label, reason in _REMOVE_REASONS:
            part = [item for item in removes if item.reason == reason]
            if part:
                lines.append(_table_line(label, len(part), sum(item.size for item in part), indent=4))
        kept = self._items("keep")
        lines.append(_table_line("keep (not removed)", len(kept), sum(item.size for item in kept)))
        notes = collections.Counter(item.note for item in kept)
        for note, count in notes.most_common():
            lines.append(_table_line(note, count, sum(item.size for item in kept if item.note == note),
                                     indent=4))
        drops = self._items("drop_row")
        if drops:
            lines.append(_table_line("already missing from the backup (record dropped)", len(drops)))
        strays = self._items("stray")
        if strays:
            lines.append(_table_line("not written by the backup (never touched)", len(strays),
                                     sum(item.size for item in strays)))
        if self.expired:
            lines.append(_table_line(f"trash older than {MIRROR_KEEP_DAYS} days: deleted for good",
                                     sum(run.files for run in self.expired),
                                     sum(run.nbytes for run in self.expired)))
        kept_runs = [run for run in self.trash_runs if run not in self.expired]
        if kept_runs:
            lines.append(_table_line("trash from earlier runs (kept)", sum(r.files for r in kept_runs),
                                     sum(r.nbytes for r in kept_runs)))
        if removes:
            groups = collections.Counter(_group(item.rel) for item in removes)
            group_bytes = collections.Counter()
            for item in removes:
                group_bytes[_group(item.rel)] += item.size
            lines += ["", "  Where the files to remove are (the folders with the most):"]
            for folder, count in groups.most_common(MIRROR_SUMMARY_LINES):
                lines.append(_table_line(folder, count, group_bytes[folder], indent=4))
        if (removes or self._items("replace")) and not self.for_good:
            lines += ["", f"  Removed and replaced files can be copied back from this folder "
                          f"until {_until()}:", f"    {os.path.join(_trash_root(self.dest), self.stamp)}"]
        if strays:
            by_folder = collections.Counter(os.path.dirname(item.rel) or "." for item in strays)
            lines += ["", "  Files the backup did not write (never touched), by folder:"]
            for folder, count in by_folder.most_common(MIRROR_SUMMARY_LINES):
                lines.append(_table_line(folder, count, indent=4))
        lines += self._attention_lines()
        if self.actions_csv:
            lines += ["", "  The list of every file and what happens to it:"] + _list_place(self.actions_csv)
        lines.append(_RULE)
        return lines

    def _attention_lines(self):
        lines = []
        if self.trash_problem:
            lines += ["", "  !! THE TRASH CANNOT BE USED: a real run changes nothing until it can."]
            lines += _wrap(self.trash_problem, indent=5)
        errored = [line for line in self.lines if line.errors]
        if errored:
            lines += ["", "  !! READ ERRORS: these lines remove nothing in this run (see the log):"]
            lines += [f"     {_short(line.name, 74)}" for line in errored[:MIRROR_SUMMARY_LINES]]
        if self.over_limit:
            lines += ["", f"  !! CHECK: these lines would remove or replace more than "
                          f"{MIRROR_MAX_DELETE_PERCENT}% of their files"]
            lines += self._limit_lines()
            lines += _wrap("Common causes: the folder was deleted or moved on the source, another "
                           "disk got the source's drive letter, the line's mode was lowered, or a "
                           "program rewrote the files (ransomware encrypts them in place).",
                           indent=5)
            if not self.allow_mass:
                lines += _wrap("A real run asks you to type the number of files; unattended, it "
                               "stops and changes nothing.", indent=5)
        outside = [item for item in self._items("remove")
                   if item.reason in ("other_source", "outside_run")]
        if outside:
            lines += ["", "  !! TOTAL MIRROR: these folders are not in this run and leave the backup:"]
            lines += self._outside_lines(outside)
        return lines

    def _outside_lines(self, outside):
        groups = collections.Counter(item.rel.split("\\")[0] for item in outside)
        group_bytes = collections.Counter()
        for item in outside:
            group_bytes[item.rel.split("\\")[0]] += item.size
        return [_table_line(folder, count, group_bytes[folder], indent=5)
                for folder, count in groups.most_common(MIRROR_SUMMARY_LINES)]

    # -- not enough space ---------------------------------------------------------------
    def space_lines(self, needed, free, margin):
        """What could be deleted for good to make room, the least first."""
        missing = needed - (free - margin)
        drive = os.path.splitdrive(self.dest)[0] or self.dest
        runs = [run for run in self.trash_runs if run not in self.expired]
        this_run = self._items("remove", "replace", "retire") if not self.for_good else []
        this_bytes = sum(item.size for item in this_run)
        options = []
        oldest, total = [], 0
        for run in runs:
            oldest.append(run)
            total += run.nbytes
            if total >= missing:
                break
        if oldest and total >= missing:
            first, last = _day(oldest[0].when), _day(oldest[-1].when)
            days = first if first == last else f"{first} to {last}"
            label = ("the whole trash" if len(oldest) == len(runs)
                     else f"the oldest trash ({days})")
            options.append((label, oldest, False))
            if len(oldest) < len(runs):
                options.append(("the whole trash", runs, False))
        if this_run and this_bytes >= missing:
            options.append(("what this run removes, without the trash", [], True))
        whole = sum(run.nbytes for run in runs)
        if (runs and this_run and not options and whole + this_bytes >= missing):
            options.append(("the whole trash + this run, without the trash", runs, True))
        self.space_options = []
        for label, chosen, with_run in options:
            files = sum(run.files for run in chosen) + (len(this_run) if with_run else 0)
            nbytes = sum(run.nbytes for run in chosen) + (this_bytes if with_run else 0)
            self.space_options.append((label, chosen, with_run, files, nbytes))
        # the one that deletes the least first (finding 13 of the rc1 review)
        self.space_options.sort(key=lambda option: (option[4], option[3]))
        lines = ["", f"  !! NOT ENOUGH SPACE on {drive} - {_size(missing)} missing."]
        if not self.space_options:
            lines += _wrap(f"Even deleting the whole trash and this run's removals for good would "
                           f"free only {_size(whole + this_bytes)}. Make room on {drive}, or choose "
                           f"a smaller mode, then run the mirror again.", indent=5)
            return lines
        lines.append("     It fits if the mirror deletes these FOR GOOD (no trash, no way back):")
        for index, (label, _chosen, _with_run, files, nbytes) in enumerate(self.space_options, 1):
            mark = "  <- recommended" if index == 1 else ""
            lines.append(f"       {index}) {_short(label, 44):<44}{_files(files):>15}"
                         f"{_size(nbytes):>11}{mark}")
        lines.append("       0) cancel: nothing changes")
        if self.log is not None:
            rows = []
            for run in runs:
                files, _links, _problems = _scan_tree(run.path)
                rows += [(f"trash {run.name}", os.path.join(run.path, rel), size)
                         for rel, size in files]
            for item in this_run:
                group = "this run: removed" if item.action == "remove" else "this run: replaced"
                rows.append((group, os.path.join(self.dest, item.rel), item.size))
            path = self.log.csv("_free_space.csv", ["group", "path", "size"], rows)
            lines += [f"     What each option deletes: {os.path.basename(path)}",
                      f"       in {os.path.dirname(path)}"]
        return lines

    def free_space(self, needed, free, margin):
        """Ask which option to delete for good (Enter = cancel), then the
        typed number of files. Never without a person at the console."""
        drive = os.path.splitdrive(self.dest)[0] or self.dest
        missing = _size(needed - (free - margin))
        if not self.space_options:
            self._stop(f"there is not enough space on {drive} ({missing} missing), even deleting "
                       f"the trash for good", f"make room on {drive} or choose a smaller mode")
            return False
        if self.unattended:
            self._stop(f"there is not enough space on {drive} ({missing} missing), and nothing is "
                       f"deleted for good without a person at the console",
                       "run the mirror by hand (photo_backup.py -> [8] Mirror) and choose what "
                       "to delete for good, or empty the trash ([8] -> [4], or --empty-trash)")
            return False
        try:
            answer = input(f"Choose 1-{len(self.space_options)}, or Enter to cancel: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            answer = ""
        if (not (answer.isascii() and answer.isdigit())
                or not 1 <= int(answer) <= len(self.space_options)):
            self._stop("nothing was chosen to make room", "make room, then run the mirror again")
            return False
        index = int(answer)
        label, chosen, with_run, files, nbytes = self.space_options[index - 1]
        sentence = (f" Everything in option {index} ({label}: {_files(files)}, {_size(nbytes)}) "
                    f"will be deleted and can NOT be recovered later. Are you sure?")
        if not _typed_confirmation(["", "!! DELETE FOR GOOD"] + _wrap(sentence.strip()), files,
                                   self.log):
            self._stop("deleting for good to make room was not confirmed",
                       "make room another way, then run the mirror again")
            return False
        self.purge_runs = list(chosen)
        if with_run:
            self.for_good = True
        self.log.log(f"FREE SPACE option {index}: {label} ({files} files, {_size(nbytes)})")
        return True

    # -- the questions -----------------------------------------------------------------------
    def confirm(self):
        """Right before the backup's own question: the trash must be usable,
        this run takes the lock, then a person types what needs typing."""
        if self.trash_problem:
            self._stop(self.trash_problem,
                       "make that folder a plain folder again (or move it away), then run again")
            return False
        try:
            self.lock_text, why = take_lock(self.dest, self.log)
        except OSError as exc:
            self.lock_text, why = None, f"the lock could not be written ({exc.strerror or exc})"
        if why:
            self._stop(why, "wait until the other mirror ends; if none is running, delete the "
                            "lock file named above")
            return False
        fate = ("They go to the trash and stay there until " + _until() + "."
                if not self.for_good else "They are deleted and can NOT be recovered later.")
        if self.over_limit and not self.allow_mass:
            count = sum(item[1] for item in self.over_limit)
            if self.unattended:
                self._stop(f"{len(self.over_limit)} line(s) would remove or replace more than "
                           f"{MIRROR_MAX_DELETE_PERCENT}% of their files (see CHECK above)",
                           "run the mirror by hand (photo_backup.py -> [8] Mirror) and type the "
                           "number of files, or, if those files really must leave the backup, "
                           "run it once with --allow-mass-delete")
                return False
            lines = ["", f"!! CHECK BEFORE GOING ON: these lines would remove or replace more "
                         f"than {MIRROR_MAX_DELETE_PERCENT}% of their files"]
            lines += self._limit_lines(rows_indent=3)
            lines += _wrap("This usually means the folder was deleted or moved on the source, "
                           "another disk got the source's drive letter, the line's mode was "
                           "lowered, or a program rewrote the files (ransomware encrypts them in "
                           "place). If that is what you did, go on; if not, cancel and look at "
                           "the source first.", indent=3)
            leaving = ("This file leaves" if count == 1
                       else f"These {_files(count)} leave")
            lines += _wrap(f"{leaving} the backup. {fate}", indent=3)
            if not _typed_confirmation(lines, count, self.log):
                self._stop("the removal over the limit was not confirmed",
                           "look at the folders listed under CHECK; run the mirror again when "
                           "they are as they should be")
                return False
        outside = [item for item in self._items("remove")
                   if item.reason in ("other_source", "outside_run")]
        if outside:
            lines = ["", "!! TOTAL MIRROR: these folders are not in this run; their files leave "
                         "the backup"]
            lines += self._outside_lines(outside)
            verb = "leaves" if len(outside) == 1 else "leave"
            lines += _wrap(f"{_files(len(outside))} {verb} the backup. {fate}", indent=3)
            if not _typed_confirmation(lines, len(outside), self.log):
                self._stop("the total mirror was not confirmed",
                           "run the scoped mirror ([8] -> [1]), which keeps folders outside "
                           "this run, or run the total one again and confirm")
                return False
        return True

    def proceed_prompt(self):
        verb = "delete for good" if self.for_good else "remove"
        convert = (f", convert {_n(self.numbers['convert'])} to JXL"
                   if self.numbers.get("convert") else "")
        return (f"Mirror now? copy {_n(self.numbers['new'])}, replace "
                f"{_n(self.numbers['changed'])}{convert}, {verb} "
                f"{_n(self.numbers['remove'])} [y/N, Enter = no] ")

    # -- before the copy ---------------------------------------------------------------------
    def remove(self, dest, stamp, catalog, orphans, log):
        """The trash past its date (and what the person chose to free space),
        then what the review showed: only if it is still removed for the same
        reason, still ours, asked again right now."""
        seen = set()
        try:
            for run in self.expired + self.purge_runs:
                if run.name in seen:
                    continue
                seen.add(run.name)
                files, nbytes, problems = _purge_trash_run(run, dest, log)
                self.purged[0] += files
                self.purged[1] += nbytes
                self.purged[2] += problems
                if files:
                    self.changed_something = True
        except KeyboardInterrupt:
            self.interrupted = True
            self.changed_something = True
            log.log("INTERRUPTED while deleting the trash past its date")
            return
        fresh = {row.key: reason for reason, row in orphans}
        if self.total:
            for row in catalog.all_rows():
                if (not sb._key_under_prefixes(row.key, self.prefixes)
                        and row.key not in self.planned_keys):
                    fresh.setdefault(row.key, "outside_run")
        trash_here = os.path.join(_trash_root(dest), stamp)
        count = 0
        try:
            for item in self._items("remove", "drop_row"):
                row = item.row
                now = fresh.get(row.key)
                if now is None:
                    item.result = "not removed: back in the plan, or no longer in the catalog"
                    continue
                if now != item.reason:
                    item.result = f"not removed: now {now}"
                    continue
                dst = os.path.join(dest, row.rel)
                try:
                    state, size, mtime_ns = _dest_stat(dst)
                    if item.action == "drop_row" and state != "missing":
                        item.result = "not removed: it is in the backup again"
                    elif state == "missing":
                        catalog.delete(row.key)
                        item.result = "record dropped (already missing from the backup)"
                        log.log(f"MIRROR ROW DROPPED {row.rel} (already missing from the backup)")
                    elif state == "unreadable":
                        self.errors += 1
                        item.result = "error: cannot be read in the backup"
                        log.log(f"ERROR MIRROR {row.rel}: cannot be read in the backup")
                    elif not _ours(row, size, mtime_ns):
                        item.result = "kept: changed in the backup since the plan"
                        log.log(f"MIRROR KEPT {row.rel}: changed in the backup")
                    else:
                        if self.for_good:
                            _delete_file(dst)
                            item.result = "deleted for good"
                            log.log(f"MIRROR DELETED {row.rel}")
                        else:
                            target = os.path.join(trash_here, row.rel)
                            os.makedirs(sb._io(os.path.dirname(target)), exist_ok=True)
                            used = sb._move_aside(dst, target)
                            kept_as = "" if used == target else f" (kept as {os.path.basename(used)})"
                            item.result = "to the trash" + kept_as
                            log.log(f"MIRROR TRASH {row.rel}{kept_as}")
                        catalog.delete(row.key)
                        self.changed_something = True
                        sb._prune_empty_parents(os.path.dirname(dst), dest, log)
                    count += 1
                    if count % 50 == 0:
                        catalog.commit()
                except OSError as exc:
                    self.errors += 1
                    item.result = f"error: {exc.strerror or exc}"
                    log.log(f"ERROR MIRROR {row.rel}: {exc}")
        except KeyboardInterrupt:
            catalog.commit()
            self.interrupted = True
            log.log("INTERRUPTED while removing")
            return
        catalog.commit()

    # -- each changed file ---------------------------------------------------------------------
    def old_copy(self, dest, stamp, status, log):
        """What happens to the old copy of a changed file, right before the new
        one takes its name: ours goes to the trash (or is deleted with no
        trash); any other one goes to versions\\, as in a normal backup."""
        key = status.planned.rel.casefold()
        row = self.ours_changed.get(key)
        if row is not None:
            state, size, mtime_ns = _dest_stat(status.dst)
            if state != "ok" or not _ours(row, size, mtime_ns):
                row = None
        if row is None:
            before = sb._handle_old_copy(dest, stamp, status, log, keep_old=self._to_versions)
        elif self.for_good:
            before = self._replace_for_good(dest, stamp, status, log, row)
        else:
            before = sb._handle_old_copy(dest, stamp, status, log,
                                         keep_old=self._to_trash_if_ours(row))

        def counted(new_sha256):
            before(new_sha256)
            self.replaced["done"] += 1
        return counted

    def _to_versions(self, dest, stamp, status, log):
        sb._move_to_versions(dest, stamp, status, log)
        self.replaced["versions"] += 1

    def _to_trash_if_ours(self, row):
        def keep_old(dest, stamp, status, log):
            state, size, mtime_ns = _dest_stat(status.dst)
            if state == "unreadable":       # never moved blind (finding 12): the copy fails
                raise OSError(f"the old copy cannot be read; it stays: {status.planned.rel}")
            if state == "missing":
                return
            if not _ours(row, size, mtime_ns):
                self._to_versions(dest, stamp, status, log)     # changed since the check
                return
            target = os.path.join(_trash_root(dest), stamp, status.planned.rel)
            os.makedirs(sb._io(os.path.dirname(target)), exist_ok=True)
            used = sb._move_aside(status.dst, target)
            log.log(f"MIRROR TRASH old copy {status.planned.rel}"
                    + ("" if used == target else f" (kept as {os.path.basename(used)})"))
            self.replaced["trash"] += 1
            self.changed_something = True
        return keep_old

    def _replace_for_good(self, dest, stamp, status, log, row):
        def before_final(_new_sha256):
            state, size, mtime_ns = _dest_stat(status.dst)
            if state == "unreadable":       # never replaced blind (finding 12): the copy fails
                raise OSError(f"the old copy cannot be read; it stays: {status.planned.rel}")
            if state == "ok" and _ours(row, size, mtime_ns):
                _make_writable(status.dst)          # Windows refuses to replace a read-only file
                log.log(f"MIRROR REPLACED {status.planned.rel} (old copy deleted)")
                self.replaced["for_good"] += 1
                self.changed_something = True
            elif state == "ok":                     # changed since the check: not ours any more
                self._to_versions(dest, stamp, status, log)
        return before_final

    def keep_old_file(self, dest, stamp, rel, log):
        """Where a backup file goes when a TIFF's JXL takes over (an old JXL
        group, or a TIFF copy the JXL replaces): ours to the trash -- deleted,
        with no trash --, anything else to versions\\, as in a normal backup.
        A TIFF copy without a row that still has the source TIFF's size and
        date (made by another program) is what a run adopts: ours too."""
        path = os.path.join(dest, rel)
        row = self.rows_by_key.get(rel.casefold())
        if row is None and rel.casefold() in self.adoptable:
            source_size, source_mtime_ns = self.adoptable[rel.casefold()]
            row = SimpleNamespace(size=source_size, mtime_ns=sb._db_ns(source_mtime_ns))
        state, size, mtime_ns = _dest_stat(path)
        if state == "unreadable":
            raise OSError(f"cannot be read in the backup; it stays: {rel}")
        if state == "ok" and row is not None and _ours(row, size, mtime_ns):
            if self.for_good:
                _delete_file(path)
                log.log(f"MIRROR DELETED {rel} (replaced by its JXL)")
                self.replaced["jxl_for_good"] += 1
                result = "deleted for good (replaced by its JXL)"
            else:
                target = os.path.join(_trash_root(dest), stamp, rel)
                os.makedirs(sb._io(os.path.dirname(target)), exist_ok=True)
                used = sb._move_aside(path, target)
                log.log(f"MIRROR TRASH {rel} (replaced by its JXL)"
                        + ("" if used == target else f" (kept as {os.path.basename(used)})"))
                self.replaced["jxl_trash"] += 1
                result = "to the trash (replaced by its JXL)"
            self.changed_something = True
        elif state == "ok":
            sb._move_file_to_versions(dest, stamp, rel, log)
            self.replaced["jxl_versions"] += 1
            result = "to versions\\ (not written by the backup)"
        else:
            result = "already missing"
        item = self.retire_items.get(rel.casefold())
        if item is not None:
            item.result = result

    # -- after the copy ------------------------------------------------------------------------
    def finish(self, log, done, done_bytes, error_count, interrupted=False):
        """What happened, in the same table as the summary; (problems?)."""
        removed = [item for item in self._items("remove")
                   if item.result.startswith(("to the trash", "deleted for good"))]
        dropped = [item for item in self._items("remove", "drop_row")
                   if item.result.startswith("record dropped")]
        not_now = [item for item in self._items("remove", "drop_row")
                   if item.result.startswith(("not removed", "kept"))]
        errors = self.errors + error_count
        title = "MIRROR INTERRUPTED" if interrupted else "MIRROR DONE"
        replaced = self.replaced["done"]
        fate = "deleted for good" if self.for_good else f"in the trash until {_until()}"
        parts = [f"{_n(done)} copied"
                 + (f" ({_n(replaced)} of them replacing a changed file)" if replaced else "")]
        parts.append(f"{_n(len(removed))} removed from the backup ({fate})" if removed
                     else "nothing removed from the backup")
        headline = ("; ".join(parts) + ". "
                    + ("No errors." if not errors else f"{_n(errors)} ERRORS: see the log."))
        lines = ["", _top_rule(), f" {title} - {self.dest}"]
        lines += _wrap(headline)
        lines += [_RULE, _table_head()]
        lines.append(_table_line("copied", done, done_bytes))
        if replaced:
            lines.append(_table_line("of them replacing a changed file", replaced, indent=4))
            if self.replaced["trash"]:
                lines.append(_table_line("old copy to the trash", self.replaced["trash"], indent=6))
            if self.replaced["for_good"]:
                lines.append(_table_line("old copy deleted (no trash)", self.replaced["for_good"],
                                         indent=6))
            if self.replaced["versions"]:
                lines.append(_table_line("old copy to versions\\ (not written by the backup)",
                                         self.replaced["versions"], indent=6))
        where = "deleted for good" if self.for_good else "to the trash"
        lines.append(_table_line(f"removed from the backup ({where})", len(removed),
                                 sum(item.size for item in removed)))
        if dropped:
            lines.append(_table_line("already missing from the backup (record dropped)",
                                     len(dropped)))
        if not_now:
            lines.append(_table_line("not removed after all (changed since the plan)",
                                     len(not_now), sum(item.size for item in not_now)))
        info = self.convert_info or {}
        if info.get("converted") or info.get("failed") or info.get("retired"):
            kinds = ", ".join(f"{info[kind]} {kind.upper()}" for kind in ("tiff", "jpeg")
                              if info.get(kind))
            lines.append(_table_line(f"converted to JXL ({kinds or 'their own size'})",
                                     info.get("converted", 0), info.get("bytes_in", 0)))
            lines.append(_table_line("the JXL written", info.get("converted", 0),
                                     info.get("bytes_out", 0), indent=4))
            for key, label in (("jxl_trash", "old copies replaced: to the trash"),
                               ("jxl_for_good", "old copies replaced: deleted (no trash)"),
                               ("jxl_versions", "old copies replaced: to versions\\")):
                if self.replaced[key]:
                    lines.append(_table_line(label, self.replaced[key], indent=4))
            if info.get("failed"):
                lines.append(_table_line("TIFF copied as TIFF (the conversion failed)",
                                         info["failed"]))
        if self.purged[0] or self.purged[2]:
            lines.append(_table_line("trash deleted for good", self.purged[0], self.purged[1]))
        lines.append(_table_line("errors", errors + self.purged[2] + info.get("errors", 0)))
        if (removed or self.replaced["trash"] or self.replaced["jxl_trash"]) and not self.for_good:
            lines += ["", f"  Removed and replaced files are in this folder until {_until()};",
                      "  to get one back, copy it from there (the folders are the same as in the backup):",
                      f"    {os.path.join(_trash_root(self.dest), self.stamp)}"]
        self._write_actions()
        if self.actions_csv:
            lines += ["", "  The list of every file and what happened to it:"]
            lines += _list_place(self.actions_csv)
        lines.append(f"  The log: {os.path.basename(log.path)}")
        lines.append(_RULE)
        for line in lines:
            log.out(line)
        return bool(self.errors or self.purged[2] or info.get("failed") or info.get("errors"))

    def release(self, log):
        if self.lock_text:
            drop_lock(self.dest, self.lock_text, log)
            self.lock_text = None


# ---- The runs ----------------------------------------------------------------------------------
def _log_header(log, args, argv):
    command = sys.argv if argv is None else ["mirror_backup.py"] + [str(item) for item in argv]
    log.log("mirror_backup " + __version__)
    log.log("command: " + " ".join(command))
    log.log("settings: " + ", ".join(f"{key}={value}" for key, value in sorted(vars(args).items())))
    log.log(f"mirror settings: MIRROR_KEEP_DAYS={MIRROR_KEEP_DAYS}, "
            f"MIRROR_MAX_DELETE_PERCENT={MIRROR_MAX_DELETE_PERCENT}, "
            f"MIRROR_MAX_DELETE_MIN={MIRROR_MAX_DELETE_MIN}")


def _new_log(stamp, kind):
    try:
        return sb._Logger(os.path.join(sb.LOG_DIR, f"{stamp}_{kind}"))
    except OSError as exc:
        print(f"cannot write the log directory {sb.LOG_DIR}: {exc}", file=sys.stderr)
        return None


def _run_mirror(args, backend_args, rows, modes, stamp, argv):
    dest = os.path.abspath(args.dest)
    log = _new_log(stamp, "mirror")
    if log is None:
        return 2
    job = MirrorJob(args, rows, stamp)
    try:
        _log_header(log, args, argv)
        if rows is not None:
            log.log("manifest: " + os.path.abspath(args.manifest))
            log.log("modes: " + os.path.abspath(args.modes))
            for row in rows:
                log.log(f"row {row.folder} mode {row.mode} originals {row.originals} "
                        f"mirror {row.mirror} tiff {row.tiff}")
        rc = None
        if args.dry_run:
            why = _live_lock(dest)      # a dry-run takes no lock, but stops while a run holds one
            if why:
                job._stop(why, "wait until that mirror ends, then run the dry-run again")
                rc = 2
        if rc is None:
            if rows is not None:
                list_cache = {}

                def plan_fn(plan_args):
                    try:
                        return mb._plan_rows(rows, modes, plan_args, list_cache,
                                             on_row=job.record_row)
                    finally:
                        list_cache.clear()
            else:
                def plan_fn(plan_args):
                    return job.record_plan(sb.plan(plan_args))
            try:
                rc = sb._run_backup(backend_args, log, stamp, plan_fn=plan_fn, mirror=job)
            except KeyboardInterrupt:
                print("Interrupted.", file=sys.stderr)
                log.log("Interrupted.")
                rc = 130
        if rc == 2 and job.changed_something:
            rc = 1          # something was done before it stopped: not "nothing changed"
        if rc == 2:
            for line in _stop_lines(dest, job.stop_reason or "see the message above",
                                    job.stop_todo or "fix what the message says, then run "
                                                     "the mirror again"):
                log.out(line)
        elif args.dry_run and rc in (0, 1):
            log.out(" Dry-run: nothing was changed in the backup.")
        return rc
    finally:
        job.release(log)
        log.close()


def _trash_args_problem(args, dest, log):
    """Why the trash of `dest` cannot be shown or emptied, or None."""
    if args.expect_backup_id:
        found = sb.read_backup_id(dest)
        if found != args.expect_backup_id:
            return (f"{dest} is not the expected backup (expected {args.expect_backup_id}, "
                    f"found {found or 'no backup here'})")
    return _trash_problem(dest)


def _show_trash(args, stamp, argv):
    """What is in the trash, where it came from, until when, and how to get a
    file back. Changes nothing."""
    dest = os.path.abspath(args.dest)
    log = _new_log(stamp, "mirror_trash")
    if log is None:
        return 2
    try:
        _log_header(log, args, argv)
        problem = _trash_args_problem(args, dest, log)
        runs = foreign = []
        if problem is None:
            runs, foreign, problem = _trash_runs(dest)
        if problem:
            lines = ["", _top_rule(), f" TRASH OF {dest}", _RULE]
            lines += _wrap("It cannot be read: " + problem)
            lines.append(_RULE)
            for line in lines:
                log.out(line)
            return 2
        lines = ["", _top_rule(), f" TRASH OF {dest}"]
        lines += _wrap("What the mirror removed from the backup or replaced in it. Nothing here "
                       "is deleted before its date.")
        lines.append(_RULE)
        if runs:
            lines += _trash_table(runs)
            lines += ["", "  To get a file back, copy it from the folder of its run; the folders "
                          "inside are", "  the same as in the backup:"]
            for run in list(reversed(runs))[:MIRROR_SUMMARY_LINES]:
                lines.append(f"    {run.path}")
            lines.append("  The list of every file of a run: <its name>_mirror_actions.csv, in")
            lines.append(f"    {sb.LOG_DIR}")
        else:
            lines.append("  The trash is empty.")
        if foreign:
            lines += ["", "  Not made by the mirror (never touched):"]
            lines += [f"    {path}" for path in foreign[:MIRROR_SUMMARY_LINES]]
        lines.append(_RULE)
        for line in lines:
            log.out(line)
        return 0
    finally:
        log.close()


def _empty_trash(args, stamp, argv):
    """Delete runs of the trash for good, after the typed number of files.
    Never without a person at the console."""
    dest = os.path.abspath(args.dest)
    log = _new_log(stamp, "mirror_trash")
    if log is None:
        return 2
    lock_text = None
    try:
        _log_header(log, args, argv)
        problem = _trash_args_problem(args, dest, log)
        runs = foreign = []
        if problem is None:
            runs, foreign, problem = _trash_runs(dest)
        if problem:
            for line in _stop_lines(dest, problem, "fix that, then try again"):
                log.out(line)
            return 2
        if args.before:
            before = datetime.strptime(args.before, "%Y-%m-%d")
            selected = [run for run in runs if run.when < before]
        else:
            selected = list(runs)
        files = sum(run.files for run in selected)
        nbytes = sum(run.nbytes for run in selected)
        which = f"taken out before {args.before}" if args.before else "everything"
        lines = ["", _top_rule(), f" EMPTY THE TRASH OF {dest} ({which})"]
        if selected:
            lines += _wrap(f"{_files(files)} ({_size(nbytes)}) will be DELETED FOR GOOD. They "
                           f"can NOT be recovered later.")
            lines.append(_RULE)
            lines += _trash_table(selected)
        else:
            lines += _wrap("Nothing to delete in the trash.")
        if foreign:
            lines += ["", "  Not made by the mirror (never touched):"]
            lines += [f"    {path}" for path in foreign[:MIRROR_SUMMARY_LINES]]
        lines.append(_RULE)
        for line in lines:
            log.out(line)
        if not selected:
            return 0
        if args.dry_run:
            log.out(" Dry-run: nothing was deleted.")
            return 0
        if args.unattended:
            for line in _stop_lines(dest, "the trash is emptied only with a person at the console",
                                    "run it by hand (photo_backup.py -> [8] Mirror -> [4]) or "
                                    "without --unattended"):
                log.out(line)
            return 2
        try:
            lock_text, why = take_lock(dest, log)
        except OSError as exc:
            lock_text, why = None, f"the lock could not be written ({exc.strerror or exc})"
        if why:
            for line in _stop_lines(dest, why, "wait until the mirror ends, then try again"):
                log.out(line)
            return 2
        sentence = (f"Everything listed ({_files(files)}, {_size(nbytes)}) will be deleted and "
                    f"can NOT be recovered later. Are you sure?")
        if not _typed_confirmation(["", "!! DELETE FOR GOOD"] + _wrap(sentence), files, log):
            for line in _stop_lines(dest, "deleting the trash was not confirmed",
                                    "nothing to do: the trash is as it was"):
                log.out(line)
            return 2
        deleted = deleted_bytes = problems = 0
        for run in selected:
            count, size, trouble = _purge_trash_run(run, dest, log)
            deleted += count
            deleted_bytes += size
            problems += trouble
        log.out(f" Trash: {_files(deleted)} ({_size(deleted_bytes)}) deleted for good"
                + (f"; {_n(problems)} problems: see the log {log.path}" if problems else "."))
        return 1 if problems else 0
    finally:
        if lock_text:
            drop_lock(dest, lock_text, log)
        log.close()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mirror_backup.py",
        description="Mirror a manifest (or folders) into a backup: what changed is replaced, what "
                    "is gone is removed, through a trash. The source is read-only.",
        epilog="With --source, every selection option of selective_backup.py also works "
               "(--unselected, --unexported, --c1-cache, --exclude-folders, ...).")
    parser.add_argument("--manifest", default=None, metavar="PATH",
                        help="CSV with the folders: Folder,Mode,Originals,Mirror")
    parser.add_argument("--source", action="append", default=None, metavar="PATH",
                        help="folder to mirror, without a manifest (repeatable)")
    parser.add_argument("--dest", required=True, metavar="PATH", help="the backup")
    parser.add_argument("--modes",
                        default=os.path.join(os.path.dirname(os.path.abspath(mb.__file__)),
                                             mb.MODES_FILE),
                        metavar="PATH", help="modes table (default: %(default)s)")
    parser.add_argument("--base", default=None, metavar="PATH",
                        help="with --source: base for the relative layout (default: drive root)")
    parser.add_argument("--mirror", choices=mb.MIRROR_VALUES, default=None,
                        help="with --source: disk removes what is gone from the folders; "
                             "selection also what is no longer selected "
                             f"(default: {mb.DEFAULT_MIRROR})")
    parser.add_argument("--total", action="store_true",
                        help="also remove what came from folders that are not in this run "
                             "(a person types the number of files)")
    parser.add_argument("--allow-mass-delete", action="store_true",
                        help="let a line remove or replace more than MIRROR_MAX_DELETE_PERCENT "
                             "of its files without typing the number")
    parser.add_argument("--unattended", action="store_true",
                        help="nobody at the console: never ask; what needs a typed number stops "
                             "the run (implies --yes)")
    parser.add_argument("--show-trash", action="store_true",
                        help="list what is in the trash of --dest and how to get it back")
    parser.add_argument("--empty-trash", action="store_true",
                        help="delete the trash of --dest for good (the number of files is typed)")
    parser.add_argument("--before", default=None, metavar="YYYY-MM-DD",
                        help="with --empty-trash: only what was taken out before that day")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask 'Mirror now?' (a typed number is still asked)")
    parser.add_argument("--export-formats", default=sb.EXPORT_FORMATS, metavar="SPEC",
                        help="all, a>b>c fallback, or a+b multiple (default: %(default)s)")
    parser.add_argument("--verify-copy", choices=("none", "size", "hash"), default=sb.VERIFY_COPY,
                        help="verification of each copy (default: %(default)s)")
    parser.add_argument("--space-margin-gb", type=float, default=sb.SPACE_MARGIN_GB, metavar="N",
                        help="free space kept on the destination (default: %(default)s)")
    parser.add_argument("--jxlphoto", default=sb.JXLPHOTO, metavar="PATH",
                        help="jxl-photo's jxl_photo.py or jxl-photo.exe, for TIFFs kept as JXL "
                             "(a manifest's Tiff column, or --tiff; default: JXLPHOTO)")
    parser.add_argument("--work-dir", default=sb.JXL_WORK_DIR, metavar="PATH",
                        help="where the conversions run, each in a folder of its own "
                             "(default: JXL_WORK_DIR; empty = the system's temp folder)")
    parser.add_argument("--no-convert", action="store_true",
                        help="the conversion off for this run: what would be converted is "
                             "copied as it is; the JXL already in the backup stay, unless "
                             "their file changed")
    parser.add_argument("--expect-backup-id", default=None, metavar="ID",
                        help="abort unless the destination holds this backup ID")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv=None):
    sb._start_output()          # ～ paths on a cp1252 console; each line out at once
    sb._STOPPING.clear()
    sb._set_console_title(sb._program_name("mirror_backup"))
    parser = build_parser()
    try:
        args, extra = parser.parse_known_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return int(code) if isinstance(code, int) else 2
    errors = _settings_errors() + _usage_errors(args, extra)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    stamp = time.strftime("%y%m%d_%H%M%S")
    if args.show_trash:
        return _show_trash(args, stamp, argv)
    if args.empty_trash:
        return _empty_trash(args, stamp, argv)
    if args.manifest:
        try:
            modes = mb.read_modes(args.modes)
            rows = mb.read_manifest(args.manifest, modes)
        except mb.ManifestError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        backend_args = _manifest_backend_args(args, rows)
        errors = mb._validate_rows(backend_args, rows)
        if not args.no_convert:
            errors += mb._tiff_rows_errors(rows, args.jxlphoto, args.dest, args.work_dir)
    else:
        rows = modes = None
        try:
            backend_args = _plain_backend_args(args, extra)
        except SystemExit:
            return 2
        errors = sb._validate_usage(backend_args)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    return _run_mirror(args, backend_args, rows, modes, stamp, argv)


if __name__ == "__main__":
    sys.exit(main())
