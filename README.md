# photo-backup — back up the photos you actually kept

A selective backup for photographers' archives. It backs up **the photos you exported** — the exports themselves, plus the RAW, camera JPEG, edited TIFF and sidecars they came from, plus your Capture One and NX Studio edits — and leaves the outtakes behind. Read-only on the source, never deletes on the destination, and verifies the backup on any later day with SHA-256. Tested on a 9 TB archive of Capture One, Lightroom and NX Studio sessions from 1991 to 2026.

---

## Contents

- [Why a selective backup?](#why-a-selective-backup)
- [Features](#features)
- [Requirements & Installation](#requirements--installation)
- [Scripts](#scripts)
- [Quick start — interactive menu](#quick-start--interactive-menu) · [Unattended runs](#unattended-runs-task-scheduler)
- [Quick start — command line](#quick-start--command-line)
- [Safety guarantees](#safety-guarantees)
- [How a photo is selected](#how-a-photo-is-selected)
- [Export formats](#export-formats)
- [Originals that were not exported](#originals-that-were-not-exported---unselected)
- [Folders without an export](#folders-without-an-export)
- [Capture One and NX Studio](#capture-one-and-nx-studio)
- [Re-runs, changed files and the versions folder](#re-runs-changed-files-and-the-versions-folder)
- [Moved and renamed folders](#moved-and-renamed-folders)
- [Verifying a backup](#verifying-a-backup) · [Rolling verify](#rolling-verify)
- [Which disk is this? (backup ID)](#which-disk-is-this-backup-id)
- [Restoring](#restoring)
- [Every option](#every-option) · [Settings](#settings) · [Logs](#logs) · [Exit codes](#exit-codes)
- [Current version](#current-version) · [Changelog](#changelog)
- [Run it after jxl-photo](#run-it-after-jxl-photo) · [Related project: jxl-photo](#related-project-jxl-photo)
- [Disclaimer](#disclaimer) · [License](#license) · [Acknowledgments](#acknowledgments)

---

## Why a selective backup?

A photo archive is mostly photos nobody will ever look at again. In the archive this was built for, the year folders hold ~9.3 TB, but the photos that were actually edited and exported — with their originals and edits — fit in **~1.2 TB**. A mirror tool (FreeFileSync, robocopy) copies all 9 TB or nothing; it cannot tell a kept photo from an outtake.

photo-backup reads the decision you already made: **if a photo has an export, it matters.** For every folder that holds an export folder (`_EXPORT`, `Lightroom_Export`, `C1_Export_WEB`, ... — any name containing "export"), it copies:

- the files inside the export folders (default: the best available format per photo, `jxl>tif>jpg`),
- the originals beside the export folder that produced those exports (RAW, camera JPEG, NX Studio TIFF, `.xmp`/`.dop`/`.pp3` sidecars),
- the Capture One folders (`Settings*`; `Cache` is skipped by default),
- the NX Studio `NKSC_PARAM` folder.

The folder tree is mirrored on the destination, so `--source G:\` and `--source G:\2025` produce the same layout and later runs add to the previous backup. Everything it did **not** copy is listed (folders without an export, outtakes, filtered formats), so nothing is left out without you knowing.

---

## Features

### 1. **Selection by export, not by folder**
The key of a photo is the real file name beside the export, matched by prefix: `DSC_9360_ProPhoto-g22_v1.jxl` → `DSC_9360.NEF` + `.JPG` + `.xmp`; `_DSC6491_NXstudio_sRGB_v1.jpg` → `_DSC6491.NEF` and its NX Studio sidecar. Nikon's `DSC_` and `_DSC`, Canon's `IMG_`, film scans and phone names all work — it never cuts a name at the first underscore. [Details](#how-a-photo-is-selected).

### 2. **Pick the export formats per run**
`jxl>tif>jpg` copies the best format each photo has (a JXL master if there is one, else the TIFF, else the JPEG); `jxl+jpg` copies both; `all` copies everything. Skip derivative folders with `--exclude-export-subfolders JXL_mobile;WEB`.

### 3. **Your edits come along**
Capture One `Settings*` folders and NX Studio `NKSC_PARAM` are copied (the regenerable Capture One `Cache` is skipped unless `--c1-cache`). Edits keep changing `.xmp`, `.cos`, `.nksc` files; changed ones are updated on every run, with the previous version kept in a `versions\` folder.

### 4. **Never loses anything**
The source is opened read-only. Nothing on the destination is ever deleted or overwritten in place — a replaced file is moved to `versions\` first. Every copy goes to a temp file, is verified, then atomically renamed.

### 5. **Checks the space before the first byte**
The summary shows what will be copied and what will not, the space needed and the space free; a run that does not fit aborts before copying anything. `--dry-run` shows all of it without writing.

### 6. **Verify any day later**
A SQLite catalog on the destination records the SHA-256 of every file at copy time. `--verify quick` checks sizes and dates in seconds; `--verify full` re-reads both sides and reports a corrupted backup copy, an original that changed without changing its date (bit rot), or an original that is gone — checking the backup copy first, because then it is the only one left.

### 7. **Re-runs are incremental**
Unchanged files are skipped by size and date; files copied by another tool (FreeFileSync, Explorer) are adopted into the catalog and hashed by the next `--verify full`.

### 8. **An interactive menu** *(v0.3.0)*
`python photo_backup.py`: answer a few questions (Enter keeps your last choice), read the dry-run, confirm the copy. Save presets and run them from the Task Scheduler. [Details](#quick-start--interactive-menu).

### 9. **Moved and renamed folders are moved, not copied again** *(v0.4.0)*
Renaming a session (`250129_Flores` → `250129_Flores [FINAL]`) used to copy everything again and leave the old copy behind forever. Now the backup copy is **moved** to the new path after the content is confirmed by SHA-256; anything in the backup whose source is gone is listed as an **orphan**, and `--move-orphans` parks it under `_photo_backup\orphans\`. [Details](#moved-and-renamed-folders).

### 10. **A backup identifies itself** *(v0.4.0)*
The first copy writes `_photo_backup\volume.json` with a random backup ID and the volume label. `--expect-backup-id` refuses the wrong disk, and a preset finds its backup even when the drive letter changed. [Details](#which-disk-is-this-backup-id).

### 11. **Verify a big backup in slices** *(v0.4.0)*
`--verify full --max-gb N` hashes at most N GB per run, oldest verification first, so a huge backup is checked over several nights. [Details](#rolling-verify).

### 12. **Restore** *(v0.4.0)*
`restore_backup.py` copies files back from a backup — under a new folder or to the original places — never overwriting anything and never writing to the backup. [Details](#restoring).

### 13. **Skips what jxl-photo can remake** *(v0.5.0)*
An sRGB or resized JXL that [jxl-photo](https://github.com/rsilvabr/jxl-photo) made from a master is left out when that master is backed up — the copy says so in its own XMP, and anything uncertain is copied. `--derivatives` keeps them. [Details](#jxl-photo-derivatives---derivatives-v050).

### 14. **Keeps what Capture One needs to show your edits** *(v0.6.0)*
A `.cos` only names its ICC profile, curve and style. The backup also keeps those files, read from where Capture One really has them, plus a double-click restore for a new computer that needs no Python. [Details](#capture-ones-own-files-captureone_backuppy-v060).

---

## Scripts

| Script | What it does |
|---|---|
| `photo_backup.py` | **Start here.** Interactive menu: back up (dry-run first, then copy), verify, presets, unattended runs, restore. Runs the scripts below for you. |
| `selective_backup.py` | The selective backup itself, from the command line: plan, copy, catalog, verify. Everything the menu does goes through it. |
| `restore_backup.py` | Restores files from a backup: under a new folder (`--to`) or to the original places (`--to-original`). It imports the catalog and the copier from `selective_backup.py`, so the catalog format exists in one place only. |
| `captureone_backup.py` | Backs up, verifies and restores Capture One's own profiles, styles and settings. The menu runs it with every backup. |

The menu never copies anything itself — it runs `selective_backup.py` (or `restore_backup.py`) with the options you confirmed, so every [safety guarantee](#safety-guarantees) holds either way.

---

## Requirements & Installation

- **Python 3.12 or newer.** Nothing else: the standard library only — no packages, no external tools.
- **Windows.** All file I/O uses extended-length `\\?\` paths, so paths longer than 260 characters (Japanese folder names, deep sessions) work.

```console
git clone https://github.com/rsilvabr/photo-backup.git
cd photo-backup
python photo_backup.py
```

Optional: [rich](https://github.com/Textualize/rich) (`python -m pip install rich`) gives the menu colours. To run the tests: `python -m pip install pytest`, then `python -m pytest tests`.

---

## Quick start — interactive menu

```console
python photo_backup.py
```

```
== Menu ==
  [1] Back up (dry-run first, then copy)
  [2] Verify a backup
  [3] Presets
  [4] Settings
  [5] Restore from a backup
  [6] Capture One only (back up, restore, force, undo)
  [0] Exit
```

- **[1] Back up** asks for the folders to back up, the destination (it lists the connected drives with their free space), the export formats, the subfolders to skip and the other options — then runs a **dry-run** with those choices. You read what would be copied, what would not, and whether it fits; only then it asks **"copy now?"**. If some folders could not be read, it says so and asks again before copying the rest.
- Your choices are remembered: the next time, it shows them and asks **"Use these settings?"** — Enter, and the dry-run starts.
- **[2] Verify** runs `--verify quick` or `--verify full` on a backup. With `full` it also asks **"Max GB to verify in this run (0 = everything)"** — see [Rolling verify](#rolling-verify).
- **[3] Presets**: after a backup the menu offers to save its settings (and the backup's ID) under a name. A preset runs from the menu (dry-run first) or unattended; if the drive letter changed, the menu finds the backup by its ID.
- **[4] Settings** shows where the remembered choices live (`~/.photo_backup_config.json`, or `$PHOTOBACKUP_CONFIG`) and the defaults of `selective_backup.py`. The menu reads those defaults from `selective_backup.py` itself: edit the top of that file and the menu follows.
- **[5] Restore** lists the backup's top folders, asks what to restore and where to (the original places or another folder), runs a dry-run and then restores after a yes.
- **[6] Capture One only** backs up just Capture One to a folder you choose, restores from one, force-restores and undoes the last force restore; see [Capture One's own files](#capture-ones-own-files-captureone_backuppy-v060).

> **Tip:** The value shown in `[brackets]` is the default — just press Enter to accept it.

### Unattended runs (Task Scheduler)

```console
python photo_backup.py --list-presets
python photo_backup.py --run-preset "NIGHTLY"
```

`--run-preset` runs a saved preset without questions and without the dry-run step; `selective_backup.py` still refuses to start when the destination drive is missing or too full (exit 2). For the Task Scheduler, run it through `cmd /k` so the window stays open with the result:

```
Program:   cmd
Arguments: /k python photo_backup.py --run-preset "NIGHTLY"
Start in:  C:\path\to\photo-backup
```

The exit code is the backend's (see [Exit codes](#exit-codes)).

---

## Quick start — command line

```console
:: 1. See what a backup would do (writes only logs, no destination)
python selective_backup.py --source "G:\2025" --dest "H:\BACKUP" --dry-run

:: 2. Back up for real (shows the summary and asks for confirmation)
python selective_backup.py --source "G:\2025" --dest "H:\BACKUP"

:: 3. Verify the backup another day
python selective_backup.py --source "G:\2025" --dest "H:\BACKUP" --verify full
```

> **Tip:** always start with `--dry-run`. The summary lists what would be copied, what would not (and why), and whether it fits. The full lists are in the CSVs next to the log. Every option is in [Every option](#every-option).

---

## Safety guarantees

- **The source is READ-ONLY.** No code path opens a source file for writing,
  renames, deletes, touches or changes attributes on anything under a
  `--source`.
- **Nothing on the destination is ever deleted.** A file whose source changed
  is reported; with `--update-changed` the old copy is MOVED to the versions
  folder first, never overwritten in place. The only file the tool removes is
  its own temp file (`*` + `.pbtmp`) after a failed copy or a crash.
- **Fail closed.** Not enough space, a destination drive that is not
  connected, an unreadable catalog, a closed stdin without `--yes`, a
  destination inside a source (or the reverse): the tool aborts BEFORE the
  first byte is copied, exit code 2.
- **Nothing is skipped silently.** A folder or file that cannot be read is
  logged, counted on the console (`Errors: N ... NOT backed up`) and makes the
  run exit 1 — a dry-run too.
- A copy is written to a temp file next to its final name, verified, then
  `os.replace`d — a crash never leaves a half file under the final name.

## How a photo is selected

For a folder that holds an export folder (a "session"), the tool collects the
photo keys of the files directly beside the export folder whose extension can
be a key (`ANCHOR_EXTENSIONS`: RAW, JPEG, TIFF, PNG, HEIC, PSD). The key is
the extension-less stem of such a file.

Each export file is matched to the shortest prefix of its own stem that ends
right before a separator (`_`, `-`, `.`, space, `(`) and equals an anchor:

- `DSC_9360_ProPhoto-g22_v1.jxl` with `DSC_9360.NEF` beside it → key
  `DSC_9360` (`DSC` is not an anchor; matching is case-insensitive).
- `_DSC6491_NXstudio_ProPhoto-g22_v1.jxl` with `_DSC6491.NEF` → key
  `_DSC6491`, so `_DSC6491.NEF`, `_DSC6491.JPG`, `_DSC6491.xmp` and
  `_DSC6491_NXstudio.xmp` are all copied.
- `DSC0001_Nxstudio_v1_sRGB.jpg` with `DSC0001.NEF` and
  `DSC0001_Nxstudio_v1.tiff` → key `DSC0001` (the shortest match wins), so
  both originals are copied.
- `DSC_9360x.jpg` with `DSC_936` beside it does NOT match: the character
  after `DSC_936` is `0`, not a separator.
- Windows' duplicate counter is part of the name *(v0.5.0)*:
  `DSC_0001 (2)_sRGB.jpg` with both `DSC_0001.NEF` and `DSC_0001 (2).NEF`
  beside it → key `DSC_0001 (2)`, not `DSC_0001`, so the export formats are
  chosen for that photo alone. (With only `DSC_0001.NEF` there, the key stays
  `DSC_0001`.) The originals of `DSC_0001 (2)` still bring `DSC_0001` along:
  a film rescan `0005 (2).tif` is often the same frame as `0005.tif`, and a
  backup copies one original too many rather than one too few.

The keys of an export file that has no matching original are recorded as
`?<stem>` and listed in `_unmatched.csv`; such files are still copied (film
scans renamed on export end up there).

A direct file of the session is copied as category `source` when any prefix
of its stem matches a selected key. This also covers `DSC_9360.NEF.xmp`
(stem `DSC_9360.NEF`) and `_DSC6491_NXstudio.xmp`. Direct files whose photo
was not selected follow `--unselected` (below).

## Export formats

`--export-formats SPEC` (default `jxl>tif>jpg`):

| SPEC | Meaning |
|---|---|
| `all` | copy every collected file, including non-image files |
| `a>b>c` | fallback per photo: for each key, copy the files of the first listed group that has any file for that key; the other groups are not copied |
| `a+b` | multiple: copy the files whose extension is in any listed group |
| `a` | a single group is the same as `a+b` |

Example with the files `16B_JXL\..jxl`, `JXL_mobile\..jxl`, `TIFF16\..tif`,
`sRGB\..jpg`, `WEB\..jpg`, `CHECK\..jpg` for one photo: `jxl>tif>jpg` copies
the 2 `.jxl`; `jxl+jpg` copies the 2 `.jxl` and the 3 `.jpg`; `all` copies
all 6. Mixing `>` and `+`, an unknown group name or an empty spec is a usage
error (exit 2). Files dropped by the filter (or by
`--exclude-export-subfolders`) are counted in the summary and the log.

Format groups (`FORMAT_GROUPS` in the script):

| Group | Extensions |
|---|---|
| `jxl` | `.jxl` |
| `tif` | `.tif`, `.tiff` |
| `jpg` | `.jpg`, `.jpeg` |
| `png` | `.png` |
| `heic` | `.heic`, `.heif`, `.hif` |
| `psd` | `.psd`, `.psb` |
| `dng` | `.dng` |

## jxl-photo derivatives (`--derivatives`) *(v0.5.0)*

[jxl-photo](https://github.com/rsilvabr/jxl-photo) makes copies of a JXL
master — an sRGB JXL, a resized or sharpened JXL — and writes
`jxlphoto-derived:<recipe>` into each copy's XMP; the master carries
`jxlphoto-src:` instead. Such a copy can be made again from the master, so by
default it is **not** backed up when the master of the same photo is copied in
the same run (the same reasoning as the Capture One `Cache`).

The rule, per photo, never on a guess:

1. the JXL's own XMP says `jxlphoto-derived:` (and not `jxlphoto-src:`), and
2. a JXL of the same photo (same key) that says `jxlphoto-src:` is selected
   in this run.

Anything else is copied: a JXL without a container or without XMP, a
compressed (`brob`) XMP box, a lossless JPEG → JXL archive (it carries no
token), a derivative whose master is excluded or missing, a file that cannot
be read. Only photos with two or more selected `.jxl` are read, and only the
boxes before the image data (a few KB per file). The summary counts what was
left out: `jxl-photo derivatives (their master is copied ...)`.

`--derivatives` (or `COPY_JXLPHOTO_DERIVATIVES = True`) copies them anyway.

## Originals that were not exported (`--unselected`)

- `skip` (default): not copied, counted in the summary.
- `raw`: copy RAW files, plus sidecars (`.xmp`, `.dop`, `.pp3`) whose stem
  prefix matches an unselected RAW of that folder.
- `all`: copy every direct file of the session.

The originals are copied only when at least one export file of that photo was
actually selected for the backup (after format filtering and
`--exclude-export-subfolders`).

## Folders without an export

A folder with no export subfolder is listed in `_unexported.csv` (path, number
of direct files, bytes, Capture One bytes without cache, NKSC_PARAM bytes).
With `--unexported copy` the direct files, Capture One and NKSC_PARAM folders
are also copied: a parent folder above a session counts as a folder without an
export (it has no export of its own).

Folders inside a session that are not export, Capture One or NKSC_PARAM
(`_OUTTAKES`, `OLD_TIFFs`, `tiff_zip`, ...) are never copied; their files are
counted as "inside sessions".

A folder that holds an export folder but **no original beside it** is not a
session for its subfolders: `G:\2017` holding `171230_Lightroom_Export`, or a
drive root holding `04_ Exported_OLD`. Its export files are copied (and listed
as exports without a matching original), but its subfolders keep their own
classification — a trip folder below it is still a folder without an export,
listed and copied with `--unexported copy`.

## Capture One and NX Studio

- Every folder named `CaptureOne` (configurable) is copied in full, except
  its direct subfolders named `Cache` (configurable) — those are regenerable.
  Use `--c1-cache` to copy them too.
- Every folder named `NKSC_PARAM` is copied in full.
- Both are copied without filtering by photo (they are small), for sessions
  and, with `--unexported copy`, for folders without an export.

## Capture One's own files (`captureone_backup.py`) *(v0.6.0)*

The `.cos` files beside your photos hold the edits, but they only **name** the ICC profile, film curve and style they use. On a computer without those files, Capture One shows the edit differently. Every backup from the menu (and every preset) therefore also keeps, in `<destination>\_captureone`:

| Folder in the backup | Where it comes from | What it holds |
|---|---|---|
| `UserData` | `%LOCALAPPDATA%\CaptureOne` | your profiles, styles (with the profiles they carry), defaults, presets, recipes, shortcuts, workspaces, plugins, any folder you add |
| `Preferences` | `%LOCALAPPDATA%\Capture_One` | `user.config` of each version |
| `Installed` | `Color Profiles` and `Film Curves` of the install folder | the profiles the `.cos` files name, in case a later version drops one |
| `WindowsColor` | the Windows colour folder | its `.icc`/`.icm` files (export and monitor profiles) |

Left out on purpose: login data and cookies (`MSALCache*`, the WebView folders), the licence file, `Logs`, `Stability`, and the program itself. `WHERE_EACH_FOLDER_GOES.txt` in that folder says where each part came from.

Keep the installer and the serial in a folder inside `%LOCALAPPDATA%\CaptureOne` (`_Instalador` by default): they are copied with the rest.

**On a new computer:**

1. Install Capture One from `_captureone\UserData\_Instalador`.
2. **Before opening it**, double-click `_captureone\RESTORE_CAPTUREONE.cmd`. It needs no Python, asks Windows for administrator rights and only adds missing files. (Opening Capture One first creates its default recipes, and a file that already exists is never replaced.)
3. Open Capture One and activate it.

With Python, the same restore checks every file against the catalog:

```console
python captureone_backup.py --dest H:\BACKUP [--dry-run] [--yes]
python captureone_backup.py --verify H:\BACKUP
python captureone_backup.py --restore-from H:\BACKUP [--dry-run] [--yes]
python captureone_backup.py --restore-from H:\BACKUP --also _Instalador --also Preferences --to D:\Out
python captureone_backup.py --restore-from H:\BACKUP --force [--force-installed]
python captureone_backup.py --revert-last
```

`PATH` can be any folder, for a backup of Capture One alone: the files go to `PATH\_captureone`, and giving that folder itself works too.

| Option | Meaning |
|---|---|
| `--dest PATH` | back up to `PATH\_captureone` |
| `--verify PATH` | re-read and hash the backup under `PATH` |
| `--restore-from PATH` | restore from the backup under `PATH` |
| `--also NAME` | restore this part too: `Preferences`, or a folder of `UserData` (`_Instalador`, `Batch*`); repeatable |
| `--to PATH` | restore under `PATH` instead of the original places |
| `--force` | with `--restore-from`: also replace files that differ, in your own folders; the current ones are kept first |
| `--force-installed` | with `--force`: also the install folder's profiles and the Windows colour folder's, as administrator. Never a monitor calibration |
| `--revert-last` | undo the last `--force` |
| `--install-folder PATH` | Capture One's install folder (default: read from Windows) |
| `--space-margin-gb N` | free space kept on the target drive |
| `--dry-run` | plan and report, write nothing |
| `--yes` | do not ask for confirmation |

A restore brings back the profiles, styles, defaults, presets, recipes, shortcuts and workspaces, plus any missing file of `Installed` and `WindowsColor`. The batch queue, `ImageCore`, plugins, `Preferences` and your own folders stay in the backup unless you name them with `--also`. It refuses to run while Capture One is open, and reports what needs an administrator instead of failing half-way. As everywhere in this tool, nothing in the backup is deleted: the old copy of a file that really changed goes to the versions folder.

**Force restore and undo** *(v0.7.0)*. A normal restore only adds what is missing. `--force` also replaces what differs, after keeping the current file in `%LOCALAPPDATA%\CaptureOne_old\old_<date>_<time>_<ms>`; only files that really differ go there, and they are never removed for you. `--revert-last` puts the newest `old_` folder back and takes out what the force added. It keeps what it replaces in a `revert_old_…` folder, which is never taken for the last one.

- To make `--revert-last` skip a folder, rename it so it ends with `_not`.
- To redo a force you undid, rename its `revert_old_…` folder to `old_…` (remove the `revert_`) and run `--revert-last` again.
- `--force` leaves factory profiles alone: on a newer Capture One, an older copy would change the colour of every photo from that camera. `--force-installed` includes them.
- A monitor calibration (a display profile with a video-card table) is never replaced, even with `--force-installed`.
- Both refuse to run unless Windows confirms Capture One is closed.

The settings are at the top of `captureone_backup.py`; `COPY_CAPTUREONE_SETTINGS` at the top of `photo_backup.py` turns the step off.

## Re-runs, changed files and the versions folder

On every run, each planned file is compared against the destination:

- missing → `NEW`, copied.
- same size and mtime (within `MTIME_TOLERANCE_S`) → `UP_TO_DATE`. If the
  catalog has no row for it (the file was copied by another tool), a row with
  `sha256 = NULL` is added; `--verify full` fills the hash later. A row whose
  size/mtime no longer match is refreshed the same way.
- otherwise → `CHANGED`: reported and NOT copied, unless `--update-changed`
  is given. With `--update-changed`, the old copy is moved to
  `<dest>\_photo_backup\versions\<YYMMDD_HHMMSS>\<rel>` and the new file is
  copied. Nothing in the destination is ever overwritten in place.
- **Edit settings and sidecars are updated automatically.** Every edit in
  NX Studio or Capture One rewrites the `.xmp`, `.cos`, `.comask`, `.nksc`
  (`AUTO_UPDATE_EXTENSIONS`). A changed file with one of those extensions is
  treated as if `--update-changed` were given: the old copy goes to the
  versions folder, the new one is copied. RAWs, JPEGs, TIFFs and exports
  still need `--update-changed`. `--no-auto-update` (or `AUTO_UPDATE = False`)
  turns this off.

The catalog `<dest>\_photo_backup\catalog.sqlite` is created on the first real
copy and remembers the size, mtime and sha256 of every copied file — the size
and mtime the source had at the moment it was copied (a file saved by your
editor while the backup runs is recorded as copied, not as planned).

## Moved and renamed folders

When you rename a session (`250129_Flores` → `250129_Flores [FINAL]`), every
file appears at a new relative path and looks `NEW`. Instead of copying
everything again and leaving the old copy behind, the tool:

1. looks for a catalogued backup file with the same file name and size whose
   **source is gone** (`possible moves: N files` in the summary — a dry-run
   writes nothing);
2. before moving anything, hashes the new source file and the old backup copy
   and compares them with the SHA-256 recorded at copy time: only a full match
   moves (`MOVE old\path -> new\path`);
3. moves it into the new place, updates the catalog and removes the folders
   that the move left empty — up to (not including) the destination root.
   This is the **only** case where the tool removes a folder on the
   destination, and it never removes a file; anything that does not match is
   copied as new (`MOVE REJECTED ...: content differs`).

`--no-detect-moves` turns the whole detection off (everything is copied
again), and `DETECT_MOVES = False` does the same by default.

A backup file whose source no longer exists is an **orphan** (an original you
deleted after it was exported). Every run writes `<log>_orphans.csv` with the
reason for each:

- `source_missing` — the original is gone;
- `not_selected` — the original still exists, but the current selection does
  not include it any more (for example you changed `--export-formats`).

Orphans are only reported by default. `--move-orphans` (real copies only)
moves the `source_missing` ones to
`<dest>\_photo_backup\orphans\<YYMMDD_HHMMSS>\<rel>`, removes their catalog
rows and cleans the folders the move left empty; `not_selected` files are
never touched. Since the old copy is only moved inside the backup, the space
does not shrink until you delete the orphans folder yourself — that is
outside this tool's scope.

## Verifying a backup

`--verify quick` checks that each catalogued file still exists with the
recorded size and mtime; `--verify full` additionally hashes the destination
(and the source when needed) and detects content corruption. Verify never
copies, never asks for confirmation and never creates the destination
(destination or catalog missing → exit 2). Use the same selection flags you
backed up with.

Statuses: `OK`, `HASH_FILLED` (an adopted file's hash was recorded),
`NOT_BACKED_UP`, `DEST_MISSING`, `DEST_SIZE`, `SOURCE_MISSING`,
`SOURCE_CHANGED`, `DEST_CORRUPT`, `DEST_DIFFERS`, `SOURCE_CORRUPT_SUSPECT`
(the source content changed without changing size or mtime — the most
important to report). The results go to the console and to `_verify.csv`.
Exit 0 only if every file is `OK` or `HASH_FILLED`.

With `--verify full`, the backup copy is hashed **before** the source is
looked at: when the original was deleted or changed, the copy may be the only
one left. A corrupted copy is `DEST_CORRUPT` even if the original is gone; an
intact one is `SOURCE_MISSING` / `SOURCE_CHANGED` with the detail "backup
copy hash verified".

Note: the `hash` mode of `--verify-copy` re-reads the temp file right after
writing, which the OS cache usually serves from memory. It catches write
errors; the real check of the media is a `--verify full` on another day.

## Rolling verify

A `--verify full` over a multi-terabyte backup takes hours. `--max-gb N`
limits a run to at most N GB, hashing first the files that were verified
longest ago (files never verified, then the oldest `verified_at`; ties go to
the oldest `copied_at`). The run prints:

```
Rolling verify: checked 250 files / 100.0 GB, deferred 900 files / 350.0 GB (oldest verification first; the next run continues)
```

The deferred files are not checked and do not affect the exit code; the next
run picks them up. A corrupted backup copy is still reported immediately. A
file whose source is gone but whose copy is intact gets its `verified_at`
advanced too, so the wheel keeps turning. `--max-gb` only applies to
`--verify full`; with `quick` it is ignored (the log says so). The default is
`VERIFY_MAX_GB` (0 = verify everything).

## Which disk is this? (backup ID)

The first real copy writes `_photo_backup\volume.json`:

```json
{
  "backup_id": "9f2c...",
  "created": "2026-10-07T22:10:05",
  "label": "PHOTOS",
  "path": "BACKUP"
}
```

The summary's `Destination:` line then ends with `backup ID 9f2c0031` (the
first 8 characters), so a glance tells you whether the disk in front of you
is the right one. A destination with no catalog yet says `NEW BACKUP`; a
backup made before v0.4.0 (catalog but no `volume.json`) says
`backup ID (none yet; created on the next copy)`.

`--expect-backup-id ID` makes it strict: the run (copy, dry-run or verify)
aborts with exit 2 before planning anything unless the destination holds
exactly that ID. The menu saves the ID with every preset, and a preset run
looks for the same relative path on every connected drive: when found, it
says `Backup found on D:\BACKUP (the preset says H:\BACKUP)` and runs there;
when not, it says the backup is not on any connected drive and exits 2.

## Restoring

`restore_backup.py` copies files **out of** a backup. The backup is
read-only; a target file that already exists is never overwritten.

```
restore_backup.py --backup PATH (--to PATH | --to-original) [options]
```

| Option | Description |
|---|---|
| `--backup PATH` | backup root (the folder that holds `_photo_backup`); required |
| `--to PATH` | restore each file under `PATH\<rel>` |
| `--to-original` | restore each file to the `source_path` recorded in the catalog |
| `--only REL` | restore only this folder or file (repeatable, case-insensitive); default: everything |
| `--expect-backup-id ID` | abort unless the backup holds this backup ID |
| `--space-margin-gb N` | free space kept on each target drive, in GB (default `5.0`) |
| `--dry-run` | plan and report, restore nothing |
| `--yes` | do not ask for confirmation |
| `--version` | print the version and exit |
| `--help` | print the usage and exit |

Exactly one of `--to` and `--to-original` is required. Outcomes per file, in
the console, in the log and in `<log>_restore.csv`:

- `RESTORED` — copied from the backup and verified against the catalog
  SHA-256 (`RESTORED_UNVERIFIED` when the catalog had no hash yet);
- `ALREADY_THERE` — the target exists with the same content (size and hash);
- `CONFLICT` — the target exists with different content; it is left alone and
  the run exits 1;
- `CORRUPT_IN_BACKUP` — the backup copy does not match the catalog hash;
  nothing is written to the target and the run exits 1;
- `MISSING_IN_BACKUP`, `REFUSED`, `ERROR` — the backup file is gone, the
  `source_path` would land inside the backup (`--to-original`), or another
  error; all exit 1.

Exit codes: 0 (everything restored or already there), 1 (any of the above),
2 (aborted before restoring: no catalog, wrong backup ID, `--to` inside the
backup or the backup inside `--to`, not enough space, no confirmation),
130 (Ctrl+C).

## Run it after jxl-photo

The smooth nightly order is: jxl-photo converts the new TIFF exports into JXL
masters, then photo-backup runs and picks up those masters the same night
(`jxl>tif>jpg` finds the JXL first). One `.cmd` chains both:

```bat
@echo off
cd /d C:\path\to\jxl-photo
py jxl_photo.py --run-preset "SYNC PHOTOS"
cd /d C:\path\to\photo-backup
py photo_backup.py --run-preset "NIGHTLY"
```

In the Task Scheduler, use `cmd /k` with that `.cmd` so the window stays open
with the result. Nothing here edits your existing scheduled tasks.

## Every option

```text
selective_backup.py --source PATH [--source PATH ...] --dest PATH [options]
```

| Option | Description |
|---|---|
| `--source PATH` | folder to back up; repeatable, required |
| `--dest PATH` | destination root; required |
| `--base PATH` | base for the relative layout; default: the drive root of each source, so `G:\` and `G:\2025` give the same layout |
| `--export-formats SPEC` | `all`, `a>b>c` fallback or `a+b` multiple (default `jxl>tif>jpg`) |
| `--exclude-export-subfolders NAMES` | `;`-separated folder names skipped inside export folders (any level), e.g. `JXL_mobile;WEB` |
| `--export-name-contains TEXT` | a folder whose name contains this (any case) is an export folder (default `export`) |
| `--exclude-export-names NAMES` | `;`-separated exact folder names that are NOT export folders |
| `--unselected {skip,raw,all}` | originals of photos that were not exported (default `skip`) |
| `--unexported {skip,copy}` | folders without an export (default `skip`) |
| `--c1-cache` | also copy the Capture One `Cache` folders |
| `--derivatives` | also copy the JXLs jxl-photo derived from a master that is copied ([details](#jxl-photo-derivatives---derivatives-v050)) |
| `--exclude-folders NAMES` | `;`-separated folder names skipped anywhere |
| `--verify-copy {none,size,hash}` | verification of each copy (default `hash`) |
| `--update-changed` | move a changed destination file to the versions folder before copying the new one |
| `--no-auto-update` | do not update changed edit settings/sidecars (`AUTO_UPDATE_EXTENSIONS`) unless `--update-changed` is given |
| `--no-detect-moves` | copy a moved/renamed file again instead of moving its backup copy |
| `--move-orphans` | move backup files whose source is gone into `_photo_backup\orphans\<stamp>` (real copies only) |
| `--expect-backup-id ID` | abort (exit 2) unless the destination holds this backup ID; also applies to `--verify` and `--dry-run` |
| `--space-margin-gb N` | free space kept on the destination, in GB (default `20.0`) |
| `--dry-run` | plan, print the summary and write the logs/CSVs; copy nothing |
| `--verify {quick,full}` | verify an existing backup; no copy |
| `--max-gb N` | with `--verify full`: verify at most N GB, oldest verification first (default `0.0` = everything) |
| `--yes` | do not ask for confirmation |
| `--version` | print the version and exit |
| `--help` | print the usage and exit |

`--verify` cannot be combined with `--dry-run`, `--update-changed` or
`--move-orphans` (exit 2); a negative `--max-gb` is a usage error too.

## Settings

Every user-editable setting lives at the top of `selective_backup.py` and is read
by the code and by the argparse defaults; editing the constant changes the
default everywhere.

| Constant | Default | Meaning |
|---|---|---|
| `EXPORT_NAME_CONTAINS` | `export` | a folder whose name contains this (any case) is an export folder |
| `EXPORT_NAME_EXCLUDE` | (empty) | `;`-separated exact folder names that are NOT export folders |
| `EXPORT_FORMATS` | `jxl>tif>jpg` | default `--export-formats` |
| `EXCLUDE_EXPORT_SUBFOLDERS` | (empty) | `;`-separated folders skipped inside export folders |
| `FORMAT_GROUPS` | see above | group names usable in `--export-formats` |
| `RAW_EXTENSIONS` | `.nef`, `.cr2`, ... | extensions treated as RAW |
| `ANCHOR_EXTENSIONS` | RAW + `.jpg`, `.tif`, ... | files whose stem can be a photo key |
| `SIDECAR_EXTENSIONS` | `.xmp`, `.dop`, `.pp3` | sidecars copied with an unselected RAW |
| `NAME_SEPARATORS` | `_-. (` | a key must be followed by one of these |
| `UNSELECTED` | `skip` | default `--unselected` |
| `CAPTUREONE_FOLDER_NAMES` | `CaptureOne` | `;`-separated Capture One folder names |
| `CAPTUREONE_CACHE_NAMES` | `Cache` | `;`-separated subfolders of a Capture One folder skipped by default |
| `COPY_CAPTUREONE_CACHE` | `False` | default for `--c1-cache` |
| `COPY_JXLPHOTO_DERIVATIVES` | `False` | default for `--derivatives` |
| `DERIVATIVE_TOKEN` / `MASTER_TOKEN` | `jxlphoto-derived:` / `jxlphoto-src:` | the XMP tokens jxl-photo writes into a derivative / a master |
| `XMP_SCAN_LIMIT_MB` | `4` | the boxes read before the image data, at most, per JXL |
| `NXSTUDIO_FOLDER_NAMES` | `NKSC_PARAM` | `;`-separated NX Studio folder names |
| `UNEXPORTED` | `skip` | default `--unexported` |
| `EXCLUDE_FOLDERS` | (empty) | `;`-separated folder names skipped anywhere |
| `SYSTEM_SKIP_FOLDERS` | `$RECYCLE.BIN;System Volume Information` | always skipped |
| `VERIFY_COPY` | `hash` | default `--verify-copy` |
| `SPACE_MARGIN_GB` | `20.0` | free space kept on the destination |
| `MTIME_TOLERANCE_S` | `2.0` | mtime tolerance (FAT/exFAT stores 2 s steps) |
| `COPY_CHUNK_MB` | `8` | read/write block size |
| `TEMP_SUFFIX` | `.pbtmp` | in-progress copy suffix |
| `CATALOG_FOLDER` | `_photo_backup` | catalog and versions folder under the destination |
| `PROGRESS_EVERY_S` | `5.0` | console progress interval |
| `AUTO_UPDATE` | `True` | changed edit settings/sidecars are updated without `--update-changed` (`--no-auto-update` turns it off) |
| `AUTO_UPDATE_EXTENSIONS` | `.xmp`, `.dop`, `.pp3`, `.cos`, `.comask`, `.nksc` | extensions treated that way |
| `DETECT_MOVES` | `True` | detect moved/renamed catalogued files and move their backup copy (`--no-detect-moves`) |
| `ORPHANS_FOLDER` | `orphans` | under `CATALOG_FOLDER`: where `--move-orphans` puts files whose source is gone |
| `BACKUP_ID_FILE` | `volume.json` | under `CATALOG_FOLDER`: the backup identity (F2) |
| `VERIFY_MAX_GB` | `0.0` | default `--max-gb` for `--verify full` (0 = everything) |
| `SUMMARY_FOLDER_LINES` | `25` | lines of the per-folder table in the summary (the CSV has all) |
| `LOG_DIR` | `$PHOTOBACKUP_LOG_DIR` or `Logs\` next to the script | where the logs are written |

`restore_backup.py` has its own setting at the top:

| Constant | Default | Meaning |
|---|---|---|
| `SPACE_MARGIN_GB` | `5.0` | free space kept on each restore target drive (default `--space-margin-gb`) |

## Logs

Every run writes `<LOG_DIR>\<YYMMDD_HHMMSS>_backup` (or `_verify`) plus:

- `.log` — command line, effective settings, summary, every `COPY`,
  `SKIP CHANGED`, `VERSION` and `ERROR`, final summary;
- `_plan.csv` — `action,category,size,mtime,src,rel` for every planned file
  (written on every run, including `--dry-run`);
- `_unexported.csv` — `folder,files,bytes,c1_bytes,nx_bytes` (always written,
  even with `--unexported skip`);
- `_unmatched.csv` — `session,export_file`;
- `_by_folder.csv` — `folder,files,bytes,cumulative_bytes` for what would be
  copied, by top folder (always written; the summary shows at most
  `SUMMARY_FOLDER_LINES` of them);
- `_orphans.csv` — `reason,rel,size,source_path,copied_at` (always written,
  header only when there is no catalog);
- `_verify.csv` — `status,rel,source_path,detail` (verify runs only).

`restore_backup.py` writes its own `<YYMMDD_HHMMSS>_restore.log` and
`<YYMMDD_HHMMSS>_restore.csv` (`status,rel,target,detail`).

CSVs are UTF-8 with BOM (`utf-8-sig`), which opens correctly in Excel with
Japanese file names.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | everything fine (also a dry-run without read errors) |
| 1 | finished with file errors or unreadable folders (a dry-run too), or verify found a problem |
| 2 | aborted before copying: usage error, not enough space, destination not available, no confirmation, bad catalog |
| 130 | interrupted with Ctrl+C (partial summary; the temp file is removed) |

---

## Current version

**v0.7.0** (2026-11-08).

- Menu `[6]`: back up only Capture One to a folder you choose, and restore from one.
- A force restore replaces what differs and keeps the current files first; one command undoes it. Factory profiles need a separate option, and a monitor calibration is never replaced. [Details](#capture-ones-own-files-captureone_backuppy-v060).

**v0.6.0** added the backup of Capture One's profiles, styles and settings, with a double-click restore for a new computer.

**106 tests**, plus real checks on the archive (a dry-run of all of `G:` and a scratch copy on `E:`, hostile paths).

## Changelog

| Version | Date | What it added |
|---------|------|---------------|
| **v0.7.0** | 2026-11-08 | Capture One alone to a folder you choose; force restore that keeps the old files, and undo |
| v0.6.0 | 2026-11-08 | Keeps Capture One's own profiles, styles and settings; double-click restore for a new computer |
| v0.5.0 | 2026-11-08 | Leaves out the sRGB/resized JXL copies jxl-photo made from a master that is backed up; `DSC_0001 (2)` treated as its own photo |
| v0.4.0 | 2026-11-08 | Renamed folders moved instead of copied again; orphans listed; a backup ID per disk; verify in slices; `restore_backup.py` |
| v0.3.0 | 2026-10-07 | The interactive menu (dry-run first, then the copy after a yes) and unattended presets |
| v0.2.0 | 2026-10-07 | First release: exported photos with their originals and edits, SHA-256 catalog, verify, space check |

What each version changed, in detail: [docs/version_history.md](docs/version_history.md).

---

## Related project: jxl-photo

[jxl-photo](https://github.com/rsilvabr/jxl-photo) turns the 16-bit TIFF exports into JPEG XL masters (lossless or near-lossless, ICC and metadata preserved) and makes small derivatives for the phone. Together: jxl-photo writes `_EXPORT\16B_JXL`, and photo-backup's default `jxl>tif>jpg` then backs up that compact master instead of the TIFF — in the archive this was built for, 76 GB of JXL instead of 426 GB of TIFF for one year.

---

## Disclaimer

This tool was made for my personal workflow.
Use at your own risk — I am not responsible for any issues you may encounter.

However, if you find any bugs, feel free to report them to me — I will gladly try my best to improve this project.

Always start with `--dry-run`, and test with a small folder before backing up important archives.

---

## License

MIT License — feel free to use, modify, and distribute.

---

## Acknowledgments

- [Claude](https://www.anthropic.com/claude) (Anthropic) and [DeepSeek](https://www.deepseek.com), among other AI tools, for code assistance, reviews and technical discussion
