"""Exact, self-contained libultra assembly fingerprints. No SDK instructions are bundled.

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
SIZES = tuple(sorted({size for _, size, _, _ in KNOWN.values()} | {size for size, _ in _MTX_CAT_ASM}))


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


def identify(code,big_endian=True):
    if big_endian and (entry := identify_entry(code)):return entry
    if not big_endian or len(code) not in SIZES:return None
    digest=hashlib.sha256(code).hexdigest()
    found=KNOWN.get(digest)
    if found is None and len(code) in (240, 360):
        variant = _MTX_CAT_ASM.get((len(code), hashlib.sha1(code + b'[]').hexdigest()))
        if variant:
            found = ('guMtxCatF', len(code), variant, 'mgu/mtxcatf.s')
    if not found or len(code)!=found[1]:return None
    name,size,variant,reference=found
    return {'name':name,'names':[name],'corpus':CORPUS,'bytes':size,'target_sha256':digest,
            'reference':REFERENCE+reference,
            'variant':variant}
