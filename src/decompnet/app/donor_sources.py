"""Bounded, read-only extraction of imported C and its local include closure.

Never run make/configure or copy foreign include paths into destination builds. Missing
conditional includes become #error so the destination preprocessor, not a guess, selects them.
"""
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

from .. import compiler, seeds
from ..forks import csource
from ..forks.project import ForkProject
from ..network import NetworkError
from ..project import compiler_config, compiler_for
from .source_preparation import Reader, declarations

MAX_HEADER = 2 * 1024 * 1024
MAX_CONTEXT = 128 * 1024
INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*[<"]([^>"\n]+)[>"][ \t]*$',re.M)
ID = r'[A-Za-z_]\w*'


class DonorReader(Reader):
    def flatten(self, path, seen=None):
        seen=set() if seen is None else seen
        path=path.resolve()
        if path in seen:
            return ''
        if len(seen)>=64:
            raise NetworkError('Donor includes exceed 64 levels.')
        seen.add(path)
        text=self.file(path)
        def include(hit):
            name=Path(hit[1])
            if name.is_absolute() or '..' in name.parts:
                raise NetworkError('Donor includes must remain within the imported checkout.')
            found=next((p for p in [path.parent/name,*[root/name for root in self.roots]] if p.is_file()),None)
            # A header used only by assembly or another compiler may legitimately be absent.
            return self.flatten(found,seen) if found else '#error donor_header_missing\n'
        text=INCLUDE.sub(include,text)
        seen.remove(path)
        if len(text.encode())>MAX_HEADER:
            raise NetworkError('Donor declarations exceed 2 MB; use a smaller source context.')
        return text


def build_hints(root):
    """Read literal include paths and unconditional literal -D flags, without interpreting make."""
    include=[]
    definitions={}
    path=root/'Makefile'
    text=path.read_text(errors='replace') if path.resolve().is_relative_to(root) and path.is_file() and path.stat().st_size<256*1024 else ''
    depth=0
    for line in text.splitlines():
        if re.match(r'\s*(?:ifeq|ifneq|ifdef|ifndef)\b',line):
            depth+=1
        elif re.match(r'\s*endif\b',line):
            depth=max(0,depth-1)
        if line.lstrip().startswith(('#','\t')):
            continue
        # Reject a whole make-variable token, rather than accidentally accepting its prefix.
        for token in re.findall(r'-I[ \t]*([^\s]+)',line):
            if '$' not in token and not Path(token).is_absolute() and '..' not in Path(token).parts:
                include.append(root/token)
        assignment=re.match(r'^([A-Za-z_]\w*)[ \t]*[:+?]?=',line)
        if depth or not assignment or assignment[1].startswith(('AS','ASM')):
            continue
        for flag in re.finditer(rf'(?:^|\s)-D({ID})(?:=(-?(?:0[xX][0-9A-Fa-f]+|[0-9]+)))?(?=\s|$)',line):
            definitions.setdefault(flag[1],set()).add(flag[2] or '1')
    include += [root/'include',root]
    include=list(dict.fromkeys(p.resolve() for p in include if p.is_dir() and p.resolve().is_relative_to(root)))[:24]
    defines={k:next(iter(v)) for k,v in definitions.items() if len(v)==1}
    return include,defines


def imported(net, proj, fn):
    root=Path(proj['root']).resolve()
    unit=ForkProject(root).unit(fn['unit'])
    if not unit.source:
        raise NetworkError('The named donor has no source file.')
    roots,defines=build_hints(root)
    reader=DonorReader({'root':str(root),'include_roots':'\n'.join(map(str,roots))})
    path=root/unit.source
    original=reader.file(path)
    # Imported symbol spelling can differ from its C spelling (e.g. COFF leading underscores).
    spellings=net.forks._names(net.forks._meta(fn),fn)
    symbol,span=next(((symbol,csource.find(original,symbol)) for symbol in spellings if csource.find(original,symbol)),(fn['name'],None))
    if not span or csource.is_stub(original[span[0]:span[1]]):
        raise NetworkError('The named donor has no ordinary C definition under its recorded name.')
    from .twins import apply
    body=apply(original[span[0]:span[1]],{symbol:fn['name']})
    header=declarations(reader.flatten(path))
    prelude=''.join(f'#define {k} {v}\n' for k,v in sorted(defines.items()))
    variants=[(body,prelude+header,'donor defaults')]
    # Try one actual version boundary present in this body and explicitly defined by its headers.
    # This is an attributed hypothesis, not an assertion about the donor's selected SDK version.
    for cond in re.finditer(rf'^[ \t]*#[ \t]*if[ \t]+({ID})[ \t]*(?:>=|<)[ \t]*({ID})[ \t]*$',body,re.M):
        values=set(re.findall(rf'^[ \t]*#[ \t]*define[ \t]+{cond[2]}[ \t]+(0[xX][0-9A-Fa-f]+|[0-9]+)[ \t]*$',header,re.M))
        if len(values)!=1:
            continue
        value=next(iter(values))
        override=f'\n#undef {cond[1]}\n#define {cond[1]} {value}\n'
        variants.append((body,prelude+override+header,f'header boundary {cond[1]}={value}'))
        break
    return variants


def compact(text, name):
    """Keep preprocessed C and the declaration closure it actually uses, never other bodies."""
    # #line filenames are compiler scratch paths, not transferable dependencies.
    text=re.sub(r'(?m)^[ \t]*#[^\n]*','',text)
    span=csource.find(text,name)
    if not span or csource.is_stub(text[span[0]:span[1]]):
        raise NetworkError('The donor preprocessor did not produce this C function.')
    source=text[span[0]:span[1]]
    context=declarations(text[:span[0]]+'\n'+text[span[1]:])
    roots=set(re.findall(ID,csource._blank(source)))-{name}
    selected=seeds.HeaderIndex(context).used(roots)
    # Do not silently transplant initialized globals or extra function bodies.
    if any('=' in csource._blank(d.text).split('{',1)[0] for d in selected):
        raise NetworkError('The donor depends on an initialized object; use explicit source preparation.')
    header='\n'.join(d.text for d in selected)
    if len(header.encode())>MAX_CONTEXT:
        raise NetworkError('The donor declaration closure exceeds 128 KB.')
    compiler.check_source(source)
    return source,header


def preprocess(net, proj, fn, source, header):
    """Reuse the pinned destination compiler sandbox. Unsupported drivers remain a visible skip."""
    settings=compiler_for(net.cfg(proj),fn['vram'], fn)
    cfg=compiler_config(Path(proj['root']),{'compiler':settings})
    if '-c' not in cfg.cmd:
        raise NetworkError('This compiler driver has no supported preprocessing mode for named donors.')
    command=['-E' if token=='-c' else token for token in cfg.cmd]
    result=compiler.compile_source(replace(cfg,cmd=command,cplusplus=False),header,source)
    if not result.ok:
        raise NetworkError('Named donor preprocessing failed: '+result.log[-600:])
    if len(result.obj or b'')>4*1024*1024:
        raise NetworkError('Named donor preprocessor output exceeds 4 MB.')
    return compact((result.obj or b'').decode('utf-8',errors='replace'),fn['name'])


def signature(source, header, settings):
    return hashlib.sha256(json.dumps([source,header,settings],sort_keys=True).encode()).hexdigest()
