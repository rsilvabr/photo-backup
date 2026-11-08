#!/usr/bin/env python3
"""photo_backup - interactive menu for selective_backup.py.

Back up, verify and keep presets without typing command lines. Every backup
starts with a dry-run of the same choices: you see what would be copied, what
would not and whether it fits, and only then decide to copy.

The menu never copies anything itself: it builds the command line and runs
selective_backup.py as a child process, so the backend's safety rules (read-only
source, nothing deleted, space check) apply unchanged. The defaults it shows
are read from selective_backup.py, never repeated here.

    python photo_backup.py                    # interactive menu
    python photo_backup.py --list-presets
    python photo_backup.py --run-preset NAME  # unattended (Task Scheduler)
"""

import argparse
import importlib.util
import json
import os
import shutil
import string
import subprocess
import sys

__version__ = "0.5.0"

# ---- Settings ------------------------------------------------------------------
CONFIG_PATH = os.environ.get("PHOTOBACKUP_CONFIG") or os.path.join(
    os.path.expanduser("~"), ".photo_backup_config.json")   # remembered choices + presets
BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selective_backup.py")
RESTORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "restore_backup.py")
VERIFY_MODE = "quick"           # default verify mode offered by the menu: quick | full
FORMAT_ALTERNATIVES = {         # offered next to the backend's EXPORT_FORMATS default
    "jxl>tif>jpg": "best per photo: the JXL master, else the TIFF, else the JPEG",
    "jxl>jpg": "the JXL, else the JPEG (never the TIFF)",
    "jxl+jpg": "both the JXL and the JPEG",
    "all": "everything inside the export folders",
}

KIND_BACKUP = "backup"
KIND_VERIFY = "verify"

try:                                    # colours when rich is installed; plain text otherwise
    from rich.console import Console
    _CONSOLE = Console(highlight=False)
except ImportError:                     # pragma: no cover - depends on the machine
    _CONSOLE = None


class Abort(Exception):
    """No more input (closed stdin / Ctrl+Z): leave the menu."""


# ---- Backend ---------------------------------------------------------------------
_BACKEND_MODULE = None


def _backend():
    """selective_backup.py imported as a module, to read its settings and helpers."""
    global _BACKEND_MODULE
    if _BACKEND_MODULE is None:
        spec = importlib.util.spec_from_file_location("selective_backup", BACKEND)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _BACKEND_MODULE = module
    return _BACKEND_MODULE


def _setting(name):
    return getattr(_backend(), name)


def run_backend(argv):
    """Run selective_backup.py with `argv`; its output goes straight to the console."""
    try:
        return subprocess.run([sys.executable, BACKEND, *argv]).returncode
    except KeyboardInterrupt:
        return 130


def run_restore(argv):
    """Run restore_backup.py with `argv`; its output goes straight to the console."""
    try:
        return subprocess.run([sys.executable, RESTORE, *argv]).returncode
    except KeyboardInterrupt:
        return 130


# ---- Console helpers ----------------------------------------------------------------
def say(text="", style=None):
    if _CONSOLE is not None and style:
        _CONSOLE.print(text, style=style, markup=False)
    else:
        print(text)


def title(text):
    say()
    say(f"== {text} ==", "bold cyan")


def ask(prompt, default=None):
    """Prompt; Enter returns `default`. A closed stdin raises Abort."""
    suffix = f" [{default}]" if default not in (None, "") else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        raise Abort()
    return answer if answer else ("" if default is None else str(default))


def yes_no(prompt, default):
    hint = "Y/n" if default else "y/N"
    while True:
        answer = ask(f"{prompt} ({hint})").casefold()
        if not answer:
            return default
        if answer in ("y", "yes", "s", "sim"):
            return True
        if answer in ("n", "no", "nao", "não"):
            return False
        say("  Answer y or n.", "yellow")


def choose(prompt, options, default_value):
    """options: list of (value, description). Returns the chosen value."""
    default_index = 1
    for index, (value, text) in enumerate(options, 1):
        mark = "  <- current" if value == default_value else ""
        if value == default_value:
            default_index = index
        say(f"  [{index}] {value:<14} {text}{mark}")
    while True:
        answer = ask(prompt, default_index)
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        say(f"  Type a number from 1 to {len(options)}.", "yellow")


# ---- Config -----------------------------------------------------------------------------
def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            config = json.load(handle)
        if not isinstance(config, dict):
            raise ValueError("not an object")
    except FileNotFoundError:
        config = {}
    except (OSError, ValueError) as exc:
        bad = CONFIG_PATH + ".bad"
        try:
            os.replace(CONFIG_PATH, bad)
        except OSError:
            pass
        say(f"The config file could not be read ({exc}); it was kept as {bad}.", "yellow")
        config = {}
    config.setdefault("last", {})
    config.setdefault("presets", {})
    return config


def save_config(config):
    folder = os.path.dirname(CONFIG_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def default_session():
    """A session with the backend's own defaults."""
    return {
        "sources": [],
        "dest": "",
        "export_formats": _setting("EXPORT_FORMATS"),
        "exclude_export_subfolders": _setting("EXCLUDE_EXPORT_SUBFOLDERS"),
        "unselected": _setting("UNSELECTED"),
        "unexported": _setting("UNEXPORTED"),
        "c1_cache": bool(_setting("COPY_CAPTUREONE_CACHE")),
        "derivatives": bool(_setting("COPY_JXLPHOTO_DERIVATIVES")),
        "update_changed": False,
        "verify_mode": VERIFY_MODE,
        "max_gb": _setting("VERIFY_MAX_GB"),
    }


def session_from(saved):
    session = default_session()
    session.update({key: value for key, value in (saved or {}).items() if key in session})
    return session


# ---- Command line built from a session ------------------------------------------------------
def build_argv(session, mode):
    """mode: 'dry-run', 'copy' (adds --yes: the menu already asked) or 'verify'.
    Every selection option is passed explicitly, with the value shown to the
    user, so what runs is exactly what was confirmed."""
    argv = []
    for source in session["sources"]:
        argv += ["--source", source]
    argv += ["--dest", session["dest"],
             "--export-formats", session["export_formats"],
             "--exclude-export-subfolders", session["exclude_export_subfolders"],
             "--unselected", session["unselected"],
             "--unexported", session["unexported"]]
    if session["c1_cache"]:
        argv.append("--c1-cache")
    if session["derivatives"]:
        argv.append("--derivatives")
    if mode == "verify":
        argv += ["--verify", session["verify_mode"]]
        if session["verify_mode"] == "full":
            argv += ["--max-gb", str(session["max_gb"])]
    elif mode == "dry-run":
        argv.append("--dry-run")
    else:
        argv.append("--yes")
        if session["update_changed"]:
            argv.append("--update-changed")
    return argv


def describe(session):
    lines = [
        "  Sources:      " + "; ".join(session["sources"]),
        "  Destination:  " + session["dest"],
        "  Formats:      " + session["export_formats"],
        "  Skip inside export: " + (session["exclude_export_subfolders"] or "(nothing)"),
        f"  Not exported originals: {session['unselected']}   "
        f"Folders without export: {session['unexported']}",
        f"  Capture One cache: {'copy' if session['c1_cache'] else 'skip'}   "
        f"Replace changed originals/exports: {'yes' if session['update_changed'] else 'no'}",
        "  jxl-photo derivatives of a copied master: "
        + ("copy" if session["derivatives"] else "skip (COPY_JXLPHOTO_DERIVATIVES)"),
    ]
    return "\n".join(lines)


# ---- Drives ------------------------------------------------------------------------------------
def list_drives():
    """(root, label, free, total) of every mounted drive; an empty card reader is skipped
    without a Windows 'no disk' dialog."""
    drives = []
    if os.name != "nt":
        return drives
    import ctypes
    kernel32 = ctypes.windll.kernel32
    old_mode = kernel32.SetErrorMode(0x0001 | 0x8000)   # no critical-error dialogs
    try:
        mask = kernel32.GetLogicalDrives()
        for index, letter in enumerate(string.ascii_uppercase):
            if not mask & (1 << index):
                continue
            root = f"{letter}:\\"
            try:
                usage = shutil.disk_usage(root)
            except OSError:
                continue
            drives.append((root, _backend()._volume_label(root), usage.free, usage.total))
    finally:
        kernel32.SetErrorMode(old_mode)
    return drives


def show_drives():
    drives = list_drives()
    if not drives:
        return
    say("  Drives:")
    for root, label, free, total in drives:
        say(f"    {root:<4} {label[:24]:<24} {free / 1e9:9,.0f} GB free of {total / 1e9:,.0f} GB")


# ---- Editing a session ---------------------------------------------------------------------------
def ask_sources(current):
    while True:
        text = ask("Folders to back up (separate several with ;)", "; ".join(current))
        sources = [part.strip().strip('"') for part in text.split(";") if part.strip()]
        missing = [source for source in sources if not os.path.isdir(source)]
        if sources and not missing:
            return sources
        if missing:
            say("  Not found: " + "; ".join(missing), "yellow")
        else:
            say("  Type at least one folder.", "yellow")


def ask_dest(current, must_exist):
    show_drives()
    while True:
        dest = ask("Backup destination folder", current).strip().strip('"')
        if not dest:
            say("  Type a destination folder.", "yellow")
            continue
        drive = os.path.splitdrive(os.path.abspath(dest))[0]
        if drive and not os.path.exists(drive + os.sep):
            say(f"  Drive {drive} is not connected.", "yellow")
            continue
        if must_exist and not os.path.isdir(dest):
            say("  That folder does not exist (nothing was backed up there yet).", "yellow")
            continue
        return dest


def ask_formats(current):
    default = _setting("EXPORT_FORMATS")
    options = [(default, FORMAT_ALTERNATIVES.get(default, "the default of selective_backup.py"))]
    options += [(spec, text) for spec, text in FORMAT_ALTERNATIVES.items() if spec != default]
    if current not in [value for value, _ in options]:
        options.append((current, "your last choice"))
    options.append(("custom", "type a spec (e.g. jxl>png, jxl+tif)"))
    say("  Which files inside the export folders?")
    while True:
        value = choose("Formats", options, current)
        if value != "custom":
            return value
        spec = ask("Spec ('a>b' = first that exists per photo, 'a+b' = all of them)")
        try:
            _backend()._parse_formats(spec)
            return spec
        except ValueError as exc:
            say(f"  {exc}", "yellow")


def edit_session(session, verify=False):
    session = dict(session)
    title("Verify a backup" if verify else "What to back up")
    session["sources"] = ask_sources(session["sources"])
    session["dest"] = ask_dest(session["dest"], must_exist=verify)
    session["export_formats"] = ask_formats(session["export_formats"])
    session["exclude_export_subfolders"] = ask(
        "Subfolders to skip inside export folders (e.g. JXL_mobile;WEB, '-' for none)",
        session["exclude_export_subfolders"] or "-")
    if session["exclude_export_subfolders"] == "-":
        session["exclude_export_subfolders"] = ""
    say("  Originals of photos that were NOT exported:")
    session["unselected"] = choose("Not exported", [
        ("skip", "leave them (the outtakes)"),
        ("raw", "copy their RAW files and sidecars"),
        ("all", "copy every file beside the export folder")], session["unselected"])
    say("  Folders that have no export folder at all:")
    session["unexported"] = choose("Without export", [
        ("skip", "only list them in the report"),
        ("copy", "copy everything in them")], session["unexported"])
    session["c1_cache"] = yes_no("Copy the Capture One Cache (previews, regenerable)?", session["c1_cache"])
    if verify:
        session["verify_mode"] = choose("Verify", [
            ("quick", "sizes and dates only (seconds)"),
            ("full", "re-read and hash both sides (hours on a big backup)")], session["verify_mode"])
        if session["verify_mode"] == "full":
            while True:
                answer = ask("Max GB to verify in this run (0 = everything)", session["max_gb"])
                try:
                    value = float(answer)
                except ValueError:
                    say("  Type a number >= 0.", "yellow")
                    continue
                if value < 0:
                    say("  Type a number >= 0.", "yellow")
                    continue
                session["max_gb"] = value
                break
    else:
        session["update_changed"] = yes_no(
            "Replace originals/exports that changed since the last backup "
            "(old copy kept in versions)?", session["update_changed"])
    return session


# ---- Flows -----------------------------------------------------------------------------------
_EXIT_TEXT = {
    0: "done",
    1: "finished with problems - read the summary above and the log",
    2: "stopped before copying anything (see the message above)",
    130: "interrupted",
}


def _with_backup_id(argv, backup_id):
    return argv + ["--expect-backup-id", backup_id] if backup_id else argv


def backup_flow(config, session=None, preset_name=None, backup_id=None):
    """Dry-run, then copy after a yes. Returns the backend's exit code of the last step."""
    if session is None:
        last = session_from(config["last"])
        if last["sources"] and last["dest"]:
            title("Back up")
            say(describe(last))
            session = last if yes_no("Use these settings?", True) else edit_session(last)
        else:
            session = edit_session(last)
    config["last"] = session
    save_config(config)

    title("Step 1/2 - what would be copied (dry-run, nothing is written)")
    rc = run_backend(_with_backup_id(build_argv(session, "dry-run"), backup_id))
    if rc == 2:
        say(f"Dry-run: {_EXIT_TEXT[2]}.", "red")
        return rc
    if rc == 130:
        return rc
    if rc == 1:
        say("Some folders or files could not be read: they would NOT be backed up (see the log).", "yellow")
        if not yes_no("Copy the rest anyway?", False):
            return rc
    if not yes_no("Step 2/2 - copy now?", True):
        say("Nothing copied.")
        return 0
    title("Copying")
    rc = run_backend(_with_backup_id(build_argv(session, "copy"), backup_id))
    say(f"Backup: {_EXIT_TEXT.get(rc, f'exit code {rc}')}.", "green" if rc == 0 else "yellow")
    if preset_name is None and rc in (0, 1):
        offer_preset(config, session, KIND_BACKUP)
    return rc


def verify_flow(config, session=None, backup_id=None):
    if session is None:
        session = edit_session(session_from(config["last"]), verify=True)
        config["last"] = session
        save_config(config)
    title(f"Verifying ({session['verify_mode']})")
    rc = run_backend(_with_backup_id(build_argv(session, "verify"), backup_id))
    if rc == 0:
        say("Verify: everything matches.", "green")
    elif rc == 1:
        say("Verify: problems found - see the statuses above and the _verify.csv.", "yellow")
    else:
        say(f"Verify: {_EXIT_TEXT.get(rc, f'exit code {rc}')}.", "red")
    return rc


def offer_preset(config, session, kind):
    if not yes_no("Save these settings as a preset?", False):
        return
    name = ask("Preset name").strip()
    if not name:
        return
    if name in config["presets"] and not yes_no(f"Replace the preset '{name}'?", False):
        return
    config["presets"][name] = dict(
        session, kind=kind, backup_id=_backend().read_backup_id(session["dest"]))
    save_config(config)
    say(f"Saved. Unattended: python photo_backup.py --run-preset \"{name}\"", "green")


def _locate_backup(preset):
    """The destination of a preset: its own path, or the same path on another
    connected drive when the backup ID matches there. None when not found."""
    dest = preset.get("dest", "")
    backup_id = preset.get("backup_id")
    if not backup_id:
        return dest
    if _backend().read_backup_id(dest) == backup_id:
        return dest
    rest = os.path.splitdrive(dest)[1].lstrip("\\/")
    for root, _label, _free, _total in list_drives():
        candidate = os.path.join(root, rest)
        if _backend().read_backup_id(candidate) == backup_id:
            say(f"Backup found on {candidate} (the preset says {dest})")
            return candidate
    return None


def _backup_not_found(name, preset):
    backup_id = preset.get("backup_id") or ""
    say(f"The backup of preset '{name}' (ID {backup_id[:8]}) is not on any connected drive.",
        "red")


def presets_menu(config):
    while True:
        title("Presets")
        names = sorted(config["presets"])
        if not names:
            say("  No presets yet - a backup offers to save one when it ends.")
        for index, name in enumerate(names, 1):
            preset = config["presets"][name]
            say(f"  [{index}] {name}  ({preset.get('kind', KIND_BACKUP)}: "
                f"{'; '.join(preset.get('sources', []))} -> {preset.get('dest', '')})")
        say("  [N] new verify preset   [D] delete   [0] back")
        answer = ask("Run which preset", "0").casefold()
        if answer == "0":
            return
        if answer == "d":
            name = ask("Name of the preset to delete").strip()
            if name in config["presets"] and yes_no(f"Delete '{name}'?", False):
                del config["presets"][name]
                save_config(config)
            continue
        if answer == "n":
            session = edit_session(session_from(config["last"]), verify=True)
            offer_preset(config, session, KIND_VERIFY)
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(names):
            name = names[int(answer) - 1]
            preset = config["presets"][name]
            dest = _locate_backup(preset)
            if dest is None:
                _backup_not_found(name, preset)
                continue
            session = session_from(preset)
            session["dest"] = dest
            backup_id = preset.get("backup_id")
            say(describe(session))
            if preset.get("kind") == KIND_VERIFY:
                verify_flow(config, session, backup_id=backup_id)
            else:
                backup_flow(config, session, preset_name=name, backup_id=backup_id)


# ---- Restore -----------------------------------------------------------------------------------
def _catalog_folder_rows(backup):
    catalog = _backend()._Catalog(backup, create=False, readonly=True)
    try:
        rows = catalog.all_rows()
    finally:
        catalog.close()
    firsts = {row.rel.split("\\")[0] for row in rows}
    depth = 2 if len(firsts) == 1 and rows else 1
    groups = {}
    for row in rows:
        parts = row.rel.replace("/", "\\").split("\\")[:-1]
        folder = "\\".join(parts[:depth]) if parts else ""
        count, nbytes = groups.get(folder, (0, 0))
        groups[folder] = (count + 1, nbytes + row.size)
    return sorted(groups.items())


def restore_flow(config):
    title("Restore from a backup")
    default = config["last"].get("dest", "")
    while True:
        backup = ask("Backup folder", default).strip().strip('"')
        if not backup:
            say("  Type the backup folder.", "yellow")
            continue
        try:
            folders = _catalog_folder_rows(backup)
        except (FileNotFoundError, RuntimeError) as exc:
            say(f"  {exc}", "yellow")
            continue
        break
    total = sum(count for _folder, (count, _bytes) in folders)
    say(f"  In the backup, by folder ({_backend()._num(total)} files):")
    for index, (folder, (count, nbytes)) in enumerate(folders, 1):
        say(f"    [{index}] {folder:<40} {_backend()._gb(nbytes):>10}  ({count} files)")
    answer = ask("What to restore (a folder from the list or a path inside it; "
                 "separate several with ';'; Enter = everything)", "")
    only = []
    for part in answer.split(";"):
        part = part.strip().strip('"')
        if not part:
            continue
        if part.isdigit() and 1 <= int(part) <= len(folders):
            only.append(folders[int(part) - 1][0])
        else:
            only.append(part)
    destination = choose("Where to restore", [
        ("original", "original place (where each file came from)"),
        ("folder", "another folder")], "original")
    argv = ["--backup", backup]
    for rel in only:
        argv += ["--only", rel]
    if destination == "original":
        argv.append("--to-original")
    else:
        to = ask("Folder to restore into").strip().strip('"')
        while not to:
            say("  Type a folder.", "yellow")
            to = ask("Folder to restore into").strip().strip('"')
        argv += ["--to", to]
    title("Step 1/2 - what would be restored (dry-run, nothing is written)")
    rc = run_restore(argv + ["--dry-run"])
    if rc == 2:
        say("Restore: stopped before restoring anything (see the message above).", "red")
        return rc
    if rc == 130:
        return rc
    if not yes_no("Step 2/2 - restore now?", True):
        say("Nothing restored.")
        return 0
    title("Restoring")
    rc = run_restore(argv + ["--yes"])
    say(f"Restore: {_EXIT_TEXT.get(rc, f'exit code {rc}')}.", "green" if rc == 0 else "yellow")
    return rc


def settings_menu(config):
    title("Settings")
    say(f"  Remembered choices and presets: {CONFIG_PATH}")
    say(f"  Backend: {BACKEND}  (v{_setting('__version__')})")
    say(f"  Logs: {_setting('LOG_DIR')}")
    say("  Defaults of selective_backup.py (edit the top of that file to change them):")
    for name in ("EXPORT_FORMATS", "EXPORT_NAME_CONTAINS", "EXCLUDE_EXPORT_SUBFOLDERS",
                 "UNSELECTED", "UNEXPORTED", "COPY_CAPTUREONE_CACHE",
                 "COPY_JXLPHOTO_DERIVATIVES", "AUTO_UPDATE",
                 "AUTO_UPDATE_EXTENSIONS", "SPACE_MARGIN_GB", "VERIFY_COPY"):
        say(f"    {name:<26} {_setting(name)!r}")
    if yes_no("Forget the remembered choices (presets are kept)?", False):
        config["last"] = {}
        save_config(config)
        say("Forgotten.")


def interactive():
    config = load_config()
    say(f"photo-backup menu {__version__}", "bold")
    while True:
        title("Menu")
        say("  [1] Back up (dry-run first, then copy)")
        say("  [2] Verify a backup")
        say("  [3] Presets")
        say("  [4] Settings")
        say("  [5] Restore from a backup")
        say("  [0] Exit")
        answer = ask("Choose", "1")
        if answer == "0":
            return 0
        if answer == "1":
            backup_flow(config)
        elif answer == "2":
            verify_flow(config)
        elif answer == "3":
            presets_menu(config)
        elif answer == "4":
            settings_menu(config)
        elif answer == "5":
            restore_flow(config)


def run_preset(name):
    """Unattended run (Task Scheduler): no dry-run step and no question; the
    backend still refuses to start when the destination is missing or full."""
    config = load_config()
    preset = config["presets"].get(name)
    if preset is None:
        say(f"No preset named '{name}'. Presets: {', '.join(sorted(config['presets'])) or '(none)'}",
            "red")
        return 2
    dest = _locate_backup(preset)
    if dest is None:
        _backup_not_found(name, preset)
        return 2
    session = session_from(preset)
    session["dest"] = dest
    backup_id = preset.get("backup_id")
    say(f"photo-backup preset '{name}' ({preset.get('kind', KIND_BACKUP)})")
    say(describe(session))
    if preset.get("kind") == KIND_VERIFY:
        return run_backend(_with_backup_id(build_argv(session, "verify"), backup_id))
    return run_backend(_with_backup_id(build_argv(session, "copy"), backup_id))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="photo_backup.py",
                                     description="Interactive menu for selective_backup.py.")
    parser.add_argument("--run-preset", metavar="NAME",
                        help="run a saved preset without questions (for the Task Scheduler)")
    parser.add_argument("--list-presets", action="store_true", help="list the saved presets")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    if args.list_presets:
        presets = load_config()["presets"]
        for name in sorted(presets):
            preset = presets[name]
            say(f"{name}\n    {preset.get('kind', KIND_BACKUP)}: "
                f"{'; '.join(preset.get('sources', []))} -> {preset.get('dest', '')}")
        if not presets:
            say("(no presets)")
        return 0
    if args.run_preset:
        return run_preset(args.run_preset)
    try:
        return interactive()
    except Abort:
        say("\nNo more input - leaving the menu.")
        return 2
    except KeyboardInterrupt:
        say("\nBye.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
