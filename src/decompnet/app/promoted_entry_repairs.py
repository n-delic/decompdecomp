"""Keep a signed-int interface while representing an entry narrowing as a typed local."""
import re

from ..forks.csource import _blank
from . import local_flow, matched_prototype_repairs, zero_argument_repairs
from .allocator_call_guards import dominators
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit, locals_for, split_args, view

FAMILY = 'promoted_entry_local'
LIMIT = 4
NARROW = {'signed char', 'unsigned char', 'short', 'unsigned short'}


def variants(source, name, header='', *, language=None, int_bits=None):
    # These must come from the current compile invocation, never a filename guess.
    # Until a pipeline supplies that evidence, this family stays disabled.
    if language != 'c' or int_bits != 32:
        return []
    # Translation-phase splicing can conceal directives and type-observing tokens.
    raw = source+'\n'+header
    if re.search(r'\\\r?\n', raw) or '??' in raw or '%:' in raw:
        return []
    if max(len(source), len(header)) > 64000:
        return []
    part = view(source, name)
    if not part:
        return []
    if re.search(r'\b(?:volatile|asm|__asm|__asm__|sizeof|typeof|__typeof__|_Generic|setjmp|_setjmp|sigsetjmp|longjmp|__builtin_frame_address|__builtin_return_address)\b', part.clean):
        return []
    context = _blank(source+'\n'+header)
    if any(directive != 'define' for directive in re.findall(r'^\s*#\s*(\w+)', context, re.M)):
        return []  # Includes (also GNU #import) and conditional types need preprocessing evidence.
    if re.search(r'\b(?:__alignof__|__alignof|_Alignof|alignof|__typeof|__typeof_unqual__|__auto_type|auto|decltype|typeid|class|template|namespace|operator|_Pragma)\b|\b__builtin_\w+', context):
        return []
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b', context, re.M))
    if macros & (zero_argument_repairs.TYPE_WORDS | {name}):
        return []
    if any(re.search(r'\b'+re.escape(m)+r'\b', part.body) for m in macros):
        return []
    signature = re.search(r'\b'+re.escape(name)+r'\s*\(([^{};()]*)\)\s*{\s*$', part.clean[:part.opening+1])
    if not signature or '...' in signature[1]:
        return []
    flow = local_flow.analyze(source, name, declarations=locals_for(part))
    if not flow:
        return []
    dom = dominators(flow)
    names = re.findall(IDENT, context)
    results = []
    for a,b in split_args(signature[1]):
        scalar = matched_prototype_repairs.scalar(signature[1][a:b], context, named=True)
        if not scalar or scalar[1] != 'int':
            continue  # Unsigned wide values have different usual arithmetic conversions.
        formal = scalar[0]['name']
        if zero_argument_repairs.shadowed(part, formal, context):
            continue
        pattern = re.compile(r'\b'+re.escape(formal)+r'\s*=\s*\(\s*('+matched_prototype_repairs.TYPE+r')\s*\)\s*('+re.escape(formal)+r')\s*;')
        masks = list(pattern.finditer(part.clean, part.opening+1, part.end))
        if len(masks) != 1:
            continue
        mask = masks[0]
        narrow = matched_prototype_repairs.scalar(mask[1], context)
        if not narrow or narrow[1] not in NARROW or block_path(part, mask.start()) != [part.opening]:
            continue
        if not zero_argument_repairs.stable(part, formal, assignment=mask.span()):
            continue
        refs = list(re.finditer(r'\b'+re.escape(formal)+r'\b', part.clean[part.opening+1:part.end]))
        offsets = [part.opening+1+r.start() for r in refs]
        if any(pos < mask.start() for pos in offsets):
            continue
        node = flow.at(mask.start())
        if (node is None or part.clean[node.start:mask.start()].strip()
                or part.clean[mask.end():node.end].strip()):
            continue
        index = flow.nodes.index(node)
        if any((use := flow.at(pos)) is None or index not in dom.get(flow.nodes.index(use), set())
               for pos in offsets if pos >= mask.end()):
            continue
        incoming = formal+'_incoming'
        while incoming in names or incoming in macros:
            incoming += '_'
        formal_start = signature.start(1)+a+scalar[0].start('name')
        changes = [(formal_start, formal_start+len(formal), incoming),
                   (part.opening+1, part.opening+1, ' '+mask[1]+' '+formal+';'),
                   (mask.start(2), mask.end(2), incoming)]
        results.append((FAMILY, edit(source, changes)))
        # The conversion cannot trap or observe a side effect. Try its natural initializer too.
        initialized = changes[:1]+[(part.opening+1,part.opening+1,' '+mask[1]+' '+formal+' = ('+mask[1]+')'+incoming+';'),
                                  (mask.start(),mask.end(),''.join('\n' if c=='\n' else ' ' for c in source[mask.start():mask.end()]))]
        results.append((FAMILY, edit(source, initialized)))
        if len(results) >= LIMIT:
            break
    return list(dict.fromkeys(results))[:LIMIT]


def profile_scope(arch, profile):
    """Prove C32 for direct pinned GCC builds, without installing or invoking tools.

    External PROJECT commands, adapters and compiler flags capable of changing
    language, headers or integer widths need invocation evidence first (ROADMAP).
    The allowed MIPS/PPC/ARM/x86 GCC targets use 32-bit int, including ILP32/LP64.
    """
    from .. import toolchains
    if arch not in {'mips', 'mipsel', 'ppc', 'arm', 'x86', 'x86_64'} or not isinstance(profile, dict):
        return {}
    if profile.get('cmd') or not profile.get('toolchain'):
        return {}
    try:
        tc = toolchains.get(profile['toolchain'])
    except toolchains.ToolchainError:
        return {}
    if tc.arch != arch or tc.cplusplus or tc.cmd or tc.driver or tc.prelude or not tc.cc:
        return {}
    if not re.search(r'(?:^|/)(?:[\w.-]+-)?gcc(?:\.exe)?$', tc.cc[0]):
        return {}
    if any(arg != '-c' and not arg.startswith('-B{tc}/') for arg in tc.cc[1:]):
        return {}
    # An explicit allowlist is intentional: unknown switches can inject macros,
    # include hidden declarations, select C++, or change integer promotion widths.
    safe = r'-(?:G\d+|g[0-3]?|O[0-3s]?|mips[1-4]|mgp32|mfp32|mhard-float|msoft-float|EB|EL|f(?:no-)?(?:fast-math|strength-reduce|omit-frame-pointer|inline-functions|builtin|common)|Wall|Wextra)'
    if any(not re.fullmatch(safe, flag) for flag in profile.get('flags', tc.flags)):
        return {}
    return {'language': 'c', 'int_bits': 32}


def scope(net, proj, fn):
    """Resolve the current ROM function profile; external builds fail closed."""
    from ..project import compiler_for
    if net.fork_slug(proj['slug']):
        return {}
    return profile_scope(proj['arch'], compiler_for(net.cfg(proj), fn['vram'], fn))
