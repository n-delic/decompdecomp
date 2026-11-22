"""Jump-table ordering: the rewrites are `decomprepair.switch_tables`; reading a ROM's tables needs the project, so
`read` stays here."""
import re

from decomprepair.switch_tables import *  # noqa: F403 - the rewrites (variants, hints, value)


def _binary_tables(net, proj, fn):
    """Read validated dispatches even when the address was hoisted out of the switch."""
    from ..arch.mips_indirect import jump_table

    code = dict(fn).get('target')
    if proj['arch'] != 'mips' or not getattr(net, 'is_rom', lambda p: False)(proj) or not code:
        return []
    start = fn['vram']
    order = 'big' if proj['big_endian'] else 'little'
    read_rom = None
    tables = []
    for offset in range(24, len(code)-7, 4):
        word = int.from_bytes(code[offset:offset+4], order)
        if word & 0xfc1fffff != 8 or (word >> 21) & 31 == 31:
            continue
        if read_rom is None:
            read_rom = net.reader(proj, fn)
        table = jump_table(code, 0, len(code), start, offset, order,
                           reader=read_rom, allow_saved_base=True)
        if not table or table['state'] != 'validated':
            continue
        at = table['guard_address'] - start
        guard = int.from_bytes(code[at:at+4], order)
        if guard >> 26 != 11:
            continue
        index = (guard >> 21) & 31
        low = 0
        if at >= 4:
            previous = int.from_bytes(code[at-4:at], order)
            if previous >> 26 == 9 and (previous >> 16) & 31 == index and (previous >> 21) & 31:
                low = -(int.from_bytes((previous & 0xffff).to_bytes(2, 'big'), 'big', signed=True))
        tables.append((table['table_address'], low, table['targets']))
    return tables


def tables(net, proj, fn, *, diagnostic=False):
    binary = _binary_tables(net, proj, fn)
    addresses = {address for address, _, _ in binary}
    return binary + [item for item in _read(net, proj, fn, diagnostic=diagnostic)
                     if item[0] not in addresses]


def read(net, proj, fn):
    return [(low, targets) for _, low, targets in tables(net, proj, fn)]


def relocation_profiles(net, proj, fn, profile, problems):
    """Try KMC scheduling changes when a compiled table has no aligned ROM relocation."""
    if (dict(proj).get('arch') != 'mips' or profile['toolchain'] != 'gcc-2.7.2-kmc'
            or not re.search(r'\.rodata\+0x[0-9A-F]+ where the ROM.s code has no address', problems or '')
            or not read(net, proj, fn)):
        return []
    current = profile['flags']
    return [[flag for flag in current if flag != enable] + [disable]
            for enable, disable in (('-fschedule-insns', '-fno-schedule-insns'),
                                    ('-fschedule-insns2', '-fno-schedule-insns2'),
                                    ('-fdelayed-branch', '-fno-delayed-branch'))
            if disable not in current]


def _read(net, proj, fn, *, diagnostic=False):
    """MIPS assembly records the lower case bound and table address; other ISAs keep generic repairs."""
    if proj['arch']!='mips' or not getattr(net,'is_rom',lambda p:False)(proj):
        return []
    asm=fn['asm'] or ''; result=[]
    for hit in re.finditer(r'%hi\(((?:jtbl_|D_)[A-Za-z0-9_]+)\)',asm):
        if diagnostic:
            bounds = _dispatch(asm, hit)
            if bounds is None:
                continue
            low, count = bounds
        else:
            prefix=asm[max(0,hit.start()-700):hit.start()]
            length=list(re.finditer(r'sltiu\s+\$\w+,\s*(\$\w+),\s*(0x[0-9A-Fa-f]+|\d+)',prefix))
            if not length:continue
            tested=length[-1][1]
            bound=list(re.finditer(r'addiu\s+'+re.escape(tested)+r',\s*\$(?!sp\b)\w+,\s*(-?(?:0x[0-9A-Fa-f]+|\d+))',prefix[:length[-1].start()]))
            low=-int(bound[-1][1],0) if bound else 0
            count=int(length[-1][2],0)
        address=net.symbols(proj['id']).resolve(hit[1])
        if address is None or not 2<=count<=256:
            continue
        data=net.reader(proj,fn)(address,count*4)
        if data is None:
            # Some basic imports describe code ranges only. A single consistent linear image
            # mapping can propose a nearby data table; ambiguous/overlay mappings are excluded.
            # Destination bounds below and the ordinary exact judge remain mandatory.
            cfg=net.cfg(proj);segments=cfg.get('segments') or []
            biases={s['vram']-s['rom_start'] for s in segments}
            last=max((s['rom_end'] for s in segments),default=0)
            if len(biases)==1 and last:
                offset=address-next(iter(biases))
                if last<=offset and offset+count*4<=last+65536:
                    image,_=net._rom(proj)
                    if offset+count*4<=len(image):data=image[offset:offset+count*4]
        if data is not None and len(data)==count*4:
            order='big' if proj['big_endian'] else 'little'
            destinations=[int.from_bytes(data[i:i+4],order) for i in range(0,len(data),4)]
            if all(fn['vram']<=n<fn['vram']+fn['size'] for n in destinations):
                result.append((address,low,destinations))
    return result


def _dispatch(asm, hit):
    """Tie the guard, scale, table load, and indirect jump to the same registers."""
    from ..arch.mips_returns import instructions

    prefix = instructions(asm[max(0,hit.start()-700):hit.start()])
    tail = instructions(asm[asm.rfind('\n',0,hit.start())+1:hit.end()+450])
    # The final partial prefix line is the LUI itself.
    before = prefix[:-1]
    if len(before) < 3 or len(tail) < 4:
        return None
    # Some profiles put a NOP in the branch delay slot, then scale.
    if len(before) >= 4 and before[-2] == 'nop':
        before = before[:-2] + before[-1:]
    guard, branch, shift = before[-3:]
    bound = re.fullmatch(r'sltiu\s+(\$\w+),\s*(\$\w+),\s*(0x[0-9a-fA-F]+|\d+)', guard)
    if not bound or not re.fullmatch(r'(?:beqz\s+'+re.escape(bound[1])+r'|beq\s+'+re.escape(bound[1])+r',\s*\$zero),\s*\S+', branch):
        return None
    scaled = re.fullmatch(r'sll\s+(\$\w+),\s*'+re.escape(bound[2])+r',\s*(?:2|0x2)', shift)
    table = re.escape(hit[1])
    base = re.fullmatch(r'lui\s+(\$\w+),\s*%hi\('+table+r'\)', tail[0])
    load = re.fullmatch(r'lw\s+(\$\w+),\s*%lo\('+table+r'\)\((\$\w+)\)',tail[2])
    adds = re.fullmatch(r'addu\s+(\$\w+),\s*(\$\w+),\s*(\$\w+)',tail[1])
    if not (scaled and base and adds and load and
            adds[1] == load[2] and {adds[2],adds[3]} == {base[1],scaled[1]} and
            re.fullmatch(r'jr\s+'+re.escape(load[1]),tail[3])):
        return None
    adjust = re.fullmatch(r'addiu\s+'+re.escape(bound[2])+r',\s*\$(?!sp\b)\w+,\s*(-?(?:0x[0-9a-fA-F]+|\d+))',
                          before[-4]) if len(before) >= 4 else None
    return (-int(adjust[1],0) if adjust else 0, int(bound[3],0))


def differences(net, proj, fn, result):
    """Resolve case numbers only when linked code and both table layouts agree."""
    if not result.instructions_exact or not result.data_mismatches:
        return []
    target_tables = tables(net, proj, fn, diagnostic=True)
    order = 'big' if proj['big_endian'] else 'little'
    rows = []
    for mismatch in result.data_mismatches:
        options = {(low, tuple(targets)) for address, low, targets in target_tables if address == mismatch.address}
        if len(options) != 1:
            continue
        low, targets = options.pop()
        size = 4 * len(targets)
        offsets = set(range(0, size, 4))
        if (len(mismatch.target) < size or offsets != mismatch.code_pointer_offsets
                or mismatch.target[size:] != mismatch.candidate[size:]
                or mismatch.target[:size] != b''.join(value.to_bytes(4, order) for value in targets)):
            continue
        for index, target in enumerate(targets):
            candidate = int.from_bytes(mismatch.candidate[4*index:4*(index+1)], order)
            if candidate != target:
                rows.append({'table':mismatch.address, 'case':low+index, 'target':target, 'candidate':candidate})
    return rows


def difference_text(rows):
    if not rows:
        return ''
    lines = ['Linked jump-table differences (case | target destination | compiled destination)']
    lines.extend(f"{r['case']} | 0x{r['target']:08X} | 0x{r['candidate']:08X} (table 0x{r['table']:08X})"
                 for r in rows[:12])
    if len(rows) > 12:
        lines.append(f'{len(rows)-12} more differing entries in the function comparison.')
    return '\n'.join(lines)
