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

### Region type map

The rounded-square map below the byte strip uses the function map's shared layout and cell renderer.
Each square is one analysed region in ROM order, regardless of byte size; the strip still shows byte proportions.
Hover for type, offsets and size, or use arrow keys and Enter to inspect a region. The table remains a keyboard-accessible alternative.
Image and audio labels use the existing format evidence. A local **3d model** classification is available in region notes;
there is no automatic 3D model detector yet. Saved classifications recolour the map without changing matching status.

### Texture discovery, usage and geometry

Open a region's **Asset preview → Find texture layouts** to compare up to 16 thumbnails.
The search tries RGBA 5551, RGB 565 in both byte orders, RGBA 8888, intensity, intensity/alpha
and indexed CI4/CI8 layouts. It compares common dimensions plus your selected dimensions
(capped at 128×128), reading at most 64 KiB from **Data offset**. Opening discovery exposes
palette controls above; indexed guesses try that offset as RGBA 5551, IA16 and RGBA 8888.
Click a thumbnail to copy its settings and open the full preview. The continuity/diversity/visibility
score ranks guesses; it is not a probability that bytes are a texture. Uniform bytes rank zero.
Row-major layouts are supported; tiled/swizzled layouts remain planned. Decompressed sources
use the existing 4 MiB decoder limits. No discovery result automatically changes a region's type.

**Used by** summarises direct address references, loader callers, copy/DMA helpers,
decompressors, buffer consumers and reads. Each known function is linked, with original
instruction addresses and evidence. Indirect callees are retained in detailed flow evidence;
unknown identities do not become function links. Tracing remains static and block-local;
passing a buffer does not establish successful loading or rendering.

The square map defaults to **Group regions with shared boundaries**. A group joins two or
more display regions covered exactly by one imported data symbol or loader source extent.
Imported symbols take precedence over overlapping loader hypotheses; groups never swallow
known code or infer shared assets merely from adjacency. The byte strip and raw region table
keep their existing boundaries. Inspect a group to see its basis, member links and **Use group
start for previews**. Ungroup to see individual types; mixed-type groups appear as data.
A symbol or transfer can contain several assets, so grouped extents remain evidence, not a
claim of a complete asset format.

**Inspect 3D geometry** reads a manual vertex/index layout using **Source** and **Data offset**
above. Choose signed 16-bit or float32 XYZ positions, byte order, vertex stride, position offset
within each record and count. Optional unsigned 8/16/32-bit indices use an absolute offset in
the same source (ROM or decompressed bytes). Triangles, strips, lines and points are supported;
limits are 4,096 vertices and 8,192 triangles. Invalid indices, non-finite coordinates, truncated
buffers and unsupported controls produce actionable errors. Display lists, primitive restart,
skinning, materials and proprietary model containers are not decoded.

Drag or use arrow keys to rotate the fitted wireframe; wheel or +/− zooms, and Home or
**Reset view** resets it. **Save notes** retains both texture and geometry layouts with the
region annotation. These settings and previews do not modify bytes, symbols or matching.

### Discover assets with agents

Open **Discover assets with agents** above the maps. Choose all unknown/repeated regions,
a ROM byte range, or an imported function whose direct/traced data you want investigated.
**Discover assets** prepares the whole eligible queue, then opens the normal agent/model
launcher. These use your selected providers and their ordinary costs. Discovery also appears
as a task in the launcher for ROM projects; object-build projects need the planned image adapter.
Startup checks the tools for the selected task. Asset discovery needs its inspection/proposal
tools, not function-claiming or compilation tools. A missing-tool error names the required tool.

Workers lease one region at a time, inspect original bytes, neighbouring boundaries, symbols,
references and loader chains, and try the existing texture/audio/geometry inspectors. They
propose a descriptive name, type, format notes, preview layouts, advisory byte boundaries,
related region IDs, citations, supporting/counter evidence and remaining uncertainty. Or they
record an unsuccessful approach and continue. The app carries the queue across provider
sessions; session size is not a per-run batch limit. Prior failed/rejected approaches and review
feedback travel with later claims. Pending and accepted proposals are excluded from new passes.

Stop retains the queue and findings. Resume selects agents again; **Choose a different scope**
starts a separate pass without deleting old findings. Scope is fixed while a pass runs. Returning
to the tab or reopening the window reads the existing server-owned pass; it never starts agents.
App restart requeues claims from the old process. Claims expire after 15 minutes without an
inspection/tool renewal, so stopped or disconnected workers cannot hold regions indefinitely.
An agent stopping unexpectedly returns its region and records the interruption. A completed
pass stops its workers; repeatedly unproductive sessions pause with an actionable explanation.

The review list is paginated, newest first. Open any finding to compare its evidence and
current interpretation, preview the proposed layout, and edit the name/type, evidence, format
notes or boundaries. Accept and reject are human actions unavailable to workers. Reject with
feedback to guide a later pass. Acceptance checks the frozen image/layout and the note version
you reviewed, preserving existing notes. If the proposed subrange differs from the analysed
region, its boundaries remain advisory and its type is not applied to the whole region. Accepted
names are local labels; no linker symbols, source files, bytes, function extents or matching
results change. Unknown and unreferenced bytes are never declared unused.

Workers receive only discovery, original assembly and guide tools. They cannot claim/compile
functions, edit symbols, accept their own work or install/run format decoders. Novel formats
can be described as text, but executable decoder proposals need a future code review/test flow.
Confidence means an agent's assessment, not proof. Static loader analysis, texture layout guesses
and manual geometry retain all the limits described above. Proprietary game data stays local
to the app’s files; selected agents receive inspection evidence through their configured provider,
just as they receive assembly when decompiling.

Asset discovery now records automatic layout/decompression checks, comparisons with
static loader extents and independent worker critiques. Use at least two discovery
agents for both stages; same-model reviews are labelled. The review dialog can
find structurally similar data regions, with pageable results and no automatic
label propagation. Parser success and static references remain evidence rather
than proof of intended use. Human acceptance is unchanged. These features share
the ROM Explorer adapter on all supported ROM consoles; project-mode object/data
mapping remains planned before these tools can reach project-mode consoles.

### Automatic native textures

N64 scans recognize source-assisted indexed texture banks, including RNC methods 1 and 2.
Recognition checks the nested offset tables, format, dimensions, every bitmap and palette
extent, and both compression checksums. Supported variants are CI8/RGBA16, CI4/RGBA16, CI4/IA16, RGBA32 and a fixed
six-level 32×32 RGBA16 mipmap layout. **Detect asset / texture bank** defaults to the first frame with its first palette;
the region shows dimensions and frame/palette counts. Frame, palette and mip selectors
resolve validated offsets automatically, and saved preview settings retain those choices. Detection needs neither a source
checkout nor release-specific ROM addresses. Generic data symbols retain their names and
references while validated texture banks appear as images. Function regions retain priority.

This validates the container layout, not runtime use. Other mipmap shapes, tiled and
proprietary layouts still need manual investigation. RGBA32 is validated with synthetic fixtures
and the source loader; the private cartridge check exercised indexed and RGBA16 mipmap banks. Native recognition is N64-only;
other ROM consoles retain the shared PNG/JPEG/audio detection and manual preview controls.
The native pass caps blocks at 16,384, individual input/output at 4 MiB and total attempted
expanded output at 64 MiB; assets beyond those limits may be missed. It checks cancellation
between blocks. Source and game bytes remain private; only generic format rules are shipped.

The shared scan allows 256 bounded media validation probes, separately from compression
probes. Headers beyond that budget remain unclassified and the report explains the limit.
Rejected PNG/JPEG/VAGp headers no longer create image/audio regions. JPEG recognition checks
frame and scan structure and dimensions; compressed scan validity is still checked by the
browser. Saved scans refresh automatically after this detector update.

RNC method-2 token grammar was checked against the
[ScummVM decoder](https://github.com/scummvm/scummvm/blob/master/common/compression/rnc_deco.cpp).

### Texture browser and playback

Open the sibling **Texture browser** card for detected banks and structurally recognized
PNG/JPEG images on any ROM platform. It stays collapsed until opened and preserves that state
across refreshes. Filter by format, dimensions, name, ROM offset or function. Pages contain
24 banks, grouped by their detected extent across display-page splits. Three concurrent
thumbnail requests bound loading; Stop cancels outstanding requests immediately. Errors stay
on their individual thumbnails with a region link for investigation. Changing pages/filters,
closing the browser or switching projects cannot paint late thumbnails into another view.

Each tile links to its ROM region and separately lists recorded address references and
static loader/buffer leads, deduplicated across the whole bank. These are static links, not
proof that the game renders the image; none found never means unused. Original imported
symbols and exact-code results remain unchanged.

Supported native banks offer frame/palette/mip selectors and explicit **Animate frames** /
**Stop animation** controls. Selecting a native view restores automatic format/offset selection.
Animation visits every frame, starts only on request, uses a chosen 1–24 frames/s preview rate,
and stops on drawer/project navigation or another inspection. Requests remain sequential;
actual playback can be slower than the requested rate. No original animation timing is claimed.
RGBA16 mipmaps use the source display-list offsets, padded rows and odd-row four-byte word
swap; all six levels are independently previewable. No palette is invented for direct color.

Native decoding and selection remain console hooks. Other consoles share the browser for
recognized images, with native formats and project-mode original-image adapters planned in
ROADMAP.md. No source checkout, external image library or proprietary fixture is shipped.
