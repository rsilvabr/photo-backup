# mobile_backup.py — phone albums by event

A phone gallery shows every folder as an album, with only the folder's name.
An archive laid out as `<event>\<camera>\_EXPORT\JXL_mobile\` therefore turns
into dozens of albums all called `JXL_mobile`. `mobile_backup.py` mirrors those
folders to another disk with **one album per event**, the photos numbered in
the order they were shot:

```text
G:\2026\260620-23_Kyoto\1_Z8\_EXPORT\JXL_mobile\DSC_0100_sRGB_v1.jxl
G:\2026\260620-23_Kyoto\2_Z8\_EXPORT\JXL_mobile\_DSC_0001_sRGB_v1.jxl
G:\2026\260620-23_Kyoto\2_Z8\_EXPORT\JXL_mobile\_DSC_0002_sRGB_v1.jxl

H:\MOBILE\2026\260620-23_Kyoto\0000__DSC_0001_sRGB_v1.jxl   12:26:19
H:\MOBILE\2026\260620-23_Kyoto\0001_DSC_0100_sRGB_v1.jxl    12:28:00
H:\MOBILE\2026\260620-23_Kyoto\0002__DSC_0002_sRGB_v1.jxl   12:30:00
```

Then sync the mirror to the phone with FreeFileSync (or anything else).

## Usage

```text
python mobile_backup.py --source G:\2024 --source G:\2025 --source G:\2026 --dest H:\MOBILE --dry-run
python mobile_backup.py --source "G:\2024;G:\2025;G:\2026" --dest H:\MOBILE
```

| Option | Meaning |
|---|---|
| `--source` | a source root; repeat it or separate with `;` (default: `SOURCES`) |
| `--dest` | the mirror's root (default: `DESTINATION`) |
| `--dry-run` | print the plan (details in the log), change nothing |
| `--version` | print the version |

Exit codes: `0` done, `1` some file failed or an album was skipped, `2`
nothing done (bad arguments, destination inside a source, unreadable state,
not enough space), `130` interrupted.

## How it decides

- **Mobile folder**: a folder named like `MOBILE_FOLDER_NAMES` (any case).
  Only its own files with an extension in `EXTENSIONS` are mirrored.
- **Album**: the nearest folder above the mobile folder whose name matches
  `EVENT_NAME_PATTERN` (default: starts with six digits, `YYMMDD`). Camera
  folders (`1_Z8`, `01_D810`) and `_EXPORT` disappear; in a nested event
  (`251219-31_TRIP\251231_Teresina\01_D810`) the album is the inner one. A
  folder without a date falls back to the folder above the export folder. The
  mirror keeps the album's full path below the drive (`2026\<event>`).
- **Number**: shot order inside the album, across cameras, from
  `DateTimeOriginal` + `SubSecTimeOriginal`; same time → by file name; no date →
  last, by name. Four digits (`NUMBER_DIGITS`), more for an album of 10 000+.
  Cameras whose clocks disagree sort by their own clocks.

## Sync rules

The destination is a **mirror**, unlike `selective_backup.py`, which never
deletes: every phone file can be made again from the archive.

- new photo → copied (temp file + size check + rename into place);
- changed photo (size or modification time) → copied again;
- a photo added or removed in the middle → the following ones are **renamed**
  to their new number, not copied again;
- photo gone from the source (or its event folder renamed) → deleted from the
  mirror; an album folder left empty is removed.

Only files this job wrote are ever renamed or deleted. It records them in
`<dest>\.mobile_backup\state.json`. Anything else in an album folder is
left alone. If a new number would land on such a file, the whole album is
skipped and reported. The same happens when one of our files was modified on
the destination. The sources are only read.

The state also keeps each photo's shot time, so a re-run reads exiftool only
for new or changed files.

**FreeFileSync:** exclude `\.mobile_backup\` from the sync to the phone. A
renumbered photo shows up there as delete + new file. That is cheap for 2048 px
JXLs.

## Settings (top of the script)

`SOURCES`, `DESTINATION`, `MOBILE_FOLDER_NAMES`, `EXTENSIONS`,
`EVENT_NAME_PATTERN`, `EXPORT_NAME_CONTAINS`, `NUMBER_DIGITS`,
`NUMBER_SEPARATOR`, `STATE_FOLDER`, `SPACE_MARGIN_GB`, `EXIFTOOL`,
`EXIFTOOL_BATCH`, `EXIFTOOL_TIMEOUT`. Logs go to `Logs\<stamp>_mobile.log`
(or `PHOTOBACKUP_LOG_DIR`).
