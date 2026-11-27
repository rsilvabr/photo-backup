# Restoring (`restore_backup.py`)

`restore_backup.py` copies files **out of** a backup. The backup is
read-only; a target file that already exists is never overwritten. From the
menu: `[5] Restore` ([menu.md](menu.md)). Capture One's own files are restored
by `captureone_backup.py` ([captureone.md](captureone.md)).

```
restore_backup.py --backup PATH (--to PATH | --to-original) [options]
```

| Option | Description |
|---|---|
| `--backup PATH` | backup root (the folder that holds `_photo_backup`); required |
| `--to PATH` | restore each file under `PATH\<rel>`. An empty `--to ""` (a variable left empty in a `.cmd`) is refused, exit 2 *(v0.13.2; it restored into the current folder)* |
| `--to-original` | restore each file to the `source_path` recorded in the catalog |
| `--only REL` | restore only this folder or file (repeatable, case-insensitive); default: everything. An `--only` that matches nothing in the catalog is named (`--only REL: matches nothing in the catalog`), the others are restored, and the run exits 1 *(v0.12.1)*. An empty `--only ""` is refused, exit 2 *(v0.13.2; it selected everything)* |
| `--expect-backup-id ID` | abort unless the backup holds this backup ID |
| `--space-margin-gb N` | free space kept on each target drive, in GB (default `5.0`) |
| `--convert-back all\|lossless\|none` | the TIFFs and JPEGs the backup keeps as JXL: `all` back to their own format (default; a JPEG byte for byte), `lossless` only the ones that come back exactly (every JPEG, a lossless TIFF; a lossy TIFF stays JXL), `none` every one as JXL *(v0.14.0-rc3; [the table](tiff_to_jxl.md#restoring))* |
| `--keep-jxl` | the same as `--convert-back none` *(v0.14.0-rc2)* |
| `--jxlphoto PATH` | jxl-photo's `jxl_photo.py` or `jxl-photo.exe`, to turn a file kept as JXL back into its TIFF or JPEG (default `JXLPHOTO`; empty or not found: the newest one kept in the backup's `_photo_backup\tools`) *(v0.14.0-rc2)*; any jxl-photo from 2.30.0 on (`JXLPHOTO_RESTORE_MIN_VERSION`, *rc4*) |
| `--dry-run` | plan and report, restore nothing |
| `--yes` | do not ask for confirmation |
| `--version` | print the version and exit |
| `--help` | print the usage and exit |

A target folder with a `%` in its path, or one so deep that jxl-photo's temp
folder beside it would be 240 characters or more: jxl-photo's temp files go to
a folder of their own in the system's temp folder, removed after
(*v0.14.0-rc6*). ExifTool reads a `%` in a file name as a format code (JP-22),
and Windows hangs a program whose temp folder has 260 characters when it
starts another one: a TIFF there waited `JXL_TIMEOUT_MIN` and failed.

*(v0.14.0-rc7)* With jxl-photo 2.31.0-rc3 or newer (and `JXLPHOTO_SERVE`) the
TIFFs (and the JPEGs when there is no djxl) are made by one jxl-photo process
per worker that takes file after file ([how](tiff_to_jxl.md#how-the-conversions-run-serve-mode)):
their temp folder is a `pbrestore_serve_...` folder in the system's temp folder,
removed when the restore ends (so a target with a `%` needs nothing else), and
their logs are in `Logs\jxl-photo`, one per process. A system temp folder with a
`%`, too deep, or inside the backup: one jxl-photo per file, as before. An older
jxl-photo (the restore accepts 2.30.0) runs one per file too. A JPEG rebuilt by
djxl makes no work folder at all: it is written to one file next to the target,
which takes the target's name or is removed.

Exactly one of `--to` and `--to-original` is required. Outcomes per file, in
the console, in the log and in `<log>_restore.csv`:

- `RESTORED` — copied from the backup and verified against the catalog
  SHA-256 (`RESTORED_UNVERIFIED` when the catalog had no hash yet); for a JPEG
  the backup keeps as JXL *(v0.14.0-rc3)*: rebuilt by libjxl's djxl straight
  from the backup's JXL *(rc4; without djxl, by jxl-photo)* and checked against
  the SHA-256 of the original JPEG — the same bytes;
- `RESTORED_FROM_JXL` *(v0.14.0-rc2)* — a TIFF the backup keeps as JXL, made
  again by jxl-photo under its own name, with its pages and its date (each JXL
  checked against its catalog SHA-256 first). Lossless gives back the same
  pixels, not the same bytes ([tiff_to_jxl.md](tiff_to_jxl.md#restoring));
- `RESTORED_ORIGINAL` *(v0.14.0-rc9)* — a TIFF the backup keeps as JXL whose
  own copy is still kept in `_photo_backup\originals\` (the TIFF copy its JXL
  replaced): that copy is restored as it is, its bytes checked against the
  SHA-256 of the TIFF the JXL was made from, and no JXL is decoded (no
  jxl-photo needed). Only with proof: the catalog's `originals` row (read
  only), the file there with its size, and its SHA-256 the JXL's source (the
  row's; a row without one: the file is read first). Several: the newest. No
  proof, or a copy that turns out other bytes (`ORIGINAL NOT USED` in the log):
  the JXL is decoded as before. With `--convert-back lossless` a lossy JXL whose
  original is kept comes back as that original (it is exact), and when that copy
  turns out other bytes it is never decoded: its JXL comes back as it is
  (`RESTORED_AS_JXL`); `none` gives the JXL as it is;
- `RESTORED_AS_JXL` *(v0.14.0-rc3)* — a file kept as JXL restored as the JXL
  it is (`photo.tif.jxl`), as `--convert-back` chose;
- `NEEDS_JXLPHOTO` *(v0.14.0-rc2)* — such a TIFF or JPEG, and no jxl-photo:
  neither `--jxlphoto` / `JXLPHOTO` nor one kept in the backup (a JPEG needs
  only djxl, *rc4*). The others are
  restored, the run exits 1; `--convert-back none` gives the JXL files as they
  are;
- `ALREADY_THERE` — the target exists with the same content (size and hash;
  for a file kept as JXL: the original TIFF's or JPEG's);
- `CONFLICT` — the target exists with different content; it is left alone and
  the run exits 1;
- `CORRUPT_IN_BACKUP` — the backup copy does not match the catalog hash;
  nothing is written to the target and the run exits 1;
- `MISSING_IN_BACKUP`, `REFUSED`, `ERROR` — the backup file is gone, the
  target would land inside the backup (the `source_path` of `--to-original`,
  or a folder that is a junction or a link into the backup, *v0.13.2*), or
  another error; all exit 1;
- an `--only` that matches nothing also makes the run exit 1.

A restored file gets the date its copy has in the backup. When that date is
the catalog's within `MTIME_TOLERANCE_S` (a backup disk in FAT/exFAT keeps
dates in 2 s steps), it gets the exact date the catalog recorded *(v0.13.1)*.
While it restores, a progress line shows files, GB, speed and time left
*(v0.13.1)*.

Exit codes: 0 (everything restored or already there), 1 (any of the above),
2 (aborted before restoring: no catalog or a damaged one, wrong backup ID, `--to` inside the
backup or the backup inside `--to` — also through a junction or a `subst`
letter, *v0.13.2* —, an empty `--to` or `--only`, a `--space-margin-gb` that
is not a number or is negative, not enough space, no confirmation, a Python older
than 3.12),
130 (Ctrl+C: it says so at once; what came back stays, the files being made
are ended and leave nothing at their targets, the CSV lists what came back,
and the last line says nothing is being written any more, *v0.14.0-rc5*).

`restore_backup.py` writes its own `<YYMMDD_HHMMSS>_restore.log` and
`<YYMMDD_HHMMSS>_restore.csv` (`status,rel,target,detail`).

## Settings

`restore_backup.py` has its own setting at the top:

| Constant | Default | Meaning |
|---|---|---|
| `SPACE_MARGIN_GB` | `5.0` | free space kept on each restore target drive (default `--space-margin-gb`) |
