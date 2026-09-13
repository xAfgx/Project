import { Injectable } from "@angular/core";
import { COMMERCE_PLATFORMS } from "../../commerce/platforms";
import { getSelectedCookieSnapshot } from "./profile-cookie-snapshot-selection";

interface BrowserPreviewTask {
  id: string;
  config: {
    name: string;
    shopId?: string;
    data?: Record<string, unknown>;
  };
  state: string;
  retries: number;
  maxRetries: number;
  lastError?: string;
}

interface BrowserPreviewLog {
  id: number;
  taskId: string;
  event: string;
  state?: string;
  level: "info" | "warn" | "error";
  message: string;
  createdAt: string;
}

@Injectable({ providedIn: "root" })
export class ElectronService {
  private readonly previewProfiles: any[] = [];
  private readonly previewProxies: any[] = [];
  private readonly previewShops: any[] = [];
  private readonly previewTasks: BrowserPreviewTask[] = [];
  private readonly previewTaskLogs = new Map<string, BrowserPreviewLog[]>();
  private readonly previewMonitorEvents = new Map<string, any[]>();
  private readonly previewPaymentSessions = new Map<string, unknown>();
  private previewLogId = 0;
  private previewAllowFinalPurchase = true;

  private get api(): any | undefined {
    return (window as any).ares;
  }

  getProfiles(): Promise<any> {
    if (this.api) return this.api.getProfiles();
    return Promise.resolve({ success: true, profiles: this.previewProfiles });
  }

  saveProfile(profile: unknown): Promise<any> {
    if (this.api) return this.api.saveProfile(profile);
    const savedProfile = profile as { id?: string };
    const index = this.previewProfiles.findIndex(item => item.id === savedProfile.id);
    if (index >= 0) this.previewProfiles[index] = profile;
    else this.previewProfiles.push(profile);
    return Promise.resolve({ success: true, profile });
  }

  deleteProfile(profileId: string): Promise<any> {
    if (this.api) return this.api.deleteProfile(profileId);
    const index = this.previewProfiles.findIndex(item => item.id === profileId);
    if (index >= 0) this.previewProfiles.splice(index, 1);
    return Promise.resolve({ success: true });
  }

  getProfileBrowserStatus(profileId: string): Promise<any> {
    if (this.api?.getProfileBrowserStatus) return this.api.getProfileBrowserStatus(profileId);
    return Promise.resolve({ success: true, status: { engine: "seleniumbase-cdp", profileId, open: false } });
  }

  openProfileBrowser(profileId: string, startUrl?: string): Promise<any> {
    if (this.api?.openProfileBrowser) return this.api.openProfileBrowser(profileId, startUrl);
    return Promise.resolve({
      success: true,
      status: { engine: "seleniumbase-cdp", profileId, open: true, startedAt: new Date().toISOString(), startUrl }
    });
  }

  closeProfileBrowser(profileId: string): Promise<any> {
    if (this.api?.closeProfileBrowser) return this.api.closeProfileBrowser(profileId);
    return Promise.resolve({ success: true, status: { engine: "seleniumbase-cdp", profileId, open: false } });
  }

  getSeleniumBaseVisionStatus(): Promise<any> {
    if (this.api?.getSeleniumBaseVisionStatus) return this.api.getSeleniumBaseVisionStatus();
    return Promise.resolve({
      success: true,
      status: {
        ready: false,
        dependenciesReady: false,
        model: "SigLIP2",
        device: "local",
        error: "SigLIP2 kann nur in der Electron-App manuell gestartet werden."
      }
    });
  }

  prepareSeleniumBaseVision(): Promise<any> {
    if (this.api?.prepareSeleniumBaseVision) return this.api.prepareSeleniumBaseVision();
    return Promise.resolve({
      success: false,
      error: "SigLIP2 lokal laden ist im Browser-Preview nicht verfügbar. Starte die Electron-App, damit der echte Vision-Prozess gestartet wird."
    });
  }

  getProxies(): Promise<any> {
    if (this.api) return this.api.getProxies();
    return Promise.resolve({ success: true, proxies: this.previewProxies });
  }

  saveProxy(proxy: unknown): Promise<any> {
    if (this.api) return this.api.saveProxy(proxy);
    const saved = proxy as { id?: string };
    const index = this.previewProxies.findIndex(item => item.id === saved.id);
    if (index >= 0) this.previewProxies[index] = proxy;
    else this.previewProxies.push(proxy);
    return Promise.resolve({ success: true, proxy });
  }

  testProxy(proxyId: string): Promise<any> {
    if (this.api) return this.api.testProxy(proxyId);
    const proxy = this.previewProxies.find(item => item.id === proxyId);
    if (!proxy) return Promise.resolve({ success: false, error: `Proxy ${proxyId} wurde nicht gefunden.` });
    proxy.health = {
      proxyId,
      status: "online",
      checkedAt: new Date().toISOString(),
      latencyMs: 74,
      exitIp: "203.0.113.42",
      geo: { country: "Germany", countryCode: "DE", region: "Hamburg", city: "Hamburg", provider: "Preview ISP", asn: "AS64500" },
      reputation: { source: "proxycheck.io", available: true, riskScore: 12, riskLevel: "low", attackTotal: 0, spamHits: 0, proxyDetected: true, detectedType: "Residential" }
    };
    return Promise.resolve({ success: true, proxy, health: proxy.health });
  }

  async testAllProxies(): Promise<any> {
    if (this.api) return this.api.testAllProxies();
    const results = [];
    for (const proxy of this.previewProxies) results.push(await this.testProxy(proxy.id));
    return { success: true, results, proxies: this.previewProxies };
  }

  deleteProxy(proxyId: string): Promise<any> {
    if (this.api) return this.api.deleteProxy(proxyId);
    const assignedProfile = this.previewProfiles.find(item => item.preferredProxyId === proxyId);
    if (assignedProfile) {
      return Promise.resolve({ success: false, error: `Proxy ist Profil ${assignedProfile.name || assignedProfile.id} zugeordnet.` });
    }
    const index = this.previewProxies.findIndex(item => item.id === proxyId);
    if (index >= 0) this.previewProxies.splice(index, 1);
    return Promise.resolve({ success: true });
  }

  getShops(): Promise<any> {
    if (this.api) return this.api.getShops();
    return Promise.resolve({
      success: true,
      shops: this.previewShops,
      platforms: COMMERCE_PLATFORMS,
      executorPlatforms: ["shopify", "mediamarkt"],
      monitorReady: true,
      earlyGateReady: true
    });
  }

  registerShop(config: unknown): Promise<any> {
    if (this.api) return this.api.registerShop(config);
    const shop = config as { id?: string; name?: string; baseUrl?: string; platform?: string };
    const normalizedShop = { ...shop, name: shop.name || shop.id, platform: shop.platform || "shopify" };
    const index = this.previewShops.findIndex(item => item.id === shop.id);
    if (index >= 0) this.previewShops[index] = normalizedShop;
    else this.previewShops.push(normalizedShop);
    return Promise.resolve({
      success: true,
      shop: normalizedShop,
      executorReady: normalizedShop.platform === "shopify" || normalizedShop.platform === "mediamarkt",
      monitorReady: true,
      earlyGateReady: true
    });
  }

  createTask(config: unknown): Promise<any> {
    const enriched = this.attachCookieSnapshotSelection(config);
    if (this.api) return this.api.createTask(enriched);
    const taskConfig = enriched as { id?: string; name?: string; shopId?: string; data?: Record<string, unknown> };
    const task: BrowserPreviewTask = {
      id: taskConfig.id || `preview_task_${Date.now()}`,
      config: {
        name: taskConfig.name || "Preview Task",
        shopId: taskConfig.shopId,
        data: {
          ...(taskConfig.data ?? {}),
          liveChallengeStatus: "Browser-Vorschau: echtes Captcha-Handling läuft nur in Electron.",
          liveChallengeType: "preview"
        }
      },
      state: "QUEUED",
      retries: 0,
      maxRetries: 0
    };
    this.previewTasks.push(task);
    this.recordPreviewLog(task, "taskCreated", "info", "Task erstellt");
    this.recordPreviewLog(task, "taskStateChanged", "info", "CREATED -> QUEUED");
    return Promise.resolve({ success: true, taskId: task.id, task });
  }

  setPaymentSession(taskId: string, payment: unknown): Promise<any> {
    if (this.api) return this.api.setPaymentSession(taskId, payment);
    this.previewPaymentSessions.set(taskId, payment);
    return Promise.resolve({ success: true });
  }

  clearPaymentSession(taskId: string): Promise<any> {
    if (this.api) return this.api.clearPaymentSession(taskId);
    this.previewPaymentSessions.delete(taskId);
    return Promise.resolve({ success: true });
  }

  async startTask(taskId: string): Promise<any> {
    await this.ensureProfilePaymentSession(taskId);
    if (this.api) return this.api.startTask(taskId);
    const task = this.previewTasks.find(item => item.id === taskId);
    if (task) {
      task.state = "RUNNING";
      this.recordPreviewLog(task, "taskStateChanged", "info", "QUEUED -> RUNNING");
    }
    return { success: true, task };
  }

  pauseTask(taskId: string): Promise<any> {
    if (this.api) return this.api.pauseTask(taskId);
    const task = this.previewTasks.find(item => item.id === taskId);
    if (task) {
      const previous = task.state;
      task.state = "PAUSED";
      this.recordPreviewLog(task, "taskStateChanged", "warn", `${previous} -> PAUSED`);
    }
    return Promise.resolve({ success: true, task });
  }

  resumeTask(taskId: string): Promise<any> {
    if (this.api) return this.api.resumeTask(taskId);
    const task = this.previewTasks.find(item => item.id === taskId);
    if (task) {
      task.state = "QUEUED";
      this.recordPreviewLog(task, "taskStateChanged", "info", "PAUSED -> QUEUED");
    }
    return Promise.resolve({ success: true, task });
  }

  stopTask(taskId: string): Promise<any> {
    if (this.api) return this.api.stopTask(taskId);
    this.previewPaymentSessions.delete(taskId);
    const task = this.previewTasks.find(item => item.id === taskId);
    if (task) {
      const previous = task.state;
      task.state = "CANCELLED";
      this.recordPreviewLog(task, "taskStateChanged", "warn", `${previous} -> CANCELLED`);
    }
    return Promise.resolve({ success: true, task });
  }

  deleteTask(taskId: string): Promise<any> {
    if (this.api) return this.api.deleteTask(taskId);
    const index = this.previewTasks.findIndex(item => item.id === taskId);
    if (index >= 0) this.previewTasks.splice(index, 1);
    this.previewTaskLogs.delete(taskId);
    this.previewPaymentSessions.delete(taskId);
    return Promise.resolve({ success: true, taskId });
  }

  restartTask(taskId: string): Promise<any> {
    if (this.api) return this.ensureProfilePaymentSession(taskId).then(() => this.api!.restartTask(taskId));
    const task = this.previewTasks.find(item => item.id === taskId);
    if (task) {
      const previous = task.state;
      task.state = "QUEUED";
      task.retries = 0;
      task.lastError = undefined;
      this.recordPreviewLog(task, "taskStateChanged", "info", `${previous} -> QUEUED`);
    }
    return Promise.resolve({ success: true, task });
  }

  updateDiscoveryKeywords(taskId: string, keywords: string[]): Promise<any> {
    if (this.api) return this.api.updateDiscoveryKeywords(taskId, keywords);
    const task = this.previewTasks.find(item => item.id === taskId);
    if (!task || task.state !== "POST_QUEUE_DISCOVERY") {
      return Promise.resolve({ success: false, error: "Discovery-Keywords können nur während POST_QUEUE_DISCOVERY geändert werden." });
    }
    const normalized = [...new Set(keywords.map(value => String(value).trim()).filter(Boolean))];
    const postQueue = task.config.data?.["postQueueDiscovery"] as Record<string, unknown> | undefined;
    task.config.data = {
      ...(task.config.data ?? {}),
      postQueueDiscovery: { ...(postQueue ?? {}), keywords: normalized, updatedAt: new Date().toISOString() }
    };
    return Promise.resolve({ success: true, taskId, keywords: normalized });
  }

  getFinalPurchaseSetting(): Promise<any> {
    if (this.api) return this.api.getFinalPurchaseSetting();
    return Promise.resolve({ success: true, allowFinalPurchase: this.previewAllowFinalPurchase });
  }

  setFinalPurchaseAllowed(allowed: boolean): Promise<any> {
    if (this.api) return this.api.setFinalPurchaseAllowed(allowed);
    this.previewAllowFinalPurchase = allowed === true;
    return Promise.resolve({ success: true, allowFinalPurchase: this.previewAllowFinalPurchase });
  }

  getTaskStatus(taskId: string): Promise<any> {
    if (this.api) return this.api.getTaskStatus(taskId);
    const task = this.previewTasks.find(item => item.id === taskId);
    return Promise.resolve(task
      ? { success: true, status: task.state, task }
      : { success: false, error: `Task ${taskId} not found.` });
  }

  getTaskList(): Promise<any> {
    if (this.api) return this.api.getTaskList();
    return Promise.resolve({ success: true, tasks: this.previewTasks });
  }

  getTaskLogs(taskId: string, limit = 100): Promise<any> {
    if (this.api) return this.api.getTaskLogs(taskId, limit);
    const safeLimit = Math.min(500, Math.max(1, Math.floor(limit)));
    const logs = this.previewTaskLogs.get(taskId) ?? [];
    return Promise.resolve({ success: true, logs: logs.slice(-safeLimit) });
  }

  getProductMonitorEvents(taskId: string, limit = 100): Promise<any> {
    if (this.api) return this.api.getProductMonitorEvents(taskId, limit);
    const safeLimit = Math.min(500, Math.max(1, Math.floor(limit)));
    const events = this.previewMonitorEvents.get(taskId) ?? [];
    return Promise.resolve({ success: true, events: events.slice(-safeLimit) });
  }

  getSystemStatus(): Promise<any> {
    if (this.api) return this.api.getSystemStatus();
    return Promise.resolve({
      success: true,
      availableWorkers: 0,
      shopCount: this.previewShops.length,
      taskCount: this.previewTasks.length,
      profileCount: this.previewProfiles.length,
      proxyCount: this.previewProxies.length,
      commercePlatforms: COMMERCE_PLATFORMS,
      commerceExecutorPlatforms: ["shopify", "mediamarkt"],
      commerceMonitorReady: true,
      earlyGateReady: true,
      allowFinalPurchase: this.previewAllowFinalPurchase,
      captchaProvider: "CapMonster",
      captchaApiKeyConfigured: false,
      liveChallengeSupport: ["turnstile", "recaptcha", "shopify-checkpoint"],
      electronNodeVersion: undefined,
      systemNodeRequirement: ">=20",
      systemNode: { executable: "browser-preview", version: "n/a", ok: true },
      persistence: { type: "preview", ready: true },
      browserPreview: true
    });
  }

  testCapmonsterApiKey(): Promise<any> {
    if (this.api?.testCapmonsterApiKey) return this.api.testCapmonsterApiKey();
    return Promise.resolve({
      provider: "CapMonster",
      configured: false,
      success: false,
      checkedAt: new Date().toISOString(),
      error: "API-Key-Check ist nur in der Electron-App verfügbar."
    });
  }

  onTaskStatusUpdate(callback: (task: unknown) => void): () => void {
    if (this.api?.onTaskStatusUpdate) {
      const unsub = this.api.onTaskStatusUpdate(callback);
      return () => {
        if (typeof unsub === "function") unsub();
        else this.api?.removeTaskStatusListener?.(callback);
      };
    }
    return () => undefined;
  }

  onProductMonitorUpdate(callback: (payload: unknown) => void): () => void {
    if (this.api?.onProductMonitorUpdate) {
      const unsub = this.api.onProductMonitorUpdate(callback);
      return () => {
        if (typeof unsub === "function") unsub();
        else this.api?.removeProductMonitorListener?.(callback);
      };
    }
    return () => undefined;
  }

  private attachCookieSnapshotSelection(config: unknown): unknown {
    if (!config || typeof config !== "object") return config;
    const taskConfig = config as { data?: Record<string, unknown> };
    const data = taskConfig.data;
    if (!data) return config;
    const action = data["monitorAction"] as Record<string, unknown> | undefined;
    if (!action || action["mode"] !== "auto-checkout") return config;
    const profileId = String(action["profileId"] ?? "").trim();
    if (!profileId) return config;
    const cookieSnapshotId = getSelectedCookieSnapshot(profileId);
    if (!cookieSnapshotId) return config;
    return {
      ...(config as Record<string, unknown>),
      data: {
        ...data,
        monitorAction: {
          ...action,
          cookieSnapshotId
        }
      }
    };
  }

  private async ensureProfilePaymentSession(taskId: string): Promise<void> {
    const taskResult = await this.getTaskStatus(taskId);
    const task = taskResult?.success ? taskResult.task : undefined;
    const data = task?.config?.data as Record<string, unknown> | undefined;
    if (!data) return;

    const action = data["monitorAction"] as { mode?: string; profileId?: string } | undefined;
    const profileId = String(action?.profileId ?? data["profileId"] ?? "").trim();
    const usesCheckoutProfile = action?.mode === "auto-checkout" || Boolean(data["profileId"]);
    if (!usesCheckoutProfile || !profileId) return;

    const profilesResult = await this.getProfiles();
    const profile = Array.isArray(profilesResult?.profiles)
      ? profilesResult.profiles.find((item: any) => String(item?.id ?? "") === profileId)
      : undefined;
    const preference = profile?.paymentPreference;
    const result = await this.setPaymentSession(taskId, {
      method: preference?.method ?? "card",
      label: typeof preference?.label === "string" && preference.label.trim()
        ? preference.label.trim()
        : undefined
    });
    if (!result?.success) {
      throw new Error(result?.error || `Profil-Zahlung für Task ${taskId} konnte nicht vorbereitet werden.`);
    }
  }

  private recordPreviewLog(task: BrowserPreviewTask, event: string, level: BrowserPreviewLog["level"], message: string): void {
    const logs = this.previewTaskLogs.get(task.id) ?? [];
    logs.push({
      id: ++this.previewLogId,
      taskId: task.id,
      event,
      state: task.state,
      level,
      message,
      createdAt: new Date().toISOString()
    });
    this.previewTaskLogs.set(task.id, logs);
  }
}
