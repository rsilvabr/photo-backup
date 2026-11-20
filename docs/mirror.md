# The mirror (`mirror_backup.py`)

A normal backup only adds: a file that changed on the source is reported (or
moved to `versions\` with `--update-changed`), and a file that is gone from the
source stays in the backup as an orphan. The mirror makes the backup **follow**
the source: what changed is replaced, and what is gone **leaves the backup** —
first into a trash inside the backup, where it stays `MIRROR_KEEP_DAYS` (30)
days before it is deleted for good.

It is the one backup script of this project that removes files from its
destination, so it removes only what it wrote itself, only with proof, and it
always says what it is about to do before it does it.

```console
python mirror_backup.py --manifest backup_manifest.csv --dest "H:\BACKUP" --dry-run
python mirror_backup.py --manifest backup_manifest.csv --dest "H:\BACKUP"
python mirror_backup.py --dest "H:\BACKUP" --show-trash
```

From the menu: `[8] Mirror` ([menu.md](menu.md)).

## Lost a file? Getting it back

Read this first if a photo is missing and a mirror may have run since.

1. **Do not empty the trash.** Everything the mirror removed from the backup or
   replaced in it is in the backup's trash for `MIRROR_KEEP_DAYS` days. No run
   deletes it before its date (only `--empty-trash`, or the space question
   below, after you type a number).
2. **Look at the trash:** `python photo_backup.py` → `[8] Mirror` → `[3] What
   is in the trash`, or `python mirror_backup.py --dest "H:\BACKUP"
   --show-trash`. It lists each run (when its files were taken out, until when
   they stay, how many), the folders they came from, and the folder of each run.
3. **Copy the file back** from `H:\BACKUP\_photo_backup\trash\<run>\`: inside,
   the folders are the same as in the backup (`2024\Trip\DSC_0001.NEF`). The
   list of every file of a run, with where it was and why it left, is
   `Logs\<run>_mirror_actions.csv` (`<run>` is the folder's name, the time the
   run started).
4. A file that is still in the backup (it did not leave) is restored as usual
   with `restore_backup.py` ([restoring.md](restoring.md)).

A file deleted from the source by mistake leaves the backup at the next mirror
(into the trash): from then on its `MIRROR_KEEP_DAYS` days count.

## What it does

Two modes:

- **Scoped** (the default): it touches only what the folders of this run
  cover. A backup that holds `A` and `B`, mirrored from a manifest that names
  only `A`: `A` is mirrored (new files copied, changed files replaced, files
  gone from `A` removed) and `B` is left exactly as it is.
- **Total** (`--total`): the backup becomes this run. Files that came from
  folders that are not in this run leave the backup too. It always needs a
  person who types the number of files, and it cannot run with `--unattended`.

What is copied is decided exactly as by [`manifest_backup.py`](per_folder_modes.md)
(or by `selective_backup.py` with `--source`): the mirror only adds what leaves.

## The Mirror column

Each manifest line chooses what "gone" means, in an optional `Mirror` column
(`manifest_backup.py` reads it and ignores it):

```csv
Folder,Mode,Originals,Mirror
G:\2024,4,raw,
G:\2025,2,raw+jpeg,selection
```

| `Mirror` | What leaves the backup |
|---|---|
| `disk` (empty = this) | only what is gone from the folder on the disk. A file still in the folder stays in the backup, even if the line no longer selects it |
| `selection` | also what the line no longer selects: a lower mode, `Skip=yes`, `Only`, `Exclude`, a Capture One cache no longer copied |

A file of a `disk` line that the line no longer selects shows as *kept: still
on the source (Mirror=disk)*. Without a manifest, `--mirror disk|selection`
chooses for the whole run (default `disk`).

## What leaves the backup and what never does

| Why the file is in the backup but not in the plan | Scoped | Total |
|---|---|---|
| gone from the source (proven: the folder above could be read and the file is not there) | leaves | leaves |
| no longer selected by its line | only in a `selection` line | only in a `selection` line |
| the source cannot be read (a drive not connected, access denied) | **never** | **never** |
| from a source that is not in this run (same folder names on another disk) | never | leaves |
| in a folder that is not in this run (the `B` above) | never | leaves |

And in every mode:

- **Only what the backup wrote leaves**: a file with a row in the catalog that
  still has that row's size and date. A file you put in the backup yourself,
  or that you changed there, never leaves; the summary counts it (*not written
  by the backup* / *changed in the backup by hand*). A file that already
  matched its source when a backup first saw it was adopted into the catalog
  then, and counts as written by the backup.
- **A changed file** is replaced. Its old copy goes to the trash when the
  backup wrote it, and to `versions\` (as in a normal backup) when it did not.
  An old copy identical byte for byte to the new one is not kept.
- **Never behind a link**: a file whose source is behind a link or junction
  the backup does not follow stays.
- **A line with read errors removes nothing** in that run (without a
  manifest, an error anywhere stops every removal).
- **Only what the summary showed**: right before removing, each source is
  asked again; a file whose reason changed stays, and one that appeared after
  the summary waits for the next run.
- A folder left empty by a removal is removed (`os.rmdir`); never a folder of
  `_photo_backup`.

## What you see before anything changes

The backup's usual summary comes first; the mirror's own box comes last, so it
is on the screen when the question is asked. Its first lines say what matters:

```
== mirror_backup 0.14.0-rc2 ====================================================
 MIRROR (scoped) - what changes in H:\BACKUP
 1,204 files (34.1 GB) will be removed from the backup: to the trash until
 2026-12-19.
================================================================================
                                                         files        size
  already the same                                     98,765      3.21 TB
  copy (new on the source)                              1,234      45.6 GB
  replace (changed on the source)                          39       2.1 GB
    old copy to the trash                                  37       2.0 GB
    old copy to versions\ (not written by the backup)      2     104.9 MB
  remove from the backup (to the trash)                 1,204      34.1 GB
    gone from the source                                1,000      30.0 GB
    no longer selected by its line                        204       4.1 GB
  keep (not removed)                                    5,120     210.4 GB
    still on the source (Mirror=disk)                   5,000     200.0 GB
    the source cannot be read                             100       9.0 GB
    changed in the backup by hand                          20       1.4 GB
  not written by the backup (never touched)                 3     102.4 MB

  Where the files to remove are (the folders with the most):
    2024\Viagem a Sao Luis\Dia 2                          250      12.0 GB

  Removed and replaced files can be copied back from this folder until 2026-12-19:
    H:\BACKUP\_photo_backup\trash\261119_221500

  The list of every file and what happens to it:
    261119_221500_mirror_actions.csv
    in C:\photo-backup\Logs
================================================================================
Mirror now? copy 1,234, replace 39, remove 1,204 [y/N, Enter = no]
```

Anything that needs attention follows in its own lines marked `!!` (the trash
cannot be used, read errors, a line over the limit, a total mirror). After the
run, a box of the same shape says what **happened**, with the trash folder of
the run again. Every box carries the program and its version in its top border
(`== mirror_backup 0.14.0-rc2 ===`), as the summary's first line and the
console's title do. A run that stops says so in a box of its own:
`MIRROR STOPPED - nothing changed in H:\BACKUP`, why, and what to do.

## The trash

- **Where:** `<backup>\_photo_backup\trash\<run>\`, one folder per run (its
  name is the time the run started, `YYMMDD_HHMMSS`), with the same folders
  as the backup inside.
- **How long:** `MIRROR_KEEP_DAYS` (30) days. The first real mirror on or
  after that day — the day the screen shows, at any hour *(v0.14.0-rc2)* —
  deletes the run's folder for good, also unattended. With
  `MIRROR_KEEP_DAYS = 0` nothing goes to the trash (what leaves is deleted at
  once), and the next run also empties the trash that is there.
- **Look:** `--show-trash` (`[8]` → `[3]`): every run, newest first, with its
  date, until when it stays, its files and where they came from. Changes
  nothing.
- **Empty it:** `--empty-trash` (`[8]` → `[4]`) deletes everything in it, or
  with `--before YYYY-MM-DD` only what was taken out before that day, after you
  type the number of files. Never with `--unattended`.
- Only folders named like a run are ever deleted, each only by its real path:
  a folder of another name in the trash is listed and never touched, and a
  link or junction inside is left where it is (and is not an error of every
  later run, *v0.14.0-rc2*). If `_photo_backup` or the trash is itself a link
  or junction, the mirror changes nothing (exit 2), and so does any other link
  or junction inside the backup on the way to its files *(v0.14.0-rc2)*.
- **Ctrl+C** while files are removed or the trash is emptied still ends with
  the box (`MIRROR INTERRUPTED`) and the list of what was done to each file
  *(v0.14.0-rc2)*.

## Locks

- **The limit per line:** a line that would remove or replace more than
  `MIRROR_MAX_DELETE_PERCENT` (20) % of its files in the backup, and at least
  `MIRROR_MAX_DELETE_MIN` (5) files, changes nothing until a person types the
  number of files. A replaced file counts when its old copy is ours (it goes
  to the trash too); the edit files every edit rewrites
  (`AUTO_UPDATE_EXTENSIONS`: `.xmp`, `.cos`, ...) do not *(v0.14.0-rc2)*. The
  usual causes: the folder was deleted or moved on the source, another disk
  got the source's drive letter, the line's mode was lowered, or a program
  rewrote the files (ransomware encrypts them in place).
  `--allow-mass-delete` lets it through without the question (from the
  command line only; a preset never saves it).
- **Typing the number** is the confirmation of everything that cannot be
  undone or is unusual: the number of files changes every time, so it cannot
  be typed from habit, and reading it is reading how much is at stake. Enter,
  `y`, a wrong number or a closed console cancel, and nothing changes.
- **One run at a time:** a real run holds `_photo_backup\mirror.lock`; a
  second run (or a dry-run) stops while a live run holds it. The lock of a run
  that is not running any more (killed, the PC turned off) is taken over.

## When it does not fit

When the copy does not fit even after the trash past its date is deleted, the
mirror offers what could be deleted **for good** to make room — only the
options that make it fit, the one that deletes the least first:

```
  !! NOT ENOUGH SPACE on H: - 312.4 GB missing.
     It fits if the mirror deletes these FOR GOOD (no trash, no way back):
       1) the oldest trash (2026-10-19 to 2026-11-02)       1,204 files   340.1 GB  <- recommended
       2) the whole trash                                   3,410 files   812.0 GB
       0) cancel: nothing changes
```

The other options are *what this run removes, without the trash* and *the
whole trash + this run, without the trash* (when nothing smaller fits). They
are in the order of what they delete, the least first *(v0.14.0-rc2: in rc1 the
trash came first even when this run deleted less)*; between two of the same
size, the oldest trash: what left the backup today is the likeliest to be a
mistake nobody has noticed yet. A run with nothing to copy never asks: it
needs no space *(v0.14.0-rc2)*. You choose a number (Enter cancels), then type
the number of files. Unattended, the mirror stops instead and says how much
each option would free. `_free_space.csv` lists every file of every option.

## Unattended runs

`--unattended` (what a preset of the menu uses) never asks anything. What would
need a typed number stops the run with exit 2, nothing changed: a line over the
limit, not enough space, `--empty-trash`. `--total` cannot run unattended.
`--yes` alone only skips the "Mirror now?" question; a number that needs typing
is still asked at the console. A preset made from `[8] Mirror` runs the scoped
mirror of its manifest with `--unattended` ([menu.md](menu.md#unattended-runs-task-scheduler)).

## Options

| Option | Description |
|---|---|
| `--manifest PATH` | the manifest (`Folder,Mode,Originals,Mirror,Tiff,Jpeg`; `Tiff` and `Jpeg` keep that line's TIFFs and JPEGs as JXL, [tiff_to_jxl.md](tiff_to_jxl.md)) |
| `--source PATH` | without a manifest: a folder to mirror (repeatable). Every selection option of `selective_backup.py` works too (`--unselected`, `--unexported`, `--c1-cache`, `--exclude-folders`, ...) |
| `--dest PATH` | the backup; required |
| `--modes PATH` | the modes table (default `backup_modes.csv` beside the scripts) |
| `--base PATH` | with `--source`: the base of the layout (default: the drive root) |
| `--mirror {disk,selection}` | with `--source`: what "gone" means for the whole run (default `disk`) |
| `--total` | also remove what came from folders that are not in this run (typed confirmation) |
| `--allow-mass-delete` | let a line remove or replace more than `MIRROR_MAX_DELETE_PERCENT` of its files without typing the number |
| `--jxlphoto PATH` | jxl-photo's `jxl_photo.py` or `jxl-photo.exe`, for the lines that keep TIFF or JPEG as JXL (default `JXLPHOTO`) *(v0.14.0-rc2)* |
| `--no-convert` | the conversion off for this run: what would be converted is copied as it is; the JXL already in the backup stay, unless their file changed *(v0.14.0-rc5; [the conversion off](tiff_to_jxl.md#the-conversion-off-a-trip-with-a-slow-computer))* |
| `--work-dir PATH` | where the conversions run (default `JXL_WORK_DIR`; empty = the system's temp folder) *(v0.14.0-rc5)* |
| `--unattended` | nobody at the console: never ask; what needs a typed number stops the run (implies `--yes`) |
| `--show-trash` | list what is in the trash of `--dest` and how to get it back; changes nothing |
| `--empty-trash` | delete the trash of `--dest` for good, after the typed number of files |
| `--before YYYY-MM-DD` | with `--empty-trash`: only what was taken out before that day |
| `--dry-run` | show what would happen; change nothing |
| `--yes` | do not ask "Mirror now?" (a number that needs typing is still asked) |
| `--export-formats SPEC` | as in `manifest_backup.py` (default from `selective_backup.py`) |
| `--verify-copy {none,size,hash}` | verification of each copy (default from `selective_backup.py`) |
| `--space-margin-gb N` | free space kept on the backup's drive (default from `selective_backup.py`) |
| `--expect-backup-id ID` | stop (exit 2) unless the destination holds this backup ID |
| `--version`, `--help` | the version, the usage |

There is no `--move-orphans` and no `--verify`: verify the mirror's backup with
`manifest_backup.py --verify` (or `selective_backup.py --verify`), as any
other ([verifying.md](verifying.md)).

## Settings

At the top of `mirror_backup.py`:

| Constant | Default | Meaning |
|---|---|---|
| `MIRROR_KEEP_DAYS` | `30` | days a removed or replaced file stays in the trash; `0` = no trash |
| `MIRROR_MAX_DELETE_PERCENT` | `20` | the limit per line, in % of its files in the backup; `0` = no limit |
| `MIRROR_MAX_DELETE_MIN` | `5` | ... and the fewest files that can trip it |
| `MIRROR_TRASH_FOLDER` | `trash` | the trash's folder, under `_photo_backup` |
| `MIRROR_LOCK_FILE` | `mirror.lock` | the lock of a running mirror, under `_photo_backup` |
| `MIRROR_SUMMARY_LINES` | `10` | lines per table on the screen (the CSV has all) |

The manifest's `Mirror` column: `DEFAULT_MIRROR` and `MIRROR_VALUES` in
`manifest_backup.py` ([per_folder_modes.md](per_folder_modes.md#settings)).

## Logs, CSVs and exit codes

In `Logs\`, next to the backup's usual CSVs (`<stamp>_mirror_plan.csv`, ...):

- `<stamp>_mirror_actions.csv` — one line per file the mirror looked at:
  `action` (`remove`, `drop_row` = already missing from the backup, only its
  record goes, `keep`, `replace`, `versions`, `stray` = not written by the
  backup, `trash_purge`, `trash_foreign`), `reason`, `line`, `rel`, `size`,
  `source_path`, `note` and `result` (written before the run as `planned`,
  rewritten at the end with what happened).
- `<stamp>_mirror_free_space.csv` — when it does not fit: every file of every
  option (`group`, `path`, `size`).
- The log names each step: `MIRROR TRASH <rel>`, `MIRROR DELETED <rel>`,
  `MIRROR TRASH old copy <rel>`, `MIRROR REPLACED <rel>`, `MIRROR ROW DROPPED
  <rel>`, `MIRROR KEPT <rel>: <why>`, `MIRROR TRASH PURGED <run>`,
  `CONFIRMATION given|refused`, `LOCK taken over`.
- `--show-trash` and `--empty-trash` write `<stamp>_mirror_trash.log`.

| Exit code | Meaning |
|---|---|
| `0` | done |
| `1` | done, with problems: a copy or a removal failed, a line could not be read (it removed nothing), the trash could not be fully deleted |
| `2` | stopped before changing anything: a usage error, the lock, the limit, a total mirror not confirmed, not enough space, a cancelled question, a damaged catalog, the wrong backup |
| `130` | interrupted (Ctrl+C) |
