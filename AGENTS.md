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
  Its own settings (`CONFIG_PATH`, `VERIFY_MODE`, `FORMAT_ALTERNATIVES`,
  `RESTORE`) are at its top; tests set `CONFIG_PATH` to a temp file.
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

## Safety rules (these are the product)
- **The source is READ-ONLY.** No code path may open a source file for
  writing, rename, delete, touch or set attributes on anything under a
  `--source`. Tests snapshot the source tree (path, size, mtime, hash) before
  and after every run and must find it identical.
- **Nothing on the destination is ever deleted.** A file whose source changed
  is reported; with `--update-changed` the old copy is MOVED to the versions
  folder first, never overwritten in place. A new file identical byte for byte
  to the old copy takes its place instead: `_handle_old_copy` compares the old
  copy with the hash of the bytes really copied (`before_final`, the last step
  before the final name), never with what the source held earlier, and the
  comparison only decides the versions folder, never whether to copy. The only
  file the tool removes is
  its own temp file (`*` + `TEMP_SUFFIX`) after a failed copy. The one
  exception: a folder left empty by one of our own moves (a detected
  move/rename, or `--move-orphans`) is removed with `os.rmdir` — never a
  file, and never anything inside `CATALOG_FOLDER`.
- **The restore never writes to the backup.** `restore_backup.py` opens the
  catalog read-only, never writes, moves or deletes anything under `--backup`,
  and never overwrites a target file that already exists.
- **Fail closed.** Not enough space, an unreadable catalog, a closed stdin
  without `--yes`, a destination inside a source (or the reverse): abort
  BEFORE the first byte is copied, exit code 2.
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
  maps to `raw_or_jpeg`: RAW + sidecars + every JPEG that has no RAW of the
  same photo beside it (`X (2).JPG` is another file, not a variant of `X`), and
  the sidecars of what it keeps. `raw+jpeg` and `all` only copy more.
- **A folder not exported yet is not lost in a `+` mode.** `Unexported=yes`
  in the modes table copies a folder without an export whole (`--unexported
  copy`, per line); a folder with one follows the rest of the mode.
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

## Pending list
The owner keeps one list of everything still pending, across all repos, in
`../PENDENCIAS.md` (the folder above this repo, outside git, in Portuguese).
Read it when asked what is left to do. When work here finishes an item, leaves
something for later, or waits on a decision from the owner, update that file in
the same session: remove what is done, add what is new. It is private: never
copy its contents into tracked files, commits or release notes.
