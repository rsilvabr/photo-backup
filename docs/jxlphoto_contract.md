# photo-backup × jxl-photo: the contract (仕様書)

photo-backup runs [jxl-photo](https://github.com/rsilvabr/jxl-photo) to keep a
TIFF or a JPEG as JXL in the backup and to make it again on restore
([tiff_to_jxl.md](tiff_to_jxl.md)), and it reads the marks jxl-photo writes
into its JXL files. This page is what jxl-photo must keep doing for that to
work: change anything else in jxl-photo freely; change one of these items and
photo-backup breaks — safely (it stops, or keeps the TIFF as it is), but it
breaks.

Each item has a number, **JP-1** to **JP-17**, used in three places:

- here: the rule, where it lives in each program, what breaks;
- in photo-backup's messages: `CONVERT FAILED <file>: ... (JP-5)`, a restore's
  `ERROR ... (JP-10)`, a verify's `DEST_NOT_FROM_SOURCE ... (JP-5)`;
- in the contract test, [`tests/test_jxlphoto_contract.py`](../tests/test_jxlphoto_contract.py):
  one test per item (`test_jp5_...`), run against a real jxl-photo.

## The minimum version

photo-backup refuses a jxl-photo older than **`JXLPHOTO_MIN_VERSION` = 2.30.0**
(top of `selective_backup.py`): a backup with a `Tiff` profile stops before
reading anything (exit 2), and the restore does not use it. A test version of
the minimum (`2.30.0_beta1`, `2.30.0-rc1`) counts as older than it. Measured on
2026-11-19 by running the contract test on each version, extracted from
jxl-photo's git tags:

| jxl-photo | Items that hold | Why |
|---|---|---|
| v2.29.1 (`jxl_photo.py`) | 3 of 17 | `jxl_photo.py encode` / `decode` / `transcode` did not exist yet: exit 2, `usage: ...` |
| v2.30.0_beta1 | 16 of 17 | everything works; JP-1 refuses a test version of 2.30.0 |
| **v2.30.0**, v2.30.1, v2.30.2 | **17 of 17** | the first release whose `jxl_photo.py` runs its backends by name |
| `jxl-photo.exe` 2.29.0_beta1 | 16 of 17 | the exe had its backends by name before the script did; JP-1 refuses it (one minimum for both: use v2.30.0-exe1 or newer) |

(JP-15 to JP-17 were added with photo-backup v0.14.0-rc3 and measured the same
day: the minimum did not move.)

## When you change jxl-photo

1. Run the contract test against your copy of jxl-photo, from photo-backup's
   folder (it needs numpy and tifffile, as jxl-photo does; about 30 s, small
   synthetic TIFFs in a temp folder):

   ```console
   set PHOTOBACKUP_JXLPHOTO=C:\path\to\jxl-photo\jxl_photo.py
   python -m pytest tests/test_jxlphoto_contract.py -v
   ```

   Without `PHOTOBACKUP_JXLPHOTO` the test is skipped, so photo-backup's own
   suite never needs jxl-photo. On a version older than
   `JXLPHOTO_MIN_VERSION`, JP-1 fails by definition: read the others.
2. **14 passed**: photo-backup keeps working with that version.
3. **A failure names its item** (`AssertionError: JP-6: multi.jxl: ...`): read
   that item below — what photo-backup needs, where it lives in jxl-photo,
   what happens when it breaks.
4. **When did it break?** Run the same test against older versions: extract a
   tag of jxl-photo into a folder of its own and point the test at it, newest
   to oldest, until it passes:

   ```console
   git -C C:\path\to\jxl-photo worktree add C:\temp\jxl-v2.30.1 v2.30.1
   set PHOTOBACKUP_JXLPHOTO=C:\temp\jxl-v2.30.1\jxl_photo.py
   python -m pytest tests/test_jxlphoto_contract.py -v
   git -C C:\path\to\jxl-photo worktree remove C:\temp\jxl-v2.30.1
   ```
5. **A change on purpose** (a new mark, a renamed option): change photo-backup
   first — the code, this page, the test, and `JXLPHOTO_MIN_VERSION` raised to
   the first jxl-photo with the new behavior — then jxl-photo. Never change
   JP-5's formula: the backups already made depend on it (keep the old mark and
   add a new one instead).

## What photo-backup does when an item breaks

It fails closed; nothing is ever left without a copy:

- **At the start** (JP-1): a jxl-photo that does not say its version, or an
  older one, is refused — exit 2, nothing read, nothing written.
- **During a backup** (JP-2 to JP-9, JP-15, JP-16): a conversion whose result fails a check is
  `CONVERT FAILED <file>: <why> (JP-n)` in the log; that TIFF is copied as it
  is, the run exits 1, and the next run tries again. Only space is lost.
- **During a restore** (JP-10 to JP-13, JP-16): `ERROR <file>: <why> (JP-10)`;
  nothing is written at the target, the JXL stays in the backup, and
  `--convert-back none` still gives it as it is. Any jxl-photo that keeps the
  contract can decode it later.
- **In the restore's choice** (JP-17): a wrong class mark makes `--convert-back
  lossless` bring back a lossy TIFF, or keep a lossless one as JXL — the wrong
  format, never a lost file.
- **During a verify** (JP-5): a JXL whose mark does not name the TIFF the
  catalog recorded is `DEST_NOT_FROM_SOURCE`.
- **In the selection** (JP-14): a derivative whose marks cannot be read is
  copied (more space), never left out.

Three items cannot be seen at run time — only the contract test sees them:
**JP-7** (a lossless class that became lossy), **JP-9** (a bad JXL that claims
its TIFF) and **JP-17** (a class mark that changed). Run the test after
changing the encoder.

## The items

### JP-1 — It says its version

- **Rule:** `jxl_photo.py encode --version` (and `jxl-photo.exe encode
  --version`) prints `jxl-photo X.Y.Z` (a suffix such as `-rc1` or `_beta1`
  allowed) and exits 0. The first `X.Y.Z` printed is the version.
- **photo-backup:** `_jxlphoto_version`, `_version_key`, `_jxlphoto_problem`
  (`selective_backup.py`); the version also names the folder of the copy kept
  in the backup (`_photo_backup\tools\jxl-photo X.Y.Z`) and the catalog's
  `tool` column of each JXL.
- **jxl-photo:** the `--version` of `jxl_tiff_encoder.py`'s argparse, reached
  through `jxl_photo.py`'s `_BACKEND_COMMANDS`.
- **If it breaks:** refused at the start, exit 2 ("did not say its version").

### JP-2 — The encode command

- **Rule:** jxl-photo accepts exactly this, for one TIFF into an empty folder,
  and exits 0 when it wrote the JXL (any other exit is a failure):

  ```
  jxl_photo.py encode <TIFF> <folder> --mode 0 --distance D --scan-distance S
      --eight-bit-distance E --effort N --workers 1 --verify-roundtrip
  ```

  (`jxl-photo.exe encode ...` the same.) The arguments are photo-backup's
  `_jxlphoto_encode_args`, run with `python -B` and the environment of JP-12.
- **jxl-photo:** `jxl_photo.py` `_BACKEND_COMMANDS` (`encode` →
  `jxl_tiff_encoder.py`), its argparse (`--mode 0` = one file into the output
  folder), `--scan-distance` (since 2.17.0), `--eight-bit-distance` (since
  2.24.0), `--verify-roundtrip`.
- **If it breaks:** every TIFF is `CONVERT FAILED ... (JP-2)` and copied as it
  is.

### JP-3 — One JXL per page, in that folder

- **Rule:** a TIFF of N pages gives N files ending in `.jxl` in the given
  folder, and no other `*.jxl` there. Page 0 is named `<TIFF stem>.jxl`; the
  others may have any name (today `<stem>_page<N>.jxl`).
- **photo-backup:** `_convert_one` takes every `*.jxl` of the folder;
  `_place_group` names each `X.jxl` → `X<the TIFF's extension>.jxl` in the
  backup (`photo.tif.jxl`), and the restore names them back before decoding.
- **jxl-photo:** the encoder's output naming in mode 0, and the split of a
  multipage TIFF.
- **If it breaks:** `jxl-photo wrote no JXL (JP-3)`, or a group that fails JP-6.

### JP-4 — The marks can be read without exiftool

- **Rule:** each JXL is a container (it starts with
  `00 00 00 0C 4A 58 4C 20 0D 0A 87 0A`) holding an **uncompressed** `xml `
  box (the XMP) **before** the first `jxlc` / `jxlp` box, within the first
  `XMP_SCAN_LIMIT_MB` (4 MB). An XMP compressed into a `brob` box, or placed
  after the image, cannot be read.
- **photo-backup:** `_jxl_xmp`, `_jxl_tokens` (it reads the boxes itself:
  fast, no exiftool), used by `_convert_one`, the verify and `_jxl_role`.
- **jxl-photo:** `jxl_photo_common.reorder_jxl_boxes` (metadata before the
  codestream), called by the encoder after exiftool writes the XMP.
- **If it breaks:** `CONVERT FAILED ... (jxlphoto-srcsum, JP-4/JP-5)`, and
  derivatives are no longer recognized (JP-14).

### JP-5 — The source mark

- **Rule:** the XMP of each JXL holds `jxlphoto-srcsum:<16 hex digits>`, the
  first 16 hex digits of the SHA-256 of the raw SHA-256 digest of the TIFF's
  bytes:

  ```python
  hashlib.sha256(hashlib.sha256(tiff_bytes).digest()).hexdigest()[:16]
  ```

  Every page of a multipage TIFF carries the TIFF's mark.
- **photo-backup:** `_jxlphoto_srcsum`; `_convert_one` keeps a JXL only when
  its mark is the one of the bytes photo-backup hashed itself before the
  conversion; `--verify full` checks it again on every JXL in the backup.
- **jxl-photo:** `SRCSUM_PREFIX` (`jxl_tiff_encoder.py`) and
  `jxl_photo_common._file_content_id` / `_file_digest_cached`.
- **If it breaks:** `CONVERT FAILED ... (JP-4/JP-5)`. **A new formula also
  makes every JXL already in a backup fail `--verify full`
  (`DEST_NOT_FROM_SOURCE`)**: never change it; add a new mark next to it.

### JP-6 — A multipage TIFF is one marked group

- **Rule:** the N files of an N-page TIFF each carry `jxlphoto-pages:N`,
  `jxlphoto-page:<i>` (0 to N-1, each once) and the same `jxlphoto-mpg:<id>`.
  A one-page TIFF carries no `jxlphoto-pages` (or `jxlphoto-pages:1`). The
  decoder rebuilds the TIFF from these marks, not from the names.
- **photo-backup:** `_convert_one` (a group must be whole: `pages` = the
  number of files), `_place_group` (one catalog row per page, with `page` and
  `pages`), the restore's `_JxlGroup.whole`.
- **jxl-photo:** `PAGES_XMP_PREFIX` (`jxl_tiff_encoder.py`), `PAGE_PREFIX`
  (`jxl_photo_common.py`), `MULTIPAGE_MARKER_PREFIX` / `PAGES_PREFIX`
  (`jxl_tiff_decoder.py`).
- **If it breaks:** `CONVERT FAILED ... the group is not whole (JP-6)`.

### JP-7 — Each class of TIFF gets its distance

- **Rule:** jxl-photo decides each TIFF's class: a film scan (a scanner's
  `Software` tag, an IR channel, or no `Software` tag and no camera EXIF) gets
  `--scan-distance`; an 8-bit TIFF `--eight-bit-distance`; any other TIFF
  `--distance`. Distance 0 is lossless: the decoder gives back the same pixels.
- **photo-backup:** passes a profile's three distances (`TIFF_JXL_PROFILES`)
  and records the profile, not the class (`settings` in the catalog).
- **jxl-photo:** `SCANNER_SOFTWARE` and the classification in
  `jxl_tiff_encoder.py`.
- **If it breaks:** no error at run time — a scan kept lossy, or a backup
  bigger than it should be. Only the contract test sees it.

### JP-8 — The TIFF is never changed

- **Rule:** encoding reads the TIFF only: no write, rename, delete, date or
  attribute change.
- **photo-backup:** `_convert_one` compares its size and date before and after
  (and photo-backup's own first rule: the source is read-only).
- **If it breaks:** `CONVERT FAILED ... the TIFF changed while it was converted
  (JP-8)` — and a source that is no longer read-only.

### JP-9 — A failure never claims the TIFF

- **Rule:** a TIFF jxl-photo cannot encode ends with a non-zero exit, or with
  no JXL that carries that TIFF's mark. `--verify-roundtrip` keeps a JXL that
  does not decode back from being written.
- **photo-backup:** trusts a JXL with the right mark (JP-5) and an exit 0.
- **If it breaks:** a bad JXL could replace the TIFF copy in the backup — the
  one silent loss this page guards against. Only the contract test sees it (a
  corrupt TIFF).

### JP-10 — The decode command

- **Rule:** jxl-photo accepts exactly this, for a folder holding the JXL files
  of **one** TIFF (with the names JP-3 gave them), and exits 0:

  ```
  jxl_photo.py decode <folder> <out> --mode 0 --workers 1
      --depth-policy preserve_original --no-preview
  ```

  It writes exactly one `.tif` / `.tiff` in `<out>`, with the TIFF's pages in
  order, each with its own bit depth and size, the original ICC profile, and
  no preview page of its own; a lossless JXL gives back the same pixels.
  The arguments are `_jxlphoto_decode_args`.
- **photo-backup:** `restore_backup._restore_group` gives that TIFF the
  target's name (never replacing a file) and the original's date.
- **jxl-photo:** `jxl_photo.py` `_BACKEND_COMMANDS` (`decode` →
  `jxl_tiff_decoder.py`), `--depth-policy`, `--no-preview`, the multipage
  reconstruction (`_parse_jxl_page_suffix`, `_group_naming_path`).
- **If it breaks:** the restore's `ERROR ... (JP-10)`; nothing at the target.

### JP-11 — The decode leaves its input alone

- **Rule:** without `--delete-source`, nothing in `<folder>` is deleted or
  changed.
- **photo-backup:** the restore copies the JXL files out of the backup first,
  so the backup is safe either way; the copies go when the restore is done.

### JP-12 — Nothing is written beside jxl-photo

- **Rule:** with `JXLPHOTO_LOG_DIR` set, `--version`, encode and decode write
  nothing into jxl-photo's own folder: no `Logs\`, no settings file, no cache.
  `JXLPHOTO_NO_UPDATE_CHECK=1` means no network.
- **photo-backup:** `_run_jxlphoto` sets `JXLPHOTO_LOG_DIR` (its own
  `Logs\jxl-photo`), `JXLPHOTO_NO_UPDATE_CHECK=1`, `PYTHONIOENCODING=utf-8`,
  and runs the script with `python -B` (no `__pycache__`). The restore may run
  the copy kept in the backup, and the restore never writes to the backup.
- **jxl-photo:** `LOG_DIR` (`jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`) and
  `WRAPPER_LOG_DIR` (`jxl_photo.py`), since 2.6.1; the update check, since
  2.22.0.
- **If it breaks:** a restore that writes into the backup.

### JP-13 — Its folder is enough to run it

- **Rule:** the `.py` files next to `jxl_photo.py` are all of jxl-photo a run
  needs, besides Python's packages (numpy, tifffile, ...) and `cjxl`, `djxl`
  and ExifTool found as usual. photo-backup keeps in the backup only those
  `.py` files, `README*.md`, `LICENSE` and `docs\*.md`; for `jxl-photo.exe`,
  its whole folder.
- **photo-backup:** `_keep_tools`, `_tool_files`; the restore's
  `_find_jxlphoto` uses that copy when `JXLPHOTO` is not set.
- **If it breaks:** the copy kept in the backup does not run on another
  computer (the restore with `JXLPHOTO` set still works). A new runtime file
  (a data folder, a package in a subfolder) must first be added to
  `_keep_tools`' list.

### JP-14 — Masters and derivatives say what they are

- **Rule:** a JXL made from a TIFF holds `jxlphoto-src:` and never
  `jxlphoto-derived:`; a derivative (the sRGB or resized copy of a master)
  holds `jxlphoto-derived:`. Both in the XMP of JP-4.
- **photo-backup:** `MASTER_TOKEN` / `DERIVATIVE_TOKEN`, `_jxl_role`,
  `_drop_derivatives` (since photo-backup v0.5.0): a derivative is left out only
  when its master is backed up in the same run.
- **jxl-photo:** `SRC_PREFIX` (`jxl_tiff_encoder.py`), the `jxlphoto-derived:`
  label (`jxl_photo_common.py`, `jxl_recompressor.py`).
- **If it breaks:** derivatives are copied (more space), never left out. The
  contract test checks the master side only.

### JP-15 — The transcode command (a JPEG)

- **Rule:** jxl-photo accepts exactly this, for one JPEG into an empty folder,
  and exits 0 when it wrote the JXL — one `<JPEG stem>.jxl`, a lossless JPEG
  recompression that keeps what rebuilds the JPEG file (other files, such as
  `checksums.md5`, may be there too):

  ```
  jxl_photo.py transcode <JPEG> <folder> --mode 0 --workers 1 --effort N --force-transcode
  ```

  `--force-transcode`: the lossless path only, never a lossy conversion. A JPEG
  it cannot keep losslessly (a CMYK JPEG) ends with a non-zero exit. The
  arguments are `_jxlphoto_transcode_args`. The JXL of a JPEG carries **no XMP
  marks** (they would break the rebuilding of JP-16): photo-backup's proof is
  JP-16, not JP-5.
- **jxl-photo:** `jxl_photo.py` `_BACKEND_COMMANDS` (`transcode` →
  `jxl_jpeg_transcoder.py`), its `--force-transcode`.
- **If it breaks:** `CONVERT FAILED ... (JP-15)`; the JPEG is copied as it is.

### JP-16 — The JPEG comes back byte for byte

- **Rule:** for a folder holding only that JXL (no `checksums.md5` beside it),
  this writes exactly one `.jpg` / `.jpeg` whose bytes are the original JPEG's
  (the same SHA-256), and leaves the JXL alone:

  ```
  jxl_photo.py transcode <JXL> <folder> --mode 0 --workers 1 --format jpeg
  ```

  The arguments are `_jxlphoto_rebuild_args`.
- **photo-backup:** `_transcode_one` runs it right after JP-15 and keeps the
  JXL only when the rebuilt JPEG has the SHA-256 it took of the original; the
  restore runs it again and checks the same SHA-256 (from the catalog) before
  the JPEG takes the target's name.
- **jxl-photo:** the JXL → JPEG lossless path of `jxl_jpeg_transcoder.py`
  (djxl's JPEG reconstruction).
- **If it breaks:** in a backup, `CONVERT FAILED ... (JP-16)`, the JPEG copied as
  it is; in a restore, `ERROR ... (JP-16)` and nothing at the target.

### JP-17 — The TIFF class marks

- **Rule:** the XMP of a TIFF's JXL says its class: `jxlphoto-scan:<raw|final>`
  on a film scan, and `jxlphoto-origin:tiff8` / `jxlphoto-origin:tiff16` for its
  bit depth. The class order is the one of JP-7: a scan first, then an 8-bit
  TIFF, then the rest.
- **photo-backup:** `_tiff_lossless` turns the marks and the profile into the
  catalog's `lossless` flag (the distance that class got is 0), which
  `restore_backup.py --convert-back lossless` reads. Without the marks, a TIFF
  is lossless only when the profile's three distances are 0, lossy when none
  is, and unknown otherwise (counted as lossy).
- **jxl-photo:** `jxlphoto-scan` (since 2.17.0) and `jxlphoto-origin` (since
  2.10.0), written by `jxl_tiff_encoder.py`.
- **If it breaks:** the wrong format in a `lossless` restore (see above).

## What jxl-photo may change freely

Everything else: the menu and presets, the other modes and backends
(`recompress`, `transcode`), its own defaults for anything photo-backup does
not pass (the compression of a decoded TIFF, workers, buffering), its speed,
its logs' content, the names of pages after page 0, more marks in the XMP,
files in the output folder that do not end in `.jxl`, which non-zero exit code
a failure has.

## Changing this contract

- **photo-backup asks jxl-photo something new:** add an item here, a test in
  `tests/test_jxlphoto_contract.py`, the item's number in the message that
  reports it, and raise `JXLPHOTO_MIN_VERSION` to the first jxl-photo that
  passes (run the test on its tags).
- **jxl-photo needs to change an item:** change photo-backup first (accepting
  the old and the new when it can) and release it, then jxl-photo. JP-5 never
  changes.
