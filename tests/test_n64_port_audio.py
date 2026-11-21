"""The port's sound (consoles/n64/port_wasm/audio.c): audio task lists written here, run through port_audio_task in a
small harness, never a game's.

A racing game's synthesizer is n_audio (libultra's n_al*), whose lists the host read as ABI 1: its ADPCM command's
flags, count and offsets were taken for the decoder state's address, so each voice's state went to 0x80204170 +
n * 0x1000, over an overlay's code, and the game stopped drawing when it next ran that code.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

AUDIO = Path(__file__).resolve().parents[1] / "src/decompnet/consoles/n64/port_wasm/audio.c"
MUSYX = AUDIO.with_name("audio_musyx.c")
TASK, LIST, STATE, SAVED, SAMPLES = 0x80100000, 0x80101000, 0x80300000, 0x80310000, 0x80320000

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
static uint8_t *mem;
uint8_t *port_memory(void) { return mem; }
void port_live_audio(const int16_t *lr, int frames, int hz) { (void)lr; (void)frames; (void)hz; }
void port_audio_task(uint32_t task);
/* argv: a file of (address, big-endian word) pairs to put in memory, the task's address, then addresses whose word
   to print after the task */
int main(int argc, char **argv) {
    mem = mmap(NULL, 1ull << 32, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
    if (mem == MAP_FAILED) return 3;
    FILE *in = fopen(argv[1], "rb");
    uint32_t pair[2];
    while (fread(pair, 4, 2, in) == 2)
        for (int i = 0; i < 4; i++) mem[pair[0] + i] = (uint8_t)(pair[1] >> (24 - 8 * i));
    fclose(in);
    port_audio_task((uint32_t)strtoul(argv[2], NULL, 16));
    for (int i = 3; i < argc; i++) {
        uint32_t a = (uint32_t)strtoul(argv[i], NULL, 16);
        printf("%02X%02X%02X%02X\n", mem[a], mem[a + 1], mem[a + 2], mem[a + 3]);
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    folder = tmp_path_factory.mktemp("audio")
    (folder / "harness.c").write_text(HARNESS)
    program = folder / "audio"
    done = subprocess.run([cc, "-O1", "-pthread", "-o", str(program), str(folder / "harness.c"), str(AUDIO), str(MUSYX)],
                          capture_output=True, text=True, timeout=120)
    if done.returncode:
        pytest.skip(f"the harness doesn't build here: {done.stderr[-300:]}")

    def go(commands: list[tuple[int, int]], memory: dict[int, int], *read: int) -> list[int]:
        words = dict(memory)
        words.setdefault(TASK + 48, LIST)
        words.setdefault(TASK + 52, 8 * len(commands))  # OSTask's data_ptr, data_size
        for i, (w0, w1) in enumerate(commands):
            words[LIST + 8 * i], words[LIST + 8 * i + 4] = w0, w1
        (folder / "memory.bin").write_bytes(b"".join(struct.pack("<II", a, v) for a, v in words.items()))
        out = subprocess.run([str(program), str(folder / "memory.bin"), f"{TASK:X}", *(f"{a:X}" for a in read)],
                             capture_output=True, text=True, timeout=30, check=True).stdout.split()
        return [int(x, 16) for x in out]
    return go


SENTINEL = 0x5A5A5A5A


def test_an_n_audio_adpcm_keeps_its_state_where_its_first_word_says(run):
    """n_alAdpcmPull's A_ADPCM: the state in the first word; the second is flags, count, the input's offset and the
    output (here count 0x120, offset 4, output 0x170: what a racing game sent), never an address."""
    commands = [(0x01000000 | (STATE & 0xFFFFFF), 0x01204170)]  # (flags 0: from the state's last samples)
    state = {STATE + 4 * i: 0x00010002 + 0x00020002 * i for i in range(8)}
    after = run(commands, {0x80204170: SENTINEL, **state}, 0x80204170, STATE)
    assert after[0] == SENTINEL  # nothing written where the second word would point
    assert after[1] != 0x00010002  # the decoder's new state, written back


def test_an_abi1_list_is_still_read_as_abi1(run):
    """alAudioFrame's lists set a segment and their buffers: there, A_ADPCM's second word is the state's address."""
    commands = [(0x07000000, 0x00000000),                 # A_SEGMENT 0 = 0
                (0x08000000, 0x00000020),                 # A_SETBUFF: in 0, out 0, 32 bytes
                (0x01000000, STATE & 0xFFFFFF)]           # A_ADPCM, state in the second word
    after = run(commands, {STATE: SENTINEL}, STATE)
    assert after[0] != SENTINEL


def test_n_audio_loads_interleaves_the_main_bus_and_saves_it(run):
    """n_alSavePull: A_INTERLEAVE (the main bus's left at 0x4E0 and right at 0x650 into 0) and A_SAVEBUFF of
    0x2E0 bytes from 0; the buses filled here by A_LOADBUFF (bytes:12 where:12 | from)."""
    left = {SAMPLES + 4 * i: (0x1000 + 2 * i) << 16 | (0x1001 + 2 * i) for i in range(92)}
    right = {SAMPLES + 0x200 + 4 * i: (0x2000 + 2 * i) << 16 | (0x2001 + 2 * i) for i in range(92)}
    commands = [(0x04000000 | 0x170 << 12 | 0x4E0, SAMPLES & 0xFFFFFF),
                (0x04000000 | 0x170 << 12 | 0x650, (SAMPLES + 0x200) & 0xFFFFFF),
                (0x0D000000, 0), (0x062E0000, SAVED & 0xFFFFFF)]
    first, second, last = run(commands, {**left, **right}, SAVED, SAVED + 4, SAVED + 0x2DC)
    assert (first, second) == (0x10002000, 0x10012001)  # left, right, sample by sample
    assert last == 0x10B720B7  # the 184th pair (0x1000 + 183, 0x2000 + 183)


def test_n_audio_clears_and_mixes_one_sub_frame(run):
    """A_CLEARBUFF (where | bytes) and A_MIXER (gain | from:16 to:16) over n_audio's fixed 184 samples."""
    samples = {SAMPLES + 4 * i: 0x40004000 for i in range(92)}
    commands = [(0x04000000 | 0x170 << 12 | 0x000, SAMPLES & 0xFFFFFF),  # 0x4000s at 0
                (0x020004E0, 0x2E0),                                     # clear the main bus
                (0x0C007FFF, 0x000004E0),                                # mix 0 into its left at full gain
                (0x0D000000, 0), (0x062E0000, SAVED & 0xFFFFFF)]
    first, last = run(commands, {**samples, SAVED: SENTINEL}, SAVED, SAVED + 0x2DC)
    assert first == 0x3FFF0000 and last == 0x3FFF0000  # left mixed (0x4000 * 0x7FFF >> 15), right cleared


def test_ai_interrupts_follow_each_dma_completion(tmp_path):
    cc = shutil.which("cc") or shutil.which("clang")
    if not cc:
        pytest.skip("no C compiler")
    harness = tmp_path / "ai.c"
    harness.write_text(r"""
#include <assert.h>
#include <sys/mman.h>
#include <time.h>
static struct timespec tick;
static int fake_clock(clockid_t id, struct timespec *out) { (void)id; *out = tick; return 0; }
#define clock_gettime fake_clock
#include "audio.c"
static uint8_t *memory;
static unsigned buffers;
uint8_t *port_memory(void) { return memory; }
void port_live_audio(const int16_t *samples, int frames, int rate) {
    (void)samples;
    assert(rate == 8000);
    assert(frames == 800 || frames == 400);
    buffers++;
}
int main(void) {
    memory = mmap(NULL, 1ull << 32, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
    assert(memory != MAP_FAILED);
    uint32_t status;
    port_ai_frequency(8000);
    port_ai_register(0xA4500000u, 1, 0x10000, NULL);
    port_ai_register(0xA4500004u, 1, 3200, NULL);
    port_ai_register(0xA4500004u, 1, 1600, NULL);
    port_ai_register(0xA4500004u, 1, 1600, NULL); /* The full FIFO rejects a third buffer. */
    port_ai_register(0xA450000Cu, 0, 0, &status);
    assert(buffers == 2 && (status & 0xC0000000u) == 0xC0000000u);
    assert(port_ai_completions() == 0);
    tick.tv_nsec = 110000000;
    port_ai_register(0xA450000Cu, 0, 0, &status); /* Polling must not consume the interrupt. */
    assert(status == 0x40000000u && port_ai_completions() == 1 && port_ai_completions() == 0);
    tick.tv_nsec = 160000000;
    assert(port_ai_completions() == 1 && port_ai_completions() == 0);
    return 0;
}
""")
    program = tmp_path / "ai"
    subprocess.run([cc, "-pthread", "-I", str(AUDIO.parent), str(harness), str(MUSYX), "-o", str(program)],
                   check=True, capture_output=True, timeout=30)
    subprocess.run([str(program)], check=True, timeout=10)


def test_musyx_uses_subframe_count_instead_of_command_byte_count(run):
    ucode, output = 0x80102000, SAVED
    memory = {
        TASK + 24: ucode, TASK + 28: 0x800, TASK + 52: 1, ucode: 0x00010010, ucode + 0x10: 1,
        LIST + 8: STATE,
        LIST + 0x10 + 0x44: output,
        STATE + 0x108: 0x0100FF00,
        STATE + 0x100: 0x0000FFFF,
        output: SENTINEL,
    }
    # A subframe with no voices still emits the mixer state's decaying left and right base gains.
    assert run([], memory, output, output + 4) == [0x00F8FF07, 0x00F8FF07]
