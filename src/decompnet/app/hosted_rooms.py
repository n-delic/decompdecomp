"""Restore explicitly open rooms, retaining invite and member identity, never closed rooms."""
import json

from ..network import NetworkError


def restore(net, open_room, log=print):
    rows=net.store.all('SELECT slug FROM projects')
    errors={}
    for row in rows:
        slug=row['slug']
        cfg=json.loads(net.project_row(slug)['config']).get('collab') or {}
        if not cfg.get('open'):
            continue
        mode=cfg.get('mode','tunnel')
        try:
            if mode not in ('address','tunnel'):
                raise NetworkError('Unknown saved room connection mode. Choose how to share this room again.')
            if mode=='address' and not cfg.get('address'):
                raise NetworkError('The saved room address is missing. Enter it in Decompile together.')
            log(f'Reopening hosted room {slug}; preserving invite and members…')
            open_room(slug,mode,cfg.get('address',''),int(cfg.get('port') or 6481),log=log,keep=True)
            log(f'Room {slug} reopened.' + (' Quick-tunnel address changed; share the new link once. Use a fixed address for reconnects across restarts.' if mode=='tunnel' else ' Existing links can reconnect.'))
        except (NetworkError,OSError,RuntimeError,ValueError) as error:
            errors[slug]=str(error)
            log(f'Could not reopen room {slug}: {error}')
    return errors
