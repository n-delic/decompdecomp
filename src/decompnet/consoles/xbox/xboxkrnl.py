"""The original Xbox kernel's exports by ordinal: what an Xbox program (XBE) imports from xboxkrnl.exe.

An XBE has no PE import directory. Its kernel thunk table is a zero-terminated list of `0x80000000 | ordinal`
words that the kernel overwrites with the functions' addresses at load time, so the game calls the kernel through
`call [slot]` exactly as a Windows program calls through its import address table. scratch.pe_view names each slot
from this table, so those calls become relocations to named imports as they do on PC.

The table is nxdk's `lib/xboxkrnl/xboxkrnl.exe.def` (github.com/XboxDev/nxdk, CC0-1.0, Stefan Schmidt 2017): one
`ordinal name` per entry, with the name decorated as the XDK's import library decorates it (`NtClose@4` for
__stdcall, `@KfRaiseIrql@4` for __fastcall, `DbgPrint` for __cdecl) and `data` after the exports that are variables
(`KeTickCount`). Ordinals 367-373 aren't exported by any retail kernel.
"""

from __future__ import annotations

import re

_EXPORTS = """
1 AvGetSavedDataAddress@0 | 2 AvSendTVEncoderOption@16 | 3 AvSetDisplayMode@24 | 4 AvSetSavedDataAddress@4
5 DbgBreakPoint@0 | 6 DbgBreakPointWithStatus@4 | 7 DbgLoadImageSymbols@12 | 8 DbgPrint
9 HalReadSMCTrayState@8 | 10 DbgPrompt@12 | 11 DbgUnLoadImageSymbols@12 | 12 ExAcquireReadWriteLockExclusive@4
13 ExAcquireReadWriteLockShared@4 | 14 ExAllocatePool@4 | 15 ExAllocatePoolWithTag@8 | 16 ExEventObjectType data
17 ExFreePool@4 | 18 ExInitializeReadWriteLock@4 | 19 ExInterlockedAddLargeInteger@16
20 @ExInterlockedAddLargeStatistic@8 | 21 @ExInterlockedCompareExchange64@12 | 22 ExMutantObjectType data
23 ExQueryPoolBlockSize@4 | 24 ExQueryNonVolatileSetting@20 | 25 ExReadWriteRefurbInfo@12
26 ExRaiseException@4 | 27 ExRaiseStatus@4 | 28 ExReleaseReadWriteLock@4 | 29 ExSaveNonVolatileSetting@16
30 ExSemaphoreObjectType data | 31 ExTimerObjectType data | 32 @ExfInterlockedInsertHeadList@8
33 @ExfInterlockedInsertTailList@8 | 34 @ExfInterlockedRemoveHeadList@4 | 35 FscGetCacheSize@0
36 FscInvalidateIdleBlocks@0 | 37 FscSetCacheSize@4 | 38 @HalClearSoftwareInterrupt@4
39 HalDisableSystemInterrupt@4 | 40 HalDiskCachePartitionCount data | 41 HalDiskModelNumber data
42 HalDiskSerialNumber data | 43 HalEnableSystemInterrupt@8 | 44 HalGetInterruptVector@8
45 HalReadSMBusValue@16 | 46 HalReadWritePCISpace@24 | 47 HalRegisterShutdownNotification@8
48 @HalRequestSoftwareInterrupt@4 | 49 HalReturnToFirmware@4 | 50 HalWriteSMBusValue@16
51 @InterlockedCompareExchange@12 | 52 @InterlockedDecrement@4 | 53 @InterlockedIncrement@4
54 @InterlockedExchange@8 | 55 @InterlockedExchangeAdd@8 | 56 @InterlockedFlushSList@4
57 @InterlockedPopEntrySList@4 | 58 @InterlockedPushEntrySList@8 | 59 IoAllocateIrp@4
60 IoBuildAsynchronousFsdRequest@24 | 61 IoBuildDeviceIoControlRequest@36 | 62 IoBuildSynchronousFsdRequest@28
63 IoCheckShareAccess@20 | 64 IoCompletionObjectType data | 65 IoCreateDevice@24 | 66 IoCreateFile@40
67 IoCreateSymbolicLink@8 | 68 IoDeleteDevice@4 | 69 IoDeleteSymbolicLink@4 | 70 IoDeviceObjectType data
71 IoFileObjectType data | 72 IoFreeIrp@4 | 73 IoInitializeIrp@12 | 74 IoInvalidDeviceRequest@8
75 IoQueryFileInformation@20 | 76 IoQueryVolumeInformation@20 | 77 IoQueueThreadIrp@4 | 78 IoRemoveShareAccess@8
79 IoSetIoCompletion@20 | 80 IoSetShareAccess@16 | 81 IoStartNextPacket@4 | 82 IoStartNextPacketByKey@8
83 IoStartPacket@12 | 84 IoSynchronousDeviceIoControlRequest@32 | 85 IoSynchronousFsdRequest@20
86 @IofCallDriver@8 | 87 @IofCompleteRequest@8 | 88 KdDebuggerEnabled data | 89 KdDebuggerNotPresent data
90 IoDismountVolume@4 | 91 IoDismountVolumeByName@4 | 92 KeAlertResumeThread@4 | 93 KeAlertThread@8
94 KeBoostPriorityThread@8 | 95 KeBugCheck@4 | 96 KeBugCheckEx@20 | 97 KeCancelTimer@4 | 98 KeConnectInterrupt@4
99 KeDelayExecutionThread@12 | 100 KeDisconnectInterrupt@4 | 101 KeEnterCriticalRegion@0 | 102 MmGlobalData data
103 KeGetCurrentIrql@0 | 104 KeGetCurrentThread@0 | 105 KeInitializeApc@28 | 106 KeInitializeDeviceQueue@4
107 KeInitializeDpc@12 | 108 KeInitializeEvent@12 | 109 KeInitializeInterrupt@28 | 110 KeInitializeMutant@8
111 KeInitializeQueue@8 | 112 KeInitializeSemaphore@12 | 113 KeInitializeTimerEx@8
114 KeInsertByKeyDeviceQueue@12 | 115 KeInsertDeviceQueue@8 | 116 KeInsertHeadQueue@8 | 117 KeInsertQueue@8
118 KeInsertQueueApc@16 | 119 KeInsertQueueDpc@12 | 120 KeInterruptTime data | 121 KeIsExecutingDpc@0
122 KeLeaveCriticalRegion@0 | 123 KePulseEvent@12 | 124 KeQueryBasePriorityThread@4 | 125 KeQueryInterruptTime@0
126 KeQueryPerformanceCounter@0 | 127 KeQueryPerformanceFrequency@0 | 128 KeQuerySystemTime@4
129 KeRaiseIrqlToDpcLevel@0 | 130 KeRaiseIrqlToSynchLevel@0 | 131 KeReleaseMutant@16 | 132 KeReleaseSemaphore@16
133 KeRemoveByKeyDeviceQueue@8 | 134 KeRemoveDeviceQueue@4 | 135 KeRemoveEntryDeviceQueue@8
136 KeRemoveQueue@12 | 137 KeRemoveQueueDpc@4 | 138 KeResetEvent@4 | 139 KeRestoreFloatingPointState@4
140 KeResumeThread@4 | 141 KeRundownQueue@4 | 142 KeSaveFloatingPointState@4 | 143 KeSetBasePriorityThread@8
144 KeSetDisableBoostThread@8 | 145 KeSetEvent@12 | 146 KeSetEventBoostPriority@8 | 147 KeSetPriorityProcess@8
148 KeSetPriorityThread@8 | 149 KeSetTimer@16 | 150 KeSetTimerEx@20 | 151 KeStallExecutionProcessor@4
152 KeSuspendThread@4 | 153 KeSynchronizeExecution@12 | 154 KeSystemTime data | 155 KeTestAlertThread@4
156 KeTickCount data | 157 KeTimeIncrement data | 158 KeWaitForMultipleObjects@32 | 159 KeWaitForSingleObject@20
160 @KfRaiseIrql@4 | 161 @KfLowerIrql@4 | 162 KiBugCheckData data | 163 @KiUnlockDispatcherDatabase@4
164 LaunchDataPage data | 165 MmAllocateContiguousMemory@4 | 166 MmAllocateContiguousMemoryEx@20
167 MmAllocateSystemMemory@8 | 168 MmClaimGpuInstanceMemory@8 | 169 MmCreateKernelStack@8
170 MmDeleteKernelStack@8 | 171 MmFreeContiguousMemory@4 | 172 MmFreeSystemMemory@8 | 173 MmGetPhysicalAddress@4
174 MmIsAddressValid@4 | 175 MmLockUnlockBufferPages@12 | 176 MmLockUnlockPhysicalPage@8 | 177 MmMapIoSpace@12
178 MmPersistContiguousMemory@12 | 179 MmQueryAddressProtect@4 | 180 MmQueryAllocationSize@4
181 MmQueryStatistics@4 | 182 MmSetAddressProtect@12 | 183 MmUnmapIoSpace@8 | 184 NtAllocateVirtualMemory@20
185 NtCancelTimer@8 | 186 NtClearEvent@4 | 187 NtClose@4 | 188 NtCreateDirectoryObject@8 | 189 NtCreateEvent@16
190 NtCreateFile@36 | 191 NtCreateIoCompletion@16 | 192 NtCreateMutant@12 | 193 NtCreateSemaphore@16
194 NtCreateTimer@12 | 195 NtDeleteFile@4 | 196 NtDeviceIoControlFile@40 | 197 NtDuplicateObject@12
198 NtFlushBuffersFile@8 | 199 NtFreeVirtualMemory@12 | 200 NtFsControlFile@40 | 201 NtOpenDirectoryObject@8
202 NtOpenFile@24 | 203 NtOpenSymbolicLinkObject@8 | 204 NtProtectVirtualMemory@16 | 205 NtPulseEvent@8
206 NtQueueApcThread@20 | 207 NtQueryDirectoryFile@40 | 208 NtQueryDirectoryObject@24 | 209 NtQueryEvent@8
210 NtQueryFullAttributesFile@8 | 211 NtQueryInformationFile@20 | 212 NtQueryIoCompletion@8
213 NtQueryMutant@8 | 214 NtQuerySemaphore@8 | 215 NtQuerySymbolicLinkObject@12 | 216 NtQueryTimer@8
217 NtQueryVirtualMemory@8 | 218 NtQueryVolumeInformationFile@20 | 219 NtReadFile@32 | 220 NtReadFileScatter@32
221 NtReleaseMutant@8 | 222 NtReleaseSemaphore@12 | 223 NtRemoveIoCompletion@20 | 224 NtResumeThread@8
225 NtSetEvent@8 | 226 NtSetInformationFile@20 | 227 NtSetIoCompletion@20 | 228 NtSetSystemTime@8
229 NtSetTimerEx@32 | 230 NtSignalAndWaitForSingleObjectEx@20 | 231 NtSuspendThread@8
232 NtUserIoApcDispatcher@12 | 233 NtWaitForSingleObject@12 | 234 NtWaitForSingleObjectEx@16
235 NtWaitForMultipleObjectsEx@24 | 236 NtWriteFile@32 | 237 NtWriteFileGather@32 | 238 NtYieldExecution@0
239 ObCreateObject@16 | 240 ObDirectoryObjectType data | 241 ObInsertObject@16 | 242 ObMakeTemporaryObject@4
243 ObOpenObjectByName@16 | 244 ObOpenObjectByPointer@12 | 245 ObpObjectHandleTable data
246 ObReferenceObjectByHandle@12 | 247 ObReferenceObjectByName@20 | 248 ObReferenceObjectByPointer@8
249 ObSymbolicLinkObjectType data | 250 @ObfDereferenceObject@4 | 251 @ObfReferenceObject@4
252 PhyGetLinkState@4 | 253 PhyInitialize@8 | 254 PsCreateSystemThread@20 | 255 PsCreateSystemThreadEx@40
256 PsQueryStatistics@4 | 257 PsSetCreateThreadNotifyRoutine@4 | 258 PsTerminateSystemThread@4
259 PsThreadObjectType data | 260 RtlAnsiStringToUnicodeString@12 | 261 RtlAppendStringToString@8
262 RtlAppendUnicodeStringToString@8 | 263 RtlAppendUnicodeToString@8 | 264 RtlAssert@16
265 RtlCaptureContext@4 | 266 RtlCaptureStackBackTrace@16 | 267 RtlCharToInteger@12 | 268 RtlCompareMemory@12
269 RtlCompareMemoryUlong@12 | 270 RtlCompareString@12 | 271 RtlCompareUnicodeString@12 | 272 RtlCopyString@8
273 RtlCopyUnicodeString@8 | 274 RtlCreateUnicodeString@8 | 275 RtlDowncaseUnicodeChar@4
276 RtlDowncaseUnicodeString@12 | 277 RtlEnterCriticalSection@4 | 278 RtlEnterCriticalSectionAndRegion@4
279 RtlEqualString@12 | 280 RtlEqualUnicodeString@12 | 281 RtlExtendedIntegerMultiply@12
282 RtlExtendedLargeIntegerDivide@16 | 283 RtlExtendedMagicDivide@20 | 284 RtlFillMemory@12
285 RtlFillMemoryUlong@12 | 286 RtlFreeAnsiString@4 | 287 RtlFreeUnicodeString@4 | 288 RtlGetCallersAddress@8
289 RtlInitAnsiString@8 | 290 RtlInitUnicodeString@8 | 291 RtlInitializeCriticalSection@4
292 RtlIntegerToChar@16 | 293 RtlIntegerToUnicodeString@12 | 294 RtlLeaveCriticalSection@4
295 RtlLeaveCriticalSectionAndRegion@4 | 296 RtlLowerChar@4 | 297 RtlMapGenericMask@8 | 298 RtlMoveMemory@12
299 RtlMultiByteToUnicodeN@20 | 300 RtlMultiByteToUnicodeSize@12 | 301 RtlNtStatusToDosError@4
302 RtlRaiseException@4 | 303 RtlRaiseStatus@4 | 304 RtlTimeFieldsToTime@8 | 305 RtlTimeToTimeFields@8
306 RtlTryEnterCriticalSection@4 | 307 @RtlUlongByteSwap@4 | 308 RtlUnicodeStringToAnsiString@12
309 RtlUnicodeStringToInteger@12 | 310 RtlUnicodeToMultiByteN@20 | 311 RtlUnicodeToMultiByteSize@12
312 RtlUnwind@16 | 313 RtlUpcaseUnicodeChar@4 | 314 RtlUpcaseUnicodeString@12
315 RtlUpcaseUnicodeToMultiByteN@20 | 316 RtlUpperChar@4 | 317 RtlUpperString@8 | 318 @RtlUshortByteSwap@4
319 RtlWalkFrameChain@12 | 320 RtlZeroMemory@8 | 321 XboxEEPROMKey data | 322 XboxHardwareInfo data
323 XboxHDKey data | 324 XboxKrnlVersion data | 325 XboxSignatureKey data | 326 XeImageFileName data
327 XeLoadSection@4 | 328 XeUnloadSection@4 | 329 READ_PORT_BUFFER_UCHAR@12 | 330 READ_PORT_BUFFER_USHORT@12
331 READ_PORT_BUFFER_ULONG@12 | 332 WRITE_PORT_BUFFER_UCHAR@12 | 333 WRITE_PORT_BUFFER_USHORT@12
334 WRITE_PORT_BUFFER_ULONG@12 | 335 XcSHAInit@4 | 336 XcSHAUpdate@12 | 337 XcSHAFinal@8 | 338 XcRC4Key@12
339 XcRC4Crypt@12 | 340 XcHMAC@28 | 341 XcPKEncPublic@12 | 342 XcPKDecPrivate@12 | 343 XcPKGetKeyLen@4
344 XcVerifyPKCS1Signature@12 | 345 XcModExp@20 | 346 XcDESKeyParity@8 | 347 XcKeyTable@12 | 348 XcBlockCrypt@20
349 XcBlockCryptCBC@28 | 350 XcCryptService@8 | 351 XcUpdateCrypto@8 | 352 RtlRip@12 | 353 XboxLANKey data
354 XboxAlternateSignatureKeys data | 355 XePublicKeyData data | 356 HalBootSMCVideoMode data
357 IdexChannelObject data | 358 HalIsResetOrShutdownPending@0 | 359 IoMarkIrpMustComplete@4
360 HalInitiateShutdown@0 | 361 RtlSnprintf | 362 RtlSprintf | 363 RtlVsnprintf | 364 RtlVsprintf
365 HalEnableSecureTrayEject@0 | 366 HalWriteSMCScratchRegister@4 | 374 MmDbgAllocateMemory@8
375 MmDbgFreeMemory@8 | 376 MmDbgQueryAvailablePages@0 | 377 MmDbgReleaseAddress@8 | 378 MmDbgWriteCheck@8
"""

_ENTRY = re.compile(r"(\d+) (@?[A-Za-z_][A-Za-z0-9_]*(?:@\d+)?)( data)?")
# ordinal -> (the name as the XDK's import library decorates it, whether the export is a variable)
EXPORTS: dict[int, tuple[str, bool]] = {int(m.group(1)): (m.group(2), bool(m.group(3)))
                                        for m in _ENTRY.finditer(_EXPORTS)}


def name(ordinal: int) -> str:
    """The export's plain C name (`NtClose`), or `xboxkrnl_<ordinal>` for one the table doesn't know, as the PC
    path names a Windows import by ordinal."""
    entry = EXPORTS.get(ordinal)
    if entry is None:
        return f"xboxkrnl_{ordinal}"
    return entry[0].lstrip("@").split("@", 1)[0]

