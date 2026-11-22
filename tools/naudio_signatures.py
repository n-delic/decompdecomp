"""Write the N64's built-in n_audio or libultra signatures from the user's own copies of the SDK's archives.

    uv run python tools/naudio_signatures.py <libgn_audio.a> [<libgn_audio_d.a> ...]
    uv run python tools/naudio_signatures.py --library libultra <libultra.a|libgultra.a|..._d.a|..._rom.a> ...

The built-in ultralib table (tools/ultralib_signatures.py) is libultra rebuilt from public source, but n_audio
(`libgn_audio.a`: the n_al* synthesizer and the sequence and sound players) isn't in ultralib, and no public C
rebuilds it as the SDK shipped it: the games that carry it in C (Rare's) built their own versions with IDO, and
the NuSystem games only the synthesizer. So without the SDK a racing game had its 50 n_audio functions unfound.
Like the GameCube's decomp-toolkit corpus, this table is made from the original archives: each function's
fingerprint (twins._key: masked code and relocation layout, a SHA-1), size, name and the archives it came from,
less what the ultralib table already has (`known`). No code is written. The archives are read where they are, never copied.

An archive's version comes from its path, as Settings' SDK folders label it (`2.0K`), and its name from the file:
`2.0K/libgn_audio`.

`--library libultra` does the same for libultra itself (`libultra.a` of 2.0I, `libgultra.a` of 2.0J to 2.0L, with
their `_d` and `_rom` builds). The ultralib table is libultra rebuilt from public source with the app's compilers; the
SDK's own builds (SGI's cc for 2.0I, GCC for the rest) differ from it, so a game linked with them had most of its
library functions (the 64-bit helpers, the audio synthesizer, the thread queues, the Controller Pak checksums)
unfound by that table and left open for agents. This table holds what the SDK's archives have beyond it: the
fingerprints neither the ultralib nor the n_audio table names.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import defaultdict
from pathlib import Path

from elftools.common.exceptions import ELFError

from decompnet.app.sdk_assembly import object_prints
from decompnet.app.twins import ACROSS_MIN_BYTES, _ar_members

TABLES = Path(__file__).resolve().parent.parent / "src" / "decompnet" / "consoles" / "n64"
OUT = TABLES / "naudio_signatures.tsv"
ULTRALIB = TABLES / "ultralib_signatures.tsv"
NAME = re.compile(r"libgn_audio(_d)?\.a", re.I)
# the libraries this tool writes a table for: the archives' names, the table, and the title of its corpus
LIBRARIES = {
    "n_audio": (NAME, OUT, "n_audio"),
    "libultra": (re.compile(r"libg?ultra(_d|_rom)?\.a", re.I), TABLES / "libultra_signatures.tsv", "libultra"),
}


def label(path: Path, library: str = "n_audio") -> str:
    """`2.0K/libgn_audio` for an archive in an SDK folder of 2.0K."""
    version = re.search(r"2\.0([A-L])", str(path), re.I)
    if not version or not LIBRARIES[library][0].fullmatch(path.name):
        raise SystemExit(f"{path}: choose a {'libgn_audio.a or libgn_audio_d.a' if library == 'n_audio' else 'libultra.a or libgultra.a (or its _d / _rom build)'} "
                         "in an SDK folder named for its version (2.0I, 2.0J, 2.0K, 2.0L)")
    return f"2.0{version[1].upper()}/{path.name.lower()[:-2]}"


def prints(path: Path) -> set[tuple[str, int, str]]:
    """{(fingerprint, size, function)} of an archive's objects, as Settings indexes it (twins.import_sdk_objects):
    every object's copy, since a static function can be in two (n_audio's `__n_setUsptFromTempo`, in both
    sequence players)."""
    found = set()
    for member in _ar_members(path.read_bytes()):
        try:
            items = object_prints(member).items()
        except (ELFError, ValueError, KeyError):
            continue  # an archive's symbol table, a member that isn't an object
        found |= {(p.key, p.size, name) for name, p in items if p.size >= ACROSS_MIN_BYTES}
    return found


def known(path: Path) -> set[str]:
    """The fingerprints the ultralib table already names. n_audio keeps libultra's own audio code under `n_` names
    (`n_alCSeqNew` is `alCSeqNew` byte for byte), and one fingerprint with two names is no identity at all: with
    both in the tables a racing game lost 33 functions the ultralib table had found. Those keep libultra's name;
    for the port both are the synthesizer it compiles."""
    return {line.split("\t")[0] for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")}


def table(archives: list[Path], skip: set[str] = frozenset(), library: str = "n_audio") -> list[str]:
    rows: dict[tuple[str, int, str], set[str]] = defaultdict(set)
    sources = []
    for path in sorted(set(archives)):
        build = label(path, library)
        found = prints(path)
        print(f"{build}: {len(found)} functions ({path})")
        sources.append(f"{build} sha256:{hashlib.sha256(path.read_bytes()).hexdigest()[:12]}")
        for row in found:
            if row[0] not in skip:
                rows[row].add(build)
    versions = sorted({s.split("/")[0] for s in sources})
    lines = [f"# {LIBRARIES[library][2]} {versions[0]}-{versions[-1]}, fingerprints of the SDK's own archives by tools/naudio_signatures.py "
             f"(hashes, sizes and names; no code): {'; '.join(sorted(set(sources)))}",
             "# fingerprint\tsize\tname\tbuilds"]
    lines += [f"{key}\t{size}\t{name}\t{','.join(sorted(builds))}" for (key, size, name), builds in sorted(rows.items())]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("archives", type=Path, nargs="+", help="the SDK's libgn_audio.a and libgn_audio_d.a, or libultra's")
    parser.add_argument("--library", choices=sorted(LIBRARIES), default="n_audio")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--ultralib", type=Path, default=ULTRALIB, help="the ultralib table whose fingerprints to leave out")
    args = parser.parse_args()
    out = args.out or LIBRARIES[args.library][1]
    skip = known(args.ultralib)
    if args.library == "libultra":  # (n_audio keeps libultra's own audio code under its n_ names: one name per fingerprint)
        skip |= known(OUT)
    lines = table([p.resolve() for p in args.archives], skip, args.library)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(lines) - 2} signatures written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
