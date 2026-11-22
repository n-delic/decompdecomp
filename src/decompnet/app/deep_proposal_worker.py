"""Pure C proposal generation over private pipes; no database, room or compiler access."""
import base64
import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace


def ask(op, *args):
    """Put a question to the app (it owns the project's symbols and original bytes) and wait for the one-line answer."""
    sys.stdout.buffer.write(json.dumps({'rpc': {'op': op, 'args': list(args)}}, ensure_ascii=False).encode() + b'\n')
    sys.stdout.buffer.flush()
    return json.loads(sys.stdin.buffer.readline())['rpc_result']


def main():
    from decompnet import compile_timing, thread_cpu
    thread_cpu.start_worker('proposal')  # where the repair library's CPU goes: logs/thread-cpu-proposal-<pid>.log
    from decompnet.app import draft_repairs
    from decompnet.app.repair_generation import PARTITION, cut_off, session
    cache_owner = SimpleNamespace()
    background = False
    for line in sys.stdin.buffer:
        thread_cpu.count('requests')
        if line.startswith(b'P'):
            # a call from decompnet.app.offload: pickled in, pickled out (both ends run this code)
            from decompnet.app import offload
            thread_cpu.count('offload_requests')
            thread_cpu.count('offload_in_bytes', len(line))
            with thread_cpu.timed('offload'):
                answer = b'P' + base64.b64encode(offload.serve(line[1:])) + b'\n'
            thread_cpu.count('offload_out_bytes', len(answer))
            sys.stdout.buffer.write(answer)
            sys.stdout.buffer.flush()
            continue
        try:
            thread_cpu.count('request_bytes', len(line))
            with thread_cpu.timed('decode'):
                args, kwargs = json.loads(line)
            if kwargs.pop('_background',False) and not background:
                from decompnet.app.workers import started_priority
                started_priority()
                background = True
            call = kwargs.pop('_call', None)
            labels = {}
            counts = compile_timing.Counts()
            partition=kwargs.pop('_partition',None)
            generation = kwargs.pop('_generation',None)
            context = session(cache_owner, **generation) if generation is not None else nullcontext()
            token=PARTITION.set(partition)
            try:
                with compile_timing.collect(counts), context as state, thread_cpu.timed('generate'):
                    if call:
                        from decompnet.app import generation_calls
                        value = generation_calls.serve(call, args, kwargs, ask)
                        choices = []
                    else:
                        choices = draft_repairs.variants(*args, labels=labels, **kwargs)
            finally:PARTITION.reset(token)
            thread_cpu.count('calls' if call else 'proposal_requests')
            thread_cpu.count('candidates', len(choices))
            result = {'candidates':[(source,labels.get(source,'')) for source in choices],'timings':counts.values,'cut_off':cut_off()}
            if call:
                result['value'] = value
            if state is not None:
                result.update(generation=state.metrics, incomplete=state.incomplete)
        except Exception as error:
            result = {'error':type(error).__name__+': '+str(error)}
        with thread_cpu.timed('encode'):
            answer = json.dumps(result,ensure_ascii=False).encode()+b'\n'
        thread_cpu.count('result_bytes', len(answer))
        with thread_cpu.timed('write'):
            sys.stdout.buffer.write(answer)
            sys.stdout.buffer.flush()


if __name__ == '__main__':main()
