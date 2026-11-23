"""The port host names libultra's routines by structure at run time (consoles/n64/port_wasm/mips.c's shape_identify),
for a game that decompresses or loads its real code and so carries its own libultra in code the build never saw: its
own osCreateThread ran against kernel structures the host doesn't keep, and no thread ever ran. The C is the same
test as consoles/n64/sdk_shapes.py's, so it is run on the same instruction words (built here, never copied from a
game) and has to say what the Python says."""
from __future__ import annotations

import importlib.util
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from decompnet.consoles.n64 import sdk_shapes
from decompnet.port.wasm import interp

ROOT = Path(__file__).resolve().parents[1]
MIPS = ROOT / "src/decompnet/consoles/n64/port_wasm/mips.c"
HOST = ROOT / "src/decompnet/consoles/n64/port_wasm/host.c"
BASE, STRIDE = 0x80001000, 0x400
JR_RA, NOP = 0x03E00008, 0

_spec = importlib.util.spec_from_file_location("shape_builders", ROOT / "tests/test_n64_sdk_thread_shapes.py")
shapes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shapes)

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
static uint8_t *mem;
uint8_t *port_memory(void) { return mem; }
uint64_t port_io_load(uint64_t a) { return a; }
uint32_t port_stack_bus(uint32_t a) { return a >= 0x14000000u && a < 0x15000000u ? a : 0; }
unsigned port_tlb_n;
uint32_t port_tlb_canon(uint32_t a) { return a; }
uint32_t port_tlb_alias(uint32_t a) { return a; }
uint32_t port_tlb_jump(uint32_t slot, uint32_t imm) { return (slot & 0xF0000000u) | imm; }
void port_tlb_write(uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)pm; (void)hi; (void)lo0; (void)lo1; }
void port_tlb_set(unsigned i, uint32_t pm, uint32_t hi, uint32_t lo0, uint32_t lo1) { (void)i; (void)pm; (void)hi; (void)lo0; (void)lo1; }
uint32_t port_tlb_probe(uint32_t hi) { (void)hi; return 0x80000000u; }
int port_tlb_read(unsigned i, uint32_t *pm, uint32_t *hi, uint32_t *lo0, uint32_t *lo1) { (void)i; (void)pm; (void)hi; (void)lo0; (void)lo1; return 0; }
int port_tlb_invalid(uint32_t a) { (void)a; return 0; }
uint64_t port_io_store(uint64_t a, uint64_t v) { (void)v; return a; }
void port_safe_point(void) {}
void port_boot_spin(void) {}
void port_idle_forever(void) { exit(0); }
int port_thread_number(void) { return -1; }
struct w2c_env;
uint32_t w2c_env_0x5F_builtin_mips_read_count(struct w2c_env *e) { (void)e; return 1234; }
const char *port_mips_shape_name(uint32_t vram);
/* argv: a file of functions, one every STRIDE bytes from BASE, and how many: each one's name, or - */
int main(int argc, char **argv) {
    mem = mmap(NULL, 1ull << 32, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
    if (mem == MAP_FAILED || argc < 3) return 3;
    FILE *in = fopen(argv[1], "rb");
    size_t n = fread(mem + 0x%(base)08Xu, 1, 0x10000, in);
    fclose(in);
    (void)n;
    /* what the synthetic `jal` words call (0x80020000): an interrupt disable, `mfc0 $t0, Status; jr $ra; nop` */
    static const uint32_t leaf[3] = {0x40086000u, 0x03E00008u, 0};
    for (int k = 0; k < 3; k++) { uint32_t b = __builtin_bswap32(leaf[k]); memcpy(mem + 0x80020000u + 4 * k, &b, 4); }
    for (int i = 0; i < atoi(argv[2]); i++) {
        const char *name = port_mips_shape_name(0x%(base)08Xu + (uint32_t)i * %(stride)du);
        printf("%%s\n", name ? name : "-");
    }
    return 0;
}
"""


def _functions():
    """Each routine the Python tests build, and a near miss of it: (label, bytes)."""
    i, code = shapes._i, shapes._code
    stores = [i(0x2B, 4, 6, o) for o in (0, 4, 8, 0xC, 0x10, 0x14)]
    out = [
        ("start", shapes._start_thread()),
        ("start-two-stores", shapes._start_thread(stores=[shapes._sh_state()] * 2)),
        ("priority", shapes._set_priority()),
        ("priority-reader", shapes._set_priority(priority=[i(0x23, 2, 9, 4)])),
        ("destroy", shapes._destroy_thread()),
        ("destroy-runnable", shapes._destroy_thread(constants=[i(9, 0, 15, 2)])),
        ("recv", shapes._queue_routine("recv")),
        ("send", shapes._queue_routine("send")),
        ("jam", shapes._queue_routine("jam")),
        ("recv-no-modulo", shapes._queue_routine("recv", modulo=False)),
        ("queue", code(*stores, JR_RA, NOP, pad=2)),
        ("queue-short", code(*stores[:5], JR_RA, NOP, pad=3)),
        ("queue-load", code(*stores, i(0x23, 4, 8, 4), JR_RA, NOP, pad=1)),
        ("event", shapes._event_message()),
        ("event-structure", shapes._event_message(tail=[i(0x2B, 10, 9, 8), shapes.JAL, shapes.JAL, JR_RA, NOP])),
    ]
    # osCreateThread: the id into +0x14, flags cleared at +0x12, a state halfword at +0x10, two calls
    create = [i(0x2B, 4, 5, 0x14), i(0x29, 4, 0, 0x12), i(9, 0, 14, 1), i(0x29, 4, 14, 0x10), i(0x2B, 4, 6, 0x11C),
              shapes.JAL, shapes.JAL]
    out.append(("create", code(*create, pad=30)))
    out.append(("create-no-flags", code(*create[:1], *create[2:], pad=30)))
    out.append(("create-no-pc", code(*create[:4], *create[5:], pad=30)))
    # an unoptimised build: the id from a register it was spilled to, not from $a1
    out.append(("create-spilled", code(i(0x2B, 15, 8, 0x14), *create[1:], pad=30)))
    out.append(("create-inline", code(*([shapes.JAL] * 6), *create[:5], pad=60)))
    # the same routine whose two calls go somewhere that does not read the Status register (a program's first function
    # setting a thread up inline, with a call to its own code): Python's bytes-only test names it, the host's does not
    other = 3 << 26 | 0x9000
    out.append(("create-callees-not-interrupts", code(*create[:5], other, other, pad=30)))
    # one call is an interrupt disable and the other is not (a bootstrap that calls osInitialize-like code, which also
    # reads Status): osCreateThread calls only the two interrupt leaves
    out.append(("create-one-callee-not-an-interrupt", code(*create[:5], shapes.JAL, other, pad=30)))
    # a program's entry: a stub that jumps through a register, and the routine six words after it: the stub is no
    # function of the routine's, and the extent of the whole is not judged (the routine itself is named at its own address)
    jalr = 4 << 21 | 31 << 11 | 9  # jalr $a0
    out.append(("create-behind-an-indirect-jump", code(i(0x0F, 0, 4, 0x13), jalr, NOP, *create, pad=30)))
    out.append(("create-id-on-the-stack", code(i(0x2B, 29, 8, 0x14), *create[1:], pad=30)))
    # osYieldThread: three calls, no branch, one state halfword set to runnable
    yield_ = [i(9, 29, 29, -8), shapes.JAL, i(9, 0, 14, 2), shapes.JAL, i(0x29, 15, 14, 0x10), shapes.JAL, NOP]
    out.append(("yield", code(*yield_, pad=2)))
    out.append(("yield-not-runnable", code(*yield_[:2], i(9, 0, 14, 3), *yield_[3:], pad=2)))
    # osGetThreadId / osGetThreadPri: bnez $a0; nop; lui $a0, hi; lw $a0, lo($a0); jr $ra; lw $v0, off($a0)
    bnez_a0 = i(5, 4, 0, 3)
    running = [bnez_a0, NOP, i(0x0F, 0, 4, 0x800C), i(0x23, 4, 4, 0x1CB0), JR_RA]
    out.append(("getter-id", code(*running, i(0x23, 4, 2, 0x14))))
    out.append(("getter-pri", code(*running, i(0x23, 4, 2, 4))))
    out.append(("getter-other-field", code(*running, i(0x23, 4, 2, 0x18))))
    out.append(("getter-no-branch", code(NOP, NOP, *running[2:], i(0x23, 4, 2, 0x14))))
    out.append(("getter-with-a-store", code(*running[:4], i(0x2B, 4, 5, 0x20), JR_RA, i(0x23, 4, 2, 0x14))))
    out.append(("getter-calls", code(*running[:4], shapes.JAL, JR_RA, i(0x23, 4, 2, 0x14))))
    return out


def _with_return(code: bytes) -> bytes:
    """The function as the interpreter finds it: up to its `jr $ra` and the delay slot (one is added when it has none)."""
    words = [w for (w,) in struct.iter_unpack(">I", code)]
    if JR_RA not in words:
        words += [JR_RA, NOP]
    at = words.index(JR_RA)
    return b"".join(struct.pack(">I", w) for w in words[:at + 2])


@pytest.fixture(scope="module")
def classified(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    tmp = tmp_path_factory.mktemp("shapes")
    cases = [(label, _with_return(code)) for label, code in _functions()]
    image = bytearray(0x10000)
    for k, (_, code) in enumerate(cases):
        image[k * STRIDE:k * STRIDE + len(code)] = code
    (tmp / "image.bin").write_bytes(bytes(image))
    (tmp / "harness.c").write_text(HARNESS % {"base": BASE, "stride": STRIDE})
    done = subprocess.run([cc, "-O1", "-o", str(tmp / "shapes"), str(tmp / "harness.c"), str(MIPS), "-lm"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    run = subprocess.run([str(tmp / "shapes"), str(tmp / "image.bin"), str(len(cases))],
                         capture_output=True, text=True, timeout=30, check=True)
    names = run.stdout.split()
    assert len(names) == len(cases)
    return [(label, code, name) for (label, code), name in zip(cases, names)]


HOST_ONLY_REJECTS = {"create-callees-not-interrupts", "create-one-callee-not-an-interrupt",
                     "create-behind-an-indirect-jump"}  # (the bytes alone are the routine's; what it calls is not)


def test_the_host_names_what_the_python_tests_name(classified):
    for label, code, name in classified:
        if label in HOST_ONLY_REJECTS:
            continue
        expected = (sdk_shapes.identify(code) or {}).get("name", "-")
        assert name == expected, (label, name, expected)


def test_the_host_names_each_routine_it_can_replace(classified):
    named = {name for _, _, name in classified}
    assert {"osCreateThread", "osYieldThread", "osStartThread", "osSetThreadPri", "osDestroyThread", "osRecvMesg",
            "osSendMesg", "osJamMesg", "osCreateMesgQueue", "osSetEventMesg", "osGetThreadId",
            "osGetThreadPri"} <= named


def test_a_near_miss_is_no_routine(classified):
    by = {label: name for label, _, name in classified}
    for label in ("start-two-stores", "priority-reader", "destroy-runnable", "recv-no-modulo", "queue-short",
                  "queue-load", "event-structure", "create-no-flags", "create-no-pc", "create-id-on-the-stack", "create-inline",
                  "yield-not-runnable", "create-callees-not-interrupts", "create-one-callee-not-an-interrupt",
                  "create-behind-an-indirect-jump", "getter-other-field", "getter-no-branch", "getter-with-a-store",
                  "getter-calls"):
        assert by[label] == "-", label
    # the bytes alone say osCreateThread: it is the callees that are not the interrupt disable
    case = next(code for label, code, _ in classified if label == "create-callees-not-interrupts")
    assert sdk_shapes.identify(case)["name"] == "osCreateThread"


def test_the_generated_bridge_calls_the_hosts_own_routine_for_each_name():
    text, _ = interp.generate("", [], [HOST])
    for name in interp.SHAPE_NAMES:
        assert f'{{"{name}", H_shape_{name}}}' in text, name
    assert f"const unsigned port_mips_shape_count = {len(interp.SHAPE_NAMES)};" in text
    # the host function it calls is the one host.c defines
    assert "w2c_env_osCreateThread(NULL" in text


def test_a_build_with_no_host_routines_has_an_empty_table():
    text, _ = interp.generate("", [], [])
    assert "const unsigned port_mips_shape_count = 0;" in text
