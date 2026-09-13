import { spawn } from "child_process";
import type { ChildProcessWithoutNullStreams } from "child_process";
import * as fs from "fs";
import * as path from "path";

export interface SessionHttpSignal {
  active: boolean;
  position?: number;
  timeToWaitSeconds?: number;
  statusText?: string;
  source: "session-http";
  observedAtMs: number;
  statusCode?: number;
  url?: string;
  available?: boolean;
  productState?: string;
  harvestSource?: string;
  cookieCount?: number;
  hasClearance?: boolean;
}

export interface SessionHttpPollerOptions {
  url: string;
  profileDir: string;
  proxy?: string;
  pollIntervalMs?: number;
}

const PREFIX = "ARES_SESSION_HTTP\t";

export class SessionHttpPoller {
  private child?: ChildProcessWithoutNullStreams;
  private buffer = "";
  private latest?: SessionHttpSignal;
  private lastError?: string;

  constructor(private readonly options: SessionHttpPollerOptions) {}

  start(): void {
    if (this.child) return;
    const script = this.resolveScript();
    const child = spawn(process.env["ARES_PYTHON_EXECUTABLE"]?.trim() || "python", ["-u", script], {
      stdio: ["pipe", "pipe", "pipe"], windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" }
    });
    this.child = child;
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", chunk => this.consume(String(chunk)));
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", chunk => { this.lastError = `${this.lastError ?? ""}${String(chunk)}`.slice(-4000); });
    child.once("exit", () => { if (this.child === child) this.child = undefined; });
    child.stdin.write(`${JSON.stringify({
      url: this.options.url,
      profileDir: this.options.profileDir,
      proxy: this.options.proxy,
      pollIntervalMs: this.options.pollIntervalMs ?? 2_000,
      // Strict engine switch: this sidecar is only spawned for UI-started
      // MONITOR instances. The token is verified by the Python sidecar, which
      // refuses to run curl_cffi for any other instance mode.
      engine: "curl_cffi",
      instanceMode: "monitor"
    })}\n`);
  }

  getLatest(maxAgeMs = 15_000): SessionHttpSignal | undefined {
    const signal = this.latest;
    if (!signal || Date.now() - signal.observedAtMs > maxAgeMs) return undefined;
    return signal;
  }

  getError(): string | undefined { return this.lastError; }

  stop(): void {
    const child = this.child;
    this.child = undefined;
    if (child && child.exitCode == null) child.kill();
  }

  private consume(chunk: string): void {
    this.buffer += chunk;
    const lines = this.buffer.split(/\r?\n/);
    this.buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith(PREFIX)) continue;
      try {
        const value = JSON.parse(line.slice(PREFIX.length)) as Record<string, unknown>;
        if (value["ok"] !== true) {
          this.lastError = String(value["error"] ?? "session HTTP poll failed");
          continue;
        }
        this.lastError = undefined;
        this.latest = {
          active: value["active"] === true,
          position: typeof value["position"] === "number" ? value["position"] : undefined,
          timeToWaitSeconds: typeof value["timeToWaitSeconds"] === "number" ? value["timeToWaitSeconds"] : undefined,
          statusText: typeof value["statusText"] === "string" ? value["statusText"] : undefined,
          source: "session-http",
          observedAtMs: Number(value["observedAtMs"] ?? Date.now()),
          statusCode: typeof value["statusCode"] === "number" ? value["statusCode"] : undefined,
          url: typeof value["url"] === "string" ? value["url"] : undefined,
          available: typeof value["available"] === "boolean" ? value["available"] : undefined,
          productState: typeof value["productState"] === "string" ? value["productState"] : undefined,
          harvestSource: typeof value["harvestSource"] === "string" ? value["harvestSource"] : undefined,
          cookieCount: typeof value["cookieCount"] === "number" ? value["cookieCount"] : undefined,
          hasClearance: typeof value["hasClearance"] === "boolean" ? value["hasClearance"] : undefined
        };
      } catch { /* best-effort sidecar telemetry */ }
    }
  }

  private resolveScript(): string {
    const resourcesPath = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath || "";
    const candidates = [
      process.env["ARES_SESSION_HTTP_POLLER"]?.trim(),
      path.join(process.cwd(), "python", "seleniumbase_cdp", "session_http_poller.py"),
      path.join(__dirname, "../../python/seleniumbase_cdp/session_http_poller.py"),
      resourcesPath ? path.join(resourcesPath, "python", "seleniumbase_cdp", "session_http_poller.py") : undefined
    ].filter((value): value is string => Boolean(value));
    const resolved = candidates.find(candidate => fs.existsSync(candidate));
    if (!resolved) throw new Error("session_http_poller.py was not found");
    return resolved;
  }
}
