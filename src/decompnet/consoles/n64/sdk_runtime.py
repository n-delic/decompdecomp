"""Exact C runtime entries from a known linked N64 image.

The bodies refer to linked functions and constants. Bind the full body digest
to the analysis image digest so identical instructions cannot identify a
different image's implementation. Store only digests, sizes and names.
"""
import hashlib

CORPUS = 'N64 C runtime'
_KNOWN = {
    'd347159808f0374a93cf44cfb6135d8f56279f7b': {
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
SIZES = tuple(sorted({size for entries in _KNOWN.values() for _, size in entries.values()}))


def identify(code: bytes, image_sha1: str) -> dict | None:
    entries = _KNOWN.get(image_sha1)
    if not entries or len(code) not in SIZES:
        return None
    digest = hashlib.sha256(code).hexdigest()
    found = entries.get(digest)
    if not found or found[1] != len(code):
        return None
    name, size = found
    return {'name': name, 'names': [name], 'corpus': CORPUS, 'bytes': size,
            'target_sha256': digest, 'from_c': True}
