import { Component, OnDestroy, OnInit } from "@angular/core";
import { ElectronService } from "./services/electron.service";
import { ProfileBrowserService, type ProfileBrowserStatusView, type SeleniumBaseProfileBrowserStatusView } from "./services/profile-browser.service";
import { TaskState } from "../models";
import { COMMERCE_PLATFORMS, CommercePlatform } from "../commerce/platforms";
import type { CheckoutPaymentSession, PaymentMethod } from "../payments/models";
import type { AresProxy, ProxyProtocol, ProxySelection } from "../proxies/models";
import type { AresProfile } from "../profiles/models";
import { clearProfileBrowserUserAgent } from "../profiles/profile-browser-reset";
import {
  isCompleteCheckoutAddress,
  toPersistedAresProfile,
  toProfileV2Draft,
  type ProfileV2Draft
} from "../profiles/profile-v2";

type AppTab = "dashboard" | "modules" | "tasks" | "monitor" | "profiles" | "proxies" | "shops" | "captchas" | "settings";
type ProfileTab = "identity" | "address" | "browser" | "payment";
type TaskCreationMode = "monitor-only" | "auto-checkout";
type MonitorStrategyMode = "product-monitor" | "early-gate";
type ModuleModeId = "monitor" | "direct" | "early-gate";
type SettingsSection = "general" | "appearance" | "runtime" | "browser" | "monitor" | "providers" | "diagnostics" | "about";
type ProfileView = ProfileV2Draft;
type FlowStepKey = "monitoring" | "gate-detected" | "waiting-queue" | "released" | "post-queue-discovery" | "product-found" | "cart" | "checkout";
type CaptchaProviderCapability = "token" | "classify";

interface ModuleModeView {
  id: ModuleModeId;
  label: string;
  hint: string;
}

interface ModuleView {
  id: string;
  name: string;
  tagline: string;
  accent: string;
  platform: CommercePlatform;
  modes: ModuleModeView[];
}

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

interface ProxyImportPreview {
  line: string;
  proxy?: AresProxy;
  valid: boolean;
  duplicate: boolean;
  error?: string;
}

interface VisionRuntimeStatus {
  ready?: boolean;
  dependenciesReady?: boolean;
  model?: string;
  device?: string;
  error?: string;
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

interface UiSettings {
  confirmDestructive: boolean;
  defaultStartTab: AppTab;
  reduceMotion: boolean;
  backgroundEffects: boolean;
  compactDensity: boolean;
  compactSidebar: boolean;
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

  // Module hub (retailer modules). Selecting a module scopes the task builder
  // to that retailer and offers its supported task modes.
  selectedModuleId = "";
  moduleMode: ModuleModeId = "monitor";
  readonly modules: ModuleView[] = [
    {
      id: "pokemon-center",
      name: "Pokémon Center",
      tagline: "Queue · Captcha · Guest Checkout",
      accent: "#e60012",
      platform: "pokemon-center",
      modes: [
        { id: "monitor", label: "Nur Monitoring", hint: "Produkt überwachen und melden, ohne Checkout-Profil und ohne Kaufpfad." },
        { id: "direct", label: "Direkt zum Checkout", hint: "Produkt überwachen und beim Verfügbarwerden sofort in den Checkout – ohne Queue-Monitor." },
        { id: "early-gate", label: "Erst Queue, dann Checkout", hint: "Früh in die Warteschlange, Position halten, bei Release automatisch Checkout starten." }
      ]
    },
    {
      id: "shopify",
      name: "Shopify",
      tagline: "Password · Cart · Checkout",
      accent: "#95bf47",
      platform: "shopify",
      modes: [
        { id: "monitor", label: "Nur Monitoring", hint: "Shopify-Produkte überwachen und Signale sammeln, ohne Checkout zu starten." },
        { id: "direct", label: "Direct Task", hint: "Ein Browser, direkter Checkout-Flow" }
      ]
    }
  ];

  captchaProviders: Array<{
    id: string;
    name: string;
    envKey: string;
    capabilities: CaptchaProviderCapability[];
    enabled: boolean;
    configured: boolean;
    maskedKey: string;
    updatedAt?: string;
  }> = [];
  readonly fallbackCaptchaProviders: Array<{
    id: string;
    name: string;
    envKey: string;
    capabilities: CaptchaProviderCapability[];
    enabled: boolean;
    configured: boolean;
    maskedKey: string;
  }> = [
    { id: "capmonster", name: "CapMonster Cloud", envKey: "CAPMONSTER_API_KEY", capabilities: ["token"], enabled: false, configured: false, maskedKey: "" },
    { id: "twocaptcha", name: "2Captcha", envKey: "TWOCAPTCHA_API_KEY", capabilities: ["token"], enabled: false, configured: false, maskedKey: "" },
    { id: "capsolver", name: "CapSolver", envKey: "CAPSOLVER_API_KEY", capabilities: ["token"], enabled: false, configured: false, maskedKey: "" },
    { id: "anticaptcha", name: "Anti-Captcha", envKey: "ANTICAPTCHA_API_KEY", capabilities: ["token"], enabled: false, configured: false, maskedKey: "" },
    { id: "nocaptchaai", name: "NoCaptchaAI", envKey: "NOCAPTCHA_API_KEY", capabilities: ["classify"], enabled: false, configured: false, maskedKey: "" }
  ];
  captchaMode: "siglip" | "siglip-api" | "api" = "siglip-api";
  readonly captchaKeyDrafts: Record<string, string> = {};
  readonly captchaStatus: Record<string, string> = {};
  captchaModeNotice = "";
  captchaBusy = false;
  captchaProviderApiAvailable = true;

  taskName = "";
  searchTerm = "";  earlyGateProductName = "";
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
  proxyImportDraft = "";
  proxyImportPreview: ProxyImportPreview[] = [];
  proxyImportSaving = false;

  purchaseChanging = false;
  bulkStarting = false;
  bulkStopping = false;

  visionStatus?: VisionRuntimeStatus;
  visionBusy = false;
  visionStatusText = "Noch nicht geprüft.";

  readonly profileBrowserStatuses: Record<string, ProfileBrowserStatusView> = {};
  readonly seleniumBaseBrowserStatuses: Record<string, SeleniumBaseProfileBrowserStatusView> = {};
  readonly profileBrowserBusyIds = new Set<string>();
  readonly seleniumBaseBrowserBusyIds = new Set<string>();
  readonly settingsStartUrls: Record<string, string> = {};
  settingsMessage = "";
  settingsSection: SettingsSection = "general";
  readonly settingsSections: Array<{ id: SettingsSection; label: string; note: string }> = [
    { id: "general", label: "General", note: "Purchase guard & Start" },
    { id: "appearance", label: "Appearance", note: "Density & motion" },
    { id: "runtime", label: "Runtime", note: "Worker & storage" },
    { id: "browser", label: "Browser & Vision", note: "Manual browser actions" },
    { id: "monitor", label: "Monitor & Network", note: "Pipeline status" },
    { id: "providers", label: "Providers", note: "Captcha overview" },
    { id: "diagnostics", label: "Diagnostics", note: "Snapshot export" },
    { id: "about", label: "About", note: "Build facts" }
  ];
  uiSettings: UiSettings = {
    confirmDestructive: true,
    defaultStartTab: "dashboard",
    reduceMotion: false,
    backgroundEffects: false,
    compactDensity: true,
    compactSidebar: false
  };

  error = "";
  info = "";

  private unsubscribeStatus?: () => void;
  private taskUpdateTimer?: ReturnType<typeof setTimeout>;

  constructor(
    private readonly electron: ElectronService,
    private readonly profileBrowser: ProfileBrowserService
  ) {}

  async ngOnInit(): Promise<void> {
    this.loadUiSettings();
    this.activeTab = this.uiSettings.defaultStartTab;
    await Promise.all([
      this.loadShops(),
      this.loadProfiles(),
      this.loadProxies(),
      this.loadTasks(),
      this.loadSystemStatus(),
      this.loadCaptchaProviders(),
      this.loadVisionStatus()
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
    if (tab === "modules") this.selectedModuleId = "";
    if (tab === "settings") void this.refreshProfileBrowserStatuses();
    if (typeof window !== "undefined") {
      window.requestAnimationFrame(() => window.scrollTo({ top: 0, left: 0, behavior: "auto" }));
    }
  }

  setProfileTab(tab: ProfileTab): void {
    this.profileTab = tab;
  }

  setMonitorStrategy(mode: MonitorStrategyMode): void {
    this.monitorStrategyMode = mode;
    if (mode === "early-gate") this.taskMode = "auto-checkout";
  }

  get selectedModule(): ModuleView | undefined {
    return this.modules.find(module => module.id === this.selectedModuleId);
  }

  get highlightedModule(): ModuleView | undefined {
    return this.selectedModule ?? this.modules[0];
  }

  /** Tasks that belong to the currently opened module (by shop platform). */
  get moduleTasks(): TaskView[] {
    const module = this.selectedModule;
    if (!module) return [];
    return this.getModuleTasks(module);
  }

  /** Shops offered inside the task builder: scoped to the module when open. */
  get composerShopOptions(): ShopView[] {
    if (this.activeTab !== "modules" || !this.selectedModule) return this.shops;
    const matching = this.shops.filter(shop => shop.platform === this.selectedModule?.platform);
    return matching.length ? matching : this.shops;
  }

  openModule(moduleId: string): void {
    const module = this.modules.find(item => item.id === moduleId);
    if (!module) return;
    const previousModuleId = this.selectedModuleId;
    this.selectedModuleId = module.id;
    this.error = "";
    this.info = "";
    this.activeTab = "modules";
    this.selectModuleMode(module.modes[0]?.id ?? "monitor");
    if (!this.taskName.trim() || (previousModuleId !== module.id && this.taskName.endsWith(" Monitor"))) {
      this.taskName = `${module.name} Monitor`;
    }
    const shop = this.shops.find(item => item.platform === module.platform);
    if (shop) {
      this.selectedShopId = shop.id;
      this.onShopSelected();
    }
  }

  closeModule(): void {
    this.selectedModuleId = "";
  }

  selectSettingsSection(section: SettingsSection): void {
    this.settingsSection = section;
    if (section === "browser") void this.refreshProfileBrowserStatuses();
    if (section === "providers") void this.loadCaptchaProviders();
    if (section === "runtime" || section === "monitor" || section === "diagnostics") void this.loadSystemStatus();
  }

  toggleUiSetting(key: "confirmDestructive" | "reduceMotion" | "backgroundEffects" | "compactDensity" | "compactSidebar"): void {
    this.uiSettings = { ...this.uiSettings, [key]: !this.uiSettings[key] };
    this.persistUiSettings();
  }

  setDefaultStartTab(value: string): void {
    const allowed: AppTab[] = ["dashboard", "modules", "tasks", "monitor", "profiles", "proxies", "shops", "captchas", "settings"];
    if (!allowed.includes(value as AppTab)) return;
    this.uiSettings = { ...this.uiSettings, defaultStartTab: value as AppTab };
    this.persistUiSettings();
  }

  async copyDiagnostics(): Promise<void> {
    const snapshot = {
      createdAt: new Date().toISOString(),
      tasks: {
        total: this.tasks.length,
        running: this.runningTaskCount,
        queued: this.queuedTaskCount,
        stopped: this.stoppedTaskCount
      },
      modules: {
        total: this.modules.length,
        ready: this.readyModuleCount,
        partial: this.partialModuleCount,
        planned: this.plannedModuleCount
      },
      system: this.system,
      captcha: {
        mode: this.captchaMode,
        providers: this.captchaProviders.map(provider => ({
          id: provider.id,
          name: provider.name,
          enabled: provider.enabled,
          configured: provider.configured,
          capabilities: provider.capabilities
        }))
      },
      browserSessions: this.profiles.map(profile => {
        const id = String(profile.id ?? "");
        return {
          id,
          name: profile.name || id,
          profileBrowser: this.getProfileBrowserStatusText(id),
          seleniumBase: this.getSeleniumBaseBrowserStatusText(id)
        };
      })
    };
    const text = JSON.stringify(snapshot, null, 2);
    try {
      await navigator.clipboard?.writeText(text);
      this.settingsMessage = "Diagnostics snapshot kopiert.";
    } catch {
      this.settingsMessage = text;
    }
  }

  private loadUiSettings(): void {
    if (typeof localStorage === "undefined") return;
    try {
      const raw = localStorage.getItem("ares.uiSettings");
      if (!raw) return;
      const parsed = JSON.parse(raw) as Partial<UiSettings>;
      this.uiSettings = { ...this.uiSettings, ...parsed };
    } catch {
      // Ignore invalid local UI preferences; backend/runtime remains untouched.
    }
  }

  private persistUiSettings(): void {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem("ares.uiSettings", JSON.stringify(this.uiSettings));
  }

  getModuleKey(module: ModuleView): string {
    return module.id;
  }

  getModuleCapabilities(module: ModuleView): string[] {
    const capabilities: string[] = [];
    if (module.modes.some(mode => mode.id === "direct")) capabilities.push("Direct Checkout");
    if (this.system.commerceMonitorReady) capabilities.push("Monitor");
    if (module.modes.some(mode => mode.id === "early-gate")) capabilities.push("Early Gate");
    return capabilities;
  }

  getModuleStatusLevel(module: ModuleView): "ready" | "partial" | "planned" {
    const checks: boolean[] = [];
    if (module.modes.some(mode => mode.id === "direct")) {
      checks.push(this.executorPlatforms.includes(module.platform));
    }
    if (this.system.commerceMonitorReady) {
      checks.push(true);
    }
    if (module.modes.some(mode => mode.id === "early-gate")) {
      checks.push(this.system.earlyGateReady === true);
    }
    if (!checks.length || checks.every(value => !value)) return "planned";
    return checks.every(Boolean) ? "ready" : "partial";
  }

  getModuleStatusLabel(module: ModuleView): string {
    const level = this.getModuleStatusLevel(module);
    if (level === "ready") return "Ready";
    if (level === "partial") return "Partial";
    return "Planned";
  }

  getModuleStatusNote(module: ModuleView): string {
    const level = this.getModuleStatusLevel(module);
    if (level === "ready") return "All exposed capabilities ready";
    if (level === "partial") return "Some capabilities not exposed";
    return "Not exposed";
  }

  getModuleShopCount(module: ModuleView): number {
    return this.shops.filter(shop => shop.platform === module.platform).length;
  }

  getModuleTasks(module: ModuleView): TaskView[] {
    const shopIds = new Set(this.shops.filter(shop => shop.platform === module.platform).map(shop => shop.id));
    return this.tasks.filter(task => {
      const shopId = String(task.config.shopId ?? "");
      return shopIds.has(shopId) || shopId === module.id || shopId === module.platform;
    });
  }

  getModuleActiveTaskCount(module: ModuleView): number {
    return this.getModuleTasks(module).filter(task => ![TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED].includes(task.state)).length;
  }

  getModuleRunningTaskCount(module: ModuleView): number {
    return this.getModuleTasks(module).filter(task => this.isPausable(task)).length;
  }

  getModuleLastUpdate(module: ModuleView): string {
    const timestamps = this.getModuleTasks(module)
      .flatMap(task => this.getTaskRuntimeTimestamps(task))
      .map(value => new Date(value).getTime())
      .filter(value => Number.isFinite(value));
    if (!timestamps.length) return "—";
    return new Date(Math.max(...timestamps)).toLocaleString("de-DE");
  }

  private getTaskRuntimeTimestamps(task: TaskView): string[] {
    const data = task.config.data ?? {};
    const values: unknown[] = [];
    const records = [
      data["monitorPipeline"],
      data["queueStatus"],
      data["earlyGateRuntime"],
      data["autoCheckoutRuntime"],
      data["postQueueDiscovery"]
    ];
    for (const record of records) {
      if (!record || typeof record !== "object") continue;
      const source = record as Record<string, unknown>;
      values.push(source["updatedAt"], source["checkedAt"], source["detectedAt"], source["startedAt"], source["triggeredAt"]);
    }
    return values.map(value => String(value ?? "")).filter(Boolean);
  }

  async refreshProfileBrowserStatuses(): Promise<void> {
    await Promise.all(this.profiles.map(async profile => {
      const profileId = String(profile.id ?? "").trim();
      if (!profileId) return;
      const [profileResult, seleniumBaseResult] = await Promise.all([
        this.profileBrowser.getStatus(profileId).catch(() => undefined),
        this.profileBrowser.getSeleniumBaseStatus(profileId).catch(() => undefined)
      ]);
      if (profileResult?.success && profileResult.status) this.profileBrowserStatuses[profileId] = profileResult.status;
      if (seleniumBaseResult?.success && seleniumBaseResult.status) this.seleniumBaseBrowserStatuses[profileId] = seleniumBaseResult.status;
    }));
  }

  isProfileBrowserOpen(profileId: string): boolean {
    return this.profileBrowserStatuses[profileId]?.open === true;
  }

  isSeleniumBaseBrowserOpen(profileId: string): boolean {
    return this.seleniumBaseBrowserStatuses[profileId]?.open === true;
  }

  getProfileBrowserStatusText(profileId: string): string {
    const status = this.profileBrowserStatuses[profileId];
    if (!status?.open) return "geschlossen";
    return status.pid ? `offen · PID ${status.pid}` : "offen";
  }

  getSeleniumBaseBrowserStatusText(profileId: string): string {
    const status = this.seleniumBaseBrowserStatuses[profileId];
    if (!status?.open) return "geschlossen";
    return status.pid ? `offen · PID ${status.pid}` : "offen";
  }

  async openManualProfileBrowser(profile: ProfileView): Promise<void> {
    const profileId = String(profile.id ?? "").trim();
    if (!profileId || this.profileBrowserBusyIds.has(profileId)) return;
    this.profileBrowserBusyIds.add(profileId);
    this.settingsMessage = "";
    try {
      const result = await this.profileBrowser.open(profileId, this.settingsStartUrls[profileId]?.trim() || undefined);
      if (!result?.success) {
        this.settingsMessage = result?.error || "Profilbrowser konnte nicht geöffnet werden.";
        return;
      }
      this.profileBrowserStatuses[profileId] = result.status;
      this.settingsMessage = `${profile.name || profileId}: Profilbrowser geöffnet.`;
    } finally {
      this.profileBrowserBusyIds.delete(profileId);
    }
  }

  async closeManualProfileBrowser(profile: ProfileView): Promise<void> {
    const profileId = String(profile.id ?? "").trim();
    if (!profileId || this.profileBrowserBusyIds.has(profileId)) return;
    this.profileBrowserBusyIds.add(profileId);
    this.settingsMessage = "";
    try {
      const result = await this.profileBrowser.close(profileId);
      if (!result?.success) {
        this.settingsMessage = result?.error || "Profilbrowser konnte nicht geschlossen werden.";
        return;
      }
      this.profileBrowserStatuses[profileId] = result.status;
      this.settingsMessage = `${profile.name || profileId}: Profilbrowser geschlossen.`;
    } finally {
      this.profileBrowserBusyIds.delete(profileId);
    }
  }

  async openSeleniumBaseProfileBrowser(profile: ProfileView): Promise<void> {
    const profileId = String(profile.id ?? "").trim();
    if (!profileId || this.seleniumBaseBrowserBusyIds.has(profileId)) return;
    this.seleniumBaseBrowserBusyIds.add(profileId);
    this.settingsMessage = "";
    try {
      const result = await this.profileBrowser.openSeleniumBase(profileId, this.settingsStartUrls[profileId]?.trim() || undefined);
      if (!result?.success) {
        this.settingsMessage = result?.error || "SeleniumBase-CDP konnte nicht geöffnet werden.";
        return;
      }
      this.seleniumBaseBrowserStatuses[profileId] = result.status;
      this.settingsMessage = `${profile.name || profileId}: SeleniumBase-CDP geöffnet.`;
    } finally {
      this.seleniumBaseBrowserBusyIds.delete(profileId);
    }
  }

  async closeSeleniumBaseProfileBrowser(profile: ProfileView): Promise<void> {
    const profileId = String(profile.id ?? "").trim();
    if (!profileId || this.seleniumBaseBrowserBusyIds.has(profileId)) return;
    this.seleniumBaseBrowserBusyIds.add(profileId);
    this.settingsMessage = "";
    try {
      const result = await this.profileBrowser.closeSeleniumBase(profileId);
      if (!result?.success) {
        this.settingsMessage = result?.error || "SeleniumBase-CDP konnte nicht geschlossen werden.";
        return;
      }
      this.seleniumBaseBrowserStatuses[profileId] = result.status;
      this.settingsMessage = `${profile.name || profileId}: SeleniumBase-CDP geschlossen.`;
    } finally {
      this.seleniumBaseBrowserBusyIds.delete(profileId);
    }
  }

  async resetProfileBrowserSession(profile: ProfileView): Promise<void> {
    const profileId = String(profile.id ?? "").trim();
    if (!profileId || this.profileBrowserBusyIds.has(profileId)) return;
    const confirmed = !this.uiSettings.confirmDestructive || typeof window === "undefined" || window.confirm(
      `Browser-Session für ${profile.name || profileId} löschen?\n\nCookies, Storage, Cache, Cookie-Snapshots und gespeicherter User-Agent werden entfernt.`
    );
    if (!confirmed) return;

    this.profileBrowserBusyIds.add(profileId);
    this.settingsMessage = "";
    try {
      const persisted = toPersistedAresProfile(profile);
      const saveResult = await this.electron.saveProfile(clearProfileBrowserUserAgent(persisted));
      if (!saveResult?.success) {
        this.settingsMessage = saveResult?.error || "User-Agent konnte vor dem Reset nicht zurückgesetzt werden.";
        return;
      }
      const result = await this.profileBrowser.resetSession(profileId);
      if (!result?.success) {
        this.settingsMessage = result?.error || "Browser-Session konnte nicht gelöscht werden.";
        return;
      }
      this.profileBrowserStatuses[profileId] = result.status;
      this.seleniumBaseBrowserStatuses[profileId] = result.status;
      this.settingsMessage = `${profile.name || profileId}: Browser-Session gelöscht.`;
      await this.loadProfiles();
    } finally {
      this.profileBrowserBusyIds.delete(profileId);
    }
  }

  selectModuleMode(mode: ModuleModeId): void {
    this.moduleMode = mode;
    if (mode === "monitor") {
      this.setMonitorStrategy("product-monitor");
      this.taskMode = "monitor-only";
      return;
    }
    if (mode === "early-gate") {
      this.setMonitorStrategy("early-gate");
      return;
    }
    this.setMonitorStrategy("product-monitor");
    this.taskMode = "auto-checkout";
  }

  // ---------- Captcha providers ----------

  async loadCaptchaProviders(): Promise<void> {
    const api = (window as any).ares;
    if (!api?.listCaptchaProviders) {
      this.captchaProviderApiAvailable = false;
      this.captchaProviders = this.fallbackCaptchaProviders.map(provider => ({ ...provider }));
      return;
    }
    const result = await api.listCaptchaProviders().catch(() => undefined);
    if (result?.success) {
      this.captchaProviderApiAvailable = true;
      this.captchaProviders = result.providers?.length
        ? result.providers
        : this.fallbackCaptchaProviders.map(provider => ({ ...provider }));
    } else {
      this.captchaProviderApiAvailable = false;
      this.captchaProviders = this.fallbackCaptchaProviders.map(provider => ({ ...provider }));
      this.captchaModeNotice = result?.error || "Captcha-Provider konnten nicht vom Backend geladen werden.";
    }
    const mode = await api.getCaptchaMode?.().catch(() => undefined);
    if (mode?.success) this.captchaMode = mode.mode;
  }

  async setCaptchaMode(mode: "siglip" | "siglip-api" | "api"): Promise<void> {
    this.captchaMode = mode;
    this.captchaModeNotice = "";
    const configuredApiProviders = this.captchaProviders.filter(provider => provider.configured);
    if (mode === "api" && !configuredApiProviders.length) {
      this.captchaModeNotice = "Nur API ist ausgewählt, aber es ist noch kein Captcha-API-Key hinterlegt. Anbieter unten hinzufügen und speichern.";
    } else if (mode === "siglip-api" && !configuredApiProviders.length) {
      this.captchaModeNotice = "SigLIP ist aktiv. API-Fallback startet erst, wenn ein Anbieter-Key gespeichert ist.";
    } else if (mode === "siglip") {
      this.captchaModeNotice = "Nur lokales SigLIP ist aktiv. Externe API-Anbieter werden übersprungen.";
    }

    const api = (window as any).ares;
    if (!api?.setCaptchaMode) return;
    const result = await api.setCaptchaMode(mode).catch(() => undefined);
    if (result?.success) {
      this.captchaMode = result.mode;
      return;
    }
    this.captchaModeNotice = result?.error || this.captchaModeNotice || "Captcha-Modus konnte nicht gespeichert werden.";
  }

  async saveCaptchaProvider(id: string): Promise<void> {
    const api = (window as any).ares;
    if (!api?.saveCaptchaProvider) {
      this.captchaStatus[id] = "Speichern ist nur in der Electron-App mit Preload/IPC verfügbar.";
      return;
    }
    this.captchaBusy = true;
    try {
      const result = await api.saveCaptchaProvider(id, { apiKey: this.captchaKeyDrafts[id] || "", enabled: true });
      if (!result?.success) {
        this.captchaStatus[id] = result?.error || "Speichern fehlgeschlagen.";
        return;
      }
      this.captchaKeyDrafts[id] = "";
      this.captchaStatus[id] = "Gespeichert.";
      await this.loadCaptchaProviders();
    } finally {
      this.captchaBusy = false;
    }
  }

  async deleteCaptchaProvider(id: string): Promise<void> {
    const api = (window as any).ares;
    if (!api?.deleteCaptchaProvider) {
      this.captchaStatus[id] = "Entfernen ist nur in der Electron-App mit Preload/IPC verfügbar.";
      return;
    }
    this.captchaBusy = true;
    try {
      await api.deleteCaptchaProvider(id);
      this.captchaStatus[id] = "Entfernt.";
      await this.loadCaptchaProviders();
    } finally {
      this.captchaBusy = false;
    }
  }

  async testCaptchaProvider(id: string): Promise<void> {
    const api = (window as any).ares;
    if (!api?.testCaptchaProvider) {
      this.captchaStatus[id] = "Test ist nur in der Electron-App mit Preload/IPC verfügbar.";
      return;
    }
    this.captchaBusy = true;
    this.captchaStatus[id] = "Teste…";
    try {
      const result = await api.testCaptchaProvider(id).catch(() => undefined);
      if (!result) {
        this.captchaStatus[id] = "Test fehlgeschlagen.";
        return;
      }
      if (!result.success) {
        this.captchaStatus[id] = result.error || "Test fehlgeschlagen.";
        return;
      }
      this.captchaStatus[id] = result.valid
        ? `Gültig${result.balance !== undefined ? ` · Guthaben: ${result.balance}` : ""}`
        : `Abgelehnt${result.error ? ` (${result.error})` : ""}`;
    } finally {
      this.captchaBusy = false;
    }
  }

  isCaptchaStatusSuccess(value?: string): boolean {
    return /gespeichert|entfernt|gültig/i.test(value || "");
  }

  isCaptchaStatusWarning(value?: string): boolean {
    return /electron-app|preload|ipc|teste|nicht verfügbar/i.test(value || "");
  }

  isCaptchaStatusError(value?: string): boolean {
    return /fehlgeschlagen|abgelehnt|fehler|error/i.test(value || "");
  }

  async loadVisionStatus(): Promise<void> {
    const result = await this.electron.getSeleniumBaseVisionStatus().catch(error => ({
      success: false,
      error: error instanceof Error ? error.message : String(error)
    }));
    if (!result?.success) {
      this.visionStatus = { ready: false, error: result?.error || "Status nicht verfügbar." };
      this.visionStatusText = this.visionStatus.error || "Status nicht verfügbar.";
      return;
    }
    this.visionStatus = result.status ?? {};
    this.visionStatusText = this.getVisionStatusLabel();
  }

  async prepareVisionRuntime(): Promise<void> {
    if (this.visionBusy) return;
    this.visionBusy = true;
    this.error = "";
    this.info = "";
    this.visionStatusText = "SigLIP2 wird lokal vorbereitet…";
    try {
      const result = await this.electron.prepareSeleniumBaseVision();
      if (!result?.success) {
        this.visionStatus = { ready: false, error: result?.error || "Vorbereitung fehlgeschlagen." };
        this.visionStatusText = this.visionStatus.error || "Vorbereitung fehlgeschlagen.";
        this.error = this.visionStatusText;
        return;
      }
      const status = (result.status ?? {}) as VisionRuntimeStatus;
      this.visionStatus = status;
      this.visionStatusText = this.getVisionStatusLabel();
      this.info = status.ready
        ? "SigLIP2 ist lokal vorbereitet."
        : status.error || "SigLIP2 ist noch nicht bereit.";
    } finally {
      this.visionBusy = false;
    }
  }

  onShopSelected(): void {
    const shop = this.shops.find(item => item.id === this.selectedShopId);
    if (this.activeTab === "modules" && this.moduleMode === "monitor") return;
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

  get monitorTasks(): TaskView[] {
    return this.tasks.filter(task => this.isMonitorTask(task));
  }

  get monitorTaskCount(): number {
    return this.monitorTasks.length;
  }

  get errorTaskCount(): number {
    return this.tasks.filter(task => task.state === TaskState.FAILED || Boolean(task.lastError)).length;
  }

  get runningTaskCount(): number {
    return this.tasks.filter(task => task.state === TaskState.RUNNING).length;
  }

  get queuedTaskCount(): number {
    return this.tasks.filter(task => task.state === TaskState.QUEUED).length;
  }

  get stoppedTaskCount(): number {
    return this.tasks.filter(task => [TaskState.FAILED, TaskState.CANCELLED].includes(task.state)).length;
  }

  get readyModuleCount(): number {
    return this.modules.filter(module => this.getModuleStatusLevel(module) === "ready").length;
  }

  get partialModuleCount(): number {
    return this.modules.filter(module => this.getModuleStatusLevel(module) === "partial").length;
  }

  get plannedModuleCount(): number {
    return this.modules.filter(module => this.getModuleStatusLevel(module) === "planned").length;
  }

  get recentTasks(): TaskView[] {
    return this.tasks.slice().reverse().slice(0, 8);
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
    if (this.uiSettings.confirmDestructive && !window.confirm(`Profil „${profile.name}" wirklich löschen? Browserdaten und Cookie-Snapshots werden ebenfalls entfernt.`)) return;
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

  updateProxyImportPreview(): void {
    const seen = new Set<string>();
    const existing = new Set(this.proxies.map(proxy => this.proxyFingerprint(proxy)));
    this.proxyImportPreview = this.proxyImportDraft
      .split(/\r?\n/)
      .map(line => line.trim())
      .filter(Boolean)
      .map((line, index) => {
        const parsed = this.parseProxyImportLine(line, index);
        if (!parsed.valid || !parsed.proxy) return parsed;
        const fingerprint = this.proxyFingerprint(parsed.proxy);
        const duplicate = existing.has(fingerprint) || seen.has(fingerprint);
        seen.add(fingerprint);
        return duplicate ? { ...parsed, duplicate: true, error: "Duplikat" } : parsed;
      });
  }

  async importProxyPreview(): Promise<void> {
    this.updateProxyImportPreview();
    const validRows = this.proxyImportPreview.filter(row => row.valid && row.proxy && !row.duplicate);
    if (!validRows.length || this.proxyImportSaving) {
      this.error = "Keine gültigen neuen Proxies zum Importieren.";
      return;
    }

    this.proxyImportSaving = true;
    this.error = "";
    this.info = `Importiere ${validRows.length} Proxy(s)…`;
    let saved = 0;
    const failures: string[] = [];
    try {
      for (const row of validRows) {
        const result = await this.electron.saveProxy(row.proxy);
        if (result?.success) saved += 1;
        else failures.push(`${row.line}: ${result?.error || "Speichern fehlgeschlagen"}`);
      }
    } finally {
      this.proxyImportSaving = false;
    }

    await Promise.all([this.loadProxies(), this.loadSystemStatus()]);
    if (failures.length) {
      this.error = failures.slice(0, 3).join(" · ");
    }
    this.info = failures.length
      ? `${saved}/${validRows.length} Proxies importiert · ${failures.length} Fehler.`
      : `${saved} Proxies importiert.`;
    if (!failures.length) this.proxyImportDraft = "";
    this.updateProxyImportPreview();
  }

  get proxyImportValidCount(): number {
    return this.proxyImportPreview.filter(row => row.valid && !row.duplicate).length;
  }

  get proxyImportInvalidCount(): number {
    return this.proxyImportPreview.filter(row => !row.valid).length;
  }

  get proxyImportDuplicateCount(): number {
    return this.proxyImportPreview.filter(row => row.duplicate).length;
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
    const confirmed = !this.uiSettings.confirmDestructive || typeof window === "undefined" || window.confirm(
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

  getMonitorPipeline(task: TaskView): Record<string, unknown> | undefined {
    const value = task.config.data?.["monitorPipeline"];
    return value && typeof value === "object" ? value as Record<string, unknown> : undefined;
  }

  getMonitorStage(task: TaskView): string {
    const pipeline = this.getMonitorPipeline(task);
    const strategy = task.config.data?.["monitorStrategy"] as Record<string, unknown> | undefined;
    return String(pipeline?.["stage"] ?? strategy?.["mode"] ?? "monitoring").replace(/-/g, " ");
  }

  getMonitorSource(task: TaskView): string {
    const pipeline = this.getMonitorPipeline(task);
    return String(pipeline?.["source"] ?? pipeline?.["mode"] ?? "—");
  }

  getMonitorAvailability(task: TaskView): string {
    const pipeline = this.getMonitorPipeline(task);
    const state = pipeline?.["productState"];
    if (typeof pipeline?.["available"] === "boolean") return pipeline["available"] ? "Available" : "Not available";
    return state ? String(state) : "—";
  }

  getMonitorStatusCode(task: TaskView): string {
    const pipeline = this.getMonitorPipeline(task);
    const code = pipeline?.["statusCode"];
    return typeof code === "number" || typeof code === "string" ? String(code) : "—";
  }

  getMonitorQueueSummary(task: TaskView): string {
    const queue = task.config.data?.["queueStatus"] as Record<string, unknown> | undefined;
    const position = queue?.["position"];
    const wait = queue?.["timeToWaitSeconds"];
    if (position === undefined && wait === undefined) return "—";
    const parts = [];
    if (position !== undefined) parts.push(`Position ${position}`);
    if (wait !== undefined) parts.push(`${wait}s wait`);
    return parts.join(" · ");
  }

  getMonitorUpdatedAt(task: TaskView): string {
    const pipeline = this.getMonitorPipeline(task);
    const raw = pipeline?.["updatedAt"] ?? task.config.data?.["updatedAt"];
    if (!raw) return "—";
    const date = new Date(String(raw));
    return Number.isNaN(date.getTime()) ? String(raw) : date.toLocaleString("de-DE");
  }

  getMonitorError(task: TaskView): string {
    const pipeline = this.getMonitorPipeline(task);
    return String(pipeline?.["error"] ?? task.lastError ?? "");
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

  private getVisionStatusLabel(): string {
    if (!this.visionStatus) return "Noch nicht geprüft.";
    if (this.visionStatus.ready) {
      const model = this.visionStatus.model ? ` · ${this.visionStatus.model}` : "";
      const device = this.visionStatus.device ? ` · ${this.visionStatus.device}` : "";
      return `Bereit${model}${device}`;
    }
    if (this.visionStatus.error) return this.visionStatus.error;
    if (this.visionStatus.dependenciesReady === false) return "Abhängigkeiten fehlen.";
    return "Nicht bereit.";
  }

  private parseProxyImportLine(line: string, index: number): ProxyImportPreview {
    let protocol: ProxyProtocol = "http";
    let host = "";
    let port = 0;
    let username = "";
    let password = "";

    try {
      if (/^(https?|socks5):\/\//i.test(line)) {
        const url = new URL(line);
        protocol = url.protocol.replace(":", "") as ProxyProtocol;
        host = url.hostname;
        port = Number(url.port);
        username = decodeURIComponent(url.username || "");
        password = decodeURIComponent(url.password || "");
      } else if (line.includes("@")) {
        const [auth, endpoint] = line.split("@");
        const [rawUser, ...rawPass] = auth.split(":");
        username = rawUser || "";
        password = rawPass.join(":");
        const parts = endpoint.split(":");
        host = parts[0] || "";
        port = Number(parts[1]);
      } else {
        const parts = line.split(":");
        if (parts.length === 2 || parts.length === 4) {
          host = parts[0] || "";
          port = Number(parts[1]);
          username = parts[2] || "";
          password = parts[3] || "";
        }
      }
    } catch (error) {
      return { line, valid: false, duplicate: false, error: error instanceof Error ? error.message : "Ungültiges Format" };
    }

    if (!["http", "https", "socks5"].includes(protocol)) {
      return { line, valid: false, duplicate: false, error: "Protokoll muss HTTP, HTTPS oder SOCKS5 sein." };
    }
    if (!host || !Number.isInteger(port) || port < 1 || port > 65535) {
      return { line, valid: false, duplicate: false, error: "Host oder Port ungültig." };
    }

    const id = this.buildProxyImportId(host, port, index);
    const proxy: AresProxy = {
      id,
      name: `${host}:${port}`,
      protocol,
      host,
      port,
      username: username.trim() || undefined,
      password: password || undefined
    };
    return { line, proxy, valid: true, duplicate: false };
  }

  private buildProxyImportId(host: string, port: number, index: number): string {
    const slug = `${host}-${port}`.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "proxy";
    let id = `proxy-${slug}-${index + 1}`;
    const existingIds = new Set([
      ...this.proxies.map(proxy => proxy.id),
      ...(this.proxyImportPreview.map(row => row.proxy?.id).filter(Boolean) as string[])
    ]);
    let counter = index + 1;
    while (existingIds.has(id)) {
      id = `proxy-${slug}-${counter}`;
      counter += 1;
    }
    return id;
  }

  private proxyFingerprint(proxy: AresProxy): string {
    return [
      proxy.protocol,
      proxy.host.trim().toLowerCase(),
      Number(proxy.port),
      proxy.username?.trim().toLowerCase() || "",
      proxy.password || ""
    ].join("|");
  }
}
