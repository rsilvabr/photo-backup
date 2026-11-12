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
  safety rule below lives here.
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
  named in its README: `tests/test_docs.py::test_docs_cover_the_settings`
  (v0.13.2), with `NOT_SETTINGS` for the constants that are not settings.
- `restore_backup.py` — restores files out of a backup (`--to` /
  `--to-original`). A backend may import the catalog and the copier from
  `selective_backup.py` (`import selective_backup as sb`); never duplicate
  the catalog format or the copy routine — they exist once.
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
- **Nothing on the destination is ever deleted.** A file whose source changed
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
- Tags: `vX.Y.Z`, betas `vX.Y.Z_betaN`.

## Docs map
- `README.md` — what it does, safety guarantees, every `--flag` (enforced by
  `tests/test_docs_cover_the_flags.py`), every setting, examples, exit codes.
- `README_mobile_backup.md` — `mobile_backup.py`: usage, how it numbers,
  the sync and delete rules, its settings.

## Pending list
The owner keeps one list of everything still pending, across all repos, in
`../PENDENCIAS.md` (the folder above this repo, outside git, in Portuguese).
Read it when asked what is left to do. When work here finishes an item, leaves
something for later, or waits on a decision from the owner, update that file in
the same session: remove what is done, add what is new. It is private: never
copy its contents into tracked files, commits or release notes.
