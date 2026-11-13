"""One repository dispatching to isolated, independently verified ROM rebuilds."""
from __future__ import annotations

import json
from pathlib import Path

from ..network import NetworkError
from .export import export_rom


def export_game(net, slug, out, log=print):
    """Freeze shared revisions and membership for the whole export."""
    from ..app import variants
    own = variants.member(net, slug)
    if own is None:raise NetworkError('This target was detached. Refresh Variants before exporting.')
    with variants.edit_lock(net, own['game'], 'export'):
        return _export_game(net, slug, out, log)


def _export_game(net, slug, out, log=print):
    """Keep each target's layout/assets/compiler/hash contract in its own directory."""
    from ..app import variants
    targets = variants.members(net, slug)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    results, manifest = [], {}
    for index, target in enumerate(targets):
        if hasattr(log, 'check_cancel'):log.check_cancel()
        if hasattr(log, 'progress'):log.progress({'done': index, 'total': len(targets), 'phase': 'Exporting variants'})
        proj = net.project_row(target['project'])
        if not net.is_rom(proj):
            raise NetworkError('PROJECT-mode variant export needs an object-build adapter; export that target separately.')
        folder = out / 'variants' / target['variant']
        sources = variants.shared_sources(net, target['project'])
        def variant_log(line):log(line)
        def progress(value, variant=target['variant'], completed=index):
            log.progress(dict(value, variant=variant, variants_done=completed, variants_total=len(targets),
                              phase=f"{variant}: {value.get('phase', 'Exporting')}"))
        if hasattr(log, 'progress'):variant_log.progress = progress
        if hasattr(log, 'check_cancel'):variant_log.check_cancel = log.check_cancel
        result = export_rom(net, target['project'], folder, net.console(proj), variant_log, sources=sources)
        # Conditional implementations share their actual body; defines remain in target wrappers.
        # Substantial overrides stay local, while unrelated identical C may also share a body.
        import hashlib
        build = json.loads((folder / 'build.json').read_text(encoding='utf-8'))
        for fn in build['functions']:
            path = fn.get('c')
            if not path:continue
            source = folder / path
            text = source.read_text(encoding='utf-8')
            lines = text.splitlines(keepends=True)
            # Preserve the target's header and compiler prelude before sharing its implementation.
            include = next((i for i, line in enumerate(lines) if line.startswith('#include "common')), None)
            if include is None:continue
            candidate = sources.get(fn.get('shared_instance'))
            if candidate and candidate['override']:continue
            body = candidate['shared_body'] if candidate else ''.join(lines[include + 1:])
            prefix = candidate['prefix'] if candidate else ''
            digest = hashlib.sha256(body.encode()).hexdigest()
            shared = out / 'shared' / f'{digest}.inc'
            shared.parent.mkdir(exist_ok=True)
            shared.write_text(body, encoding='utf-8')
            build.setdefault('shared_files', {})[f'shared/{digest}.inc'] = f'../../shared/{digest}.inc'
            source.write_text(''.join(lines[:include + 1]) + prefix + f'#include "shared/{digest}.inc"\n', encoding='utf-8')
        (folder / 'build.json').write_text(json.dumps(build, indent=1), encoding='utf-8')
        manifest[target['variant']] = {'path': f"variants/{target['variant']}",
                                       'sha1': build['sha1'], 'project': target['project']}
        results.append(dict(result, variant=target['variant']))
    (out / 'variants.json').write_text(json.dumps({'version': 1, 'variants': manifest}, indent=2), encoding='utf-8')
    from shutil import copyfile
    copyfile(Path(__file__).with_name('export_variant_build.py'), out / 'build.py')
    (out / '.gitignore').write_text('**/build/\n**/baserom*\n**/asm/\n**/assets/\n**/*.ld\n**/*_auto.txt\n**/.splat/\n**/*.d\nvariants/*/include/shared/\n', encoding='utf-8')
    (out / 'README.md').write_text(
        '# Multi-variant decompilation\n\nUse `python3 build.py --variant <id>` or '
        '`python3 build.py --all-variants`. Each target has its own input, symbols, layout, assets, '
        'compiler settings, build directory and expected hash. Put your own ROMs at the paths in '
        'each variant README; never commit game data.\n\n'
        'Identical C is in `shared/`; target wrappers retain their own headers and defines. '
        'Unmatched functions use original assembly. Edit substantial differences independently. '
        'A shared edit is accepted only when every affected variant still matches. '
        'Run `--all-variants` after editing shared files.\n', encoding='utf-8')
    # Rebuild the final wrapper structure, not only the intermediate exports.
    from ..hostenv import console_python
    from .export_process import run as verify_build
    if hasattr(log, 'progress'):log.progress({'phase': 'Verifying final repository', 'variants_total': len(targets)})
    proc = verify_build([console_python(), 'build.py', '--all-variants'], out, log,
                        timeout=max(3600, 3600 * len(targets)))
    identical = proc.returncode == 0 and all(r['identical'] for r in results)
    log(f'All variants rebuild exactly. Repository: {out}' if identical else
        f'Export needs attention: one or more variants did not rebuild exactly. Repository: {out}')
    if not identical:log((proc.stdout + proc.stderr)[-3000:])
    if hasattr(log, 'progress'):log.progress({'done': len(targets), 'total': len(targets),
                                             'phase': 'Export verified' if identical else 'Export needs attention'})
    return {'path': str(out), 'identical': identical,
            'variants': results, 'matched': sum(r['matched'] for r in results),
            'functions': sum(r['functions'] for r in results), 'fallback': [f for r in results for f in r['fallback']],
            'log': (proc.stdout + proc.stderr)[-3000:]}
