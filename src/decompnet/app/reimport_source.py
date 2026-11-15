"""Find the retained original game, never an extracted/flattened analysis image."""
import hashlib
import json
from pathlib import Path

from .. import consoles
from ..network import NetworkError


def stored(net, slug):
    """Require a contained, unchanged original before offering picker-free reimport."""
    proj=net.project_row(slug);cfg=json.loads(proj['config'])
    root=Path(proj['root']).resolve()
    if cfg.get('kind')=='rom':
        rom=cfg.get('rom') or {}
        filename=rom.get('program') or rom.get('file')
        expected=rom.get('sha1')
    else:
        _,fp,_=net.forks.get(slug)
        game=(fp.manifest or {}).get('game') or {}
        filename=game.get('path');expected=game.get('sha1')
    if not filename or not expected:
        raise NetworkError('The saved original game cannot be verified. Choose your copy to reimport.')
    path=(root/filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise NetworkError('The original game is missing from this project folder. Choose your copy to reimport.')
    with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha1').hexdigest()
    if actual!=expected:
        raise NetworkError('The saved game differs from the original import. Choose your original copy to reimport.')
    if consoles.identify(path).id!=consoles.of(cfg).id:
        raise NetworkError('The saved game format cannot be used for this project. Choose your original copy to reimport.')
    return path
