import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import http from "node:http";
import { after, before, test } from "node:test";

let hits = 0;
const upstream = http.createServer((request, response) => {
  hits++;
  if (request.url === "/external") response.writeHead(302, { location: "http://127.0.0.1:3189/" }).end();
  else if (request.url === "/redirect") response.writeHead(307, { location: "/target" }).end();
  else response.writeHead(200).end("ok");
});
let proxy;
before(async () => {
  await new Promise(resolve => upstream.listen(3186, "127.0.0.1", resolve));
  proxy = spawn(process.execPath, ["scripts/connect-focus-proxy.mjs"], { stdio: "ignore", env: {} });
  for (let i = 0; i < 100; i++) {
    try { await fetch("http://127.0.0.1:3187/"); return; } catch { await new Promise(resolve => setTimeout(resolve, 20)); }
  }
  throw new Error("Proxy did not start");
});
after(async () => {
  if (proxy && proxy.exitCode === null) {
    const exited = new Promise(resolve => proxy.once("exit", resolve));
    proxy.kill();
    await exited;
  }
  await new Promise(resolve => upstream.close(resolve));
});
test("external redirect is rejected before reaching the client", async () => {
  const response = await fetch("http://127.0.0.1:3187/external", { redirect: "manual" });
  assert.equal(response.status, 502);
  assert.equal(response.headers.get("location"), null);
});
test("307 target cannot receive POST", async () => {
  const redirect = await fetch("http://127.0.0.1:3187/redirect", { redirect: "manual" });
  assert.equal(redirect.status, 307);
  const beforePost = hits;
  const post = await fetch("http://127.0.0.1:3187/target", { method: "POST" });
  assert.equal(post.status, 403);
  assert.equal(hits, beforePost);
});
