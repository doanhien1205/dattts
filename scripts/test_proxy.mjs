#!/usr/bin/env node
// Kiểm thử Pages Function functions/api/[[path]].js bằng Node 22 (Request/Response/fetch toàn cục),
// chạy với một máy chủ HTTP "echo" tại chỗ thay cho Modal. Không cần wrangler hay mạng.
//
//   node scripts/test_proxy.mjs
import http from "node:http";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const mod = await import(pathToFileURL(path.join(ROOT, "functions", "api", "[[path]].js")).href);
const { onRequest } = mod;

const SECRET = "test-secret-" + "z".repeat(32);
const FILE = Buffer.from(Array.from({ length: 1000 }, (_, i) => i % 256));
let lastSeen = null;
let releaseSlow = null;
const longSeen = [];

const server = http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks);
  const u = new URL(req.url, "http://x");
  lastSeen = { method: req.method, url: req.url, headers: req.headers, body };

  if (u.pathname === "/api/files/abc.mp3") {
    const common = { "content-type": "audio/mpeg", "accept-ranges": "bytes",
      "cache-control": "public, max-age=31536000, immutable", "content-disposition": 'inline; filename="abc.mp3"' };
    const m = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || "");
    if (m) {
      const start = +m[1], end = m[2] ? +m[2] : FILE.length - 1;
      res.writeHead(206, { ...common, "content-range": `bytes ${start}-${end}/${FILE.length}`,
        "content-length": end - start + 1 });
      return res.end(req.method === "HEAD" ? undefined : FILE.subarray(start, end + 1));
    }
    res.writeHead(200, { ...common, "content-length": FILE.length });
    return res.end(req.method === "HEAD" ? undefined : FILE);
  }
  if (u.pathname === "/api/slow") {
    res.writeHead(200, { "content-type": "text/plain" });
    res.write("first;");
    releaseSlow = () => res.end("second");
    return;
  }
  if (u.pathname === "/api/redirect") {
    res.writeHead(302, { location: "/api/elsewhere" });
    return res.end();
  }
  if (u.pathname === "/api/longtts") {
    // Giả lập Modal: quá 150 s → 303 về chính URL kèm mã lời gọi; lần hỏi thứ 2 mới có kết quả.
    const n = Number(u.searchParams.get("__modal_function_call_id") ? u.searchParams.get("n") : 0);
    longSeen.push({ method: req.method, url: req.url, secret: req.headers["x-dattts-proxy-secret"],
      ip: req.headers["x-dattts-client-ip"], len: body.length });
    if (n < 2) {
      res.writeHead(303, { location: `${ORIGIN}/api/longtts?__modal_function_call_id=fc-1&n=${n + 1}` });
      return res.end();
    }
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ done: true }));
  }
  if (u.pathname === "/api/loop303") {
    res.writeHead(303, { location: "/api/loop303?__modal_function_call_id=x" });
    return res.end();
  }
  if (u.pathname === "/api/nocontent") {
    res.writeHead(204);
    return res.end();
  }
  if (u.pathname === "/api/cookies") {
    res.setHeader("set-cookie", ["a=1; Path=/", "b=2; Path=/"]);
    res.setHeader("connection", "keep-alive, x-private-hop");
    res.setHeader("x-private-hop", "secret-hop");
    res.setHeader("keep-alive", "timeout=5");
    res.writeHead(200, { "content-type": "text/plain" });
    return res.end("ok");
  }
  if (req.method === "OPTIONS") {
    res.writeHead(200, { "access-control-allow-origin": req.headers.origin || "*",
      "access-control-allow-methods": "GET, POST", vary: "Origin" });
    return res.end();
  }
  const status = Number(u.searchParams.get("status") || 200);
  res.writeHead(status, { "content-type": "application/json", vary: "Accept-Language",
    "x-echo-sha": createHash("sha256").update(body).digest("hex") });
  res.end(JSON.stringify({ method: req.method, url: req.url, len: body.length }));
});
server.listen(0, "127.0.0.1");
await once(server, "listening");
const ORIGIN = `http://127.0.0.1:${server.address().port}`;
const ENV = { MODAL_ORIGIN: ORIGIN + "/", PROXY_SHARED_SECRET: SECRET };

function call(url, init = {}, env = ENV) {
  return onRequest({ request: new Request("https://dattts.com" + url, init), env, params: {}, waitUntil() {} });
}

let passed = 0, failed = 0;
async function test(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`ok   ${name}`);
  } catch (e) {
    failed++;
    console.log(`FAIL ${name}\n     ${e && e.stack || e}`);
  }
}

await test("GET: path + query + status/header/body giữ nguyên", async () => {
  const r = await call("/api/voices?x=1&y=%C4%91", { headers: { "accept-language": "vi" } });
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("vary"), "Accept-Language");
  const j = await r.json();
  assert.deepEqual(j, { method: "GET", url: "/api/voices?x=1&y=%C4%91", len: 0 });
  assert.equal(lastSeen.headers["accept-language"], "vi");
});

await test("thêm secret + IP khách; xoá X-DatTTS-* khách gửi, host, hop-by-hop, cf-*", async () => {
  await call("/api/quota", { headers: {
    "cf-connecting-ip": "203.0.113.7",
    "cf-ray": "abc",
    "x-dattts-proxy-secret": "spoofed",
    "x-DatTTS-client-ip": "6.6.6.6",
    "x-dattts-other": "zzz",
    "x-api-key": "DATTTS-KEY",
    "authorization": "Bearer t",
    "connection": "x-drop-me",
    "x-drop-me": "1",
    "te": "trailers",
  } });
  const h = lastSeen.headers;
  assert.equal(h["x-dattts-proxy-secret"], SECRET);
  assert.equal(h["x-dattts-client-ip"], "203.0.113.7");
  assert.equal(h["x-dattts-other"], undefined);
  assert.equal(h["x-api-key"], "DATTTS-KEY");
  assert.equal(h["authorization"], "Bearer t");
  assert.equal(h["x-drop-me"], undefined);
  assert.equal(h["te"], undefined);
  assert.equal(h["cf-ray"], undefined);
  assert.equal(h["cf-connecting-ip"], undefined);
  assert.equal(h.host, new URL(ORIGIN).host);
});

await test("không có CF-Connecting-IP → không gửi X-DatTTS-Client-IP (khách không giả được)", async () => {
  await call("/api/quota", { headers: { "x-dattts-client-ip": "6.6.6.6" } });
  assert.equal(lastSeen.headers["x-dattts-client-ip"], undefined);
  assert.equal(lastSeen.headers["x-dattts-proxy-secret"], SECRET);
});

await test("POST body thô (byte không phải UTF-8) đi qua nguyên vẹn — chữ ký webhook", async () => {
  const raw = Buffer.concat([Buffer.from('{"type":"order.paid","x":"'), Buffer.from([0xff, 0xfe, 0x00, 0xc3]),
    Buffer.from('"}\r\n  ')]);
  const r = await call("/api/billing/webhook", { method: "POST", body: raw,
    headers: { "content-type": "application/json", "webhook-signature": "v1,abc", "webhook-id": "m1" } });
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("x-echo-sha"), createHash("sha256").update(raw).digest("hex"));
  assert.ok(lastSeen.body.equals(raw));
  assert.equal(lastSeen.headers["webhook-signature"], "v1,abc");
  assert.equal(lastSeen.headers["content-type"], "application/json");
});

await test("POST body dạng stream (ReadableStream) được chuyển dạng stream", async () => {
  const parts = ["Xin ", "chào ", "Việt Nam"].map((s) => new TextEncoder().encode(s));
  const stream = new ReadableStream({ pull(c) { const p = parts.shift(); p ? c.enqueue(p) : c.close(); } });
  const r = await call("/api/tts", { method: "POST", body: stream, duplex: "half",
    headers: { "content-type": "text/plain" } });
  assert.equal(r.status, 200);
  assert.equal(lastSeen.body.toString("utf8"), "Xin chào Việt Nam");
});

await test("status lỗi của gốc (429) giữ nguyên", async () => {
  const r = await call("/api/tts?status=429", { method: "POST", body: "{}" });
  assert.equal(r.status, 429);
});

await test("Range → 206 + Content-Range + byte đúng, Cache-Control immutable giữ nguyên", async () => {
  const r = await call("/api/files/abc.mp3", { headers: { range: "bytes=10-19" } });
  assert.equal(r.status, 206);
  assert.equal(lastSeen.headers.range, "bytes=10-19");
  assert.equal(r.headers.get("content-range"), "bytes 10-19/1000");
  assert.equal(r.headers.get("accept-ranges"), "bytes");
  assert.equal(r.headers.get("cache-control"), "public, max-age=31536000, immutable");
  assert.equal(r.headers.get("content-disposition"), 'inline; filename="abc.mp3"');
  const buf = Buffer.from(await r.arrayBuffer());
  assert.ok(buf.equals(FILE.subarray(10, 20)));
});

await test("HEAD: không body, header giữ nguyên", async () => {
  const r = await call("/api/files/abc.mp3", { method: "HEAD" });
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("content-length"), "1000");
  assert.equal(r.body, null);
});

await test("204 không body", async () => {
  const r = await call("/api/nocontent", { method: "DELETE" });
  assert.equal(r.status, 204);
  assert.equal(r.body, null);
});

await test("phản hồi stream: nhận phần đầu trước khi gốc kết thúc", async () => {
  const r = await call("/api/slow");
  const reader = r.body.getReader();
  const first = await Promise.race([
    reader.read(),
    new Promise((_, rej) => setTimeout(() => rej(new Error("không stream: chờ quá 2 s")), 2000)),
  ]);
  assert.equal(new TextDecoder().decode(first.value), "first;");
  releaseSlow();
  let rest = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    rest += new TextDecoder().decode(value);
  }
  assert.equal(rest, "second");
});

await test("3xx không tự theo (redirect: manual)", async () => {
  const r = await call("/api/redirect");
  assert.equal(r.status, 302);
  assert.equal(r.headers.get("location"), "/api/elsewhere");
});

await test("303 của Modal (cùng origin + path) → proxy tự hỏi lại bằng GET, giữ secret/IP", async () => {
  longSeen.length = 0;
  const r = await call("/api/longtts", { method: "POST", body: JSON.stringify({ text: "x".repeat(50) }),
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.7" } });
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { done: true });
  assert.equal(longSeen.length, 3);
  assert.equal(longSeen[0].method, "POST");
  for (const s of longSeen.slice(1)) {
    assert.equal(s.method, "GET");
    assert.equal(s.len, 0);
    assert.match(s.url, /__modal_function_call_id=fc-1/);
  }
  for (const s of longSeen) { assert.equal(s.secret, SECRET); assert.equal(s.ip, "203.0.113.7"); }
});

await test("303 lặp mãi → dừng sau MODAL_MAX_FOLLOW lần, trả 303 cuối", async () => {
  const r = await call("/api/loop303");
  assert.equal(r.status, 303);
  assert.ok(mod.MODAL_MAX_FOLLOW >= 4);
});

await test("OPTIONS preflight được chuyển tới gốc (CORS do backend xử lý)", async () => {
  const r = await call("/api/tts", { method: "OPTIONS",
    headers: { origin: "https://dattts.com", "access-control-request-method": "POST" } });
  assert.equal(lastSeen.method, "OPTIONS");
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("access-control-allow-origin"), "https://dattts.com");
  assert.equal(lastSeen.headers["x-dattts-proxy-secret"], SECRET);
});

await test("phản hồi: bỏ hop-by-hop, giữ nhiều Set-Cookie", async () => {
  const r = await call("/api/cookies");
  assert.equal(r.headers.get("x-private-hop"), null);
  assert.equal(r.headers.get("keep-alive"), null);
  assert.equal(r.headers.get("connection"), null);
  assert.deepEqual(r.headers.getSetCookie(), ["a=1; Path=/", "b=2; Path=/"]);
});

await test("Modal không kết nối được → 502 upstream_unavailable (EN mặc định, VI theo Accept-Language)", async () => {
  const dead = http.createServer();
  dead.listen(0, "127.0.0.1");
  await once(dead, "listening");
  const port = dead.address().port;
  dead.close();
  await once(dead, "close");
  const env = { MODAL_ORIGIN: `http://127.0.0.1:${port}`, PROXY_SHARED_SECRET: SECRET };
  let r = await call("/api/health", {}, env);
  assert.equal(r.status, 502);
  assert.equal(r.headers.get("cache-control"), "no-store");
  let j = await r.json();
  assert.equal(j.error.code, "upstream_unavailable");
  assert.match(j.error.message, /temporarily unavailable/);
  r = await call("/api/health", { headers: { "accept-language": "vi-VN,vi;q=0.9,en;q=0.8" } }, env);
  j = await r.json();
  assert.equal(j.error.code, "upstream_unavailable");
  assert.match(j.error.message, /tạm thời/);
});

await test("thiếu MODAL_ORIGIN / PROXY_SHARED_SECRET hoặc origin hỏng → 502, không gọi đi đâu", async () => {
  for (const env of [{}, { MODAL_ORIGIN: ORIGIN }, { PROXY_SHARED_SECRET: SECRET },
    { MODAL_ORIGIN: "not a url", PROXY_SHARED_SECRET: SECRET },
    { MODAL_ORIGIN: "ftp://x", PROXY_SHARED_SECRET: SECRET }]) {
    lastSeen = null;
    const r = await call("/api/health", {}, env);
    assert.equal(r.status, 502);
    assert.equal((await r.json()).error.code, "upstream_unavailable");
    assert.equal(lastSeen, null);
  }
});

await test("MODAL_ORIGIN có path/dấu / thừa → chỉ dùng origin", async () => {
  const r = await call("/api/voices", {}, { MODAL_ORIGIN: ORIGIN + "/ignored/path/", PROXY_SHARED_SECRET: SECRET });
  assert.equal(r.status, 200);
  assert.equal(lastSeen.url, "/api/voices");
});

server.close();
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
