import { Component, OnDestroy, OnInit } from "@angular/core";
import { ElectronService } from "./services/electron.service";
import { TaskState } from "../models";
import { COMMERCE_PLATFORMS, CommercePlatform } from "../commerce/platforms";
import type { CheckoutPaymentSession, PaymentMethod } from "../payments/models";
import type { AresProxy, ProxySelection } from "../proxies/models";
import type { AresProfile } from "../profiles/models";
import {
  isCompleteCheckoutAddress,
  toPersistedAresProfile,
  toProfileV2Draft,
  type ProfileV2Draft
} from "../profiles/profile-v2";

type AppTab = "dashboard" | "tasks" | "profiles" | "proxies" | "shops";
type ProfileTab = "identity" | "address" | "browser" | "payment";
type TaskCreationMode = "monitor-only" | "auto-checkout";
type MonitorStrategyMode = "product-monitor" | "early-gate";
type ProfileView = ProfileV2Draft;
type FlowStepKey = "monitoring" | "gate-detected" | "waiting-queue" | "released" | "post-queue-discovery" | "product-found" | "cart" | "checkout";

interface ShopView {
  id: string;
  name: string;
  baseUrl: string;
  platform: CommercePlatform;
}

interface TaskView {
  id: string;
  config: {
    name: string;
    shopId?: string;
    data?: Record<string, unknown>;
  };
  state: TaskState;
  retries?: number;
  maxRetries?: number;
  lastError?: string;
}

interface TaskLogView {
  id?: number;
  taskId: string;
  event: string;
  state?: TaskState;
  level: "info" | "warn" | "error";
  message: string;
  createdAt: string | Date;
}

interface SystemNodeStatus {
  executable: string;
  version?: string;
  major?: number;
  ok: boolean;
  error?: string;
}

interface PersistenceStatus {
  type: string;
  ready: boolean;
  error?: string;
}

interface SystemStatus {
  availableWorkers: number;
  shopCount: number;
  taskCount: number;
  profileCount?: number;
  proxyCount?: number;
  commercePlatforms?: CommercePlatform[];
  commerceExecutorPlatforms?: CommercePlatform[];
  commerceMonitorReady?: boolean;
  earlyGateReady?: boolean;
  allowFinalPurchase?: boolean;
  captchaProvider?: string;
  captchaApiKeyConfigured?: boolean;
  liveChallengeSupport?: string[];
  electronNodeVersion?: string;
  systemNodeRequirement?: string;
  systemNode?: SystemNodeStatus;
  persistence?: PersistenceStatus;
  browserPreview?: boolean;
  browserWorkerPool?: unknown;
}

@Component({
  selector: "app-root",
  templateUrl: "./app.component.html",
  styleUrls: ["./app.component.scss"]
})
export class AppComponent implements OnInit, OnDestroy {
  activeTab: AppTab = "dashboard";
  profileTab: ProfileTab = "identity";

  shops: ShopView[] = [];
  profiles: ProfileView[] = [];
  proxies: AresProxy[] = [];
  selectedProfileId = "";
  selectedShopId = "";
  commercePlatforms: CommercePlatform[] = [...COMMERCE_PLATFORMS];
  executorPlatforms: CommercePlatform[] = ["shopify"];

  newProfile: ProfileView = this.emptyProfile();
  newProxy: AresProxy = this.emptyProxy();
  newShop: { id: string; name: string; baseUrl: string; platform: CommercePlatform } = {
    id: "",
    name: "",
    baseUrl: "",
    platform: "shopify"
  };

  tasks: TaskView[] = [];
  taskLogs: Record<string, TaskLogView[]> = {};
  expandedTaskLogId = "";
  system: SystemStatus = {
    availableWorkers: 0,
    shopCount: 0,
    taskCount: 0,
    profileCount: 0,
    proxyCount: 0,
    commercePlatforms: [...COMMERCE_PLATFORMS],
    commerceExecutorPlatforms: ["shopify"],
    commerceMonitorReady: true,
    earlyGateReady: false,
    allowFinalPurchase: true,
    captchaProvider: "CapMonster",
    captchaApiKeyConfigured: false,
    liveChallengeSupport: [],
    systemNode: { executable: "node", ok: false }
  };

  monitorStrategyMode: MonitorStrategyMode = "product-monitor";
  taskMode: TaskCreationMode = "monitor-only";
  taskName = "";
  searchTerm = "";
  earlyGateProductName = "";
  discoveryKeywords: string[] = [];
  newDiscoveryKeyword = "";
  readonly liveKeywordDrafts: Record<string, string> = {};
  readonly earlyGateFlowSteps: Array<{ key: FlowStepKey; label: string }> = [
    { key: "monitoring", label: "MONITORING" },
    { key: "gate-detected", label: "GATE DETECTED" },
    { key: "waiting-queue", label: "WAITING_QUEUE" },
    { key: "released", label: "RELEASED" },
    { key: "post-queue-discovery", label: "POST_QUEUE_DISCOVERY" },
    { key: "product-found", label: "PRODUCT_FOUND" },
    { key: "cart", label: "CART" },
    { key: "checkout", label: "CHECKOUT" }
  ];
  taskIntervalSeconds = 30;
  headless = false;
  taskProxyMode: ProxySelection["mode"] = "profile-default";
  selectedTaskProxyId = "";

  taskPaymentEnabled = false;
  taskPaymentMethod: PaymentMethod = "card";
  taskPaymentLabel = "";

  testingAllProxies = false;
  readonly testingProxyIds = new Set<string>();

  purchaseChanging = false;
  bulkStarting = false;
  bulkStopping = false;

  error = "";
  info = "";

  private unsubscribeStatus?: () => void;
  private taskUpdateTimer?: ReturnType<typeof setTimeout>;

  constructor(private readonly electron: ElectronService) {}

  async ngOnInit(): Promise<void> {
    await Promise.all([
      this.loadShops(),
      this.loadProfiles(),
      this.loadProxies(),
      this.loadTasks(),
      this.loadSystemStatus()
    ]);

    this.syncProfileDefaults();
    this.unsubscribeStatus = this.electron.onTaskStatusUpdate(() => {
      this.scheduleTaskViewRefresh();
    });
  }

  ngOnDestroy(): void {
    this.unsubscribeStatus?.();
    if (this.taskUpdateTimer) clearTimeout(this.taskUpdateTimer);
  }

  private scheduleTaskViewRefresh(): void {
    if (this.taskUpdateTimer) return;
    this.taskUpdateTimer = setTimeout(() => {
      this.taskUpdateTimer = undefined;
      const expandedTaskLogId = this.expandedTaskLogId;
      void Promise.all([
        this.loadTasks(),
        this.loadSystemStatus(),
        expandedTaskLogId ? this.loadTaskLogs(expandedTaskLogId) : Promise.resolve()
      ]);
    }, 300);
  }

  setTab(tab: AppTab): void {
    this.activeTab = tab;
    this.error = "";
    this.info = "";
  }

  setProfileTab(tab: ProfileTab): void {
    this.profileTab = tab;
  }

  setMonitorStrategy(mode: MonitorStrategyMode): void {
    this.monitorStrategyMode = mode;
    if (mode === "early-gate") this.taskMode = "auto-checkout";
  }

  onShopSelected(): void {
    const shop = this.shops.find(item => item.id === this.selectedShopId);
    if (shop && this.monitorStrategyMode === "product-monitor" && this.isPokemonCenterShop(shop)) {
      this.setMonitorStrategy("early-gate");
    }
  }

  private isPokemonCenterShop(shop: ShopView): boolean {
    if (shop.platform === "pokemon-center") return true;
    try { return /(^|\.)pokemoncenter\.(com|de)$/i.test(new URL(shop.baseUrl).hostname); }
    catch { return false; }
  }

  get monitorOnlyTasks(): TaskView[] {
    return this.tasks.filter(task => this.isMonitorOnlyTask(task));
  }

  get autoCheckoutTasks(): TaskView[] {
    return this.tasks.filter(task => this.isAutoCheckoutTask(task));
  }

  get checkoutRuns(): TaskView[] {
    return this.tasks.filter(task => this.isCheckoutChildTask(task));
  }

  get activeTaskCount(): number {
    return this.tasks.filter(task => ![TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED].includes(task.state)).length;
  }

  get startableTasks(): TaskView[] {
    return this.tasks.filter(task => task.state === TaskState.QUEUED);
  }

  get restartableTasks(): TaskView[] {
    return this.tasks.filter(task => this.isRestartable(task));
  }

  get stoppableTasks(): TaskView[] {
    return this.tasks.filter(task => this.isStoppable(task));
  }

  get bulkRunnableCount(): number {
    return this.startableTasks.length + this.restartableTasks.length;
  }

  get taskNeedsCheckoutSession(): boolean {
    return this.monitorStrategyMode === "early-gate" || this.taskMode === "auto-checkout";
  }

  get selectedShopSupportsCheckout(): boolean {
    if (this.monitorStrategyMode === "early-gate") return Boolean(this.system.earlyGateReady);
    const shop = this.shops.find(item => item.id === this.selectedShopId);
    return Boolean(shop && this.hasExecutorForShop(shop));
  }

  addDiscoveryKeyword(): void {
    const value = this.newDiscoveryKeyword.trim().replace(/\s+/g, " ");
    if (!value) return;
    if (!this.discoveryKeywords.some(item => item.toLocaleLowerCase("de-DE") === value.toLocaleLowerCase("de-DE"))) {
      this.discoveryKeywords = [...this.discoveryKeywords, value.slice(0, 160)];
    }
    this.newDiscoveryKeyword = "";
  }

  removeDiscoveryKeyword(keyword: string): void {
    this.discoveryKeywords = this.discoveryKeywords.filter(item => item !== keyword);
  }

  async loadProfiles(): Promise<void> {
    const result = await this.electron.getProfiles();
    if (!result.success) return;
    this.profiles = (result.profiles as AresProfile[]).map(profile => toProfileV2Draft(profile));
    if (!this.selectedProfileId && this.profiles.length > 0 && this.taskNeedsCheckoutSession) {
      this.selectedProfileId = this.profiles[0].id;
    }
  }

  async loadProxies(): Promise<void> {
    const result = await this.electron.getProxies();
    if (!result.success) return;
    this.proxies = result.proxies;
    if (this.selectedTaskProxyId && !this.proxies.some(proxy => proxy.id === this.selectedTaskProxyId)) {
      this.selectedTaskProxyId = "";
      if (this.taskProxyMode === "proxy") this.taskProxyMode = "profile-default";
    }
  }

  async saveProfile(): Promise<void> {
    this.error = "";
    this.info = "";
    const profile = this.newProfile;

    if (
      !profile.id.trim() ||
      !profile.name.trim() ||
      !profile.contact.firstName.trim() ||
      !profile.contact.lastName.trim() ||
      !profile.contact.email.trim() ||
      !isCompleteCheckoutAddress(profile.shippingAddress) ||
      (!profile.billingSameAsShipping && !isCompleteCheckoutAddress(profile.billingAddress))
    ) {
      this.error = profile.billingSameAsShipping
        ? "Bitte Profilname, Kontakt und Lieferadresse vollständig ausfüllen."
        : "Bitte Profilname, Kontakt, Lieferadresse und separate Rechnungsadresse vollständig ausfüllen.";
      return;
    }

    if (profile.preferredProxyId && !this.proxies.some(proxy => proxy.id === profile.preferredProxyId)) {
      this.error = "Der ausgewählte Standard-Proxy existiert nicht mehr.";
      return;
    }

    const persistedProfile = toPersistedAresProfile(profile);
    const result = await this.electron.saveProfile(persistedProfile);

    if (!result.success) {
      this.error = result.error;
      return;
    }

    this.info = "Profil gespeichert.";
    this.newProfile = toProfileV2Draft(persistedProfile);
    await this.loadProfiles();
  }

  editProfile(profile: ProfileView): void {
    this.newProfile = toProfileV2Draft(toPersistedAresProfile(profile));
    this.profileTab = "identity";
    this.info = `Profil ${profile.name} geladen.`;
  }

  async deleteProfile(profile: ProfileView, event?: Event): Promise<void> {
    if (event) event.stopPropagation();
    this.error = "";
    this.info = "";
    if (!window.confirm(`Profil „${profile.name}" wirklich löschen? Browserdaten und Cookie-Snapshots werden ebenfalls entfernt.`)) return;
    const result = await this.electron.deleteProfile(profile.id);
    if (!result.success) {
      this.error = result.error;
      return;
    }
    this.info = `Profil „${profile.name}" gelöscht.`;
    if (this.selectedProfileId === profile.id) this.selectedProfileId = "";
    await this.loadProfiles();
  }

  resetProfileForm(): void {
    this.newProfile = this.emptyProfile();
    this.profileTab = "identity";
  }

  async saveProxy(): Promise<void> {
    this.error = "";
    this.info = "";
    const proxy = this.newProxy;
    if (!proxy.id.trim() || !proxy.name.trim() || !proxy.host.trim()) {
      this.error = "Proxy-ID, Name und Host sind erforderlich.";
      return;
    }
    const port = Number(proxy.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      this.error = "Proxy-Port muss zwischen 1 und 65535 liegen.";
      return;
    }

    const result = await this.electron.saveProxy({
      ...proxy,
      id: proxy.id.trim(),
      name: proxy.name.trim(),
      host: proxy.host.trim(),
      port,
      username: proxy.username?.trim() || undefined,
      password: proxy.password || undefined
    });
    if (!result.success) {
      this.error = result.error;
      return;
    }
    this.info = `Proxy ${result.proxy?.name || proxy.name} gespeichert.`;
    this.newProxy = this.emptyProxy();
    await Promise.all([this.loadProxies(), this.loadSystemStatus()]);
  }

  async testProxy(proxy: AresProxy): Promise<void> {
    if (this.testingProxyIds.has(proxy.id) || this.testingAllProxies) return;
    this.error = "";
    this.info = "";
    this.testingProxyIds.add(proxy.id);
    try {
      const result = await this.electron.testProxy(proxy.id);
      await this.loadProxies();
      if (!result.success) {
        this.error = result.error || `Proxy ${proxy.name} ist nicht erreichbar.`;
        return;
      }
      const latency = result.health?.latencyMs;
      this.info = `${proxy.name}: online${typeof latency === "number" ? ` · ${latency} ms` : ""}.`;
    } finally {
      this.testingProxyIds.delete(proxy.id);
    }
  }

  async testAllProxies(): Promise<void> {
    if (this.testingAllProxies || !this.proxies.length) return;
    this.error = "";
    this.info = "";
    this.testingAllProxies = true;
    try {
      const result = await this.electron.testAllProxies();
      await this.loadProxies();
      if (!result.success) {
        this.error = result.error || "Proxy-Checks konnten nicht abgeschlossen werden.";
        return;
      }
      const online = Array.isArray(result.results) ? result.results.filter((item: any) => item.success).length : 0;
      this.info = `Proxy-Checks abgeschlossen · ${online}/${this.proxies.length} online.`;
    } finally {
      this.testingAllProxies = false;
    }
  }

  isProxyTesting(proxyId: string): boolean {
    return this.testingAllProxies || this.testingProxyIds.has(proxyId);
  }

  getProxyLocation(proxy: AresProxy): string {
    const geo = proxy.health?.geo;
    if (!geo) return "Ort unbekannt";
    return [geo.city, geo.region, geo.countryCode || geo.country].filter(Boolean).join(" · ") || "Ort unbekannt";
  }

  getProxyNetwork(proxy: AresProxy): string {
    const geo = proxy.health?.geo;
    if (!geo) return "ASN / Provider unbekannt";
    return [geo.asn, geo.provider].filter(Boolean).join(" · ") || "ASN / Provider unbekannt";
  }

  getProxyRiskLabel(proxy: AresProxy): string {
    const reputation = proxy.health?.reputation;
    if (!reputation?.available) return "RISK —";
    return typeof reputation.riskScore === "number" ? `RISK ${reputation.riskScore}/100` : "RISK —";
  }

  getProxySpamLabel(proxy: AresProxy): string {
    const reputation = proxy.health?.reputation;
    if (!reputation?.available) return "SPAM —";
    return typeof reputation.spamHits === "number" ? `SPAM ${reputation.spamHits}` : "SPAM —";
  }

  getProxyCheckLabel(proxy: AresProxy): string {
    const health = proxy.health;
    if (!health) return "Noch nicht getestet";
    const time = new Date(health.checkedAt);
    const formatted = Number.isNaN(time.getTime()) ? health.checkedAt : time.toLocaleString("de-DE");
    return `Letzter Check ${formatted}`;
  }

  editProxy(proxy: AresProxy): void {
    this.newProxy = { ...proxy };
    this.info = `Proxy ${proxy.name} geladen.`;
  }

  async deleteProxy(proxyId: string): Promise<void> {
    this.error = "";
    this.info = "";
    const result = await this.electron.deleteProxy(proxyId);
    if (!result.success) {
      this.error = result.error || "Proxy konnte nicht gelöscht werden.";
      return;
    }
    if (this.newProxy.id === proxyId) this.newProxy = this.emptyProxy();
    this.info = "Proxy gelöscht.";
    await Promise.all([this.loadProxies(), this.loadSystemStatus()]);
  }

  async loadShops(): Promise<void> {
    const result = await this.electron.getShops();
    if (!result.success) return;
    this.shops = result.shops;
    if (Array.isArray(result.platforms) && result.platforms.length) this.commercePlatforms = result.platforms;
    if (Array.isArray(result.executorPlatforms)) this.executorPlatforms = result.executorPlatforms;
    if (typeof result.earlyGateReady === "boolean") this.system.earlyGateReady = result.earlyGateReady;
    if (!this.selectedShopId && this.shops.length > 0) this.selectedShopId = this.shops[0].id;
  }

  async loadTasks(): Promise<void> {
    const result = await this.electron.getTaskList();
    if (result.success) this.tasks = result.tasks;
  }

  async loadTaskLogs(taskId: string): Promise<void> {
    const result = await this.electron.getTaskLogs(taskId, 100);
    if (result.success) this.taskLogs[taskId] = result.logs;
    else this.error = result.error || "Task-Verlauf konnte nicht geladen werden.";
  }

  async toggleTaskLogs(taskId: string): Promise<void> {
    if (this.expandedTaskLogId === taskId) {
      this.expandedTaskLogId = "";
      return;
    }
    this.expandedTaskLogId = taskId;
    await this.loadTaskLogs(taskId);
  }

  async loadSystemStatus(): Promise<void> {
    const result = await this.electron.getSystemStatus();
    if (!result.success) return;
    this.system = result;
    if (Array.isArray(result.commercePlatforms) && result.commercePlatforms.length) this.commercePlatforms = result.commercePlatforms;
    if (Array.isArray(result.commerceExecutorPlatforms)) this.executorPlatforms = result.commerceExecutorPlatforms;
  }

  async toggleFinalPurchase(): Promise<void> {
    if (this.purchaseChanging) return;
    this.purchaseChanging = true;
    this.error = "";
    this.info = "";
    try {
      const allowed = this.system.allowFinalPurchase !== true;
      const result = await this.electron.setFinalPurchaseAllowed(allowed);
      if (!result?.success) {
        this.error = result?.error || "Kauf-Freigabe wurde vom Backend abgelehnt.";
        return;
      }
      this.info = result.allowFinalPurchase
        ? "Auto-Kauf freigegeben. Checkout läuft bis zum finalen Submit."
        : "Auto-Kauf gesperrt. Checkout stoppt vor dem finalen Submit.";
      await this.loadSystemStatus();
    } finally {
      this.purchaseChanging = false;
    }
  }

  async registerShop(): Promise<void> {
    this.error = "";
    this.info = "";
    const id = this.newShop.id.trim();
    const baseUrl = this.newShop.baseUrl.trim();
    const platform = this.newShop.platform;
    if (!id || !baseUrl) {
      this.error = "Shop-ID und Shop-URL sind erforderlich.";
      return;
    }

    const result = await this.electron.registerShop({
      ...this.newShop,
      id,
      name: this.newShop.name.trim() || id,
      baseUrl,
      platform
    });
    if (!result.success) {
      this.error = result.error;
      return;
    }

    this.info = result.executorReady
      ? `${this.getPlatformLabel(platform)} Shop registriert · Monitoring + Checkout-Executor bereit.`
      : `${this.getPlatformLabel(platform)} Shop registriert · Monitoring verfügbar${result.earlyGateReady ? " · Early Gate bereit" : ""}.`;
    this.newShop = { id: "", name: "", baseUrl: "", platform };
    await Promise.all([this.loadShops(), this.loadSystemStatus()]);
  }

  async createTask(): Promise<void> {
    this.error = "";
    this.info = "";
    const earlyGate = this.monitorStrategyMode === "early-gate";
    const needsCheckout = earlyGate || this.taskMode === "auto-checkout";

    if (!this.taskName.trim() || !this.selectedShopId) {
      this.error = "Task-Name und Shop sind erforderlich.";
      return;
    }
    if (earlyGate && !this.earlyGateProductName.trim()) {
      this.error = "Für Early Gate ist ein Produktname erforderlich.";
      return;
    }
    if (!earlyGate && !this.searchTerm.trim()) {
      this.error = "Für den normalen Produktmonitor ist ein Produkt/Keyword erforderlich.";
      return;
    }

    if (needsCheckout) {
      if (!this.selectedProfileId) {
        this.error = "Für den Browser-Checkout ist ein Profil erforderlich.";
        return;
      }
      if (!this.selectedShopSupportsCheckout) {
        this.error = earlyGate
          ? "Der Early-Gate-Browser-Executor ist nicht verfügbar."
          : "Für diesen Shop ist kein Browser-Checkout-Executor verfügbar.";
        return;
      }
    }
    if (this.taskProxyMode === "proxy" && !this.selectedTaskProxyId) {
      this.error = needsCheckout
        ? "Bitte einen Proxy für die Checkout-Session auswählen."
        : "Bitte einen Proxy für die Monitor-Runtime auswählen.";
      return;
    }

    const prefix = earlyGate ? "gate" : this.taskMode === "auto-checkout" ? "auto" : "monitor";
    const taskId = `${prefix}_${Date.now()}`;
    const intervalSeconds = Math.max(1, Math.floor(Number(this.taskIntervalSeconds) || 30));
    const proxySelection: ProxySelection = {
      mode: this.taskProxyMode,
      ...(this.taskProxyMode === "proxy" ? { proxyId: this.selectedTaskProxyId } : {})
    };
    const runtimeProfile = this.selectedProfileId
      ? this.profiles.find(profile => profile.id === this.selectedProfileId)
      : undefined;
    const runtimeBrowser = {
      ...(runtimeProfile?.id ? { runtimeProfileId: runtimeProfile.id } : {}),
      ...(runtimeProfile?.browser?.userAgent ? { runtimeUserAgent: runtimeProfile.browser.userAgent } : {}),
      ...(runtimeProfile?.preferredProxyId ? { runtimePreferredProxyId: runtimeProfile.preferredProxyId } : {})
    };

    const monitorAction = needsCheckout
      ? {
          mode: "auto-checkout",
          profileId: this.selectedProfileId,
          proxySelection,
          headless: this.headless,
          paymentEnabled: this.taskPaymentEnabled,
          ...runtimeBrowser
        }
      : {
          mode: "monitor-only",
          proxySelection,
          headless: this.headless,
          ...runtimeBrowser
        };

    const data: Record<string, unknown> = {
      monitorIntervalMs: intervalSeconds * 1_000,
      monitorAction,
      monitorStrategy: earlyGate
        ? {
            mode: "early-gate",
            productName: this.earlyGateProductName.trim(),
            discoveryKeywords: [...this.discoveryKeywords]
          }
        : { mode: "product-monitor" }
    };
    if (!earlyGate) data["productCriteria"] = { searchTerm: this.searchTerm.trim() };

    const result = await this.electron.createTask({
      id: taskId,
      name: this.taskName.trim(),
      shopId: this.selectedShopId,
      data
    });

    if (!result.success) {
      this.error = result.error;
      return;
    }

    if (needsCheckout && this.taskPaymentEnabled) {
      const paymentResult = await this.electron.setPaymentSession(taskId, this.buildPaymentSession());
      if (!paymentResult.success) {
        this.error = `Task erstellt, aber Zahlungs-Session konnte nicht gesetzt werden: ${paymentResult.error}`;
        return;
      }
    }

    this.info = earlyGate
      ? `Early-Gate-Task ${result.taskId} erstellt. ARES startet den Browser erst beim passiven Gate-Signal.`
      : this.taskMode === "auto-checkout"
        ? `Auto-Checkout-Task ${result.taskId} erstellt. Bei Verfügbarkeit startet ARES genau eine isolierte Checkout-Session.`
        : `Monitoring-Task ${result.taskId} erstellt. Chromium wird nur bei leerem/unklarem Fast-Path zugeschaltet.`;
    this.taskName = "";
    this.searchTerm = "";
    this.earlyGateProductName = "";
    this.discoveryKeywords = [];
    this.newDiscoveryKeyword = "";
    await Promise.all([this.loadTasks(), this.loadSystemStatus()]);
  }

  syncProfileDefaults(): void {
    const profile = this.profiles.find(item => item.id === this.selectedProfileId);
    if (!profile) return;
    if (profile.paymentPreference?.method) {
      this.taskPaymentMethod = profile.paymentPreference.method;
      this.taskPaymentLabel = profile.paymentPreference.label || "";
    }
    this.taskProxyMode = "profile-default";
    this.selectedTaskProxyId = "";
  }

  async startTask(taskId: string): Promise<void> {
    this.error = "";
    const result = await this.electron.startTask(taskId);
    if (!result.success) this.error = result.error;
    await this.refreshTaskView(taskId);
  }

  async startAllTasks(): Promise<void> {
    if (this.bulkStarting) return;
    const targets = [...this.startableTasks, ...this.restartableTasks];
    if (!targets.length) {
      this.info = "Keine startbaren oder neu startbaren Tasks.";
      return;
    }

    this.bulkStarting = true;
    this.error = "";
    this.info = `Starte ${targets.length} Task(s)…`;

    let started = 0;
    const failures: string[] = [];
    try {
      for (let index = 0; index < targets.length; index += 1) {
        const target = targets[index];
        const restart = this.isRestartable(target);
        try {
          const result = restart
            ? await this.electron.restartTask(target.id)
            : await this.electron.startTask(target.id);
          if (result?.success) started += 1;
          else failures.push(`${target.config.name || target.id}: ${result?.error || "unbekannter Fehler"}`);
        } catch (error) {
          failures.push(`${target.config.name || target.id}: ${error instanceof Error ? error.message : String(error)}`);
        }
        this.info = `${started}/${targets.length} Tasks ${restart ? "neu gestartet" : "gestartet"}…`;
        if (index < targets.length - 1) await this.delay(500);
      }
    } finally {
      this.bulkStarting = false;
    }

    this.info = failures.length
      ? `${started}/${targets.length} Tasks gestartet · ${failures.length} Fehler.`
      : `${started}/${targets.length} Tasks gestartet.`;
    if (failures.length) this.error = failures.slice(0, 3).join(" · ");
    await Promise.all([this.loadTasks(), this.loadSystemStatus()]);
  }

  async pauseTask(taskId: string): Promise<void> {
    this.error = "";
    const result = await this.electron.pauseTask(taskId);
    if (!result.success) this.error = result.error;
    await this.refreshTaskView(taskId);
  }

  async resumeTask(taskId: string): Promise<void> {
    this.error = "";
    const result = await this.electron.resumeTask(taskId);
    if (!result.success) this.error = result.error;
    await this.refreshTaskView(taskId);
  }

  async stopTask(taskId: string): Promise<void> {
    this.error = "";
    await this.electron.clearPaymentSession(taskId);
    const result = await this.electron.stopTask(taskId);
    if (!result.success) this.error = result.error;
    await this.refreshTaskView(taskId);
  }

  async stopAllTasks(): Promise<void> {
    if (this.bulkStopping) return;
    const targets = [...this.stoppableTasks];
    if (!targets.length) {
      this.info = "Keine laufenden Tasks zum Stoppen.";
      return;
    }

    this.bulkStopping = true;
    this.error = "";
    this.info = `Stoppe ${targets.length} Task(s)…`;

    let stopped = 0;
    const failures: string[] = [];
    try {
      for (let index = 0; index < targets.length; index += 1) {
        const target = targets[index];
        try {
          await this.electron.clearPaymentSession(target.id).catch(() => undefined);
          const result = await this.electron.stopTask(target.id);
          if (result?.success) stopped += 1;
          else failures.push(`${target.config.name || target.id}: ${result?.error || "unbekannter Fehler"}`);
        } catch (error) {
          failures.push(`${target.config.name || target.id}: ${error instanceof Error ? error.message : String(error)}`);
        }
        this.info = `${stopped}/${targets.length} Tasks gestoppt…`;
        if (index < targets.length - 1) await this.delay(300);
      }
    } finally {
      this.bulkStopping = false;
    }

    this.info = failures.length
      ? `${stopped}/${targets.length} Tasks gestoppt · ${failures.length} Fehler.`
      : `${stopped}/${targets.length} Tasks gestoppt.`;
    if (failures.length) this.error = failures.slice(0, 3).join(" · ");
    await Promise.all([this.loadTasks(), this.loadSystemStatus()]);
  }

  async restartTask(taskId: string): Promise<void> {
    this.error = "";
    this.info = "";
    const result = await this.electron.restartTask(taskId);
    if (!result.success) {
      this.error = result.error;
      return;
    }
    this.info = "Task neu gestartet.";
    await this.refreshTaskView(taskId);
  }

  async deleteTask(taskId: string): Promise<void> {
    const confirmed = typeof window === "undefined" || window.confirm(
      "Task dauerhaft löschen? Verlauf, Logs und Monitor-Ereignisse werden entfernt."
    );
    if (!confirmed) return;

    this.error = "";
    this.info = "";
    const result = await this.electron.deleteTask(taskId);
    if (!result.success) {
      this.error = result.error;
      return;
    }
    this.info = "Task gelöscht.";
    if (this.expandedTaskLogId === taskId) this.expandedTaskLogId = "";
    await Promise.all([this.loadTasks(), this.loadSystemStatus()]);
  }

  isMonitorTask(task: TaskView): boolean {
    const data = task.config.data ?? {};
    const criteria = data["productCriteria"];
    const strategy = data["monitorStrategy"] as Record<string, unknown> | undefined;
    return Boolean((criteria && typeof criteria === "object") || strategy?.["mode"] === "early-gate");
  }

  isEarlyGateMonitorTask(task: TaskView): boolean {
    const strategy = task.config.data?.["monitorStrategy"] as Record<string, unknown> | undefined;
    return this.isMonitorTask(task) && strategy?.["mode"] === "early-gate";
  }

  isEarlyGateChildTask(task: TaskView): boolean {
    const trigger = task.config.data?.["triggerSource"] as Record<string, unknown> | undefined;
    return trigger?.["kind"] === "early-gate" && Boolean(trigger?.["parentTaskId"]);
  }

  isEarlyGateFlowTask(task: TaskView): boolean {
    return this.isEarlyGateMonitorTask(task) || this.isEarlyGateChildTask(task);
  }

  isAutoCheckoutTask(task: TaskView): boolean {
    const action = task.config.data?.["monitorAction"] as Record<string, unknown> | undefined;
    return this.isMonitorTask(task) && action?.["mode"] === "auto-checkout";
  }

  isMonitorOnlyTask(task: TaskView): boolean {
    return this.isMonitorTask(task) && !this.isAutoCheckoutTask(task);
  }

  isCheckoutChildTask(task: TaskView): boolean {
    const trigger = task.config.data?.["triggerSource"] as Record<string, unknown> | undefined;
    return Boolean(trigger?.["parentTaskId"]);
  }

  getTaskKind(task: TaskView): string {
    if (this.isEarlyGateMonitorTask(task)) return "EARLY GATE";
    if (this.isEarlyGateChildTask(task)) return "GATE CHILD";
    if (this.isAutoCheckoutTask(task)) return "AUTO CHECKOUT";
    if (this.isMonitorOnlyTask(task)) return "MONITOR";
    if (this.isCheckoutChildTask(task)) return "CHECKOUT RUN";
    return "BROWSER";
  }

  getTaskProfileName(task: TaskView): string {
    let profileId = "";
    if (this.isMonitorTask(task)) {
      const action = task.config.data?.["monitorAction"] as Record<string, unknown> | undefined;
      profileId = String(action?.[this.isAutoCheckoutTask(task) ? "profileId" : "runtimeProfileId"] ?? "");
    } else {
      profileId = String(task.config.data?.["profileId"] ?? "");
    }
    if (!profileId) return this.isMonitorOnlyTask(task) ? "Runtime-Profil optional" : "kein Profil";
    return this.profiles.find(profile => profile.id === profileId)?.name ?? profileId;
  }

  getTaskProxyStatus(task: TaskView): string {
    if (this.isMonitorTask(task)) {
      const action = task.config.data?.["monitorAction"] as Record<string, unknown> | undefined;
      const selection = action?.["proxySelection"] as ProxySelection | undefined;
      const profileId = String(action?.[this.isAutoCheckoutTask(task) ? "profileId" : "runtimeProfileId"] ?? "");
      return this.proxySelectionLabel(selection, profileId);
    }

    const runtime = task.config.data?.["proxyRuntime"] as Record<string, unknown> | undefined;
    if (runtime?.["mode"] === "direct") return "Direktverbindung";
    if (runtime?.["proxyName"]) return String(runtime["proxyName"]);
    if (runtime?.["mode"] === "legacy-profile") return "Legacy Profil-Proxy";

    const selection = task.config.data?.["proxySelection"] as ProxySelection | undefined;
    const profileId = String(task.config.data?.["profileId"] ?? "");
    return this.proxySelectionLabel(selection, profileId);
  }

  getTaskAutomationStatus(task: TaskView): string {
    if (!this.isAutoCheckoutTask(task)) return "";
    if (this.isEarlyGateMonitorTask(task)) {
      const runtime = this.getEarlyGateRuntime(task);
      if (!runtime) return "MONITORING · wartet auf passives Gate-Signal";
      if (runtime["childTaskId"]) return `Gate erkannt · Browser-Child ${String(runtime["childTaskId"])}`;
      return String(runtime["stage"] ?? "monitoring").toUpperCase().replace(/-/g, "_");
    }
    const runtime = task.config.data?.["autoCheckoutRuntime"] as Record<string, unknown> | undefined;
    if (!runtime) return "Wartet auf verfügbares Produkt";
    if (runtime["status"] === "failed") return `Trigger fehlgeschlagen: ${String(runtime["error"] ?? "unbekannt")}`;
    if (runtime["childTaskId"]) return `Checkout gestartet · ${String(runtime["childTaskId"])}`;
    return "Checkout wird gestartet";
  }

  getTaskParentId(task: TaskView): string {
    const trigger = task.config.data?.["triggerSource"] as Record<string, unknown> | undefined;
    return trigger?.["parentTaskId"] ? String(trigger["parentTaskId"]) : "";
  }

  getTaskChildId(task: TaskView): string {
    const runtime = this.getEarlyGateRuntime(task);
    if (runtime?.["childTaskId"]) return String(runtime["childTaskId"]);
    const auto = task.config.data?.["autoCheckoutRuntime"] as Record<string, unknown> | undefined;
    return auto?.["childTaskId"] ? String(auto["childTaskId"]) : "";
  }

  getActiveArea(task: TaskView): string {
    if (this.isEarlyGateChildTask(task)) return "Browser-Child";
    const runtime = this.getEarlyGateRuntime(task);
    const area = String(runtime?.["activeArea"] ?? "");
    if (area === "gate") return "Gate";
    if (area === "browser-child") return "Browser-Child";
    if (area === "monitor") return "Monitor";
    return this.isMonitorTask(task) ? "Monitor" : "Browser-Child";
  }

  getActiveDiscoveryKeywords(task: TaskView): string[] {
    const postQueue = task.config.data?.["postQueueDiscovery"] as Record<string, unknown> | undefined;
    const runtime = this.getEarlyGateRuntime(task);
    const raw = postQueue?.["keywords"] ?? runtime?.["keywords"];
    return Array.isArray(raw) ? raw.map(value => String(value)).filter(Boolean) : [];
  }

  isLiveKeywordEditable(task: TaskView): boolean {
    return this.isEarlyGateChildTask(task) && task.state === TaskState.POST_QUEUE_DISCOVERY;
  }

  async addLiveDiscoveryKeyword(task: TaskView): Promise<void> {
    const value = String(this.liveKeywordDrafts[task.id] ?? "").trim().replace(/\s+/g, " ");
    if (!value) return;
    const current = this.getActiveDiscoveryKeywords(task);
    const exists = current.some(item => item.toLocaleLowerCase("de-DE") === value.toLocaleLowerCase("de-DE"));
    this.liveKeywordDrafts[task.id] = "";
    if (exists) return;
    await this.updateLiveKeywords(task, [...current, value]);
  }

  async removeLiveDiscoveryKeyword(task: TaskView, keyword: string): Promise<void> {
    await this.updateLiveKeywords(task, this.getActiveDiscoveryKeywords(task).filter(item => item !== keyword));
  }

  getFlowStepState(task: TaskView, key: FlowStepKey): "done" | "active" | "pending" {
    if (!this.isEarlyGateFlowTask(task)) return "pending";
    if (this.isFlowStepActive(task, key)) return "active";
    return this.isFlowStepDone(task, key) ? "done" : "pending";
  }

  getTaskQueuePosition(task: TaskView): string {
    const queue = task.config.data?.["queueStatus"] as Record<string, unknown> | undefined;
    const position = Number(queue?.["position"]);
    return Number.isFinite(position) ? Math.max(0, Math.floor(position)).toLocaleString("de-DE") : "–";
  }

  getTaskQueueWait(task: TaskView): string {
    const queue = task.config.data?.["queueStatus"] as Record<string, unknown> | undefined;
    const seconds = Number(queue?.["timeToWaitSeconds"]);
    if (!Number.isFinite(seconds)) return "–";
    if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
    if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
    return `${Math.max(0, Math.floor(seconds))}s`;
  }

  getTaskCaptchaStatus(task: TaskView): string {
    if (this.isMonitorOnlyTask(task)) return "HTTP fast path · SeleniumBase fallback bei Bedarf";
    if (this.isMonitorTask(task)) return "Browser startet erst beim Checkout-Trigger";
    const data = task.config.data ?? {};
    const value = data["liveChallengeStatus"] ?? data["captchaStatus"] ?? data["challengeStatus"];
    return value ? String(value) : "Kein aktueller Challenge-Status";
  }

  getTaskChallengeType(task: TaskView): string {
    const data = task.config.data ?? {};
    const value = data["liveChallengeType"] ?? data["captchaType"] ?? data["challengeType"];
    return value ? String(value) : "";
  }

  getTaskPaymentStatus(task: TaskView): string {
    if (this.isMonitorOnlyTask(task)) return "";
    if (this.isAutoCheckoutTask(task)) {
      const action = task.config.data?.["monitorAction"] as Record<string, unknown> | undefined;
      return action?.["paymentEnabled"] ? "Session-Payment wird beim Trigger an den Browser-Child übergeben." : "Payment bleibt manuell.";
    }
    const preparation = task.config.data?.["paymentPreparation"] as Record<string, unknown> | undefined;
    if (!preparation) return this.isEarlyGateChildTask(task) ? "Checkout-Session aktiv; Zahlungsstatus folgt dem realen Checkout-DOM." : "Wird im Checkout erkannt, sobald sichtbar.";
    return String(preparation["note"] ?? "Zahlungsstatus aktualisiert.");
  }

  getTaskDetectedPaymentMethods(task: TaskView): string {
    const preparation = task.config.data?.["paymentPreparation"] as Record<string, unknown> | undefined;
    const methods = preparation?.["detectedMethods"];
    return Array.isArray(methods) && methods.length ? methods.join(", ") : "";
  }

  getProfileProxyName(profile: ProfileView): string {
    if (profile.preferredProxyId) return this.getProxyName(profile.preferredProxyId);
    if (profile.proxy?.host) return "Legacy Proxy";
    return "Direkt";
  }

  getProxyName(proxyId: string): string {
    return this.proxies.find(proxy => proxy.id === proxyId)?.name ?? proxyId;
  }

  getProxyEndpoint(proxy: AresProxy): string {
    return `${proxy.protocol}://${proxy.host}:${proxy.port}`;
  }

  getCaptchaKeyStatusLabel(): string {
    return this.system.captchaApiKeyConfigured ? "API-Key gesetzt" : "API-Key fehlt";
  }

  getSupportedChallengeTypes(): string {
    return this.system.liveChallengeSupport?.length
      ? this.system.liveChallengeSupport.join(", ")
      : "turnstile, recaptcha, shopify-checkpoint";
  }

  getPlatformLabel(platform: CommercePlatform): string {
    const labels: Record<CommercePlatform, string> = {
      shopify: "Shopify", woocommerce: "WooCommerce", jtl: "JTL-Shop", wix: "Wix Stores",
      shopware: "Shopware", magento: "Magento / Adobe Commerce", bigcommerce: "BigCommerce",
      prestashop: "PrestaShop", squarespace: "Squarespace Commerce", ecwid: "Ecwid",
      lightspeed: "Lightspeed eCom", commercetools: "commercetools",
      "salesforce-commerce-cloud": "Salesforce Commerce Cloud", "pokemon-center": "Pokémon Center", custom: "Custom / Sonstige"
    };
    return labels[platform] || platform;
  }

  getPaymentMethodLabel(method?: PaymentMethod): string {
    const labels: Record<PaymentMethod, string> = {
      card: "Karte", paypal: "PayPal", "shop-pay": "Shop Pay", klarna: "Klarna", other: "Andere"
    };
    return method ? labels[method] : "Nicht gesetzt";
  }

  hasExecutorForShop(shop: ShopView): boolean {
    return this.executorPlatforms.includes(shop.platform);
  }

  getSystemNodeStatusLabel(): string {
    if (this.system.browserPreview) return "Browser-Vorschau";
    if (!this.system.systemNode) return "Node unbekannt";
    return this.system.systemNode.ok ? "NODE OK" : "NODE FEHLER";
  }

  getSystemNodeDetails(): string {
    if (this.system.browserPreview) return "Echter Worker-Check läuft nur in Electron.";
    const node = this.system.systemNode;
    if (!node) return "System Node konnte nicht geprüft werden.";
    const version = node.version || "unbekannt";
    const requirement = this.system.systemNodeRequirement || ">=20";
    const base = `${node.executable} ${version} · benötigt ${requirement}`;
    return node.ok ? base : `${base} · ${node.error || "Worker kann evtl. nicht starten."}`;
  }

  getPersistenceStatusLabel(): string {
    if (this.system.browserPreview) return "PREVIEW";
    return this.system.persistence?.ready && !this.system.persistence?.error ? "SQLITE OK" : "DB FEHLER";
  }

  getPersistenceDetails(): string {
    if (this.system.browserPreview) return "Browser-Vorschau speichert nur im Arbeitsspeicher.";
    const persistence = this.system.persistence;
    if (!persistence) return "Persistenzstatus unbekannt.";
    return persistence.error || `${persistence.type.toUpperCase()} · Task-Historie aktiv`;
  }

  isSystemNodeOk(): boolean {
    return Boolean(this.system.browserPreview || this.system.systemNode?.ok);
  }

  isPersistenceOk(): boolean {
    return Boolean(this.system.browserPreview || (this.system.persistence?.ready && !this.system.persistence?.error));
  }

  isStartable(task: TaskView): boolean {
    return task.state === TaskState.QUEUED;
  }

  isPausable(task: TaskView): boolean {
    return [TaskState.QUEUED, TaskState.STARTING, TaskState.RUNNING, TaskState.WAITING_QUEUE,
      TaskState.POST_QUEUE_DISCOVERY, TaskState.PRODUCT_FOUND, TaskState.CART, TaskState.CHECKOUT, TaskState.RETRYING].includes(task.state);
  }

  isResumable(task: TaskView): boolean {
    return task.state === TaskState.PAUSED;
  }

  isStoppable(task: TaskView): boolean {
    return [TaskState.STARTING, TaskState.RUNNING, TaskState.WAITING_QUEUE, TaskState.POST_QUEUE_DISCOVERY, TaskState.PRODUCT_FOUND,
      TaskState.CART, TaskState.CHECKOUT, TaskState.RETRYING, TaskState.PAUSED].includes(task.state);
  }

  isRestartable(task: TaskView): boolean {
    return [TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED].includes(task.state);
  }

  trackTask(_index: number, task: TaskView): string { return task.id; }
  trackShop(_index: number, shop: ShopView): string { return shop.id; }
  trackProxy(_index: number, proxy: AresProxy): string { return proxy.id; }

  private getEarlyGateRuntime(task: TaskView): Record<string, unknown> | undefined {
    const value = task.config.data?.["earlyGateRuntime"];
    return value && typeof value === "object" ? value as Record<string, unknown> : undefined;
  }

  private async updateLiveKeywords(task: TaskView, keywords: string[]): Promise<void> {
    this.error = "";
    const result = await this.electron.updateDiscoveryKeywords(task.id, keywords);
    if (!result.success) {
      this.error = result.error || "Discovery-Keywords konnten nicht aktualisiert werden.";
      return;
    }
    const postQueue = task.config.data?.["postQueueDiscovery"] as Record<string, unknown> | undefined;
    task.config.data = {
      ...(task.config.data ?? {}),
      postQueueDiscovery: {
        ...(postQueue ?? {}),
        keywords: result.keywords,
        updatedAt: new Date().toISOString()
      }
    };
    this.info = `Aktive Discovery-Keywords aktualisiert: ${result.keywords.length}`;
  }

  private isFlowStepActive(task: TaskView, key: FlowStepKey): boolean {
    const runtime = this.getEarlyGateRuntime(task);
    if (key === "monitoring") return this.isEarlyGateMonitorTask(task) && !runtime?.["gateDetectedAt"] && task.state === TaskState.RUNNING;
    if (key === "gate-detected") return this.isEarlyGateMonitorTask(task) && Boolean(runtime?.["gateDetectedAt"]) && !runtime?.["browserChildStartedAt"];
    if (key === "waiting-queue") return task.state === TaskState.WAITING_QUEUE;
    if (key === "released") return false;
    if (key === "post-queue-discovery") return task.state === TaskState.POST_QUEUE_DISCOVERY;
    if (key === "product-found") return task.state === TaskState.PRODUCT_FOUND;
    if (key === "cart") return task.state === TaskState.CART;
    if (key === "checkout") return task.state === TaskState.CHECKOUT;
    return false;
  }

  private isFlowStepDone(task: TaskView, key: FlowStepKey): boolean {
    const runtime = this.getEarlyGateRuntime(task);
    const queue = task.config.data?.["queueStatus"] as Record<string, unknown> | undefined;
    if (key === "monitoring") return Boolean(runtime?.["monitoringAt"] || runtime?.["gateDetectedAt"]);
    if (key === "gate-detected") return Boolean(runtime?.["gateDetectedAt"]);
    if (key === "waiting-queue") return Boolean(runtime?.["queueEnteredAt"] || queue?.["detectedAt"]);
    if (key === "released") return Boolean(runtime?.["queueReleasedAt"] || queue?.["phase"] === "released");
    if (key === "post-queue-discovery") return Boolean(runtime?.["postQueueDiscoveryAt"] || [TaskState.PRODUCT_FOUND, TaskState.CART, TaskState.CHECKOUT, TaskState.SUCCESS].includes(task.state));
    if (key === "product-found") return Boolean(runtime?.["productFoundAt"] || [TaskState.CART, TaskState.CHECKOUT, TaskState.SUCCESS].includes(task.state));
    if (key === "cart") return Boolean(runtime?.["cartAt"] || [TaskState.CHECKOUT, TaskState.SUCCESS].includes(task.state));
    if (key === "checkout") return Boolean(runtime?.["checkoutAt"] || task.state === TaskState.SUCCESS);
    return false;
  }

  private proxySelectionLabel(selection: ProxySelection | undefined, profileId: string): string {
    if (selection?.mode === "direct") return "Direktverbindung";
    if (selection?.mode === "proxy" && selection.proxyId) return this.getProxyName(selection.proxyId);
    const profile = this.profiles.find(item => item.id === profileId);
    return profile?.preferredProxyId ? `Profilstandard · ${this.getProxyName(profile.preferredProxyId)}` : "Profilstandard · direkt";
  }

  private buildPaymentSession(): CheckoutPaymentSession {
    return {
      method: this.taskPaymentMethod,
      label: this.taskPaymentLabel.trim() || undefined
    };
  }

  private async refreshTaskView(taskId: string): Promise<void> {
    await Promise.all([
      this.loadTasks(),
      this.loadSystemStatus(),
      this.expandedTaskLogId === taskId ? this.loadTaskLogs(taskId) : Promise.resolve()
    ]);
  }

  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  private emptyProfile(): ProfileView {
    return toProfileV2Draft();
  }

  private emptyProxy(): AresProxy {
    return { id: "", name: "", protocol: "http", host: "", port: 8080, username: "", password: "" };
  }
}
