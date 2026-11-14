/* The browser build runs the game's WebAssembly itself: a trap is the browser's, reported by the page. */
#ifndef PORT_WEB_WASM_RT_IMPL_H
#define PORT_WEB_WASM_RT_IMPL_H
typedef int wasm_rt_trap_t;
#define WASM_RT_TRAP_CALL_INDIRECT 1
static inline void wasm_rt_init(void) {}
static inline void wasm_rt_init_thread(void) {}
#define wasm_rt_impl_try() 0
static inline const char *wasm_rt_strerror(wasm_rt_trap_t t) { (void)t; return "trap"; }
_Noreturn static inline void wasm_rt_trap(wasm_rt_trap_t t) { (void)t; __builtin_trap(); }
#endif
