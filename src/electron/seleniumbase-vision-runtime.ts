import { spawn } from "child_process";
import type { ChildProcessWithoutNullStreams } from "child_process";
import { randomBytes } from "crypto";
import * as fs from "fs";
import * as path from "path";

export interface SeleniumBaseVisionRuntimeStatus {
  ready: boolean;
  dependenciesReady?: boolean;
  model?: string;
  device?: string;
  error?: string;
  sharedService?: boolean;
  serviceUrl?: string;
}

interface SharedVisionService {
  child: ChildProcessWithoutNullStreams;
  url: string;
  token: string;
}

export class SeleniumBaseVisionRuntime {
  private cachedReady?: SeleniumBaseVisionRuntimeStatus;
  private sharedService?: SharedVisionService;
  private sharedServiceStart?: Promise<SharedVisionService | undefined>;

  async status(): Promise<SeleniumBaseVisionRuntimeStatus> {
    const service = this.sharedService;
    if (service && service.child.exitCode == null) {
      return {
        ...(this.cachedReady ?? { ready: true }),
        ready: true,
        sharedService: true,
        serviceUrl: service.url
      };
    }
    if (this.cachedReady?.ready) return this.cachedReady;
    return this.run("--status", 30_000);
  }

  async prepare(): Promise<SeleniumBaseVisionRuntimeStatus> {
    if (!this.cachedReady?.ready) {
      if (process.env["ARES_VISION_AUTO_PREPARE"]?.trim() === "0") {
        this.cachedReady = await this.run("--status", 30_000);
      } else {
        this.cachedReady = await this.run("--prepare", 10 * 60_000);
      }
    }

    if (!this.cachedReady.ready) return this.cachedReady;
    const service = await this.ensureSharedService();
    return {
      ...this.cachedReady,
      sharedService: Boolean(service || process.env["ARES_VISION_SERVICE_URL"]?.trim()),
      serviceUrl: service?.url || process.env["ARES_VISION_SERVICE_URL"]?.trim()
    };
  }

  async ensureSharedService(): Promise<SharedVisionService | undefined> {
    if (process.env["ARES_SHARED_VISION_DISABLED"]?.trim() === "1") return undefined;

    const configuredUrl = process.env["ARES_VISION_SERVICE_URL"]?.trim();
    if (configuredUrl && !this.sharedService) return undefined;

    const existing = this.sharedService;
    if (existing && existing.child.exitCode == null) {
      this.publishSharedVisionEnvironment(existing);
      return existing;
    }
    if (this.sharedServiceStart) return this.sharedServiceStart;

    this.sharedServiceStart = this.startSharedVisionService()
      .finally(() => { this.sharedServiceStart = undefined; });
    return this.sharedServiceStart;
  }

  async shutdown(): Promise<void> {
    const starting = this.sharedServiceStart;
    if (starting) await starting.catch(() => undefined);
    const service = this.sharedService;
    this.sharedService = undefined;
    if (!service) return;

    this.clearSharedVisionEnvironment(service);
    if (service.child.exitCode != null) return;
    service.child.kill("SIGTERM");
    const exited = await this.waitForExit(service.child, 3_000);
    if (!exited && service.child.exitCode == null) service.child.kill("SIGKILL");
  }

  private async startSharedVisionService(): Promise<SharedVisionService | undefined> {
    let child: ChildProcessWithoutNullStreams | undefined;
    try {
      const script = this.resolveVisionServiceScript();
      const python = process.env["ARES_PYTHON_EXECUTABLE"]?.trim() || "python";
      const token = randomBytes(24).toString("hex");
      const args = [
        "-u",
        script,
        "--host", "127.0.0.1",
        "--port", "0",
        "--token", token,
        "--preload"
      ];

      child = spawn(python, args, {
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
        env: { ...process.env, PYTHONUNBUFFERED: "1" }
      });
      const runningChild = child;

      const started = await this.readStartupLine(runningChild, 8_000);
      if (started["ready"] !== true) {
        throw new Error(String(started["error"] || "Shared vision service did not become ready."));
      }
      const url = String(started["url"] || "").trim();
      if (!/^http:\/\/(?:127\.0\.0\.1|localhost):\d+$/.test(url)) {
        throw new Error(`Shared vision service returned unsafe URL: ${url || "<empty>"}`);
      }

      const service: SharedVisionService = { child: runningChild, url, token };
      this.sharedService = service;
      this.publishSharedVisionEnvironment(service);
      runningChild.once("exit", () => {
        if (this.sharedService?.child !== runningChild) return;
        this.clearSharedVisionEnvironment(service);
        this.sharedService = undefined;
      });
      return service;
    } catch (error) {
      if (child && child.exitCode == null) child.kill("SIGKILL");
      process.stderr.write(
        `[ARES vision] manual shared service unavailable; worker will use local fallback: ${error instanceof Error ? error.message : String(error)}\n`
      );
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

  private run(mode: "--status" | "--prepare", timeoutMs: number): Promise<SeleniumBaseVisionRuntimeStatus> {
    const script = this.resolveBootstrapScript();
    const python = process.env["ARES_PYTHON_EXECUTABLE"]?.trim() || "python";
    return new Promise((resolve, reject) => {
      const child = spawn(python, [script, mode], {
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
        env: { ...process.env }
      });
      let stdout = "";
      let stderr = "";
      let settled = false;
      const timeout = setTimeout(() => finishError(new Error("ARES Vision Runtime Timeout.")), timeoutMs);
      const cleanup = (): void => {
        clearTimeout(timeout);
        child.removeAllListeners();
        child.stdout.removeAllListeners();
        child.stderr.removeAllListeners();
      };
      const finishError = (error: Error): void => {
        if (settled) return;
        settled = true;
        cleanup();
        if (child.exitCode == null) child.kill("SIGTERM");
        reject(error);
      };
      const finish = (): void => {
        if (settled) return;
        settled = true;
        cleanup();
        const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
        const last = lines[lines.length - 1] || "{}";
        try {
          const value = JSON.parse(last) as SeleniumBaseVisionRuntimeStatus;
          if (!value.ready && stderr.trim() && !value.error) value.error = stderr.trim().slice(-2000);
          resolve(value);
        } catch {
          resolve({ ready: false, error: stderr.trim() || stdout.trim() || "Vision runtime returned no status." });
        }
      };
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stdout.on("data", chunk => { stdout += String(chunk); });
      child.stderr.on("data", chunk => { stderr = `${stderr}${String(chunk)}`.slice(-4000); });
      child.once("error", finishError);
      child.once("exit", finish);
    });
  }

  private resolveBootstrapScript(): string {
    const configured = process.env["ARES_VISION_BOOTSTRAP"]?.trim();
    const resourcesPath = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath || "";
    const candidates = [
      configured,
      path.join(process.cwd(), "python", "seleniumbase_cdp", "vision_runtime_bootstrap.py"),
      path.join(__dirname, "../../python/seleniumbase_cdp/vision_runtime_bootstrap.py"),
      resourcesPath ? path.join(resourcesPath, "python", "seleniumbase_cdp", "vision_runtime_bootstrap.py") : undefined
    ].filter((value): value is string => Boolean(value));
    const script = candidates.find(candidate => fs.existsSync(candidate));
    if (!script) throw new Error("ARES Vision Bootstrap wurde nicht gefunden.");
    return script;
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
    if (!script) throw new Error("ARES Shared Vision Service wurde nicht gefunden.");
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
      const onStdout = (chunk: Buffer | string): void => {
        stdout += String(chunk);
        const newline = stdout.indexOf("\n");
        if (newline < 0) return;
        const line = stdout.slice(0, newline).trim();
        try {
          finish(undefined, JSON.parse(line) as Record<string, unknown>);
        } catch (error) {
          finish(new Error(`Invalid shared vision startup response: ${error instanceof Error ? error.message : String(error)}`));
        }
      };
      const onStderr = (chunk: Buffer | string): void => {
        stderr = `${stderr}${String(chunk)}`.slice(-4096);
      };
      const onExit = (code: number | null): void => {
        finish(new Error(`Shared vision service exited during startup (${code ?? "signal"}): ${stderr.trim()}`));
      };
      const timeout = setTimeout(() => finish(new Error(`Shared vision service startup timed out: ${stderr.trim()}`)), timeoutMs);
      child.stdout.on("data", onStdout);
      child.stderr.on("data", onStderr);
      child.once("exit", onExit);
    });
  }

  private waitForExit(child: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<boolean> {
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
