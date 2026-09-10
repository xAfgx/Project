import { Component, Input, OnDestroy, OnInit } from "@angular/core";
import { ElectronService } from "../services/electron.service";
import { ProfileBrowserService, type ProfileBrowserStatusView } from "../services/profile-browser.service";
import type { AresProfile } from "../../profiles/models";
import { clearProfileBrowserUserAgent } from "../../profiles/profile-browser-reset";

interface QueueView {
  active?: boolean;
  phase?: string;
  position?: number;
  timeToWaitSeconds?: number;
  statusText?: string;
  source?: string;
  detectedAt?: string;
  updatedAt?: string;
  elapsedMs?: number;
  maxWaitMs?: number;
}

@Component({
  selector: "app-runtime-control",
  templateUrl: "./runtime-control.component.html",
  styleUrls: ["./runtime-control.component.scss"]
})
export class RuntimeControlComponent implements OnInit, OnDestroy {
  @Input() system: any;
  @Input() tasks: any[] = [];

  runtimeSystem: any;
  actionMessage = "";
  purchaseChanging = false;
  apiKeyTesting = false;
  apiKeyCheck: any;
  bulkStarting = false;
  bulkStopping = false;
  profiles: any[] = [];
  readonly profileBrowserStatuses: Record<string, ProfileBrowserStatusView> = {};
  readonly profileBrowserBusyIds = new Set<string>();
  private refreshTimer?: ReturnType<typeof setInterval>;

  constructor(
    private readonly electron: ElectronService,
    private readonly profileBrowser: ProfileBrowserService
  ) {}

  ngOnInit(): void {
    this.runtimeSystem = this.system;
    void this.refreshRuntime();
    this.refreshTimer = setInterval(() => void this.refreshRuntime(), 10_000);
  }

  ngOnDestroy(): void {
    if (this.refreshTimer) clearInterval(this.refreshTimer);
  }

  get activeQueues(): any[] {
    return this.tasks.filter(task => {
      const queue = this.getQueue(task);
      return task.state === "WAITING_QUEUE" || Boolean(queue?.active);
    });
  }

  get checkoutReadyTasks(): any[] {
    return this.tasks.filter(task => task.state === "CHECKOUT" && this.isEarlyGateChild(task));
  }

  get startableTasks(): any[] {
    return this.tasks.filter(task => task?.state === "QUEUED");
  }

  get stoppableTasks(): any[] {
    const stoppable = new Set([
      "STARTING",
      "RUNNING",
      "WAITING_QUEUE",
      "POST_QUEUE_DISCOVERY",
      "PRODUCT_FOUND",
      "CART",
      "CHECKOUT",
      "RETRYING",
      "PAUSED"
    ]);
    return this.tasks.filter(task => stoppable.has(String(task?.state ?? "")));
  }

  get openProfileBrowserCount(): number {
    return this.profiles.filter(profile => this.isProfileBrowserOpen(profile.id)).length;
  }

  get workers(): any[] {
    return this.currentSystem?.browserWorkerPool?.workers ?? [];
  }

  get watchdog(): any {
    return this.currentSystem?.browserWorkerPool?.watchdog ?? {};
  }

  get finalPurchaseAllowed(): boolean {
    return this.currentSystem?.allowFinalPurchase === true;
  }

  get apiKeyConfigured(): boolean {
    return this.currentSystem?.captchaApiKeyConfigured === true;
  }

  get apiKeyCheckLabel(): string {
    if (!this.apiKeyCheck) return "NOCH NICHT GETESTET";
    if (this.apiKeyCheck.valid === true) return "GÜLTIG";
    if (this.apiKeyCheck.valid === false) return "UNGÜLTIG";
    return "CHECK FEHLER";
  }

  get healthyWorkerCount(): number {
    return this.workers.filter(worker => worker.running && worker.browser?.state !== "degraded").length;
  }

  get activeWorkerTaskCount(): number {
    return this.workers.reduce((sum, worker) => sum + Number(worker.activeTasks ?? 0), 0);
  }

  get restartCount(): number {
    return this.workers.reduce((sum, worker) => sum + Number(worker.restartCount ?? 0), 0);
  }

  get lastWorkerError(): string {
    return String(this.currentSystem?.browserWorkerPool?.lastError ?? "");
  }

  getQueue(task: any): QueueView | undefined {
    const data = task?.config?.data;
    const queue = data?.queueStatus;
    return queue && typeof queue === "object" ? queue as QueueView : undefined;
  }

  queuePosition(task: any): string {
    const position = this.getQueue(task)?.position;
    return typeof position === "number" && Number.isFinite(position)
      ? Math.max(0, Math.floor(position)).toLocaleString("de-DE")
      : "–";
  }

  queueWait(task: any): string {
    const seconds = this.getQueue(task)?.timeToWaitSeconds;
    if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "wird ermittelt";
    return this.formatDuration(seconds * 1_000);
  }

  queueElapsed(task: any): string {
    return this.formatDuration(Number(this.getQueue(task)?.elapsedMs ?? 0));
  }

  queueMaximum(task: any): string {
    return this.formatDuration(Number(this.getQueue(task)?.maxWaitMs ?? 60 * 60_000));
  }

  queueProgress(task: any): number {
    const queue = this.getQueue(task);
    const max = Number(queue?.maxWaitMs ?? 0);
    const elapsed = Number(queue?.elapsedMs ?? 0);
    if (!Number.isFinite(max) || max <= 0 || !Number.isFinite(elapsed)) return 0;
    return Math.min(100, Math.max(0, (elapsed / max) * 100));
  }

  queueStatusText(task: any): string {
    const queue = this.getQueue(task);
    return queue?.statusText || "Warteschlange aktiv – Browser bleibt verbunden.";
  }

  workerState(worker: any): string {
    if (!worker.running) return "OFFLINE";
    if (worker.browser?.state === "degraded") return "DEGRADED";
    return worker.activeTasks > 0 ? "BUSY" : "HEALTHY";
  }

  workerHeartbeat(worker: any): string {
    if (!worker.lastHeartbeatAt) return "noch kein Heartbeat";
    const value = new Date(worker.lastHeartbeatAt);
    return Number.isNaN(value.getTime()) ? "Heartbeat unbekannt" : value.toLocaleTimeString("de-DE");
  }

  watchdogLabel(): string {
    const interval = Number(this.watchdog.heartbeatIntervalMs ?? 30_000);
    const timeout = Number(this.watchdog.heartbeatTimeoutMs ?? 10_000);
    return `${Math.round(interval / 1_000)}s Heartbeat · ${Math.round(timeout / 1_000)}s Timeout`;
  }

  isProfileBrowserOpen(profileId: string): boolean {
    return this.profileBrowserStatuses[profileId]?.open === true;
  }

  profileBrowserDetails(profileId: string): string {
    const status = this.profileBrowserStatuses[profileId];
    if (!status?.open) return "GESCHLOSSEN";
    return `OFFEN${status.pid ? ` · PID ${status.pid}` : ""}`;
  }

  async openProfileBrowser(profile: any): Promise<void> {
    const profileId = String(profile?.id ?? "").trim();
    if (!profileId || this.profileBrowserBusyIds.has(profileId)) return;
    this.profileBrowserBusyIds.add(profileId);
    this.actionMessage = "";
    try {
      const result = await this.profileBrowser.open(profileId);
      if (!result?.success) {
        this.actionMessage = result?.error || `Profil-Browser ${profile?.name || profileId} konnte nicht geöffnet werden.`;
        return;
      }
      this.profileBrowserStatuses[profileId] = result.status;
      this.actionMessage = `Profil-Browser ${profile?.name || profileId} geöffnet.`;
    } finally {
      this.profileBrowserBusyIds.delete(profileId);
    }
  }

  async closeProfileBrowser(profile: any): Promise<void> {
    const profileId = String(profile?.id ?? "").trim();
    if (!profileId || this.profileBrowserBusyIds.has(profileId)) return;
    this.profileBrowserBusyIds.add(profileId);
    this.actionMessage = "";
    try {
      const result = await this.profileBrowser.close(profileId);
      if (!result?.success) {
        this.actionMessage = result?.error || `Profil-Browser ${profile?.name || profileId} konnte nicht geschlossen werden.`;
        return;
      }
      this.profileBrowserStatuses[profileId] = result.status;
      this.actionMessage = `Profil-Browser ${profile?.name || profileId} geschlossen. Session bleibt erhalten.`;
    } finally {
      this.profileBrowserBusyIds.delete(profileId);
    }
  }

  async resetProfileBrowserSession(profile: AresProfile): Promise<void> {
    const profileId = String(profile?.id ?? "").trim();
    if (!profileId || this.profileBrowserBusyIds.has(profileId)) return;
    const confirmed = typeof window === "undefined" || window.confirm(
      `Browser-Session für ${profile.name || profileId} vollständig löschen?\n\n` +
      "Browserdaten, Cookies, Storage, Cache, Cookie-Snapshots und der gespeicherte User-Agent werden entfernt. Adresse, Proxy und Zahlung bleiben erhalten."
    );
    if (!confirmed) return;

    this.profileBrowserBusyIds.add(profileId);
    this.actionMessage = "";
    const profileWithoutUserAgent = clearProfileBrowserUserAgent(profile);
    try {
      const saveResult = await this.electron.saveProfile(profileWithoutUserAgent);
      if (!saveResult?.success) {
        this.actionMessage = saveResult?.error || "User-Agent konnte vor dem Session-Reset nicht sicher zurückgesetzt werden.";
        return;
      }

      const resetResult = await this.profileBrowser.resetSession(profileId);
      if (!resetResult?.success) {
        const rollback = await this.electron.saveProfile(profile).catch(() => undefined);
        this.actionMessage = rollback?.success
          ? (resetResult?.error || "Browser-Session konnte nicht gelöscht werden; User-Agent wurde wiederhergestellt.")
          : `${resetResult?.error || "Browser-Session konnte nicht gelöscht werden."} User-Agent-Rollback ebenfalls fehlgeschlagen.`;
        return;
      }

      this.profileBrowserStatuses[profileId] = resetResult.status;
      this.actionMessage = `Profil ${profile.name || profileId}: Browserdaten, Cookie-Snapshots und User-Agent vollständig gelöscht.`;
      await this.refreshRuntime();
    } finally {
      this.profileBrowserBusyIds.delete(profileId);
    }
  }

  async startAllTasks(): Promise<void> {
    if (this.bulkStarting) return;
    const targets = [...this.startableTasks];
    if (!targets.length) {
      this.actionMessage = "Keine QUEUED Tasks zum Starten.";
      return;
    }

    this.bulkStarting = true;
    this.actionMessage = `${targets.length} Task(s) werden gestaffelt gestartet…`;

    let started = 0;
    let failed = 0;
    for (let index = 0; index < targets.length; index += 1) {
      try {
        const result = await this.electron.startTask(targets[index].id);
        if (result?.success) started += 1;
        else failed += 1;
      } catch {
        failed += 1;
      }
      this.actionMessage = `${started}/${targets.length} gestartet…`;
      if (index < targets.length - 1) await this.delay(500);
    }

    this.actionMessage = failed
      ? `${started}/${targets.length} Task(s) gestartet · ${failed} Fehler.`
      : `${targets.length} Task(s) gestartet.`;
    await this.refreshRuntime();
    this.bulkStarting = false;
  }

  async stopAllTasks(): Promise<void> {
    if (this.bulkStopping) return;
    const targets = [...this.stoppableTasks];
    if (!targets.length) {
      this.actionMessage = "Keine laufenden Tasks zum Stoppen.";
      return;
    }

    this.bulkStopping = true;
    this.actionMessage = "";
    try {
      const results = await Promise.allSettled(targets.map(async task => {
        await this.electron.clearPaymentSession(task.id).catch(() => undefined);
        return this.electron.stopTask(task.id);
      }));
      const failed = results.filter(result => result.status === "rejected" || (result.status === "fulfilled" && !result.value?.success));
      this.actionMessage = failed.length
        ? `${targets.length - failed.length}/${targets.length} Task(s) gestoppt · ${failed.length} Fehler.`
        : `${targets.length} Task(s) gestoppt.`;
      await this.refreshRuntime();
    } finally {
      this.bulkStopping = false;
    }
  }

  async testCapmonsterApiKey(): Promise<void> {
    if (this.apiKeyTesting) return;
    this.apiKeyTesting = true;
    this.actionMessage = "";
    try {
      const result = await this.electron.testCapmonsterApiKey();
      this.apiKeyCheck = result;
      if (result?.valid === true) {
        this.actionMessage = "CapMonster API-Key ist gültig.";
      } else if (result?.valid === false) {
        this.actionMessage = `CapMonster API-Key wurde abgelehnt${result.errorCode ? ` · ${result.errorCode}` : ""}.`;
      } else {
        this.actionMessage = result?.error || "CapMonster API-Key konnte nicht geprüft werden.";
      }
      await this.refreshRuntime();
    } finally {
      this.apiKeyTesting = false;
    }
  }

  async setFinalPurchaseAllowed(allowed: boolean): Promise<void> {
    if (this.purchaseChanging || allowed === this.finalPurchaseAllowed) return;
    this.purchaseChanging = true;
    this.actionMessage = "";
    try {
      const result = await this.electron.setFinalPurchaseAllowed(allowed);
      if (!result?.success) {
        this.actionMessage = result?.error || "Globale Kauf-Freigabe wurde vom Backend abgelehnt.";
      } else {
        this.actionMessage = result.allowFinalPurchase
          ? "Finaler Kauf global freigegeben. Backend-Guard bleibt bis unmittelbar vor Submit aktiv."
          : "Finaler Kauf global gesperrt. Alle Checkout-Tasks stoppen vor dem Submit.";
      }
      await this.refreshRuntime();
    } finally {
      this.purchaseChanging = false;
    }
  }

  async pauseTask(taskId: string): Promise<void> {
    const result = await this.electron.pauseTask(taskId);
    this.actionMessage = result.success
      ? "Queue-Task pausiert. Browser-Kontext wird kontrolliert beendet."
      : result.error || "Task konnte nicht pausiert werden.";
  }

  async stopTask(taskId: string): Promise<void> {
    const result = await this.electron.stopTask(taskId);
    this.actionMessage = result.success
      ? "Queue-Task gestoppt."
      : result.error || "Task konnte nicht gestoppt werden.";
  }

  private isEarlyGateChild(task: any): boolean {
    const trigger = task?.config?.data?.triggerSource;
    return trigger?.kind === "early-gate" && Boolean(trigger?.parentTaskId);
  }

  private get currentSystem(): any {
    return this.runtimeSystem ?? this.system ?? {};
  }

  private async refreshProfileBrowsers(): Promise<void> {
    const profilesResult = await this.electron.getProfiles().catch(() => undefined);
    if (!profilesResult?.success) return;
    this.profiles = Array.isArray(profilesResult.profiles) ? profilesResult.profiles : [];

    await Promise.all(this.profiles.map(async profile => {
      const profileId = String(profile?.id ?? "").trim();
      if (!profileId || this.profileBrowserBusyIds.has(profileId)) return;
      const result = await this.profileBrowser.getStatus(profileId).catch(() => undefined);
      if (result?.success && result.status) this.profileBrowserStatuses[profileId] = result.status;
    }));
  }

  private async refreshRuntime(): Promise<void> {
    const [result] = await Promise.all([
      this.electron.getSystemStatus(),
      this.refreshProfileBrowsers()
    ]);
    if (result?.success) this.runtimeSystem = result;
  }

  private formatDuration(ms: number): string {
    if (!Number.isFinite(ms) || ms < 0) return "–";
    const totalSeconds = Math.floor(ms / 1_000);
    const hours = Math.floor(totalSeconds / 3_600);
    const minutes = Math.floor((totalSeconds % 3_600) / 60);
    const seconds = totalSeconds % 60;
    if (hours > 0) return `${hours}h ${minutes}m`;
    if (minutes > 0) return `${minutes}m ${seconds}s`;
    return `${seconds}s`;
  }

  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
