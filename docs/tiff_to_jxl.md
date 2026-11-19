# TIFF and JPEG kept as JXL in the backup (v0.14.0-rc2, JPEG since rc3)

A developed TIFF beside its RAW (NX Studio, DxO, Photoshop) and a film scan are
the biggest files of an archive: about 218 MB for one 45 MP photo. JPEGs are
the most numerous: camera JPEGs, lab scans, exports. On the computer they stay
as they are — Capture One edits the TIFF, everything opens a JPEG. In the
backup they can be JPEG XL: [jxl-photo](https://github.com/rsilvabr/jxl-photo)
converts each one, photo-backup proves what came out, and the restore gives the
original format back, under the file's own name, with its pages and its date.

```
G:\2025\250301_Birds\                        H:\BACKUP\2025\250301_Birds\
  DSC_0101.NEF                         ->      DSC_0101.NEF
  DSC_0101.JPG            (9.4 MB)     ->      DSC_0101.JPG.jxl                 (7.6 MB, the same bytes back)
  DSC_0101_NXstudio.TIF   (218 MB)     ->      DSC_0101_NXstudio.TIF.jxl        (34 MB)
                                               DSC_0101_NXstudio_page1.TIF.jxl  (its thumbnail page)
  _EXPORT\DSC_0101_v1.jpg              ->      _EXPORT\DSC_0101_v1.jpg.jxl
G:\2020\200501_Film12\                       H:\BACKUP\2020\200501_Film12\
  scan_001.tif            (23.5 MB)    ->      scan_001.tif.jxl                 (14.3 MB, lossless)
```

Measured on real files of the archive (2026-11-19, jxl-photo 2.30.2, the
profile `jxl` below, 4 conversions at a time):

| File | How | Size in the backup | Back from the restore |
|---|---|---:|---|
| JPEG: camera, lab scans, exports (146 files, 1.38 GB) | lossless transcode | 80.8% | the same bytes (SHA-256), every one |
| TIFF from NX Studio, 16-bit, 45 MP (2 pages) | distance 0.05 | 15.5% | same pages and date; mean difference 0.07%, the largest single pixel 6.5% |
| TIFF film scan, 16-bit, 41 MP | distance 0 (lossless) | 71% | the same pixels |
| TIFF film scan, 8-bit (SilverFast) | distance 0 (lossless) | 59% | the same pixels |

Speed *(v0.14.0-rc4; a 16-core Ryzen 9 5950X, NVMe)*: those 146 JPEG and 10
TIFF (1.8 GB) are kept as JXL in 32 s (rc3: 71 s) and come back in 13 s (rc3:
116 s). A JPEG costs one jxl-photo run (the transcode, about 0.5 s) and its
proof by djxl (0.17 s); one runs per physical core (`JXL_WORKERS`). The
conversions run in the system's temp folder (an SSD on most computers), and
one writer gives the backup's disk each finished JXL in turn — an SMR disk
takes that far better than many files written at once.

**A JPEG comes back byte for byte**: the JXL keeps what rebuilds the JPEG file
exactly, metadata included, and photo-backup checks that before it keeps the
JXL. **A TIFF comes back with the same pixels** when its distance is 0, never
the same bytes (the restore writes a new TIFF file: its SHA-256 differs from
the original's), and close to them at a distance above 0.

## Turning it on

1. Tell photo-backup where jxl-photo is: `JXLPHOTO` at the top of
   `selective_backup.py` (`jxl_photo.py` of a jxl-photo folder, or
   `jxl-photo.exe`), or `--jxlphoto PATH` on the command line. It must be
   jxl-photo 2.31.0-rc1 or newer (`JXLPHOTO_MIN_VERSION`: the first that checks
   every page it converts), and a TIFF needs libjxl's `djxl` on PATH (jxl-photo
   checks the pages with it; without it a `Tiff` profile is refused, exit 2).
   What photo-backup needs from jxl-photo is [its contract](jxlphoto_contract.md),
   checked by a test you can run against any jxl-photo.
2. Choose per manifest line, in the optional columns `Tiff` and `Jpeg`
   ([per_folder_modes.md](per_folder_modes.md)). `Tiff`: `copy` (empty: the
   TIFF as it is) or the name of a profile of `TIFF_JXL_PROFILES`. `Jpeg`:
   `copy` (empty) or `jxl`:

   ```csv
   Folder,Mode,Originals,Mirror,Tiff,Jpeg
   G:\2025,6,,selection,jxl,jxl
   G:\2019,3+,raw,,copy,jxl
   ```

   Without a manifest: `selective_backup.py --tiff jxl --jpeg jxl` (or
   `mirror_backup.py --source ... --tiff jxl --jpeg jxl`).

A TIFF profile names three distances; jxl-photo decides which one each TIFF
gets:

```python
TIFF_JXL_PROFILES = {
    "jxl": {"distance": 0.05,         # any other TIFF (a developed 16-bit TIFF)
            "scan_distance": 0,       # a film scan (jxl-photo knows a scanner's TIFF)
            "eight_bit_distance": 0}, # an 8-bit TIFF: lossless is already small
}
```

0 is lossless. Add profiles of your own (`"jxl-archive": {...}`) and name them
in the `Tiff` column; a line can use one profile and another line another. A
JPEG has no profile: its transcode is always lossless.

**Which files.** With a profile, every `.tif` / `.tiff` (`TIFF_EXTENSIONS`) of
the line except an export: a TIFF in a folder whose name contains
`EXPORT_NAME_CONTAINS` stays a TIFF, also in a mode that copies the whole
folder (a TIFF export is a delivery, and a distance above 0 would change it).
With `Jpeg=jxl`, every `.jpg` / `.jpeg` (`JPEG_JXL_EXTENSIONS`) of the line,
**exports too**: a JPEG comes back byte for byte, so nothing of the delivery
changes.

**The name in the backup** is the file's own name plus `.jxl` (`photo.tif.jxl`,
`photo.jpg.jxl`; the other pages of a multipage TIFF `photo_page1.tif.jxl`,
...): `photo.tif`, `photo.tiff` and `photo.jpg` never meet, a `photo.jxl` that
is already a photo of yours is never confused with them, and Capture One's
settings (`photo.tif.cos`) still say which photo it is.

## How a JXL is trusted

jxl-photo converts each file in a folder of its own, outside the backup: in
`JXL_WORK_DIR` (by default the system's temp folder), `pbconv\<process
number>\` (`JXL_WORK_FOLDER`). photo-backup keeps the result only with proof,
and then one writer copies it into the backup, each file read back and
checked against its SHA-256 (`--verify-copy`) before it takes its name.

**A TIFF**:

- jxl-photo ended without an error and wrote at least one JXL;
- **jxl-photo decoded every page and compared it with that page of the TIFF
  before the page took its name** (`--verify-roundtrip`, jxl-photo 2.31.0-rc1
  or newer: [JP-20](jxlphoto_contract.md#jp-20--every-page-is-checked-before-it-takes-its-name)):
  a lossless page decodes to the same pixels; a lossy one holds the same image
  — its brightness and a PSNR of at least 20 dB catch a black or scrambled
  encode, not a small loss (real NX Studio TIFFs at distance 0.05 measured 45
  to 68 dB). A page that fails, or cannot be checked, fails the whole TIFF;
- every JXL says it was made from the very bytes this run read: its
  `jxlphoto-srcsum` mark is the SHA-256 of the TIFF, taken by photo-backup
  before the conversion;
- a multipage TIFF came back whole (`jxlphoto-pages` = the number of files);
- the TIFF has the same size and date at the end of the conversion.

**A JPEG** (jxl-photo's lossless transcode, `--force-transcode`: never a lossy
route):

- jxl-photo ended without an error and wrote one JXL;
- **the JPEG rebuilt from that JXL (by libjxl's djxl, or by jxl-photo) has the
  SHA-256 photo-backup took of the JPEG before** — the same bytes, proven before
  the JXL counts (the JXL of a JPEG carries no marks: they would break that
  rebuilding);
- the JPEG has the same size and date at the end.

Then the JXL files go into the backup — every file of a group written next to
its final name and checked first, then the names (should a name fail to change,
the next run moves the pages already renamed aside and makes the group again)
— and the catalog records each one (its own size, date and SHA-256) and
the file it came from (size, date, SHA-256, page, pages, its kind, whether it
is lossless, jxl-photo's version and the settings). A TIFF is lossless when
the distance its class got is 0 (jxl-photo marks a scan and an 8-bit TIFF:
[JP-17](jxlphoto_contract.md#jp-17--the-tiff-class-marks)).

**Anything else copies the file as it is**, under its own name: jxl-photo
missing or failing, a page that fails its check, a mark that does not match, a
group not whole, a JPEG that does not come back byte for byte (a CMYK JPEG
cannot be transcoded), a file that changed while it was converted,
`JXL_TIMEOUT_MIN` passed, a JXL that could not be written into the backup. The
log says `CONVERT FAILED <file>: <why> (JP-n)` — for an error of jxl-photo, its
own ERROR line and the path of jxl-photo's log of that conversion, kept in
`Logs\jxl-photo\` (a conversion that worked keeps no log) —, the summary counts
it, and the run exits 1. Nothing is left without a copy; the next run tries
again and, once it works, that copy leaves (below).

## A second run

- **Nothing changed**: the JXL is up to date (`N up to date` in the summary),
  nothing is converted again.
- **The file was edited**: CHANGED. With `--update-changed` (always in the
  mirror) it is converted again; the old JXL goes to `versions\` (the
  mirror: to its trash).
- **Another profile** (or other distances, or another `TIFF_JXL_EFFORT`):
  CHANGED, converted again the same way. The catalog keeps the settings each
  JXL was made with. Another `JPEG_JXL_EFFORT` is not a change: every effort
  gives the same JPEG back, so a JPEG already kept as JXL stays as it is.
- **A page missing in the backup**: the group is made again.

## A backup that already holds the TIFF or the JPEG

When a line gets a profile (or `Jpeg=jxl`), the copies already in the backup
are replaced: the JXL is written first, recorded, and only then the old copy
leaves — to `versions\` in a normal backup, to the trash in a mirror. That
holds for a copy this tool wrote and for one another program wrote with the
source's size and date (FreeFileSync, robocopy): the same files a run adopts as
its own. A file in the backup that differs from the source is not a copy of it
and is left where it is.

The summary says how many before the question: `copies in the backup that
their JXL replaces: N (to versions\)`.

## Moving from FreeFileSync to the mirror

FreeFileSync and a backup with JXL do not mix: FreeFileSync would copy the
TIFFs and JPEGs back (the source has them) and, mirroring, delete the JXL (the
source has no JXL). To keep them as JXL in a backup FreeFileSync made, let
photo-backup's mirror take that backup over instead: a manifest line with mode
`6` (the whole folder), `Mirror`, `Tiff=jxl` and `Jpeg=jxl`
([mirror.md](mirror.md)). Its first run:

- adopts every file FreeFileSync copied with the source's size and date —
  nothing is copied again;
- converts the TIFFs and JPEGs and moves FreeFileSync's copies of them to the
  trash (`MIRROR_KEEP_DAYS` days, then deleted);
- leaves anything FreeFileSync copied that is not in the plan (a Capture One
  `Cache`, say) where it is: not written by the backup, never removed.

From then on, do not point FreeFileSync at that backup again.

## Restoring

`restore_backup.py` (or the menu's `[5] Restore`, which asks) gives each file
back in the format you choose ([restoring.md](restoring.md)):

| `--convert-back` | A JPEG | A lossless TIFF | A lossy TIFF |
|---|---|---|---|
| `all` (default) | the JPEG, byte for byte | the TIFF, the same pixels | the TIFF, close to the original |
| `lossless` | the JPEG, byte for byte | the TIFF, the same pixels | **stays JXL** (`photo.tif.jxl`) |
| `none` (or `--keep-jxl`) | stays JXL | stays JXL | stays JXL |

`lossless` brings back only what comes back exactly; a lossy TIFF stays the
JXL it is in the backup (a TIFF made from it would be bigger, never better).
A file the catalog cannot tell (a backup made by rc2) counts as lossy there.

How each one comes back: a JPEG is rebuilt by libjxl's djxl straight from
the backup's JXL (read once and checked against its catalog SHA-256, never
written there; [JP-18](jxlphoto_contract.md#jp-18--djxl-gives-the-jpeg-back-from-its-jxl))
and checked against the SHA-256 of the original JPEG: the same bytes,
`RESTORED`; a rebuild that differs is an `ERROR (JP-18)` and nothing is
written. Without djxl (`DJXL`), the restore copies the JXL next to the target
and jxl-photo rebuilds it there (`transcode --format jpeg`, JP-16). A TIFF's
JXL files are copied next to the target the same way (each one checked
against its catalog SHA-256) and decoded by jxl-photo (`decode --mode 0
--depth-policy preserve_original --no-preview`: its own pages and bit depths,
no preview page added): `RESTORED_FROM_JXL`. A JXL kept as it is:
`RESTORED_AS_JXL`. Every file gets the original's date and never replaces a
file that is there.

- jxl-photo comes from `--jxlphoto` / `JXLPHOTO`; when that is empty or not
  found, the restore uses the newest one kept in the backup (below) and says
  so. The restore runs a jxl-photo from 2.30.0 on
  (`JXLPHOTO_RESTORE_MIN_VERSION`): the one an older backup keeps still
  brings its files back.
- No jxl-photo at all: `NEEDS_JXLPHOTO` for those files (the others are
  restored), exit 1. `--convert-back none` restores the JXL files as they are.
- `--only` takes the file's own name (`2025\shoot\photo.tif`) or a JXL's.
- Several at a time (v0.14.0-rc4): `JXL_WORKERS` JPEGs and `JXL_TIFF_WORKERS`
  TIFFs come back together while the plain copies go on in order; the CSV
  keeps the order of the catalog. `--convert-back none` is a plain copy.

## Verify

A `--verify` checks a JXL as it checks any copy (size, date; `full`: its
SHA-256), and its source against the TIFF or JPEG the catalog recorded
(`full`: its SHA-256). For a TIFF, `full` also reads the JXL's mark: a JXL that
does not say it came from that TIFF is `DEST_NOT_FROM_SOURCE`
([verifying.md](verifying.md)). A JPEG's JXL has no mark to read: it was proven
byte for byte when it was made.

## Space

The space check counts each file to convert at its full size (the JXL is
smaller: the room is there for the worst case). `manifest_backup.py --analyze`
adds the columns `tiff_to_jxl_bytes` and `jpeg_to_jxl_bytes`: how much of each
line is TIFF or JPEG that it would keep as JXL. The work folder
(`JXL_WORK_DIR`) holds only the files in flight — each pool runs at most twice
its size ahead of the writer —: a few GB, however slow the backup's disk is.

## The programs kept in the backup

With `KEEP_TOOLS_IN_BACKUP` (on), every real run keeps in
`<backup>\_photo_backup\tools\` (`TOOLS_FOLDER`) what a restore on another
computer needs:

- `photo-backup <version>\` — the scripts of the version that wrote to the
  backup, `backup_modes.csv`, the README and `docs\` (never your manifest);
- `jxl-photo <version>\` — the folder of `JXLPHOTO` (its `.py` files and docs;
  for `jxl-photo.exe`, its whole folder);
- each file or folder of `KEEP_TOOLS_EXTRA` (the `.zip` of photo-backup-exe
  and jxl-photo-exe, say), under its own name;
- `HOW TO RESTORE.txt`, which says what to run.

Each folder is written as `<name>.pbtmp` and renamed when whole. Nothing there
is ever replaced or deleted: a new version gets a folder of its own, and a
`KEEP_TOOLS_EXTRA` file whose name is taken by another size gets ` (2)`. A
problem here is a warning, never the end of the backup. jxl-photo itself needs
Python 3.12 with libjxl's `cjxl` / `djxl` and ExifTool on the other computer
(jxl-photo-exe brings its own).

## Options and settings

| Option | Script | Meaning |
|---|---|---|
| `--tiff copy\|PROFILE` | `selective_backup.py`, `mirror_backup.py --source` | the TIFFs of this run: copied as they are (default) or kept as JXL with a profile |
| `--jpeg copy\|jxl` | `selective_backup.py`, `mirror_backup.py --source` | the JPEGs of this run: copied as they are (default) or kept as JXL, byte for byte |
| `--jxlphoto PATH` | every script but the menu | jxl-photo's `jxl_photo.py` or `jxl-photo.exe` (default `JXLPHOTO`) |
| `--convert-back all\|lossless\|none` | `restore_backup.py` | which files kept as JXL come back in their own format (above) |
| `--keep-jxl` | `restore_backup.py` | the same as `--convert-back none` |

At the top of `selective_backup.py`:

| Setting | Default | Meaning |
|---|---|---|
| `JXLPHOTO` | `""` | jxl-photo's `jxl_photo.py` or `jxl-photo.exe`; `""` = no conversion (a line that asks for one is refused, exit 2) |
| `JXLPHOTO_MIN_VERSION` | `"2.31.0-rc1"` | the oldest jxl-photo a backup runs ([the contract](jxlphoto_contract.md); 2.31.0-rc1 checks every page it converts, JP-20); an older one, or one that does not say its version, is refused (exit 2) |
| `JXLPHOTO_RESTORE_MIN_VERSION` | `"2.30.0"` | the oldest the restore runs to bring a file back (the decode and the JPEG rebuild did not change since): the jxl-photo kept in an older backup still restores it |
| `TIFF_JXL_PROFILES` | `{"jxl": ...}` | the profiles the `Tiff` column (or `--tiff`) names: `distance`, `scan_distance`, `eight_bit_distance` (0 = lossless); no profile may be called `jpeg` |
| `TIFF_EXTENSIONS` | `(".tif", ".tiff")` | what a profile converts (never in an export folder) |
| `TIFF_JXL_EFFORT` | `7` | jxl-photo's `--effort` for a TIFF (1 to 10) |
| `JPEG_JXL_EXTENSIONS` | `(".jpg", ".jpeg")` | what `Jpeg=jxl` (or `--jpeg jxl`) keeps as JXL, exports too |
| `JPEG_JXL_EFFORT` | `5` | jxl-photo's `--effort` for a JPEG (1 to 10; lossless at any effort). Measured on 60 real JPEGs: 3, 5 and 7 give the same size; 16 at a time, 5 was 30% faster than 7 (one at a time, the same); 9 is 8× slower. Changing it never converts a JPEG again |
| `JXL_WORKERS` | `0` | files converted at the same time (one jxl-photo each); `0` = one per physical core (on a 16-core CPU, 16 were as fast as 32 and left a third of the CPU free), one per logical CPU where the cores cannot be counted |
| `JXL_TIFF_WORKERS` | `0` | of those, TIFFs at the same time (a TIFF takes GBs of memory); `0` = a quarter of the logical CPUs, at most one per 4 GB of free memory |
| `DJXL` | `""` | libjxl's `djxl`, which proves and restores a JPEG ([JP-18](jxlphoto_contract.md#jp-18--djxl-gives-the-jpeg-back-from-its-jxl)); `""` = the one on PATH; `"none"` = always through jxl-photo (JP-16) |
| `JXL_TIMEOUT_MIN` | `30` | a conversion longer than this is given up: the file is copied as it is |
| `JXL_WORK_DIR` | `""` | where the conversions run, each in a folder of its own; `""` = the system's temp folder (an SSD on most computers). The work folder is never inside or above a source or the backup (refused, exit 2: a source that holds the temp folder needs another `JXL_WORK_DIR`). On another volume than the backup, each JXL is copied into it and checked; on the same one, renamed |
| `JXL_WORK_FOLDER` | `"pbconv"` | photo-backup's folder inside `JXL_WORK_DIR` (one plain name): one folder per running backup, named by its process number and holding a mark file, removed at the end; a later run removes the marked ones of a backup that no longer runs |
| `KEEP_TOOLS_IN_BACKUP` | `True` | keep photo-backup, jxl-photo and `KEEP_TOOLS_EXTRA` in the backup |
| `KEEP_TOOLS_EXTRA` | `()` | more files or folders kept there |
| `TOOLS_FOLDER` | `"tools"` | where, under `_photo_backup` |

The manifest's `Jpeg` column accepts the words of `JPEG_VALUES`
(`manifest_backup.py`). jxl-photo runs with its log in the conversion's own
folder (kept in photo-backup's `Logs\jxl-photo\` only when the conversion
failed: a backup of a whole archive would leave one per photo; the restore's
go there directly), with its own defaults and not the settings overlay of its
presets (`JXLPHOTO_SETTINGS` empty:
[JP-21](jxlphoto_contract.md#jp-21--an-empty-jxlphoto_settings-turns-the-settings-overlay-off)),
without its update check, and without writing anything next to itself: its
compiled Python is kept in `Logs\pycache\` (rebuilt by itself when it is gone
or jxl-photo changes), so it starts in 0.08 s instead of 0.3 to 0.5 s
([JP-19](jxlphoto_contract.md#jp-19--each-command-is-a-script-beside-jxl_photopy)).
