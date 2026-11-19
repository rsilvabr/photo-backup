# TIFF kept as JXL in the backup (v0.14.0-rc2)

A developed TIFF beside its RAW (NX Studio, DxO, Photoshop) and a film scan are
the biggest files of an archive: about 218 MB for one 45 MP photo. On the
computer they stay TIFF, because Capture One edits them. In the backup they
can be JPEG XL: [jxl-photo](https://github.com/rsilvabr/jxl-photo) converts
each one, photo-backup proves where every JXL came from, and the restore turns
it back into the TIFF, with its own name, pages and date.

```
G:\2025\250301_Birds\                        H:\BACKUP\2025\250301_Birds\
  DSC_0101.NEF                         ->      DSC_0101.NEF
  DSC_0101_NXstudio.TIF   (218 MB)     ->      DSC_0101_NXstudio.TIF.jxl        (34 MB)
                                               DSC_0101_NXstudio_page1.TIF.jxl  (its thumbnail page)
G:\2020\200501_Film12\                       H:\BACKUP\2020\200501_Film12\
  scan_001.tif            (23.5 MB)    ->      scan_001.tif.jxl                 (14.3 MB, lossless)
```

Measured on real files of the archive (2026-11-19, jxl-photo 2.30.2, the
profile `jxl` below, 4 conversions at a time: 16 TIFF, 1.8 GB, in 54 s):

| TIFF | Distance | Size in the backup | Back from the restore |
|---|---|---:|---|
| NX Studio, 16-bit, 45 MP (2 pages) | 0.05 | 15.5% | same pages and date; mean difference 0.07%, the largest single pixel 6.5% |
| film scan, 16-bit, 41 MP | 0 (lossless) | 71% | the same pixels |
| film scan, 8-bit (SilverFast) | 0 (lossless) | 59% | the same pixels |

Lossless gives back the same pixels, not the same bytes: the TIFF the restore
makes is a new file (its own compression and layout), so its SHA-256 differs
from the original's.

## Turning it on

1. Tell photo-backup where jxl-photo is: `JXLPHOTO` at the top of
   `selective_backup.py` (`jxl_photo.py` of a jxl-photo folder, or
   `jxl-photo.exe`), or `--jxlphoto PATH` on the command line.
2. Choose a profile per manifest line, in the optional `Tiff` column
   ([per_folder_modes.md](per_folder_modes.md)): `copy` (empty: the TIFF as it
   is) or the name of a profile of `TIFF_JXL_PROFILES`:

   ```csv
   Folder,Mode,Originals,Mirror,Tiff
   G:\2025,6,,selection,jxl
   G:\2019,3+,raw,,copy
   ```

   Without a manifest: `selective_backup.py --tiff jxl` (or
   `mirror_backup.py --source ... --tiff jxl`).

A profile names three distances; jxl-photo decides which one each TIFF gets:

```python
TIFF_JXL_PROFILES = {
    "jxl": {"distance": 0.05,         # any other TIFF (a developed 16-bit TIFF)
            "scan_distance": 0,       # a film scan (jxl-photo knows a scanner's TIFF)
            "eight_bit_distance": 0}, # an 8-bit TIFF: lossless is already small
}
```

0 is lossless. Add profiles of your own (`"jxl-archive": {...}`) and name them
in the `Tiff` column; a line can use one profile and another line another.

**Which TIFFs.** Every `.tif` / `.tiff` (`TIFF_EXTENSIONS`) of a line with a
profile, except an export: a TIFF in a folder whose name contains
`EXPORT_NAME_CONTAINS` stays a TIFF, also in a mode that copies the whole
folder (an export is a delivery: kept as it was made).

**The name in the backup** is the TIFF's own name plus `.jxl`
(`photo.tif.jxl`; the other pages of a multipage TIFF `photo_page1.tif.jxl`,
...): `photo.tif` and `photo.tiff` never meet, a `photo.jxl` that is already a
photo of yours is never confused with it, and Capture One's settings
(`photo.tif.cos`) still say which photo it is.

## How a JXL is trusted

jxl-photo converts each TIFF into a folder of its own inside
`_photo_backup\converting` (`TIFF_JXL_FOLDER`), with `--verify-roundtrip`
(it decodes what it wrote and compares). photo-backup then keeps the result
only when:

- jxl-photo ended without an error and wrote at least one JXL;
- every JXL says it was made from the very bytes this run read: its
  `jxlphoto-srcsum` mark is the SHA-256 of the TIFF, taken by photo-backup
  before the conversion;
- a multipage TIFF came back whole (`jxlphoto-pages` = the number of files);
- the TIFF has the same size and date at the end of the conversion.

Then the JXL files take their final names and the catalog records each one
(its own size, date and SHA-256) and the TIFF it came from (size, date,
SHA-256, page, pages, jxl-photo's version and the profile).

**Anything else copies the TIFF as it is**, under its own name: jxl-photo
missing or failing, a mark that does not match, a group not whole, a TIFF that
changed while it was converted, `TIFF_JXL_TIMEOUT_MIN` passed. The log says
`CONVERT FAILED <file>: <why>`, the summary counts it, and the run exits 1.
Nothing is left without a copy; the next run tries again and, once it works,
that TIFF copy leaves (below).

## A second run

- **Nothing changed**: the JXL is up to date (`N up to date` in the summary),
  nothing is converted again.
- **The TIFF was edited**: CHANGED. With `--update-changed` (always in the
  mirror) it is converted again; the old JXL goes to `versions\` (the
  mirror: to its trash).
- **Another profile** (or other distances, or another `TIFF_JXL_EFFORT`):
  CHANGED, converted again the same way. The catalog keeps the settings each
  JXL was made with.
- **A page missing in the backup**: the group is made again.

## A backup that already holds the TIFF

When a line gets a profile, the TIFF copies already in the backup are
replaced: the JXL is written first, recorded, and only then the TIFF copy
leaves — to `versions\` in a normal backup, to the trash in a mirror. That
holds for a copy this tool wrote and for one another program wrote with the
source's size and date (FreeFileSync, robocopy): the same files a run adopts
as its own. A TIFF in the backup that differs from the source is not a copy
of it and is left where it is.

The summary says how many before the question: `TIFF copies in the backup
that their JXL replaces: N (to versions\)`.

## Moving from FreeFileSync to the mirror

FreeFileSync and a backup with JXL do not mix: FreeFileSync would copy the
TIFFs back (the source has TIFF) and, mirroring, delete the JXL (the source has
no JXL). To keep TIFF as JXL in a backup FreeFileSync made, let photo-backup's
mirror take that backup over instead: a manifest line with mode `6` (the whole
folder), `Mirror` and `Tiff=jxl` ([mirror.md](mirror.md)). Its first run:

- adopts every file FreeFileSync copied with the source's size and date —
  nothing is copied again;
- converts the TIFFs and moves FreeFileSync's TIFF copies to the trash
  (`MIRROR_KEEP_DAYS` days, then deleted);
- leaves anything FreeFileSync copied that is not in the plan (a Capture One
  `Cache`, say) where it is: not written by the backup, never removed.

From then on, do not point FreeFileSync at that backup again.

## Restoring

`restore_backup.py` (or the menu's `[5] Restore`) gives the TIFF back under
its own name ([restoring.md](restoring.md)): it copies the JXL files of the
TIFF next to the target (each one checked against its catalog SHA-256, never
written in the backup), runs jxl-photo's decoder there (`decode --mode 0
--depth-policy preserve_original --no-preview`: the TIFF's own pages and bit
depths, no preview page added) and gives the one TIFF it makes the target's
name, never replacing a file, with the original's date. The outcome is
`RESTORED_FROM_JXL`.

- jxl-photo comes from `--jxlphoto` / `JXLPHOTO`; when that is empty or not
  found, the restore uses the newest one kept in the backup (below) and says
  so.
- No jxl-photo at all: `NEEDS_JXLPHOTO` for those files (the others are
  restored), exit 1. `--keep-jxl` restores the JXL files as they are.
- `--only` takes the TIFF's name (`2025\shoot\photo.tif`) or a JXL's.

## Verify

A `--verify` checks a JXL as it checks any copy (size, date; `full`: its
SHA-256), and its source against the TIFF the catalog recorded (`full`: the
TIFF's SHA-256). `full` also reads the JXL's mark: a JXL that does not say it
came from that TIFF is `DEST_NOT_FROM_SOURCE` ([verifying.md](verifying.md)).

## Space

The space check counts each TIFF to convert at its full size (the JXL is
smaller: the room is there for the worst case). `manifest_backup.py --analyze`
adds the column `tiff_to_jxl_bytes`: how much of each line is TIFF that its
profile would keep as JXL.

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
| `--jxlphoto PATH` | every script but the menu | jxl-photo's `jxl_photo.py` or `jxl-photo.exe` (default `JXLPHOTO`) |
| `--keep-jxl` | `restore_backup.py` | restore the JXL as it is instead of a TIFF |

At the top of `selective_backup.py`:

| Setting | Default | Meaning |
|---|---|---|
| `JXLPHOTO` | `""` | jxl-photo's `jxl_photo.py` or `jxl-photo.exe`; `""` = no conversion (a line with a profile is refused, exit 2) |
| `TIFF_JXL_PROFILES` | `{"jxl": ...}` | the profiles the `Tiff` column (or `--tiff`) names: `distance`, `scan_distance`, `eight_bit_distance` (0 = lossless) |
| `TIFF_EXTENSIONS` | `(".tif", ".tiff")` | what a profile converts (never in an export folder) |
| `TIFF_JXL_EFFORT` | `7` | jxl-photo's `--effort` |
| `TIFF_JXL_WORKERS` | `4` | TIFFs converted at the same time (one jxl-photo each) |
| `TIFF_JXL_TIMEOUT_MIN` | `30` | a conversion longer than this is given up: the TIFF is copied as it is |
| `TIFF_JXL_FOLDER` | `"converting"` | the conversions' own folder, under `_photo_backup` (emptied by each run) |
| `KEEP_TOOLS_IN_BACKUP` | `True` | keep photo-backup, jxl-photo and `KEEP_TOOLS_EXTRA` in the backup |
| `KEEP_TOOLS_EXTRA` | `()` | more files or folders kept there |
| `TOOLS_FOLDER` | `"tools"` | where, under `_photo_backup` |

jxl-photo runs with its own logs in photo-backup's log folder
(`Logs\jxl-photo\`), without its update check, and without writing anything
next to itself.
