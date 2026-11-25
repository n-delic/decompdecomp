"""Side by side: the port and the console (mupenRE) running the same game in one page, one pad for both.

The left is the port as it plays (its live view, proxied); the right is the console: the reversing emulator runs the
user's own ROM live (consoles/n64/emulator/live.py), and each graphics task it hands over is drawn with the port's
renderer (port/frame_compare.py), the console's RAM carried from one frame to the next, so a difference between the
halves is the game running differently (the port's code, its host or its input), not a different renderer. The page's
keyboard or gamepad is sent to both at once.

Both run in real time and are not kept in step (the port loads in no time and never drops a frame; the console does):
each half shows its own frame count, so drift is visible. Lockstep by retrace is the next step (ROADMAP.md).

    python -m decompnet.port.wasm.side_by_side ROM (--port-url http://127.0.0.1:PORT/ | --program build/game)
"""
from __future__ import annotations

import argparse
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


class Session:
    """The viewer: a page on 127.0.0.1 showing the port (proxied from its live view) beside the console, one pad."""

    def __init__(self, rom: Path, port_url: str, work: Path | None = None, *, expansion_pak: bool = False):
        from ...consoles.n64.emulator import live
        self.port_url = port_url.rstrip("/")
        self._tmp = tempfile.TemporaryDirectory(prefix="decomp-side-by-side-") if work is None else None
        self.work = Path(work or self._tmp.name)
        self.work.mkdir(parents=True, exist_ok=True)
        self.shared = live.Shared(self.work / "live.bin", expansion_pak)
        self.emulator = live.start(rom, self.shared, expansion_pak=expansion_pak)
        self.console = ConsoleView(self.shared, self.work)
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
                if url.path != "/input":
                    return self._send(b"", status=404)
                q = parse_qs(url.query)
                buttons, x, y = (int((q.get(k) or ["0"])[0]) for k in ("b", "x", "y"))
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
        self.server.shutdown()
        self.console.close()
        if self.emulator.poll() is None:
            self.emulator.kill()
        self.shared.close()
        if self._tmp:
            self._tmp.cleanup()


def start_port(program: Path, rom: Path, work: Path) -> tuple[subprocess.Popen, str]:
    """A built port running with its live view on a free port, as Play starts it; (process, its address)."""
    port = _free_port()
    env = {**os.environ, "PORT_LIVE": str(port), "PORT_SAVE": str(work / "game.eep")}
    for key in ("PORT_RT64", "PORT_WINDOW", "PORT_RECOMP_UI"):
        env.pop(key, None)
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
    ap.add_argument("--no-open", action="store_true", help="don't open the page in the browser")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="decomp-side-by-side-") as tmp:
        port_proc = None
        url = args.port_url
        if args.program:
            port_proc, url = start_port(args.program.resolve(), args.rom.resolve(), Path(tmp))
        session = Session(args.rom.resolve(), url, Path(tmp), expansion_pak=args.expansion_pak)
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
