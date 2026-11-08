# Version history

Detailed notes for every release, newest first — the current one included.
The [main README](../README.md) describes how the tool works today; the
releases are also published as
[GitHub Releases](https://github.com/rsilvabr/photo-backup/releases).

## Release history

| Version | Date | Highlights |
|---------|------|------------|
| **[v0.5.0](#v050)** | 2026-11-08 | Leaves out what jxl-photo can make again: an sRGB/resized JXL copy is not backed up when its master is. `DSC_0001 (2)` is treated as its own photo |
| [v0.4.0](#v040) | 2026-11-08 | A renamed folder is moved inside the backup instead of copied again; orphans listed; each backup knows which disk it is; verify a big backup in slices; restore files from a backup |
| [v0.3.0](#v030) | 2026-10-07 | An interactive menu: questions, a dry-run, then the copy after a yes; presets that run unattended |
| [v0.2.0](#v020) | 2026-10-07 | First release: back up the exported photos with their originals and edits, a SHA-256 catalog, verify, a space check before copying |
| v0.1.0 | 2026-10-06 | First implementation, not released |

`mobile_backup.py` has its own version: [see the end of this page](#mobile_backuppy).

---

## v0.5.0

**Released 2026-11-08.** Nothing to do when upgrading.

- **Copies that jxl-photo can make again are left out.** jxl-photo turns a
  TIFF export into a JXL master and, from that master, makes copies: an sRGB
  JXL, a resized JXL for the phone. Until v0.4.0 the default
  `--export-formats jxl>tif>jpg` copied *every* `.jxl` of a photo, so each
  photo with a master also sent its sRGB copy to the backup — on a 1.6 TB
  test plan, 5 944 files and 31.6 GB that the master can remake in minutes.
  Now such a copy is skipped, the same way the Capture One `Cache` is.

  It is skipped **only on proof**, read from the files themselves:
  1. the copy's own XMP says `jxlphoto-derived:` (jxl-photo writes it into
     every copy it makes, with the recipe), and does not say `jxlphoto-src:`;
  2. a JXL of the same photo that says `jxlphoto-src:` (a master) is copied
     in the same run.

  Everything else is copied, as before: a JXL without metadata, with a
  compressed metadata box the tool cannot read, a lossless JPEG → JXL archive
  (it carries no token), a copy whose master is excluded or missing, a file
  that cannot be read. Only photos with two or more selected `.jxl` are read
  at all, and only the few KB before the image data. The summary has a new
  line: `jxl-photo derivatives (their master is copied; --derivatives copies
  them): N / X GB`.

  `--derivatives` (or `COPY_JXLPHOTO_DERIVATIVES = True` at the top of
  `selective_backup.py`) copies them anyway. A backup made by an earlier
  version keeps its copies; from now on they are listed as "no longer
  selected" orphans — reported, never deleted.
- **`DSC_0001 (2)` is its own photo.** Windows adds ` (2)` when two files
  with the same name meet (two folders merged, a film frame scanned twice).
  The tool matched an export to the *shortest* original name in front of it,
  so `DSC_0001 (2)_sRGB.jpg` was taken for a version of `DSC_0001`; when
  `DSC_0001` had a JXL master, `jxl>tif>jpg` then dropped this JPEG of a
  different photo. Now, when an original called `DSC_0001 (2)` is beside the
  export, the export belongs to it and its formats are chosen for it alone.

  Which originals are copied does not shrink: the originals of
  `DSC_0001 (2)` still bring `DSC_0001` along. In film scans `0005 (2).tif`
  is usually a rescan of the same frame as `0005.tif`, and both were copied
  before; for two camera photos it costs one original too many, never one
  too few.
- Checked against v0.4.0 on the whole 1.6 TB test plan, file by file: only
  the 5 944 sRGB copies leave it, 4 rescan originals join it, no original is
  dropped. A real backup of real masters and copies (in a folder with
  Japanese, `[ ]`, a space and `%` in its name): the masters copied, the
  copies left out, the verify clean, `--derivatives` bringing them back.
- Tests: `tests/test_v05.py` (12, hostile folders, JXL files with real
  container boxes); 106 in all.

## v0.4.0

**Released 2026-11-08** (written 2026-10-07, reviewed and published a month
later). Nothing to do when upgrading.

- **A renamed folder is moved, not copied again.** A session is often
  renamed when it is finished (`250129_Flores` → `250129_Flores [FINAL]`).
  Until v0.3.0 the next backup saw a folder it had never seen, copied all of
  it again and left the old copy behind for good: twice the space, and a
  verify that kept reporting the old copy's originals as missing. Now a new
  file with the same name and size as a backup file whose original is gone is
  a *possible move*; it is checked with SHA-256 (original = backup copy =
  catalog) and then the backup copy is **moved** to the new path. The folders
  the move leaves empty are removed — folders only, never a file. Measured:
  141 files of a real session moved in about one second, nothing copied. If
  the hash does not match, the file is copied as before. `--no-detect-moves`
  turns it off.
- **Orphans.** A backup file whose original is gone (deleted or moved away
  on the source) stays in the backup — the tool never deletes. The summary now
  counts these orphans, and `<log>_orphans.csv` lists them, split in "source
  gone" and "no longer selected" (the original exists but the current options
  leave it out, e.g. another `--export-formats`). `--move-orphans` moves the
  "source gone" ones into `_photo_backup\orphans\<date>`, out of the mirrored
  tree; deleting that folder is up to you.
- **Each backup knows which disk it is.** The first copy writes
  `_photo_backup\volume.json` with a random backup ID. The summary shows it
  (`backup ID 1a2b3c4d`, or `NEW BACKUP` on an empty disk), `--expect-backup-id`
  refuses a destination with another ID, and a preset saved in the menu finds
  its backup when the drive letter changed (`H:` → `I:`) and refuses the wrong
  disk instead of starting a new backup on it.
- **Verify in slices.** A full verify of a large backup takes hours.
  `--verify full --max-gb N` checks at most N GB per run, starting with the
  files verified longest ago (never-verified first); the next run continues
  where it stopped. Run weekly, the whole backup is re-read over time.
- **Restore.** New script `restore_backup.py`, and `[5] Restore from a
  backup` in the menu: copies files back out of a backup, under a folder of
  your choice or to the place each file came from (`--to-original`), all of
  it or only some folders (`--only`). The backup is opened read-only; a target
  file that already exists is never overwritten — identical ones are reported
  `ALREADY_THERE`, different ones `CONFLICT` — and every restored file is
  checked against the catalog's SHA-256 (a damaged backup copy is reported
  `CORRUPT_IN_BACKUP` and nothing is written).
- **The summary per folder.** The dry-run shows what will be copied per top
  folder (2024, 2025, …) with a running total, and `<log>_by_folder.csv` has
  the full table — handy when only part of the archive fits on a disk.
- Found in the review and fixed before release: a file that appeared in the
  restore's target while it was being restored could be overwritten; the
  final step now refuses an existing file (reported `CONFLICT`).
- Tests: 94; real checks of moves, orphans, backup IDs, sliced verify and
  restore in folders with Japanese, `[ ]`, a space and `%`, and a dry-run of
  the whole archive.

## v0.3.0

**Released 2026-10-07.**

- **An interactive menu.** `python photo_backup.py` asks a few questions
  (sources, destination, formats, what to do with outtakes) — Enter keeps
  the last answer — then runs a **dry-run first** and copies only after a
  yes. The connected drives are listed with their free space. The choices are
  remembered in `~/.photo_backup_config.json`.
- **Presets.** A backup or verify can be saved under a name and run
  unattended (`python photo_backup.py --run-preset "NIGHTLY"`, e.g. from the
  Task Scheduler); `--list-presets` lists them.
- **New script names.** `photo_backup.py` is now the menu, the project's
  entry point; the backup engine of v0.2.0 is `selective_backup.py`, with
  the same options. The menu reads its defaults from the engine, so a
  setting changed at the top of `selective_backup.py` reaches the menu too.

## v0.2.0

**Released 2026-10-07.** The first release.

- **Backs up the photos you kept.** For every folder that holds an export
  folder (any name containing "export"), it copies the exports, the originals
  of the exported photos beside them (RAW, camera JPEG, edited TIFF,
  sidecars), the Capture One settings (not the regenerable `Cache`) and the
  NX Studio `NKSC_PARAM`. Outtakes and folders without an export are listed,
  not copied. The folder tree is mirrored on the destination.
- **The best format per photo.** `--export-formats jxl>tif>jpg` copies the
  JXL master when there is one, else the TIFF, else the JPEG; `a+b` and `all`
  copy more.
- **Never loses anything.** The source is read-only; a copy goes to a temp
  file, is verified and renamed into place; nothing on the destination is
  deleted or overwritten — a changed file's old copy is moved to `versions\`
  first. Edit settings and sidecars that change on every edit (`.xmp`,
  `.cos`, `.nksc`, …) are updated on each run that way.
- **A catalog and a verify.** `_photo_backup\catalog.sqlite` on the
  destination records each file's SHA-256 at copy time; `--verify quick`
  checks sizes and dates, `--verify full` re-reads both sides and reports a
  damaged backup copy, an original that changed without changing its date,
  or an original that is gone.
- **Checks the space first.** The summary shows what will and will not be
  copied and the space needed; a run that does not fit stops before copying
  anything. Unreadable folders and a disconnected destination stop the run
  cleanly.

---

## mobile_backup.py

A separate script with its own version: it mirrors the phone-size
`JXL_mobile` folders to another disk as one album per event
([README_mobile_backup.md](../README_mobile_backup.md)).

| Version | Date | Highlights |
|---------|------|------------|
| 0.1.1 | 2026-10-13 | Photos numbered camera by camera, each camera in its own shooting order (cameras' clocks disagree) |
| 0.1.0 | 2026-10-13 | First version: one album per event, photos numbered in shooting order, synchronised (only its own files are renamed or removed) |
