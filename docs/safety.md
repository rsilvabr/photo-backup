# Safety guarantees

The README keeps one line for each; here is every rule, with the version that
added the newer parts. The restore has its own guarantees
([restoring.md](restoring.md)), and so does the Capture One force restore
([captureone.md](captureone.md#force-restore-and-undo-v070)).

- **The source is READ-ONLY.** No code path opens a source file for writing,
  renames, deletes, touches or changes attributes on anything under a
  `--source`.
- **Nothing on the destination is ever deleted.** A file whose source changed
  is reported; with `--update-changed` the old copy is MOVED to the versions
  folder first, never overwritten in place. The one exception is a new file
  identical byte for byte to the old one, which takes its place: nothing is
  lost, and the versions folder only holds real old versions. The only file the
  tool removes is its own temp file (`*` + `.pbtmp`): after a failed copy or a
  crash, and the one it writes to test that the destination still takes
  writes (`DEST_CHECK_AFTER_ERRORS`). Nothing already in the versions or the
  orphans folder is ever replaced either: a name taken there (two runs started
  in the same second) gets ` (2)` *(v0.13.2)*, and a moved file never lands
  on a file that is already at its new place. The backup ID file is written
  once and never replaced, also when it cannot be read for a moment
  *(v0.13.2)*. The one folder the tool removes is one left empty by its own
  move inside the backup ([reruns_and_moves.md](reruns_and_moves.md#moved-and-renamed-folders)).
  **The exception is the mirror** (`mirror_backup.py`), whose job is to remove
  from the backup what is gone from the source: its own rules are
  [at the end of this page](#the-mirror-mirror_backuppy).
- **Fail closed.** Not enough space, a destination drive that is not
  connected, an unreadable or damaged catalog (checked every time it is
  opened, a dry-run and a verify too, *v0.13.1*; also a catalog row whose
  path would lead outside the backup, *v0.13.2*), a closed stdin without
  `--yes`, a destination inside a source (or the reverse) — as typed or where
  it really is, through a junction, a symbolic link or a `subst` letter
  *(v0.13.2)* —, a plan that would write into the backup's own `_photo_backup`
  folder (a backup copied with its root as `--base`, *v0.13.1*), a
  `--space-margin-gb` that is not a number or is negative *(v0.13.2)*, a link,
  a junction or another reparse point inside the backup on the way to one of
  its files — it could lead into a source, and nothing is written, moved or
  deleted through one *(v0.14.0-rc2)* —, a `Tiff` profile without jxl-photo
  or with a distance that is not a number of 0 or more *(v0.14.0-rc2)*: the
  tool aborts BEFORE the first byte is copied, exit code 2. Nothing to copy
  needs no space: a full disk never stops a run with nothing new
  *(v0.14.0-rc2)*.
- **Nothing is skipped silently.** A folder or file that cannot be read is
  logged, counted on the console (`Errors: N ... NOT backed up`) and makes the
  run exit 1 — a dry-run too. Every file under a source is either in the plan
  or in exactly one line of the summary's `Not copied:` *(v0.12.1)*, and the
  links and junctions it does not follow are counted and logged. A Capture One
  cache folder that is itself a link or a junction (a cache moved to another
  disk that way) is named on the `Capture One cache` line —
  `(+ N cache folders that are links to another place, not followed)` — and
  logged as `CACHE LINK not followed <path>`, so `links/junctions not
  followed` counts only folders whose content is not backed up *(v0.13.2)*;
  with `--c1-cache` it is a plain link again. A file whose name Windows opens
  as a device (`NUL`) is an error of the plan (`ERROR name ...`, exit 1), no
  longer a traceback that stops the run *(v0.13.2)*. The exception
  is a folder skipped by its name (`SYSTEM_SKIP_FOLDERS`, `--exclude-folders`),
  at any depth and in every mode *(v0.12.1)*: it is never listed (a `System Volume Information` cannot be read, and a
  folder may be excluded because it gives errors), so its files are not
  counted; the folder itself is named (`folders skipped by name` in the
  summary, `SKIPPED FOLDER <path>` in the log).
- **A source that cannot be read is not a source that is gone** *(v0.12.1)*.
  A backup file counts as "source gone" only with proof: the system answered
  "does not exist" and the nearest folder above that does exist could be
  listed. Access denied, a drive that is not connected, a share that does not
  answer: `source_unreadable`, never moved by `--move-orphans` or by the move
  detection. A file of another source (a second drive with the same folder
  names) is `other_source`. A run with read errors moves no orphan at all. The
  folders above are asked about themselves, never where a link leads: behind a
  junction whose target is not connected, a file is `source_unreadable`,
  never "gone" *(v0.14.0-rc2)*.
- **A folder left empty is removed; a link or a junction never is**
  *(v0.14.0-rc2)*: Windows takes a junction away with `rmdir` even when what it
  leads to is full, so a folder that is a link or a reparse point stops the
  clean-up there.
- **A TIFF kept as JXL is the one copy that is not byte for byte**
  *(v0.14.0-rc2)*, and only in a line whose `Tiff` column (or `--tiff`) names
  a profile. Each JXL is kept only with proof: jxl-photo checked its own
  round trip, the JXL's `jxlphoto-srcsum` mark is the SHA-256 photo-backup
  took of the TIFF, a multipage TIFF came back whole, and the TIFF did not
  change meanwhile. Anything else copies the TIFF as it is (exit 1): nothing is
  ever left without a copy. A TIFF copy already in the backup leaves only
  after its JXL is in the backup and in the catalog, to `versions\` (the
  mirror: its trash). An export is never converted
  ([tiff_to_jxl.md](tiff_to_jxl.md)).
- A copy is written to a temp file next to its final name, verified, then
  `os.replace`d — a crash never leaves a half file under the final name.
- **A destination that stops taking writes stops the backup** *(v0.12.1)*.
  After `DEST_CHECK_AFTER_ERRORS` failed copies in a row (a USB disk pulled
  out, a full disk) the tool writes and removes a test file of its own on the
  destination. If that fails too, it stops reading the source, records what
  was already copied, prints the normal summary with a `STOPPED:` line (why,
  and how many files were left to copy) and exits 1. If it works, the backup
  goes on. A catalog that can no longer be written (the disk went away with
  rows still waiting for their commit) stops the copy the same way: the summary
  adds a `CATALOG:` line with the number of copied files that have no row yet.
  They keep their final names, and the next run adopts them by their size and
  date instead of copying them again; `--move-orphans` moves nothing in that
  run.

## The mirror (`mirror_backup.py`)

The mirror removes from the backup what is gone from the source — the one
exception to "nothing on the destination is ever deleted". Every rule in full:
[mirror.md](mirror.md).

- **Only what it wrote itself:** a file with a catalog row that still has that
  row's size and date. A file it did not write, or that was changed in the
  backup, is never removed, and its changed copy goes to `versions\`.
- **Only with proof:** "gone" is proven as for the orphans (the folder above
  could be read); a source that cannot be read keeps its files. A `disk` line
  keeps what is still on the disk; only a `selection` line removes what it no
  longer selects.
- **Only its scope:** the scoped mirror never touches what came from folders
  that are not in its run; only `--total` does, after the typed number.
- **Nothing behind a link** the backup does not follow; **nothing in a line
  with read errors** (without a manifest: in a run with read errors).
- **Only what the summary showed:** each source is asked again right before;
  what changed reason stays, what appeared later waits for the next run.
- **Through a trash:** what leaves and the old copy of what is replaced go to
  `_photo_backup\trash\<run>\` for `MIRROR_KEEP_DAYS` days; nothing in it is
  deleted before that date except after a typed number (`--empty-trash`, the
  space question). Only folders named like a run are deleted, by their real
  path; a link inside is never followed; a trash or `_photo_backup` that is a
  link stops every real run (exit 2).
- **Typed numbers:** a line over `MIRROR_MAX_DELETE_PERCENT`, a total mirror,
  deleting for good to make room and emptying the trash need the number of
  files typed; anything else cancels and changes nothing. `--unattended`
  never asks: those cases stop the run (exit 2), nothing changed. The limit
  counts what a line removes **and** what of ours it replaces (a program that
  rewrites every file in place — ransomware — sends every old copy to the
  trash too), leaving out the edit files every edit rewrites
  (`AUTO_UPDATE_EXTENSIONS`) *(v0.14.0-rc2)*.
- **The question is the real run's** *(v0.14.0-rc2)*: the menu runs the
  mirror once, without `--yes`, and the mirror asks right below its own box,
  with the numbers of the run that changes the backup — never a dry-run's
  answer carried over to a later run.
- **An old copy that cannot be read is never replaced nor moved**
  *(v0.14.0-rc2)*: the copy of the new one fails instead, and the old one stays.
- **One run at a time** (`_photo_backup\mirror.lock`); a folder is removed only
  when its removal left it empty, never inside `_photo_backup`.
