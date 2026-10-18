"""The PS2 kernel's system calls, by number: the names their stubs get.

A PS2 game calls the kernel through tiny stubs from Sony's libkernl, `addiu v1, $zero, N; syscall; jr ra; nop`, one
per call. They're hand-written assembly (handwritten.py marks them so), and in a stripped game they're just
func_0012A0 like everything else (this table names 154 of them in GTA San Andreas, 129 in Ridge Racer V). The number in v1 says which
kernel call each one is, and the names are public knowledge (the PS2 homebrew SDK documents them), so each stub gets
its real name at import, and every function that calls one reads FlushCache() instead of func_0012A0().

Negative numbers are the calls made from interrupt handlers (iSignalSema, iFlushCache, ...).
"""

from __future__ import annotations

SYSCALLS = {
    0x01: "ResetEE", 0x02: "SetGsCrt", 0x04: "KExit", 0x05: "ResumeIntrDispatch", 0x06: "_LoadExecPS2",
    0x07: "_ExecPS2", 0x08: "ResumeT3IntrDispatch", 0x09: "RFU009", 0x0A: "AddSbusIntcHandler",
    0x0B: "RemoveSbusIntcHandler", 0x0C: "Interrupt2Iop", 0x0D: "SetVTLBRefillHandler", 0x0E: "SetVCommonHandler",
    0x0F: "SetVInterruptHandler", 0x10: "AddIntcHandler", 0x11: "RemoveIntcHandler", 0x12: "AddDmacHandler",
    0x13: "RemoveDmacHandler", 0x14: "_EnableIntc", 0x15: "_DisableIntc", 0x16: "_EnableDmac", 0x17: "_DisableDmac",
    0x18: "_SetAlarm", 0x19: "_ReleaseAlarm", 0x20: "CreateThread", 0x21: "DeleteThread", 0x22: "StartThread",
    0x23: "ExitThread", 0x24: "ExitDeleteThread", 0x25: "TerminateThread", 0x27: "DisableDispatchThread",
    0x28: "EnableDispatchThread", 0x29: "ChangeThreadPriority", 0x2B: "RotateThreadReadyQueue",
    0x2D: "ReleaseWaitThread", 0x2F: "GetThreadId", 0x30: "ReferThreadStatus", 0x32: "SleepThread",
    0x33: "WakeupThread", 0x35: "CancelWakeupThread", 0x37: "SuspendThread", 0x39: "ResumeThread", 0x3B: "RFU059",
    0x3C: "SetupThread", 0x3D: "SetupHeap", 0x3E: "EndOfHeap", 0x40: "CreateSema", 0x41: "DeleteSema",
    0x42: "SignalSema", 0x44: "WaitSema", 0x45: "PollSema", 0x47: "ReferSemaStatus", 0x4A: "SetOsdConfigParam",
    0x4B: "GetOsdConfigParam", 0x4C: "GetGsHParam", 0x4D: "GetGsVParam", 0x4E: "SetGsHParam", 0x4F: "SetGsVParam",
    0x50: "CreateEventFlag", 0x51: "DeleteEventFlag", 0x52: "SetEventFlag", 0x53: "iSetEventFlag", 0x54: "xlaunch",
    0x55: "PutTLBEntry", 0x56: "_SetTLBEntry", 0x57: "GetTLBEntry", 0x58: "ProbeTLBEntry", 0x59: "ExpandScratchPad",
    0x5A: "Copy", 0x5B: "GetEntryAddress", 0x5C: "EnableIntcHandler", 0x5D: "DisableIntcHandler",
    0x5E: "EnableDmacHandler", 0x5F: "DisableDmacHandler", 0x60: "KSeg0", 0x61: "EnableCache", 0x62: "DisableCache",
    0x63: "GetCop0", 0x64: "FlushCache", 0x66: "CpuConfig", 0x69: "RFU105", 0x6B: "sceSifStopDma",
    0x6C: "SetCPUTimerHandler", 0x6D: "SetCPUTimer", 0x6E: "SetOsdConfigParam2", 0x6F: "GetOsdConfigParam2",
    0x70: "GsGetIMR", 0x71: "GsPutIMR", 0x72: "SetPgifHandler", 0x73: "SetVSyncFlag", 0x74: "SetSyscall",
    0x75: "_print", 0x76: "sceSifDmaStat", 0x77: "sceSifSetDma", 0x78: "sceSifSetDChain", 0x79: "sceSifSetReg",
    0x7A: "sceSifGetReg", 0x7B: "_ExecOSD", 0x7C: "Deci2Call", 0x7D: "PSMode", 0x7E: "MachineType",
    0x7F: "GetMemorySize", 0x80: "_GetGsDxDyOffset", 0x82: "_InitTLB", 0x83: "FindAddress", 0x85: "SetMemoryMode",
    0x86: "GetMemoryMode", 0x87: "ExecPSX", 0xFC: "SetAlarm", 0xFE: "ReleaseAlarm", -0x1A: "_iEnableIntc",
    -0x1B: "_iDisableIntc", -0x1C: "_iEnableDmac", -0x1D: "_iDisableDmac", -0x1E: "_iSetAlarm",
    -0x1F: "_iReleaseAlarm", -0x26: "iTerminateThread", -0x2A: "iChangeThreadPriority",
    -0x2C: "_iRotateThreadReadyQueue", -0x2E: "iReleaseWaitThread", -0x2F: "_iGetThreadId",
    -0x31: "iReferThreadStatus", -0x34: "_iWakeupThread", -0x36: "iCancelWakeupThread", -0x38: "_iSuspendThread",
    -0x3A: "iResumeThread", -0x43: "iSignalSema", -0x46: "iPollSema", -0x48: "iReferSemaStatus",
    -0x49: "iDeleteSema", -0x55: "iPutTLBEntry", -0x56: "iSetTLBEntry", -0x57: "iGetTLBEntry",
    -0x58: "iProbeTLBEntry", -0x5C: "iEnableIntcHandler", -0x5D: "iDisableIntcHandler", -0x5E: "iEnableDmacHandler",
    -0x5F: "iDisableDmacHandler", -0x67: "iGetCop0", -0x68: "iFlushCache", -0x6A: "iCpuConfig", -0x70: "iGsGetIMR",
    -0x71: "iGsPutIMR", -0x76: "isceSifDmaStat", -0x77: "isceSifSetDma", -0x78: "isceSifSetDChain",
    -0xFD: "iSetAlarm", -0xFF: "iReleaseAlarm",
}

WORD_BYTES = 4
SET_V1 = 0x24030000  # addiu v1, $zero, imm
SET_V1_MASK = 0xFFFF0000
IMMEDIATE_MASK = 0xFFFF
SIGN_BIT = 0x8000
SYSCALL_MASK = 0xFC00003F  # opcode 0 (SPECIAL), funct 0x0C; the code field between them is free
SYSCALL = 0x0000000C
JR_RA = 0x03E00008
NOP = 0


def stub_number(code: bytes) -> int | None:
    """The system call number of a kernel stub (its code, little-endian), or None when it isn't one."""
    words = [int.from_bytes(code[at:at + WORD_BYTES], "little") for at in range(0, len(code) - WORD_BYTES + 1, WORD_BYTES)]
    if len(words) not in (3, 4) or words[0] & SET_V1_MASK != SET_V1 or words[1] & SYSCALL_MASK != SYSCALL:
        return None
    if words[2] != JR_RA or words[3:] not in ([], [NOP]):
        return None
    number = words[0] & IMMEDIATE_MASK
    return number - 2 * SIGN_BIT if number & SIGN_BIT else number


def stub_names(functions: list[tuple[str, bytes]], taken: set[str]) -> list[tuple[str, str]]:
    """(old name, kernel name) for each kernel stub among (name, code) that still has a generated name (func_...).
    A second stub of the same call gets _2, and a name the game already uses is left alone."""
    renames, used = [], set(taken)
    for name, code in functions:
        if not name.startswith("func_") or (number := stub_number(code or b"")) is None or number not in SYSCALLS:
            continue
        new, copy = SYSCALLS[number], 2
        while new in used:
            new, copy = f"{SYSCALLS[number]}_{copy}", copy + 1
        used.add(new)
        renames.append((name, new))
    return renames
