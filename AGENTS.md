# photo-backup — agent notes

A selective backup of a photo archive: for every folder that holds an export
folder (`_EXPORT`, `Lightroom_Export`, ... — any name containing "export"),
copy the exports plus the originals of the exported photos (RAW, camera JPEG,
NX Studio TIFF, sidecars), the Capture One settings and the NX Studio
`NKSC_PARAM`, mirroring the folder tree on the destination. A later run can
verify that the backup still matches.

## Scripts
Same layout as the sibling `jxl-photo`: `photo_backup.py` is the entry point
(the menu), each job is a standalone backend script that also works on its own
from the command line. A future job (e.g. a full mirror) is a new
`<job>_backup.py` run by the menu — never folded into `selective_backup.py`.

- `selective_backup.py` — the backend: planner, copy, catalog, verify. Every
  safety rule below lives here. Since v0.14.0-rc2 it also keeps a TIFF as JXL
  (`docs/tiff_to_jxl.md`): `_Planner._add` gives a TIFF of a line with a
  profile (`FolderRules.tiff` / `--tiff`; never under an export-named folder)
  the rel `<name>.tif.jxl` and `convert`; `_convert_status` judges it from its
  group (catalog schema 2, table `converted`: one row per JXL, with the
  TIFF's size/date/SHA-256, page, pages, tool, settings); `_copy_all` leaves
  it out of the copy loop and `_convert_all` runs jxl-photo
  (`_convert_one`, a thread each: a JPEG pool of `_jxl_workers()` — one per
  physical core, `_physical_cores` — and a TIFF pool of `_jxl_tiff_workers()`,
  v0.14.0-rc4; a JPEG's proof is libjxl's djxl from stdin, `_rebuild_jpeg`,
  JP-18, jxl-photo when `DJXL` finds none). Since rc4 the conversions run in
  `_work_root(work_dir)` — `--work-dir` (rc5; `None` = `JXL_WORK_DIR`, ""
  = the system's temp folder; the menu remembers one per computer, `work_dir`
  in its config, and `_run_child` passes it to the backup scripts) +
  `JXL_WORK_FOLDER\<pid>` with `_WORK_MARK` in it; each conversion's folder
  holds a `temp` folder (`_conversion_temp`) that `_jxlphoto_env(log_dir,
  temp_dir)` gives jxl-photo as TMPDIR/TEMP/TMP (rc5: its own temp files never
  reach the system's temp folder); `--no-convert` (rc5; the menu's
  `no_convert` per computer) turns the conversion off for a run:
  `_without_conversion` keeps a whole JXL group of an unchanged file
  (`FileStatus.settings_only`) and a current copy as they are, and
  `_copy_without_conversion` copies the rest with `_copy_as_is` (not a
  failure, no jxl-photo, no work folder); never inside or above a
  source or the backup, the default too (`_work_dir_errors`, which also
  refuses a `JXL_WORK_FOLDER` that is not one plain name); a dead run's folder
  removed only when it carries the mark (`_ours_to_sweep`, `_pid_alive`); a
  work folder that cannot be made is a `problem` (every file copied as it is);
  rc2/rc3's `_photo_backup\converting` is removed (`_OLD_WORK_FOLDER`) — and
  each pool runs at most twice its size ahead of the writer (`submit_more`).
  The main thread is the one writer of the backup's disk: `_place_group` puts
  each proven group in place — by a copy when the work folder is on another
  volume (`_jxl_copy_needed`: `st_dev`, not the letter; every file to
  `<final>.pbtmp`, fsync, read back against its SHA-256 by `_write_jxl_temp`,
  THEN the old group leaves and the names change; a rename that fails midway
  leaves the pages already renamed without a row, which the next run moves
  aside), else by a rename —, then a TIFF copy it replaces is retired
  (`replaces` → `keep_old_file`: versions, or the mirror's own); a failure is
  `_copy_as_is`. `_keep_tools` keeps photo-backup, jxl-photo
  and `KEEP_TOOLS_EXTRA` in `_photo_backup\tools` on every real run. jxl-photo
  always runs through `_run_jxlphoto` (`_jxlphoto_env`: `JXLPHOTO_LOG_DIR` =
  the conversion's own folder, its log copied to our `Logs\jxl-photo` by
  `_keep_jxlphoto_log` only when it failed, the restore's straight there;
  `JXLPHOTO_SETTINGS` empty = no settings overlay, JP-21;
  `JXLPHOTO_NO_UPDATE_CHECK`; `PYTHONPYCACHEPREFIX` = our `Logs\pycache`:
  never a `__pycache__` beside jxl-photo, JP-12); `_jxlphoto_said` puts
  jxl-photo's ERROR lines (else its last lines) in `CONVERT FAILED`; since rc4
  `_jxlphoto_argv` runs a command's own script as a module (`_JXLPHOTO_BOOT`,
  JP-19: its bytecode is kept, a start is 0.08 s instead of 0.3-0.5 s) and
  `jxl_photo.py` with `-B` only when that script is not there. **What
  photo-backup needs from jxl-photo is a contract,
  `docs/jxlphoto_contract.md` (仕様書, JP-1 to JP-22):**
  the two command lines live once, in `_jxlphoto_encode_args` and
  `_jxlphoto_decode_args`; `_jxlphoto_problem` refuses a jxl-photo older than
  `JXLPHOTO_MIN_VERSION` (2.31.0-rc3: refuses a `%` temp folder, JP-22; 2.31.0-rc1
  was the first that checks every page it encodes, JP-20; the restore passes its own `JXLPHOTO_RESTORE_MIN_VERSION`,
  2.30.0, so a backup comes back with the jxl-photo it keeps) or one that
  does not say its version; a `Tiff` profile without djxl on PATH
  (`_djxl_on_path`) is refused before reading, exit 2; each failure message
  names its item (`(JP-5)`). Asking jxl-photo something new = a new item in
  that page, a test in the contract test, its number in the message, and
  `JXLPHOTO_MIN_VERSION` raised to the first jxl-photo that passes (measured
  by running `tests/test_jxlphoto_contract.py` on jxl-photo's tags). The
  contract test runs only with `PHOTOBACKUP_JXLPHOTO` set (a real jxl-photo;
  numpy + tifffile; djxl on PATH). **v0.14.0-rc6:** `_work_dir_errors` refuses
  a `%` in the work folder's path (`_work_folder_name_problem` and `_work_root`
  too: JP-22, ExifTool reads a `%` in a file name as a format code);
  `_jxlphoto_said` adds the continuation lines under an ERROR line (cjxl's own
  message; no time) or argparse's `error:` line, at most
  `_JXLPHOTO_SAID_CHARS`, never the WARNING recap; a TIFF or JPEG that
  jxl-photo could not convert in `CONVERT_GIVE_UP_AFTER` runs in a row (catalog
  table `not_converted`, no new schema version; `_Catalog.record_not_converted`
  / `forget_not_converted`; same size, date, jxl-photo and settings) stays its
  copy as it is: `_not_converted_again` (`FileStatus.given_up`, `NOT CONVERTED
  AGAIN`, not a failure; `--retry-conversions` in the three scripts turns it
  off); `_remove_empty_work_parent` removes the empty `JXL_WORK_FOLDER` itself
  (`os.rmdir`, never a link); `_start_output` writes UTF-8 into a pipe or a
  file (unless `PYTHONIOENCODING` is set), and every script uses it. From the
  review before rc6 (`AI_tools/261122_Fable_review_rc6.md`,
  `tests/test_rc6_review.py`): a failure counts towards `CONVERT_GIVE_UP_AFTER`
  only with proof it is the file's — `_ConvertFailed.counts` (False: a source
  that cannot be read or changed meanwhile, a converter not started) and
  `_record_not_converted` at the end of `_convert_all`, only for a kind (JPEG,
  TIFF) this run converted at least one of (`NOT COUNTED` else); jxl-photo's
  temp folder stays under `_JXL_TEMP_MAX_CHARS` (240: Windows hangs a program
  whose TEMP is 260+ characters when it starts a child) — `_work_dir_errors`
  / `_work_root` refuse a work folder that deep, the restore takes its own
  temp folder beside a deep target; `_copy_as_is` retires the old copy in
  `before_final`, never before the copy; `_place_group(..., taken=)` refuses a
  page whose name is another planned file's or another source's JXL
  (`_Catalog.converted_source`); `_convert_status` makes our copy as it is of
  a changed source `ST_CHANGED` (with `replaces`; the mirror's review skips it
  in its changed loop, its "retire" item covers it); `_convert_all` logs one
  `TIMING conversions:` line (`_Conversion.seconds`, `_place_group(...,
  timing=)`).
- `manifest_backup.py` — per-folder modes, read from `backup_manifest.csv`
  (gitignored; `backup_manifest.example.csv` is versioned) and
  `backup_modes.csv` (versioned, UTF-8; `--modes` reads another). The code
  never names a mode, `_OUTTAKES` or `RAW SCANS`: those are settings of the
  CSV. It builds one `selective_backup._Planner` per manifest line (with
  `FolderRules`, the most specific line first) and hands the joined `Plan` to
  `selective_backup._run_backup(..., plan_fn=...)` — the copy, catalog, space
  check, versions, moves and orphans are the selective backup's own.
  `--analyze` plans every mode through the same `_Planner` and writes
  `<stamp>_analysis.csv`. `--verify` hands the same joined plan to
  `selective_backup._run_verify(..., plan_fn=...)`: only the manifest knows
  what each line selects. Never duplicate a copy or catalog routine here.
- `mirror_backup.py` — the mirror (v0.14.0): the backup follows a manifest
  (or `--source`): what changed is replaced, what is gone is removed, through
  a trash. It hands `selective_backup._run_backup(..., plan_fn=...,
  mirror=MirrorJob)` the same joined plan as `manifest_backup.py`; the job is
  called at fixed points (`review` before the summary, `space_lines` /
  `free_space`, `confirm`, `proceed_prompt`, `remove` before the copy,
  `old_copy` per changed file, `finish`). All of its policy lives in this
  script; `selective_backup.py` only has the call points and the parameters
  `listings=` (`_file_statuses`), `keep_old=` (`_handle_old_copy`),
  `old_copy=` (`_copy_all`), `freed=` (`_summary_lines`) — `None`/`0` = the
  normal backup, unchanged. Its lock functions are a deliberate copy of
  `mobile_backup.py` 0.1.4's (that script stays independent). The manifest's
  optional `Mirror` column (`disk`/`selection`) is read by
  `manifest_backup.read_manifest` and ignored by `manifest_backup.py` itself.
  Its screen: the verdict first, the mirror's box last (right above the
  question), tables label | files | size, concrete dates, the trash folder
  always printed; keep it that way (the owner reads it under pressure). The
  menu runs it ONCE, without `--yes`: the mirror asks with the numbers of the
  run that changes the backup (v0.14.0-rc2, finding 5 of the rc1 review; never
  a dry-run confirmed in the menu and then `--yes`). `keep_old_file` is its
  home for an old JXL group or a TIFF copy a JXL replaces: ours (a row, or no
  row and still the source TIFF's size and date: `adoptable`) to the trash,
  anything else to `versions\`.
- `photo_backup.py` — interactive menu + presets (`--run-preset`). It
  never copies by itself: it builds an argv and runs `selective_backup.py` (or
  `restore_backup.py`, via `run_restore`) as a child process (`run_backend`).
  It reads the backend's defaults by importing
  it (`_setting`) and always passes every selection option explicitly — a
  default repeated as a literal in the menu would silently override the
  owner's edit of `selective_backup.py` (the bug class of jxl-photo round 43).
  Its own settings (`CONFIG_PATH`, `VERIFY_MODE`, `MAX_ONLY_CHARS`,
  `FORMAT_ALTERNATIVES`, `RESTORE`, ...) are at its top; tests set
  `CONFIG_PATH` to a temp file. Every setting at the top of every script is
  named in its docs (`README.md` + `docs/`, or `README_mobile_backup.md`):
  `tests/test_docs.py::test_docs_cover_the_settings` (v0.13.2), with
  `NOT_SETTINGS` for the constants that are not settings.
- `restore_backup.py` — restores files out of a backup (`--to` /
  `--to-original`). A `%` in the restore's folder next to the target gives
  jxl-photo a temp folder of its own in the system's temp folder (`pbrestore_`,
  JP-22, removed after; refused when that one has a `%` too or is inside the
  backup); so does a work folder of 240+ characters (`_JXL_TEMP_MAX_CHARS`).
  The groups' results reach the main thread through a queue fed by
  `add_done_callback` (rc6 review, S2: no scan of every pending future per
  item). The menu's "try them again" is a marker file beside its config
  (`photo_backup._retry_marker`), never a config key: the menu writes its
  config from memory. A backend may import the catalog and the copier from
  `selective_backup.py` (`import selective_backup as sb`); never duplicate
  the catalog format or the copy routine — they exist once. A TIFF or JPEG
  kept as JXL is one entry (`_JxlGroup`): its JXL files are copied (hash
  checked) under jxl-photo's own names into a folder of the restore NEXT TO THE
  TARGET, decoded there (TIFF: `_jxlphoto_decode_args`, `RESTORED_FROM_JXL`;
  JPEG: `_jxlphoto_rebuild_args`, and the rebuilt file must have the catalog's
  `source_sha256` → `RESTORED`, else `ERROR (JP-16)`), and the one file takes the
  target's name with `_rename_no_clobber`. Since rc4 a JPEG with djxl skips the
  copy: `_rebuild_jpeg_with_djxl` reads its JXL once from the backup (size and
  catalog SHA-256 checked), rebuilds it in memory (JP-18) and writes it next to
  the target; a JPEG needs no jxl-photo then (`NEEDS_JXLPHOTO` only without
  both). The groups run on two pools (`_run_group`: every error a status;
  JPEG `_jxl_workers()`, TIFF `_jxl_tiff_workers()`), their results kept by
  index, while the plain copies go on in order on the main thread; Ctrl+C
  cancels what has not started and writes the CSV. `--convert-back all|lossless|none`
  (v0.14.0-rc3; `--keep-jxl` = none): `lossless` brings back a group only when
  its `lossless` flag is 1 (every JPEG; a TIFF per JP-17), the others are
  restored as their JXL (`RESTORED_AS_JXL`). `_find_jxlphoto` falls back to
  the newest jxl-photo kept in the backup.
- `captureone_backup.py` — keeps what Capture One itself holds (profiles,
  styles, defaults, ...) in `<dest>\_captureone`, a backup root with its own
  catalog, so `selective_backup.py`'s orphan logic never sees those rows. Rows
  are `<label>\<rest>` and the restore resolves the label on the CURRENT
  machine (a new PC has another user name). Numbered folders change with
  Capture One versions (`Styles50`, `Recipes165`): never name one exactly,
  use the `*` patterns in its settings. Login data, cookies and the licence
  stay out. `RESTORE_CAPTUREONE.cmd` is generated into the backup for a PC
  without Python: robocopy `/XC /XN /XO` only, no `call` and no `( )` block
  around a path (both break on `%`/`)` in the backup's path). Tests point
  `PHOTOBACKUP_C1_*` (conftest) away from the real Capture One folders.
  `--force` is the ONE path that replaces a target file: the current file is
  copied to `%LOCALAPPDATA%\CaptureOne_old\old_<time>` and journaled before an
  atomic swap, with Capture One known to be closed (cannot tell = refuse).
  Without a catalog hash, a file is not replaced. Factory profiles need
  `--force-installed`; a monitor calibration (`mntr` + `vcgt`, or a file that
  cannot be read as ICC) never. `--revert-last` picks only `old_*` not ending
  in `_not`; its own snapshots are `revert_old_*`, same journal format.
  The old copy of a changed file keeps its name until the new one is on the
  disk: the backup uses `selective_backup._handle_old_copy` / `before_final`,
  never a routine of its own. `--force` journals `added` only after its copy
  of that file succeeded (a target that appears meanwhile is someone else's and
  `--revert-last` must never take it out); `--revert-last` does the same. A
  catalog row without a hash is filled by `--verify` only from a source with the
  row's size and date (`HASH_FILLED`), and its restore checks the size
  (`RESTORED_UNVERIFIED`). v0.13.1: the `.cmd` and the map are written from the
  catalog's rows (what the backup holds), never from this run's plan alone; the
  `.cmd` has `/XF *` + `TEMP_SUFFIX` on every robocopy line and gives
  `ELEVATED_ARGUMENT` to the instance it elevates. The journal records each
  file's absolute `target`; `--revert-last` undoes it there only when
  `_journal_target_problem` accepts it (v0.13.2, T3 of the 261112 sweep: the
  journal lives where any program of the user writes, and the revert may run as
  administrator): this run's root of the label, or for `Installed` an
  `INSTALL_DATA_FOLDERS` folder of an install folder (this run's, or one
  holding the program or every data folder: N10), and the real path of the
  target is its root's real path + rest (no link below the root). One bad line
  = exit 2, nothing changed; targets outside this run's roots are listed before
  the question. `_Snapshot._open` creates the journal with the `old_` folder:
  the newest `old_*` is always the one judged (`_last_snapshot` no longer skips
  one without a journal); no journal or an empty one = exit 2 ("Nothing to
  undo"; rename it `_not` to reach the previous one). A read-only target is
  made writable only in `before_final` (`_writable_before_swap`), after the
  current file is in the snapshot. Links are listed in `plan.links` and
  reported, except where `_walk` would not go anyway (`_link_left_out`); what
  could not be read is in `plan.unread` and is never "gone". The overlap checks
  (backup × Capture One folders, `--to` × backup) use `sb._overlaps`. The
  dry-run checks an existing catalog read-only (`_catalog_problem`). The map
  and the `.cmd` go through `_write_ours` (the `.cmd` first; an `OSError` is a
  message + exit 1, never a traceback after the copy). The backup's statuses
  come from `sb._file_statuses`; its copy stays one `sb._copy_file` per file
  (`test_n8_*` injects failures there; batching was left out on purpose).
- `mobile_backup.py` — mirrors the `JXL_mobile` folders to another disk as
  one album per event, numbered in shot order (exiftool); its own version
  (`0.1.x`) and its own README (`README_mobile_backup.md`). It is the one
  script that deletes on its destination: see its safety rule below. It saves
  its state right after the renames and every `STATE_SAVE_EVERY` copies, holds
  `run.lock` in its state folder while it runs, and keeps `state.json.bak` (the
  state of the last finished run).

## Safety rules (these are the product)
- **The source is READ-ONLY.** No code path may open a source file for
  writing, rename, delete, touch or set attributes on anything under a
  `--source`. Tests snapshot the source tree (path, size, mtime, hash) before
  and after every run and must find it identical. A destination is compared
  with every source as typed AND where it really is (`_overlaps`, with
  `os.path.realpath` through `_real`; v0.13.2): a junction, a symbolic link or
  a `subst` letter cannot lead the backup into a source. Use `_overlaps` for
  every such safety check (also restore target × backup); `_is_under` stays
  string-only for the catalog's own paths.
- **Nothing on the destination is ever deleted** — except by `mirror_backup.py`
  (v0.14.0), see its rule below; its trash is the one folder inside
  `CATALOG_FOLDER` where files are deleted. A file whose source changed
  is reported; with `--update-changed` the old copy is MOVED to the versions
  folder first, never overwritten in place. A new file identical byte for byte
  to the old copy takes its place instead: `_handle_old_copy` compares the old
  copy with the hash of the bytes really copied (`before_final`, the last step
  before the final name), never with what the source held earlier, and the
  comparison only decides the versions folder, never whether to copy. The only
  file the tool removes is
  its own temp file (`*` + `TEMP_SUFFIX`): after a failed copy, and the one
  `_dest_accepts_writes` writes in `CATALOG_FOLDER` to test the destination
  after `DEST_CHECK_AFTER_ERRORS` failed copies in a row (a test that fails
  stops the copy and the reading, exit 1). The one
  exception: a folder left empty by one of our own moves (a detected
  move/rename, or `--move-orphans`) is removed with `os.rmdir` — never a
  file, and never anything inside `CATALOG_FOLDER`. Nothing already in
  `versions\` or `orphans\` is replaced either: `_move_aside` renames with
  `_rename_no_clobber` and takes ` (2)`, ` (3)`, ... when a name is taken (two
  runs started in the same second share a stamp); a detected move uses
  `_rename_no_clobber` too; `_ensure_backup_id` never replaces a
  `volume.json` that is there, readable or not (v0.13.2). Never `os.replace`
  onto a name that may hold a file we did not just check.
- **`mirror_backup.py` removes only what it wrote, with proof (v0.14.0).** A
  catalogued file leaves the backup only when it is "ours" (`_ours`: the
  file still has its row's size and date) and its reason allows it:
  `source_missing` always; `not_selected` only in a `Mirror=selection` line;
  `other_source` and `outside_run` (rows outside this run's prefixes) only
  with `--total`; `source_unreadable` never. Never behind a link the plan did
  not follow (`_behind_link`), never in a line with read errors (without a
  manifest: a run with any), never what the review did not show (`remove`
  re-asks `_find_orphans` and acts only when the reason is the same). A
  changed file that is ours: old copy to the trash; not ours: `versions\`
  (`sb._handle_old_copy`). What leaves goes to
  `_photo_backup\trash\<stamp>\<rel>` (`sb._move_aside`) for
  `MIRROR_KEEP_DAYS`; a run deletes trash runs past that date
  (`_purge_trash_run`: only folders named `\d{6}_\d{6}`, only by their real
  path, never following a link; `_trash_problem` stops a real run when
  `_photo_backup` or the trash is a link). Deleting for good before the date,
  a line over `MIRROR_MAX_DELETE_PERCENT` (unless `--allow-mass-delete`) and
  a total mirror need the number of files typed (`_typed_confirmation`);
  `--unattended` never asks and stops instead (exit 2, nothing changed). One
  real run at a time (`mirror.lock`). Files it did not write are never
  removed, only counted (`stray`). Since v0.14.0-rc2 (the rc1 review,
  `tests/test_mirror_review.py`): the limit per line counts `remove` AND
  `replace` items of ours (not `AUTO_UPDATE_EXTENSIONS`), checked after both
  are known; Ctrl+C while removing or purging still calls `finish`; the trash
  expires by its day (`(now.date() - run.when.date()).days`); a link inside a
  trash run is left and is not a problem; the space options are sorted by
  what they delete; an old copy whose `_dest_stat` is `unreadable` raises
  (the copy fails, the old copy stays); a typed choice must be ASCII digits.
- **Nothing is written, moved or deleted through a link inside the backup
  (v0.14.0-rc2, finding 1 of the rc1 review).** `_run_backup` refuses (exit 2,
  before the first byte; the mirror's STOP box) when `_first_dest_link` finds
  a link, junction or other reparse point (`_is_reparse`: lstat, a path that
  cannot be asked counts as one) on the way from the backup root to the folder
  of any planned file or catalog row: a junction into a source would let a
  copy, a versions move or the mirror's trash act on source files.
  `_prune_empty_parents` never `rmdir`s a reparse point (Windows removes a
  junction whose target is full), and `_source_stat`'s walk-up asks each folder
  about itself (`follow_symlinks=False`): behind a dangling junction a file is
  unreadable, never missing.
- **A TIFF or JPEG kept as JXL needs proof, and a failure copies the file
  (v0.14.0-rc2; JPEG rc3).** A TIFF's JXL group is kept only when jxl-photo
  exited 0 (given `--verify-roundtrip`: since jxl-photo 2.31.0-rc1 every page
  is decoded and compared with the TIFF's before it takes its name, JP-20,
  rc4; up to 2.30.x it compared pixels only with `--delete-source`),
  every file's `jxlphoto-srcsum` is
  `_jxlphoto_srcsum(SHA-256 photo-backup took before)`, `jxlphoto-pages` = the
  number of files, and the TIFF's size/date did not change (`_encode_one`). A
  JPEG's JXL (no marks: they break the rebuilding) is kept only when the JPEG
  jxl-photo rebuilds from it has the SHA-256 taken before (`_transcode_one`,
  `--force-transcode`): the same bytes. Anything else is `CONVERT FAILED ...
  (JP-n)` + `_copy_as_is` (exit 1). The catalog's `converted` rows carry `kind`
  and `lossless` (schema 3; `_tiff_lossless` from the class marks, JP-17); a
  schema 2 catalog reads with kind from the extension and lossless unknown,
  and is upgraded with `ALTER TABLE` when opened to write. JPEG exports ARE
  converted (byte for byte); TIFF exports never. Order: the new group's bytes
  are on the backup's disk next to their final names (copy route, rc4) before
  the old group moves aside; then the group gets its final names
  (`_rename_no_clobber`) and rows, THEN the TIFF copy it replaces leaves (never
  before). This run's work folder (`JXL_WORK_DIR\JXL_WORK_FOLDER\<pid>`), a
  conversion's folder in it, the folder there of a backup whose process no
  longer runs (`_pid_alive`; only a name that is digits AND holds
  `_WORK_MARK`), rc2/rc3's `_photo_backup\converting`, and `.pbtmp` folders
  under `tools` are the only folders this removes (`_remove_own_folder`,
  never a link), plus the empty `JXL_WORK_FOLDER` itself
  (`_remove_empty_work_parent`: `os.rmdir`, never a link). Nothing in `tools` is replaced or deleted otherwise.
- **The restore never writes to the backup.** `restore_backup.py` opens the
  catalog read-only, never writes, moves or deletes anything under `--backup`,
  and never overwrites a target file that already exists. An empty `--to` or
  `--only` is exit 2 (v0.13.2: `""` meant the current folder / everything),
  and every target is checked against the backup as typed and where its
  folder really is (`inside_backup` → `REFUSED`).
- **Fail closed.** Not enough space, an unreadable catalog, a closed stdin
  without `--yes`, a destination inside a source (or the reverse, also
  through a link: `_overlaps`), a `--space-margin-gb` that is not finite or is
  negative (`_validate_usage`, the restore; v0.13.2): abort BEFORE the first
  byte is copied, exit code 2.
- A copy is written to a temp file next to its final name, verified, then
  `os.replace`d — a crash never leaves a half file under the final name.
  The restore uses `no_clobber=True` (`os.rename` on Windows): a target that
  appears during the restore is never replaced.
- **The order of a copy is write → on the disk → final name → catalog.** The
  backup reads ahead on a thread (`_ReadAhead`) and flushes small files in
  batches (`finish_pending` in `_copy_all`), but a file gets its final name
  only after its `fsync`, and a catalog row only after that name: a re-run
  trusts a final name with the right size and mtime. The flush runs before
  `_finish_temp` because `copystat` can make the temp read-only. The catalog
  stays on the main thread (SQLite).
- **Leaving a file out needs proof (v0.5.0).** A jxl-photo derivative is
  skipped only when its own XMP says `jxlphoto-derived:` AND a JXL of the same
  photo that says `jxlphoto-src:` is selected in the same run
  (`_drop_derivatives`, `_jxl_role`). Anything unreadable or ambiguous is
  copied. Any new "skip" rule follows the same shape: positive evidence that
  the file can be remade from something this run copies.
- **A manifest's `Originals=raw` never leaves a photo without a copy.** It
  maps to `raw_or_jpeg`: RAW + sidecars + every JPEG, TIFF, PNG, HEIC or PSD
  (`ANCHOR_EXTENSIONS`) that has no RAW of the same photo beside it (`X
  (2).JPG` is another file, not a variant of `X`), and the sidecars of what it
  keeps (`photo_stems`). `raw+jpeg` (`raw_jpeg`) adds every JPEG; `all` copies
  everything: `raw` within `raw+jpeg` within `all`, in every mode (v0.12.1,
  `tests/test_bug_sweep_round2.py`).
- **A folder not exported yet is not lost in a `+` mode.** `Unexported=yes`
  in the modes table copies a folder without an export whole (`--unexported
  copy`, per line) -- also a subfolder of a session that `LeaveOut` does not
  name (v0.13.0); a folder with an export follows the rest of the mode.
- **A session leaves out only what the table names; the rest is judged alone
  (v0.13.0).** A direct subfolder of a session (export + an original beside
  it) goes "inside sessions" only when it matches the mode's `LeaveOut`
  (`FolderRules.leave_out`; `SESSION_LEAVE_OUT`, `*`, for `selective_backup.py`
  alone and for a table without the column). Any other one keeps the
  session's own `inside` in `build`, as the subfolder of a folder with an
  export and no original beside it always did: a session if it holds an
  export, a folder without an export if not. Order: `Exclude`, `AddWhole`,
  `AddFolders`, then `LeaveOut`. A table without the column, or with `*`,
  plans exactly what v0.12.1 planned (`tests/test_leave_out.py` keeps the
  v0.12.1 plans of the invariant tree as a fixed list), and no mode copies
  less than v0.12.1. The code never names `_OUTTAKES`: the table does.
- **Leaving out by a manifest line is the owner's explicit choice, and it is
  reported.** `Skip=yes` plans nothing and `Only` keeps only those extensions
  (both in `_Planner.build`, so analysis, dry-run and copy agree); the summary
  counts what they left out. Backed-up files they no longer select stay on
  the destination as `not_selected` orphans.
- **A manifest never hides a loss.** An export folder cannot have its own line
  below another line (the line above would stop being a session and lose its
  originals), and `--analyze` exits 1 when a folder could not be read.
- **A copy carries the dates of the bytes it holds (v0.12.0).** `_finish_temp`
  sets the temp's times from `stat0` (taken when the file was read), never from
  the source as it is at that moment: a file edited after it was read must show
  as CHANGED on the next run, not as old bytes under a new date.
- **The old copy of a CHANGED file keeps its name until the new one is on the
  disk (v0.12.0).** `_handle_old_copy` only decides; the move to the versions
  folder happens in `before_final`, for every size.
- **Two source files with one destination path are an error (v0.12.0)**, never
  a line in the log only: `_Planner._add` puts them in `errors` (summary,
  exit 1). The same file planned twice stays a warning.
- **Reading the catalog never writes to the backup (v0.12.0).** A read-only
  `_Catalog` with a pending `-journal` beside it reads a private copy
  (`scratch`); only a run that may write rolls the journal back.
- **Every listed file is planned or counted once, and a bigger mode never
  copies less (v0.12.1).** Each file under a source is in the plan or counted
  exactly once under `Not copied:`: in one `Stats` counter, or, for a folder
  without an export that is not copied, in its `unexported_folders` entry (a
  new "leave out" branch gets its own counter and summary line; links/junctions
  are counted in `_list_dir`, once per planner, cache or not; a Capture One
  cache folder that is itself a link, in a run that does not copy the cache,
  goes to `cache_links` / `Stats.c1_cache_links` instead of `links`, so
  "links/junctions not followed" names only what is NOT backed up — the
  owner's archive has 429 such junctions on G:, caches moved to another disk;
  v0.13.2). The exception: a
  folder skipped by its name (`SYSTEM_SKIP_FOLDERS`, `--exclude-folders`) is
  never listed, so its files are not counted; the folder is named instead
  (`skipped_folders`, once per planner, `SKIPPED FOLDER` in the log).
  `tests/test_bug_sweep_round2.py` checks this file by file (sizes `8**k`). Mode `N` ⊆ `N+1` and `N` ⊆
  `N+`: a folder with an export inside an `AddFolders` folder is a session, and
  a file there -- loose or inside its export folders (`_filter_keeps`) -- is
  copied when either the session or the `Originals` filter keeps it, as v0.12.0
  did. `tests/test_bug_sweep.py` checks both on synthetic trees;
  a change to `_Planner.build` must keep them. The rules allowed to copy
  less are `Exclude` (the owner's explicit choice, counted) and a folder skipped
  by its name (`SYSTEM_SKIP_FOLDERS`, `--exclude-folders`), which `_list_dir`
  leaves out everywhere, inside `AddFolders` folders too (v0.12.1 round 3;
  named, not counted), plus one declared case: in an `AddFolders` folder, a
  `Cache` below the first level of a `CaptureOne` inside an export folder counts as Capture One cache (v0.12.0
  copied it; a cache is made again).
- **A source that cannot be read is not a source that is gone (v0.12.1).**
  `_source_state` says `missing` only with proof: `FileNotFoundError` AND the
  nearest existing folder above could be listed; any other error is
  `unreadable`. Orphans (`source_missing` vs `source_unreadable` /
  `other_source`, the latter for a row whose `source_path` is under no
  `--source` of this run), the move detection and `--verify`
  (`SOURCE_UNREADABLE`) use it. `--move-orphans` moves only `source_missing`,
  and nothing in a run with an error in its plan. Never use `_exists()` to
  decide that a source is gone.
- **Only `--verify full` records `verified_at` (v0.12.1).** A quick verify
  reads no file; recording it would send the `--max-gb` queue back to its
  start. The same for `manifest_backup.py --verify` (it uses `_run_verify`).
- **A quick verify takes a planned source from the plan's listing (v0.13.2).**
  With `VERIFY_QUICK_USES_PLAN`, `_listed_sources` gives a job the planned
  file's size and date only when the planned path IS the row's `source_path`;
  every other row still goes through `_source_stat` (missing needs proof). A
  file listed but whose own `stat` is refused is `OK` in a quick verify and
  `SOURCE_UNREADABLE` in a full one (`test_b4_verify_says_source_unreadable`
  pins the old behaviour with the setting off).
- **A verify never ends on a file, and keeps what it checked (v0.13.1).**
  `_verify_row` turns every read error into a status (`DEST_UNREADABLE`,
  `SOURCE_UNREADABLE`), never an exception. `_verify_catalog` records
  `verified_at` through `write_marks` every `VERIFY_COMMIT_S`, in one short
  transaction, and writes the summary and `_verify.csv` on Ctrl+C too. With two
  drives the copy is hashed on `_CopyReader`'s thread while the main thread
  hashes the source (`_same_drive`, `VERIFY_PARALLEL`); the catalog stays on the
  main thread. A `DEST_MISSING` on a destination that no longer answers
  (`_dest_available`) stops the verify instead of being reported. `upsert`
  clears `verified_at`: the date belongs to the bytes that were read.
- **A listing never proves that a destination file is NEW (v0.13.1).**
  `_file_statuses` takes size and date from one `scandir` per destination
  folder only for a name the listing holds exactly as planned; a miss asks
  `os.stat` (`_file_status`). A false NEW would be `os.replace`d over an
  existing file without the versions folder. Only a folder that does not
  exist makes its files NEW without asking.
- **A name Windows rewrites goes through `os.path.relpath` (v0.13.1).**
  `_Planner._rel` cuts the base's prefix only when `_REL_REWRITTEN` finds no
  name ending in a dot or a space and no device name: relpath reads `Trip.` as
  `Trip`, which is how two folders with one destination are found (S8).
- **One odd file never stops a run (v0.13.1).** A file date goes to a CSV
  through `_csv_date` and to the catalog through `_db_ns` (SQLite holds 64
  bits: a date after 2262 is stored at the limit and compared the same way) --
  never `datetime.fromtimestamp` on a file date. A read asks for what the file
  holds (`_read_chunks`), never `handle.read(COPY_CHUNK_MB ...)`. A name
  Windows opens as a device (`NUL`: `relpath` raises `ValueError`) is an error
  of the plan: `_Planner._rel` records it and returns `None`, `_add` leaves the
  file out (v0.13.2).
- **The catalog is checked when it is opened (v0.13.1).** `_Catalog` runs
  `PRAGMA quick_check`: a damaged catalog is a `RuntimeError` (exit 2) for the
  backup, the dry-run, the verify, the restore and the menu. So is a row whose
  path would lead outside the backup (`_first_unsafe_rel` / `_unsafe_rel`: a
  `.` or `..` part, a drive or `:`, a root, empty; v0.13.2) — this tool never
  writes one, and every consumer of the rows (restore, `--move-orphans`, move
  detection, verify) joins `rel` to the backup's root. A run reads the
  catalog's rows inside `except sqlite3.Error`. A plan with a path under
  `CATALOG_FOLDER` is refused (`_reserved_rel_error`, exit 2).
- **The big CSVs list what changed (v0.13.1).** `_plan.csv` of a run that
  copies leaves out the UP_TO_DATE lines (`PLAN_CSV_UP_TO_DATE`; a dry-run
  lists every file); `_verify.csv` and the verify's `_plan.csv` leave out the
  OK ones (`VERIFY_CSV_OK`). A test that reads those lines sets the setting.
- **`mobile_backup.py` 0.1.3.** The state records the size and date a photo
  had while its bytes were read (`_copy` returns them); an entry with no source
  and no mirror file is dropped (`forget`, never in a run with a read error); a
  mirror file that a live photo holds is never deleted in another entry's name
  (`held_by_another`); `chmod` only on our temp suffixes or a file the state
  proves ours; the album never depends on `--source` (`album_folder` searches
  up to the drive; a partial album is numbered with the state's other photos,
  counted only).
- **`mobile_backup.py` 0.1.4.** One run at a time: `take_lock` creates
  `run.lock` with O_CREAT|O_EXCL (PID + process start time); a live holder =
  exit 2, nothing changed; a dead one (or a reused PID) is taken over and
  logged; `_process_status` never calls `os.kill` on Windows (it would end the
  process); a `--dry-run` takes no lock and stops while a live run holds one.
  A delete that fails for a photo still in a source postpones that photo's new
  copy (`postponed` in `execute`): the state never forgets a file it put in the
  mirror. A mirror path is "absent" only on FileNotFoundError
  (`_mirror_stat`); any other error freezes every action that touches that
  album (`_leave_unreadable_albums`), exit 1. Copies and the state are fsynced
  before `os.replace`; the state's replace is retried on PermissionError
  (`_replace_retrying`); `state.json.bak` is written after every finished run
  and used (no delete/rename/forget, exit 1) when `state.json` cannot be read.
  The overlap check compares `os.path.realpath` too (`_overlaps`).
- **`mobile_backup.py` deletes only with proof (0.1.2).** It is the one script
  that deletes on its destination (a mirror). A photo is deleted or renumbered
  only when its path is under a `--source` of this run (`_under_run`; never by
  the root recorded in the state, which a narrower test run would pin), and
  nothing is deleted or renamed in a run where `os.walk` (`onerror`) or a
  `stat` failed on a source. Since 0.1.4 also: never a photo behind a link or
  junction the walk did not follow (`find_photos` does not follow them;
  `behind_link` in `plan`), never in a mirror folder that could not be read,
  and never as a mass delete: a run whose photos gone outnumber its new ones
  by more than `MAX_DELETE_PERCENT` of the mirror's photos of its sources (and
  by at least `MAX_DELETE_MIN`) changes nothing and exits 1 unless
  `--allow-mass-delete`. A photo exiftool gave no record for keeps no `shot` in the state, so
  the next run reads it again; a null `shot` of a state written before 0.1.2
  is read once more.

## Settings are never hardcoded
Every user-editable setting lives at the TOP of `selective_backup.py`
(`EXPORT_NAME_CONTAINS`, `EXPORT_FORMATS`, `CAPTUREONE_FOLDER_NAMES`,
`SPACE_MARGIN_GB`, ...). Code and argparse defaults READ those constants —
never repeat a default as a literal (`"export"`, `"Cache"`, `"jxl>tif>jpg"`).
A literal equal to the default passes every test and silently discards the
owner's edit.

## Owner's archive (what the code must handle)
Measured on 2026-10-06 (read-only survey of `G:\<year>`, ~9.3 TB, 1.19 M
files):
- Export folder names: 37 spellings, all containing "export"
  (`Lightroom_Export` 553, `_EXPORT` 158, `C1_Export_WEB`, `Exported`,
  `export`, `Lioghtroom_Export`, ...).
- File names: Nikon `DSC_9360`, Nikon AdobeRGB `_DSC6489` (leading
  underscore), Canon `IMG_0014`, film scans `000037`, phone
  `20130805_192545`. NEVER derive a photo's key by cutting at the first `_`:
  match against the real stems in the folder (see the plan).
- `_EXPORT` subfolders: `16B_JXL`, `JXL_mobile`, `TIFF16`, `sRGB`, `WEB`,
  `CHECK`, `AdobeRGB`.
- `CaptureOne\Cache` (Proxies, Thumbnails) is regenerable and skipped by
  default; `CaptureOne\Settings*` and `NKSC_PARAM` are always copied.
- Paths contain Japanese and Portuguese characters, spaces, `[FINAL]`,
  `～`. Longest seen: 194 chars — the destination prefix can push it past
  260, so all file I/O uses Windows extended paths.

## Verification
- After editing: `python -m py_compile photo_backup.py selective_backup.py
  restore_backup.py`.
- Tests: `python -m pytest tests/` (pytest lives in the `python` 3.12
  interpreter; `py` is 3.13 without pytest). Tests build synthetic trees in
  `tmp_path` and set `PHOTOBACKUP_LOG_DIR`, so no test writes to `Logs\`.
- **Real-archive checks**: the owner's `G:` is READ-ONLY for every agent.
  A `--dry-run` over a `G:` folder is always allowed; a real copy goes only
  to `E:\temp_jxl\photo_backup_test` (scratch). The plan lists the exact
  commands and expected results — paste their output into the report.
- When fixing a bug, prove the new test **fails against the pre-fix code**:
  `git show HEAD:selective_backup.py > <tmpdir>/selective_backup.py` and run the test
  there. Do **not** use `git stash`.
- Changing a function's signature or return shape: grep every caller and
  every test double first; new parameters go last, with a default.

## Editing source safely
- Bash heredocs eat backslashes (Windows paths, regexes). Use the Edit tool
  for exact edits; when a bulk edit needs Python, write the script to a file
  and `assert s.count(old) == N` before replacing.
- Stage files by name, never `git add -A`.

## AI workflow: planner → coder → reviewer
Same as the sibling repo `jxl-photo`. Documents live in `AI_tools/`
(gitignored), named `YYMMDD_<Tool>_<kind>_<topic>.md` with the same
`<topic>` across plan / report / review.
1. **Plan** (Claude/Opus): literal and complete; every decision taken.
2. **Code** (DeepSeek or another coder): follow the plan literally. When the
   plan is wrong or ambiguous, take the most conservative option (fail
   closed, change less) and record it. **Never commit.** Write the report:
   per plan item done/partly/not done; every decision and deviation; the
   commands run with their REAL output; what was not verified. Finish by
   telling the user the report's path.
3. **Review** (Claude/Opus): re-run tests and real checks, must-fix /
   should-fix list, commit verdict.

## Commits and releases
- Commits carry the repo owner's authorship only — **no `Co-Authored-By` or
  `Claude-Session` trailers**.
- Tags: `vX.Y.Z`; a test version before a release is `vX.Y.Z-rcN` (since
  v0.14.0-rc1; never `_betaN`, as in jxl-photo: the version is written into
  the catalog, `created_by` / `updated_by`, and must stay a clean
  `X.Y.Z[-rcN]`). `__version__` is the same in every script but
  `mobile_backup.py` (its own `0.1.x`).
- Each program shows its name and version: the summary's first line
  (`sb._program_name()`: the script it was started as), the mirror's box
  borders (`_top_rule`), the console's title (`sb._set_console_title`; the
  menu takes its title back after each script it runs).

## Docs map
- `README.md` — short by design, in jxl-photo's format (owner's rule,
  2026-11-18: the README must not grow; about 250 lines, from 1365): the
  opening lines, one session tree, "Does it fit" in short, features one line
  each, the scripts, a short install, the menu's and the command line's quick
  start, the modes table, safety and "Good to know" one line each, the
  documentation table and a SHORT "What's new" for the current release ONLY
  (replaced at each release, never appended to; the same bullets go to the
  top of `docs/whats_new.md`). No table of contents, no release table, no
  option or setting tables. Anything longer goes to a page below, with a link
  and a row in the documentation table.
- `docs/how_it_selects.md` — sessions, how an export finds its originals,
  "Does it fit" in full, export formats, jxl-photo derivatives,
  `--unselected`, folders without an export, inside sessions.
- `docs/per_folder_modes.md` — `manifest_backup.py`: the two CSVs, every
  column, the shipped modes and their sizes, its options and settings.
- `docs/menu.md` — the menu item by item, unattended runs, after jxl-photo,
  the menu's settings.
- `docs/mirror.md` — `mirror_backup.py`: getting a file back (first, for the
  owner under pressure), the two modes, the `Mirror` column, what leaves and
  what never does, the screen, the trash, the locks, space, unattended runs,
  options, settings, logs and exit codes.
- `docs/commands_and_settings.md` — every option and setting of
  `selective_backup.py`, the logs and CSVs, exit codes.
- `docs/safety.md` — every safety guarantee in full (the README keeps one
  line each).
- `docs/reruns_and_moves.md` — re-runs, the versions folder, moves, orphans.
- `docs/verifying.md` — verify, statuses, the check after each copy, a
  damaged catalog (the backend's message points to "A damaged catalog", which
  the README's "Good to know" names too), rolling verify, backup ID.
- `docs/restoring.md` — `restore_backup.py`: options, outcomes, its setting.
- `docs/captureone.md` — `captureone_backup.py`: what is kept, the restore,
  force and undo, its options and settings.
- `docs/jxlphoto_contract.md` — the contract with jxl-photo (仕様書): every
  item JP-1 to JP-22 with its rule, where it lives in both programs and what
  breaks; the minimum version and how it was measured; how to run the
  contract test against any jxl-photo or an old tag.
- `docs/tiff_to_jxl.md` — a TIFF or JPEG kept as JXL in the backup (the file
  name stays: links), with the restore's `--convert-back` table: profiles and the
  `Tiff` column, the proof, a second run, TIFF copies already there, moving
  from FreeFileSync, the restore, verify, space, the tools kept in the backup,
  its options and settings (v0.14.0-rc2).
- `docs/related_projects.md` — the Windows program and jxl-photo.
- `docs/whats_new.md` — the short notes of each release, newest first, and
  the at-a-glance table. `docs/version_history.md` — every release in detail.
- `README_mobile_backup.md` — `mobile_backup.py`: usage, how it numbers,
  the sync and delete rules, its settings.
- `tests/test_docs.py`: every `--flag` and every setting appears in
  `README.md` + `docs/` (the release notes do not count), and every relative
  link of the README, the mobile README and `docs/` leads to a file and a
  heading that exist. A section moved between pages keeps its heading or
  updates every link to it.

## Pending list
The owner keeps one list of everything still pending, across all repos, in
`../PENDENCIAS.md` (the folder above this repo, outside git, in Portuguese).
Read it when asked what is left to do. When work here finishes an item, leaves
something for later, or waits on a decision from the owner, update that file in
the same session: remove what is done, add what is new. It is private: never
copy its contents into tracked files, commits or release notes.
