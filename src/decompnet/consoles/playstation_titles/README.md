# PlayStation titles

Serial → title names for PS1 and PS2 games, read from
[libretro-database](https://github.com/libretro/libretro-database)'s Redump lists
(`metadat/redump/Sony - PlayStation.dat`, `Sony - PlayStation 2.dat`). Credit: the libretro database
contributors and Redump; the lists are **CC BY-SA 4.0**, and their LICENSE is downloaded with them.

They aren't in this repository: `playstation-titles` in `toolchains.toml` pins the files (a libretro-database
commit and each file's SHA-256), and the first PS1/PS2 import installs them with the other tools. To update, change
the commit in the three URLs and their checksums. Showing a title never uses the network; without the lists a disc
keeps its volume label or serial. Titles identify a release for display only: they don't verify the contents or
change the matching judge.
