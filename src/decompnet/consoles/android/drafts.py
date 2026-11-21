"""Android's Ghidra drafts, with the current shared header and compiler-compatible C (#133).

Cache by original bytes, boundaries, name, header, and provider version. Neither a failed decompilation nor an
unparseable header is cached as a successful empty draft. The same provider serves briefs, redraft and autopilot.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from ... import seeds
from ...forks import ghidra, ghidra_worker
from ...network import NetworkError

VERSION = 5
_CACHE_LOCK = threading.Lock()
_TYPES = {'bool': '_Bool', 'undefined': 'unsigned char', 'undefined1': 'unsigned char', 'undefined2': 'unsigned short',
          'undefined3': 'unsigned int', 'undefined4': 'unsigned int', 'undefined8': 'unsigned long long',
          'byte': 'unsigned char', 'uchar': 'unsigned char', 'ushort': 'unsigned short', 'uint': 'unsigned int',
          'uint3': 'unsigned int', 'ulong': 'unsigned long', 'word': 'unsigned short', 'dword': 'unsigned int',
          'qword': 'unsigned long long', 'longlong': 'long long', 'ulonglong': 'unsigned long long',
          'float10': 'long double', 'longdouble': 'long double'}
_AUTO = re.compile(r'\b_?(?:DAT|PTR|FUN|LAB|UNK)_([0-9a-fA-F]{8})(?:_\d+)?\b')
_RAM = re.compile(r'\b(?:[a-z]+Ram|PTR_[A-Za-z_0-9]*_)([0-9a-fA-F]{8})\b')
_CALL = re.compile(r'\b([A-Za-z_]\w*)\s*\(')
_PARTIAL = re.compile(r'\b([A-Za-z_]\w*)\._(\d+)_(1|2|4|8)_')
_UNWRITABLE = re.compile(r'\b(?:unaff_\w+|extraout_\w+|in_stack_\w+|in_r(?:[4-9]|1[0-5])|halt_baddata|swi)\b')


def ready():
    return ghidra.available()


def header_for_parser(header):
    return '\n'.join(line for line in seeds.c_only(header).splitlines() if not line.lstrip().startswith('#'))


def compilable(raw, name, header, symbols, imports=(), image=None):
    """Supply only the types/externs a draft needs; keep declarations from the shared header authoritative."""
    raw = raw.replace('__thiscall ', '').replace('__cdecl ', '').replace('__stdcall ', '')
    def automatic(match):
        address = int(match.group(1), 16)
        return f'func_{address:08X}' if match.group(0).lstrip('_').startswith('FUN_') else f'D_{address:08X}'
    raw = re.sub(r"\bfunc_0x([0-9a-fA-F]{8})\b", lambda m: f"func_{int(m[1], 16):08X}", raw)
    raw = _AUTO.sub(automatic, raw)
    def memory_name(match):
        address = int(match[1], 16)
        if image is not None and address in image.slots:
            original, value = image.slots[address]
            symbol = None
            if value is not None:
                symbol = symbols.by_addr.get(value & ~1)
            label = symbol.name if symbol is not None else original if re.fullmatch(r'[A-Za-z_]\w*', original) else None
            if label:
                return f'(&{label})'
        return f'D_{address:08X}'
    raw = _RAM.sub(memory_name, raw)
    raw = _PARTIAL.sub(lambda m: f'(*((unsigned {_integer(int(m[3]))} *)((unsigned char *)&{m[1]} + {m[2]})))', raw)
    declared = set().union(*(d.names for d in seeds.declarations(header)))
    for token in set(re.findall(r'\b_[A-Za-z_]\w*\b', raw)) - declared:
        if token not in symbols.by_name and token[1:] in symbols.by_name:
            raw = re.sub(rf'\b{re.escape(token)}\b', token[1:], raw)
    tokens = set(re.findall(r'\b[A-Za-z_]\w*\b', raw))
    prelude = [f'typedef {ctype} {token};' for token, ctype in _TYPES.items() if token in tokens and token not in declared]
    if 'code' in tokens and 'code' not in declared:
        prelude.append('typedef int code();')
    for token, value in (("true", "1"), ("false", "0")):
        if token in tokens and token not in declared:
            prelude.append(f'#define {token} {value}')
    calls = set(_CALL.findall(raw))
    for token in sorted(tokens - declared - {name}):
        symbol = symbols.by_name.get(token)
        if symbol is not None or token in imports or re.fullmatch(r'(?:D|func)_[0-9A-F]{8}', token):
            if token in calls or symbol is not None and symbol.kind == 'func' or image is not None and image.types.get(token) == 'STT_FUNC':
                prelude.append(f'extern int {token}();')
            else:
                prelude.append(f'extern unsigned int {token}[];' if re.search(rf'\b{re.escape(token)}\s*\[', raw) else f'extern unsigned int {token};')
    for token in tokens:
        if (m := re.fullmatch(r'CONCAT([1-8])([1-8])', token)) and int(m[1]) + int(m[2]) <= 8:
            left, right = int(m[1]), int(m[2])
            prelude.append(f'#define {token}(a,b) ((unsigned {_integer(left + right)})((((unsigned long long)(a) & {(1 << (left * 8)) - 1}ULL) << {right * 8}) | ((unsigned long long)(b) & {(1 << (right * 8)) - 1}ULL)))')
        elif (m := re.fullmatch(r'SUB([1-8])([1-8])', token)):
            prelude.append(f'#define {token}(x,n) ((unsigned {_integer(int(m[2]))} )((unsigned long long)(x) >> ((n)*8)))')
        elif (m := re.fullmatch(r'(Z|S)EXT([1-8])([1-8])', token)):
            sign = 'unsigned' if m[1] == 'Z' else 'signed'
            prelude.append(f'#define {token}(x) (({sign} {_integer(int(m[3]))} )(({sign} {_integer(int(m[2]))} )(x)))')
        elif (m := re.fullmatch(r'(CARRY|SCARRY|SBORROW)(1|2|4|8)', token)):
            bits, cast = int(m[2]) * 8, f'unsigned {_integer(int(m[2]))}'
            a, b = f'(({cast})(a))', f'(({cast})(b))'
            if m[1] == 'CARRY':
                value = f'(({cast})({a}+{b}) < {a})'
            elif m[1] == 'SCARRY':
                value = f'((~({a}^{b}) & ({a}^({a}+{b}))) >> {bits - 1}) & 1'
            else:
                value = f'((({a}^{b}) & ({a}^({a}-{b}))) >> {bits - 1}) & 1'
            prelude.append(f'#define {token}(a,b) ({value})')
        elif token == 'POPCOUNT':
            prelude.append('#define POPCOUNT(x) __builtin_popcountll((unsigned long long)(x))')
    return '\n'.join(prelude) + '\n' + raw.strip() + '\n'


def _integer(size):
    return 'char' if size == 1 else 'short' if size == 2 else 'int' if size <= 4 else 'long long'


def generate(net, proj, fn, header, valid=False):
    if not ready():
        raise NetworkError('Android machine drafts need Ghidra and Java 21+: install Ghidra in Setup → Compilers & tools.')
    cfg = net.cfg(proj)
    root = Path(proj['root'])
    program = root / cfg['rom']['program']
    syms = net.symbols(proj['id'])
    bindings = [{'name': token, 'address': syms.by_name[token].address}
                for token in set(re.findall(r'\b[A-Za-z_]\w*\b', header)) | {fn['name']} if token in syms.by_name]
    request = {'address': fn['vram'], 'size': fn['size'], 'name': fn['name'],
               'thumb': json.loads(fn['meta'] or '{}').get('thumb', True),
               'header': header_for_parser(header), 'symbols': sorted(bindings, key=lambda b: b['name'])}
    key = hashlib.sha256(json.dumps([VERSION, cfg['rom']['sha1'], request], sort_keys=True).encode()).hexdigest()
    folder = root / '.decomp-wtf' / 'drafts'
    cache = folder / f'{key}.json'
    with _CACHE_LOCK:
        try:
            raw = json.loads(cache.read_text(encoding='utf-8'))['c']
            if not isinstance(raw, str):
                raw = None
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            raw = None
    if raw is None:
        state = net.autopilots.get(proj['slug'], {})
        stop = state.get('stop') if state.get('state') == 'running' else None
        raw = ghidra_worker.request(program, request, cancel=stop.is_set if stop is not None else None,
                                    generation=cfg['rom']['sha1'])
        if not raw.strip():
            raise NetworkError('Ghidra returned an empty draft. Retry or check this function’s boundary.')
        folder.mkdir(parents=True, exist_ok=True)
        with _CACHE_LOCK:
            temporary = cache.with_suffix('.tmp')
            temporary.write_text(json.dumps({'c': raw}), encoding='utf-8')
            temporary.replace(cache)
    if not valid:
        return raw
    if _UNWRITABLE.search(raw):
        return None  # readable to agents, but an unknown input register isn't sound compilable C
    from ...arch.arm_image import image
    context = image(net._rom(proj)[0])
    return compilable(raw, fn['name'], header, syms, context.imports, context)
