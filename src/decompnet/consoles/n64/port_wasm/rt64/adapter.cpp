/* RT64 renders the compatibility host's display lists. The host keeps its existing scheduler and software
 * RDP for writes into game memory; RT64 gets a separate, word-swapped view of that memory for presentation. */
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sys/mman.h>
#include <memory>
#include <string>
#include <vector>

#include <objc/message.h>
#include <objc/runtime.h>

#include "hle/rt64_application.h"
#include "rhi/rt64_render_hooks.h"
#include "plume_metal.h"
#include "ultramodern/ultramodern.hpp"
#include "lightweightsemaphore.h"
#include "adapter.h"
#include "presentation.h"
#include "png_dump.h"

/* The compatibility host owns the game's threads. Only ultramodern's VI state is used by this adapter. */
std::atomic_bool exited{false};
moodycamel::LightweightSemaphore graphics_shutdown_ready;
bool ultramodern::is_game_started() { return false; }
void run_thread_function(uint8_t *, uint64_t, uint64_t, uint64_t) { std::abort(); }

/* PORT_RT64_FRAMES=<folder> (PORT_RT64_FRAME_EVERY=n: every nth frame): what RT64 actually presented, saved as PNG
 * (frame_NNNNN.png, 8-bit RGB). The window can't be captured from outside (it belongs to the game's own process and
 * needs the screen-recording permission), so the evidence that a port renders under RT64 comes from here. RT64 draws
 * each presented frame into the swap chain's drawable; its draw hook runs after that, and the drawable (made
 * readable: the layer's framebufferOnly is turned off in open) is blitted into a shared buffer that the next frame's
 * hook writes out, once the GPU has finished it (the present queue waits for each frame's commands before it
 * presents). Unset, none of this runs. */
namespace {
const char *frames_dir;
unsigned frames_every = 1, frames_seen, frames_written;
RenderDevice *hook_device;
std::unique_ptr<RenderBuffer> readback;
uint32_t readback_w, readback_h;
bool readback_pending;

void hook_init(RenderInterface *, RenderDevice *device) {
    hook_device = device;
}

void hook_draw(RenderCommandList *list, RenderFramebuffer *framebuffer) {
    if (!frames_dir || !hook_device) return;
    // The previous frame's copy is finished: write it out.
    if (readback_pending && readback) {
        char name[32];
        std::snprintf(name, sizeof name, "/frame_%05u.png", frames_written++);
        const uint8_t *pixels = static_cast<const uint8_t *>(readback->map(0, nullptr));
        if (pixels) {
            png_dump::write(std::string(frames_dir) + name, pixels, readback_w, readback_h, size_t(readback_w) * 4);
            readback->unmap(0, nullptr);
        }
        readback_pending = false;
    }
    if (frames_seen++ % frames_every) return;
    auto *target = static_cast<MetalFramebuffer *>(framebuffer);
    if (target->colorAttachments.empty()) return;
    MTL::Texture *texture = target->colorAttachments[0].getTexture();
    if (!texture || texture->pixelFormat() != MTL::PixelFormatBGRA8Unorm) return;
    const uint32_t w = uint32_t(texture->width()), h = uint32_t(texture->height());
    if (!readback || w != readback_w || h != readback_h) {
        readback = hook_device->createBuffer(RenderBufferDesc::ReadbackBuffer(uint64_t(w) * h * 4));
        readback_w = w;
        readback_h = h;
    }
    auto *metal_list = static_cast<MetalCommandList *>(list);
    auto *buffer = static_cast<MetalBuffer *>(readback.get());
    metal_list->checkActiveBlitEncoder();
    metal_list->activeBlitEncoder->copyFromTexture(texture, 0, 0, MTL::Origin(0, 0, 0), MTL::Size(w, h, 1),
                                                   buffer->mtl, 0, uint64_t(w) * 4, uint64_t(w) * h * 4);
    readback_pending = true;
}

void enable_frame_readback(void *layer) {
    frames_dir = std::getenv("PORT_RT64_FRAMES");
    if (!frames_dir || !*frames_dir) {
        frames_dir = nullptr;
        return;
    }
    if (const char *every = std::getenv("PORT_RT64_FRAME_EVERY"))
        frames_every = std::max(1, std::atoi(every));
    // A drawable can only be copied from when its layer isn't framebuffer-only.
    reinterpret_cast<void (*)(void *, SEL, bool)>(objc_msgSend)(layer, sel_registerName("setFramebufferOnly:"), false);
    RT64::SetRenderHooks(hook_init, hook_draw, nullptr);
}
}

namespace {
constexpr uint32_t rdram_size = 0x800000;
// RT64 masks every RDP address with 0xFFFFFF (rt64_rdp.h RDP_ADDRESS_MASK) and indexes its RDRAM with the result, so
// the view it reads spans 16 MB, not the console's 8 MB. The host keeps the MIPS stacks of recompiled and interpreted
// code in KSEG0 just above the 8 MB (physical 0x800000 and up), where a game's locals (a palette, a vertex list built
// on the stack) can be what a display list points at.
constexpr uint32_t snapshot_size = 0x1000000;
constexpr uint32_t physical_mask = 0x1FFFFFFF;
// RT64 computes some RDRAM indexes from display-list fields without bounding them. The bytes past the 16 MB it is
// given are reserved address space that reads as zero (4 GB and a margin, lazily backed), so a stray index reads
// zeros instead of faulting the host: a fault inside RT64 is taken by the game's wasm trap handler and showed up as a
// bogus "out-of-bounds" stop of a game thread.
struct Ram {
    uint8_t *base = nullptr;
    uint8_t *data() { return base; }
    static constexpr size_t size() { return snapshot_size; }
    void reserve() {
        if (base) return;
        void *p = mmap(nullptr, (size_t(1) << 32) + (size_t(1) << 28), PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE | MAP_NORESERVE, -1, 0);
        base = p == MAP_FAILED ? nullptr : static_cast<uint8_t *>(p);
    }
} memory;
std::array<uint8_t, 0x1000> dmem, imem;
std::array<uint8_t, 0x40> header;
std::array<uint32_t, 8> dpc;
uint32_t interrupt, framebuffer;
bool has_framebuffer;
std::array<PortRt64ViField, 2> vi_fields;
float vertical_scale;
std::unique_ptr<RT64::Application> app;

auto *vi() {
    return ultramodern::renderer::get_vi_regs();
}

void snapshot(const uint8_t *rdram) {
    // The game's memory is big-endian bytes; RT64 reads each word byte-swapped (memory[i ^ 3]): one swap per word.
    for (uint32_t i = 0; i < snapshot_size; i += 4) {
        uint32_t word;
        std::memcpy(&word, rdram + i, 4);
        word = __builtin_bswap32(word);
        std::memcpy(memory.data() + i, &word, 4);
    }
}

int open(void *window, void *layer) {
    memory.reserve();
    if (!memory.base) { std::fprintf(stderr, "RT64 RDRAM mapping failed\n"); return 0; }
    enable_frame_readback(layer);
    RT64::Application::Core core{};
    core.window = {window, layer};
    core.HEADER = header.data();
    core.RDRAM = memory.data();
    core.DMEM = dmem.data();
    core.IMEM = imem.data();
    core.MI_INTR_REG = &interrupt;
    core.DPC_START_REG = &dpc[0];
    core.DPC_END_REG = &dpc[1];
    core.DPC_CURRENT_REG = &dpc[2];
    core.DPC_STATUS_REG = &dpc[3];
    core.DPC_CLOCK_REG = &dpc[4];
    core.DPC_BUFBUSY_REG = &dpc[5];
    core.DPC_PIPEBUSY_REG = &dpc[6];
    core.DPC_TMEM_REG = &dpc[7];
    auto *regs = vi();
    *regs = {0x3202, 0, 320, 2, 0, 0x03E52239, 0x20D, 0xC15, 0x0C150C15,
             0x006C02EC, 0x002501FF, 0x000E0204, 0x200, 0x400};
    vi_fields.fill({0, regs->VI_Y_SCALE_REG, regs->VI_V_START_REG, regs->VI_V_BURST_REG, regs->VI_INTR_REG});
    vertical_scale = 1.0f;
    has_framebuffer = false;
    core.VI_STATUS_REG = &regs->VI_STATUS_REG;
    core.VI_ORIGIN_REG = &regs->VI_ORIGIN_REG;
    core.VI_WIDTH_REG = &regs->VI_WIDTH_REG;
    core.VI_INTR_REG = &regs->VI_INTR_REG;
    core.VI_V_CURRENT_LINE_REG = &regs->VI_V_CURRENT_LINE_REG;
    core.VI_TIMING_REG = &regs->VI_TIMING_REG;
    core.VI_V_SYNC_REG = &regs->VI_V_SYNC_REG;
    core.VI_H_SYNC_REG = &regs->VI_H_SYNC_REG;
    core.VI_LEAP_REG = &regs->VI_LEAP_REG;
    core.VI_H_START_REG = &regs->VI_H_START_REG;
    core.VI_V_START_REG = &regs->VI_V_START_REG;
    core.VI_V_BURST_REG = &regs->VI_V_BURST_REG;
    core.VI_X_SCALE_REG = &regs->VI_X_SCALE_REG;
    core.VI_Y_SCALE_REG = &regs->VI_Y_SCALE_REG;
    core.checkInterrupts = [] {};
    RT64::ApplicationConfiguration config{};
    config.detectDataPath = config.useConfigurationFile = false;
    app = std::make_unique<RT64::Application>(core, config);
    app->userConfig.graphicsAPI = RT64::UserConfiguration::GraphicsAPI::Metal;
    const auto status = app->setup(0);
    if (status != RT64::Application::SetupResult::Success) {
        std::fprintf(stderr, "RT64 graphics setup failed (%d)\n", static_cast<int>(status));
        app.reset();
        return 0;
    }
    return 1;
}

void task(const uint8_t *rdram, uint32_t ucode, uint32_t ucode_data, uint32_t display_list) {
    ucode &= physical_mask;
    ucode_data &= physical_mask;
    display_list &= physical_mask;
    // Microcode detection reads up to 0x1000 bytes of code and 0x800 bytes of data.
    if (ucode > rdram_size - 0x1000 || ucode_data > rdram_size - 0x1000 ||
        display_list > rdram_size - 8 || display_list % 8)
        return;
    snapshot(rdram);
    app->state->rsp->reset();
    app->interpreter->loadUCodeGBI(ucode, ucode_data, true);
    // A microcode RT64's database does not know (a game's own Fast3D build, switched to for some tasks) gives no GBI,
    // or one with no commands: its display-list loop then reads on, past the list, until the host faults or hangs
    // (Glover, Frogger 2, Flying Dragon: the crash surfaced as a bogus game-thread trap, the picture stayed black).
    // The task is left to the host's own RDP, whose writes RT64 presents from game memory.
    const auto *gbi = app->interpreter->hleGBI;
    if (!gbi || gbi->ucode == RT64::GBIUCode::Unknown) {
        static std::vector<uint32_t> reported;  // each such microcode is named once, though a game may alternate two
        if (std::find(reported.begin(), reported.end(), ucode) == reported.end()) {
            reported.push_back(ucode);
            std::fprintf(stderr, "RT64 does not know the microcode at %08X: its tasks are drawn by the host's RDP\n", ucode);
        }
        return;
    }
    app->processDisplayLists(memory.data(), display_list, 0, true);
    static bool first = true;
    if (first) {
        std::fprintf(stderr, "RT64 received the game's first graphics task\n");
        first = false;
    }
}

void features(uint32_t flags) {
    vi()->VI_STATUS_REG = port_rt64_vi_features(vi()->VI_STATUS_REG, flags);
}

bool interlaced() {
    return (vi()->VI_STATUS_REG & PORT_RT64_VI_SERRATE) != 0;
}

void present(unsigned field) {
    auto *regs = vi();
    const auto &scan = vi_fields[field];
    if (scan.origin >= rdram_size - framebuffer) return;
    regs->VI_ORIGIN_REG = framebuffer + scan.origin;
    regs->VI_V_CURRENT_LINE_REG = field;
    regs->VI_Y_SCALE_REG = (scan.y_scale & ~0xFFFu) |
        std::min(0xFFFu, uint32_t((scan.y_scale & 0xFFFu) * vertical_scale));
    regs->VI_V_START_REG = scan.v_start;
    regs->VI_V_BURST_REG = scan.v_burst;
    regs->VI_INTR_REG = scan.interrupt;
    auto &queue = *app->presentQueue;
    const auto video = app->core.decodeVI();
    const auto *target = video.visible() ? app->state->framebufferManager.find(video.fbAddress()) : nullptr;
    bool use_rdram = false;
    if (target) {
        const auto size = video.fbSize();
        use_rdram = port_rt64_incomplete_framebuffer(
            target->width, target->height, target->siz, size.x, size.y, video.fbSiz());
    }
    if (use_rdram != queue.viewRDRAM) {
        // The VI scans a continuous range. A smaller cached GPU target can cover only the first part of it.
        // RT64 already presents RAM when it cannot find a GPU target. Select that path for this incomplete
        // target, draining pending presents before switching the queue's shared setting.
        queue.waitForPresentId(app->state->presentId);
        queue.waitForIdle();
        queue.viewRDRAM = use_rdram;
    }
    app->updateScreen();
    static bool first = true;
    if (first) {
        std::fprintf(stderr, "RT64 compatibility frame submitted\n");
        first = false;
    }
}

void screen(const uint8_t *rdram, uint32_t address) {
    address &= physical_mask;
    if (address >= rdram_size) return;
    framebuffer = address;
    has_framebuffer = true;
    snapshot(rdram);
    if (!interlaced()) present(0);
}

void retrace(unsigned field) {
    // Use the last snapshot taken on the game thread at a swap or graphics task. The host serializes
    // adapter calls, and the retrace thread never copies memory the game is writing into.
    if (has_framebuffer && interlaced()) present(field & 1);
}

void mode(const uint32_t *w) {
    auto *regs = vi();
    regs->VI_STATUS_REG = w[0];
    regs->VI_WIDTH_REG = w[1];
    regs->VI_TIMING_REG = w[2];
    regs->VI_V_SYNC_REG = w[3];
    regs->VI_H_SYNC_REG = w[4];
    regs->VI_LEAP_REG = w[5];
    regs->VI_H_START_REG = w[6];
    regs->VI_X_SCALE_REG = w[7];
    vi_fields = {port_rt64_vi_field(w, 0), port_rt64_vi_field(w, 1)};
    vertical_scale = 1.0f;
}

void yscale(float scale) {
    if (scale > 0 && scale <= 4)
        vertical_scale = scale;
}

void close() {
    app->end();
    app.reset();
}
}

extern "C" __attribute__((visibility("default"))) const PortRt64 *decompnet_rt64_adapter(void) {
    static const PortRt64 api = {PORT_RT64_ABI, open, task, screen, mode, yscale, close, features, retrace};
    return &api;
}
