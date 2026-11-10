# Version history

Detailed notes for every release, newest first — the current one included.
The [main README](../README.md) describes how the tool works today; the
releases are also published as
[GitHub Releases](https://github.com/rsilvabr/photo-backup/releases).

## Release history

| Version | Date | Highlights |
|---------|------|------------|
| **[v0.13.0](#v0130)** | 2026-11-11 | A session leaves out only the subfolders its mode names (`LeaveOut`, `_OUTTAKES*` in the shipped modes); any other one is judged alone, so a `+` mode copies it |
| [v0.12.1](#v0121) | 2026-11-11 | Bug sweep: nothing left out without a count, a bigger mode never copies less, a source that cannot be read is not a source that is gone, the rolling verify keeps turning; `Originals=raw` keeps a photo whose only original is a TIFF, PNG, HEIC or PSD; `[7] Manifest` does what `[1]` does |
| [v0.12.0](#v0120) | 2026-11-10 | A file never counts as saved when the right content cannot be restored: eight fixes from an audit; `manifest_backup.py --verify` |
| [v0.11.0](#v0110) | 2026-11-10 | A manifest line can leave a folder out (`skip`) or keep only one type of file in it (`raw-only`, `jpeg-only`, `jxl-only`) |
| [v0.10.0](#v0100) | 2026-11-10 | Modes `1+` to `5+`: a folder not exported yet is copied whole, so what has not been edited is not left out |
| [v0.9.0](#v090) | 2026-11-09 | Each folder with its own backup mode, from a manifest; `--analyze` shows the size of every mode first |
| [v0.8.1](#v081) | 2026-11-09 | The versions folder keeps only real old versions: an old copy identical to the new one is no longer kept |
| [v0.8.0](#v080) | 2026-11-09 | Faster backups: the source is read ahead while the destination writes, and small files reach the disk in batches |
| [v0.7.0](#v070) | 2026-11-08 | Capture One alone to a folder you choose; a force restore that keeps the old files, and undo |
| [v0.6.0](#v060) | 2026-11-08 | Keeps what Capture One needs to show your edits on another computer: profiles, styles, defaults and settings, with a double-click restore |
| [v0.5.0](#v050) | 2026-11-08 | Leaves out what jxl-photo can make again: an sRGB/resized JXL copy is not backed up when its master is. `DSC_0001 (2)` is treated as its own photo |
| [v0.4.0](#v040) | 2026-11-08 | A renamed folder is moved inside the backup instead of copied again; orphans listed; each backup knows which disk it is; verify a big backup in slices; restore files from a backup |
| [v0.3.0](#v030) | 2026-10-07 | An interactive menu: questions, a dry-run, then the copy after a yes; presets that run unattended |
| [v0.2.0](#v020) | 2026-10-07 | First release: back up the exported photos with their originals and edits, a SHA-256 catalog, verify, a space check before copying |
| v0.1.0 | 2026-10-06 | First implementation, not released |

`mobile_backup.py` has its own version: [see the end of this page](#mobile_backuppy).

---

## v0.13.0

**Released 2026-11-11.** A session leaves out only the subfolders its mode
names. What is a session does not change: a folder with an export folder and
an original beside it.

**Before.** Every direct subfolder of a session that was not an export,
`CaptureOne` or `NKSC_PARAM` folder stayed out ("inside sessions"), unless the
mode added it (`AddFolders`, `AddWhole`). That was meant for the outtakes, but
it took any folder: one loose JPEG beside an export folder made a session, and
a folder with 240 photos of another event, put beside that export, had no copy
in mode `3+`. Measured on the owner's archive (all years planned in mode 3):
86 `_OUTTAKES` / `_OUTTAKES_DROP` folders left out inside sessions (190 636
files, 2 325 GB), as intended, and 84 folders with other names (2 841 files,
24.6 GB: alternative scans, "Selected" and "Edited" folders, the one above)
that nobody asked to leave out.

**Now.** The modes table has a new optional column, **`LeaveOut`**:
`;`-separated patterns (only `*` is a wildcard) of the direct subfolders of a
session that stay out, with everything below them, counted under "inside
sessions" and listed in `_inside.csv` as before. Any other direct subfolder of
a session is judged alone, exactly as the subfolder of a folder with an export
and no original beside it always was: a session if it holds an export folder; a
folder without an export if not — copied whole in a `+` mode, listed in
`_unexported.csv` in the others. The order is `Exclude`, `AddWhole`,
`AddFolders`, then `LeaveOut`: `_OUTTAKES_KEEP` matches `_OUTTAKES*` and mode 3
still adds it. The shipped `backup_modes.csv` has `LeaveOut = _OUTTAKES*` in
modes 1 to 5 and `1+` to `5+`, and an empty cell in the `Whole`, `Only` and
`skip` modes. To leave out one more name, add it: `_OUTTAKES*;rejects`.

- A table **without** the column (written before this version) reads it as
  `*`: every subfolder, the v0.12.1 behaviour, nothing changes.
- A column that is there with an **empty** cell leaves nothing out.
- `LeaveOut` with `Whole=yes` or `Skip=yes` aborts (exit 2), as the other
  combinations that make no sense: those modes walk no session.
- `selective_backup.py` on its own, without a modes table, does not change and
  has no new option; the menu does not change. Its new setting
  `SESSION_LEAVE_OUT` (`*`) is what a session leaves out when no table says
  otherwise.
- The summary line reads `inside sessions (subfolders the mode leaves out)`.

**Copies more on purpose.** A subfolder that is no longer "inside" a session is
handled as the subfolder of a folder with an export and no original beside it,
a path that already existed. In a mode without `+` it is still not copied (it
moves from `_inside.csv` to `_unexported.csv`); in a `+` mode it is copied. No
mode copies less than v0.12.1, on any tree: the test suite holds the plans of
v0.12.1 on its invariant tree, every mode, as a fixed list, and checks that a
table without `LeaveOut`, or with `*`, plans exactly that and that the new
table plans at least that. On the owner's archive (the owner's manifest:
1991–2023 in mode 6, 2024–2026 in `3+`, a dry-run of both versions compared
file by file): 254 files / 2.13 GB more, none less, all under four session
subfolders of 2024–2026 — the 240 photos of the case above, two small folders
(2 and 4 files) and one that only holds a `CaptureOne` folder (8 `.cos`).
"Inside sessions" goes from 190 890 to 190 636 files, and `_inside.csv` now
lists only `_OUTTAKES*` folders. `--analyze` of both versions, combination by
combination (855): none has fewer files or bytes; the modes without `+` do not
change; every `+` mode grows by 2 841 files / 24.6 GB over the whole archive,
of which 2 587 files / 22.4 GB in the years the manifest backs up whole
(1991–2023), had they been in `3+`.

**When upgrading.**

- **A modes table of your own without the column** (`--modes`): nothing
  changes. Add the column when you want the new rule.
- **With the new shipped table:** the next backup in a `+` mode copies the
  session subfolders that used to stay out. A `manifest_backup.py --verify`
  run before that backup shows them as `NOT_BACKED_UP`, exit 1 (the manifest
  selects them now, and they are not on the backup yet): expected, the next
  backup copies them. Look at the dry-run's `_unexported.csv` (with `copied = yes`) to see
  which folders they are. In a mode without `+` they stay without a copy and
  appear in `_unexported.csv` instead of `_inside.csv`.

---

## v0.12.1

**Released 2026-11-11.** Nothing to do when upgrading, with three exceptions
below (`Exclude` and a folder skipped by its name copy less; `Originals=raw`
copies more). A sweep for bugs looked for what the backup leaves out without
saying so, what it could lose, and what it calls a success when it is not;
this version fixes what it found, in three rounds.

**Nothing left out without a count.**

- **`AddFolders`** (`_OUTTAKES_KEEP` in mode 3) left out the files its
  `Originals` filter did not keep — JPEG twins of a RAW, TIFF, HEIC, PSD,
  video — without counting them anywhere. On the owner's archive: 2 269 files,
  30.5 GB. They are now a line of the summary: `left out inside added folders
  (Originals filter)`.
- **A bigger mode never copies less.** Mode 3 treats `_OUTTAKES\_EXPORT` as a
  session (its exports and their originals are copied); mode 4, which adds
  `_OUTTAKES`, filtered it like a folder of originals and lost those exports.
  A folder with an export folder inside an `AddFolders` folder is now a session
  too, and a file is copied when either rule keeps it: a loose file, and a file
  of its export folders too (the JPEG of a photo that has a JXL, a sidecar, a
  DNG, the `CaptureOne` folder of the export, which v0.12.0 copied there). The
  test suite
  checks `1 ⊆ 2 ⊆ 3 ⊆ 4 ⊆ 5 ⊆ 6` and `N ⊆ N+`, and that every file under a
  manifest line is in the plan or in exactly one count of the summary.
- **`CaptureOne` and `NKSC_PARAM` inside a session's subfolder** were neither
  copied nor counted (88 124 files, 0.59 GB of outtake settings on the owner's
  archive): they now count as "inside sessions". With `--unexported copy`, a
  folder that has no file of its own, only subfolders and a `CaptureOne`
  folder, has its Capture One and NKSC_PARAM folders copied.
- **Links and junctions** that are not followed are counted (`links/junctions
  not followed: N`) and each one is in the log. On the owner's archive: 429,
  every one a `CaptureOne\Cache` moved to another disk. A listing shared
  between the lines of a manifest no longer hides them.
- **`_inside.csv`** lists every direct subfolder of a session left out, and the
  summary names the five biggest by name under "inside sessions", so a folder
  that is not an outtake (on the owner's archive, a folder of party photos
  inside a folder made a session by one loose JPEG) stands out. The rule of what is a
  session does not change in this version.
- **`AddWhole` and `AddFolders`** apply to the subfolders of any folder with an
  export folder, also when no original is beside it: a film session whose only
  originals are in `RAW SCANS` keeps them in modes 1 to 5.
- **A column the modes table or the manifest does not know aborts** (exit 2,
  with its name), as does a line with more values than columns. A header typed
  `Unexported ` with a space used to be ignored, and every `+` mode became its
  base mode. An empty column name (an extra comma at the end of every line,
  often a cell left in Excel) aborts too, and the message says so.
- **Folders skipped by their name** (`$RECYCLE.BIN`, `System Volume
  Information`, `--exclude-folders`) are never read, so what they hold is not
  counted file by file; they are now named: `folders skipped by name (system /
  --exclude-folders): N` in the summary, `SKIPPED FOLDER <path>` in the log.
- **`folders without export`** under `Not copied:` counted also the folders a
  `+` mode (or `--unexported copy`) copies. It now counts only those left out;
  `_unexported.csv` still lists all of them, with a new last column `copied`
  (`yes`/`no`).

**Copies less on purpose: `Exclude`.** A folder that matches a mode's
`Exclude` is now never walked, in every mode and at any depth, and what it
holds is counted (`excluded by the mode (Exclude)`), as the README always said.
Before, a session inside an excluded folder was still copied by a mode without
`+` and dropped, without a count, by the same mode with `+`. The export,
Capture One and NKSC_PARAM folders of a session are not affected. The shipped
modes have no `Exclude`.

A smaller case, also declared: in an `AddFolders` folder, a `Cache` folder
deeper than the first level of a `CaptureOne` folder that is inside an export
folder (`_OUTTAKES_KEEP\_EXPORT\CaptureOne\X\Cache`) is counted as Capture One
cache, as in every export folder; v0.12.0 copied it there. A cache is made
again by Capture One. No such folder exists in the owner's archive.

**Copies less on purpose, too: a folder skipped by its name.** The folders of
`SYSTEM_SKIP_FOLDERS` (`$RECYCLE.BIN`, `System Volume Information`) and of
`--exclude-folders` / `EXCLUDE_FOLDERS` are now skipped everywhere, at any
depth and in every mode: also inside an `AddFolders` folder, a session inside
one, an export folder and a `CaptureOne` folder. v0.12.0 copied them there, so
mode 5 (`_OUTTAKES_KEEP` added) copied what mode 6 (everything) did not, and an
`EXCLUDE_FOLDERS` you set did not apply inside `_OUTTAKES_KEEP`, without a word
on the screen. Each one is named, as the others (`folders skipped by name` in
the summary, `SKIPPED FOLDER <path>` in the log). A `$RECYCLE.BIN` only exists
at the root of a drive, and the shipped `EXCLUDE_FOLDERS` is empty: on the
owner's archive, nothing changes.

**Copies more on purpose: `Originals=raw`.** It kept every RAW and a JPEG
only when no RAW of the same photo was beside it; a photo whose only original
is a TIFF, PNG, HEIC/HEIF/HIF or PSD (a camera that wrote TIFF, a developed
TIFF whose RAW is not there, a screenshot) had no copy at all: on the owner's
archive, 13 files / 0.62 GB in 2024 to 2026. Such a file is now kept, under
`raw` and `raw+jpeg` (`--unselected raw_or_jpeg` / `raw_jpeg`), with the same
rule of "the same photo" as the JPEG (`X (2).tif` is another file), and its
sidecars too. The TIFF developed from a RAW that is beside it
(`DSC_4321_NXstudio.TIF`, `P1010099.tif` with `P1010099.ORF`) still stays out.
It applies everywhere the filter does: `--unselected`, `AddFolders`, and the
export folders inside an added folder. `raw` within `raw+jpeg` within `all` is
tested in every mode.

**A source that cannot be read is not a source that is gone.**

- **`--move-orphans`** moved good backup copies out of the backup tree and the
  catalog when their source could not be reached: another drive with the same
  folder names that was not connected, or a folder that could not be read. A
  source now counts as gone only with proof: the system said "does not exist"
  and the nearest existing folder above could be listed. Anything else is
  `source_unreadable`; a file of a source not given in this run is
  `other_source`. Only proven `source_missing` files are moved, and a run with
  an error in its plan moves none (the summary says why: `N errors in the plan
  of this run`, a duplicate destination path included). The proof is by path:
  another disk on the same letter with the same folders passes it (the README
  says to look at a dry-run after changing the source disk).
- **The move detection** (a renamed folder moved inside the backup) uses the
  same proof.
- **`--verify`** says `SOURCE_UNREADABLE` (a problem, exit 1) where there is no
  proof, instead of `SOURCE_MISSING`.

**What the verify did not check.**

- **The rolling verify (`--max-gb`)** kept the rows that never get a
  `verified_at` (`DEST_CORRUPT`, `DEST_MISSING`, ...) at the front of the queue,
  and they used up the budget every night: the rest of the backup stopped being
  verified. Those rows are still checked every night and keep the exit code 1,
  but their size no longer counts against `--max-gb`.
- **`--verify`** now says how many catalog rows are outside the given sources
  and were not checked.
- **A `--verify quick` recorded `verified_at`** on every row it found in place,
  without reading any file, and sent the rolling queue of a `--verify full
  --max-gb` back to its start: a nightly quick between the slices meant the
  same first slice every night. Only a full verify records `verified_at` now
  (also for `manifest_backup.py --verify`).

**The copy.**

- **A destination that stops taking writes** (a USB disk pulled out, a full
  disk) made the backup read the whole source to the end, file by file, only
  to fail each one. After `DEST_CHECK_AFTER_ERRORS` (20) failed copies in a
  row, the destination is tested with a file of its own (written and removed in
  `_photo_backup`); when that fails, the backup stops reading, records what was
  copied, prints the summary with a `STOPPED:` line and exits 1.
- **The catalog of a destination that went away** could not be written either:
  when the rows of the last copies were still waiting for their commit (a file
  of 100 MB or more is recorded alone, and the rows are committed every
  `FLUSH_BATCH_FILES`), the backup ended in a `sqlite3` traceback, without the
  `STOPPED:` line or the summary. A catalog that cannot be written now stops the
  copy as a destination that stopped taking writes: nothing more is read or
  tried, the summary has the `STOPPED:` line and a `CATALOG:` line saying how
  many copied files have no row yet (they keep their final names, and the next
  run adopts them by their size and date), `--move-orphans` moves nothing, and
  the run exits 1.
- **A destination with a name Windows does not accept** (`--dest "E:\x|y"`)
  ended in a traceback; it is now `Destination not available: ...`, exit 2,
  before anything is copied.
- **Not enough space because of a renamed folder.** A possible move inside the
  backup still counts as a copy in the space check (it is only confirmed by
  hash later), but the message now says how much of what is needed may be
  moves.
- **Ctrl+C while a manifest, a restore or a Capture One job was still
  planning** printed a traceback; it now says `Interrupted.` and exits 130, as
  during the copy.
- **Two files with one destination path**: the message now names both and says
  which one has a copy; for a folder whose name ends with a dot or a space
  (`Trip.` beside `Trip`, which Windows cannot read apart), it says that what is
  in that folder is not backed up and why.

**The restore.** An `--only` that matches nothing in the catalog is named on
the screen and in the log, the others are restored, and the run exits 1 (it
used to say "done").

**Capture One's own files (`captureone_backup.py`).**

- **The old copy of a changed file** went to the versions folder before the
  new one was safe: a copy that failed (Capture One holding the file) left the
  backup without that file. It now follows `selective_backup.py`'s rule: the
  old copy keeps its name until the new one is on the disk.
- **A catalog row without a hash** (the catalog was made again, or a run was
  stopped) was `OK_SIZE_ONLY` forever, and its restore said `RESTORED` even
  with damaged bytes. `--verify` now compares it with its source when the
  source still has the size and date of the row: the same bytes record the
  hash (`HASH_FILLED`), other bytes are `DEST_DIFFERS` (exit 1); what cannot be
  checked stays `OK_SIZE_ONLY` and the summary counts it. Its restore checks
  the size and says `RESTORED_UNVERIFIED`; another size is not restored.
- **`--force` and a file that appears meanwhile.** A file restored by
  `--force` was journaled as added before the copy; when another program
  wrote that file in between, `--revert-last` took that program's file out.
  It is now journaled only after the copy succeeded (also in `--revert-last`
  itself).

**The menu says what it shows.**

- **`--unselected raw_or_jpeg`** (the default at the top of
  `selective_backup.py`, or a remembered choice) became `skip` when you pressed
  Enter: the menu offered only three of the five values. It now offers every
  value the backend accepts, and the current one is always marked.
- **`--no-c1-cache` and `--no-derivatives`.** With a `True` default at the top
  of `selective_backup.py`, a "no" in the menu was not on the command line and
  the backend copied anyway. The menu and the presets now always pass one of
  the two forms. The menu also asks whether to copy the jxl-photo derivatives
  and Capture One's own files, and no longer blames `COPY_CAPTUREONE_SETTINGS`
  for a choice it remembered.
- **A "no" to the Capture One question of `[2] Verify`** also turned the
  Capture One step off for the next backup: the two questions shared one
  remembered choice. The verify has its own now (a config without it uses the
  backup's).
- **`[7] Manifest` does what `[1]` does.** Its backup did not keep Capture
  One's own files and never replaced a changed original or export
  (`--update-changed`); its verify offered no "Max GB" and did not check
  `_captureone`. It now asks the same questions (remembered for the next
  manifest run) and runs the same steps.
- The menu's description of `raw_or_jpeg` and `raw_jpeg` says what they keep
  now.

**`mobile_backup.py` 0.1.2** — see [below](#mobile_backuppy).

---

## v0.12.0

**Released 2026-11-10.** Nothing to do when upgrading. An audit asked one
question of the whole cycle, from the selection to the restore: can a file
count as saved when the right content cannot be restored? It found these.

- **An original edited while it was being copied.** The copy got the date the
  original had at the END of the copy. An original rewritten by another
  program in between, with the same size, became old bytes under the new date,
  and every later run called it up to date. The copy now carries the date of
  the bytes it holds, so the next run sees the file as changed.
- **Two files with one place in the backup are an error.** Two sources on
  different drives with the same folders, or two names that differ only for
  Windows (`Straße.jpg` and `Strasse.jpg`): the second file was left out with
  a line in the log file only. It is now counted under `Errors:` and the run
  exits 1.
- **`manifest_backup.py --verify quick|full`** (and `[3] Verify` in the menu's
  Manifest). `selective_backup.py --verify` does not know the manifest: it
  could say `OK` with a file of the manifest missing.
- **The old copy of a changed file keeps its name** until the new one is on the
  disk. It used to move to the versions folder before the new copy started; a
  copy that failed, or a power cut, left nothing under that name.
- **A read-only temp file left by a crash** no longer blocks that file on every
  later run.
- **The restore checks the size** of each backup file against the catalog. A
  file without a hash that had lost bytes was restored as
  `RESTORED_UNVERIFIED`; it is now `CORRUPT_IN_BACKUP`.
- **Copy errors are said on the screen**, not only in the log and the exit
  code: `ERRORS: N files were NOT copied`.
- **The restore and the dry-run never write to the backup**, also when a run
  was killed in the middle of a catalog update: the catalog is then read from
  a private copy.

Measured in the same audit, documented but not changed: `--verify-copy hash`
re-reads the copy from the Windows cache, not from the disk. The check of the
disk is a `--verify full` after the disk was removed and connected again.

---

## v0.11.0

**Released 2026-11-10.** Nothing to do when upgrading: tables without the new
columns keep working.

- **Leave a folder out.** Mode `skip` on a line below a larger one: that folder
  stays out and the rest of the larger line is copied. Lines inside it can still
  have their own mode.
- **Keep one type.** `raw-only` (with sidecars and edit settings), `jpeg-only`,
  `jxl-only`, or your own list in the new `Only` column. It is literal: a photo
  that does not exist in that type has no copy.
- **Shown before copying.** The summary counts what these lines left out, and
  `--analyze` shows every mode of such a folder.
- **Nothing is deleted.** Files already backed up that a line no longer selects
  stay on the backup and appear as `not_selected` orphans.

---

## v0.10.0

**Released 2026-11-10.** Nothing to do when upgrading: the modes `1` to `6` work
as before.

- **Modes `1+` to `5+`.** A new optional column of the modes table,
  `Unexported`: a folder with an export follows the rest of the mode, and a
  folder without one is copied whole. A table without the column keeps working.
- **Exporting later deletes nothing.** When a folder gets an export, the files
  that left the selection appear as `not_selected` orphans and stay on the
  backup.
- **`Exclude` also applies** to the folders without an export in these modes.

---

## v0.9.0

**Released 2026-11-09.** Nothing to do when upgrading: the normal backup works
as before.

- **Each folder with its own mode.** `manifest_backup.py` reads a manifest
  (`Folder,Mode,Originals`) and a table of modes (`backup_modes.csv`), which you
  can edit: unselected originals, subfolders to add filtered or whole, folders
  to exclude, or the whole folder, also what has no export. The most specific
  line wins. Menu `[7]`.
- **See the size first.** `--analyze` shows every mode of every folder through
  the same planner the copy uses, and copies nothing.
- **Originals, per line.** `raw` keeps the RAW, its sidecars and any JPEG with
  no RAW beside it, so no photo is left without a copy; `raw+jpeg` keeps every
  JPEG; `all` keeps everything.
- **Mistakes stop before the first byte.** A repeated or missing folder, an
  unknown mode, a file that is not UTF-8, or an export folder with a line of its
  own below another line abort with exit code 2.
- **Same safety as a normal backup.** The copy, catalog, space check, versions
  and orphans are the selective backup's own. Lowering a mode deletes nothing:
  those files appear as orphans.

---

## v0.8.1

**Released 2026-11-09.** Nothing to do when upgrading.

- **The versions folder keeps only real old versions.** Capture One rewrites
  every `.cos` of a folder it opens: new date, same bytes (37 of 38 in a real
  folder). Each of those used to leave an identical copy in `versions\`. The new
  file is still copied every time; the old one is left out only when it is
  identical to the bytes really copied, and the log says `IDENTICAL`.
- **Checked at the last moment.** The comparison uses the hash of what was
  copied, right before the final name, so a file saved during the copy still
  keeps its old version. A different size goes to the versions folder at once,
  as before.
- **A failed copy of an identical file** now leaves the old copy where it was.

---

## v0.8.0

**Released 2026-11-09.** Nothing to do when upgrading. An interrupted backup
continues from where it stopped, as before.

- **Both disks work at once.** A thread reads the source up to `READ_AHEAD_MB`
  (512) ahead while the destination writes. Before, the source disk waited for
  the destination at the end of every file.
- **Small files reach the disk in batches.** Files below `SMALL_FILE_KB` (1024)
  are flushed `FLUSH_BATCH_FILES` (50) at a time, by `FLUSH_THREADS` (8). On a
  USB hard disk this took the wait from 71-78 ms to about 9 ms per file, which
  matters because edit settings and sidecars are most of the files in a backup.
- **The order did not change.** A file gets its final name only after it is on
  the disk, and the catalog lists it only after that. Restores and the Capture
  One backup copy as before.
- **Measured.** A real 44 GB session (4 983 files) to a scratch SSD went from
  34 to 45 MB/s. A large file's own flush costs nothing measurable (about
  100 MB/s on that USB disk with any strategy), so large files keep theirs.

---

## v0.7.0

**Released 2026-11-08.** Nothing to do when upgrading.

- **Capture One alone.** Menu `[6]` backs up just Capture One to a folder you
  choose and restores from one.
- **Force restore.** `--force` also replaces files that differ, after keeping
  each current one in `%LOCALAPPDATA%\CaptureOne_old\old_<date>_<time>_<ms>`.
  The swap is atomic and happens only after the old file is safe.
- **Undo.** `--revert-last` puts back the newest `old_` folder and takes out
  what the force added, keeping what it replaces in `revert_old_<...>`. A folder
  renamed to end with `_not` is skipped.
- **Limits.** Factory profiles need `--force-installed`, because an older copy
  on a newer Capture One changes the colour of every photo from that camera. A
  monitor calibration (a display profile with a `vcgt` tag) is never replaced.

---

## v0.6.0

**Released 2026-11-08.** Nothing to do when upgrading: existing presets and the
menu start keeping these files on their next backup
(`COPY_CAPTUREONE_SETTINGS` at the top of `photo_backup.py` turns it off).

- **`captureone_backup.py` keeps what the edits depend on.** A `.cos` file only
  names its ICC profile, film curve and style. The script copies them from
  where Capture One really has them into `<destination>\_captureone`, with its
  own catalog: everything in `%LOCALAPPDATA%\CaptureOne` and `Capture_One`
  except login data, cookies, the licence file and logs; the install folder's
  `Color Profiles` and `Film Curves`; the `.icc`/`.icm` files of the Windows
  colour folder. A folder you add there, for the installer and serial, comes
  along. `WHERE_EACH_FOLDER_GOES.txt` says where each part came from.
- **A new computer needs no Python.** `RESTORE_CAPTUREONE.cmd`, written into
  the backup, asks for administrator rights and only adds missing files. With
  Python, `--restore-from` does the same and checks each file against the
  catalog; `--also` brings back what a restore leaves alone by default (batch
  queue, plugins, preferences, your own folders).
- `--verify` re-hashes this backup; the menu's verify includes it, and item
  `[6]` restores it.
- As in the rest of the tool, the sources are read-only, nothing in the backup
  is deleted (a file that really changed keeps its old copy in the versions
  folder) and a restore never overwrites a file.

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
| 0.1.2 | 2026-11-11 | Deletes only with proof: never a photo of a source not given in this run, nothing when a source folder could not be read; an exiftool failure is read again next time instead of becoming "no date"; the state is saved more often |
| 0.1.1 | 2026-10-13 | Photos numbered camera by camera, each camera in its own shooting order (cameras' clocks disagree) |
| 0.1.0 | 2026-10-13 | First version: one album per event, photos numbered in shooting order, synchronised (only its own files are renamed or removed) |

### 0.1.2

**Released 2026-11-11** with photo-backup v0.12.1. Nothing to do when
upgrading: a state written by 0.1.1 keeps working, and its "no date" photos are
read once more (see below).

- **A run with fewer sources deleted the albums of the others.** A run now
  only deletes or renumbers the photos whose path is under one of the sources it
  was given; the others are left alone, and the summary says how many. (The
  state also records each photo's source root. The decision is by the path, so
  a test run with a narrower `--source` does not keep a photo deleted later in
  the mirror.)
- **A folder that could not be read looked empty**, and its photos were
  deleted from the mirror (and then from the phone at the next sync), exit 0.
  Now the run names the folder, deletes and renames nothing, copies only new
  photos to free names, and exits 1.
- **An exiftool failure** (not installed, a timeout, a crash) recorded every
  photo of that batch as "no date" for good, numbered by name, exit 0. Those
  photos are now read again on the next run, and the run exits 1. A photo
  exiftool read and found no date in is still last of its camera, as before.
  The README says that exiftool is required.
- **A "no date" written by 0.1.1 after an exiftool failure** stayed for good.
  When the state was written before 0.1.2, every photo without a date in it is
  read once more; one really without a date is not read again after that.
- **A run killed before the end** (not Ctrl+C) left the state of the run
  before: the files it had renamed or copied were then unknown, and their album
  was skipped on every run after. The state is now saved right after the
  renames and every `STATE_SAVE_EVERY` (20) copies. What remains is a kill in
  the middle of the renames or between a copy and the next save: that album is
  skipped until the file is removed by hand.
