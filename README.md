# photo-backup — back up the photos you actually kept

You shoot 400 frames, the RAWs land in one folder, and the 30 you keep are exported into an `_EXPORT` folder inside it. photo-backup reads that export folder and backs up **those 30 photos**: the exports, plus — picked out of the 400 — their RAW, camera JPEG, edited TIFF and sidecars, plus your Capture One and NX Studio edits. The rest stays behind, or comes along folder by folder if you choose. Read-only on the source, never deletes on the destination, and verifies the backup on any later day with SHA-256. Tested on a 9 TB archive of Capture One, Lightroom and NX Studio sessions from 1991 to 2026.

A backup from the menu (`[1] Back up` or a preset) also keeps **Capture One itself**. Your `.cos` edits only *name* the ICC profiles, film curves and styles they use; on a computer without those files the same edit looks different. The backup keeps them, with your presets, recipes, shortcuts and workspaces, and a double-click restore (no Python needed) puts them back on a new computer: Capture One opens your photos with the same look and the same setup. Login data and the licence stay out. [Details](#capture-ones-own-files-captureone_backuppy-v060).

**Windows program, no Python needed:** [photo-backup-exe](https://github.com/rsilvabr/photo-backup-exe/releases/latest) — unzip and double-click.

---

## Contents

- [Why a selective backup?](#why-a-selective-backup) · [Does it fit your archive?](#does-it-fit-your-archive)
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
- [Per-folder modes (`manifest_backup.py`)](#per-folder-modes-manifest_backuppy)
- [Capture One and NX Studio](#capture-one-and-nx-studio)
- [Re-runs, changed files and the versions folder](#re-runs-changed-files-and-the-versions-folder)
- [Moved and renamed folders](#moved-and-renamed-folders)
- [Verifying a backup](#verifying-a-backup) · [A damaged catalog](#a-damaged-catalog) · [Rolling verify](#rolling-verify)
- [Which disk is this? (backup ID)](#which-disk-is-this-backup-id)
- [Restoring](#restoring)
- [Every option](#every-option) · [Settings](#settings) · [Logs](#logs) · [Short or full lists in the CSVs](#short-or-full-lists-in-the-csvs-v0131) · [Exit codes](#exit-codes)
- [Current version](#current-version) · [Changelog](#changelog)
- [Related project: photo-backup as a Windows program](#related-project-photo-backup-as-a-windows-program)
- [Run it after jxl-photo](#run-it-after-jxl-photo) · [Related project: jxl-photo](#related-project-jxl-photo)
- [Disclaimer](#disclaimer) · [License](#license) · [Acknowledgments](#acknowledgments)

---

## Why a selective backup?

A photo archive is mostly photos nobody will ever look at again. In the archive this was built for, the year folders hold ~9.3 TB, but the photos that were actually edited and exported — with their originals and edits — fit in **~1.2 TB**. photo-backup reads the decision you already made: **if a photo has an export, it matters.**

### How the archive is organized

One folder per shoot, the originals directly in it, and the exports in a folder inside it whose name contains "export". Such a folder is a **session**. A typical one, and what a backup with the default settings takes from it:

```
G:\2025\250129_Flores\                        a session: it holds an export folder
├── DSC_9358.NEF                              ✗  not exported
├── DSC_9358.JPG                              ✗
├── DSC_9359.NEF                              ✗  not exported
├── DSC_9360.NEF                              ✓  exported (see _EXPORT): its RAW,
├── DSC_9360.JPG                              ✓  its camera JPEG
├── DSC_9360.xmp                              ✓  and its sidecar
├── DSC_9361.NEF                              ✗  not exported
├── _EXPORT\                                     any folder whose name contains "export"
│   ├── 16B_JXL\DSC_9360_ProPhoto-g22_v1.jxl  ✓  the best format of the photo (jxl>tif>jpg)
│   ├── TIFF16\DSC_9360_ProPhoto-g22_v1.tif   ✗  same photo, it has a JXL
│   └── sRGB\DSC_9360_sRGB.jpg                ✗  same photo, it has a JXL
├── CaptureOne\
│   ├── Settings166\                          ✓  the Capture One edits (.cos)
│   └── Cache\                                ✗  previews; Capture One rebuilds them
├── NKSC_PARAM\                               ✓  the NX Studio edits
└── _OUTTAKES\                                ✗  the rejects (a per-folder mode can add them)
```

**One photo through three programs.** A RAW developed in NX Studio into a TIFF beside it, that TIFF edited in Capture One and exported: everything is kept. From the real session the tool was tested on:

```
_DSC6491.NEF                                           ✓  the RAW
_DSC6491.JPG                                           ✓  the camera JPEG
_DSC6491.xmp                                           ✓  its sidecar
_DSC6491_NXstudio.tif                                  ✓  developed in NX Studio, beside the RAW
_DSC6491_NXstudio.xmp                                  ✓  its sidecar
NKSC_PARAM\_DSC6491.NEF.nksc                           ✓  the NX Studio edit of the RAW
CaptureOne\Settings166\_DSC6491_NXstudio.TIF.cos       ✓  the Capture One edit of the TIFF
_EXPORT\16B_JXL\_DSC6491_NXstudio_ProPhoto-g22_v1.jxl  ✓  exported from the edited TIFF
_EXPORT\TIFF16\_DSC6491_NXstudio_ProPhoto-g22_v1.tif   ✗  same photo, it has a JXL
```

The export is matched to the shortest original name it starts with, `_DSC6491`, and every file beside the export folder whose name starts with that name comes along. It makes no difference whether you exported from the RAW or from the TIFF. The TIFF beside the RAW is an original, so the export formats do not filter it; only the files inside `_EXPORT` are.

Sessions are found at any depth. A folder without an export is not copied, but it is listed:

```
G:\2025\
├── 250129_Flores\        session (holds _EXPORT)
├── 250214_Trip\
│   ├── 01_D810\          session (holds Lightroom_Export)
│   └── 02_D200\          session (holds _EXPORT)
└── 250301_New\           no export yet: listed in _unexported.csv; copied whole
                          with --unexported copy or a "+" mode
```

The tree is mirrored on the destination (`G:\2025\250214_Trip\01_D810\DSC_0001.NEF` → `H:\BACKUP\2025\250214_Trip\01_D810\DSC_0001.NEF`), so `--source G:\` and `--source G:\2025` produce the same layout and later runs add to the previous backup.

### What a sync tool cannot do

FreeFileSync, robocopy and the like select by name and by folder: leave `_OUTTAKES` out, take only `*.NEF`, give another folder other rules. If that is all you need, they do it well.

What they cannot do is the choice in the session above: `DSC_9360.NEF` and `DSC_9361.NEF` sit in the same folder with the same type, and only the export folder says that one of them was kept. photo-backup makes that choice photo by photo:

- **The originals of what you exported.** Each export is matched to the real file names beside the export folder: `DSC_9360_ProPhoto-g22_v1.jxl` → `DSC_9360.NEF`, `.JPG`, `.xmp`. Nikon's `DSC_` and `_DSC`, Canon's `IMG_`, film scans and phone names all work; a name is never cut at the first underscore. [How a photo is selected](#how-a-photo-is-selected).
- **One export format per photo.** `jxl>tif>jpg` keeps a photo's JXL; its TIFF when it has no JXL; its JPEG when it has neither — per photo, not per folder. [Export formats](#export-formats).
- **No camera photo left without a copy.** When the photos you did not export come along too, a manifest's `Originals=raw` keeps every RAW with its sidecars, and a JPEG, TIFF, PNG, HEIC or PSD only when no RAW of the same photo is beside it: a JPEG-only or TIFF-only photo is kept, the JPEG twin of a RAW or a TIFF developed from it is not. [Originals that were not exported](#originals-that-were-not-exported---unselected).
- **A checksum from the day of the copy.** The catalog keeps the SHA-256 of every file copied, so `--verify full` tells a damaged backup copy from an original that changed without changing its date (bit rot). Comparing the two sides only says that they differ. [Verifying a backup](#verifying-a-backup).

**Different rules per folder, in one run.** A manifest CSV gives each folder its own mode — old years whole, recent years with their exports and originals — and every mode starts from the export selection above, then adds the originals that were not exported, `_OUTTAKES_KEEP`, `_OUTTAKES` or the whole folder. `--analyze` shows how many TB each mode would take before you choose. [Per-folder modes](#per-folder-modes-manifest_backuppy).

Everything it did **not** copy is counted in the summary and listed in the CSVs (folders without an export, outtakes, filtered formats), so nothing is left out without you knowing. The one exception is a folder skipped by its name (`$RECYCLE.BIN`, `System Volume Information`, `--exclude-folders`): it is not read at all, so it is named in the summary and the log, not counted file by file.

### Does it fit your archive?

It fits when:

- the photos you keep are **exported**, into a folder whose name contains `export` in any case (`_EXPORT`, `Lightroom_Export`, `C1_Export_WEB`, `Exported`; the word is a [setting](#settings)), with subfolders inside it if you like (`16B_JXL`, `sRGB`, `WEB`);
- that export folder is **directly inside the folder that holds the originals**. Lightroom and Capture One can both export this way, depending on how the export is set up;
- an export's name **starts with the name of its original**: `DSC_9360.NEF` → `DSC_9360_anything.jxl`, `DSC_9360-2.jpg`, `DSC_9360 web.jpg`.

It falls short when:

- **exports are renamed** (`Flores_001.jpg`): they are copied, but their originals are not found; they are listed in `_unmatched.csv`;
- **exports are not inside the originals' folder**: one `Pictures\Exports\` for everything, or an export folder beside the originals' folder instead of inside it. No original is selected, because only the files beside an export folder are its originals (the exports themselves are still copied when their folder's name contains `export`);
- **the choice lives only in a catalog** (Lightroom stars, picks or collections, never exported): photo-backup reads folders, not catalogs. A per-folder mode that also keeps the originals that were not exported, or the whole folder, still backs these photos up.

A `--dry-run` on one of your folders shows what it would take before anything is written.

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
| `manifest_backup.py` | Each folder of a manifest with its own mode (older years one configuration, recent years another). `--analyze` shows how many TB each mode would take per folder. It plans with `selective_backup.py`'s `_Planner` and copies with its `_run_backup`. |

The menu never copies anything itself — it runs `selective_backup.py` (or `restore_backup.py`) with the options you confirmed, so every [safety guarantee](#safety-guarantees) holds either way.

---

## Requirements & Installation

> Rather not install Python? A Windows program packages everything: see
> [photo-backup-exe](#related-project-photo-backup-as-a-windows-program).

- **Python 3.12 or newer.** Nothing else: the standard library only — no packages, no external tools. (`mobile_backup.py`, the phone mirror, also needs [ExifTool](https://exiftool.org/) to read the shot times; see [its README](README_mobile_backup.md).)
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
  [7] Manifest (each folder with its own mode)
  [0] Exit
```

- **[1] Back up** asks for the folders to back up, the destination (it lists the connected drives with their free space), the export formats, the subfolders to skip and the other options (every `--unselected` value the backend accepts; whether to copy the Capture One cache, the jxl-photo derivatives and Capture One's own files) — then runs a **dry-run** with those choices, "replace what changed" included *(v0.13.1: the plan you confirm counts the changed files that the copy will replace)*. The command line it runs always names each choice, also a "no" (`--no-c1-cache`, `--no-derivatives`), so a default changed at the top of `selective_backup.py` never turns what the menu showed into something else. You read what would be copied, what would not, and whether it fits; only then it asks **"copy now?"**. If some folders could not be read, it says so and asks again before copying the rest.
- Your choices are remembered: the next time, it shows them and asks **"Use these settings?"** — Enter, and the dry-run starts.
- **[2] Verify** runs `--verify quick` or `--verify full` on a backup. With `full` it also asks **"Max GB to verify in this run (0 = everything)"** — see [Rolling verify](#rolling-verify). Its question "Also verify Capture One's own files?" is remembered apart from the backup's: a "no" here does not turn off the Capture One step of the next backup.
- **[3] Presets**: after a backup the menu offers to save its settings (and the backup's ID) under a name. A preset runs from the menu (dry-run first) or unattended; if the drive letter changed, the menu finds the backup by its ID.
- **[4] Settings** shows where the remembered choices live (`~/.photo_backup_config.json`, or `$PHOTOBACKUP_CONFIG`) and the defaults of `selective_backup.py`. The menu reads those defaults from `selective_backup.py` itself: edit the top of that file and the menu follows.
- **[5] Restore** lists the backup's top folders, asks what to restore and where to (the original places or another folder), runs a dry-run and then restores after a yes. Files that sit loose above the listed folders (directly in the year) have a line of their own, `(files directly in 2025)`, which restores those files only *(v0.13.1)*.
- **[6] Capture One only** backs up just Capture One to a folder you choose, restores from one, force-restores and undoes the last force restore; see [Capture One's own files](#capture-ones-own-files-captureone_backuppy-v060).
- **[7] Manifest** asks for a manifest and a destination and offers `[1] Analyze only` (how many bytes each mode would take in each folder), `[2] Back up` or `[3] Verify` (the backup against what the manifest selects); see [Per-folder modes](#per-folder-modes-manifest_backuppy). `[2] Back up` does what `[1]` does: it asks whether to back up Capture One's own files too and whether to replace originals/exports that changed since the last backup (`--update-changed`; the answers are remembered for the next manifest run), then runs a dry-run with those answers — the Capture One step too — and the copy after a yes. `[3] Verify` asks quick or full, with `full` the "Max GB" (default `VERIFY_MAX_GB` of `selective_backup.py`), and verifies `_captureone` too when the backup has one.

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
  folder first, never overwritten in place. The one exception is a new file
  identical byte for byte to the old one, which takes its place: nothing is
  lost, and the versions folder only holds real old versions. The only file the
  tool removes is its own temp file (`*` + `.pbtmp`): after a failed copy or a
  crash, and the one it writes to test that the destination still takes
  writes (`DEST_CHECK_AFTER_ERRORS`).
- **Fail closed.** Not enough space, a destination drive that is not
  connected, an unreadable or damaged catalog (checked every time it is
  opened, a dry-run and a verify too, *v0.13.1*), a closed stdin without
  `--yes`, a destination inside a source (or the reverse), a plan that would
  write into the backup's own `_photo_backup` folder (a backup copied with its
  root as `--base`, *v0.13.1*): the tool aborts BEFORE the first byte is
  copied, exit code 2.
- **Nothing is skipped silently.** A folder or file that cannot be read is
  logged, counted on the console (`Errors: N ... NOT backed up`) and makes the
  run exit 1 — a dry-run too. Every file under a source is either in the plan
  or in exactly one line of the summary's `Not copied:` *(v0.12.1)*, and the
  links and junctions it does not follow are counted and logged. The exception
  is a folder skipped by its name (`SYSTEM_SKIP_FOLDERS`, `--exclude-folders`),
  at any depth and in every mode *(v0.12.1)*: it is never listed (a `System Volume Information` cannot be read, and a
  folder may be excluded because it gives errors), so its files are not
  counted; the folder itself is named (`folders skipped by name` in the
  summary, `SKIPPED FOLDER <path>` in the log).
- **A source that cannot be read is not a source that is gone** *(v0.12.1)*.
  A backup file counts as "source gone" only with proof: the system answered
  "does not exist" and the nearest folder above that does exist could be
  listed. Access denied, a drive that is not connected, a share that does not
  answer: `source_unreadable`, never moved by `--move-orphans` or by the move
  detection. A file of another source (a second drive with the same folder
  names) is `other_source`. A run with read errors moves no orphan at all.
- A copy is written to a temp file next to its final name, verified, then
  `os.replace`d — a crash never leaves a half file under the final name.
- **A destination that stops taking writes stops the backup** *(v0.12.1)*.
  After `DEST_CHECK_AFTER_ERRORS` failed copies in a row (a USB disk pulled
  out, a full disk) the tool writes and removes a test file of its own on the
  destination. If that fails too, it stops reading the source, records what
  was already copied, prints the normal summary with a `STOPPED:` line (why,
  and how many files were left to copy) and exits 1. If it works, the backup
  goes on. A catalog that can no longer be written (the disk went away with
  rows still waiting for their commit) stops the copy the same way: the summary
  adds a `CATALOG:` line with the number of copied files that have no row yet.
  They keep their final names, and the next run adopts them by their size and
  date instead of copying them again; `--move-orphans` moves nothing in that
  run.

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
- `raw_or_jpeg`: `raw`, plus every JPEG (`.jpg`, `.jpeg`), TIFF, PNG, HEIC
  (`.heic`, `.heif`, `.hif`) or PSD that has no RAW of the same photo beside
  it, and the sidecars of those photos — no photo is left without a copy
  *(v0.12.1: the TIFF, PNG, HEIC and PSD too)*. "The same photo" is the rule of
  the photo key: `DSC_4321_NXstudio.TIF` beside `DSC_4321.NEF`, or
  `P1010099.tif` beside `P1010099.ORF`, is that RAW's photo and stays out;
  `X (2).tif` is another file, not a variant of `X.NEF`, and is kept.
- `raw_jpeg`: `raw_or_jpeg`, plus every JPEG of the session.
- `all`: copy every direct file of the session.

`raw_or_jpeg` and `raw_jpeg` were added for the manifest's `Originals` column
([Per-folder modes](#per-folder-modes-manifest_backuppy)); `raw` keeps exactly
the behaviour it had.

The originals are copied only when at least one export file of that photo was
actually selected for the backup (after format filtering and
`--exclude-export-subfolders`).

## Folders without an export

A folder with no export subfolder is listed in `_unexported.csv` (path, number
of direct files, bytes, Capture One bytes without cache, NKSC_PARAM bytes, and
`copied`: `yes` when this run copies it). The summary's `folders without
export` line, under `Not copied:`, counts only those that are not copied
*(v0.12.1)*; the CSV lists all of them.
With `--unexported copy` (or a `+` mode of the manifest) the direct files,
Capture One and NKSC_PARAM folders are also copied — also for a folder that has no file of its own, only
subfolders and a `CaptureOne` folder *(v0.12.1)*: a parent folder above a
session counts as a folder without an export (it has no export of its own).

**Inside sessions.** `selective_backup.py` on its own leaves out every direct
subfolder of a session that is not an export, Capture One or NKSC_PARAM folder
(`_OUTTAKES`, `OLD_TIFFs`, `tiff_zip`, ...), with everything below it
(`SESSION_LEAVE_OUT = "*"`). With a modes table *(v0.13.0)*, a session leaves
out only the subfolders its mode names in `LeaveOut` — `_OUTTAKES*` in the
shipped table — and any other direct subfolder is **judged alone**, exactly as
the subfolder of a folder with an export and no original beside it (below): a
session if it holds an export folder; a folder without an export if not, copied
whole in a `+` mode and listed in `_unexported.csv` in the others. So a folder
with the photos of another event, put beside the export of a session, is no
longer left out in a `+` mode.

What a session leaves out — the files, and those of the left-out folder's own
`CaptureOne` and `NKSC_PARAM` folders — is counted as "inside sessions".
`_inside.csv` has one line per direct subfolder of a session left out
(session, subfolder, files, bytes), and the summary names the five biggest,
grouped by name *(v0.12.1)*:

```
  inside sessions (subfolders the mode leaves out): 1550 / 58.4 GB   -> <log>_inside.csv
      _OUTTAKES          1200 files      48.0 GB
      _OUTTAKES_DROP      350 files      10.4 GB
```

A folder that holds an export folder but **no original beside it** is not a
session for its subfolders: `G:\2017` holding `171230_Lightroom_Export`, or a
drive root holding `04_ Exported_OLD`. Its export files are copied (and listed
as exports without a matching original), but its subfolders keep their own
classification — a trip folder below it is still a folder without an export,
listed and copied with `--unexported copy`. A manifest mode's `AddWhole` and
`AddFolders` still apply to its direct subfolders *(v0.12.1)*: a film session
whose only originals are in `RAW SCANS` keeps them.

## Per-folder modes (`manifest_backup.py`)

One run can use a different rule for each folder: one set of years in one mode,
another set in another. Two CSV files say what to do:

- **The manifest** (`--manifest`, required) lists the folders:
  `Folder,Mode,Originals`. The owner's real manifest (`backup_manifest.csv`)
  stays out of git; `backup_manifest.example.csv` shows the format.
- **The modes table** (`--modes`, default `backup_modes.csv` beside the
  script) says what each mode copies:
  `Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,Only,Skip,LeaveOut,Description`.

| Column | Meaning |
|---|---|
| `Mode` | the name used in the manifest's `Mode` column |
| `Unselected` | `yes`/`no`: also copy the originals of the photos that were not exported |
| `AddFolders` | `;`-separated patterns of direct subfolders of a folder with an export folder (with or without an original beside it), copied with the filter of `Originals`. A folder inside it that has an export folder is a session (its exports and their originals are copied too), and a file is copied when either the session or the filter keeps it, also inside those export folders. What neither keeps is counted: `left out inside added folders (Originals filter)`. One exception copies less than v0.12.0: a `Cache` folder deeper than the first level of a `CaptureOne` folder inside such an export folder is counted as Capture One cache (it is made again) |
| `AddWhole` | `;`-separated patterns of direct subfolders of a folder with an export folder (with or without an original beside it), copied whole |
| `Exclude` | `;`-separated patterns of folders never walked, at any depth and in every mode; what they hold is counted: `excluded by the mode (Exclude)`. The export, Capture One and NKSC_PARAM folders of a session are not affected (inside an export folder, use `--exclude-export-subfolders`) |
| `Whole` | `yes`/`no`: the whole folder, session or not |
| `Unexported` | `yes`/`no`, optional: a folder without an export is copied whole; a folder with one follows the rest of the mode |
| `Only` | optional, `;`-separated: copy only these types. A group (`raw`, `jpeg`, `jpg`, `jxl`, `tif`, `png`, `heic`, `psd`, `dng`) or an extension (`.xmp`) |
| `Skip` | `yes`/`no`, optional: this folder stays out of the backup |
| `LeaveOut` | optional, `;`-separated patterns *(v0.13.0)*: the direct subfolders of a session (a folder with an export folder and an original beside it) left out, with everything below them — "inside sessions", `_inside.csv`. Any other direct subfolder of a session is judged alone: a session if it holds an export folder, a folder without an export if not (copied whole with `Unexported=yes`, listed in `_unexported.csv` without). Checked after `Exclude`, `AddWhole` and `AddFolders`: `_OUTTAKES_KEEP` matches `_OUTTAKES*` and mode 3 still adds it. An empty cell leaves nothing out. A table without the column (written before v0.13.0) reads it as `*`: every subfolder, as v0.12.1. With `Whole=yes` or `Skip=yes` it must be empty (they walk no session; abort, exit 2) |
| `Description` | free text shown nowhere; for the owner |

A pattern is matched against the folder name, case-insensitively; only `*` is
a wildcard, everything else is literal (so `[FINAL]` is a name, not a
character class). `yes`/`no` are case-insensitive.

**Leaving out one more name inside sessions.** Add it to `LeaveOut`: with
`_OUTTAKES*;rejects`, a session's `rejects` folder is left out too, in that
mode, counted under "inside sessions" and listed in `_inside.csv`. Without it,
`rejects` is judged alone (copied whole in a `+` mode).

**`Originals`** chooses how the photos that were not exported are kept:

| Value | Copies |
|---|---|
| `raw` (empty = this) | RAW + sidecars + every JPEG, TIFF, PNG, HEIC or PSD that has no RAW of the same photo beside it (`DSC_0001 (2).JPG` is its own photo) |
| `raw+jpeg` | `raw` + every JPEG |
| `all` | everything |

**The most specific line wins.** With `G:\2025` in mode 3 and
`G:\2025\evento` in mode 6, the event follows mode 6 only; the mode 3 line
never plans a file inside it. An export folder cannot have its own line below
another line (the line above would no longer see its session's export). A
folder repeated in the manifest aborts (exit 2), as does a file that is not
UTF-8 (in Excel, save as "CSV UTF-8"), a mode that does not exist, a folder that does not exist, folders
on more than one drive, a column the table does not know (a header typed
`Unexported ` with a space would otherwise be ignored and the mode would lose
its `+`), an empty column name in the header (an extra comma at the end of
every line, often a cell left in Excel: the message says so) or a line with
more values than columns *(v0.12.1)*, and every other
invalid table value — before any photo is read.

**Leaving a folder out, or keeping one type.** Give that folder its own line
below the larger one, with one of the modes `skip`, `raw-only`, `jpeg-only` or
`jxl-only`:

```csv
Folder,Mode,Originals
G:\2026,3+,raw
G:\2026\event I care less about,skip,
G:\2026\event from a phone,jpeg-only,
```

`Only` is literal: `jpeg-only` copies the JPEGs and nothing else, so a photo that
exists only as RAW has no copy, and `jxl-only` leaves out a JPEG not converted
yet. `raw-only` also keeps the sidecars and edit settings. The summary shows
what these lines left out, and `--analyze` shows every mode of such a folder.

**Lowering a mode deletes nothing.** A file that a lower mode no longer
selects stays on the destination and appears as a `not_selected` orphan in
`_orphans.csv`, exactly like a normal backup.

The modes shipped in `backup_modes.csv`:

```csv
Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,LeaveOut,Description
1,no,,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,_OUTTAKES*,Exports and the originals of the exported photos
2,yes,,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,_OUTTAKES*,Mode 1 + unselected originals
3,yes,_OUTTAKES_KEEP,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,_OUTTAKES*,Mode 2 + _OUTTAKES_KEEP
4,yes,_OUTTAKES_KEEP;_OUTTAKES,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,_OUTTAKES*,Mode 3 + _OUTTAKES
5,yes,_OUTTAKES*,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,_OUTTAKES*,Mode 4 + every _OUTTAKES*
6,no,,,,yes,no,,The whole folder
```

(Shown without the `Only` and `Skip` columns, which are empty in these six.)

In modes 1 to 5 a session leaves out its `_OUTTAKES*` folders (those the mode
does not add); every other subfolder of a session is judged alone *(v0.13.0)*.
Modes `1+` to `5+` are the same five with `Unexported=yes`: a folder with an
export follows the mode, and a folder not exported yet is copied whole — also
a subfolder of a session that `LeaveOut` does not name — so what has not been
edited or selected is not left out.

A bigger mode never copies less *(v0.12.1)*: whatever mode `N` copies, mode
`N+1` and mode `N+` copy too (`_OUTTAKES\_EXPORT` is a session in mode 3 and
stays one when mode 4 adds `_OUTTAKES`). The test suite checks it on a tree with
outtakes, exports inside outtakes, film scans, folders not exported yet and
session subfolders without a special name, with and without an export of their
own *(v0.13.0)*. A table without `LeaveOut`, or with `*`, plans exactly what
v0.12.1 planned on that tree.

Measured with `--analyze` on one 11 TB drive listed as a single line
(2026-11-09). The `all` column also carries loose TIFF, JXL and other files:

| Mode | What it copies | `raw` | `raw+jpeg` | `all` |
|---|---|---:|---:|---:|
| 1 | exports + originals of the exported photos + film scan folders | 1.37 TB | 1.37 TB | 1.37 TB |
| 2 | mode 1 + unselected originals | 2.41 TB | 2.68 TB | 2.88 TB |
| 3 | mode 2 + `_OUTTAKES_KEEP` | 2.50 TB | 2.80 TB | 3.00 TB |
| 4 | mode 3 + `_OUTTAKES` | 3.84 TB | 4.57 TB | 4.77 TB |
| 5 | mode 4 + every `_OUTTAKES*` | 4.25 TB | 5.12 TB | 5.33 TB |
| 6 | the whole folder, also what has no export (without the Capture One `Cache`) | | | 11.16 TB |

Options of `manifest_backup.py`:

| Option | Description |
|---|---|
| `--manifest PATH` | the manifest CSV (`Folder,Mode,Originals`); required |
| `--dest PATH` | destination root; required unless `--analyze` |
| `--modes PATH` | the modes table (default `backup_modes.csv` beside the script) |
| `--analyze` | print how many bytes each mode would take in each folder and write `<stamp>_analysis.csv`; copies nothing and needs no destination |
| `--dry-run` | plan, print the summary and write the logs/CSVs; copy nothing |
| `--yes` | do not ask for confirmation |
| `--update-changed` | move a changed destination file to the versions folder before copying the new one |
| `--no-auto-update` | do not update changed edit settings/sidecars (`AUTO_UPDATE_EXTENSIONS`) unless `--update-changed` is given |
| `--verify-copy {none,size,hash}` | verification of each copy (default: `hash`, from `selective_backup.py`) |
| `--space-margin-gb N` | free space kept on the destination, in GB (default: `20.0`, from `selective_backup.py`) |
| `--export-formats SPEC` | `all`, `a>b>c` fallback or `a+b` multiple (default: `jxl>tif>jpg`, from `selective_backup.py`) |
| `--expect-backup-id ID` | abort (exit 2) unless the destination holds this backup ID |
| `--verify {quick,full}` | verify the backup against what the manifest selects; copies nothing. Same statuses and exit codes as `selective_backup.py --verify` |
| `--max-gb N` | with `--verify full`: verify at most N GB in this run, oldest verification first (default: `0.0` = everything, from `selective_backup.py`) |
| `--version` | print the version and exit |
| `--help` | print the usage and exit |

Verify a backup made from a manifest with `manifest_backup.py --verify`, not
with `selective_backup.py --verify`: only the manifest knows which files each
line selects. A file a line selects that is not on the backup is
`NOT_BACKED_UP`; a folder a line leaves out is not reported.

`--analyze` plans every mode of the table with every `Originals` value through
the same `_Planner` the copy uses, sharing the folder scan between the modes,
and marks the mode chosen by each manifest line. A folder that could not be
read makes the numbers partial: the run says so and exits 1. The copy itself is
`selective_backup.py`'s `_run_backup`: the same catalog, space check, versions
folder, move detection and orphan report.

## Capture One and NX Studio

- Every folder named `CaptureOne` (configurable) is copied in full, except
  its direct subfolders named `Cache` (configurable) — those are regenerable.
  Use `--c1-cache` to copy them too.
- Every folder named `NKSC_PARAM` is copied in full.
- Both are copied without filtering by photo (they are small), for sessions
  and, with `--unexported copy`, for folders without an export.

## Capture One's own files (`captureone_backup.py`) *(v0.6.0)*

The `.cos` files beside your photos hold the edits, but they only **name** the ICC profile, film curve and style they use. On a computer without those files, Capture One shows the edit differently. Every backup from the menu's `[1] Back up` and `[7] Manifest` (and every preset) therefore also keeps, in `<destination>\_captureone`:

| Folder in the backup | Where it comes from | What it holds |
|---|---|---|
| `UserData` | `%LOCALAPPDATA%\CaptureOne` | your profiles, styles (with the profiles they carry), defaults, presets, recipes, shortcuts, workspaces, plugins, any folder you add |
| `Preferences` | `%LOCALAPPDATA%\Capture_One` | `user.config` of each version |
| `Installed` | `Color Profiles` and `Film Curves` of the install folder | the profiles the `.cos` files name, in case a later version drops one |
| `WindowsColor` | the Windows colour folder | its `.icc`/`.icm` files (export and monitor profiles) |

The command-line scripts (`selective_backup.py`, `manifest_backup.py`) do not keep these: with them, also run `[6] Capture One only` (or `captureone_backup.py --dest`) to the same destination.

Left out on purpose: login data and cookies (`MSALCache*`, the WebView folders), the licence file, `Logs`, `Stability`, and the program itself. `WHERE_EACH_FOLDER_GOES.txt` in that folder says where each part came from.

A link or junction inside a Capture One folder is not followed, and the summary says so *(v0.13.1)*: `Links/junctions not followed: N (what is behind them is NOT in this backup)`, with their paths (all of them in the log). A Capture One folder that cannot be reached is an error (exit 1), not "nothing found", and the files of the backup under a folder that could not be read in this run are not counted as "gone from Capture One" *(v0.13.1)*.

Keep the installer and the serial in a folder inside `%LOCALAPPDATA%\CaptureOne` (`_Instalador` by default): they are copied with the rest.

**On a new computer:**

1. Install Capture One from `_captureone\UserData\_Instalador`.
2. **Before opening it**, double-click `_captureone\RESTORE_CAPTUREONE.cmd`. It needs no Python, asks Windows for administrator rights and only adds missing files. (Opening Capture One first creates its default recipes, and a file that already exists is never replaced.) The script and `WHERE_EACH_FOLDER_GOES.txt` are written from everything the backup holds *(v0.13.1)*: a run that does not find a folder on this computer (Capture One was reset, another Windows user ran it) no longer drops that folder from them.
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

A restore brings back the profiles, styles, defaults, presets, recipes, shortcuts and workspaces, plus any missing file of `Installed` and `WindowsColor`. The batch queue, `ImageCore`, plugins, `Preferences` and your own folders stay in the backup unless you name them with `--also`. It refuses to run while Capture One is open, and reports what needs an administrator instead of failing half-way. As everywhere in this tool, nothing in the backup is deleted: the old copy of a file that really changed goes to the versions folder, and only once the new copy is on the disk — a copy that fails leaves the old one under its name *(v0.12.1)*. A file whose catalog row has no hash is restored after a size check only, as `RESTORED_UNVERIFIED` (counted apart in the result); a copy of another size is not restored (`CORRUPT_IN_BACKUP`, exit 1). A damaged copy of the same size is therefore restored as it is: what finds it is a `--verify` before the restore (`DEST_DIFFERS`, when the file on this computer still has the size and date of the row).

**Force restore and undo** *(v0.7.0)*. A normal restore only adds what is missing. `--force` also replaces what differs, after keeping the current file in `%LOCALAPPDATA%\CaptureOne_old\old_<date>_<time>_<ms>`; only files that really differ go there, and they are never removed for you. `--revert-last` puts the newest `old_` folder back and takes out what the force added (a file is journaled as added only after the force's copy of it is there: a file another program put there meanwhile is never taken out, *v0.12.1*). It keeps what it replaces in a `revert_old_…` folder, which is never taken for the last one. Since v0.13.1: a read-only file is replaced or taken out once it is safe in the snapshot (before, the undo failed on it); each file is undone where the force really put it (the journal records the full path, so an `--install-folder` given only to the force is not needed again); and a target that appears during the undo is left as it was (`CONFLICT`, exit 1).

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
- **Same bytes, new date.** Capture One rewrites every `.cos` of a folder it
  opens, without changing them. Such a file is still copied, but when the bytes
  copied are identical to the old copy, the old copy is not kept in the versions
  folder (the log says `IDENTICAL`). A different file is always kept.
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

The space check still counts a possible move as a copy: it is only confirmed
by hash during the run. When that makes the backup not fit, the message says
how much of what is needed may be moves *(v0.12.1)*: `Not enough space: need
..., free ..., margin ...; 120.0 GB of it may be files moved inside the backup
(possible moves, ...)`. Make room, or use `--no-detect-moves` knowing that
everything is then copied again.

A backup file whose source no longer exists is an **orphan** (an original you
deleted after it was exported). Every run writes `<log>_orphans.csv` with the
reason for each:

- `source_missing` — the original is gone (proven: see below);
- `not_selected` — the original still exists, but the current selection does
  not include it any more (for example you changed `--export-formats`);
- `source_unreadable` *(v0.12.1)* — the original could not be checked (access
  denied, a drive that is not connected): not proof that it is gone;
- `other_source` *(v0.12.1)* — the file came from a source that is not given in
  this run (two drives with the same folder names backed up to one
  destination): not checked.

"Gone" needs proof: the system answered "does not exist" and the nearest
folder above that does exist could be listed. The move detection above uses
the same proof. The proof is by **path**: another disk mounted on the same
letter, with the same folders, passes it — after changing the source disk,
look at a dry-run before using `--move-orphans`. And the catalog keeps the
source path a file had when it was copied: if the source moves to another
letter or folder, what is deleted there later shows as `other_source` and is
never moved by `--move-orphans`.

Orphans are only reported by default. `--move-orphans` (real copies only)
moves the `source_missing` ones to
`<dest>\_photo_backup\orphans\<YYMMDD_HHMMSS>\<rel>`, removes their catalog
rows and cleans the folders the move left empty; `not_selected` files are
never touched, and neither are the other reasons. A run with an error in its
plan (a folder or file that could not be read, two files with one destination
path) moves no orphan at all, and says so: `--move-orphans: nothing moved: N
errors in the plan of this run`. Since the old
copy is only moved inside the backup, the space does not shrink until you
delete the orphans folder yourself — that is outside this tool's scope.

## Verifying a backup

`--verify quick` checks that each catalogued file still exists with the
recorded size and mtime; `--verify full` additionally hashes the destination
(and the source when needed) and detects content corruption. Verify never
copies, never asks for confirmation and never creates the destination
(destination or catalog missing → exit 2). Use the same selection flags you
backed up with.

Statuses: `OK`, `HASH_FILLED` (an adopted file's hash was recorded),
`NOT_BACKED_UP`, `DEST_MISSING`, `DEST_SIZE`, `DEST_UNREADABLE` *(v0.13.1: the
backup copy is there and gave an error when it was read — what a failing disk
shows first)*, `SOURCE_MISSING` (the original is proven gone, as for the
orphans), `SOURCE_UNREADABLE` *(v0.12.1: the original could not be checked;
v0.13.1: also when it gave an error while it was read)*, `SOURCE_CHANGED`,
`DEST_CORRUPT`, `DEST_DIFFERS`, `SOURCE_CORRUPT_SUSPECT` (the source content
changed without changing size or mtime — the most important to report). The
counts go to the console; `_verify.csv` lists every file that is not `OK`
(`VERIFY_CSV_OK = True` lists the `OK` ones too), and the log gets each of
those lines the moment it is found. Exit 0 only if every file is `OK` or
`HASH_FILLED`.

A file that cannot be read never stops the verify *(v0.13.1)*: it gets its
status, the run goes on, and the exit code is 1.

**A long verify keeps what it has checked** *(v0.13.1)*. `--verify full`
writes the verification dates to the catalog every `VERIFY_COMMIT_S` (30 s),
in one short transaction, so a backup started meanwhile is not locked out. A
Ctrl+C (exit 130) still prints the summary and writes `_verify.csv` for what
was checked, and the next run goes on from there. If the destination itself
goes away in the middle (a USB disk that drops), the verify stops with
`STOPPED: the destination is not available any more` and exit 1, instead of
listing every remaining file as `DEST_MISSING`.

**Two disks are read at the same time** *(v0.13.1)*. When the backup and the
source are on different drives, `--verify full` reads the copy and its
original together: the time of the slower disk instead of the sum. Files on
one drive letter are read one after the other; `VERIFY_PARALLEL = False`
forces that everywhere (two partitions of one hard disk).

### A damaged catalog

Every run checks the catalog when it opens it (`PRAGMA quick_check`). A
damaged one stops the run with `catalog is damaged (...)`, exit 2 — a backup,
a dry-run, a verify and a restore alike *(v0.13.1; before, a verify could say
`OK` over it)*. The copied files are not touched by this. To go on: move
`_photo_backup\catalog.sqlite` to another name and run the backup again. The
files already on the destination are adopted by their size and date (nothing
is copied again) and the next `--verify full` records their hashes again from
the originals. What is lost is the old hashes: until that verify, a copy
damaged earlier cannot be told from a good one.

Only the catalog rows under the given sources are checked. The others are
counted *(v0.12.1)*, without changing the exit code:

```
2 files / 0.0 GB of the catalog are outside these sources: not checked
```

With `--verify full`, the backup copy is judged **before** the source: when
the original was deleted or changed, the copy may be the only one left. A corrupted copy is `DEST_CORRUPT` even if the original is gone; an
intact one is `SOURCE_MISSING` / `SOURCE_CHANGED` with the detail "backup
copy hash verified".

Note: the `hash` mode of `--verify-copy` re-reads the temp file right after
writing, and Windows serves that read from its cache, not from the disk
(measured: 0 MB read from the disk while 2 GB were re-read). It catches a copy
damaged in memory, not a bad write on the media. The real check of the media
is a `--verify full` with a cold cache: after the backup disk was safely
removed and connected again, or after a restart. Run right after the backup,
a `--verify full` can come out of the cache too.

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
advanced too. A row that stays without `verified_at` (`DEST_CORRUPT`,
`DEST_MISSING`, `DEST_SIZE`, ...) is checked again every run and keeps the exit
code 1, but its size does not count against `--max-gb` *(v0.12.1)*: problems
at the front of the queue never stop the rest of the backup from being
verified, so the wheel keeps turning. `--max-gb` only applies to
`--verify full`; with `quick` it is ignored (the log says so). Only a
`--verify full` records `verified_at` *(v0.12.1)*: a `--verify quick` reads no
file, and a nightly quick between the slices of a rolling full no longer
sends the queue back to its start. A file copied again (`--update-changed`, an
auto-updated sidecar) loses its `verified_at` *(v0.13.1)*: the date was of the
old bytes, so the new copy goes to the front of the queue. A run that is
interrupted, or that a task's time limit ends, keeps what it had checked
*(v0.13.1)*. The default is `VERIFY_MAX_GB` (0 = verify everything).

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
| `--only REL` | restore only this folder or file (repeatable, case-insensitive); default: everything. An `--only` that matches nothing in the catalog is named (`--only REL: matches nothing in the catalog`), the others are restored, and the run exits 1 *(v0.12.1)* |
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
  error; all exit 1;
- an `--only` that matches nothing also makes the run exit 1.

A restored file gets the date its copy has in the backup. When that date is
the catalog's within `MTIME_TOLERANCE_S` (a backup disk in FAT/exFAT keeps
dates in 2 s steps), it gets the exact date the catalog recorded *(v0.13.1)*.
While it restores, a progress line shows files, GB, speed and time left
*(v0.13.1)*.

Exit codes: 0 (everything restored or already there), 1 (any of the above),
2 (aborted before restoring: no catalog or a damaged one, wrong backup ID, `--to` inside the
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
| `--unselected {skip,raw,raw_or_jpeg,raw_jpeg,all}` | originals of photos that were not exported (default `skip`; see [the values](#originals-that-were-not-exported---unselected)) |
| `--unexported {skip,copy}` | folders without an export (default `skip`) |
| `--c1-cache` | also copy the Capture One `Cache` folders |
| `--no-c1-cache` | do not copy them, whatever `COPY_CAPTUREONE_CACHE` says *(v0.12.1; the menu always passes one of the two)* |
| `--derivatives` | also copy the JXLs jxl-photo derived from a master that is copied ([details](#jxl-photo-derivatives---derivatives-v050)) |
| `--no-derivatives` | skip those JXLs, whatever `COPY_JXLPHOTO_DERIVATIVES` says *(v0.12.1; the menu always passes one of the two)* |
| `--exclude-folders NAMES` | `;`-separated folder names skipped anywhere |
| `--verify-copy {none,size,hash}` | verification of each copy (default `hash`) |
| `--update-changed` | move a changed destination file to the versions folder before copying the new one |
| `--no-auto-update` | do not update changed edit settings/sidecars (`AUTO_UPDATE_EXTENSIONS`) unless `--update-changed` is given |
| `--no-detect-moves` | copy a moved/renamed file again instead of moving its backup copy |
| `--move-orphans` | move backup files whose source is proven gone into `_photo_backup\orphans\<stamp>` (real copies only; nothing in a run with an error in its plan, or one the destination stopped) |
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
| `SESSION_LEAVE_OUT` | `*` | `;`-separated direct subfolders of a session left out ("inside sessions") when no modes table says otherwise: `selective_backup.py` on its own, and a modes table without the `LeaveOut` column. `*` = every one, as up to v0.12.1 *(v0.13.0)* |
| `EXCLUDE_FOLDERS` | (empty) | `;`-separated folder names skipped anywhere |
| `SYSTEM_SKIP_FOLDERS` | `$RECYCLE.BIN;System Volume Information` | always skipped |
| `VERIFY_COPY` | `hash` | default `--verify-copy` |
| `SPACE_MARGIN_GB` | `20.0` | free space kept on the destination |
| `MTIME_TOLERANCE_S` | `2.0` | mtime tolerance (FAT/exFAT stores 2 s steps) |
| `COPY_CHUNK_MB` | `8` | read/write block size, at most: a smaller file is read with a block of its own size *(v0.13.1; 2.5 ms less per small file)* |
| `READ_AHEAD_MB` | `512` | backup: source data read ahead in RAM while the destination writes (`0` = one disk at a time) |
| `SMALL_FILE_KB` | `102400` | backup: files below this are flushed to disk in batches |
| `FLUSH_BATCH_FILES` | `50` | backup: files per flush batch and per catalog commit (`1` = flush each file) |
| `FLUSH_THREADS` | `8` | backup: simultaneous flushes of a batch |
| `TEMP_SUFFIX` | `.pbtmp` | in-progress copy suffix |
| `DEST_CHECK_AFTER_ERRORS` | `20` | backup: after this many failed copies in a row, the destination is tested (a file of its own, `*.pbtmp` in `CATALOG_FOLDER`, written and removed); when the test fails the backup stops (`0` = never test) *(v0.12.1)* |
| `CATALOG_FOLDER` | `_photo_backup` | catalog and versions folder under the destination |
| `PROGRESS_EVERY_S` | `5.0` | console progress interval |
| `AUTO_UPDATE` | `True` | changed edit settings/sidecars are updated without `--update-changed` (`--no-auto-update` turns it off) |
| `AUTO_UPDATE_EXTENSIONS` | `.xmp`, `.dop`, `.pp3`, `.cos`, `.comask`, `.nksc` | extensions treated that way |
| `DETECT_MOVES` | `True` | detect moved/renamed catalogued files and move their backup copy (`--no-detect-moves`) |
| `ORPHANS_FOLDER` | `orphans` | under `CATALOG_FOLDER`: where `--move-orphans` puts files whose source is gone |
| `BACKUP_ID_FILE` | `volume.json` | under `CATALOG_FOLDER`: the backup identity (F2) |
| `VERIFY_MAX_GB` | `0.0` | default `--max-gb` for `--verify full` (0 = everything) |
| `VERIFY_PARALLEL` | `True` | `--verify full` reads the backup copy and the source at the same time when they are on two drives; `False` = one after the other *(v0.13.1)* |
| `VERIFY_COMMIT_S` | `30.0` | `--verify full` writes what it has verified to the catalog at least this often *(v0.13.1)* |
| `PLAN_CSV_UP_TO_DATE` | `False` | `_plan.csv` of a run that copies: also one line per `UP_TO_DATE` file. A `--dry-run` always lists every planned file *(v0.13.1)*. [For and against](#short-or-full-lists-in-the-csvs-v0131) |
| `VERIFY_CSV_OK` | `False` | `_verify.csv` and the verify's `_plan.csv`: also one line per file that is `OK` *(v0.13.1)*. [For and against](#short-or-full-lists-in-the-csvs-v0131) |
| `SUMMARY_FOLDER_LINES` | `25` | lines of the per-folder table in the summary (the CSV has all) |
| `SUMMARY_INSIDE_NAMES` | `5` | subfolder names shown under "inside sessions" in the summary (`_inside.csv` has all) |
| `LOG_DIR` | `$PHOTOBACKUP_LOG_DIR` or `Logs\` next to the script | where the logs are written |

`restore_backup.py` has its own setting at the top:

| Constant | Default | Meaning |
|---|---|---|
| `SPACE_MARGIN_GB` | `5.0` | free space kept on each restore target drive (default `--space-margin-gb`) |

## Logs

Every run writes `<LOG_DIR>\<YYMMDD_HHMMSS>_backup` (or `_verify`) plus:

- `.log` — command line, effective settings, summary, every `COPY`,
  `SKIP CHANGED`, `VERSION` and `ERROR`, every link or junction not followed
  (`LINK not followed <path>`), every folder skipped by its name
  (`SKIPPED FOLDER <path>`, *v0.12.1*), the destination tests
  (`DEST CHECK ...`), final summary;
- `_plan.csv` — `action,category,size,mtime,src,rel` (written on every run).
  A `--dry-run` lists every planned file. A run that copies lists what it
  changes (`NEW`, `CHANGED`), and the log says how many `UP_TO_DATE` files
  were left out of the list *(v0.13.1: on a 425 000-file backup the full
  list was 83 MB every run; `PLAN_CSV_UP_TO_DATE = True` brings it back)*. A
  date Windows cannot show in local time (before 1970, after the year 3000) is
  written in UTC *(v0.13.1; one such file stopped every backup before)*;
- `_unexported.csv` — `folder,files,bytes,c1_bytes,nx_bytes,copied` (always
  written, even with `--unexported skip`; `copied` is `yes` or `no`,
  *v0.12.1*);
- `_unmatched.csv` — `session,export_file`;
- `_inside.csv` — `session,subfolder,files,bytes`: every direct subfolder of a
  session left out ("inside sessions"), with what it holds *(v0.12.1)*; with a
  modes table, only those its `LeaveOut` names *(v0.13.0)*;
- `_by_folder.csv` — `folder,files,bytes,cumulative_bytes` for what would be
  copied, by top folder (always written; the summary shows at most
  `SUMMARY_FOLDER_LINES` of them);
- `_orphans.csv` — `reason,rel,size,source_path,copied_at` (always written,
  header only when there is no catalog);
- `_verify.csv` — `status,rel,source_path,detail` (verify runs only): the
  files that are not `OK` *(v0.13.1; `VERIFY_CSV_OK = True` lists every file
  checked)*. A verify's `_plan.csv` follows the same rule.

Nothing in `Logs\` is ever removed by the tool: delete old runs yourself when
the folder grows.

### Short or full lists in the CSVs *(v0.13.1)*

Two settings at the top of `selective_backup.py` choose how much the two big
CSVs list. Both are `False` by default (short lists); set them to `True` for
the lists of v0.13.0 and before. They change only what is written to `Logs\`:
never what is copied, what is verified, the summary on the screen or the exit
code.

| Setting | `False` (default): short | `True`: full |
|---|---|---|
| `PLAN_CSV_UP_TO_DATE` | `_plan.csv` of a run that copies lists the `NEW` and `CHANGED` files | it also lists every `UP_TO_DATE` file |
| `VERIFY_CSV_OK` | `_verify.csv` and the verify's `_plan.csv` list the files with a problem (and `HASH_FILLED`) | they also list every `OK` file |

A `--dry-run` writes the full `_plan.csv` with either value.

**Short lists (the default)**

- *For:* a run where little changed writes a few lines instead of one per
  file. On a 425 000-file backup the full `_plan.csv` is 83 MB per run and the
  full verify CSVs about 250 MB; a nightly task adds some 30 GB a year to
  `Logs\`, more if that folder is synced to a cloud drive.
- *For:* what changed, or what is wrong, is the whole file. Nothing to filter
  in Excel, and a problem cannot hide among 425 000 `OK` lines.
- *Against:* the CSV of a run that copies is no longer the list of everything
  the backup holds. For that list, run a `--dry-run` (it reads only) or look in
  the catalog.
- *Against:* after a rolling verify (`--max-gb`), `_verify.csv` no longer says
  which files were checked that night, only which had a problem. The summary
  still gives the counts, and each file's `verified_at` is in the catalog.
- *Against:* a script or spreadsheet of your own that expects one line per
  file gets fewer lines. The log says how many were left out
  (`_plan.csv: N UP_TO_DATE files not listed`, `_verify.csv: N OK files not
  listed`).

**Full lists**

- *For:* every run leaves the complete list, so two runs can be compared file
  by file, and a file's `OK` on a given night is on paper without opening the
  catalog.
- *Against:* the size above on every run, and CSVs slow to open (Excel stops at
  1 048 576 lines).

Either way the tool deletes nothing from `Logs\`. A `--dry-run` of a big
manifest still writes its 83 MB every time.

`restore_backup.py` writes its own `<YYMMDD_HHMMSS>_restore.log` and
`<YYMMDD_HHMMSS>_restore.csv` (`status,rel,target,detail`).

CSVs are UTF-8 with BOM (`utf-8-sig`), which opens correctly in Excel with
Japanese file names.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | everything fine (also a dry-run without read errors) |
| 1 | finished with file errors or unreadable folders (a dry-run too), the destination stopped taking writes, or verify found a problem |
| 2 | aborted before copying: usage error, not enough space, destination not available, no confirmation, bad or damaged catalog, a plan that would write into `_photo_backup` |
| 130 | interrupted with Ctrl+C (partial summary; the temp file is removed; a verify keeps what it had checked) |

---

## Current version

**v0.13.1** (2026-11-11).

- Fixes and speed from a second bug sweep, most of them for a long `--verify full`. A file that cannot be read — the backup copy or the original — no longer ends the verify with a traceback: it is `DEST_UNREADABLE` or `SOURCE_UNREADABLE` and the run goes on. What a verify has checked is written to the catalog every 30 s and survives a Ctrl+C, which still prints the summary and writes `_verify.csv`. The backup copy and its original are read at the same time when they are on two drives, and a small file is read with a block of its own size (5 000 small files: copy 26 s → 10.5 s, verify full 20 s → 2.1 s). A damaged catalog stops every run with a message instead of an `OK`; a file dated before 1970 or after the year 3000 no longer stops the backup; a backup copied with its own root as `--base` is refused; the menu's dry-run carries "replace what changed", and `[5] Restore` no longer restores a whole year for its loose files. `_plan.csv` of a run that copies and `_verify.csv` list what changed or is wrong, not every file (settings bring the full lists back). `captureone_backup.py`: junctions are reported, the double-click restore is written from everything the backup holds, a read-only file no longer breaks the undo. `mobile_backup.py` 0.1.3: a photo exported again during a run no longer leaves its album skipped. [Details](docs/version_history.md#v0131).

**v0.13.0** (2026-11-11):

- A session leaves out only the subfolders its mode names. The modes table has a new column, `LeaveOut` (`_OUTTAKES*` in the shipped modes); any other subfolder of a session — scans, a "Selected" folder, the photos of another event put beside the export — is judged alone: a session if it holds an export, a folder without an export if not, copied whole in a `+` mode and listed in `_unexported.csv` in the others. **Copies more on purpose** in the `+` modes; no mode copies less. A modes table of your own without the column keeps the v0.12.1 behaviour, and so does `selective_backup.py` on its own. [Details](docs/version_history.md#v0130).

**v0.12.1** (2026-11-11):

- Fixes from a bug sweep. Every file under a source is now in the plan or in exactly one line of `Not copied:` (what `AddFolders` leaves out, the Capture One folders inside sessions, the links and junctions not followed; a folder skipped by its name is named), and a bigger mode never copies less: `_OUTTAKES\_EXPORT` stays a session when mode 4 adds `_OUTTAKES`. A source that cannot be read is no longer treated as gone by `--move-orphans`, the move detection or `--verify`. The rolling verify no longer stops behind rows with a problem, and a `--verify quick` no longer sends it back to the start. A destination that stops taking writes stops the backup, also when its catalog can no longer be written. The menu keeps `raw_or_jpeg`, always says "no" on the command line, and `[7] Manifest` does what `[1]` does (Capture One, `--update-changed`, "Max GB"). `captureone_backup.py` keeps the old copy under its name until the new one is safe and checks a row without a hash against its source. `mobile_backup.py` 0.1.2 deletes nothing of a source it was not given or could not read, and saves its state more often. Two changes copy less on purpose (a folder matching a mode's `Exclude` is never walked; a folder skipped by its name, such as `$RECYCLE.BIN` or one of `--exclude-folders`, is skipped everywhere, inside `AddFolders` too) and one more (`Originals=raw` keeps a photo whose only original is a TIFF, PNG, HEIC or PSD). [Details](docs/version_history.md#v0121).

**v0.12.0** made sure a file never counts as saved when the right content cannot be restored (eight fixes from an audit) and added `manifest_backup.py --verify`.

**769 tests**, plus real checks on the archive (a dry-run of all of `G:` and a scratch copy on `E:`, hostile paths).

## Changelog

| Version | Date | What it added |
|---------|------|---------------|
| **v0.13.1** | 2026-11-11 | Second bug sweep: a verify that survives unreadable files and Ctrl+C and reads both disks at once; small files read faster; a damaged catalog and odd file dates handled; shorter CSVs |
| v0.13.0 | 2026-11-11 | A session leaves out only the subfolders its mode names (`LeaveOut`, `_OUTTAKES*`); any other one is judged alone, copied whole in a `+` mode |
| v0.12.1 | 2026-11-11 | Bug sweep: nothing left out without a count, a bigger mode never copies less, an unreadable source is not a source that is gone, the rolling verify keeps turning; `Originals=raw` keeps TIFF/PNG/HEIC/PSD-only photos; `[7] Manifest` does what `[1]` does |
| v0.12.0 | 2026-11-10 | A file never counts as saved when the right content cannot be restored: eight fixes; `manifest_backup.py --verify` |
| v0.11.0 | 2026-11-10 | A manifest line can leave a folder out or keep only one type of file in it |
| v0.10.0 | 2026-11-10 | Modes `1+` to `5+`: a folder not exported yet is copied whole |
| v0.9.0 | 2026-11-09 | Per-folder backup modes from a manifest, with `--analyze` to see the size of each mode first |
| v0.8.1 | 2026-11-09 | The versions folder no longer keeps an old copy identical to the new one |
| v0.8.0 | 2026-11-09 | Faster backups: read-ahead while the destination writes, small files flushed in batches |
| v0.7.0 | 2026-11-08 | Capture One alone to a folder you choose; force restore that keeps the old files, and undo |
| v0.6.0 | 2026-11-08 | Keeps Capture One's own profiles, styles and settings; double-click restore for a new computer |
| v0.5.0 | 2026-11-08 | Leaves out the sRGB/resized JXL copies jxl-photo made from a master that is backed up; `DSC_0001 (2)` treated as its own photo |
| v0.4.0 | 2026-11-08 | Renamed folders moved instead of copied again; orphans listed; a backup ID per disk; verify in slices; `restore_backup.py` |
| v0.3.0 | 2026-10-07 | The interactive menu (dry-run first, then the copy after a yes) and unattended presets |
| v0.2.0 | 2026-10-07 | First release: exported photos with their originals and edits, SHA-256 catalog, verify, space check |

What each version changed, in detail: [docs/version_history.md](docs/version_history.md).

---

## Related project: photo-backup as a Windows program

[photo-backup-exe](https://github.com/rsilvabr/photo-backup-exe) packages this backup as `photo-backup.exe`: no Python to install. Unzip and double-click for the menu, or use the same command lines as here (`photo-backup.exe backup ...`, `manifest`, `restore`, `captureone`). The scripts inside are byte-for-byte those of a photo-backup release, and the settings at their top can be edited there as here. Presets and backups are shared: a backup made by one version is continued, verified and restored by the other.

This Python version, the one I run on my own archive, remains by far the more tested. Use the program if you would rather not install Python; otherwise use the scripts here.

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
