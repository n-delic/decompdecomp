"""Rebuild libultra from decompals/ultralib's public source and write the N64's built-in library signatures.

    git clone https://github.com/decompals/ultralib.git <dir> && git -C <dir> checkout <COMMIT below>
    uv run python tools/ultralib_signatures.py <dir> [--compare-db DECOMP_HOME/.decomp/decomp.db]

Without the user's own SDK, library detection had nothing to compare a game's functions with: a PC with no N64 SDK
indexed found 2 library functions in a racing game where one with six SDK folders found 152, and the port's
WebAssembly build then compiled libultra's own C. ultralib rebuilds Nintendo's archives byte for byte from public C
and assembly, so the fingerprints of its objects are the SDK's, made without it. This builds each version and archive
ultralib vouches for (its README's table) with the app's own pinned compilers (gcc-2.7.2-kmc: decompals'
GCC 2.7.2 and binutils 2.6, ido-5.3: the IDO 5.3 recompilation, the ones ultralib's tools/Makefile fetches), keeps
the objects each archive holds (base/<version>/<archive>.txt) and writes every function's fingerprint (twins._key:
masked code and relocation layout, a SHA-1), size, name and the builds it came from. No code is written: the table is
hashes and names, like the GameCube's decomp-toolkit corpus.

--compare-db reads (read only) the signatures a decompdecomp home indexed from the original archives (Settings, SDK
folders, labelled `libultra 2.0X / <archive>.a`) and reports how many of their functions the rebuild reproduces.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from decompnet import toolchains
from decompnet.app.sdk_assembly import object_prints
from decompnet.app.twins import ACROSS_MIN_BYTES

COMMIT = "e24c836796df4bf520ff8b11a5c9d2cea3a66cbd"
VERSIONS = ("I", "J", "K", "L")
# ultralib's README: every archive of 2.0I-2.0L builds but IDO's libultra_d
ARCHIVES = ("libultra", "libultra_rom", "libgultra", "libgultra_d", "libgultra_rom")
OUT = Path(__file__).resolve().parent.parent / "src" / "decompnet" / "consoles" / "n64" / "ultralib_signatures.tsv"


def tools(checkout: Path) -> None:
    """ultralib's tools/gcc and tools/ido, as links to the app's pinned copies of the same compilers."""
    for folder, pack, extra in (("gcc", "gcc-2.7.2-kmc", {"strip-2.7": "strip"}), ("ido", "ido-5.3", {})):
        source = Path(toolchains.ensure(pack, log=print))
        target = checkout / "tools" / folder
        target.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            link = target / item.name
            if not link.exists():
                link.symlink_to(item)
        for name, of in extra.items():
            if not (target / name).exists():
                (target / name).symlink_to(source / of)


def build(checkout: Path, version: str, archive: str) -> dict[str, tuple[str, int]]:
    """{function: (fingerprint, size)} of the objects the archive holds, built without the original to compare."""
    # AR_OLD=true: the archive itself isn't needed, only its objects (and ultralib's IDO ar wants binutils)
    done = subprocess.run(["make", "-k", f"-j{os.cpu_count() or 4}", "COMPARE=0", f"VERSION={version}",
                           f"TARGET={archive}", "AR_OLD=true"], cwd=checkout, capture_output=True, text=True)
    # by name in any case, as ultralib's Makefile finds them (`find -iname`): IDO's lists name drvrNew.o, the file
    # is drvrnew.o
    members = {m.lower() for m in (checkout / "base" / version / f"{archive}.txt").read_text().split()}
    found: dict[str, tuple[str, int]] = {}
    built = set()
    for obj in sorted((checkout / "build" / version / archive).rglob("*.o")):
        if obj.name.lower() not in members:
            continue
        built.add(obj.name.lower())
        for name, p in object_prints(obj.read_bytes()).items():
            if p.size >= ACROSS_MIN_BYTES:
                found[name] = (p.key, p.size)
    if missing := sorted(members - built):
        print(f"  2.0{version} {archive}: {len(missing)} of {len(members)} objects didn't build: {missing[:8]}"
              + (f"\n{done.stderr[-1500:]}" if done.returncode else ""))
    return found


def compare(db: Path, version: str, archive: str, found: dict) -> str:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute("SELECT name, fingerprint, size FROM sdk_signatures WHERE lower(corpus) LIKE ?",
                        (f"libultra 2.0{version.lower()} / {archive}.a /%",)).fetchall()
    if not rows:
        return "no original indexed"
    same = sum(found.get(n) == (k, s) for n, k, s in rows)
    differ = sorted(n for n, k, s in rows if n in found and found[n] != (k, s))
    return f"{same} of {len(rows)} original functions reproduced" + (f"; differ: {', '.join(differ[:6])}" if differ else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ultralib", type=Path, help=f"a clone of decompals/ultralib at {COMMIT}")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--compare-db", type=Path, help="a decompdecomp home's .decomp/decomp.db (read only)")
    args = parser.parse_args()
    checkout = args.ultralib.resolve()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, text=True).stdout.strip()
    if head != COMMIT:
        sys.exit(f"{checkout} is at {head or 'no commit'}: check out {COMMIT}, the commit this table pins")
    tools(checkout)
    rows: dict[tuple[str, int, str], list[str]] = defaultdict(list)
    for version in VERSIONS:
        for archive in ARCHIVES:
            found = build(checkout, version, archive)
            note = compare(args.compare_db, version, archive, found) if args.compare_db else ""
            print(f"2.0{version} {archive}: {len(found)} functions" + (f" ({note})" if note else ""))
            for name, (key, size) in found.items():
                rows[key, size, name].append(f"2.0{version}/{archive}")
    lines = [f"# decompals/ultralib {COMMIT}, rebuilt by tools/ultralib_signatures.py with the app's pinned compilers",
             "# fingerprint\tsize\tname\tbuilds"]
    lines += [f"{key}\t{size}\t{name}\t{','.join(builds)}" for (key, size, name), builds in sorted(rows.items())]
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(rows)} signatures ({len({k for k, _, _ in rows})} distinct fingerprints) written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
