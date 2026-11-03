"""Rank profile hypotheses from target evidence, never from a familiar function name alone."""
import json

from ..arch.mips_runtime import helper


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
    from .sdk_io_repairs import io_target
    if naming.get('method')=='sdk_signature' or io_target(dict(fn).get('asm','')):
        # Prioritize -O1 only for an identity recorded from SDK signature evidence.
        candidate=[('-O1' if flag.startswith('-O') else flag) for flag in flags]
        if not any(flag.startswith('-O') for flag in flags):candidate.append('-O1')
        out.append(candidate)
    return [f for f in out if f!=flags]


def neighbours(net, proj, fn, own):
    """Nearby exact matches propose flags, not an inferred compiler range or a setting change."""
    from ..project import compiler_for
    rows=net.store.all("SELECT vram FROM functions WHERE project_id=? AND status='matched' AND best_score=0 "
                       "AND vram BETWEEN ? AND ? ORDER BY ABS(vram-?) LIMIT 8",
                       proj['id'],fn['vram']-16384,fn['vram']+16384,fn['vram'])
    cfg=net.cfg(proj);ranked=[]
    for row in rows:
        profile=compiler_for(cfg,row['vram'])
        flags=list(profile.get('flags',[]))
        if profile.get('toolchain')==own.get('toolchain') and not profile.get('cmd') and flags!=list(own['flags']) and flags not in ranked:
            ranked.append(flags)
    return ranked[:2]


def captured(net, proj, own):
    """Prior successful same-project profiles; rejudge with today's header before adoption.

    Captures can change headers too. Frequency ranks a hypothesis, never proves the flag's effect.
    """
    if not hasattr(net,'repair_feedback'):return []
    rows=net.store.all("SELECT evidence FROM repair_examples WHERE project_id=? ORDER BY id DESC LIMIT 128",proj['id'])
    ranked={}
    for row in rows:
        try:
            evidence=json.loads(row['evidence']);after=evidence['after'];before=evidence.get('before') or {}
            profile=after['settings']['compiler']
            if not isinstance(profile,dict) or profile.get('cmd') or profile.get('toolchain')!=own.get('toolchain'):continue
            if not before or before.get('source_sha256')!=after.get('source_sha256'):continue
            flags=profile.get('flags')
            if not isinstance(flags,list) or not all(isinstance(f,str) for f in flags) or len(flags)>64:continue
            if flags==list(own['flags']):continue
            key=tuple(flags);ranked[key]=ranked.get(key,0)+1
        except (ValueError,KeyError,TypeError):continue
    return [list(flags) for flags in sorted(ranked,key=lambda f:-ranked[f])[:2]]
