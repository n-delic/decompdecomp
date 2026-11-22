"""Hints from a function's real calls: how many arguments it takes, float or integer, what values they had, and what
it reads and writes through its pointer arguments.

Matching proves bytes, and the code says a lot about its arguments, but not what they were: that an argument was
always 0..40 (a u8 once a mask is dropped), that a pointer argument points to a record whose +0x1C halfword stays
under 256 and whose +0x20 word is another pointer, that no value was ever negative. The console's reversing
emulator records a function's first calls (the recorder `compare_behaviour` uses: its registers and RAM at the
entry); the processor's reader (`Arch.call_hints`, MIPS: arch/mips_call_hints.py) turns them with the function's code
into hints. They are saved beside the recordings in the project's build folder (derived from the user's ROM: git
ignores it; nothing here is ever committed) and offered:

- in the function's brief ("Recorded calls ..."), when a recording was made;
- as draft candidates (`draft_candidates`, project setting `draft_call_hints`, off: measured no gain): the m2c draft with a narrow parameter type where the code masks the
  argument and every recorded value fits, and without trailing parameters the code never reads; judged beside the
  raw draft like the other candidates, so a wrong hint costs one compile.

`get_call_hints(function)` records (bounded: the first calls within the VIs asked for, only with the ROM and the
emulator installed) and answers; `compare_behaviour` saves the hints from its own recording too. Nothing is run
for a brief or a draft: only recordings that exist are read. N64 only for now (the console with an emulator):
ROADMAP.md.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from .. import arch as architectures
from ..network import NetworkError

# Project setting (`draft_call_hints`), off: on 359 drafts of an N64 game with recorded hints the narrowing fired on
# 5 (0 better, 1 worse, 3 same, 1 did not compile) and dropping trailing parameters on none
# (docs/learnings/finding/emulator-call-hints.md). The brief's lines do not depend on it.
SETTING = 'draft_call_hints'
CALLS = 4


def enabled(cfg) -> bool:
    return bool((cfg or {}).get(SETTING, False))


def folder(proj, name: str) -> Path:
    return Path(proj['root']) / 'build' / 'hints' / name


def _key(asm: str) -> str:
    return hashlib.sha256((asm or '').encode()).hexdigest()[:16]


def derive(net, proj, fn, bases, *, keep=True) -> dict | None:
    """The hints of `fn` from its recorded calls (`bases`: `<base>.state`/`.rdram` pairs), saved beside them. None:
    no reader for this processor, or nothing recorded."""
    reader = getattr(architectures.get(proj['arch']), 'call_hints', None)
    console = net.console(proj)
    if reader is None or not bases or console.emulator is None:
        return None
    asm = net._asm_text(fn, proj)
    recordings = [console.emulator.read_recording(Path(b)) for b in bases]
    hints = reader(asm, recordings)
    hints['key'] = _key(asm)
    where = folder(proj, fn['name'])
    where.mkdir(parents=True, exist_ok=True)
    (where / 'hints.json').write_text(json.dumps(hints, indent=1, default=list), encoding='utf-8')
    if not keep:
        for base in bases:
            for f in Path(base).parent.glob(Path(base).name + '.*'):
                f.unlink(missing_ok=True)
    return hints


def saved(net, proj, fn) -> dict | None:
    """The saved hints of `fn`, when they were derived from its present code."""
    try:
        hints = json.loads((folder(proj, fn['name']) / 'hints.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    try:
        current = _key(net._asm_text(fn, proj))
    except Exception:  # noqa: BLE001 - a hint is never worth failing a brief
        return None
    return hints if hints.get('key') == current else None


def brief_lines(net, proj, fn) -> list[str]:
    """The brief's lines from the saved hints (none when none were recorded)."""
    reader = architectures.get(proj['arch'])
    hints = saved(net, proj, fn)
    if not hints:
        return []
    from ..arch import mips_call_hints
    text = mips_call_hints.lines(hints) if reader.call_hints else []
    return ['', '### What the console passed (recorded calls)', *text,
            'These are what happened in a few calls of the real game: a lead for types and widths, not a proof. '
            'get_call_hints records more.'] if text else []


def _params(draft: str, name: str):
    from .. import seeds
    found = re.search(seeds._SIGNATURE.format(name=re.escape(name)), draft or '', re.M)
    if not found:
        return None, []
    return found, [p.strip() for p in found['params'].split(',') if p.strip()]


def draft_candidates(draft: str, name: str, hints: dict) -> list[tuple[str, str]]:
    """[(family, source)]: m2c's draft with what the recorded calls make consistent (see the module docstring):
    'call_hints_narrow' (parameter types narrowed, the mask dropped) and 'call_hints_args' (trailing parameters
    the code never reads removed). Only where the hint holds in every recorded call."""
    from ..arch import mips_call_hints
    found, params = _params(draft, name)
    if not found or not hints:
        return []
    original, original_params = found, params
    out = []
    narrow = mips_call_hints.consistent_narrowing(hints)
    if narrow:
        text, changed = draft, False
        for index, kind in narrow.items():
            arg = f'arg{index}'
            for position, param in enumerate(params):
                if re.fullmatch(r'(?:s32|u32|int|M2C_UNK)\s+' + arg, param):
                    mask = {'u8': '0xFF', 'u16': '0xFFFF'}.get(kind)
                    body = text[found.end():]
                    if mask:
                        body = re.sub(r'\(\s*' + arg + r'\s*&\s*' + mask + r'\s*\)', arg, body)
                        body = re.sub(r'\b' + arg + r'\s*&\s*' + mask + r'\b', arg, body)
                    head = text[:found.start('params')] + found['params'].replace(param, f'{kind} {arg}', 1)
                    text = head + text[found.end('params'):found.end()] + body
                    changed = True
                    found, params = _params(text, name)
                    break
        if changed and text != draft:
            out.append(('call_hints_narrow', text))
    count = hints.get('count', 0)
    if not hints.get('float_args') and len(original_params) > count >= 0:
        names = [re.search(r'(\w+)$', p) for p in original_params[count:]]
        body = draft[original.end():]
        if all(m and re.fullmatch(r'arg\d+', m[1]) and not re.search(r'\b' + m[1] + r'\b', body) for m in names):
            kept = ', '.join(original_params[:count]) or 'void'
            out.append(('call_hints_args', draft[:original.start('params')] + kept + draft[original.end('params'):]))
    return out


def _check(net, slug):
    proj = net.project_row(slug)
    console = net.console(proj)
    if console.emulator is None or getattr(architectures.get(proj['arch']), 'call_hints', None) is None:
        raise NetworkError(f"{console.name} has no reversing emulator in decompdecomp yet: call hints are recorded on N64 "
                           "games for now")
    if not net.is_rom(proj):
        raise NetworkError('Call hints are recorded for projects imported from a ROM')
    return proj, console


def record(net, slug: str, function: str, calls: int = CALLS, vis: int = 3600, from_vi: int = 0, input: str = '',
           expansion_pak: bool = False, log=lambda *a: None) -> str:
    """Record the function's first `calls` calls in the emulator and derive the hints (saved; the answer says them)."""
    from ..network import IN_BUILD
    proj, console = _check(net, slug)
    with net.progress.view():
        fn = net.project_store(proj['id']).one(
            f'SELECT * FROM functions WHERE project_id=? AND name=? AND {IN_BUILD}', proj['id'], function)
    if fn is None or fn['vram'] is None:
        raise NetworkError(f'no function {function!r} in this game: give a function\'s name as list_functions shows it')
    where = folder(proj, function)
    shutil.rmtree(where, ignore_errors=True)
    (where / 'rec').mkdir(parents=True)
    rom = Path(proj['root']) / net.cfg(proj)['rom']['file']
    log(f"recording {function}'s first {calls} calls in the reversing emulator")
    run = console.emulator.run(rom, vis, inputs=input.strip(), breaks=[fn['vram']], report_from=from_vi,
                               expansion_pak=expansion_pak, record=(where / 'rec', calls), timeout=1500)
    if not run.recorded:
        return (f"{function} wasn't called in {'VIs ' + str(from_vi) + ' to ' if from_vi else 'the first '}{run.vis} VIs of "
                "the game: give input to reach where it runs, more VIs, or a later from_vi.")
    hints = derive(net, proj, fn, run.recorded)
    from ..arch import mips_call_hints
    text = mips_call_hints.lines(hints) if hints else []
    return '\n'.join([f'{function}: {len(run.recorded)} calls recorded on the console (kept in the project\'s build '
                      'folder, never committed).', *(text or ['Nothing the code reads from its arguments could be '
                                                              'compared with the recorded values.']),
                      'The brief and the draft stage use these now.'])


def answer(net, slug: str, function: str, calls: int = CALLS, vis: int = 3600, from_vi: int = 0, input: str = '',
           expansion_pak: bool = False) -> str:
    """The tool's answer: the hints, or "still running" when the recording outlasts a call (the same call collects it)."""
    from .behaviour import later
    calls = max(1, min(int(calls), 8))
    vis, from_vi = int(vis), int(from_vi)
    if not 1 <= vis <= 60 * 60 * 5 or not 0 <= from_vi < vis:
        raise NetworkError('vis is 1 to 18000 (60 a second), and from_vi below it')
    _check(net, slug)
    key = ('call_hints', slug, function, calls, vis, from_vi, input.strip(), bool(expansion_pak))
    done, value = later(key, lambda: record(net, slug, function, calls, vis, from_vi, input, expansion_pak))
    if not done:
        return (f'Still running ({value:.0f} s so far: the game runs until the function is called). Call get_call_hints '
                'again with the same arguments for the result.')
    return value
