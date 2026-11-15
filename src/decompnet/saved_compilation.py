"""A saved judge may request fresh compilation, but never while owning its verdict lock."""
from concurrent.futures import Future

from . import compiler


class CompileAgain(Exception):
    """Internal control flow before scoring/recording; input text is never an error message."""
    def __init__(self, proj, header, source, fn, snapshot):
        super().__init__()
        self.inputs = (dict(proj), header, source, dict(fn))
        self.snapshot = snapshot

    def prepare(self, net, check_cancel):
        check_cancel()
        future = Future()
        try:
            with compiler.cancellable(check_cancel):
                result = net._compile(*self.inputs)
        except Exception as error:
            # Deliver failures through the ordinary attempt's existing handling,
            # only if its current input snapshot still agrees with this compile.
            future.set_exception(error)
        else:
            future.set_result(result)
        check_cancel()
        return self.snapshot, future
