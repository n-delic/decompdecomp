"""beslots.py game.wasm: swap each 32-bit value the linker wrote into data (pointers, function table indices) to
big-endian, in place. Needs the module linked with --emit-relocs."""
import sys

ADDEND = {3, 4, 5, 8, 9, 11, 14, 15, 16, 17, 21, 22}
SWAP = {2, 5}  # TABLE_INDEX_I32, MEMORY_ADDR_I32


def leb(b, i, signed=False):
    result = shift = 0
    while True:
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break
    if signed and byte & 0x40:
        result -= 1 << shift
    return result, i


def sections(b):
    i = 8
    out = []
    while i < len(b):
        sid = b[i]
        size, j = leb(b, i + 1)
        name = None
        if sid == 0:
            n, k = leb(b, j)
            name = b[k:k + n].decode()
        out.append((sid, name, j, j + size))
        i = j + size
    return out


def main(path):
    b = bytearray(open(path, "rb").read())
    secs = sections(b)
    index = {k: s for k, s in enumerate(secs)}
    swapped = 0
    for sid, name, start, end in secs:
        if sid != 0 or name != "reloc.DATA":
            continue
        n, i = leb(b, start)
        i += n  # the name
        target, i = leb(b, i)
        tsid, _, tstart, _ = index[target]
        assert tsid == 11, "reloc.DATA must point at the data section"
        count, i = leb(b, i)
        for _ in range(count):
            kind = b[i]
            offset, i = leb(b, i + 1)
            _, i = leb(b, i)
            if kind in ADDEND:
                _, i = leb(b, i, True)
            if kind in SWAP:
                at = tstart + offset
                b[at:at + 4] = b[at:at + 4][::-1]
                swapped += 1
    open(path, "wb").write(b)
    print(f"{swapped} pointers and function indices in data made big-endian")


if __name__ == "__main__":
    main(sys.argv[1])
