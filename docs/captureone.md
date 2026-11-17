# Capture One's own files (`captureone_backup.py`) *(v0.6.0)*

The `.cos` files beside your photos hold the edits, but they only **name** the ICC profile, film curve and style they use. On a computer without those files, Capture One shows the edit differently. Every backup from the menu's `[1] Back up` and `[7] Manifest` (and every preset) therefore also keeps, in `<destination>\_captureone`:

| Folder in the backup | Where it comes from | What it holds |
|---|---|---|
| `UserData` | `%LOCALAPPDATA%\CaptureOne` | your profiles, styles (with the profiles they carry), defaults, presets, recipes, shortcuts, workspaces, plugins, any folder you add |
| `Preferences` | `%LOCALAPPDATA%\Capture_One` | `user.config` of each version |
| `Installed` | `Color Profiles` and `Film Curves` of the install folder | the profiles the `.cos` files name, in case a later version drops one |
| `WindowsColor` | the Windows colour folder | its `.icc`/`.icm` files (export and monitor profiles) |

The command-line scripts (`selective_backup.py`, `manifest_backup.py`) do not keep these: with them, also run `[6] Capture One only` (or `captureone_backup.py --dest`) to the same destination.

Left out on purpose: login data and cookies (`MSALCache*`, the WebView folders), the licence file, `Logs`, `Stability`, and the program itself. `WHERE_EACH_FOLDER_GOES.txt` in that folder says where each part came from.

A link or junction inside a Capture One folder is not followed, and the summary says so *(v0.13.1)*: `Links/junctions not followed: N (what is behind them is NOT in this backup)`, with their paths (all of them in the log). A link where the backup never goes anyway (`Logs`, `EBWebView`, a folder inside the Windows colour folder, an excluded file) is not listed *(v0.13.2)*. A Capture One folder that cannot be reached is an error (exit 1), not "nothing found", and the files of the backup under a folder that could not be read in this run are not counted as "gone from Capture One" *(v0.13.1)*.

A `--dest` that is really inside a Capture One folder (through a junction or a `subst` letter), or a `--to` that is really inside the backup, is refused with exit 2, and the message shows where the path leads *(v0.13.2)*. A `--dry-run` checks the catalog that is already there, as the copy does: a damaged one, or one with a path that leads outside the backup, ends it with exit 2 *(v0.13.2)*. `RESTORE_CAPTUREONE.cmd` is written before `WHERE_EACH_FOLDER_GOES.txt`, and a read-only old copy of either is replaced; one that cannot be written is said, and the run ends with exit 1 — the copied files stay in the backup and the next run writes it again *(v0.13.2; it was a traceback after the copy)*.

Keep the installer and the serial in a folder inside `%LOCALAPPDATA%\CaptureOne` (`_Instalador` by default): they are copied with the rest.

## On a new computer

1. Install Capture One from `_captureone\UserData\_Instalador`.
2. **Before opening it**, double-click `_captureone\RESTORE_CAPTUREONE.cmd`. It needs no Python, asks Windows for administrator rights and only adds missing files. (Opening Capture One first creates its default recipes, and a file that already exists is never replaced.) The script and `WHERE_EACH_FOLDER_GOES.txt` are written from everything the backup holds *(v0.13.1)*: a run that does not find a folder on this computer (Capture One was reset, another Windows user ran it) no longer drops that folder from them.
3. Open Capture One and activate it.

## With Python

With Python, the same restore checks every file against the catalog:

```console
python captureone_backup.py --dest H:\BACKUP [--dry-run] [--yes]
python captureone_backup.py --verify H:\BACKUP
python captureone_backup.py --restore-from H:\BACKUP [--dry-run] [--yes]
python captureone_backup.py --restore-from H:\BACKUP --also _Instalador --also Preferences --to D:\Out
python captureone_backup.py --restore-from H:\BACKUP --force [--force-installed]
python captureone_backup.py --revert-last
```

`PATH` can be any folder, for a backup of Capture One alone: the files go to `PATH\_captureone`, and giving that folder itself works too. From the menu: `[6] Capture One only` ([menu.md](menu.md)).

| Option | Meaning |
|---|---|
| `--dest PATH` | back up to `PATH\_captureone` |
| `--verify PATH` | re-read and hash the backup under `PATH` against its catalog. A row without a hash (the catalog was made again, or a run was stopped) is checked against its source on this computer: when the source still has the size and date of the row, its bytes are compared with the copy — the same bytes record the hash (`HASH_FILLED`), other bytes are `DEST_DIFFERS` (exit 1); otherwise only the size is checked (`OK_SIZE_ONLY`, exit 0) and the summary says how many files were not checked by hash. On a backup that cannot be written, the rows proven by their source are `HASH_NOT_RECORDED` (exit 0, with a line saying so) *(v0.13.1)* |
| `--restore-from PATH` | restore from the backup under `PATH` |
| `--also NAME` | restore this part too: `Preferences`, or a folder of `UserData` (`_Instalador`, `Batch*`); repeatable |
| `--to PATH` | restore under `PATH` instead of the original places. A file already there that differs is always a `CONFLICT` (exit 1); `KEPT_INSTALLED` only applies to the real places *(v0.13.1)* |
| `--force` | with `--restore-from`: also replace files that differ, in your own folders; the current ones are kept first |
| `--force-installed` | with `--force`: also the install folder's profiles and the Windows colour folder's, as administrator. Never a monitor calibration |
| `--revert-last` | undo the last `--force` |
| `--install-folder PATH` | Capture One's install folder (default: read from Windows) |
| `--space-margin-gb N` | free space kept on the target drive (a finite number, 0 or more) |
| `--dry-run` | plan and report, write nothing |
| `--yes` | do not ask for confirmation |

An empty path for `--dest`, `--verify`, `--restore-from`, `--to` or `--install-folder` (an unset variable in a `.cmd`) is refused with exit 2 *(v0.13.1; `--to ""` used to restore into the real places)*.

## What a restore brings back

A restore brings back the profiles, styles, defaults, presets, recipes, shortcuts and workspaces, plus any missing file of `Installed` and `WindowsColor`. The batch queue, `ImageCore`, plugins, `Preferences` and your own folders stay in the backup unless you name them with `--also`. It refuses to run while Capture One is open, and reports what needs an administrator instead of failing half-way. As everywhere in this tool, nothing in the backup is deleted: the old copy of a file that really changed goes to the versions folder, and only once the new copy is on the disk — a copy that fails leaves the old one under its name *(v0.12.1)*. A file whose catalog row has no hash is restored after a size check only, as `RESTORED_UNVERIFIED` (counted apart in the result); a copy of another size is not restored (`CORRUPT_IN_BACKUP`, exit 1). A damaged copy of the same size is therefore restored as it is: what finds it is a `--verify` before the restore (`DEST_DIFFERS`, when the file on this computer still has the size and date of the row).

## Force restore and undo *(v0.7.0)*

A normal restore only adds what is missing. `--force` also replaces what differs, after keeping the current file in `%LOCALAPPDATA%\CaptureOne_old\old_<date>_<time>_<ms>`; only files that really differ go there, and they are never removed for you. `--revert-last` puts the newest `old_` folder back and takes out what the force added (a file is journaled as added only after the force's copy of it is there: a file another program put there meanwhile is never taken out, *v0.12.1*). It keeps what it replaces in a `revert_old_…` folder, which is never taken for the last one. Since v0.13.1: a read-only file is replaced or taken out once it is safe in the snapshot (before, the undo failed on it); each file is undone where the force really put it (the journal records the full path, so an `--install-folder` given only to the force is not needed again); and a target that appears during the undo is left as it was (`CONFLICT`, exit 1).

**What `--revert-last` trusts** *(v0.13.2)*. The `old_…` folders live in `%LOCALAPPDATA%`, where any program of your user can write, and an undo of `Installed` runs as administrator. So a journal line is used only where a `--force` of this computer writes: `UserData`, `Preferences` and `WindowsColor` in this computer's own folder of that part; `Installed` only inside a `Color Profiles` or `Film Curves` folder of a Capture One install folder (this run's, or another one that holds `CaptureOne.exe` or both of those folders — a force made with `--install-folder` is still undone there). A target anywhere else, or one reached through a link or a junction below those folders, stops the whole undo before anything changes (exit 2). The targets outside this run's Capture One folders are listed before the question and in `--dry-run`.

`--revert-last` always judges the newest `old_…` folder. If it has no journal, or an empty one (a `--force` that could not change any file), it says `Nothing to undo` and exits 2 instead of undoing the force before it *(v0.13.2)*; a force that changed nothing says `Nothing was replaced or added`.

- To make `--revert-last` skip a folder, rename it so it ends with `_not` (also the way to reach the force before an empty one).
- To redo a force you undid, rename its `revert_old_…` folder to `old_…` (remove the `revert_`) and run `--revert-last` again.
- `--force` leaves factory profiles alone: on a newer Capture One, an older copy would change the colour of every photo from that camera. `--force-installed` includes them.
- A monitor calibration (a display profile with a video-card table) is never replaced, even with `--force-installed`.
- Both refuse to run unless Windows confirms Capture One is closed.

## Settings

The settings are at the top of `captureone_backup.py`; `COPY_CAPTUREONE_SETTINGS` at the top of `photo_backup.py` turns the step off. *(Listed since v0.13.2.)* Folder numbers change with Capture One versions (`Styles50`, `Recipes165`), so the patterns never name one exactly:

| Constant | Default | Meaning |
|---|---|---|
| `BACKUP_SUBFOLDER` | `_captureone` | under `--dest`: this script's own backup, with its own catalog |
| `MAP_FILE` | `WHERE_EACH_FOLDER_GOES.txt` | the readable map of the original places, in the backup |
| `RESTORE_SCRIPT` | `RESTORE_CAPTUREONE.cmd` | the double-click restore for a new computer (no Python), in the backup |
| `INSTALLER_FOLDER` | `_Instalador` | the folder of `UserData` where you keep the installer and the serial |
| `OLD_PARENT_FOLDER` | `CaptureOne_old` | under `%LOCALAPPDATA%`: what `--force` and `--revert-last` replaced |
| `OLD_PREFIX` | `old_` | + date and time: a `--force` snapshot, the one `--revert-last` uses |
| `REVERT_PREFIX` | `revert_old_` | + date and time: what a `--revert-last` replaced (never taken for the last one) |
| `DISABLED_SUFFIX` | `_not` | an `old_` folder whose name ends with it is skipped by `--revert-last` |
| `JOURNAL_FILE` | `journal.jsonl` | in each snapshot: one line per file replaced or added |
| `ELEVATED_ARGUMENT` | `--elevated` | what `RESTORE_SCRIPT` gives the copy of itself it starts as administrator (so it never asks again) |
| `SUMMARY_LINK_LINES` | `10` | links/junctions not followed named on the screen (the log has all) |
| `USER_FOLDER_NAME` | `CaptureOne` | the `UserData` folder, under `%LOCALAPPDATA%` |
| `PREFERENCES_FOLDER_NAME` | `Capture_One` | the `Preferences` folder, under `%LOCALAPPDATA%` |
| `REGISTRY_DISPLAY_NAME` | `Capture One` | the program's name in Windows' list of installed programs (to find the install folder) |
| `DEFAULT_INSTALL_FOLDER` | `C:\Program Files\Capture One\Capture One` | the install folder when Windows does not say |
| `INSTALL_DATA_FOLDERS` | `Color Profiles;Film Curves` | the folders of the install folder that are kept (`Installed`) |
| `WINDOWS_COLOR_FOLDER` | `System32\spool\drivers\color` | under `%SystemRoot%`: the Windows colour folder |
| `WINDOWS_COLOR_EXTENSIONS` | `.icc`, `.icm` | the files kept from it |
| `PROCESS_NAMES` | `CaptureOne.exe` | `;`-separated: a restore, `--force` and `--revert-last` refuse to run while one is open |
| `EXCLUDE_TOP_FOLDERS` | `Logs;Stability;MSALCache*;*WebView*;*UserData*` | folders at the top of a Capture One folder left out: login data, cookies, logs |
| `EXCLUDE_ANY_FOLDER` | `EBWebView` | a browser profile, left out at any depth |
| `EXCLUDE_FILES` | `license*.enc;*.pbtmp` | files left out: the licence, and a temp copy of this tool |
| `RESTORE_USER_FOLDERS` | `Color Profiles;Styles*;StyleBrushes;Defaults;Presets*;Recipes*;...` | the `UserData` folders a restore brings back without `--also` |
