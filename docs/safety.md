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
- **Fail closed.** Not enough space, a destination drive that is not
  connected, an unreadable or damaged catalog (checked every time it is
  opened, a dry-run and a verify too, *v0.13.1*; also a catalog row whose
  path would lead outside the backup, *v0.13.2*), a closed stdin without
  `--yes`, a destination inside a source (or the reverse) — as typed or where
  it really is, through a junction, a symbolic link or a `subst` letter
  *(v0.13.2)* —, a plan that would write into the backup's own `_photo_backup`
  folder (a backup copied with its root as `--base`, *v0.13.1*), a
  `--space-margin-gb` that is not a number or is negative *(v0.13.2)*: the
  tool aborts BEFORE the first byte is copied, exit code 2.
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
  names) is `other_source`. A run with read errors moves no orphan at all.
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
