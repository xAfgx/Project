import { spawn } from "child_process";
import type { ChildProcessWithoutNullStreams } from "child_process";
import * as fs from "fs";
import * as path from "path";
import { randomUUID } from "crypto";
import type { Task } from "../models";
import type { CommerceShop } from "../commerce/platforms";
import { GenericHtmlProductApiAdapter } from "../commerce/product-api/generic-html-product-api-adapter";
import type { TextHttpClient, TextHttpResponse } from "../commerce/product-api/types";
import type { ProductObservation, ProductQuery } from "../monitor/models";
import type { BrowserProductFallback } from "../monitor/browser-product-fallback";
import type { AresProxy, ProxySelection } from "../proxies/models";

const WIRE_PREFIX = "ARES_MONITOR_BROWSER\t";

interface MonitorRuntimeAction {
  mode?: string;
  headless?: boolean;
  runtimeProfileId?: string;
  runtimeUserAgent?: string;
  runtimePreferredProxyId?: string;
  proxySelection?: ProxySelection;
}

interface BrowserSession {
  taskId: string;
  child: ChildProcessWithoutNullStreams;
  profileDir: string;
  startedAt: string;
}

interface BrowserWireMessage {
  type?: string;
  requestId?: string;
  pid?: number;
  url?: string;
  title?: string;
  html?: string;
  text?: string;
  readyState?: string;
  stable?: boolean;
  error?: string;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function safeSegment(value: string): string {
  return value.replace(/[^a-z0-9._-]+/gi, "_").slice(0, 120) || "monitor";
}

export class SeleniumBaseProductMonitorBrowserAdapter implements BrowserProductFallback {
  private readonly sessions = new Map<string, BrowserSession>();

  constructor(
    private readonly profileRoot: string,
    private readonly getProxy: (proxyId: string) => AresProxy | undefined
  ) {}

  async search(
    task: Task,
    shop: CommerceShop,
    query: ProductQuery,
    limit = 50,
    signal?: AbortSignal
  ): Promise<ProductObservation[]> {
    if (signal?.aborted) return [];
    const session = await this.ensureSession(task, shop);
    const http: TextHttpClient = {
      get: async (url: string): Promise<TextHttpResponse> => {
        if (signal?.aborted) throw new Error("Monitor browser request aborted.");
        const document = await this.render(session, url);
        if (signal?.aborted) throw new Error("Monitor browser request aborted.");
        return {
          status: 200,
          headers: { "content-type": "text/html; charset=utf-8" },
          text: document.html || document.text || "",
          url: document.url || url
        };
      }
    };
    const parser = new GenericHtmlProductApiAdapter(http);
    const observations = await parser.search(shop, query, limit);
    return observations.map(observation => ({
      ...observation,
      attributes: {
        ...(observation.attributes ?? {}),
        source: "seleniumbase-rendered-html",
        browserRendered: true
      }
    }));
  }

  async cancelTask(taskId: string): Promise<void> {
    const id = String(taskId ?? "").trim();
    const session = this.sessions.get(id);
    if (!session) return;
    this.sessions.delete(id);
    await this.closeSession(session);
  }

  async close(): Promise<void> {
    const sessions = [...this.sessions.values()];
    this.sessions.clear();
    await Promise.allSettled(sessions.map(session => this.closeSession(session)));
  }

  private async ensureSession(task: Task, shop: CommerceShop): Promise<BrowserSession> {
    const taskId = String(task.id ?? "").trim();
    if (!taskId) throw new Error("Monitor browser task id is missing.");
    const existing = this.sessions.get(taskId);
    if (existing && existing.child.exitCode == null) return existing;
    if (existing) this.sessions.delete(taskId);

    const action = this.monitorAction(task);
    const profileDir = path.join(
      this.profileRoot,
      "monitor-runtime",
      safeSegment(action.runtimeProfileId || taskId),
      safeSegment(taskId)
    );
    fs.mkdirSync(profileDir, { recursive: true });

    const workerScript = this.resolveWorkerScript();
    const python = process.env["ARES_PYTHON_EXECUTABLE"]?.trim() || "python";
    const child = spawn(python, [workerScript], {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
      env: { ...process.env }
    });
    const session: BrowserSession = {
      taskId,
      child,
      profileDir,
      startedAt: new Date().toISOString()
    };
    this.sessions.set(taskId, session);
    child.once("exit", () => {
      if (this.sessions.get(taskId)?.child === child) this.sessions.delete(taskId);
    });

    const requestId = randomUUID();
    const ready = this.waitForMessage(child, requestId, "ready", 45_000, true);
    child.stdin.write(`${JSON.stringify({
      type: "start",
      requestId,
      taskId,
      profileDir,
      startUrl: shop.baseUrl,
      headless: action.headless === true,
      userAgent: action.runtimeUserAgent || undefined,
      proxy: this.proxyString(this.resolveProxy(action))
    })}\n`);
    try {
      await ready;
      return session;
    } catch (error) {
      this.sessions.delete(taskId);
      if (child.exitCode == null) child.kill("SIGTERM");
      throw error;
    }
  }

  private async render(session: BrowserSession, url: string): Promise<Required<Pick<BrowserWireMessage, "url" | "html">> & BrowserWireMessage> {
    const requestId = randomUUID();
    const response = this.waitForMessage(session.child, requestId, "rendered-document", 20_000, true);
    session.child.stdin.write(`${JSON.stringify({
      type: "render",
      requestId,
      url,
      stableMs: 650,
      timeoutMs: 9_000
    })}\n`);
    const message = await response;
    if (!message.url) throw new Error("SeleniumBase monitor returned no document URL.");
    if (typeof message.html !== "string") throw new Error("SeleniumBase monitor returned no rendered HTML.");
    return message as Required<Pick<BrowserWireMessage, "url" | "html">> & BrowserWireMessage;
  }

  private monitorAction(task: Task): MonitorRuntimeAction {
    return asRecord(task.config.data?.["monitorAction"]) as MonitorRuntimeAction | undefined ?? {};
  }

  private resolveProxy(action: MonitorRuntimeAction): AresProxy | undefined {
    const selection = action.proxySelection;
    if (selection?.mode === "direct") return undefined;
    if (selection?.mode === "proxy") {
      const proxyId = String(selection.proxyId ?? "").trim();
      if (!proxyId) throw new Error("Monitor proxy selection has no proxy id.");
      const proxy = this.getProxy(proxyId);
      if (!proxy) throw new Error(`Monitor proxy ${proxyId} was not found.`);
      return proxy;
    }
    const preferred = String(action.runtimePreferredProxyId ?? "").trim();
    if (!preferred) return undefined;
    const proxy = this.getProxy(preferred);
    if (!proxy) throw new Error(`Monitor runtime profile proxy ${preferred} was not found.`);
    return proxy;
  }

  private proxyString(proxy?: AresProxy): string | undefined {
    if (!proxy?.host || !proxy.port) return undefined;
    const auth = proxy.username ? `${proxy.username}:${proxy.password ?? ""}@` : "";
    const endpoint = `${auth}${proxy.host}:${proxy.port}`;
    return proxy.protocol && proxy.protocol !== "http" ? `${proxy.protocol}://${endpoint}` : endpoint;
  }

  private async closeSession(session: BrowserSession): Promise<void> {
    const child = session.child;
    if (child.exitCode != null) return;
    const requestId = randomUUID();
    try {
      const closed = this.waitForMessage(child, requestId, "closed", 8_000);
      child.stdin.write(`${JSON.stringify({ type: "close", requestId })}\n`);
      await closed;
    } catch {
      if (child.exitCode == null) child.kill("SIGTERM");
    }
  }

  private resolveWorkerScript(): string {
    const configured = process.env["ARES_MONITOR_BROWSER_WORKER"]?.trim();
    const resourcesPath = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath || "";
    const candidates = [
      configured,
      path.join(process.cwd(), "python", "seleniumbase_cdp", "product_monitor_browser.py"),
      path.join(__dirname, "../../python/seleniumbase_cdp/product_monitor_browser.py"),
      resourcesPath ? path.join(resourcesPath, "python", "seleniumbase_cdp", "product_monitor_browser.py") : undefined
    ].filter((value): value is string => Boolean(value));
    const worker = candidates.find(candidate => fs.existsSync(candidate));
    if (!worker) throw new Error("SeleniumBase product monitor worker was not found.");
    return worker;
  }

  private waitForMessage(
    child: ChildProcessWithoutNullStreams,
    requestId: string,
    expectedType: string,
    timeoutMs: number,
    includeStderr = false
  ): Promise<BrowserWireMessage> {
    return new Promise<BrowserWireMessage>((resolve, reject) => {
      let stdoutBuffer = "";
      let stderrBuffer = "";
      let settled = false;
      const timeout = setTimeout(() => finishError(new Error(`SeleniumBase monitor ${expectedType} timeout.`)), timeoutMs);
      const cleanup = (): void => {
        clearTimeout(timeout);
        child.stdout.removeListener("data", onStdout);
        child.stderr.removeListener("data", onStderr);
        child.removeListener("exit", onExit);
        child.removeListener("error", onError);
      };
      const finishError = (error: Error): void => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(error);
      };
      const finishSuccess = (message: BrowserWireMessage): void => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(message);
      };
      const onExit = (code: number | null): void => {
        const detail = includeStderr && stderrBuffer.trim() ? ` ${stderrBuffer.trim()}` : "";
        finishError(new Error(`SeleniumBase monitor process exited (code=${String(code)}).${detail}`));
      };
      const onError = (error: Error): void => finishError(error);
      const onStderr = (chunk: unknown): void => {
        stderrBuffer = `${stderrBuffer}${String(chunk)}`.slice(-5_000);
      };
      const onStdout = (chunk: unknown): void => {
        stdoutBuffer += String(chunk);
        const lines = stdoutBuffer.split(/\r?\n/);
        stdoutBuffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith(WIRE_PREFIX)) continue;
          let message: BrowserWireMessage;
          try {
            message = JSON.parse(line.slice(WIRE_PREFIX.length)) as BrowserWireMessage;
          } catch {
            continue;
          }
          if (message.requestId && message.requestId !== requestId) continue;
          if (message.type === "error") {
            finishError(new Error(message.error || "SeleniumBase monitor worker error."));
            return;
          }
          if (message.type === expectedType) {
            finishSuccess(message);
            return;
          }
        }
      };
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stdout.on("data", onStdout);
      child.stderr.on("data", onStderr);
      child.once("exit", onExit);
      child.once("error", onError);
    });
  }
}
