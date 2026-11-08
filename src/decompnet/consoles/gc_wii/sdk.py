"""Identify whole SDK functions with dtk's public masked patterns, independently of C matching.

Only a complete function with the same relocation layout establishes an identity.
Names reached through references are retained as leads, never promoted to identities.
The corpus contains binary patterns, not SDK source, prototypes or version attribution.
"""

import base64
import functools
import hashlib
import json
import threading
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path

import yaml
from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from ... import toolchains
from ...forks.project import ForkError, ForkProject

PACK = 'dtk-signatures'
CORPUS = 'decomp-toolkit v1.8.4 (GameCube/Wii)'
MIN_BYTES = 32
MAX_OBJECT_BYTES = 64 << 20
# dtk records instruction offsets; ELF's 16-bit relocations address the immediate halfword.
RELOCS = {'Absolute': (1, 0), 'PpcAddr16Lo': (4, 2), 'PpcAddr16Hi': (5, 2),
          'PpcAddr16Ha': (6, 2), 'PpcRel24': (10, 0), 'PpcRel14': (11, 0), 'PpcEmbSda21': (109, 0)}


@functools.lru_cache(maxsize=2)
def patterns(folder, revision):
    """Parse the checksum-pinned pack once; index by size and relocation layout before comparing bytes."""
    grouped = defaultdict(list)
    for path in sorted(folder.glob('*.yml')):
        rows = yaml.load(path.read_text(encoding='utf-8'), Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))
        for row in rows:
            symbol = row['symbols'][row['symbol']]
            size = symbol['size']
            if size < MIN_BYTES or size % 4:
                continue
            data = base64.b64decode(row['signature'], validate=True)
            if len(data) != size * 2 or hashlib.sha1(data).hexdigest() != row['hash']:
                raise ValueError(f"Invalid SDK pattern: {path.name}")
            words = [(int.from_bytes(data[i:i+4], 'big'), int.from_bytes(data[i+4:i+8], 'big'))
                     for i in range(0, len(data), 8)]
            # Very weak patterns cannot establish identity even when they cover a whole function.
            if sum(mask.bit_count() for _, mask in words) < MIN_BYTES * 8:
                continue
            relocs = row['relocations']
            if any(r['kind'] not in RELOCS for r in relocs):
                continue
            layout = tuple(sorted((r['offset'] + RELOCS[r['kind']][1], RELOCS[r['kind']][0]) for r in relocs))
            grouped[size, layout].append((symbol['name'], row['hash'], words, row))
    return dict(grouped)


def identify_object(data, corpus):
    """Return direct, unambiguous identities by ELF symbol, including advisory reference names."""
    elf = ELFFile(BytesIO(data))
    if elf['e_machine'] != 'EM_PPC' or elf.little_endian or elf['e_type'] != 'ET_REL':
        return {}
    table = elf.get_section_by_name('.symtab')
    if table is None:
        return {}
    by_section = defaultdict(list)
    for section in elf.iter_sections():
        if not isinstance(section, RelocationSection):
            continue
        symbols = elf.get_section(section['sh_link'])
        for reloc in section.iter_relocations():
            by_section[section['sh_info']].append((reloc['r_offset'], reloc['r_info_type'],
                                                  symbols.get_symbol(reloc['r_info_sym']).name,
                                                  reloc.entry.get('r_addend', 0)))
    sections, out = {}, {}
    for symbol in table.iter_symbols():
        section_index, start, size = symbol['st_shndx'], symbol['st_value'], symbol['st_size']
        if symbol['st_info']['type'] != 'STT_FUNC' or not isinstance(section_index, int) or size < MIN_BYTES:
            continue
        if section_index not in sections:
            section = elf.get_section(section_index)
            sections[section_index] = section.data() if section['sh_type'] != 'SHT_NOBITS' and section['sh_flags'] & 4 else b''
        code = sections[section_index][start:start+size]
        if len(code) != size or size % 4:
            continue
        relocs = sorted((offset-start, kind, name, addend) for offset, kind, name, addend in by_section[section_index]
                        if start <= offset < start+size)
        layout = tuple((offset, kind) for offset, kind, _, _ in relocs)
        hits = []
        for name, digest, words, row in corpus.get((size, layout), []):
            if any(int.from_bytes(code[i*4:i*4+4], 'big') & mask != word for i, (word, mask) in enumerate(words)):
                continue
            refs = {offset: (target, addend) for offset, _, target, addend in relocs}
            from ...app.function_names import generated
            contradicted = False
            references = []
            for reloc in row['relocations']:
                target, addend = refs[reloc['offset'] + RELOCS[reloc['kind']][1]]
                expected = row['symbols'][reloc['symbol']]['name']
                # Named referents constrain a pattern; a generated symbol remains only a lead.
                if (target and not generated(target) and not target.startswith(('.', '@'))
                        and not expected.startswith(('.', '@')) and target != expected):
                    contradicted = True
                if target and addend == reloc['addend']:
                    references.append({'target': target, 'name': expected, 'offset': reloc['offset']})
            if not contradicted:
                hits.append({'name': name, 'hash': digest, 'references': references})
        if hits and len({h['name'] for h in hits}) == 1:
            out[symbol.name] = hits
    return out


def scan(net, proj, job=None):
    """Serialize optional scans; concurrent naming jobs must not race their evidence caches."""
    from ...app.maintenance import checkpoint

    lock = net.__dict__.setdefault('_sdk_scan_lock', threading.Lock())
    while not lock.acquire(timeout=0.25):
        checkpoint()
        if job is not None and job.stop.is_set():
            return
    try:
        _scan(net, proj, job)
    finally:
        lock.release()


def _scan(net, proj, job=None):
    """Refresh evidence during SDK naming, without building, changing names or opening idle projects."""
    from ...app.maintenance import checkpoint
    from ...app.sdk_library import allowed
    from ...app.twins import object_prints

    if net.cfg(proj).get('kind') != 'fork' or net.cfg(proj).get('ready') is False or proj['slug'] in net.links:
        return
    rows = net.project_store(proj['id']).all('SELECT id,name,unit,size,meta FROM functions WHERE project_id=?', proj['id'])
    if not rows or not allowed(net, proj, CORPUS):
        return
    if job is not None and job.stop.is_set():
        return
    log = getattr(job, 'log', None)
    folder = toolchains.ensure(PACK, log=log if callable(log) else lambda *_: None)
    corpus = patterns(folder / 'signatures', toolchains.get(PACK).source)
    fp = ForkProject(Path(proj['root']))
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['unit']].append(row)
    cache = net.__dict__.setdefault('_sdk_object_checks', {})
    changed_targets = False
    pending, multiplicity, seen_targets = {}, Counter(), set()
    for unit_name, members in grouped.items():
        checkpoint()
        if job is not None and job.stop.is_set():
            return
        try:
            target = fp.unit(unit_name).target
            if target is None:
                continue
            target = target.resolve()
            relative = str(target.relative_to(fp.root.resolve()))
            stat = target.stat()
            if stat.st_size > MAX_OBJECT_BYTES:
                continue
            stamp = [stat.st_mtime_ns, stat.st_size]
            key = (str(target), toolchains.get(PACK).source)
            prior = cache.get(key)
            if prior and prior['stamp'] == stamp:
                result = prior
            else:
                data = target.read_bytes()
                result = {'stamp': stamp, 'sha256': hashlib.sha256(data).hexdigest(),
                          'hits': identify_object(data, corpus), 'prints': object_prints(data)}
                if len(cache) >= 4096:
                    cache.pop(next(iter(cache)))
                cache[key] = result
                changed_targets = True
        except (OSError, ValueError, ELFError, ForkError):
            continue  # An unavailable or unsupported target never supplies an SDK identity.
        if str(target) not in seen_targets:
            seen_targets.add(str(target))
            multiplicity.update(p.key for p in result['prints'].values())
        for row in members:
            meta = json.loads(row['meta'] or '{}')
            symbol = meta.get('symbol') or row['name'].split('@')[0]
            hits, target_print = result['hits'].get(symbol), result['prints'].get(symbol)
            identity = None
            if hits and target_print and row['size'] == target_print.size:
                identity = {'names': [hits[0]['name']], 'corpora': [CORPUS], 'fingerprint': target_print.key,
                            'size': target_print.size, 'target_path': relative, 'target_stamp': stamp,
                            'evidence': 'Unique whole-function relocation-masked SDK binary signature; SDK release unknown',
                            'binary_evidence': [h | {'source': toolchains.get(PACK).source, 'unit': unit_name,
                                                    'symbol': symbol, 'target_sha256': result['sha256'],
                                                    'method': 'direct'} for h in hits]}
            pending[row['id']] = (row, identity)
    # A generic wrapper can look exactly like a library function after its calls are masked.
    # Corpus uniqueness alone is insufficient: repeated target bodies are ambiguous too.
    for fid, (row, identity) in pending.items():
        checkpoint()
        if job is not None and job.stop.is_set():
            break
        if identity and multiplicity[identity['fingerprint']] != 1:
            identity = None
        with net.project_store(proj['id']).tx() as db:
            latest = db.execute('SELECT meta,size,unit FROM functions WHERE id=?', (fid,)).fetchone()
            if latest is None or latest['unit'] != row['unit'] or latest['size'] != row['size']:
                continue
            meta = json.loads(latest['meta'] or '{}')
            if meta.get('symbol') != json.loads(row['meta'] or '{}').get('symbol'):
                continue  # A concurrent rename or layout refresh owns the new symbol.
            if identity != meta.get('sdk_identity'):
                if identity:
                    meta['sdk_identity'] = identity
                else:
                    meta.pop('sdk_identity', None)
                db.execute('UPDATE functions SET meta=? WHERE id=?', (json.dumps(meta), fid))
    if changed_targets:
        net.twins.forget(proj)
