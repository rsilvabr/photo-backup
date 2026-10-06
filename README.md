# photo-backup — back up the photos you actually kept

A selective backup for photographers' archives. It backs up **the photos you exported** — the exports themselves, plus the RAW, camera JPEG, edited TIFF and sidecars they came from, plus your Capture One and NX Studio edits — and leaves the outtakes behind. Read-only on the source, never deletes on the destination, and verifies the backup on any later day with SHA-256. Tested on a 9 TB archive of Capture One, Lightroom and NX Studio sessions from 1991 to 2026.

---

## Contents

- [Why a selective backup?](#why-a-selective-backup)
- [Features](#features)
- [Requirements & Installation](#requirements--installation)
- [Quick start](#quick-start)
- [Safety guarantees](#safety-guarantees)
- [How a photo is selected](#how-a-photo-is-selected)
- [Export formats](#export-formats)
- [Originals that were not exported](#originals-that-were-not-exported---unselected)
- [Folders without an export](#folders-without-an-export)
- [Capture One and NX Studio](#capture-one-and-nx-studio)
- [Re-runs, changed files and the versions folder](#re-runs-changed-files-and-the-versions-folder)
- [Verifying a backup](#verifying-a-backup)
- [Every option](#every-option) · [Settings](#settings) · [Logs](#logs) · [Exit codes](#exit-codes)
- [Current version](#current-version) · [Changelog](#changelog)
- [Related project: jxl-photo](#related-project-jxl-photo)
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

---

## Requirements & Installation

- **Python 3.12 or newer.** Nothing else: the standard library only — no packages, no external tools.
- **Windows.** All file I/O uses extended-length `\\?\` paths, so paths longer than 260 characters (Japanese folder names, deep sessions) work.

```console
git clone https://github.com/rsilvabr/photo-backup.git
cd photo-backup
python photo_backup.py --help
```

Optional, to run the tests: `python -m pip install pytest`, then `python -m pytest tests`.

---

## Quick start

```console
:: 1. See what a backup would do (writes only logs, no destination)
python photo_backup.py --source "G:\2025" --dest "H:\BACKUP" --dry-run

:: 2. Back up for real (shows the summary and asks for confirmation)
python photo_backup.py --source "G:\2025" --dest "H:\BACKUP"

:: 3. Verify the backup another day
python photo_backup.py --source "G:\2025" --dest "H:\BACKUP" --verify full
```

> **Tip:** always start with `--dry-run`. The summary lists what would be copied, what would not (and why), and whether it fits. The full lists are in the CSVs next to the log.

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

## Every option

```text
photo_backup.py --source PATH [--source PATH ...] --dest PATH [options]
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
| `--exclude-folders NAMES` | `;`-separated folder names skipped anywhere |
| `--verify-copy {none,size,hash}` | verification of each copy (default `hash`) |
| `--update-changed` | move a changed destination file to the versions folder before copying the new one |
| `--no-auto-update` | do not update changed edit settings/sidecars (`AUTO_UPDATE_EXTENSIONS`) unless `--update-changed` is given |
| `--space-margin-gb N` | free space kept on the destination, in GB (default `20.0`) |
| `--dry-run` | plan, print the summary and write the logs/CSVs; copy nothing |
| `--verify {quick,full}` | verify an existing backup; no copy |
| `--yes` | do not ask for confirmation |
| `--version` | print the version and exit |
| `--help` | print the usage and exit |

`--verify` cannot be combined with `--dry-run` or `--update-changed` (exit 2).

## Settings

Every user-editable setting lives at the top of `photo_backup.py` and is read
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
| `LOG_DIR` | `$PHOTOBACKUP_LOG_DIR` or `Logs\` next to the script | where the logs are written |

## Logs

Every run writes `<LOG_DIR>\<YYMMDD_HHMMSS>_backup` (or `_verify`) plus:

- `.log` — command line, effective settings, summary, every `COPY`,
  `SKIP CHANGED`, `VERSION` and `ERROR`, final summary;
- `_plan.csv` — `action,category,size,mtime,src,rel` for every planned file
  (written on every run, including `--dry-run`);
- `_unexported.csv` — `folder,files,bytes,c1_bytes,nx_bytes` (always written,
  even with `--unexported skip`);
- `_unmatched.csv` — `session,export_file`;
- `_verify.csv` — `status,rel,source_path,detail` (verify runs only).

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

**v0.2.0** (2026-10-07) — first release.

- Selective backup by export: exports in the formats you choose, plus the originals, sidecars, Capture One and NX Studio edits of the exported photos.
- Read-only source, nothing deleted on the destination, space checked before the first byte, `--dry-run` summary with every "not copied" reason.
- SQLite catalog with SHA-256; `--verify quick|full` on any later day.
- Edit settings and sidecars (`.xmp`, `.cos`, `.comask`, `.nksc`) are kept current automatically, previous versions in `versions\`.

**44 tests**, plus a dry-run over a real 9 TB archive and real copies verified with `--verify full`.

## Changelog

### v0.2.0 (2026-10-07)
- A folder that holds an export folder but no original beside it (a year folder holding a `Lightroom_Export`, or a drive root) no longer hides its subfolders: they keep their own classification and folders without an export below it are listed again.
- `--verify full` hashes the backup copy before looking at the source: a corrupted copy of a deleted original is reported as `DEST_CORRUPT`.
- The catalog records the size and date the source had when it was copied; a file saved by your editor during the backup no longer reports a false `DEST_SIZE`. Stale rows are refreshed on the next run.
- Unreadable folders and files are shown on the console (`Errors: N ... NOT backed up`) and fail the run, a dry-run too.
- A destination drive that is not connected is a clean abort (exit 2).
- Changed edit settings and sidecars are updated automatically (`AUTO_UPDATE_EXTENSIONS`, `--no-auto-update`).

### v0.1.0 (2026-10-06)
- First implementation (not released).

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
