# photo-backup — selective backup for photo archives

Backs up the photos you kept, found through the exports you already make: each exported photo with its RAW, camera JPEG, edited TIFF and sidecars, plus its Capture One and NX Studio edits, in the same folder tree. **Read-only on the source, never deletes on the destination** (only the optional mirror does, through a trash), **and checks every copy with SHA-256**, right after it is made and on any later day. Per-folder modes decide how much of the rest comes along. Tested on a 9 TB archive of Capture One, Lightroom and NX Studio sessions from 1991 to 2026.

Also keeps **Capture One itself**: the ICC profiles, film curves and styles your edits name, with your presets and workspaces, and a double-click restore for a new computer. [Details](docs/captureone.md).

**Current version: v0.13.2** (2026-11-12) · [What's new](#whats-new-in-v0132) · [All releases](docs/version_history.md) · Test version: [v0.14.0-rc5](https://github.com/rsilvabr/photo-backup/releases/tag/v0.14.0-rc5), the mirror, TIFF and JPEG kept as JXL ([what's new](docs/whats_new.md#v0140-rc5-test-version-not-released-yet))

**Windows program, no Python needed:** [photo-backup-exe](https://github.com/rsilvabr/photo-backup-exe/releases/latest) — unzip and double-click.

---

## Why a selective backup?

A photo archive is mostly photos nobody will ever look at again. In the archive this was built for, the year folders hold ~9.3 TB, but the photos that were actually edited and exported — with their originals and edits — fit in **~1.2 TB**. photo-backup reads the decision you already made: **if a photo has an export, it matters.**

One folder per shoot, the originals directly in it, and the exports in a folder inside it whose name contains "export": such a folder is a **session**. What a backup with the default settings takes from one:

```
G:\2025\250129_Flores\                        a session: it holds an export folder
├── DSC_9359.NEF                              ✗  not exported
├── DSC_9360.NEF                              ✓  exported (see _EXPORT): its RAW,
├── DSC_9360.JPG                              ✓  its camera JPEG
├── DSC_9360.xmp                              ✓  and its sidecar
├── DSC_9361.NEF                              ✗  not exported
├── _EXPORT\                                     any folder whose name contains "export"
│   ├── 16B_JXL\DSC_9360_ProPhoto-g22_v1.jxl  ✓  the best format of the photo (jxl>tif>jpg)
│   └── TIFF16\DSC_9360_ProPhoto-g22_v1.tif   ✗  same photo, it has a JXL
├── CaptureOne\
│   ├── Settings166\                          ✓  the Capture One edits (.cos)
│   └── Cache\                                ✗  previews; Capture One rebuilds them
├── NKSC_PARAM\                               ✓  the NX Studio edits
└── _OUTTAKES\                                ✗  the rejects (a per-folder mode can add them)
```

A photo developed in another program first keeps the whole chain: the final photo, the intermediate file and the RAW, because each name starts with the RAW's. Whether you exported from the RAW, from the intermediate or from both makes no difference:

```
DSC_9362.NEF                                            ✓  the RAW
DSC_9362_NXstudio.tif                                   ✓  the TIFF NX Studio developed from it
_EXPORT\DSC_9362_NXstudio_ProPhoto-g22_v1.jxl           ✓  the final photo, edited from the TIFF
DSC_9363.NEF                                            ✓  the RAW
DSC_9363-DxO_DeepPRIME XD3.dng                          ✓  the DNG DxO denoised from it, with its .xmp
_EXPORT\DSC_9363-DxO_DeepPRIME XD3_ProPhoto-g22_v1.jxl  ✓  the final photo, edited from the DNG
```

Each export is matched to the real file names beside the export folder, never by cutting a name at the first underscore: Nikon's `DSC_` and `_DSC`, Canon's `IMG_`, film scans and phone names all work. Sessions are found at any depth. A folder without an export is not copied but listed (`_unexported.csv`), and comes along whole with `--unexported copy` or a `+` mode. The tree is mirrored on the destination (`G:\2025\250129_Flores\DSC_9360.NEF` → `H:\BACKUP\2025\250129_Flores\DSC_9360.NEF`), and later runs add to it.

A sync tool (FreeFileSync, robocopy) selects by name and by folder; it cannot tell `DSC_9360.NEF` from `DSC_9361.NEF`, same folder and same type, when only the export folder says that one of them was kept. Everything photo-backup does **not** copy is counted in the summary and listed in the CSVs, so nothing is left out without you knowing. Every rule, with more examples: [docs/how_it_selects.md](docs/how_it_selects.md).

### Does it fit your archive?

It fits when:

- the photos you keep are **exported**, into a folder whose name contains `export` in any case (`_EXPORT`, `Lightroom_Export`, `C1_Export_WEB`, `Exported`; the word is a [setting](docs/commands_and_settings.md#settings)), with subfolders inside it if you like (`16B_JXL`, `sRGB`, `WEB`);
- that export folder is **directly inside the folder that holds the originals** (Lightroom and Capture One can both export this way);
- an export's name **starts with the name of its original**: `DSC_9360.NEF` → `DSC_9360_anything.jxl`, `DSC_9360-2.jpg`, `DSC_9360 web.jpg`.

It falls short when:

- **an export's name no longer starts with its original's** (`Flores_001.jpg`, `Final_DSC_9360.jpg`): it is copied, but its original is not found (it is listed in `_unmatched.csv`). Adding to the end is fine (`DSC_9360_Flores.jpg`);
- **exports are not inside the originals' folder** (one `Pictures\Exports\` for everything, or an export folder beside the originals' folder): no original is selected;
- **the choice lives only in a catalog** (Lightroom stars or collections, never exported): photo-backup reads folders, not catalogs. A per-folder mode that also keeps the originals that were not exported still backs these photos up.

A `--dry-run` on one of your folders shows what it would take before anything is written. [More](docs/how_it_selects.md#does-it-fit-your-archive)

---

## Features

- **Selection by export, not by folder**: the RAW, camera JPEG, edited TIFF and sidecars of each photo you exported, found by the real file names beside the export ([how](docs/how_it_selects.md#how-a-photo-is-selected)).
- **One export format per photo**: `jxl>tif>jpg` keeps a photo's JXL; its TIFF when it has no JXL; its JPEG when it has neither ([formats](docs/how_it_selects.md#export-formats)).
- **Your edits come along**: Capture One `Settings*` and NX Studio `NKSC_PARAM`. Changed `.xmp`, `.cos` and `.nksc` files are updated on every run, the previous version kept in a `versions\` folder ([re-runs](docs/reruns_and_moves.md)).
- **Different rules per folder, in one run**: a manifest gives each folder its own mode, and `--analyze` shows how many TB each mode would take first ([modes](docs/per_folder_modes.md)).
- **Never loses anything**: the source is read-only, nothing on the destination is deleted or overwritten in place (the mirror aside), and each copy goes to a temp file, is verified, then renamed ([safety](docs/safety.md)).
- **A mirror when you want one**: `mirror_backup.py` makes the backup follow a manifest — what is gone from the source leaves the backup too, kept 30 days in a trash first ([mirror](docs/mirror.md)).
- **TIFF and JPEG on the computer, JPEG XL in the backup**: a developed TIFF, a film scan or any JPEG can be kept as JXL, made by [jxl-photo](https://github.com/rsilvabr/jxl-photo) and proven before it counts; the restore gives the original format back — a JPEG byte for byte ([smaller with jxl-photo](#smaller-with-jxl-photo)).
- **Checks the space before the first byte**: a run that does not fit stops before copying anything.
- **Verify any day later**: the SHA-256 recorded at copy time tells a damaged backup copy from an original that changed without changing its date (bit rot); `--max-gb` checks a big backup over several nights ([verify](docs/verifying.md)).
- **Renamed folders are moved, not copied again**, once SHA-256 confirms the content; files whose original is gone are listed as orphans ([moves](docs/reruns_and_moves.md#moved-and-renamed-folders)).
- **Restore** under a new folder or to the original places, never overwriting anything and never writing to the backup ([restore](docs/restoring.md)).
- **Capture One on a new computer**: its profiles, styles and settings, with a double-click restore ([Capture One](docs/captureone.md)).
- **Made to work with [jxl-photo](https://github.com/rsilvabr/jxl-photo)**: it turns 16-bit TIFF exports into JPEG XL masters at a fraction of the size, with their ICC profile and metadata, and makes the sRGB and resized copies from them; the backup keeps the masters and leaves out the copies jxl-photo can make again ([derivatives](docs/how_it_selects.md#jxl-photo-derivatives---derivatives-v050), [smaller with jxl-photo](#smaller-with-jxl-photo)).

### Smaller with jxl-photo

[jxl-photo](https://github.com/rsilvabr/jxl-photo) turns TIFF and JPEG into JPEG XL and back, keeping the ICC profile and the metadata. The two are made to work together:

```
you export a 16-bit TIFF  ->  jxl-photo makes its JXL master (+ sRGB and phone copies)
photo-backup              ->  backs up the master and the photo's originals, not the copies
                              (optional) keeps your TIFFs and JPEGs as JXL in the backup
restore                   ->  the TIFF or JPEG back, with the jxl-photo the backup keeps
                              (a JPEG byte for byte; a TIFF with its JXL's pixels: the same
                              for scans and 8-bit TIFFs, near-identical at distance 0.05)
```

Four things, each one optional, make the backup smaller without losing a photo:

| | What | Measured on the archive this was built for |
|---|---|---|
| 1 | **Exports as JXL masters**: `jxl>tif>jpg` (the default) backs up the JXL master instead of the TIFF | one year: 76 GB of JXL instead of 426 GB of TIFF |
| 2 | **Derivatives left out**: the sRGB or resized JXL jxl-photo can make again from a master in the backup | |
| 3 | **TIFF kept as JXL** (`Tiff` column): a developed TIFF or a film scan stays TIFF on your disk, JXL in the backup | NX Studio 45 MP: 218 MB → 34 MB; scans, lossless: 59–71%, the same pixels |
| 4 | **JPEG kept as JXL** (`Jpeg` column): every JPEG, exports too, by a lossless transcode | 146 real JPEGs: 81% of their size, every one back byte for byte |

**Turning it on.** 1 and 2 need nothing: they follow what jxl-photo left in your export folders. For 3 and 4: jxl-photo 2.31.0-rc1 or newer and libjxl's `djxl` on PATH; tell the menu where jxl-photo is (`[4] Settings`, also where the conversions run, say another disk); put `jxl` in the manifest's `Tiff` or `Jpeg` column. On a slow computer, the conversion can be turned off for a while: new files go as they are, and are converted later.

**Nothing is lost on the way.** Each JXL counts only after it is proven (every TIFF page decoded and compared with the TIFF's, the JPEG rebuilt byte for byte); anything else is copied as it is. The restore gives back all the original formats, only the lossless ones, or none. In detail: [TIFF and JPEG as JXL](docs/tiff_to_jxl.md), [jxl-photo and photo-backup](docs/related_projects.md#jxl-photo), and what jxl-photo must keep doing for all this: [the contract](docs/jxlphoto_contract.md).

### The scripts

| Script | What it does |
|---|---|
| [`photo_backup.py`](photo_backup.py) | **Start here.** The interactive menu: back up (dry-run first, then copy), verify, presets, unattended runs, restore. Runs the scripts below for you |
| [`selective_backup.py`](selective_backup.py) | The selective backup from the command line: plan, copy, catalog, verify. Everything the menu does goes through it |
| [`manifest_backup.py`](manifest_backup.py) | Each folder of a manifest with its own mode; `--analyze` shows how many TB each mode would take |
| [`mirror_backup.py`](mirror_backup.py) | The backup follows a manifest: what changed is replaced, what is gone is removed, through a 30-day trash |
| [`restore_backup.py`](restore_backup.py) | Restores files from a backup: under a new folder or to the original places |
| [`captureone_backup.py`](captureone_backup.py) | Backs up, verifies and restores Capture One's own profiles, styles and settings |
| [`mobile_backup.py`](mobile_backup.py) | Mirrors the phone-sized exports to another disk, one album per event ([its own README](README_mobile_backup.md)) |

---

## Install

**On Windows, without Python:** [photo-backup-exe](https://github.com/rsilvabr/photo-backup-exe/releases/latest) — unzip and double-click.

**With Python 3.12 or newer** — the standard library only, no packages and no external tools:

```console
git clone https://github.com/rsilvabr/photo-backup.git
cd photo-backup
python photo_backup.py
```

Windows only: all file I/O uses extended-length `\\?\` paths, so paths longer than 260 characters (Japanese folder names, deep sessions) work. Optional: `python -m pip install rich` gives the menu colours. `mobile_backup.py` also needs [ExifTool](https://exiftool.org/).

---

## Quick start — the menu

```console
python photo_backup.py
```

```
== Menu ==
  [1] Back up (dry-run first, then copy)
  [2] Verify a backup
  [3] Presets
  [4] Settings
  [5] Restore from a backup
  [6] Capture One only (back up, restore, force, undo)
  [7] Manifest (each folder with its own mode)
  [8] Mirror (the backup follows a manifest; removes what is gone, through a trash)
  [0] Exit
```

**[1] Back up** asks for the folders, the destination (it lists the connected drives with their free space), the export formats and a few options, then runs a **dry-run**: you read what would be copied, what would not, and whether it fits; only then it asks **"copy now?"**. The next time it shows your choices and asks **"Use these settings?"**: Enter, and the dry-run starts. After a backup it offers to save the settings as a **preset**. The value in `[brackets]` is the default: press Enter to accept it. Every item of the menu: [docs/menu.md](docs/menu.md).

**Unattended (Task Scheduler):** `python photo_backup.py --list-presets` lists the presets, and `python photo_backup.py --run-preset "NIGHTLY"` runs one without questions and without the dry-run step. The backup still refuses to start when the destination drive is missing or too full (exit 2). [How to schedule it](docs/menu.md#unattended-runs-task-scheduler), also [right after jxl-photo](docs/menu.md#run-it-after-jxl-photo).

The menu never copies anything itself: it runs the scripts above with the options you confirmed, so every safety guarantee holds either way.

---

## Quick start — command line

```console
:: 1. See what a backup would do (writes only logs, no destination)
python selective_backup.py --source "G:\2025" --dest "H:\BACKUP" --dry-run

:: 2. Back up for real (shows the summary and asks for confirmation)
python selective_backup.py --source "G:\2025" --dest "H:\BACKUP"

:: 3. Verify the backup another day
python selective_backup.py --source "G:\2025" --dest "H:\BACKUP" --verify full
```

Always start with `--dry-run`: the summary lists what would be copied, what would not (and why), and whether it fits; the full lists are in the CSVs next to the log, in `Logs\`. Every option, every setting, the logs and the exit codes: [docs/commands_and_settings.md](docs/commands_and_settings.md).

---

## Per-folder modes

One run can use a different rule for each folder: old years whole, recent years with their exports and originals. A manifest lists the folders, and a modes table (`backup_modes.csv`) says what each mode copies:

```csv
Folder,Mode,Originals
G:\2026,3+,raw
G:\2026\event I care less about,skip,
G:\2026\event from a phone,jpeg-only,
```

Every mode starts from the export selection and adds to it. Measured with `--analyze` on one 11 TB drive (2026-11-09), with `Originals=raw`:

| Mode | What it copies | Size |
|---|---|---:|
| 1 | exports + originals of the exported photos + film scan folders | 1.37 TB |
| 2 | mode 1 + the originals that were not exported | 2.41 TB |
| 3 | mode 2 + `_OUTTAKES_KEEP` | 2.50 TB |
| 4 | mode 3 + `_OUTTAKES` | 3.84 TB |
| 5 | mode 4 + every `_OUTTAKES*` | 4.25 TB |
| 6 | the whole folder (without the Capture One `Cache`) | 11.16 TB |

Modes `1+` to `5+` also copy whole every folder not exported yet. A bigger mode never copies less, and lowering a mode deletes nothing (in a [mirror](docs/mirror.md), a `selection` line removes it). Every column, the shipped modes and the commands: [docs/per_folder_modes.md](docs/per_folder_modes.md).

---

## Safety guarantees

One line each; every rule in full: [docs/safety.md](docs/safety.md).

- **The source is read-only.** Nothing under a `--source` is opened for writing, renamed, deleted, touched or changed.
- **Nothing on the destination is ever deleted** — except by the mirror (`mirror_backup.py`): only what it wrote itself, and through a trash. A file whose source changed is reported; with `--update-changed` the old copy is moved to the versions folder first, never overwritten in place.
- **Fail closed.** Not enough space, a missing drive, a damaged catalog, a destination inside a source (also through a junction or a `subst` letter), a junction inside the backup: the run stops before the first byte is copied, exit 2.
- **Nothing is skipped silently.** Every file under a source is in the plan or counted in the summary's `Not copied` table; a folder or file that cannot be read makes the run exit 1.
- **A copy is never half a file.** It is written to a temp file, verified and put on the disk, and only then given its final name.
- **A source that cannot be read is not a source that is gone.** Only proof that it does not exist makes a backup file an orphan.
- **The restore never writes to the backup**, and never replaces a file that is already there.

---

## Good to know

One line each; the detail is behind each link.

- **Changed originals need `--update-changed`.** Edit settings and sidecars (`.xmp`, `.cos`, `.nksc`, ...) are updated on their own; a changed RAW, JPEG, TIFF or export is reported and left as it is. [More](docs/reruns_and_moves.md#re-runs-changed-files-and-the-versions-folder)
- **Orphans are only reported** (`_orphans.csv`); `--move-orphans` parks those whose original is proven gone in `_photo_backup\orphans\`. The versions and orphans folders only grow: delete them yourself when you need the space. [More](docs/reruns_and_moves.md#orphans)
- **The check right after each copy reads from Windows' cache**, not from the disk: the real check of the media is a `--verify full` after the backup disk was reconnected or the computer restarted. [More](docs/verifying.md#the-check-right-after-each-copy)
- **A damaged catalog** stops every run (exit 2): move `_photo_backup\catalog.sqlite` aside and run the backup again; nothing is copied twice. [More](docs/verifying.md#a-damaged-catalog)
- **A TIFF or JPEG kept as JXL needs jxl-photo to come back in its format**; the backup keeps a copy of jxl-photo and of photo-backup in `_photo_backup\tools\`, and `--convert-back none` gives the JXL as it is. [More](docs/tiff_to_jxl.md#restoring)
- **Each backup knows which disk it is**: a preset finds it again when the drive letter changed, and `--expect-backup-id` refuses the wrong disk. [More](docs/verifying.md#which-disk-is-this-backup-id)
- **The command-line scripts do not keep Capture One's own files**; the menu does. From the command line, also run `captureone_backup.py --dest`. [More](docs/captureone.md)
- **A folder skipped by its name** (`$RECYCLE.BIN`, `System Volume Information`, `--exclude-folders`) is not read, so it is named in the summary, not counted file by file. [More](docs/safety.md)
- **Links and junctions are not followed**; they are counted and logged, so you know what is not in the backup. [More](docs/safety.md)
- **Nothing in `Logs\` is ever removed**, and the big CSVs list only what changed by default. [More](docs/commands_and_settings.md#short-or-full-lists-in-the-csvs-v0131)
- **Settings live at the top of each script**: edit `selective_backup.py` and the menu follows. [Every setting](docs/commands_and_settings.md#settings)

---

## Documentation

| Document | Contents |
|---|---|
| [docs/how_it_selects.md](docs/how_it_selects.md) | How a session is read, how an export finds its originals, export formats, what happens to the rest |
| [docs/per_folder_modes.md](docs/per_folder_modes.md) | `manifest_backup.py`: the manifest, the modes table column by column, the shipped modes, `--analyze` |
| [docs/mirror.md](docs/mirror.md) | `mirror_backup.py`: getting a file back, the two modes, what leaves the backup and what never does, the trash, the locks |
| [docs/tiff_to_jxl.md](docs/tiff_to_jxl.md) | A TIFF, a scan or a JPEG kept as JXL in the backup: profiles, the proof, the restore's choice of formats, moving from FreeFileSync, the programs kept in the backup |
| [docs/jxlphoto_contract.md](docs/jxlphoto_contract.md) | What jxl-photo must keep doing for photo-backup (仕様書, JP-1 to JP-21), the minimum version, the contract test |
| [docs/menu.md](docs/menu.md) | The interactive menu item by item, unattended runs, running after jxl-photo, the menu's settings |
| [docs/commands_and_settings.md](docs/commands_and_settings.md) | Every option and setting of `selective_backup.py`, the logs and CSVs, exit codes |
| [docs/safety.md](docs/safety.md) | Every safety guarantee in full |
| [docs/reruns_and_moves.md](docs/reruns_and_moves.md) | What a second run does: changed files, the versions folder, renamed folders, orphans |
| [docs/verifying.md](docs/verifying.md) | Verify quick and full, every status, a damaged catalog, the rolling verify, the backup ID |
| [docs/restoring.md](docs/restoring.md) | `restore_backup.py`: options, outcomes, exit codes |
| [docs/captureone.md](docs/captureone.md) | Capture One's own files: what is kept, the restore on a new computer, force and undo, settings |
| [README_mobile_backup.md](README_mobile_backup.md) | `mobile_backup.py`: the phone mirror, one album per event |
| [docs/related_projects.md](docs/related_projects.md) | The Windows program and jxl-photo |
| [docs/whats_new.md](docs/whats_new.md) | What changed in each release, in a few lines |
| [docs/version_history.md](docs/version_history.md) | Every release in detail, and the full release table |

**Testing.** `python -m pip install pytest`, then `python -m pytest tests` runs the suite (synthetic trees in temporary folders; nothing is written to `Logs\`).

---

## What's new in v0.13.2

2026-11-12. Fixes from a third bug sweep: most of them close edge cases where an unusual input could get around a written safety rule. Nothing to do when upgrading.

- A Capture One cache folder that is a junction to another disk now has its own words on the cache line of the summary, so "links/junctions not followed" names only what is really not in the backup.
- `--verify quick` takes the planned originals from the plan's own listing instead of asking the disk once more per file (`VERIFY_QUICK_USES_PLAN`).
- A destination reached through a junction or a `subst` letter can no longer land inside a source, nor a restore target inside the backup.
- `restore_backup.py --to ""` and `--only ""` are refused (they restored into the current folder / selected everything); a `--space-margin-gb` of `nan`, `inf` or below 0 is refused; a file named `NUL` is an error of the plan instead of a traceback.
- Nothing in the versions or orphans folder is ever replaced (a taken name gets ` (2)`), the backup ID file is never rewritten, and a catalog row that leads outside the backup stops every run as a damaged catalog does.
- `captureone_backup.py --revert-last` trusts a journal line only where Capture One keeps that part, and refuses an empty `old_` folder instead of undoing the force before it.
- `mobile_backup.py` 0.1.4: one run at a time; a run that would empty the mirror changes nothing without `--allow-mass-delete`; junctions are not followed.

[Technical notes](docs/version_history.md#v0132) · [earlier releases, in short](docs/whats_new.md) · [every release](docs/version_history.md#release-history)

---

## Related projects

All mine ([more on each](docs/related_projects.md)):

- [photo-backup-exe](https://github.com/rsilvabr/photo-backup-exe) — this backup as a Windows program, no Python needed. The scripts inside are byte for byte those of a release; the Python version here is the one I run on my own archive and remains the more tested.
- [jxl-photo](https://github.com/rsilvabr/jxl-photo) — TIFF and JPEG to JPEG XL and back, for photographers: JXL masters with the ICC profile and metadata, sRGB and resized copies, thumbnails in Explorer with jxl-thumb. It makes the JXL this backup keeps and gives them back on restore; how much smaller: [above](#smaller-with-jxl-photo).

---

## Disclaimer

This tool was made for my personal workflow.
Use at your own risk — I am not responsible for any issues you may encounter.

However, if you find any bugs, feel free to report them to me — I will gladly try my best to improve this project.

Always start with `--dry-run`, and test with a small folder before backing up important archives.

---

## License

MIT License — feel free to use, modify, and distribute.

---

## Acknowledgments

- [Claude](https://www.anthropic.com/claude) (Anthropic) and [DeepSeek](https://www.deepseek.com), among other AI tools, for code assistance, reviews and technical discussion
