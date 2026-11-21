/* RT64 renders the compatibility host's display lists. The host keeps its existing scheduler and software
 * RDP for writes into game memory; RT64 gets a separate, word-swapped view of that memory for presentation. */
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <memory>

#include "hle/rt64_application.h"
#include "ultramodern/ultramodern.hpp"
#include "lightweightsemaphore.h"
#include "adapter.h"
#include "presentation.h"

/* The compatibility host owns the game's threads. Only ultramodern's VI state is used by this adapter. */
std::atomic_bool exited{false};
moodycamel::LightweightSemaphore graphics_shutdown_ready;
bool ultramodern::is_game_started() { return false; }
void run_thread_function(uint8_t *, uint64_t, uint64_t, uint64_t) { std::abort(); }

namespace {
constexpr uint32_t rdram_size = 0x800000;
constexpr uint32_t physical_mask = 0x1FFFFFFF;
std::array<uint8_t, rdram_size> memory;
std::array<uint8_t, 0x1000> dmem, imem;
std::array<uint8_t, 0x40> header;
std::array<uint32_t, 8> dpc;
uint32_t interrupt, origin, base_y;
std::unique_ptr<RT64::Application> app;

auto *vi() {
    return ultramodern::renderer::get_vi_regs();
}

void snapshot(const uint8_t *rdram) {
    for (uint32_t i = 0; i < rdram_size; ++i)
        memory[i ^ 3] = rdram[i];
}

int open(void *window, void *layer) {
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
    base_y = regs->VI_Y_SCALE_REG;
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
    if (!app->interpreter->hleGBI) return;
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

void screen(const uint8_t *rdram, uint32_t framebuffer) {
    framebuffer &= physical_mask;
    if (framebuffer >= rdram_size || origin >= rdram_size - framebuffer)
        return;
    snapshot(rdram);
    vi()->VI_ORIGIN_REG = framebuffer + origin;
    app->updateScreen();
    static bool first = true;
    if (first) {
        std::fprintf(stderr, "RT64 compatibility frame submitted\n");
        first = false;
    }
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
    origin = w[9];
    base_y = regs->VI_Y_SCALE_REG = w[10];
    regs->VI_V_START_REG = w[11];
    regs->VI_V_BURST_REG = w[12];
    regs->VI_INTR_REG = w[13];
}

void yscale(float scale) {
    if (scale > 0 && scale <= 4)
        vi()->VI_Y_SCALE_REG = (base_y & ~0xFFF) | std::min(0xFFFu, uint32_t((base_y & 0xFFF) * scale));
}

void close() {
    app->end();
    app.reset();
}
}

extern "C" __attribute__((visibility("default"))) const PortRt64 *decompnet_rt64_adapter(void) {
    static const PortRt64 api = {PORT_RT64_ABI, open, task, screen, mode, yscale, close, features};
    return &api;
}
