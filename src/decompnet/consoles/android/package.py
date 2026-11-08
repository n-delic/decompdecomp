"""Read APK/XAPK members in memory: no archive-supplied path is ever written to disk (#133).

An XAPK can carry gigabytes of OBB assets. Only its APKs and the chosen native library are read;
limits apply to declared and actual member sizes, APK count, and total decompressed bytes.
"""

from __future__ import annotations

import hashlib
import io
import struct
import zipfile
from pathlib import Path

from ...rom import Picked, RomError

MAX_APK_BYTES = 512 << 20
MAX_LIBRARY_BYTES = 256 << 20
MAX_TOTAL_BYTES = 1 << 30
MAX_MEMBERS = 50000
MAX_APKS = 32


def arm_elf(head: bytes) -> bool:
    return (len(head) >= 52 and head[:6] == b"\x7fELF\x01\x01"
            and struct.unpack_from("<HH", head, 16) == (3, 40))


def sniff(head: bytes, file) -> bool:
    if arm_elf(head):
        return True
    if not head.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(file if isinstance(file, Path) else io.BytesIO(file)) as archive:
            names = set(archive.namelist())
            return "AndroidManifest.xml" in names or ("manifest.json" in names and any(n.endswith(".apk") for n in names))
    except (OSError, ValueError, zipfile.BadZipFile):
        return False


def read(file, name: str = "") -> Picked:
    path = Path(file) if isinstance(file, (str, Path)) else None
    title = Path(name or (path.name if path else "Android game")).stem
    head = path.open("rb") if path else io.BytesIO(file)
    with head as stream:
        magic = stream.read(52)
    selected = ""
    if arm_elf(magic):
        if (path.stat().st_size if path else len(file)) > MAX_LIBRARY_BYTES:
            raise RomError("This native library exceeds the 256 MiB Android import limit.")
        data = path.read_bytes() if path else bytes(file)
        selected = path.name if path else name or "game.so"
        count = 1
    else:
        candidates = []
        budget = MAX_TOTAL_BYTES

        def member(archive, entry, maximum):
            nonlocal budget
            if entry.file_size > maximum or entry.file_size > budget:
                raise RomError("This Android archive exceeds the import limits. Extract the ARMv7 .so library yourself and import it directly.")
            with archive.open(entry) as src:
                payload = src.read(maximum + 1)
            if len(payload) > maximum or len(payload) > budget:
                raise RomError("This Android archive expands beyond the import limit. Import its ARMv7 .so directly.")
            budget -= len(payload)
            return payload

        def libraries(archive, prefix=""):
            entries = archive.infolist()
            if len(entries) > MAX_MEMBERS:
                raise RomError("This Android archive contains too many files. Import its ARMv7 .so directly.")
            for entry in entries:
                parts = entry.filename.split("/")
                if len(parts) == 3 and parts[:2] == ["lib", "armeabi-v7a"] and parts[2].endswith(".so"):
                    candidates.append((entry.file_size, prefix + entry.filename, archive, entry))

        # Keep APK streams open until the largest library has been read; assets never leave their archive.
        from contextlib import ExitStack

        try:
            with ExitStack() as stack:
                archive = stack.enter_context(zipfile.ZipFile(path if path else io.BytesIO(file)))
                libraries(archive)
                apks = [e for e in archive.infolist() if e.filename.endswith(".apk")]
                if len(apks) > MAX_APKS:
                    raise RomError("This XAPK contains too many APKs. Import its ARMv7 .so directly.")
                for entry in apks:
                    inner = stack.enter_context(zipfile.ZipFile(io.BytesIO(member(archive, entry, MAX_APK_BYTES))))
                    libraries(inner, entry.filename + ":")
                if not candidates:
                    raise RomError("No lib/armeabi-v7a/*.so library was found. This Android beta supports native 32-bit ARM code; DEX and ARM64 aren't supported yet.")
                _, selected, owner, entry = max(candidates, key=lambda c: (c[0], c[1]))
                data = member(owner, entry, MAX_LIBRARY_BYTES)
                count = len(candidates)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as error:
            raise RomError(f"Couldn't read this Android package: {error}. Extract its ARMv7 .so and import that file.") from None
    if not arm_elf(data[:52]):
        raise RomError("The selected Android library isn't a little-endian 32-bit ARM shared ELF. Choose an armeabi-v7a library.")
    return Picked("android", data, "game.so", {
        "title": title, "game_code": "", "region": "", "size": len(data),
        "sha1": hashlib.sha1(data).hexdigest(), "compiler_guess": "CLANG",
        "library": selected, "native_libraries": count,
        "scope": "largest ARMv7 native library only; DEX, other libraries and assets are not decompiled",
    }, False)
