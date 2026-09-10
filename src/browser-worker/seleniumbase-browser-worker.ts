import { spawn } from "child_process";
import type { ChildProcessWithoutNullStreams } from "child_process";
import * as fs from "fs";
import * as path from "path";
import type { BrowserWorker } from "./browser-worker";
import type { ProfileCookieSnapshotCookie } from "../cookies/profile-cookie-snapshot-vault";
import {
  BrowserContextAlreadyExistsError,
  BrowserProfileInUseError,
  BrowserWorkerStateError
} from "./errors";
import {
  acquireBrowserProfileLease,
  resolveProfileUserDataDir,
  type BrowserProfileLease
} from "./profile-session-manager";
import { collectBrowserEnvironment, failedBrowserEnvironmentAudit } from "./browser-environment-audit";
import { SeleniumBaseRpcPage, SeleniumBaseRpcTransport } from "./seleniumbase-rpc-page";
import { installWebRtcProxyPolicy } from "./webrtc/webrtc-proxy-policy";
import type {
  BrowserContext,
  BrowserContextConfig,
  BrowserContextHandle,
  BrowserProxyConfig,
  BrowserWorkerHealth,
  BrowserWorkerState
} from "./types";

interface RuntimeSession {
  child: ChildProcessWithoutNullStreams;
  transport: SeleniumBaseRpcTransport;
  page: SeleniumBaseRpcPage;
  context: BrowserContext;
  handle: BrowserContextHandle;
}

// WebRTC proxy policy: Forces WebRTC to report the SAME proxy IP as HTTP
// WebRTC is ALLOWED, not blocked – it routes through the Residential Proxy
// HTTP-IP = WebRTC-IP = Proxy-IP → perfect cross-layer consistency for DataDome
const WEBRTC_PROXY_POLICY = "--force-webrtc-ip-handling-policy=disable_non_proxied_udp";
const WEBRTC_PERMISSION_CHECK = "--enforce-webrtc-ip-permission-check";
// DoH (DNS-over-HTTPS) stays enabled so Chrome can fetch the HTTPS/SVCB record
// containing the ech= key. ECH (Encrypted Client Hello) then engages, which
// browserleaks.com/ech reports as Success=True. NetworkPrediction stays opted
// out to keep low-level network telemetry quiet.
const DISABLE_FEATURES = "--disable-features=NetworkPrediction";
const DNS_OVER_HTTPS_MODE = "--dns-over-https-mode=secure";
const DNS_OVER_HTTPS_TEMPLATES = "--dns-over-https-templates=https://dns.google/dns-query";
const ECH_FLAGS = [DNS_OVER_HTTPS_MODE, DNS_OVER_HTTPS_TEMPLATES];
const PROFILE_PRE_CLOSE_SETTLE_MS = 800;

// Safe headed Chrome flags - compatible with all Windows GPU configs
// Removed: --enable-unsafe-webgpu (crashes Chrome on some drivers)
// Removed: --angle-backend=gl (conflicts with ANGLE backend)
// Removed: --enable-gpu-completion-handler (invalid flag)
// WebRTC handled by Chrome policy flags below
const HEADED_CHROME_FLAGS = [
   "--enable-gpu-rasterization",
   "--enable-zero-copy",
   "--force-color-profile=srgb",
   "--disable-features=HeadlessChrome,AutomationControlled",
   "--disable-blink-features=AutomationControlled",
   "--font-render-hinting=medium",
   "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
];

// Only enforce headed mode for automation tasks.
// UI profiles and manual browsers respect their own headless setting.
// Production ARES tasks require headed mode for GPU/WebGL authenticity.
function enforceHeadedMode(config: BrowserContextConfig): BrowserContextConfig {
   // If headless is explicitly set to true (manual browser, UI profile),
   // respect that configuration - do NOT override
   if (config.headless === true) {
      return { ...config };
   }
   // Otherwise enforce headed mode for automated tasks
   return { ...config, headless: false, headed: true };
}

function proxyValue(proxy?: BrowserProxyConfig): string | undefined {
  if (!proxy) return undefined;
  if (!proxy.host?.trim()) throw new TypeError("Proxy host must not be empty.");
  if (!Number.isInteger(proxy.port) || proxy.port < 1 || proxy.port > 65535) {
    throw new RangeError(`Invalid proxy port: ${proxy.port}`);
  }
  const auth = proxy.username ? `${proxy.username}:${proxy.password ?? ""}@` : "";
  const endpoint = `${auth}${proxy.host.trim()}:${proxy.port}`;
  return proxy.protocol && proxy.protocol !== "http" ? `${proxy.protocol}://${endpoint}` : endpoint;
}

function browserArgs(config: BrowserContextConfig): string[] {
   const enforcedConfig = enforceHeadedMode(config);
   const args = [...(enforcedConfig.args ?? [])];
   if (!enforcedConfig.proxy) return args;
   const filtered = args.filter(arg =>
      !arg.startsWith("--force-webrtc-ip-handling-policy=")
      && !arg.startsWith("--disable-features=")
      && !arg.startsWith("--host-resolver-rules=")
      && !arg.startsWith("--dns-over-https-mode=")
      && !arg.startsWith("--dns-over-https-templates=")
      && arg !== "--enable-async-dns"
      && arg !== "--disable-async-dns"
   );
   if (!filtered.includes(WEBRTC_PERMISSION_CHECK)) filtered.push(WEBRTC_PERMISSION_CHECK);
   // DoH must run over Chrome's built-in async resolver; --disable-async-dns
   // is deliberately excluded above so it can never silently kill ECH.
   filtered.push(WEBRTC_PROXY_POLICY, ...ECH_FLAGS, DISABLE_FEATURES);
   // Only add headed Chrome flags when browser is NOT headless
   // UI profiles and manual browsers with headless=true don't need these
   if (!enforcedConfig.headless) {
      filtered.push(...HEADED_CHROME_FLAGS);
   }
   if (!enforcedConfig.proxy.bypass?.trim()) {
      // dns.google is excluded alongside the proxy host so the DoH bootstrap
      // query can leave the machine and ECH can fetch its ech= key. All other
      // hostnames keep resolving through the proxy (MAP * ~NOTFOUND blocks
      // direct resolution, which is what forces the WebRTC/proxy IP match).
      filtered.push(`--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE ${enforcedConfig.proxy.host.trim()} , EXCLUDE dns.google`);
   }
   return filtered;
}

export class SeleniumBaseBrowserWorker implements BrowserWorker {
  private readonly sessions = new Map<string, RuntimeSession>();
  private readonly pendingCreations = new Set<string>();
  private readonly activeProfileDirs = new Set<string>();
  private readonly taskProfileIds = new Map<string, string>();
  private readonly taskCookieSnapshots = new Map<string, ProfileCookieSnapshotCookie[]>();
  private readonly profileLeases = new Map<string, BrowserProfileLease>();
  private readonly startedAt = new Date();
  private state: BrowserWorkerState = "healthy";
  private lastError?: string;

  bindTaskProfile(taskId: string, profileId: string): void {
    const normalizedTaskId = String(taskId ?? "").trim();
    const normalizedProfileId = String(profileId ?? "").trim();
    if (!normalizedTaskId || !normalizedProfileId) throw new TypeError("taskId and profileId are required.");
    this.taskProfileIds.set(normalizedTaskId, normalizedProfileId);
  }

  setTaskCookieSnapshot(taskId: string, cookies: ProfileCookieSnapshotCookie[] | undefined): void {
    const id = String(taskId ?? "").trim();
    if (!id) throw new TypeError("taskId is required.");
    if (!cookies?.length) {
      this.taskCookieSnapshots.delete(id);
      return;
    }
    this.taskCookieSnapshots.set(id, cookies.map(cookie => ({ ...cookie })));
  }

  unbindTaskProfile(taskId: string): void {
    this.taskProfileIds.delete(taskId);
    this.taskCookieSnapshots.delete(taskId);
  }

  getBoundProfileId(taskId: string): string | undefined {
    return this.taskProfileIds.get(taskId);
  }

  getContext(taskId: string): BrowserContextHandle | undefined {
    return this.sessions.get(taskId)?.handle;
  }

  async createContext(config: BrowserContextConfig): Promise<BrowserContextHandle> {
    if (this.state !== "healthy") throw new BrowserWorkerStateError(this.state);
    if (this.sessions.has(config.taskId) || this.pendingCreations.has(config.taskId)) {
      throw new BrowserContextAlreadyExistsError(config.taskId);
    }

    const profileId = this.taskProfileIds.get(config.taskId);
    const requestedRoot = path.dirname(config.userDataDir);
    const effectiveUserDataDir = profileId
      ? resolveProfileUserDataDir(profileId, requestedRoot)
      : config.userDataDir;
    const normalizedDir = path.resolve(effectiveUserDataDir);
    if (this.activeProfileDirs.has(normalizedDir)) {
      throw new BrowserProfileInUseError(effectiveUserDataDir, `worker:${process.pid}:${config.taskId}`);
    }

    this.pendingCreations.add(config.taskId);
    this.activeProfileDirs.add(normalizedDir);
    fs.mkdirSync(normalizedDir, { recursive: true });
    let lease: BrowserProfileLease | undefined;
    let child: ChildProcessWithoutNullStreams | undefined;

    try {
      lease = acquireBrowserProfileLease(normalizedDir, `worker:${process.pid}:${config.taskId}`);
      this.profileLeases.set(config.taskId, lease);

      child = spawn(
        process.env["ARES_PYTHON_EXECUTABLE"]?.trim() || "python",
        ["-u", this.resolveWorkerScript()],
        {
          stdio: ["pipe", "pipe", "pipe"],
          windowsHide: true,
          env: {
            ...process.env,
            PYTHONUNBUFFERED: "1",
            ARES_SB_MONITOR_MODE: config.monitorMode === true ? "1" : "0"
          }
        }
      );
      const runningChild = child;
      const transport = new SeleniumBaseRpcTransport(runningChild);

      await transport.start({
        taskId: config.taskId,
        profileDir: normalizedDir,
        headless: config.headless ?? false,
        proxy: proxyValue(config.proxy),
        userAgent: config.userAgent || undefined,
        browserArgs: browserArgs(config),
        locale: config.locale,
        timezoneId: config.timezoneId
      }, 35_000);

      const page = new SeleniumBaseRpcPage(transport);
      page["passiveQueueSnapshot"] = async () => {
        const reply = await transport.request("rpc", { action: "passive-queue-dom" }, 5_000);
        const result = reply.result && typeof reply.result === "object" ? reply.result as Record<string, unknown> : {};
        return {
          hasQueuePosition: result["hasQueuePosition"] === true,
          hasPosition: result["hasPosition"] === true,
          positionText: String(result["positionText"] ?? ""),
          statusText: String(result["statusText"] ?? ""),
          url: String(result["url"] ?? reply.url ?? page.url())
        };
      };
      const context: BrowserContext = {
        addCookies: async cookies => {
          await transport.request("apply-cookies", { cookies }, 12_000);
        },
        addInitScript: async script => {
          const content = typeof script === "string" ? script : script.content;
          if (!content?.trim()) return;
          await transport.request("add-init-script", { script: content }, 8_000);
        },
        close: async () => {
          await page.closeTransport();
          await this.waitForExit(runningChild, 5_000);
          if (runningChild.exitCode == null) runningChild.kill("SIGKILL");
        }
      };

      if (config.proxy) await installWebRtcProxyPolicy(context);

      const snapshot = this.taskCookieSnapshots.get(config.taskId);
      if (snapshot?.length) {
        await context.addCookies(snapshot as unknown[]);
      }

      const environmentAudit = await collectBrowserEnvironment(page)
        .catch(error => failedBrowserEnvironmentAudit(error));
      const handle: BrowserContextHandle = {
        taskId: config.taskId,
        context,
        page,
        createdAt: new Date(),
        userDataDir: normalizedDir,
        environmentAudit
      };
      this.sessions.set(config.taskId, { child: runningChild, transport, page, context, handle });
      this.lastError = undefined;
      return handle;
    } catch (error) {
      if (child && child.exitCode == null) child.kill("SIGKILL");
      this.profileLeases.delete(config.taskId);
      lease?.release();
      this.activeProfileDirs.delete(normalizedDir);
      this.lastError = error instanceof Error ? error.message : String(error);
      throw error;
    } finally {
      this.pendingCreations.delete(config.taskId);
    }
  }

  async closeContext(taskId: string): Promise<void> {
    const session = this.sessions.get(taskId);
    const lease = this.profileLeases.get(taskId);
    this.sessions.delete(taskId);
    this.profileLeases.delete(taskId);

    if (!session) {
      lease?.release();
      return;
    }

    this.activeProfileDirs.delete(path.resolve(session.handle.userDataDir));
    try {
      await new Promise<void>(resolve => setTimeout(resolve, PROFILE_PRE_CLOSE_SETTLE_MS));
      await session.context.close();
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error);
      if (session.child.exitCode == null) session.child.kill("SIGKILL");
      throw error;
    } finally {
      lease?.release();
    }
  }

  async health(): Promise<BrowserWorkerHealth> {
    return {
      state: this.state,
      activeContexts: this.sessions.size,
      pendingCreations: this.pendingCreations.size,
      contextIds: [...this.sessions.keys()],
      startedAt: this.startedAt,
      uptimeMs: Date.now() - this.startedAt.getTime(),
      lastError: this.lastError
    };
  }

  async shutdown(): Promise<void> {
    if (this.state === "stopping" || this.state === "stopped") return;
    this.state = "stopping";
    const ids = [...this.sessions.keys()];
    const results = await Promise.allSettled(ids.map(id => this.closeContext(id)));
    for (const lease of this.profileLeases.values()) lease.release();
    this.profileLeases.clear();
    this.activeProfileDirs.clear();
    this.taskProfileIds.clear();
    this.taskCookieSnapshots.clear();
    const failed = results.filter(result => result.status === "rejected");
    if (failed.length) this.lastError = `${failed.length} SeleniumBase browser context(s) failed to close cleanly.`;
    this.state = "stopped";
  }

  private resolveWorkerScript(): string {
    const configured = process.env["ARES_SELENIUMBASE_TASK_WORKER"]?.trim();
    const resourcesPath = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath || "";
    const oopifWorker = "task_browser_worker_oopif.py";
    const legacyWorker = "task_browser_worker.py";
    const candidates = [
      configured,
      path.join(process.cwd(), "python", "seleniumbase_cdp", oopifWorker),
      path.join(__dirname, "../../python/seleniumbase_cdp", oopifWorker),
      resourcesPath ? path.join(resourcesPath, "python", "seleniumbase_cdp", oopifWorker) : undefined,
      path.join(process.cwd(), "python", "seleniumbase_cdp", legacyWorker),
      path.join(__dirname, "../../python/seleniumbase_cdp", legacyWorker),
      resourcesPath ? path.join(resourcesPath, "python", "seleniumbase_cdp", legacyWorker) : undefined
    ].filter((value): value is string => Boolean(value));
    const worker = candidates.find(candidate => fs.existsSync(candidate));
    if (!worker) throw new Error("SeleniumBase task worker was not found. Set ARES_SELENIUMBASE_TASK_WORKER if needed.");
    return worker;
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
