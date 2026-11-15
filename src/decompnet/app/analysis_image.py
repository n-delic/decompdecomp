"""Verify frozen analysis bytes separately from the original imported program."""
import hashlib
import re
from pathlib import Path

import yaml

from ..network import NetworkError


def _digest(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9a-fA-F]{40}',value)


def verify(image, proj, cfg, *, check=lambda: None):
    check()
    rom = cfg.get('rom', {})
    expected = rom.get('analysis_sha1')
    if expected is None:
        if rom.get('program',rom.get('file')) == rom.get('file'):
            expected = rom.get('sha1')
        else:
            expected = _legacy(proj, rom, check)
    if not _digest(expected):
        raise NetworkError('No recorded hash identifies this analysis image. Reimport the original game before auditing.')
    if hashlib.sha1(image).hexdigest() != expected.lower():
        raise NetworkError('The analysis image differs from its recorded hash. Restore it or reimport the original game before auditing.')


def _legacy(proj, rom, check):
    """Older derived imports recorded the image hash in their split configuration.

    Do not adopt the current image's hash: require a single, path-bound recorded
    split hash and an independently verified retained original program.
    """
    root = Path(proj['root']).resolve()
    program, image = rom.get('program'), rom.get('file')
    if not isinstance(program,str) or not isinstance(image,str) or not _digest(rom.get('sha1')):
        return None
    original = root/program
    analysis = root/image
    try:
        if (not original.resolve().is_relative_to(root) or original.is_symlink()
                or not analysis.resolve().is_relative_to(root)):
            return None
        configs = list(root.glob('*.yaml'))
        if len(configs)!=1 or configs[0].is_symlink():
            return None
        if configs[0].stat().st_size > 1024*1024:
            return None
        config = yaml.safe_load(configs[0].read_text(encoding='utf-8'))
        options = config.get('options', {})
        target = root/str(options.get('base_path','.'))/options['target_path']
        if target.resolve()!=analysis.resolve() or not _digest(config.get('sha1')):
            return None
    except (OSError, UnicodeError, yaml.YAMLError, AttributeError, KeyError, TypeError, ValueError, RuntimeError):
        return None
    try:
        stream = original.open('rb')
    except OSError:
        return None
    identity = hashlib.sha1()
    with stream:
        while True:
            check()
            try:
                block = stream.read(1024*1024)
            except OSError:
                return None
            if not block:break
            identity.update(block)
    if identity.hexdigest() != rom['sha1'].lower():
        raise NetworkError('The original program differs from its recorded hash. Restore it before auditing.')
    return config['sha1']
