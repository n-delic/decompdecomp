# Offline PlayStation titles

`ps.json` and `ps2.json` contain only serial/title metadata derived from
[libretro-database](https://github.com/libretro/libretro-database), snapshot
`c4e17d41ec1bb744fb064f7cefeb61b878fc5d64`, `metadat/redump/Sony - PlayStation.dat`
and `metadat/redump/Sony - PlayStation 2.dat`. Credit: the libretro database
contributors and Redump. These derived databases retain the source's
**CC BY-SA 4.0** license, reproduced in LICENSE. No game files are included.

Changes: extract serial/title pairs, combine revision names, omit conflicting
serials, and serialize as JSON. Regenerate with `python tools/update_playstation_titles.py`.
Imports never access the network. Titles identify a release for display only;
they do not verify the contents or change the matching judge.
