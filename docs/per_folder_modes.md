# Per-folder modes (`manifest_backup.py`)

One run can use a different rule for each folder: one set of years in one mode,
another set in another. Every mode starts from the export selection of
[how_it_selects.md](how_it_selects.md) and adds to it.

## The two CSV files

Two CSV files say what to do:

- **The manifest** (`--manifest`, required) lists the folders:
  `Folder,Mode,Originals`, and optionally `Mirror` (`disk` or `selection`),
  which only [the mirror](mirror.md#the-mirror-column) reads. The owner's real
  manifest (`backup_manifest.csv`) stays out of git;
  `backup_manifest.example.csv` shows the format.
- **The modes table** (`--modes`, default `backup_modes.csv` beside the
  script) says what each mode copies:
  `Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,Only,Skip,LeaveOut,Description`.

| Column | Meaning |
|---|---|
| `Mode` | the name used in the manifest's `Mode` column |
| `Unselected` | `yes`/`no`: also copy the originals of the photos that were not exported |
| `AddFolders` | `;`-separated patterns of direct subfolders of a folder with an export folder (with or without an original beside it), copied with the filter of `Originals`. A folder inside it that has an export folder is a session (its exports and their originals are copied too), and a file is copied when either the session or the filter keeps it, also inside those export folders. What neither keeps is counted: `left out inside added folders (Originals filter)`. One exception copies less than v0.12.0: a `Cache` folder deeper than the first level of a `CaptureOne` folder inside such an export folder is counted as Capture One cache (it is made again) |
| `AddWhole` | `;`-separated patterns of direct subfolders of a folder with an export folder (with or without an original beside it), copied whole |
| `Exclude` | `;`-separated patterns of folders never walked, at any depth and in every mode; what they hold is counted: `excluded by the mode (Exclude)`. The export, Capture One and NKSC_PARAM folders of a session are not affected (inside an export folder, use `--exclude-export-subfolders`) |
| `Whole` | `yes`/`no`: the whole folder, session or not |
| `Unexported` | `yes`/`no`, optional: a folder without an export is copied whole; a folder with one follows the rest of the mode |
| `Only` | optional, `;`-separated: copy only these types. A group (`raw`, `jpeg`, `jpg`, `jxl`, `tif`, `png`, `heic`, `psd`, `dng`) or an extension (`.xmp`) |
| `Skip` | `yes`/`no`, optional: this folder stays out of the backup |
| `LeaveOut` | optional, `;`-separated patterns *(v0.13.0)*: the direct subfolders of a session (a folder with an export folder and an original beside it) left out, with everything below them — "inside sessions", `_inside.csv`. Any other direct subfolder of a session is judged alone: a session if it holds an export folder, a folder without an export if not (copied whole with `Unexported=yes`, listed in `_unexported.csv` without). Checked after `Exclude`, `AddWhole` and `AddFolders`: `_OUTTAKES_KEEP` matches `_OUTTAKES*` and mode 3 still adds it. An empty cell leaves nothing out. A table without the column (written before v0.13.0) reads it as `*`: every subfolder, as v0.12.1. With `Whole=yes` or `Skip=yes` it must be empty (they walk no session; abort, exit 2) |
| `Description` | free text shown nowhere; for the owner |

A pattern is matched against the folder name, case-insensitively; only `*` is
a wildcard, everything else is literal (so `[FINAL]` is a name, not a
character class). `yes`/`no` are case-insensitive.

**Leaving out one more name inside sessions.** Add it to `LeaveOut`: with
`_OUTTAKES*;rejects`, a session's `rejects` folder is left out too, in that
mode, counted under "inside sessions" and listed in `_inside.csv`. Without it,
`rejects` is judged alone (copied whole in a `+` mode).

**`Originals`** chooses how the photos that were not exported are kept:

| Value | Copies |
|---|---|
| `raw` (empty = this) | RAW + sidecars + every JPEG, TIFF, PNG, HEIC or PSD that has no RAW of the same photo beside it (`DSC_0001 (2).JPG` is its own photo) |
| `raw+jpeg` | `raw` + every JPEG |
| `all` | everything |

## Lines inside other lines

**The most specific line wins.** With `G:\2025` in mode 3 and
`G:\2025\evento` in mode 6, the event follows mode 6 only; the mode 3 line
never plans a file inside it. An export folder cannot have its own line below
another line (the line above would no longer see its session's export). A
folder repeated in the manifest aborts (exit 2), as does a file that is not
UTF-8 (in Excel, save as "CSV UTF-8"), a mode that does not exist, a folder that does not exist, folders
on more than one drive, a column the table does not know (a header typed
`Unexported ` with a space would otherwise be ignored and the mode would lose
its `+`), an empty column name in the header (an extra comma at the end of
every line, often a cell left in Excel: the message says so) or a line with
more values than columns *(v0.12.1)*, and every other
invalid table value — before any photo is read.

**Leaving a folder out, or keeping one type.** Give that folder its own line
below the larger one, with one of the modes `skip`, `raw-only`, `jpeg-only` or
`jxl-only`:

```csv
Folder,Mode,Originals
G:\2026,3+,raw
G:\2026\event I care less about,skip,
G:\2026\event from a phone,jpeg-only,
```

`Only` is literal: `jpeg-only` copies the JPEGs and nothing else, so a photo that
exists only as RAW has no copy, and `jxl-only` leaves out a JPEG not converted
yet. `raw-only` also keeps the sidecars and edit settings. The summary shows
what these lines left out, and `--analyze` shows every mode of such a folder.

**Lowering a mode deletes nothing.** A file that a lower mode no longer
selects stays on the destination and appears as a `not_selected` orphan in
`_orphans.csv`, exactly like a normal backup.

## The modes shipped

The modes shipped in `backup_modes.csv`:

```csv
Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,LeaveOut,Description
1,no,,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,_OUTTAKES*,Exports and the originals of the exported photos
2,yes,,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,_OUTTAKES*,Mode 1 + unselected originals
3,yes,_OUTTAKES_KEEP,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,_OUTTAKES*,Mode 2 + _OUTTAKES_KEEP
4,yes,_OUTTAKES_KEEP;_OUTTAKES,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,_OUTTAKES*,Mode 3 + _OUTTAKES
5,yes,_OUTTAKES*,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,_OUTTAKES*,Mode 4 + every _OUTTAKES*
6,no,,,,yes,no,,The whole folder
```

(Shown without the `Only` and `Skip` columns, which are empty in these six.)

In modes 1 to 5 a session leaves out its `_OUTTAKES*` folders (those the mode
does not add); every other subfolder of a session is judged alone *(v0.13.0)*.
Modes `1+` to `5+` are the same five with `Unexported=yes`: a folder with an
export follows the mode, and a folder not exported yet is copied whole — also
a subfolder of a session that `LeaveOut` does not name — so what has not been
edited or selected is not left out.

A bigger mode never copies less *(v0.12.1)*: whatever mode `N` copies, mode
`N+1` and mode `N+` copy too (`_OUTTAKES\_EXPORT` is a session in mode 3 and
stays one when mode 4 adds `_OUTTAKES`). The test suite checks it on a tree with
outtakes, exports inside outtakes, film scans, folders not exported yet and
session subfolders without a special name, with and without an export of their
own *(v0.13.0)*. A table without `LeaveOut`, or with `*`, plans exactly what
v0.12.1 planned on that tree.

Measured with `--analyze` on one 11 TB drive listed as a single line
(2026-11-09). The `all` column also carries loose TIFF, JXL and other files:

| Mode | What it copies | `raw` | `raw+jpeg` | `all` |
|---|---|---:|---:|---:|
| 1 | exports + originals of the exported photos + film scan folders | 1.37 TB | 1.37 TB | 1.37 TB |
| 2 | mode 1 + unselected originals | 2.41 TB | 2.68 TB | 2.88 TB |
| 3 | mode 2 + `_OUTTAKES_KEEP` | 2.50 TB | 2.80 TB | 3.00 TB |
| 4 | mode 3 + `_OUTTAKES` | 3.84 TB | 4.57 TB | 4.77 TB |
| 5 | mode 4 + every `_OUTTAKES*` | 4.25 TB | 5.12 TB | 5.33 TB |
| 6 | the whole folder, also what has no export (without the Capture One `Cache`) | | | 11.16 TB |

## Options

```console
python manifest_backup.py --manifest backup_manifest.csv --analyze
python manifest_backup.py --manifest backup_manifest.csv --dest "H:\BACKUP" --dry-run
python manifest_backup.py --manifest backup_manifest.csv --dest "H:\BACKUP"
python manifest_backup.py --manifest backup_manifest.csv --dest "H:\BACKUP" --verify full
```

From the menu: `[7] Manifest` ([menu.md](menu.md)).

| Option | Description |
|---|---|
| `--manifest PATH` | the manifest CSV (`Folder,Mode,Originals`); required |
| `--dest PATH` | destination root; required unless `--analyze` |
| `--modes PATH` | the modes table (default `backup_modes.csv` beside the script) |
| `--analyze` | print how many bytes each mode would take in each folder and write `<stamp>_analysis.csv`; copies nothing and needs no destination |
| `--dry-run` | plan, print the summary and write the logs/CSVs; copy nothing |
| `--yes` | do not ask for confirmation |
| `--update-changed` | move a changed destination file to the versions folder before copying the new one |
| `--no-auto-update` | do not update changed edit settings/sidecars (`AUTO_UPDATE_EXTENSIONS`) unless `--update-changed` is given |
| `--verify-copy {none,size,hash}` | verification of each copy (default: `hash`, from `selective_backup.py`) |
| `--space-margin-gb N` | free space kept on the destination, in GB (default: `20.0`, from `selective_backup.py`) |
| `--export-formats SPEC` | `all`, `a>b>c` fallback or `a+b` multiple (default: `jxl>tif>jpg`, from `selective_backup.py`) |
| `--expect-backup-id ID` | abort (exit 2) unless the destination holds this backup ID |
| `--verify {quick,full}` | verify the backup against what the manifest selects; copies nothing. Same statuses and exit codes as `selective_backup.py --verify` |
| `--max-gb N` | with `--verify full`: verify at most N GB in this run, oldest verification first (default: `0.0` = everything, from `selective_backup.py`) |
| `--version` | print the version and exit |
| `--help` | print the usage and exit |

## Settings

Settings at the top of `manifest_backup.py` *(listed since v0.13.2)*:

| Constant | Default | Meaning |
|---|---|---|
| `MODES_FILE` | `backup_modes.csv` | the modes table next to the script (`--modes` reads another) |
| `DEFAULT_ORIGINALS` | `raw` | what a manifest line with an empty `Originals` uses |
| `ORIGINALS_VALUES` | `raw` → `raw_or_jpeg`, `raw+jpeg` → `raw_jpeg`, `all` → `all` | the manifest's `Originals` words and what each one keeps |
| `ANALYSIS_ORIGINALS` | `raw`, `raw+jpeg`, `all` | the `Originals` columns of `--analyze` |
| `ONLY_GROUPS` | the export format groups + `raw`, `jpeg` | the names the `Only` column accepts |
| `MODE_COLUMNS`, `MODE_OPTIONAL_COLUMNS`, `MANIFEST_COLUMNS`, `MANIFEST_OPTIONAL_COLUMNS` | the column names | the headers of the two tables (a column name nobody knows stops the run) |
| `DEFAULT_MIRROR` | `disk` | what a manifest line with an empty `Mirror` means to `mirror_backup.py` |
| `MIRROR_VALUES` | `disk`, `selection` | the words the `Mirror` column accepts ([the mirror](mirror.md#the-mirror-column)) |

## Verify and analyze

Verify a backup made from a manifest with `manifest_backup.py --verify`, not
with `selective_backup.py --verify`: only the manifest knows which files each
line selects. A file a line selects that is not on the backup is
`NOT_BACKED_UP`; a folder a line leaves out is not reported.

`--analyze` plans every mode of the table with every `Originals` value through
the same `_Planner` the copy uses, sharing the folder scan between the modes,
and marks the mode chosen by each manifest line. A folder that could not be
read makes the numbers partial: the run says so and exits 1. The copy itself is
`selective_backup.py`'s `_run_backup`: the same catalog, space check, versions
folder, move detection and orphan report.
