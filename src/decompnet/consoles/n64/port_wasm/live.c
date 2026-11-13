/* PORT_LIVE=port: the game played in a browser. The host serves http://127.0.0.1:port/ (this machine only): a page
 * that shows each frame as the game shows it and sends the keyboard to the first controller.
 *
 * Keys: arrows steer (the stick and the d-pad), X is A, Z (or C) is B, Enter is Start, Space is Z, Q and W are L and
 * R, I/J/K/L are the C buttons. No dependency: plain sockets and a page that draws the frames on a canvas. */
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

static const char page[] =
    "<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
    "<title>Port</title><style>html,body{margin:0;height:100%;background:#111;color:#bbb;font:13px system-ui}"
    "canvas{display:block;margin:12px auto;width:min(96vw,calc((100vh - 90px)*4/3));image-rendering:pixelated;"
    "background:#000}p{text-align:center;margin:4px}</style></head><body><canvas id=c width=320 height=240></canvas>"
    "<p>Arrows steer &middot; X: A &middot; Z: B &middot; Enter: Start &middot; Space: Z &middot; Q/W: L/R &middot; "
    "I/J/K/L: C buttons</p><p id=s></p><script>\n"
    "const c=document.getElementById('c'),g=c.getContext('2d'),s=document.getElementById('s');let last=-1,shown=0,t0=performance.now();\n"
    "const keys={ArrowUp:0x0800,ArrowDown:0x0400,ArrowLeft:0x0200,ArrowRight:0x0100,KeyX:0x8000,KeyZ:0x4000,KeyC:0x4000,"
    "Enter:0x1000,Space:0x2000,KeyQ:0x0020,KeyW:0x0010,KeyI:0x0008,KeyK:0x0004,KeyJ:0x0002,KeyL:0x0001};\n"
    "let down=new Set();function send(){let b=0;for(const k of down)b|=keys[k];"
    "const x=(down.has('ArrowRight')?80:0)-(down.has('ArrowLeft')?80:0),y=(down.has('ArrowUp')?80:0)-(down.has('ArrowDown')?80:0);"
    "fetch('/input?b='+b+'&x='+x+'&y='+y,{method:'POST'}).catch(()=>{});}\n"
    "addEventListener('keydown',e=>{if(e.code in keys){e.preventDefault();if(!down.has(e.code)){down.add(e.code);send();}}});\n"
    "addEventListener('keyup',e=>{if(e.code in keys){e.preventDefault();down.delete(e.code);send();}});\n"
    "addEventListener('blur',()=>{down.clear();send();});\n"
    "async function loop(){try{const r=await fetch('/frame?after='+last);const b=new Uint8Array(await r.arrayBuffer());"
    "if(b.length>12){const d=new DataView(b.buffer),w=d.getUint32(0,true),h=d.getUint32(4,true);last=d.getUint32(8,true);"
    "if(c.width!=w||c.height!=h){c.width=w;c.height=h;}const im=g.createImageData(w,h),p=im.data;"
    "for(let i=0,j=12;i<w*h;i++,j+=3){p[i*4]=b[j];p[i*4+1]=b[j+1];p[i*4+2]=b[j+2];p[i*4+3]=255;}g.putImageData(im,0,0);shown++;"
    "const now=performance.now();if(now-t0>1000){s.textContent=Math.round(shown*1000/(now-t0))+' frames a second';shown=0;t0=now;}}}"
    "catch(e){await new Promise(r=>setTimeout(r,500));}requestAnimationFrame(loop);}loop();\n"
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
