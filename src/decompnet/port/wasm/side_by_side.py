"""Side by side: the port and the console (mupenRE) running the same game in one page, one pad for both.

The left is the port as it plays (its live view, proxied); the right is the console: the reversing emulator runs the
user's own ROM live (consoles/n64/emulator/live.py), and each graphics task it hands over is drawn with the port's
renderer (port/frame_compare.py), the console's RAM carried from one frame to the next, so a difference between the
halves is the game running differently (the port's code, its host or its input), not a different renderer. The page's
keyboard or gamepad is sent to both at once.

Free-running, both play in real time and drift apart (the port loads in no time and never drops a frame; the console
does), and each half shows its own frame count. With --lockstep (the default when this starts the port) the viewer is
the clock: the port (PORT_LOCKSTEP and PORT_VTIME) and the emulator (the shared file's gate) each park before every
retrace, the viewer reads both RAMs there, says which regions differ and which are new at this retrace, sets the one
pad for both and lets them go on one retrace. A game that times its menus in retraces then stays on the same screen on
both sides, and the first retrace at which the RAMs part is on the page. Pause, Step (one retrace) and the rate are
in the page.

    python -m decompnet.port.wasm.side_by_side ROM (--port-url http://127.0.0.1:PORT/ | --program build/game)
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ...network import NetworkError

PAGE = r"""<!doctype html><html><head><meta charset=utf-8><title>Port | Console</title><style>
html,body{margin:0;height:100%;background:#111;color:#bbb;font:13px system-ui}
.row{display:flex;gap:8px;justify-content:center;margin:12px 8px 0}
.side{flex:1;min-width:0;text-align:center}
canvas{display:block;width:100%;image-rendering:pixelated;background:#000;aspect-ratio:4/3}
h2{margin:0 0 6px;font-size:14px;font-weight:600}p{text-align:center;margin:6px}
</style></head><body><div class=row>
<div class=side><h2>Port</h2><canvas id=p width=320 height=240></canvas><p id=ps></p></div>
<div class=side><h2>Console (mupenRE)</h2><canvas id=e width=320 height=240></canvas><p id=es></p></div></div>
<p id=hud hidden><button id=run>Pause</button> <button id=step>Step</button> <span id=vi></span></p>
<pre id=diff hidden style="margin:6px auto;max-width:900px;text-align:left;color:#9c9;font:12px ui-monospace,monospace"></pre>
<p>Arrows steer &middot; X: A &middot; Z: B &middot; Enter: Start &middot; Space: Z &middot; Q/W: L/R &middot; I/J/K/L: C buttons &middot; or a gamepad. Both sides get the same pad.</p>
<script>
const keys={ArrowUp:0x0800,ArrowDown:0x0400,ArrowLeft:0x0200,ArrowRight:0x0100,KeyX:0x8000,KeyZ:0x4000,KeyC:0x4000,
Enter:0x1000,Space:0x2000,KeyQ:0x0020,KeyW:0x0010,KeyI:0x0008,KeyK:0x0004,KeyJ:0x0002,KeyL:0x0001};
const pad={0:0x8000,2:0x4000,1:0x4000,3:0x4000,9:0x1000,4:0x0020,5:0x0010,6:0x2000,7:0x2000,12:0x0800,13:0x0400,14:0x0200,15:0x0100};
let down=new Set(),sent='';
function send(){let b=0;for(const k of down)b|=keys[k];
let x=(down.has('ArrowRight')?80:0)-(down.has('ArrowLeft')?80:0),y=(down.has('ArrowUp')?80:0)-(down.has('ArrowDown')?80:0);
for(const gp of (navigator.getGamepads?navigator.getGamepads():[])){if(!gp)continue;
gp.buttons.forEach((v,i)=>{if((v.pressed||v.value>0.5)&&pad[i])b|=pad[i];});
const ax=gp.axes[0]||0,ay=gp.axes[1]||0,cx=gp.axes[2]||0,cy=gp.axes[3]||0;
if(Math.abs(ax)>0.15)x=Math.round(ax*80);if(Math.abs(ay)>0.15)y=Math.round(-ay*80);
if(cx>0.5)b|=0x0001;if(cx<-0.5)b|=0x0002;if(cy>0.5)b|=0x0004;if(cy<-0.5)b|=0x0008;}
const q='b='+b+'&x='+x+'&y='+y;if(q!=sent){sent=q;fetch('/input?'+q,{method:'POST'}).catch(()=>{});}}
setInterval(send,16);
addEventListener('keydown',e=>{if(e.code in keys){e.preventDefault();if(!down.has(e.code)){down.add(e.code);send();}}});
addEventListener('keyup',e=>{if(e.code in keys){e.preventDefault();down.delete(e.code);send();}});
addEventListener('blur',()=>{down.clear();send();});
function side(path,canvas,label){const g=canvas.getContext('2d');let last=-1,shown=0,t0=performance.now();
async function loop(){try{const r=await fetch(path+'?after='+last);const b=new Uint8Array(await r.arrayBuffer());
if(b.length>12){const d=new DataView(b.buffer),w=d.getUint32(0,true),h=d.getUint32(4,true);last=d.getUint32(8,true);
if(canvas.width!=w||canvas.height!=h){canvas.width=w;canvas.height=h;}const im=g.createImageData(w,h),p=im.data;
for(let i=0,j=12;i<w*h;i++,j+=3){p[i*4]=b[j];p[i*4+1]=b[j+1];p[i*4+2]=b[j+2];p[i*4+3]=255;}g.putImageData(im,0,0);shown++;
const now=performance.now();if(now-t0>1000){label.textContent='frame '+last+' · '+Math.round(shown*1000/(now-t0))+' a second';shown=0;t0=now;}}}
catch(e){await new Promise(r=>setTimeout(r,500));}requestAnimationFrame(loop);}loop();}
let running=true;
async function status(){try{const r=await fetch('/state');const d=await r.json();
if(d.lockstep){document.getElementById('hud').hidden=false;document.getElementById('diff').hidden=false;
document.getElementById('run').textContent=d.running?'Pause':'Resume';running=d.running;
document.getElementById('vi').textContent='retrace '+d.vi+' · '+d.blocks+' differing 256-byte blocks'+(d.first_vi?' · first seen at retrace '+d.first_vi:'');
document.getElementById('diff').textContent=(d.new.length?'new at this retrace:\n'+d.new.join('\n')+'\n\n':'')+'regions (port vs console):\n'+d.regions.join('\n')+(d.note?'\n\n'+d.note:'');}}catch(e){}setTimeout(status,250);}
status();
document.getElementById('run').onclick=()=>fetch('/control?run='+(running?0:1),{method:'POST'});
document.getElementById('step').onclick=()=>fetch('/control?step=1',{method:'POST'});
side('/port/frame',document.getElementById('p'),document.getElementById('ps'));
side('/emu/frame',document.getElementById('e'),document.getElementById('es'));
</script></body></html>"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def to_rgb(pixels: list) -> bytes:
    """Pixels as (r, g, b) tuples to the live page's frame bytes."""
    return bytes(c for p in pixels for c in p)


def pick_image(images: dict) -> tuple[int, int, list] | None:
    """The picture the console shows from what a frame drew: the largest colour image (a frame draws the screen's,
    sometimes also a smaller buffer it renders to first)."""
    best = None
    for width, lines, pixels in images.values():
        if best is None or width * lines >= best[0] * best[1]:
            best = (width, lines, pixels)
    return best


class ConsoleView:
    """The console's side: reads each new frame from the shared file and draws it with the port's renderer."""

    def __init__(self, shared, work: Path):
        from .. import frame_compare as fc
        self.fc, self.shared, self.work = fc, shared, Path(work)
        self.program = fc.build(self.work)
        self.renderer = fc.Renderer(self.program, self.work, "live")
        self.carry: dict[int, bytes] = {}
        self.lock = threading.Condition()
        self.frame: bytes | None = None
        self.number = 0
        self.game_frames = 0
        self.stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        dump = self.work / "console.ram"
        last = -1
        while not self.stop:
            snap = self.shared.snapshot()
            if snap is None or snap[0] == last:
                time.sleep(0.005)
                continue
            frames, _vi, task, ram = snap
            last = frames
            dump.write_bytes(ram)
            dump.with_name(dump.name + ".task").write_bytes(task)
            try:
                drawn = self.fc.frames(self.program, dump, self.work, carry=self.carry, renderer=self.renderer)
            except Exception:  # noqa: BLE001  a frame that can't be drawn (a list half built) is skipped, as in Check pictures
                continue
            image = pick_image(drawn)
            if image is None:
                continue
            width, lines, pixels = image
            rgb = to_rgb(pixels)
            with self.lock:
                self.number += 1
                self.game_frames = frames
                self.frame = struct.pack("<III", width, lines, self.number) + rgb
                self.lock.notify_all()

    def wait(self, after: int, timeout: float = 1.0) -> bytes | None:
        """The newest frame if it isn't number `after`, waiting up to `timeout` for one."""
        with self.lock:
            self.lock.wait_for(lambda: self.frame is not None and self.number != after, timeout)
            return self.frame if self.frame is not None and self.number != after else None

    def close(self) -> None:
        self.stop = True
        self.renderer.close()


BLOCK = 256  # the unit the RAM comparison reports in


def differing_blocks(a: bytes, b: bytes, ranges: list[tuple[int, int]] | None = None) -> list[int]:
    """The offsets of the BLOCK-byte blocks where two RAM dumps differ, over the shorter of them, or only inside
    `ranges` (RAM offsets, start to end): the port's RAM isn't laid out like the console's everywhere (its exception
    vectors, its memory size, its stacks), so a person looking for a game's own difference names the game's data."""
    n = min(len(a), len(b)) // BLOCK * BLOCK
    spans = [(max(0, lo) // BLOCK * BLOCK, min(n, hi)) for lo, hi in ranges] if ranges else [(0, n)]
    return [i for lo, hi in spans for i in range(lo, hi - BLOCK + 1, BLOCK) if a[i:i + BLOCK] != b[i:i + BLOCK]]


def regions(blocks: list[int], a: bytes, b: bytes, limit: int = 12) -> list[str]:
    """Runs of differing blocks as text lines: the address range and the first word that differs in each, with both values."""
    out, i = [], 0
    while i < len(blocks) and len(out) < limit:
        j = i
        while j + 1 < len(blocks) and blocks[j + 1] == blocks[j] + BLOCK:
            j += 1
        start, end = blocks[i], blocks[j] + BLOCK
        w = next(o for o in range(start, end, 4) if a[o:o + 4] != b[o:o + 4])
        out.append(f"{0x80000000 + start:08X}-{0x80000000 + end:08X}  first {0x80000000 + w:08X}: "
                   f"{a[w:w + 4].hex().upper()} vs {b[w:w + 4].hex().upper()}")
        i = j + 1
    return out


class Lockstep:
    """The viewer as the clock: both sides park before each retrace, their RAMs are compared there, then the one pad is
    given to both and they go on one retrace (docs/PORTING.md, "Side by side, live")."""

    def __init__(self, session: "Session", rate: float = 60.0, offset: int = 0, ranges: list | None = None):
        self.session, self.interval, self.offset, self.ranges = session, 1.0 / rate, offset, ranges
        self.running, self.stepping, self.stop = True, False, False
        self.pad, self.sent_pad = (0, 0, 0), None
        self.held: dict[int, int] = {}  # button bit: retraces it stays down at least (a tap shorter than a game's poll is still seen)
        self.vi, self.blocks, self.first_vi, self.new, self.regions, self.note = 0, 0, 0, [], [], "starting"
        self.previous: set[int] = set()
        self.lock = threading.Condition()
        self.frame: bytes | None = None
        self.number = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _port(self, path: str, post: bool = False, timeout: float = 10) -> bytes:
        request = urllib.request.Request(self.session.port_url + path, method="POST" if post else "GET")
        with urllib.request.urlopen(request, timeout=timeout) as r:
            return r.read()

    def _wait(self, n: int, give_up: float = 30.0):
        """Both RAMs at retrace n, once both sides have parked there: (port, console), or None if one didn't."""
        end = time.monotonic() + give_up
        port = emu = None
        while time.monotonic() < end and not self.stop:
            if port is None:
                try:
                    port = self._port(f"/ram?vi={n}") or None
                except OSError:
                    port = None
            if emu is None:
                emu = self.session.shared.retrace_ram(n + self.offset)
            if port and emu:
                return port, emu
            time.sleep(0.003)
        return None

    TAP = 4  # retraces

    def press(self, buttons: int, x: int, y: int) -> None:
        """The page's pad, kept until the next retrace; a button pressed since the last one stays down for TAP retraces."""
        for bit in range(16):
            if buttons >> bit & 1 and not self.pad[0] >> bit & 1:
                self.held[bit] = self.TAP
        self.pad = (buttons, x, y)

    def _apply_pad(self) -> None:
        buttons = self.pad[0]
        for bit in list(self.held):
            buttons |= 1 << bit
            self.held[bit] -= 1
            if self.held[bit] <= 0:
                del self.held[bit]
        pad = (buttons, self.pad[1], self.pad[2])
        if pad != self.sent_pad:
            self.sent_pad = pad
            self.session.shared.set_pad(*pad)
            try:
                self._port("/input?b=%d&x=%d&y=%d" % pad, post=True, timeout=2)
            except OSError:
                pass

    def _compare(self, n: int, port: bytes, emu: bytes) -> None:
        blocks = differing_blocks(port, emu, self.ranges)
        now = set(blocks)
        new = sorted(now - self.previous)
        self.previous = now
        self.vi, self.blocks = n, len(blocks)
        if blocks and not self.first_vi:
            self.first_vi = n
        self.new = regions(new, port, emu, 6)
        self.regions = regions(blocks, port, emu)

    def _run(self) -> None:
        n = 1
        while not self.stop:
            if not (self.running or self.stepping):
                time.sleep(0.02)
                continue
            began = time.monotonic()
            both = self._wait(n)
            if both is None:
                self.note = f"waiting: retrace {n} not reached by both sides yet (the port needs PORT_LOCKSTEP=1 PORT_VTIME=1)"
                continue
            self.note = ""
            self._compare(n, *both)
            try:
                frame = self._port("/frame?after=-1")
            except OSError:
                frame = b""
            if len(frame) > 12 and (self.frame is None or frame[12:] != self.frame[12:]):
                with self.lock:
                    self.number += 1
                    self.frame = frame[:8] + struct.pack("<I", self.number) + frame[12:]
                    self.lock.notify_all()
            self._apply_pad()
            self.stepping = False
            try:  # let both go on to the next retrace
                self._port(f"/gate?to={n}", post=True)
            except OSError:
                continue
            self.session.shared.set_gate(n + self.offset)
            n += 1
            left = self.interval - (time.monotonic() - began)
            if left > 0 and self.running:
                time.sleep(left)

    def wait(self, after: int, timeout: float = 1.0) -> bytes | None:
        with self.lock:
            self.lock.wait_for(lambda: self.frame is not None and self.number != after, timeout)
            return self.frame if self.frame is not None and self.number != after else None

    def state(self) -> dict:
        return {"lockstep": True, "running": self.running, "vi": self.vi, "blocks": self.blocks, "first_vi": self.first_vi,
                "new": self.new, "regions": self.regions, "note": self.note}

    def close(self) -> None:
        self.stop = True


class Session:
    """The viewer: a page on 127.0.0.1 showing the port (proxied from its live view) beside the console, one pad."""

    def __init__(self, rom: Path, port_url: str, work: Path | None = None, *, expansion_pak: bool = False,
                 lockstep: bool = False, rate: float = 60.0, offset: int = 0, ranges: list | None = None):
        from ...consoles.n64.emulator import live
        self.port_url = port_url.rstrip("/")
        self._tmp = tempfile.TemporaryDirectory(prefix="decomp-side-by-side-") if work is None else None
        self.work = Path(work or self._tmp.name)
        self.work.mkdir(parents=True, exist_ok=True)
        self.shared = live.Shared(self.work / "live.bin", expansion_pak, lockstep)
        self.emulator = live.start(rom, self.shared, expansion_pak=expansion_pak)
        self.console = ConsoleView(self.shared, self.work)
        self.lockstep = Lockstep(self, rate, offset, ranges) if lockstep else None
        session = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body: bytes, kind: str = "application/octet-stream", status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def do_GET(self):
                url = urlparse(self.path)
                after = int((parse_qs(url.query).get("after") or ["-1"])[0])
                if url.path == "/":
                    self._send(PAGE.encode(), "text/html; charset=utf-8")
                elif url.path == "/emu/frame":
                    self._send(session.console.wait(after) or b"")
                elif url.path == "/state":
                    state = session.lockstep.state() if session.lockstep else {"lockstep": False}
                    self._send(json.dumps(state).encode(), "application/json")
                elif url.path == "/port/frame" and session.lockstep:  # the viewer fetches it once a retrace
                    self._send(session.lockstep.wait(after) or b"")
                elif url.path == "/port/frame":
                    try:
                        with urllib.request.urlopen(f"{session.port_url}/frame?after={after}", timeout=5) as r:
                            self._send(r.read())
                    except OSError:
                        self._send(b"")
                else:
                    self._send(b"", status=404)

            def do_POST(self):
                url = urlparse(self.path)
                q = parse_qs(url.query)
                if url.path == "/control" and session.lockstep:
                    if "run" in q:
                        session.lockstep.running = q["run"][0] == "1"
                    if "step" in q:
                        session.lockstep.running, session.lockstep.stepping = False, True
                    return self._send(b"", "text/plain")
                if url.path != "/input":
                    return self._send(b"", status=404)
                buttons, x, y = (int((q.get(k) or ["0"])[0]) for k in ("b", "x", "y"))
                if session.lockstep:  # latched: both sides get it at the next retrace, never mid-step
                    session.lockstep.press(buttons, x, y)
                    return self._send(b"", "text/plain")
                session.shared.set_pad(buttons, x, y)
                try:
                    urllib.request.urlopen(urllib.request.Request(
                        f"{session.port_url}/input?b={buttons}&x={x}&y={y}", method="POST"), timeout=2).close()
                except OSError:
                    pass
                self._send(b"", "text/plain")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        if self.lockstep:
            self.lockstep.close()
        self.server.shutdown()
        self.console.close()
        if self.emulator.poll() is None:
            self.emulator.kill()
        self.shared.close()
        if self._tmp:
            self._tmp.cleanup()


def start_port(program: Path, rom: Path, work: Path, lockstep: bool = False) -> tuple[subprocess.Popen, str]:
    """A built port running with its live view on a free port, as Play starts it; (process, its address)."""
    port = _free_port()
    env = {**os.environ, "PORT_LIVE": str(port), "PORT_SAVE": str(work / "game.eep")}
    for key in ("PORT_RT64", "PORT_WINDOW", "PORT_RECOMP_UI"):
        env.pop(key, None)
    if lockstep:  # parked before each retrace until the viewer says go; virtual time, so the game is idle there
        env.update(PORT_LOCKSTEP="1", PORT_VTIME="1")
    proc = subprocess.Popen([str(program), str(rom), "0"], cwd=work, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(100):
        if proc.poll() is not None:
            raise NetworkError("The port stopped as it started.")
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return proc, url
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise NetworkError("The port didn't start.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rom", type=Path)
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--port-url", help="the address of a port already running with PORT_LIVE (Play's)")
    where.add_argument("--program", type=Path, help="a built port to start (its build/game)")
    ap.add_argument("--expansion-pak", action="store_true", help="8 MB of RAM, as the game's console had")
    ap.add_argument("--lockstep", action=argparse.BooleanOptionalAction, default=None,
                    help="advance both one retrace at a time and compare their RAM (the default when --program starts the port; "
                         "a port given by --port-url needs PORT_LOCKSTEP=1 PORT_VTIME=1)")
    ap.add_argument("--rate", type=float, default=60.0, help="retraces a second in lockstep (50 for a PAL game)")
    ap.add_argument("--emulator-offset", type=int, default=0, help="lockstep: the console's retrace number minus the port's")
    ap.add_argument("--range", action="append", default=[], metavar="START-END",
                    help="lockstep: compare only this RAM range (hex addresses, repeatable): the game's data, not the port's "
                         "own layout (exception vectors, memory size, stacks) where the two always differ")
    ap.add_argument("--no-open", action="store_true", help="don't open the page in the browser")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="decomp-side-by-side-") as tmp:
        port_proc = None
        url = args.port_url
        lockstep = bool(args.program) if args.lockstep is None else args.lockstep
        ranges = [tuple(int(x, 16) & 0x1FFFFFFF for x in r.split("-")) for r in args.range]
        if args.program:
            port_proc, url = start_port(args.program.resolve(), args.rom.resolve(), Path(tmp), lockstep)
        session = Session(args.rom.resolve(), url, Path(tmp), expansion_pak=args.expansion_pak, lockstep=lockstep,
                          rate=args.rate, offset=args.emulator_offset, ranges=ranges)
        print(f"side by side at {session.url}  (Ctrl-C to stop)", flush=True)
        if not args.no_open:
            webbrowser.open(session.url)
        try:
            while session.emulator.poll() is None and (port_proc is None or port_proc.poll() is None):
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            session.close()
            if port_proc and port_proc.poll() is None:
                port_proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
