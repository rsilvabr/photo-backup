#!/usr/bin/env python3
"""mobile_backup - mirror the phone derivatives as one album per event.

A phone gallery shows each folder as an album and only the folder's name, so
an archive of `<event>\\<camera>\\_EXPORT\\JXL_mobile\\` folders turns into a
wall of albums all called "JXL_mobile". This job copies every file of those
mobile folders to a destination, keeping the path down to the EVENT folder
and putting the photos directly in it:

    G:\\2026\\260620_Kyoto\\2_Z8\\_EXPORT\\JXL_mobile\\_DSC_0001_sRGB_v1.jxl
      -> H:\\MOBILE\\2026\\260620_Kyoto\\0000__DSC_0001_sRGB_v1.jxl

Every photo gets a number, camera by camera: the mobile folders in the
natural order of their paths (1_Z8, 2_Z8, ...), and inside each one in shot
order (DateTimeOriginal + SubSecTimeOriginal, read with exiftool); photos
without a date close their camera, by name. Cameras are never interleaved by
their clocks, which disagree.

The destination is a MIRROR of the sources, kept in sync: a new photo is
copied, a changed one is re-copied, a renumbered one is renamed, a photo gone
from the source is deleted from the mirror. Only files this job wrote (listed
in its state file under STATE_FOLDER) are ever renamed or deleted; anything
else found in an album folder is left alone, and an album whose new name would
land on such a file is skipped and reported. The sources are READ-ONLY.

A delete needs proof (v0.1.2): a run only deletes or renumbers the photos whose
path is under one of the sources it was given (the state also records each
photo's root); a folder of a source that cannot be read stops every delete and
rename of that run (exit 1). A photo whose shot time exiftool did not answer for is read
again on the next run (exit 1), never recorded as "no date".

The state records what was really copied (v0.1.3): the size and date the
source had while its bytes were read, not what the scan saw earlier. A state
entry with nothing behind it (source gone, mirror file gone) is dropped, and a
mirror file that a photo still in the source holds is never deleted in the
name of another entry.
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

__version__ = "0.1.3"

# ---- Sources and destination ---------------------------------------------------
SOURCES = ""                        # ';'-separated default for --source (e.g. r"G:\2024;G:\2025;G:\2026")
DESTINATION = ""                    # default for --dest (e.g. r"H:\MOBILE")

# ---- What is a mobile folder, what is an event -----------------------------------
MOBILE_FOLDER_NAMES = "JXL_mobile"  # ';'-separated folder names (any case) whose files are mirrored
EXTENSIONS = (".jxl", ".jpg", ".jpeg", ".heic", ".avif", ".png")
EVENT_NAME_PATTERN = r"^\d{6}"      # the album is the nearest folder ABOVE the mobile folder whose name matches
                                    # (up to the drive: the same album whatever --source was given)
EXPORT_NAME_CONTAINS = "export"     # without a match: the nearest folder above whose name does not contain this
SYSTEM_SKIP_FOLDERS = "$RECYCLE.BIN;System Volume Information"

# ---- Numbering ----------------------------------------------------------------------
NUMBER_DIGITS = 4                   # 0000_; widened automatically for an album of 10 000+ photos
NUMBER_SEPARATOR = "_"

# ---- State, copy, exiftool -----------------------------------------------------------
STATE_FOLDER = ".mobile_backup"     # under the destination; exclude it in FreeFileSync
STATE_FILE = "state.json"
STATE_SAVE_EVERY = 20               # copies between two saves of the state (also saved after the renames)
STATE_SAVE_SPREAD = 200             # ... and in a long run 1/200 of the copies done so far, when that is more
                                    # (after 4 000 copies): every save rewrites the whole state. A kill loses
                                    # the copies since the last save (their albums are skipped until the files
                                    # are removed by hand), so keep it high; 0 = always STATE_SAVE_EVERY
PROGRESS_EVERY = 200                # copies between two progress lines on the screen
TEMP_SUFFIX = ".mbtmp"              # in-progress copy, next to its final name
RENAME_SUFFIX = ".mbren"            # a file between its old and new number
SPACE_MARGIN_GB = 5.0
EXIFTOOL = "exiftool"
EXIFTOOL_BATCH = 200                # files per exiftool call
EXIFTOOL_TIMEOUT = 600              # seconds per batch (a hard disk under load is slow)

# ---- Logs ------------------------------------------------------------------------------
LOG_DIR = os.environ.get("PHOTOBACKUP_LOG_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "Logs")


def _io(path):
    """Extended-length Windows path for every filesystem call."""
    path = os.path.abspath(path)
    if os.name != "nt" or path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _plain(path):
    """Undo _io: the path as the user writes it."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _names(value):
    return {part.strip().lower() for part in value.split(";") if part.strip()}


def _is_inside(child, parent):
    child = os.path.normcase(os.path.abspath(child))
    parent = os.path.normcase(os.path.abspath(parent))
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:              # different drives
        return False


class Logger:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.handle = open(path, "a", encoding="utf-8")

    def log(self, message):
        self.handle.write(message + "\n")
        self.handle.flush()

    def out(self, message):
        print(message)
        self.log(message)

    def close(self):
        try:
            self.handle.close()
        except OSError:
            pass


# ---- Discovery --------------------------------------------------------------------------

def album_folder(mobile_dir, source_root=None):
    """The event folder a mobile folder belongs to (absolute path): the nearest
    folder above it whose name matches EVENT_NAME_PATTERN, else the nearest one
    whose name does not contain EXPORT_NAME_CONTAINS. The search goes up to the
    drive, also above `source_root` (kept for its callers, not used): the album
    of a photo depends on where the photo is, never on which --source the run
    was given. A run with --source on a camera folder inside an event must not
    move that camera's photos to an album of their own (v0.1.3)."""
    pattern = re.compile(EVENT_NAME_PATTERN)
    current = os.path.dirname(os.path.abspath(mobile_dir))
    fallback = None
    while True:
        name = os.path.basename(current)
        if not name:                # reached the drive root
            break
        if pattern.search(name):
            return current
        if fallback is None and EXPORT_NAME_CONTAINS.lower() not in name.lower():
            fallback = current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return fallback or os.path.dirname(os.path.abspath(mobile_dir))


def album_rel(album_dir):
    """The album's path below its drive: G:\\2026\\event -> 2026\\event."""
    drive, rest = os.path.splitdrive(os.path.abspath(album_dir))
    return rest.lstrip("\\/")


def find_photos(sources, errors=None, roots=None):
    """{source file: album rel path} for every file of every mobile folder.
    `errors` (a list) receives "folder: reason" for every folder that could not
    be read -- a folder os.walk cannot list is otherwise silently empty, and
    its photos would look gone. `roots` (a dict) receives each file's source root."""
    mobile_names = _names(MOBILE_FOLDER_NAMES)
    skip = _names(SYSTEM_SKIP_FOLDERS)
    found = {}
    for root in sources:
        def unreadable(exc, root=root):
            if errors is not None:
                folder = _plain(getattr(exc, "filename", None) or _io(root))
                errors.append(f"{folder}: {getattr(exc, 'strerror', None) or exc}")

        for current, dirs, files in os.walk(_io(root), onerror=unreadable):
            dirs[:] = sorted(d for d in dirs if d.lower() not in skip)
            if os.path.basename(current).lower() not in mobile_names:
                continue
            dirs[:] = []            # a mobile folder's own subfolders are not albums
            plain = _plain(current)
            rel = album_rel(album_folder(plain, root))
            for name in sorted(files):
                if os.path.splitext(name)[1].lower() in EXTENSIONS:
                    found[os.path.join(plain, name)] = rel
                    if roots is not None:
                        roots[os.path.join(plain, name)] = root
    return found


# ---- Shot time ----------------------------------------------------------------------------

def _shot_key(record):
    value = str(record.get("DateTimeOriginal") or "").strip()
    if not re.match(r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}", value) or value.startswith("0000"):
        return None
    sub = re.sub(r"\D", "", str(record.get("SubSecTimeOriginal") or ""))
    return value[:19] + "." + (sub + "000000")[:6]


def read_shot_times(paths, log=None):
    """{path: 'YYYY:MM:DD HH:MM:SS.ffffff' or None}, exiftool in batches. None =
    exiftool read the file and it has no date. A path exiftool gave no record
    for (it failed, timed out or is not installed) is left OUT of the result:
    it was not read, and must not be remembered as "no date"."""
    result = {}
    paths = list(paths)
    for start in range(0, len(paths), EXIFTOOL_BATCH):
        batch = paths[start:start + EXIFTOOL_BATCH]
        fd, argfile = tempfile.mkstemp(suffix=".args")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(["-charset", "filename=utf8", "-j", "-fast",
                                        "-DateTimeOriginal", "-SubSecTimeOriginal"] + batch) + "\n")
            proc = subprocess.run([EXIFTOOL, "-@", argfile], capture_output=True,
                                  timeout=EXIFTOOL_TIMEOUT)
            text = proc.stdout.decode("utf-8", errors="replace").strip()
            records = json.loads(text) if text else []
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            if log:
                log.out(f"WARNING exiftool failed on {len(batch)} file(s): {exc}")
            records = []
        finally:
            try:
                os.unlink(argfile)
            except OSError:
                pass
        by_path = {os.path.normcase(os.path.abspath(r.get("SourceFile", ""))): r for r in records}
        for path in batch:
            record = by_path.get(os.path.normcase(os.path.abspath(path)))
            if record is not None:
                result[path] = _shot_key(record)
        if log and len(paths) > EXIFTOOL_BATCH:
            log.out(f"  shot times: {min(start + EXIFTOOL_BATCH, len(paths))}/{len(paths)} read")
    return result


# ---- State ----------------------------------------------------------------------------------

def _state_path(dest):
    return os.path.join(dest, STATE_FOLDER, STATE_FILE)


def load_state(dest, meta=None):
    """The state's files. `meta` (a dict) receives the state's "tool"."""
    path = _state_path(dest)
    if not os.path.exists(_io(path)):
        return {}
    with open(_io(path), encoding="utf-8") as handle:
        data = json.load(handle)    # an unreadable state aborts: never guess what we wrote
    if meta is not None:
        meta["tool"] = data.get("tool")
    return data.get("files", {})


def _tool_version(tool):
    """(0, 1, 2) from "mobile_backup 0.1.2"; None when it cannot be read."""
    match = re.fullmatch(r"mobile_backup (\d+)\.(\d+)\.(\d+)", str(tool or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def forget_old_null_shots(state, tool):
    """A state written before 0.1.2 may hold shot = null from an exiftool that
    failed (0.1.1 recorded that as "no date"). Every null shot of such a state
    is read once more (S2); the state saved after it carries this version, so a
    photo read and really without a date is not read again."""
    version = _tool_version(tool)
    if version is not None and version >= (0, 1, 2):
        return 0
    count = 0
    for rec in state.values():
        if "shot" in rec and rec["shot"] is None:
            del rec["shot"]
            count += 1
    return count


def save_state(dest, files):
    path = _state_path(dest)
    os.makedirs(_io(os.path.dirname(path)), exist_ok=True)
    tmp = path + TEMP_SUFFIX
    with open(_io(tmp), "w", encoding="utf-8") as handle:
        json.dump({"version": 1, "tool": "mobile_backup " + __version__, "files": files},
                  handle, ensure_ascii=False, indent=0)
    os.replace(_io(tmp), _io(path))


# ---- Plan -------------------------------------------------------------------------------------

def _natural(text):
    """'2_Z8' < '10_Z8': digit runs compare as numbers."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part)
            for part in re.split(r"(\d+)", text.lower()) if part]


def numbered_names(entries):
    """entries: [(source, shot)] of one album -> {source: numbered file name}.

    Camera by camera: the mobile folders in the natural order of their paths
    (1_Z8 before 2_Z8, the folder numbers the owner gives his cameras), and
    inside each one by shot time. Cameras are never interleaved by their
    clocks — even two bodies of the same model drift minutes apart, and old
    ones carry 1970/2006 dates or the home time zone.
    """
    ordered = sorted(entries, key=lambda e: (_natural(os.path.dirname(e[0])),
                                             e[1] is None, e[1] or "",
                                             os.path.basename(e[0]).lower(), e[0].lower()))
    width = max(NUMBER_DIGITS, len(str(len(ordered) - 1)))
    return {source: f"{index:0{width}d}{NUMBER_SEPARATOR}{os.path.basename(source)}"
            for index, (source, _shot) in enumerate(ordered)}


def _under_run(source, run_roots):
    """Whether a photo of the state belongs to this run: its path is under one of
    the run's sources, which were read in full (a read error stops every delete).
    Decided by the path, not by the root recorded in the state: after a test run
    with a narrower --source, a photo deleted from the source must still leave
    the mirror on the next normal run."""
    return any(_is_inside(source, root) for root in run_roots)


def plan(photos, stats, shots, state, dest, run_roots=None):
    """Decide copies, renames and deletes. Returns a dict of lists. With
    `run_roots`, a photo of the state that is not under one of them is never
    deleted (it is listed in "elsewhere"), and an album whose numbers would
    land on one is skipped."""
    albums = {}
    for source, rel in photos.items():
        albums.setdefault(rel, []).append((source, shots.get(source)))
    if run_roots is not None:
        # A run given only a part of an event (--source on one camera folder)
        # numbers that part as the album last was: the album's other photos,
        # known from the state, keep their place in the count. They are counted
        # only -- never copied, renamed or deleted here (v0.1.3).
        by_fold = {os.path.normcase(rel): rel for rel in albums}
        for source, rec in state.items():
            if source in photos or _under_run(source, run_roots):
                continue
            rel = by_fold.get(os.path.normcase(album_rel(album_folder(os.path.dirname(source)))))
            if rel is not None:
                albums[rel].append((source, rec.get("shot")))
    desired = {}
    for rel, entries in albums.items():
        for source, name in numbered_names(entries).items():
            if source in photos:
                desired[source] = os.path.join(rel, name)

    # Who holds each mirror name. Two entries with one name is a state gone
    # wrong (0.1.2 could write that): the photo still in a source is the owner.
    tracked = {}
    holders = {}
    for source, rec in state.items():
        key = rec["dest"].lower()
        holders.setdefault(key, []).append(source)
        if key not in tracked or source in desired:
            tracked[key] = source

    def held_by_another(source, rel):
        """A photo still in a source holds this mirror name too: the file there
        is that photo's, and is not deleted in the name of `source`."""
        return any(other != source and other in desired for other in holders.get(rel.lower(), ()))

    actions = {"copy": [], "rename": [], "delete": [], "keep": [], "conflict": [], "kept_changed": [],
               "elsewhere": [], "forget": []}

    blocked = set()
    for source, rel_dest in desired.items():
        target = os.path.join(dest, rel_dest)
        if not os.path.exists(_io(target)):
            continue
        owner = tracked.get(rel_dest.lower())
        # a file we did not write, or one of ours that was changed since: never overwrite it
        if owner is None or os.path.getsize(_io(target)) != state[owner]["size"]:
            blocked.add(photos[source])
        # nor a photo of a source this run was not given
        elif run_roots is not None and owner not in photos and not _under_run(owner, run_roots):
            blocked.add(photos[source])
    for rel in sorted(blocked):
        actions["conflict"].append(rel)

    for source, rel_dest in sorted(desired.items(), key=lambda kv: kv[1].lower()):
        if photos[source] in blocked:
            continue
        size, mtime_ns = stats[source]
        rec = state.get(source)
        current = os.path.join(dest, rec["dest"]) if rec else None
        current_ok = bool(rec) and os.path.exists(_io(current)) and \
            os.path.getsize(_io(current)) == rec["size"] and \
            not held_by_another(source, rec["dest"])    # whose file it is cannot be told: copy again
        unchanged = bool(rec) and rec["size"] == size and rec["mtime_ns"] == mtime_ns
        # the same name in another case is the same file of the mirror (a
        # --source typed as g:\ instead of G:\): nothing to rename
        same_name = bool(rec) and os.path.normcase(rec["dest"]) == os.path.normcase(rel_dest)
        if current_ok and unchanged:
            if same_name:
                actions["keep"].append(source)
            else:
                actions["rename"].append((source, rec["dest"], rel_dest))
        else:
            actions["copy"].append((source, rel_dest))
            if current_ok and not same_name:
                actions["delete"].append((source, rec["dest"]))

    for source, rec in sorted(state.items()):
        if source in desired:       # still in a source (renamed, kept, or its album skipped)
            continue
        if run_roots is not None and not _under_run(source, run_roots):
            actions["elsewhere"].append(source)     # not proof that it is gone
            continue
        current = os.path.join(dest, rec["dest"])
        if not os.path.exists(_io(current)):
            # Source gone and nothing of it on the mirror: the entry is dropped.
            # Left in the state, it would delete the file of whatever photo is
            # copied to that name later (v0.1.3). Dropping deletes nothing.
            actions["forget"].append(source)
            continue
        if held_by_another(source, rec["dest"]):
            actions["forget"].append(source)        # that file is the other photo's: never deleted
            continue
        if os.path.getsize(_io(current)) != rec["size"]:
            actions["kept_changed"].append(rec["dest"])   # not the file we wrote: leave it
            continue
        actions["delete"].append((source, rec["dest"]))
    return actions


# ---- Execute ----------------------------------------------------------------------------------

def _writable(path):
    """Clear the read-only attribute. Only ever called on a file that is ours:
    one with our temp suffixes, or a mirror file the state proves this job
    wrote (0.1.2 and older carried the attribute over from a read-only source,
    and then could neither delete nor replace their own copy)."""
    os.chmod(_io(path), stat.S_IREAD | stat.S_IWRITE)


def _is_read_only(path):
    try:
        return not os.stat(_io(path)).st_mode & stat.S_IWRITE
    except OSError:
        return False


def _unlink_ours(path):
    """Delete a file that is ours (see _writable), read-only or not."""
    try:
        os.unlink(_io(path))
    except PermissionError:
        if not _is_read_only(path):
            raise
        _writable(path)
        os.unlink(_io(path))


def _rename_free(old, new):
    """Rename to a name nothing holds; never onto a file (os.rename refuses that
    on Windows by itself)."""
    if os.path.lexists(_io(new)):
        raise FileExistsError(f"a file is in the way: {new}")
    os.rename(_io(old), _io(new))


def _copy(source, target):
    """Copy `source` to `target` through a temp file next to it. Returns the
    (size, mtime_ns) the source had WHILE ITS BYTES WERE READ: that is what the
    state must record, not what the scan saw minutes earlier (a photo exported
    again in between left the album skipped for good). A source that changes
    during the read is an error; the next run copies it. The copy keeps the
    source's date and never its read-only attribute."""
    os.makedirs(_io(os.path.dirname(target)), exist_ok=True)
    tmp = target + TEMP_SUFFIX
    try:
        with open(_io(source), "rb") as reader:
            before = os.fstat(reader.fileno())
            with open(_io(tmp), "wb") as writer:
                shutil.copyfileobj(reader, writer)
                written = writer.tell()
            after = os.fstat(reader.fileno())
        if written != before.st_size or \
                (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise OSError(f"changed while it was copied: {source}")
        os.utime(_io(tmp), ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(_io(tmp), _io(target))
    except BaseException:
        try:
            _unlink_ours(tmp)
        except OSError:
            pass
        raise
    return before.st_size, before.st_mtime_ns


def _remove_empty_dirs(dest, rel_paths):
    for rel in sorted({os.path.dirname(r) for r in rel_paths}, key=len, reverse=True):
        current = os.path.join(dest, rel)
        while rel and _is_inside(current, dest) and \
                os.path.normcase(os.path.abspath(current)) != os.path.normcase(os.path.abspath(dest)):
            try:
                os.rmdir(_io(current))          # only an EMPTY folder; never a file
            except OSError:
                break
            current = os.path.dirname(current)


def cleanup_leftovers(dest, log):
    """Our own temp files from an interrupted run (only our suffixes), read-only
    or not. Returns how many could not be removed (each is named)."""
    problems = 0
    for current, dirs, files in os.walk(_io(dest)):
        dirs[:] = [d for d in dirs if d != STATE_FOLDER]
        for name in files:
            if name.endswith(TEMP_SUFFIX) or name.endswith(RENAME_SUFFIX):
                try:
                    _unlink_ours(os.path.join(current, name))
                except OSError as exc:
                    log.out(f"ERROR cannot remove the leftover {_plain(os.path.join(current, name))}: {exc}")
                    problems += 1
                    continue
                log.log(f"LEFTOVER removed {name}")
    return problems


def _save_interval(copied):
    """Copies between two saves of the state, after `copied` copies of this run.
    Every save rewrites the whole state: at a fixed interval a first run of N
    photos costs N*N/interval; this way it is a few dozen saves per tenfold."""
    if STATE_SAVE_SPREAD > 0:
        return max(STATE_SAVE_EVERY, copied // STATE_SAVE_SPREAD)
    return STATE_SAVE_EVERY


def execute(actions, photos, stats, shots, state, dest, log, unread=frozenset(), roots=None):
    """`unread`: photos whose shot time was not read (their state gets no
    "shot", so the next run reads them again); `roots`: each photo's source root."""
    errors = 0
    # 0. state entries with nothing behind them, or whose file is another photo's
    forgotten = 0
    for source in actions.get("forget", ()):
        if state.pop(source, None) is not None:
            forgotten += 1
            log.log(f"FORGET {source}")
    # 1. renames, step one: every renamed file leaves its old name first
    moving = []
    for source, old, new in actions["rename"]:
        old_path = os.path.join(dest, old)
        tmp = old_path + RENAME_SUFFIX
        try:
            os.replace(_io(old_path), _io(tmp))
        except OSError as exc:      # open in another program: it keeps its name and number
            log.out(f"ERROR rename {old}: {exc}")
            errors += 1
            continue
        moving.append((source, old, tmp, new))
    # 2. deletes (gone from the source, or replaced by a new copy under a new number)
    for source, old in actions["delete"]:
        path = os.path.join(dest, old)
        try:
            _unlink_ours(path)      # the plan proved it ours: in the state, with the size we wrote
            log.log(f"DELETE {old}")
        except OSError as exc:
            log.out(f"ERROR delete {old}: {exc}")
            errors += 1
            continue
        if source not in photos:
            state.pop(source, None)
    # 3. renames, step two
    for source, old, tmp, new in moving:
        target = os.path.join(dest, new)
        try:
            os.makedirs(_io(os.path.dirname(target)), exist_ok=True)
            _rename_free(tmp, target)
        except OSError as exc:
            log.out(f"ERROR rename {old} -> {new}: {exc}")
            errors += 1
            try:                    # back to the name the state knows
                _rename_free(tmp, os.path.join(dest, old))
            except OSError as back:
                log.out(f"ERROR {old} is left as {os.path.basename(tmp)}: {back}")
            continue
        state[source]["dest"] = new
        log.log(f"RENAME {new}")
    if moving or actions["delete"] or forgotten:
        save_state(dest, state)     # a kill after this point does not leave the old names (B13)
    # 4. copies
    copied = unsaved = 0
    for done, (source, rel_dest) in enumerate(actions["copy"], 1):
        target = os.path.join(dest, rel_dest)
        old = state.get(source)
        try:
            if old and os.path.normcase(old["dest"]) == os.path.normcase(rel_dest) and \
                    _is_read_only(target) and os.path.getsize(_io(target)) == old["size"]:
                _writable(target)   # our own read-only copy of this photo, about to be replaced
            result = _copy(source, target)
        except OSError as exc:
            log.out(f"ERROR copy {source}: {exc}")
            errors += 1
            continue
        # what was really copied; a stand-in for _copy that returns nothing
        # (the tests' kill) leaves what the scan saw
        size, mtime_ns = result or stats[source]
        rec = {"dest": rel_dest, "size": size, "mtime_ns": mtime_ns}
        if source in unread:
            pass                    # no "shot": read again on the next run
        elif (size, mtime_ns) != tuple(stats[source]):
            # changed between the scan and the copy: the shot time read is the
            # old file's, so it is read again too
            log.log(f"CHANGED since the scan, shot time read again on the next run: {source}")
        else:
            rec["shot"] = shots.get(source)
        if roots is not None and roots.get(source):
            rec["root"] = roots[source]
        state[source] = rec
        log.log(f"COPY {rel_dest}")
        copied += 1
        unsaved += 1
        if STATE_SAVE_EVERY > 0 and unsaved >= _save_interval(copied):
            save_state(dest, state)
            unsaved = 0
        if PROGRESS_EVERY > 0 and done % PROGRESS_EVERY == 0:
            print(f"  copied {done}/{len(actions['copy'])}")
    _remove_empty_dirs(dest, [old for _s, old in actions["delete"]] +
                       [old for _s, old, _n in actions["rename"]])
    return errors


# ---- Main ---------------------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        description="Mirror the phone derivatives (JXL_mobile folders) as one album per event, "
                    "numbered in shot order.")
    parser.add_argument("--source", action="append", default=None,
                        help=f"source root, repeatable or ';'-separated (default: SOURCES = {SOURCES!r})")
    parser.add_argument("--dest", default=DESTINATION or None,
                        help=f"destination root (default: DESTINATION = {DESTINATION!r})")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _sources(args):
    raw = args.source if args.source else [SOURCES]
    return [p.strip() for item in raw for p in item.split(";") if p.strip()]


def _dest_problem(dest):
    """Why `dest` cannot be the mirror, or None. Asked BEFORE the scan: a drive
    that is not connected must not cost the reading of every shot time first."""
    current = dest
    while True:
        if os.path.exists(_io(current)):
            if not os.path.isdir(_io(current)):
                return f"{current} is not a folder"
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return f"{current} does not answer (a drive that is not connected?)"
        current = parent


def match_state_case(state, photos):
    """Give a state entry the spelling its photo has in this run. The state is
    keyed by the source path as text, and the same folder typed in another case
    (g:\\2026 after G:\\2026) made every photo look new: all read again, all
    deleted and copied again. An entry moves only when exactly one entry has
    that path in another case and no photo of this run has the entry's own
    spelling. Returns how many moved."""
    folded = {}
    for key in state:
        folded.setdefault(os.path.normcase(key), []).append(key)
    moved = 0
    for source in photos:
        if source in state:
            continue
        keys = folded.get(os.path.normcase(source), [])
        if len(keys) == 1 and keys[0] not in photos:
            state[source] = state.pop(keys[0])
            moved += 1
    return moved


def run(args, log):
    sources = [os.path.abspath(s) for s in _sources(args)]
    if not sources or not args.dest:
        log.out("ERROR: give --source and --dest (or set SOURCES/DESTINATION at the top of the script).")
        return 2
    dest = os.path.abspath(args.dest)
    for source in sources:
        if not os.path.isdir(_io(source)):
            log.out(f"ERROR: source not found: {source}")
            return 2
        if _is_inside(dest, source) or _is_inside(source, dest):
            log.out(f"ERROR: the destination and a source contain each other: {dest} / {source}")
            return 2
    problem = _dest_problem(dest)
    if problem:
        log.out(f"ERROR: the destination cannot be used: {problem}")
        return 2
    meta = {}
    try:
        state = load_state(dest, meta)
    except (OSError, ValueError) as exc:
        log.out(f"ERROR: cannot read the state file {_state_path(dest)}: {exc}")
        return 2
    reread = forget_old_null_shots(state, meta.get("tool"))
    if reread:
        log.log(f"{reread} null shot time(s) of a state written by {meta.get('tool') or 'an older version'}: "
                f"read again")

    started = time.time()
    log.out(f"Scanning {len(sources)} source(s)...")
    read_errors = []
    roots = {}
    photos = find_photos(sources, read_errors, roots)
    stats = {}
    for source in list(photos):
        try:
            st = os.stat(_io(source))
        except OSError as exc:
            read_errors.append(f"{source}: {exc.strerror or exc}")
            del photos[source]
            continue
        stats[source] = (st.st_size, st.st_mtime_ns)
    respelled = match_state_case(state, photos)
    if respelled:
        log.log(f"{respelled} photo(s) of the state are spelled in another case in this run: the same photos")

    def unchanged(s):
        return s in state and state[s]["size"] == stats[s][0] and state[s]["mtime_ns"] == stats[s][1]

    # "shot" missing from the state = exiftool did not answer last time: read again
    need = [s for s in photos if not (unchanged(s) and "shot" in state[s])]
    needed = set(need)              # a list here was 9 s of lookups for 50 000 new photos
    shots = {s: state[s].get("shot") for s in photos if s not in needed}
    unread = set()
    if need:
        log.out(f"Reading the shot time of {len(need)} new or changed file(s)...")
        read = read_shot_times(need, log)
        unread = {s for s in need if s not in read}
        shots.update(read)
        for s in need:
            if s in read and unchanged(s):
                state[s]["shot"] = read[s]
    for source in photos:
        if source in state:
            state[source]["root"] = roots[source]

    actions = plan(photos, stats, shots, state, dest, sources)
    if read_errors:
        # A folder that could not be read looks empty: its photos would be
        # deleted, or renamed over. Only new photos go to free names this run.
        actions["delete"] = []
        actions["rename"] = []
        actions["forget"] = []      # a source not read in full proves nothing gone
        actions["copy"] = [(s, r) for s, r in actions["copy"]
                           if s not in state and not os.path.exists(_io(os.path.join(dest, r)))]
    albums = len(set(photos.values()))
    no_date = sum(1 for s in photos if not shots.get(s))
    copy_bytes = sum(stats[s][0] for s, _r in actions["copy"])
    log.out(f"{len(photos)} photo(s) in {albums} album(s) | copy {len(actions['copy'])} "
            f"({copy_bytes / 1e9:.2f} GB) | rename {len(actions['rename'])} | "
            f"delete {len(actions['delete'])} | unchanged {len(actions['keep'])} | "
            f"without a shot date {no_date}")
    for rel in actions["conflict"]:
        log.out(f"SKIPPED album {rel}: a file this job did not write is in the way")
    for rel in actions["kept_changed"]:
        log.out(f"LEFT ALONE {rel}: changed since this job wrote it")
    if actions["elsewhere"]:
        log.out(f"{len(actions['elsewhere'])} photo(s) of sources not given in this run "
                f"were left alone")
    for error in read_errors:
        log.out(f"ERROR cannot read {error}")
    if read_errors:
        log.out(f"{len(read_errors)} folder(s)/file(s) of the sources could not be read: "
                f"nothing is deleted or renamed in this run, only new photos are copied.")
    if unread:
        log.out(f"WARNING the shot time of {len(unread)} file(s) was not read (exiftool did "
                f"not answer): numbered by name for now, read again on the next run.")
    problems = 1 if read_errors or unread else 0
    if args.dry_run:
        for source, rel_dest in actions["copy"]:
            log.log(f"would COPY {rel_dest}")
        for _s, old, new in actions["rename"]:
            log.log(f"would RENAME {old} -> {new}")
        for _s, old in actions["delete"]:
            log.log(f"would DELETE {old}")
        for source in actions["forget"]:
            log.log(f"would FORGET {source}")
        log.out("Dry run: nothing changed.")
        # the exit code of the real run this plan gives: a skipped album is 1 there too
        return 1 if problems or actions["conflict"] else 0

    try:
        os.makedirs(_io(dest), exist_ok=True)
        free = shutil.disk_usage(_io(dest)).free
    except OSError as exc:
        log.out(f"ERROR: the destination cannot be used: {dest}: {exc}")
        return 2
    if copy_bytes + SPACE_MARGIN_GB * 1e9 > free:
        log.out(f"ERROR: not enough space on {dest}: {copy_bytes / 1e9:.2f} GB to copy, "
                f"{free / 1e9:.2f} GB free, margin {SPACE_MARGIN_GB} GB")
        return 2
    errors = cleanup_leftovers(dest, log)
    try:
        try:
            errors += execute(actions, photos, stats, shots, state, dest, log, unread, roots)
        finally:
            save_state(dest, state)
    except OSError as exc:
        # only the state file raises here (every copy, rename and delete has its
        # own error line): a destination that went away in the middle
        log.out(f"ERROR: the run stopped, the state file could not be written: {exc}")
        return 1
    log.out(f"Done in {time.time() - started:.0f}s: {errors} error(s), "
            f"{len(actions['conflict'])} album(s) skipped.")
    return 1 if errors or actions["conflict"] or problems else 0


def main(argv=None):
    try:                        # a cp1252 console cannot print the owner's Japanese paths
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    args = _build_parser().parse_args(argv)
    stamp = time.strftime("%y%m%d_%H%M%S")
    try:
        log = Logger(os.path.join(LOG_DIR, f"{stamp}_mobile.log"))
    except OSError as exc:
        print(f"cannot write the log directory {LOG_DIR}: {exc}", file=sys.stderr)
        return 2
    try:
        log.log("mobile_backup " + __version__)
        log.log("command: " + " ".join(sys.argv if argv is None else ["mobile_backup.py"] + list(argv)))
        try:
            return run(args, log)
        except KeyboardInterrupt:
            log.out("Interrupted.")
            return 130
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
