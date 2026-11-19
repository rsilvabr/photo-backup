# The interactive menu (`photo_backup.py`)

The easiest way to use photo-backup. The menu never copies anything itself:
it runs `selective_backup.py`, `manifest_backup.py`, `mirror_backup.py`,
`restore_backup.py` or `captureone_backup.py` with the options you confirmed,
so every [safety guarantee](safety.md) holds either way.

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

- **[1] Back up** asks for the folders to back up, the destination (it lists the connected drives with their free space), the export formats, the subfolders to skip and the other options (every `--unselected` value the backend accepts; whether to copy the Capture One cache, the jxl-photo derivatives and Capture One's own files) — then runs a **dry-run** with those choices, "replace what changed" included *(v0.13.1: the plan you confirm counts the changed files that the copy will replace)*. The command line it runs always names each choice, also a "no" (`--no-c1-cache`, `--no-derivatives`), so a default changed at the top of `selective_backup.py` never turns what the menu showed into something else. You read what would be copied, what would not, and whether it fits; only then it asks **"copy now?"**. If some folders could not be read, it says so and asks again before copying the rest.
- Your choices are remembered: the next time, it shows them and asks **"Use these settings?"** — Enter, and the dry-run starts.
- **[2] Verify** runs `--verify quick` or `--verify full` on a backup. With `full` it also asks **"Max GB to verify in this run (0 = everything)"** — see [Rolling verify](verifying.md#rolling-verify). Its question "Also verify Capture One's own files?" is remembered apart from the backup's: a "no" here does not turn off the Capture One step of the next backup.
- **[3] Presets**: after a backup the menu offers to save its settings (and the backup's ID) under a name. A preset runs from the menu (dry-run first) or unattended; if the drive letter changed, the menu finds the backup by its ID ([backup ID](verifying.md#which-disk-is-this-backup-id)).
- **[4] Settings** shows where the remembered choices live (`~/.photo_backup_config.json`, or `$PHOTOBACKUP_CONFIG`) and the defaults of `selective_backup.py`, with those of the TIFFs kept as JXL (`JXLPHOTO`, `TIFF_JXL_PROFILES`, `JXL_WORK_DIR`, `KEEP_TOOLS_IN_BACKUP`, `KEEP_TOOLS_EXTRA`; [tiff_to_jxl.md](tiff_to_jxl.md), *v0.14.0-rc2*). The menu reads those defaults from `selective_backup.py` itself: edit the top of that file and the menu follows. **"Set where jxl-photo is"** *(v0.14.0-rc4)* remembers your jxl-photo (`jxl_photo.py` or `jxl-photo.exe`, checked first: [the contract](jxlphoto_contract.md)) in that same file, and the menu gives it (`--jxlphoto`) to every backup, mirror and restore it runs — no path of yours inside the scripts; `-` forgets it.
- **[5] Restore** lists the backup's top folders, asks what to restore and where to (the original places or another folder), runs a dry-run and then restores after a yes. When the backup keeps TIFFs or JPEGs as JXL, it says how many (and how many TIFF are lossy) and asks what they become: all back to TIFF / JPEG, only the lossless ones, or every one as JXL (`--convert-back`, [the table](tiff_to_jxl.md#restoring); *v0.14.0-rc3*). Files that sit loose above the listed folders (directly in the year) have a line of their own, `(files directly in 2025)`, which restores those files only *(v0.13.1)*. See [restoring.md](restoring.md).
- **[6] Capture One only** backs up just Capture One to a folder you choose, restores from one, force-restores and undoes the last force restore; see [Capture One's own files](captureone.md).
- **[7] Manifest** asks for a manifest and a destination and offers `[1] Analyze only` (how many bytes each mode would take in each folder), `[2] Back up` or `[3] Verify` (the backup against what the manifest selects); see [Per-folder modes](per_folder_modes.md). `[2] Back up` does what `[1]` does: it asks whether to back up Capture One's own files too and whether to replace originals/exports that changed since the last backup (`--update-changed`; the answers are remembered for the next manifest run), then runs a dry-run with those answers — the Capture One step too — and the copy after a yes. `[3] Verify` asks quick or full, with `full` the "Max GB" (default `VERIFY_MAX_GB` of `selective_backup.py`), and verifies `_captureone` too when the backup has one.
- **[8] Mirror** runs [`mirror_backup.py`](mirror.md): `[1] Mirror` asks for the manifest and the backup, then runs the mirror once: its box, last on the screen, says how many files will leave the backup and until when they stay in the trash, and right below it the mirror asks **"Mirror now? copy N, replace N, remove N [y/N, Enter = no]"** — the default is **no**: Enter changes nothing. The numbers are those of the run that changes the backup *(v0.14.0-rc2; in rc1 the menu asked after a dry-run and then ran the mirror with `--yes`, so what changed in between was not asked about)*. When something needs it (a line over the limit, not enough space), the mirror asks you to type the number of files. After a mirror it offers to save it as a preset. `[2] Total mirror` also removes what came from folders not in the manifest (never as a preset). `[3] What is in the trash` lists what the mirror took out, until when it stays and how to get a file back ([Lost a file?](mirror.md#lost-a-file-getting-it-back)); `[4] Empty the trash` deletes it for good, everything or only what was taken out before a day, after the typed number.

> **Tip:** The value shown in `[brackets]` is the default — just press Enter to accept it.

## Unattended runs (Task Scheduler)

```console
python photo_backup.py --list-presets
python photo_backup.py --run-preset "NIGHTLY"
```

`--run-preset` runs a saved preset without questions and without the dry-run step; `selective_backup.py` still refuses to start when the destination drive is missing or too full (exit 2). For the Task Scheduler, run it through `cmd /c`: the window closes when the run ends, and the task's **Last Run Result** is the backup's own exit code (see [Exit codes](commands_and_settings.md#exit-codes)); what happened is in the log and the CSVs of `Logs\`.

```
Program:   cmd
Arguments: /c python photo_backup.py --run-preset "NIGHTLY"
Start in:  C:\path\to\photo-backup
```

Not `cmd /k`: the window would stay open after the run, the task would stay "Running" until someone closes it — and by default Windows does not start a task while it is still running, so the next night's backup would not run at all. The result the Task Scheduler shows would then be the closed window's, not the backup's.

A **mirror preset** (saved from `[8] Mirror`) runs its manifest with `mirror_backup.py --unattended`: it never asks anything, never deletes from the trash before its date, and stops with exit 2, changing nothing, when something would need a typed number — a line over the limit, not enough space ([unattended runs of the mirror](mirror.md#unattended-runs)). A total mirror is never a preset.

## Run it after jxl-photo

The smooth nightly order is: [jxl-photo](https://github.com/rsilvabr/jxl-photo)
converts the new TIFF exports into JXL
masters, then photo-backup runs and picks up those masters the same night
(`jxl>tif>jpg` finds the JXL first). One `.cmd` chains both:

```bat
@echo off
cd /d C:\path\to\jxl-photo
py jxl_photo.py --run-preset "SYNC PHOTOS"
cd /d C:\path\to\photo-backup
py photo_backup.py --run-preset "NIGHTLY"
```

In the Task Scheduler, use `cmd /k` with that `.cmd` so the window stays open
with the result. Nothing here edits your existing scheduled tasks.

## Settings of the menu

The menu, `photo_backup.py`, has its own settings at its top *(listed
since v0.13.2)*. It reads every backup default from `selective_backup.py`
instead of repeating it:

| Constant | Default | Meaning |
|---|---|---|
| `CONFIG_PATH` | `$PHOTOBACKUP_CONFIG` or `%USERPROFILE%\.photo_backup_config.json` | the remembered choices and the presets |
| `BACKEND`, `RESTORE`, `CAPTUREONE`, `MANIFEST_BACKEND`, `MIRROR_BACKEND` | the scripts next to it | what the menu runs |
| `COPY_CAPTUREONE_SETTINGS` | `True` | every backup and verify of the menu also keeps (and checks) Capture One's own files |
| `VERIFY_MODE` | `quick` | the verify mode the menu offers first: `quick` or `full` |
| `MAX_ONLY_CHARS` | `20000` | `[5] Restore`: the most `--only` text one command may carry (Windows takes about 32 000 characters); a longer choice asks for the folder above *(v0.13.1)* |
| `FORMAT_ALTERNATIVES` | four specs | the export formats offered next to `EXPORT_FORMATS`, with what each one means |
| `UNSELECTED_TEXT` | one line per value | how the menu explains each `--unselected` value |
