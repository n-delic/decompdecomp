"""Rank profile hypotheses from target evidence, never from a familiar function name alone."""
import json
import re
from collections import Counter

from ..arch.mips_runtime import helper


def kmc_variants(toolchain, flags, *, single_pass=False):
    """Bounded KMC trials that change scheduling or induction-variable reduction."""
    if toolchain != 'gcc-2.7.2-kmc':
        return []
    scheduling = {'-fschedule-insns', '-fschedule-insns2',
                  '-fno-schedule-insns', '-fno-schedule-insns2'}
    base = [flag for flag in flags if flag not in scheduling]
    variants = []
    if '-O1' in base:
        variants.append([*base, '-fschedule-insns', '-fschedule-insns2'])
        if single_pass:
            variants.append([*base, '-fschedule-insns2'])
    if base != flags:
        variants.append(base)
    if '-fno-strength-reduce' in flags:
        variants.append([flag for flag in flags if flag != '-fno-strength-reduce'])
    return [variant for variant in variants if variant != flags]


def kmc_cse_address_probe(own, rows):
    """Try disabling CSE across skipped blocks when the target reloads one address repeatedly."""
    flags = list(own['flags'])
    option = '-fno-cse-skip-blocks'
    if (own['toolchain'] != 'gcc-2.7.2-kmc' or '-O2' not in flags
            or option in flags or '-fcse-skip-blocks' in flags):
        return None
    high = re.compile(r'^lui\s+\$\w+,\s*%hi\(([^()]+)\)$')
    low = re.compile(r'^addiu\s+\$\w+,\s*\$\w+,\s*%lo\(([^()]+)\)$')
    counts = []
    for side in (1, 2):
        halves = [Counter(match[1] for row in rows if (match := pattern.fullmatch(row[side].strip())))
                  for pattern in (high, low)]
        counts.append(halves[0] & halves[1])
    # A relocated store can use the high register directly, without an addiu.
    high_register = re.compile(r'lui\s+(\$\w+),\s*%hi\(([^()]+)\)')
    store = re.compile(r's(?:b|h|w)\s+\$\w+,\s*%lo\(([^()]+)\)\((\$\w+)\)')
    store_counts = []
    for side in (1, 2):
        instructions = [row[side].strip() for row in rows if row[side].strip()]
        pairs = Counter()
        for before, after in zip(instructions, instructions[1:]):
            setup = high_register.fullmatch(before)
            write = store.fullmatch(after)
            if setup and write and setup.groups() == (write[2], write[1]):
                pairs[write[1]] += 1
        store_counts.append(pairs)
    for target, candidate in (counts, store_counts):
        if any(candidate[symbol] > 0 and count - candidate[symbol] >= 2 for symbol, count in target.items()):
            return [*flags, option]
    return None


def kmc_cse_hoist_probe(own, rows):
    """Try both of cse's block options off when the candidate keeps an address in a saved register that the target
    builds again at each use.

    At -O2 cse follows a branch and skips a short block, so the address of a global passed to two calls in one arm is
    kept in `$sN` and copied into the argument register (`move $a0,$s2` twice) where the target has `lui/addiu $a0`
    before each call. An N64 dispatcher matched at 0 with both options off and plain C; either one alone left it
    unchanged (605), so the single-option probe above never found it (nor does it see one extra pair)."""
    flags = list(own['flags'])
    options = ['-fno-cse-follow-jumps', '-fno-cse-skip-blocks']
    if (own['toolchain'] != 'gcc-2.7.2-kmc' or '-O2' not in flags
            or any(flag in flags for flag in (*options, '-fcse-follow-jumps', '-fcse-skip-blocks'))):
        return None
    low = re.compile(r'addiu\s+(\$\w+),\s*(?:\$\w+),\s*%lo\(([^()]+)\)')
    copy = re.compile(r'(?:move|addu)\s+\$a[0-3],\s*(\$s[0-7])(?:,\s*\$zero)?')
    target, candidate = Counter(), Counter()
    kept = {}
    for _, left, right in rows:
        if found := low.fullmatch(left.strip()):
            target[found[2]] += 1
        if found := low.fullmatch(right.strip()):
            candidate[found[2]] += 1
            if found[1].startswith('$s'):
                kept[found[1]] = found[2]
    copies = Counter(found[1] for _, _, right in rows if (found := copy.fullmatch(right.strip())))
    if any(copies[register] >= 2 and target[symbol] >= 2 and target[symbol] > candidate[symbol]
           for register, symbol in kept.items()):
        return [*flags, *options]
    return None


def kmc_cse_skip_probe(own, rows):
    """Try dropping `-fno-cse-skip-blocks` when the candidate reloads a field the target loads once.

    cse's "skip blocks" lets a value computed before a short conditional block (a `MIN`/`MAX` clamp written as `?:`, a
    `x ? x : 1`) be reused after it. With the option off every such block ends cse's extended basic block, so a macro that
    reads `p->w` before and after a clamp reads it twice (a 653-instruction N64 sprite drawer: the SDK's `gDPLoadBlock`
    arguments, 750 -> 430 with the option on, every other row unchanged). The mirror of kmc_cse_address_probe: that one
    adds the option when the target reloads addresses, this one removes it when the candidate reads memory again that an
    earlier row of its own already loaded and the target has no such row."""
    flags = list(own['flags'])
    option = '-fno-cse-skip-blocks'
    if own['toolchain'] != 'gcc-2.7.2-kmc' or option not in flags:
        return None
    load = re.compile(r'^(l(?:b|bu|h|hu|w))\s+\$\w+,\s*(-?(?:0x)?[0-9A-Fa-f]+)\(\$\w+\)$')
    earlier = set()
    again = set()
    for marker, _, right in rows:
        found = load.fullmatch(right.strip())
        if not found:
            continue
        key = found.groups()
        if marker == '>':
            if key in earlier:
                again.add(key)
        else:
            earlier.add(key)
    if not again:
        return None
    return [flag for flag in flags if flag != option]


def kmc_expensive_probe(own, rows):
    """Try `-fno-expensive-optimizations` when the target keeps a zero-extension (`andi rd,rs,0xffff` or `0xff`) the
    candidate lacks: combine.c drops a `(u16)` or `& 0xff` of a halfword/byte-loaded local at -O2, and kept it with the
    flag off in a KMC GCC 2.7.2 record scan (100 -> 0 with the natural source)."""
    flags = list(own['flags'])
    option = '-fno-expensive-optimizations'
    if (own['toolchain'] != 'gcc-2.7.2-kmc' or '-O2' not in flags
            or option in flags or '-fexpensive-optimizations' in flags):
        return None
    zero_extend = re.compile(r'^andi\s+\$\w+,\s*\$\w+,\s*0x(?:ff|ffff)$')
    for marker, target, candidate in rows:
        if marker == '<' and zero_extend.match(target.strip()) and not candidate.strip():
            return [*flags, option]
    return None


def kmc_priorities(own, assembly, rows):
    from .allocation_evidence import diagnose

    flags = list(own['flags'])
    diagnosis = diagnose(rows)
    changed = [right for marker, _, right in rows if marker != ' ']
    prologue = any(marker not in {' ', 'r'} and '$sp' in (left + right)
                   for marker, left, right in rows[:12])
    variants = []
    if cse := kmc_cse_address_probe(own, rows):
        variants.append(cse)
    if hoist := kmc_cse_hoist_probe(own, rows):
        variants.append(hoist)
    if skip := kmc_cse_skip_probe(own, rows):
        variants.append(skip)
    if expensive := kmc_expensive_probe(own, rows):
        variants.append(expensive)
    if diagnosis['delay_slot'] and any(right.strip() == 'nop' for right in changed):
        variants.append([flag for flag in flags if not re.fullmatch(r'-g[0-3]?', flag)])
    scheduling = kmc_variants(own['toolchain'], flags, single_pass=prologue)
    if prologue and not diagnosis['delay_slot']:
        scheduling.sort(key=lambda variant: '-fschedule-insns2' not in variant or '-fschedule-insns' in variant)
    for variant in scheduling:
        restores_induction = '-fno-strength-reduce' in flags and '-fno-strength-reduce' not in variant
        if restores_induction:
            if re.search(r'\bmultu?\b', ' '.join(changed)) and re.search(r'\baddiu?\b', assembly):
                variants.insert(0, variant)
        elif prologue or diagnosis['scheduling'] or diagnosis['delay_slot']:
            variants.append(variant)
    return list(dict.fromkeys(tuple(variant) for variant in variants if variant != flags))


def float_sqrt_source(own, assembly, source, name, header=''):
    """The single-precision target can diagnose a draft's wrong sqrt spelling."""
    if (own.get('toolchain') != 'gcc-2.7.2-kmc' or not re.search(r'\bsqrt\.s\b', assembly)
            or re.search(r'\bjalr?\b.*\bsqrtf?\b', assembly)):
        return None
    from decomprepair.near_match_repairs import square_rootf
    return square_rootf(source, name, header)


def kmc_sqrt_probe(own, assembly, rows):
    """Test the missing GCC square-root fallback only when the target uses hardware sqrt."""
    if (own['toolchain'] != 'gcc-2.7.2-kmc' or '-ffast-math' in own['flags']
            or not re.search(r'\bsqrt\.[sd]\b', assembly)
            or re.search(r'\bjalr?\b.*\bsqrtf?\b', assembly)):
        return None
    extra = [right for marker, _, right in rows if marker != ' ']
    if (any(re.match(r'c\.eq\.[sd]\b', op.strip()) for op in extra)
            and any(re.match(r'jal\b.*\bsqrtf?\b', op.strip()) for op in extra)):
        return [*own['flags'], '-ffast-math']
    return None


def prioritized(proj, fn, own):
    if dict(proj).get('arch')!='mips' or not own.get('toolchain','').startswith('ido-'):
        return []
    flags=list(own['flags'])
    out=[]
    if helper(dict(fn).get('asm','')):
        # Target O32 return/home-slot evidence is required before changing ISA/ABI hypotheses.
        kept=[];i=0
        while i<len(flags):
            flag=flags[i]
            if flag=='-mabi':
                i+=2;continue
            if not (flag.startswith(('-O','-mips','-mabi=','-g')) or flag in {'-32','-n32','-64','-Wab,-r4300_mul'}):
                kept.append(flag)
            i+=1
        out.append(['-O1','-mips3','-32',*kept])
    meta=json.loads(dict(fn).get('meta') or '{}')
    naming=meta.get('naming') or {}
    from decomprepair.sdk_io_repairs import io_target
    if naming.get('method')=='sdk_signature' or io_target(dict(fn).get('asm','')):
        # Prioritize -O1 only for an identity recorded from SDK signature evidence.
        candidate=[('-O1' if flag.startswith('-O') else flag) for flag in flags]
        if not any(flag.startswith('-O') for flag in flags):candidate.append('-O1')
        out.append(candidate)
    return [f for f in out if f!=flags]


def neighbours(net, proj, fn, own):
    """Nearby exact matches propose flags, not an inferred compiler range or a setting change."""
    from ..project import compiler_for
    rows=net.project_store(proj['id']).all("SELECT name,vram,rom_offset FROM functions WHERE project_id=? AND status='matched' AND best_score=0 "
                       "AND vram BETWEEN ? AND ? ORDER BY ABS(vram-?) LIMIT 8",
                       proj['id'],fn['vram']-16384,fn['vram']+16384,fn['vram'])
    cfg=net.cfg(proj);ranked=[]
    for row in rows:
        profile=compiler_for(cfg,row['vram'], row)
        flags=list(profile.get('flags',[]))
        if profile.get('toolchain')==own.get('toolchain') and not profile.get('cmd') and flags!=list(own['flags']) and flags not in ranked:
            ranked.append(flags)
    return ranked[:2]


def captured(net, proj, own):
    """Prior successful same-project profiles; rejudge with today's header before adoption.

    Captures can change headers too. Frequency ranks a hypothesis, never proves the flag's effect.
    """
    if not hasattr(net,'repair_feedback'):return []
    profiles = net.project_store(proj['id']).read_cache.get(('profile-hypotheses', proj['id']),
        net.project_store(proj['id']).read_version('repair_profiles', proj['id']), lambda: _captured(net, proj))
    ranked = {}
    for toolchain, flags in profiles:
        if toolchain == own['toolchain'] and list(flags) != list(own['flags']):
            ranked[flags] = ranked.get(flags, 0) + 1
    return [list(flags) for flags in sorted(ranked, key=lambda f: -ranked[f])[:2]]


def _captured(net, proj):
    # Maintained when evidence changes, so hypothesis reads never parse historical C JSON.
    rows = net.project_store(proj['id']).all('SELECT profile,before_hash,after_hash FROM repair_profile_evidence '
                         'WHERE project_id=? ORDER BY example_id DESC LIMIT 128', proj['id'])
    profiles = []
    for row in rows:
        try:
            profile = json.loads(row['profile'])
            if not isinstance(profile, dict) or profile.get('cmd') or row['before_hash'] is None or row['before_hash'] != row['after_hash']:
                continue
            flags = profile.get('flags')
            if not isinstance(flags, list) or len(flags) > 64 or not all(isinstance(f, str) for f in flags):
                continue
            profiles.append((profile.get('toolchain'), tuple(flags)))
        except (ValueError, TypeError):
            continue
    return profiles
