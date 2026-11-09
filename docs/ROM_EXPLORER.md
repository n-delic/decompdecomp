# ROM Explorer

Open a ROM project's **ROM Explorer** tab. It automatically attaches to a running scan or
checks the current image and mappings. Unchanged inputs reuse the saved map, keeping tab
entry quick. **Refresh** repeats the input check; **Stop** retains the last complete map.
No compiler, agent, external service or game code is run by this analysis.

## Loader source groups

The MIPS reader follows constants, incoming arguments, saved registers, stack arguments
and call-return values within a basic block. It applies call delay slots before reading
arguments. Branch joins, unsupported instructions, uncertain values, overlapping stack
writes and caller-clobbered registers discard facts rather than joining incompatible paths.
PS2 uses the EE EABI's eight register arguments; the other MIPS ROM adapters use four
register arguments and O32 stack arguments. Other processors need a call-argument adapter.

Named `memcpy`, `memmove`, `bcopy`, `uncompress` and `uncompress2` calls select their documented
argument contracts. N64 additionally supplies contracts for its named PI DMA functions.
Extended PI message fields are read only from explicit stores in the current block;
mutable ROM initializers are not treated as current message contents. Small wrappers
can forward a named contract through up to two levels. A function's name
and argument layout do not verify its implementation. Unknown helpers can produce
**candidate** source/destination/length permutations; conflicting permutations remain leads.

A source group records its complete ROM start/end range, caller, call instruction,
argument definitions and destination buffer. It links later same-block calls that receive
the buffer and original instructions that load from it. A named decompressor can extend
the chain into its output buffer. A store that may overwrite a buffer stops its later links.
Overlay mappings remain ambiguous where the owner cannot be established. Source extents
supply map boundaries but do not label the contents as data rather than loaded code.

These are static chains, not execution traces. DMA completion, successful decompression,
callee side effects and runtime reachability are unproven. Indexed file IDs, dynamic table
values, cross-branch paths and arbitrary interprocedural data flow can remain unresolved.
Each function is bounded to 32,768 instruction words and 2,048 events; a scan retains
at most 4,096 chains. Reaching a bound is reported as incomplete tracing. The UI states
the evidence and limits beside each chain. Matching and export mappings
never change as a result of this analysis.

If an unnamed helper's role is known, expand **Describe a loader function**. Choose the
function, its role, source domain and zero-based source/destination/byte-length argument
numbers. Save, then refresh. This is a local analysis hypothesis. Removing a role reverses
it; it never renames a function or changes a build input.

## Asset previews

Open a region and choose **Preview asset**. Automatic detection supports bounded PNG,
JPEG, PCM WAVE and standard mono VAGp audio. PNG chunk/pixel lengths and checksums are
checked; JPEG marker structure and dimensions are checked, then the browser validates its
scan. WAVE supports mono/stereo 8- or 16-bit PCM. VAGp uses the five standard PS-ADPCM
predictors; custom/interleaved variants are rejected and integer predictor rounding may
differ slightly from hardware or other tools.

Automatic detection also unwraps a recognizable gzip, zlib, Yaz0, MIO0 or Yay0 block when
its decoded payload has a supported image/audio header. For a raw payload choose
**Decompressed block**, enter its original ROM offset, then enter the data/palette offsets
relative to the expanded bytes. Decoder input and output are capped; incomplete streams,
invalid back-references and oversized declared output fail visibly.

For raw textures, choose the format and dimensions explicitly:

- RGBA8888, RGB888, RGBA5551 big endian, RGB565 little/big endian.
- Intensity 4/8 bit, intensity-alpha 4/8/16 bit.
- Indexed 4/8 bit with an explicit RGBA5551, IA16 or RGBA8888 palette.
- Palette grids in RGBA5551 or IA16.

Raw layouts are row-major and unswizzled. Four-bit rows are byte-padded when their width
is odd. A plausible-looking preview does not prove the format, dimensions or palette.
Console tiling, texture command interpretation and automatic raw-asset dimensions remain
separate work.

Raw audio supports unsigned 8-bit PCM and signed 16-bit little/big-endian PCM. Enter its
sample rate, channel count and byte length. Audio never autoplays; use Play. Closing or
replacing the drawer stops playback. Browser decode failures remain visible in the panel.

Preview limits are 4 MiB of input/decoded data, 1,048,576 pixels, 2048 pixels per dimension,
and 30 seconds of audio. Ogg signatures and other recognized compression headers can
remain identified without a supported preview decoder. Preview bytes are returned only to
the requesting local app/browser session and are never saved into notes or source files.
**Save notes** retains the format/layout controls alongside the region's label and notes.

## Scope and evidence

The scanner uses the shared recorded analysis-image hash verifier, including legacy split
metadata where necessary. The map covers the analysis image, which can be a flattened executable or contain expanded
code. Its offsets are not necessarily physical cartridge/disc offsets. Header-derived
container boundaries and loader source groups are distinguished from arbitrary display
pages. Classification totals remain separate from exact code-matching progress.

Object-build projects still need original-image adapters. Processor adapters plug into
`Arch.asset_flow`; console-specific call contracts and argument counts belong to `Console`.
The preview decoders and local UI/API are shared by every ROM console.

Reference implementations used for validation:

- [Archived SDK PI DMA contracts](https://ultra64.ca/files/documentation/online-manuals/man-v5-1/n64man/os/osPiStartDma.htm)
  and [extended PI message contract](https://ultra64.ca/files/documentation/online-manuals/man-v5-1/n64man/os/osEPiStartDma.htm).
- [zlib's documented uncompress contracts](https://www.zlib.net/manual.html).
- [crunch64's compression implementations](https://github.com/decompals/crunch64), used
  for independent homemade back-reference fixtures when installed.
- [vgmstream's PS-ADPCM decoder](https://github.com/vgmstream/vgmstream/blob/master/src/coding/psx_decoder.c)
  and [VAG container reader](https://github.com/vgmstream/vgmstream/blob/master/src/meta/vag.c).
