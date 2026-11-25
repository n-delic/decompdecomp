/* The file the live emulator shares with its viewer (EMU_LIVE=path, port/wasm/side_by_side.py), mapped by the video
   plugin (which writes the console's RAM as the game hands over each graphics task), the input plugin (which reads the
   pad the viewer writes) and the viewer. A fixed header, then RDRAM big-endian from 0x80000000 as a dump holds it, so
   the viewer draws it as it draws any frame dump. The header's words are host-order u32s:
     0   seq      odd while the emulator is writing a frame, even when the frame is whole (read it twice: a seqlock)
     4   frames   the game's frames so far (the graphics tasks handed over)
     8   vi       video refreshes so far
     16  task     the 64-byte OSTask the RSP was given, big-endian, pointers physical (frame_dumps' NAME.task)
     12  gate     lockstep: the highest retrace the emulator may complete (written by the viewer; 0xFFFFFFFF: free-running)
     80  viseq    like seq, for the second block below
     84  vivi     the retrace the second block was written at
     88  audio_hz the audio interface's sample rate
     92  audio_end the stereo frames written to the ring so far (it wraps at 2^32)
     128 input    the first controller as the input plugin gives it: the buttons in bits 0-15, the stick's x in 16-23
                  and y in 24-31 (the same layout as EMU_INPUT's, input.c)
   RDRAM (8 MB reserved, the console's 4 or 8) is there twice: at LIVE_HEADER as of the newest graphics task (what the
   picture is drawn from) and at LIVE_VIRAM as of the newest retrace, where lockstep parks the emulator until the viewer
   lets it go on (frontend.c). After both, the audio ring: LIVE_RING stereo frames, 16-bit little-endian, left sample in the
   low half of each word (audio.c). Keep the numbers in step with live.py. */
#ifndef DECOMP_LIVE_H
#define DECOMP_LIVE_H
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

#define LIVE_SEQ 0
#define LIVE_FRAMES 4
#define LIVE_VI 8
#define LIVE_TASK 16
#define LIVE_INPUT 128
#define LIVE_GATE 12
#define LIVE_VISEQ 80
#define LIVE_VIVI 84
#define LIVE_AUDIO_HZ 88
#define LIVE_AUDIO_END 92
#define LIVE_HEADER 256
#define LIVE_RAM_MAX (8u << 20)
#define LIVE_VIRAM (LIVE_HEADER + LIVE_RAM_MAX)
#define LIVE_AUDIO (LIVE_VIRAM + LIVE_RAM_MAX)
#define LIVE_RING (1u << 17)
#define LIVE_TOTAL (LIVE_AUDIO + 4 * LIVE_RING)

/* the shared file's words, or NULL when EMU_LIVE isn't set or the file can't be mapped */
static inline volatile uint32_t *live_map(void) {
    size_t bytes = LIVE_TOTAL;
    const char *path = getenv("EMU_LIVE");
    if (!path || !*path) return NULL;
    int fd = open(path, O_RDWR);
    if (fd < 0) return NULL;
    void *p = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    return p == MAP_FAILED ? NULL : (volatile uint32_t *)p;
}
#endif
