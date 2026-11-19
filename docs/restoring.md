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
| `--keep-jxl` | a TIFF the backup keeps as JXL: restore the JXL files as they are (`photo.tif.jxl`) instead of a TIFF made by jxl-photo *(v0.14.0-rc2)* |
| `--jxlphoto PATH` | jxl-photo's `jxl_photo.py` or `jxl-photo.exe`, to turn a TIFF kept as JXL back into the TIFF (default `JXLPHOTO`; empty or not found: the newest one kept in the backup's `_photo_backup\tools`) *(v0.14.0-rc2)* |
| `--dry-run` | plan and report, restore nothing |
| `--yes` | do not ask for confirmation |
| `--version` | print the version and exit |
| `--help` | print the usage and exit |

Exactly one of `--to` and `--to-original` is required. Outcomes per file, in
the console, in the log and in `<log>_restore.csv`:

- `RESTORED` — copied from the backup and verified against the catalog
  SHA-256 (`RESTORED_UNVERIFIED` when the catalog had no hash yet);
- `RESTORED_FROM_JXL` *(v0.14.0-rc2)* — a TIFF the backup keeps as JXL, made
  again by jxl-photo under its own name, with its pages and its date (each JXL
  checked against its catalog SHA-256 first). Lossless gives back the same
  pixels, not the same bytes ([tiff_to_jxl.md](tiff_to_jxl.md#restoring));
- `NEEDS_JXLPHOTO` *(v0.14.0-rc2)* — such a TIFF, and no jxl-photo: neither
  `--jxlphoto` / `JXLPHOTO` nor one kept in the backup. The others are
  restored, the run exits 1; `--keep-jxl` gives the JXL files as they are;
- `ALREADY_THERE` — the target exists with the same content (size and hash;
  for a TIFF kept as JXL: the original TIFF's);
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
is not a number or is negative, not enough space, no confirmation),
130 (Ctrl+C).

`restore_backup.py` writes its own `<YYMMDD_HHMMSS>_restore.log` and
`<YYMMDD_HHMMSS>_restore.csv` (`status,rel,target,detail`).

## Settings

`restore_backup.py` has its own setting at the top:

| Constant | Default | Meaning |
|---|---|---|
| `SPACE_MARGIN_GB` | `5.0` | free space kept on each restore target drive (default `--space-margin-gb`) |
