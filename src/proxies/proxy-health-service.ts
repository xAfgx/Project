import * as https from "https";
import * as net from "net";
import * as tls from "tls";
import type { AresProxy, ProxyGeo, ProxyHealthResult, ProxyReputation } from "./models";

const IP_HOST = "api.ipify.org";
const IP_PATH = "/?format=json";
const DEFAULT_TIMEOUT_MS = 8_000;
const MAX_RESPONSE_BYTES = 512 * 1024;

type SocketLike = net.Socket | tls.TLSSocket;

export interface ProxyHealthServiceDependencies {
  probeExitIp?: (proxy: AresProxy, timeoutMs: number) => Promise<{ exitIp: string; latencyMs: number }>;
  lookupReputation?: (exitIp: string, timeoutMs: number) => Promise<{ geo?: ProxyGeo; reputation: ProxyReputation }>;
}

function toError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function asNumber(value: unknown): number | undefined {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function getRiskLevel(score?: number): ProxyReputation["riskLevel"] {
  if (typeof score !== "number") return undefined;
  if (score <= 33) return "low";
  if (score <= 66) return "high";
  return "critical";
}

function parseAttackHistory(value: unknown): Record<string, number> | undefined {
  if (!value || typeof value !== "object") return undefined;
  const result: Record<string, number> = {};
  for (const [name, raw] of Object.entries(value as Record<string, unknown>)) {
    const count = asNumber(raw);
    if (typeof count === "number") result[name] = count;
  }
  return Object.keys(result).length ? result : undefined;
}

export function parseProxycheckResponse(exitIp: string, payload: unknown): { geo?: ProxyGeo; reputation: ProxyReputation } {
  const root = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
  const rawRecord = root[exitIp];
  if (!rawRecord || typeof rawRecord !== "object") {
    return {
      reputation: {
        source: "proxycheck.io",
        available: false,
        error: typeof root["message"] === "string" ? root["message"] : "Keine Reputation-Daten erhalten."
      }
    };
  }

  const record = rawRecord as Record<string, unknown>;
  const riskScore = asNumber(record["risk"]);
  const attackHistory = parseAttackHistory(record["attack history"]);
  const attackTotal = attackHistory ? asNumber(attackHistory["Total"]) : undefined;
  const spamHits = attackHistory
    ? Object.entries(attackHistory)
        .filter(([name]) => name !== "Total" && /spam/i.test(name))
        .reduce((sum, [, count]) => sum + count, 0)
    : undefined;

  return {
    geo: {
      country: typeof record["country"] === "string" ? record["country"] : undefined,
      countryCode: typeof record["isocode"] === "string" ? record["isocode"] : undefined,
      region: typeof record["region"] === "string" ? record["region"] : undefined,
      city: typeof record["city"] === "string" ? record["city"] : undefined,
      provider: typeof record["provider"] === "string" ? record["provider"] : undefined,
      asn: typeof record["asn"] === "string" ? record["asn"] : undefined,
      latitude: asNumber(record["latitude"]),
      longitude: asNumber(record["longitude"])
    },
    reputation: {
      source: "proxycheck.io",
      available: true,
      riskScore,
      riskLevel: getRiskLevel(riskScore),
      attackTotal,
      attackHistory,
      spamHits,
      proxyDetected: String(record["proxy"] ?? "").toLowerCase() === "yes",
      detectedType: typeof record["type"] === "string" ? record["type"] : undefined,
      lastSeen: typeof record["last seen human"] === "string" ? record["last seen human"] : undefined
    }
  };
}

function connectBase(proxy: AresProxy, timeoutMs: number): Promise<SocketLike> {
  return new Promise((resolve, reject) => {
    let done = false;
    const socket: SocketLike = proxy.protocol === "https"
      ? tls.connect({ host: proxy.host, port: proxy.port, servername: proxy.host, rejectUnauthorized: true })
      : net.connect({ host: proxy.host, port: proxy.port });
    const readyEvent = proxy.protocol === "https" ? "secureConnect" : "connect";
    const fail = (error: unknown) => {
      if (done) return;
      done = true;
      socket.destroy();
      reject(error instanceof Error ? error : new Error(String(error)));
    };
    socket.setTimeout(timeoutMs, () => fail(new Error("Proxy-Verbindung Timeout.")));
    socket.once("error", fail);
    socket.once(readyEvent, () => {
      if (done) return;
      done = true;
      socket.removeListener("error", fail);
      socket.setTimeout(0);
      resolve(socket);
    });
  });
}

function readExact(socket: SocketLike, bytes: number, timeoutMs: number): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    let data = Buffer.alloc(0);
    const timer = setTimeout(() => finish(new Error("Proxy-Handshake Timeout.")), timeoutMs);
    const onData = (chunk: Buffer) => {
      data = Buffer.concat([data, chunk]);
      if (data.length < bytes) return;
      const result = data.subarray(0, bytes);
      const rest = data.subarray(bytes);
      if (rest.length) socket.unshift(rest);
      finish(undefined, result);
    };
    const onError = (error: Error) => finish(error);
    const onEnd = () => finish(new Error("Proxy hat den Handshake beendet."));
    const finish = (error?: Error, result?: Buffer) => {
      clearTimeout(timer);
      socket.removeListener("data", onData);
      socket.removeListener("error", onError);
      socket.removeListener("end", onEnd);
      if (error) reject(error); else resolve(result ?? Buffer.alloc(0));
    };
    socket.on("data", onData);
    socket.once("error", onError);
    socket.once("end", onEnd);
  });
}

function readHeaders(socket: SocketLike, timeoutMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = Buffer.alloc(0);
    const timer = setTimeout(() => finish(new Error("HTTP CONNECT Timeout.")), timeoutMs);
    const onData = (chunk: Buffer) => {
      data = Buffer.concat([data, chunk]);
      const marker = data.indexOf("\r\n\r\n");
      if (marker < 0) {
        if (data.length > 32 * 1024) finish(new Error("CONNECT-Header zu groß."));
        return;
      }
      const rest = data.subarray(marker + 4);
      if (rest.length) socket.unshift(rest);
      finish(undefined, data.subarray(0, marker + 4).toString("utf8"));
    };
    const onError = (error: Error) => finish(error);
    const onEnd = () => finish(new Error("Proxy hat CONNECT beendet."));
    const finish = (error?: Error, result?: string) => {
      clearTimeout(timer);
      socket.removeListener("data", onData);
      socket.removeListener("error", onError);
      socket.removeListener("end", onEnd);
      if (error) reject(error); else resolve(result ?? "");
    };
    socket.on("data", onData);
    socket.once("error", onError);
    socket.once("end", onEnd);
  });
}

function tlsToTarget(socket: SocketLike, targetHost: string, timeoutMs: number): Promise<tls.TLSSocket> {
  return new Promise((resolve, reject) => {
    let done = false;
    const secure = tls.connect({ socket: socket as net.Socket, servername: targetHost, rejectUnauthorized: true });
    const fail = (error: unknown) => {
      if (done) return;
      done = true;
      secure.destroy();
      reject(error instanceof Error ? error : new Error(String(error)));
    };
    secure.setTimeout(timeoutMs, () => fail(new Error("TLS über Proxy Timeout.")));
    secure.once("error", fail);
    secure.once("secureConnect", () => {
      if (done) return;
      done = true;
      secure.removeListener("error", fail);
      secure.setTimeout(0);
      resolve(secure);
    });
  });
}

async function httpTunnel(proxy: AresProxy, targetHost: string, targetPort: number, timeoutMs: number): Promise<tls.TLSSocket> {
  const socket = await connectBase(proxy, timeoutMs);
  const auth = proxy.username || proxy.password
    ? `Proxy-Authorization: Basic ${Buffer.from(`${proxy.username ?? ""}:${proxy.password ?? ""}`).toString("base64")}\r\n`
    : "";
  socket.write(`CONNECT ${targetHost}:${targetPort} HTTP/1.1\r\nHost: ${targetHost}:${targetPort}\r\n${auth}Proxy-Connection: Keep-Alive\r\n\r\n`);
  const headers = await readHeaders(socket, timeoutMs);
  const status = headers.split("\r\n", 1)[0] ?? "";
  if (!/^HTTP\/\d(?:\.\d)? 200\b/.test(status)) {
    socket.destroy();
    throw new Error(`Proxy CONNECT fehlgeschlagen: ${status || "unbekannt"}`);
  }
  return tlsToTarget(socket, targetHost, timeoutMs);
}

async function socksTunnel(proxy: AresProxy, targetHost: string, targetPort: number, timeoutMs: number): Promise<tls.TLSSocket> {
  const socket = await connectBase(proxy, timeoutMs);
  const useAuth = Boolean(proxy.username || proxy.password);
  socket.write(useAuth ? Buffer.from([5, 2, 0, 2]) : Buffer.from([5, 1, 0]));
  const greeting = await readExact(socket, 2, timeoutMs);
  if (greeting[0] !== 5 || greeting[1] === 0xff) throw new Error("SOCKS5 Auth-Methode abgelehnt.");

  if (greeting[1] === 2) {
    const user = Buffer.from(proxy.username ?? "", "utf8");
    const pass = Buffer.from(proxy.password ?? "", "utf8");
    if (user.length > 255 || pass.length > 255) throw new Error("SOCKS5 Login zu lang.");
    socket.write(Buffer.concat([Buffer.from([1, user.length]), user, Buffer.from([pass.length]), pass]));
    const auth = await readExact(socket, 2, timeoutMs);
    if (auth[1] !== 0) throw new Error("SOCKS5 Login abgelehnt.");
  }

  const host = Buffer.from(targetHost, "utf8");
  socket.write(Buffer.concat([
    Buffer.from([5, 1, 0, 3, host.length]), host,
    Buffer.from([(targetPort >> 8) & 255, targetPort & 255])
  ]));
  const response = await readExact(socket, 4, timeoutMs);
  if (response[0] !== 5 || response[1] !== 0) throw new Error(`SOCKS5 Connect Code ${response[1]}.`);
  if (response[3] === 1) await readExact(socket, 6, timeoutMs);
  else if (response[3] === 4) await readExact(socket, 18, timeoutMs);
  else if (response[3] === 3) {
    const len = await readExact(socket, 1, timeoutMs);
    await readExact(socket, len[0] + 2, timeoutMs);
  } else throw new Error("SOCKS5 unbekannter Adresstyp.");
  return tlsToTarget(socket, targetHost, timeoutMs);
}

function decodeChunked(body: Buffer): Buffer {
  let offset = 0;
  const chunks: Buffer[] = [];
  while (offset < body.length) {
    const lineEnd = body.indexOf("\r\n", offset);
    if (lineEnd < 0) break;
    const size = parseInt(body.subarray(offset, lineEnd).toString("ascii").split(";", 1)[0], 16);
    if (!Number.isFinite(size) || size === 0) break;
    offset = lineEnd + 2;
    if (offset + size > body.length) break;
    chunks.push(body.subarray(offset, offset + size));
    offset += size + 2;
  }
  return Buffer.concat(chunks);
}

function bodyFromResponse(response: Buffer): Buffer {
  const marker = response.indexOf("\r\n\r\n");
  if (marker < 0) throw new Error("Ungültige HTTP-Antwort vom IP-Check.");
  const headers = response.subarray(0, marker).toString("utf8");
  const status = headers.split("\r\n", 1)[0] ?? "";
  if (!/^HTTP\/\d(?:\.\d)? 2\d\d\b/.test(status)) throw new Error(`IP-Check fehlgeschlagen: ${status}`);
  const body = response.subarray(marker + 4);
  return /transfer-encoding:\s*chunked/i.test(headers) ? decodeChunked(body) : body;
}

function readResponse(socket: tls.TLSSocket, timeoutMs: number): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    const timer = setTimeout(() => finish(new Error("IP-Check Timeout.")), timeoutMs);
    const onData = (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_RESPONSE_BYTES) return finish(new Error("IP-Check Antwort zu groß."));
      chunks.push(chunk);
    };
    const onEnd = () => finish(undefined, Buffer.concat(chunks));
    const onError = (error: Error) => finish(error);
    const finish = (error?: Error, result?: Buffer) => {
      clearTimeout(timer);
      socket.removeListener("data", onData);
      socket.removeListener("end", onEnd);
      socket.removeListener("error", onError);
      socket.destroy();
      if (error) reject(error); else resolve(result ?? Buffer.alloc(0));
    };
    socket.on("data", onData);
    socket.once("end", onEnd);
    socket.once("error", onError);
  });
}

async function defaultProbeExitIp(proxy: AresProxy, timeoutMs: number): Promise<{ exitIp: string; latencyMs: number }> {
  const started = Date.now();
  const socket = proxy.protocol === "socks5"
    ? await socksTunnel(proxy, IP_HOST, 443, timeoutMs)
    : await httpTunnel(proxy, IP_HOST, 443, timeoutMs);
  socket.write(`GET ${IP_PATH} HTTP/1.1\r\nHost: ${IP_HOST}\r\nAccept: application/json\r\nConnection: close\r\nUser-Agent: ARES-ProxyHealth/1.0\r\n\r\n`);
  const response = await readResponse(socket, timeoutMs);
  const payload = JSON.parse(bodyFromResponse(response).toString("utf8")) as { ip?: string };
  const exitIp = String(payload.ip ?? "").trim();
  if (!exitIp) throw new Error("Exit-IP konnte nicht bestimmt werden.");
  return { exitIp, latencyMs: Date.now() - started };
}

function defaultLookupReputation(exitIp: string, timeoutMs: number): Promise<{ geo?: ProxyGeo; reputation: ProxyReputation }> {
  return new Promise(resolve => {
    const encodedIp = encodeURIComponent(exitIp);
    const path = `/v2/${encodedIp}?vpn=1&asn=1&risk=2&days=30&seen=1&port=1`;
    const request = https.get({
      host: "proxycheck.io",
      port: 443,
      path,
      timeout: timeoutMs,
      headers: { "User-Agent": "ARES-ProxyHealth/1.0", Accept: "application/json" }
    }, response => {
      const chunks: Buffer[] = [];
      let size = 0;
      response.on("data", (chunk: Buffer) => {
        size += chunk.length;
        if (size <= MAX_RESPONSE_BYTES) chunks.push(chunk);
      });
      response.on("end", () => {
        try {
          if (size > MAX_RESPONSE_BYTES) throw new Error("Reputation-Antwort zu groß.");
          if ((response.statusCode ?? 500) >= 400) throw new Error(`proxycheck.io HTTP ${response.statusCode}`);
          const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          resolve(parseProxycheckResponse(exitIp, payload));
        } catch (error) {
          resolve({ reputation: { source: "proxycheck.io", available: false, error: toError(error) } });
        }
      });
    });
    request.on("timeout", () => request.destroy(new Error("proxycheck.io Timeout.")));
    request.on("error", error => resolve({ reputation: { source: "proxycheck.io", available: false, error: toError(error) } }));
  });
}

export class ProxyHealthService {
  constructor(private readonly dependencies: ProxyHealthServiceDependencies = {}) {}

  async test(proxy: AresProxy, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<ProxyHealthResult> {
    const checkedAt = new Date().toISOString();
    try {
      const probe = await (this.dependencies.probeExitIp ?? defaultProbeExitIp)(proxy, timeoutMs);
      const reputation = await (this.dependencies.lookupReputation ?? defaultLookupReputation)(probe.exitIp, timeoutMs);
      return {
        proxyId: proxy.id,
        status: "online",
        checkedAt,
        latencyMs: probe.latencyMs,
        exitIp: probe.exitIp,
        geo: reputation.geo,
        reputation: reputation.reputation
      };
    } catch (error) {
      return {
        proxyId: proxy.id,
        status: "offline",
        checkedAt,
        error: toError(error)
      };
    }
  }
}
