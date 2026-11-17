# Related projects

All mine. The README keeps one line for each.

## photo-backup as a Windows program

[photo-backup-exe](https://github.com/rsilvabr/photo-backup-exe) packages this backup as `photo-backup.exe`: no Python to install. Unzip and double-click for the menu, or use the same command lines as here (`photo-backup.exe backup ...`, `manifest`, `restore`, `captureone`). The scripts inside are byte-for-byte those of a photo-backup release, and the settings at their top can be edited there as here. Presets and backups are shared: a backup made by one version is continued, verified and restored by the other.

This Python version, the one I run on my own archive, remains by far the more tested. Use the program if you would rather not install Python; otherwise use the scripts here.

## jxl-photo

[jxl-photo](https://github.com/rsilvabr/jxl-photo) turns the 16-bit TIFF exports into JPEG XL masters (lossless or near-lossless, ICC and metadata preserved) and makes small derivatives for the phone. Together: jxl-photo writes `_EXPORT\16B_JXL`, and photo-backup's default `jxl>tif>jpg` then backs up that compact master instead of the TIFF — in the archive this was built for, 76 GB of JXL instead of 426 GB of TIFF for one year.

The derivatives jxl-photo makes from a master are left out of the backup when the master is copied ([how](how_it_selects.md#jxl-photo-derivatives---derivatives-v050)), and one `.cmd` can run both every night ([Run it after jxl-photo](menu.md#run-it-after-jxl-photo)).
