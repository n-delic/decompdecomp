"""Rotate paid conversations at completed-tool boundaries, never during a compiler run."""
import threading

from ..network import NetworkError

TOOL_LIMIT = 80
CHAR_LIMIT = 160000
NOTICE = 'Session context budget reached. Saved candidates, claims and experiments will continue in a fresh session. Finish this reply; do not request more tools.'


class Budget:
    def __init__(self, tools=TOOL_LIMIT, chars=CHAR_LIMIT):
        self.tool_limit = max(1,int(tools))
        self.char_limit = max(1000,int(chars))
        self.calls = self.chars = self.active = 0
        self.lock = threading.Lock()
        self.waiting = False

    def begin(self):
        with self.lock:
            if self.calls >= self.tool_limit or self.chars >= self.char_limit:
                raise NetworkError(NOTICE)
            self.active += 1

    def end(self, result):
        with self.lock:
            self.active -= 1
            self.calls += 1
            self.chars += len(result)

    def ready(self):
        with self.lock:
            return not self.active and (self.waiting or self.calls >= self.tool_limit or self.chars >= self.char_limit)


def current(net, agent, slug):
    return getattr(net,'_session_budgets',{}).get((getattr(agent,'id',None),slug))


def start(net, agent_id, slug, options):
    if not hasattr(net,'_session_budgets'):
        net._session_budgets={}
    if options.get('focus_id'):
        net._session_budgets.pop((agent_id,slug),None)
        return None  # A focused conversation compacts instead of rotating.
    budget=Budget(options.get('session_tool_limit',TOOL_LIMIT),options.get('session_char_limit',CHAR_LIMIT))
    net._session_budgets[agent_id,slug]=budget
    return budget
