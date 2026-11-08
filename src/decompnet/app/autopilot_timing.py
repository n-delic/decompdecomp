"""Per-run cumulative worker time; concurrent stage totals may exceed elapsed time."""
import threading
from contextlib import contextmanager

from .. import compile_timing, request_timing


class Timings:
    def __init__(self):
        self.lock=threading.Lock()
        self.values={}

    def add(self,name,value):
        if name not in {'generation','configuration','setup','execute','queue','link','scoring',
                        'publication','claim_write','history_write','database_reads','configuration_hits','exact_score_skips'}:return
        with self.lock:self.values[name]=self.values.get(name,0.)+value

    def snapshot(self):
        with self.lock:values=dict(self.values)
        return {name:round(sum(values.get(key,0.) for key in keys),4) for name,keys in {
            'generation':('generation',),'preparation':('configuration','setup'),
            'compile':('execute',),'compiler_wait':('queue',),'link':('link',),'scoring':('scoring',),
            'database_reads':('database_reads',),
            'database_writes':('publication','claim_write','history_write'),
            'configuration_hits':('configuration_hits',),'exact_score_skips':('exact_score_skips',)}.items()}


@contextmanager
def database_reads():
    with request_timing.capture() as measured:
        try:yield
        finally:compile_timing.add('database_reads',measured['sql'])
