"""Retain a verified function's compile context without rolling back other workers' changes."""
import json

from ..match_bundle import abi, compiler_identity, symbols
from ..network import NetworkError
from ..project import compiler_for, function_compiler_key
from .permuter_inputs import build_state

IDENTITY = ('id', 'project_id', 'name', 'unit', 'vram', 'size', 'rom_offset', 'n_insns', 'target', 'relocs', 'meta')


# Project settings that record work, spending, advice or where things are saved: none is a compiler input.
# Polishing's input guard ignores them too (polish_automation.input_key).
WORKFLOW_SETTINGS = ('suggestions', 'knowledge_auto', 'range_checks', 'compiler_survey', 'progress_revision',
                     'progress_folder', 'save_folder', 'instructions', 'priority', 'draft_generation', 'spend',
                     'autopilot', 'auto_commit_matches', 'auto_commit_folder', 'auto_commit_branch',
                     'score_commits', 'checked', 'export')


def destination(proj, fn):
    cfg = json.loads(proj['config'])
    identity = abi(proj, cfg)
    # Freeze unknown build settings too. Only known workflow metadata is excluded;
    # the selected compiler and exact header are preserved separately below.
    for key in ('compiler', 'compiler_ranges', 'function_compilers', 'boundaries', 'auto_push') + WORKFLOW_SETTINGS:
        cfg.pop(key, None)
    return (tuple(fn[key] for key in IDENTITY), proj['root'], identity,
            json.dumps(cfg, sort_keys=True), build_state(proj))


def capture(net, proj, fn, context, source):
    cfg = json.loads(proj['config'])
    profile = compiler_for(cfg, fn['vram'], fn)
    return {'destination':destination(proj, fn), 'symbols':symbols(net.project_store(proj['id']).conn, proj['id']),
            'source':source, 'header':context['header'], 'compiler':dict(profile), 'compiler_identity':compiler_identity(profile)}


def publish(net, db, fn, evidence, context_version, agent):
    """Inside the exact record transaction, after successful compile/link. Never relax the byte verdict."""
    if fn is None:
        raise NetworkError('The target inputs changed during exact verification. Recheck the updated target.')
    proj = db.execute('SELECT * FROM projects WHERE id=?', (fn['project_id'],)).fetchone()
    if (proj is None or fn['status'] not in ('open', 'matched') or destination(proj, fn) != evidence['destination']
            or symbols(db, fn['project_id']) != evidence['symbols']):
        raise NetworkError('Target, symbol or build inputs changed during exact verification. Recheck the match against the updated target.')
    cfg = json.loads(proj['config'])
    own = compiler_for(cfg, fn['vram'], fn)
    frozen = evidence['compiler']
    # Per-function profiles can preserve the tested toolchain/flags. Global command,
    # include and environment changes need a fresh compile, rather than an invented profile.
    if ({k:v for k,v in own.items() if k not in ('toolchain','flags')} !=
            {k:v for k,v in frozen.items() if k not in ('toolchain','flags')}
            or compiler_identity(frozen) != evidence['compiler_identity']):
        raise NetworkError('Compiler installation or build inputs changed during exact verification. Recheck the match.')
    if own != frozen:
        key = function_compiler_key(fn) or next((key for key, profile in cfg.get('function_compilers', {}).items()
            if profile.get('function') == fn['name'] and profile.get('vram') == fn['vram']), 'function:'+fn['name'])
        cfg.setdefault('function_compilers', {})[key] = {
            'function':fn['name'], 'vram':fn['vram'], 'toolchain':frozen['toolchain'], 'flags':list(frozen['flags'])}
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), fn['project_id']))
        net._event(db, fn['project_id'], agent.id, 'compiler', 'Preserved verified compiler profile for '+fn['name'])
    ctx = net._context(fn['project_id'], fn['unit'])
    context_version = ctx['version']
    if ctx['header'] != evidence['header']:
        # Keep the successful header private to this function. Other functions continue
        # with the latest shared header; the save/export and match bundle retain both.
        context_version = net._next_version(db, fn['project_id'])
        unit = f'exact_match_{fn["id"]}_{context_version}'
        db.execute('INSERT INTO context_versions(project_id,version,header,unit,message) VALUES(?,?,?,?,?)',
                   (fn['project_id'], context_version, evidence['header'], unit, 'Preserve verified exact-match header'))
        db.execute('UPDATE functions SET unit=? WHERE id=?', (unit, fn['id']))
    return context_version
