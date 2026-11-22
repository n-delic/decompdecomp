"""Keep cartridge titles safe while splat creates its initial YAML."""

import hashlib
import json
import re

import yaml


def create(args, workdir, log):
    from ...rom.split import _splat

    path = workdir / args[-1]
    with path.open('rb') as stream:
        header = stream.read(64)
    if len(header) < 64 or header[:4] != b'\x80\x37\x12\x40':
        return _splat(args, workdir, log)
    original = header[0x20:0x34]
    try:
        name = original.decode('ascii').rstrip(' \x00')
    except UnicodeDecodeError:
        return _splat(args, workdir, log)
    expected = name.title() + ' (Region)'
    try:
        safe = yaml.safe_load('name: ' + expected)
    except yaml.YAMLError:
        safe = None
    if safe == {'name': expected}:
        return _splat(args, workdir, log)
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
        output = _splat(args, workdir, log)
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
