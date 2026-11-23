# Related projects

All mine. The README keeps one line for each.

## photo-backup as a Windows program

[photo-backup-exe](https://github.com/rsilvabr/photo-backup-exe) packages this backup as `photo-backup.exe`: no Python to install. Unzip and double-click for the menu, or use the same command lines as here (`photo-backup.exe backup ...`, `manifest`, `restore`, `captureone`). The scripts inside are byte-for-byte those of a photo-backup release, and the settings at their top can be edited there as here. Presets and backups are shared: a backup made by one version is continued, verified and restored by the other.

This Python version, the one I run on my own archive, remains by far the more tested. Use the program if you would rather not install Python; otherwise use the scripts here.

## jxl-photo

[jxl-photo](https://github.com/rsilvabr/jxl-photo) turns TIFF and JPEG into JPEG XL and back (lossless or near-lossless, ICC and metadata preserved), and makes small derivatives for the phone. photo-backup uses it in four ways, each one optional, to keep a backup small:

1. **The exports' masters.** jxl-photo writes `_EXPORT\16B_JXL`, and photo-backup's default `jxl>tif>jpg` backs up that compact master instead of the TIFF — in the archive this was built for, 76 GB of JXL instead of 426 GB of TIFF for one year ([export formats](how_it_selects.md#export-formats)).
2. **The derivatives.** The sRGB or resized JXL jxl-photo makes from a master are left out of the backup when the master is copied: jxl-photo can make them again ([how](how_it_selects.md#jxl-photo-derivatives---derivatives-v050)).
3. **TIFF kept as JXL** *(v0.14.0-rc2)*. A manifest line's `Tiff` column names a profile: photo-backup runs jxl-photo's encoder on each developed TIFF and film scan of the line, keeps the JXL only when it carries the TIFF's own mark, and the restore runs jxl-photo's decoder to give the TIFF back. A 45 MP NX Studio TIFF: 218 MB → 34 MB at distance 0.05; scans lossless at 59–71%, the same pixels.
4. **JPEG kept as JXL** *(v0.14.0-rc3)*. A line's `Jpeg` column: jxl-photo's lossless JPEG transcode, then the JPEG rebuilt from the JXL must be the same bytes before the JXL counts. 146 real JPEGs (camera, lab scans, exports) took 81% of their size, and every one came back byte for byte.

The restore chooses what comes back in its own format: everything, only what is lossless (every JPEG, the lossless TIFFs), or nothing (the JXL as it is). Every backup keeps a copy of the jxl-photo that wrote it (`_photo_backup\tools\`), so a restore on another computer has it. All of it: [TIFF and JPEG as JXL](tiff_to_jxl.md).

What photo-backup needs from jxl-photo — the commands, the marks in the files, the page check, the version (2.31.0 or newer for a backup, 2.30.0 for a restore) — is written down item by item in [the contract](jxlphoto_contract.md), with a test that checks any jxl-photo against it: edit jxl-photo freely, run the test, and a failure names what broke.

One `.cmd` can run both every night ([Run it after jxl-photo](menu.md#run-it-after-jxl-photo)).
