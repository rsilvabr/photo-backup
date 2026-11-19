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
