import * as http from "http";
import * as https from "https";
import type { JsonHttpClient, JsonHttpResponse } from "./types";

function assertHttpTarget(value: string, base?: URL): URL {
  const target = base ? new URL(value, base) : new URL(value);
  if (target.protocol !== "http:" && target.protocol !== "https:") {
    throw new Error(`Unsupported HTTP protocol: ${target.protocol}`);
  }
  return target;
}

export class NodeJsonHttpClient implements JsonHttpClient {
  constructor(
    private readonly timeoutMs = 12_000,
    private readonly userAgent = "ARES-Product-Monitor/1.0",
    private readonly maxRedirects = 5
  ) {}

  get<T>(url: string, headers: Record<string, string> = {}): Promise<JsonHttpResponse<T>> {
    return this.getWithRedirects<T>(url, headers, 0);
  }

  private getWithRedirects<T>(
    url: string,
    headers: Record<string, string>,
    redirectCount: number
  ): Promise<JsonHttpResponse<T>> {
    return new Promise((resolve, reject) => {
      let target: URL;
      try {
        target = assertHttpTarget(url);
      } catch (error) {
        reject(error);
        return;
      }
      const transport = target.protocol === "http:" ? http : https;
      const request = transport.request(target, {
        method: "GET",
        headers: {
          Accept: "application/json",
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

          let nextUrl: string;
          try {
            nextUrl = assertHttpTarget(location, target).toString();
          } catch (error) {
            reject(error instanceof Error ? error : new Error(String(error)));
            return;
          }

          resolve(this.getWithRedirects<T>(nextUrl, headers, redirectCount + 1));
          return;
        }

        const chunks: Buffer[] = [];
        response.on("data", chunk => chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)));
        response.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          const responseHeaders: Record<string, string> = {};
          for (const [key, value] of Object.entries(response.headers)) {
            if (typeof value === "string") responseHeaders[key] = value;
            else if (Array.isArray(value)) responseHeaders[key] = value.join(", ");
          }

          let data: T | undefined;
          if (text.trim()) {
            try {
              data = JSON.parse(text) as T;
            } catch {
              data = undefined;
            }
          }

          resolve({
            status,
            headers: responseHeaders,
            data,
            text: data === undefined ? text.slice(0, 1_000) : undefined
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
