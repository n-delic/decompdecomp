/* A cartridge's FlashRAM (128 KiB, the save chip of the later games), saved across runs.

   libultra's osFlash* routines drive it through a PI handle of type 8 whose base is the 0x08000000 window: a command
   word written to 0x08010000 (osEPiWriteIo), the status or the chip's id read back from 0x08000000 (osEPiReadIo or a
   DMA), the 128-byte page buffer filled by a DMA write to 0x08000000, and the array read by a DMA whose device
   address is the byte offset divided by two. Without the chip the host answered those reads from the game's ROM,
   so a game that tests its save chip at boot (a strategy game wrote its 128-byte pattern and read it back about
   130,000 times in 15 seconds) never went on to draw.

   The commands and the status words are the chip's (the same as the reference emulators'):
     0xE1 status mode (reads give the id/status), 0xF0 read-array mode, 0x4B set the erase page, 0x78 erase mode,
     0x3C erase the whole chip, 0xA5 set the page to write, 0xB4 write mode, 0xD2 execute the erase or the write. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef PORT_WEB
#ifdef _WIN32
#include <process.h>
#include <windows.h>
#define port_getpid _getpid
#else
#include <unistd.h>
#define port_getpid getpid
#endif
#endif

uint8_t *port_memory(void);
#define FLASH_SIZE 0x20000u
#define FLASH_PAGE 128u
#define FLASH_SECTOR 0x4000u /* what one erase clears: 16 KiB, 128 pages */
enum { MODE_NOP, MODE_ERASE, MODE_WRITE, MODE_READ, MODE_STATUS };
static uint8_t flash[FLASH_SIZE];
static uint8_t page[FLASH_PAGE];
static int loaded, mode = MODE_NOP, erase_all;
static uint32_t erase_offset, write_offset;
static uint64_t status = 0x1111800100C2001Eull;

#ifdef PORT_WEB
/* the browser build keeps the chip for the session only: its save store has no flash import yet */
static void load(void) {}
static int save(void) { return 0; }
#else
static const char *path(void) { const char *p = getenv("PORT_FLASH"); return p && *p ? p : "game.fla"; }
static void load(void) {
    FILE *f = fopen(path(), "rb");
    if (f) {
        size_t n = fread(flash, 1, sizeof flash, f);
        if (n != sizeof flash) fprintf(stderr, "FlashRAM save %s is incomplete; unread bytes stay blank.\n", path());
        fclose(f);
    }
}
static int save(void) {
    const char *p = path();
    size_t n = strlen(p);
    char *temporary = malloc(n + 32);
    if (!temporary) return -1;
    snprintf(temporary, n + 32, "%s.tmp.%lu", p, (unsigned long)port_getpid());
    FILE *f = fopen(temporary, "wb");
    int ok = f && fwrite(flash, 1, sizeof flash, f) == sizeof flash;
    if (f && fclose(f)) ok = 0;
#ifdef _WIN32
    if (ok && !MoveFileExA(temporary, p, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) ok = 0;
#else
    if (ok && rename(temporary, p)) ok = 0;
#endif
    if (!ok) { fprintf(stderr, "Cannot save FlashRAM to %s; check the folder is writable.\n", p); remove(temporary); }
    free(temporary);
    return ok ? 0 : -1;
}
#endif

static void ready(void) {
    if (!loaded) { loaded = 1; memset(flash, 0xff, sizeof flash); load(); }
}

/* a word written to the command register (0x08010000) */
void port_flash_command(uint32_t command) {
    ready();
    switch (command >> 24) {
    case 0x4B: erase_offset = (command & 0xFFFFu) * FLASH_PAGE; break;
    case 0x78: mode = MODE_ERASE; status = 0x1111800800C2001Eull; break;
    case 0x3C: erase_all = 1; break;
    case 0xA5: write_offset = (command & 0xFFFFu) * FLASH_PAGE; status = 0x1111800400C2001Eull; break;
    case 0xB4: mode = MODE_WRITE; break;
    case 0xD2:
        if (mode == MODE_ERASE) {
            if (erase_all) memset(flash, 0xff, sizeof flash);
            else { uint32_t at = (erase_offset % FLASH_SIZE) & ~(FLASH_SECTOR - 1); memset(flash + at, 0xff, FLASH_SECTOR); }
            erase_all = 0;
            save();
        } else if (mode == MODE_WRITE) {
            if (write_offset + FLASH_PAGE <= FLASH_SIZE) memcpy(flash + write_offset, page, FLASH_PAGE);
            save();
        }
        break;
    case 0xE1: mode = MODE_STATUS; status = 0x1111800100C2001Eull; break;
    case 0xF0: mode = MODE_READ; status = 0x11118004F0000000ull; break;
    default: break;
    }
}

/* a word read from the chip's window (osEPiReadIo): the status or id words in status mode */
uint32_t port_flash_read(uint32_t offset) {
    ready();
    if (mode == MODE_READ) {
        uint32_t at = (offset * 2) % FLASH_SIZE;
        return (uint32_t)flash[at] << 24 | (uint32_t)flash[(at + 1) % FLASH_SIZE] << 16 |
               (uint32_t)flash[(at + 2) % FLASH_SIZE] << 8 | flash[(at + 3) % FLASH_SIZE];
    }
    return (uint32_t)(offset & 4 ? status : status >> 32);
}

/* a DMA to or from the chip: a write fills the page buffer; a read gives the status words or, in read-array mode,
   the array at twice the device offset */
int port_flash_dma(uint32_t dram, uint32_t offset, uint32_t size, uint32_t write) {
    ready();
    uint8_t *memory = port_memory();
    if (size > UINT32_MAX - dram || write > 1) return -1;
    if (write) {
        memset(page, 0xff, sizeof page);
        memcpy(page, memory + dram, size < FLASH_PAGE ? size : FLASH_PAGE);
        return 0;
    }
    if (mode == MODE_READ) {
        for (uint32_t i = 0; i < size; i++) memory[dram + i] = flash[(offset * 2 + i) % FLASH_SIZE];
    } else {
        for (uint32_t i = 0; i < size && i < 8; i++) memory[dram + i] = (uint8_t)(status >> (56 - 8 * i));
    }
    return 0;
}
