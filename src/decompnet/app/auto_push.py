"""Opt-in threshold pushes, bound to the origin and branch the user enabled."""
import json
import time
import uuid

from ..network import NetworkError
from . import repository_git, repository_push


def status(net, proj):
    cfg = net.cfg(proj).get('auto_push', {})
    return {key: cfg.get(key, default) for key, default in
            (('enabled', False), ('threshold', 10), ('error', ''), ('last_push', ''), ('phase', ''))}


def _change(net, slug, change):
    # These are local Git preferences, not game settings. _update_cfg synchronizes
    # saved code and can launch a recheck; a network retry must never cause that.
    with net.store.tx() as db:
        row = db.execute('SELECT id,config FROM projects WHERE slug=?', (slug,)).fetchone()
        if row is None:raise NetworkError('This project is no longer open. Refresh the project list.')
        cfg = json.loads(row['config'])
        change(cfg)
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), row['id']))


def configure(net, slug, enabled, threshold=10, folder=''):
    if not isinstance(enabled, bool):raise NetworkError('Choose automatic pushing with a boolean value.')
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 1 <= threshold <= 10000:
        raise NetworkError('Choose a push threshold from 1 to 10,000 commits.')
    proj = net.store.one('SELECT * FROM projects WHERE slug=?', slug)
    if proj is None:raise NetworkError('Open a project before configuring automatic pushes.')
    settings = dict(enabled=enabled, threshold=threshold, token=uuid.uuid4().hex, error='', phase='')
    if enabled:
        root = repository_push.checkout(net, slug, folder)
        branch, ref = repository_push.destination(root)
        repository_git.git(root, 'rev-parse', '--verify', 'HEAD', error='Commit saved progress once before enabling automatic pushes.')
        settings.update(folder=str(root), branch=branch, ref=ref,
                        origin=repository_git.git(root, 'remote', 'get-url', 'origin'))
        previous = net.cfg(proj).get('auto_push', {})
        if previous.get('enabled') and any(previous.get(key) != settings[key] for key in ('folder', 'branch', 'ref', 'origin')):
            raise NetworkError('The branch or origin changed. Disable and re-enable automatic pushing for the intended destination.')
    _change(net, slug, lambda cfg: cfg.update(auto_push=settings))
    return status(net, net.store.one('SELECT * FROM projects WHERE slug=?', slug))


def process(net, slug, log=lambda message: None, check_cancel=lambda: None):
    """Check local refs first; only a threshold crossing may contact the remote. Retry failures once a minute."""
    proj = net.store.one('SELECT * FROM projects WHERE slug=?', slug)
    if proj is None:return
    settings = net.cfg(proj).get('auto_push', {})
    if not settings.get('enabled') or settings.get('next_check', 0) > time.time():return
    token = settings['token']
    def update(**values):
        def apply(cfg):
            current = cfg.get('auto_push', {})
            if current.get('token') == token:current.update(values)
        _change(net, slug, apply)
    def validate(root=None, branch=None, ref=None, url=None):
        check_cancel()
        fresh = net.store.one('SELECT * FROM projects WHERE slug=?', slug)
        current = net.cfg(fresh).get('auto_push', {}) if fresh else {}
        if not current.get('enabled') or current.get('token') != token:
            raise NetworkError('Automatic pushing was disabled or its settings changed.')
        if root is not None and (str(root), branch, ref, url) != (
                settings['folder'], settings['branch'], settings['ref'], settings['origin']):
            raise NetworkError('The branch or origin changed. Disable and re-enable automatic pushing for the intended destination.')
    try:
        root = repository_push.checkout(net, slug, settings['folder'])
        branch, ref = repository_push.destination(root)
        validate(root, branch, ref, repository_git.git(root, 'remote', 'get-url', 'origin'))
        local = repository_push.describe(root, branch, ref)
        if local['unpushed'] < settings['threshold']:
            update(error='', phase='', next_check=time.time()+5)
            return
        update(phase='Checking origin and pushing committed changes…')
        # The ordinary push path reserves the repository, refreshes origin and rejects rewrites.
        result = repository_push.run(net, slug, settings['folder'], log, validate=validate)
        update(error='', phase='', last_push=result['commit'], next_check=time.time()+5)
    except (NetworkError, OSError, ValueError, KeyError) as error:
        update(error=str(error) if isinstance(error, NetworkError) else
               'Automatic push could not read this checkout. Check its Git setup and retry.',
               phase='', next_check=time.time()+60)
