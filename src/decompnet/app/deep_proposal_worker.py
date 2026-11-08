"""Pure C proposal generation over private pipes; no database, room or compiler access."""
import json
import sys


def main():
    from decompnet import compile_timing
    from decompnet.app import draft_repairs
    for line in sys.stdin.buffer:
        try:
            args, kwargs = json.loads(line)
            labels = {}
            counts = compile_timing.Counts()
            with compile_timing.collect(counts):
                choices = draft_repairs.variants(*args, labels=labels, **kwargs)
            result = {'choices':choices,'labels':labels,'timings':counts.values}
        except Exception as error:
            result = {'error':type(error).__name__+': '+str(error)}
        sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode()+b'\n')
        sys.stdout.buffer.flush()


if __name__ == '__main__':main()
