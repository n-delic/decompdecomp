"""bemem.py game-impl.h: wasm2c's loads and stores read and write memory big-endian, as the N64 does."""
import sys

p = sys.argv[1]
s = open(p).read()
swap = r'''
/* decompdecomp: the game's memory is big-endian, as on the N64 (port/wasm) */
#define PORT_BE(x)                                                        \
  do {                                                                    \
    if (sizeof(x) == 2) { u16 t_; wasm_rt_memcpy(&t_, &(x), 2); t_ = __builtin_bswap16(t_); wasm_rt_memcpy(&(x), &t_, 2); } \
    else if (sizeof(x) == 4) { u32 t_; wasm_rt_memcpy(&t_, &(x), 4); t_ = __builtin_bswap32(t_); wasm_rt_memcpy(&(x), &t_, 4); } \
    else if (sizeof(x) == 8) { u64 t_; wasm_rt_memcpy(&t_, &(x), 8); t_ = __builtin_bswap64(t_); wasm_rt_memcpy(&(x), &t_, 8); } \
  } while (0)
'''
# the console's uncached addresses (0xA0000000 up: RAM's mirror, the hardware's registers) go through the host
io = """
uint64_t port_io_load(uint64_t addr);
uint64_t port_io_store(uint64_t addr, uint64_t value);
"""
s = s.replace("#define DEFINE_LOAD(", io + "\n#define DEFINE_LOAD(", 1)
load = '''    wasm_rt_memcpy(&result, MEM_ADDR_MEMOP(mem, addr, sizeof(t1)),            \\
                   sizeof(t1));                                               \\
'''
store = '''    t1 wrapped = (t1)value;                                            \\
'''
assert load in s and store in s and "PORT_BE" not in s
s = s.replace("#define DEFINE_LOAD(", swap + "\n#define DEFINE_LOAD(", 1)
s = s.replace(load, "    if (UNLIKELY(addr >= 0xA0000000u)) addr = port_io_load(addr);              \\\n" + load + "    PORT_BE(result);                                                          \\\n", 1)
s = s.replace(store, store + "    if (UNLIKELY(addr >= 0xA0000000u)) addr = port_io_store(addr, (uint64_t)wrapped); \\\n"
              "    PORT_BE(wrapped);                                                  \\\n", 1)  # the host sees the value
# A function pointer is the function's console address: an indirect call finds the function there (port_func, the
# host's table of the game's functions by address) and doesn't check its type, which the game's C calls through
# pointers of looser types (K&R prototypes), as the N64 never checked either.
call = """#define CALL_INDIRECT(table, t, ft, x, ...) \\
  (CHECK_CALL_INDIRECT(table, ft, x),       \\
   DO_CALL_INDIRECT(table, t, x, __VA_ARGS__))"""
assert call in s, "wasm2c's CALL_INDIRECT changed"
s = s.replace(call, """void *port_func(uint32_t address);
#define CALL_INDIRECT(table, t, ft, x, ...) ((t)port_func(x))(__VA_ARGS__)""")
open(p, "w").write(s)
# a trap says where it happened (the host prints a backtrace) before it stops the thread
trap = "#define TRAP(x) (wasm_rt_trap(WASM_RT_TRAP_##x), 0)"
assert trap in s, "wasm2c's TRAP changed"
s = s.replace(trap, "void port_trace(void);\n#define TRAP(x) (port_trace(), wasm_rt_trap(WASM_RT_TRAP_##x), 0)")
open(p, "w").write(s)
import glob
import os
import re

for f in glob.glob(os.path.join(os.path.dirname(p), "game_*.c")):
    text = open(f).read()
    text = re.sub(r"instance->\w+\.data\[\w+\]\.module_instance", "(void*)instance", text)
    open(f, "w").write(text)
