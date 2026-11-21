"""Read argument setup from an N64Recomp wrapper for a known ROM revision."""
import re

import httpx

from ..forks.csource import _blank
from ..network import NetworkError

PERMALINK = re.compile(r'https://github\.com/([\w.-]+)/([\w.-]+)/blob/([0-9a-f]{40})/'
                       r'([\w./-]+)(?:#L\d+(?:-L\d+)?)?')
MAX_SOURCE_BYTES = 32_000
REGISTER = re.compile(r'\b(\w+)->r([4-7])\s*=(?!=)\s*([^;]+);')
STACK = re.compile(r'\b\w*MEM_W\s*\(\s*(0x[\da-fA-F]+|\d+)\s*,\s*(\w+)\s*\)\s*=(?!=)\s*([^;]+);')
ADDRESS = re.compile(r'\b(?:const\s+)?(?:u?int32_t|auto)\s+(\w+)\s*=\s*(\w+)\s*\+\s*(?:0x[\da-fA-F]+|\d+)U?\s*;')
IDENT = re.compile(r'\b[A-Za-z_]\w*\b')
SIGNED_BYTE = re.compile(r'\bsll\s+(\$\w+),\s*(\$\w+),\s*(?:0x18|24)\s*\n'
                         r'\s*sra\s+\1,\s*\1,\s*(?:0x18|24)\b')
CASTS = {'static_cast', 'uint32_t', 'int32_t', 'gpr'}


def fetch_source(url):
    try:
        with httpx.stream('GET', url, timeout=5, follow_redirects=False) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                body.extend(chunk)
                if len(body) > MAX_SOURCE_BYTES:
                    raise NetworkError('The reference file exceeds 32 KB. Paste the individual wrapper instead.')
        return body.decode('utf-8')
    except (httpx.HTTPError, UnicodeError) as error:
        raise NetworkError('Could not read the reference file. Check the permalink or paste the wrapper source.') from error


def analyze(source, url, expected_sha256, image_sha256, name, address, asm):
    link = PERMALINK.fullmatch(url)
    if not link or not re.fullmatch('[0-9a-fA-F]{64}', expected_sha256):
        raise NetworkError('Provide a GitHub commit permalink and the ROM SHA-256 required by the reference.')
    if expected_sha256.lower() != image_sha256:
        raise NetworkError('The reference ROM SHA-256 differs from this project.')
    owner, repo, commit, path = link.groups()
    if any(part in {'', '.', '..'} for part in (owner, repo, *path.split('/'))):
        raise NetworkError('Use a GitHub permalink to one source file.')
    supplied = bool(source)
    if not supplied:
        source = fetch_source(f'https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{path}')
    if not source.strip() or len(source.encode()) > MAX_SOURCE_BYTES:
        raise NetworkError('Provide one reference wrapper source file of at most 32 KB.')
    if not IDENT.fullmatch(name):
        raise NetworkError('This wrapper needs a target function with a C identifier.')
    code = _blank(source)
    calls = list(re.finditer(rf'\bfunc_{address:08x}\s*\(\s*\w+\s*,\s*(\w+)\s*\)\s*;', code, re.I))
    if len(calls) != 1:
        raise NetworkError(f'Paste a wrapper with one call to func_{address:08X}.')
    call = calls[0]
    ctx = call[1]
    blocks = []
    for brace in re.finditer('[{}]', code[:call.start()]):
        if brace[0] == '{':
            blocks.append(brace.end())
        elif blocks:
            blocks.pop()
    setup = code[blocks[-1] if blocks else 0:call.start()]
    depth = 0
    after = code[call.end():]
    for brace in re.finditer('[{}]', after):
        depth += 1 if brace[0] == '{' else -1
        if depth < 0:
            after = after[:brace.start()]
            break
    stacks = {base for base in re.findall(rf'\b{ctx}->r29\s*=[^;]*?(?<!\w)(\w+)\s*\)*\s*;', setup)}
    if len(stacks) != 1:
        raise NetworkError('The wrapper must assign its emulated stack pointer before the call.')
    stack = stacks.pop()
    pointers = {symbol for symbol, base in ADDRESS.findall(setup) if base == stack}
    args = {int(reg)-4: value for owner, reg, value in REGISTER.findall(setup) if owner == ctx}
    args.update({int(offset, 0)//4: value for offset, base, value in STACK.findall(setup)
                 if base == stack and 16 <= int(offset, 0) < 64 and int(offset, 0) % 4 == 0})
    if not args or sorted(args) != list(range(len(args))):
        raise NetworkError('The wrapper must show a contiguous argument setup in registers and stack slots.')
    asm = _blank(asm)
    frame = re.search(r'\baddiu\s+\$sp,\s*\$sp,\s*-(0x[\da-fA-F]+|\d+)', asm)
    incoming = set()
    signed = set()
    if frame:
        size = int(frame[1], 0)
        sign_inputs = {register for _, register in SIGNED_BYTE.findall(asm)}
        for register, offset in re.findall(r'\blw\s+(\$\w+),\s*(0x[\da-fA-F]+|\d+)\(\$sp\)', asm):
            slot = int(offset, 0) - size
            if slot >= 16 and slot % 4 == 0:
                incoming.add(slot//4)
                if register in sign_inputs:
                    signed.add(slot//4)
    if incoming and max(incoming) >= len(args):
        raise NetworkError('The original function reads a stack argument missing from the wrapper.')
    hint = []
    params = []
    for index, value in sorted(args.items()):
        words = [word for word in IDENT.findall(value) if word not in CASTS]
        symbol = words[0] if words else f'arg{index}'
        location = f'a{index}' if index < 4 else f'sp+0x{index*4:X}'
        description = f'{location}={symbol}'
        if index in incoming:
            description += ' (stack load confirmed)'
        hint.append(description)
        # Only classify pointers whose stack-buffer address is explicit.
        ctype = 'void *' if symbol in pointers else 'signed char' if index in signed else 'unsigned int'
        params.append(f'{ctype} arg{index}')
    prototype = f'int {name}({", ".join(params)});'
    result_used = bool(re.search(rf'\b{ctx}->r2\s*(?:==|!=)', after))
    if result_used:
        hint.append('caller tests v0 as a status')
    provenance = 'Source supplied by caller' if supplied else 'Source fetched from commit'
    evidence = ('Reference wrapper evidence (caller hypotheses; original stack loads marked): '
                + '; '.join(hint) + f'. Draft prototype: {prototype} ROM SHA-256 matched ({image_sha256}). '
                + f'{provenance}: {url}')
    return prototype, evidence
