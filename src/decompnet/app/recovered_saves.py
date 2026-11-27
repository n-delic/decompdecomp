"""Keep verified post-import ROM discoveries when a saved project is opened again."""

import hashlib
import json
import re

from ..network import NetworkError


def validate(manifest):
    """Reject malformed placements before reading a game or touching saved files."""
    entries = manifest.get('recovered', [])
    if not isinstance(entries, list):
        raise ValueError('recovered must be a list')
    layout = {item['name']: item for item in manifest.get('layout', [])}
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('a recovered function must be an object')
        name = entry.get('name')
        saved = layout.get(name)
        digest = entry.get('sha256')
        if (name in seen or saved is None or type(entry.get('rom_offset')) is not int
                or entry['rom_offset'] < 0 or type(entry.get('vram')) is not int
                or type(entry.get('size')) is not int or entry['size'] <= 0
                or (entry['vram'], entry['size']) != (saved['vram'], saved['size'])
                or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise ValueError(f'recovered function {name!r} has an invalid layout, offset or fingerprint')
        seen.add(name)


def records(net, project_id, rendered_rows=None):
    """Save fingerprints and placements, never bytes from the user's game."""
    store = net.project_store(project_id)
    if rendered_rows is None:
        rows = store.all("SELECT name,rom_offset,vram,size,target FROM functions WHERE project_id=? "
                         "AND CASE WHEN json_valid(meta) THEN json_extract(meta,'$.found_after_split')=1 "
                         "OR json_type(meta,'$.donor_boundary')='object' "
                         "OR json_type(meta,'$.reviewed_recompile')='object' ELSE 0 END "
                         "ORDER BY rom_offset,name", project_id)
    else:
        # The manifest already read every row's metadata. Avoid another full project scan on every save.
        names, expected = [], {}
        for row in rendered_rows:
            raw = row['meta'] or ''
            if not any(key in raw for key in ('found_after_split', 'donor_boundary', 'reviewed_recompile')):
                continue
            try:
                meta = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(meta, dict):
                continue
            if (meta.get('found_after_split') is True or isinstance(meta.get('donor_boundary'), dict)
                    or isinstance(meta.get('reviewed_recompile'), dict)):
                names.append(row['name'])
                expected[row['name']] = (row['vram'], row['size'])
        if not names:
            return []
        rows = store.all('SELECT name,rom_offset,vram,size,target FROM functions WHERE project_id=? '
                         'AND name IN (SELECT value FROM json_each(?)) ORDER BY rom_offset,name',
                         project_id, json.dumps(names))
        if len(rows) != len(names) or any((row['vram'], row['size']) != expected[row['name']] for row in rows):
            raise NetworkError('The project changed while saving recovered functions. Save again.')
    result = []
    for row in rows:
        target = row['target']
        if (not isinstance(row['rom_offset'], int) or not isinstance(row['vram'], int)
                or not isinstance(row['size'], int) or row['size'] <= 0 or not isinstance(target, bytes)
                or len(target) != row['size']):
            raise NetworkError(f"Couldn't save recovered function {row['name']}: its original bytes or placement are missing.")
        result.append({'name': row['name'], 'rom_offset': row['rom_offset'], 'vram': row['vram'],
                       'size': row['size'], 'sha256': hashlib.sha256(target).hexdigest()})
    return result


def ensure(net, slug, manifest, log, *, scan=True):
    """Re-run the verified discovery once before applying a save; never silently discard unrecovered work."""
    entries = manifest.get('recovered') or []
    if not entries:
        return
    check = getattr(log, 'check_cancel', lambda: None)
    proj = net._project(slug)
    image, _ = net._rom(proj)
    for entry in entries:
        check()
        start, size = entry['rom_offset'], entry['size']
        if (start < 0 or size <= 0 or start + size > len(image)
                or hashlib.sha256(image[start:start + size]).hexdigest() != entry['sha256']):
            raise NetworkError(f"The original bytes for saved function {entry['name']} are unavailable in this "
                               "analysis image. Keep the save and check the game copy or its code mappings.")

    def missing():
        rows = net.project_store(proj['id']).all(
            'SELECT rom_offset,vram,size,target FROM functions WHERE project_id=? AND rom_offset IS NOT NULL', proj['id'])
        actual = {}
        for row in rows:
            actual.setdefault((row['rom_offset'], row['vram']), []).append(row)
        # A recovered entry can later be split. Rediscovery finds the original whole body first; the saved
        # layout will split it again after this check, so a matching prefix is sufficient at the same mapping.
        return [entry for entry in entries if not any(
            row['size'] >= entry['size'] and isinstance(row['target'], bytes)
            and hashlib.sha256(row['target'][:entry['size']]).hexdigest() == entry['sha256']
            for row in actual.get((entry['rom_offset'], entry['vram']), []))]

    if pending := missing():
        if not scan:
            raise NetworkError(f"This save refers to {len(pending)} recovered function{'s' if len(pending) != 1 else ''} "
                               "that this project does not have. Restore the save from the original game copy "
                               "before synchronizing it; the saved files have not been changed.")
        log(f'Checking the game for {len(pending)} functions recorded after its original import.')
        net.find_missed_functions(slug, log)
        check()
        pending = missing()
    if pending:
        example = pending[0]['name']
        raise NetworkError(f"Couldn't restore {len(pending)} recovered function{'s' if len(pending) != 1 else ''} "
                           f"(including {example}). The save is unchanged. Recheck the code mappings in the original "
                           "project and try saving it again.")
