"""Consoles decompdecomp works on only through forks of public decomps (a project someone published, forked by its
repository address): their name, processor, m2c's target for drafts and the guides agents read first. Their ids are
the ones decomp.dev gave them, which those projects' configs keep. Starting one of their games from the user's own
copy needs a package of its own (consoles/template/): GBA had one before the rewrite (the git history keeps
it).
"""

from __future__ import annotations

from . import Console

PUBLIC_ONLY = [
    Console(id="psp", name="PSP", arch="mips", m2c="mipsel-gcc", guides=("mips",)),
    Console(id="gba", name="Game Boy Advance", short="GBA", arch="arm", m2c="gba", guides=("arm",)),
    Console(id="3ds", name="Nintendo 3DS", short="3DS", arch="arm", m2c="arm", guides=("arm", "cpp")),
    Console(id="switch", name="Switch", arch="aarch64", guides=("aarch64", "cpp")),
    Console(id="xbox", name="Xbox", arch="x86", guides=("x86", "cpp")),
    Console(id="xbox360", name="Xbox 360", arch="ppc", guides=("ppc", "cpp")),
]
