# Demo Quest PSP start-up: clear .bss, set the stack and $gp, call main. Written for decompdecomp (CC0).
# A PSP module's loader sets $gp itself (from the module info); a demo with none sets it here.
# Numeric registers ($8 = t0, $9 = t1, $1 = at, $28 = gp, $29 = sp): the SDK 1.3.1 assembler knows no others.
        .set noreorder
        .set noat
        .section .text
        .globl _start
        .ent _start
_start:
        lui     $8, %hi(_bss_start)
        addiu   $8, $8, %lo(_bss_start)
        lui     $9, %hi(_bss_end)
        addiu   $9, $9, %lo(_bss_end)
.Lclear:
        sw      $0, 0($8)
        addiu   $8, $8, 4
        sltu    $1, $8, $9
        bnez    $1, .Lclear
        nop
        lui     $29, 0x0A00
        lui     $28, %hi(_gp)
        jal     main
        addiu   $28, $28, %lo(_gp)
.Lhang:
        j       .Lhang
        nop
        .end _start
