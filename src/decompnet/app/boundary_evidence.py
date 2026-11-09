"""Original-image evidence for human and agent review, independent of candidate similarity."""
import json

from .. import arch
from ..network import NetworkError
from . import boundary_repairs as repairs

EDGE_WORD_LIMIT = 65536
EDGE_LIMIT = 80
WINDOW_WORDS = 8


def _project_inputs(proj):
    # Reporting token usage is not a change to original instructions or their mapping.
    values = dict(proj)
    cfg = json.loads(values['config']); cfg.pop('spend', None)
    values['config'] = cfg
    return values


def revision(net, proj, name):
    """Original layout and relevant entry states stay stable while unrelated agents improve C."""
    rows = [dict(r) for r in net.project_store(proj['id']).all('SELECT id,name,vram,rom_offset,size,target,unit,status,meta '
            'FROM functions WHERE project_id=? ORDER BY id', proj['id'])]
    selected = next((r for r in rows if r['name'] == name), None)
    for row in rows:
        meta = json.loads(row.pop('meta') or '{}')
        row['gone'] = bool(meta.get('gone')); row['boundary_override'] = bool(meta.get('boundary_override'))
        neighbour = selected and row['unit'] == selected['unit'] and row['vram'] is not None and selected['vram'] is not None and (
            row['vram'] + row['size'] == selected['vram'] or row['vram'] == selected['vram'] + selected['size'])
        if row['name'] != name and not neighbour: row.pop('status')
    symbols = [dict(r) for r in net.project_store(proj['id']).all('SELECT * FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    return repairs._digest([_project_inputs(net.project_row(proj['slug'])), rows, symbols])


def assembly(reader, image, owner, start, end, points, big_endian):
    """Keep both sides of every disputed boundary visible even for a large function."""
    offsets = set()
    for point in points:
        offset = owner.rom_start + point - owner.vram
        offsets.update(range(max(owner.rom_start, offset - WINDOW_WORDS * 4),
                             min(owner.rom_end, len(image)-3, offset + WINDOW_WORDS * 4), 4))
    lines = []
    for offset in sorted(offsets):
        address = owner.vram + offset - owner.rom_start
        line = reader(int.from_bytes(image[offset:offset+4], 'big' if big_endian else 'little'), address)
        prior = reader(int.from_bytes(image[offset-4:offset], 'big' if big_endian else 'little'), address-4) if offset-4 >= owner.rom_start else None
        lines.append({**line, 'inside': start <= address < end,
                      'delay_slot_of': address-4 if prior and prior['delay_slot'] else None})
    return lines


def read(net, slug, name):
    """Explain suspicious entries without claiming them or changing the detected layout."""
    proj = repairs._supported(net, slug)
    stamp = revision(net, proj, name)
    fn = dict(net._function(name, slug))
    image, segments = repairs._image(net, proj)
    start = fn['vram']; offset = fn['rom_offset']; end = start + fn['size'] if start is not None else None
    owners = [s for s in segments if offset is not None and s.rom_start <= offset < s.rom_end
              and s.vram + offset - s.rom_start == start and offset + fn['size'] <= s.rom_end
              and 0 <= s.rom_start < s.rom_end <= len(image)]
    if len(owners) != 1 or image[offset:offset+fn['size']] != fn['target']:
        raise NetworkError('Restore one unambiguous original image mapping before investigating this boundary.')
    if fn.get('gone_mark') is not None:
        raise NetworkError('This entry is archived. Undo its repair before investigating it.')
    owner = owners[0]; reader = arch.get(proj['arch']).boundary_instruction
    if reader is None:
        raise NetworkError('This processor needs a boundary evidence reader (ROADMAP.md).')
    rows = [r for r in repairs._rows(net, proj) if r['gone_mark'] is None
            and r['rom_offset'] is not None and r['vram'] is not None
            and owner.rom_start <= r['rom_offset'] < r['rom_offset'] + r['size'] <= owner.rom_end
            and r['vram'] - owner.vram == r['rom_offset'] - owner.rom_start]
    previous = [r for r in rows if r['vram'] + r['size'] == start and r['id'] != fn['id'] and r['unit'] == fn['unit']]
    following = [r for r in rows if r['vram'] == end and r['id'] != fn['id'] and r['unit'] == fn['unit']]
    neighbours = previous + following
    points = [start, end] + [r['vram'] for r in previous]
    lines = assembly(reader, image, owner, start, end, points, proj['big_endian'])
    last = next((line for line in lines if line['address'] == end - 4), None)
    before = next((line for line in lines if line['address'] == end - 8), None)
    diagnosis = ('The final instruction requires the next instruction as its delay slot.' if last and last['delay_slot'] else
                 'This entry contains a single nop; it may be padding or an intentional instruction.' if fn['target'] == bytes(4) else
                 'The current end has no recognised terminal transfer and completed delay slot.' if repairs.reason(net.console(proj), fn) else
                 'No incomplete-end warning was detected. Review other evidence before changing this entry.')
    evidence = [{'address': end - 4, 'text': diagnosis}]
    if before and before['terminal'] and not (last and last['delay_slot']):
        evidence.append({'address': end - 8, 'text': 'A terminal transfer precedes the last instruction; review its delay slot and other paths.'})
    if previous:
        evidence.append({'address': start, 'text': 'There is an adjacent preceding entry. Adjacency alone does not establish shared ownership.'})
    edges = []; words = 0; limited = False
    entry_points = {r['vram'] for r in neighbours}
    # Scan mapped original bytes, not candidate disassembly. Unknown indirect edges remain unknown.
    for row in sorted(rows, key=lambda r: (r['id'] != fn['id'], abs(r['vram'] - start))):
        for at in range(row['rom_offset'], row['rom_offset'] + row['size'] - 3, 4):
            if words >= EDGE_WORD_LIMIT or len(edges) >= EDGE_LIMIT:
                limited = True; break
            address = owner.vram + at - owner.rom_start
            ins = reader(int.from_bytes(image[at:at+4], 'big' if proj['big_endian'] else 'little'), address)
            words += 1
            dest = ins['target']
            if dest is not None and (dest in entry_points or start - 32 <= dest <= end + 32 or start <= address < end and not start <= dest < end):
                edges.append({'from': address, 'to': dest, 'function': row['name'],
                              'kind': 'call' if ins['call'] else 'branch', 'instruction': ins['instruction']})
        if limited: break
    symbols = [dict(r) for r in net.project_store(proj['id']).all('SELECT name,address,size,kind FROM symbols WHERE project_id=? '
                 'AND address>=? AND address<=? ORDER BY address,name LIMIT 80', proj['id'], start-32, end+32)]
    if stamp != revision(net, proj, name) or _project_inputs(net.project_row(slug)) != _project_inputs(proj):
        raise NetworkError('The project changed while reading boundary evidence. Read the evidence again.')
    return {'function': name, 'start': start, 'end': end, 'size': fn['size'], 'diagnosis': diagnosis,
            'segment': {'name': owner.name, 'start': owner.vram, 'end': owner.vram + owner.rom_end - owner.rom_start},
            'neighbours': [{'name': r['name'], 'start': r['vram'], 'end': r['vram']+r['size'], 'status': r['status']} for r in neighbours],
            'evidence': evidence, 'assembly': lines, 'edges': edges, 'symbols': symbols,
            'limits': f'Scanned {words} mapped instructions in this segment; ' +
                      ('scan limit reached. ' if limited else '') +
                      'Indirect calls, raw gaps, other segments and unmapped overlays are not resolved. No observed caller is not proof of padding. Symbols may be generated.',
            'revision': stamp, 'image': repairs._digest(image)}
