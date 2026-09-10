import { spawn } from "child_process";
import type { ChildProcessWithoutNullStreams } from "child_process";
import { randomBytes } from "crypto";
import * as fs from "fs";
import * as path from "path";
import { SeleniumBaseBrowserWorker } from "./seleniumbase-browser-worker";
import type { BrowserContextConfig, BrowserContextHandle } from "./types";

interface SharedVisionService {
  child: ChildProcessWithoutNullStreams;
  url: string;
  token: string;
}

/**
 * Single browser-runtime boundary for task/monitor sessions.
 *
 * Manual profile sessions and normal task sessions now share SeleniumBase Pure
 * CDP as the only active browser engine. Session ownership, leases, health,
 * cookies and shutdown stay in TypeScript; browser operations are delegated to
 * the Python SeleniumBase RPC worker.
 *
 * One loopback SigLIP service is also owned per Node browser worker so all
 * SeleniumBase session processes reuse a single warm model instead of loading
 * one model per browser session.
 */
export class AresBrowserRuntime extends SeleniumBaseBrowserWorker {
  readonly runtimeId = "ares-browser-runtime" as const;
  readonly engine = "seleniumbase-cdp" as const;

  private sharedVision?: SharedVisionService;
  private sharedVisionStart?: Promise<SharedVisionService | undefined>;

  constructor() {
    super();
    if (!this.sharedVisionDisabled() && !process.env["ARES_VISION_SERVICE_URL"]?.trim()) {
      // Start the service immediately, while the Python side preloads the model
      // in the background. Browser worker readiness itself stays non-blocking.
      void this.ensureSharedVisionService();
    }
  }

  override async createContext(config: BrowserContextConfig): Promise<BrowserContextHandle> {
    // Session Python processes inherit ARES_VISION_SERVICE_URL/TOKEN from this
    // process. Wait only for the lightweight loopback listener, not model load.
    await this.ensureSharedVisionService();
    const handle = await super.createContext(config);
    // Reuse the existing task identity as the seed namespace for the existing
    // InteractionEngine/SeededRandom path. No second RNG or seed subsystem is
    // introduced; every task simply owns a distinct namespace from startup.
    handle.page["interactionSeed"] = String(config.taskId);
    return handle;
  }

  override async shutdown(): Promise<void> {
    await super.shutdown();
    const starting = this.sharedVisionStart;
    if (starting) await starting.catch(() => undefined);
    await this.stopSharedVisionService();
  }

  private sharedVisionDisabled(): boolean {
    return process.env["ARES_SHARED_VISION_DISABLED"]?.trim() === "1";
  }

  private async ensureSharedVisionService(): Promise<SharedVisionService | undefined> {
    if (this.sharedVisionDisabled()) return undefined;

    // Respect an explicitly supplied external/shared service instead of
    // shadowing operator configuration.
    if (process.env["ARES_VISION_SERVICE_URL"]?.trim() && !this.sharedVision) return undefined;

    const existing = this.sharedVision;
    if (existing && existing.child.exitCode == null) {
      this.publishSharedVisionEnvironment(existing);
      return existing;
    }
    if (this.sharedVisionStart) return this.sharedVisionStart;

    this.sharedVisionStart = this.startSharedVisionService()
      .finally(() => { this.sharedVisionStart = undefined; });
    return this.sharedVisionStart;
  }

  private async startSharedVisionService(): Promise<SharedVisionService | undefined> {
    let child: ChildProcessWithoutNullStreams | undefined;
    try {
      const script = this.resolveVisionServiceScript();
      const token = randomBytes(24).toString("hex");
      const args = [
        "-u",
        script,
        "--host", "127.0.0.1",
        "--port", "0",
        "--token", token
      ];
      if (process.env["ARES_VISION_SERVICE_PRELOAD"]?.trim() !== "0") args.push("--preload");

      child = spawn(
        process.env["ARES_PYTHON_EXECUTABLE"]?.trim() || "python",
        args,
        {
          stdio: ["pipe", "pipe", "pipe"],
          windowsHide: true,
          env: { ...process.env, PYTHONUNBUFFERED: "1" }
        }
      );

      const started = await this.readStartupLine(child, 8_000);
      if (started["ready"] !== true) throw new Error(String(started["error"] || "shared vision service did not become ready"));
      const url = String(started["url"] || "").trim();
      if (!/^http:\/\/(?:127\.0\.0\.1|localhost):\d+$/.test(url)) {
        throw new Error(`shared vision service returned unsafe URL: ${url || "<empty>"}`);
      }

      const service: SharedVisionService = { child, url, token };
      this.sharedVision = service;
      this.publishSharedVisionEnvironment(service);
      child.once("exit", () => {
        const current = this.sharedVision;
        if (!current || current.child !== child) return;
        this.clearSharedVisionEnvironment(current);
        this.sharedVision = undefined;
      });
      return service;
    } catch (error) {
      if (child && child.exitCode == null) child.kill("SIGKILL");
      // Availability beats optimization: if the shared owner cannot start,
      // session processes retain the existing local lazy-classifier fallback.
      process.stderr.write(`[ARES vision] shared service unavailable; using local fallback: ${error instanceof Error ? error.message : String(error)}\n`);
      return undefined;
    }
  }

  private publishSharedVisionEnvironment(service: SharedVisionService): void {
    process.env["ARES_VISION_SERVICE_URL"] = service.url;
    process.env["ARES_VISION_SERVICE_TOKEN"] = service.token;
  }

  private clearSharedVisionEnvironment(service: SharedVisionService): void {
    if (process.env["ARES_VISION_SERVICE_URL"] === service.url) delete process.env["ARES_VISION_SERVICE_URL"];
    if (process.env["ARES_VISION_SERVICE_TOKEN"] === service.token) delete process.env["ARES_VISION_SERVICE_TOKEN"];
  }

  private async stopSharedVisionService(): Promise<void> {
    const service = this.sharedVision;
    this.sharedVision = undefined;
    if (!service) return;
    this.clearSharedVisionEnvironment(service);
    if (service.child.exitCode != null) return;
    service.child.kill("SIGTERM");
    const exited = await this.waitForChildExit(service.child, 3_000);
    if (!exited && service.child.exitCode == null) service.child.kill("SIGKILL");
  }

  private resolveVisionServiceScript(): string {
    const configured = process.env["ARES_VISION_SERVICE_SCRIPT"]?.trim();
    const resourcesPath = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath || "";
    const filename = "vision_inference_service.py";
    const candidates = [
      configured,
      path.join(process.cwd(), "python", "seleniumbase_cdp", filename),
      path.join(__dirname, "../../python/seleniumbase_cdp", filename),
      resourcesPath ? path.join(resourcesPath, "python", "seleniumbase_cdp", filename) : undefined
    ].filter((value): value is string => Boolean(value));
    const script = candidates.find(candidate => fs.existsSync(candidate));
    if (!script) throw new Error("Shared vision service script was not found. Set ARES_VISION_SERVICE_SCRIPT if needed.");
    return script;
  }

  private readStartupLine(child: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<Record<string, unknown>> {
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      let stdout = "";
      let stderr = "";
      let settled = false;
      const finish = (error?: Error, value?: Record<string, unknown>): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        child.stdout.removeListener("data", onStdout);
        child.stderr.removeListener("data", onStderr);
        child.removeListener("exit", onExit);
        if (error) reject(error); else resolve(value ?? {});
      };
      const onStderr = (chunk: Buffer | string): void => {
        stderr = `${stderr}${String(chunk)}`.slice(-4096);
      };
      const onStdout = (chunk: Buffer | string): void => {
        stdout += String(chunk);
        const newline = stdout.indexOf("\n");
        if (newline < 0) return;
        const line = stdout.slice(0, newline).trim();
        try {
          const value = JSON.parse(line) as Record<string, unknown>;
          finish(undefined, value);
        } catch (error) {
          finish(new Error(`invalid shared vision startup response: ${error instanceof Error ? error.message : String(error)}`));
        }
      };
      const onExit = (code: number | null): void => finish(new Error(`shared vision service exited during startup (${code ?? "signal"}): ${stderr.trim()}`));
      const timeout = setTimeout(() => finish(new Error(`shared vision service startup timed out: ${stderr.trim()}`)), timeoutMs);
      child.stdout.on("data", onStdout);
      child.stderr.on("data", onStderr);
      child.once("exit", onExit);
    });
  }

  private waitForChildExit(child: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<boolean> {
    if (child.exitCode != null) return Promise.resolve(true);
    return new Promise<boolean>(resolve => {
      let settled = false;
      const finish = (value: boolean): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        child.removeListener("exit", onExit);
        resolve(value);
      };
      const onExit = (): void => finish(true);
      const timeout = setTimeout(() => finish(false), timeoutMs);
      child.once("exit", onExit);
    });
  }
}
