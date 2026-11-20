"""Compare a measured open candidate using recorded calls and a disposable port build."""

import json
import re
import tempfile
from pathlib import Path

from .. import toolchains
from ..network import NetworkError
from . import agent_evidence, agent_tokens, behaviour, guided_matching, recompile


def _write_workspace(folder, fn, source, header, symbols, layout):
    progress = folder / 'progress'
    (progress / 'src').mkdir(parents=True)
    (progress / 'include').mkdir()
    (progress / 'src' / f"{fn['name']}.c").write_text(source)
    (progress / 'include' / 'common.h').write_text(header)
    (progress / 'decompdecomp.json').write_text(json.dumps({'layout': layout, 'symbols': symbols}))


def _build_and_compare(net, slug, proj, fn, console, rom, source, header, calls, vis, inputs):
    from ..port.wasm import build, play

    with tempfile.TemporaryDirectory(prefix='decomp-candidate-behaviour-') as work:
        folder = Path(work)
        records = folder / 'calls'
        records.mkdir()
        run = console.emulator.run(rom, vis, inputs=inputs, breaks=[fn['vram']],
                                   record=(records, calls), timeout=120)
        if not run.recorded:
            if run.timed_out:
                return 'No calls were recorded before the emulator time limit. Try an earlier call or a different input.'
            return 'No calls were recorded. Supply input or a longer recording window to reach the function.'
        store = net.project_store(proj['id'])
        layout = [dict(row) for row in store.all('SELECT name,vram FROM functions WHERE project_id=? '
                                                 'AND gone_mark IS NULL AND vram IS NOT NULL', proj['id'])]
        if sum(row['vram'] == fn['vram'] for row in layout) != 1:
            raise NetworkError('The call address has multiple function owners. Resolve the overlay before comparing.')
        _write_workspace(folder, fn, source, header, net.layout_symbols(proj['id']), layout)
        quiet = lambda *args: None  # noqa: E731
        with rom.open('rb') as stream:
            stream.seek(8)
            entry = int.from_bytes(stream.read(4), 'big')
        with recompile.ready(net, slug, proj, console.recompiler, quiet) as recomp:
            harness = console.recompiler.harness(recomp, entry, quiet)
        program = build.build(folder, console=net.cfg(proj)['platform'], log=quiet,
                              library_names=play.library_names(net, slug), ensure_host_import=True)
        # PORT_CALL can otherwise run a fallback. Demand a real WebAssembly export for this function.
        functions = (program.parent / 'funcs.c').read_text()
        if not re.search(rf'\{{0x{fn["vram"]:08X}u, \(void \*\).*\}}, /\* {re.escape(fn["name"])} \*/',
                         functions):
            return 'Candidate did not produce a callable port export. No behavior verdict.'
        label = net.symbols(proj['id']).label
        verdicts = []
        for index, recorded in enumerate(run.recorded, 1):
            result, differences, evidence = behaviour.one_call(
                console, harness, program, rom, fn['vram'], recorded, label,
                fn['name'] in behaviour.v0_takers(program))
            verdicts.append(describe_call(index, result, differences, evidence))
        return '\n'.join(verdicts)


def describe_call(index, result, changes, evidence):
    if evidence.get('runtime'):
        return f'Call {index}. Inconclusive after runtime stubs: ' + ', '.join(evidence['runtime'])
    if result == 'error':
        return f'Call {index}. Could not compare: ' + (changes or ['call failed'])[0]
    if changes:
        return f'Call {index}. First observable difference: {changes[0]}'
    return f'Call {index}. Observed return and compared memory agreed.'


def answer(net, agent, slug, function, revision, calls=2, vis=3600, input=''):
    proj, console, _ = behaviour._project(net, slug)
    fn = net._function(function, slug)
    if not re.fullmatch(r'[A-Za-z_]\w*', fn['name']) or fn['vram'] is None:
        raise NetworkError('Use a named function with one known call address.')
    if not 1 <= calls <= 3 or not 1 <= vis <= 18000 or len(input) > 2048:
        raise NetworkError('Use 1–3 calls, 1–18000 VIs, and at most 2048 characters of controller input.')
    store = net.project_store(fn['project_id'])

    def validate():
        current = net._function(function, slug)
        source, actual = agent_tokens.candidate(net, agent, slug, function)
        if actual != revision or current['status'] != 'open' or not guided_matching._held(store, current, agent):
            raise NetworkError('Hold this open function and supply its current candidate revision.')
        return source, agent_evidence.build_key(net, current)[0]

    source, scope = validate()
    report = getattr(net, '_agent_outcomes', {}).get((agent.id, fn['id'])) or {}
    if report.get('source') != source or report.get('input_scope') != scope or not report.get('score'):
        raise NetworkError('Compile this revision once to obtain current mismatch evidence before replaying it.')
    if (not console.emulator.installed() or not toolchains.installed(console.recompiler.TOOL)
            or not (console.recompiler.toolchain_folder() / 'include' / 'recomp.h').is_file()):
        raise NetworkError('Install the reversing emulator and recompiler through Setup before comparing calls.')
    if not all(toolchains.installed(name) for name in ('zig', 'wabt')):
        raise NetworkError('Install Zig and WABT through Setup before comparing candidate behavior.')
    context = net._context(proj['id'], fn['unit'])
    header = context['header'] if context else ''
    rom = Path(proj['root']) / net.cfg(proj)['rom']['file']
    key = ('candidate-behaviour', id(net), slug, fn['id'], agent.id, revision, scope, calls, vis, input.strip())
    done, value = behaviour.later(key, lambda: _build_and_compare(net, slug, proj, fn, console, rom, source,
                                                                 header, calls, vis, input.strip()))
    if validate() != (source, scope):
        raise NetworkError('The build changed during behavior replay. Compare the current candidate again.')
    if not done:
        return f'Behavior check still running ({value:.0f} s). Call compare_candidate_behaviour again with the same arguments.'
    return value + '\nSampled behavior is diagnostic. The ordinary compiler and exact judge decide matches.'
