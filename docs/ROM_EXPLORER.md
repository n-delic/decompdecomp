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
Image, audio and supported native **3d model** labels use format evidence. A local **3d model** classification is also available in region notes. Saved classifications recolour the map without changing matching status.

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
skinning and materials are not decoded by the manual inspector. Supported native containers use the automatic preview described below.

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
six-level 32×32 RGBA16 mipmap layout. CI8 banks may mix 16- and 256-colour palettes; each frame is checked against its cyclically paired palette. Palette controls show the colour count, and mixed banks follow the matching palette when changing or animating frames. An incompatible manually selected palette produces an error instead of reading adjacent bytes. **Detect asset / bank** defaults to the first frame with its first palette;
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

### Files inside embedded ZIP archives

Every console's scan reads an embedded ZIP's own directory and names each file's bytes by
its path in the archive (the Name column, sorting and the search box use it). A deflated
file is **compressed**, a stored one **data**; either is validated only when its data
expands to the declared size and CRC32, within 256 MiB of expansion per scan, and stays
tentative past that budget. A file whose data fails is rejected evidence and leaves its
bytes unclassified. Folder entries and the central directory with its end record are
labelled as the archive's **container**. The limits below apply.

### Texture packs inside embedded ZIP archives

N64 scans also recognize trailer-count texture packs inside embedded ZIP archives,
including archives followed by ROM padding. CI4/CI8 palettes, RGBA32 images, multiple
textures per pack, alternate palettes and supported CI4 frames are checked against
complete member layouts. Use **Texture** in region details to choose a pack member's
texture, then select its palette or frame. Saved preview settings retain these choices.
Archive paths supply display names; no archive files are extracted or executed.

The reader validates central/local headers, complete stored/deflate streams, lengths and
CRC32. It checks up to 64 archive end records and 16,384 members per archive, with a
4 MiB member limit and 64 MiB of attempted pack expansion per scan. Unsupported records,
partial packs, odd-width packed CI4 rows, ZIP64, split/encrypted archives and other ZIP
methods remain unclassified. Other archived asset formats need separate format adapters.
The updated scan revision refreshes existing maps on tab entry, retaining local notes.

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

### Automatic N64 audio

ROM scans recognize unrelocated libultra **B1** sound-bank control graphs. The detector checks
bank/instrument/sound pointers, nominal rates, key maps, envelopes, loop bounds and ADPCM
predictor books. Each waveform descriptor is mono: order-2 N64 VADPCM (nine-byte frames,
one zero even-byte pad allowed) or big-endian PCM16. Pointers in the control graph are relative
to its header; waveform offsets are relative to a separate sample table.

Named `alBnkfNew` calls supply static sample-table arguments through the shared MIPS reader,
including call delay slots. A table is associated only when the complete bounds and every
ADPCM frame selector validate and exactly one candidate fits. The named helper is a static
contract, not proof of its implementation or runtime execution. Without that evidence, a
unique adjacent aligned ADPCM table remains **tentative**; raw PCM cannot establish its own
location by looking like arbitrary bytes. If placement is ambiguous or invalid, only the
validated control graph is labelled audio; sample bytes remain unclassified and automatic
preview explains the unresolved placement. No source checkout or release-specific address
is needed at runtime. Other consoles retain their existing shared audio detection/previews.

Open the control region to choose among its sample descriptors, or open a detected waveform
region directly. **Detect asset / bank** resolves the descriptor and predictor book; **Preview
asset** prepares PCM WAVE locally. Sample selection prepares another preview and stops any
playing sample. Audio uses controls with no autoplay and no preloading; Play is always a
separate user action. Saved settings retain the sample index. Static loader-argument links
are separate from recorded address references, and audio banks never enter the texture gallery.

Preview decodes a single pass from zero history using the bank's nominal sample rate. Pitch,
mixing, effects, sequence synthesis and loop playback are not recreated. It returns at most
30 seconds / 4 MiB of WAV output and reports truncation while preserving the original sample
count. VADPCM arithmetic is checked against the equations in the
[vgmstream decoder](https://github.com/vgmstream/vgmstream/blob/master/src/coding/vadpcm_decoder.c);
it is a preview rather than a cycle-accurate audio microcode emulator. PCM16 and nonzero
predictor feedback have independent homemade regression vectors. Private ROM validation
exercised the VADPCM path; PCM16 was validated with synthetic fixtures and the source layout.

The audio pass caps plausible B1 graphs at 128, each graph at 1 MiB, sounds at 4,096,
unique wavetable/rate pairs at 2,048, table candidates per bank at 32 and total frame-header
checks at 4,194,304. Individual encoded samples are capped at 4 MiB. It checks cancellation
while following graphs, reading callers and validating frames. Beyond these limits, or for
compressed/relocated controls, other book orders, custom streaming codecs or sequenced music,
recognition may be incomplete. These gaps and project-mode image adapters remain planned in
ROADMAP.md. Detector revision 5 automatically refreshes old saved maps. Source and game bytes
remain private; only format rules and homemade fixtures are committed.

### Automatic native model geometry

The N64 adapter recognizes a source-assisted indexed geometry format in raw and RNC 1/2
blocks. It checks every nested node/part/section table, child and part index, 24-byte
material extent, byte-plane vertex stream and polygon stream. Triangle commands index a
32-slot cache: the decoder loads the first 32 vertices, then alternates 16-slot replacements.
Every reference must resolve to a loaded vertex; every run must terminate and consume the
complete stream. Positions alone never establish a model. Empty containers are not labelled
as models. Compressed candidates must pass both RNC checksums.

Open a model region, choose **Model mesh**, then **Preview asset**. Changing the mesh prepares
its wireframe. The selector shows node, part and section identity; **Save notes** retains the
selection. Drag/arrow keys rotate, wheel/+/- zoom and Home resets. Each section is shown in
local coordinates; skeleton transforms, animation poses, textures and material shading are
not applied. A container can contain alternative parts rather than one assembled character.
Detection validates the storage layout, not runtime rendering or a semantic object name.

Limits: 4 MiB per container, 64 MiB attempted decompression per scan, 16,384 candidates,
2,048 sections per container, 65,536 total vertices and 131,072 polygon commands. Each mesh
has at most 4,096 vertices and 8,192 polygon commands. Invalid or unsupported layouts retain
ordinary region evidence and manual inspection. Other native model formats and original-image
adapters for project mode remain planned. Original-image integrity is checked before previews;
all decoding stays local and never alters matching or the ROM.

### What generalizes to other games

Recognition uses headers, offset tables, payload sizes and checksums, not game titles or
known ROM offsets. The supported indexed texture/model layouts and B1 sound banks can
therefore be recognized in another N64 game using those same formats. RNC is a general
compression format; the corrected method-1 reader also serves compressed-code importing
and rebuild validation. Its tail-word prefetch is separate from physical control-bit and
literal bounds, and both checksums remain mandatory.

This is not a universal native-asset classifier. A supported indexed cartridge directory
now supplies animation, collision, grid/bounds, placement and binary-resource roles after
its complete hierarchy and ID-count relationships pass validation. The same schema in
another N64 game can be recognized without its source checkout. Different native formats
need additional rules. The shared ROM inspector still recognizes supported standard media
headers and provides explicit manual layouts.

### Native resource directories and metadata

The N64 adapter searches for a candidate eleven-part indexed directory, validates all
object/level/animation/grid index tables, matches object and binary ID counts against their
collections, and checks that binary IDs are ordered and unique. It never chooses a release
address or a game name. A broken or unfamiliar hierarchy supplies no semantic directory
roles. Compressed leaves must pass RNC sizes and both checksums; corrupt leaves remain
unclassified rather than inheriting a confident asset type.

New types in the square map and region notes are **animation**, **collision**, **object
placements**, **world grid**, **music sequence** and **demo**. Existing image, model and audio
preview descriptors retain priority and gain directory paths where available. Empty grid
sections and geometry without meshes are reported as empty storage records, never unused
space. Search the region table for a type, directory path or numeric object/level identity.
Imported function regions retain priority; nothing changes the judge or code progress.

Open a typed resource and choose **Inspect resource** to see its path, compression and byte
sizes, frame/node/record counts, track headers or demo frame/start-location IDs. Animation
streams validate record extents and translation/rotation set indices; missing initial
orientation records are flagged. Collision, bounds, placement and grid collections validate
record widths and extents. Binary resources need additional sequence or demo header checks
before receiving those types; arbitrary binary data retains its generic role. Sequence
headers validate track offsets and time division, not the complete event program. Demo
headers validate frame counts and ordered bitstream offsets, not playback. No skeleton pose,
scene reconstruction, animation playback, demo playback or music synthesis is implied.

Resource inspectors now show up to 64 records per page, with numeric column sorting on the
current page. Directory entries link to their mapped regions; empty entries have no link.
Object indices link placements to their object directory, geometry and animation collection.
Repeated object-type flags are shown separately and are never substituted for object indices.
Links stay within the same validated directory root and analysis image.

Model previews include a skeleton table (parent, children and alternative part mappings),
with cycle and multiple-parent checks, plus material colors, UV multipliers, shading flags
and a texture-bank link when that index is present. The wireframe still displays one mesh
in local coordinates; alternative parts are not assembled into a guessed character.
Animation tables expose node track indices, initial positions/quaternions and track encoding.
The frame input reads supported uncompressed XYZ tracks. ADPCM translation/rotation tracks
are identified but not decoded or played. Missing initial orientations stay missing.

Collision inspection deinterleaves attribute/corner/region byte planes, checks the selected
page's corner, attribute and neighbour indices, and renders those surfaces as a rotatable
wireframe. Tables show surface flags, ground/wall material IDs and warp IDs. Material IDs and
flags are numeric storage values, not inferred labels such as floor, hazard or walkable.
Object metadata exposes finite ordered bounds and animation-type IDs. Placement and grid
inspectors show dynamic, static, simple and extended-static records. Extended-static X/Y/Z
components remain quantized values until grid origins and flags are resolved; they are not
presented as world positions. Unsupported record layouts show a warning while retaining
validated header counts.

All ROM adapters share paged strict UTF-8, NUL-terminated text-candidate and aligned-pointer
inspectors. Pointer words are mapped through imported segments, retaining overlay alternatives;
integer coincidences and runtime use remain unresolved. These inspectors do not strengthen
tentative classifications and read at most 4 MiB. Automatic font detection currently supports
PSF2 bitmap fonts with a version-zero 32-byte header, exact glyph extents, and no Unicode table.
The glyph atlas uses stored order, without guessing a character mapping. The weak two-byte
PSF1 signature and headerless console fonts are not automatically classified. Game-specific
fonts, non-UTF-8 encodings, animation playback and reconstructed scenes remain planned.

Compression is retained as a separate property alongside the semantic type. Standalone RNC
streams also receive validated extents and output sizes without a recognized directory.
Code-like MIPS output is labelled as a packed program image only when its decoded bytes
exactly match another location in the same analysis image. That label does not establish
that every byte is an instruction or certify physical cartridge mappings.

Directory work is capped at 128 root candidates, 16,384 tables/resources, 4 MiB per leaf and
64 MiB of attempted decoded/raw payload inspection per pass. Cancellation is checked between
tables and leaves. Generic signature limits are independent of native resource counts.
Failed zlib/gzip decoding produces **rejected evidence**, not a compressed type; probes
that cannot finish within their budget also do not label bytes as compressed. Strong headers
for other unvalidated codecs can remain tentative clues. Map legends count display regions,
not unique assets; directory paths and native offsets identify whole resources.

### Audio browser and libmus pointer banks

**Audio browser** lists detected samples and validated PCM WAVE/mono VAGp streams on
ROM platforms. Filter by name, codec or function, sort by heading and page through all
entries. **Prepare audio** makes one bounded WAV preview; **Play** starts playback.
Stop cancels preparation, and changing projects/tabs, closing the card or changing filters
ends the current preview. Errors remain visible and the controls recover for retry.

N64 scans recognize unrelocated `N64 PtrTablesV2` banks: tuning arrays, waveform records,
order-2 predictor books and loop extents must fit. Up to 128 banks / marked wave tables,
2,048 samples per bank, 1 MiB of control data and four million frame-header checks are
allowed per scan. Exactly one marked `N64 WaveTables` candidate must fit every sample
and ADPCM selector. This pairing remains a static inference; ambiguous placement stays
unresolved and does not classify sample data. The parser uses the
[libmus pointer-bank layout](https://github.com/cdlewis/libmus/blob/master/src/libmus_data.h).

The pointer bank stores no sample rate. Its 22,050 Hz default is an editable preview
choice, not a verified original rate or pitch. Region details can save the chosen rate
and sample. Output remains mono, limited to 30 seconds / 4 MiB, with no pitch, mixing
or loop recreation. Existing B1 banks retain their recorded nominal rates.

CRC-checked ZIP `.bin` members whose indexed channel/volume/bend tables fit are shown
as tentative sequence candidates. Track commands and musical interpretation are not
validated, and sequence synthesis is not implemented. They are never offered as WAV
waveforms. Unsupported formats and remapped banks remain unclassified. This native
adapter is N64-only; other consoles keep generic audio browsing and raw previews.
