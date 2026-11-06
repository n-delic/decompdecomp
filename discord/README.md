# /report on Discord

`/report` on the decompdecomp Discord asks for the console and system, opens a form, and turns the answers into a
GitHub issue with the bug form's sections and labels. It's a Cloudflare Worker that Discord calls for each command
(`src/index.js`), so no bot has to keep running.

## Set it up (once)

1. **Discord app.** At https://discord.com/developers/applications, New Application. From General Information copy
   the **Application ID** and **Public Key**; under Bot, Reset Token and copy the **bot token** (only `register.mjs`
   uses it). Under Installation, add it to the server with the `applications.commands` scope.
2. **GitHub token.** At https://github.com/settings/personal-access-tokens, a fine-grained token for
   `n-delic/decompdecomp` only, with Issues: Read and write.
3. **Deploy the Worker**, from this folder:
   ```bash
   npx wrangler deploy
   npx wrangler secret put DISCORD_PUBLIC_KEY   # the Public Key
   npx wrangler secret put GITHUB_TOKEN          # the GitHub token
   ```
   It prints the Worker's URL (`https://decompdecomp-discord.<you>.workers.dev`).
4. **Point Discord at it.** In the app's General Information, set Interactions Endpoint URL to that URL and save
   (Discord checks it answers; it refuses requests without Discord's signature).
5. **Register the command:**
   ```bash
   DISCORD_APP_ID=... DISCORD_BOT_TOKEN=... node register.mjs
   ```

Tests: `node --test test.mjs`.
