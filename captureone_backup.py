#!/usr/bin/env python3
"""captureone_backup - back up and restore what Capture One itself keeps.

The edits live in the .cos files beside the photos (selective_backup.py copies
those), but they only NAME their ICC profiles, film curves and styles. Without
those files an edit is shown differently, so this script keeps them too, read
from where Capture One really has them:

    UserData      %LOCALAPPDATA%\\CaptureOne (profiles, styles, defaults, presets,
                  recipes, shortcuts, workspaces, ...)
    Preferences   %LOCALAPPDATA%\\Capture_One (user.config of every version)
    Installed     <install folder>\\Color Profiles and Film Curves
    WindowsColor  the .icc/.icm files of the Windows colour folder

Login data, cookies, logs and the licence are left out (settings below). The
backup is a folder of its own, <dest>\\_captureone, with its own catalog.

    python captureone_backup.py --dest H:\\BACKUP [--dry-run]
    python captureone_backup.py --verify H:\\BACKUP
    python captureone_backup.py --restore-from H:\\BACKUP [--dry-run]
    python captureone_backup.py --restore-from H:\\BACKUP --also _Instalador --to D:\\Out
    python captureone_backup.py --restore-from H:\\BACKUP --force [--force-installed]
    python captureone_backup.py --revert-last

Sources are READ-ONLY. Nothing in the backup is deleted: a changed file's old
copy goes to the versions folder first. A restore never overwrites a file;
only --force does, and it first keeps the current file in an old_<time> folder
that --revert-last puts back.
"""

import argparse
import fnmatch
import json
import math
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selective_backup as sb

__version__ = "0.13.2"

# ---- Settings -------------------------------------------------------------------
BACKUP_SUBFOLDER = "_captureone"            # under --dest: this script's own backup root
MAP_FILE = "WHERE_EACH_FOLDER_GOES.txt"     # under BACKUP_SUBFOLDER: readable map of the original places
RESTORE_SCRIPT = "RESTORE_CAPTUREONE.cmd"   # under BACKUP_SUBFOLDER: double-click restore for a new PC (no Python)
INSTALLER_FOLDER = "_Instalador"            # folder of UserData where the owner keeps the installer + serial

OLD_PARENT_FOLDER = "CaptureOne_old"        # under %LOCALAPPDATA%: what --force and --revert-last replaced
OLD_PREFIX = "old_"                         # + date_time_ms: a --force snapshot, the one --revert-last uses
REVERT_PREFIX = "revert_old_"               # + date_time_ms: what a --revert-last replaced (never picked as 'last')
DISABLED_SUFFIX = "_not"                    # rename an old_ folder to end with this and --revert-last skips it
JOURNAL_FILE = "journal.jsonl"              # in each snapshot: one line per file replaced or added
ELEVATED_ARGUMENT = "--elevated"            # RESTORE_SCRIPT gives it to the instance it starts as administrator
SUMMARY_LINK_LINES = 10                     # links/junctions not followed named on the screen (the log has all)

USER_FOLDER_NAME = "CaptureOne"             # under %LOCALAPPDATA%
PREFERENCES_FOLDER_NAME = "Capture_One"     # under %LOCALAPPDATA%
REGISTRY_DISPLAY_NAME = "Capture One"       # the installed program's name in Windows' uninstall list
DEFAULT_INSTALL_FOLDER = r"C:\Program Files\Capture One\Capture One"  # when the registry does not say
INSTALL_DATA_FOLDERS = "Color Profiles;Film Curves"     # ';'-separated, inside the install folder
WINDOWS_COLOR_FOLDER = r"System32\spool\drivers\color"  # under %SystemRoot%
WINDOWS_COLOR_EXTENSIONS = (".icc", ".icm")
PROCESS_NAMES = "CaptureOne.exe"            # ';'-separated: the restore refuses to run while one is open

# ';'-separated, any case, '*' allowed. Folder numbers change with Capture One
# versions (Styles50, Recipes165), so nothing below may name one exactly.
EXCLUDE_TOP_FOLDERS = "Logs;Stability;MSALCache*;*WebView*;*UserData*"  # login data, cookies, logs
EXCLUDE_ANY_FOLDER = "EBWebView"            # browser profile, at any depth
EXCLUDE_FILES = "license*.enc;*" + sb.TEMP_SUFFIX

# Inside UserData, the folders a restore brings back without being asked.
# Everything else (a Batch queue, ImageCore, Plugins, _Instalador, ...) is
# restored only with --also.
RESTORE_USER_FOLDERS = ("Color Profiles;Styles*;StyleBrushes;Defaults;Presets*;Recipes*;"
                        "CustomCommands*;Workspaces*;KeywordLibraries;SmartAdjustments;"
                        "SearchPresets;GuideSets;PrintTemplates;Templates")

LABEL_USER = "UserData"
LABEL_PREFERENCES = "Preferences"
LABEL_INSTALLED = "Installed"
LABEL_WINDOWS = "WindowsColor"
PROTECTED_LABELS = (LABEL_INSTALLED, LABEL_WINDOWS)     # writing there needs an administrator

BAD_RESTORE = ("CONFLICT", "NEEDS_ADMIN", "NOT_INSTALLED", "NO_TARGET", "MISSING_IN_BACKUP",
               "CORRUPT_IN_BACKUP", "ERROR")
BAD_REVERT = ("NEEDS_ADMIN", "MISSING_IN_SNAPSHOT", "NO_TARGET", "CONFLICT", "ERROR")


# ---- Where Capture One keeps things ------------------------------------------------
def _local_appdata():
    return os.environ.get("PHOTOBACKUP_C1_LOCALAPPDATA") or os.environ.get("LOCALAPPDATA") or ""


def _windows_color_folder():
    override = os.environ.get("PHOTOBACKUP_C1_WINDOWS_COLOR")
    if override:
        return override
    system_root = os.environ.get("SystemRoot") or ""
    return os.path.join(system_root, WINDOWS_COLOR_FOLDER) if system_root else ""


def _registry_install():
    """(install folder, version) from Windows' uninstall list; (None, None) if not listed."""
    try:
        import winreg
    except ImportError:
        return None, None
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, uninstall) as parent:
            count = winreg.QueryInfoKey(parent)[0]
            for index in range(count):
                try:
                    with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as key:
                        name = winreg.QueryValueEx(key, "DisplayName")[0]
                        if str(name).casefold() != REGISTRY_DISPLAY_NAME.casefold():
                            continue
                        folder = winreg.QueryValueEx(key, "InstallLocation")[0]
                        version = winreg.QueryValueEx(key, "DisplayVersion")[0]
                        return str(folder).rstrip("\\/"), str(version)
                except OSError:
                    continue
    except OSError:
        pass
    return None, None


def _install_folder(explicit):
    """(install folder, version or None). --install-folder wins, then the registry."""
    if explicit:
        return os.path.abspath(explicit), None
    override = os.environ.get("PHOTOBACKUP_C1_INSTALL")
    if override:
        return override, None
    folder, version = _registry_install()
    if folder:
        return folder, version
    return DEFAULT_INSTALL_FOLDER, None


def _roots(args):
    """label -> folder on this machine, and the installed version (or None)."""
    appdata = _local_appdata()
    install, version = _install_folder(args.install_folder)
    return {
        LABEL_USER: os.path.join(appdata, USER_FOLDER_NAME) if appdata else "",
        LABEL_PREFERENCES: os.path.join(appdata, PREFERENCES_FOLDER_NAME) if appdata else "",
        LABEL_INSTALLED: install,
        LABEL_WINDOWS: _windows_color_folder(),
    }, version


def _is_admin():
    if os.name != "nt":
        return False
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def _captureone_running():
    """True / False, or None when Windows could not say."""
    if os.name != "nt":
        return False
    wanted = sb._split_names(PROCESS_NAMES)
    try:
        output = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                                text=True, errors="replace", timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if not output.strip():
        return None
    folded = output.casefold()
    return any('"' + name.casefold() + '"' in folded for name in wanted)


# ---- Planning ------------------------------------------------------------------------
@dataclass
class _Plan:
    files: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    links: list = field(default_factory=list)   # links/junctions not followed (paths)
    found: list = field(default_factory=list)
    unread: list = field(default_factory=list)  # rels (a folder or a file) that could not be read


def _matches(name, patterns):
    folded = name.casefold()
    return any(fnmatch.fnmatchcase(folded, pattern.casefold()) for pattern in patterns)


def _walk(root, rel_prefix, plan, top_exclude=(), keep_extensions=None, recurse=True):
    """Add every file under `root` to the plan; junctions and links are never followed."""
    any_folder = sb._split_names(EXCLUDE_ANY_FOLDER)
    exclude_files = sb._split_names(EXCLUDE_FILES)
    stack = [(root, rel_prefix, True)]
    while stack:
        folder, rel, is_top = stack.pop()
        try:
            with os.scandir(sb._io(folder)) as iterator:
                entries = list(iterator)
        except OSError as exc:
            plan.errors.append((folder, str(exc)))
            plan.unread.append(rel)
            continue
        for entry in entries:
            path = os.path.join(folder, entry.name)
            child = os.path.join(rel, entry.name)
            try:
                if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                    if not _link_left_out(entry, is_top, recurse, any_folder, top_exclude,
                                          exclude_files, keep_extensions):
                        plan.links.append(path)
                elif entry.is_dir(follow_symlinks=False):
                    if not recurse or _matches(entry.name, any_folder):
                        continue
                    if is_top and _matches(entry.name, top_exclude):
                        continue
                    stack.append((path, child, False))
                elif entry.is_file(follow_symlinks=False):
                    if _matches(entry.name, exclude_files):
                        continue
                    if (keep_extensions is not None
                            and os.path.splitext(entry.name)[1].casefold() not in keep_extensions):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    plan.files.append(sb.PlannedFile(path, child, stat.st_size, stat.st_mtime_ns,
                                                     sb.CAT_CAPTURE_ONE))
            except OSError as exc:
                plan.errors.append((path, str(exc)))
                plan.unread.append(child)


def _link_is_folder(entry):
    """Whether a link or junction stands for a folder, without following it:
    a junction always does; a symbolic link carries the folder attribute."""
    if getattr(entry, "is_junction", lambda: False)():
        return True
    try:
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & 0x10)          # FILE_ATTRIBUTE_DIRECTORY


def _link_left_out(entry, is_top, recurse, any_folder, top_exclude, exclude_files,
                   keep_extensions):
    """Whether a link or junction stands where _walk would not go anyway: an
    excluded folder (Logs, EBWebView, ...), a subfolder of a folder that is
    not walked below (the colour folder), a file of EXCLUDE_FILES or of
    another extension. Such a link is not something missing from the backup,
    and is not reported as one (T11 of the 261112 sweep)."""
    if _link_is_folder(entry):
        return (not recurse or _matches(entry.name, any_folder)
                or (is_top and _matches(entry.name, top_exclude)))
    return (_matches(entry.name, exclude_files)
            or (keep_extensions is not None
                and os.path.splitext(entry.name)[1].casefold() not in keep_extensions))


def _folder_there(path, rel, plan):
    """Whether a folder to back up is there. One that cannot be reached (access
    denied, a drive that does not answer) is an error of this run and its rows
    are 'not read', never 'gone': only proof makes a source missing."""
    if not path:
        return False
    state = sb._source_state(path)
    if state == sb.SOURCE_UNREADABLE:
        plan.errors.append((path, "cannot be reached (not proven missing)"))
        plan.unread.append(rel)
    return state == sb.SOURCE_PRESENT


def _build_plan(roots):
    plan = _Plan()
    for label in (LABEL_USER, LABEL_PREFERENCES):
        if _folder_there(roots[label], label, plan):
            plan.found.append(label)
            top = sb._split_names(EXCLUDE_TOP_FOLDERS) if label == LABEL_USER else ()
            _walk(roots[label], label, plan, top_exclude=top)
    install = roots[LABEL_INSTALLED]
    if _folder_there(install, LABEL_INSTALLED, plan):
        for name in sb._split_names(INSTALL_DATA_FOLDERS):
            folder = os.path.join(install, name)
            if _folder_there(folder, os.path.join(LABEL_INSTALLED, name), plan):
                if LABEL_INSTALLED not in plan.found:
                    plan.found.append(LABEL_INSTALLED)
                _walk(folder, os.path.join(LABEL_INSTALLED, name), plan)
    color = roots[LABEL_WINDOWS]
    # The colour folder is only worth keeping next to a Capture One that was found.
    if plan.found and _folder_there(color, LABEL_WINDOWS, plan):
        plan.found.append(LABEL_WINDOWS)
        _walk(color, LABEL_WINDOWS, plan, keep_extensions=WINDOWS_COLOR_EXTENSIONS, recurse=False)
    return plan


def _not_read(key, plan):
    """Whether a catalog key is at or under something this run could not read."""
    for rel in plan.unread:
        folded = rel.casefold()
        if key == folded or key.startswith(folded + os.sep):
            return True
    return False


def _link_lines(plan, log):
    """Say what was not followed: what is behind a link is NOT in this backup."""
    if not plan.links:
        return
    log.out(f"Links/junctions not followed: {sb._num(len(plan.links))} "
            f"(what is behind them is NOT in this backup)")
    for index, link in enumerate(sorted(plan.links, key=str.casefold)):
        if index < SUMMARY_LINK_LINES:
            log.out(f"  LINK not followed {link}")
        else:
            log.log(f"  LINK not followed {link}")
    if len(plan.links) > SUMMARY_LINK_LINES:
        print(f"  ... {sb._num(len(plan.links) - SUMMARY_LINK_LINES)} more in the log")


def _confirm(prompt, log):
    try:
        answer = input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("Aborted.")
        log.log("Aborted (no answer)")
        return False
    if answer.strip().casefold() not in ("y", "yes", "s", "sim"):
        print("Aborted.")
        log.log("Aborted by the user")
        return False
    return True


def _held_origins(rows):
    """label -> the folder its files came from, for what the backup holds (the
    catalog's rows): the source path of a row without the part below the label."""
    origins = {}
    for row in rows:
        label, _sep, rest = row.rel.partition(os.sep)
        if not rest or label in origins:
            continue
        source = row.source_path or ""
        tail = os.sep + rest
        origins[label] = (source[:-len(tail)] if source.casefold().endswith(tail.casefold())
                          else "")
    return origins


def _write_map(backup, roots, version, plan, rows=None):
    """The readable answer to 'where did each thing come from?' (rewritten each
    run). `rows`: the catalog's rows; a part the backup holds is named even when
    this run did not find it on this computer (None = only what this run found)."""
    what = {
        LABEL_USER: "profiles, styles, defaults, presets, recipes, shortcuts, workspaces, ...",
        LABEL_PREFERENCES: "user.config of each Capture One version (restored only with --also)",
        LABEL_INSTALLED: "Color Profiles and Film Curves of the installed program",
        LABEL_WINDOWS: "the .icc/.icm files of the Windows colour folder",
    }
    lines = [f"Capture One files kept by captureone_backup.py {__version__}",
             f"Written: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"Capture One version: {version or 'unknown'}",
             "",
             "Folder here -> where it came from on this computer:"]
    origins = _held_origins(rows or [])
    for label in what:
        if label in plan.found:
            lines.append(f"  {label:<13} -> {roots[label]}")
        elif label in origins:
            lines.append(f"  {label:<13} -> {origins[label] or 'unknown'}"
                         f"  (not found there in the last run; kept from an earlier one)")
        else:
            continue
        lines.append(f"  {'':<13}    {what[label]}")
    lines += ["",
              "On a new computer:",
              f"  1. Install Capture One ({LABEL_USER}\\{INSTALLER_FOLDER} holds the installer and the serial).",
              f"  2. BEFORE opening it, double-click {RESTORE_SCRIPT} (no Python needed).",
              "  3. Open Capture One and activate it.",
              "Nothing that already exists is overwritten.",
              "",
              "With Python, the same restore checks every file against the catalog:",
              "  python captureone_backup.py --restore-from <the backup folder> --dry-run",
              ""]
    _write_ours(os.path.join(backup, MAP_FILE), "\n".join(lines))


def _write_ours(target, text, newline=None):
    """Write one of this script's own files in the backup (the map, the
    double-click restore) through a temp file next to it. A read-only old copy
    -- ours, rewritten every run -- is made writable first, and a failed write
    leaves no temp behind (T7 of the 261112 sweep); the caller reports it."""
    tmp = target + sb.TEMP_SUFFIX
    sb._remove_temp(tmp)                # ours, left by a run that died
    try:
        with open(sb._io(tmp), "w", encoding="utf-8", newline=newline) as handle:
            handle.write(text)
        _make_writable(target)
        os.replace(sb._io(tmp), sb._io(target))
    except BaseException:
        sb._remove_temp(tmp)
        raise


def _cmd_escape(value):
    """A literal for a .cmd file: a '%' would otherwise start a variable."""
    return value.replace("%", "%%")


def _cmd_install_target(install):
    """The install folder as the new computer spells it: %ProgramFiles% is not always C:."""
    program_files = os.environ.get("ProgramFiles") or ""
    if program_files and sb._is_under(install, program_files):
        return "%ProgramFiles%" + _cmd_escape(install[len(program_files.rstrip("\\/")):])
    return _cmd_escape(install)


def _write_restore_script(backup, roots, plan, rels=None):
    """RESTORE_SCRIPT: everything a new computer needs, with nothing but Windows.
    robocopy with /XC /XN /XO only ever adds missing files. No 'call' and no
    (...) block around a path: both break on a '%' or a ')' in the backup's path.
    `rels`: what the backup holds (the catalog's rows after this run), so a run
    that did not find a folder on this computer does not drop it from the
    script (None = this run's plan). One of our own temp files left by a run
    that died is never copied (/XF). The instance started as administrator
    gets ELEVATED_ARGUMENT and never asks for elevation again, whatever the
    administrator test answers."""
    no_overwrite = (f"/XF *{_cmd_escape(sb.TEMP_SUFFIX)} "
                    "/XC /XN /XO /R:1 /W:1 /NP /NDL /NFL /NJH /NJS")
    exe = sb._split_names(PROCESS_NAMES)[0]
    restored = sb._split_names(RESTORE_USER_FOLDERS)
    user_folders = {}
    install_folders = {}
    has_color = False
    for rel in ([item.rel for item in plan.files] if rels is None else rels):
        parts = rel.split(os.sep)
        if parts[0] == LABEL_USER and len(parts) >= 3 and _matches(parts[1], restored):
            user_folders.setdefault(parts[1].casefold(), parts[1])
        elif parts[0] == LABEL_INSTALLED and len(parts) >= 3:
            install_folders.setdefault(parts[1].casefold(), parts[1])
        elif parts[0] == LABEL_WINDOWS and len(parts) >= 2:
            has_color = True
    user_folders = set(user_folders.values())
    install_folders = set(install_folders.values())
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal",
        'set "HERE=%~dp0"',
        'set "SELF=%~f0"',
        f'if not defined C1_INSTALL_TARGET set "C1_INSTALL_TARGET={_cmd_install_target(roots[LABEL_INSTALLED])}"',
        f'if not defined C1_COLOR_TARGET set "C1_COLOR_TARGET=%SystemRoot%\\{_cmd_escape(WINDOWS_COLOR_FOLDER)}"',
        'if /i "%~1"=="--unattended" goto :installed',
        f'if /i "%~1"=="{ELEVATED_ARGUMENT}" goto :checks',
        "net session >nul 2>&1",
        "if not errorlevel 1 goto :checks",
        "echo Asking Windows for administrator rights...",
        'powershell -NoProfile -Command "$q=[char]34; Start-Process -FilePath $env:ComSpec '
        f"-Verb RunAs -ArgumentList ('/c '+$q+$q+$env:SELF+$q+' {ELEVATED_ARGUMENT}'+$q)\"",
        "if not errorlevel 1 exit /b 0",
        "echo Windows did not give administrator rights: nothing was restored.",
        "goto :fail",
        ":checks",
        f'tasklist /FI "IMAGENAME eq {exe}" 2>nul | find /i "{exe}" >nul',
        "if errorlevel 1 goto :installed",
        "echo Close Capture One first, then run this again.",
        "goto :fail",
        ":installed",
        f'if exist "%C1_INSTALL_TARGET%\\{exe}" goto :copy',
        "echo Capture One is not installed yet. Install it first, from:",
        f'echo   "%HERE%{LABEL_USER}\\{_cmd_escape(INSTALLER_FOLDER)}"',
        "echo Then run this again, before opening Capture One.",
        "goto :fail",
        ":copy",
        'set "FAILED=0"',
        "echo Restoring Capture One's profiles, styles and settings...",
    ]
    for name in sorted(user_folders, key=str.casefold):
        safe = _cmd_escape(name)
        lines += [
            f'echo   "{safe}"',
            f'robocopy "%HERE%{LABEL_USER}\\{safe}" '
            f'"%LOCALAPPDATA%\\{_cmd_escape(USER_FOLDER_NAME)}\\{safe}" /E {no_overwrite}',
            'if errorlevel 8 set "FAILED=1"',
        ]
    for name in sorted(install_folders, key=str.casefold):
        safe = _cmd_escape(name)
        lines += [
            f'echo   "{safe}"',
            f'robocopy "%HERE%{LABEL_INSTALLED}\\{safe}" "%C1_INSTALL_TARGET%\\{safe}" /E {no_overwrite}',
            'if errorlevel 8 set "FAILED=1"',
        ]
    if has_color:
        patterns = " ".join("*" + ext for ext in WINDOWS_COLOR_EXTENSIONS)
        lines += [
            'echo   "Windows colour profiles"',
            f'robocopy "%HERE%{LABEL_WINDOWS}" "%C1_COLOR_TARGET%" {patterns} {no_overwrite}',
            'if errorlevel 8 set "FAILED=1"',
        ]
    lines += [
        'if "%FAILED%"=="0" goto :ok',
        "echo Some files could not be copied: see the messages above.",
        "goto :fail",
        ":ok",
        "echo Done. Nothing that already existed was changed. Open Capture One and activate it.",
        'set "CODE=0"',
        "goto :finish",
        ":fail",
        'set "CODE=1"',
        ":finish",
        'if /i not "%~1"=="--unattended" pause',
        "exit /b %CODE%",
        "",
    ]
    _write_ours(os.path.join(backup, RESTORE_SCRIPT), "\n".join(lines), newline="\r\n")


# ---- Back up -------------------------------------------------------------------------------
def run_backup(args, log, stamp):
    dest = os.path.abspath(args.dest)
    backup = _backup_root(dest)
    drive = os.path.splitdrive(dest)[0]
    if drive and not os.path.exists(drive + os.sep):
        print(f"Drive {drive} is not connected.", file=sys.stderr)
        return 2
    roots, version = _roots(args)
    for root in roots.values():
        # as typed and where they really are: a --dest through a junction or a
        # subst letter must not land in a Capture One folder (T4, 261112 sweep)
        if root and sb._overlaps(backup, root):
            print(f"The backup cannot overlap a Capture One folder: {backup} vs {root}"
                  + sb._really(backup, root), file=sys.stderr)
            return 2
    damaged = _catalog_problem(backup)
    if damaged is not None:
        print(damaged, file=sys.stderr)
        log.log(f"ERROR {damaged}")
        return 2
    plan = _build_plan(roots)
    if not plan.files:
        log.out("Capture One settings: nothing found on this computer, nothing to do.")
        for path, message in plan.errors:
            log.out(f"Could not read {path}: {message}")
        _link_lines(plan, log)
        return 1 if plan.errors else 0
    # one listing per folder of the backup, the same answers (O5, 261112 sweep)
    statuses = sb._file_statuses(plan.files, backup)
    new = [s for s in statuses if s.status == sb.ST_NEW]
    changed = [s for s in statuses if s.status == sb.ST_CHANGED]
    to_copy = new + changed
    needed = sum(s.planned.size for s in to_copy)
    margin = int(float(args.space_margin_gb) * 1e9)
    try:
        free = shutil.disk_usage(sb._io(sb._existing_ancestor(dest))).free
    except OSError as exc:
        print(f"Cannot read the free space of {dest}: {exc}", file=sys.stderr)
        return 2
    fits = needed <= free - margin
    if _captureone_running():
        log.out("Warning: Capture One is open; files it is writing may fail or be caught "
                "half-way. Close it for a clean copy.")
    log.out(f"captureone_backup {__version__}")
    log.out(f"Backup:    {backup}")
    log.out(f"Capture One version: {version or 'unknown'}")
    for label in plan.found:
        count = sum(1 for item in plan.files if item.rel.startswith(label + os.sep))
        log.out(f"  {label:<13} {sb._num(count):>7} files  from {roots[label]}")
    log.out(f"Plan:      new {sb._num(len(new))}, changed {sb._num(len(changed))}, "
            f"up to date {sb._num(len(statuses) - len(to_copy))}; copy {sb._gb(needed)}")
    log.out(f"Space:     free {sb._gb(free)}, margin {sb._gb(margin)} -> "
            + ("fits" if fits else "DOES NOT FIT"))
    for path, message in plan.errors:
        log.out(f"Could not read {path}: {message}")
    _link_lines(plan, log)
    if not fits and not args.dry_run:
        print("Not enough space; nothing copied.", file=sys.stderr)
        log.log("Not enough space; nothing copied.")
        return 2
    if args.dry_run:
        log.log("dry run: nothing copied")
        return 1 if plan.errors or not fits else 0
    if not args.yes and to_copy and not _confirm(f"Copy {sb._num(len(to_copy))} files? [y/N] ", log):
        return 2
    try:
        os.makedirs(sb._io(backup), exist_ok=True)
        catalog = sb._Catalog(backup, create=True)
    except (RuntimeError, OSError) as exc:
        print(f"Cannot use {backup} as the backup folder: {exc}", file=sys.stderr)
        log.log(f"Cannot use {backup} as the backup folder: {exc}; nothing copied")
        return 2
    failed = []
    versions = 0
    started = False
    try:
        for status in statuses:
            if status.status == sb.ST_UP_TO_DATE:
                catalog.adopt(status.planned)
        catalog.commit()
        last = time.time()
        for index, status in enumerate(to_copy, 1):
            started = True
            try:
                before_final = None
                if status.status == sb.ST_CHANGED:
                    # selective_backup's own rule (B12): the old copy keeps its
                    # name until the new one is on the disk, and goes to the
                    # versions folder only when the bytes really copied differ
                    # (Capture One rewrites files with the same bytes).
                    decide = sb._handle_old_copy(backup, stamp, status, log)

                    def old_copy(digest, decide=decide, status=status):
                        nonlocal versions
                        decide(digest)
                        if not sb._exists(status.dst):
                            versions += 1
                    before_final = old_copy
                digest, size, mtime_ns = sb._copy_file(status.planned, status.dst, sb.VERIFY_COPY,
                                                       before_final=before_final)
                catalog.upsert(status.planned, digest, size, mtime_ns)
                log.log(f"{status.status} {status.planned.rel}")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                failed.append((status.planned.rel, str(exc)))
                log.log(f"ERROR {status.planned.rel}: {exc}")
            if time.time() - last >= sb.PROGRESS_EVERY_S:
                last = time.time()
                catalog.commit()
                print(f"  {sb._num(index)}/{sb._num(len(to_copy))} files", flush=True)
        catalog.commit()
        # what the backup holds after this run: the map and the double-click
        # restore are made from it, not from what this run happened to find
        rows = catalog.all_rows()
    except KeyboardInterrupt:
        try:
            catalog.commit()
        except sqlite3.Error:
            pass
        print("Interrupted.", file=sys.stderr)
        log.log("Interrupted.")
        return 130
    except sqlite3.Error as exc:
        print(f"The catalog of {backup} cannot be read or written: {exc}. Stopped.",
              file=sys.stderr)
        log.log(f"Stopped: catalog error: {exc}")
        return 1 if started else 2
    finally:
        catalog.close()
    keys = {item.rel.casefold() for item in plan.files}
    absent = [row for row in rows if row.key not in keys]
    # a folder that could not be read this run is not a folder that is gone
    not_read = [row for row in absent if _not_read(row.key, plan)]
    gone = [row for row in absent if not _not_read(row.key, plan)]
    # The double-click restore first (it is what a new computer needs), then
    # the map. Neither may end the run with a traceback after the copy: an
    # error is said, and the run ends with 1 (T7 of the 261112 sweep).
    write_errors = []
    for name, write in ((RESTORE_SCRIPT, lambda: _write_restore_script(
                            backup, roots, plan, [row.rel for row in rows])),
                        (MAP_FILE, lambda: _write_map(backup, roots, version, plan, rows))):
        try:
            write()
        except OSError as exc:
            write_errors.append((name, exc))
            log.log(f"ERROR {name} not written: {exc}")
    log.csv("_gone.csv", ["rel", "size", "source_path"],
            [(row.rel, row.size, row.source_path) for row in gone])
    log.out(f"Result:    copied {sb._num(len(to_copy) - len(failed))}, "
            f"old copies kept in versions {sb._num(versions)}, failed {sb._num(len(failed))}, "
            f"kept although gone from Capture One {sb._num(len(gone))}")
    if not_read:
        log.out(f"  {sb._num(len(not_read))} files of the backup are under a folder that could "
                f"not be read in this run: kept, and not counted as gone")
    for rel, message in failed:
        log.out(f"  FAILED {rel}: {message}")
    for name, exc in write_errors:
        log.out(f"  ERROR {os.path.join(backup, name)} could not be written ({exc}): the copied "
                f"files are in the backup; run the backup again to write it")
    return 1 if failed or plan.errors or write_errors else 0


def _catalog_problem(backup):
    """Why the catalog that is already there cannot be used (damaged, a path
    that leads outside the backup, not ours), or None -- also when there is
    none yet. Checked before the plan, so a --dry-run (what the menu shows
    before the copy) never says 'fine' about a catalog the copy then refuses
    (T9 of the 261112 sweep)."""
    try:
        catalog = sb._Catalog(backup, create=False, readonly=True)
    except FileNotFoundError:
        return None
    except RuntimeError as exc:
        return str(exc)
    catalog.close()
    return None


# ---- Verify ---------------------------------------------------------------------------------
def _backup_root(path):
    """PATH\\_captureone; a PATH that already is that folder is used as it is."""
    path = os.path.abspath(path)
    if os.path.basename(path.rstrip("\\/")).casefold() == BACKUP_SUBFOLDER.casefold():
        return path
    return os.path.join(path, BACKUP_SUBFOLDER)


def _open_backup(path):
    """(backup folder, rows), or raises FileNotFoundError / RuntimeError."""
    backup = _backup_root(path)
    catalog = sb._Catalog(backup, create=False, readonly=True)
    try:
        return backup, catalog.all_rows()
    except sqlite3.Error as exc:        # a damaged catalog: a message, not a traceback
        raise RuntimeError(f"cannot read catalog {catalog.path}: {exc}") from exc
    finally:
        catalog.close()


GOOD_VERIFY = ("OK", "HASH_FILLED", "OK_SIZE_ONLY")


def _current_source(row, roots):
    """Where the file of a catalog row is on THIS computer (the label resolved
    here, as the restore does), or None."""
    label, _sep, rest = row.rel.partition(os.sep)
    root = roots.get(label)
    return os.path.join(root, rest) if root and rest else None


def _unhashed_status(row, path, roots):
    """(status, sha256) for a row without a catalog hash (B11). Its source still
    there with the size and date of the row proves what the copy must hold:
    the same bytes fill the hash (HASH_FILLED), other bytes are DEST_DIFFERS.
    Without that proof only the size was checked: OK_SIZE_ONLY."""
    source = _current_source(row, roots)
    if source is None:
        return "OK_SIZE_ONLY", None
    tolerance = int(sb.MTIME_TOLERANCE_S * 1_000_000_000)
    try:
        stat = os.stat(sb._io(source), follow_symlinks=False)
        if stat.st_size != row.size or abs(stat.st_mtime_ns - row.mtime_ns) > tolerance:
            return "OK_SIZE_ONLY", None
        source_hash = sb._sha256_file(source)
        if os.stat(sb._io(source), follow_symlinks=False).st_mtime_ns != stat.st_mtime_ns:
            return "OK_SIZE_ONLY", None         # changed while it was read
    except OSError:
        return "OK_SIZE_ONLY", None
    copy_hash = sb._sha256_file(path)           # an error here is the backup's: ERROR
    if copy_hash == source_hash:
        return "HASH_FILLED", copy_hash
    return "DEST_DIFFERS", None


def run_verify(args, log):
    try:
        backup, rows = _open_backup(args.verify)
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    roots, _version = _roots(args)
    counts = {}
    report = []
    filled = []
    for row in rows:
        path = os.path.join(backup, row.rel)
        try:
            size = os.stat(sb._io(path), follow_symlinks=False).st_size
            if size != row.size:
                status = "DEST_SIZE"
            elif row.sha256 is None:
                status, new_hash = _unhashed_status(row, path, roots)
                if new_hash is not None:
                    filled.append((row.key, new_hash))
            else:
                status = "OK" if sb._sha256_file(path) == row.sha256 else "CORRUPT"
        except FileNotFoundError:
            status = "MISSING"
        except OSError as exc:
            status = "ERROR"
            log.log(f"ERROR {row.rel}: {exc}")
        counts[status] = counts.get(status, 0) + 1
        if status == "DEST_DIFFERS":
            log.log(f"DEST_DIFFERS {row.rel}: the copy differs from its source, which has the "
                    f"size and date of the catalog")
        if status not in GOOD_VERIFY:
            report.append((status, row.rel))
            log.out(f"  {status} {row.rel}")
    not_recorded = None
    if filled:
        # Only a row proven by its source gets a hash; the catalog is opened
        # for writing only then (reading it never writes to the backup).
        catalog = None
        try:
            catalog = sb._Catalog(backup, create=False)
            when = time.strftime("%Y-%m-%dT%H:%M:%S")
            for key, sha256 in filled:
                catalog.set_hash_verified(key, sha256, when)
            catalog.commit()
        except (sqlite3.Error, RuntimeError, OSError) as exc:
            # a backup that cannot be written (a write-protected disk, a
            # read-only share): the files were read and compared all the same
            not_recorded = str(exc)
            counts["HASH_NOT_RECORDED"] = counts.pop("HASH_FILLED")
        finally:
            if catalog is not None:
                catalog.close()
    log.csv("_verify.csv", ["status", "rel"], report)
    log.out("Verify: " + ("  ".join(f"{status} {sb._num(count)}"
                                    for status, count in sorted(counts.items())) or "(empty)"))
    if not_recorded is not None:
        log.out(f"  {sb._num(counts['HASH_NOT_RECORDED'])} copies have the bytes of their source, "
                f"but the catalog could not be written ({not_recorded}): their hashes were "
                f"not recorded")
    if counts.get("OK_SIZE_ONLY"):
        log.out(f"  {sb._num(counts['OK_SIZE_ONLY'])} files were not checked by hash: the catalog "
                f"has no hash for them and their source on this computer changed or is not "
                f"there, so only their size was checked")
    return 1 if report else 0


# ---- Restore --------------------------------------------------------------------------------
def _selected_for_restore(label, rest, also):
    first = rest.split(os.sep, 1)[0]
    if also and (_matches(label, also) or _matches(first, also)):
        return True
    if label in PROTECTED_LABELS:
        return True
    if label == LABEL_USER and os.sep in rest:
        return _matches(first, sb._split_names(RESTORE_USER_FOLDERS))
    return False


def _backup_size(path):
    try:
        return os.stat(sb._io(path), follow_symlinks=False).st_size
    except OSError:
        return None


def _size_check(target, row):
    """before_final for a row without a catalog hash: the copy must at least
    have the catalog's size, or it never takes the target's name."""
    if row.sha256 is not None:
        return None

    def check(_digest):
        size = _backup_size(target + sb.TEMP_SUFFIX)
        if size != row.size:
            raise RuntimeError(f"content differs from the catalog: size {size} != {row.size} "
                               f"(backup copy corrupt?)")
    return check


def _existing_outcome(label, row, src, target, real_places=True):
    """What to do with a target that already has a file: it is never overwritten.
    A different file of the installed program is expected in its real place (a
    newer Capture One): KEPT_INSTALLED. Under --to nothing is 'installed', and a
    different file there is a CONFLICT like any other."""
    keep = "KEPT_INSTALLED" if real_places and label in PROTECTED_LABELS else "CONFLICT"
    if os.stat(sb._io(target), follow_symlinks=False).st_size != row.size:
        return keep
    expected = row.sha256 if row.sha256 is not None else sb._sha256_file(src)
    return "ALREADY_THERE" if sb._sha256_file(target) == expected else keep


def _is_monitor_calibration(path):
    """A display profile with a video-card table is this monitor's calibration: an
    older copy would silently leave the screen wrong. Cannot tell = treat it as one."""
    try:
        with open(sb._io(path), "rb") as handle:
            data = handle.read(65536)
        if len(data) < 132 or data[36:40] != b"acsp":
            return True
        if data[12:16] != b"mntr":
            return False
        count = struct.unpack(">I", data[128:132])[0]
        available = (len(data) - 132) // 12
        return any(data[132 + 12 * index:136 + 12 * index] == b"vcgt"
                   for index in range(min(count, available)))
    except OSError:
        return True


def _force_outcome(label, outcome, row, src, target, args, admin):
    """What --force does with an existing file that differs from the backup's."""
    if row.sha256 is None:      # no catalog hash: the backup's copy cannot be proven good
        return outcome
    if label in PROTECTED_LABELS:
        if not args.force_installed:
            return outcome
        if label == LABEL_WINDOWS and (_is_monitor_calibration(target)
                                       or _is_monitor_calibration(src)):
            return "KEPT_CALIBRATION"
        if not admin:
            return "NEEDS_ADMIN"
    return "REPLACE"


class _Snapshot:
    """Keeps what a --force or a --revert-last is about to change. The folder is
    made on first use, so a run that changes nothing leaves none. Same format
    for both prefixes: a revert_old_ folder renamed to old_ can be reverted too."""

    def __init__(self, prefix):
        self.prefix = prefix
        self.folder = None
        self.journaled = 0          # lines written: 0 = this run changed nothing

    def _open(self):
        if self.folder is not None:
            return
        parent = _snapshots_parent()
        os.makedirs(sb._io(parent), exist_ok=True)
        while True:
            now = time.time()
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
            folder = os.path.join(parent, f"{self.prefix}{stamp}_{int((now % 1) * 1000):03d}")
            try:
                os.mkdir(sb._io(folder))     # exclusive: the name is this run's alone
                break
            except FileExistsError:
                time.sleep(0.001)
        self.folder = folder
        # The journal is there from the start, empty until a change is safe:
        # the old_ folder of a --force that changed nothing is the newest one,
        # and --revert-last says "nothing to undo" instead of skipping it and
        # undoing the force before (T6 of the 261112 sweep).
        with open(sb._io(os.path.join(folder, JOURNAL_FILE)), "a", encoding="utf-8"):
            pass

    def _journal(self, operation, rel, target=None):
        entry = {"op": operation, "rel": rel}
        if target:
            # where the file really was: --revert-last undoes it THERE, also when
            # the label resolves to another folder in that run (--install-folder)
            entry["target"] = os.path.abspath(target)
        with open(sb._io(os.path.join(self.folder, JOURNAL_FILE)), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.journaled += 1

    def keep_current(self, rel, target):
        """Copy the file about to be replaced; it is journaled only once it is safe."""
        self._open()
        sb._copy_file(sb.PlannedFile(target, rel, 0, 0, "old"),
                      os.path.join(self.folder, rel), sb.VERIFY_COPY)
        self._journal("replaced", rel, target)

    def note_added(self, rel, target=None):
        self._open()
        self._journal("added", rel, target)


def _writable_before_swap(target):
    """before_final for a swap over a target that exists: Windows refuses to
    replace a read-only file. It runs last, once the current file is safe in the
    snapshot and the new copy is verified; a copy that fails earlier leaves the
    target as it was, attribute included."""
    def clear(_digest=None):
        _make_writable(target)
    return clear


def _make_writable(path):
    try:
        mode = os.stat(sb._io(path), follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    if not mode & 0o200:
        os.chmod(sb._io(path), mode | 0o200)


def _snapshots_parent():
    return os.path.join(_local_appdata(), OLD_PARENT_FOLDER)


def run_restore(args, log):
    try:
        backup, rows = _open_backup(args.restore_from)
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    to = os.path.abspath(args.to) if args.to is not None else None
    if to and sb._overlaps(to, backup):         # also through a junction (T4, 261112 sweep)
        print(f"--to cannot be inside the backup or contain it: {to} vs {backup}"
              + sb._really(to, backup), file=sys.stderr)
        return 2
    if args.force and not _local_appdata():
        print("--force needs %LOCALAPPDATA%: there is nowhere safe to keep the old files.",
              file=sys.stderr)
        return 2
    running = _captureone_running()
    if not to and not args.dry_run and (running or (args.force and running is None)):
        print("Close Capture One first: it would not see the restored files, and could "
              "write over them when it closes.", file=sys.stderr)
        log.log("Refused: Capture One is open")
        return 2
    roots, _version = _roots(args)
    also = args.also or []
    admin = _is_admin()
    items = []
    for row in rows:
        label, _sep, rest = row.rel.partition(os.sep)
        if not rest or not _selected_for_restore(label, rest, also):
            continue
        src = os.path.join(backup, row.rel)
        root = roots.get(label)
        if to:
            target = os.path.join(to, row.rel)
        elif not root:
            items.append(("NO_TARGET", row, src, ""))
            continue
        else:
            target = os.path.join(root, rest)
        if not sb._exists(src):
            status = "MISSING_IN_BACKUP"
        elif row.sha256 is None and _backup_size(src) != row.size:
            # no hash to check the copy against: its size is all there is (B11)
            status = "CORRUPT_IN_BACKUP"
        elif not to and label == LABEL_INSTALLED and not sb._exists(root):
            status = "NOT_INSTALLED"
        elif sb._exists(target):
            try:
                status = _existing_outcome(label, row, src, target, real_places=not to)
                if args.force and status != "ALREADY_THERE":
                    status = _force_outcome(label, status, row, src, target, args, admin)
            except OSError as exc:
                log.log(f"ERROR {row.rel}: {exc}")
                status = "ERROR"
        elif not to and label in PROTECTED_LABELS and not admin:
            status = "NEEDS_ADMIN"
        else:
            status = "RESTORE"
        items.append((status, row, src, target))
    restores = [item for item in items if item[0] in ("RESTORE", "REPLACE")]
    by_drive = {}
    for status, row, _src, target in restores:
        root_drive = os.path.splitdrive(os.path.abspath(target))[0] + os.sep
        # A replaced file also leaves a copy of the old one in the snapshot.
        by_drive[root_drive] = by_drive.get(root_drive, 0) + row.size * (2 if status == "REPLACE" else 1)
    margin = int(float(args.space_margin_gb) * 1e9)
    space_ok = True
    for root_drive, need in sorted(by_drive.items()):
        try:
            free = shutil.disk_usage(root_drive).free
        except OSError as exc:
            log.out(f"Space: {root_drive} not available ({exc})")
            space_ok = False
            continue
        fits = need <= free - margin
        space_ok = space_ok and fits
        log.out(f"Space: {root_drive} need {sb._gb(need)}, free {sb._gb(free)} -> "
                + ("fits" if fits else "DOES NOT FIT"))
    counts = {}
    for status, _row, _src, _target in items:
        counts[status] = counts.get(status, 0) + 1
    log.out(f"captureone_backup {__version__} restore")
    log.out(f"Backup:  {backup}")
    log.out(f"Target:  {to if to else 'the places Capture One uses on this computer'}")
    log.out("Plan:    " + ("  ".join(f"{status} {sb._num(count)}"
                                     for status, count in sorted(counts.items()))
                           or "(nothing selected)"))
    if counts.get("REPLACE"):
        log.out(f"  {sb._num(counts['REPLACE'])} existing files will be REPLACED; the current ones "
                f"are kept first in {_snapshots_parent()} (undo: --revert-last).")
    if counts.get("KEPT_CALIBRATION"):
        log.out(f"  {sb._num(counts['KEPT_CALIBRATION'])} monitor calibration profiles stay as they are.")
    if counts.get("NEEDS_ADMIN"):
        log.out(f"  {sb._num(counts['NEEDS_ADMIN'])} files go to protected folders "
                "(Program Files, Windows colour): run this as administrator to put them there.")
    if counts.get("NOT_INSTALLED"):
        log.out("  Capture One is not installed: install it first, then run this again. Installer: "
                + os.path.join(backup, LABEL_USER, INSTALLER_FOLDER))
    if not space_ok and not args.dry_run:
        print("Not enough space on a target drive; nothing restored.", file=sys.stderr)
        log.log("Not enough space on a target drive; nothing restored.")
        return 2
    log.csv("_plan.csv", ["status", "rel", "target"],
            [(status, row.rel, target) for status, row, _src, target in items])
    if args.dry_run:
        log.log("dry run: nothing restored")
        return 1 if any(item[0] in BAD_RESTORE for item in items) else 0
    if restores and not args.yes and not _confirm(
            f"Restore {sb._num(len(restores))} files? [y/N] ", log):
        return 2
    results = []
    snapshot = _Snapshot(OLD_PREFIX) if args.force else None
    try:
        for status, row, src, target in items:
            detail = ""
            if status in ("RESTORE", "REPLACE"):
                try:
                    planned = sb.PlannedFile(src, row.rel, row.size, row.mtime_ns, "restore")
                    if status == "REPLACE":
                        # The old file is safe before the swap, and the swap is
                        # atomic: at no moment is the target missing.
                        snapshot.keep_current(row.rel, target)
                        sb._copy_file(planned, target, sb.VERIFY_COPY,
                                      expected_sha256=row.sha256,
                                      before_final=_writable_before_swap(target))
                        status = "REPLACED"
                    else:
                        sb._copy_file(planned, target, sb.VERIFY_COPY,
                                      expected_sha256=row.sha256, no_clobber=True,
                                      before_final=_size_check(target, row))
                        # journaled only once the copy is there: a target that
                        # appeared meanwhile is someone else's, and --revert-last
                        # must never take it out (B22)
                        if snapshot is not None:
                            snapshot.note_added(row.rel, target)
                        status = "RESTORED" if row.sha256 else "RESTORED_UNVERIFIED"
                except FileExistsError:
                    status = "CONFLICT"
                    detail = "the target appeared during the restore; left as it was"
                except RuntimeError as exc:
                    status = ("CORRUPT_IN_BACKUP"
                              if "content differs from the catalog" in str(exc) else "ERROR")
                    detail = str(exc)
                except Exception as exc:
                    status = "ERROR"
                    detail = str(exc)
            results.append((status, row.rel, target, detail))
            log.log(f"{status} {row.rel}" + (f": {detail}" if detail else ""))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        log.csv("_restore.csv", ["status", "rel", "target", "detail"], results)
        return 130
    log.csv("_restore.csv", ["status", "rel", "target", "detail"], results)
    final = {}
    for status, _rel, _target, _detail in results:
        final[status] = final.get(status, 0) + 1
    log.out("Result:  " + "  ".join(f"{status} {sb._num(count)}"
                                    for status, count in sorted(final.items())))
    for status, rel, _target, detail in results:
        if status in ("CONFLICT", "ERROR", "CORRUPT_IN_BACKUP", "MISSING_IN_BACKUP"):
            log.out(f"  {status} {rel}" + (f": {detail}" if detail else ""))
    if final.get("RESTORED_UNVERIFIED"):
        log.out(f"  {sb._num(final['RESTORED_UNVERIFIED'])} files restored without a catalog "
                f"hash (RESTORED_UNVERIFIED): only their size was checked")
    if snapshot is not None and snapshot.journaled:
        log.out(f"Old files kept in: {snapshot.folder}  (undo: --revert-last)")
    elif snapshot is not None and snapshot.folder is not None:
        log.out(f"Nothing was replaced or added: {snapshot.folder} records no change "
                f"(--revert-last would undo nothing)")
    return 1 if any(result[0] in BAD_RESTORE for result in results) else 0


# ---- Revert ---------------------------------------------------------------------------------
def _last_snapshot():
    """The newest old_ folder that is not disabled, or None. Names sort by time.
    One without a journal is not skipped: skipping it made --revert-last undo
    the force BEFORE it (T6 of the 261112 sweep); run_revert refuses it."""
    parent = _snapshots_parent()
    try:
        with os.scandir(sb._io(parent)) as iterator:
            names = [entry.name for entry in iterator if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return None
    usable = [name for name in names
              if name.casefold().startswith(OLD_PREFIX.casefold())
              and not name.casefold().endswith(DISABLED_SUFFIX.casefold())]
    return os.path.join(parent, max(usable)) if usable else None


def _journal_target_valid(rel, target):
    """A journaled target is the absolute place of that same rel: it ends with
    the part of the rel below its label, and has no '..'."""
    if not isinstance(target, str) or not os.path.isabs(target):
        return False
    parts = target.replace("/", os.sep).split(os.sep)
    rest = rel.partition(os.sep)[2]
    return bool(rest) and ".." not in parts and \
        target.casefold().endswith(os.sep + rest.casefold())


def _read_journal(folder):
    """The journal's entries: (operation, rel, target). `target` is where the
    file really was (None in a journal written before it was recorded: the
    label is then resolved on this run). Only a last line cut by a crash may be
    unreadable."""
    with open(sb._io(os.path.join(folder, JOURNAL_FILE)), encoding="utf-8") as handle:
        lines = [line for line in handle.read().split("\n") if line.strip()]
    entries = []
    for index, line in enumerate(lines):
        try:
            entry = json.loads(line)
            operation, rel = entry["op"], entry["rel"]
            target = entry.get("target")
        except (ValueError, KeyError, TypeError, AttributeError):
            if index == len(lines) - 1:
                break
            raise ValueError(f"line {index + 1} cannot be read")
        if (operation not in ("replaced", "added") or not isinstance(rel, str)
                or sb._unsafe_rel(rel) is not None):
            raise ValueError(f"line {index + 1} is not valid")
        if target is not None and not _journal_target_valid(rel, target):
            raise ValueError(f"line {index + 1} is not valid")
        entries.append((operation, rel, target))
    return entries


def _same_folder(first, second):
    return (os.path.normcase(os.path.abspath(first)).rstrip("\\/")
            == os.path.normcase(os.path.abspath(second)).rstrip("\\/"))


def _install_folder_at(folder):
    """Whether `folder` is a Capture One install folder: it holds the program,
    or every INSTALL_DATA_FOLDERS folder as a real folder (not a link)."""
    names = sb._split_names(PROCESS_NAMES)
    if names and os.path.isfile(sb._io(os.path.join(folder, names[0]))):
        return True
    data = sb._split_names(INSTALL_DATA_FOLDERS)
    if not data:
        return False
    for name in data:
        path = sb._io(os.path.join(folder, name))
        if (not os.path.isdir(path) or os.path.islink(path)
                or getattr(os.path, "isjunction", lambda _path: False)(path)):
            return False
    return True


def _journal_target_problem(label, rest, target, roots):
    """Why a journaled target is not a place a --force of this computer can
    have written, or None (T3 of the 261112 sweep). The journal lives in
    %LOCALAPPDATA%, where any program of the user can write, and --revert-last
    may run as administrator: a target is trusted only where Capture One keeps
    that part --
      UserData, Preferences, WindowsColor: this run's folder of the label;
      Installed: inside a folder of INSTALL_DATA_FOLDERS of this run's install
        folder, or of another Capture One install folder (a --force made with
        --install-folder is undone there, N10);
    and only if no link or junction below that folder leads somewhere else."""
    if label not in roots or not rest:
        return "is not under a part this script keeps"
    root = target[:len(target) - len(os.sep + rest)]     # it ends with \rest (_journal_target_valid)
    if label == LABEL_INSTALLED:
        data = {name.casefold() for name in sb._split_names(INSTALL_DATA_FOLDERS)}
        if rest.split(os.sep, 1)[0].casefold() not in data:
            return "is not in a folder of INSTALL_DATA_FOLDERS"
        if not (roots[label] and _same_folder(root, roots[label])) and not _install_folder_at(root):
            return "is not in a Capture One install folder"
    elif not roots[label] or not _same_folder(root, roots[label]):
        return f"is not in this computer's {label} folder ({roots[label] or 'unknown'})"
    if (os.path.normcase(sb._real(target)).rstrip("\\/")
            != os.path.normcase(os.path.join(sb._real(root), rest)).rstrip("\\/")):
        return "leads somewhere else through a link or a junction"
    return None


def _under_roots(path, roots):
    return any(root and sb._is_under(path, root) for root in roots.values())


class _TargetAppeared(Exception):
    """A --revert-last target that was not there got a file before our copy took its name."""


def _same_bytes(first, second):
    if os.path.getsize(sb._io(first)) != os.path.getsize(sb._io(second)):
        return False
    return sb._sha256_file(first) == sb._sha256_file(second)


def run_revert(args, log):
    if not _local_appdata():
        print("%LOCALAPPDATA% is not set.", file=sys.stderr)
        return 2
    folder = _last_snapshot()
    if folder is None:
        print(f"Nothing to undo: no usable {OLD_PREFIX}* folder in {_snapshots_parent()}.",
              file=sys.stderr)
        return 2
    skip_hint = (f"To undo the --force before it, rename this folder so that its name ends "
                 f"with {DISABLED_SUFFIX}.")
    if not sb._exists(os.path.join(folder, JOURNAL_FILE)):
        print(f"Cannot undo {folder}: it has no {JOURNAL_FILE} (a --force that stopped before "
              f"it changed a file). Nothing changed. {skip_hint}", file=sys.stderr)
        log.log(f"Refused: {folder} has no journal")
        return 2
    try:
        entries = _read_journal(folder)
    except (OSError, ValueError) as exc:
        print(f"Cannot read {folder}: {exc}. Nothing changed.", file=sys.stderr)
        return 2
    if not entries:
        print(f"Nothing to undo in {folder}: that --force changed no file. Nothing changed. "
              f"{skip_hint}", file=sys.stderr)
        log.log(f"Nothing to undo: {folder} has an empty journal")
        return 2
    running = _captureone_running()
    if not args.dry_run and running is not False:
        print("Close Capture One first (or Windows could not say whether it is open).",
              file=sys.stderr)
        log.log("Refused: Capture One may be open")
        return 2
    roots, _version = _roots(args)
    # Every journaled target is checked before anything is touched (T3 of the
    # 261112 sweep): one that is not a place a force of this computer writes
    # makes the whole journal untrusted.
    outside = []
    for index, (_operation, rel, journaled) in enumerate(entries, 1):
        if journaled is None:
            continue
        label, _sep, rest = rel.partition(os.sep)
        why = _journal_target_problem(label, rest, journaled, roots)
        if why is not None:
            print(f"Cannot undo {folder}: line {index} of its {JOURNAL_FILE}: {journaled} {why}. "
                  f"Nothing changed.", file=sys.stderr)
            log.log(f"Refused: journal line {index}: {journaled} {why}")
            return 2
        if not _under_roots(journaled, roots):
            outside.append(journaled)
    admin = _is_admin()
    items = []
    for operation, rel, journaled in entries:
        label, _sep, rest = rel.partition(os.sep)
        root = roots.get(label)
        if journaled:
            # undone where the force really put it, not where the label
            # resolves in this run (another --install-folder)
            target = journaled
        elif not root or not rest:
            items.append(("NO_TARGET", rel, "", ""))
            continue
        else:
            target = os.path.join(root, rest)
        snap = os.path.join(folder, rel)
        # By the place: a target was checked above to be in the folder of its
        # own label (an install or the colour folder for the protected ones).
        protected = label in PROTECTED_LABELS and not admin
        try:
            if operation == "added":
                status = ("ALREADY_GONE" if not sb._exists(target)
                          else "NEEDS_ADMIN" if protected else "TAKE_OUT")
            elif not sb._exists(snap):
                status = "MISSING_IN_SNAPSHOT"
            elif sb._exists(target) and _same_bytes(snap, target):
                status = "ALREADY_THERE"
            else:
                status = "NEEDS_ADMIN" if protected else "PUT_BACK"
        except OSError as exc:
            log.log(f"ERROR {rel}: {exc}")
            status = "ERROR"
        items.append((status, rel, snap, target))
    counts = {}
    for status, _rel, _snap, _target in items:
        counts[status] = counts.get(status, 0) + 1
    log.out(f"captureone_backup {__version__} revert")
    log.out(f"Undoing: {folder}")
    log.out("Plan:    " + ("  ".join(f"{status} {sb._num(count)}"
                                     for status, count in sorted(counts.items())) or "(empty)"))
    if outside:
        # named before the question: where the force went (--install-folder),
        # not where this run's folders are
        log.out(f"  {sb._num(len(outside))} files are undone where the --force put them, "
                f"outside this run's Capture One folders:")
        for path in outside:
            log.out(f"    {path}")
    log.csv("_revert_plan.csv", ["status", "rel", "target"],
            [(status, rel, target) for status, rel, _snap, target in items])
    if args.dry_run:
        log.log("dry run: nothing changed")
        return 1 if any(item[0] in BAD_REVERT for item in items) else 0
    changes = [item for item in items if item[0] in ("PUT_BACK", "TAKE_OUT")]
    if changes and not args.yes and not _confirm(
            f"Change {sb._num(len(changes))} files? [y/N] ", log):
        return 2
    snapshot = _Snapshot(REVERT_PREFIX)
    results = []
    try:
        for status, rel, snap, target in items:
            detail = ""
            try:
                if status == "PUT_BACK":
                    existed = sb._exists(target)
                    planned = sb.PlannedFile(snap, rel, 0, 0, "revert")
                    if existed:
                        # the current file is safe first; a read-only one (the
                        # force copies the attribute too) is then replaceable
                        snapshot.keep_current(rel, target)
                        sb._copy_file(planned, target, sb.VERIFY_COPY,
                                      before_final=_writable_before_swap(target))
                    else:
                        # a target that appears meanwhile is someone else's:
                        # never replaced, as in the restore
                        try:
                            sb._copy_file(planned, target, sb.VERIFY_COPY, no_clobber=True)
                        except FileExistsError:
                            raise _TargetAppeared() from None
                        snapshot.note_added(rel, target)    # journaled once it is there (B22)
                    status = "PUT_BACK_DONE"
                elif status == "TAKE_OUT":
                    # A file the force added leaves Capture One but is not lost.
                    snapshot.keep_current(rel, target)
                    _make_writable(target)      # Windows refuses to remove a read-only file
                    os.remove(sb._io(target))
                    status = "TAKEN_OUT"
            except _TargetAppeared:
                status = "CONFLICT"
                detail = "the target appeared during the revert; left as it was"
            except Exception as exc:
                status = "ERROR"
                detail = str(exc)
            results.append((status, rel, target, detail))
            log.log(f"{status} {rel}" + (f": {detail}" if detail else ""))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        log.csv("_revert.csv", ["status", "rel", "target", "detail"], results)
        return 130
    log.csv("_revert.csv", ["status", "rel", "target", "detail"], results)
    final = {}
    for status, _rel, _target, _detail in results:
        final[status] = final.get(status, 0) + 1
    log.out("Result:  " + "  ".join(f"{status} {sb._num(count)}"
                                    for status, count in sorted(final.items())))
    for status, rel, _target, detail in results:
        if status in BAD_REVERT:
            log.out(f"  {status} {rel}" + (f": {detail}" if detail else ""))
    if snapshot.folder is not None:
        log.out(f"What this replaced is kept in: {snapshot.folder}")
    return 1 if any(result[0] in BAD_REVERT for result in results) else 0


# ---- Command line ---------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="captureone_backup.py",
        description="Back up and restore Capture One's profiles, styles and settings.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dest", metavar="PATH",
                       help=f"back up to PATH\\{BACKUP_SUBFOLDER}")
    group.add_argument("--verify", metavar="PATH",
                       help="re-read and hash the backup under PATH against its catalog")
    group.add_argument("--restore-from", metavar="PATH",
                       help="restore from the backup under PATH (never overwrites a file)")
    group.add_argument("--revert-last", action="store_true",
                       help=f"undo the last --force: put back the newest {OLD_PREFIX}* folder "
                            f"(a folder ending in {DISABLED_SUFFIX} is skipped)")
    parser.add_argument("--force", action="store_true",
                        help=f"restore: also replace files that differ, keeping the current ones "
                             f"in {OLD_PREFIX}<time> first (your files only, see --force-installed)")
    parser.add_argument("--force-installed", action="store_true",
                        help="with --force: also replace the install folder's profiles and the "
                             "Windows colour folder's (never a monitor calibration)")
    parser.add_argument("--also", action="append", default=None, metavar="NAME",
                        help="restore: also bring back this part (Preferences, or a folder of "
                             "UserData such as _Instalador; '*' allowed; repeatable)")
    parser.add_argument("--to", metavar="PATH",
                        help="restore: under PATH\\<part>\\... instead of the original places")
    parser.add_argument("--install-folder", metavar="PATH",
                        help="Capture One's install folder (default: read from Windows)")
    parser.add_argument("--space-margin-gb", type=float, default=sb.SPACE_MARGIN_GB, metavar="N",
                        help="free space kept on the target drive (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="plan and report, write nothing")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv=None):
    try:
        return _main(argv)
    except KeyboardInterrupt:   # while planning; each job handles its own copy (B20)
        print("Interrupted.", file=sys.stderr)
        return 130


def _main(argv):
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
    if not math.isfinite(args.space_margin_gb) or args.space_margin_gb < 0:
        print("--space-margin-gb must be a number, 0 or more", file=sys.stderr)
        return 2
    # An empty path (an unset variable in a .cmd: --to "%OUT%") is never "the
    # option was not given": --to "" would restore into the real places.
    for flag, value in (("--dest", args.dest), ("--verify", args.verify),
                        ("--restore-from", args.restore_from), ("--to", args.to),
                        ("--install-folder", args.install_folder)):
        if value is not None and not value.strip():
            print(f"{flag} needs a folder: it was given empty", file=sys.stderr)
            return 2
    if (args.also or args.to is not None) and args.restore_from is None:
        print("--also and --to only apply to --restore-from", file=sys.stderr)
        return 2
    if args.verify is not None and args.dry_run:
        print("--verify cannot be combined with --dry-run", file=sys.stderr)
        return 2
    if args.force and args.restore_from is None:
        print("--force only applies to --restore-from", file=sys.stderr)
        return 2
    if args.force_installed and not args.force:
        print("--force-installed needs --force", file=sys.stderr)
        return 2
    if args.force and args.to is not None:
        print("--force works on the real places; --to never replaces anything", file=sys.stderr)
        return 2
    stamp = time.strftime("%y%m%d_%H%M%S")
    try:
        log = sb._Logger(os.path.join(sb.LOG_DIR, f"{stamp}_captureone"))
    except OSError as exc:
        print(f"cannot write the log directory {sb.LOG_DIR}: {exc}", file=sys.stderr)
        return 2
    try:
        if args.dest is not None:
            return run_backup(args, log, stamp)
        if args.verify is not None:
            return run_verify(args, log)
        if args.revert_last:
            return run_revert(args, log)
        return run_restore(args, log)
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
