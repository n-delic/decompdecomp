"""Checked bulk naming for ROM projects, with expected addresses guarding stale audits."""
import threading

from ..network import NetworkError
from ..rom.symbols import IDENT

_LOCK = threading.Lock()
from .function_names import check_rename


def prepare(net, slug, rows):
    proj = net.project_row(slug)
    if not net.is_rom(proj) or net.fork_slug(slug) or slug in net.links:
        raise NetworkError('Batch naming requires a local ROM project; use individual renames for imported projects or rooms.')
    if not isinstance(rows, list) or not rows or len(rows) > 10000:
        raise NetworkError('Supply between 1 and 10,000 name/address entries.')
    pairs, old_names, new_names = [], set(), set()
    symbols = net.symbols(proj['id'])
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'old', 'new', 'address'}:
            raise NetworkError('Each name entry needs old, new and address.')
        old, new, address = row['old'], row['new'], row['address']
        if not isinstance(old, str) or not isinstance(new, str) or type(address) is not int:
            raise NetworkError('Names must be strings and addresses integers.')
        old, new = old.strip(), new.strip()
        if not IDENT.fullmatch(old) or not IDENT.fullmatch(new):
            raise NetworkError('Names must be C identifiers.')
        check_rename(old, new)
        if old in old_names or new in new_names:
            raise NetworkError('Each old and new name must occur only once in a batch.')
        old_names.add(old); new_names.add(new)
        if symbols.resolve(old) != address:
            raise NetworkError(f'{old} no longer names 0x{address:08X}. Refresh the naming audit.')
        taken = symbols.resolve(new)
        if taken is not None and taken != address:
            raise NetworkError(f'{new} already names another address. Resolve the conflict before renaming.')
        if old != new:pairs.append((old, new))
    return pairs


def apply(net, slug, rows, message, log):
    with _LOCK:
        return _apply(net, slug, rows, message, log)


def _apply(net, slug, rows, message, log):
    pairs = prepare(net, slug, rows)
    if not pairs:return {'renamed': 0, 'message': 'All supplied names are already current.'}
    agent, _ = net.local_agent('you')
    result = net.rename_symbols(agent, slug, pairs, message, progress=getattr(log, 'progress', None))
    if not result.startswith('✅'):raise NetworkError(result)
    net.progress.sync(slug, force=True)
    return {'renamed': len(pairs), 'message': result}
