/* SPDX-License-Identifier: CC0-1.0
 * A separate unit and a stand-in import entry, entirely synthetic. Alignment makes
 * the importer put these beyond the first unit without needing a large function. */
__attribute__((aligned(8192), noinline)) int demo_external_callee(int value) { return value * 5 + 2; }
int demo_import(int value) { return value ^ 0x55; }
