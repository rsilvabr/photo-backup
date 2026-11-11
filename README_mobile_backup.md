# mobile_backup.py — phone albums by event

A phone gallery shows every folder as an album, with only the folder's name.
An archive laid out as `<event>\<camera>\_EXPORT\JXL_mobile\` therefore turns
into dozens of albums all called `JXL_mobile`. `mobile_backup.py` mirrors those
folders to another disk with **one album per event**, the photos numbered
camera by camera, each camera in the order it shot:

```text
G:\2026\260620-23_Kyoto\1_Z8\_EXPORT\JXL_mobile\DSC_0100_sRGB_v1.jxl
G:\2026\260620-23_Kyoto\2_Z8\_EXPORT\JXL_mobile\_DSC_0001_sRGB_v1.jxl
G:\2026\260620-23_Kyoto\2_Z8\_EXPORT\JXL_mobile\_DSC_0002_sRGB_v1.jxl

H:\MOBILE\2026\260620-23_Kyoto\0000_DSC_0100_sRGB_v1.jxl     1_Z8
H:\MOBILE\2026\260620-23_Kyoto\0001__DSC_0001_sRGB_v1.jxl    2_Z8
H:\MOBILE\2026\260620-23_Kyoto\0002__DSC_0002_sRGB_v1.jxl    2_Z8
```

Then sync the mirror to the phone with FreeFileSync (or anything else).

**Requires [ExifTool](https://exiftool.org)** on the `PATH` (or its path in
`EXIFTOOL` at the top of the script): it reads the shot time of each photo.
Without it, or when it fails or times out for a file, that file is numbered by
name for now, the run exits 1, and the next run reads it again *(v0.1.2)*.

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

Exit codes: `0` done, `1` some file failed, an album was skipped, a folder
of a source could not be read, or exiftool gave no answer for a file, `2`
nothing done (bad arguments, destination inside a source, a destination that
does not answer or is not a folder, unreadable state, not enough space), `130`
interrupted. A `--dry-run` exits with the code its real run would give for the
same plan: `1` when an album would be skipped *(v0.1.3)*. The destination is
checked before the sources are read, so a drive that is not connected costs
nothing *(v0.1.3)*.

## How it decides

- **Mobile folder**: a folder named like `MOBILE_FOLDER_NAMES` (any case).
  Only its own files with an extension in `EXTENSIONS` are mirrored.
- **Album**: the nearest folder above the mobile folder whose name matches
  `EVENT_NAME_PATTERN` (default: starts with six digits, `YYMMDD`). Camera
  folders (`1_Z8`, `01_D810`) and `_EXPORT` disappear; in a nested event
  (`251219-31_TRIP\251231_Teresina\01_D810`) the album is the inner one. A
  folder without a date falls back to the folder above the export folder. The
  mirror keeps the album's full path below the drive (`2026\<event>`). The
  album depends only on where the photo is, not on the `--source` given: the
  search goes up to the drive *(v0.1.3; before, `--source` on a camera folder
  inside an event made that camera an album of its own)*.
- **Number**: camera by camera — the mobile folders in the natural order of
  their paths (`1_Z8` before `2_Z8` before `10_…`), so the folder numbers you
  give your cameras set the order. Inside each camera: `DateTimeOriginal` +
  `SubSecTimeOriginal`; same time → by file name; no date → last of that
  camera, by name. Four digits (`NUMBER_DIGITS`), more for an album of 10 000+.
- **Why not interleave the cameras by time?** Their clocks disagree: even two
  bodies of the same model drift minutes apart, old cameras carry 1970/2006
  dates, and a body still on the home time zone is hours off on a trip.

## Sync rules

The destination is a **mirror**, unlike `selective_backup.py`, which never
deletes: every phone file can be made again from the archive.

- new photo → copied (temp file + size check + rename into place). The copy
  keeps the photo's date and never its read-only attribute. A photo that
  changes while it is being copied is an error (exit 1); the next run copies
  it *(v0.1.3)*;
- changed photo (size or modification time) → copied again;
- a photo added or removed in the middle → the following ones are **renamed**
  to their new number, not copied again;
- photo gone from the source (or its event folder renamed) → deleted from the
  mirror; an album folder left empty is removed.

A delete needs proof *(v0.1.2)*:

- **only the sources of this run.** A photo is deleted or renumbered only
  when its path is under one of the `--source` folders of this run. A run with
  fewer sources (`--source G:\2025` alone) never touches the photos of the
  others (`G:\2024`) and says how many it left alone; an album whose new
  numbers would land on one of them is skipped. This works the same with a
  state written before v0.1.2. A run given only a part of an event
  (`--source` on one camera folder) numbers that part inside the whole album:
  the album's other photos, known from the state, keep their place in the
  count and are not touched *(v0.1.3)*.
- **a source that was read in full.** A folder that cannot be read (a disk
  that drops out, access denied) would look empty and its photos gone: the run
  names the folder, deletes and renames nothing, copies only new photos to free
  names, and exits 1.

Only files this job wrote are ever renamed or deleted. It records them in
`<dest>\.mobile_backup\state.json`. Anything else in an album folder is
left alone. If a new number would land on such a file, the whole album is
skipped and reported. The same happens when one of our files was modified on
the destination. The sources are only read.

What the state records *(v0.1.3)*:

- **what was really copied.** The size and date a photo had while its bytes
  were read, not what the scan saw minutes earlier. Before, a photo exported
  again while the run was reading the shot times left its album skipped in
  every later run. Its shot time is read again on the next run.
- **nothing for a photo that is nowhere.** An entry whose source is gone and
  whose mirror file does not exist is dropped. Before, it stayed and later
  deleted the file of whatever photo was copied to that name. A mirror file
  that a photo still in the source holds is never deleted in the name of
  another entry.
- **the same folder typed in another case** (`g:\2026` after `G:\2026`) is the
  same photos: nothing is read, deleted or copied again.

A file of the mirror that is read-only (older versions carried the attribute
over from a read-only source) is still replaced or deleted when it is ours: in
the state, with the size this job wrote. A read-only file that is not ours is
never touched. A mirror file open in another program when it must be
renumbered keeps its name; the run goes on, exits 1, and the next run renames
it *(v0.1.3; before: a traceback)*.

The state also keeps each photo's shot time, so a re-run reads exiftool only
for new or changed files, and for those it did not answer for last time. A
state written before v0.1.2 may hold "no date" for a photo only because
exiftool failed then: every such photo is read once more, and a photo really
without a date is not read again after that.

The state is saved right after the renames and every `STATE_SAVE_EVERY` (20)
copies, not only at the end: a run killed in the middle (not Ctrl+C, which
saves it too) no longer leaves the album skipped forever behind files the
state does not know. In a long run the saves spread out *(v0.1.3)*: after the
first 4 000 copies, one save per 1/`STATE_SAVE_SPREAD` (1/200) of the copies
done so far (every 50 copies at 10 000, every 100 at 20 000). Every save
rewrites the whole state, and at a fixed interval a first run of 20 000 photos
spent three quarters of its time on that. `STATE_SAVE_SPREAD = 0` keeps the
fixed interval. Two short windows remain: a kill in the middle of the
renames, or after a copy reached its final name and before the next save. The
album is then skipped (`SKIPPED album ...`, exit 1) until that file is removed
from the mirror by hand; nothing is lost.

**FreeFileSync:** exclude `\.mobile_backup\` from the sync to the phone. A
renumbered photo shows up there as delete + new file. That is cheap for 2048 px
JXLs.

## Settings (top of the script)

`SOURCES`, `DESTINATION`, `MOBILE_FOLDER_NAMES`, `EXTENSIONS`,
`EVENT_NAME_PATTERN`, `EXPORT_NAME_CONTAINS`, `NUMBER_DIGITS`,
`NUMBER_SEPARATOR`, `STATE_FOLDER`, `STATE_SAVE_EVERY`, `STATE_SAVE_SPREAD`,
`PROGRESS_EVERY`, `SPACE_MARGIN_GB`, `EXIFTOOL`, `EXIFTOOL_BATCH`,
`EXIFTOOL_TIMEOUT`. Logs go to `Logs\<stamp>_mobile.log`
(or `PHOTOBACKUP_LOG_DIR`).
