# Verifying a backup

`--verify quick` checks that each catalogued file still exists with the
recorded size and mtime; `--verify full` additionally hashes the destination
(and the source when needed) and detects content corruption. Verify never
copies, never asks for confirmation and never creates the destination
(destination or catalog missing → exit 2). Use the same selection flags you
backed up with.

```console
python selective_backup.py --source "G:\2025" --dest "H:\BACKUP" --verify quick
python selective_backup.py --source "G:\2025" --dest "H:\BACKUP" --verify full --max-gb 200
```

A backup made from a manifest is verified with `manifest_backup.py --verify`
([per_folder_modes.md](per_folder_modes.md#verify-and-analyze)); from the
menu, `[2] Verify` ([menu.md](menu.md)).

## Statuses

`OK`, `HASH_FILLED` (an adopted file's hash was recorded),
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

With `--verify full`, the backup copy is judged **before** the source: when
the original was deleted or changed, the copy may be the only one left. A corrupted copy is `DEST_CORRUPT` even if the original is gone; an
intact one is `SOURCE_MISSING` / `SOURCE_CHANGED` with the detail "backup
copy hash verified".

Only the catalog rows under the given sources are checked. The others are
counted *(v0.12.1)*, without changing the exit code:

```
2 files / 0.0 GB of the catalog are outside these sources: not checked
```

## How a verify runs

A file that cannot be read never stops the verify *(v0.13.1)*: it gets its
status, the run goes on, and the exit code is 1.

**A quick verify does not ask for a planned original twice** *(v0.13.2)*.
The plan has just listed every source folder, with each file's size and date:
`--verify quick` compares the catalog with that listing — what the backup
itself uses to call a file `UP_TO_DATE` — instead of asking the disk once
more per file (425 000 questions on a cold disk; about 30% of a quick verify
on a warm one). A catalogued file the plan does not hold (another source path,
no longer selected) is still asked, with the same proof as before for
`SOURCE_MISSING`. The one difference: a file that is listed but whose own
record the system refuses (a permission on that file alone) is `OK` in a
quick verify. A `--verify full` reads every file and reports it
(`SOURCE_UNREADABLE`); `VERIFY_QUICK_USES_PLAN = False` asks for each file
again, as before.

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

## The check right after each copy

The `hash` mode of `--verify-copy` re-reads the temp file right after
writing, and Windows serves that read from its cache, not from the disk
(measured: 0 MB read from the disk while 2 GB were re-read). It catches a copy
damaged in memory, not a bad write on the media. The real check of the media
is a `--verify full` with a cold cache: after the backup disk was safely
removed and connected again, or after a restart. Run right after the backup,
a `--verify full` can come out of the cache too.

## A damaged catalog

Every run checks the catalog when it opens it (`PRAGMA quick_check`). A
damaged one stops the run with `catalog is damaged (...)`, exit 2 — a backup,
a dry-run, a verify and a restore alike *(v0.13.1; before, a verify could say
`OK` over it)*. So does a row whose path would lead outside the backup (a
`..` part, a drive, a root): `catalog is damaged: the row '...' has ..., which
would lead outside the backup` *(v0.13.2)*. This tool never writes such a
row; a damaged or forged catalog made the restore read a file from outside
the backup and write it outside `--to`, and `--move-orphans` could have moved
a file of another folder. The copied files are not touched by this. To go on: move
`_photo_backup\catalog.sqlite` to another name and run the backup again. The
files already on the destination are adopted by their size and date (nothing
is copied again) and the next `--verify full` records their hashes again from
the originals. What is lost is the old hashes: until that verify, a copy
damaged earlier cannot be told from a good one.

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

**Which version made it** *(v0.14.0)*: the catalog records the program and
version that created it and the last one that changed it (`created_by`,
`created_at`, `updated_by`, `updated_at` in its `meta` table; a run that
changes nothing records nothing). The summary shows them under the
`Destination:` line — `catalog created by selective_backup 0.13.2 on ...; last
written by mirror_backup 0.14.0-rc1 on ...` — and the log keeps the same line.
A catalog made before v0.14.0 says `an earlier version (before 0.14.0)`, with
the date of its first copy.

`--expect-backup-id ID` makes it strict: the run (copy, dry-run or verify)
aborts with exit 2 before planning anything unless the destination holds
exactly that ID. The menu saves the ID with every preset, and a preset run
looks for the same relative path on every connected drive: when found, it
says `Backup found on D:\BACKUP (the preset says H:\BACKUP)` and runs there;
when not, it says the backup is not on any connected drive and exits 2.
