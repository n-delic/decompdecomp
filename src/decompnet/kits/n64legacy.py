"""Original N64 compiler builds the user supplies because their archives have no approved download source.

Each card names one exact profile. Setup records the folder in place; no compiler executable or archive is copied
into decompdecomp's toolchain cache. The ordinary pinned runtime tools still supply wibo or the PsyQ converter.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import Kit, OwnCompiler, find

_SN_FILES = ('cc1n64.exe', 'cppn64.exe', 'asn64.exe')
_IRIX_FILES = ('usr/bin/qemu-irix', 'usr/lib/driver')
_PROFILES = {
    'sn-n64-281': ('SN64 GCC 2.8.1', _SN_FILES, 'cc1n64.exe', 'gcc'),
    'sn-n64-272-0004': ('SN64 GCC 2.7.2 build 0004', _SN_FILES, 'cc1n64.exe', 'gcc'),
    'ido-5.2': ('IDO 5.2', _IRIX_FILES, 'usr/lib/driver', 'ido'),
    'ido-5.3-cxx': ('IDO 5.3 C++', (*_IRIX_FILES, 'usr/lib/CC'), 'usr/lib/CC', 'ido'),
    'ido-6.0': ('IDO 6.0', _IRIX_FILES, 'usr/lib/driver', 'ido'),
    'mipspro-7.4.4': ('MIPSpro 7.4.4', _IRIX_FILES, 'usr/lib/driver', 'ido'),
}
_KNOWN_PROGRAMS = {
    'sn-n64-281': 'd90df1028c46d826e37146dbcc3e3385f78c59e05a9ddd952daa94c9ad379d9c',
    'sn-n64-272-0004': '17b0d9dc6a80b2aaec90f912bb442321ec8bc5cecaf7071f5bd187e1d6525d49',
}


class N64LegacyCompiler(Kit):
    """A specific build brought in by the user, kept at the location they select in Setup."""

    platform = 'N64'

    def __init__(self, profile: str, name: str, files: tuple[str, ...], program: str, family: str) -> None:
        self.id = 'own-' + profile
        self.profile = profile
        self.name = name + ' (your own copy)'
        self.files = files
        self.program = program
        self.family = family
        self.known_files = (program,)
        self.known = {_KNOWN_PROGRAMS[profile]: name} if profile in _KNOWN_PROGRAMS else {}
        host = 'Linux x86_64' if family == 'ido' else 'Linux or macOS'
        self.description = (f'Use your own {name} compiler folder on {host}. The folder stays where you keep it; '
                            'decompdecomp downloads no proprietary compiler files.')

    def build(self, folder: Path) -> str | None:
        if not self.locate(folder):
            return None
        program = find(folder, self.program)
        digest = hashlib.sha256(program.read_bytes()).hexdigest() if program else ''
        return self.profile if digest in self.known else f'{self.profile} (user selected; build unverified)'

    def locate(self, folder: Path) -> Path | None:
        return folder.resolve() if self.compilers(folder) else None

    def compilers(self, folder: Path) -> list[OwnCompiler]:
        from .. import toolchains

        host = toolchains.current_platform()
        if host not in (('linux-x86_64',) if self.family == 'ido' else
                        ('linux-x86_64', 'macos-x86_64', 'macos-arm64')):
            return []
        program = find(folder, self.program)
        if program is None or not all(find(folder, rel) for rel in self.files):
            return []
        digest = hashlib.sha256(program.read_bytes()).hexdigest()[:8]
        profile = toolchains.get(self.profile)
        return [OwnCompiler(f'{self.profile}-own-{digest}', f'{self.name} in {folder.name}', program, (),
                            runner='', build=self.profile, root=folder.resolve(), flags=tuple(profile.flags),
                            like=self.profile)]


N64_LEGACY = [N64LegacyCompiler(profile, *details) for profile, details in _PROFILES.items()]
