"""The autopilot's saved-source pass runs the profile probe (profile_probe.py) for each open function with saved C.

Same shape as return_repair_autopilot: the pass already holds the function, has its Stop and runs only while the
project is being worked on. A small allowance of its own: one judge compile per planned profile and the two an exact pair
is rechecked with. A function the target points nowhere for costs nothing; one probed before for this saved C and these
build inputs is skipped from its durable record.
"""
import logging

from . import deep_permuter, profile_probe

# the plan for the saved C and for the machine draft, their widened sources' share, the apply rechecks
PROBES = 2 * profile_probe.MAX_PROBES + 2 * profile_probe.RESERVE


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
        return profile_probe.run(search, agent, proj, fn, fn['best_source'])
    except deep_permuter.SearchLimit:
        if stop and stop.is_set():
            raise
        return False
