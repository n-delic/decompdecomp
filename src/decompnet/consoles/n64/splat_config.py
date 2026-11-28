"""Keep cartridge titles safe while splat creates its initial YAML."""

import hashlib
import json
import re
import struct

import yaml


def _boot_call_size(rom, vram, offset, size):
    """A call immediately into the following function ends this boot stub.

    Splat otherwise reads the callee as part of the entrypoint and crashes on
    its indirect call. Keep this exception narrow: the original instructions
    remain untouched and the direct call and its delay slot are both retained.
    """
    data = rom[offset:offset+size]
    words = [word for word, in struct.iter_unpack('>I', data[:len(data)//4*4])]
    for index, word in enumerate(words):
        opcode = word >> 26
        if opcode in (1, 2, 4, 5, 6, 7) or (word & 0xfc00003f) in (8, 9):
            break
        if opcode != 3:
            continue
        pc = vram + 4*index
        target = ((pc+4) & 0xf0000000) | ((word & 0x03ffffff) << 2)
        if (target == pc+8 and index+1 < len(words)
                and any((w & 0xfc00003f) == 9 for w in words[index+2:])):
            return 4*(index+2)
        break
    return size


def create(args, workdir, log):
    from ...rom.split import _splat

    path = workdir / args[-1]
    with path.open('rb') as stream:
        header = stream.read(64)
    if len(header) < 64 or header[:4] != b'\x80\x37\x12\x40':
        return _splat(args, workdir, log)
    with path.open('rb') as stream:
        boot = stream.read(0x1100)
    vram = int.from_bytes(header[8:12], 'big')
    module = ('decompnet.consoles.n64.splat_boot_config'
              if _boot_call_size(boot, vram, 0x1000, 0x100) < 0x100 else 'splat')
    def run():
        return _splat(args, workdir, log, module=module) if module != 'splat' else _splat(args, workdir, log)
    original = header[0x20:0x34]
    try:
        name = original.decode('ascii').rstrip(' \x00')
    except UnicodeDecodeError:
        return run()
    expected = name.title() + ' (Region)'
    try:
        safe = yaml.safe_load('name: ' + expected)
    except yaml.YAMLError:
        safe = None
    if safe == {'name': expected}:
        return run()
    # Only splat's config generator sees the sanitized title. Its output is
    # corrected after restoring the import copy, before any analysis reads it.
    replacement = bytes(c if 32 <= c < 127 and chr(c).isalnum() else 32 for c in original)
    replacement = re.sub(rb' +', b' ', replacement).strip()[:20].ljust(20, b' ')
    if not replacement.strip():
        replacement = b'Cartridge'.ljust(20, b' ')
    with path.open('r+b') as stream:
        stream.seek(0x20)
        stream.write(replacement)
    try:
        output = run()
    finally:
        with path.open('r+b') as stream:
            stream.seek(0x20)
            stream.write(original)
    configs = sorted(workdir.glob('*.yaml'))
    if configs:
        config = configs[0]
        text = config.read_text(encoding='utf-8')
        current = yaml.safe_load(text)
        safe_name = replacement.decode('ascii').strip().title()
        restored = current['name'].replace(safe_name, name.title(), 1)
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha1').hexdigest()
        text = re.sub(r'^name:.*$', 'name: ' + json.dumps(restored), text, count=1, flags=re.M)
        text = re.sub(r'^sha1:.*$', 'sha1: ' + sha, text, count=1, flags=re.M)
        config.write_text(text, encoding='utf-8')
    return output
