"""The names of the PlayStation 3's system functions and kernel calls: what a game's import stubs and `sc` stubs get called.

A PS3 program calls the system's libraries through stubs the loader links by a library's name and a NID, a 32-bit
number: the first four bytes of the SHA-1 of the function's canonical name followed by a fixed 16-byte suffix, read
little-endian (not the PSP's plain SHA-1: nid_of below, checked in tests/test_ps3_imports.py). It calls the kernel
with `sc` and a call number in r11.

The names are public knowledge from the open-source PS3 SDK, PSL1GHT (MIT licence): each library's `exports.h` lists
`EXPORT(videoGetResolution, 0xe558748d)` under the library name its `config.h` gives (`cellSysutil`), and
`lv2/syscalls.h` numbers the kernel's calls. nids.json is those lists from the pinned commit below, with the SDK's
names (its C names, which are not always the canonical names the NIDs hash). LICENSE.psl1ght beside it is the SDK's
licence, which its copyright notice must travel with. Nothing here comes from firmware or from Sony's SDK.

    uv run python -m decompnet.consoles.ps3.nids   ->  rewrites nids.json and LICENSE.psl1ght from PSL1GHT_COMMIT

A NID the SDK doesn't name keeps its stub's `<library>_<NID>` name (imports.py).
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
DATABASE, LICENSE = HERE / "nids.json", HERE / "LICENSE.psl1ght"
PSL1GHT_COMMIT = "7e1bd96307eeac8000f18fd75e643d2fa826b5c1"  # ps3dev/PSL1GHT, read 2026-10-31
PSL1GHT_ARCHIVE = f"https://github.com/ps3dev/PSL1GHT/archive/{PSL1GHT_COMMIT}.tar.gz"
SUFFIX = bytes.fromhex("6759659904250490566427499489741A")
_EXPORT = re.compile(r"^\s*EXPORT(?:_VA)?\(\s*(\w+)\s*,\s*0x([0-9A-Fa-f]{1,8})\s*[,)]", re.M)
_LIBRARY = re.compile(r'#define\s+LIBRARY_NAME\s+"(\w+)"')
_SYSCALL = re.compile(r"^#define\s+SYSCALL_(\w+)\s+(\d+)\b", re.M)


def nid_of(name: str) -> int:
    """The NID a function's canonical name hashes to: how the PS3's own libraries number their functions."""
    return int.from_bytes(hashlib.sha1(name.encode() + SUFFIX).digest()[:4], "little")


@cache
def _raw() -> dict:
    return json.loads(DATABASE.read_text(encoding="utf-8"))


@cache
def database() -> dict[str, dict[int, str]]:
    """library -> {NID: name}, as nids.json keeps them."""
    return {library: {int(nid, 16): name for nid, name in names.items()} for library, names in _raw()["libraries"].items()}


def name(library: str, nid: int) -> str | None:
    """The function a library's NID names, None when the SDK doesn't know it."""
    return database().get(library, {}).get(nid)


@cache
def syscalls() -> dict[int, str]:
    """Kernel call number -> name (`sys_process_getpid`)."""
    return {int(number): label for number, label in _raw()["syscalls"].items()}


def from_sources(files: dict[str, str]) -> tuple[dict[str, dict[int, str]], dict[int, str]]:
    """(library -> {NID: name}, number -> syscall name) from the SDK's files (path -> text): each ppu/sprx/<lib>/
    exports.h under the library name its config.h gives, and ppu/include/lv2/syscalls.h."""
    libraries: dict[str, dict[int, str]] = {}
    for path, text in sorted(files.items()):
        if path.startswith("ppu/sprx/") and path.endswith("/exports.h"):
            config = files.get(path.removesuffix("exports.h") + "config.h", "")
            if match := _LIBRARY.search(config):
                for function, nid in _EXPORT.findall(text):
                    libraries.setdefault(match[1], {}).setdefault(int(nid, 16), function)
    calls: dict[int, str] = {}
    for label, number in _SYSCALL.findall(files.get("ppu/include/lv2/syscalls.h", "")):
        calls.setdefault(int(number), "sys_" + label.lower())
    return libraries, calls


def main() -> None:
    """nids.json and LICENSE.psl1ght rewritten from the pinned PSL1GHT."""
    import httpx

    archive = httpx.get(PSL1GHT_ARCHIVE, follow_redirects=True, timeout=120).raise_for_status().content
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            path = member.name.split("/", 1)[-1]  # without the archive's top folder
            if member.isfile() and (path == "LICENSE" or path.endswith(("/exports.h", "/config.h", "lv2/syscalls.h"))):
                files[path] = tar.extractfile(member).read().decode("utf-8", "replace")
    licence = files.pop("LICENSE", "")
    libraries, calls = from_sources(files)
    if not licence or not libraries or not calls:
        raise SystemExit(f"PSL1GHT {PSL1GHT_COMMIT} has no LICENSE, exports or syscall list where they used to be")
    DATABASE.write_text(json.dumps({
        "source": f"PSL1GHT (github.com/ps3dev/PSL1GHT) {PSL1GHT_COMMIT}, MIT licence: see LICENSE.psl1ght",
        "libraries": {library: {f"{nid:08X}": libraries[library][nid] for nid in sorted(libraries[library])}
                      for library in sorted(libraries)},
        "syscalls": {str(number): calls[number] for number in sorted(calls)},
    }, indent=0, ensure_ascii=True) + "\n", encoding="utf-8")
    LICENSE.write_text(f"nids.json is made from PSL1GHT ({PSL1GHT_ARCHIVE}), under its licence:\n\n{licence}",
                       encoding="utf-8")
    print(f"{sum(map(len, libraries.values()))} functions in {len(libraries)} libraries, {len(calls)} kernel calls")


if __name__ == "__main__":
    main()
