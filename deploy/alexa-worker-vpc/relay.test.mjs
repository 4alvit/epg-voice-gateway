import assert from "node:assert/strict";
import test from "node:test";
import worker from "./relay.mjs";

const encoder = new TextEncoder();
const PUBLIC_HOST = "home-media.example.workers.dev";
const requestBody = '{ "request": {"type":"LaunchRequest"}, "text":"café Кукутики" }\n';
const responseBody = '{"version":"1.0","response":{"shouldEndSession":false}}';
const signature = "c3ludGhldGljLXNpZ25hdHVyZQ==";
const certificate = "https://s3.amazonaws.com/echo.api/echo-api-cert.pem";

function request(path = "/alexa", body = requestBody, extra = {}, options = {}) {
  return new Request(`https://${PUBLIC_HOST}${path}`, {
    method: "POST", body,
    headers: {
      "Content-Type": "application/json;charset=UTF-8",
      "Signature-256": signature, SignatureCertChainUrl: certificate, ...extra,
    },
    ...(body instanceof ReadableStream ? { duplex: "half" } : {}), ...options,
  });
}

function backend(handler = () => new Response(responseBody, { headers: { "Content-Type": "application/json" } })) {
  const calls = [];
  return { calls, env: { PUBLIC_HOST, BACKEND: { fetch: async (...args) => {
    calls.push(args);
    return handler(...args);
  } } } };
}

function streamChunks(...chunks) {
  return new ReadableStream({ start(controller) {
    for (const chunk of chunks) controller.enqueue(chunk);
    controller.close();
  } });
}

function assertHeaders(response) {
  assert.deepEqual(Object.fromEntries(response.headers), {
    "cache-control": "no-store", "content-type": "application/json",
    "referrer-policy": "no-referrer", "x-content-type-options": "nosniff",
  });
}

async function assertError(response, status) {
  assert.equal(response.status, status);
  assertHeaders(response);
  assert.deepEqual(await response.json(), {
    error: status === 404 ? "Not found" : status === 502 ? "Service unavailable" : "Invalid request",
  });
}

test("preserves signed UTF-8 bytes and only the supported gateway headers", async () => {
  const upstream = backend();
  const response = await worker.fetch(request("/alexa", requestBody, {
    Signature: "unsupported-sha1-signature", Authorization: "Bearer never-forward",
    Cookie: "session=never-forward", Forwarded: "for=spoofed;host=spoofed",
    "X-Forwarded-For": "192.0.2.1", "X-Forwarded-Host": "other.example.test",
    "X-Forwarded-Proto": "http", "CF-Connecting-IP": "192.0.2.2",
    "CF-Access-Client-Secret": "never-forward", "User-Agent": "private-client",
    "Content-Length": String(encoder.encode(requestBody).byteLength),
  }), upstream.env);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), responseBody);
  assertHeaders(response);
  assert.equal(upstream.calls.length, 1);
  const [url, init] = upstream.calls[0];
  assert.equal(url, "http://epg-voice-gateway/alexa");
  assert.equal(init.method, "POST");
  assert.equal(init.redirect, "manual");
  assert.ok(init.body instanceof ArrayBuffer);
  assert.deepEqual(new Uint8Array(init.body), encoder.encode(requestBody));
  assert.deepEqual(Object.fromEntries(init.headers), {
    "content-type": "application/json;charset=UTF-8",
    "signature-256": signature, signaturecertchainurl: certificate,
  });
});

for (const path of ["/", "/alexa/", "/ALexa", "/alexa?", "/alexa?query=x", "/alexa#",
  "/alexa#part", "/%61lexa", "/v1/play", "/v1/play-text", "/v1/search", "/v1/channels",
  "/media/token/stream.m3u8", "/healthz", "/auth/token", "/api/google_assistant"]) {
  test(`rejects unroutable path ${path} without a private request`, async () => {
    const upstream = backend();
    await assertError(await worker.fetch(request(path), upstream.env), 404);
    assert.equal(upstream.calls.length, 0);
  });
}

for (const method of ["GET", "HEAD", "PUT", "OPTIONS", "DELETE"]) {
  test(`rejects ${method} without reaching the gateway`, async () => {
    const upstream = backend();
    const input = new Request(`https://${PUBLIC_HOST}/alexa`, { method });
    await assertError(await worker.fetch(input, upstream.env), 404);
    assert.equal(upstream.calls.length, 0);
  });
}

for (const url of [`http://${PUBLIC_HOST}/alexa`, "https://other.example.test/alexa",
  `https://${PUBLIC_HOST}:8443/alexa`, `https://${PUBLIC_HOST}./alexa`]) {
  test(`rejects wrong scheme or authority ${url}`, async () => {
    const upstream = backend();
    const input = new Request(url, request());
    await assertError(await worker.fetch(input, upstream.env), 404);
    assert.equal(upstream.calls.length, 0);
  });
}

for (const host of [undefined, null, "", "UPPER.example", "https://example.test", "example.test:443",
  "example.test/path", "example.test.", "localhost", "a".repeat(64) + ".test", "bad_name.test"]) {
  test(`fails closed for invalid public host ${host}`, async () => {
    const upstream = backend();
    await assertError(await worker.fetch(request(), { ...upstream.env, PUBLIC_HOST: host }), 502);
    assert.equal(upstream.calls.length, 0);
  });
}

for (const header of ["Signature-256", "SignatureCertChainUrl"]) {
  for (const value of [null, "", " ", "x".repeat(8193), "internal\ttab"]) {
    test(`requires bounded ${header}: ${value === null ? "missing" : value.length}`, async () => {
      const upstream = backend();
      const input = request();
      if (value === null) input.headers.delete(header);
      else input.headers.set(header, value);
      input.headers.set("Signature", signature);
      await assertError(await worker.fetch(input, upstream.env), 400);
      assert.equal(upstream.calls.length, 0);
    });
  }
}

for (const headers of [
  { "Content-Type": "text/plain" }, { "Content-Type": "application/json" + " ".repeat(257) + ";x=y" },
  { "Content-Type": "application/json;\tcharset=utf-8" }, { "Content-Encoding": "gzip" },
  { "Content-Length": "-1" }, { "Content-Length": "01" }, { "Content-Length": "1, 1" },
  { "Content-Length": "2.0" }, { "Content-Length": "0" }, { "Content-Length": "1" },
]) {
  test(`rejects invalid request metadata ${JSON.stringify(headers)}`, async () => {
    const upstream = backend();
    await assertError(await worker.fetch(request("/alexa", requestBody, headers), upstream.env), 400);
    assert.equal(upstream.calls.length, 0);
  });
}

test("rejects an empty body and missing JSON Content-Type", async () => {
  const upstream = backend();
  await assertError(await worker.fetch(request("/alexa", ""), upstream.env), 400);
  const input = request();
  input.headers.delete("Content-Type");
  await assertError(await worker.fetch(input, upstream.env), 400);
  assert.equal(upstream.calls.length, 0);
});

test("accepts exact request bound without rewriting streamed signed bytes", async () => {
  const upstream = backend();
  const body = encoder.encode(JSON.stringify({ text: "x".repeat(32768 - 11) }));
  assert.equal(body.byteLength, 32768);
  const response = await worker.fetch(request("/alexa", streamChunks(body.slice(0, 5), body.slice(5))), upstream.env);
  assert.equal(response.status, 200);
  assert.deepEqual(new Uint8Array(upstream.calls[0][1].body), body);
});

test("rejects declared oversize before consuming the body", async () => {
  const upstream = backend();
  await assertError(await worker.fetch(request("/alexa", new ReadableStream(), {
    "Content-Length": "32769",
  }), upstream.env), 413);
  assert.equal(upstream.calls.length, 0);
});

test("enforces streamed size independently of a smaller declared length", async () => {
  const upstream = backend();
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(32769)); },
    cancel() { cancelled = true; },
  });
  await assertError(await worker.fetch(request("/alexa", body, { "Content-Length": "10" }), upstream.env), 413);
  assert.equal(cancelled, true);
  assert.equal(upstream.calls.length, 0);
});

test("caller routing hints cannot override the single fixed private target", async () => {
  const upstream = backend();
  const body = '{"url":"https://other.example.test","path":"/v1/play"}';
  const response = await worker.fetch(request("/alexa", body, {
    "Host": "other.example.test", "X-Original-URL": "/v1/play", "X-Rewrite-URL": "/media/test",
  }), { ...upstream.env, BACKEND_URL: "https://other.example.test", BACKEND_PATH: "/v1/play" });
  assert.equal(response.status, 200);
  assert.equal(upstream.calls[0][0], "http://epg-voice-gateway/alexa");
  assert.equal(upstream.calls[0][1].headers.has("Host"), false);
  assert.deepEqual(new Uint8Array(upstream.calls[0][1].body), encoder.encode(body));
});

for (const status of [301, 302, 303, 307, 308]) {
  test(`never follows or releases an upstream ${status} redirect`, async () => {
    const upstream = backend(() => new Response('{"private":"redirect"}', { status, headers: {
      "Content-Type": "application/json", Location: "https://other.example.test/private",
    } }));
    await assertError(await worker.fetch(request(), upstream.env), 502);
    assert.equal(upstream.calls.length, 1);
    assert.equal(upstream.calls[0][1].redirect, "manual");
  });
}

for (const status of [400, 401, 403, 404, 413, 429, 500, 503]) {
  test(`preserves JSON gateway status ${status} while dropping all origin headers`, async () => {
    const body = '{"error":"synthetic rejection"}';
    const upstream = backend(() => new Response(body, { status, headers: {
      "Content-Type": "application/json", "Set-Cookie": "private=value", Server: "private-origin",
      "Access-Control-Allow-Origin": "*", "WWW-Authenticate": "private-challenge",
      Location: "https://private.example.test", "X-Private-Header": "private",
    } }));
    const response = await worker.fetch(request(), upstream.env);
    assert.equal(response.status, status);
    assert.equal(await response.text(), body);
    assertHeaders(response);
  });
}

for (const [name, handler] of [
  ["HTML", () => new Response("<html>private challenge</html>", { status: 403 })],
  ["invalid JSON", () => new Response("{", { headers: { "Content-Type": "application/json" } })],
  ["JSON array", () => new Response("[]", { headers: { "Content-Type": "application/json" } })],
  ["JSON null", () => new Response("null", { headers: { "Content-Type": "application/json" } })],
  ["invalid UTF8", () => new Response(new Uint8Array([255]), { headers: { "Content-Type": "application/json" } })],
  ["empty204", () => new Response(null, { status: 204 })],
  ["not a response", () => ({ status: 200 })],
  ["exception", () => { throw new Error("private address and credentials"); }],
  ["large body", () => new Response(JSON.stringify({ text: "x".repeat(65536) }), { headers: { "Content-Type": "application/json" } })],
  ["large declared length", () => new Response("{}", { headers: { "Content-Type": "application/json", "Content-Length": "65537" } })],
  ["invalid declared length", () => new Response("{}", { headers: { "Content-Type": "application/json", "Content-Length": "wrong" } })],
  ["mismatched length", () => new Response("{}", { headers: { "Content-Type": "application/json", "Content-Length": "10" } })],
  ["compressed body", () => new Response("{}", { headers: { "Content-Type": "application/json", "Content-Encoding": "gzip" } })],
]) {
  test(`sanitizes upstream failure: ${name}`, async () => {
    const upstream = backend(handler);
    await assertError(await worker.fetch(request(), upstream.env), 502);
  });
}

test("returns exactly 64 KiB JSON unchanged", async () => {
  const body = JSON.stringify({ text: "x".repeat(65536 - 11) });
  assert.equal(encoder.encode(body).byteLength, 65536);
  const upstream = backend(() => new Response(body, { headers: { "Content-Type": "application/json" } }));
  const response = await worker.fetch(request(), upstream.env);
  assert.equal(await response.text(), body);
});

test("missing binding never falls back to public fetch", async (t) => {
  const fallback = t.mock.method(globalThis, "fetch", () => { throw new Error("Forbidden public fallback"); });
  for (const binding of [undefined, {}, { fetch: null }]) {
    await assertError(await worker.fetch(request(), { PUBLIC_HOST, BACKEND: binding }), 502);
  }
  assert.equal(fallback.mock.callCount(), 0);
});

test("seven-second deadline includes stalled caller bytes", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let cancelled = false;
  const upstream = backend();
  const pending = worker.fetch(request("/alexa", new ReadableStream({ cancel() { cancelled = true; } })), upstream.env);
  t.mock.timers.tick(7000);
  await assertError(await pending, 502);
  assert.equal(cancelled, true);
  assert.equal(upstream.calls.length, 0);
});

test("seven-second deadline bounds a binding that ignores cancellation", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let called;
  const started = new Promise(resolve => { called = resolve; });
  const upstream = backend(() => { called(); return new Promise(() => {}); });
  const pending = worker.fetch(request(), upstream.env);
  await started;
  t.mock.timers.tick(7000);
  await assertError(await pending, 502);
  assert.equal(upstream.calls[0][1].signal.aborted, true);
});

test("the same deadline cancels an upstream response stream", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let reading;
  const started = new Promise(resolve => { reading = resolve; });
  let cancelled = false;
  const upstream = backend(() => new Response(new ReadableStream({
    pull() { reading(); }, cancel() { cancelled = true; },
  }), { headers: { "Content-Type": "application/json" } }));
  const pending = worker.fetch(request(), upstream.env);
  await started;
  t.mock.timers.tick(7000);
  await assertError(await pending, 502);
  assert.equal(cancelled, true);
});
