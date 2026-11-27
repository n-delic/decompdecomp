"""Small speculative local windows; publication remains on the adaptive search lane."""
from contextlib import ExitStack

from . import deep_compilation

MAXIMUM_DEPTH=8  # measured: 24 compile slots were 15% busy at a depth of two to four


class Window:
    def __init__(self,search,agent,proj,fn,fork):
        self.search,self.agent,self.proj,self.fn,self.fork=search,agent,proj,fn,fork
        self.stack=ExitStack()
        self.depth=3
        self.tickets=[]

    def ensure(self,sources):
        search=self.search;pool=getattr(search,'compile_pool',None)
        if not sources or self.fork:
            return
        if pool is None or not hasattr(search.net, 'compiled_ahead') or len(getattr(search, 'compiler_profiles', [])) > 1:
            if len(sources) > 1:
                search._prime_trials(self.agent, self.proj, self.fn, sources, self.fork)
            return
        ticket=getattr(search,'_ahead',{}).get(sources[0])
        if ticket is not None and not ticket.counted:return
        if self.tickets:
            useful=sum(ticket.counted for ticket in self.tickets)
            self.depth = min(MAXIMUM_DEPTH,self.depth+1) if useful == len(self.tickets) else max(1,self.depth-1)
        self.stack.close();self.stack=ExitStack()
        search.check()  # Closing a window charges every discarded running compile.
        count=min(self.depth,pool.count,max(0,search.limit-search.tested))
        if not count:return
        sources=list(dict.fromkeys(sources))[:count]
        search._prime_trials(self.agent,self.proj,self.fn,sources,self.fork)
        self.stack.enter_context(deep_compilation.chunk(search,self.agent,self.proj,self.fn,sources,self.fork))
        self.tickets=list(getattr(search,"_ahead",{}).values())

    def __enter__(self):return self
    def __exit__(self,*exc):return self.stack.__exit__(*exc)
