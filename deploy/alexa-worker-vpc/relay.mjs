// Amazon verifies against these exact request bytes at the private gateway.
const BACKEND_URL = "http://epg-voice-gateway/alexa";
const MAX_REQUEST_BYTES = 32 * 1024;
const MAX_RESPONSE_BYTES = 64 * 1024;
const MAX_SIGNATURE_LENGTH = 8192;
const DEADLINE_MS = 7000;
const SIGNATURE_HEADERS = ["Signature-256", "SignatureCertChainUrl"];
const DNS_LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
const PUBLIC_HOST_PATTERN = new RegExp(`^${DNS_LABEL}(?:\\.${DNS_LABEL})+$`);
const RESPONSE_HEADERS = {
  "Content-Type": "application/json",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};

class RequestError extends Error {
  constructor(status) {
    super("Invalid request");
    this.status = status;
  }
}

function jsonError(status) {
  return new Response(JSON.stringify({
    error: status === 404 ? "Not found" : status === 502 ? "Service unavailable" : "Invalid request",
  }), { status, headers: RESPONSE_HEADERS });
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
      || request.url.includes("?") || request.url.includes("#") || url.pathname !== "/alexa") {
    throw new RequestError(404);
  }
}

function requestHeaders(request) {
  const contentType = request.headers.get("Content-Type");
  const encoding = request.headers.get("Content-Encoding");
  if (!contentType || contentType.length > 256 || mediaType(contentType) !== "application/json"
      || /[\u0000-\u001f\u007f]/.test(contentType)
      || (encoding && encoding.toLowerCase() !== "identity")) {
    throw new RequestError(400);
  }
  const headers = new Headers({ "Content-Type": contentType });
  for (const name of SIGNATURE_HEADERS) {
    const value = request.headers.get(name);
    if (!value?.trim() || value.length > MAX_SIGNATURE_LENGTH
        || /[\u0000-\u001f\u007f]/.test(value)) {
      throw new RequestError(400);
    }
    headers.set(name, value);
  }
  // The gateway deliberately verifies SHA-256 only. A legacy Signature header
  // cannot replace Signature-256 and is never forwarded as an alternative.
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

async function relay(request, env, signal) {
  if (typeof env.BACKEND?.fetch !== "function") throw new Error("Missing backend binding");
  const headers = requestHeaders(request);
  const expectedSize = contentLength(request.headers, MAX_REQUEST_BYTES);
  const body = await readLimited(request.body, MAX_REQUEST_BYTES, signal);
  if (!body.byteLength || (expectedSize !== null && expectedSize !== body.byteLength)) {
    throw new RequestError(400);
  }
  signal.throwIfAborted();
  const response = await env.BACKEND.fetch(BACKEND_URL, {
    method: "POST", headers,
    // A fixed-length ArrayBuffer preserves signed bytes and avoids chunked
    // request encoding. The VPC binding fixes the host and port independently.
    body: body.buffer, redirect: "manual", signal,
  });
  try {
    signal.throwIfAborted();
    if (!(response instanceof Response)
        || !(response.status === 200 || (response.status >= 400 && response.status <= 599))
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
    const payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(responseBody));
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("Invalid backend JSON object");
    }
    return new Response(responseBody, { status: response.status, headers: RESPONSE_HEADERS });
  } catch {
    if (response instanceof Response) void response.body?.cancel().catch(() => {});
    throw new Error("Invalid backend response");
  }
}

export default {
  async fetch(request, env) {
    let timer;
    const controller = new AbortController();
    try {
      route(request, env);
      const deadline = new Promise((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new Error("Relay deadline exceeded"));
        }, DEADLINE_MS);
      });
      // Bound the entire exchange even if a binding ignores cancellation.
      return await Promise.race([relay(request, env, controller.signal), deadline]);
    } catch (error) {
      return jsonError(error instanceof RequestError && !controller.signal.aborted ? error.status : 502);
    } finally {
      clearTimeout(timer);
    }
  },
};
