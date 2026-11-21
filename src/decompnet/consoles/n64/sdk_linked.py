"""Exact SDK library entries from a known linked N64 image.

The bodies refer to linked functions and constants. Bind the full body digest
to the analysis image digest so identical instructions cannot identify a
different image's implementation. Store only digests, sizes and names.
"""
import hashlib

CORPUS = 'N64 C runtime'
_LINKED_IMAGE = 'd347159808f0374a93cf44cfb6135d8f56279f7b'
_LINKED_CLEANUP_THREE_WORDS = '1824380911579424e50042d45aef4851f7ab25a7'
_LINKED_CLEANUP_SLOT = 'eb9bacb12eb6665be9936a3e1dfd6d57b2eaabdb'
_KNOWN = {
    _LINKED_IMAGE: {
        'dd2ba42627dae6bc53ff6e61ca8ca0cfbe7f38149db59f304e22c4f9b5fa0897': ('atan2', 364),
        '8991c66219b057ccddd77b017db7eacf6b2569fd0c1338390313ddbe3024045d': ('log', 232),
        '3800217312c49d1fa8ff5c34782d0481830d3dc9aa4f08236d2dd7b5012b3078': ('sin', 224),
        'e559352b58aed460f5708a4a0e519d8e7ad7a9c15a5c7b22d23ea09472620fd5': ('cos', 152),
        'edc8f9769d484b63cae532ab0740bfb1817d9c294d9d25a5754c8bb7f2462f9b': ('tan', 212),
        'fb814f8e0cb680506fb611e3b96340cb2a085c70e8749a4cc314aef5e114ed5f': ('sqrt', 540),
        'd1b8e02bfd5e2c5514fe5b58ba1aca3f52fde2cd6e4e6a66f2721176b394ddd9': ('strcpy', 200),
        '82b5982445b3366b275d92e0bc0370cd7122c0ecf804a7658c906ab1ddb31594': ('strupr', 96),
    },
}
# The PI manager references the EPI DMA entry. The scheduler uses linked
# addresses for queues and OS globals when saving and restoring context.
_LIBULTRA = {
    _LINKED_CLEANUP_THREE_WORDS: {
        'cb6887bd3a5941c6251ff092561b66d2fa53929becbf2cc8f3715e0972ec68fa':
            ('__osDispatchThread', 380, False, 'os/exceptasm.s'),
        '3b278142e6244777038334c3fe9182c3e6d5d528be3334528be8053492c444d0':
            ('__osCleanupThread', 12, False, 'os/exceptasm.s'),
    },
    _LINKED_CLEANUP_SLOT: {
        '49204e7bcbcbb5aa09f6bd05cc18cce5b6a108c1fad5dd19cdacafc43298981a':
            ('__osDispatchThread', 380, False, 'os/exceptasm.s'),
        '0a995ca509a00083c2fedae1bb36d2e75c8fbd76e8fe38802abe620e4bd0f343':
            ('__osCleanupThread', 8, False, 'os/exceptasm.s'),
    },
    '70d08c55952d61fdb1e52a04836bd75b5bc2a428': {
        'f5183d7de6c4f9af7e9cbef377ed73e3777aded4aca8c731517b942ae3b0eb22':
            ('__osDispatchThread', 380, False, 'os/exceptasm.s'),
        '7293df9a83f05c62098c262ee68f059973d64519cae0ca9dd53cef242eeb8ff9':
            ('__osCleanupThread', 12, False, 'os/exceptasm.s'),
    },
    'd2fb7f03a895a0c2c61de58c63af9b2db2439c75': {
        '05a713617290f417c521b3ad4a4fe7e1761e262782152541310369bda7666e4a':
            ('__osDispatchThread', 380, False, 'os/exceptasm.s'),
        'eab6c45cb505ba249cd0c3b8d90e65e1f92aef7b49df5deba3d7bde755739565':
            ('__osCleanupThread', 12, False, 'os/exceptasm.s'),
    },
    _LINKED_IMAGE: {
        '6df5833b52a06e452920791e169336c96b19c5fb45c111dca9b4950e844bb6bf':
            ('__osEPiRawStartDma', 492, True, 'io/epirawdma.c'),
        'ee9739c6cc43722de7448204888a8dc83abb0b4baf964a89484edb55932d2453':
            ('__osEnqueueAndYield', 264, False, 'os/exceptasm.s'),
        # Timer, initialization, and scheduler callers corroborate the
        # names of the register accessors, which have very short bodies.
        'da3f6c89552e0ed52b5872b8398bbc13755c0deaa60d29d429fa7f4707694df2':
            ('__osDisableInt', 112, False, 'os/interrupt.s'),
        'b6f58a0412d63a52440645623c5a49320459a39befc7ea4359a374051eceb105':
            ('__osRestoreInt', 28, False, 'os/interrupt.s'),
        '974544a72c7c790c5bf1d0ac8efb349bc7c0a15650913c5f22f6c5ebefff225f':
            ('__osGetCause', 12, False, 'os/getcause.s'),
        '923a8ad5844532bc55f28624d13567c9795c3f893ebf07353f29b4ea1df8bc84':
            ('osGetCount', 12, False, 'os/getcount.s'),
        '2ea7fc1f266eee7e59be3d8ffee2aebda5c74a4e6765e268c725a9a6a9f50ddf':
            ('__osGetSR', 12, False, 'os/getsr.s'),
        '36d5b735ab25f9bb478700d6bf34d5e54555d915b3f13a889e07eade088947cd':
            ('__osSetCompare', 12, False, 'os/setcompare.s'),
        '8c9798aafb630c54a679991a2a686c3379687a94f8b3c8c14e5eafe1cd8cb961':
            ('__osSetSR', 16, False, 'os/setsr.s'),
        '57a60924b5ab6bcac4231c2f07f0716ee5d63495724303dbfa626ca0f4112708':
            ('__osSetWatchLo', 16, False, 'os/setwatchlo.s'),
        # Require the dispatcher to end at ERET. Some splitters include the
        # thread cleanup and interrupt-mask code that follows it.
        '3c994631aaa63f51168e1a49f7cb26ec59bcd7392c6fc2a77b4e3eaafafa6b4f':
            ('__osDispatchThread', 380, False, 'os/exceptasm.s'),
        'e248b50ab051ccd65b9e8c6a7be7b2ab303897048922f84f433fc916631821ee':
            ('__osCleanupThread', 12, False, 'os/exceptasm.s'),
        '571a86ab329313610faed979a12764d0e386fd6d382f5c0b0c69eb2ee5e20f75':
            ('osSetIntMask', 160, False, 'os/setintmask.s'),
    },
}
SIZES = tuple(sorted({size for entries in _KNOWN.values() for _, size in entries.values()}
                     | {entry[1] for entries in _LIBULTRA.values() for entry in entries.values()}))


def identify(code: bytes, image_sha1: str) -> dict | None:
    if len(code) not in SIZES:
        return None
    digest = hashlib.sha256(code).hexdigest()
    entry = _LIBULTRA.get(image_sha1, {}).get(digest)
    if entry:
        name, size, from_c, file = entry
        corpus = 'libultra C library' if from_c else 'libultra handwritten assembly'
        reference = {'reference': f'https://github.com/decompals/ultralib/blob/main/src/{file}'}
    else:
        runtime = _KNOWN.get(image_sha1, {}).get(digest)
        if runtime is None:
            return None
        name, size = runtime
        from_c, corpus, reference = True, CORPUS, {}
    if size != len(code):
        return None
    return {'name': name, 'names': [name], 'corpus': corpus, 'bytes': size, 'image_sha1': image_sha1,
            'target_sha256': digest, 'from_c': from_c, **reference}


# ERET ends the dispatcher. The cleanup stub calls osDestroyThread and
# never returns. The next aligned entry reads and updates interrupt masks.
_MERGED_SCHEDULER = '7d564e31a6368dd0ec8d03a12c38dbf239fd9295dcf26a9f4bdcf3c4fcc79fce'


def split_scheduler(result, image_sha1: str) -> None:
    """Split a specifically verified merged scheduler target during ROM analysis."""
    if digest := _CLEANUP_WITH_PADDING.get(image_sha1):
        _split_cleanup_padding(result,digest)
        return
    if image_sha1 != _LINKED_IMAGE:
        return
    from ...rom.split import split_function

    for index, fn in enumerate(result.functions):
        if fn.size != 556:
            continue
        code = b''.join(line.word.to_bytes(4, 'big') for line in fn.lines)
        if hashlib.sha256(code).hexdigest() != _MERGED_SCHEDULER:
            continue
        cleanup, mask = fn.vram + 380, fn.vram + 396
        dispatch, tail = split_function(fn, cleanup, f'func_{cleanup:08X}')
        cleanup_fn, mask_fn = split_function(tail, mask, f'func_{mask:08X}')
        result.functions[index:index + 1] = [dispatch, cleanup_fn, mask_fn]
        return


_CLEANUP_WITH_PADDING = {
    _LINKED_CLEANUP_THREE_WORDS:
        'd246ea272f63bc5f3ee83b0a97d7bb5ea38571e55607676a9ef028f414642b61',
    _LINKED_CLEANUP_SLOT:
        '5e1020780847e0662b9f6d8a5f68456a8c2cb7c2575181bd253b217024738a42',
    'aa576624f2a66034cb0d6e621ba16a0d3beaca3a':
        '68cdef5461e1450d4fadc5c4fc480fc9e828e9a874925593bfd890aa18583e53',
    '4f1b8635322190bb3d0c6fc7d3ba844941f05574':
        '3eaf77212e85954523bcb217d98360d57d19247e28009cf659932a27399d7db0',
}


def _split_cleanup_padding(result,digest):
    from ...rom.split import split_function

    for index, (fn, padding) in enumerate(zip(result.functions, result.functions[1:])):
        if (fn.size not in (388, 392) or padding.size != 4 or padding.rom != fn.rom+fn.size
                or padding.vram != fn.vram+fn.size or padding.vram in result.named.values()):
            continue
        code = b''.join(line.word.to_bytes(4, 'big') for line in (*fn.lines, *padding.lines))
        if hashlib.sha256(code).hexdigest() != digest:
            continue
        # The dispatcher ends at ERET. The following cleanup stub destroys its
        # own thread and cannot return to the alignment word after its call.
        cleanup = fn.vram+380
        head, tail = split_function(fn, cleanup, f'func_{cleanup:08X}')
        result.functions[index:index+2] = [head, tail]
        return
