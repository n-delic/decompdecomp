// /report on the decompdecomp Discord: a Cloudflare Worker that Discord calls for each interaction (no bot process
// to keep running). The command asks the console and system, a form asks the rest, and the answers become a GitHub
// issue with the labels the issue forms would have given it.

const PING = 1, COMMAND = 2, MODAL_SUBMIT = 5;
const PONG = 1, MESSAGE = 4, MODAL = 9;
const EPHEMERAL = 64;

// The command's choices (register.mjs offers the same) and the GitHub labels each gives
export const CONSOLES = { n64: "Nintendo 64", ps1: "PS1", ps2: "PS2", psp: "PSP", gc: "GameCube", wii: "Wii",
                          pc: "Windows PC", ds: "Nintendo DS", other: "Other / not about one console" };
const CONSOLE_LABELS = { n64: "n64", ps1: "psx", ps2: "ps2", psp: "psp", gc: "gc", wii: "wii", pc: "pc", ds: "ds" };
export const SYSTEMS = { linux: "Linux", windows: "Windows", macos: "macOS" };
const SYSTEM_LABELS = { linux: "linux", windows: "windows", macos: "macos" };

const field = (id, label, style, required, max, placeholder) => ({
  type: 1, components: [{ type: 4, custom_id: id, label, style, required, max_length: max, placeholder }] });

export function form(consoleId, system) {
  return {
    custom_id: `report:${consoleId}:${system}`,
    title: "Report a bug in decompdecomp",
    components: [
      field("title", "Short title", 1, true, 100, "Import of a PS2 disc stops at splitting"),
      field("version", "Version (bottom left of the app)", 1, true, 40, "1.0.6-alpha.4"),
      field("did", "What you did", 2, true, 1000),
      field("happened", "What happened (with the error text)", 2, true, 2000),
      field("expected", "What you expected", 2, false, 1000),
    ],
  };
}

// The issue a submitted form makes: the bug form's sections, so labels.yml and people read it the same way
export function issue(customId, values, user) {
  const [, consoleId, system] = customId.split(":");
  const labels = ["bug", CONSOLE_LABELS[consoleId], SYSTEM_LABELS[system]].filter(Boolean);
  const body = [
    `### Console\n\n${CONSOLES[consoleId] ?? "_No response_"}`,
    `### Your system\n\n${SYSTEMS[system] ?? "_No response_"}`,
    `### Version\n\n${values.version}`,
    `### What you did\n\n${values.did}`,
    `### What you expected\n\n${values.expected || "_No response_"}`,
    `### What happened\n\n${values.happened}`,
    `---\nReported on Discord by ${user}.`,
  ].join("\n\n");
  return { title: values.title, body, labels };
}

function hex(s) {
  return Uint8Array.from(s.match(/../g).map((b) => parseInt(b, 16)));
}

export async function verified(request, body, publicKey) {
  const signature = request.headers.get("x-signature-ed25519");
  const timestamp = request.headers.get("x-signature-timestamp");
  if (!signature || !timestamp) return false;
  try {  // a malformed signature is refused, not a crash: Discord tests the endpoint with one
    const key = await crypto.subtle.importKey("raw", hex(publicKey), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify("Ed25519", key, hex(signature), new TextEncoder().encode(timestamp + body));
  } catch {
    return false;
  }
}

const reply = (data) => new Response(JSON.stringify(data), { headers: { "content-type": "application/json" } });
const say = (content) => reply({ type: MESSAGE, data: { content, flags: EPHEMERAL } });

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("decompdecomp's Discord interactions endpoint", { status: 405 });
    const body = await request.text();
    // a key pasted with a space or newline around it, or not set: say which, the dashboard is where it's fixed
    const publicKey = (env.DISCORD_PUBLIC_KEY ?? "").trim();
    if (!/^[0-9a-f]{64}$/i.test(publicKey)) {
      return new Response("DISCORD_PUBLIC_KEY isn't set to the Discord app's Public Key (64 hex digits)", { status: 500 });
    }
    // Discord checks the endpoint rejects bad signatures before it accepts it, and anyone can POST here
    if (!(await verified(request, body, publicKey))) return new Response("bad signature", { status: 401 });
    const interaction = JSON.parse(body);

    if (interaction.type === PING) return reply({ type: PONG });
    if (interaction.type === COMMAND && interaction.data.name === "report") {
      const option = (name) => interaction.data.options?.find((o) => o.name === name)?.value ?? "";
      return reply({ type: MODAL, data: form(option("console"), option("system")) });
    }
    if (interaction.type === MODAL_SUBMIT && interaction.data.custom_id.startsWith("report:")) {
      const values = Object.fromEntries(interaction.data.components.map((row) => row.components[0])
        .map((c) => [c.custom_id, c.value.trim()]));
      const user = (interaction.member?.user ?? interaction.user)?.username ?? "someone";
      const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/issues`, {
        method: "POST",
        headers: { authorization: `Bearer ${env.GITHUB_TOKEN}`, accept: "application/vnd.github+json",
                   "user-agent": "decompdecomp-discord", "content-type": "application/json" },
        body: JSON.stringify(issue(interaction.data.custom_id, values, user)),
      });
      if (!res.ok) return say(`Couldn't open the issue (GitHub said ${res.status}). Post it in #bug-reports instead.`);
      const made = await res.json();
      return say(`Thanks! Opened issue #${made.number}: ${made.html_url}`);
    }
    return say("I don't know that one.");
  },
};
