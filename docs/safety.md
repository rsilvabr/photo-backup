# Safety guarantees

The README keeps one line for each; here is every rule, with the version that
added the newer parts. The restore has its own guarantees
([restoring.md](restoring.md)), and so does the Capture One force restore
([captureone.md](captureone.md#force-restore-and-undo-v070)).

- **The source is READ-ONLY.** No code path opens a source file for writing,
  renames, deletes, touches or changes attributes on anything under a
  `--source`.
- **The normal backup deletes only four things, each with proof or a person
  (261126, keep-days):** (a) a run folder of `versions\` or `originals\` past
  its `VERSIONS_KEEP_DAYS` / `ORIGINALS_KEEP_DAYS` (never when it is `never`,
  the default); (b) what a person chose when space was short, after
  typing the number of files; (c) with a `*_KEEP_DAYS` of `0`, a file at the
  moment it would be set aside and the run folders already in that folder, after
  the typed number, and never more files of a kind than that number (anything
  more is moved, and the next run asks for it); (d) a JPEG copy whose JXL gives its very bytes back (the copy
  itself read: its SHA-256 = the JXL's `source_sha256`). Never in a dry-run,
  `--verify` or `--analyze`; never without a person for (b) and (c) (`--yes`
  stops, exit 2, nothing changed); never through a link (a store, `orphans\`
  with `--move-orphans` or `tools\` that is a link stops a real run before the
  first byte); only folders named like a run stamp, only by their real
  path. When the space check counted on what is deleted first, the disk is asked
  again after deleting it: not enough, exit 2 before the first byte. The
  mirror's trash folders are the mirror's: the normal backup lists them and
  never deletes in them. Every run shows what is set aside and when it goes
  ([what the backup sets aside](reruns_and_moves.md#what-the-backup-sets-aside-and-for-how-long)).
- **Otherwise nothing on the destination is deleted.** A file whose source changed
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
  A JXL group the manifest no longer wants (`Tiff` / `Jpeg` say copy now) goes
  to the versions folder (the mirror: its trash) only after the plain copy of
  the same file is in the backup (UP_TO_DATE, or recorded by this run) and
  holds the very bytes the JXL was made from (its SHA-256 is the catalog's
  `source_sha256`); a failed or skipped copy, or one without that proof, keeps
  it *(v0.14.0)*. A JXL group proven to be of ANOTHER version of the file (the
  file's size differs from the one it was made from, or the bytes of the source
  -- copied by this run, adopted by their hash, or our row -- differ) is the old
  copy of a changed file: with `--update-changed` (the mirror: always) it goes
  to the versions folder as an old version, without it it waits (`UNDO WAITS`);
  a backup file of another program with other bytes proves nothing and keeps
  it (`UNDO SKIPPED`) *(final review 261126, SF1)*. When that plain copy is a
  TIFF whose own copy is kept in `originals\`, it is renamed back instead of
  copied only with proof that it is the source as it is now (its row, the
  source's size and date, the SHA-256 the JXL was made from, no link or
  junction on the way to it and its real path its place in the backup: a
  junction there could make a source file "the kept file") and with nothing
  at its place; its rows change in the same step, and any failure leaves it
  where it was and copies the TIFF from the source -- except a file that
  appeared at its place, which is never replaced (left as it is, exit 1)
  *(v0.14.0-rc9)*. With `--reconvert-older`, a TIFF whose new JXL fails keeps
  its old JXL while that is still whole in the backup; once part of it has
  left, the TIFF is copied as it is; if that copy fails too, the TIFF has no
  copy in the backup's tree until the next run (the old JXL is in `versions\`
  or the mirror's trash; a mirror with no trash, or a normal backup with
  `VERSIONS_KEEP_DAYS = 0` typed, deleted it), exit 1. With
  `--adopt-by-hash` the backup sets the date of a file it did not write (no
  row, never through a link) to the source's, and only when both were read,
  their SHA-256 is the same and neither changed since; its bytes are never
  written. A read-only file whose date would have to change is left as it is
  in that run: not adopted, not replaced, never made writable (its bytes are
  the source's) *(v0.14.0; [reruns_and_moves.md](reruns_and_moves.md#a-backup-made-by-another-program))*.
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
  or in exactly one row of the summary's `Not copied` table *(v0.12.1)*, and the
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
  a profile. Each JXL is kept only with proof: jxl-photo ended without an
  error, **jxl-photo decoded every page and compared it with the TIFF's before
  the page took its name** *(v0.14.0-rc4, jxl-photo 2.31.0-rc1:
  [JP-20](jxlphoto_contract.md#jp-20--every-page-is-checked-before-it-takes-its-name);
  up to jxl-photo 2.30.x no pixel was compared)*, the JXL's `jxlphoto-srcsum`
  mark is the SHA-256 photo-backup took of the TIFF, a multipage TIFF came
  back whole, and the TIFF did not change meanwhile. Anything else copies the
  TIFF as it is (exit 1): nothing is ever left without a copy. A TIFF copy
  already in the backup leaves only after its JXL is in the backup and in the
  catalog, to `versions\` (the mirror: its trash). A TIFF export is never
  converted ([tiff_to_jxl.md](tiff_to_jxl.md)). *(rc6)* A copy as it is that a
  new copy as it is replaces keeps its name until the new one is written, on
  the disk and checked, as every changed file; a source edited since its copy as
  it is was made is a changed file (only `--update-changed`, or the mirror,
  replaces it). A page of a multipage TIFF never takes the name of another file
  of the backup (`x_page1.tif.jxl` of `x.tif` beside an `x_page1.tif`): that
  TIFF is copied as it is instead (exit 1), and both files have a copy.
- **The conversions never run inside the backup or a source** *(v0.14.0-rc4)*:
  each one has a folder of its own in `JXL_WORK_DIR` (or `--work-dir`,
  *rc5*) + `JXL_WORK_FOLDER` (the system's temp folder by default; a work
  folder inside or above a source or the backup is refused, exit 2 — the
  default one too), jxl-photo's own temp files inside it *(rc5)*, and one writer copies
  each finished group into the backup: every file of the group is written next
  to its final name and checked by its SHA-256 before an old file leaves or any
  name changes. Should a name then fail to change (a file in the way that
  another program holds open, a power cut), the pages already renamed stay
  without a catalog row; the next run moves them aside to `versions\` and makes
  the group again — nothing is lost. Ctrl+C waits for a group's names and rows,
  a matter of milliseconds *(rc5)*. The only folders removed there are this
  tool's own: this run's `pbconv\<process number>` at its end, and the one of a
  backup that no longer runs — only a folder that carries the run's mark file,
  never a link; the mark goes last, so what could not be removed yet is removed
  by a later run *(rc5)*. A work folder that cannot be made copies every file as
  it is (exit 1). At the end of a run the empty `pbconv` folder itself goes too
  (`os.rmdir`, never a link; one that still holds anything stays) *(rc6)*. A `%`
  in the work folder's path is refused before anything is read, exit 2: jxl-photo
  cannot keep its temp files there (JP-22); so is a work folder so deep that
  jxl-photo's temp folders would be 240 characters or more (Windows hangs a
  program whose temp folder has 260 when it starts another one).
- **Ctrl+C stops at a safe point, in seconds, and says so** *(v0.14.0-rc5)*:
  at once `Ctrl+C: stopping at a safe point...`; the conversions running are
  ended (with what jxl-photo started), never waited for — their result would be
  thrown away; the file being written into the backup is finished or removed; a
  group's names and catalog rows go together; the catalog is written. A stop
  that takes longer says `still stopping: ...` every few seconds. The last line,
  `Stopped (Ctrl+C) ... the disk can be removed`, comes only once the catalog
  is closed. A second Ctrl+C stops waiting (the next run removes what is left
  in the work folder). A window closed or a power cut loses nothing either: the
  next run finishes or redoes what was half done.
- **A JPEG kept as JXL comes back byte for byte** *(v0.14.0-rc3)*: jxl-photo's
  lossless transcode only (`--force-transcode`), and the JXL is kept only after
  the JPEG rebuilt from it (by libjxl's djxl, *rc4*; without it by jxl-photo)
  had the SHA-256 photo-backup took of the JPEG; anything else copies the JPEG
  as it is. The restore checks that SHA-256 again before the JPEG takes its
  name.
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
- **Through a trash, three folders (261126, keep-days):** what leaves goes to
  `_photo_backup\trash\<run>\` for `MIRROR_KEEP_DAYS` days, the old copy of
  what is replaced to `trash_versions\<run>\` for `MIRROR_VERSIONS_KEEP_DAYS`,
  a TIFF copy a JXL replaces to `trash_originals\<run>\` for
  `MIRROR_ORIGINALS_KEEP_DAYS` (a JPEG copy whose JXL gives its very bytes
  back, the copy read, is deleted); what it did not write goes to `versions\`. Nothing in them
  is deleted before its date except after a typed number (`--empty-trash`, the
  shared space chooser). Only folders named like a run are deleted, by their
  real path; a link inside is never followed; a trash folder, `versions\`,
  `tools\` or `_photo_backup` that is a link stops every real run (exit 2). A
  catalog another program holds while the mirror deletes its trash stops it
  there: nothing more leaves the backup, the `MIRROR STOPPED` box says what was
  deleted (exit 1).
- **Typed numbers:** a line over `MIRROR_MAX_DELETE_PERCENT`, a total mirror,
  deleting for good to make room, a trash folder with `0` days that would
  delete something, and emptying the trash need the number of files typed; anything else cancels and changes nothing. A
  folder with `0` days then deletes at once at most the number typed for it;
  anything more goes to that folder, and the next run asks for it. `--unattended`
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
