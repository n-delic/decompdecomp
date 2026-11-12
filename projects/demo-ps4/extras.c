/* SPDX-License-Identifier: CC0-1.0
 * Small independent importer fixtures; no game-derived code. */
int demo_choose(int a, int b, int choose_a) { return choose_a ? a : b; }
unsigned demo_sum(unsigned a, unsigned b) { return a + b; }

__attribute__((noinline)) int demo_local_callee(int value) { return value * 3 + 1; }
int demo_local_caller(int value) { return demo_local_callee(value) + 7; }

extern int demo_external_callee(int value);
extern int demo_import(int value);
int demo_cross_caller(int value) { return demo_external_callee(value) + 11; }
int demo_import_caller(int value) { return demo_import(value) + 13; }
