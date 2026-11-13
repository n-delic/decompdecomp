/* PORT_LIVE=port: the game played in a browser. The host serves http://127.0.0.1:port/ (this machine only): a page
 * that shows each frame as the game shows it and sends the keyboard to the first controller.
 *
 * Keys: arrows steer (the stick and the d-pad), X is A, Z (or C) is B, Enter is Start, Space is Z, Q and W are L and
 * R, I/J/K/L are the C buttons. A gamepad works too (the browser's standard layout: the left stick steers, the right
 * stick is the C buttons, the triggers Z), and the page can go full screen. The page's Sound button plays the game's sound (audio.c) as it's made. No dependency:
 * plain sockets and a page that draws the frames on a canvas and plays the sound through Web Audio. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
void port_live_start(void) {
    if (getenv("PORT_LIVE")) fprintf(stderr, "PORT_LIVE: the live view isn't built for Windows yet\n");
}
int port_live_active(void) { return 0; }
void port_live_frame(const uint8_t *rgb, int w, int h) { (void)rgb; (void)w; (void)h; }
int port_live_input(uint16_t *buttons, int8_t *x, int8_t *y) { (void)buttons; (void)x; (void)y; return 0; }
void port_live_audio(const int16_t *lr, int frames, int hz) { (void)lr; (void)frames; (void)hz; }
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <pthread.h>
#include <sys/socket.h>
#include <unistd.h>

static pthread_mutex_t live_lock = PTHREAD_MUTEX_INITIALIZER;
static uint8_t *frame; /* 8 bytes (width, height, little-endian u32) then RGB */
static size_t frame_size;
static unsigned long frame_number;
static uint16_t live_buttons;
static int8_t live_x, live_y;
static int active;
/* the sound: the last few seconds of 16-bit stereo, numbered by frame, which the page fetches as it plays */
#define RING (1 << 17)
static int16_t ring[RING * 2];
static unsigned long long ring_end; /* frames written so far */
static uint32_t ring_hz = 22050;

static const char page[] =
    "<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
    "<title>Port</title><style>html,body{margin:0;height:100%;background:#111;color:#bbb;font:13px system-ui}"
    "canvas{display:block;margin:12px auto;width:min(96vw,calc((100vh - 90px)*4/3));image-rendering:pixelated;"
    "background:#000}canvas:fullscreen{width:auto;height:100vh;margin:auto;object-fit:contain}"
    "p{text-align:center;margin:4px}</style></head><body><canvas id=c width=320 height=240></canvas>"
    "<p>Arrows steer &middot; X: A &middot; Z: B &middot; Enter: Start &middot; Space: Z &middot; Q/W: L/R &middot; "
    "I/J/K/L: C buttons &middot; or a gamepad &middot; <button id=snd>Sound on</button> <button id=fs>Full screen</button></p><p id=s></p><script>\n"
    "const c=document.getElementById('c'),g=c.getContext('2d'),s=document.getElementById('s');let last=-1,shown=0,t0=performance.now();\n"
    "const keys={ArrowUp:0x0800,ArrowDown:0x0400,ArrowLeft:0x0200,ArrowRight:0x0100,KeyX:0x8000,KeyZ:0x4000,KeyC:0x4000,"
    "Enter:0x1000,Space:0x2000,KeyQ:0x0020,KeyW:0x0010,KeyI:0x0008,KeyK:0x0004,KeyJ:0x0002,KeyL:0x0001};\n"
    /* a gamepad in the browser's standard layout: A and X are A, B and Y are B, Start, the shoulders L and R, the
       triggers Z, the d-pad, the left stick, the right stick as the C buttons */
    "const pad={0:0x8000,2:0x4000,1:0x4000,3:0x4000,9:0x1000,4:0x0020,5:0x0010,6:0x2000,7:0x2000,12:0x0800,13:0x0400,14:0x0200,15:0x0100};\n"
    "let down=new Set(),sent='';function send(){let b=0;for(const k of down)b|=keys[k];"
    "let x=(down.has('ArrowRight')?80:0)-(down.has('ArrowLeft')?80:0),y=(down.has('ArrowUp')?80:0)-(down.has('ArrowDown')?80:0);"
    "for(const gp of (navigator.getGamepads?navigator.getGamepads():[])){if(!gp)continue;"
    "gp.buttons.forEach((v,i)=>{if((v.pressed||v.value>0.5)&&pad[i])b|=pad[i];});"
    "const ax=gp.axes[0]||0,ay=gp.axes[1]||0,cx=gp.axes[2]||0,cy=gp.axes[3]||0;"
    "if(Math.abs(ax)>0.15)x=Math.round(ax*80);if(Math.abs(ay)>0.15)y=Math.round(-ay*80);"
    "if(cx>0.5)b|=0x0001;if(cx<-0.5)b|=0x0002;if(cy>0.5)b|=0x0004;if(cy<-0.5)b|=0x0008;}"
    "const q='b='+b+'&x='+x+'&y='+y;if(q!=sent){sent=q;fetch('/input?'+q,{method:'POST'}).catch(()=>{});}}\n"
    "setInterval(send,16);addEventListener('gamepadconnected',e=>{s.dataset.pad=e.gamepad.id;});\n"
    "addEventListener('keydown',e=>{if(e.code in keys){e.preventDefault();if(!down.has(e.code)){down.add(e.code);send();}}});\n"
    "addEventListener('keyup',e=>{if(e.code in keys){e.preventDefault();down.delete(e.code);send();}});\n"
    "addEventListener('blur',()=>{down.clear();send();});\n"
    "async function loop(){try{const r=await fetch('/frame?after='+last);const b=new Uint8Array(await r.arrayBuffer());"
    "if(b.length>12){const d=new DataView(b.buffer),w=d.getUint32(0,true),h=d.getUint32(4,true);last=d.getUint32(8,true);"
    "if(c.width!=w||c.height!=h){c.width=w;c.height=h;}const im=g.createImageData(w,h),p=im.data;"
    "for(let i=0,j=12;i<w*h;i++,j+=3){p[i*4]=b[j];p[i*4+1]=b[j+1];p[i*4+2]=b[j+2];p[i*4+3]=255;}g.putImageData(im,0,0);shown++;"
    "const now=performance.now();if(now-t0>1000){s.textContent=Math.round(shown*1000/(now-t0))+' frames a second';shown=0;t0=now;}}}"
    "catch(e){await new Promise(r=>setTimeout(r,500));}requestAnimationFrame(loop);}loop();\n"
    "document.getElementById('fs').onclick=()=>c.requestFullscreen&&c.requestFullscreen();\n"
    "let ac=null,next=0,heard=-1;document.getElementById('snd').onclick=e=>{if(!ac){ac=new AudioContext();e.target.textContent='Sound playing';sound();}};\n"
    "async function sound(){try{const r=await fetch('/audio?after='+heard);const b=await r.arrayBuffer();if(b.byteLength>16){"
    "const d=new DataView(b),hz=d.getUint32(0,true),first=Number(d.getBigUint64(4,true)),n=(b.byteLength-16)>>2;"
    "if(heard>=0&&first>heard){next=0;}heard=first+n;const buf=ac.createBuffer(2,n,hz),L=buf.getChannelData(0),R=buf.getChannelData(1);"
    "for(let i=0;i<n;i++){L[i]=d.getInt16(16+i*4,true)/32768;R[i]=d.getInt16(18+i*4,true)/32768;}"
    "const src=ac.createBufferSource();src.buffer=buf;src.connect(ac.destination);"
    "if(next<ac.currentTime+0.02||next>ac.currentTime+0.5)next=ac.currentTime+0.08;src.start(next);next+=n/hz;}}"
    "catch(e){}setTimeout(sound,40);}\n"
    "</script></body></html>";

static void reply(int fd, const char *type, const void *body, size_t n) {
    char head[256];
    int k = snprintf(head, sizeof head, "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %zu\r\n"
                     "Cache-Control: no-store\r\nConnection: close\r\n\r\n", type, n);
    if (write(fd, head, (size_t)k) < 0) return;
    for (size_t off = 0; off < n;) {
        ssize_t w = write(fd, (const char *)body + off, n - off);
        if (w <= 0) return;
        off += (size_t)w;
    }
}

static void *serve(void *arg) {
    int server = (int)(intptr_t)arg;
    for (;;) {
        int fd = accept(server, NULL, NULL);
        if (fd < 0) continue;
        char req[2048];
        ssize_t n = read(fd, req, sizeof req - 1);
        if (n <= 0) { close(fd); continue; }
        req[n] = 0;
        if (!strncmp(req, "GET / ", 6)) {
            reply(fd, "text/html; charset=utf-8", page, sizeof page - 1);
        } else if (!strncmp(req, "GET /frame", 10)) {
            const char *a = strstr(req, "after=");
            long after = a ? atol(a + 6) : -1;
            uint8_t *copy = NULL;
            size_t size = 0;
            for (int tries = 0; tries < 100; tries++) { /* wait up to a second for a frame newer than the page's */
                pthread_mutex_lock(&live_lock);
                if (frame && (long)frame_number != after) {
                    size = frame_size + 4;
                    copy = malloc(size);
                    memcpy(copy, frame, 8);
                    uint32_t num = (uint32_t)frame_number;
                    memcpy(copy + 8, &num, 4);
                    memcpy(copy + 12, frame + 8, frame_size - 8);
                }
                pthread_mutex_unlock(&live_lock);
                if (copy) break;
                usleep(10000);
            }
            reply(fd, "application/octet-stream", copy ? (const void *)copy : "", size);
            free(copy);
        } else if (!strncmp(req, "GET /audio", 10)) { /* hz, the first frame's number, then the frames after `after` */
            const char *a = strstr(req, "after=");
            long long after = a ? atoll(a + 6) : -1;
            pthread_mutex_lock(&live_lock);
            unsigned long long end = ring_end, start = end > RING / 2 ? end - RING / 2 : 0;
            if (after < 0) start = end > (unsigned long long)ring_hz / 10 ? end - ring_hz / 10 : 0; /* begin near now */
            else if ((unsigned long long)after > start) start = (unsigned long long)after;
            if (start > end) start = end;
            size_t n = (size_t)(end - start), size = 16 + n * 4;
            uint8_t *out = malloc(size);
            memcpy(out, &ring_hz, 4);
            memcpy(out + 4, &start, 8);
            memset(out + 12, 0, 4);
            for (size_t i = 0; i < n; i++) memcpy(out + 16 + i * 4, &ring[((start + i) % RING) * 2], 4);
            pthread_mutex_unlock(&live_lock);
            reply(fd, "application/octet-stream", out, size);
            free(out);
        } else if (!strncmp(req, "POST /input", 11)) {
            const char *b = strstr(req, "b="), *x = strstr(req, "x="), *y = strstr(req, "y=");
            pthread_mutex_lock(&live_lock);
            if (b) live_buttons = (uint16_t)atoi(b + 2);
            if (x) live_x = (int8_t)atoi(x + 2);
            if (y) live_y = (int8_t)atoi(y + 2);
            pthread_mutex_unlock(&live_lock);
            reply(fd, "text/plain", "", 0);
        } else {
            const char *nf = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
            if (write(fd, nf, strlen(nf)) < 0) { /* the page went */ }
        }
        close(fd);
    }
    return NULL;
}

void port_live_start(void) {
    const char *p = getenv("PORT_LIVE");
    if (!p) return;
    int server = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in a = {0};
    a.sin_family = AF_INET;
    a.sin_port = htons((uint16_t)atoi(p));
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK); /* this machine only */
    if (server < 0 || bind(server, (struct sockaddr *)&a, sizeof a) || listen(server, 16)) {
        fprintf(stderr, "PORT_LIVE: can't listen on 127.0.0.1:%s\n", p);
        return;
    }
    active = 1;
    pthread_t t;
    pthread_create(&t, NULL, serve, (void *)(intptr_t)server);
    pthread_detach(t);
    fprintf(stderr, "playing at http://127.0.0.1:%s/\n", p);
}

int port_live_active(void) { return active; }

void port_live_frame(const uint8_t *rgb, int w, int h) {
    size_t n = 8 + (size_t)w * h * 3;
    pthread_mutex_lock(&live_lock);
    if (frame_size != n) { free(frame); frame = malloc(n); frame_size = n; }
    uint32_t dims[2] = {(uint32_t)w, (uint32_t)h};
    memcpy(frame, dims, 8);
    memcpy(frame + 8, rgb, n - 8);
    frame_number++;
    pthread_mutex_unlock(&live_lock);
}

int port_live_input(uint16_t *buttons, int8_t *x, int8_t *y) {
    if (!active) return 0;
    pthread_mutex_lock(&live_lock);
    *buttons = live_buttons; *x = live_x; *y = live_y;
    pthread_mutex_unlock(&live_lock);
    return 1;
}
#endif

#ifndef _WIN32
void port_live_audio(const int16_t *lr, int frames, int hz) {
    if (!active) return;
    pthread_mutex_lock(&live_lock);
    ring_hz = (uint32_t)hz;
    for (int i = 0; i < frames; i++, ring_end++) {
        ring[(ring_end % RING) * 2] = lr[i * 2];
        ring[(ring_end % RING) * 2 + 1] = lr[i * 2 + 1];
    }
    pthread_mutex_unlock(&live_lock);
}
#endif
