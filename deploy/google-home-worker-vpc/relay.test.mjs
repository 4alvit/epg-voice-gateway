import assert from "node:assert/strict";
import test from "node:test";
import worker from "./relay.mjs";

const encoder = new TextEncoder();
const PUBLIC_HOST = "home-media.example.workers.dev";
const tokenBody = "grant_type=authorization_code&code=synthetic%2Bcode&client_id=https%3A%2F%2Foauth-redirect.googleusercontent.com%2Fr%2Fexample&redirect_uri=https%3A%2F%2Fexample.test%2Fcallback";
const syncBody = '{ "requestId": "synthetic", "inputs": [{"intent":"action.devices.SYNC"}], "text":"café" }\n';
const tokenResponse = '{"access_token":"synthetic-token","token_type":"Bearer","expires_in":1800}';

function request(path = "/auth/token", body = tokenBody, extraHeaders = {}, options = {}) {
  const headers = new Headers({
    "Content-Type": path === "/api/google_assistant" ? "application/json;charset=UTF-8"
      : "application/x-www-form-urlencoded;charset=UTF-8",
    ...(path === "/api/google_assistant" ? { Authorization: "Bearer synthetic.access.token" } : {}),
    ...extraHeaders,
  });
  return new Request(`https://${PUBLIC_HOST}${path}`, {
    method: "POST", headers, body,
    ...(body instanceof ReadableStream ? { duplex: "half" } : {}),
    ...options,
  });
}

function backend(handler = () => new Response(tokenResponse, { headers: { "Content-Type": "application/json" } })) {
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

async function assertError(response, status) {
  assert.equal(response.status, status);
  assert.deepEqual(Object.fromEntries(response.headers), {
    "cache-control": "no-store", "content-type": "application/json",
  });
  assert.deepEqual(await response.json(), {
    error: status === 404 ? "Not found" : status === 502 ? "Service unavailable" : "Invalid request",
  });
}

test("preserves form bytes and only Content-Type through the fixed private token binding", async () => {
  const upstream = backend();
  const response = await worker.fetch(request("/auth/token", tokenBody, {
    Authorization: "Basic never-forward",
    Cookie: "session=never-forward",
    Forwarded: "for=spoofed;host=spoofed",
    "X-Forwarded-For": "192.0.2.1",
    "X-Forwarded-Host": "spoofed.example",
    "X-Forwarded-Proto": "http",
    "CF-Connecting-IP": "192.0.2.2",
    "CF-Access-Client-Secret": "never-forward",
    "User-Agent": "private-client",
    "Content-Length": String(encoder.encode(tokenBody).byteLength),
  }), upstream.env);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), tokenResponse);
  assert.equal(upstream.calls.length, 1);
  const [url, init] = upstream.calls[0];
  assert.equal(url, "http://home-assistant/auth/token");
  assert.equal(init.method, "POST");
  assert.equal(init.redirect, "manual");
  assert.ok(init.body instanceof ArrayBuffer);
  assert.deepEqual(new Uint8Array(init.body), encoder.encode(tokenBody));
  assert.deepEqual(Object.fromEntries(init.headers), {
    "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
  });
  assert.equal(init.signal.aborted, false);
});

test("forwards authenticated SYNC bytes and only the two allowed fulfillment headers", async () => {
  const syncResponse = '{"requestId":"synthetic","payload":{"agentUserId":"synthetic-user","devices":[]}}';
  const upstream = backend(() => new Response(syncResponse, { headers: { "Content-Type": "application/json" } }));
  const response = await worker.fetch(request("/api/google_assistant", syncBody, {
    Cookie: "never-forward", Forwarded: "for=spoofed", "X-Forwarded-For": "192.0.2.1",
  }), upstream.env);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), syncResponse);
  assert.equal(upstream.calls.length, 1);
  const [url, init] = upstream.calls[0];
  assert.equal(url, "http://home-assistant/api/google_assistant");
  assert.deepEqual(new Uint8Array(init.body), encoder.encode(syncBody));
  assert.deepEqual(Object.fromEntries(init.headers), {
    authorization: "Bearer synthetic.access.token", "content-type": "application/json;charset=UTF-8",
  });
});

for (const [status, type, body] of [
  [401, "text/plain", "401: Unauthorized"],
  [403, "text/html", "<html>Private rejection details</html>"],
]) {
  test(`preserves non-JSON fulfillment ${status} as a safe JSON authentication boundary`, async () => {
    const upstream = backend(() => new Response(body, { status, headers: {
      "Content-Type": type, "Set-Cookie": "private=value", Server: "private-origin",
    } }));
    await assertError(await worker.fetch(request("/api/google_assistant", syncBody), upstream.env), status);
    assert.equal(upstream.calls.length, 1);
  });
}

for (const status of [400, 401, 403, 429, 500, 503]) {
  test(`preserves upstream JSON status ${status} and strips private response headers`, async () => {
    const body = '{ "error": "invalid_grant", "error_description": "Synthetic error" }\n';
    const upstream = backend(() => new Response(body, { status, headers: {
      "Content-Type": "application/json;charset=UTF-8", "Cache-Control": "public, max-age=3600",
      "Set-Cookie": "private=value", Location: "https://private.example", Server: "private-origin",
    } }));
    const response = await worker.fetch(request(), upstream.env);
    assert.equal(response.status, status);
    assert.equal(await response.text(), body);
    assert.deepEqual(Object.fromEntries(response.headers), {
      "cache-control": "no-store", "content-type": "application/json",
    });
  });
}

for (const [method, path] of [
  ["GET", "/auth/token"], ["HEAD", "/auth/token"], ["OPTIONS", "/auth/token"],
  ["PUT", "/api/google_assistant"], ["GET", "/api/google_assistant"],
  ["POST", "/"], ["POST", "/auth/authorize"], ["POST", "/auth/token/"],
  ["POST", "/auth/token?"], ["POST", "/auth/token?url=https://other.example"],
  ["POST", "/api/google_assistant?access_token=synthetic"],
  ["POST", "/%61uth/token"], ["POST", "/auth//token"], ["POST", "/auth/token#fragment"],
]) {
  test(`rejects ${method} ${path} without contacting the backend`, async () => {
    const upstream = backend();
    await assertError(await worker.fetch(new Request(`https://${PUBLIC_HOST}${path}`, { method }), upstream.env), 404);
    assert.equal(upstream.calls.length, 0);
  });
}

for (const origin of ["http://home-media.example.workers.dev", "https://other.example", "https://home-media.example.workers.dev:8443"]) {
  test(`rejects an unexpected public origin ${origin}`, async () => {
    const upstream = backend();
    const input = new Request(`${origin}/auth/token`, { method: "POST", body: tokenBody });
    await assertError(await worker.fetch(input, upstream.env), 404);
    assert.equal(upstream.calls.length, 0);
  });
}

for (const value of [undefined, "", "https://home-media.example.workers.dev", "host:443", "host/path", "UPPER.example"]) {
  test(`fails closed for invalid PUBLIC_HOST ${value}`, async () => {
    const upstream = backend();
    upstream.env.PUBLIC_HOST = value;
    await assertError(await worker.fetch(request(), upstream.env), 502);
    assert.equal(upstream.calls.length, 0);
  });
}

for (const headers of [
  { "Content-Type": "application/json" }, { "Content-Type": "text/plain" },
  { "Content-Type": "application/x-www-form-urlencoded;" + "x".repeat(256) },
  { "Content-Encoding": "gzip" }, { "Content-Length": "-1" },
  { "Content-Length": "not-a-number" }, { "Content-Length": "01" },
  { "Content-Length": "0" }, { "Content-Length": "2" },
]) {
  test(`rejects invalid request header ${Object.keys(headers)[0]}: ${Object.values(headers)[0].slice(0, 45)}`, async () => {
    const upstream = backend();
    await assertError(await worker.fetch(request("/auth/token", tokenBody, headers), upstream.env), 400);
    assert.equal(upstream.calls.length, 0);
  });
}

test("requires form Content-Type and a nonempty request body", async () => {
  for (const input of [request("/auth/token", ""), request()]) {
    const upstream = backend();
    if (input.body !== null && input.headers.has("Content-Type")) input.headers.delete("Content-Type");
    await assertError(await worker.fetch(input, upstream.env), 400);
    assert.equal(upstream.calls.length, 0);
  }
  const upstream = backend();
  await assertError(await worker.fetch(request("/auth/token", ""), upstream.env), 400);
  assert.equal(upstream.calls.length, 0);
});

test("requires JSON Content-Type for fulfillment", async () => {
  const upstream = backend();
  await assertError(await worker.fetch(request("/api/google_assistant", syncBody, {
    "Content-Type": "application/x-www-form-urlencoded",
  }), upstream.env), 400);
  assert.equal(upstream.calls.length, 0);
});

for (const authorization of [null, "", "Basic synthetic", "Bearer two tokens", "Bearer token,other", `Bearer ${"x".repeat(8192)}`]) {
  test(`rejects missing or invalid fulfillment bearer: ${authorization?.slice(0, 24)}`, async () => {
    const upstream = backend();
    const input = request("/api/google_assistant", syncBody);
    if (authorization === null) input.headers.delete("Authorization");
    else input.headers.set("Authorization", authorization);
    await assertError(await worker.fetch(input, upstream.env), 401);
    assert.equal(upstream.calls.length, 0);
  });
}

test("accepts exactly 32 KiB in a streamed request and buffers its original bytes", async () => {
  const upstream = backend();
  const body = encoder.encode("code=" + "x".repeat(32768 - 5));
  const response = await worker.fetch(request("/auth/token", streamChunks(body.slice(0, 1000), body.slice(1000))), upstream.env);
  assert.equal(response.status, 200);
  assert.deepEqual(new Uint8Array(upstream.calls[0][1].body), body);
});

test("rejects a declared oversized request before reading its body", async () => {
  const upstream = backend();
  await assertError(await worker.fetch(request("/auth/token", new ReadableStream(), {
    "Content-Length": "32769",
  }), upstream.env), 413);
  assert.equal(upstream.calls.length, 0);
});

test("enforces streamed request size despite a smaller advertised length", async () => {
  const upstream = backend();
  await assertError(await worker.fetch(request("/auth/token", streamChunks(new Uint8Array(32768), new Uint8Array(1)), {
    "Content-Length": "10",
  }), upstream.env), 413);
  assert.equal(upstream.calls.length, 0);
});

test("cancels a producer that exceeds the streamed request limit without Content-Length", async () => {
  let cancelled = false;
  const upstream = backend();
  const body = new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(32769)); },
    cancel() { cancelled = true; },
  });
  await assertError(await worker.fetch(request("/auth/token", body), upstream.env), 413);
  assert.equal(cancelled, true);
  assert.equal(upstream.calls.length, 0);
});

for (const status of [301, 302, 303, 307, 308]) {
  test(`never follows or returns an upstream ${status} redirect, even with JSON`, async () => {
    const upstream = backend(() => new Response('{"redirect":"private"}', { status, headers: {
      "Content-Type": "application/json", Location: "https://other.example/capture",
    } }));
    await assertError(await worker.fetch(request(), upstream.env), 502);
    assert.equal(upstream.calls.length, 1);
    assert.equal(upstream.calls[0][1].redirect, "manual");
  });
}

for (const [label, handler] of [
  ["HTML challenge", () => new Response("<html>Private challenge</html>", { headers: { "Content-Type": "text/html" } })],
  ["malformed JSON", () => new Response("{", { headers: { "Content-Type": "application/json" } })],
  ["invalid UTF-8", () => new Response(new Uint8Array([0xff]), { headers: { "Content-Type": "application/json" } })],
  ["empty success", () => new Response(null, { status: 204 })],
  ["non-response", () => ({ status: 200 })],
  ["exception", () => { throw new Error("Private address and credentials"); }],
  ["oversized body", () => new Response(JSON.stringify({ text: "x".repeat(262144) }), { headers: { "Content-Type": "application/json" } })],
  ["oversized Content-Length", () => new Response("{}", { headers: { "Content-Type": "application/json", "Content-Length": "262145" } })],
  ["invalid Content-Length", () => new Response("{}", { headers: { "Content-Type": "application/json", "Content-Length": "invalid" } })],
  ["mismatched Content-Length", () => new Response("{}", { headers: { "Content-Type": "application/json", "Content-Length": "10" } })],
  ["compressed body", () => new Response("{}", { headers: { "Content-Type": "application/json", "Content-Encoding": "gzip" } })],
]) {
  test(`returns a generic upstream failure for ${label}`, async () => {
    const upstream = backend(handler);
    await assertError(await worker.fetch(request(), upstream.env), 502);
  });
}

test("accepts an exactly 256 KiB JSON response without rewriting it", async () => {
  const body = JSON.stringify({ text: "x".repeat(262144 - 11) });
  assert.equal(encoder.encode(body).byteLength, 262144);
  const upstream = backend(() => new Response(body, { headers: { "Content-Type": "application/json" } }));
  const response = await worker.fetch(request(), upstream.env);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), body);
});

test("a missing binding fails closed and never calls public fetch", async (t) => {
  const publicFetch = t.mock.method(globalThis, "fetch", () => { throw new Error("Public fallback forbidden"); });
  for (const binding of [undefined, {}, { fetch: null }]) {
    await assertError(await worker.fetch(request(), { PUBLIC_HOST, BACKEND: binding }), 502);
  }
  assert.equal(publicFetch.mock.callCount(), 0);
});

test("the shared eight-second deadline includes a stalled client body", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let cancelled = false;
  const upstream = backend();
  const pending = worker.fetch(request("/auth/token", new ReadableStream({ cancel() { cancelled = true; } })), upstream.env);
  t.mock.timers.tick(8000);
  await assertError(await pending, 502);
  assert.equal(cancelled, true);
  assert.equal(upstream.calls.length, 0);
});

test("the shared eight-second deadline aborts a stalled private binding", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let called;
  const started = new Promise(resolve => { called = resolve; });
  const upstream = backend(() => { called(); return new Promise(() => {}); });
  const pending = worker.fetch(request(), upstream.env);
  await started;
  t.mock.timers.tick(8000);
  await assertError(await pending, 502);
  assert.equal(upstream.calls[0][1].signal.aborted, true);
});

test("the same deadline cancels a stalled upstream response stream", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let reading;
  const started = new Promise(resolve => { reading = resolve; });
  let cancelled = false;
  const upstream = backend(() => new Response(new ReadableStream({
    pull() { reading(); }, cancel() { cancelled = true; },
  }), { headers: { "Content-Type": "application/json" } }));
  const pending = worker.fetch(request(), upstream.env);
  await started;
  t.mock.timers.tick(8000);
  await assertError(await pending, 502);
  assert.equal(upstream.calls[0][1].signal.aborted, true);
  assert.equal(cancelled, true);
});
