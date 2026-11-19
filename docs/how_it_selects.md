# How the backup chooses what to copy

The long version of the README's "Why a selective backup?": how a session is
read, how an export finds its originals, which export format is kept, and what
happens to everything else. The per-folder modes add to this selection: see
[per_folder_modes.md](per_folder_modes.md).

## How the archive is organized

One folder per shoot, the originals directly in it, and the exports in a folder inside it whose name contains "export". Such a folder is a **session**. A typical one, and what a backup with the default settings takes from it:

```
G:\2025\250129_Flores\                        a session: it holds an export folder
├── DSC_9358.NEF                              ✗  not exported
├── DSC_9358.JPG                              ✗
├── DSC_9359.NEF                              ✗  not exported
├── DSC_9360.NEF                              ✓  exported (see _EXPORT): its RAW,
├── DSC_9360.JPG                              ✓  its camera JPEG
├── DSC_9360.xmp                              ✓  and its sidecar
├── DSC_9361.NEF                              ✗  not exported
├── _EXPORT\                                     any folder whose name contains "export"
│   ├── 16B_JXL\DSC_9360_ProPhoto-g22_v1.jxl  ✓  the best format of the photo (jxl>tif>jpg)
│   ├── TIFF16\DSC_9360_ProPhoto-g22_v1.tif   ✗  same photo, it has a JXL
│   └── sRGB\DSC_9360_sRGB.jpg                ✗  same photo, it has a JXL
├── CaptureOne\
│   ├── Settings166\                          ✓  the Capture One edits (.cos)
│   └── Cache\                                ✗  previews; Capture One rebuilds them
├── NKSC_PARAM\                               ✓  the NX Studio edits
└── _OUTTAKES\                                ✗  the rejects (a per-folder mode can add them)
```

**One photo through three programs.** A RAW developed in NX Studio into a TIFF beside it, that TIFF edited in Capture One and exported: everything is kept. From the real session the tool was tested on:

```
_DSC6491.NEF                                           ✓  the RAW
_DSC6491.JPG                                           ✓  the camera JPEG
_DSC6491.xmp                                           ✓  its sidecar
_DSC6491_NXstudio.tif                                  ✓  developed in NX Studio, beside the RAW
_DSC6491_NXstudio.xmp                                  ✓  its sidecar
NKSC_PARAM\_DSC6491.NEF.nksc                           ✓  the NX Studio edit of the RAW
CaptureOne\Settings166\_DSC6491_NXstudio.TIF.cos       ✓  the Capture One edit of the TIFF
_EXPORT\16B_JXL\_DSC6491_NXstudio_ProPhoto-g22_v1.jxl  ✓  exported from the edited TIFF
_EXPORT\TIFF16\_DSC6491_NXstudio_ProPhoto-g22_v1.tif   ✗  same photo, it has a JXL
```

The export is matched to the shortest original name it starts with, `_DSC6491`, and every file beside the export folder whose name starts with that name comes along. It makes no difference whether you exported from the RAW or from the TIFF. The TIFF beside the RAW is an original, so the export formats do not filter it; only the files inside `_EXPORT` are.

**A DNG from DxO's denoise.** DxO PureRAW and PhotoLab save the denoised photo as a DNG beside the RAW, named after it; the same rule keeps the whole chain:

```
DSC_9363.NEF                                                   ✓  the RAW
DSC_9363.JPG                                                   ✓  the camera JPEG
DSC_9363-DxO_DeepPRIME XD3.dng                                 ✓  the DNG DxO denoised from the RAW
DSC_9363-DxO_DeepPRIME XD3.xmp                                 ✓  its sidecar
CaptureOne\Settings1674\DSC_9363-DxO_DeepPRIME XD3.dng.cos     ✓  the Capture One edit of the DNG
_EXPORT\16B_JXL\DSC_9363-DxO_DeepPRIME XD3_ProPhoto-g22_v1.jxl ✓  exported from the edited DNG
_EXPORT\TIFF16\DSC_9363-DxO_DeepPRIME XD3_ProPhoto-g22_v1.tif  ✗  same photo, it has a JXL
```

The export's key is `DSC_9363`, the shortest original name it starts with, so the RAW comes along even when only the DNG was exported (checked on a real session of 2026 with ten such DNGs: every one kept with its RAW). Keep the DNG beside the RAW: DxO can also save into a subfolder, and a file in a subfolder is not an original of the session. A DNG is a RAW type (`RAW_EXTENSIONS`), so a mode that also keeps the originals that were not exported keeps a DNG that was not exported too.

Sessions are found at any depth. A folder without an export is not copied, but it is listed:

```
G:\2025\
├── 250129_Flores\        session (holds _EXPORT)
├── 250214_Trip\
│   ├── 01_D810\          session (holds Lightroom_Export)
│   └── 02_D200\          session (holds _EXPORT)
└── 250301_New\           no export yet: listed in _unexported.csv; copied whole
                          with --unexported copy or a "+" mode
```

The tree is mirrored on the destination (`G:\2025\250214_Trip\01_D810\DSC_0001.NEF` → `H:\BACKUP\2025\250214_Trip\01_D810\DSC_0001.NEF`), so `--source G:\` and `--source G:\2025` produce the same layout and later runs add to the previous backup.

## What a sync tool cannot do

FreeFileSync, robocopy and the like select by name and by folder: leave `_OUTTAKES` out, take only `*.NEF`, give another folder other rules. If that is all you need, they do it well.

What they cannot do is the choice in the session above: `DSC_9360.NEF` and `DSC_9361.NEF` sit in the same folder with the same type, and only the export folder says that one of them was kept. photo-backup makes that choice photo by photo:

- **The originals of what you exported.** Each export is matched to the real file names beside the export folder: `DSC_9360_ProPhoto-g22_v1.jxl` → `DSC_9360.NEF`, `.JPG`, `.xmp`. Nikon's `DSC_` and `_DSC`, Canon's `IMG_`, film scans and phone names all work; a name is never cut at the first underscore. [How a photo is selected](#how-a-photo-is-selected).
- **One export format per photo.** `jxl>tif>jpg` keeps a photo's JXL; its TIFF when it has no JXL; its JPEG when it has neither — per photo, not per folder. [Export formats](#export-formats).
- **No camera photo left without a copy.** When the photos you did not export come along too, a manifest's `Originals=raw` keeps every RAW with its sidecars, and a JPEG, TIFF, PNG, HEIC or PSD only when no RAW of the same photo is beside it: a JPEG-only or TIFF-only photo is kept, the JPEG twin of a RAW or a TIFF developed from it is not. [Originals that were not exported](#originals-that-were-not-exported---unselected).
- **A checksum from the day of the copy.** The catalog keeps the SHA-256 of every file copied, so `--verify full` tells a damaged backup copy from an original that changed without changing its date (bit rot). Comparing the two sides only says that they differ. [Verifying a backup](verifying.md).

**Different rules per folder, in one run.** A manifest CSV gives each folder its own mode — old years whole, recent years with their exports and originals — and every mode starts from the export selection above, then adds the originals that were not exported, `_OUTTAKES_KEEP`, `_OUTTAKES` or the whole folder. `--analyze` shows how many TB each mode would take before you choose. [Per-folder modes](per_folder_modes.md).

Everything it did **not** copy is counted in the summary and listed in the CSVs (folders without an export, outtakes, filtered formats), so nothing is left out without you knowing. The one exception is a folder skipped by its name (`$RECYCLE.BIN`, `System Volume Information`, `--exclude-folders`): it is not read at all, so it is named in the summary and the log, not counted file by file.

## Does it fit your archive?

It fits when:

- the photos you keep are **exported**, into a folder whose name contains `export` in any case (`_EXPORT`, `Lightroom_Export`, `C1_Export_WEB`, `Exported`; the word is a [setting](commands_and_settings.md#settings)), with subfolders inside it if you like (`16B_JXL`, `sRGB`, `WEB`);
- that export folder is **directly inside the folder that holds the originals**. Lightroom and Capture One can both export this way, depending on how the export is set up;
- an export's name **starts with the name of its original**: `DSC_9360.NEF` → `DSC_9360_anything.jxl`, `DSC_9360-2.jpg`, `DSC_9360 web.jpg`.

It falls short when:

- **exports are renamed** (`Flores_001.jpg`): they are copied, but their originals are not found; they are listed in `_unmatched.csv`;
- **exports are not inside the originals' folder**: one `Pictures\Exports\` for everything, or an export folder beside the originals' folder instead of inside it. No original is selected, because only the files beside an export folder are its originals (the exports themselves are still copied when their folder's name contains `export`);
- **the choice lives only in a catalog** (Lightroom stars, picks or collections, never exported): photo-backup reads folders, not catalogs. A per-folder mode that also keeps the originals that were not exported, or the whole folder, still backs these photos up.

A `--dry-run` on one of your folders shows what it would take before anything is written.

## How a photo is selected

For a folder that holds an export folder (a "session"), the tool collects the
photo keys of the files directly beside the export folder whose extension can
be a key (`ANCHOR_EXTENSIONS`: RAW, JPEG, TIFF, PNG, HEIC, PSD). The key is
the extension-less stem of such a file.

Each export file is matched to the shortest prefix of its own stem that ends
right before a separator (`_`, `-`, `.`, space, `(`) and equals an anchor:

- `DSC_9360_ProPhoto-g22_v1.jxl` with `DSC_9360.NEF` beside it → key
  `DSC_9360` (`DSC` is not an anchor; matching is case-insensitive).
- `_DSC6491_NXstudio_ProPhoto-g22_v1.jxl` with `_DSC6491.NEF` → key
  `_DSC6491`, so `_DSC6491.NEF`, `_DSC6491.JPG`, `_DSC6491.xmp` and
  `_DSC6491_NXstudio.xmp` are all copied.
- `DSC0001_Nxstudio_v1_sRGB.jpg` with `DSC0001.NEF` and
  `DSC0001_Nxstudio_v1.tiff` → key `DSC0001` (the shortest match wins), so
  both originals are copied.
- `DSC_9363-DxO_DeepPRIME XD3_ProPhoto-g22_v1.jxl` with `DSC_9363.NEF` and
  `DSC_9363-DxO_DeepPRIME XD3.dng` → key `DSC_9363` (`-` is a separator), so
  the RAW and DxO's DNG, with their sidecars, are both copied.
- `DSC_9360x.jpg` with `DSC_936` beside it does NOT match: the character
  after `DSC_936` is `0`, not a separator.
- Windows' duplicate counter is part of the name *(v0.5.0)*:
  `DSC_0001 (2)_sRGB.jpg` with both `DSC_0001.NEF` and `DSC_0001 (2).NEF`
  beside it → key `DSC_0001 (2)`, not `DSC_0001`, so the export formats are
  chosen for that photo alone. (With only `DSC_0001.NEF` there, the key stays
  `DSC_0001`.) The originals of `DSC_0001 (2)` still bring `DSC_0001` along:
  a film rescan `0005 (2).tif` is often the same frame as `0005.tif`, and a
  backup copies one original too many rather than one too few.

The keys of an export file that has no matching original are recorded as
`?<stem>` and listed in `_unmatched.csv`; such files are still copied (film
scans renamed on export end up there).

A direct file of the session is copied as category `source` when any prefix
of its stem matches a selected key. This also covers `DSC_9360.NEF.xmp`
(stem `DSC_9360.NEF`) and `_DSC6491_NXstudio.xmp`. Direct files whose photo
was not selected follow `--unselected` ([below](#originals-that-were-not-exported---unselected)).

## Export formats

`--export-formats SPEC` (default `jxl>tif>jpg`):

| SPEC | Meaning |
|---|---|
| `all` | copy every collected file, including non-image files |
| `a>b>c` | fallback per photo: for each key, copy the files of the first listed group that has any file for that key; the other groups are not copied |
| `a+b` | multiple: copy the files whose extension is in any listed group |
| `a` | a single group is the same as `a+b` |

Example with the files `16B_JXL\..jxl`, `JXL_mobile\..jxl`, `TIFF16\..tif`,
`sRGB\..jpg`, `WEB\..jpg`, `CHECK\..jpg` for one photo: `jxl>tif>jpg` copies
the 2 `.jxl`; `jxl+jpg` copies the 2 `.jxl` and the 3 `.jpg`; `all` copies
all 6. Mixing `>` and `+`, an unknown group name or an empty spec is a usage
error (exit 2). Files dropped by the filter (or by
`--exclude-export-subfolders`) are counted in the summary and the log.

Format groups (`FORMAT_GROUPS` in the script):

| Group | Extensions |
|---|---|
| `jxl` | `.jxl` |
| `tif` | `.tif`, `.tiff` |
| `jpg` | `.jpg`, `.jpeg` |
| `png` | `.png` |
| `heic` | `.heic`, `.heif`, `.hif` |
| `psd` | `.psd`, `.psb` |
| `dng` | `.dng` |

## jxl-photo derivatives (`--derivatives`) *(v0.5.0)*

[jxl-photo](https://github.com/rsilvabr/jxl-photo) makes copies of a JXL
master — an sRGB JXL, a resized or sharpened JXL — and writes
`jxlphoto-derived:<recipe>` into each copy's XMP; the master carries
`jxlphoto-src:` instead. Such a copy can be made again from the master, so by
default it is **not** backed up when the master of the same photo is copied in
the same run (the same reasoning as the Capture One `Cache`).

The rule, per photo, never on a guess:

1. the JXL's own XMP says `jxlphoto-derived:` (and not `jxlphoto-src:`), and
2. a JXL of the same photo (same key) that says `jxlphoto-src:` is selected
   in this run.

Anything else is copied: a JXL without a container or without XMP, a
compressed (`brob`) XMP box, a lossless JPEG → JXL archive (it carries no
token), a derivative whose master is excluded or missing, a file that cannot
be read. Only photos with two or more selected `.jxl` are read, and only the
boxes before the image data (a few KB per file). The summary counts what was
left out: `jxl-photo derivatives (their master is copied ...)`.

`--derivatives` (or `COPY_JXLPHOTO_DERIVATIVES = True`) copies them anyway.

## Originals that were not exported (`--unselected`)

- `skip` (default): not copied, counted in the summary.
- `raw`: copy RAW files, plus sidecars (`.xmp`, `.dop`, `.pp3`) whose stem
  prefix matches an unselected RAW of that folder.
- `raw_or_jpeg`: `raw`, plus every JPEG (`.jpg`, `.jpeg`), TIFF, PNG, HEIC
  (`.heic`, `.heif`, `.hif`) or PSD that has no RAW of the same photo beside
  it, and the sidecars of those photos — no photo is left without a copy
  *(v0.12.1: the TIFF, PNG, HEIC and PSD too)*. "The same photo" is the rule of
  the photo key: `DSC_4321_NXstudio.TIF` beside `DSC_4321.NEF`, or
  `P1010099.tif` beside `P1010099.ORF`, is that RAW's photo and stays out;
  `X (2).tif` is another file, not a variant of `X.NEF`, and is kept.
- `raw_jpeg`: `raw_or_jpeg`, plus every JPEG of the session.
- `all`: copy every direct file of the session.

`raw_or_jpeg` and `raw_jpeg` were added for the manifest's `Originals` column
([Per-folder modes](per_folder_modes.md)); `raw` keeps exactly
the behaviour it had.

The originals are copied only when at least one export file of that photo was
actually selected for the backup (after format filtering and
`--exclude-export-subfolders`).

## Folders without an export

A folder with no export subfolder is listed in `_unexported.csv` (path, number
of direct files, bytes, Capture One bytes without cache, NKSC_PARAM bytes, and
`copied`: `yes` when this run copies it). The summary's `folders without
export` line, under `Not copied:`, counts only those that are not copied
*(v0.12.1)*; the CSV lists all of them.
With `--unexported copy` (or a `+` mode of the manifest) the direct files,
Capture One and NKSC_PARAM folders are also copied — also for a folder that has no file of its own, only
subfolders and a `CaptureOne` folder *(v0.12.1)*: a parent folder above a
session counts as a folder without an export (it has no export of its own).

**Inside sessions.** `selective_backup.py` on its own leaves out every direct
subfolder of a session that is not an export, Capture One or NKSC_PARAM folder
(`_OUTTAKES`, `OLD_TIFFs`, `tiff_zip`, ...), with everything below it
(`SESSION_LEAVE_OUT = "*"`). With a modes table *(v0.13.0)*, a session leaves
out only the subfolders its mode names in `LeaveOut` — `_OUTTAKES*` in the
shipped table — and any other direct subfolder is **judged alone**, exactly as
the subfolder of a folder with an export and no original beside it (below): a
session if it holds an export folder; a folder without an export if not, copied
whole in a `+` mode and listed in `_unexported.csv` in the others. So a folder
with the photos of another event, put beside the export of a session, is no
longer left out in a `+` mode.

What a session leaves out — the files, and those of the left-out folder's own
`CaptureOne` and `NKSC_PARAM` folders — is counted as "inside sessions".
`_inside.csv` has one line per direct subfolder of a session left out
(session, subfolder, files, bytes), and the summary names the five biggest,
grouped by name *(v0.12.1)*:

```
  inside sessions (subfolders the mode leaves out): 1550 / 58.4 GB   -> <log>_inside.csv
      _OUTTAKES          1200 files      48.0 GB
      _OUTTAKES_DROP      350 files      10.4 GB
```

A folder that holds an export folder but **no original beside it** is not a
session for its subfolders: `G:\2017` holding `171230_Lightroom_Export`, or a
drive root holding `04_ Exported_OLD`. Its export files are copied (and listed
as exports without a matching original), but its subfolders keep their own
classification — a trip folder below it is still a folder without an export,
listed and copied with `--unexported copy`. A manifest mode's `AddWhole` and
`AddFolders` still apply to its direct subfolders *(v0.12.1)*: a film session
whose only originals are in `RAW SCANS` keeps them.

## Capture One and NX Studio

- Every folder named `CaptureOne` (configurable) is copied in full, except
  its direct subfolders named `Cache` (configurable) — those are regenerable.
  Use `--c1-cache` to copy them too.
- Every folder named `NKSC_PARAM` is copied in full.
- Both are copied without filtering by photo (they are small), for sessions
  and, with `--unexported copy`, for folders without an export.

Capture One's own files — the profiles, film curves and styles your `.cos`
edits name — are kept by another script: [captureone.md](captureone.md).
