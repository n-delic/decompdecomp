"""Your own compiler, one kit per console decompdecomp splits and links itself (ROM mode: N64, PS1, PS2, PSP, GBA),
in that console's group in Setup: another build of a compiler one of the console's pinned packs already runs (an EE-GCC, a CodeWarrior PS2, an
SNC, a PSP GCC, a KMC GCC, an IDO, an agbcc), which a game was built with and no pack has. A copy is the folder that
compiler is in, laid out as the pack's is (its program at the same place: `bin/ee-gcc`, `mwccps2.exe`, `psp/bin/gcc`),
and it runs exactly as that pack runs (OwnCompiler.like: the pack's command, wibo, the PS1's maspsx and assembler...),
with the user's folder in the pack's place. Nothing is copied.

Which pack a copy runs like: the packs whose program the folder has; of those sharing one program, the ones whose
version ("2.96", "3.0.1") is written in its program or its cc1, else the first. A console lists the copies that run
like one of its own packs (Console.kit_compilers), and each console's kit looks only at its packs (their `platform`). The other project-mode consoles' compilers (CodeWarrior for
GameCube, Wii and DS) aren't here: their builds pick compilers from the community's archive by version, which holds
every known one (ROADMAP.md, "Your own compiler for GameCube, Wii and DS").
"""

from __future__ import annotations

import functools
import hashlib
import re
import tomllib
from importlib import resources
from pathlib import Path

from . import Kit, OwnCompiler, file_version, find

# a pack's program in its command: "{tc}/bin/ee-gcc", or "$tc/gcc" inside a shell script; not its headers' folder
_PROGRAM = re.compile(r"(?:\{tc\}|\$tc)/([\w.+-]+(?:/[\w.+-]+)*)")
_VERSION = re.compile(r"\d+(?:\.\d+)+")


@functools.cache
def packs(platform: str) -> dict[str, tuple[str, str, str, tuple[str, ...]]]:
    """Every pinned compiler of a platform ("PS2", as Setup groups them) a copy can run like: its id -> (its program's path in its folder, its name, how it runs:
    packs alike in that, the builds of one CodeWarrior, are one way to run a copy; its flags). Read from
    toolchains.toml itself: the registry asks the kits for their facts as it's made."""
    pinned = tomllib.loads(resources.files("decompnet").joinpath("toolchains.toml").read_text(encoding="utf-8"))
    out = {}
    for tid, spec in pinned.items():
        if spec.get("kind", "compiler") != "compiler" or spec.get("platform") != platform:
            continue
        program = next((m for m in _PROGRAM.findall(" ".join([*spec.get("cc", []), *spec.get("cmd", [])]))
                        if m != "include"), None)
        if program:
            out[tid] = (program, spec.get("name", tid), repr((spec.get("cc"), spec.get("cmd"), spec.get("requires"))),
                        tuple(spec.get("flags", ())))
    return out


class OwnCompilers(Kit):
    """The kit of one console: `platform` is its packs' (and its Setup group's), `short` what its card calls it."""

    def __init__(self, kid: str, platform: str, short: str) -> None:
        self.id, self.platform = kid, platform
        self.name = f"Your own {short} compiler"
        self.description = (f"Another build of one of the {short}'s compilers, for a game built with one decompdecomp "
                            "doesn't have. Give the folder it's in, laid out as that compiler's is: it runs the same "
                            "way, from your folder.")

    @property
    def files(self) -> tuple[str, ...]:  # (what Setup says the folder should hold: one of these)
        return tuple(dict.fromkeys(program for program, *_ in packs(self.platform).values()))

    def locate(self, folder: Path) -> Path | None:
        try:
            return folder.resolve() if self.compilers(folder) else None
        except OSError:
            return None

    def build(self, folder: Path) -> str | None:
        """What the copy runs like, as Setup lists it: "runs like EE-GCC 2.96"."""
        names, found = packs(self.platform), self.compilers(folder)
        return "runs like " + " or ".join(names[c.like][1] for c in found) if found else None

    def verify(self, folder: Path) -> str:
        return ", ".join(f"version {c.build}" for c in self.compilers(folder) if c.build)

    def compilers(self, folder: Path) -> list[OwnCompiler]:
        by_program: dict[Path, list[tuple[str, str, str]]] = {}
        for tid, (program, name, runs, _) in packs(self.platform).items():
            if (exe := find(folder, program)) is not None:
                by_program.setdefault(exe, []).append((tid, name, runs))
        read = {}  # program -> (its hash, its version record, the kinds whose version it names)
        for exe, kinds in by_program.items():
            versions = {tid: v.group() for tid, _, _ in kinds if (v := _VERSION.search(tid))}
            stat = exe.stat()
            built = file_version(exe) if exe.suffix.lower() == ".exe" else ""  # (CodeWarrior's says 3.0.1.145)
            digest, seen = _read(str(exe), stat.st_mtime_ns, stat.st_size, str(folder), built, tuple(versions.values()))
            read[exe] = (digest, built, [k for k in kinds if versions.get(k[0]) in seen])
        # the kinds whose version the folder names; none named: the first. (A PSP GCC 1.3.1 also holds psp/bin/gcc,
        # which 1.7.1 runs: named nowhere, it isn't offered.)
        named = [(exe, k) for exe, (*_, ks) in read.items() for k in ks]
        # An installed pack's exact compiler binary identifies its build even when the executable has no
        # dotted version string (SN N64's 2.72 and 2.81 both call theirs cc1n64.exe).
        from .. import toolchains

        for exe, kinds in by_program.items():
            exact = []
            for kind in kinds:
                tid = kind[0]
                if toolchains.installed(tid) is None:
                    continue
                bundled = toolchains.path(tid) / packs(self.platform)[tid][0]
                if bundled.is_file() and bundled.stat().st_size == exe.stat().st_size \
                        and hashlib.sha256(bundled.read_bytes()).hexdigest()[:8] == read[exe][0]:
                    exact.append((exe, kind))
            if exact:
                named = [(candidate, kind) for candidate, kind in named if candidate != exe] + exact
        if not named and by_program:
            exe, kinds = next(iter(by_program.items()))
            named = [(exe, kinds[0])]
        alike = {runs: (exe, tid, name) for exe, (tid, name, runs) in reversed(named)}  # (the first of those alike)
        out = []
        for exe, tid, name in alike.values():  # (its id starts with the pack's: what a console's flags go by, "mwcps2")
            digest, built, _ = read[exe]
            out.append(OwnCompiler(f"{tid}-own-{digest}", f"{name}, your copy in {folder.name}", exe, (), runner="",
                                   build=built, root=folder.resolve(), flags=packs(self.platform)[tid][3], like=tid))
        return out


@functools.lru_cache(maxsize=64)
def _read(exe: str, mtime_ns: int, size: int, folder: str, built: str,
          versions: tuple[str, ...]) -> tuple[str, frozenset[str]]:
    """A program's hash (what its compiler id is made of) and which of `versions` its bytes, its cc1's or its version
    record (`built`) name, as a whole number ("2.9" isn't in "2.95.2", nor "3.2" in "13.2"). Cached by its time and
    size: a console's compiler list asks often."""
    data = Path(exe).read_bytes()
    near = [*Path(exe).parent.glob("cc1*"), *Path(folder).glob("lib/gcc-lib/*/*/cc1*"), *Path(folder).glob("cc1*")]
    text = data + b"".join(p.read_bytes() for p in sorted(set(near))[:4] if p.is_file()) + b" " + built.encode()
    named = frozenset(v for v in versions if re.search(rb"(?<![\d.])" + re.escape(v.encode()) + rb"(?!\d)", text))
    return hashlib.sha256(data).hexdigest()[:8], named


OWN_COMPILERS = [OwnCompilers(f"own-{c}", platform, short) for c, platform, short in (
    ("n64", "N64", "N64"), ("ps1", "PS1", "PS1"), ("ps2", "PS2", "PS2"), ("psp", "PSP", "PSP"),
    ("gba", "Game Boy Advance", "GBA"))]
