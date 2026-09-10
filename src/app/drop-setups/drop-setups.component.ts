import { Component, OnDestroy, OnInit, ViewEncapsulation } from "@angular/core";
import { TaskState } from "../../models";
import type { CheckoutPaymentSession } from "../../payments/models";
import type { AresProfile } from "../../profiles/models";
import type { AresProxy, ProxySelection } from "../../proxies/models";
import { ElectronService } from "../services/electron.service";

type DropStrategy = "early-gate" | "product-monitor";
type FeedbackKind = "info" | "success" | "error";

interface DropAssignment {
  id: string;
  profileId: string;
  proxySelection: ProxySelection;
  headless: boolean;
}

interface DropSetup {
  version: 1;
  id: string;
  name: string;
  shopId: string;
  strategy: DropStrategy;
  productName: string;
  searchTerm: string;
  discoveryKeywords: string[];
  monitorIntervalSeconds: number;
  assignments: DropAssignment[];
  staggerMs: number;
  createdAt: string;
  updatedAt: string;
}

interface LocalFeedback {
  kind: FeedbackKind;
  text: string;
}

@Component({
  selector: ".task-builder",
  templateUrl: "./drop-setups.component.html",
  styleUrls: ["./drop-setups.component.scss"],
  encapsulation: ViewEncapsulation.None
})
export class DropSetupsComponent implements OnInit, OnDestroy {
  readonly storageKey = "ares.dropSetups.v1";
  readonly steps = [
    { number: 1, label: "Shop & Strategie" },
    { number: 2, label: "Produkt & Keywords" },
    { number: 3, label: "Profile & Proxys" },
    { number: 4, label: "Speichern & Starten" }
  ];

  shops: any[] = [];
  profiles: AresProfile[] = [];
  proxies: AresProxy[] = [];
  tasks: any[] = [];
  setups: DropSetup[] = [];
  taskLogs: Record<string, any[]> = {};
  keywordDrafts: Record<string, string> = {};

  wizardOpen = false;
  wizardStep = 1;
  editingSetupId = "";
  expandedSetupId = "";
  expandedRunId = "";
  startingSetupId = "";
  feedback?: LocalFeedback;

  draft: DropSetup = this.emptySetup();

  sessionPaymentEnabled = false;

  private unsubscribeStatus?: () => void;

  constructor(private readonly electron: ElectronService) {}

  async ngOnInit(): Promise<void> {
    this.loadStoredSetups();
    await this.refreshReferenceData();
    await this.loadTasks();
    this.unsubscribeStatus = this.electron.onTaskStatusUpdate(() => void this.loadTasks());
  }

  ngOnDestroy(): void {
    this.unsubscribeStatus?.();
  }

  openNewSetup(): void {
    this.draft = this.emptySetup();
    this.editingSetupId = "";
    this.wizardStep = 1;
    this.wizardOpen = true;
    this.feedback = undefined;
  }

  editSetup(setup: DropSetup): void {
    this.draft = this.cloneSetup(setup);
    this.editingSetupId = setup.id;
    this.wizardStep = 1;
    this.wizardOpen = true;
    this.feedback = undefined;
  }

  closeWizard(): void {
    this.wizardOpen = false;
    this.wizardStep = 1;
    this.editingSetupId = "";
  }

  previousStep(): void {
    this.feedback = undefined;
    this.wizardStep = Math.max(1, this.wizardStep - 1);
  }

  nextStep(): void {
    const error = this.validateStep(this.wizardStep);
    if (error) {
      this.feedback = { kind: "error", text: error };
      return;
    }
    this.feedback = undefined;
    this.wizardStep = Math.min(4, this.wizardStep + 1);
  }

  setStrategy(strategy: DropStrategy): void {
    this.draft.strategy = strategy;
    this.feedback = undefined;
  }

  addKeyword(): void {
    const input = document.querySelector<HTMLInputElement>("#drop-keyword-input");
    const value = input?.value.trim().replace(/\s+/g, " ") || "";
    if (!value) return;
    if (!this.draft.discoveryKeywords.some(item => item.toLocaleLowerCase("de-DE") === value.toLocaleLowerCase("de-DE"))) {
      this.draft.discoveryKeywords = [...this.draft.discoveryKeywords, value.slice(0, 160)];
    }
    if (input) input.value = "";
  }

  removeKeyword(keyword: string): void {
    this.draft.discoveryKeywords = this.draft.discoveryKeywords.filter(item => item !== keyword);
  }

  hasAssignment(profileId: string): boolean {
    return this.draft.assignments.some(item => item.profileId === profileId);
  }

  assignmentFor(profileId: string): DropAssignment | undefined {
    return this.draft.assignments.find(item => item.profileId === profileId);
  }

  toggleAssignment(profileId: string, selected: boolean): void {
    if (selected && !this.hasAssignment(profileId)) {
      this.draft.assignments = [
        ...this.draft.assignments,
        {
          id: this.makeId("assignment"),
          profileId,
          proxySelection: { mode: "profile-default" },
          headless: false
        }
      ];
      return;
    }
    if (!selected) this.draft.assignments = this.draft.assignments.filter(item => item.profileId !== profileId);
  }

  setAssignmentProxyMode(profileId: string, mode: ProxySelection["mode"]): void {
    const assignment = this.assignmentFor(profileId);
    if (!assignment) return;
    assignment.proxySelection = mode === "proxy"
      ? { mode: "proxy", proxyId: assignment.proxySelection.proxyId || "" }
      : { mode };
  }

  setAssignmentProxy(profileId: string, proxyId: string): void {
    const assignment = this.assignmentFor(profileId);
    if (!assignment) return;
    assignment.proxySelection = { mode: "proxy", proxyId };
  }

  setAssignmentHeadless(profileId: string, headless: boolean): void {
    const assignment = this.assignmentFor(profileId);
    if (assignment) assignment.headless = headless;
  }

  saveDraft(): DropSetup | undefined {
    const error = this.validateAll();
    if (error) {
      this.feedback = { kind: "error", text: error };
      return undefined;
    }

    const now = new Date().toISOString();
    const existing = this.setups.find(item => item.id === this.draft.id);
    const saved: DropSetup = {
      ...this.cloneSetup(this.draft),
      version: 1,
      id: existing?.id || this.draft.id || this.makeId("drop"),
      name: this.draft.name.trim(),
      shopId: this.draft.shopId.trim(),
      productName: this.draft.productName.trim(),
      searchTerm: this.draft.searchTerm.trim(),
      discoveryKeywords: this.normalizeKeywords(this.draft.discoveryKeywords),
      monitorIntervalSeconds: this.safeInterval(this.draft.monitorIntervalSeconds),
      staggerMs: this.safeStagger(this.draft.staggerMs),
      createdAt: existing?.createdAt || this.draft.createdAt || now,
      updatedAt: now
    };

    const index = this.setups.findIndex(item => item.id === saved.id);
    if (index >= 0) this.setups = this.setups.map(item => item.id === saved.id ? saved : item);
    else this.setups = [saved, ...this.setups];

    this.persistSetups();
    this.draft = this.cloneSetup(saved);
    this.editingSetupId = saved.id;
    this.feedback = { kind: "success", text: `Drop Setup „${saved.name}“ gespeichert.` };
    return saved;
  }

  async saveAndStart(): Promise<void> {
    const setup = this.saveDraft();
    if (!setup) return;
    await this.startSetup(setup);
  }

  async startSetup(setup: DropSetup): Promise<void> {
    if (this.startingSetupId) return;
    const error = this.validateSetup(setup);
    if (error) {
      this.feedback = { kind: "error", text: error };
      return;
    }

    this.startingSetupId = setup.id;
    this.feedback = { kind: "info", text: `${setup.assignments.length} Run(s) werden vorbereitet…` };
    let started = 0;
    let failure = "";
    const launchStamp = Date.now();

    try {
      for (let index = 0; index < setup.assignments.length; index += 1) {
        const assignment = setup.assignments[index];
        if (index > 0 && setup.staggerMs > 0) await this.delay(setup.staggerMs);

        const taskId = `drop_${this.safeId(setup.id)}_${this.safeId(assignment.profileId)}_${launchStamp}_${index + 1}`;
        const taskName = `${setup.name} · ${this.profileName(assignment.profileId)}`;
        const data: Record<string, unknown> = {
          monitorIntervalMs: this.safeInterval(setup.monitorIntervalSeconds) * 1_000,
          monitorAction: {
            mode: "auto-checkout",
            profileId: assignment.profileId,
            proxySelection: assignment.proxySelection,
            headless: assignment.headless,
            paymentEnabled: this.sessionPaymentEnabled
          },
          monitorStrategy: setup.strategy === "early-gate"
            ? {
                mode: "early-gate",
                productName: setup.productName,
                discoveryKeywords: this.normalizeKeywords(setup.discoveryKeywords)
              }
            : { mode: "product-monitor" },
          dropSetupId: setup.id,
          dropSetupRun: {
            setupName: setup.name,
            assignmentId: assignment.id,
            profileId: assignment.profileId,
            startedFromUiAt: new Date().toISOString()
          }
        };
        if (setup.strategy === "product-monitor") data["productCriteria"] = { searchTerm: setup.searchTerm };

        const created = await this.electron.createTask({
          id: taskId,
          name: taskName,
          shopId: setup.shopId,
          data
        });
        if (!created?.success) {
          failure = created?.error || `${taskName} konnte nicht erstellt werden.`;
          break;
        }

        if (this.sessionPaymentEnabled) {
          const payment = await this.electron.setPaymentSession(taskId, this.buildPaymentSession(assignment.profileId));
          if (!payment?.success) {
            failure = payment?.error || `${taskName}: Zahlungs-Session konnte nicht gesetzt werden.`;
            break;
          }
        }

        const start = await this.electron.startTask(taskId);
        if (!start?.success) {
          failure = start?.error || `${taskName} konnte nicht gestartet werden.`;
          break;
        }
        started += 1;
      }
    } finally {
      this.startingSetupId = "";
      await this.loadTasks();
    }

    this.feedback = failure
      ? { kind: "error", text: `${started}/${setup.assignments.length} Run(s) gestartet. ${failure}` }
      : { kind: "success", text: `${started} Run(s) gestartet${setup.staggerMs ? ` · ${setup.staggerMs / 1_000}s Staffelung` : ""}.` };
  }

  deleteSetup(setup: DropSetup): void {
    if (!window.confirm(`Drop Setup „${setup.name}“ wirklich löschen? Bereits erstellte Runs bleiben erhalten.`)) return;
    this.setups = this.setups.filter(item => item.id !== setup.id);
    this.persistSetups();
    if (this.expandedSetupId === setup.id) this.expandedSetupId = "";
    this.feedback = { kind: "success", text: `Drop Setup „${setup.name}“ gelöscht.` };
  }

  toggleSetup(setupId: string): void {
    this.expandedSetupId = this.expandedSetupId === setupId ? "" : setupId;
    this.expandedRunId = "";
  }

  async toggleRun(run: any): Promise<void> {
    if (this.expandedRunId === run.id) {
      this.expandedRunId = "";
      return;
    }
    this.expandedRunId = run.id;
    await this.loadTaskLogs(run.id);
    const child = this.childFor(run);
    if (child) await this.loadTaskLogs(child.id);
    const discoveryTask = child?.state === TaskState.POST_QUEUE_DISCOVERY ? child : run.state === TaskState.POST_QUEUE_DISCOVERY ? run : undefined;
    if (discoveryTask && this.keywordDrafts[run.id] === undefined) {
      this.keywordDrafts[run.id] = this.discoveryKeywordsFor(run).join(", ");
    }
  }

  async updateLiveKeywords(run: any): Promise<void> {
    const child = this.childFor(run);
    const target = child?.state === TaskState.POST_QUEUE_DISCOVERY ? child : run.state === TaskState.POST_QUEUE_DISCOVERY ? run : undefined;
    if (!target) {
      this.feedback = { kind: "error", text: "Keywords sind nur während POST_QUEUE_DISCOVERY live änderbar." };
      return;
    }
    const keywords = this.normalizeKeywords((this.keywordDrafts[run.id] || "").split(/[\n,]+/));
    const result = await this.electron.updateDiscoveryKeywords(target.id, keywords);
    if (!result?.success) {
      this.feedback = { kind: "error", text: result?.error || "Keywords konnten nicht aktualisiert werden." };
      return;
    }
    this.keywordDrafts[run.id] = (result.keywords || keywords).join(", ");
    this.feedback = { kind: "success", text: "Live-Keywords vom Browser-Run bestätigt." };
    await this.loadTasks();
  }

  async pauseRun(run: any): Promise<void> {
    const target = this.childFor(run) || run;
    const result = await this.electron.pauseTask(target.id);
    this.feedback = result?.success
      ? { kind: "success", text: "Run pausiert." }
      : { kind: "error", text: result?.error || "Run konnte nicht pausiert werden." };
    await this.loadTasks();
  }

  async stopRun(run: any): Promise<void> {
    const child = this.childFor(run);
    if (child) await this.electron.clearPaymentSession(child.id).catch(() => undefined);
    await this.electron.clearPaymentSession(run.id).catch(() => undefined);
    const target = child || run;
    const result = await this.electron.stopTask(target.id);
    this.feedback = result?.success
      ? { kind: "success", text: "Run gestoppt." }
      : { kind: "error", text: result?.error || "Run konnte nicht gestoppt werden." };
    await this.loadTasks();
  }

  setupRuns(setupId: string): any[] {
    return this.tasks
      .filter(task => task?.config?.data?.dropSetupId === setupId)
      .sort((a, b) => String(b?.config?.data?.dropSetupRun?.startedFromUiAt || "").localeCompare(String(a?.config?.data?.dropSetupRun?.startedFromUiAt || "")));
  }

  childFor(run: any): any | undefined {
    return this.tasks.find(task => task?.config?.data?.triggerSource?.parentTaskId === run.id);
  }

  runPhase(run: any): string {
    const child = this.childFor(run);
    return String(child?.state || run?.state || "UNKNOWN").replace(/_/g, " ");
  }

  runBrowserStatus(run: any): string {
    const child = this.childFor(run);
    const audit = child?.config?.data?.browserEnvironment || run?.config?.data?.browserEnvironment;
    const environment = audit?.status === "green"
      ? " · Env GRÜN"
      : audit?.status === "warning"
        ? " · Env WARNUNG"
        : "";
    if (child) return `Browser läuft${environment}`;
    if (run?.state === TaskState.FAILED) return `Fehler${environment}`;
    if (run?.state === TaskState.CANCELLED) return `Gestoppt${environment}`;
    return `Monitor aktiv${environment}`;
  }

  runProfileName(run: any): string {
    const profileId = String(run?.config?.data?.dropSetupRun?.profileId || run?.config?.data?.monitorAction?.profileId || "");
    return this.profileName(profileId);
  }

  runProxyLabel(run: any): string {
    const selection = run?.config?.data?.monitorAction?.proxySelection as ProxySelection | undefined;
    const profileId = String(run?.config?.data?.monitorAction?.profileId || "");
    return this.proxyLabel(selection, profileId);
  }

  setupShopName(setup: DropSetup): string {
    return this.shops.find(shop => shop.id === setup.shopId)?.name || setup.shopId || "Shop fehlt";
  }

  setupReadiness(setup: DropSetup): string {
    const error = this.validateSetup(setup);
    return error ? "Prüfen" : "Bereit";
  }

  setupRunSummary(setup: DropSetup): string {
    const runs = this.setupRuns(setup.id);
    if (!runs.length) return "Noch nicht gestartet";
    const active = runs.filter(run => ![TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED].includes(run.state)).length;
    return `${runs.length} Run(s) · ${active} aktiv`;
  }

  profileName(profileId: string): string {
    const profile: any = this.profiles.find(item => item.id === profileId);
    return profile?.name || profileId || "Profil fehlt";
  }

  profilePaymentLabel(profileId: string): string {
    const profile = this.profiles.find(item => item.id === profileId);
    const method = profile?.paymentPreference?.method ?? "card";
    const labels: Record<string, string> = {
      card: "Karte · verschlüsselter Profile Vault",
      paypal: "PayPal",
      "shop-pay": "Shop Pay",
      klarna: "Klarna",
      other: "Andere Zahlungsart"
    };
    const label = profile?.paymentPreference?.label?.trim();
    return label ? `${labels[method] ?? method} · ${label}` : (labels[method] ?? method);
  }

  profileProxyName(profileId: string): string {
    const profile: any = this.profiles.find(item => item.id === profileId);
    if (!profile?.preferredProxyId) return "Direkt / kein Standardproxy";
    return this.proxies.find(proxy => proxy.id === profile.preferredProxyId)?.name || profile.preferredProxyId;
  }

  proxyName(proxyId: string): string {
    return this.proxies.find(proxy => proxy.id === proxyId)?.name || proxyId || "Proxy fehlt";
  }

  proxyLabel(selection: ProxySelection | undefined, profileId: string): string {
    if (!selection || selection.mode === "profile-default") return `Profilstandard · ${this.profileProxyName(profileId)}`;
    if (selection.mode === "direct") return "Direkt";
    return this.proxyName(selection.proxyId || "");
  }

  queueFor(run: any): any | undefined {
    const child = this.childFor(run);
    return child?.config?.data?.queueStatus || run?.config?.data?.queueStatus;
  }

  discoveryKeywordsFor(run: any): string[] {
    const child = this.childFor(run);
    const postQueue = child?.config?.data?.postQueueDiscovery || run?.config?.data?.postQueueDiscovery;
    if (Array.isArray(postQueue?.keywords)) return postQueue.keywords.map((item: unknown) => String(item));
    const strategy = run?.config?.data?.monitorStrategy;
    return Array.isArray(strategy?.discoveryKeywords) ? strategy.discoveryKeywords.map((item: unknown) => String(item)) : [];
  }

  canEditLiveKeywords(run: any): boolean {
    const child = this.childFor(run);
    return child?.state === TaskState.POST_QUEUE_DISCOVERY || run?.state === TaskState.POST_QUEUE_DISCOVERY;
  }

  logsFor(run: any): any[] {
    const child = this.childFor(run);
    return [...(this.taskLogs[run.id] || []), ...(child ? this.taskLogs[child.id] || [] : [])]
      .sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime())
      .slice(-30);
  }

  formatLogTime(value: string | Date): string {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "–" : date.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  trackSetup(_index: number, setup: DropSetup): string {
    return setup.id;
  }

  trackProfile(_index: number, profile: AresProfile): string {
    return profile.id;
  }

  trackRun(_index: number, run: any): string {
    return run.id;
  }

  private async refreshReferenceData(): Promise<void> {
    const [shops, profiles, proxies] = await Promise.all([
      this.electron.getShops(),
      this.electron.getProfiles(),
      this.electron.getProxies()
    ]);
    if (shops?.success) this.shops = shops.shops || [];
    if (profiles?.success) this.profiles = profiles.profiles || [];
    if (proxies?.success) this.proxies = proxies.proxies || [];
  }

  private async loadTasks(): Promise<void> {
    const result = await this.electron.getTaskList();
    if (result?.success) this.tasks = result.tasks || [];
  }

  private async loadTaskLogs(taskId: string): Promise<void> {
    const result = await this.electron.getTaskLogs(taskId, 100);
    if (result?.success) this.taskLogs[taskId] = result.logs || [];
  }

  private validateStep(step: number): string {
    if (step === 1) {
      if (!this.draft.name.trim()) return "Bitte einen Namen für das Drop Setup vergeben.";
      if (!this.draft.shopId) return "Bitte einen Shop auswählen.";
    }
    if (step === 2) {
      if (this.draft.strategy === "early-gate" && !this.draft.productName.trim()) return "Für Early Gate ist ein Produktname erforderlich.";
      if (this.draft.strategy === "product-monitor" && !this.draft.searchTerm.trim()) return "Für den Produktmonitor ist ein Suchbegriff oder eine Produkt-URL erforderlich.";
    }
    if (step === 3) {
      if (!this.draft.assignments.length) return "Bitte mindestens ein Profil auswählen.";
      const missingProfile = this.draft.assignments.find(item => !this.profiles.some(profile => profile.id === item.profileId));
      if (missingProfile) return "Mindestens ein zugeordnetes Profil existiert nicht mehr.";
      const missingProxy = this.draft.assignments.find(item => item.proxySelection.mode === "proxy" && !this.proxies.some(proxy => proxy.id === item.proxySelection.proxyId));
      if (missingProxy) return `Für ${this.profileName(missingProxy.profileId)} fehlt der ausgewählte Proxy.`;
    }
    return "";
  }

  private validateAll(): string {
    return this.validateStep(1) || this.validateStep(2) || this.validateStep(3);
  }

  private validateSetup(setup: DropSetup): string {
    const original = this.draft;
    this.draft = setup;
    const error = this.validateAll();
    this.draft = original;
    return error;
  }

  private emptySetup(): DropSetup {
    const now = new Date().toISOString();
    return {
      version: 1,
      id: this.makeId("drop"),
      name: "",
      shopId: "",
      strategy: "early-gate",
      productName: "",
      searchTerm: "",
      discoveryKeywords: [],
      monitorIntervalSeconds: 30,
      assignments: [],
      staggerMs: 2_000,
      createdAt: now,
      updatedAt: now
    };
  }

  private loadStoredSetups(): void {
    try {
      const raw = localStorage.getItem(this.storageKey);
      if (!raw) {
        this.setups = [];
        return;
      }
      const parsed = JSON.parse(raw);
      this.setups = Array.isArray(parsed)
        ? parsed.filter(item => item && item.version === 1 && typeof item.id === "string" && Array.isArray(item.assignments)).map(item => this.normalizeStoredSetup(item))
        : [];
    } catch {
      this.setups = [];
      this.feedback = { kind: "error", text: "Gespeicherte Drop Setups konnten nicht gelesen werden. Es wurden keine Zahlungsdaten geladen oder gespeichert." };
    }
  }

  private persistSetups(): void {
    localStorage.setItem(this.storageKey, JSON.stringify(this.setups));
  }

  private normalizeStoredSetup(value: any): DropSetup {
    return {
      version: 1,
      id: String(value.id),
      name: String(value.name || "Drop Setup"),
      shopId: String(value.shopId || ""),
      strategy: value.strategy === "product-monitor" ? "product-monitor" : "early-gate",
      productName: String(value.productName || ""),
      searchTerm: String(value.searchTerm || ""),
      discoveryKeywords: this.normalizeKeywords(Array.isArray(value.discoveryKeywords) ? value.discoveryKeywords : []),
      monitorIntervalSeconds: this.safeInterval(value.monitorIntervalSeconds),
      assignments: value.assignments.map((item: any) => ({
        id: String(item.id || this.makeId("assignment")),
        profileId: String(item.profileId || ""),
        proxySelection: this.normalizeProxySelection(item.proxySelection),
        headless: item.headless === true
      })).filter((item: DropAssignment) => Boolean(item.profileId)),
      staggerMs: this.safeStagger(value.staggerMs),
      createdAt: String(value.createdAt || new Date().toISOString()),
      updatedAt: String(value.updatedAt || new Date().toISOString())
    };
  }

  private normalizeProxySelection(value: any): ProxySelection {
    if (value?.mode === "direct") return { mode: "direct" };
    if (value?.mode === "proxy") return { mode: "proxy", proxyId: String(value.proxyId || "") };
    return { mode: "profile-default" };
  }

  private cloneSetup(setup: DropSetup): DropSetup {
    return JSON.parse(JSON.stringify(setup)) as DropSetup;
  }

  private normalizeKeywords(values: unknown[]): string[] {
    const seen = new Set<string>();
    const result: string[] = [];
    for (const raw of values) {
      const value = String(raw || "").trim().replace(/\s+/g, " ").slice(0, 160);
      const key = value.toLocaleLowerCase("de-DE");
      if (!value || seen.has(key)) continue;
      seen.add(key);
      result.push(value);
    }
    return result;
  }

  private safeInterval(value: unknown): number {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.max(1, Math.floor(parsed)) : 30;
  }

  private safeStagger(value: unknown): number {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.max(0, Math.min(60_000, Math.floor(parsed))) : 2_000;
  }

  private safeId(value: string): string {
    return String(value || "run").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 48) || "run";
  }

  private makeId(prefix: string): string {
    return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  }

  private buildPaymentSession(profileId: string): CheckoutPaymentSession {
    const profile = this.profiles.find(item => item.id === profileId);
    return {
      method: profile?.paymentPreference?.method ?? "card",
      label: profile?.paymentPreference?.label?.trim() || undefined
    };
  }

  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
