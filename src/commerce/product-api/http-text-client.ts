import * as http from "http";
import * as https from "https";
import * as zlib from "zlib";
import type { TextHttpClient, TextHttpResponse } from "./types";

function resolveHttpUrl(value: string, base?: URL): URL {
  const target = base ? new URL(value, base) : new URL(value);
  if (target.protocol !== "http:" && target.protocol !== "https:") {
    throw new Error(`Unsupported HTTP protocol: ${target.protocol}`);
  }
  return target;
}

export class NodeTextHttpClient implements TextHttpClient {
  constructor(
    private readonly timeoutMs = 12_000,
    private readonly userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    private readonly maxRedirects = 5,
    private readonly maxBodyBytes = 16 * 1024 * 1024
  ) {}

  get(url: string, headers: Record<string, string> = {}): Promise<TextHttpResponse> {
    return this.getWithRedirects(url, headers, 0);
  }

  private getWithRedirects(
    url: string,
    headers: Record<string, string>,
    redirectCount: number
  ): Promise<TextHttpResponse> {
    return new Promise((resolve, reject) => {
      let target: URL;
      try {
        target = resolveHttpUrl(url);
      } catch (error) {
        reject(error);
        return;
      }

      const transport = target.protocol === "http:" ? http : https;
      const request = transport.request(target, {
        method: "GET",
        headers: {
          Accept: "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
          "Accept-Encoding": "gzip, deflate, br",
          "User-Agent": this.userAgent,
          ...headers
        }
      }, response => {
        const status = response.statusCode ?? 0;
        const location = response.headers.location;
        const isRedirect = status === 301 || status === 302 || status === 303 || status === 307 || status === 308;

        if (isRedirect && location) {
          response.resume();
          if (redirectCount >= this.maxRedirects) {
            reject(new Error(`Too many HTTP redirects while requesting ${url}`));
            return;
          }
          try {
            const next = resolveHttpUrl(location, target).toString();
            resolve(this.getWithRedirects(next, headers, redirectCount + 1));
          } catch (error) {
            reject(error instanceof Error ? error : new Error(String(error)));
          }
          return;
        }

        const encoding = String(response.headers["content-encoding"] ?? "").toLowerCase();
        let stream: NodeJS.ReadableStream = response;
        if (encoding.includes("gzip")) stream = response.pipe(zlib.createGunzip());
        else if (encoding.includes("deflate")) stream = response.pipe(zlib.createInflate());
        else if (encoding.includes("br")) stream = response.pipe(zlib.createBrotliDecompress());

        const chunks: Buffer[] = [];
        let bodyBytes = 0;
        let tooLarge = false;
        let failed = false;
        const fail = (error: Error): void => {
          if (failed) return;
          failed = true;
          reject(error);
        };
        stream.on("data", chunk => {
          const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
          bodyBytes += buffer.length;
          if (bodyBytes <= this.maxBodyBytes) chunks.push(buffer);
          else tooLarge = true;
        });
        stream.on("error", fail);
        stream.on("end", () => {
          if (tooLarge) {
            reject(new Error(`HTTP body exceeded ${this.maxBodyBytes} bytes for ${target.toString()}`));
            return;
          }
          const responseHeaders: Record<string, string> = {};
          for (const [key, value] of Object.entries(response.headers)) {
            if (typeof value === "string") responseHeaders[key] = value;
            else if (Array.isArray(value)) responseHeaders[key] = value.join(", ");
          }
          resolve({
            status,
            headers: responseHeaders,
            text: Buffer.concat(chunks).toString("utf8"),
            url: target.toString()
          });
        });
      });

      request.setTimeout(this.timeoutMs, () => {
        request.destroy(new Error(`HTTP timeout after ${this.timeoutMs}ms`));
      });
      request.on("error", reject);
      request.end();
    });
  }
}
