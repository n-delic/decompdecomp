# PS4 importer demo

This builds the repository's CC0 Demo Quest source with the pinned public clang
5.0.2 and lld. It needs no Sony SDK or game files. The output is a synthetic Orbis
ELF for importer and matching tests, not a bootable PS4 application.

Install Clang 5.0.2 in Compilers & tools, then run:

```sh
uv run python projects/demo-ps4/build.py
```

Open `build/demoquest.elf` in decompdecomp. Keep its adjacent
`demoquest.functions.jsonl` file: it supplies exact function extents without
requiring Ghidra. Generated files stay in the ignored `build/` directory.
