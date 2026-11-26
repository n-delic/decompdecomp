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
both sides, and the first retrace at which the RAMs part is on the page. The port still gets ahead in the game (it loads
in no time and never drops a frame), so the page can slow it: it then goes on for only that share of the console's
retraces, or is held while the console catches up. Pause, Step (one retrace) and the port's speed are in the page.

In lockstep what each side was given at every retrace (the pad, and whether the port went on) is the whole of the run's
input, so a **bookmark** is that record with the saves the run started from: going to it starts both again on copies of
those saves and replays the record as fast as they go (Replay, below). Neither side has save states.

    python -m decompnet.port.wasm.side_by_side ROM (--port-url http://127.0.0.1:PORT/ | --program build/game)
"""
from __future__ import annotations

import argparse
import array
import base64
import bisect
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zlib
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
<p>Sound: <select id=snd><option value=''>off</option><option value=/port/audio>port</option><option value=/emu/audio>console</option></select> &nbsp; <button id=shot>Save screenshot</button> <span id=shotmsg></span></p>
<p id=hud hidden><button id=run>Pause</button> <button id=step>Step</button> &nbsp; <label>Port speed <select id=speed title="Slow or hold the port so the console can keep up with it">
<option value=100>100%</option><option value=95>95%</option><option value=90>90%</option><option value=80>80%</option><option value=75>75%</option><option value=66>66%</option><option value=50>50%</option><option value=25>25%</option><option value=0>held</option></select></label> &nbsp; <span id=vi></span></p>
<p id=marks hidden><button id=mark>Bookmark</button> <select id=list aria-label=Bookmark></select> <button id=go>Go to bookmark</button> <button id=del>Delete</button> <button id=cancel hidden>Stop</button> <progress id=prog hidden></progress> <span id=markmsg></span></p>
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
let ac=null,next=0,heard=-1,src='';
document.getElementById('snd').onchange=e=>{src=e.target.value;heard=-1;next=0;if(src&&!ac)ac=new AudioContext();};
async function sound(){try{if(src){const mine=src;const r=await fetch(mine+'?after='+heard);const b=await r.arrayBuffer();
if(mine===src&&b.byteLength>16){const d=new DataView(b),hz=d.getUint32(0,true),first=Number(d.getBigUint64(4,true)),n=(b.byteLength-16)>>2;
if(heard>=0&&first>heard){next=0;}heard=first+n;const buf=ac.createBuffer(2,n,hz),L=buf.getChannelData(0),R=buf.getChannelData(1);
for(let i=0;i<n;i++){L[i]=d.getInt16(16+i*4,true)/32768;R[i]=d.getInt16(18+i*4,true)/32768;}
const o=ac.createBufferSource();o.buffer=buf;o.connect(ac.destination);
if(next<ac.currentTime+0.02||next>ac.currentTime+0.5)next=ac.currentTime+0.08;o.start(next);next+=n/hz;}}}
catch(e){}setTimeout(sound,40);}
sound();
// both pictures as they are now, their frame counts, the retrace and the difference list, in one PNG the viewer saves
function composite(){const text=id=>{const e=document.getElementById(id);return e.hidden?'':e.textContent;};
const w=640,h=480,gap=12,lines=[text('vi'),...text('diff').split('\n')].filter((l,i)=>i||l);
const c=document.createElement('canvas');c.width=2*w+3*gap;c.height=h+70+lines.length*18+gap;const g=c.getContext('2d');
g.fillStyle='#111';g.fillRect(0,0,c.width,c.height);g.imageSmoothingEnabled=false;
[['Port','p','ps',gap],['Console (mupenRE)','e','es',w+2*gap]].forEach(([name,id,note,x])=>{
g.fillStyle='#ddd';g.font='600 16px system-ui';g.textAlign='center';g.fillText(name,x+w/2,22);
g.drawImage(document.getElementById(id),x,32,w,h);g.fillStyle='#bbb';g.font='13px system-ui';g.fillText(text(note),x+w/2,h+52);});
g.textAlign='left';g.fillStyle='#9c9';g.font='13px ui-monospace,monospace';lines.forEach((l,i)=>g.fillText(l,gap,h+80+i*18));return c;}
const shot=document.getElementById('shot'),shotmsg=document.getElementById('shotmsg');
shot.onclick=async()=>{shot.disabled=true;shot.blur();shotmsg.textContent='Saving\u2026';
try{const png=await new Promise((ok,no)=>composite().toBlob(b=>b?ok(b):no(new Error('the picture could not be made')),'image/png'));
const r=await fetch('/screenshot',{method:'POST',headers:{'X-Side-By-Side':'1'},body:png});const answer=await r.json();
if(!r.ok)throw new Error(answer.error||r.status);shotmsg.textContent='Saved to '+answer.path;}
catch(e){shotmsg.textContent='Not saved: '+e.message;}finally{shot.disabled=false;}};
let running=true,speedSending=false;const speed=document.getElementById('speed');
// (blurred once chosen: the arrow keys are the game's, not the list's)
speed.onchange=async()=>{speedSending=true;speed.blur();try{await fetch('/control?speed='+speed.value/100,{method:'POST'});}catch(e){}speedSending=false;};
const el=id=>document.getElementById(id);let asked=false,replayed=false,listed='';
// one request at a time, said at once; the buttons stay off until the viewer has taken it (and while it replays)
async function bookmark(what,button,doing){if(asked)return;asked=true;button.blur();el('markmsg').textContent=doing;
for(const b of ['mark','go','del'])el(b).disabled=true;
try{const r=await fetch('/bookmark?do='+what+'&id='+encodeURIComponent(el('list').value),{method:'POST',headers:{'X-Side-By-Side':'1'}});
if(!r.ok)el('markmsg').textContent=(await r.json()).error;}catch(e){el('markmsg').textContent='Not done: '+e.message;}asked=false;}
el('mark').onclick=()=>bookmark('save',el('mark'),'Bookmarking\u2026');
el('go').onclick=()=>bookmark('go',el('go'),'Restarting both\u2026');
el('del').onclick=()=>{if(confirm('Delete this bookmark?'))bookmark('delete',el('del'),'Deleting\u2026');};
el('cancel').onclick=()=>{el('cancel').disabled=true;bookmark('stop',el('cancel'),'Stopping\u2026');};
function marks(d){const m=d.marks;el('marks').hidden=!m;if(!m)return;
const now=JSON.stringify(m.list);if(now!==listed){listed=now;const was=el('list').value,fresh=m.list.length&&m.list[0].id;
el('list').replaceChildren(...m.list.map(b=>new Option(b.name,b.id)));el('list').value=m.list.some(b=>b.id===was)&&!m.saved?was:fresh;}
const busy=!!m.busy;if(!asked){el('mark').disabled=busy;el('go').disabled=el('del').disabled=busy||!m.list.length;el('list').disabled=busy||!m.list.length;
el('markmsg').textContent=m.message;}
const going=!!m.replaying;el('cancel').hidden=el('prog').hidden=!going;if(!going)el('cancel').disabled=false;
else if(m.replaying.total){el('prog').max=m.replaying.total;el('prog').value=m.replaying.done;}
if(replayed&&!going)heard=-1;replayed=going;for(const b of ['run','step','speed'])el(b).disabled=going;}
async function status(){try{const r=await fetch('/state');const d=await r.json();marks(d);
if(d.lockstep){document.getElementById('hud').hidden=false;document.getElementById('diff').hidden=false;
document.getElementById('run').textContent=d.running?'Pause':'Resume';running=d.running;
if(document.activeElement!==speed&&!speedSending)speed.value=Math.round(d.speed*100);
document.getElementById('vi').textContent=(d.port_vi===d.vi?'retrace '+d.vi:'console retrace '+d.vi+', port '+d.port_vi)+' · '+d.blocks+' differing 256-byte blocks'+(d.first_vi?' · first seen at retrace '+d.first_vi:'');
const going=d.marks&&d.marks.replaying;
if(going){document.getElementById('vi').textContent='replaying: retrace '+going.done+' of '+going.total;document.getElementById('diff').textContent='';}
else document.getElementById('diff').textContent=(d.new.length?'new at this retrace:\n'+d.new.join('\n')+'\n\n':'')+'regions (port vs console):\n'+d.regions.join('\n')+(d.note?'\n\n'+d.note:'');}}catch(e){}setTimeout(status,250);}
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
                time.sleep(0.003)
                continue
            frames, _vi, task, ram = snap
            last = frames
            try:
                picture = self._draw(dump, ram, task)
            except Exception:  # noqa: BLE001  a frame that can't be drawn (a list half built) is skipped, as in Check pictures
                continue
            if picture is None:
                continue
            width, lines, rgb = picture
            with self.lock:
                self.number += 1
                self.game_frames = frames
                self.frame = struct.pack("<III", width, lines, self.number) + rgb
                self.lock.notify_all()

    def _draw(self, dump: Path, ram: bytes, task: bytes):
        """One frame: the RAM with the task the RSP was given placed where the renderer finds it and what earlier frames
        drew put back (Check pictures' carry), in one file, drawn by the one renderer process."""
        fc = self.fc
        mem = bytearray(ram.ljust(fc.RAM, b"\0"))
        words = list(struct.unpack(">16I", task))
        for i in (2, 4, 6, 8, 10, 12):  # physical pointers: the renderer reads them as the game's
            if words[i] and words[i] < 0x80000000:
                words[i] |= 0x80000000
        mem[fc.TASK_AT & 0x7FFFFF:(fc.TASK_AT & 0x7FFFFF) + 64] = struct.pack(">16I", *words)
        for addr, raw in self.carry.items():
            at = addr & 0x7FFFFF
            mem[at:at + len(raw)] = raw
        dump.write_bytes(mem)
        kept: dict[int, bytes] = {}
        picture = self.renderer.draw_picture(dump, fc.TASK_AT, kept)
        for addr, raw in kept.items():  # a task that draws fewer lines than an earlier one leaves the rest as it was
            self.carry[addr] = raw + self.carry.get(addr, b"")[len(raw):]
        return picture

    def reset(self) -> None:
        """Another run of the console: nothing an earlier one drew is carried into its pictures."""
        self.carry, self.game_frames = {}, 0

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


class Symbols:
    """Names for addresses: (address, size, name) entries, looked up as `name+0xN` inside an entry's size (an entry with no
    size covers up to 0x100 bytes, a function's usual reach for a label)."""

    def __init__(self, entries=()):
        self.entries = sorted(set((int(a) & 0xFFFFFFFF, int(n or 0), str(name)) for a, n, name in entries))
        self.starts = [e[0] for e in self.entries]

    def __bool__(self) -> bool:
        return bool(self.entries)

    def name(self, address: int) -> str:
        i = bisect.bisect_right(self.starts, address) - 1
        if i < 0:
            return ""
        start, size, name = self.entries[i]
        offset = address - start
        if offset >= (size or 0x100):
            return ""
        return f"{name}+0x{offset:X}" if offset else name

    @classmethod
    def from_file(cls, path: Path) -> "Symbols":
        """A symbol list in the usual `name = 0xADDRESS; // size:0xN` form (splat's symbol_addrs.txt)."""
        out = []
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*(\w+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*;(?:.*size:\s*(0x[0-9A-Fa-f]+|\d+))?", line)
            if m:
                out.append((int(m.group(2), 0), int(m.group(3), 0) if m.group(3) else 0, m.group(1)))
        return cls(out)


def regions(blocks: list[int], a: bytes, b: bytes, limit: int = 12, symbols: Symbols | None = None) -> list[str]:
    """Runs of differing blocks as text lines: the address range and the first word that differs in each, with both
    values, and the symbol that word is in when the project's names are known."""
    out, i = [], 0
    while i < len(blocks) and len(out) < limit:
        j = i
        while j + 1 < len(blocks) and blocks[j + 1] == blocks[j] + BLOCK:
            j += 1
        start, end = blocks[i], blocks[j] + BLOCK
        w = next(o for o in range(start, end, 4) if a[o:o + 4] != b[o:o + 4])
        where = symbols.name(0x80000000 + w) if symbols else ""
        out.append(f"{0x80000000 + start:08X}-{0x80000000 + end:08X}  first {0x80000000 + w:08X}"
                   f"{f' ({where})' if where else ''}: {a[w:w + 4].hex().upper()} vs {b[w:w + 4].hex().upper()}")
        i = j + 1
    return out


class Lockstep:
    """The viewer as the clock: both sides park before each retrace, their RAMs are compared there, then the one pad is
    given to both and they go on one retrace (docs/PORTING.md, "Side by side, live")."""

    history: list  # what each retrace was given: [buttons, x, y, port went on, how many retraces running]
    request = None  # a bookmark action asked for by the page, done by the stepping thread
    replaying = None  # {"done", "total"} while going to a bookmark
    cancel = saved = False
    message = ""

    def __init__(self, session: "Session", rate: float = 60.0, offset: int = 0, ranges: list | None = None,
                 symbols: Symbols | None = None):
        self.session, self.interval, self.offset, self.ranges = session, 1.0 / rate, offset, ranges
        self.symbols = symbols
        self.running, self.stepping, self.stop = True, False, False
        self.speed, self.port_vi = 1.0, 1  # the share of the console's retraces the port goes on for, and its retrace
        self.pad, self.sent_pad = (0, 0, 0), None
        self.held: dict[int, int] = {}  # button bit: retraces it stays down at least (a tap shorter than a game's poll is still seen)
        self.vi, self.blocks, self.first_vi, self.new, self.regions, self.note = 0, 0, 0, [], [], "starting"
        self.previous: set[int] = set()
        self.history = []
        self.lock = threading.Condition()
        self.frame: bytes | None = None
        self.number = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _port(self, path: str, post: bool = False, timeout: float = 10) -> bytes:
        request = urllib.request.Request(self.session.port_url + path, method="POST" if post else "GET")
        with urllib.request.urlopen(request, timeout=timeout) as r:
            return r.read()

    def _wait(self, n: int, give_up: float = 30.0, port: bytes | None = None, port_vi: int | None = None):
        """Both RAMs once both sides have parked, the console at retrace n and the port at `port_vi` (n unless the port
        has been slowed): (port, console), or None if one didn't. `port`: its RAM when it was held there already."""
        end = time.monotonic() + give_up
        port_vi = n if port_vi is None else port_vi
        emu = None
        while time.monotonic() < end and not self.stop:
            if port is None:
                try:
                    port = self._port(f"/ram?vi={port_vi}") or None
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
        """The page's pad, kept until the next retrace; a button pressed since the last one stays down for TAP of the
        port's retraces (the console's while the port is held), so a slowed port still sees it."""
        if self.replaying:
            return
        for bit in range(16):
            if buttons >> bit & 1 and not self.pad[0] >> bit & 1:
                self.held[bit] = self.TAP
        self.pad = (buttons, x, y)

    def set_speed(self, speed: float) -> None:
        self.speed = min(1.0, max(0.0, speed)) if speed == speed else 1.0  # (a NaN is no speed)

    def _apply_pad(self, counts: bool = True) -> None:
        buttons = self.pad[0]
        for bit in list(self.held):
            buttons |= 1 << bit
            self.held[bit] -= counts
            if self.held[bit] <= 0:
                del self.held[bit]
        self._send_pad((buttons, self.pad[1], self.pad[2]))

    def _send_pad(self, pad: tuple) -> None:
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
        self.new = regions(new, port, emu, 6, self.symbols)
        self.regions = regions(blocks, port, emu, 12, self.symbols)

    def _run(self) -> None:
        self.n = self.p = 1  # the retrace each side is parked before: the console's (but for the offset), and the port's
        owed = 0.0  # port retraces due: `speed` more with each of the console's
        held: bytes | None = None  # the port's RAM, kept while it isn't let go on
        while not self.stop:
            if self.request:
                if self._bookmark(held):
                    owed, held = 0.0, None
                continue
            if not (self.running or self.stepping):
                time.sleep(0.02)
                continue
            n, p = self.n, self.p
            began = time.monotonic()
            both = self._wait(n, port=held, port_vi=p)
            if both is None:
                self.note = f"waiting: retrace {n} not reached by both sides yet (the port needs PORT_LOCKSTEP=1 PORT_VTIME=1)"
                continue
            self.note, held = "", both[0]
            self.port_vi = p
            self._compare(n, *both)
            self._show_port()
            speed = self.speed
            owed = owed + speed if speed else 0.0
            advance = owed >= 1 - 1e-9
            self._apply_pad(counts=advance or not speed)
            self.stepping = False
            if not self._go_on(advance):
                continue
            if advance:
                owed, held = max(0.0, owed - 1), None
            left = self.interval - (time.monotonic() - began)
            if left > 0 and self.running:
                time.sleep(left)

    def _show_port(self) -> None:
        try:
            frame = self._port("/frame?after=-1")
        except OSError:
            return
        if len(frame) > 12 and (self.frame is None or frame[12:] != self.frame[12:]):
            with self.lock:
                self.number += 1
                self.frame = frame[:8] + struct.pack("<I", self.number) + frame[12:]
                self.lock.notify_all()

    def _go_on(self, advance: bool) -> bool:
        """Lets the console, and the port when `advance`, run on to the next retrace, and records what they were given.
        False when the port didn't answer: nothing went on."""
        if advance:
            try:
                self._port(f"/gate?to={self.p}", post=True)
            except OSError:
                return False
            self.p += 1
        self.session.shared.set_gate(self.n + self.offset)
        self.n += 1
        step = [*(self.sent_pad or (0, 0, 0)), int(advance)]
        if self.history and self.history[-1][:4] == step:
            self.history[-1][4] += 1
        else:
            self.history.append([*step, 1])
        return True

    # -- bookmarks (Replay) --

    def ask(self, action: str, mark: dict | None = None) -> None:
        """A bookmark action from the page: "save", "go" (with the bookmark) or "stop" (a replay)."""
        if action == "stop":
            self.cancel = True
        elif self.request or self.replaying:
            raise NetworkError("Wait for the bookmark action that is running.")
        else:
            self.cancel, self.saved, self.message = False, False, ""
            self.request = (action, mark)

    def _bookmark(self, held: bytes | None) -> bool:
        """Does what was asked, between two retraces. True when the sides were started again."""
        action, mark = self.request
        try:
            if action == "save":
                both = self._wait(self.n, port=held, port_vi=self.p)
                if both is None:
                    raise NetworkError("Not bookmarked: the two sides aren't both waiting at a retrace.")
                self.session.save_bookmark(self.n, self.p, self.first_vi, self.history, *both)
                self.saved, self.message = True, f"Bookmarked console retrace {self.n}."
                return False
            self._replay(mark)
            return True
        except (NetworkError, OSError) as error:
            self.message = str(error)
            return action == "go"
        finally:
            self.request = self.replaying = None

    def _parked(self, give_up: float = 30.0) -> None:
        """Waits until both sides are at their next retrace, without reading their RAM."""
        end = time.monotonic() + give_up
        port = emu = False
        while not (port and emu):
            if self.stop or time.monotonic() > end:
                raise NetworkError(f"The replay stopped: retrace {self.n} wasn't reached by both sides.")
            if not port:
                try:
                    port = json.loads(self._port("/state", timeout=2) or b"{}").get("parked") == self.p
                except (OSError, ValueError):
                    pass
            emu = emu or self.session.shared.parked() == self.n + self.offset
            if not (port and emu):
                time.sleep(0.0005)

    def _replay(self, mark: dict) -> None:
        """Both started again and given the bookmark's record, retrace by retrace, then left paused there."""
        total = sum(run[4] for run in mark["history"])
        self.replaying, self.running, self.stepping = {"done": 0, "total": total}, False, False
        self.session.restart(mark)
        self.n = self.p = 1
        self.sent_pad, self.pad, self.held, self.history = None, (0, 0, 0), {}, []
        self.previous, self.first_vi, self.vi, self.port_vi, self.note = set(), 0, 0, 1, ""
        for buttons, x, y, advance, count in mark["history"]:
            for _ in range(count):
                if self.cancel or self.stop:
                    self.message = f"Stopped on the way, at console retrace {self.n}."
                    first = mark.get("first_vi", 0)
                    return self._arrive(first_vi=first if first <= self.n else 0)
                self._parked()
                self._send_pad((buttons, x, y))
                if not self._go_on(bool(advance)):
                    raise NetworkError("The replay stopped: the port didn't answer.")
                self.replaying["done"] += 1
                if self.replaying["done"] % 30 == 0:
                    self._show_port()
        self._arrive(mark)

    def _arrive(self, mark: dict | None = None, first_vi: int = 0) -> None:
        """The picture and the comparison where the replay ended, and whether both are as they were when bookmarked."""
        both = self._wait(self.n, port_vi=self.p)
        if both is None:
            raise NetworkError("The replay ended with the two sides not both waiting at a retrace.")
        self.port_vi = self.p
        self.first_vi = mark.get("first_vi", 0) if mark else first_vi
        self._compare(self.n, *both)
        self.new = []  # (nothing was compared on the way, so none of it is new here)
        self._show_port()
        if mark:
            off = [f"{name} ({n} blocks differ)" for name, ram in zip(("port", "console"), both)
                   if (n := changed_blocks(mark[name], ram))]
            self.message = ("At the bookmark: both are exactly as they were. Paused." if not off else
                            f"At the bookmark, paused. Not as it was: {', '.join(off)}. A rebuilt port differs this way; "
                            "check the picture is where you expect.")

    def wait(self, after: int, timeout: float = 1.0) -> bytes | None:
        with self.lock:
            self.lock.wait_for(lambda: self.frame is not None and self.number != after, timeout)
            return self.frame if self.frame is not None and self.number != after else None

    def state(self) -> dict:
        return {"lockstep": True, "running": self.running, "speed": self.speed, "port_vi": self.port_vi, "vi": self.vi, "blocks": self.blocks, "first_vi": self.first_vi,
                "new": self.new, "regions": self.regions, "note": self.note}

    def close(self) -> None:
        self.stop = True


def fingerprint(ram: bytes) -> str:
    """A RAM dump as one checksum a block, small enough to keep in a bookmark."""
    view = memoryview(ram)
    return base64.b64encode(array.array("I", (zlib.crc32(view[i:i + BLOCK]) for i in range(0, len(ram), BLOCK))).tobytes()).decode()


def changed_blocks(kept: str, ram: bytes) -> int:
    """How many blocks of `ram` aren't as `kept` (a fingerprint) has them."""
    a, b = array.array("I", base64.b64decode(kept)), array.array("I", base64.b64decode(fingerprint(ram)))
    return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))


# the port's save files: the setting that names each, and the name it has when none does (host.c)
PORT_SAVES = {"PORT_SAVE": "game.eep", "PORT_SRAM": "game.sra", "PORT_FLASH": "game.fla"}


def port_save_files(env: dict, cwd: Path) -> dict[str, Path]:
    return {name: Path(env[key]) if env.get(key) else Path(cwd) / name for key, name in PORT_SAVES.items()}


def read_saves(files: dict[str, Path]) -> dict[str, str]:
    """The save files that exist, by name, as base64."""
    return {name: base64.b64encode(path.read_bytes()).decode() for name, path in files.items() if path.is_file()}


def write_saves(saves: dict[str, str], folder: Path) -> None:
    """`folder` holding exactly these save files."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    for name, data in saves.items():
        if name != Path(name).name or name.startswith("."):
            raise NetworkError("That bookmark names a save file outside its folder.")
        (folder / name).write_bytes(base64.b64decode(data))


class Bookmarks:
    """The bookmarks of one game, one JSON file each in `folder`."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.names: dict[str, str] = {}
        for path in self.folder.glob("*.json"):
            try:
                self.names[path.stem] = str(json.loads(path.read_text(encoding="utf-8"))["name"])
            except (OSError, ValueError, KeyError, TypeError):
                continue  # not one of ours, or half written

    def list(self) -> list[dict]:
        return [{"id": i, "name": self.names[i]} for i in sorted(self.names, reverse=True)]

    def add(self, mark: dict) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        stem = time.strftime("%Y%m%d-%H%M%S")
        ident, n = f"{stem}-01", 1
        while (self.folder / f"{ident}.json").exists():
            n += 1
            ident = f"{stem}-{n:02d}"
        part = self.folder / f"{ident}.part"
        part.write_text(json.dumps(mark), encoding="utf-8")
        part.replace(self.folder / f"{ident}.json")
        self.names[ident] = mark["name"]
        return ident

    def _path(self, ident: str) -> Path:
        if ident not in self.names:  # (only the ones listed: never a path of the page's choosing)
            raise NetworkError("That bookmark isn't there any more.")
        return self.folder / f"{ident}.json"

    def load(self, ident: str) -> dict:
        try:
            return json.loads(self._path(ident).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise NetworkError(f"That bookmark can't be read: {error}") from error

    def delete(self, ident: str) -> None:
        self._path(ident).unlink(missing_ok=True)
        del self.names[ident]


class Port:
    """The port's process as the viewer runs it, so it can be started again for a bookmark. It answers as a Popen does
    (poll, terminate, wait, kill, pid), and its pid stays the first one's: Play knows the game by it."""

    def __init__(self, proc, argv: list, cwd: Path, env: dict, log: Path | None = None):
        self.proc, self.argv, self.cwd, self.env, self.log = proc, argv, Path(cwd), dict(env), log
        self.pid = getattr(proc, "pid", 0)
        self.saves = read_saves(port_save_files(self.env, self.cwd))  # what this run started from
        self.restarting = self.closed = False
        self.lock = threading.Lock()

    def poll(self):
        with self.lock:
            return None if self.restarting else self.proc.poll()

    def terminate(self) -> None:
        with self.lock:
            self.closed = True
            self.proc.terminate()

    def kill(self) -> None:
        with self.lock:
            self.closed = True
            self.proc.kill()

    def wait(self, timeout: float | None = None):
        return self.proc.wait(timeout=timeout)

    def restart(self, saves: dict[str, str], folder: Path) -> str:
        """Started again on copies of `saves` in `folder` (the user's own saves are left alone); its new address."""
        self.restarting = True
        try:
            with self.lock:
                if self.closed:
                    raise NetworkError("The game has been stopped.")
                self.proc.kill()
                self.proc.wait(timeout=10)
                write_saves(saves, folder)
                port = _free_port()
                self.env.update(PORT_LIVE=str(port), **{k: str((folder / v).resolve()) for k, v in PORT_SAVES.items()})
                with open(self.log or os.devnull, "ab") as out:
                    self.proc = subprocess.Popen(self.argv, cwd=self.cwd, env=self.env, stdout=out, stderr=out,
                                                 stdin=subprocess.DEVNULL)
                self.saves = saves
            url = f"http://127.0.0.1:{port}/"
            for _ in range(100):
                if self.proc.poll() is not None:
                    raise NetworkError("The port stopped as it started again.")
                try:
                    urllib.request.urlopen(url, timeout=1).close()
                    return url
                except OSError:
                    time.sleep(0.1)
            raise NetworkError("The port didn't start again.")
        finally:
            self.restarting = False


SCREENSHOT_MAX = 20 << 20


def save_screenshot(data: bytes, folder: Path, title: str = "") -> Path:
    """The page's picture of both halves, written as a new PNG in `folder`, never over one already there. It holds the
    game's own pictures, so it goes where the user keeps downloads, not into a project or a repository."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > SCREENSHOT_MAX:
        raise NetworkError("That isn't a screenshot the viewer can save.")
    name = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
    stem = "-".join(x for x in ("side-by-side", name, time.strftime("%Y%m%d-%H%M%S")) if x)
    folder.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        path = folder / (f"{stem}.png" if n == 1 else f"{stem}-{n}.png")
        try:
            with path.open("xb") as f:
                f.write(data)
            return path
        except FileExistsError:
            continue
    raise NetworkError(f"Too many screenshots with one name in {folder}.")


class Session:
    """The viewer: a page on 127.0.0.1 showing the port (proxied from its live view) beside the console, one pad."""

    def __init__(self, rom: Path, port_url: str, work: Path | None = None, *, expansion_pak: bool = False,
                 lockstep: bool = False, rate: float = 60.0, offset: int = 0, ranges: list | None = None,
                 symbols: Symbols | None = None, title: str = "", screenshots: Path | None = None,
                 port: Port | None = None, keep: Path | None = None):
        """`port`: the port's process, when the viewer may start it again (bookmarks need that and lockstep). `keep`: the
        game's folder for the console's saves and the bookmarks (none: in `work`, gone with it)."""
        from ...consoles.n64.emulator import live
        self.rom, self.port, self.expansion_pak, self.offset = Path(rom), port, expansion_pak, offset
        self.title, self.screenshots = title, Path(screenshots or Path.home() / "Downloads")
        self.port_url = port_url.rstrip("/")
        self._tmp = tempfile.TemporaryDirectory(prefix="decomp-side-by-side-") if work is None else None
        self.work = Path(work or self._tmp.name)
        self.work.mkdir(parents=True, exist_ok=True)
        self.shared = live.Shared(self.work / "live.bin", expansion_pak, lockstep)
        keep = Path(keep) if keep else self.work
        self.console_folder = keep / "console-saves"
        self.console_folder.mkdir(parents=True, exist_ok=True)
        self.console_saves = read_saves({p.name: p for p in self.console_folder.iterdir() if not p.name.startswith(".")})
        self.bookmarks = Bookmarks(keep / "bookmarks") if port and lockstep else None
        self.rom_id = hashlib.sha1(self.rom.read_bytes()).hexdigest() if self.bookmarks is not None else ""
        self.emulator = live.start(rom, self.shared, expansion_pak=expansion_pak, saves=self.console_folder)
        self.console = ConsoleView(self.shared, self.work)
        self.lockstep = Lockstep(self, rate, offset, ranges, symbols) if lockstep else None
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
                elif url.path == "/emu/audio":
                    self._send(session.shared.audio(after))
                elif url.path == "/port/audio":
                    try:
                        with urllib.request.urlopen(f"{session.port_url}/audio?after={after}", timeout=5) as r:
                            self._send(r.read())
                    except OSError:
                        self._send(b"")
                elif url.path == "/state":
                    state = session.lockstep.state() if session.lockstep else {"lockstep": False}
                    if session.bookmarks is not None:
                        ls = session.lockstep
                        state["marks"] = {"list": session.bookmarks.list(), "busy": bool(ls.request or ls.replaying),
                                          "replaying": ls.replaying, "message": ls.message, "saved": ls.saved}
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

            def _screenshot(self) -> None:
                # (the header makes another site's page ask first, which nothing here answers: only this page can write)
                size = int(self.headers.get("Content-Length") or 0)
                if self.headers.get("X-Side-By-Side") != "1" or not 0 < size <= SCREENSHOT_MAX:
                    return self._send(b'{"error": "refused"}', "application/json", 400)
                try:
                    path = save_screenshot(self.rfile.read(size), session.screenshots, session.title)
                except (NetworkError, OSError) as error:
                    return self._send(json.dumps({"error": str(error)}).encode(), "application/json", 500)
                self._send(json.dumps({"path": str(path)}).encode(), "application/json")

            def _bookmark(self, q: dict) -> None:
                # (the header: as for a screenshot, only this page can make the viewer write)
                action, ident = (q.get("do") or [""])[0], (q.get("id") or [""])[0]
                if self.headers.get("X-Side-By-Side") != "1" or session.bookmarks is None \
                        or action not in ("save", "go", "delete", "stop"):
                    return self._send(b'{"error": "refused"}', "application/json", 400)
                try:
                    if action == "delete":
                        session.bookmarks.delete(ident)
                        session.lockstep.message = "Deleted."
                    else:
                        session.lockstep.ask(action, session.load_bookmark(ident) if action == "go" else None)
                except (NetworkError, OSError) as error:
                    return self._send(json.dumps({"error": str(error)}).encode(), "application/json", 409)
                self._send(b"{}", "application/json")

            def do_POST(self):
                url = urlparse(self.path)
                q = parse_qs(url.query)
                if url.path == "/control" and session.lockstep:
                    if "run" in q:
                        session.lockstep.running = q["run"][0] == "1"
                    if "step" in q:
                        session.lockstep.running, session.lockstep.stepping = False, True
                    if "speed" in q:
                        try:
                            session.lockstep.set_speed(float(q["speed"][0]))
                        except ValueError:
                            return self._send(b"", "text/plain", 400)
                    return self._send(b"", "text/plain")
                if url.path == "/bookmark":
                    return self._bookmark(q)
                if url.path == "/screenshot":
                    return self._screenshot()
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

    def _kind(self) -> dict:
        """What a bookmark must share with this run for its record to mean the same."""
        return {"rom": self.rom_id, "expansion_pak": self.expansion_pak, "offset": self.offset}

    def save_bookmark(self, n: int, p: int, first_vi: int, history: list, port: bytes, emu: bytes) -> str:
        return self.bookmarks.add({
            **self._kind(), "name": f"retrace {n} · {time.strftime('%d %b %H:%M:%S')}", "vi": n, "port_vi": p,
            "first_vi": first_vi, "history": history, "port": fingerprint(port), "console": fingerprint(emu),
            "port_saves": self.port.saves, "console_saves": self.console_saves})

    def load_bookmark(self, ident: str) -> dict:
        mark = self.bookmarks.load(ident)
        if any(mark.get(k) != v for k, v in self._kind().items()):
            raise NetworkError("That bookmark was made with another ROM or console setting.")
        return mark

    def restart(self, mark: dict) -> None:
        """Both sides started again from the saves `mark`'s run started from, parked before their first retrace."""
        from ...consoles.n64.emulator import live
        self.emulator.kill()
        self.emulator.wait(timeout=10)
        self.shared.reset()
        self.console.reset()
        self.port_url = self.port.restart(mark["port_saves"], self.work / "replay" / "port").rstrip("/")
        folder = self.work / "replay" / "console"
        write_saves(mark["console_saves"], folder)
        self.console_saves = mark["console_saves"]
        self.emulator = live.start(self.rom, self.shared, expansion_pak=self.expansion_pak, saves=folder)

    def close(self) -> None:
        if self.lockstep:
            self.lockstep.close()
            self.lockstep.thread.join(timeout=15)  # (it may be starting the sides again)
        self.server.shutdown()
        self.console.close()
        if self.emulator.poll() is None:
            self.emulator.kill()
        self.shared.close()
        if self._tmp:
            self._tmp.cleanup()
        else:
            self.shared.path.unlink(missing_ok=True)  # 16 MB of the console's RAM, not kept


def start_port(program: Path, rom: Path, work: Path, lockstep: bool = False,
               expansion_pak: bool = False) -> tuple[Port, str]:
    """A built port running with its live view on a free port, as Play starts it; (process, its address)."""
    port = _free_port()
    # the same console on both sides: the port defaults to 8 MB, the emulator to 4 unless it has the Expansion Pak
    env = {**os.environ, "PORT_LIVE": str(port), "PORT_SAVE": str(work / "game.eep"),
           "PORT_MEMSIZE": "8" if expansion_pak else "4"}
    for key in ("PORT_RT64", "PORT_WINDOW", "PORT_RECOMP_UI"):
        env.pop(key, None)
    if lockstep:  # parked before each retrace until the viewer says go; virtual time, so the game is idle there
        env.update(PORT_LOCKSTEP="1", PORT_VTIME="1")
    argv = [str(program), str(rom), "0"]
    saves = read_saves(port_save_files(env, work))  # as they are before it runs
    proc = subprocess.Popen(argv, cwd=work, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(100):
        if proc.poll() is not None:
            raise NetworkError("The port stopped as it started.")
        try:
            urllib.request.urlopen(url, timeout=1).close()
            port = Port(proc, argv, work, env)
            port.saves = saves
            return port, url
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
    ap.add_argument("--symbols", type=Path, help="a symbol list (name = 0xADDRESS; // size:0xN) to name the differing addresses")
    ap.add_argument("--keep", type=Path, help="a folder to keep the console's saves and the bookmarks in between runs "
                                              "(bookmarks need --program)")
    ap.add_argument("--no-open", action="store_true", help="don't open the page in the browser")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="decomp-side-by-side-") as tmp:
        port_proc = None
        url = args.port_url
        lockstep = bool(args.program) if args.lockstep is None else args.lockstep
        ranges = [tuple(int(x, 16) & 0x1FFFFFFF for x in r.split("-")) for r in args.range]
        if args.program:
            port_proc, url = start_port(args.program.resolve(), args.rom.resolve(), Path(tmp), lockstep, args.expansion_pak)
        session = Session(args.rom.resolve(), url, Path(tmp), expansion_pak=args.expansion_pak, lockstep=lockstep,
                          rate=args.rate, offset=args.emulator_offset, ranges=ranges,
                          symbols=Symbols.from_file(args.symbols) if args.symbols else None, title=args.rom.stem,
                          port=port_proc, keep=args.keep)
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
