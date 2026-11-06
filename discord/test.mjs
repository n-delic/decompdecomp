// node --test discord/test.mjs
import assert from "node:assert/strict";
import test from "node:test";

import { form, issue } from "./src/index.js";

test("a report becomes an issue the bug form's way, with its labels", () => {
  const values = { title: "Import stops", version: "1.0.6", did: "Dropped the ISO", happened: "Error: x", expected: "" };
  const made = issue(form("ps1", "windows").custom_id, values, "someone");
  assert.equal(made.title, "Import stops");
  assert.deepEqual(made.labels, ["bug", "psx", "windows"]);
  assert.match(made.body, /### Console\n\nPS1/);
  assert.match(made.body, /### What you expected\n\n_No response_/);
  assert.match(made.body, /Reported on Discord by someone/);
  assert.deepEqual(issue("report:other:linux", values, "x").labels, ["bug", "linux"]);
});

test("only requests Discord signed get through", async () => {
  const { verified } = await import("./src/index.js");
  const { publicKey, privateKey } = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const hex = (b) => Buffer.from(b).toString("hex");
  const pub = hex(await crypto.subtle.exportKey("raw", publicKey));
  const body = '{"type":1}', timestamp = "1700000000";
  const sig = hex(await crypto.subtle.sign("Ed25519", privateKey, new TextEncoder().encode(timestamp + body)));
  const req = (s) => new Request("https://x", { method: "POST", headers: { "x-signature-ed25519": s, "x-signature-timestamp": timestamp } });
  assert.equal(await verified(req(sig), body, pub), true);
  assert.equal(await verified(req(sig.replace(/^./, sig[0] === "0" ? "1" : "0")), body, pub), false);
});

test("a malformed signature or key is refused, not a crash", async () => {
  const { verified } = await import("./src/index.js");
  const req = new Request("https://x", { method: "POST", headers: { "x-signature-ed25519": "00", "x-signature-timestamp": "1" } });
  assert.equal(await verified(req, "{}", "zz"), false);
});
