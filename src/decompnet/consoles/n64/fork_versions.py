"""Original cartridge versions in public N64 Splat builds."""

from __future__ import annotations

import re
from pathlib import Path

from ...network import NetworkError
from ...rom import RomError
from .rom import z64_file_sha1


def version_configs(root: Path) -> dict[str, Path]:
    """Only infer editions from the YAML template the project's Makefile actually uses."""
    makefile = root / 'Makefile'
    if not makefile.is_file():
        return {}
    make = makefile.read_text(errors='replace', encoding='utf-8')
    basename = re.search(r'^BASENAME\s*[:?]?=\s*([A-Za-z0-9_-]+)\s*$', make, re.M)
    if not basename:
        return {}
    suffix = '$(BASENAME).$(VERSION).yaml'
    nested = 'config/$(VERSION)/' + suffix
    if nested in make:
        paths = root.glob('config/*/' + basename[1] + '.*.yaml')
    elif suffix in make:
        paths = root.glob(basename[1] + '.*.yaml')
    else:
        return {}
    configs = {}
    for path in sorted(paths):
        version = path.name[len(basename[1]) + 1:-5]
        if (re.fullmatch(r'[A-Za-z0-9._-]+', version) and
                (nested not in make or path.parent.name == version) and
                root.resolve() in path.resolve().parents):
            configs[version] = path
    return configs


def original_roms(root: Path) -> dict[str, str]:
    """Find original hashes; never confuse generated decompressed-image hashes for cartridge hashes."""
    makefile = root / 'Makefile'
    if not makefile.is_file():
        return {}
    make = makefile.read_text(errors='replace', encoding='utf-8')
    configs = version_configs(root)
    if configs:
        import yaml

        originals = {}
        for version, path in configs.items():
            try:
                config = yaml.safe_load(path.read_text(encoding='utf-8'))
            except (OSError, ValueError, yaml.YAMLError):
                return {}  # Do not advertise a partial list of supported versions.
            if not isinstance(config, dict) or not isinstance(config.get('options'), dict):
                return {}
            options = config['options']
            digest = config.get('sha1', options.get('sha1'))
            base = path.parent / str(options.get('base_path', '.'))
            if not (options.get('target_path') == f'baserom.{version}.z64'
                    and base.resolve() == root.resolve()
                    and isinstance(digest, str) and re.fullmatch(r'[0-9a-fA-F]{40}', digest)):
                return {}
            originals[version] = digest.lower()
        return originals
    if not (re.search(r'^BASEROM\s*[:?+]?=\s*baserom\.\$\(VERSION\)\.z64\s*$', make, re.M) and
            re.search(r'^DECOMPRESSED_BASEROM\s*[:?+]?=\s*decompressed\.\$\(VERSION\)\.z64\s*$', make, re.M)):
        return {}
    readme = root / 'README.md'
    if not readme.is_file():
        return {}
    text = readme.read_text(errors='replace', encoding='utf-8')
    configured = {p.name.removeprefix('decompressed.').removesuffix('.yaml')
                  for p in root.glob('decompressed.*.yaml')}
    return {version: digest.lower() for version, digest in re.findall(
        r'`?baserom\.([A-Za-z0-9._-]+)\.z64`?\s*:\s*`?([0-9a-fA-F]{40})\b', text)
            if version in configured}


def default_version(root: Path, versions: dict[str, str]) -> str | None:
    """Prefer the Makefile's VERSION, then the first recognized original edition."""
    if not versions:
        return None
    make = (root / 'Makefile').read_text(errors='replace', encoding='utf-8')
    selected = re.search(r'^VERSION\s*[:?]?=\s*([A-Za-z0-9._-]+)', make, re.M)
    return selected[1] if selected and selected[1] in versions else next(iter(versions))


def match(game: Path, versions: dict[str, str], log=print) -> str | None:
    """Select an original edition from normalized z64, v64 or n64 bytes."""
    if not versions:
        return None
    try:
        identity = z64_file_sha1(game)
    except (OSError, RomError) as error:
        raise NetworkError(f'Could not read that N64 ROM: {error}') from error
    if identity is None:
        raise NetworkError('That file is not an N64 cartridge ROM (.z64, .v64 or .n64).')
    digest, byte_order = identity
    version = next((v for v, sha1 in versions.items() if sha1 == digest), None)
    if version is None:
        raise NetworkError('That ROM does not match any supported original version: '
                           + ', '.join(f'{v}: {sha1}' for v, sha1 in versions.items()))
    if byte_order != 'z64':
        log(f'your {byte_order} ROM matches after conversion to z64 byte order')
    return version
