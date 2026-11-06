// Registers /report with Discord (run once, and again when its options change):
//   DISCORD_APP_ID=... DISCORD_BOT_TOKEN=... node register.mjs
import { CONSOLES, SYSTEMS } from "./src/index.js";

const { DISCORD_APP_ID: app, DISCORD_BOT_TOKEN: token } = process.env;
if (!app || !token) throw new Error("set DISCORD_APP_ID and DISCORD_BOT_TOKEN (the Discord app's ID and bot token)");

const choices = (map) => Object.entries(map).map(([value, name]) => ({ name, value }));
const command = {
  name: "report",
  description: "Report a bug in decompdecomp: it becomes a GitHub issue",
  options: [
    { type: 3, name: "console", description: "The console of the game", required: true, choices: choices(CONSOLES) },
    { type: 3, name: "system", description: "Your computer's system", required: true, choices: choices(SYSTEMS) },
  ],
};

const res = await fetch(`https://discord.com/api/v10/applications/${app}/commands`, {
  method: "PUT",
  headers: { authorization: `Bot ${token}`, "content-type": "application/json" },
  body: JSON.stringify([command]),
});
console.log(res.ok ? "registered /report" : `Discord said ${res.status}: ${await res.text()}`);
