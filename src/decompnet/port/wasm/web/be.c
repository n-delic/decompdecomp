/*
 * The browser build's part of the game module (port/wasm/web.py): what bemem.py makes wasm2c's C do for the native
 * build. The game's IR calls these for every load and store and every call through a pointer (webir.py).
 *
 * - Memory is big-endian, as on the N64: each access swaps its bytes. An uncached address (0xA0000000 up: RAM's
 *   mirror, the hardware's registers) goes through the host (port_io_load / port_io_store), which says where to
 *   read or write instead, and sees each value written.
 * - A function pointer is the function's console address (or, for a function with none, its wasm table index).
 *   port_icall finds the function's universal entry (F.port_u) in the table each file registers at start-up and
 *   calls it with the call's arguments in 64-bit slots: no wasm type check, as the N64 had none.
 *
 * Compiled with the game's objects (freestanding: no C library) and linked into game-web.wasm.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;

#define IMPORT(name) __attribute__((import_module("env"), import_name(name)))
IMPORT("port_io_load") u64 port_io_load(u64 addr);
IMPORT("port_io_store") u64 port_io_store(u64 addr, u64 value);
IMPORT("port_preempt") void port_preempt(void);
IMPORT("port_bad_call") void port_bad_call(u32 address);

IMPORT("port_yield") void port_yield(void);

/* Time slices: natively the retrace (the host's main thread) runs while a game thread spins on memory, waiting for
   what an interrupt's thread will write (a racing game's loader polls a flag the VI's thread sets). In the browser
   nothing else runs until the thread gives way, so every so many loads it asks the host whether it's time to
   (port_yield: a pending preemption, else once its slice is up, back to the page for the retrace). */
static u32 loads;
#define AT(a, t) (*(volatile t *)(unsigned long)(a))
static inline u32 at_load(u32 a) {
    if (!(++loads & 0x3FFFF)) port_yield();
    return a >= 0xA0000000u ? (u32)port_io_load(a) : a;
}

u8 port_ld8(void *p) { return AT(at_load((u32)(unsigned long)p), u8); }
u16 port_ld16(void *p) { return __builtin_bswap16(AT(at_load((u32)(unsigned long)p), u16)); }
u32 port_ld32(void *p) { return __builtin_bswap32(AT(at_load((u32)(unsigned long)p), u32)); }
u64 port_ld64(void *p) { return __builtin_bswap64(AT(at_load((u32)(unsigned long)p), u64)); }
float port_ldf(void *p) { u32 v = port_ld32(p); float f; __builtin_memcpy(&f, &v, 4); return f; }
double port_ldd(void *p) { u64 v = port_ld64(p); double d; __builtin_memcpy(&d, &v, 8); return d; }

/* A byte or halfword comes as a whole word, narrowed here: the IR passes an i8 or i16 with whatever its upper bits
   hold (a sign-extended -127 is 0xFFFFFF81), and a u16 parameter would let the compiler assume them zero, which
   swapped 0xFF81 into 0xFFFF (a racing game's menu text came out as stray glyphs). */
#define STORE(a, v) ((a) >= 0xA0000000u ? (u32)port_io_store((a), (u64)(v)) : (a))
void port_st8(void *p, u32 v) { u32 a = (u32)(unsigned long)p; v &= 0xFF; AT(STORE(a, v), u8) = (u8)v; }
void port_st16(void *p, u32 v) { u32 a = (u32)(unsigned long)p; v &= 0xFFFF; AT(STORE(a, v), u16) = __builtin_bswap16((u16)v); }
void port_st32(void *p, u32 v) { u32 a = (u32)(unsigned long)p; AT(STORE(a, v), u32) = __builtin_bswap32(v); }
void port_st64(void *p, u64 v) { u32 a = (u32)(unsigned long)p; AT(STORE(a, v), u64) = __builtin_bswap64(v); }
void port_stf(void *p, float f) { u32 v; __builtin_memcpy(&v, &f, 4); port_st32(p, v); }
void port_std(void *p, double d) { u64 v; __builtin_memcpy(&v, &d, 8); port_st64(p, v); }

/* ---------------------------------------------------------------- calls through pointers */

/* As each file registers it: its console address (0: none) as the IR wrote it, then the function and its universal
   entry, which the linker filled in and beslots.py swapped with every other pointer in the game's data. */
typedef struct { u32 address, function, universal; } Entry;
typedef void (*Universal)(u64 *slots, u32 count);

#define MAX_FILES 16384
#define MAX_FUNCS 65536
static struct { const Entry *entries; u32 count; } files[MAX_FILES];
static u32 file_count;
static struct { u32 key, universal; } by_address[MAX_FUNCS], by_index[MAX_FUNCS];
static u32 address_count, index_count, sorted;

void port_register(const Entry *entries, u32 count) {
    if (file_count < MAX_FILES) { files[file_count].entries = entries; files[file_count].count = count; file_count++; }
    sorted = 0;
}

static void sort(void *base, u32 n) { /* the tables, by key: a shell sort (no C library here) */
    struct { u32 key, universal; } *a = base, t;
    for (u32 gap = n / 2; gap; gap /= 2)
        for (u32 i = gap; i < n; i++) {
            t = a[i];
            u32 j = i;
            for (; j >= gap && a[j - gap].key > t.key; j -= gap) a[j] = a[j - gap];
            a[j] = t;
        }
}

static void build_tables(void) {
    address_count = index_count = 0;
    for (u32 f = 0; f < file_count; f++)
        for (u32 i = 0; i < files[f].count; i++) {
            const Entry *e = &files[f].entries[i];
            u32 universal = __builtin_bswap32(e->universal), function = __builtin_bswap32(e->function);
            if (e->address && address_count < MAX_FUNCS) {
                by_address[address_count].key = e->address; by_address[address_count++].universal = universal;
            }
            if (index_count < MAX_FUNCS) { by_index[index_count].key = function; by_index[index_count++].universal = universal; }
        }
    sort(by_address, address_count);
    sort(by_index, index_count);
    sorted = 1;
}

static u32 find(void *table, u32 n, u32 key) {
    struct { u32 key, universal; } *a = table;
    u32 lo = 0, hi = n;
    while (lo < hi) {
        u32 mid = (lo + hi) / 2;
        if (a[mid].key < key) lo = mid + 1; else hi = mid;
    }
    return lo < n && a[lo].key == key ? a[lo].universal : 0;
}

/* The universal entry of the function a pointer names: a console address, else a table index; 0 for none. */
u32 port_universal(u32 pointer) {
    if (!sorted) build_tables();
    u32 u = pointer >= 0x80000000u ? find(by_address, address_count, pointer) : 0;
    return u ? u : find(by_index, index_count, pointer);
}

void port_icall(void *pointer, u64 *slots, u32 count) {
    port_preempt(); /* a safe point, as the native build's port_func is */
    u32 u = port_universal((u32)(unsigned long)pointer);
    if (!u) { port_bad_call((u32)(unsigned long)pointer); __builtin_trap(); }
    ((Universal)(unsigned long)u)(slots, count);
}

/* The host starts each game thread here: its entry (a function pointer) with its one argument. */
void port_call1(u32 pointer, u32 arg) {
    u64 slots[17] = {0};
    slots[0] = arg;
    port_icall((void *)(unsigned long)pointer, slots, 1);
}

/* The game's shadow stack pointer, which the host gives each game thread its own of (host.c's wait_turn). */
u32 port_sp(void) {
    u32 sp;
    __asm__ volatile(".globaltype __stack_pointer, i32\n\tglobal.get __stack_pointer\n\tlocal.set %0" : "=r"(sp));
    return sp;
}
void port_set_sp(u32 sp) {
    __asm__ volatile(".globaltype __stack_pointer, i32\n\tlocal.get %0\n\tglobal.set __stack_pointer" : : "r"(sp));
}

