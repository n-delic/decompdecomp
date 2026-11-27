"""Exact, self-contained N64 SDK assembly fingerprints. No SDK instructions are bundled.

These bodies have no external references or data: full bytes are invariant at their
link address. The public ultralib references identify each routine. The digests cover complete
linked bodies, including assembler scheduling and return delay slots.
Do not add relocation-normalized hashes here: they cannot prove linked equality.
"""
import hashlib

CORPUS = 'libultra handwritten assembly'
REFERENCE = 'https://github.com/decompals/ultralib/blob/main/src/'
KNOWN = {
    '44d38cf51b9d9f008e5245f63723219224ec20e1bc04fb0010af395b86016dd4':
        ('bcmp', 272, 'period GNU assembler: move -> addu', 'libc/bcmp.s'),
    '5130e1e88ac02a986d4360646384120293476316e575ab621f99dee6998fe5af':
        ('bcmp', 272, 'modern GNU assembler: move -> or', 'libc/bcmp.s'),
    '1c6198ec810303f71baaf1c5fa044a8e0eea9446ac383854d910c4fee4fffcf3':
        ('bzero', 160, 'period and modern GNU assemblers', 'libc/bzero.s'),
    '7a8576245f42a86f8db2583eda5f1e2ae908120fa2abda59d18eb9e53c614ad5':
        ('bcopy', 796, 'overlap-aware scheduled copy', 'libc/bcopy.s'),
    '80864487ec220bb1752f4b37d9ea39cb086920386412e76c70e46b00d0bf8c6a':
        ('guScaleF', 72, 'scheduled MGU float scale matrix', 'mgu/scale.s'),
    '67997834f0ff8e984db1d02069d6be1d0ef693a45f5f1a88dde6d1b6360237a6':
        ('guMtxCatF', 240, 'scheduled MGU float matrix product', 'mgu/mtxcatf.s'),
    '0ec1c8e451e2aa4e77599d0aea02d5b338957ffa874d03a3ff434d6f4ea226c3':
        ('guMtxF2L', 100, 'scheduled MGU fixed-point packer', 'mgu/mtxf2l.s'),
    'e706afe4b72d296062571ca9918036a07197bdf713976810f05d26e67be6a9e3':
        ('guMtxL2F', 108, 'scheduled MGU fixed-point unpacker', 'mgu/mtxl2f.s'),
    # The t9 scratch register and the store in the return slot agree with
    # exceptasm's queue pop, directly called by the adjacent dispatcher.
    '482c149f613482f79eda678641c3b4a0a160a6346976865d6055c4494c476ad9':
        ('__osEnqueueThread', 72, 'scheduler priority enqueue', 'os/exceptasm.s'),
    '9cfacc440af965d2989166dd711ef58ae5a92e68c6ef3cf399a0403e1fd36afe':
        ('__osPopThread', 16, 'scheduler queue pop', 'os/exceptasm.s'),
    '8ae1ab78f7b451eff125bd297f76cbcc5f2e3c7a54c6c1497d3f3c0b281f886f':
        ('osInvalDCache', 176, 'scheduled data cache invalidation', 'os/invaldcache.s'),
    '1f0c92717c0320c652883aff97a82af6fc935d0aba9eab058253f183113ec924':
        ('osWritebackDCache', 116, 'scheduled data cache writeback', 'os/writebackdcache.s'),
    'b868489aa115fdc2f51ebae88065f21a34d22bfd3e4c0f14ab431fe181859810':
        ('osInvalICache', 116, 'scheduled instruction cache invalidation', 'os/invalicache.s'),
    # The same four routines as other libultra builds assembled them: `move` where the older build has
    # `addu $x, $y, $zero`, and the `andi`/`addiu` pair in the other order. The cache operations, the loop bounds
    # and the control flow are the same instruction for instruction (checked against the variants above).
    '713e7b78373df6fbf3d030b5237e9c1e2148c9443cbfdf938f2e8ffdf8e2d326':
        ('osInvalICache', 116, 'instruction cache invalidation, `move` form', 'os/invalicache.s'),
    'f8a381ee23df95612ed0fb5ddcef245f947b01e12d78364fa5b45491f4eb7b55':
        ('osInvalDCache', 172, 'data cache invalidation, `move` form with the filled delay slot', 'os/invaldcache.s'),
    '5306341d7122fdbbae63d48917c76f7f6c2ee0321e490862302581561bf0474c':
        ('osWritebackDCache', 116, 'data cache writeback, `move` form', 'os/writebackdcache.s'),
    '394d3bf1ec1a51ef7eff9113ae1103280d0f09b29035cfd8630a0843d890c4f5':
        ('osWritebackDCacheAll', 40, 'whole data cache writeback', 'os/writebackdcacheall.s'),
    # Found by a later reading: bcopy and the RCP monitor's stubs (bodies that end in a branch, not a return), and
    # the 2.0H archives (LIBGULTRA.A, whose upper-case names the first reading skipped).
    '3ae142d98dccf82c67c4e1c414dd733ec61e39967df75dfb68d686009654d870':
        ('__rmonStepRCP', 20, 'libultra 2.0H–2.0L', 'rmon/rmonrcp.s'),
    '91d19e63bcea185d83fbf1167b992e896c4fbc17c7c2f3191efa3ab1d640e848':
        ('__rmonStepRCP', 24, 'libultra 2.0I debug library', 'rmon/rmonrcp.s'),
    'f398a13824f7307d1d66f05ca80f49e0ed734886d23c0d2a3bcdd4f11601b6b0':
        ('bcopy', 772, 'libultra 2.0I', 'libc/bcopy.s'),
    'd80867d460a740ff5de92cc5d7e394dfdec340ab29b1df8af4dcb5c183c19208':
        ('bcopy', 856, 'libultra 2.0I debug library', 'libc/bcopy.s'),
    '7fa4b4a0cf423034cf3a4c9d888781edd4ee755a263607b407beca17d13cf322':
        ('osUnmapTLBAll', 68, 'libultra 2.0H', 'os/unmaptlball.s'),
    # Every other self-contained routine in the SDK's own libultra libraries (2.0I to 2.0L, the release, debug and ROM
    # builds), read from the archives' objects: assembly-only objects, bodies with no relocation, trailing alignment
    # padding dropped after the return's delay slot. The same routines the older entries above come from, so those
    # digests were checked against this reading (it finds each of them). A digest shared by two names is left out.
    'aa2baf4a99416ed0e79b573e97912af1cf2ce317244b986310d5194f43f93dd7':
        ('__assertBreak', 12, 'libultra 2.0I–2.0L', 'os/assertbreak.s'),
    'b5ec893cd5c1e37c723f982142b67fc24befcf35b6e48b596abbcca4c4d44560':
        ('__osDisableInt', 32, 'libultra 2.0I', 'os/interrupt.s'),
    '8091a95dae39c6b1fc1a33a413913174da32d2a1c61971b45d5170e8b51c54f6':
        ('__osEnqueueThread', 72, 'libultra 2.0I', 'os/exceptasm.s'),
    'c830daccbfdb0ffa3dd1861154b3cc9d6e5940ae67f14f1443acb092f5c05d53':
        ('__osEnqueueThread', 76, 'libultra 2.0I debug library', 'os/exceptasm.s'),
    '974544a72c7c790c5bf1d0ac8efb349bc7c0a15650913c5f22f6c5ebefff225f':
        ('__osGetCause', 12, 'libultra 2.0I–2.0L', 'os/getcause.s'),
    '342c77126d95ebac093fdedad6833d9719721ba7ff39aa95c101e925de397c51':
        ('__osGetCompare', 12, 'libultra 2.0I–2.0L', 'os/getcompare.s'),
    '65d36b16835cf1085b63bae825f4a76f7ea31d18275ab853984187fc29778e5b':
        ('__osGetConfig', 12, 'libultra 2.0I–2.0L', 'os/getconfig.s'),
    'c06660694acce58d87bf63ec39c82802b9c9336ecb797f82009a8d8db4bca64f':
        ('__osGetFpcCsr', 12, 'libultra 2.0I–2.0L', 'os/getfpccsr.s'),
    '2ea7fc1f266eee7e59be3d8ffee2aebda5c74a4e6765e268c725a9a6a9f50ddf':
        ('__osGetSR', 12, 'libultra 2.0I–2.0L', 'os/getsr.s'),
    '6b3d8aaa13c1d3e6287e1b21dd5311398940ce04d8ef634e9eb8f818cf34e2e9':
        ('__osGetTLBASID', 12, 'libultra 2.0I–2.0L', 'os/gettlbasid.s'),
    'dc5d1591b68d900403769a8ffa25d5d677a741bda2b8cbeeeed9bce3185106aa':
        ('__osGetTLBHi', 36, 'libultra 2.0I–2.0L', 'os/gettlbhi.s'),
    '1cbb3b68fef5f134d801d2fbe36593c8cfd60d1fe748a8abad982578904150be':
        ('__osGetTLBLo0', 36, 'libultra 2.0I–2.0L', 'os/gettlblo0.s'),
    '9946b1d964027a4410a40a1b68378d73138ea116dfbe583b534b7ecf205fb867':
        ('__osGetTLBLo1', 36, 'libultra 2.0I–2.0L', 'os/gettlblo1.s'),
    'aa05adc4fb512b806fcf67ab91b9ad844a4050dfe700066391b5584fe15a2d98':
        ('__osGetTLBPageMask', 36, 'libultra 2.0I–2.0L', 'os/gettlbpagemask.s'),
    'a1520beae79c18daf4cdca5579b995fba56260e234684862db06c71fd4f68a6a':
        ('__osGetWatchLo', 12, 'libultra 2.0K–2.0L', 'os/getwatchlo.s'),
    'cb7889136714f88c7e6faba8b9c7d70495134cfa2f8f0edb98482d35cf973898':
        ('__osPopThread', 24, 'libultra 2.0I debug library', 'os/exceptasm.s'),
    '12b6f5b60134817aeedf2b981de4b804b3c6fa887e20414084efbbacb88b0052':
        ('__osProbeTLB', 184, 'libultra 2.0I–2.0L', 'os/probetlb.s'),
    'b6f58a0412d63a52440645623c5a49320459a39befc7ea4359a374051eceb105':
        ('__osRestoreInt', 28, 'libultra 2.0I–2.0L', 'os/interrupt.s'),
    '65b1fd11f742080022b6c80ad8a186368a39dfe694d15c89dcaa68d6e5a2cf40':
        ('__osSetCause', 12, 'libultra 2.0I–2.0L', 'os/setcause.s'),
    '36d5b735ab25f9bb478700d6bf34d5e54555d915b3f13a889e07eade088947cd':
        ('__osSetCompare', 12, 'libultra 2.0I–2.0L', 'os/setcompare.s'),
    '702d110d5a68b7d680cf2963514ed5a1d76b0d8a6f9a8c2a5a5d0ecb24aeee8e':
        ('__osSetConfig', 12, 'libultra 2.0I–2.0L', 'os/setconfig.s'),
    '5967f733518c97f6c7f0de39c398d09e4b2740dcbbbbb8296d45b76838fe5278':
        ('__osSetCount', 12, 'libultra 2.0I–2.0L', 'os/setcount.s'),
    '1b1c2a5e117a433a988d960120310c5634987537358a5b13b49fddcee5d2dac4':
        ('__osSetFpcCsr', 16, 'libultra 2.0I–2.0L', 'os/setfpccsr.s'),
    '8c9798aafb630c54a679991a2a686c3379687a94f8b3c8c14e5eafe1cd8cb961':
        ('__osSetSR', 16, 'libultra 2.0I–2.0L', 'os/setsr.s'),
    '57a60924b5ab6bcac4231c2f07f0716ee5d63495724303dbfa626ca0f4112708':
        ('__osSetWatchLo', 16, 'libultra 2.0K–2.0L', 'os/setwatchlo.s'),
    'a3fae8e4545aeafe4a37df7262f5a54e14a1f621e7a9b895c38c69cf5ff4774b':
        ('__rmonIdleRCP', 68, 'libultra 2.0I', 'rmon/rmonrcp.s'),
    'da19e19ad4a1b3d122186bcfc6cb3d550bfb45a062788a728e2a056b3a6f93ec':
        ('__rmonIdleRCP', 68, 'libultra 2.0I–2.0L', 'rmon/rmonrcp.s'),
    '513e8f86e4cae04589afadb28155ab85fa7c6da3440aeca9d4410f4cbcce9a8a':
        ('__rmonRCPrunning', 36, 'libultra 2.0I', 'rmon/rmonrcp.s'),
    '6bf05e6a0587db019f5d8bb53875c4b8ed098ee625244888584e6242955c3ef9':
        ('__rmonRCPrunning', 36, 'libultra 2.0I debug library', 'rmon/rmonrcp.s'),
    'f729409b36649c2f1398287774df41ce7c68995a09589fdceb44c3e58895cbaa':
        ('__rmonRCPrunning', 36, 'libultra 2.0J–2.0L', 'rmon/rmonrcp.s'),
    '4636b72748d8c4f6efbb8b1049117b61557b524c8f8a3725a71595abe75acc51':
        ('__rmonRunRCP', 36, 'libultra 2.0I–2.0L', 'rmon/rmonrcp.s'),
    'f45ca32c82a60b1a8aaaf5cdec98db45fff60bf2ec232c30d25bc7d37dcd505c':
        ('__rmonRunRCP', 40, 'libultra 2.0I debug library', 'rmon/rmonrcp.s'),
    '866698c4fe169b9170e5adef8cfba9a393b444438a58545551eb550fe3d1d63c':
        ('bcmp', 284, 'libultra 2.0I', 'libc/bcmp.s'),
    '6b2f8f1d342753613e8798f006a00a95c050b9e590f76bf77f54d69da42cccf4':
        ('bcmp', 324, 'libultra 2.0I debug library', 'libc/bcmp.s'),
    'ddb7b30f2e08070a5eaafe4e056d5fe4194f18200acb5d618385e973e698b391':
        ('bzero', 156, 'libultra 2.0I', 'libc/bzero.s'),
    '13111051e91a44b030c85d2ddd49d0e5fa49f9d27cc78bf9e608a862e27c5f1d':
        ('bzero', 184, 'libultra 2.0I debug library', 'libc/bzero.s'),
    '88ecad609341cf1891c7945b0f69ee8d2428c176ac91a15a9129be818fbdcb7a':
        ('guMtxCatF', 220, 'libultra 2.0J', 'mgu/mtxcatf.s'),
    'd42a0df5095f62e3bbae675752ed7416cdbac2773ed08b5cd75819c6ab80a20f':
        ('guMtxCatF', 300, 'libultra 2.0I debug library', 'mgu/mtxcatf.s'),
    '59e6d4522a13e13ead4e9c2f30dbe00221a0546793a4c1a0ca07eabd151c337c':
        ('guMtxIdent', 76, 'libultra 2.0K–2.0L', 'mgu/mtxident.s'),
    '951d6c84c7f95f53592fbcf3b2b6bdbc544c349e485d5a7c5d0e59fd9791d976':
        ('guMtxIdentF', 72, 'libultra 2.0K–2.0L', 'mgu/mtxidentf.s'),
    'ec5bc225baf6c5b426a7a126d1448d04e80da0a67c84200870933a97bed00b10':
        ('guMtxXFMF', 160, 'libultra 2.0I', 'mgu/mtxcatf.s'),
    '5cd5184f47a0e21ddcfcc6317e6269b8f43a8fc0f64e4da50bd82dd9840bd5f2':
        ('guMtxXFMF', 172, 'libultra 2.0J', 'mgu/mtxcatf.s'),
    '991d6bb07ed0353148964fae1d63eca6c802ebbc08fad65a69a56de8c310be33':
        ('guMtxXFMF', 208, 'libultra 2.0I debug library', 'mgu/mtxcatf.s'),
    '29493d6ce21a2433d617c02cbe9628a7f5e1295d359c51abb28a9b86e2d9c4b1':
        ('guMtxXFMF', 316, 'libultra 2.0J debug library', 'mgu/mtxcatf.s'),
    'b4addc6bc43c34e1263952ad1e0811e0229db57fc377cbf3346c2ac65f84c887':
        ('guNormalize', 84, 'libultra 2.0K–2.0L', 'mgu/normalize.s'),
    'c66d20177a3d7663d24050bc0284552550dca41cc1cc00d877d32c3e9d69d4a4':
        ('guScale', 184, 'libultra 2.0K–2.0L', 'mgu/scale.s'),
    '307575fc51ec8366c126191cacb3f4ea45e3b56df159d1987e64e4cab1126564':
        ('guTranslate', 216, 'libultra 2.0K–2.0L', 'mgu/translate.s'),
    '87161b09f136b903fee891575d01cad359ace9547bd84d9135f067277edff1f3':
        ('guTranslateF', 72, 'libultra 2.0K–2.0L', 'mgu/translatef.s'),
    '4b21bfd046b98a3d83e9a862a133d42875bb5cf3145bbc42f98d37e99975c671':
        ('osDelay', 44, 'libultra 2.0I–2.0L', 'os/delay.s'),
    '30227453c76b867de492b81ef449631a9548fecf8b82c86e7b52fac91625d3e2':
        ('osDelay', 48, 'libultra 2.0I debug library', 'os/delay.s'),
    '923a8ad5844532bc55f28624d13567c9795c3f893ebf07353f29b4ea1df8bc84':
        ('osGetCount', 12, 'libultra 2.0I–2.0L', 'os/getcount.s'),
    '5ec5ae8075d92ada2deacd3a17fc22b62304c6e3bfe13cbd95eecb234fe98fff':
        ('osInvalDCache', 176, 'libultra 2.0I debug library', 'os/invaldcache.s'),
    'c1570ebb6df094ed11eb1f796d57c456c51d3502452578b61343bdf9ed4c5e59':
        ('osInvalICache', 116, 'libultra 2.0I debug library', 'os/invalicache.s'),
    '16d5c2d13e74eb08ce688fedc60a028b364541b52c37cbd9b45f8743705047e4':
        ('osMapTLB', 180, 'libultra 2.0I–2.0L', 'os/maptlb.s'),
    'faf6c1366ff17c109e9fde902f3c1520ad4afa1d1c95ec9819b045cf211ddd60':
        ('osMapTLBRdb', 88, 'libultra 2.0I–2.0L', 'os/maptlbrdb.s'),
    '54ba683355d11eb3d984a5ff9c560dc91de17c463c8355acb7612021c69bbf24':
        ('osSetTLBASID', 12, 'libultra 2.0I–2.0L', 'os/settlbasid.s'),
    'c037e600630f331429105fd34ad990c5a2eded4ef0077f698f7c34650bb4d3dd':
        ('osUnmapTLB', 60, 'libultra 2.0I–2.0L', 'os/unmaptlb.s'),
    '768bdca0d47853c4ad2219d3a7ce67beb987cae59401d5a92eedfb9c4612f23b':
        ('osUnmapTLBAll', 68, 'libultra 2.0I–2.0L', 'os/unmaptlball.s'),
    '998df2b024334c9bd4f11f2da7a3c96bfaffc1fec06bac31766d83cbd8b463d0':
        ('osWritebackDCache', 116, 'libultra 2.0I debug library', 'os/writebackdcache.s'),
    'd3ee816e0177eb33107228096347c8cbb1f85472af6f347933eb3ffa5441c410':
        ('sqrtf', 12, 'libultra 2.0I debug library', 'gu/sqrtf.s'),
}
# The rebuild corpus hashes relocation-free functions as SHA-1(body + b"[]").
# The SDK's libultra builds use MGU assembly; libgultra builds use C and keep
# their existing library detection and port source path.
_MTX_CAT_ASM = {
    (240, '3e0621ac1ef55cdf00a2e71debe31eb35e285628'): 'libultra 2.0K–2.0L',
    (360, 'c08cde806b54a974c79759eddf10ab042ba2de05'): 'libultra 2.0I–2.0J',
}
# Library C code that has no relocation, so its bytes are the same wherever a game links it: read from the SDK's own
# libultra archives (2.0I to 2.0L, release, debug and ROM libraries) like the assembly above, from the objects that
# are C (the 64-bit helpers, the audio synthesizer's small functions, the thread queues, the Controller Pak checksums).
# A body with two names is left out (one set of bytes, no identity). These are set aside by the project's library
# setting (app/sdk_assembly.SETTING), not as assembly, and only complete functions of 32 bytes or more are listed.
KNOWN_C = {
    '09cd19f39dfa44d2cc1c62af44818942f04faf68e0010bf786d3d8010298ec1b': ('__osContAddressCrc', 72, 'libultra 2.0H'),
    'bdb9601c6627893c6f1ed3440ea02c5a144e0014a2795bf5ac0cf1dcb8ccab50': ('__osContDataCrc', 100, 'libultra 2.0H'),
    '2dc9e8815e9fbebc815aeeaaecfcb3ac60eaa2210b6cdb9272a60d79cb079687': ('__osSumcalc', 56, 'libultra 2.0H'),
    '3d8343ee78e6492daa82964e83214e272fc41c09d9ff3b267d5f1848b52fdb36': ('guMtxL2F', 168, 'libultra 2.0H'),
    '4ce45bb23486ed6bbeade52c331006c95d1e12a033802c0abdb2fc0a545d3578': ('osEPiRawReadIo', 76, 'libultra 2.0H'),
    '84459a61b2a10b249ccf3175e8a303b1a05c1d947f2198cb78cdfff7460b2f73': ('osEPiRawWriteIo', 72, 'libultra 2.0H'),
    '0a4ddf0bd0510f0fda167dde0f5c7901e48a81921741545ffc3264545e37694d': ('MonitorInitBreak', 32, 'libultra 2.0K–2.0L'),
    '517472cee59ad4f2f0a0b304203d93633d43366752bc4b62deeb856d6a908b81': ('__alCSeqNextDelta', 220, 'libultra 2.0I debug library'),
    '5874b1c73b9c4c80cddaae1b3b16a49ff5735b80172fd5c8d336a65cb579b426': ('__alCSeqNextDelta', 360, 'libultra 2.0I'),
    '5a4ee00bd7126857c834e48b4962e67f48e1a583194223198c3253323237a24e': ('__checkHardware_kmc', 60, 'libultra 2.0K–2.0L'),
    '60b098ab474fbb30336ffac8e25e9f5cfa0d365d25e381e150be45c16081e6ed': ('__checkHardware_msp', 48, 'libultra 2.0K–2.0L'),
    '0653c6e362515606ca1b4f31b87233de71ed4a70a6d6d54c17bac61f80d4545a': ('__d_to_ll', 44, 'libultra 2.0I debug library'),
    '070d124a24dcf02ca6e0aaf90f65a6c1708803a47717dd6d7c794a063eb8091f': ('__f_to_ll', 44, 'libultra 2.0I debug library'),
    'a36e8686ab86b0e38137c218dc86b856eec108a48afb506f1383373063b9a23a': ('__initChanState', 180, 'libultra 2.0J–2.0L'),
    'ccb371da2012c1d34462a426245610cc197858a84dcb0064a26077bf931d082c': ('__ll_bit_insert', 188, 'libultra 2.0I'),
    '51bf19c6155287272bae66b4d5dad2ebc70a0784546a0374ef166d4564b28a9d': ('__ll_bit_insert', 216, 'libultra 2.0I debug library'),
    '8522fcd27821f2d5268e6a264f568055a735078e4fc8f2b68d8f43f0d62892b5': ('__ll_bit_insert', 368, 'libultra 2.0J–2.0L'),
    'ad1ec2c0626553f19a061562ca4e6a401634710b08c69faef884c6784be1d129': ('__ll_div', 92, 'libultra 2.0I'),
    '9aaf93ea08be79d34ff408a0d8c02924da131b088b1d6d85196f4780c040ad0d': ('__ll_div', 108, 'libultra 2.0I debug library'),
    'f77aab92ad5cc45115c9fc3fcd0c3bd0d88c2404de136dae2c80660b21302d7b': ('__ll_lshift', 44, 'libultra 2.0I'),
    '573dbd9999b3259f250b54bbc587d378112eab5d96f76d0deea89c729a4ec28e': ('__ll_lshift', 56, 'libultra 2.0J–2.0L'),
    '39550daaeba88a1f58d793e04fdb73a24e30d29139beeb9611039358fbb47030': ('__ll_lshift', 60, 'libultra 2.0I debug library'),
    'b9073be5a5aff0b39a538e2ebe2581b5b909de1093c324bf7db16b412a0cbbef': ('__ll_mod', 156, 'libultra 2.0I'),
    '4b1f45ccf719530fc067207fc6b344089c1e873391abe98adfb073f69a6018c7': ('__ll_mod', 180, 'libultra 2.0I debug library'),
    'deb3d19732cb6f876f0c3c367210dd4e094a4eee114b0e409a307c64fc292596': ('__ll_mul', 48, 'libultra 2.0I'),
    '18cc46779268f20c742f0e459904f3f11a0da9d1a35bff62982f67e8f2f06fdf': ('__ll_mul', 64, 'libultra 2.0J–2.0L'),
    'ab80256c084afa4963b94443206d73769a06c7c340a39b1fe7ed92b8eb25d7ba': ('__ll_mul', 64, 'libultra 2.0I debug library'),
    'f947c8dc8dcd2bc0b62bf25945bc1815051fade3c2f24f7454b9420b94c699f6': ('__ll_rshift', 44, 'libultra 2.0I'),
    '641faaf9298091ddee8c24d8192b9d4506ae12e061955325893533d738c4b66b': ('__ll_rshift', 56, 'libultra 2.0J–2.0L'),
    '8f04bb74d396c656d8dad55b0881645e91932466b360e4153846f31163e306b6': ('__ll_rshift', 60, 'libultra 2.0I debug library'),
    '69c1936fc79c3b0f955a79ff39c9317dcdfb826a6d078f2905b8b6e20cc798f5': ('__ll_to_d', 40, 'libultra 2.0I debug library'),
    'b3c25972a946eaef2ae0f0ab276ed6e2c61c45111cace8e5dc2507f419b7eeee': ('__ll_to_f', 40, 'libultra 2.0I debug library'),
    '88edbc8ac789228b41b8fb9309af58c5f51773d621f1635559df8b7666f22a05': ('__lookupSoundQuick', 212, 'libultra 2.0J–2.0L'),
    'ba1f9e52def46a38ab8c6e8fb6bc7d512065b98f30c9afd0ad090aeba176498d': ('__lookupSoundQuick', 252, 'libultra 2.0I'),
    'd04e6242e075d73155fd20bf57a80684e6662dd28bacfd0d505d978878410029': ('__lookupVoice', 84, 'libultra 2.0J–2.0L'),
    '051f9a5b96269a7cfe6cfab18c3b460d2b4fc7238d8ddc1b267a34e1d7eda29a': ('__lookupVoice', 116, 'libultra 2.0I'),
    '3e07a94f3417e612ecb8853d6401ad067828eae65d45cb8d86577b82b73fc42c': ('__lookupVoice', 136, 'libultra 2.0I debug library'),
    'f3074f5c3a0cb642085679e2022878eb74a5eb8081db2c1e7155750fd77e9b06': ('__mapVoice', 112, 'libultra 2.0I'),
    '47824ca71cbe4df0cb2c88fe91c283735e5cf9c33881d6fccaca37f29451b818': ('__mapVoice', 156, 'libultra 2.0I debug library'),
    'aa2704a888a1387f8c699d85e491fdb1671d393d008fcf920e77b7641292a15a': ('__nextSampleTime', 84, 'libultra 2.0J–2.0L'),
    '94d8674f022e85b272c7575b4a263fd32588717bb737e3a99536d3c589db394f': ('__osAiDeviceBusy', 48, 'libultra 2.0I'),
    '69b5cece108f9768c389c885b7b15f80906cf5d40844076a91a50c4592d0f750': ('__osAiDeviceBusy', 68, 'libultra 2.0I debug library'),
    '319998fd133973f4cd3526a0c0980d764034ad6ae652895f7b18bac0f4b89d93': ('__osContAddressCrc', 176, 'libultra 2.0I'),
    '44a339e76b3c6013a6d937066a95fff14a89fade93711f694238413de19a6532': ('__osContAddressCrc', 192, 'libultra 2.0I debug library'),
    'bbfa6798a2048b18ffe28c5d1b22a152d7274c197ac4e90fff8f7aed13ac3fdf': ('__osContDataCrc', 208, 'libultra 2.0I'),
    'aca013c3a1cb2099fec8a0b1798311854c3fe757e4fbd8fcb7bff2858998337f': ('__osContDataCrc', 224, 'libultra 2.0I debug library'),
    '6950f1d67e7328147547bdd321085cf5f0e2327d377a5d6ae2dfca3d5a3c6708': ('__osDequeueThread', 64, 'libultra 2.0I'),
    '8f9c927edf359007bb87d80eecc83e9090a9fd6d026dba5b21d750b6849d28cd': ('__osDequeueThread', 80, 'libultra 2.0I debug library'),
    'b2aa27efaf3e1cfff0086223c85ce576ad80c32f8d48f794f877ba090a2ee729': ('__osDpDeviceBusy', 44, 'libultra 2.0I'),
    '986e038314adc049c152a8550c03fa79ee1c704a36cdad677e78e84e8ecca857': ('__osDpDeviceBusy', 64, 'libultra 2.0I debug library'),
    '1aae3be1a18819e283af54c578b058f9fd4c76f6b687881d6cd5640e519bf95d': ('__osGIORawInterrupt', 100, 'libultra 2.0I'),
    '2f0a8bcddc3f38ae06f865daecc68cd044929e18d1b06f2003d9b35f7df20d5d': ('__osGIORawInterrupt', 108, 'libultra 2.0J–2.0L'),
    '45ebebdb543cab03f29ce2e0ebe553f4e32a04022eb4426aba8c201c2b66c881': ('__osGIORawInterrupt', 108, 'libultra 2.0I debug library'),
    'bdd7a8bcefa6ed2359a5c561d67b1c4f4df8cb972924b20c5af0bcf607df8776': ('__osIdCheckSum', 68, 'libultra 2.0J–2.0L'),
    '6af53aa37bbc984589264392e1d170b4b923726c4736d69f61867edfbf93fd92': ('__osIdCheckSum', 104, 'libultra 2.0I'),
    'ccf2550fa2a9e8259517df429eb2e55b0ac90d6a9585323762f4de9adebd4469': ('__osIdCheckSum', 120, 'libultra 2.0I debug library'),
    '7784ae5360bb5520c08d44990eba7fb00e764ecaef78adda794552704cd77c0f': ('__osPfsReleasePages', 104, 'libultra 2.0J–2.0L'),
    '357d124077afbccbbe838ac39e3b8418a598c53ebb980bc867a21a111d598f52': ('__osPiDeviceBusy', 44, 'libultra 2.0I'),
    '59ae10a76231e623a034d283c7ca80d30b6d6def845601c163a35cb81d675bb8': ('__osPiDeviceBusy', 64, 'libultra 2.0I debug library'),
    'c40efb0efd62baebfcb5aa0af3421d1155c9d8e9a20163f10b0c84f61c4ac156': ('__osSiDeviceBusy', 44, 'libultra 2.0I'),
    '4f8f395dcc316ee428eddf35314d0f1b5fa0a952a20d7c6900a2e414764a337b': ('__osSiDeviceBusy', 64, 'libultra 2.0I debug library'),
    '5b8a49766e2e41e50b893cb38c81db6ebaaacf069cefe96035b118b0d9f9c4f0': ('__osSpDeviceBusy', 44, 'libultra 2.0I'),
    '7e0b2ba639f4a98f49c4f591ddc6cbea409f5076e44761e6719bccc6314dd5cb': ('__osSpDeviceBusy', 64, 'libultra 2.0I debug library'),
    'af66ed65d77fde1aae49eedb1374b1c64c2305b9dfb37653f1b09d17adb34bdc': ('__osSpSetPc', 52, 'libultra 2.0I'),
    'f3a4b43935ea3a6925b2a43c4c50a409882c35249d2468cc355d772f388ec560': ('__osSpSetPc', 64, 'libultra 2.0I debug library'),
    '2beec6d2c12280558fdd69e37f5ffedbfe9de106cd68787373383ee44b58a22a': ('__osSpSetStatus', 44, 'libultra 2.0J–2.0L debug library'),
    '6098b7b05dfe7c584c62ae151ce12b7f394ef44bbe16b65a8b4d8ff67fbb2c03': ('__osSumcalc', 52, 'libultra 2.0J–2.0L'),
    'b0bd16a938ace6cc15173eaa521d073515ab4f6f57c999650c91545c6223d96c': ('__osSumcalc', 92, 'libultra 2.0I'),
    'da5dad629d66269fd7183c4dd3de83a592035adeb2ff27ea82881c69f5476628': ('__osSumcalc', 104, 'libultra 2.0I debug library'),
    '2a2e27810063a6e6a8c3c257c9d8bba0520b6850dd4a95ad68bed2a0f75a25f2': ('__resetPerfChanState', 128, 'libultra 2.0I'),
    'd1f5439ca8122299ecd9c31f3d0db255018fe36c8b420ac8464013d245016b59': ('__resetPerfChanState', 128, 'libultra 2.0J–2.0L'),
    'c54e499b09c16248fa80a70418805c9d3a3ee6a8c8feef880c2725becd5104b4': ('__resetPerfChanState', 168, 'libultra 2.0I debug library'),
    '324cbca8b21c332f469ad3f776c65bb43ec2b45ba0f5939727b3f7d97f28e863': ('__resetPerfChanState', 288, 'libultra 2.0J–2.0L debug library'),
    '35161253295776f89a85bf28ecf76a9d9dfa4115050ce34b9d6338890638998c': ('__rmonCopyWords', 48, 'libultra 2.0J–2.0L'),
    '7ddd0b3da158e67493f9c1d356c07777e431c71e3510a7204774494105b9bfc4': ('__rmonCopyWords', 52, 'libultra 2.0I'),
    '952f9881584803723e4306ef4e3234f96339c8de63dc9865acdd3f7569c01159': ('__rmonCopyWords', 60, 'libultra 2.0I debug library'),
    '3690c10ecaedcc45c7df36169a62f90d6f6b2aa0bc6ac96f6beeb5e537a4a732': ('__rmonGetExceptionStatus', 60, 'libultra 2.0J–2.0L'),
    '60798b272356f95c93237cc43f1c4a80f1f08c394df37cfa0584eea9aa384f82': ('__rmonGetExceptionStatus', 64, 'libultra 2.0I'),
    '12c7f590e5539d24b4b50eb617ca58da12e8bdf87c44d7d893de19ac6b001227': ('__rmonGetExceptionStatus', 76, 'libultra 2.0I debug library'),
    '4e2ab8a872fa11058c727fce34cf2c35bcf6e5429fb29f7fb8498977b705bc31': ('__rmonGetExceptionStatus', 128, 'libultra 2.0J–2.0L debug library'),
    'b56088ccd650f1b0136bc22b4e6581fbc5fdf43188d3369821997664ef9986ed': ('__rmonMemcpy', 48, 'libultra 2.0J–2.0L'),
    'bbd702cec01d32214140245d9768267d92ce5731eba80af98e7d8ec124cad269': ('__rmonMemcpy', 52, 'libultra 2.0I'),
    '37c4234dbea9534e8aa770299c28bd84f43e41b6ff8f75b95fd6b66d851ddd7b': ('__rmonMemcpy', 60, 'libultra 2.0I debug library'),
    '642f1f1801e8779fa2490c27ff2e5dd64356945cd6596d373e97fbfbcca2dab1': ('__rmonPanic', 32, 'libultra 2.0I'),
    '27c1632b1920c33d3c959f6839287c7685c128da0176c7c8850e0139add79e05': ('__setInstChanState', 84, 'libultra 2.0J–2.0L'),
    'be8cc063336432a5f2d7e85ad74d630efa0689945416dc2d81dee845d07c552f': ('__setInstChanState', 84, 'libultra 2.0I'),
    '98f1440cc2edf3abba07be68227736f564156cb844dec60e414ab628044bf301': ('__setInstChanState', 112, 'libultra 2.0I debug library'),
    '23ed9277ae945b4d750f64146c8f3c663d21cbde3767bfbebaf3382dccf48f88': ('__setInstChanState', 216, 'libultra 2.0J–2.0L debug library'),
    'd23ded15fa515f8703c3978be0059ae854f70f07e9d9554b76ee6aaeb587419b': ('__ull_div', 60, 'libultra 2.0I'),
    '99060ee6157064cf14b935e736d187c2b7b26edfc592bdc02719f4236f13c7bc': ('__ull_div', 76, 'libultra 2.0I debug library'),
    'b5999d5b01a6650ebf056b9106a465b2fc5dd3de630e1b20ffed19a52458ec30': ('__ull_divremi', 96, 'libultra 2.0I'),
    '813b076d82548d0a4f1a7872f6afc000c4088cc81cb89b4fabf78b7345ec830b': ('__ull_divremi', 112, 'libultra 2.0I debug library'),
    '675cfd7ae5bb2dead54ece083b62caea4b89a02698826dc4ae301701effb8bdd': ('__ull_rshift', 44, 'libultra 2.0I'),
    '7415ef90658d49e9070bbd745ddc5113cbfd1f195efb00e4dde325887064ffa1': ('__ull_rshift', 56, 'libultra 2.0J–2.0L'),
    '49029c3d4e46cc0b285cba70c513308109c23583b552ca8eef07bbe4f678c53f': ('__ull_rshift', 60, 'libultra 2.0I debug library'),
    '2bf204310a357d143eb371fdd8ccdae79321899f20602753fb72e84f0f3a4962': ('__ull_to_d', 52, 'libultra 2.0I'),
    '91bd820d267e942178bf2f501ab92ba37832ce4a91c434a4e05f48221d965044': ('__ull_to_d', 68, 'libultra 2.0I debug library'),
    'c538de7c8951d186372586940e88b73e634f793065da5d4cf4a444b3b0969bb6': ('__ull_to_f', 48, 'libultra 2.0I'),
    'f1d582969c1ddfc9fd360ad8b399ab38d977de2af3d0128fe2e6454ec9e5b2f0': ('__ull_to_f', 64, 'libultra 2.0I debug library'),
    'ef44edcc970ca54a83cccb6b354c86e1774fd13fb02d1f9b5cbc8d288899929e': ('__unmapVoice', 112, 'libultra 2.0I'),
    'f9b7611ee2f10d818e275231bcaa00e974ff77365d3c2e714d0a348b8a1b199e': ('__vsDelta', 36, 'libultra 2.0I'),
    'e00492d14efc4f37362bf98a183fabf0b01d29a010eb243f3f60165c7f5aaf84': ('__vsDelta', 68, 'libultra 2.0I debug library'),
    '6690b48478cda1859b374cb6b42508900de6a7799b4f2bd9692af0dba932f142': ('__vsPan', 68, 'libultra 2.0J–2.0L'),
    '234ad7488e172a253084f9f9da6555d7b3a33c10b830d1910c90656233cdece4': ('__vsPan', 72, 'libultra 2.0I'),
    '065e810eed93f4c1763af51e34c858ea4d487489b550a30bb14be0accbb2ecf5': ('__vsPan', 124, 'libultra 2.0I debug library'),
    'e46f97a57195935b3a17b3992867ae0ebce7ad60cbc3b95e38862266d95b5c9f': ('__vsVol', 112, 'libultra 2.0J–2.0L'),
    '82d961d9cda468d9d7c59c5a0c4a007282c6ea2ef55777dae23a98a19fe17fa0': ('__vsVol', 136, 'libultra 2.0I'),
    'fec7d136add921ff2ea48a32407a4cff0fe69901d85b055b2bfb7eac79cad558': ('__vsVol', 172, 'libultra 2.0I debug library'),
    '39577c7b00ce342eb29453fe70eab44baee8cb02bbe401b8a829b97b18d4e907': ('_doModFunc', 168, 'libultra 2.0I'),
    '24306e224ace1847c9e5387fbfd58b3d4d19c10548c4894e480cc9fbf6d341f2': ('_doModFunc', 236, 'libultra 2.0I debug library'),
    '4498bb6a00d9565ff10d6ae1b3fb5fc3a0aa745c4e1ed73a7e82659c640f9ba7': ('_frexpf', 236, 'libultra 2.0I'),
    'a4583cf869db2b08c570364c77fedf78e562598817a5a5727c2af967b7ab0410': ('_frexpf', 344, 'libultra 2.0I debug library'),
    '412c93b8610646e94b70c4e07ba0237df0f6f254d35b22609d6e0db9cc9d189b': ('_ldexpf', 36, 'libultra 2.0J–2.0L'),
    'd4f85e1c13e30e6ebf5484e05ed872373e395dbd9be5719d923807ecddd6eb6e': ('_ldexpf', 40, 'libultra 2.0I'),
    'aacdbdf0d99024fe8ad525e334bf23f0dde9971af16f35ae235979b94bc485c1': ('_ldexpf', 72, 'libultra 2.0I debug library'),
    'a10f5fa255dae9c413fc9da039e005a3f3b0dfd27a79d7547888d7b7454cc696': ('alAuxBusPull', 216, 'libultra 2.0I'),
    '88b1c98835eb1bb6b9ba032115ca1c6b2c70730e7ff47f1226db0b064b73b130': ('alAuxBusPull', 220, 'libultra 2.0J–2.0L'),
    '78537fef2bbf447bf6518d0095779ef0ccb33bbd58a8389b0fc25220e05f5eb3': ('alAuxBusPull', 288, 'libultra 2.0I debug library'),
    '33360998ee6552f59865311ec0bf2abb75a0ba79e289524c1312e726804b74b7': ('alCSPGetTempo', 44, 'libultra 2.0J–2.0L'),
    '3061f1240ecd6dced3888944cf12c049fb62f27fd466b9eae0096887af531f1d': ('alCSPGetTempo', 64, 'libultra 2.0I'),
    'dd2e321d3cf24441376216318344420672e66e1387ba5ced405714ad581f7221': ('alCSPGetTempo', 76, 'libultra 2.0I debug library'),
    '4daa974e1171180c60f2f699bb955c52c89f932560ee4525c53d88bad58ae4e8': ('alCSeqGetLoc', 116, 'libultra 2.0J–2.0L'),
    '0f58d632ab9ecae7b1b18deb6adfccbfa3a3b33683f0ccf52efa0fee5419086b': ('alCSeqGetLoc', 160, 'libultra 2.0I'),
    '74ed7f43cbd62307c81022337b7aafa5c0196f2c94ac86490a43e3ddd1a894c7': ('alCSeqGetLoc', 180, 'libultra 2.0I debug library'),
    'fdbf077b3315ea17291ee987603253e1a68a0f1878c9163ff4c9d42e37617f27': ('alCSeqSetLoc', 116, 'libultra 2.0J–2.0L'),
    '61aca52e904fedacb7ac6cf1424fcdcef87fa5bcdd8477106f078d8ee9d3074b': ('alCSeqSetLoc', 160, 'libultra 2.0I'),
    '3158e2a00ee43646001ba7619070e7637a2a64a8ead072ccf985ac0a7f1f0cd4': ('alCSeqSetLoc', 180, 'libultra 2.0I debug library'),
    'd811db31e000330b00e35d42a248823e8fb5685dd55afd818cf5f29c8b3f6a1f': ('alCopy', 52, 'libultra 2.0J–2.0L'),
    '4c4627672b86607ff18a4411225e460d542db38f822fc2825fd062ff35521b35': ('alCopy', 96, 'libultra 2.0I debug library'),
    'b18b79e5540ae25ee1efb59a8143de0161383aa07e08df8fcf25540135ec634b': ('alCopy', 116, 'libultra 2.0I'),
    '58df1db0bbf3a29edda51dc0e17fc4104dd25b60acd33e74d56017ee123830e0': ('alEnvmixerParam', 224, 'libultra 2.0I'),
    'da58313619bed0a23f4eacaa45bae5afae00ac1702170fce0edfe8b881068ce0': ('alEnvmixerParam', 428, 'libultra 2.0I debug library'),
    '005a4851b52793a3afcf15f1f4d74fc75c0d6dfb61fb561c2fce8a5642cacd80': ('alFilterNew', 40, 'libultra 2.0I debug library'),
    '9c6a81211508a1379399afe16c82eec2c35f03cd1118fdca82b70d5fb1413792': ('alFilterNew', 104, 'libultra 2.0J–2.0L debug library'),
    '5005fcbaa1889e17b5c719878b0acf52003b5d6f2eaa1842f728ac4706685a41': ('alFxParam', 52, 'libultra 2.0I debug library'),
    '20bb9d32cbacb4238d3cb992dd313be563e371402392074a95b4942e12b9fa4c': ('alHeapDBAlloc', 72, 'libultra 2.0J–2.0L'),
    '56508dc8caf9a7d66b0e49d0be08a956a2708e57e0531c2841f96aff024f75e9': ('alHeapDBAlloc', 84, 'libultra 2.0I'),
    'aed6cfe37d43f076f275814ba5f99486aafa8f3de789d528c8bbec2766a438af': ('alHeapInit', 52, 'libultra 2.0I'),
    'dd9cd74005505fa5741c62d2913829d18c74594838a5644139e8693d7da6584c': ('alHeapInit', 88, 'libultra 2.0I debug library'),
    '8e1e0e1b596c1ad95da3c64f594dfdc9bd751e01410837e8ccf40ee45be06116': ('alLink', 32, 'libultra 2.0J–2.0L'),
    'bc5376a9c0214c2355430e588d8b8846c00202a85ac01f8f183f4b845820d31a': ('alLink', 36, 'libultra 2.0I'),
    '29c481b43360a8b7a3d78a8dce238aca06ac0499f2d4dec944c8b3ba20c06300': ('alLink', 52, 'libultra 2.0I debug library'),
    'e83035bee910d27443f1d6cd9d113ea61a5d9db76f3e18edb0db3a8afe99845d': ('alLink', 108, 'libultra 2.0J–2.0L debug library'),
    '3cb1a61f1d48a650158bcaa85ff611be0e2a7edc5f62c96e99b31833d6737b74': ('alMainBusPull', 288, 'libultra 2.0I'),
    '621560773504aa5c6d224ffa8fefb0dc1d41bf13c5a11abd08d433a6be7ef574': ('alMainBusPull', 320, 'libultra 2.0J–2.0L'),
    'f44832820e66acec17bfb41283aaca87d74591e13b1077cc7fe4b3ac1220aa71': ('alMainBusPull', 432, 'libultra 2.0I debug library'),
    '0d1accfdf71958b853216588b90f77fd4f66082d89ce5117feea67da6190cd56': ('alRaw16Pull', 940, 'libultra 2.0I'),
    '25bbcaee0ee23e8dac3572556469762450b7a2cb3ab2a0373c73835ac0742b62': ('alRaw16Pull', 1596, 'libultra 2.0I debug library'),
    'b7b30fe537667c0e5547257b445cba9e23aac12a7c4b9e3c2b0047c5bb57fb71': ('alSaveParam', 52, 'libultra 2.0I'),
    '1a36d0bfdc72ac590c1fad04812c3ef13d063aa8ba1c74f6fcec995a98520881': ('alSaveParam', 120, 'libultra 2.0I debug library'),
    '8e29dd8a34b471ef867750b988e1a6fe0673f8412ec86bfcecb3078970c19f8c': ('alSavePull', 140, 'libultra 2.0I'),
    'b230ad8d2575b3e1954b8877855843c91fbc4e439c9c27170c2f08cb81117dcc': ('alSavePull', 168, 'libultra 2.0J–2.0L'),
    '5a4abe723b1ade23a5042f2245782f5118dc69c7316d19fd88a79d88424801ee': ('alSeqFileNew', 64, 'libultra 2.0I'),
    'b6084831ded7cdb153f2167f21e7a6f115a3fcb1e51562b928c382dcadfb4b3c': ('alSeqFileNew', 64, 'libultra 2.0J–2.0L'),
    'e26588534344a2d93884a31d2726b673eccced94af6c26a2c7411d45ed078b3b': ('alSeqFileNew', 100, 'libultra 2.0I debug library'),
    '97a2d1f2b7671136d4706b6835ec03320bbe6200d9f4cba865e065ac533e36b1': ('alSeqGetLoc', 40, 'libultra 2.0I debug library'),
    '6a0745c19bfedf39fc633b9b75b3f4ad9b01b5b6ed9e0d7c7af1374811e86a99': ('alSeqGetLoc', 84, 'libultra 2.0J–2.0L debug library'),
    '03889e9eaeb7212454f2497c266960510a49ace4756fdb7912b804aaf6c446f3': ('alSeqSetLoc', 40, 'libultra 2.0I debug library'),
    '33f7574f0f1a1f1d5675e749f88e7e019502b9bb276fab461f795958484f918d': ('alSeqSetLoc', 84, 'libultra 2.0J–2.0L debug library'),
    '5f9b382b0ff26b94a5f9736a5e722aff5f51bbe040acb12c18a95c84b63ab391': ('alSeqpGetTempo', 44, 'libultra 2.0J–2.0L'),
    'c9bff36caca18b28f804b97041386b0ea0f8840f0e260cd061ac90975c5f3a9a': ('alSeqpGetTempo', 64, 'libultra 2.0I'),
    '282e4d5f48f9a9941a8eaa420daac7db476fcb79e25c1d4e150fedf4da429169': ('alSeqpGetTempo', 76, 'libultra 2.0I debug library'),
    'ee1fc423cf92f9d69a11131b6ea94e36a371cb956e59f0fec1c2d04b0ac34b74': ('alSndpAllocate', 156, 'libultra 2.0I'),
    'eeb38fd6d14b74c13df9673849ef969ee9c771fc72daaae61d077590c7458d2c': ('alSndpAllocate', 368, 'libultra 2.0I debug library'),
    'd3c9872a26c1362cce2b22dd32e55af6ed4a99fb4d053653553631f7dcfc492b': ('alSndpDeallocate', 68, 'libultra 2.0J–2.0L'),
    '5e2e128d14d389e20090df1065278ba88d9b38a7c169f46f601a3ee1376fde81': ('alSndpDeallocate', 80, 'libultra 2.0I'),
    'c3174ff5c11ed18536a518e6894e9d5bca5e6dfbd35d8bca3abc737858bddd39': ('alSndpGetState', 32, 'libultra 2.0J–2.0L'),
    'ea101ecd19842cbb6d25f5d4cc45a50aa1dc1fc5ca5d0e0f2b5d1cc6679ecc9a': ('alSndpGetState', 32, 'libultra 2.0I'),
    'd56d33ddee56bc35680de7de7399fc127eef2a4e8d24ec0af1b712ab663296c8': ('alSndpGetState', 60, 'libultra 2.0I debug library'),
    'e0c6bfb449e42d31201f6b1b0b8d6435dacd332a492f976dd4c7e0184a936285': ('alSndpSetPriority', 40, 'libultra 2.0J–2.0L'),
    'bf34edda091b3e56a977525b000ddac68cd0b20540f81311a6d309524ffcdc24': ('alSndpSetPriority', 48, 'libultra 2.0I'),
    '4ec04b7456d7ef9fc93fc0ecc8804e5c1eb319d31c24e98e1adf0cba765d3fb7': ('alSynDelete', 40, 'libultra 2.0J–2.0L debug library'),
    '31a6d80a9d62fda84c2f316079918d8290983eb089dee8913ad727e646e1dbf9': ('alSynFreeFX', 36, 'libultra 2.0J–2.0L debug library'),
    '96d2aa1d8e2be42980279c8cdfae23b562b978061397f2d12ecba7aeaa51e31a': ('alSynGetFXRef', 108, 'libultra 2.0J–2.0L'),
    'e4fefda94a8109d5750f3ced4908f9179e8ee6789c99d6125e537c3024217ecd': ('alSynGetFXRef', 120, 'libultra 2.0I'),
    '88a1a8fd439b3c1083357dad67c57e59b2d093ae08dbda575132d478cf469c40': ('alSynGetFXRef', 156, 'libultra 2.0I debug library'),
    'b4f5e014c13d26b145101a3d880646d2f360d8b13a020753f0843d0a8115064e': ('alSynSetFXParam', 44, 'libultra 2.0J–2.0L'),
    '69d69e3ab813ba0e098279798573ceaf947832a00a1e904effed41ce45d770c4': ('alSynSetFXParam', 64, 'libultra 2.0I'),
    'edbd84ddce881cd4410f7e93fca426f1623e8009ed53d8d8bfba117293bc39af': ('alSynSetFXParam', 84, 'libultra 2.0I debug library'),
    '4eff8d8954e2057f22a4cb6bea67e0f68fb6beb2b65b313889fa80578eede9b4': ('alSynSetFXParam', 100, 'libultra 2.0J–2.0L debug library'),
    '002dda3330936b4eff4fb29ac545f6c6cf74b7106c0f3f4661f10e9750fb218b': ('alSynSetPriority', 32, 'libultra 2.0I debug library'),
    '3b95b1842a64037e68e8c21b8c83d2048cf8f6d6a5af6c38357ef4a6d437763b': ('alSynSetPriority', 56, 'libultra 2.0J–2.0L debug library'),
    '2ec55fa0ed8dc9e3afb60ebfc4120974dd453c2892cabe1c447697a1a54e2844': ('alUnlink', 48, 'libultra 2.0J–2.0L'),
    'a6094f1c1ad9362d419c3830ac448e92a6d5d36ba8a476c78bf4723efac94381': ('alUnlink', 48, 'libultra 2.0I'),
    '3f686eea3a5f1c5fd3ddb6e69542c33ea62e70d65fa03632bb4fb7a4c2a4a851': ('alUnlink', 64, 'libultra 2.0I debug library'),
    '9448ae13f3254cfee9cd28b554bd82fabddb9bd8442f93addfef2cb1041bc04e': ('alUnlink', 104, 'libultra 2.0J–2.0L debug library'),
    '9e3f4e0924eadb9ceeda5542b93ef166e3228e572bc744efe909c79ff8cc2bbe': ('guDPLoadTextureTile', 1204, 'libultra 2.0I'),
    '87dc571159685ed17c319fc84b046059f4fee419cbbc14356b716559bec5c812': ('guDPLoadTextureTile', 1496, 'libultra 2.0I debug library'),
    'f6516d35f6a00ee5fe5865cae32fc71e196540fb1ff78b58f72ac2fd075f1433': ('guDPLoadTextureTile_4b', 868, 'libultra 2.0J–2.0L'),
    '80d078bb8a868c620cd219aac5c106b8d286ef8c2a886c66010294e761553576': ('guDPLoadTextureTile_4b', 1004, 'libultra 2.0I'),
    '08050082a52b09793b4d74f528cd5ea564ff3b19a7aec660e9a36c3b00187cc9': ('guDPLoadTextureTile_4b', 1256, 'libultra 2.0I debug library'),
    'a24f7382e3afeec95553a338105f19fb82ff7d8e37f084d7facc43bf9846d0f6': ('guMtxF2L', 152, 'libultra 2.0J'),
    '766ecf1975b0b289ca1034155d694cc04c33d95ea7f69907505b4173ed0ed7d8': ('guMtxF2L', 256, 'libultra 2.0I'),
    'e546d7f75329ad36a66d2d8eca22ff21b33b3e77b37d0b6fced0ec6b1800c03b': ('guMtxF2L', 304, 'libultra 2.0I debug library'),
    'c848c6f0d08939e5bb2fe8b65e0522a4e2fcadeafeb216a620deacd9e9d54a29': ('guMtxIdent', 240, 'libultra 2.0J'),
    '17df6c24f5aea688b6f672ce0da965bb11eed67e4b90fb87a05d5ecee067c51b': ('guMtxIdentF', 80, 'libultra 2.0J'),
    '8325b24de93e8818fe1d58e4d181e272f6bb2ad475863afed99d3a024438b2db': ('guMtxIdentF', 136, 'libultra 2.0I'),
    '77dada5d3941274e813f5bd4151fb3971ce0299db25d10585d1465c5db76f280': ('guMtxIdentF', 156, 'libultra 2.0I debug library'),
    '568c4a4d23a3aaad70e0713ee23ba2173c8fceae8afeb119a73f43805b37b3ec': ('guMtxL2F', 168, 'libultra 2.0J'),
    '0b2bf12a8684f3decf44b0326354901ab06aff35cde6700bb3238ead80cbaf04': ('guMtxL2F', 180, 'libultra 2.0I'),
    '44a512271def95d028e0004544f7f8395481f7078ecb9a6cafe297414db19954': ('guMtxL2F', 312, 'libultra 2.0I debug library'),
    '0577cd8d95870fae2360f96c474468569db335260aebb8d82e0ec59747714125': ('guS2DInitBg', 372, 'libultra 2.0I'),
    '4afc830cc5a626af392cc5931e84b2fd8bd54135d66f720b3aeceff92d2b3555': ('guS2DInitBg', 444, 'libultra 2.0I debug library'),
    'b8af8fa8c1998f82fed4f4541208ca98938fe1e3e34ab91d208b660bb5d04ec3': ('guSprite2DInit', 64, 'libultra 2.0J–2.0L'),
    '3099c4cd5c9c0ccef63b74e58cd74e00300517fb73a7f94528f0f2d453ff68d3': ('guSprite2DInit', 68, 'libultra 2.0I'),
    'b7c3fc5b91ed7ec4b242d3d34fda8ec59fb178484a7199d4b5ec03c2311f5194': ('guSprite2DInit', 84, 'libultra 2.0I debug library'),
    '6c0eab0ad0f22d6c233884dc1746cc44ad843629dfaaa5fc29fc762eb5dce453': ('guSprite2DInit', 204, 'libultra 2.0J–2.0L debug library'),
    '806a712b699bb14ebb3ebbc909fb448f18c649b0937278d1a10d287ce8a1e81f': ('init_lpfilter', 160, 'libultra 2.0I'),
    '026df01f62553b8a8811fceee533065601c18191593882badbdf1b9a78c6cfce': ('init_lpfilter', 276, 'libultra 2.0I debug library'),
    '2995a6f8e965e5eeafef35a2b57ac86e02f262cb60d4fa3e0bbed5d667cb1228': ('ldiv', 132, 'libultra 2.0I'),
    '394fb39bc33aaaad925a716a91f2b93dbee1332956c1e87bb80f5ded7ca68581': ('ldiv', 132, 'libultra 2.0J–2.0L'),
    '431bd24c00a665a9dfc31e0438205ca4c56746adfa8f88bd3dff5d7034373e03': ('ldiv', 164, 'libultra 2.0I debug library'),
    '281d8e44a86568d4acad8d512145c457af63f4bbf46381b3a65ac239997b3eb5': ('memcpy', 40, 'libultra 2.0J–2.0L'),
    '770c81b6c68fa85d2b98688a09dd33a41366dd56288fda754c24eb3247e8763d': ('memcpy', 44, 'libultra 2.0I'),
    'e7269bcaf1aa12647eba8553b059248f901fda12ceda0934a73b0e3bf877fc5a': ('memcpy', 88, 'libultra 2.0I debug library'),
    '66d82b61cb2f0bbfa7ee014d2e43b8a97e09efa7e20b29a132336a1cdbb83c34': ('osCreateRegion', 248, 'libultra 2.0I'),
    'e1f8fc22b310d9b1de38e005abb82e51e407bae179b1d3a5e30e0d2791d53e58': ('osCreateRegion', 260, 'libultra 2.0J–2.0L'),
    '412bda181519fac2c3ccca97341a1a0ed8c5797e9f12bcc6a34ac7caad1b442e': ('osDpGetCounters', 68, 'libultra 2.0I'),
    'b35994cc82f14702d7839e647017e17a87baeefa861a4464ecb989c8dd04c0b1': ('osDpGetCounters', 76, 'libultra 2.0J–2.0L'),
    '7843b15bfe6a2977c9455c863fc5340abd19636530a685781441a63c8efee91b': ('osDpGetCounters', 80, 'libultra 2.0I debug library'),
    '90db2ee37f6cc2f991052c5a20ca4ea26d86cab81b5e8f3128ed1cd81303d668': ('osDpGetCounters', 128, 'libultra 2.0J–2.0L debug library'),
    'bd63154afde9072507ecb5c744ccc2422fcd7c4c0e053ad97f1b563ef589bf6d': ('osDpSetStatus', 44, 'libultra 2.0J–2.0L debug library'),
    'b878f5f6f53d95f1800363acd8695d6516009542374b16fcce92be1b40af23a2': ('osEPiGetDeviceType', 40, 'libultra 2.0I debug library'),
    'ac60ea01465c29257b470c9fb9dc846e354d08c12856b02edb0c536280b3a25c': ('osFree', 84, 'libultra 2.0I'),
    'ef8b9984b6cdb4e4d05da3b49cb614fe33874df00f815adce51a37cbe454b15c': ('osFree', 92, 'libultra 2.0J–2.0L'),
    '92102fdd6bc7adf613543094bcadf3266f7d54f193b3236ef3a63297497d4b41': ('osGetMemSize', 144, 'libultra 2.0I'),
    'a23e1783e10b4e45055c794a8339a29b72e3a360df718541896e3234e5c58a2a': ('osGetMemSize', 160, 'libultra 2.0J–2.0L'),
    'cf597320dda8f990b5c5c145dcb4fe9252a815ab4c4558c4cfc3f02731908c2d': ('osGetMemSize', 160, 'libultra 2.0I debug library'),
    '9c4001caddfcfa873faa46919c88464c7a6f40915fb467a513f17e8525f8d6ba': ('osLogFloat', 32, 'libultra 2.0I debug library'),
    '9af1d681c69fdfef018040369c8b1f629488a82a285ddf83f2b9a01830e838a3': ('osMalloc', 52, 'libultra 2.0J–2.0L'),
    '7fff4760999328a22e45d11f152406279cdf916f335075af434a9a511ba7a051': ('osMalloc', 60, 'libultra 2.0I'),
    '26dd204fd60612f208e5646126f0062a3ad07def6bb4e67e2bfc333d2300b488': ('osViGetCurrentField', 36, 'libultra 2.0I debug library'),
    'dc4983b74bfe4a378d55d21ee3648fe79e8fcb66386a6c23f6861f657d159325': ('osVoiceCheckWord', 2448, 'libultra 2.0J–2.0L'),
    'd6b1513a3310c253cc17cff8c21ce7dcd6aaae6b82188b8d9389df3bef7e8ff9': ('read16', 48, 'libultra 2.0J–2.0L'),
    '758e936c0f15b9ac435ce3e9ab8de198fd1f0bb7af60754693a9652f1c6bbe5d': ('read32', 80, 'libultra 2.0J–2.0L'),
    '5204cc7cb887189b5fa6e3aef1af2e95444dfb4bb21f7898ffad2ded6d72e786': ('readVarLen', 80, 'libultra 2.0J–2.0L'),
    'cd03d660ae03553d74c6f69c8003d879dee993e86317283ae428a1ae02e602c7': ('spClearAttribute', 32, 'libultra 2.0I debug library'),
    'b7118e71934c8550432ef03c2ad2c59a5621139dfa415484e9a6b70909926ab2': ('spClearAttribute', 68, 'libultra 2.0J–2.0L debug library'),
    '6ba389372613ce47b15de8a82b3140b2586895180b0fb3f76b8d3cbda861d42a': ('spColor', 36, 'libultra 2.0I'),
    '0b713d48a13b7b618d46256efff789fdef6ac240c5b1bffc37c6bfd8d827fe56': ('spColor', 48, 'libultra 2.0I debug library'),
    '268563827040168edb54122d5f783b7cc8827033d62416a1febbe401d4d7939b': ('spColor', 112, 'libultra 2.0J–2.0L debug library'),
    'ba2761f3207dff85cbc1d54f49af1b40c699bf5d9e9aaeb5967923fb3db01409': ('spSetAttribute', 60, 'libultra 2.0J–2.0L debug library'),
    'ba3fd0f15d38dd7f65a8cec858cd95f6cd476e3920b567661676fef4ad5babba': ('strchr', 64, 'libultra 2.0I'),
    '2b0ae85162d555ae1be5b8a3cf669d7ecb2fa69dc92cec235158bfe6522db2ad': ('strchr', 88, 'libultra 2.0I debug library'),
    'bd978992a5fc782a6819fa1742c862ce261c4fb67e473e63b08cd3a010c1d467': ('strlen', 36, 'libultra 2.0K–2.0L'),
    'd495c369e103751741e63ebe8e74a11d71dbcfcae8f62d0bed2fdcfcfaa9f172': ('strlen', 36, 'libultra 2.0J'),
    '1a0aa68ec64cb0416c51af6928756ae3e13fda25594f3ae75665118951c7f97f': ('strlen', 40, 'libultra 2.0I'),
    'aac63b79d1aa11732d40ebb8756952c6444a9984b67546da8fcb89c4d73849cb': ('strlen', 76, 'libultra 2.0I debug library'),
}

# Complete relocation-free KMC runtime bodies. The multiply's 56 bytes were
# compared instruction by instruction across four independently imported
# projects and their KMC runtime sources; all four are identical.
KMC_ASSEMBLY = {
    '13cb3d5a4c97202ce93535c8a83499feb6da54c55a40590b17c074fecd62aa09':
        ('__muldi3', 56, 'KMC 64-bit integer multiply'),
}

SIZES = tuple(sorted({size for _, size, _, _ in KNOWN.values()} | {size for size, _ in _MTX_CAT_ASM}
                     | {size for _, size, _ in KNOWN_C.values()} | {size for _, size, _ in KMC_ASSEMBLY.values()}))


# The cartridge's entry stub (libultra's makerom boot code, which the port replaces with its own start-up): clear BSS
# with paired stores and signed `addi`, then jump to the game's `main` with $sp set in the delay slot. Only the
# addresses (BSS start and size, main, the stack top) differ between games, so a game's copy is told by its shape:
# the 16-bit immediates of lui/addiu are masked, everything else must be exactly this.
_ENTRY_SHAPE = (0x3C080000, 0x25080000, 0x3C090000, 0x25290000, 0xAD000000, 0xAD000004, 0x21080008, 0x2129FFF8,
                0x1520FFFB, 0x00000000, 0x3C0A0000, 0x254A0000, 0x3C1D0000, 0x01400008, 0x27BD0000)
_ADDRESS_OPCODES = (0x0F, 0x09)  # lui, addiu


def identify_entry(code):
    """The entry stub (see _ENTRY_SHAPE) with its alignment padding, or None."""
    words = [int.from_bytes(code[at:at + 4], 'big') for at in range(0, len(code) - 3, 4)]
    while words and words[-1] == 0:
        words.pop()
    if len(code) % 4 or len(words) != len(_ENTRY_SHAPE):
        return None
    if any((w & 0xFFFF0000 if w >> 26 in _ADDRESS_OPCODES else w) != shape for w, shape in zip(words, _ENTRY_SHAPE)):
        return None
    return {'name': 'entrypoint', 'names': ['entrypoint'], 'corpus': 'N64 boot code (makerom entry)', 'bytes': len(code),
            'target_sha256': hashlib.sha256(code).hexdigest(), 'reference': '',
            'variant': "clears BSS, sets the stack and jumps to main; a port starts the game its own way",
            'evidence': 'The cartridge entry stub: its instructions are the SDK boot code\'s, only its addresses are the game\'s.'}


def identify_relocated_entry(code, relocations):
    """The makerom entry shape with exactly four paired MIPS address relocations."""
    if not (identity := identify_entry(code)):
        return None
    pairs = ((0, 4), (8, 12), (40, 44), (48, 56))
    expected = {(offset, kind) for hi, lo in pairs for offset, kind in ((hi, 5), (lo, 6))}
    if len(relocations) != len(expected) or {(offset, kind) for offset, kind, _ in relocations} != expected:
        return None
    symbols = {offset: symbol for offset, _, symbol in relocations}
    if any(not symbols[hi] or symbols[hi] != symbols[lo] for hi, lo in pairs):
        return None
    return identity


def identify(code,big_endian=True):
    if big_endian and (entry := identify_entry(code)):return entry
    if not big_endian or len(code) not in SIZES:return None
    digest=hashlib.sha256(code).hexdigest()
    if (kmc := KMC_ASSEMBLY.get(digest)) and len(code) == kmc[1]:
        name, size, variant = kmc
        return {'name': name, 'names': [name], 'corpus': 'KMC handwritten runtime', 'bytes': size,
                'target_sha256': digest, 'reference': '', 'variant': variant}
    found=KNOWN.get(digest)
    if found is None and len(code) in (240, 360):
        variant = _MTX_CAT_ASM.get((len(code), hashlib.sha1(code + b'[]').hexdigest()))
        if variant:
            found = ('guMtxCatF', len(code), variant, 'mgu/mtxcatf.s')
    if found is None and (c_found:=KNOWN_C.get(digest)) and len(code)==c_found[1]:
        name,size,variant=c_found
        return {'name':name,'names':[name],'corpus':'libultra C library','bytes':size,'target_sha256':digest,
                'reference':'','variant':variant,'from_c':True}
    if not found or len(code)!=found[1]:return None
    name,size,variant,reference=found
    return {'name':name,'names':[name],'corpus':CORPUS,'bytes':size,'target_sha256':digest,
            'reference':REFERENCE+reference,
            'variant':variant}
