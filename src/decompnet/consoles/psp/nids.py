"""The names of the PSP's system functions, by library and NID: what a game's import stubs get called.

A PSP program calls the system through stubs the loader links by a library's name and a NID, a 32-bit number (the
first four bytes of the SHA-1 of the function's name, little-endian). The names are public knowledge: the open-source
PSP SDK (pspsdk, BSD licence) declares every function it knows in its import files
(`IMPORT_FUNC "sceDisplay",0x289D82FE,sceDisplaySetFrameBuf`), and nids.json is those declarations, library by
library, from the pinned commit below. LICENSE.pspsdk beside it is the SDK's licence, which its copyright notice must
travel with.

    uv run python -m decompnet.consoles.psp.nids   ->  rewrites nids.json and LICENSE.pspsdk from PSPSDK_COMMIT

A NID the SDK doesn't name keeps its stub's `<library>_<NID>` name (prx.stub_names).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from functools import cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATABASE, LICENSE = HERE / "nids.json", HERE / "LICENSE.pspsdk"
PSPSDK_COMMIT = "6ef2b21dc440ef8d4125f000301faaa2a470975e"  # pspdev/pspsdk, 2026-10-16
PSPSDK_ARCHIVE = f"https://github.com/pspdev/pspsdk/archive/{PSPSDK_COMMIT}.tar.gz"
NID_BYTES = 4
# `IMPORT_FUNC "Library",0xNID,name` (and _WITH_ALIAS, whose first name is the one the NID hashes)
_IMPORT = re.compile(r'^\s*IMPORT_FUNC(?:_WITH_ALIAS)?\s+"(\w+)"\s*,\s*0x([0-9A-Fa-f]{1,8})\s*,\s*(\w+)', re.M)


def nid_of(name: str) -> int:
    """The NID a function's name hashes to: how the PSP's own libraries number their functions."""
    return int.from_bytes(hashlib.sha1(name.encode()).digest()[:NID_BYTES], "little")


@cache
def database() -> dict[str, dict[int, str]]:
    """library -> {NID: name}, as nids.json keeps them."""
    raw = json.loads(DATABASE.read_text(encoding="utf-8"))
    return {library: {int(nid, 16): name for nid, name in names.items()} for library, names in raw["libraries"].items()}


def name(library: str, nid: int) -> str | None:
    """The function a library's NID names, None when the SDK doesn't know it."""
    return database().get(library, {}).get(nid)


def from_sources(files: dict[str, str]) -> dict[str, dict[int, str]]:
    """library -> {NID: name} from the SDK's import files (path -> text). Its samples are left out (they import the
    sample's own libraries), and so are its stand-in names (`sceNand_driver_C478C1DE`): an unknown NID is named that
    way without them."""
    found: dict[str, dict[int, str]] = {}
    for path, text in sorted(files.items()):
        if "/samples/" in path:
            continue
        for match in _IMPORT.finditer(text):
            library, nid, function = match[1], int(match[2], 16), match[3]
            if function.upper().endswith(f"{nid:08X}"):
                continue
            found.setdefault(library, {}).setdefault(nid, function)
    return found


def main() -> None:
    """nids.json and LICENSE.pspsdk rewritten from the pinned pspsdk."""
    import httpx

    archive = httpx.get(PSPSDK_ARCHIVE, follow_redirects=True, timeout=120).raise_for_status().content
    files, licence = {}, ""
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            path = member.name.split("/", 1)[-1]  # without the archive's top folder
            if member.isfile() and (path == "LICENSE" or path.startswith("src/") and path.endswith((".S", ".s"))):
                text = tar.extractfile(member).read().decode("utf-8", "replace")
                if path == "LICENSE":
                    licence = text
                else:
                    files[path] = text
    if not licence or not files:
        raise SystemExit(f"pspsdk {PSPSDK_COMMIT} has no LICENSE or import files where they used to be")
    found = from_sources(files)
    DATABASE.write_text(json.dumps({
        "source": f"pspsdk (github.com/pspdev/pspsdk) {PSPSDK_COMMIT}, BSD licence: see LICENSE.pspsdk",
        "libraries": {library: {f"{nid:08X}": found[library][nid] for nid in sorted(found[library])}
                      for library in sorted(found)},
    }, indent=0, ensure_ascii=True) + "\n", encoding="utf-8")
    LICENSE.write_text(f"nids.json is made from pspsdk ({PSPSDK_ARCHIVE}), under its licence:\n\n{licence}",
                       encoding="utf-8")
    print(f"{sum(map(len, found.values()))} functions in {len(found)} libraries")


if __name__ == "__main__":
    main()
