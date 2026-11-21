"""A homemade Android shared library and APK for native import/judge conformance (#133).

Uses the repository's CC0 Demo Quest C, not a commercial game's code. No Android SDK,
Java compiler, device or signing key is needed: this is a native analysis fixture, not an installable app.
"""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.limits import capped

HERE = Path(__file__).resolve().parent
BUILD = HERE / 'build'
GAME = HERE.parent / 'demo-n64' / 'original'


def main():
    tc = toolchains.ensure('android-clang-3.5')
    BUILD.mkdir(exist_ok=True)
    cmd = [a.replace('{tc}', str(tc)) for a in toolchains.get('android-clang-3.5').cc]
    subprocess.run(capped([*cmd, *toolchains.get('android-clang-3.5').flags, '-I', str(GAME),
                           str(GAME / 'game.c'), '-o', str(BUILD / 'game.o')]), check=True, timeout=120)
    extra = BUILD / 'extra.c'
    extra.write_text('int demo_answer(void) { return 42; }\n'
                     'int demo_twice(int x) { return x * 2; }\n'
                     'int demo_identity(int x) { return x; }\n'
                     'typedef struct { int unused; int value; } DemoFields;\n'
                     'int demo_field(DemoFields *p) { return p->value; }\n', encoding='utf-8')
    subprocess.run(capped([*cmd, *toolchains.get('android-clang-3.5').flags, str(extra),
                           '-o', str(BUILD / 'extra.o')]), check=True, timeout=120)
    subprocess.run(capped([str(tc / 'gcc/bin/arm-linux-androideabi-ld'), '-shared',
                           str(BUILD / 'game.o'), str(BUILD / 'extra.o'), '-o', str(BUILD / 'game.so')]), check=True, timeout=120)
    with zipfile.ZipFile(BUILD / 'demo.apk', 'w', compression=zipfile.ZIP_DEFLATED) as apk:
        apk.writestr('AndroidManifest.xml', '<manifest package="org.decompdecomp.demo"/>')
        apk.write(BUILD / 'game.so', 'lib/armeabi-v7a/libdemo.so')
    with (BUILD / 'game.so').open('rb') as f:
        symbols = {s.name: s['st_value'] for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols() if s.name}
    (BUILD / 'demo.json').write_text(json.dumps({'game': 'demo.apk', 'symbols': symbols}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
