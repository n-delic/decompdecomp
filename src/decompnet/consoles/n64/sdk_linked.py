"""Exact SDK library entries from a known linked N64 image.

The bodies refer to linked functions and constants. Bind the full body digest
to the analysis image digest so identical instructions cannot identify a
different image's implementation. Store only digests, sizes and names.
"""
import hashlib

CORPUS = 'N64 C runtime'
_LINKED_IMAGE = 'd347159808f0374a93cf44cfb6135d8f56279f7b'
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
    _LINKED_IMAGE: {
        '6df5833b52a06e452920791e169336c96b19c5fb45c111dca9b4950e844bb6bf':
            ('__osEPiRawStartDma', 492, True, 'io/epirawdma.c'),
        'ee9739c6cc43722de7448204888a8dc83abb0b4baf964a89484edb55932d2453':
            ('__osEnqueueAndYield', 264, False, 'os/exceptasm.s'),
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
