"""A cartridge's SRAM survives a restart and rejects transfers beyond its 32 KiB."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which('cc') is None, reason='needs a C compiler')
def test_sram_dma_persists_and_checks_bounds(tmp_path):
    source = tmp_path / 'test.c'
    source.write_text(r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uint8_t memory[128];
uint8_t *port_memory(void) { return memory; }
int port_sram_dma(uint32_t dram, uint32_t offset, uint32_t size, uint32_t write);
int main(int argc, char **argv) {
    if (argc > 1) {
        memcpy(memory + 16, "snow", 4);
        if (port_sram_dma(16, 0x120, 4, 1)) return 2;
        return port_sram_dma(16, 0x7fff, 2, 1) == -1 ? 0 : 3;
    }
    if (port_sram_dma(16, 0x120, 4, 0)) return 4;
    printf("%.4s %d\n", memory + 16, port_sram_dma(16, 0x8000, 1, 0));
    return 0;
}
''')
    host = Path(__file__).resolve().parents[1] / 'src/decompnet/consoles/n64/port_wasm/sram.c'
    binary = tmp_path / 'sram'
    subprocess.run(['cc', '-O1', '-o', str(binary), str(source), str(host)], check=True, capture_output=True, timeout=60)
    env = {**os.environ, 'PORT_SRAM': str(tmp_path / 'game.sra')}
    subprocess.run([str(binary), 'write'], env=env, check=True, timeout=30)
    result = subprocess.run([str(binary)], env=env, check=True, capture_output=True, text=True, timeout=30)
    assert result.stdout.strip() == 'snow -1'
    assert (tmp_path / 'game.sra').stat().st_size == 32768
