# Re-runs, changed files, moves and orphans

What a second backup to the same destination does: which files it copies
again, where the old copies go, what happens to a renamed folder, and what
happens to a backup file whose original is gone.

## Re-runs, changed files and the versions folder

On every run, each planned file is compared against the destination:

- missing → `NEW`, copied.
- same size and mtime (within `MTIME_TOLERANCE_S`) → `UP_TO_DATE`. If the
  catalog has no row for it (the file was copied by another tool, such as
  FreeFileSync or Explorer), a row with
  `sha256 = NULL` is added; `--verify full` fills the hash later. A row whose
  size/mtime no longer match is refreshed the same way.
- otherwise → `CHANGED`: reported and NOT copied, unless `--update-changed`
  is given. With `--update-changed`, the old copy is moved to
  `<dest>\_photo_backup\versions\<YYMMDD_HHMMSS>\<rel>` and the new file is
  copied. Nothing in the destination is ever overwritten in place.
- **Same bytes, new date.** Capture One rewrites every `.cos` of a folder it
  opens, without changing them. Such a file is still copied, but when the bytes
  copied are identical to the old copy, the old copy is not kept in the versions
  folder (the log says `IDENTICAL`). A different file is always kept.
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

## What the backup sets aside, and for how long

What leaves the backup's tree is one of three kinds. Each has a folder per run
(`<dest>\_photo_backup\<folder>\<YYMMDD_HHMMSS>\<rel>`) and its own number of
days, separately for the normal backup and the mirror:

| kind | what | normal backup: folder / setting | mirror: folder / setting |
|---|---|---|---|
| old versions | the old copy of a changed file; a JXL group undone or made again (`--reconvert-older`); the JXL of an older version of a file | `versions\` / `VERSIONS_KEEP_DAYS` (`--versions-keep-days`) | `trash_versions\` / `MIRROR_VERSIONS_KEEP_DAYS` |
| TIFF originals | a TIFF copy its JXL replaced | `ORIGINALS_FOLDER` (`originals\`) / `ORIGINALS_KEEP_DAYS` (`--originals-keep-days`) | `trash_originals\` / `MIRROR_ORIGINALS_KEEP_DAYS` |
| removed | (mirror only) a file gone from the source, no longer selected, ... | — (the normal backup never removes those: [orphans](#orphans)) | `trash\` / `MIRROR_KEEP_DAYS` |

- **A whole number N**: kept N days. The first real run (a dry-run, `--verify`
  or `--analyze` never deletes) on or after the day *taken out + N* deletes that
  run folder, right after the question and before the first copy. Each run
  deletes only in the folders it owns: the normal backup in `versions\` and
  `originals\`, the mirror in its three trash folders. The day *taken out* is
  the run folder's name, written with the computer's clock: a run made while
  the clock was behind (a dead CMOS battery, no time sync) names its folder in
  the past, and once the clock is right that folder goes that many days early.
  It cannot be told apart (the folder's own dates come from the same clock):
  keep the clock right, or use `never`.
- **`never`** (`None` in the script): never deleted by age; only offered when
  space is short. The default of the normal backup: it keeps everything, as
  before. The mirror keeps its 30 days.
- **`0`**: not kept at all: what leaves the backup's tree is deleted at once,
  and the run folders already kept in that folder are deleted at the next run.
  Both need the number of files typed in that run; `--yes` (the normal backup)
  or `--unattended` (the mirror) stops instead (exit 2, nothing changed). The
  number is an upper bound: never more files of a kind are deleted than were
  typed for it; anything more is kept in its folder, and the next run asks for
  it. The menu's own copy steps run with `--yes`: they stop too.
  Capture One rewrites its settings files at each edit: with old versions at 0
  almost every run asks, and every preset stops.
- TIFF copies a JXL replaced before v0.14.0-rc9 were set aside in `versions\`
  and follow `VERSIONS_KEEP_DAYS`. The old copy (as it is) of a TIFF whose
  source changed goes to `originals\` whether the new version is converted or
  copied as it is (the conversion off or failed): where the summary counted it.
- Anything else is refused before anything is read (exit 2).
- A copy the mirror did not write (no row, not adoptable) always goes to the
  normal backup's `versions\`, never to a trash folder.
- **A JPEG copy its JXL replaced** is deleted, but only when the file itself,
  read, holds the bytes its JXL gives back (the JXL's `source_sha256`, proven when
  it was made) and its row does not say other bytes: the log says `DELETED (its
  JXL gives these bytes back)`. Without that proof (a file that cannot be read,
  other bytes) it is kept as an old version.
- The catalog's table `originals` lists each TIFF copy kept in `originals\` (or
  the mirror's `trash_originals\`): where it is, the name it had, its size, date
  and SHA-256. Its row goes when the file is deleted. *(v0.14.0-rc9)* A TIFF
  original is used again with proof (the row, the file's size and date, the
  SHA-256 the JXL was made from): the restore gives it back as it is instead of
  decoding the JXL (`RESTORED_ORIGINAL`), and when the manifest goes back to
  `Tiff` = copy the normal backup renames it back to its place instead of
  copying the TIFF again (`PUT BACK` in the log; a rename that fails, or no
  proof: copied from the source). The mirror still copies
  ([details](tiff_to_jxl.md#going-back-from-jxl-to-tiff-the-kept-original-is-renamed-back-v0140-rc9)).
- Only folders named like a stamp are deleted, by their real path, never through
  a link or junction; a store that is a link stops a real run (exit 2), and so
  does `orphans\` with `--move-orphans` or `tools\` (the programs kept in the
  backup), before the first byte.
- When the space check counted on what expires (or what a person chose), the
  disk is asked again after deleting it: a run folder that could not be deleted
  (a file held by another program) leaves too little room, and the run stops
  before the first copy (exit 2, `Still not enough space after deleting what was
  set aside`).
- A catalog that cannot be written while deleting (another program holds it)
  stops the deletion there: what was deleted is said, the rest stays (exit 1).

**Every run shows the space block** next to its `Space:` line: one row per
folder that exists (the normal backup lists the mirror's trash too: the same
disk), its files, size, how long it is kept and its next deletion, what expires
in this run, and how much would be free after deleting each.
`selective_backup.py --space-report --dest X` (the menu's **[9] Backup space**)
prints the same in full, run folder by run folder, and reads only the
destination.

**When the run does not fit**, a person at the console (not `--yes`, not
`--unattended`) is offered what this run owns that is set aside: per folder its
oldest run folders (when they are enough) and all of it, the smallest first,
with "free after". Several can be chosen (`1 3`; two of one folder: the larger
wins), then the number of files is typed. The run deletes them before the first
copy, asks the disk again, and stops (exit 2) if it still does not fit. A
dry-run only shows the options; `--yes` or `--unattended` stops, naming what is
set aside. The list of what each option deletes is `<stamp>_free_space.csv`.

## Moved and renamed folders

When you rename a session (`250129_Flores` → `250129_Flores [FINAL]`), every
file appears at a new relative path and looks `NEW`. Instead of copying
everything again and leaving the old copy behind, the tool:

1. looks for a catalogued backup file with the same file name and size whose
   **source is gone** (`possible moves: N files` in the summary — a dry-run
   writes nothing);
2. before moving anything, hashes the new source file and the old backup copy
   and compares them with the SHA-256 recorded at copy time: only a full match
   moves (`MOVE old\path -> new\path`);
3. moves it into the new place, updates the catalog and removes the folders
   that the move left empty — up to (not including) the destination root.
   This is the **only** case where the tool removes a folder on the
   destination, and it never removes a file; anything that does not match is
   copied as new (`MOVE REJECTED ...: content differs`).

`--no-detect-moves` turns the whole detection off (everything is copied
again), and `DETECT_MOVES = False` does the same by default.

The space check still counts a possible move as a copy: it is only confirmed
by hash during the run. When that makes the backup not fit, the message says
how much of what is needed may be moves *(v0.12.1)*: `Not enough space: need
..., free ..., margin ...; 120.0 GB of it may be files moved inside the backup
(possible moves, ...)`. Make room, or use `--no-detect-moves` knowing that
everything is then copied again.

## A backup made by another program

A folder half copied by FreeFileSync, or by hand, holds files this backup did not write:
no catalog row. Today a file there with the source's size and date is adopted (a row
without a hash, `--verify full` fills it); the same size with another date is a
changed file (reported, or moved to `versions\` and copied again with
`--update-changed`).

`--adopt-by-hash` (or `ADOPT_BY_HASH = True` at the top of `selective_backup.py`) asks
for proof instead: for every such file the source and the backup file are both read
and their SHA-256 compared (the cost: twice the size of those files, read once per
run; the menu's dry-run reads them too, so a menu backup reads them twice before it
copies anything).

- Same bytes: adopted, its row has the hash. When its date was another, the backup
  sets it to the source's (the file's bytes are never written), so it is current.
- Same bytes, another date, and the file is read-only: left as it is in that run —
  not adopted, not replaced (not even with `--update-changed` or by the mirror),
  never made writable; `ADOPT SKIPPED ...: read-only` in the log, a row *the
  source's bytes, read-only: left as they are* in the summary, and no error.
  Clear its read-only attribute to let the next run adopt it.
- Same size and date as the source, other bytes: a changed file (`ADOPT REFUSED`).
  Nothing remembers it: a later run without the option adopts it by its size and
  date, as before.
- Anything unreadable, a link, or a file that changed while it was read: left as it
  is, without a row (`ADOPT SKIPPED`). A file that changed between the reading and
  the write (or was made read-only meanwhile) is left without a row too (`ADOPT
  SKIPPED` / `ADOPT FAILED`): an error, exit 1.
- A dry-run does the reading too and says in the summary what would be adopted.

## Orphans

A backup file whose source no longer exists is an **orphan** (an original you
deleted after it was exported). Every run writes `<log>_orphans.csv` with the
reason for each:

- `source_missing` — the original is gone (proven: see below);
- `not_selected` — the original still exists, but the current selection does
  not include it any more (for example you changed `--export-formats`);
- `source_unreadable` *(v0.12.1)* — the original could not be checked (access
  denied, a drive that is not connected): not proof that it is gone;
- `other_source` *(v0.12.1)* — the file came from a source that is not given in
  this run (two drives with the same folder names backed up to one
  destination): not checked.

"Gone" needs proof: the system answered "does not exist" and the nearest
folder above that does exist could be listed. The move detection above uses
the same proof. The proof is by **path**: another disk mounted on the same
letter, with the same folders, passes it — after changing the source disk,
look at a dry-run before using `--move-orphans`. And the catalog keeps the
source path a file had when it was copied: if the source moves to another
letter or folder, what is deleted there later shows as `other_source` and is
never moved by `--move-orphans`.

Orphans are only reported by default. `--move-orphans` (real copies only)
moves the `source_missing` ones to
`<dest>\_photo_backup\orphans\<YYMMDD_HHMMSS>\<rel>`, removes their catalog
rows and cleans the folders the move left empty; `not_selected` files are
never touched, and neither are the other reasons. A run with an error in its
plan (a folder or file that could not be read, two files with one destination
path) moves no orphan at all, and says so: `--move-orphans: nothing moved: N
errors in the plan of this run`. Since the old
copy is only moved inside the backup, the space does not shrink until you
delete the orphans folder yourself — that is outside this tool's scope.

To make the backup follow the source instead (what is gone leaves the backup,
through a 30-day trash), see [the mirror](mirror.md).
