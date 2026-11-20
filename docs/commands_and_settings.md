# Commands, options, settings, logs and exit codes

The reference of `selective_backup.py`, the script every backup goes through.
The other scripts have their options and settings in their own pages: the menu
([menu.md](menu.md)), the per-folder modes ([per_folder_modes.md](per_folder_modes.md)),
the restore ([restoring.md](restoring.md)), Capture One
([captureone.md](captureone.md)) and the phone mirror
([README_mobile_backup.md](../README_mobile_backup.md)).

## Everyday commands

```console
:: 1. See what a backup would do (writes only logs, no destination)
python selective_backup.py --source "G:\2025" --dest "H:\BACKUP" --dry-run

:: 2. Back up for real (shows the summary and asks for confirmation)
python selective_backup.py --source "G:\2025" --dest "H:\BACKUP"

:: 3. Verify the backup another day
python selective_backup.py --source "G:\2025" --dest "H:\BACKUP" --verify full
```

> **Tip:** always start with `--dry-run`. The summary lists what would be copied, what would not (and why), and whether it fits. The full lists are in the CSVs next to the log.

## Every option

```text
selective_backup.py --source PATH [--source PATH ...] --dest PATH [options]
```

| Option | Description |
|---|---|
| `--source PATH` | folder to back up; repeatable, required |
| `--dest PATH` | destination root; required |
| `--base PATH` | base for the relative layout; default: the drive root of each source, so `G:\` and `G:\2025` give the same layout |
| `--export-formats SPEC` | `all`, `a>b>c` fallback or `a+b` multiple (default `jxl>tif>jpg`; see [Export formats](how_it_selects.md#export-formats)) |
| `--exclude-export-subfolders NAMES` | `;`-separated folder names skipped inside export folders (any level), e.g. `JXL_mobile;WEB` |
| `--export-name-contains TEXT` | a folder whose name contains this (any case) is an export folder (default `export`) |
| `--exclude-export-names NAMES` | `;`-separated exact folder names that are NOT export folders |
| `--unselected {skip,raw,raw_or_jpeg,raw_jpeg,all}` | originals of photos that were not exported (default `skip`; see [the values](how_it_selects.md#originals-that-were-not-exported---unselected)) |
| `--unexported {skip,copy}` | folders without an export (default `skip`; see [Folders without an export](how_it_selects.md#folders-without-an-export)) |
| `--c1-cache` | also copy the Capture One `Cache` folders |
| `--no-c1-cache` | do not copy them, whatever `COPY_CAPTUREONE_CACHE` says *(v0.12.1; the menu always passes one of the two)* |
| `--derivatives` | also copy the JXLs jxl-photo derived from a master that is copied ([details](how_it_selects.md#jxl-photo-derivatives---derivatives-v050)) |
| `--no-derivatives` | skip those JXLs, whatever `COPY_JXLPHOTO_DERIVATIVES` says *(v0.12.1; the menu always passes one of the two)* |
| `--exclude-folders NAMES` | `;`-separated folder names skipped anywhere |
| `--verify-copy {none,size,hash}` | verification of each copy (default `hash`; [what it catches](verifying.md#the-check-right-after-each-copy)) |
| `--update-changed` | move a changed destination file to the versions folder before copying the new one ([re-runs](reruns_and_moves.md)) |
| `--no-auto-update` | do not update changed edit settings/sidecars (`AUTO_UPDATE_EXTENSIONS`) unless `--update-changed` is given |
| `--no-detect-moves` | copy a moved/renamed file again instead of moving its backup copy |
| `--move-orphans` | move backup files whose source is proven gone into `_photo_backup\orphans\<stamp>` (real copies only; nothing in a run with an error in its plan, or one the destination stopped; see [Orphans](reruns_and_moves.md#orphans)) |
| `--tiff {copy,PROFILE}` | the TIFFs outside export folders: copied as they are (`copy`, default) or kept as JXL with a profile of `TIFF_JXL_PROFILES`, made by jxl-photo *(v0.14.0-rc2; [TIFF kept as JXL](tiff_to_jxl.md))* |
| `--jpeg {copy,jxl}` | the JPEGs (exports too): copied as they are (`copy`, default) or kept as JXL by jxl-photo's lossless transcode, proven byte for byte *(v0.14.0-rc3)* |
| `--jxlphoto PATH` | jxl-photo's `jxl_photo.py` or `jxl-photo.exe`, for `--tiff` and `--jpeg` (default `JXLPHOTO`). A run that asks for either without jxl-photo is refused before anything is read, exit 2 |
| `--no-convert` | the conversion off for this run (a slow computer, a trip): a TIFF or JPEG that `--tiff`/`--jpeg` would convert is copied as it is, not a failure; the JXL already in the backup stay unless their file changed; no jxl-photo needed *(v0.14.0-rc5; [the conversion off](tiff_to_jxl.md#the-conversion-off-a-trip-with-a-slow-computer))* |
| `--work-dir PATH` | where the conversions of `--tiff` and `--jpeg` run, jxl-photo's temp files too (default `JXL_WORK_DIR`; empty = the system's temp folder); never inside or above a source or the backup, exit 2 *(v0.14.0-rc5)* |
| `--expect-backup-id ID` | abort (exit 2) unless the destination holds this backup ID; also applies to `--verify` and `--dry-run` |
| `--space-margin-gb N` | free space kept on the destination, in GB (default `20.0`) |
| `--dry-run` | plan, print the summary and write the logs/CSVs; copy nothing |
| `--verify {quick,full}` | verify an existing backup; no copy ([verifying.md](verifying.md)) |
| `--max-gb N` | with `--verify full`: verify at most N GB, oldest verification first (default `0.0` = everything; [Rolling verify](verifying.md#rolling-verify)) |
| `--yes` | do not ask for confirmation |
| `--version` | print the version and exit |
| `--help` | print the usage and exit |

`--verify` cannot be combined with `--dry-run`, `--update-changed` or
`--move-orphans` (exit 2); a negative `--max-gb` is a usage error too.

## Settings

Every user-editable setting lives at the top of `selective_backup.py` and is read
by the code and by the argparse defaults; editing the constant changes the
default everywhere — the menu reads them from there too.

| Constant | Default | Meaning |
|---|---|---|
| `EXPORT_NAME_CONTAINS` | `export` | a folder whose name contains this (any case) is an export folder |
| `EXPORT_NAME_EXCLUDE` | (empty) | `;`-separated exact folder names that are NOT export folders |
| `EXPORT_FORMATS` | `jxl>tif>jpg` | default `--export-formats` |
| `EXCLUDE_EXPORT_SUBFOLDERS` | (empty) | `;`-separated folders skipped inside export folders |
| `FORMAT_GROUPS` | see [Export formats](how_it_selects.md#export-formats) | group names usable in `--export-formats` |
| `RAW_EXTENSIONS` | `.nef`, `.cr2`, ... | extensions treated as RAW |
| `JPEG_EXTENSIONS` | `.jpg`, `.jpeg` | extensions treated as a camera JPEG (`raw_jpeg` keeps every one) |
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
| `PROGRESS_EVERY_S` | `5.0` | console progress interval: the copy's and the full verify's line, and while a plan reads the sources (a dry-run of a whole archive takes minutes), the folders and files read so far — that last one on a console only, never in a log *(v0.14.0-rc4)* |
| `PROGRESS_RECENT_S` | `120.0` | the progress line shows the speed since the start and over these last seconds (`85 MB/s, last 2 min 40 MB/s`), and its ETA follows the recent one; while a batch of small files goes to the disk it says so instead of repeating itself *(v0.14.0-rc4)* |
| `AUTO_UPDATE` | `True` | changed edit settings/sidecars are updated without `--update-changed` (`--no-auto-update` turns it off) |
| `AUTO_UPDATE_EXTENSIONS` | `.xmp`, `.dop`, `.pp3`, `.cos`, `.comask`, `.nksc` | extensions treated that way |
| `DETECT_MOVES` | `True` | detect moved/renamed catalogued files and move their backup copy (`--no-detect-moves`) |
| `ORPHANS_FOLDER` | `orphans` | under `CATALOG_FOLDER`: where `--move-orphans` puts files whose source is gone |
| `BACKUP_ID_FILE` | `volume.json` | under `CATALOG_FOLDER`: the backup identity ([backup ID](verifying.md#which-disk-is-this-backup-id)) |
| `VERIFY_MAX_GB` | `0.0` | default `--max-gb` for `--verify full` (0 = everything) |
| `VERIFY_PARALLEL` | `True` | `--verify full` reads the backup copy and the source at the same time when they are on two drives; `False` = one after the other *(v0.13.1)* |
| `VERIFY_COMMIT_S` | `30.0` | `--verify full` writes what it has verified to the catalog at least this often *(v0.13.1)* |
| `VERIFY_QUICK_USES_PLAN` | `True` | `--verify quick` takes the size and date of a planned original from the plan's own listing instead of asking for each file again; `False` = ask for each one (a file listed but whose own record is refused is then `SOURCE_UNREADABLE`) *(v0.13.2)*. [Why](verifying.md#how-a-verify-runs) |
| `PLAN_CSV_UP_TO_DATE` | `False` | `_plan.csv` of a run that copies: also one line per `UP_TO_DATE` file. A `--dry-run` always lists every planned file *(v0.13.1)*. [For and against](#short-or-full-lists-in-the-csvs-v0131) |
| `VERIFY_CSV_OK` | `False` | `_verify.csv` and the verify's `_plan.csv`: also one line per file that is `OK` *(v0.13.1)*. [For and against](#short-or-full-lists-in-the-csvs-v0131) |
| `SUMMARY_FOLDER_LINES` | `25` | lines of the per-folder table in the summary (the CSV has all) |
| `SUMMARY_INSIDE_NAMES` | `5` | subfolder names shown under "inside sessions" in the summary (`_inside.csv` has all) |
| `LOG_DIR` | `$PHOTOBACKUP_LOG_DIR` or `Logs\` next to the script | where the logs are written |

TIFF and JPEG kept as JXL in the backup, and the programs kept in it
*(v0.14.0-rc2, JPEG rc3; every detail in
[tiff_to_jxl.md](tiff_to_jxl.md#options-and-settings))*:

| Constant | Default | Meaning |
|---|---|---|
| `JXLPHOTO` | `""` | jxl-photo's `jxl_photo.py` or `jxl-photo.exe` (default `--jxlphoto`); `""` = no conversion |
| `JXLPHOTO_MIN_VERSION` | `2.31.0-rc1` | the oldest jxl-photo a backup runs: [the contract](jxlphoto_contract.md) between the two programs (2.31.0-rc1 checks every page it converts) |
| `JXLPHOTO_RESTORE_MIN_VERSION` | `2.30.0` | the oldest jxl-photo the restore runs (the one an older backup keeps still brings its files back) |
| `TIFF_JXL_PROFILES` | `{"jxl": {"distance": 0.05, "scan_distance": 0, "eight_bit_distance": 0}}` | the profiles a manifest's `Tiff` column (or `--tiff`) names; jxl-photo picks the distance per TIFF (0 = lossless) |
| `TIFF_EXTENSIONS` | `.tif`, `.tiff` | what a profile converts (never in an export folder) |
| `TIFF_JXL_EFFORT` | `7` | jxl-photo's `--effort` for a TIFF |
| `JPEG_JXL_EXTENSIONS` | `.jpg`, `.jpeg` | what `--jpeg jxl` keeps as JXL, exports too |
| `JPEG_JXL_EFFORT` | `5` | jxl-photo's `--effort` for a JPEG (lossless at any effort; 3, 5 and 7 give the same size; 16 at a time, 5 was 30% faster than 7) |
| `JXL_WORKERS` | `0` | files converted (and restored from JXL) at the same time; `0` = one per physical core |
| `JXL_TIFF_WORKERS` | `0` | of those, TIFFs at the same time; `0` = a quarter of the logical CPUs, at most one per 4 GB of free memory |
| `DJXL` | `""` | libjxl's `djxl` for the proof and the restore of a JPEG; `""` = PATH; `"none"` = through jxl-photo |
| `JXL_TIMEOUT_MIN` | `30` | a conversion longer than this is given up: the file is copied as it is |
| `JXL_WORK_DIR` | `""` | where the conversions run, jxl-photo's temp files too; `""` = the system's temp folder (`--work-dir` for one run; the menu remembers one per computer). One writer then copies each finished JXL into the backup |
| `JXL_WORK_FOLDER` | `pbconv` | photo-backup's folder inside `JXL_WORK_DIR`: one per running backup (its process number), removed at the end |
| `KEEP_TOOLS_IN_BACKUP` | `True` | every real run keeps photo-backup and jxl-photo in the backup, one folder per version |
| `KEEP_TOOLS_EXTRA` | `()` | more files or folders kept there (the `.zip` of photo-backup-exe, say) |
| `TOOLS_FOLDER` | `tools` | under `CATALOG_FOLDER`: where they are kept, with `HOW TO RESTORE.txt` |

The settings of the other scripts: the menu ([menu.md](menu.md#settings-of-the-menu)),
`manifest_backup.py` ([per_folder_modes.md](per_folder_modes.md#settings)),
`restore_backup.py` ([restoring.md](restoring.md#settings)),
`captureone_backup.py` ([captureone.md](captureone.md#settings)) and
`mobile_backup.py` ([README_mobile_backup.md](../README_mobile_backup.md)).

## Logs

Every run writes `<LOG_DIR>\<YYMMDD_HHMMSS>_backup` (or `_verify`) plus:

- `.log` — command line, effective settings, summary, every `COPY`,
  `SKIP CHANGED`, `VERSION` (with `(kept as <name> (2)...)` when that name
  was taken, *v0.13.2*) and `ERROR` (`ERROR name <path>` for a name Windows
  opens as a device, *v0.13.2*), every link or junction not followed
  (`LINK not followed <path>`; a Capture One cache folder that is a link:
  `CACHE LINK not followed <path>`, *v0.13.2*), every folder skipped by its
  name (`SKIPPED FOLDER <path>`, *v0.12.1*), the destination tests
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

`restore_backup.py` writes its own `<YYMMDD_HHMMSS>_restore.log` and
`<YYMMDD_HHMMSS>_restore.csv` (`status,rel,target,detail`).

CSVs are UTF-8 with BOM (`utf-8-sig`), which opens correctly in Excel with
Japanese file names.

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

## Exit codes

| Code | Meaning |
|---|---|
| 0 | everything fine (also a dry-run without read errors) |
| 1 | finished with file errors or unreadable folders (a dry-run too), the destination stopped taking writes, or verify found a problem |
| 2 | aborted before copying: usage error (a `--space-margin-gb` or `--max-gb` that is not a number, *v0.13.2*), not enough space, destination not available, a destination that is really inside a source (a junction or a `subst` letter, *v0.13.2*), no confirmation, bad or damaged catalog (a row leading outside the backup too, *v0.13.2*), a plan that would write into `_photo_backup` |
| 130 | interrupted with Ctrl+C (partial summary; the temp file is removed; a verify keeps what it had checked). Since *v0.14.0-rc5* it says so at once, ends the conversions running instead of waiting for them, writes the catalog and ends with `Stopped (Ctrl+C) ... the disk can be removed` ([safety.md](safety.md)) |

The restore has its own ([restoring.md](restoring.md)); `manifest_backup.py`
uses these.
