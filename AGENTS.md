# photo-backup — agent notes

A selective backup of a photo archive: for every folder that holds an export
folder (`_EXPORT`, `Lightroom_Export`, ... — any name containing "export"),
copy the exports plus the originals of the exported photos (RAW, camera JPEG,
NX Studio TIFF, sidecars), the Capture One settings and the NX Studio
`NKSC_PARAM`, mirroring the folder tree on the destination. A later run can
verify that the backup still matches.

## Scripts
- `photo_backup.py` — the backend: planner, copy, catalog, verify. Every
  safety rule below lives here.
- `photo_backup_menu.py` — interactive menu + presets (`--run-preset`). It
  never copies by itself: it builds an argv and runs `photo_backup.py` as a
  child process (`run_backend`). It reads the backend's defaults by importing
  it (`_setting`) and always passes every selection option explicitly — a
  default repeated as a literal in the menu would silently override the
  owner's edit of `photo_backup.py` (the bug class of jxl-photo round 43).
  Its own settings (`CONFIG_PATH`, `VERIFY_MODE`, `FORMAT_ALTERNATIVES`) are
  at its top; tests set `CONFIG_PATH` to a temp file.

## Safety rules (these are the product)
- **The source is READ-ONLY.** No code path may open a source file for
  writing, rename, delete, touch or set attributes on anything under a
  `--source`. Tests snapshot the source tree (path, size, mtime, hash) before
  and after every run and must find it identical.
- **Nothing on the destination is ever deleted.** A file whose source changed
  is reported; with `--update-changed` the old copy is MOVED to the versions
  folder first, never overwritten in place. The only file the tool removes is
  its own temp file (`*` + `TEMP_SUFFIX`) after a failed copy.
- **Fail closed.** Not enough space, an unreadable catalog, a closed stdin
  without `--yes`, a destination inside a source (or the reverse): abort
  BEFORE the first byte is copied, exit code 2.
- A copy is written to a temp file next to its final name, verified, then
  `os.replace`d — a crash never leaves a half file under the final name.

## Settings are never hardcoded
Every user-editable setting lives at the TOP of `photo_backup.py`
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
- After editing: `python -m py_compile photo_backup.py`.
- Tests: `python -m pytest tests/` (pytest lives in the `python` 3.12
  interpreter; `py` is 3.13 without pytest). Tests build synthetic trees in
  `tmp_path` and set `PHOTOBACKUP_LOG_DIR`, so no test writes to `Logs\`.
- **Real-archive checks**: the owner's `G:` is READ-ONLY for every agent.
  A `--dry-run` over a `G:` folder is always allowed; a real copy goes only
  to `E:\temp_jxl\photo_backup_test` (scratch). The plan lists the exact
  commands and expected results — paste their output into the report.
- When fixing a bug, prove the new test **fails against the pre-fix code**:
  `git show HEAD:photo_backup.py > <tmpdir>/photo_backup.py` and run the test
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
