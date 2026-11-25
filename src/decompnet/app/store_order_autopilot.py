"""The autopilot's saved-source pass climbs the order of a long block of independent stores (store_order_climb.py).

Same shape as profile_probe_autopilot: the pass already holds the function, has its Stop and runs only while the project
is being worked on. A small allowance of its own (one judge compile per step, `ALLOWANCE` at most); a saved C that is
not close, has no block of eight stores or was climbed before for these build inputs costs nothing.
"""
import logging

from . import deep_permuter, store_order_climb

ALLOWANCE = store_order_climb.BUDGET + store_order_climb.RESERVE


def run(net, agent, proj, fn):
    source = fn['best_source']
    fork = net.fork_slug(proj['slug'])
    header = net.forks.permuter_header(fork, fn['name']) if fork else (net._context(proj['id'], fn['unit']) or {}).get('header', '')
    if not store_order_climb.eligible(net, proj, fn, source, header):
        return False
    stop = net.autopilots.get(proj['slug'], {}).get('stop')

    class Log:
        def __call__(self, text):
            logging.getLogger('decompdecomp').info('autopilot store-order climb: %s', text)

        def progress(self, value):
            pass

        def check_cancel(self):
            if stop and stop.is_set():
                raise deep_permuter.SearchLimit('Stopped')
    search = deep_permuter.Search(net, 5, ALLOWANCE, Log(), fn)
    try:
        return store_order_climb.run(search, agent, proj, fn, source, header, fork) == 0
    except deep_permuter.SearchLimit:
        if stop and stop.is_set():
            raise
        return False
