"""Bounded advisory review of explicit callback arguments, never a signature rewrite."""
import re

from ..forks.csource import _blank, find
from . import callee_contracts as contracts
from . import zero_argument_repairs
from .integer_repair_support import IDENT, calls, split_args, view
from .pattern_repairs import close

LIMIT = 2
POINTER = re.compile(r'\s*(void|int|s32|u32)\s*\(\s*\*\s*(?:'+IDENT+r')?\s*\)\s*\(([^()]*)\)\s*')


def signature(text):
    hit = POINTER.fullmatch(text)
    return contracts.signature(hit[1],hit[2],named=False) if hit else None


def known(value):
    return value is not None and '?' not in (value[0],*value[1])


def leads(source, name, selected, limit=LIMIT):
    """Use only already-fetched selected definitions, explicit prototypes and direct calls."""
    if len(source)>64000 or not (part := view(source,name)):
        return []
    clean = part.clean
    if re.search(r'^\s*#\s*(?!define\b)\w+|::|\b(?:class|namespace|template|operator)\b',clean,re.M):
        return []
    macros = set(re.findall(r'^\s*#\s*define\s+('+IDENT+r')\b',clean,re.M))
    if macros & {'void','int','s32','u32',name}:
        return []
    result = []
    focal_span = find(source,name)
    formals = clean[focal_span[0]:part.opening] if focal_span else ''
    def shadowed(variable):
        return (bool(re.search(r'\b'+re.escape(variable)+r'\b',formals))
                or zero_argument_repairs.shadowed(part,variable,clean)
                or bool(re.search(r'\b(?!(?:return|if|while|for|switch)\b)'+IDENT+r'\s+[*()\s]*'+re.escape(variable)+r'\b',part.body)))
    # A constructor/helper declaration can contain an explicit function-pointer
    # parameter. Typedef callback types and indirect calls require fuller evidence.
    pattern = re.compile(r'(?:^|(?<=[;}]))\s*(?:extern\s+)?'+contracts.TYPE+r'\s*\b('+IDENT+r')\s*\(',re.M)
    declarations = {}
    for hit in pattern.finditer(clean,0,part.opening):
        if hit[1] in macros or clean[:hit.start()].count('{') != clean[:hit.start()].count('}'):
            continue
        end = close(clean,hit.end()-1)
        if end and re.match(r'\s*;',clean[end:]):
            declarations.setdefault(hit[1],[]).append((hit.end(),end-1))
    for callee,spans in declarations.items():
        if len(spans)!=1 or shadowed(callee):
            continue
        a,b = spans[0]
        params = [clean[a+x:a+y]for x,y in split_args(clean[a:b])]
        if len(params)>8:
            continue
        expected = [(i,signature(p))for i,p in enumerate(params)]
        expected = [(i,s)for i,s in expected if known(s)]
        for site,(start,end) in enumerate(calls(part,callee)):
            if site>=16:
                break
            arguments = [clean[start+x:start+y].strip()for x,y in split_args(clean[start:end])]
            if len(arguments)!=len(params):
                continue
            for index,wanted in expected:
                argument = arguments[index]
                # Peel only an explicit plain function-pointer cast, not an
                # arbitrary expression or cast which could select another value.
                if argument.startswith('('):
                    cast_end = close(argument,0)
                    if not cast_end or signature(argument[1:cast_end-1]) is None:
                        continue
                    argument = argument[cast_end:].strip()
                target = re.fullmatch(r'&?\s*('+IDENT+r')',argument)
                if not target or target[1] in macros or target[1] not in selected or shadowed(target[1]):
                    continue
                callback = target[1]
                text = selected[callback]
                if len(text)>64000:
                    continue
                text = _blank(text)
                if re.search(r'^\s*#\s*(?!define\b)\w+',text,re.M) or re.search(r'^\s*#\s*define\s+(?:void|int|s32|u32|'+re.escape(callback)+r')\b',text,re.M):
                    continue
                span = find(text,callback)
                if not span or len(re.findall(r'\b'+re.escape(callback)+r'\s*\([^;{}]*\)\s*{',text))!=1:
                    continue
                definition = re.fullmatch(r'\s*(?:static\s+)?('+contracts.TYPE+r')\s+'+re.escape(callback)+r'\s*\(([^()]*)\)\s*{[\s\S]*}\s*',text[span[0]:span[1]])
                actual = contracts.signature(definition[1],definition[2],named=True) if definition else None
                if not known(actual) or actual==wanted:
                    continue
                render = lambda s:s[0]+'('+(','.join(s[1])or'void')+')'
                item = f'{callee} argument {index+1}: {callback} selected {render(actual)}, declared callback {render(wanted)}'
                if item not in result:
                    result.append(item)
                if len(result)>=limit:
                    return result
    return result


def message(items):
    if not items:
        return ''
    return ('Callback-contract review (advisory): '+'; '.join(items)+'. A function-pointer cast does not '
            'make incompatible callable types compatible. Inspect the actual dispatcher, all registered '
            'callbacks and other direct callers; validate any justified public-entry/consumed-view correction '
            'jointly before allocation searches. Unknown typedefs, indirect calls and macro registries are not '
            'resolved by this bounded check. No signature or source was changed.')


def source_hint(source, name):
    """Cheap claim-path cue; defer all matched-definition lookups to the full brief."""
    part = view(source,name) if source and len(source)<=64000 else None
    if not part or not re.search(r'\(\s*(?:void|int|s32|u32)\s*\(\s*\*\s*\)\s*\([^()]*\)\s*\)\s*&?\s*'+IDENT,part.body):
        return ''
    return ('Callback casts present: get_function(section="brief") can review explicit callback formals '
            'against selected definitions. Inspect the dispatcher and registered callback contracts before '
            'allocation searches; a cast does not establish callable-type compatibility.')
