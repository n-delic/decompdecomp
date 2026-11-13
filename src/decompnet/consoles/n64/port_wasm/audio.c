/*
 * The N64's sound, in software (port/wasm): the audio tasks libultra's synthesizer sends the RSP (the "ABI 1" command
 * list: ADPCM decoding, resampling, envelopes and mixing in the RSP's 4 KB of data memory), and the audio interface
 * (AI) the game hands each finished buffer of 16-bit stereo samples to. The resampler's 4-tap table is linear
 * interpolation here, not the microcode's own.
 *
 * The finished samples go to the live view (live.c, PORT_LIVE) and, with PORT_AUDIO=file.wav, to a WAV file.
 * The AI's registers are answered as the hardware would at that moment: the length of the buffer still playing, and
 * whether the AI's two-buffer queue is full, in real time at the game's sample rate, so the game paces its sound.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

uint8_t *port_memory(void);
void port_live_audio(const int16_t *lr, int frames, int hz); /* live.c */

static uint8_t *A; /* the game's memory: physical p is A + (0x80000000 | p) */
static uint32_t ram(uint32_t a) { return 0x80000000u | (a & 0x1FFFFFFF); }
static uint16_t rd16(uint32_t a) { return (uint16_t)(A[a] << 8 | A[a + 1]); }
static void wr16(uint32_t a, uint16_t v) { A[a] = (uint8_t)(v >> 8); A[a + 1] = (uint8_t)v; }
static uint32_t rd32(uint32_t a) { return (uint32_t)rd16(a) << 16 | rd16(a + 2); }
static void wr32(uint32_t a, uint32_t v) { wr16(a, (uint16_t)(v >> 16)); wr16(a + 2, (uint16_t)v); }

/* ---------------------------------------------------------------- the RSP's audio microcode (ABI 1) */

#define DMEM_BASE 0x5C0
static uint8_t dmem[4096 + 64];
static int16_t ds(uint32_t o) { o &= 0xFFF; return (int16_t)(dmem[o] << 8 | dmem[o + 1]); }
static void ds_set(uint32_t o, int16_t v) { o &= 0xFFF; dmem[o] = (uint8_t)((uint16_t)v >> 8); dmem[o + 1] = (uint8_t)v; }
static int16_t clamp16(int32_t v) { return v > 32767 ? 32767 : v < -32768 ? -32768 : (int16_t)v; }
static uint32_t align(uint32_t v, uint32_t a) { return (v + a - 1) & ~(a - 1); }

static uint32_t segments[64];
static struct {
    uint16_t in, out, count, dry_right, wet_left, wet_right;
    int16_t dry, wet, vol[2], target[2];
    int32_t rate[2];
    uint32_t loop;
    int16_t table[16 * 8];
} st;
static uint32_t address(uint32_t a) { return segments[(a >> 24) & 0x3F] + (a & 0xFFFFFF); }

static void dram_to_dmem(uint32_t d, uint32_t addr, uint32_t n) {
    for (uint32_t i = 0; i < n; i++) dmem[(d + i) & 0xFFF] = A[ram(addr + i)];
}
static void dmem_to_dram(uint32_t addr, uint32_t d, uint32_t n) {
    for (uint32_t i = 0; i < n; i++) A[ram(addr + i)] = dmem[(d + i) & 0xFFF];
}

static int32_t rdot(int n, const int16_t *x, const int16_t *y) {
    int32_t accu = 0;
    y += n;
    while (n-- > 0) accu += *x++ * *--y;
    return accu;
}
static void residuals(int16_t *dst, const int16_t *book, const int16_t *last, const int16_t *src) {
    const int16_t *book2 = book + 8;
    int16_t l1 = last[0], l2 = last[1];
    for (int i = 0; i < 8; i++) {
        int32_t accu = (int32_t)src[i] << 11;
        accu += book[i] * l1 + book2[i] * l2 + rdot(i, book2, src);
        dst[i] = clamp16(accu >> 11);
    }
}
static void adpcm(int init, int loop, uint32_t state) {
    int16_t last[16];
    uint32_t o = st.out + DMEM_BASE, in = st.in + DMEM_BASE;
    int count = (int)align(st.count, 32);
    if (init) memset(last, 0, sizeof last);
    else for (int i = 0; i < 16; i++) last[i] = (int16_t)rd16(ram((loop ? st.loop : state) + 2 * i));
    for (int i = 0; i < 16; i++, o += 2) ds_set(o, last[i]);
    while (count > 0) {
        uint8_t code = dmem[in++ & 0xFFF];
        int scale = code >> 4, rshift = scale < 12 ? 12 - scale : 0;
        const int16_t *book = st.table + ((code & 0xF) << 4);
        int16_t frame[16];
        for (int i = 0; i < 8; i++) {
            uint8_t b = dmem[in++ & 0xFFF];
            frame[2 * i] = (int16_t)((int16_t)((b & 0xF0) << 8) >> rshift);
            frame[2 * i + 1] = (int16_t)((int16_t)((b & 0x0F) << 12) >> rshift);
        }
        residuals(last, book, last + 14, frame);
        residuals(last + 8, book, last + 6, frame + 8);
        for (int i = 0; i < 16; i++, o += 2) ds_set(o, last[i]);
        count -= 32;
    }
    for (int i = 0; i < 16; i++) wr16(ram(state + 2 * i), (uint16_t)last[i]);
}

/* A_POLEF: libultra's effects low-pass, a two-pole filter whose coefficients are the loaded ADPCM table: the first 8
   for the previous two outputs, the next 8 (scaled by the gain) for the frame's own earlier samples. */
static void polef(int init, int16_t gain, uint32_t state) {
    uint32_t in = st.in + DMEM_BASE, out = st.out + DMEM_BASE;
    int count = (int)align(st.count, 16);
    const int16_t *h1 = st.table;
    int16_t h2[8], h2_before[8], l1, l2;
    if (init) { l1 = 0; l2 = 0; }
    else { l1 = (int16_t)rd16(ram(state + 4)); l2 = (int16_t)rd16(ram(state + 6)); }
    for (int i = 0; i < 8; i++) { h2_before[i] = st.table[8 + i]; h2[i] = (int16_t)(((int32_t)st.table[8 + i] * gain) >> 14); }
    int16_t last[8] = {0};
    while (count > 0) {
        int16_t frame[8];
        for (int i = 0; i < 8; i++, in += 2) frame[i] = ds(in);
        for (int i = 0; i < 8; i++) {
            int32_t accu = frame[i] * gain + h1[i] * l1 + h2_before[i] * l2 + rdot(i, h2, frame);
            last[i] = clamp16(accu >> 14);
            ds_set(out + 2 * (uint32_t)i, last[i]);
        }
        l1 = last[6]; l2 = last[7];
        out += 16; count -= 16;
    }
    for (int i = 0; i < 4; i++) wr16(ram(state + 2 * (uint32_t)i), (uint16_t)last[4 + i]); /* the frame's last four */
}

static void resample(int init, uint32_t pitch, uint32_t state) {
    uint32_t ipos = ((st.in + DMEM_BASE) >> 1) - 4, opos = (st.out + DMEM_BASE) >> 1, accu;
    int count = (int)(align(st.count, 16) >> 1);
    if (init) { for (int k = 0; k < 4; k++) ds_set((ipos + k) * 2, 0); accu = 0; }
    else { for (int k = 0; k < 4; k++) ds_set((ipos + k) * 2, (int16_t)rd16(ram(state + 2 * k))); accu = rd16(ram(state + 8)); }
    while (count-- > 0) { /* the microcode's 4-tap filter, here linear between the middle two */
        int32_t f = (int32_t)(accu & 0xFFFF) >> 1;
        int32_t a = ds((ipos + 1) * 2), b = ds((ipos + 2) * 2);
        ds_set(opos++ * 2, clamp16((a * (32768 - f) + b * f) >> 15));
        accu += pitch;
        ipos += accu >> 16;
        accu &= 0xFFFF;
    }
    for (int k = 0; k < 4; k++) wr16(ram(state + 2 * k), (uint16_t)ds((ipos + k) * 2));
    wr16(ram(state + 8), (uint16_t)accu);
}

typedef struct { int32_t value, target, step; } Ramp;
static int16_t ramp_step(Ramp *r) {
    r->value += r->step;
    int reached = r->step <= 0 ? r->value <= r->target : r->value >= r->target;
    if (reached) { r->value = r->target; r->step = 0; }
    return (int16_t)(r->value >> 16);
}
static void envmixer(int init, int aux, uint32_t state) {
    uint32_t in = st.in + DMEM_BASE, dl = st.out + DMEM_BASE, dr = st.dry_right + DMEM_BASE;
    uint32_t wl = st.wet_left + DMEM_BASE, wr = st.wet_right + DMEM_BASE;
    Ramp ramps[2];
    int32_t seq[2], rates[2];
    int16_t dry = st.dry, wet = st.wet;
    if (init) {
        for (int i = 0; i < 2; i++) {
            ramps[i].value = st.vol[i] << 16; ramps[i].target = st.target[i] << 16;
            rates[i] = st.rate[i]; seq[i] = st.vol[i] * st.rate[i];
        }
    } else { /* the state the last task saved: 80 bytes, its own layout */
        uint32_t s = ram(state);
        wet = (int16_t)rd16(s); dry = (int16_t)rd16(s + 4);
        for (int i = 0; i < 2; i++) {
            ramps[i].target = (int32_t)rd32(s + 8 + 4 * i); rates[i] = (int32_t)rd32(s + 16 + 4 * i);
            seq[i] = (int32_t)rd32(s + 24 + 4 * i); ramps[i].value = (int32_t)rd32(s + 32 + 4 * i);
        }
    }
    for (int i = 0; i < 2; i++) ramps[i].step = ramps[i].target - ramps[i].value;
    uint32_t p = 0;
    for (uint32_t y = 0; y < st.count; y += 16) {
        for (int i = 0; i < 2; i++)
            if (ramps[i].step != 0) {
                seq[i] = (int32_t)(((int64_t)seq[i] * rates[i]) >> 16);
                ramps[i].step = (seq[i] - ramps[i].value) >> 3;
            }
        for (int x = 0; x < 8; x++, p += 2) {
            int16_t l = ramp_step(&ramps[0]), r = ramp_step(&ramps[1]), s = ds(in + p);
            int16_t g[4] = {clamp16((l * dry + 0x4000) >> 15), clamp16((r * dry + 0x4000) >> 15),
                            clamp16((l * wet + 0x4000) >> 15), clamp16((r * wet + 0x4000) >> 15)};
            uint32_t to[4] = {dl + p, dr + p, wl + p, wr + p};
            for (int k = 0; k < (aux ? 4 : 2); k++) ds_set(to[k], clamp16(ds(to[k]) + ((s * g[k]) >> 15)));
        }
    }
    uint32_t s = ram(state);
    wr16(s, (uint16_t)wet); wr16(s + 4, (uint16_t)dry);
    for (int i = 0; i < 2; i++) {
        wr32(s + 8 + 4 * i, (uint32_t)ramps[i].target); wr32(s + 16 + 4 * i, (uint32_t)rates[i]);
        wr32(s + 24 + 4 * i, (uint32_t)seq[i]); wr32(s + 32 + 4 * i, (uint32_t)ramps[i].value);
    }
}

static unsigned long unknown_commands;
void port_audio_task(uint32_t task) {
    A = port_memory();
    uint32_t list = ram(rd32(ram(task) + 48)), size = rd32(ram(task) + 52); /* OSTask's data_ptr and data_size */
    for (uint32_t pc = 0; pc + 8 <= size; pc += 8) {
        uint32_t w0 = rd32(list + pc), w1 = rd32(list + pc + 4);
        uint8_t flags = (uint8_t)(w0 >> 16);
        switch (w0 >> 24) {
        case 0x00: break; /* A_SPNOOP */
        case 0x01: adpcm(flags & 1, flags & 2, address(w1)); break;
        case 0x02: /* A_CLEARBUFF */
            for (uint32_t i = 0, d = (w0 & 0xFFFF) + DMEM_BASE; i < align(w1 & 0xFFFF, 16); i++) dmem[(d + i) & 0xFFF] = 0;
            break;
        case 0x03: envmixer(flags & 1, flags & 8, address(w1)); break;
        case 0x04: if (st.count) dram_to_dmem(st.in + DMEM_BASE, address(w1), st.count); break; /* A_LOADBUFF */
        case 0x05: resample(flags & 1, (w0 & 0xFFFF) << 1, address(w1)); break;
        case 0x06: if (st.count) dmem_to_dram(address(w1), st.out + DMEM_BASE, st.count); break; /* A_SAVEBUFF */
        case 0x07: segments[(w1 >> 24) & 0x3F] = w1 & 0xFFFFFF; break; /* A_SEGMENT */
        case 0x08: /* A_SETBUFF */
            if (flags & 8) { st.dry_right = (uint16_t)w0; st.wet_left = (uint16_t)(w1 >> 16); st.wet_right = (uint16_t)w1; }
            else { st.in = (uint16_t)w0; st.out = (uint16_t)(w1 >> 16); st.count = (uint16_t)w1; }
            break;
        case 0x09: /* A_SETVOL */
            if (flags & 8) { st.dry = (int16_t)w0; st.wet = (int16_t)w1; }
            else {
                int lr = (flags & 2) ? 0 : 1;
                if (flags & 4) st.vol[lr] = (int16_t)w0;
                else { st.target[lr] = (int16_t)w0; st.rate[lr] = (int32_t)w1; }
            }
            break;
        case 0x0A: { /* A_DMEMMOVE */
            uint32_t from = (w0 & 0xFFFF) + DMEM_BASE, to = (w1 >> 16) + DMEM_BASE, n = align(w1 & 0xFFFF, 16);
            for (uint32_t i = 0; i < n; i++) dmem[(to + i) & 0xFFF] = dmem[(from + i) & 0xFFF];
            break; }
        case 0x0B: { /* A_LOADADPCM */
            uint32_t a = address(w1), n = align(w0 & 0xFFFF, 8) / 2;
            for (uint32_t i = 0; i < n && i < 128; i++) st.table[i] = (int16_t)rd16(ram(a + 2 * i));
            break; }
        case 0x0C: { /* A_MIXER */
            int16_t gain = (int16_t)w0;
            uint32_t from = (w1 >> 16) + DMEM_BASE, to = (w1 & 0xFFFF) + DMEM_BASE, n = align(st.count, 32);
            for (uint32_t i = 0; i < n; i += 2) ds_set(to + i, clamp16(ds(to + i) + ((ds(from + i) * gain) >> 15)));
            break; }
        case 0x0D: { /* A_INTERLEAVE: left and right into the output, sample by sample */
            uint32_t l = (w1 >> 16) + DMEM_BASE, r = (w1 & 0xFFFF) + DMEM_BASE, o = st.out + DMEM_BASE;
            for (uint32_t i = 0; i < st.count; i += 2, o += 4) { ds_set(o, ds(l + i)); ds_set(o + 2, ds(r + i)); }
            break; }
        case 0x0E: polef(flags & 1, (int16_t)w0, address(w1)); break;
        case 0x0F: st.loop = address(w1); break; /* A_SETLOOP */
        default:
            if (unknown_commands++ < 4) fprintf(stderr, "audio: command %02X not known\n", w0 >> 24);
        }
    }
}

/* ---------------------------------------------------------------- the audio interface */

static uint32_t ai_addr, ai_last_len, hz = 22050;
static double play_end; /* when what's queued has played, in seconds */
static int bug_offset; /* the AI's DMA bug libultra works around: a buffer after one ending on 8 KB starts 8 KB on */
static FILE *wav;
static uint32_t wav_bytes;

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + t.tv_nsec / 1e9;
}
void port_ai_frequency(uint32_t f) { if (f >= 4000 && f <= 96000) hz = f; }

static void wav_header(void) {
    uint8_t h[44] = "RIFF\0\0\0\0WAVEfmt \x10\0\0\0\x01\0\x02\0\0\0\0\0\0\0\0\0\x04\0\x10\0data\0\0\0\0";
    uint32_t v[] = {36 + wav_bytes, hz, hz * 4, wav_bytes};
    int at[] = {4, 24, 28, 40};
    for (int i = 0; i < 4; i++) for (int k = 0; k < 4; k++) h[at[i] + k] = (uint8_t)(v[i] >> (8 * k));
    fseek(wav, 0, SEEK_SET);
    fwrite(h, 1, 44, wav);
    fseek(wav, 0, SEEK_END);
}

static void play(uint32_t phys, uint32_t len) {
    A = port_memory();
    int frames = (int)(len / 4);
    if (frames <= 0) return;
    int16_t *lr = malloc((size_t)frames * 4);
    for (int i = 0; i < frames * 2; i++) lr[i] = (int16_t)rd16(ram(phys + 2 * (uint32_t)i));
    port_live_audio(lr, frames, (int)hz);
    static int opened;
    if (!opened) { opened = 1; const char *p = getenv("PORT_AUDIO"); if (p && (wav = fopen(p, "wb+"))) wav_header(); }
    if (wav) {
        fwrite(lr, 4, (size_t)frames, wav); /* WAV is little-endian, as the host is */
        wav_bytes += (uint32_t)frames * 4;
        wav_header();
        fflush(wav);
    }
    free(lr);
    double t = now();
    if (play_end < t) play_end = t;
    play_end += (double)frames / hz;
}

/* AI_DRAM_ADDR, AI_LEN, AI_CONTROL, AI_STATUS, AI_DACRATE, AI_BITRATE: 1 when addr is one of them. */
int port_ai_register(uint32_t addr, int write, uint32_t value, uint32_t *read) {
    if (addr < 0xA4500000u || addr > 0xA4500014u) return 0;
    double left = play_end - now();
    uint32_t queued = left > 0 ? (uint32_t)(left * hz) * 4 : 0;
    switch (addr & 0x1F) {
    case 0x00: if (write) ai_addr = value & 0x1FFFFFFF; else *read = ai_addr; break;
    case 0x04:
        if (write) {
            uint32_t len = value & 0x3FFF8;
            play(ai_addr + (bug_offset ? 0x2000 : 0), len);
            bug_offset = ((ai_addr + (bug_offset ? 0x2000 : 0) + len) & 0x1FFF) == 0;
            ai_last_len = len;
        } else *read = queued < ai_last_len ? queued : ai_last_len; /* what's left of the buffer playing */
        break;
    case 0x0C: /* full when a buffer waits behind the one playing; busy while one plays */
        if (!write) *read = (queued > ai_last_len ? 0x80000001u : 0) | (queued ? 0x40000000u : 0);
        break;
    case 0x10: if (write && value) port_ai_frequency(49656530u / (value + 1)); else if (read) *read = 0; break;
    default: if (read) *read = 0;
    }
    return 1;
}
