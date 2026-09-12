import * as os from "os";
import * as path from "path";
import type { ITaskExecutor } from "../interfaces";
import type { Task } from "../models";
import type { CommerceShop } from "../commerce/platforms";
import type { AresProfile } from "../profiles/models";
import type { BrowserWorker } from "../browser-worker/browser-worker";
import { BrowserQueueWaiter } from "../browser-worker/queue-waiter";
import { SessionHttpPoller } from "./session-http-poller";
import { getMonitorStrategy, setEarlyGateRuntime } from "./early-gate";
import { resolveMonitorPolicy, type MonitorNetworkMode, type MonitorPolicy } from "./monitor-policy";

interface ActiveBrowserMonitor { controller: AbortController; }

export type { MonitorNetworkMode } from "./monitor-policy";

export interface BrowserGateMonitorExecutorOptions {
  pollIntervalMs?: number;
  refreshIntervalMs?: number;
}

const POST_NAVIGATION_OBSERVATION_MS = 30_000;
const RELEASE_STATUS_RE = /(released|complete|completed|redirect|passed|admitted)/i;

function proxyValue(profile: AresProfile): string | undefined {
  const proxy = profile.proxy;
  if (!proxy?.host || !proxy.port) return undefined;
  const auth = proxy.username ? `${proxy.username}:${proxy.password ?? ""}@` : "";
  const scheme = proxy.protocol || "http";
  return `${scheme}://${auth}${proxy.host}:${proxy.port}`;
}

function sessionPollUrl(task: Task, shop: CommerceShop): string {
  const taskValue = String(task.config.data?.["sessionHttpPollUrl"] ?? "").trim();
  const shopValue = String(shop.config?.["sessionHttpPollUrl"] ?? "").trim();
  const candidate = taskValue || shopValue || shop.baseUrl;
  return /^https?:\/\//i.test(candidate) ? candidate : shop.baseUrl;
}

export function resolveMonitorNetworkMode(task: Task, shop: CommerceShop): MonitorNetworkMode {
  return resolveMonitorPolicy(task, shop).networkMode;
}

function safeUrlLabel(value: string): string {
  try {
    const url = new URL(value);
    return `${url.host}${url.pathname}`;
  } catch {
    return "unknown";
  }
}

function safeLogText(value: unknown): string {
  return String(value ?? "").replace(/[\r\n\t]+/g, " ").slice(0, 600);
}

function monitorLog(taskId: string, message: string): void {
  process.stderr.write(`[MONITOR] task=${safeLogText(taskId)} ${safeLogText(message)}\n`);
}

/** Lightweight browser-backed gate observer. Checkout/payment modules are intentionally absent. */
export class BrowserGateMonitorExecutor implements ITaskExecutor {
  private readonly active = new Map<string, ActiveBrowserMonitor>();
  private readonly runtimeListeners = new Set<(task: Task) => void>();
  private readonly pollIntervalMs: number;
  private readonly refreshIntervalMs: number;

  constructor(
    private readonly getShop: (shopId: string) => CommerceShop | undefined,
    private readonly getProfile: (profileId: string) => AresProfile | undefined,
    private readonly browserWorker: BrowserWorker,
    options: BrowserGateMonitorExecutorOptions = {}
  ) {
    this.pollIntervalMs = Math.min(10_000, Math.max(250, options.pollIntervalMs ?? 750));
    this.refreshIntervalMs = Math.min(60_000, Math.max(POST_NAVIGATION_OBSERVATION_MS, options.refreshIntervalMs ?? 5_000));
  }

  onTaskUpdate(callback: (task: Task) => void): () => void {
    this.runtimeListeners.add(callback);
    return () => this.runtimeListeners.delete(callback);
  }

  async execute(task: Task): Promise<boolean | { success: boolean; handle?: import("../browser-worker/types").BrowserContextHandle }> {
    const strategy = getMonitorStrategy(task);
    const shopId = task.config.shopId;
    const profileId = String(task.config.data?.["profileId"] ?? "").trim();
    if (strategy.mode !== "early-gate" || !shopId || !profileId) {
      task.lastError = "Browser-Gate-Monitor benötigt Early-Gate, shopId und profileId.";
      return false;
    }
    const shop = this.getShop(shopId);
    const profile = this.getProfile(profileId);
    if (!shop || !profile) {
      task.lastError = !shop ? `Shop ${shopId} ist nicht registriert.` : `Profil ${profileId} ist nicht registriert.`;
      return false;
    }
    if (this.active.has(task.id)) {
      task.lastError = `Browser-Gate-Monitor ${task.id} läuft bereits.`;
      return false;
    }

    const controller = new AbortController();
    this.active.set(task.id, { controller });
    const profileRoot = process.env["ARES_BROWSER_PROFILE_ROOT"]?.trim() || path.join(os.tmpdir(), "ares-browser-profiles");
    const userDataDir = path.join(profileRoot, profile.id.replace(/[^a-zA-Z0-9_-]/g, "_"));
    const proxy = profile.proxy?.host && profile.proxy.port ? {
      protocol: profile.proxy.protocol || "http" as const,
      host: profile.proxy.host,
      port: profile.proxy.port,
      username: profile.proxy.username || undefined,
      password: profile.proxy.password || undefined
    } : undefined;
    const policy = resolveMonitorPolicy(task, shop);
    const networkMode = policy.networkMode;

    let sessionPoller: SessionHttpPoller | undefined;
    let lastSessionLogKey = "";
    let lastSessionError = "";
    let passiveIdleLogged = false;
    try {
      const handle = await this.browserWorker.createContext({
        taskId: task.id,
        targetId: shop.platform,
        userDataDir,
        headless: profile.browser?.headless ?? Boolean((task.config.data?.["browserConfig"] as Record<string, unknown> | undefined)?.["headless"]),
        proxy,
        userAgent: profile.browser?.userAgent?.trim() || undefined,
        viewport: null,
        navigationTimeoutMs: 30_000,
        actionTimeoutMs: 8_000,
        monitorMode: true
      });
      const page = handle.page;
      const pollUrl = sessionPollUrl(task, shop);

      monitorLog(task.id, `mode=${networkMode === "browser-only" ? "STRICT" : "NORMAL"} engine=${policy.engine} stage=${networkMode === "browser-only" ? "network" : "session-http"} profile=${profile.id}`);
      if (policy.sessionHttpRequested && !policy.sessionHttpAllowed) {
        monitorLog(task.id, "stage=session-http blocked=true reason=engine-locked-non-monitor engine=cdp harvest=cdp");
      }
      if (policy.sessionHttpAllowed) {
        sessionPoller = new SessionHttpPoller({
          url: pollUrl,
          profileDir: handle.userDataDir,
          proxy: proxyValue(profile),
          pollIntervalMs: Math.max(1_000, this.pollIntervalMs)
        });
        try {
          sessionPoller.start();
          monitorLog(task.id, `stage=session-http engine=curl_cffi target=${safeUrlLabel(pollUrl)}`);
        } catch (error) {
          sessionPoller = undefined;
          monitorLog(task.id, `session-http=start-failed fallback=${policy.allowPassiveNetwork ? "network" : policy.allowPassiveDom ? "dom" : "none"} error=${error instanceof Error ? error.message : String(error)}`);
        }
      } else {
        monitorLog(task.id, "stage=session-http skipped=true reason=browser-only engine=cdp harvest=cdp");
        monitorLog(task.id, `stage=${policy.allowPassiveNetwork ? "network" : policy.allowPassiveDom ? "dom" : "passive-idle"} source=${policy.allowPassiveNetwork ? "passive-cdp" : policy.allowPassiveDom ? "native-cdp-dom" : "none"}`);
      }

      setEarlyGateRuntime(task, {
        activeArea: "monitor", stage: "monitoring", productName: strategy.productName,
        keywords: strategy.discoveryKeywords, monitoringAt: new Date().toISOString()
      });
      task.config.data = {
        ...(task.config.data ?? {}),
        browserGateMonitor: {
          mode: "browser", profileId: profile.id, proxyBound: Boolean(proxy), networkMode, policy,
          userAgent: handle.environmentAudit.snapshot.userAgent,
          pollIntervalMs: this.pollIntervalMs, refreshIntervalMs: this.refreshIntervalMs,
          sessionHttp: {
            enabled: Boolean(sessionPoller),
            skipped: !policy.sessionHttpAllowed,
            url: policy.sessionHttpAllowed ? pollUrl : undefined,
            engine: policy.sessionHttpAllowed ? "curl_cffi" : "cdp",
            harvest: policy.sessionHttpAllowed ? "jar" : "cdp"
          },
          startedAt: new Date().toISOString()
        },
        monitorPipeline: {
          mode: networkMode,
          stage: networkMode === "browser-only" ? (policy.allowPassiveNetwork ? "network" : "dom") : "session-http",
          source: networkMode === "browser-only" ? (policy.allowPassiveNetwork ? "passive-cdp" : "native-cdp-dom") : "curl_cffi",
          updatedAt: new Date().toISOString()
        }
      };
      this.emit(task);

      const publishSessionTelemetry = (): void => {
        if (!sessionPoller) return;
        const signal = sessionPoller.getLatest();
        if (signal) {
          const key = [signal.statusCode ?? "-", signal.active, signal.position ?? "-", signal.timeToWaitSeconds ?? "-", signal.statusText ?? "", signal.productState ?? "-", signal.harvestSource ?? "-"].join("|");
          if (key !== lastSessionLogKey) {
            lastSessionLogKey = key;
            monitorLog(task.id, `stage=session-http status=${signal.statusCode ?? "-"} active=${signal.active} pos=${signal.position ?? "-"} ttw=${signal.timeToWaitSeconds ?? "-"} product=${signal.productState ?? "-"} harvest=${signal.harvestSource ?? "-"}`);
            task.config.data = {
              ...(task.config.data ?? {}),
              monitorPipeline: {
                mode: networkMode,
                stage: "session-http",
                source: "curl_cffi",
                statusCode: signal.statusCode,
                active: signal.active,
                position: signal.position,
                timeToWaitSeconds: signal.timeToWaitSeconds,
                statusText: signal.statusText,
                productState: signal.productState,
                available: signal.available,
                harvestSource: signal.harvestSource,
                cookieCount: signal.cookieCount,
                updatedAt: new Date().toISOString()
              }
            };
            this.emit(task);
          }
        }
        const error = sessionPoller.getError() ?? "";
        if (error && error !== lastSessionError) {
          lastSessionError = error;
          monitorLog(task.id, `session-http=error fallback=${policy.allowPassiveNetwork ? "network" : policy.allowPassiveDom ? "dom" : "none"} error=${error}`);
          task.config.data = {
            ...(task.config.data ?? {}),
            monitorPipeline: {
              mode: networkMode,
              stage: policy.allowPassiveNetwork ? "network" : policy.allowPassiveDom ? "dom" : "passive-idle",
              source: policy.allowPassiveNetwork ? "passive-cdp" : policy.allowPassiveDom ? "native-cdp-dom" : "none",
              fallbackFrom: "session-http",
              error,
              updatedAt: new Date().toISOString()
            }
          };
          this.emit(task);
        }
      };

      const waiter = new BrowserQueueWaiter(page, task, current => this.emit(current), {
        pollIntervalMs: this.pollIntervalMs,
        releaseConfirmations: 2,
        maxWaitMs: 60 * 60_000,
        allowPassiveNetwork: policy.allowPassiveNetwork,
        allowPassiveDom: policy.allowPassiveDom,
        challengePollIntervalMs: 7_000,
        challengeAction: () => {
          const solver = (page as unknown as { solveCaptcha?: () => Promise<boolean> }).solveCaptcha;
          return typeof solver === "function" ? solver.call(page) : undefined;
        },
        externalSignal: networkMode === "session-http-preferred" ? () => {
          publishSessionTelemetry();
          const signal = sessionPoller?.getLatest();
          if (!signal) return undefined;
          const authoritativeRelease = signal.active === false && Boolean(signal.statusText && RELEASE_STATUS_RE.test(signal.statusText));
          if (!signal.active && !authoritativeRelease) return undefined;
          return {
            active: signal.active,
            authoritativeRelease,
            position: signal.position,
            timeToWaitSeconds: signal.timeToWaitSeconds,
            statusText: signal.statusText,
            source: "session-http"
          };
        } : undefined
      });
      waiter.start();
      try {
        await page.goto(shop.baseUrl, { waitUntil: "domcontentloaded", timeout: 30_000 }).catch(() => undefined);

        if (networkMode === "session-http-preferred" && sessionPoller) {
          // Browser job is done: page loaded, anti-bot challenge/cookies are
          // being harvested into the jar. Wait until the DataDome/clearance
          // cookie actually lands in the jar (or the settle timeout), then
          // close the browser and continue polling with curl_cffi only.
          const settleMs = this.harvestSettleMs(task);
          const settleDeadline = Date.now() + settleMs;
          while (!controller.signal.aborted && Date.now() < settleDeadline) {
            publishSessionTelemetry();
            const signal = sessionPoller.getLatest();
            if (signal?.hasClearance) {
              monitorLog(task.id, "stage=session-http harvest=clearance cookie=datadome closing=browser");
              break;
            }
            await this.delay(1_000, controller.signal);
          }
          if (controller.signal.aborted) return true;
          monitorLog(task.id, "stage=session-http harvest=done closing=browser curl-only=true");
          task.config.data = {
            ...(task.config.data ?? {}),
            monitorPipeline: { mode: networkMode, stage: "session-http", source: "curl_cffi", browserClosed: true, updatedAt: new Date().toISOString() }
          };
          this.emit(task);
          waiter.stop();
          await this.browserWorker.closeContext(task.id);
          return this.runSessionHttpLoop(task, shopId, policy, networkMode, sessionPoller, publishSessionTelemetry, controller);
        }

        while (!controller.signal.aborted) {
          publishSessionTelemetry();
          const queue = await waiter.waitIfQueued();
          if (queue.detected) {
            const status = task.config.data?.["queueStatus"] as Record<string, unknown> | undefined;
            const detectedAt = String(status?.["detectedAt"] ?? new Date().toISOString());
            monitorLog(task.id, `queue=detected source=${String(status?.["source"] ?? "combined")} released=${queue.released}`);
            setEarlyGateRuntime(task, { activeArea: "gate", stage: "gate-detected", gateDetectedAt: detectedAt });
            task.config.data = {
              ...(task.config.data ?? {}),
              browserGateHandoff: {
                type: "queue-signal", shopId, observedAt: detectedAt,
                source: `browser-${String(status?.["source"] ?? "combined")}`,
                position: status?.["position"], timeToWaitSeconds: status?.["timeToWaitSeconds"],
                statusText: status?.["statusText"], released: queue.released,
                readyAt: new Date().toISOString()
              },
              monitorPipeline: {
                mode: networkMode,
                stage: queue.released ? "released" : "queue",
                source: String(status?.["source"] ?? "combined"),
                active: status?.["active"],
                position: status?.["position"],
                timeToWaitSeconds: status?.["timeToWaitSeconds"],
                statusText: status?.["statusText"],
                updatedAt: new Date().toISOString()
              }
            };
            this.emit(task);
            return { success: true, handle };
          }
          if (!queue.detected) {
            const probePage = page as unknown as { passiveQueueSnapshot?: () => Promise<unknown> };
            const snapshot = typeof probePage.passiveQueueSnapshot === "function"
              ? await probePage.passiveQueueSnapshot().catch(() => undefined)
              : undefined;
            task.config.data = {
              ...(task.config.data ?? {}),
              queueProbe: {
                pageUrl: page.url(),
                snapshot: snapshot ?? null,
                at: new Date().toISOString()
              }
            };
            this.emit(task);
          }
          await this.delay(this.refreshIntervalMs, controller.signal);
          if (controller.signal.aborted) continue;
          if (!policy.allowActiveBrowserFallback) {
            if (!passiveIdleLogged) {
              passiveIdleLogged = true;
              monitorLog(task.id, "stage=passive-idle active-browser-fallback=disabled");
            }
            task.config.data = {
              ...(task.config.data ?? {}),
              monitorPipeline: {
                mode: networkMode,
                stage: "passive-idle",
                source: policy.allowPassiveNetwork ? "passive-cdp" : policy.allowPassiveDom ? "native-cdp-dom" : "none",
                updatedAt: new Date().toISOString()
              }
            };
            this.emit(task);
            continue;
          }
          monitorLog(task.id, "stage=navigation reason=no-passive-signal-after-observation-window");
          task.config.data = {
            ...(task.config.data ?? {}),
            monitorPipeline: {
              mode: networkMode,
              stage: "navigation",
              source: "browser",
              updatedAt: new Date().toISOString()
            }
          };
          this.emit(task);
          await page.goto(shop.baseUrl, { waitUntil: "domcontentloaded", timeout: 30_000 }).catch(() => undefined);
        }
        return true;
      } finally { waiter.stop(); }
    } catch (error) {
      if (controller.signal.aborted) return true;
      task.lastError = error instanceof Error ? error.message : String(error);
      monitorLog(task.id, `runtime=failed error=${task.lastError}`);
      this.emit(task);
      return false;
    } finally {
      sessionPoller?.stop();
      this.active.delete(task.id);
      const queueStatus = task.config.data?.["queueStatus"] as Record<string, unknown> | undefined;
      const handoff = task.config.data?.["browserGateHandoff"] as Record<string, unknown> | undefined;
      // Keep the browser alive whenever a release was confirmed and a handle was
      // handed to the child lane. Closing here would invalidate the reused
      // context. Any non-handoff exit still closes eagerly.
      const released = queueStatus?.["phase"] === "released" || handoff?.["released"] === true;
      if (!released) {
        await this.browserWorker.closeContext(task.id).catch(() => undefined);
      }
    }
  }

  async cancelTask(taskId: string): Promise<void> {
    this.active.get(taskId)?.controller.abort();
    this.active.delete(taskId);
    await this.browserWorker.closeContext(taskId).catch(() => undefined);
  }

  async close(): Promise<void> {
    const ids = [...this.active.keys()];
    for (const run of this.active.values()) run.controller.abort();
    this.active.clear();
    await Promise.allSettled(ids.map(id => this.browserWorker.closeContext(id)));
    this.runtimeListeners.clear();
  }

  private emit(task: Task): void { for (const listener of this.runtimeListeners) listener(task); }

  private async runSessionHttpLoop(
    task: Task,
    shopId: string,
    policy: MonitorPolicy,
    networkMode: MonitorNetworkMode,
    sessionPoller: SessionHttpPoller,
    publishSessionTelemetry: () => void,
    controller: AbortController
  ): Promise<boolean> {
    while (!controller.signal.aborted) {
      publishSessionTelemetry();
      const signal = sessionPoller.getLatest();
      if (signal) {
        const released = signal.active === false && Boolean(signal.statusText && RELEASE_STATUS_RE.test(signal.statusText));
        const productAvailable = signal.available === true;
        if (released || productAvailable) {
          const detectedAt = new Date().toISOString();
          monitorLog(task.id, `queue=detected source=session-http released=${released} product=${signal.productState ?? "-"}`);
          setEarlyGateRuntime(task, { activeArea: "gate", stage: "gate-detected", gateDetectedAt: detectedAt });
          task.config.data = {
            ...(task.config.data ?? {}),
            browserGateHandoff: {
              type: "queue-signal", shopId, observedAt: detectedAt,
              source: "session-http", position: signal.position,
              timeToWaitSeconds: signal.timeToWaitSeconds, statusText: signal.statusText,
              released, productAvailable, readyAt: new Date().toISOString()
            },
            monitorPipeline: {
              mode: networkMode,
              stage: released ? "released" : "product-available",
              source: "curl_cffi",
              available: signal.available,
              productState: signal.productState,
              position: signal.position,
              timeToWaitSeconds: signal.timeToWaitSeconds,
              statusText: signal.statusText,
              updatedAt: new Date().toISOString()
            }
          };
          this.emit(task);
          return true;
        }
      }
      await this.delay(this.pollIntervalMs, controller.signal);
    }
    return true;
  }

  private harvestSettleMs(task: Task): number {
    const raw = Number(task.config.data?.["sessionHttpHarvestSettleMs"] ?? 60_000);
    return Number.isFinite(raw) ? Math.min(180_000, Math.max(15_000, raw)) : 60_000;
  }

  private delay(ms: number, signal: AbortSignal): Promise<void> {
    if (signal.aborted) return Promise.resolve();
    return new Promise(resolve => {
      const timer = setTimeout(done, ms);
      function done(): void { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); }
      signal.addEventListener("abort", done, { once: true });
    });
  }
}
