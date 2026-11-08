"""Capture a stalled event loop from another thread without logging/database locks."""
import asyncio
import os
import sys
import threading
import time
from pathlib import Path


class Watchdog:
    def __init__(self, path, *, threshold=5, interval=1):
        self.path=Path(path)
        self.threshold,self.interval=threshold,interval
        self.last_beat=time.monotonic()
        self.last_dump=float('-inf')
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self.watch,daemon=True,name='event-loop-watchdog')

    async def pulse(self):
        while True:
            self.last_beat=time.monotonic()
            await asyncio.sleep(.25)

    def capture(self, elapsed):
        # Code locations only: never frame locals, arguments, source lines, SQL,
        # request URLs, tokens or game data. Replace one bounded local report.
        names={t.ident:t.name for t in threading.enumerate()}
        lines=[f'Event loop unresponsive for {elapsed:.1f}s; captured at {time.strftime("%Y-%m-%d %H:%M:%S %z")}\n']
        frames=sorted(sys._current_frames().items(),key=lambda item:item[0]!=threading.main_thread().ident)
        for ident,frame in frames[:256]:
            lines.append(f'\nThread {ident}: {names.get(ident,"unknown")}\n')
            for _ in range(24):
                if frame is None:break
                lines.append(f'  {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}\n')
                frame=frame.f_back
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp')
        temp.write_text(''.join(lines)[:512_000],encoding='utf-8')
        os.replace(temp,self.path)
        try:os.write(2,f'[decompdecomp] Server stalled for {elapsed:.0f}s. Thread locations saved to {self.path}\n'.encode())
        except OSError:pass

    def watch(self):
        while not self.stop.wait(self.interval):
            now=time.monotonic();elapsed=now-self.last_beat
            if elapsed>=self.threshold and now-self.last_dump>=30:
                self.last_dump=now
                try:self.capture(elapsed)
                except OSError:pass  # Disk/terminal failures cannot interfere with shutdown.

    def start(self):self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=.2)
