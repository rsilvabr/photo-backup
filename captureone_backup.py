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
import os
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selective_backup as sb

__version__ = "0.10.0"

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
BAD_REVERT = ("NEEDS_ADMIN", "MISSING_IN_SNAPSHOT", "NO_TARGET", "ERROR")


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
    links: int = 0
    found: list = field(default_factory=list)


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
            continue
        for entry in entries:
            path = os.path.join(folder, entry.name)
            child = os.path.join(rel, entry.name)
            try:
                if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                    plan.links += 1
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


def _build_plan(roots):
    plan = _Plan()
    for label in (LABEL_USER, LABEL_PREFERENCES):
        if roots[label] and sb._exists(roots[label]):
            plan.found.append(label)
            top = sb._split_names(EXCLUDE_TOP_FOLDERS) if label == LABEL_USER else ()
            _walk(roots[label], label, plan, top_exclude=top)
    install = roots[LABEL_INSTALLED]
    if install and sb._exists(install):
        for name in sb._split_names(INSTALL_DATA_FOLDERS):
            folder = os.path.join(install, name)
            if sb._exists(folder):
                if LABEL_INSTALLED not in plan.found:
                    plan.found.append(LABEL_INSTALLED)
                _walk(folder, os.path.join(LABEL_INSTALLED, name), plan)
    color = roots[LABEL_WINDOWS]
    # The colour folder is only worth keeping next to a Capture One that was found.
    if plan.found and color and sb._exists(color):
        plan.found.append(LABEL_WINDOWS)
        _walk(color, LABEL_WINDOWS, plan, keep_extensions=WINDOWS_COLOR_EXTENSIONS, recurse=False)
    return plan


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


def _write_map(backup, roots, version, plan):
    """The readable answer to 'where did each thing come from?' (rewritten each run)."""
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
    for label in plan.found:
        lines.append(f"  {label:<13} -> {roots[label]}")
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
    target = os.path.join(backup, MAP_FILE)
    tmp = target + sb.TEMP_SUFFIX
    with open(sb._io(tmp), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    os.replace(sb._io(tmp), sb._io(target))


def _cmd_escape(value):
    """A literal for a .cmd file: a '%' would otherwise start a variable."""
    return value.replace("%", "%%")


def _cmd_install_target(install):
    """The install folder as the new computer spells it: %ProgramFiles% is not always C:."""
    program_files = os.environ.get("ProgramFiles") or ""
    if program_files and sb._is_under(install, program_files):
        return "%ProgramFiles%" + _cmd_escape(install[len(program_files.rstrip("\\/")):])
    return _cmd_escape(install)


def _write_restore_script(backup, roots, plan):
    """RESTORE_SCRIPT: everything a new computer needs, with nothing but Windows.
    robocopy with /XC /XN /XO only ever adds missing files. No 'call' and no
    (...) block around a path: both break on a '%' or a ')' in the backup's path."""
    no_overwrite = "/XC /XN /XO /R:1 /W:1 /NP /NDL /NFL /NJH /NJS"
    exe = sb._split_names(PROCESS_NAMES)[0]
    restored = sb._split_names(RESTORE_USER_FOLDERS)
    user_folders = set()
    install_folders = set()
    has_color = False
    for item in plan.files:
        parts = item.rel.split(os.sep)
        if parts[0] == LABEL_USER and len(parts) >= 3 and _matches(parts[1], restored):
            user_folders.add(parts[1])
        elif parts[0] == LABEL_INSTALLED:
            install_folders.add(parts[1])
        elif parts[0] == LABEL_WINDOWS:
            has_color = True
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal",
        'set "HERE=%~dp0"',
        'set "SELF=%~f0"',
        f'if not defined C1_INSTALL_TARGET set "C1_INSTALL_TARGET={_cmd_install_target(roots[LABEL_INSTALLED])}"',
        f'if not defined C1_COLOR_TARGET set "C1_COLOR_TARGET=%SystemRoot%\\{_cmd_escape(WINDOWS_COLOR_FOLDER)}"',
        'if /i "%~1"=="--unattended" goto :installed',
        "net session >nul 2>&1",
        "if not errorlevel 1 goto :checks",
        "echo Asking Windows for administrator rights...",
        'powershell -NoProfile -Command "$q=[char]34; Start-Process -FilePath $env:ComSpec '
        "-Verb RunAs -ArgumentList ('/c '+$q+$q+$env:SELF+$q+$q)\"",
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
    target = os.path.join(backup, RESTORE_SCRIPT)
    tmp = target + sb.TEMP_SUFFIX
    with open(sb._io(tmp), "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write("\n".join(lines))
    os.replace(sb._io(tmp), sb._io(target))


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
        if root and (sb._is_under(backup, root) or sb._is_under(root, backup)):
            print(f"The backup cannot overlap a Capture One folder: {backup} vs {root}",
                  file=sys.stderr)
            return 2
    plan = _build_plan(roots)
    if not plan.files:
        log.out("Capture One settings: nothing found on this computer, nothing to do.")
        return 1 if plan.errors else 0
    statuses = [sb._file_status(item, backup) for item in plan.files]
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
    if not fits and not args.dry_run:
        print("Not enough space; nothing copied.", file=sys.stderr)
        log.log("Not enough space; nothing copied.")
        return 2
    if args.dry_run:
        log.log("dry run: nothing copied")
        return 1 if plan.errors or not fits else 0
    if not args.yes and to_copy and not _confirm(f"Copy {sb._num(len(to_copy))} files? [y/N] ", log):
        return 2
    os.makedirs(sb._io(backup), exist_ok=True)
    try:
        catalog = sb._Catalog(backup, create=True)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    failed = []
    versions = 0
    try:
        for status in statuses:
            if status.status == sb.ST_UP_TO_DATE:
                catalog.adopt(status.planned)
        catalog.commit()
        last = time.time()
        for index, status in enumerate(to_copy, 1):
            try:
                if status.status == sb.ST_CHANGED:
                    # Capture One rewrites files with the same bytes: only a real
                    # difference is worth an entry in the versions folder.
                    if sb._sha256_file(status.planned.src) != sb._sha256_file(status.dst):
                        sb._move_to_versions(backup, stamp, status, log)
                        versions += 1
                digest, size, mtime_ns = sb._copy_file(status.planned, status.dst, sb.VERIFY_COPY)
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
        keys = {item.rel.casefold() for item in plan.files}
        gone = [row for row in catalog.all_rows() if row.key not in keys]
        catalog.commit()
    except KeyboardInterrupt:
        catalog.commit()
        print("Interrupted.", file=sys.stderr)
        log.log("Interrupted.")
        return 130
    finally:
        catalog.close()
    _write_map(backup, roots, version, plan)
    _write_restore_script(backup, roots, plan)
    log.csv("_gone.csv", ["rel", "size", "source_path"],
            [(row.rel, row.size, row.source_path) for row in gone])
    log.out(f"Result:    copied {sb._num(len(to_copy) - len(failed))}, "
            f"old copies kept in versions {sb._num(versions)}, failed {sb._num(len(failed))}, "
            f"kept although gone from Capture One {sb._num(len(gone))}")
    for rel, message in failed:
        log.out(f"  FAILED {rel}: {message}")
    return 1 if failed or plan.errors else 0


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
    finally:
        catalog.close()


def run_verify(args, log):
    try:
        backup, rows = _open_backup(args.verify)
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    counts = {}
    report = []
    for row in rows:
        path = os.path.join(backup, row.rel)
        try:
            size = os.stat(sb._io(path), follow_symlinks=False).st_size
            if size != row.size:
                status = "DEST_SIZE"
            elif row.sha256 is None:
                status = "OK_SIZE_ONLY"
            else:
                status = "OK" if sb._sha256_file(path) == row.sha256 else "CORRUPT"
        except FileNotFoundError:
            status = "MISSING"
        except OSError as exc:
            status = "ERROR"
            log.log(f"ERROR {row.rel}: {exc}")
        counts[status] = counts.get(status, 0) + 1
        if not status.startswith("OK"):
            report.append((status, row.rel))
            log.out(f"  {status} {row.rel}")
    log.csv("_verify.csv", ["status", "rel"], report)
    log.out("Verify: " + ("  ".join(f"{status} {sb._num(count)}"
                                    for status, count in sorted(counts.items())) or "(empty)"))
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


def _existing_outcome(label, row, src, target):
    """What to do with a target that already has a file: it is never overwritten."""
    keep = "KEPT_INSTALLED" if label in PROTECTED_LABELS else "CONFLICT"
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

    def _journal(self, operation, rel):
        with open(sb._io(os.path.join(self.folder, JOURNAL_FILE)), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"op": operation, "rel": rel}, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def keep_current(self, rel, target):
        """Copy the file about to be replaced; it is journaled only once it is safe."""
        self._open()
        sb._copy_file(sb.PlannedFile(target, rel, 0, 0, "old"),
                      os.path.join(self.folder, rel), sb.VERIFY_COPY)
        self._journal("replaced", rel)

    def note_added(self, rel):
        self._open()
        self._journal("added", rel)


def _snapshots_parent():
    return os.path.join(_local_appdata(), OLD_PARENT_FOLDER)


def run_restore(args, log):
    try:
        backup, rows = _open_backup(args.restore_from)
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    to = os.path.abspath(args.to) if args.to else None
    if to and (sb._is_under(to, backup) or sb._is_under(backup, to)):
        print(f"--to cannot be inside the backup or contain it: {to} vs {backup}", file=sys.stderr)
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
        elif not to and label == LABEL_INSTALLED and not sb._exists(root):
            status = "NOT_INSTALLED"
        elif sb._exists(target):
            try:
                status = _existing_outcome(label, row, src, target)
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
                                      expected_sha256=row.sha256)
                        status = "REPLACED"
                    else:
                        if snapshot is not None:
                            snapshot.note_added(row.rel)
                        sb._copy_file(planned, target, sb.VERIFY_COPY,
                                      expected_sha256=row.sha256, no_clobber=True)
                        status = "RESTORED"
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
    if snapshot is not None and snapshot.folder is not None:
        log.out(f"Old files kept in: {snapshot.folder}  (undo: --revert-last)")
    return 1 if any(result[0] in BAD_RESTORE for result in results) else 0


# ---- Revert ---------------------------------------------------------------------------------
def _last_snapshot():
    """The newest old_ folder that is not disabled, or None. Names sort by time."""
    parent = _snapshots_parent()
    try:
        with os.scandir(sb._io(parent)) as iterator:
            names = [entry.name for entry in iterator if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return None
    usable = [name for name in names
              if name.casefold().startswith(OLD_PREFIX.casefold())
              and not name.casefold().endswith(DISABLED_SUFFIX.casefold())
              and sb._exists(os.path.join(parent, name, JOURNAL_FILE))]
    return os.path.join(parent, max(usable)) if usable else None


def _read_journal(folder):
    """The journal's entries. Only a last line cut by a crash may be unreadable."""
    with open(sb._io(os.path.join(folder, JOURNAL_FILE)), encoding="utf-8") as handle:
        lines = [line for line in handle.read().split("\n") if line.strip()]
    entries = []
    for index, line in enumerate(lines):
        try:
            entry = json.loads(line)
            operation, rel = entry["op"], entry["rel"]
        except (ValueError, KeyError, TypeError):
            if index == len(lines) - 1:
                break
            raise ValueError(f"line {index + 1} cannot be read")
        if operation not in ("replaced", "added") or ".." in rel.split(os.sep):
            raise ValueError(f"line {index + 1} is not valid")
        entries.append((operation, rel))
    return entries


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
    try:
        entries = _read_journal(folder)
    except (OSError, ValueError) as exc:
        print(f"Cannot read {folder}: {exc}. Nothing changed.", file=sys.stderr)
        return 2
    running = _captureone_running()
    if not args.dry_run and running is not False:
        print("Close Capture One first (or Windows could not say whether it is open).",
              file=sys.stderr)
        log.log("Refused: Capture One may be open")
        return 2
    roots, _version = _roots(args)
    admin = _is_admin()
    items = []
    for operation, rel in entries:
        label, _sep, rest = rel.partition(os.sep)
        root = roots.get(label)
        if not root or not rest:
            items.append(("NO_TARGET", rel, "", ""))
            continue
        target = os.path.join(root, rest)
        snap = os.path.join(folder, rel)
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
                    if sb._exists(target):
                        snapshot.keep_current(rel, target)
                    else:
                        snapshot.note_added(rel)
                    sb._copy_file(sb.PlannedFile(snap, rel, 0, 0, "revert"), target, sb.VERIFY_COPY)
                    status = "PUT_BACK_DONE"
                elif status == "TAKE_OUT":
                    # A file the force added leaves Capture One but is not lost.
                    snapshot.keep_current(rel, target)
                    os.remove(sb._io(target))
                    status = "TAKEN_OUT"
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
    if args.space_margin_gb < 0:
        print("--space-margin-gb cannot be negative", file=sys.stderr)
        return 2
    if (args.also or args.to) and not args.restore_from:
        print("--also and --to only apply to --restore-from", file=sys.stderr)
        return 2
    if args.verify and args.dry_run:
        print("--verify cannot be combined with --dry-run", file=sys.stderr)
        return 2
    if args.force and not args.restore_from:
        print("--force only applies to --restore-from", file=sys.stderr)
        return 2
    if args.force_installed and not args.force:
        print("--force-installed needs --force", file=sys.stderr)
        return 2
    if args.force and args.to:
        print("--force works on the real places; --to never replaces anything", file=sys.stderr)
        return 2
    stamp = time.strftime("%y%m%d_%H%M%S")
    try:
        log = sb._Logger(os.path.join(sb.LOG_DIR, f"{stamp}_captureone"))
    except OSError as exc:
        print(f"cannot write the log directory {sb.LOG_DIR}: {exc}", file=sys.stderr)
        return 2
    try:
        if args.dest:
            return run_backup(args, log, stamp)
        if args.verify:
            return run_verify(args, log)
        if args.revert_last:
            return run_revert(args, log)
        return run_restore(args, log)
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
