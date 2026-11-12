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
| `--allow-mass-delete` | let a run delete more of the mirror than `MAX_DELETE_PERCENT` allows, when those photos really are gone from the sources *(v0.1.4)* |
| `--version` | print the version |

Exit codes:

- `0` done.
- `1` some file failed, an album was skipped, a folder of a source or of the
  mirror could not be read, exiftool gave no answer for a file, a run that
  would delete too much was stopped, or the state was taken from its backup.
- `2` nothing done: bad arguments, a destination and a source that contain
  each other (also through a junction or a `subst` letter, *v0.1.4*), a
  destination that does not answer or is not a folder, another run working on
  the mirror *(v0.1.4)*, a state that cannot be read with no readable backup,
  not enough space.
- `130` interrupted.

A `--dry-run` exits with the code its real run would give for the same plan:
`1` when an album would be skipped *(v0.1.3)* or when the run would be stopped
as a mass delete *(v0.1.4)*. The destination is checked before the sources are
read, so a drive that is not connected costs nothing *(v0.1.3)*.

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
- **links are not followed** *(v0.1.4)*. A link or junction inside a source is
  never walked: before, one pointing to a folder above it was a loop, and the
  Capture One cache folders moved to another drive (`CaptureOne\Cache` as a
  junction) were walked on every run. Each link is in the log; the ones
  that are not a Capture One cache are counted on the screen (what is behind
  them is not mirrored). A photo of the mirror whose path is behind such a link
  is left alone, as a photo of a source not given: not proof that it is gone.
- **a mirror folder that answers.** Only "does not exist" proves that a mirror
  file is not there *(v0.1.4)*. An album folder of the mirror that cannot be
  read (access denied, a USB disk that drops out and comes back) is named, and
  nothing is copied, renamed, deleted or forgotten there in that run (exit 1).
  Before, its entries were forgotten, and the files became "not ours" for good.
- **not too much at once** *(v0.1.4)*. A run in which the photos gone from its
  sources outnumber its new photos by more than `MAX_DELETE_PERCENT` (20%) of
  the photos the mirror keeps for those sources, and by at least
  `MAX_DELETE_MIN` (5), changes nothing: it says how many it would delete and
  exits 1. That is what a `JXL_mobile` folder renamed (a jxl-photo setting) or
  another disk mounted on the source's letter looks like, and the sync would
  then empty the phone. Photos moved (an event folder renamed: as many new as
  gone) do not count. If the photos really are gone, run again with
  `--allow-mass-delete`; `MAX_DELETE_PERCENT = 0` turns the check off.

One run at a time *(v0.1.4)*: a run holds `<dest>\.mobile_backup\run.lock`
(`LOCK_FILE`) while it works. A second run on the same mirror exits 2 and
changes nothing; before, the run that finished last saved the state it had read
at its start, the other run's copies were forgotten, and their album was
skipped from then on. A lock left by a run that is no longer running (killed,
the PC turned off) is taken over, and the log says so. A `--dry-run` never
takes the lock, but stops (exit 2) while another run holds it.

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
it *(v0.1.3; before: a traceback)*. When it must be deleted because its photo
was exported again under a new number, the new copy waits too *(v0.1.4)*: the
state keeps the old file, and the next run copies the new one and deletes the
old one. Before, the new copy was made, the state forgot the old file, and it
stayed in the album next to the new one (then the album was skipped for good).

What this job writes is on the disk before it gets its name *(v0.1.4)*: every
copy and every save of the state is flushed (`fsync`) first, so a power cut
leaves the old file or the new one, never a file of the right size with other
bytes (the plan checks the size, and would never copy it again). Every finished
run also keeps `state.json.bak` (`STATE_BACKUP_SUFFIX`), a copy of the state
it leaves. When `state.json` cannot be read, the run says so and uses the
backup: the photos copied after it are not known, so that run deletes, renames
and forgets nothing, copies only new photos to free names, keeps the unreadable
file as `state.json.unreadable`, and exits 1; the next run is a normal one.
Without a readable backup it stops (exit 2), as before. A `state.json` held
for a moment by an antivirus or an indexer is tried again
(`STATE_REPLACE_TRIES`, `STATE_REPLACE_WAIT_S`) instead of stopping the run.

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

**FreeFileSync:** exclude `\.mobile_backup\` and the files of a run in progress, `*.mbtmp;*.mbren` *(v0.1.4)*, from the sync to the phone.
A renumbered photo shows up there as delete + new file. That is cheap for
2048 px JXLs.

## Settings (top of the script)

| Setting | Default | What it does |
|---|---|---|
| `SOURCES` | `""` | `;`-separated default for `--source` |
| `DESTINATION` | `""` | default for `--dest` |
| `MOBILE_FOLDER_NAMES` | `JXL_mobile` | `;`-separated folder names (any case) whose files are mirrored |
| `EXTENSIONS` | `.jxl .jpg .jpeg .heic .avif .png` | the files of a mobile folder that are mirrored |
| `EVENT_NAME_PATTERN` | `^\d{6}` | the album is the nearest folder above the mobile folder whose name matches |
| `EXPORT_NAME_CONTAINS` | `export` | without a match: the nearest folder above whose name does not contain this |
| `SYSTEM_SKIP_FOLDERS` | `$RECYCLE.BIN;System Volume Information` | folders never walked |
| `CAPTUREONE_FOLDER_NAMES` | `CaptureOne` | with `CAPTUREONE_CACHE_NAMES`: a link with a cache's name directly inside such a folder is a Capture One cache on another drive, counted only in the log *(v0.1.4)* |
| `CAPTUREONE_CACHE_NAMES` | `Cache` | see `CAPTUREONE_FOLDER_NAMES` |
| `MAX_DELETE_PERCENT` | `20` | a run whose photos gone outnumber its new ones by more than this % of the mirror's photos of those sources changes nothing (exit 1); `0` = no check *(v0.1.4)* |
| `MAX_DELETE_MIN` | `5` | ... and by at least this many photos *(v0.1.4)* |
| `NUMBER_DIGITS` | `4` | `0000_`; widened automatically for an album of 10 000+ photos |
| `NUMBER_SEPARATOR` | `_` | between the number and the file name |
| `STATE_FOLDER` | `.mobile_backup` | under the destination: the state, its backup and the lock |
| `STATE_FILE` | `state.json` | the files this job wrote |
| `STATE_BACKUP_SUFFIX` | `.bak` | `state.json.bak`: the state the last finished run left *(v0.1.4)* |
| `LOCK_FILE` | `run.lock` | held while a run works on the mirror *(v0.1.4)* |
| `STATE_REPLACE_TRIES` | `5` | tries to put a new `state.json` in place *(v0.1.4)* |
| `STATE_REPLACE_WAIT_S` | `0.2` | wait before try n+1: n times this, in seconds *(v0.1.4)* |
| `STATE_SAVE_EVERY` | `20` | copies between two saves of the state |
| `STATE_SAVE_SPREAD` | `200` | in a long run, 1/200 of the copies done so far, when that is more; `0` = always `STATE_SAVE_EVERY` |
| `PROGRESS_EVERY` | `200` | copies between two progress lines |
| `TEMP_SUFFIX` | `.mbtmp` | a copy in progress, next to its final name |
| `RENAME_SUFFIX` | `.mbren` | a file between its old and new number |
| `SPACE_MARGIN_GB` | `5.0` | free space kept on the destination |
| `EXIFTOOL` | `exiftool` | the ExifTool command (or its path) |
| `EXIFTOOL_BATCH` | `200` | files per ExifTool call |
| `EXIFTOOL_TIMEOUT` | `600` | seconds per batch |
| `LOG_DIR` | `Logs\` next to the script | where `<stamp>_mobile.log` goes (or `PHOTOBACKUP_LOG_DIR`) |

This page describes `mobile_backup.py` 0.1.4.
