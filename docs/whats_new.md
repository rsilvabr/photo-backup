# What's new, in short

The short notes of each recent release, newest first; the README keeps the
current one. Every release in detail: [version_history.md](version_history.md).

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
