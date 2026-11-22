/* A cartridge's 32 KiB SRAM, separate from its EEPROM and saved across runs. */
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
#define SRAM_SIZE 0x8000u
static uint8_t sram[SRAM_SIZE];
static int loaded;

#ifdef PORT_WEB
__attribute__((import_module("port_web"), import_name("sram_load"))) void port_web_sram_load(uint8_t *chip, uint32_t size);
__attribute__((import_module("port_web"), import_name("sram_save"))) void port_web_sram_save(const uint8_t *chip, uint32_t size);
static void load(void) { port_web_sram_load(sram, sizeof sram); }
static int save(void) { port_web_sram_save(sram, sizeof sram); return 0; }
#else
static const char *path(void) { const char *p = getenv("PORT_SRAM"); return p && *p ? p : "game.sra"; }
static void load(void) {
    FILE *f = fopen(path(), "rb");
    if (f) {
        size_t n = fread(sram, 1, sizeof sram, f);
        if (n != sizeof sram) fprintf(stderr, "SRAM save %s is incomplete; unread bytes stay blank.\n", path());
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
    int ok = f && fwrite(sram, 1, sizeof sram, f) == sizeof sram;
    if (f && fclose(f)) ok = 0;
#ifdef _WIN32
    if (ok && !MoveFileExA(temporary, p, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) ok = 0;
#else
    if (ok && rename(temporary, p)) ok = 0;
#endif
    if (!ok) { fprintf(stderr, "Cannot save SRAM to %s; check the folder is writable.\n", p); remove(temporary); }
    free(temporary);
    return ok ? 0 : -1;
}
#endif

int port_sram_dma(uint32_t dram, uint32_t offset, uint32_t size, uint32_t write) {
    if (offset > SRAM_SIZE || size > SRAM_SIZE - offset || size > UINT32_MAX - dram || write > 1) return -1;
    if (!loaded) { loaded = 1; memset(sram, 0xff, sizeof sram); load(); }
    uint8_t *memory = port_memory();
    if (!write) memcpy(memory + dram, sram + offset, size);
    else { memcpy(sram + offset, memory + dram, size); return save(); }
    return 0;
}
