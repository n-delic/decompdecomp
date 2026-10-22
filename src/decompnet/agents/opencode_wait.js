// #24: opencode 2 connects MCP servers in the background and lets the first model request go out before
// decompdecomp's tools are registered, so the model has to guess tool names. Wait once, before the first prompt,
// until the decompdecomp server reports connected and the tool registry's 100 ms debounce has passed.
// Remove this when opencode waits for MCP itself: upstream #51812 (the bug) and #54942 (the fix).
import { Plugin } from "@opencode/plugin"

const SERVER = __DECOMP_MCP_SERVER__
const DEADLINE_MS = __DECOMP_MCP_DEADLINE_MS__
const SETTLE_MS = __DECOMP_MCP_SETTLE_MS__
const POLL_MS = 50

export default Plugin.define({
  id: "decompdecomp-wait-for-mcp",
  setup: async (ctx) => {
    try {
      let waited = false
      await ctx.session.hook("prompt", async () => {
        if (waited) return
        waited = true
        try {
          let connected = false
          const deadline = Date.now() + DEADLINE_MS
          while (Date.now() < deadline) {
            try {
              const result = await ctx.mcp.list()
              const servers = Array.isArray(result) ? result : (result?.data ?? [])
              const target = servers.find((server) => server.name === SERVER)
              if (target?.status?.status === "connected") {
                connected = true
                break
              }
            } catch {
              // A poll that fails is no reason to fail the session: try again until the deadline.
            }
            await new Promise((resolve) => setTimeout(resolve, POLL_MS))
          }
          // ToolsChanged is debounced 100 ms: let the registry publish before the request's tool snapshot.
          if (connected) await new Promise((resolve) => setTimeout(resolve, SETTLE_MS))
        } catch {
          // #24: a helper must still run when this plugin cannot wait, exactly as opencode would without it.
        }
      })
    } catch {
      // #24: a helper must still run when this plugin cannot wait, exactly as opencode would without it.
    }
  },
})
