"""A cartridge's FlashRAM answers libultra's osFlash* protocol and survives a restart (flash.c)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOST = Path(__file__).resolve().parents[1] / 'src/decompnet/consoles/n64/port_wasm'


@pytest.mark.skipif(shutil.which('cc') is None, reason='needs a C compiler')
def test_flash_ram_id_write_erase_and_save(tmp_path):
    """A strategy game wrote a 128-byte pattern to its save chip and read it back about 130,000 times in 15 seconds:
    the host answered from the ROM and the write was dropped, so its detection never succeeded."""
    source = tmp_path / 'test.c'
    source.write_text(r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static uint8_t memory[1024];
uint8_t *port_memory(void) { return memory; }
int port_flash_dma(uint32_t dram, uint32_t offset, uint32_t size, uint32_t write);
void port_flash_command(uint32_t command);
uint32_t port_flash_read(uint32_t offset);
int main(int argc, char **argv) {
    if (argc > 1) { /* first run: write page 3 */
        for (int i = 0; i < 128; i++) memory[i] = (uint8_t)(i + 1);
        port_flash_command(0xE1000000);                       /* status mode: the chip's id */
        printf("%08X %08X\n", port_flash_read(0), port_flash_read(4));
        port_flash_command(0x4B000000); port_flash_command(0x78000000); port_flash_command(0xD2000000); /* erase */
        port_flash_dma(0, 0, 128, 1);                         /* the page buffer */
        port_flash_command(0xB4000000); port_flash_command(0xA5000003); port_flash_command(0xD2000000); /* write page 3 */
        return 0;
    }
    port_flash_command(0xF0000000);                           /* read-array mode */
    memset(memory + 256, 0, 128);
    port_flash_dma(256, 3 * 64, 128, 0);                      /* the device offset is half the byte offset */
    int same = memcmp(memory + 256, (uint8_t[128]){1,2,3,4,5,6,7,8}, 8) == 0;
    uint32_t erased = port_flash_read(0x100 / 2);             /* a page nobody wrote */
    printf("%d %08X\n", same, erased);
    return 0;
}
''')
    binary = tmp_path / 'flash'
    subprocess.run(['cc', '-O1', '-o', str(binary), str(source), str(HOST / 'flash.c')],
                   check=True, capture_output=True, timeout=60)
    env = {**os.environ, 'PORT_FLASH': str(tmp_path / 'game.fla')}
    first = subprocess.run([str(binary), 'write'], env=env, check=True, capture_output=True, text=True, timeout=30)
    assert first.stdout.split() == ['11118001', '00C2001E']  # the chip's id words
    second = subprocess.run([str(binary)], env=env, check=True, capture_output=True, text=True, timeout=30)
    assert second.stdout.split()[0] == '1'  # the page written before the restart reads back
    assert second.stdout.split()[1] == 'FFFFFFFF'
    assert (tmp_path / 'game.fla').stat().st_size == 0x20000


def test_the_host_sends_a_flash_handle_to_the_chip():
    text = (HOST / 'host.c').read_text()
    assert 'flash_handle(handle)' in text and 'port_flash_command(data)' in text
    assert 'PORT_FLASH' in (Path(__file__).resolve().parents[1] / 'src/decompnet/port/wasm/play.py').read_text()
