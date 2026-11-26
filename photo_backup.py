#!/usr/bin/env python3
"""photo_backup - interactive menu for selective_backup.py.

Back up, verify and keep presets without typing command lines. Every backup
starts with a dry-run of the same choices: you see what would be copied, what
would not and whether it fits, and only then decide to copy.

The menu never copies anything itself: it builds the command line and runs
selective_backup.py (or manifest_backup.py, mirror_backup.py, restore_backup.py,
captureone_backup.py) as a child process, so the backends' safety rules
(read-only source, space check; nothing deleted but by the mirror, through its
trash) apply unchanged. The defaults it shows are read from the scripts
themselves, never repeated here.

    python photo_backup.py                    # interactive menu
    python photo_backup.py --list-presets
    python photo_backup.py --run-preset NAME  # unattended (Task Scheduler)
"""

import argparse
import datetime
import importlib.util
import json
import os
import shutil
import sqlite3
import string
import subprocess
import sys

__version__ = "0.14.0-rc8"

# ---- Settings ------------------------------------------------------------------
CONFIG_PATH = os.environ.get("PHOTOBACKUP_CONFIG") or os.path.join(
    os.path.expanduser("~"), ".photo_backup_config.json")   # remembered choices + presets
BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selective_backup.py")
RESTORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "restore_backup.py")
CAPTUREONE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captureone_backup.py")
MANIFEST_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifest_backup.py")
MIRROR_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mirror_backup.py")
COPY_CAPTUREONE_SETTINGS = True # every backup also keeps Capture One's profiles, styles and settings
VERIFY_MODE = "quick"           # default verify mode offered by the menu: quick | full
MAX_ONLY_CHARS = 20000          # [5] Restore: the --only values of one command (Windows takes about 32 000 characters)
FORMAT_ALTERNATIVES = {         # offered next to the backend's EXPORT_FORMATS default
    "jxl>tif>jpg": "best per photo: the JXL master, else the TIFF, else the JPEG",
    "jxl>jpg": "the JXL, else the JPEG (never the TIFF)",
    "jxl+jpg": "both the JXL and the JPEG",
    "all": "everything inside the export folders",
}
UNSELECTED_TEXT = {             # what each --unselected value means (the values come from the backend)
    "skip": "leave them (the outtakes)",
    "raw": "copy their RAW files and sidecars",
    "raw_or_jpeg": "RAW + sidecars + every JPEG, TIFF, PNG, HEIC or PSD that has no RAW beside it",
    "raw_jpeg": "RAW + sidecars + every JPEG + every TIFF, PNG, HEIC or PSD that has no RAW beside it",
    "all": "copy every file beside the export folder",
}

KIND_BACKUP = "backup"
KIND_VERIFY = "verify"
KIND_MIRROR = "mirror"

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


def _backend_choices(dest):
    """The values the backend's parser accepts for one option (by its dest)."""
    for action in _backend()._build_parser()._actions:
        if action.dest == dest and action.choices:
            return list(action.choices)
    raise KeyError(dest)


_CAPTUREONE_MODULE = None


def _captureone_setting(name):
    """A setting of captureone_backup.py, read from the script itself."""
    global _CAPTUREONE_MODULE
    if _CAPTUREONE_MODULE is None:
        spec = importlib.util.spec_from_file_location("captureone_backup", CAPTUREONE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CAPTUREONE_MODULE = module
    return getattr(_CAPTUREONE_MODULE, name)


def _menu_title():
    """The console's title while the menu runs: its name and version (a
    script it ran shows its own; the menu takes the title back after it)."""
    try:
        _backend()._set_console_title(f"photo-backup {__version__}")
    except Exception:
        pass


def _remembered(key):
    """A path the menu remembers on this computer ([4] Settings), or ""."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            value = json.load(handle).get(key, "")
        return value if isinstance(value, str) else ""
    except (OSError, ValueError, AttributeError):
        return ""


def _remembered_jxlphoto():
    """The jxl-photo the menu remembers ([4] Settings, v0.14.0-rc4), or ""."""
    return _remembered("jxlphoto")


# the options of a run that backs nothing up: it never gets nor spends the
# one-run markers (_retry_marker, _reconvert_marker; review 261126, S3)
_NOT_A_BACKUP = ("--verify", "--analyze", "--show-trash", "--empty-trash")


def _run_child(script, argv):
    """Run one of the scripts with `argv`; its output (and its questions) go
    straight to the console. A script that converts or restores files kept as
    JXL gets the jxl-photo the menu remembers (`--jxlphoto`), and a backup the
    conversions' work folder it remembers (`--work-dir`, v0.14.0-rc5), unless
    `argv` names one: the paths of this computer stay out of the scripts. With
    the conversion off on this computer (`no_convert`, rc5) a backup gets
    `--no-convert`. Asked to try again the files jxl-photo could not convert
    ([4] Settings: _retry_marker, v0.14.0-rc6), the backups get
    `--retry-conversions` until one of them really runs (no dry-run, exit 0
    or 1); the same for `--reconvert-older` (_reconvert_marker). A verify, an
    analysis or the mirror's trash commands get neither (_NOT_A_BACKUP)."""
    argv = list(argv)
    remembered = _remembered_jxlphoto()
    if (remembered and "--jxlphoto" not in argv
            and script in (BACKEND, RESTORE, MANIFEST_BACKEND, MIRROR_BACKEND)):
        argv += ["--jxlphoto", remembered]
    work_dir = _remembered("work_dir")
    if (work_dir and "--work-dir" not in argv
            and script in (BACKEND, MANIFEST_BACKEND, MIRROR_BACKEND)):
        argv += ["--work-dir", work_dir]
    if (_remembered("no_convert") == "yes" and "--no-convert" not in argv
            and script in (BACKEND, MANIFEST_BACKEND, MIRROR_BACKEND)
            and "--verify" not in argv):
        argv.append("--no-convert")
    # the one-run markers go only to a run that backs up: never a verify, an
    # analysis or the mirror's trash commands, which would accept the flag and
    # spend the marker without converting anything (review 261126, S3)
    backs_up = (script in (BACKEND, MANIFEST_BACKEND, MIRROR_BACKEND)
                and not any(flag in argv for flag in _NOT_A_BACKUP)
                and "--no-convert" not in argv)
    retry = (os.path.isfile(_retry_marker()) and "--retry-conversions" not in argv
             and backs_up)
    if retry:
        argv.append("--retry-conversions")
    # the same for "make again the TIFF JXL made by an older jxl-photo" (261126)
    reconvert = (os.path.isfile(_reconvert_marker()) and "--reconvert-older" not in argv
                 and backs_up)
    if reconvert:
        argv.append("--reconvert-older")
    code = 130
    try:
        code = subprocess.run([sys.executable, script, *argv]).returncode
        return code
    except KeyboardInterrupt:
        return 130
    finally:
        _menu_title()
        if retry and "--dry-run" not in argv and code in (0, 1):
            try:                # that one run tried them again: done
                os.remove(_retry_marker())
            except OSError:
                pass
        if reconvert and "--dry-run" not in argv and code in (0, 1):
            try:                # that one run made them again: done
                os.remove(_reconvert_marker())
            except OSError:
                pass


def _reconvert_marker():
    """The file that asks the next backup to make again the TIFFs kept as JXL
    by an older jxl-photo (--reconvert-older, 261126): beside the menu's own
    file, apart from it, like _retry_marker."""
    return CONFIG_PATH + ".reconvert-older"


def _retry_marker():
    """The file that asks the next backup to try again the TIFFs and JPEGs
    jxl-photo could not convert in CONVERT_GIVE_UP_AFTER runs (v0.14.0-rc6):
    beside the menu's own file, apart from it -- the menu writes its settings
    from memory, which would bring back a flag removed on the disk."""
    return CONFIG_PATH + ".retry-conversions"


def run_backend(argv):
    """Run selective_backup.py with `argv`; its output goes straight to the console."""
    return _run_child(BACKEND, argv)


def run_restore(argv):
    """Run restore_backup.py with `argv`; its output goes straight to the console."""
    return _run_child(RESTORE, argv)


def run_captureone(argv):
    """Run captureone_backup.py with `argv`; its output goes straight to the console."""
    return _run_child(CAPTUREONE, argv)


def run_manifest(argv):
    """Run manifest_backup.py with `argv`; its output goes straight to the console."""
    return _run_child(MANIFEST_BACKEND, argv)


def run_mirror(argv):
    """Run mirror_backup.py with `argv`; its output (and its own questions, such
    as a number to type) go straight to the console."""
    return _run_child(MIRROR_BACKEND, argv)


_MIRROR_MODULE = None


def _mirror_setting(name):
    """A setting of mirror_backup.py, read from the script itself."""
    global _MIRROR_MODULE
    if _MIRROR_MODULE is None:
        spec = importlib.util.spec_from_file_location("mirror_backup", MIRROR_BACKEND)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MIRROR_MODULE = module
    return getattr(_MIRROR_MODULE, name)


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
    hint = "Y/n, Enter = yes" if default else "y/N, Enter = no"
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
        if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= len(options):
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
        "captureone": bool(COPY_CAPTUREONE_SETTINGS),
        "captureone_verify": bool(COPY_CAPTUREONE_SETTINGS),
        "update_changed": False,
        "verify_mode": VERIFY_MODE,
        "max_gb": _setting("VERIFY_MAX_GB"),
    }


def session_from(saved):
    session = default_session()
    saved = saved or {}
    session.update({key: value for key, value in saved.items() if key in session})
    if "captureone_verify" not in saved:
        # a config or preset written before the verify had its own key (S6)
        session["captureone_verify"] = session["captureone"]
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
             "--unexported", session["unexported"],
             # always one of the two forms: a True default at the top of
             # selective_backup.py must not turn a "no" shown here into a yes
             "--c1-cache" if session["c1_cache"] else "--no-c1-cache",
             "--derivatives" if session["derivatives"] else "--no-derivatives"]
    if mode == "verify":
        argv += ["--verify", session["verify_mode"]]
        if session["verify_mode"] == "full":
            argv += ["--max-gb", str(session["max_gb"])]
    else:
        argv.append("--dry-run" if mode == "dry-run" else "--yes")
        # in the dry-run too: the plan the user confirms must count the changed
        # files that the copy will replace (and the space they take)
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
        + _choice_text(session["derivatives"],
                       bool(_setting("COPY_JXLPHOTO_DERIVATIVES")), "COPY_JXLPHOTO_DERIVATIVES"),
        "  Capture One profiles, styles and settings: "
        + _choice_text(session["captureone"], bool(COPY_CAPTUREONE_SETTINGS),
                       "COPY_CAPTUREONE_SETTINGS"),
    ]
    return "\n".join(lines)


def _choice_text(value, default, setting):
    """'copy' / 'skip', naming the setting only when the value is its default:
    a choice remembered in the config is never blamed on the setting."""
    text = "copy" if value else "skip"
    return f"{text} ({setting})" if value == default else f"{text} (your choice)"


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


def ask_unselected(current):
    """--unselected, offering every value the backend accepts. The current value
    is always among them and marked: Enter keeps it (a value missing from the
    list used to make Enter choose the first one, 'skip')."""
    choices = _backend_choices("unselected")
    if current not in choices:
        current = _setting("UNSELECTED")
    say("  Originals of photos that were NOT exported:")
    return choose("Not exported", [(value, UNSELECTED_TEXT.get(value, "see the README"))
                                   for value in choices], current)


def ask_max_gb(current):
    while True:
        answer = ask("Max GB to verify in this run (0 = everything)", current)
        try:
            value = float(answer)
        except ValueError:
            say("  Type a number >= 0.", "yellow")
            continue
        if value < 0:
            say("  Type a number >= 0.", "yellow")
            continue
        return value


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
    session["unselected"] = ask_unselected(session["unselected"])
    say("  Folders that have no export folder at all:")
    session["unexported"] = choose("Without export", [
        ("skip", "only list them in the report"),
        ("copy", "copy everything in them")], session["unexported"])
    session["c1_cache"] = yes_no("Copy the Capture One Cache (previews, regenerable)?", session["c1_cache"])
    session["derivatives"] = yes_no(
        "Copy the jxl-photo derivatives (sRGB/mobile JXLs) of a master that is copied?",
        session["derivatives"])
    if verify:      # its own key: a "no" here never turns the backup's step off (S6)
        session["captureone_verify"] = yes_no(
            "Also verify Capture One's own files (profiles, styles, settings) when the backup "
            "has them?", session["captureone_verify"])
    else:
        session["captureone"] = yes_no(
            "Also back up Capture One's own files (profiles, styles, settings)?",
            session["captureone"])
    if verify:
        session["verify_mode"] = choose("Verify", [
            ("quick", "sizes and dates only (seconds)"),
            ("full", "re-read and hash both sides (hours on a big backup)")], session["verify_mode"])
        if session["verify_mode"] == "full":
            session["max_gb"] = ask_max_gb(session["max_gb"])
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


def captureone_step(session, mode):
    """Keep Capture One's own files in the same backup. mode: 'dry-run' or 'copy'."""
    if not session["captureone"]:
        return 0
    title("Capture One profiles, styles and settings")
    rc = run_captureone(["--dest", session["dest"], "--dry-run" if mode == "dry-run" else "--yes"])
    if rc != 0:
        say(f"Capture One: {_EXIT_TEXT.get(rc, f'exit code {rc}')}.", "yellow")
    return rc


def captureone_verify_step(session):
    """Verify Capture One's files too, when this backup holds them."""
    if not session.get("captureone_verify", session["captureone"]):
        return 0
    if not os.path.isdir(os.path.join(session["dest"], _captureone_setting("BACKUP_SUBFOLDER"))):
        return 0
    title("Verifying the Capture One files")
    return run_captureone(["--verify", session["dest"]])


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
    captureone_step(session, "dry-run")
    if rc == 1:
        say("Some folders or files could not be read: they would NOT be backed up (see the log).", "yellow")
        if not yes_no("Copy the rest anyway?", False):
            return rc
    if not yes_no("Step 2/2 - copy now?", True):
        say("Nothing copied.")
        return 0
    title("Copying")
    rc = run_backend(_with_backup_id(build_argv(session, "copy"), backup_id))
    if rc in (0, 1):
        rc = max(rc, 1 if captureone_step(session, "copy") else 0)
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
    if rc in (0, 1):
        rc = max(rc, 1 if captureone_verify_step(session) else 0)
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
    say(f"Saved. Unattended: {_backend()._run_command('photo_backup.py')} --run-preset "
        f"\"{name}\"", "green")


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
            say(f"  [{index}] {_preset_line(name, config['presets'][name])}")
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
        if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= len(names):
            name = names[int(answer) - 1]
            preset = config["presets"][name]
            dest = _locate_backup(preset)
            if dest is None:
                _backup_not_found(name, preset)
                continue
            if preset.get("kind") == KIND_MIRROR:
                mirror_flow(config, manifest=preset.get("manifest", ""), dest=dest,
                            backup_id=preset.get("backup_id"), preset_name=name,
                            captureone=preset.get("captureone", COPY_CAPTUREONE_SETTINGS))
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
def _kept_as_jxl(backup):
    """(TIFFs, JPEGs, lossy or unknown TIFFs) the backup keeps as JXL, counted
    once per file (not per page)."""
    catalog = _backend()._Catalog(backup, create=False, readonly=True)
    try:
        converted = catalog.converted_rows()
    except sqlite3.Error:
        return 0, 0, 0
    finally:
        catalog.close()
    first = [item for item in converted.values() if item.page == 0]
    tiffs = [item for item in first if item.kind != "jpeg"]
    jpegs = [item for item in first if item.kind == "jpeg"]
    return len(tiffs), len(jpegs), sum(1 for item in tiffs if item.lossless != 1)


def _catalog_folder_rows(backup):
    """The backup by folder: [(label, files, bytes, only), ...], `only` being
    the --only values that restore exactly that line. The files that sit
    above the listed folders (loose in the year, or in the backup's root) get
    a line of their own, restored file by file: --only <their folder> would
    restore everything below it."""
    catalog = _backend()._Catalog(backup, create=False, readonly=True)
    try:
        rows = catalog.all_rows()
    except sqlite3.Error as exc:
        raise RuntimeError(f"cannot read catalog {catalog.path}: {exc}")
    finally:
        catalog.close()
    firsts = {row.rel.split("\\")[0] for row in rows}
    depth = 2 if len(firsts) == 1 and rows else 1
    groups = {}
    for row in rows:
        parts = row.rel.replace("/", "\\").split("\\")[:-1]
        loose = len(parts) < depth
        folder = "\\".join(parts[:depth])
        entry = groups.setdefault((folder, loose), [0, 0, []])
        entry[0] += 1
        entry[1] += row.size
        if loose:
            entry[2].append(row.rel)
    listed = []
    for (folder, loose), (count, nbytes, files) in sorted(groups.items()):
        if loose:
            where = folder or "the backup's root"
            listed.append((f"(files directly in {where})", count, nbytes, files))
        else:
            listed.append((folder, count, nbytes, [folder]))
    return listed


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
    total = sum(count for _folder, count, _bytes, _only in folders)
    say(f"  In the backup, by folder ({_backend()._num(total)} files):")
    for index, (folder, count, nbytes, _only) in enumerate(folders, 1):
        say(f"    [{index}] {folder:<40} {_backend()._gb(nbytes):>10}  ({count} files)")
    answer = ask("What to restore (a folder from the list or a path inside it; "
                 "separate several with ';'; Enter = everything)", "")
    only = []
    for part in answer.split(";"):
        part = part.strip().strip('"')
        if not part:
            continue
        if part.isascii() and part.isdigit() and 1 <= int(part) <= len(folders):
            only.extend(folders[int(part) - 1][3])
        else:
            only.append(part)
    if len(subprocess.list2cmdline(only)) > MAX_ONLY_CHARS:
        say("  Too many loose files for one command: restore the folder above them "
            "(type its path), or everything.", "yellow")
        return 2
    destination = choose("Where to restore", [
        ("original", "original place (where each file came from)"),
        ("folder", "another folder")], "original")
    argv = ["--backup", backup]
    for rel in only:
        argv += ["--only", rel]
    tiffs, jpegs, lossy = _kept_as_jxl(backup)
    if tiffs or jpegs:
        say(f"  This backup keeps {tiffs} TIFF ({lossy} of them lossy) and {jpegs} JPEG as JXL.")
        argv += ["--convert-back", choose("Those files", [
            ("all", "back to TIFF / JPEG, every one (a JPEG byte for byte)"),
            ("lossless", "back only what is lossless (every JPEG, the lossless TIFFs); the lossy "
                         "TIFFs stay JXL"),
            ("none", "keep every one as JXL (photo.tif.jxl, photo.jpg.jxl)")], "all")]
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


def manifest_flow(config):
    """Each folder of a manifest with its own mode: analyze, or dry-run then copy."""
    title("Manifest - each folder with its own mode")
    default = config.get("last_manifest") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "backup_manifest.csv")
    while True:
        manifest = ask("Manifest file", default).strip().strip('"')
        if manifest:
            break
        say("  Type the manifest file.", "yellow")
    config["last_manifest"] = manifest
    dest = ask_dest(config.get("last_manifest_dest") or config["last"].get("dest", ""),
                    must_exist=False)
    config["last_manifest_dest"] = dest
    save_config(config)
    say("  [1] Analyze only (how many bytes each mode would take in each folder)")
    say("  [2] Back up (dry-run first, then copy)")
    say("  [3] Verify the backup against this manifest")
    choice = ask("Choose", "2")
    if choice == "1":
        rc = run_manifest(["--manifest", manifest, "--analyze"])
        say(f"Analyze: {_EXIT_TEXT.get(rc, f'exit code {rc}')}.",
            "green" if rc == 0 else "yellow")
        return rc
    if choice == "3":
        mode = choose("Verify", [
            ("quick", "sizes and dates only (seconds)"),
            ("full", "re-read and hash both sides (hours on a big backup)")], VERIFY_MODE)
        argv = ["--manifest", manifest, "--dest", dest, "--verify", mode]
        if mode == "full":
            argv += ["--max-gb", str(ask_max_gb(_setting("VERIFY_MAX_GB")))]
        step = {"dest": dest, "captureone": False, "captureone_verify": False}
        if os.path.isdir(os.path.join(dest, _captureone_setting("BACKUP_SUBFOLDER"))):
            remembered = config.get("last_manifest_captureone_verify",
                                    config.get("last_manifest_captureone",
                                               COPY_CAPTUREONE_SETTINGS))
            step["captureone_verify"] = yes_no(
                "Also verify Capture One's own files (profiles, styles, settings)?",
                bool(remembered))
            config["last_manifest_captureone_verify"] = step["captureone_verify"]
            save_config(config)
        title(f"Verifying ({mode})")
        rc = run_manifest(argv)
        if rc in (0, 1):
            rc = max(rc, 1 if captureone_verify_step(step) else 0)
        if rc == 0:
            say("Verify: everything matches.", "green")
        elif rc == 1:
            say("Verify: problems found - see the statuses above and the _verify.csv.", "yellow")
        else:
            say(f"Verify: {_EXIT_TEXT.get(rc, f'exit code {rc}')}.", "red")
        return rc
    if choice != "2":
        return 0
    # the same steps as [1]: Capture One's own files, and replacing what changed
    step = {"dest": dest,
            "captureone": yes_no("Also back up Capture One's own files (profiles, styles, "
                                 "settings)?",
                                 bool(config.get("last_manifest_captureone",
                                                 COPY_CAPTUREONE_SETTINGS)))}
    update_changed = yes_no("Replace originals/exports that changed since the last backup "
                            "(old copy kept in versions)?",
                            bool(config.get("last_manifest_update_changed", False)))
    config["last_manifest_captureone"] = step["captureone"]
    config["last_manifest_update_changed"] = update_changed
    save_config(config)
    title("Step 1/2 - what would be copied (dry-run, nothing is written)")
    # the same choices as the copy below: what is confirmed is what runs
    update_argv = ["--update-changed"] if update_changed else []
    rc = run_manifest(["--manifest", manifest, "--dest", dest, "--dry-run"] + update_argv)
    if rc == 2:
        say(f"Dry-run: {_EXIT_TEXT[2]}.", "red")
        return rc
    if rc == 130:
        return rc
    captureone_step(step, "dry-run")
    if rc == 1:
        say("Some folders or files could not be read: they would NOT be backed up "
            "(see the log).", "yellow")
        if not yes_no("Copy the rest anyway?", False):
            return rc
    if not yes_no("Step 2/2 - copy now?", True):
        say("Nothing copied.")
        return 0
    title("Copying")
    rc = run_manifest(["--manifest", manifest, "--dest", dest, "--yes"] + update_argv)
    if rc in (0, 1):
        rc = max(rc, 1 if captureone_step(step, "copy") else 0)
    say(f"Manifest backup: {_EXIT_TEXT.get(rc, f'exit code {rc}')}.",
        "green" if rc == 0 else "yellow")
    return rc


# ---- Mirror ----------------------------------------------------------------------------------------
_MIRROR_EXIT_TEXT = {
    0: "done",
    1: "finished with problems - read the box above and the log",
    2: "stopped: nothing changed in the backup (see the message above)",
    130: "interrupted",
}


def _trash_sentence():
    """What happens to what leaves the backup, with the day it is deleted."""
    keep_days = _mirror_setting("MIRROR_KEEP_DAYS")
    if not keep_days:
        return "what leaves the backup is DELETED FOR GOOD (no trash: MIRROR_KEEP_DAYS = 0)"
    until = datetime.date.today() + datetime.timedelta(days=keep_days)
    return f"what leaves the backup goes to its trash until {until:%Y-%m-%d}"


def mirror_menu(config):
    while True:
        title("Mirror - the backup follows a manifest")
        say("  What changed on the source is replaced; what is gone leaves the backup.")
        keep_days = _mirror_setting("MIRROR_KEEP_DAYS")
        if keep_days:
            say(f"  It stays {keep_days} days in the backup's trash first (MIRROR_KEEP_DAYS).")
        else:
            say("  No trash: it is deleted for good at once (MIRROR_KEEP_DAYS = 0).", "yellow")
        say("  [1] Mirror (dry-run first, then mirror)")
        say("  [2] Total mirror (also removes what came from folders not in this manifest)")
        say("  [3] What is in the trash (and how to get files back)")
        say("  [4] Empty the trash")
        say("  [0] Back")
        answer = ask("Choose", "0")
        if answer == "0":
            return
        if answer == "1":
            mirror_flow(config)
        elif answer == "2":
            mirror_flow(config, total=True)
        elif answer == "3":
            show_trash_flow(config)
        elif answer == "4":
            empty_trash_flow(config)


def _mirror_dest_default(config):
    return (config.get("last_mirror_dest") or config.get("last_manifest_dest")
            or config["last"].get("dest", ""))


def mirror_flow(config, total=False, manifest=None, dest=None, backup_id=None,
                preset_name=None, captureone=None):
    """Dry-run, then the mirror after an explicit yes (Enter = no). The mirror
    itself asks for a typed number when something needs it."""
    if manifest is None:
        title("Total mirror" if total else "Mirror")
        if total:
            say("  A total mirror also removes from the backup what came from folders that are "
                "not in this manifest.", "yellow")
        default = config.get("last_mirror_manifest") or config.get("last_manifest") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "backup_manifest.csv")
        while True:
            manifest = ask("Manifest file", default).strip().strip('"')
            if manifest:
                break
            say("  Type the manifest file.", "yellow")
        dest = ask_dest(_mirror_dest_default(config), must_exist=False)
        config["last_mirror_manifest"] = manifest
        config["last_mirror_dest"] = dest
    if captureone is None:
        captureone = yes_no("Also back up Capture One's own files (profiles, styles, settings)?",
                            bool(config.get("last_mirror_captureone", COPY_CAPTUREONE_SETTINGS)))
        config["last_mirror_captureone"] = captureone
    save_config(config)
    step = {"dest": dest, "captureone": bool(captureone)}
    base = _with_backup_id(["--manifest", manifest, "--dest", dest]
                           + (["--total"] if total else []), backup_id)
    # One run: the mirror shows what it will do and asks right below its box,
    # with the numbers of this very run (finding 5 of the rc1 review: a dry-run
    # confirmed here, then a real run with --yes, removed what changed in
    # between without asking). Nothing else prints between the box and the
    # question (finding 10).
    title("Mirror - what will change, then one question (Enter = no)")
    say(f"  {_trash_sentence()[0].upper()}{_trash_sentence()[1:]}.")
    rc = run_mirror(base)
    if rc == 2:
        say(f"Mirror: {_MIRROR_EXIT_TEXT[2]}.", "yellow")
        return 0 if preset_name is None else rc
    if rc in (0, 1):
        rc = max(rc, 1 if captureone_step(step, "copy") else 0)
    say(f"Mirror: {_MIRROR_EXIT_TEXT.get(rc, f'exit code {rc}')}.", "green" if rc == 0 else "yellow")
    if preset_name is None and not total and rc in (0, 1):
        offer_mirror_preset(config, manifest, dest, captureone)
    return rc


def show_trash_flow(config):
    title("What is in the trash")
    dest = ask_dest(_mirror_dest_default(config), must_exist=True)
    config["last_mirror_dest"] = dest
    save_config(config)
    rc = run_mirror(["--dest", dest, "--show-trash"])
    if rc != 0:
        say(f"Trash: {_MIRROR_EXIT_TEXT.get(rc, f'exit code {rc}')}.", "yellow")
    return rc


def empty_trash_flow(config):
    title("Empty the trash")
    say("  What is deleted here can NOT be recovered. The mirror empties the trash by itself "
        "when its date comes.", "yellow")
    dest = ask_dest(_mirror_dest_default(config), must_exist=True)
    config["last_mirror_dest"] = dest
    save_config(config)
    rc = run_mirror(["--dest", dest, "--empty-trash", "--dry-run"])
    if rc != 0:
        say(f"Trash: {_MIRROR_EXIT_TEXT.get(rc, f'exit code {rc}')}.", "yellow")
        return rc
    while True:
        before = ask("Delete only what was taken out before this day (YYYY-MM-DD, "
                     "Enter = everything listed)", "").strip()
        if not before:
            break
        try:
            datetime.datetime.strptime(before, "%Y-%m-%d")
            break
        except ValueError:
            say("  Write the day as YYYY-MM-DD, or press Enter.", "yellow")
    rc = run_mirror(["--dest", dest, "--empty-trash"] + (["--before", before] if before else []))
    say(f"Trash: {_MIRROR_EXIT_TEXT.get(rc, f'exit code {rc}')}.", "green" if rc == 0 else "yellow")
    return rc


def offer_mirror_preset(config, manifest, dest, captureone):
    if not yes_no("Save this mirror as a preset (to run it unattended)?", False):
        return
    name = ask("Preset name").strip()
    if not name:
        return
    if name in config["presets"] and not yes_no(f"Replace the preset '{name}'?", False):
        return
    config["presets"][name] = {"kind": KIND_MIRROR, "manifest": os.path.abspath(manifest),
                               "dest": dest, "captureone": bool(captureone),
                               "backup_id": _backend().read_backup_id(dest)}
    save_config(config)
    say(f"Saved. Unattended: {_backend()._run_command('photo_backup.py')} --run-preset "
        f"\"{name}\"", "green")
    say("  Unattended, the mirror never deletes for good before the trash's date and stops "
        "(changing nothing) when something needs a typed number.")


def _preset_line(name, preset):
    if preset.get("kind") == KIND_MIRROR:
        return f"{name}  (mirror: {preset.get('manifest', '')} -> {preset.get('dest', '')})"
    return (f"{name}  ({preset.get('kind', KIND_BACKUP)}: "
            f"{'; '.join(preset.get('sources', []))} -> {preset.get('dest', '')})")


def _captureone_folder(config, must_exist):
    """The folder of a Capture One job; the last one is offered again."""
    folder = ask_dest(config.get("captureone_folder") or config["last"].get("dest", ""), must_exist)
    config["captureone_folder"] = folder
    save_config(config)
    return folder


def _captureone_job(argv, what):
    """Dry-run, then the real thing after a yes."""
    title("Step 1/2 - dry-run, nothing is written")
    rc = run_captureone(argv + ["--dry-run"])
    if rc in (2, 130):
        say(f"{what}: {_EXIT_TEXT.get(rc, f'exit code {rc}')}.", "red")
        return rc
    if not yes_no("Step 2/2 - do it now?", True):
        say("Nothing changed.")
        return 0
    title(what)
    rc = run_captureone(argv + ["--yes"])
    say(f"{what}: {_EXIT_TEXT.get(rc, f'exit code {rc}')}.", "green" if rc == 0 else "yellow")
    return rc


def _captureone_restore_argv(config):
    backup = _captureone_folder(config, must_exist=True)
    say("  Also restore (optional, separated with ';'): "
        f"{_captureone_setting('INSTALLER_FOLDER')}, Preferences, a folder like Batch*.")
    extras = [part.strip() for part in ask("Also restore", "").split(";") if part.strip()]
    argv = ["--restore-from", backup]
    for name in extras:
        argv += ["--also", name]
    return argv


def captureone_menu(config):
    while True:
        title("Capture One only")
        say("  [1] Back up only Capture One, to a folder you choose")
        say("  [2] Restore from a folder (only adds what is missing)")
        say("  [3] Force restore (also replaces files that differ; the current ones are kept)")
        say("  [4] Undo the last force restore")
        say("  [0] Back")
        answer = ask("Choose", "0")
        if answer == "0":
            return
        if answer == "1":
            _captureone_job(["--dest", _captureone_folder(config, must_exist=False)], "Capture One backup")
        elif answer == "2":
            say("  Close Capture One first. Nothing that already exists is overwritten.")
            _captureone_job(_captureone_restore_argv(config), "Restore")
        elif answer == "3":
            say("  Close Capture One first. What gets replaced is kept and can be undone with [4].")
            argv = _captureone_restore_argv(config) + ["--force"]
            if yes_no("Also replace the factory profiles of the installed Capture One "
                      "(needs administrator; a newer version's would be replaced by older ones)?",
                      False):
                argv.append("--force-installed")
            _captureone_job(argv, "Force restore")
        elif answer == "4":
            say("  To skip a snapshot, rename its folder so it ends with "
                f"{_captureone_setting('DISABLED_SUFFIX')}.")
            _captureone_job(["--revert-last"], "Undo")


def settings_menu(config):
    title("Settings")
    say(f"  Remembered choices and presets: {CONFIG_PATH}")
    say(f"  Backend: {BACKEND}  (v{_setting('__version__')})")
    say(f"  Logs: {_setting('LOG_DIR')}")
    say("  Defaults of selective_backup.py (edit the top of that file to change them):")
    for name in ("EXPORT_FORMATS", "EXPORT_NAME_CONTAINS", "EXCLUDE_EXPORT_SUBFOLDERS",
                 "UNSELECTED", "UNEXPORTED", "COPY_CAPTUREONE_CACHE",
                 "COPY_JXLPHOTO_DERIVATIVES", "AUTO_UPDATE",
                 "AUTO_UPDATE_EXTENSIONS", "SPACE_MARGIN_GB", "VERIFY_COPY", "ADOPT_BY_HASH"):
        say(f"    {name:<26} {_setting(name)!r}")
    say("  TIFF and JPEG kept as JXL in the backup (a manifest's Tiff and Jpeg columns):")
    for name in ("JXLPHOTO", "JXLPHOTO_MIN_VERSION", "TIFF_JXL_PROFILES", "JPEG_JXL_EXTENSIONS",
                 "JXL_WORK_DIR", "KEEP_TOOLS_IN_BACKUP", "KEEP_TOOLS_EXTRA"):
        say(f"    {name:<26} {_setting(name)!r}")
    remembered = config.get("jxlphoto") or ""
    say(f"  jxl-photo the menu gives the scripts: {remembered or '(none: JXLPHOTO above)'}")
    if yes_no("Set where jxl-photo is (to keep TIFF and JPEG as JXL, and to restore them)?",
              False):
        path = ask("Its jxl_photo.py or jxl-photo.exe (type - for none)", remembered)
        path = "" if path.strip() == "-" else path.strip().strip('"')
        problem = _backend()._jxlphoto_problem(path) if path else None
        if problem:
            say(f"  Not saved: {problem}", "yellow")
        else:
            config["jxlphoto"] = path
            save_config(config)
            say(f"  Saved: {path}" if path else "  Forgotten: the scripts use JXLPHOTO.")
    work_dir = config.get("work_dir") or ""
    say(f"  Work folder of the conversions on this computer: "
        f"{work_dir or '(none: JXL_WORK_DIR above; empty = the system temp folder)'}")
    if yes_no("Set where the conversions run (each JXL is written there first)?", False):
        path = ask("A folder that exists (type - for none)", work_dir)
        path = "" if path.strip() == "-" else path.strip().strip('"')
        if path and not os.path.isdir(path):
            say(f"  Not saved: not a folder that exists: {path}", "yellow")
        else:
            config["work_dir"] = path
            save_config(config)
            say(f"  Saved: {path}" if path else "  Forgotten: the scripts use JXL_WORK_DIR.")
    off = config.get("no_convert") == "yes"
    say(f"  Conversion to JXL on this computer: "
        f"{'OFF (TIFF and JPEG copied as they are)' if off else 'on'}")
    say("    Off: for a slow computer (a trip). The JXL already in the backup stay; a new or "
        "changed TIFF or JPEG is copied as it is, and a backup with it on converts it later.")
    if yes_no(f"Turn the conversion {'back on' if off else 'off'} on this computer?", False):
        config["no_convert"] = "" if off else "yes"
        save_config(config)
        say("  Conversion on: TIFF and JPEG are kept as JXL again." if off
            else "  Conversion off: the backups copy TIFF and JPEG as they are.")
    asked = os.path.isfile(_retry_marker())
    give_up = _setting("CONVERT_GIVE_UP_AFTER")
    say(f"  Files jxl-photo could not convert in {give_up} backups in a row: "
        f"{'tried again by the next backup' if asked else 'kept as they are, not tried again'}")
    if yes_no("Cancel trying them again?" if asked else
              "Try them again in the next backup (a new jxl-photo or libjxl, say)?", False):
        try:
            if asked:
                os.remove(_retry_marker())
            else:
                with open(_retry_marker(), "w", encoding="utf-8") as handle:
                    handle.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}: the "
                                 f"next backup tries again the files jxl-photo could not convert "
                                 f"(--retry-conversions); removed when it has run\n")
            say("  Cancelled." if asked else
                "  The next backup (or manifest, or mirror) tries them again.")
        except OSError as exc:
            say(f"  Not saved: {exc}", "yellow")
    again = os.path.isfile(_reconvert_marker())
    say(f"  TIFF kept as JXL by an older jxl-photo: "
        f"{'made again by the next backup' if again else 'kept as they are'}")
    if yes_no("Cancel making them again?" if again else
              "Make again the TIFF JXL made by an older jxl-photo (next backup only)?", False):
        try:
            if again:
                os.remove(_reconvert_marker())
            else:
                with open(_reconvert_marker(), "w", encoding="utf-8") as handle:
                    handle.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}: the "
                                 f"next backup makes again the TIFF JXL made by an older "
                                 f"jxl-photo (--reconvert-older); removed when it has run\n")
            say("  Cancelled." if again else
                "  The next backup (or manifest, or mirror) makes them again.")
        except OSError as exc:
            say(f"  Not saved: {exc}", "yellow")
    say("  Defaults of mirror_backup.py (edit the top of that file to change them):")
    for name in ("MIRROR_KEEP_DAYS", "MIRROR_MAX_DELETE_PERCENT", "MIRROR_MAX_DELETE_MIN"):
        say(f"    {name:<26} {_mirror_setting(name)!r}")
    if yes_no("Forget the remembered choices (presets are kept)?", False):
        config["last"] = {}
        save_config(config)
        say("Forgotten.")


def interactive():
    config = load_config()
    say(f"photo-backup menu {__version__}", "bold")
    while True:
        title("Menu")
        if config.get("no_convert") == "yes":
            say("  Conversion to JXL is OFF on this computer ([4] Settings to turn it on)",
                "yellow")
        say("  [1] Back up (dry-run first, then copy)")
        say("  [2] Verify a backup")
        say("  [3] Presets")
        say("  [4] Settings")
        say("  [5] Restore from a backup")
        say("  [6] Capture One only (back up, restore, force, undo)")
        say("  [7] Manifest (each folder with its own mode)")
        say("  [8] Mirror (the backup follows a manifest; removes what is gone, through a trash)")
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
        elif answer == "6":
            captureone_menu(config)
        elif answer == "7":
            manifest_flow(config)
        elif answer == "8":
            mirror_menu(config)


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
    backup_id = preset.get("backup_id")
    if preset.get("kind") == KIND_MIRROR:
        say(f"photo-backup preset '{name}' (mirror, unattended)")
        say(f"  Manifest:     {preset.get('manifest', '')}\n  Destination:  {dest}")
        rc = run_mirror(_with_backup_id(["--manifest", preset.get("manifest", ""), "--dest", dest,
                                         "--unattended"], backup_id))
        if rc in (0, 1):
            step = {"dest": dest,
                    "captureone": bool(preset.get("captureone", COPY_CAPTUREONE_SETTINGS))}
            rc = max(rc, 1 if captureone_step(step, "copy") else 0)
        return rc
    session = session_from(preset)
    session["dest"] = dest
    say(f"photo-backup preset '{name}' ({preset.get('kind', KIND_BACKUP)})")
    say(describe(session))
    if preset.get("kind") == KIND_VERIFY:
        rc = run_backend(_with_backup_id(build_argv(session, "verify"), backup_id))
        if rc in (0, 1):
            rc = max(rc, 1 if captureone_verify_step(session) else 0)
        return rc
    rc = run_backend(_with_backup_id(build_argv(session, "copy"), backup_id))
    if rc in (0, 1):
        rc = max(rc, 1 if captureone_step(session, "copy") else 0)
    return rc


def main(argv=None):
    parser = argparse.ArgumentParser(prog="photo_backup.py",
                                     description="Interactive menu for selective_backup.py.")
    parser.add_argument("--run-preset", metavar="NAME",
                        help="run a saved preset without questions (for the Task Scheduler)")
    parser.add_argument("--list-presets", action="store_true", help="list the saved presets")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    try:
        _backend()._start_output()
    except Exception:
        pass
    if args.list_presets:
        presets = load_config()["presets"]
        for name in sorted(presets):
            preset = presets[name]
            if preset.get("kind") == KIND_MIRROR:
                say(f"{name}\n    mirror: {preset.get('manifest', '')} -> {preset.get('dest', '')}")
                continue
            say(f"{name}\n    {preset.get('kind', KIND_BACKUP)}: "
                f"{'; '.join(preset.get('sources', []))} -> {preset.get('dest', '')}")
        if not presets:
            say("(no presets)")
        return 0
    _menu_title()
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
