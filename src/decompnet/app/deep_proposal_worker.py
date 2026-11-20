"""Pure C proposal generation over private pipes; no database, room or compiler access."""
import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace


def main():
    from decompnet import compile_timing
    from decompnet.app import draft_repairs
    from decompnet.app.repair_generation import PARTITION, cut_off, session
    cache_owner = SimpleNamespace()
    background = False
    for line in sys.stdin.buffer:
        try:
            args, kwargs = json.loads(line)
            if kwargs.pop('_background',False) and not background:
                from decompnet.app.workers import started_priority
                started_priority()
                background = True
            labels = {}
            counts = compile_timing.Counts()
            partition=kwargs.pop('_partition',None)
            generation = kwargs.pop('_generation',None)
            context = session(cache_owner, **generation) if generation is not None else nullcontext()
            token=PARTITION.set(partition)
            try:
                with compile_timing.collect(counts), context as state:
                    choices = draft_repairs.variants(*args, labels=labels, **kwargs)
            finally:PARTITION.reset(token)
            result = {'candidates':[(source,labels.get(source,'')) for source in choices],'timings':counts.values,'cut_off':cut_off()}
            if state is not None:
                result.update(generation=state.metrics, incomplete=state.incomplete)
        except Exception as error:
            result = {'error':type(error).__name__+': '+str(error)}
        sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode()+b'\n')
        sys.stdout.buffer.flush()


if __name__ == '__main__':main()
