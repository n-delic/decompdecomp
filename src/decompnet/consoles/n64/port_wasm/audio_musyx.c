/* * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * *
 *   Mupen64plus-rsp-hle - musyx.c                                         *
 *   Mupen64Plus homepage: https://mupen64plus.org/                        *
 *   Copyright (C) 2013 Bobby Smiles                                       *
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 *   This program is distributed in the hope that it will be useful,       *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU General Public License for more details.                          *
 *                                                                         *
 *   You should have received a copy of the GNU General Public License     *
 *   along with this program; if not, write to the                         *
 *   Free Software Foundation, Inc.,                                       *
 *   51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.          *
 * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * */

/* Adapted from Mupen64Plus RSP HLE at 4cdc3051efbae9cd615f5fea37c62e5ab15a1ded.
   The port stores console RAM in big-endian byte order. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

static uint8_t read8(const uint8_t *memory, uint32_t addr) { return memory[0x80000000u | (addr & 0x7FFFFFu)]; }
static uint16_t read16(const uint8_t *memory, uint32_t addr) {
    return ((uint16_t)read8(memory, addr) << 8) | read8(memory, addr + 1);
}
static uint32_t read32(const uint8_t *memory, uint32_t addr) {
    return ((uint32_t)read16(memory, addr) << 16) | read16(memory, addr + 2);
}
static void write16(uint8_t *memory, uint32_t addr, uint16_t word) {
    memory[0x80000000u | (addr & 0x7FFFFFu)] = (uint8_t)(word >> 8);
    memory[0x80000000u | ((addr + 1) & 0x7FFFFFu)] = (uint8_t)word;
}
static void dram_load_u8(uint8_t *memory, uint8_t *out, uint32_t addr, size_t n) {
    for (size_t i = 0; i < n; i++) out[i] = read8(memory, addr + i);
}
static void dram_load_u16(uint8_t *memory, uint16_t *out, uint32_t addr, size_t n) {
    for (size_t i = 0; i < n; i++) out[i] = read16(memory, addr + 2*i);
}
static void dram_load_u32(uint8_t *memory, uint32_t *out, uint32_t addr, size_t n) {
    for (size_t i = 0; i < n; i++) out[i] = read32(memory, addr + 4*i);
}
static void dram_store_u16(uint8_t *memory, const uint16_t *in, uint32_t addr, size_t n) {
    for (size_t i = 0; i < n; i++) write16(memory, addr + 2*i, in[i]);
}

static unsigned align(unsigned value, unsigned size) { return (value + size - 1) & ~(size - 1); }
static int16_t clamp_s16(int32_t value) { return value < -32768 ? -32768 : value > 32767 ? 32767 : (int16_t)value; }
static int16_t adpcm_predict_sample(uint8_t byte, uint8_t mask, unsigned left, unsigned right) {
    return ((int16_t)((byte & mask) << left)) >> right;
}
static void adpcm_compute_residuals(int16_t *dst, const int16_t *src, const int16_t *book,
                                    const int16_t *last, size_t count) {
    int16_t l1 = last[0], l2 = last[1];
    for (size_t i = 0; i < count; i++) {
        int64_t sum = (int64_t)src[i] * 2048 + book[i] * l1 + book[8+i] * l2;
        for (size_t k = 0; k < i; k++) sum += book[8+k] * src[i-k-1];
        dst[i] = clamp_s16((int32_t)(sum >> 11));
    }
}

static const int16_t RESAMPLE_LUT[64 * 4] = {
    (int16_t)0x0c39, (int16_t)0x66ad, (int16_t)0x0d46, (int16_t)0xffdf,
    (int16_t)0x0b39, (int16_t)0x6696, (int16_t)0x0e5f, (int16_t)0xffd8,
    (int16_t)0x0a44, (int16_t)0x6669, (int16_t)0x0f83, (int16_t)0xffd0,
    (int16_t)0x095a, (int16_t)0x6626, (int16_t)0x10b4, (int16_t)0xffc8,
    (int16_t)0x087d, (int16_t)0x65cd, (int16_t)0x11f0, (int16_t)0xffbf,
    (int16_t)0x07ab, (int16_t)0x655e, (int16_t)0x1338, (int16_t)0xffb6,
    (int16_t)0x06e4, (int16_t)0x64d9, (int16_t)0x148c, (int16_t)0xffac,
    (int16_t)0x0628, (int16_t)0x643f, (int16_t)0x15eb, (int16_t)0xffa1,
    (int16_t)0x0577, (int16_t)0x638f, (int16_t)0x1756, (int16_t)0xff96,
    (int16_t)0x04d1, (int16_t)0x62cb, (int16_t)0x18cb, (int16_t)0xff8a,
    (int16_t)0x0435, (int16_t)0x61f3, (int16_t)0x1a4c, (int16_t)0xff7e,
    (int16_t)0x03a4, (int16_t)0x6106, (int16_t)0x1bd7, (int16_t)0xff71,
    (int16_t)0x031c, (int16_t)0x6007, (int16_t)0x1d6c, (int16_t)0xff64,
    (int16_t)0x029f, (int16_t)0x5ef5, (int16_t)0x1f0b, (int16_t)0xff56,
    (int16_t)0x022a, (int16_t)0x5dd0, (int16_t)0x20b3, (int16_t)0xff48,
    (int16_t)0x01be, (int16_t)0x5c9a, (int16_t)0x2264, (int16_t)0xff3a,
    (int16_t)0x015b, (int16_t)0x5b53, (int16_t)0x241e, (int16_t)0xff2c,
    (int16_t)0x0101, (int16_t)0x59fc, (int16_t)0x25e0, (int16_t)0xff1e,
    (int16_t)0x00ae, (int16_t)0x5896, (int16_t)0x27a9, (int16_t)0xff10,
    (int16_t)0x0063, (int16_t)0x5720, (int16_t)0x297a, (int16_t)0xff02,
    (int16_t)0x001f, (int16_t)0x559d, (int16_t)0x2b50, (int16_t)0xfef4,
    (int16_t)0xffe2, (int16_t)0x540d, (int16_t)0x2d2c, (int16_t)0xfee8,
    (int16_t)0xffac, (int16_t)0x5270, (int16_t)0x2f0d, (int16_t)0xfedb,
    (int16_t)0xff7c, (int16_t)0x50c7, (int16_t)0x30f3, (int16_t)0xfed0,
    (int16_t)0xff53, (int16_t)0x4f14, (int16_t)0x32dc, (int16_t)0xfec6,
    (int16_t)0xff2e, (int16_t)0x4d57, (int16_t)0x34c8, (int16_t)0xfebd,
    (int16_t)0xff0f, (int16_t)0x4b91, (int16_t)0x36b6, (int16_t)0xfeb6,
    (int16_t)0xfef5, (int16_t)0x49c2, (int16_t)0x38a5, (int16_t)0xfeb0,
    (int16_t)0xfedf, (int16_t)0x47ed, (int16_t)0x3a95, (int16_t)0xfeac,
    (int16_t)0xfece, (int16_t)0x4611, (int16_t)0x3c85, (int16_t)0xfeab,
    (int16_t)0xfec0, (int16_t)0x4430, (int16_t)0x3e74, (int16_t)0xfeac,
    (int16_t)0xfeb6, (int16_t)0x424a, (int16_t)0x4060, (int16_t)0xfeaf,
    (int16_t)0xfeaf, (int16_t)0x4060, (int16_t)0x424a, (int16_t)0xfeb6,
    (int16_t)0xfeac, (int16_t)0x3e74, (int16_t)0x4430, (int16_t)0xfec0,
    (int16_t)0xfeab, (int16_t)0x3c85, (int16_t)0x4611, (int16_t)0xfece,
    (int16_t)0xfeac, (int16_t)0x3a95, (int16_t)0x47ed, (int16_t)0xfedf,
    (int16_t)0xfeb0, (int16_t)0x38a5, (int16_t)0x49c2, (int16_t)0xfef5,
    (int16_t)0xfeb6, (int16_t)0x36b6, (int16_t)0x4b91, (int16_t)0xff0f,
    (int16_t)0xfebd, (int16_t)0x34c8, (int16_t)0x4d57, (int16_t)0xff2e,
    (int16_t)0xfec6, (int16_t)0x32dc, (int16_t)0x4f14, (int16_t)0xff53,
    (int16_t)0xfed0, (int16_t)0x30f3, (int16_t)0x50c7, (int16_t)0xff7c,
    (int16_t)0xfedb, (int16_t)0x2f0d, (int16_t)0x5270, (int16_t)0xffac,
    (int16_t)0xfee8, (int16_t)0x2d2c, (int16_t)0x540d, (int16_t)0xffe2,
    (int16_t)0xfef4, (int16_t)0x2b50, (int16_t)0x559d, (int16_t)0x001f,
    (int16_t)0xff02, (int16_t)0x297a, (int16_t)0x5720, (int16_t)0x0063,
    (int16_t)0xff10, (int16_t)0x27a9, (int16_t)0x5896, (int16_t)0x00ae,
    (int16_t)0xff1e, (int16_t)0x25e0, (int16_t)0x59fc, (int16_t)0x0101,
    (int16_t)0xff2c, (int16_t)0x241e, (int16_t)0x5b53, (int16_t)0x015b,
    (int16_t)0xff3a, (int16_t)0x2264, (int16_t)0x5c9a, (int16_t)0x01be,
    (int16_t)0xff48, (int16_t)0x20b3, (int16_t)0x5dd0, (int16_t)0x022a,
    (int16_t)0xff56, (int16_t)0x1f0b, (int16_t)0x5ef5, (int16_t)0x029f,
    (int16_t)0xff64, (int16_t)0x1d6c, (int16_t)0x6007, (int16_t)0x031c,
    (int16_t)0xff71, (int16_t)0x1bd7, (int16_t)0x6106, (int16_t)0x03a4,
    (int16_t)0xff7e, (int16_t)0x1a4c, (int16_t)0x61f3, (int16_t)0x0435,
    (int16_t)0xff8a, (int16_t)0x18cb, (int16_t)0x62cb, (int16_t)0x04d1,
    (int16_t)0xff96, (int16_t)0x1756, (int16_t)0x638f, (int16_t)0x0577,
    (int16_t)0xffa1, (int16_t)0x15eb, (int16_t)0x643f, (int16_t)0x0628,
    (int16_t)0xffac, (int16_t)0x148c, (int16_t)0x64d9, (int16_t)0x06e4,
    (int16_t)0xffb6, (int16_t)0x1338, (int16_t)0x655e, (int16_t)0x07ab,
    (int16_t)0xffbf, (int16_t)0x11f0, (int16_t)0x65cd, (int16_t)0x087d,
    (int16_t)0xffc8, (int16_t)0x10b4, (int16_t)0x6626, (int16_t)0x095a,
    (int16_t)0xffd0, (int16_t)0x0f83, (int16_t)0x6669, (int16_t)0x0a44,
    (int16_t)0xffd8, (int16_t)0x0e5f, (int16_t)0x6696, (int16_t)0x0b39,
    (int16_t)0xffdf, (int16_t)0x0d46, (int16_t)0x66ad, (int16_t)0x0c39
};

/* various constants */
enum { SUBFRAME_SIZE = 192 };
enum { MAX_VOICES = 32 };

enum { SAMPLE_BUFFER_SIZE = 0x200 };

enum {
    SFD_SFX_INDEX       = 0x2,
    SFD_VOICE_BITMASK   = 0x4,
    SFD_STATE_PTR       = 0x8,
    SFD_SFX_PTR         = 0xc,
    SFD_VOICES          = 0x10,

};

enum {
    VOICE_ENV_BEGIN         = 0x00,
    VOICE_ENV_STEP          = 0x10,
    VOICE_PITCH_Q16         = 0x20,
    VOICE_PITCH_SHIFT       = 0x22,
    VOICE_CATSRC_0          = 0x24,
    VOICE_CATSRC_1          = 0x30,
    VOICE_ADPCM_FRAMES      = 0x3c,
    VOICE_SKIP_SAMPLES      = 0x3e,

    /* for PCM16 */
    VOICE_U16_40            = 0x40,
    VOICE_U16_42            = 0x42,

    /* for ADPCM */
    VOICE_ADPCM_TABLE_PTR   = 0x40,

    VOICE_INTERLEAVED_PTR   = 0x44,
    VOICE_END_POINT         = 0x48,
    VOICE_RESTART_POINT     = 0x4a,
    VOICE_U16_4E            = 0x4e,

    VOICE_SIZE              = 0x50
};

enum {
    CATSRC_PTR1     = 0x00,
    CATSRC_PTR2     = 0x04,
    CATSRC_SIZE1    = 0x08,
    CATSRC_SIZE2    = 0x0a
};

enum {
    STATE_LAST_SAMPLE   = 0x0,
    STATE_BASE_VOL      = 0x100,
    STATE_CC0           = 0x110,
    STATE_740_LAST4_V1  = 0x290
};

enum {
    SFX_CBUFFER_PTR     = 0x00,
    SFX_CBUFFER_LENGTH  = 0x04,
    SFX_TAP_COUNT       = 0x08,
    SFX_FIR4_HGAIN      = 0x0a,
    SFX_TAP_DELAYS      = 0x0c,
    SFX_TAP_GAINS       = 0x2c,
    SFX_FIR4_HCOEFFS    = 0x40
};

/* struct definition */
typedef struct {
    /* internal subframes */
    int16_t left[SUBFRAME_SIZE];
    int16_t right[SUBFRAME_SIZE];
    int16_t cc0[SUBFRAME_SIZE];
    int16_t e50[SUBFRAME_SIZE];

    /* internal subframes base volumes */
    int32_t base_vol[4];

    /* */
    int16_t subframe_740_last4[4];
} musyx_t;

/* helper functions prototypes */
static void load_base_vol(uint8_t *memory, int32_t *base_vol, uint32_t address);
static void save_base_vol(uint8_t *memory, const int32_t *base_vol, uint32_t address);
static void update_base_vol(uint8_t *memory, int32_t *base_vol,
                            uint32_t voice_mask, uint32_t last_sample_ptr);

static void init_subframes_v1(musyx_t *musyx);

static uint32_t voice_stage(uint8_t *memory, musyx_t *musyx,
                            uint32_t voice_ptr, uint32_t last_sample_ptr);

static void dma_cat8(uint8_t *memory, uint8_t *dst, size_t capacity, uint32_t catsrc_ptr);
static void dma_cat16(uint8_t *memory, uint16_t *dst, size_t capacity, uint32_t catsrc_ptr);

static void load_samples_PCM16(uint8_t *memory, uint32_t voice_ptr, int16_t *samples,
                               unsigned *segbase, unsigned *offset);
static void load_samples_ADPCM(uint8_t *memory, uint32_t voice_ptr, int16_t *samples,
                               unsigned *segbase, unsigned *offset);

static void adpcm_decode_frames(int16_t *dst, const uint8_t *src,
                                const int16_t *table, uint8_t count,
                                uint8_t skip_samples);

static void adpcm_predict_frame(int16_t *dst, const uint8_t *src,
                                const uint8_t *nibbles,
                                unsigned int rshift);

static void mix_voice_samples(uint8_t *memory, musyx_t *musyx,
                              uint32_t voice_ptr, const int16_t *samples,
                              unsigned segbase, unsigned offset, uint32_t last_sample_ptr);

static void sfx_stage(uint8_t *memory, musyx_t *musyx, uint32_t sfx_ptr, uint16_t idx);

static void mix_sfx_with_main_subframes(musyx_t *musyx, const int16_t *subframe);

static void mix_samples(int16_t *y, int16_t x, int16_t hgain);
static void mix_subframes(int16_t *y, const int16_t *x, int16_t hgain);
static void mix_fir4(int16_t *y, const int16_t *x, int16_t hgain, const int16_t *hcoeffs);

static void interleave_stage_v1(uint8_t *memory, musyx_t *musyx,
                                uint32_t output_ptr);

static int32_t dot4(const int16_t *x, const int16_t *y)
{
    size_t i;
    int32_t accu = 0;

    for (i = 0; i < 4; ++i)
        accu = clamp_s16(accu + (((int32_t)x[i] * (int32_t)y[i]) >> 15));

    return accu;
}

/**************************************************************************
 * MusyX v1 audio ucode
 **************************************************************************/
void port_musyx_v1(uint8_t *memory, uint32_t sfd_ptr, uint32_t sfd_count)
{
    if (!sfd_count || sfd_count > 32)
        return;
    uint32_t state_ptr;
    musyx_t musyx;

    state_ptr = read32(memory, sfd_ptr + SFD_STATE_PTR);

    /* load initial state */
    load_base_vol(memory, musyx.base_vol, state_ptr + STATE_BASE_VOL);
    dram_load_u16(memory, (uint16_t *)musyx.cc0, state_ptr + STATE_CC0, SUBFRAME_SIZE);
    dram_load_u16(memory, (uint16_t *)musyx.subframe_740_last4, state_ptr + STATE_740_LAST4_V1, 4);

    for (;;) {
        /* parse SFD structure */
        uint16_t sfx_index   = read16(memory, sfd_ptr + SFD_SFX_INDEX);
        uint32_t voice_mask  = read32(memory, sfd_ptr + SFD_VOICE_BITMASK);
        uint32_t sfx_ptr     = read32(memory, sfd_ptr + SFD_SFX_PTR);
        uint32_t voice_ptr       = sfd_ptr + SFD_VOICES;
        uint32_t last_sample_ptr = state_ptr + STATE_LAST_SAMPLE;
        uint32_t output_ptr;

        /* initialize internal subframes using updated base volumes */
        update_base_vol(memory, musyx.base_vol, voice_mask, last_sample_ptr);
        init_subframes_v1(&musyx);

        /* active voices get mixed into L,R,cc0,e50 subframes (optional) */
        output_ptr = voice_stage(memory, &musyx, voice_ptr, last_sample_ptr);

        /* apply delay-based effects (optional) */
        sfx_stage(memory, &musyx, sfx_ptr, sfx_index);

        /* emit interleaved L,R subframes */
        if (output_ptr) interleave_stage_v1(memory, &musyx, output_ptr);

        --sfd_count;
        if (sfd_count == 0)
            break;

        sfd_ptr += SFD_VOICES + MAX_VOICES * VOICE_SIZE;
        state_ptr = read32(memory, sfd_ptr + SFD_STATE_PTR);
    }

    /* writeback updated state */
    save_base_vol(memory, musyx.base_vol, state_ptr + STATE_BASE_VOL);
    dram_store_u16(memory, (uint16_t *)musyx.cc0, state_ptr + STATE_CC0, SUBFRAME_SIZE);
    dram_store_u16(memory, (uint16_t *)musyx.subframe_740_last4, state_ptr + STATE_740_LAST4_V1, 4);
}

static void load_base_vol(uint8_t *memory, int32_t *base_vol, uint32_t address)
{
    base_vol[0] = ((uint32_t)(read16(memory, address))     << 16) | (read16(memory, address +  8));
    base_vol[1] = ((uint32_t)(read16(memory, address + 2)) << 16) | (read16(memory, address + 10));
    base_vol[2] = ((uint32_t)(read16(memory, address + 4)) << 16) | (read16(memory, address + 12));
    base_vol[3] = ((uint32_t)(read16(memory, address + 6)) << 16) | (read16(memory, address + 14));
}

static void save_base_vol(uint8_t *memory, const int32_t *base_vol, uint32_t address)
{
    unsigned k;

    for (k = 0; k < 4; ++k) {
        write16(memory, address, (uint16_t)(base_vol[k] >> 16));
        address += 2;
    }

    for (k = 0; k < 4; ++k) {
        write16(memory, address, (uint16_t)base_vol[k]);
        address += 2;
    }
}

static void update_base_vol(uint8_t *memory, int32_t *base_vol,
                            uint32_t voice_mask, uint32_t last_sample_ptr)
{
    unsigned i, k;
    uint32_t mask;

    /* optim: skip voices contributions entirely if voice_mask is empty */
    if (voice_mask != 0) {
        for (i = 0, mask = 1; i < MAX_VOICES;
             ++i, mask <<= 1, last_sample_ptr += 8) {
            if ((voice_mask & mask) == 0)
                continue;

            for (k = 0; k < 4; ++k)
                base_vol[k] += (int16_t)read16(memory, last_sample_ptr + k * 2);
        }
    }

    /* apply 3% decay */
    for (k = 0; k < 4; ++k)
        base_vol[k] = ((int64_t)base_vol[k] * 0x0000f850) >> 16;

}

static void init_subframes_v1(musyx_t *musyx)
{
    unsigned i;

    int16_t base_cc0 = clamp_s16(musyx->base_vol[2]);
    int16_t base_e50 = clamp_s16(musyx->base_vol[3]);

    int16_t *left  = musyx->left;
    int16_t *right = musyx->right;
    int16_t *cc0   = musyx->cc0;
    int16_t *e50   = musyx->e50;

    for (i = 0; i < SUBFRAME_SIZE; ++i) {
        *(e50++)    = base_e50;
        *(left++)   = clamp_s16(*cc0 + base_cc0);
        *(right++)  = clamp_s16(-*cc0 - base_cc0);
        *(cc0++)    = 0;
    }
}

/* Process voices, and returns interleaved subframe destination address */
static uint32_t voice_stage(uint8_t *memory, musyx_t *musyx,
                            uint32_t voice_ptr, uint32_t last_sample_ptr)
{
    uint32_t output_ptr;

    /* voice stage can be skipped if first voice has no samples */
    if (read16(memory, voice_ptr + VOICE_CATSRC_0 + CATSRC_SIZE1) == 0) {
        output_ptr = read32(memory, voice_ptr + VOICE_INTERLEAVED_PTR);
    } else {
        /* otherwise process voices until a non null output_ptr is encountered */
        output_ptr = 0;
        for (unsigned i = 0; i < MAX_VOICES; i++) {
            /* load voice samples (PCM16 or APDCM) */
            int16_t samples[SAMPLE_BUFFER_SIZE] = {0};
            unsigned segbase;
            unsigned offset;

            if (read8(memory, voice_ptr + VOICE_ADPCM_FRAMES) == 0)
                load_samples_PCM16(memory, voice_ptr, samples, &segbase, &offset);
            else
                load_samples_ADPCM(memory, voice_ptr, samples, &segbase, &offset);

            /* mix them with each internal subframes */
            mix_voice_samples(memory, musyx, voice_ptr, samples, segbase, offset,
                              last_sample_ptr + i * 8);

            /* check break condition */
            output_ptr = read32(memory, voice_ptr + VOICE_INTERLEAVED_PTR);
            if (output_ptr != 0)
                break;

            /* next voice */
            voice_ptr += VOICE_SIZE;
        }
    }

    return output_ptr;
}

static void dma_cat8(uint8_t *memory, uint8_t *dst, size_t capacity, uint32_t catsrc_ptr)
{
    uint32_t ptr1  = read32(memory, catsrc_ptr + CATSRC_PTR1);
    uint32_t ptr2  = read32(memory, catsrc_ptr + CATSRC_PTR2);
    uint16_t size1 = read16(memory, catsrc_ptr + CATSRC_SIZE1);
    uint16_t size2 = read16(memory, catsrc_ptr + CATSRC_SIZE2);

    size_t count1 = size1;
    size_t count2 = size2;

    if (count1 > capacity) count1 = capacity;
    if (count2 > capacity - count1) count2 = capacity - count1;
    dram_load_u8(memory, dst, ptr1, count1);

    if (size2 == 0)
        return;

    dram_load_u8(memory, dst + count1, ptr2, count2);
}

static void dma_cat16(uint8_t *memory, uint16_t *dst, size_t capacity, uint32_t catsrc_ptr)
{
    uint32_t ptr1  = read32(memory, catsrc_ptr + CATSRC_PTR1);
    uint32_t ptr2  = read32(memory, catsrc_ptr + CATSRC_PTR2);
    uint16_t size1 = read16(memory, catsrc_ptr + CATSRC_SIZE1);
    uint16_t size2 = read16(memory, catsrc_ptr + CATSRC_SIZE2);

    size_t count1 = size1 >> 1;
    size_t count2 = size2 >> 1;

    if (count1 > capacity) count1 = capacity;
    if (count2 > capacity - count1) count2 = capacity - count1;
    dram_load_u16(memory, dst, ptr1, count1);

    if (size2 == 0)
        return;

    dram_load_u16(memory, dst + count1, ptr2, count2);
}

static void load_samples_PCM16(uint8_t *memory, uint32_t voice_ptr, int16_t *samples,
                               unsigned *segbase, unsigned *offset)
{

    uint8_t  u8_3e  = read8(memory, voice_ptr + VOICE_SKIP_SAMPLES);
    uint16_t u16_40 = read16(memory, voice_ptr + VOICE_U16_40);
    uint16_t u16_42 = read16(memory, voice_ptr + VOICE_U16_42);

    unsigned count = align(u16_40 + u8_3e, 4);

    if (count > SAMPLE_BUFFER_SIZE) count = SAMPLE_BUFFER_SIZE;
    *segbase = SAMPLE_BUFFER_SIZE - count;
    *offset  = u8_3e;

    dma_cat16(memory, (uint16_t *)samples + *segbase, count, voice_ptr + VOICE_CATSRC_0);

    if (u16_42 != 0)
        dma_cat16(memory, (uint16_t *)samples, SAMPLE_BUFFER_SIZE, voice_ptr + VOICE_CATSRC_1);
}

static void load_samples_ADPCM(uint8_t *memory, uint32_t voice_ptr, int16_t *samples,
                               unsigned *segbase, unsigned *offset)
{
    /* Each pass reads at most 16 frames. Leave room for the initial skipped frame. */
    uint8_t buffer[17 * 24] = {0};
    int16_t adpcm_table[128];

    uint8_t u8_3c = read8(memory, voice_ptr + VOICE_ADPCM_FRAMES    );
    uint8_t u8_3d = read8(memory, voice_ptr + VOICE_ADPCM_FRAMES + 1);
    uint8_t u8_3e = read8(memory, voice_ptr + VOICE_SKIP_SAMPLES    );
    uint8_t u8_3f = read8(memory, voice_ptr + VOICE_SKIP_SAMPLES + 1);
    uint32_t adpcm_table_ptr = read32(memory, voice_ptr + VOICE_ADPCM_TABLE_PTR);
    unsigned count;

    dram_load_u16(memory, (uint16_t *)adpcm_table, adpcm_table_ptr, 128);

    if (u8_3c > SAMPLE_BUFFER_SIZE / 32) u8_3c = SAMPLE_BUFFER_SIZE / 32;
    if (u8_3d > SAMPLE_BUFFER_SIZE / 32) u8_3d = SAMPLE_BUFFER_SIZE / 32;
    count = u8_3c << 5;

    *segbase = SAMPLE_BUFFER_SIZE - count;
    *offset  = u8_3e & 0x1f;

    dma_cat8(memory, buffer, sizeof buffer, voice_ptr + VOICE_CATSRC_0);
    adpcm_decode_frames(samples + *segbase, buffer, adpcm_table, u8_3c, u8_3e);

    if (u8_3d != 0) {
        dma_cat8(memory, buffer, sizeof buffer, voice_ptr + VOICE_CATSRC_1);
        adpcm_decode_frames(samples, buffer, adpcm_table, u8_3d, u8_3f);
    }
}

static void adpcm_decode_frames(int16_t *dst, const uint8_t *src,
                                const int16_t *table, uint8_t count,
                                uint8_t skip_samples)
{
    int16_t frame[32];
    const uint8_t *nibbles = src + 8;
    unsigned i;
    bool jump_gap = false;

    if (skip_samples >= 32) {
        jump_gap = true;
        nibbles += 16;
        src += 4;
    }

    for (i = 0; i < count; ++i) {
        uint8_t c2 = nibbles[0];

        const int16_t *book = (c2 & 0xf0) + table;
        unsigned int rshift = (c2 & 0x0f);

        adpcm_predict_frame(frame, src, nibbles, rshift);

        memcpy(dst, frame, 2 * sizeof(frame[0]));
        adpcm_compute_residuals(dst +  2, frame +  2, book, dst     , 6);
        adpcm_compute_residuals(dst +  8, frame +  8, book, dst +  6, 8);
        adpcm_compute_residuals(dst + 16, frame + 16, book, dst + 14, 8);
        adpcm_compute_residuals(dst + 24, frame + 24, book, dst + 22, 8);

        if (jump_gap) {
            nibbles += 8;
            src += 32;
        }

        jump_gap = !jump_gap;
        nibbles += 16;
        src += 4;
        dst += 32;
    }
}

static void adpcm_predict_frame(int16_t *dst, const uint8_t *src,
                                const uint8_t *nibbles,
                                unsigned int rshift)
{
    unsigned int i;

    *(dst++) = (src[0] << 8) | src[1];
    *(dst++) = (src[2] << 8) | src[3];

    for (i = 1; i < 16; ++i) {
        uint8_t byte = nibbles[i];

        *(dst++) = adpcm_predict_sample(byte, 0xf0,  8, rshift);
        *(dst++) = adpcm_predict_sample(byte, 0x0f, 12, rshift);
    }
}

static void mix_voice_samples(uint8_t *memory, musyx_t *musyx,
                              uint32_t voice_ptr, const int16_t *samples,
                              unsigned segbase, unsigned offset, uint32_t last_sample_ptr)
{
    int i, k;

    /* parse VOICE structure */
    const uint16_t pitch_q16   = read16(memory, voice_ptr + VOICE_PITCH_Q16);
    const uint16_t pitch_shift = read16(memory, voice_ptr + VOICE_PITCH_SHIFT); /* Q4.12 */

    const uint16_t end_point     = read16(memory, voice_ptr + VOICE_END_POINT);
    const uint16_t restart_point = read16(memory, voice_ptr + VOICE_RESTART_POINT);

    const uint16_t u16_4e = read16(memory, voice_ptr + VOICE_U16_4E);

    /* init values and pointers */
    unsigned sample = segbase + offset + u16_4e;
    unsigned sample_end = segbase + end_point;
    unsigned sample_restart = (restart_point & 0x7fff) + (restart_point & 0x8000 ? 0 : segbase);

    uint32_t pitch_accu = pitch_q16;
    uint32_t pitch_step = pitch_shift << 4;

    uint32_t v4_env[4];
    uint32_t v4_env_step[4];
    int16_t *v4_dst[4];
    int16_t  v4[4];

    dram_load_u32(memory, (uint32_t *)v4_env,      voice_ptr + VOICE_ENV_BEGIN, 4);
    dram_load_u32(memory, (uint32_t *)v4_env_step, voice_ptr + VOICE_ENV_STEP,  4);

    v4_dst[0] = musyx->left;
    v4_dst[1] = musyx->right;
    v4_dst[2] = musyx->cc0;
    v4_dst[3] = musyx->e50;

    for (i = 0; i < SUBFRAME_SIZE; ++i) {
        /* update sample and lut pointers and then pitch_accu */
        const int16_t *lut = (RESAMPLE_LUT + ((pitch_accu & 0xfc00) >> 8));
        int16_t v;

        sample += (pitch_accu >> 16);
        pitch_accu &= 0xffff;
        pitch_accu += pitch_step;

        /* handle end/restart points */
        if (sample >= sample_end)
            sample = sample_restart + sample - sample_end;

        /* apply resample filter */
        v = sample <= SAMPLE_BUFFER_SIZE - 4 ? clamp_s16(dot4(samples + sample, lut)) : 0;

        for (k = 0; k < 4; ++k) {
            /* envmix */
            int32_t accu = (v * ((int32_t)v4_env[k] >> 16)) >> 15;
            v4[k] = clamp_s16(accu);
            *(v4_dst[k]) = clamp_s16(accu + *(v4_dst[k]));

            /* update envelopes and dst pointers */
            ++(v4_dst[k]);
            v4_env[k] += v4_env_step[k];
        }
    }

    /* save last resampled sample */
    dram_store_u16(memory, (uint16_t *)v4, last_sample_ptr, 4);

}

static void sfx_stage(uint8_t *memory, musyx_t *musyx, uint32_t sfx_ptr, uint16_t idx)
{
    unsigned int i;

    int16_t buffer[SUBFRAME_SIZE + 4];
    int16_t *subframe = buffer + 4;

    uint32_t tap_delays[8];
    int16_t tap_gains[8];
    int16_t fir4_hcoeffs[4];

    int16_t delayed[SUBFRAME_SIZE];
    uint32_t dpos, dlength;

    const uint32_t pos = idx * SUBFRAME_SIZE;

    uint32_t cbuffer_ptr;
    uint32_t cbuffer_length;
    uint16_t tap_count;
    int16_t fir4_hgain;

    if (sfx_ptr == 0)
        return;

    /* load sfx  parameters */
    cbuffer_ptr    = read32(memory, sfx_ptr + SFX_CBUFFER_PTR);
    cbuffer_length = read32(memory, sfx_ptr + SFX_CBUFFER_LENGTH);

    tap_count      = read16(memory, sfx_ptr + SFX_TAP_COUNT);
    if (tap_count > 8 || pos + SUBFRAME_SIZE > cbuffer_length || cbuffer_length > 0x400000u) return;

    dram_load_u32(memory, tap_delays, sfx_ptr + SFX_TAP_DELAYS, 8);
    dram_load_u16(memory, (uint16_t *)tap_gains,  sfx_ptr + SFX_TAP_GAINS,  8);

    fir4_hgain     = read16(memory, sfx_ptr + SFX_FIR4_HGAIN);
    dram_load_u16(memory, (uint16_t *)fir4_hcoeffs, sfx_ptr + SFX_FIR4_HCOEFFS, 4);

    /* mix up to 8 delayed subframes */
    memset(subframe, 0, SUBFRAME_SIZE * sizeof(subframe[0]));
    for (i = 0; i < tap_count; ++i) {

        uint32_t delay = tap_delays[i] % cbuffer_length;
        dpos = pos >= delay ? pos - delay : cbuffer_length - (delay - pos);
        dlength = SUBFRAME_SIZE;

        if (cbuffer_length - dpos < SUBFRAME_SIZE) {
            dlength = cbuffer_length - dpos;
            dram_load_u16(memory, (uint16_t *)delayed + dlength, cbuffer_ptr, SUBFRAME_SIZE - dlength);
        }

        dram_load_u16(memory, (uint16_t *)delayed, cbuffer_ptr + dpos * 2, dlength);

        mix_subframes(subframe, delayed, tap_gains[i]);
    }

    /* add resulting subframe to main subframes */
    mix_sfx_with_main_subframes(musyx, subframe);

    /* apply FIR4 filter and writeback filtered result */
    memcpy(buffer, musyx->subframe_740_last4, 4 * sizeof(int16_t));
    memcpy(musyx->subframe_740_last4, subframe + SUBFRAME_SIZE - 4, 4 * sizeof(int16_t));
    mix_fir4(musyx->e50, buffer + 1, fir4_hgain, fir4_hcoeffs);
    dram_store_u16(memory, (uint16_t *)musyx->e50, cbuffer_ptr + pos * 2, SUBFRAME_SIZE);
}

static void mix_sfx_with_main_subframes(musyx_t *musyx, const int16_t *subframe)
{
    unsigned i;

    for (i = 0; i < SUBFRAME_SIZE; ++i) {
        int16_t v = subframe[i];
        musyx->left[i]  = clamp_s16(musyx->left[i]  + v);
        musyx->right[i] = clamp_s16(musyx->right[i] + v);
    }
}

static void mix_samples(int16_t *y, int16_t x, int16_t hgain)
{
    *y = clamp_s16(*y + ((x * hgain + 0x4000) >> 15));
}

static void mix_subframes(int16_t *y, const int16_t *x, int16_t hgain)
{
    unsigned int i;

    for (i = 0; i < SUBFRAME_SIZE; ++i)
        mix_samples(&y[i], x[i], hgain);
}

static void mix_fir4(int16_t *y, const int16_t *x, int16_t hgain, const int16_t *hcoeffs)
{
    unsigned int i;
    int32_t h[4];

    h[0] = (hgain * hcoeffs[0]) >> 15;
    h[1] = (hgain * hcoeffs[1]) >> 15;
    h[2] = (hgain * hcoeffs[2]) >> 15;
    h[3] = (hgain * hcoeffs[3]) >> 15;

    for (i = 0; i < SUBFRAME_SIZE; ++i) {
        int64_t sum = (int64_t)h[0] * x[i] + (int64_t)h[1] * x[i + 1]
                    + (int64_t)h[2] * x[i + 2] + (int64_t)h[3] * x[i + 3];
        y[i] = clamp_s16(y[i] + (int32_t)(sum >> 15));
    }
}

static void interleave_stage_v1(uint8_t *memory, musyx_t *musyx, uint32_t output_ptr)
{
    size_t i;

    int16_t base_left;
    int16_t base_right;

    int16_t *left;
    int16_t *right;

    base_left  = clamp_s16(musyx->base_vol[0]);
    base_right = clamp_s16(musyx->base_vol[1]);

    left  = musyx->left;
    right = musyx->right;

    for (i = 0; i < SUBFRAME_SIZE; ++i) {
        uint16_t l = clamp_s16(*(left++)  + base_left);
        uint16_t r = clamp_s16(*(right++) + base_right);

        write16(memory, output_ptr + 4*i, l);
        write16(memory, output_ptr + 4*i + 2, r);
    }
}
