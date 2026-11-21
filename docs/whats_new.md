# What's new, in short

The short notes of each recent release, newest first; the README keeps the
current one. Every release in detail: [version_history.md](version_history.md).

## v0.14.0-rc6 (test version, not released yet)

2026-11-22. jxl-photo 2.31.0-rc3, a backup that stops failing on the same files
every night, and a failed conversion that says why. Needs jxl-photo 2.31.0-rc3.
Everything of rc5 to rc1 below holds.

- **jxl-photo 2.31.0-rc3, and the `%`**: it is the new minimum (test versions of one number are now compared by their own number, so 2.31.0-rc1 and -rc2 are refused: point `[4] Settings` at a 2.31.0-rc3). An ExifTool step that cannot open its file is an error there: before, a TIFF's JXL could, rarely, lose its EXIF or its XMP and still be OK (jxl-photo's known issue #686). It refuses a temp folder with a `%` (ExifTool reads a `%` in a file name as a format code), so a work folder with a `%` is refused before anything is read (exit 2, [JP-22](jxlphoto_contract.md#jp-22--the-temp-folder-never-has-a-)), and a restore into a folder with a `%` gives jxl-photo a temp folder of its own in the system's temp folder.
- **A file that never converts is not tried on every run**: a JPEG cjxl cannot read (19 old ones in a real archive made every run end with exit 1) or a `._` file of macOS fails the first runs as before (`CONVERT FAILED`, copied as it is), then, after `CONVERT_GIVE_UP_AFTER` runs in a row (default 2), stays the copy as it is: `NOT CONVERTED AGAIN` in the log, a row `JPEG: not converted again` in the summary, exit 0. A changed file, another jxl-photo, other settings or `--retry-conversions` try again ([tiff_to_jxl.md](tiff_to_jxl.md#a-file-jxl-photo-cannot-convert)).
- **A failed conversion says cjxl's reason**: `CONVERT FAILED` now holds the lines under jxl-photo's ERROR line (`Error while decoding the JPEG image ...`, `Try using --allow_jpeg_reconstruction 0`), not only the end of cjxl's banner.
- **Both paces, each with its ETA**: `whole run 13 MB/s (ETA 5h05m), last 10 min 11 MB/s (ETA 4h50m)` in the copy, the conversion, the full verify and the restore; when they disagree, the pace is changing (`PROGRESS_RECENT_S` is 10 minutes).
- **A log in a file keeps the names**: output into a file or a pipe is UTF-8, so `～` and Japanese folder names are no longer `?` in a scheduled run's log.
- **No empty `pbconv` left** in the work folder after a run.
- **A restored TIFF has its master's EXIF exactly** (jxl-photo 2.31.0-rc3 copies the EXIF by value: an f/2.83 stays f/2.83).
- **The log says where the conversion time went**: one `TIMING conversions:` line at the end — the writer (waiting, putting each JXL in place, old copies aside) and the workers' sums (reading the sources, jxl-photo, the proof). The next long run on a hard disk says which disk holds it back.
- **From an independent review before release** (each with a test that fails without it):
  - a restore into a deep folder no longer waits 30 minutes per TIFF: Windows hangs a program whose temp folder has 260 characters when it starts another one, so jxl-photo's temp folder is kept under 240 (beside a deep target it goes to the system's temp folder; a work folder that deep is refused, exit 2);
  - two runs with a broken tool (cjxl gone from PATH, the work disk full) no longer give up every file: a failure counts only in a run that converted another file of its kind, and never one that says nothing of the file (unreadable, changed meanwhile, the converter not started);
  - a copy as it is keeps its name until the new copy as it is is on the disk (before, it left first, and a copy that then failed left nothing under that name);
  - a source edited after its copy as it is was made is a changed file: only `--update-changed` (or the mirror) replaces it, and the summary counts it there;
  - a page of a multipage TIFF never takes the name of another TIFF of the folder (`x_page1.tif.jxl`); that TIFF is copied as it is (exit 1) instead of the two pushing each other into `versions\` on every run;
  - the menu can ask the next backup to try the failed conversions again (`[4] Settings`);
  - a restore of many files kept as JXL no longer spends minutes of CPU looking at every waiting file for each new one.

## v0.14.0-rc5 (test version, not released yet)

2026-11-20. A summary you read at a glance, the conversions where you want
them, and the conversion off for a trip. Needs jxl-photo 2.31.0-rc1, as rc4. Everything of rc4, rc3, rc2 and rc1
below holds.

- **The summary in tables**: "This run", "In the backup, not on the source (orphans)" and "Not copied" are tables of files and sizes, one fact per line (the categories to copy each on a row with a plain name; the orphans by reason); counts with thousands separators and sizes in TB from 1,000 GB on, everywhere; up to three sources by name, else how many and the first and last; the CSVs and the log named once, at the end, with what each holds and their folder; `Space` stays the last line, right above the question.
- **Ctrl+C stops in seconds, and says so**: at once `Ctrl+C: stopping at a safe point...`; the conversions running are ended (with the cjxl and exiftool jxl-photo started) instead of waited for, a group's names and catalog rows go together, the catalog is written, and only then `Stopped (Ctrl+C) ... the disk can be removed`. A longer stop says `still stopping: ...` every few seconds; a second Ctrl+C stops waiting (the next run cleans up). The restore does the same. Every line now reaches a pipe or a file as it is printed (a scheduled run's log no longer lags minutes behind).
- **The conversions where you want them**: `--work-dir` says where this run converts (default `JXL_WORK_DIR`: the system's temp folder), checked as that setting is, and the menu remembers one per computer (`[4] Settings` → "Set where the conversions run") — a work folder on another disk spares the system's SSD. jxl-photo's own temp files now go into each conversion's folder too (before, into the system's temp folder whatever `JXL_WORK_DIR` said) and leave with it; the restore's, into its own folder next to the target.
- **The conversion off, for a trip with a slow computer**: `--no-convert` (or `[4] Settings` → "Turn the conversion off on this computer", which the menu's first screen then shows) keeps the manifest as it is and converts nothing: the JXL already in the backup stay, a new or changed TIFF or JPEG is copied as it is (not a failure; an outdated JXL to `versions\` after the copy), no jxl-photo needed. Back home, a run with the conversion on makes them JXL.

## v0.14.0-rc4 (test version, not released yet)

2026-11-20. Faster, every TIFF page checked, and the conversions out of the
backup. Needs jxl-photo 2.31.0-rc1. Everything of rc3, rc2 and rc1 below holds.

- **Every page of a TIFF is checked**: jxl-photo 2.31.0-rc1 decodes each page it converts and compares it with the TIFF's before the page counts — the same pixels for a lossless page, the same image for a lossy one ([JP-20](jxlphoto_contract.md#jp-20--every-page-is-checked-before-it-takes-its-name)). Up to jxl-photo 2.30.x photo-backup asked for that check and nothing was checked. It is now the minimum (`JXLPHOTO_MIN_VERSION`), and a `Tiff` profile needs libjxl's `djxl` on PATH (refused before reading without it, exit 2).
- **Twice as fast, and the restore nine times**: 146 JPEG and 10 TIFF were kept as JXL in 32 s (rc3: 71 s) and came back in 13 s (rc3: 116 s). JPEGs and TIFFs convert in two pools at once; a JPEG is proven and restored by libjxl's djxl straight from memory (`DJXL`, [JP-18](jxlphoto_contract.md#jp-18--djxl-gives-the-jpeg-back-from-its-jxl)); jxl-photo starts from its kept bytecode in 0.08 s instead of 0.3–0.5 s ([JP-19](jxlphoto_contract.md#jp-19--each-command-is-a-script-beside-jxl_photopy)); a JPEG is transcoded at effort 5 (`JPEG_JXL_EFFORT`: as small as 7, 30% faster).
- **The conversions run outside the backup**: in the system's temp folder (`JXL_WORK_DIR`; never in a source or the backup), and one writer copies each finished JXL into the backup, read back and checked before it takes its name — a slow or SMR disk gets one file at a time instead of dozens. The temp folder holds a few GB at most, however slow the disk.
- **One conversion per physical core** (`JXL_WORKERS = 0`): as fast as one per logical CPU on a 16-core CPU, with a third of the CPU left free.
- **A failed conversion says why**: `CONVERT FAILED` shows jxl-photo's own error line (a page that failed its check: `(JP-20)`) and keeps jxl-photo's log of that conversion in `Logs\jxl-photo\`; a conversion that worked keeps none (one per photo was 140,000 files in a backup of a whole archive).
- **jxl-photo's own defaults**: a backup and a restore turn jxl-photo's settings overlay off (the one made for its presets: another temp disk, its tags), [JP-21](jxlphoto_contract.md#jp-21--an-empty-jxlphoto_settings-turns-the-settings-overlay-off).
- **An older backup still comes back**: the restore runs any jxl-photo from 2.30.0 on (`JXLPHOTO_RESTORE_MIN_VERSION`), so the copy an older backup keeps still restores it.
- **The menu remembers jxl-photo**: `[4] Settings` → "Set where jxl-photo is" (checked first, kept in the menu's own file) and every backup, mirror and restore it runs gets it — no path of yours inside the scripts.
- **Easier to follow**: every yes/no question says what Enter does (`[y/N, Enter = no]`); a dry-run says which manifest line it reads and, every few seconds, how many folders and files it has read (on a console); the copy's and the full verify's progress line shows the speed of the last 2 minutes beside the average, its ETA follows the recent one (`PROGRESS_RECENT_S`), and it says when a batch of small files is being written instead of repeating itself.
- **Scheduled runs**: the Task Scheduler line is now `cmd /c` ([menu.md](menu.md#unattended-runs-task-scheduler)): with `cmd /k` the window stayed open, the task stayed "Running" and the next night's backup did not run. In the Windows program the hints say `photo-backup.exe ...`.
- The contract with jxl-photo has 21 items; settings: `JXL_WORK_DIR`, `JXL_TIFF_WORKERS`, `DJXL` and `JXLPHOTO_RESTORE_MIN_VERSION` are new, `JXL_WORK_FOLDER` is now photo-backup's folder inside `JXL_WORK_DIR` (`pbconv`).

## v0.14.0-rc3 (test version, not released yet)

2026-11-19. JPEG kept as JXL, the restore's choice of formats, and the contract
with jxl-photo. Everything of rc2 and rc1 below holds.

- **JPEG kept as JXL, byte for byte**: a manifest line's new `Jpeg` column (or `--jpeg jxl`) keeps every JPEG of the line as JXL — exports too — by jxl-photo's lossless transcode. The JXL counts only after jxl-photo rebuilt the JPEG from it and the rebuilt file had the original's SHA-256; anything else (a CMYK JPEG) is copied as it is. Measured: 146 real JPEGs took 81% of their size ([tiff_to_jxl.md](tiff_to_jxl.md)).
- **The restore chooses the formats**: `--convert-back all` (default: TIFF and JPEG back; a JPEG byte for byte), `lossless` (every JPEG and the lossless TIFFs back; a lossy TIFF stays JXL) or `none` (every one as JXL; `--keep-jxl` still works). The menu asks when the backup holds such files. New outcome `RESTORED_AS_JXL`.
- **The contract with jxl-photo (仕様書)**: [jxlphoto_contract.md](jxlphoto_contract.md) says, item by item (JP-1 to JP-17), what jxl-photo must keep doing for photo-backup, where each item lives in both programs and what breaks; `tests/test_jxlphoto_contract.py` checks a real jxl-photo against it. photo-backup refuses a jxl-photo older than 2.30.0 (`JXLPHOTO_MIN_VERSION`), measured by running that test on jxl-photo's own versions; every failure message names its item (`CONVERT FAILED ...: ... (JP-5)`).
- The catalog's table of files kept as JXL knows their kind and whether they are lossless (schema 3; a catalog of rc2 is upgraded when written). `TIFF_JXL_WORKERS`, `TIFF_JXL_TIMEOUT_MIN` and `TIFF_JXL_FOLDER` are now `JXL_WORKERS`, `JXL_TIMEOUT_MIN` and `JXL_WORK_FOLDER` (they serve JPEG too).
- The README says how photo-backup and jxl-photo make a backup smaller, in four steps.

## v0.14.0-rc2 (test version, not released yet)

2026-11-19. TIFF kept as JXL in the backup, and the fixes of an independent
review of rc1. Everything of rc1 below holds.

- **TIFF on the computer, JPEG XL in the backup**: a manifest line's new `Tiff` column (or `--tiff`) names a profile of `TIFF_JXL_PROFILES`, and jxl-photo converts that line's TIFFs — a developed TIFF beside its RAW, a film scan; never an export. Each JXL is kept only with proof that it came from those very bytes; anything else copies the TIFF as it is. Measured: a 218 MB NX Studio TIFF took 34 MB at distance 0.05; film scans, lossless, 59 to 71% and the same pixels back ([tiff_to_jxl.md](tiff_to_jxl.md)).
- **The restore gives the TIFF back** under its own name, with its pages and its date (`RESTORED_FROM_JXL`); `--keep-jxl` gives the JXL as it is. A verify checks each JXL against the TIFF it came from (`DEST_NOT_FROM_SOURCE`).
- **A TIFF copy already in the backup becomes JXL**: the JXL first, then the TIFF copy leaves (to `versions\`; in the mirror, to its trash) — also one FreeFileSync made, which lets the mirror take over a FreeFileSync backup.
- **The programs that restore a backup are kept in it**: `_photo_backup\tools\` holds photo-backup and jxl-photo of each version that wrote there, plus what `KEEP_TOOLS_EXTRA` names, and `HOW TO RESTORE.txt`.
- **Fixes from the rc1 review**: a junction inside the backup that leads into a source could let the mirror move or delete source files — now every run refuses a link inside the backup (exit 2); the mirror's limit counts replaced files too (a program that rewrites every file in place); a junction whose target is not connected is "cannot be read", never "gone"; a junction is never removed as an empty folder; the menu runs the mirror once and the mirror asks with the real numbers (rc1 confirmed a dry-run, then ran with `--yes`); Ctrl+C while removing keeps the box and the list; a run with nothing to copy needs no space; the trash goes on the day it shows; the smallest "make room" option comes first; an old copy that cannot be read is never replaced; a superscript digit no longer stops the space question with a traceback.
- A photo kept only as JXL is a photo (`.jxl` in `ANCHOR_EXTENSIONS`): `Originals=raw` keeps it when it has no RAW.

## v0.14.0-rc1 (test version, not released yet)

2026-11-19. The mirror, for testing before v0.14.0.

- **`mirror_backup.py`, the mirror**: the backup follows a manifest — what changed is replaced, what is gone leaves the backup, first into a trash for `MIRROR_KEEP_DAYS` (30) days. It removes only what it wrote itself, with proof, and a person types the number of files for anything unusual ([mirror.md](mirror.md)).
- **A screen made to be read in a hurry**: the verdict first ("N files will be removed: to the trash until ..."), the mirror's box last, right above the question, and where to copy a file back from. `[8] Mirror` in the menu, with "What is in the trash".
- **The version on the screen and in the backup**: each program shows its name and version (the summary's first line, the mirror's boxes, the console's title), and the catalog records which version created it and which one last changed it.
- The manifest has an optional `Mirror` column (`disk` / `selection`); `manifest_backup.py` reads it and ignores it.
- The README shows a photo developed in another program first — a TIFF from NX Studio, a denoised DNG from DxO — and how the final photo, the intermediate and the RAW all come along (nothing changed in the selection: checked on a real session, and now a test).

## v0.13.2

2026-11-12. Fixes from a third bug sweep: most of them close edge cases where
an unusual input could get around a written safety rule. Nothing to do when
upgrading.

- A Capture One cache folder that is a junction to another disk now has its own words on the cache line of the summary, so "links/junctions not followed" names only what is really not in the backup.
- `--verify quick` takes the planned originals from the plan's own listing instead of asking the disk once more per file (`VERIFY_QUICK_USES_PLAN`).
- A destination reached through a junction or a `subst` letter can no longer land inside a source, nor a restore target inside the backup.
- `restore_backup.py --to ""` and `--only ""` are refused (they restored into the current folder / selected everything); a `--space-margin-gb` of `nan`, `inf` or below 0 is refused; a file named `NUL` is an error of the plan instead of a traceback.
- Nothing in the versions or orphans folder is ever replaced (a taken name gets ` (2)`), the backup ID file is never rewritten, and a catalog row that leads outside the backup stops every run as a damaged catalog does.
- `captureone_backup.py --revert-last` trusts a journal line only where Capture One keeps that part, and refuses an empty `old_` folder instead of undoing the force before it.
- `mobile_backup.py` 0.1.4: one run at a time; a run that would empty the mirror changes nothing without `--allow-mass-delete`; junctions are not followed.

833 tests, plus real checks on the archive (a dry-run of all of `G:` and a
scratch copy on `E:`, hostile paths). [Technical notes](version_history.md#v0132).

## v0.13.1

2026-11-11. Fixes and speed from a second bug sweep, most of them for a long `--verify full`. A file that cannot be read — the backup copy or the original — no longer ends the verify with a traceback: it is `DEST_UNREADABLE` or `SOURCE_UNREADABLE` and the run goes on. What a verify has checked is written to the catalog every 30 s and survives a Ctrl+C, which still prints the summary and writes `_verify.csv`. The backup copy and its original are read at the same time when they are on two drives, and a small file is read with a block of its own size (5 000 small files: copy 26 s → 10.5 s, verify full 20 s → 2.1 s). A damaged catalog stops every run with a message instead of an `OK`; a file dated before 1970 or after the year 3000 no longer stops the backup; a backup copied with its own root as `--base` is refused; the menu's dry-run carries "replace what changed", and `[5] Restore` no longer restores a whole year for its loose files. `_plan.csv` of a run that copies and `_verify.csv` list what changed or is wrong, not every file (settings bring the full lists back). `captureone_backup.py`: junctions are reported, the double-click restore is written from everything the backup holds, a read-only file no longer breaks the undo. `mobile_backup.py` 0.1.3: a photo exported again during a run no longer leaves its album skipped. [Technical notes](version_history.md#v0131).

## v0.13.0

2026-11-11. A session leaves out only the subfolders its mode names. The modes table has a new column, `LeaveOut` (`_OUTTAKES*` in the shipped modes); any other subfolder of a session — scans, a "Selected" folder, the photos of another event put beside the export — is judged alone: a session if it holds an export, a folder without an export if not, copied whole in a `+` mode and listed in `_unexported.csv` in the others. **Copies more on purpose** in the `+` modes; no mode copies less. A modes table of your own without the column keeps the v0.12.1 behaviour, and so does `selective_backup.py` on its own. [Technical notes](version_history.md#v0130).

## v0.12.1 and v0.12.0

**v0.12.1** fixed a first bug sweep (nothing left out without a count, a bigger mode never copies less, a source that cannot be read is not a source that is gone), and **v0.12.0** made sure a file never counts as saved when the right content cannot be restored. [Technical notes](version_history.md#v0121).

## At a glance

| Version | Date | What it added |
|---------|------|---------------|
| **v0.13.2** | 2026-11-12 | Third bug sweep: junctions and `subst` letters can no longer lead a backup into a source or a restore into the backup; a `--revert-last` that trusts only Capture One's places; nothing in versions or orphans replaced; a quicker `--verify quick`; `mobile_backup.py` 0.1.4 (one run at a time, no mass delete) |
| v0.13.1 | 2026-11-11 | Second bug sweep: a verify that survives unreadable files and Ctrl+C and reads both disks at once; small files read faster; a damaged catalog and odd file dates handled; shorter CSVs |
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
