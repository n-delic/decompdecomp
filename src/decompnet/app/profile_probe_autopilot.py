"""The autopilot's saved-source pass runs the profile probe (profile_probe.py) for each open function with saved C.

Same shape as return_repair_autopilot: the pass already holds the function, has its Stop and runs only while the
project is being worked on. A small allowance of its own: one judge compile per planned profile and -O level, for the saved C and the draft, and
the two an exact pair is rechecked with. One probed before for this saved C and these build inputs is skipped from its
durable record. The search under a closer level is the Deep Permuter's.

`catch_up` is the same probe, search included, for a Deep Permuter pass over a function it would otherwise skip as
already finished: functions worked on before the probe learnt something get it too.
"""
import logging

from . import deep_permuter, opt_levels, profile_probe

# the plan and the -O levels for the saved C and for the machine draft, the draft under its own settings, their widened
# sources' share, the apply rechecks
PROBES = 2 * (profile_probe.MAX_PROBES + opt_levels.LEVELS_MAX) + 1 + 2 * profile_probe.RESERVE


def run(net, agent, proj, fn):
    if not fn['best_source'] or not profile_probe.supported(net, proj):
        return False
    stop = net.autopilots.get(proj['slug'], {}).get('stop')

    class Log:
        def __call__(self, text):
            logging.getLogger('decompdecomp').info('autopilot profile probe: %s', text)

        def progress(self, value):
            pass

        def check_cancel(self):
            if stop and stop.is_set():
                raise deep_permuter.SearchLimit('Stopped')
    search = deep_permuter.Search(net, 2, PROBES, Log(), fn)
    try:
        return profile_probe.run(search, agent, proj, fn, fn['best_source'], climb=False)
    except deep_permuter.SearchLimit:
        if stop and stop.is_set():
            raise
        return False


def catch_up(net, slug, function_id, minutes, variants, log) -> bool:
    """The probe alone for one finished function of a Deep Permuter pass, held as a pass holds it. True on a match."""
    from ..network import NetworkError
    from .progress import ProgressMoved
    proj = net.project_row(slug)
    row = net.project_store(slug).one('SELECT name FROM functions WHERE id=? AND project_id=?', function_id, proj['id'])
    if row is None:
        return False
    name = row['name']
    fn = net._function(name, slug)

    def say(line):
        log(f'{name}: {line}')
    say.check_cancel = log.check_cancel
    say.progress = lambda value: None
    agent, _ = net.local_agent(f'permuter-{function_id}')
    search = deep_permuter.Search(net, minutes, variants, say, fn)
    search.compile_pool = getattr(log, 'compile_pool', None)
    try:
        if not search._hold(agent, fn, count_turn=False):
            return False
        try:
            source = deep_permuter._candidate_source(fn, fn['best_score'])
            return profile_probe.run(search, agent, proj, fn, source)
        finally:
            search._let_go(agent, fn)
    except (deep_permuter.SearchLimit, NetworkError, ProgressMoved) as error:
        say(f'Profile probe: {error}')
        return False
