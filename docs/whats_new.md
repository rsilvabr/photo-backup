# What's new, in short

The short notes of each recent release, newest first; the README keeps the
current one. Every release in detail: [version_history.md](version_history.md).

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
