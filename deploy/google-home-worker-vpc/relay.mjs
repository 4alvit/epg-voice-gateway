// Only Google's server-to-server token exchange and fulfillment use this relay.
// Browser authorization remains on the existing Home Assistant origin.
const BACKEND_ORIGIN = "http://home-assistant";
const MAX_REQUEST_BYTES = 32 * 1024;
const MAX_RESPONSE_BYTES = 256 * 1024;
const DEADLINE_MS = 8000;
const DNS_LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
const PUBLIC_HOST_PATTERN = new RegExp(`^${DNS_LABEL}(?:\\.${DNS_LABEL})+$`);

class RequestError extends Error {
  constructor(status) {
    super("Invalid request");
    this.status = status;
  }
}

function jsonError(status) {
  return new Response(JSON.stringify({
    error: status === 404 ? "Not found" : status === 502 ? "Service unavailable" : "Invalid request",
  }), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

function mediaType(value) {
  return value?.split(";", 1)[0].trim().toLowerCase();
}

function route(request, env) {
  if (typeof env?.PUBLIC_HOST !== "string" || env.PUBLIC_HOST.length > 253
      || !PUBLIC_HOST_PATTERN.test(env.PUBLIC_HOST)) {
    throw new Error("Invalid deployment configuration");
  }
  const url = new URL(request.url);
  if (request.method !== "POST" || url.protocol !== "https:"
      || url.host !== env.PUBLIC_HOST || url.username || url.password
      || request.url.includes("?") || request.url.includes("#")
      || !["/auth/token", "/api/google_assistant"].includes(url.pathname)) {
    throw new RequestError(404);
  }
  return url.pathname;
}

function requestHeaders(request, path) {
  const contentType = request.headers.get("Content-Type");
  const expectedType = path === "/auth/token"
    ? "application/x-www-form-urlencoded" : "application/json";
  const encoding = request.headers.get("Content-Encoding");
  if (!contentType || contentType.length > 256 || mediaType(contentType) !== expectedType
      || /[\u0000-\u001f\u007f]/.test(contentType)
      || (encoding && encoding.toLowerCase() !== "identity")) {
    throw new RequestError(400);
  }
  const headers = new Headers({ "Content-Type": contentType });
  if (path === "/api/google_assistant") {
    const authorization = request.headers.get("Authorization");
    if (!authorization || authorization.length > 8192
        || !/^Bearer [A-Za-z0-9._~+/-]+=*$/i.test(authorization)) {
      throw new RequestError(401);
    }
    headers.set("Authorization", authorization);
  }
  return headers;
}

function contentLength(headers, limit) {
  const value = headers.get("Content-Length");
  if (value === null) return null;
  if (!/^(0|[1-9][0-9]*)$/.test(value)) throw new RequestError(400);
  if (Number(value) > limit) throw new RequestError(413);
  return Number(value);
}

async function readLimited(stream, limit, signal) {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks = [];
  let size = 0;
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", cancel, { once: true });
  try {
    while (true) {
      signal.throwIfAborted();
      const { value, done } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw new RequestError(413);
      if (value.byteLength) chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return bytes;
  } catch (error) {
    cancel();
    throw error;
  } finally {
    signal.removeEventListener("abort", cancel);
    reader.releaseLock();
  }
}

async function relay(request, env, path, signal) {
  // A missing private binding must never fall back to the public HA origin.
  if (typeof env.BACKEND?.fetch !== "function") throw new Error("Missing backend binding");
  const headers = requestHeaders(request, path);
  const expectedSize = contentLength(request.headers, MAX_REQUEST_BYTES);
  const body = await readLimited(request.body, MAX_REQUEST_BYTES, signal);
  if (!body.byteLength || (expectedSize !== null && expectedSize !== body.byteLength)) {
    throw new RequestError(400);
  }
  signal.throwIfAborted();
  const response = await env.BACKEND.fetch(`${BACKEND_ORIGIN}${path}`, {
    method: "POST",
    headers,
    // A fixed-length body avoids chunked request encoding at the HA origin.
    body: body.buffer,
    redirect: "manual",
    signal,
  });
  try {
    signal.throwIfAborted();
    if (response instanceof Response && path === "/api/google_assistant"
        && [401, 403].includes(response.status)
        && mediaType(response.headers.get("Content-Type")) !== "application/json") {
      // HA's authentication middleware can reject an expired token as plain
      // text. Preserve that status so Google can refresh it, without releasing
      // upstream text, HTML, headers, or cookies to the public caller.
      void response.body?.cancel().catch(() => {});
      return jsonError(response.status);
    }
    if (!(response instanceof Response)
        || !((response.status >= 200 && response.status < 300)
          || (response.status >= 400 && response.status <= 599))
        || mediaType(response.headers.get("Content-Type")) !== "application/json") {
      throw new Error("Invalid backend response");
    }
    const encoding = response.headers.get("Content-Encoding");
    if (encoding && encoding.toLowerCase() !== "identity") throw new Error("Invalid backend encoding");
    const expectedResponseSize = contentLength(response.headers, MAX_RESPONSE_BYTES);
    const responseBody = await readLimited(response.body, MAX_RESPONSE_BYTES, signal);
    if (expectedResponseSize !== null && expectedResponseSize !== responseBody.byteLength) {
      throw new Error("Invalid backend length");
    }
    JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(responseBody));
    return new Response(responseBody, {
      status: response.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    if (response instanceof Response) void response.body?.cancel().catch(() => {});
    // Do not expose transport details, non-JSON challenges, or redirect targets.
    throw new Error("Invalid backend response");
  }
}

export default {
  async fetch(request, env) {
    let timer;
    const controller = new AbortController();
    try {
      const path = route(request, env);
      const deadline = new Promise((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new Error("Relay deadline exceeded"));
        }, DEADLINE_MS);
      });
      // The shared deadline includes both body reads and a binding that ignores
      // cancellation. No response bytes are released before JSON validation.
      return await Promise.race([relay(request, env, path, controller.signal), deadline]);
    } catch (error) {
      return jsonError(error instanceof RequestError && !controller.signal.aborted ? error.status : 502);
    } finally {
      clearTimeout(timer);
    }
  },
};
